from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_verified_knowledge_lineage"
down_revision: str | None = "0034_catalogue_admission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_document_trust(*, verified: bool) -> None:
    old = "document_status <> 'trusted'"
    new = (
        "NOT public.knowledge_source_lineage_is_current(NEW.source_document_id, "
        "NEW.page_number, NEW.source_candidate_id, NEW.curriculum_version_id, NEW.text)"
    )
    source, target = (old, new) if verified else (new, old)
    op.execute(
        str(
            sa.text(
                """
            DO $migration$
            DECLARE original text; revised text;
            BEGIN
                original := pg_get_functiondef(
                    'validate_knowledge_record_references()'::regprocedure
                );
                revised := replace(original, :source, :target);
                IF revised = original THEN
                    RAISE EXCEPTION 'knowledge reference guard did not match expected migration';
                END IF;
                EXECUTE revised;
            END $migration$;
            """
            )
            .bindparams(source=source, target=target)
            .compile(compile_kwargs={"literal_binds": True})
        )
    )


def upgrade() -> None:
    for table in ("knowledge_chunks", "historical_questions"):
        op.add_column(table, sa.Column("source_candidate_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_source_candidate",
            table,
            "source_page_text_candidates",
            ["source_candidate_id", "source_document_id", "page_number"],
            ["id", "document_id", "page_number"],
            ondelete="RESTRICT",
        )
    op.execute(
        """
        CREATE FUNCTION public.knowledge_source_lineage_is_current(
            source_id uuid, number integer, candidate_id uuid, curriculum_id uuid, source_text text
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT candidate_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM public.source_documents d
                JOIN public.source_page_text_candidates c ON c.document_id = d.id
                    AND c.page_number = number AND c.id = candidate_id
                WHERE d.id = source_id AND d.curriculum_version_id = curriculum_id
                    AND d.active_for_ai AND NOT d.metadata_review_required
                    AND public.catalogue_curriculum_is_admitted(curriculum_id)
                    AND public.source_page_fidelity_is_current(source_id, number, candidate_id)
                    AND c.normalized_text IS NOT NULL AND c.can_confirm
                    AND c.text_sha256 = encode(sha256(convert_to(c.normalized_text, 'UTF8')), 'hex')
                    AND source_text = normalize(source_text, NFC)
                    AND length(btrim(source_text)) > 0
                    AND strpos(c.normalized_text, source_text) > 0
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.lock_knowledge_source_lineage(
            source_id uuid, number integer, curriculum_id uuid
        ) RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM 1 FROM public.source_documents WHERE id = source_id FOR SHARE;
            PERFORM 1 FROM public.source_page_review_states
                WHERE document_id = source_id AND page_number = number FOR SHARE;
            PERFORM 1 FROM public.curriculum_versions cv
                JOIN public.exam_configurations e ON e.id = cv.exam_configuration_id
                JOIN public.media m ON m.id = cv.medium_id
                JOIN public.subjects s ON s.id = cv.subject_id
                WHERE cv.id = curriculum_id FOR SHARE OF cv, e, m, s;
            PERFORM 1 FROM public.catalogue_admission_current
                WHERE curriculum_version_id = curriculum_id FOR SHARE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.knowledge_record_is_eligible(kind text, record_id uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            WITH record AS (
                SELECT id, curriculum_version_id, source_document_id, page_number,
                    source_candidate_id, source_block_id, text, review_state, unit_id, lesson_id,
                    competency_id, skill_id, sub_skill_id, learning_concept_id
                FROM public.knowledge_chunks WHERE kind = 'knowledge_chunk' AND id = record_id
                UNION ALL
                SELECT id, curriculum_version_id, source_document_id, page_number,
                    source_candidate_id, source_block_id, text, review_state, unit_id, lesson_id,
                    competency_id, skill_id, sub_skill_id, learning_concept_id
                FROM public.historical_questions
                WHERE kind = 'historical_question' AND id = record_id
            )
            SELECT EXISTS (
                SELECT 1 FROM record r
                JOIN public.source_documents d ON d.id = r.source_document_id
                JOIN public.extracted_blocks b ON b.id = r.source_block_id
                    AND b.source_document_id = r.source_document_id
                    AND b.page_number = r.page_number
                JOIN public.taxonomy_nodes competency ON competency.id = r.competency_id
                    AND competency.curriculum_version_id = r.curriculum_version_id
                    AND competency.level = 'competency' AND competency.active
                    AND competency.review_state = 'reviewed'
                LEFT JOIN public.taxonomy_nodes skill ON skill.id = r.skill_id
                LEFT JOIN public.taxonomy_nodes sub ON sub.id = r.sub_skill_id
                LEFT JOIN public.taxonomy_nodes concept ON concept.id = r.learning_concept_id
                LEFT JOIN public.curriculum_units u ON u.id = r.unit_id
                LEFT JOIN public.curriculum_lessons l ON l.id = r.lesson_id
                WHERE r.review_state = 'reviewed'
                    AND r.unit_id IS NOT DISTINCT FROM d.unit_id
                    AND r.lesson_id IS NOT DISTINCT FROM d.lesson_id
                    AND public.knowledge_source_lineage_is_current(
                        r.source_document_id, r.page_number, r.source_candidate_id,
                        r.curriculum_version_id, r.text
                    )
                    AND (r.unit_id IS NULL OR (u.active
                        AND u.curriculum_version_id = r.curriculum_version_id))
                    AND (r.lesson_id IS NULL OR (l.active AND l.unit_id = r.unit_id
                        AND l.curriculum_version_id = r.curriculum_version_id))
                    AND (r.skill_id IS NULL OR (skill.active AND skill.review_state = 'reviewed'
                        AND skill.level = 'skill' AND skill.parent_id = competency.id
                        AND skill.curriculum_version_id = r.curriculum_version_id))
                    AND (r.sub_skill_id IS NULL OR (sub.active AND sub.review_state = 'reviewed'
                        AND sub.level = 'sub_skill' AND sub.parent_id = skill.id
                        AND sub.curriculum_version_id = r.curriculum_version_id))
                    AND (r.learning_concept_id IS NULL OR (concept.active
                        AND concept.review_state = 'reviewed'
                        AND concept.level = 'learning_concept' AND concept.parent_id = sub.id
                        AND concept.curriculum_version_id = r.curriculum_version_id))
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.lock_knowledge_record_lineage(kind text, record_id uuid)
        RETURNS boolean LANGUAGE plpgsql AS $$
        DECLARE item record;
        BEGIN
            IF kind = 'knowledge_chunk' THEN
                SELECT * INTO item FROM public.knowledge_chunks WHERE id = record_id FOR SHARE;
            ELSIF kind = 'historical_question' THEN
                SELECT * INTO item FROM public.historical_questions WHERE id = record_id FOR SHARE;
            ELSE RETURN false;
            END IF;
            IF NOT FOUND THEN RETURN false; END IF;
            PERFORM public.lock_knowledge_source_lineage(
                item.source_document_id, item.page_number, item.curriculum_version_id
            );
            PERFORM 1 FROM public.curriculum_units WHERE id = item.unit_id FOR SHARE;
            PERFORM 1 FROM public.curriculum_lessons WHERE id = item.lesson_id FOR SHARE;
            PERFORM 1 FROM public.taxonomy_nodes WHERE id IN (
                item.competency_id, item.skill_id, item.sub_skill_id, item.learning_concept_id
            ) ORDER BY id FOR SHARE;
            RETURN public.knowledge_record_is_eligible(kind, record_id);
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_verified_knowledge_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'knowledge lineage history cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE'
                AND NEW.source_candidate_id IS DISTINCT FROM OLD.source_candidate_id THEN
                RAISE EXCEPTION 'knowledge source candidate binding is immutable'
                    USING ERRCODE = '23514';
            END IF;
            PERFORM public.lock_knowledge_source_lineage(
                NEW.source_document_id, NEW.page_number, NEW.curriculum_version_id
            );
            IF NOT public.knowledge_source_lineage_is_current(
                NEW.source_document_id, NEW.page_number, NEW.source_candidate_id,
                NEW.curriculum_version_id, NEW.text
            ) THEN
                RAISE EXCEPTION 'knowledge requires current verified page text and admitted scope'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    _replace_document_trust(verified=True)
    for table in ("knowledge_chunks", "historical_questions"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER aa_verified_lineage BEFORE INSERT OR UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION public.enforce_verified_knowledge_lineage()"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER verified_lineage_no_truncate BEFORE TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION public.enforce_verified_knowledge_lineage()"
            )
        )
    op.execute(
        """
        CREATE FUNCTION public.enforce_verified_embedding_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT public.lock_knowledge_record_lineage(
                CASE WHEN NEW.knowledge_chunk_id IS NOT NULL THEN 'knowledge_chunk'
                    ELSE 'historical_question' END,
                COALESCE(NEW.knowledge_chunk_id, NEW.historical_question_id)
            ) THEN
                RAISE EXCEPTION 'embedding requires current verified knowledge lineage'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER aa_verified_embedding_lineage BEFORE INSERT ON knowledge_embeddings
        FOR EACH ROW EXECUTE FUNCTION public.enforce_verified_embedding_lineage()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.generation_context_lineage_is_current(
            curriculum_id uuid, chunk_ids jsonb, question_ids jsonb,
            snapshot jsonb, lock_sources boolean DEFAULT false
        ) RETURNS boolean LANGUAGE plpgsql AS $$
        DECLARE item jsonb; source record; kind text; record_id uuid; candidate_hash text;
            seen text[] := ARRAY[]::text[]; identity text; source_hash text; provenance jsonb;
        BEGIN
            IF lock_sources THEN
                PERFORM public.lock_knowledge_source_lineage(NULL, NULL, curriculum_id);
            END IF;
            IF NOT public.catalogue_curriculum_is_admitted(curriculum_id)
                OR jsonb_typeof(chunk_ids) IS DISTINCT FROM 'array'
                OR jsonb_typeof(question_ids) IS DISTINCT FROM 'array'
                OR jsonb_typeof(snapshot->'items') IS DISTINCT FROM 'array'
                OR snapshot->>'trust' IS DISTINCT FROM 'untrusted_data'
            THEN RETURN false; END IF;
            IF jsonb_array_length(snapshot->'items') NOT BETWEEN 1 AND 16
                OR jsonb_array_length(snapshot->'items') <>
                    jsonb_array_length(chunk_ids) + jsonb_array_length(question_ids)
            THEN RETURN false; END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(snapshot->'items')
                ORDER BY value->>'record_kind', value->>'record_id'
            LOOP
                kind := item->>'record_kind';
                record_id := (item->>'record_id')::uuid;
                identity := kind || ':' || record_id::text;
                IF identity IS NULL OR identity = ANY(seen)
                    OR item->>'context_id' IS DISTINCT FROM identity
                    OR item->>'trust' IS DISTINCT FROM 'untrusted_data'
                THEN RETURN false; END IF;
                seen := array_append(seen, identity);
                provenance := item->'provenance';
                IF jsonb_typeof(provenance) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
                IF provenance - ARRAY[
                    'source_document_id', 'source_version', 'page_number', 'chunk_id',
                    'source_block_id', 'source_candidate_id', 'source_candidate_sha256'
                ] IS DISTINCT FROM '{}'::jsonb
                    OR NOT (provenance ?& ARRAY[
                        'source_document_id', 'source_version', 'page_number', 'chunk_id',
                        'source_block_id', 'source_candidate_id', 'source_candidate_sha256'
                    ])
                    OR jsonb_typeof(provenance->'source_candidate_id') IS DISTINCT FROM 'string'
                    OR jsonb_typeof(provenance->'source_candidate_sha256')
                        IS DISTINCT FROM 'string'
                THEN RETURN false; END IF;
                IF lock_sources THEN
                    IF NOT public.lock_knowledge_record_lineage(kind, record_id) THEN
                        RETURN false;
                    END IF;
                ELSIF NOT public.knowledge_record_is_eligible(kind, record_id) THEN RETURN false;
                END IF;
                IF kind = 'knowledge_chunk' AND chunk_ids ? record_id::text THEN
                    SELECT * INTO source FROM public.knowledge_chunks WHERE id = record_id;
                ELSIF kind = 'historical_question' AND question_ids ? record_id::text THEN
                    SELECT * INTO source FROM public.historical_questions WHERE id = record_id;
                ELSE RETURN false;
                END IF;
                IF NOT FOUND THEN RETURN false; END IF;
                SELECT text_sha256 INTO candidate_hash FROM public.source_page_text_candidates
                    WHERE id = source.source_candidate_id;
                SELECT checksum_sha256 INTO source_hash FROM public.source_documents
                    WHERE id = source.source_document_id;
                IF item->>'text' IS DISTINCT FROM source.text
                    OR item->>'record_version' IS DISTINCT FROM source.version::text
                    OR item->'provenance'->>'source_candidate_id'
                        IS DISTINCT FROM source.source_candidate_id::text
                    OR item->'provenance'->>'source_candidate_sha256'
                        IS DISTINCT FROM candidate_hash
                    OR item->'provenance'->>'source_document_id'
                        IS DISTINCT FROM source.source_document_id::text
                    OR item->'provenance'->>'source_version'
                        IS DISTINCT FROM ('sha256:' || source_hash)
                    OR item->'provenance'->>'source_block_id'
                        IS DISTINCT FROM source.source_block_id::text
                    OR item->'provenance'->>'page_number' IS DISTINCT FROM source.page_number::text
                    OR item->'provenance'->>'chunk_id' IS DISTINCT FROM record_id::text
                THEN RETURN false; END IF;
            END LOOP;
            RETURN true;
        EXCEPTION WHEN invalid_text_representation THEN RETURN false;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_generation_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' OR NEW.status = 'succeeded' THEN
                IF NOT public.generation_context_lineage_is_current(
                    NEW.curriculum_version_id, NEW.knowledge_chunk_ids,
                    NEW.historical_question_ids, NEW.context_snapshot, true
                ) THEN
                    RAISE EXCEPTION 'generation requires current verified knowledge lineage'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER aa_generation_verified_lineage BEFORE INSERT OR UPDATE ON generation_runs
        FOR EACH ROW EXECUTE FUNCTION public.enforce_generation_verified_lineage()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_validation_review_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run public.generation_runs%ROWTYPE;
        BEGIN
            SELECT * INTO run FROM public.generation_runs
                WHERE id = NEW.generation_run_id
                    AND curriculum_version_id = NEW.curriculum_version_id FOR SHARE;
            IF NOT FOUND OR NOT public.generation_context_lineage_is_current(
                run.curriculum_version_id, run.knowledge_chunk_ids,
                run.historical_question_ids, run.context_snapshot, true
            ) THEN
                RAISE EXCEPTION 'validation and review require current verified knowledge lineage'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER aa_validation_verified_lineage BEFORE INSERT ON validation_runs
        FOR EACH ROW EXECUTE FUNCTION public.enforce_validation_review_verified_lineage()
        """
    )
    op.execute(
        """
        CREATE TRIGGER aa_review_verified_lineage BEFORE INSERT ON question_candidates
        FOR EACH ROW EXECUTE FUNCTION public.enforce_validation_review_verified_lineage()
        """
    )
    op.execute(
        """
        CREATE TRIGGER aa_review_transition_verified_lineage
        BEFORE UPDATE OF state ON question_candidates
        FOR EACH ROW WHEN (NEW.state IS DISTINCT FROM OLD.state AND NEW.state <> 'rejected')
        EXECUTE FUNCTION public.enforce_validation_review_verified_lineage()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_publication_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run public.generation_runs%ROWTYPE; found_run boolean := false;
        BEGIN
            PERFORM public.lock_knowledge_source_lineage(NULL, NULL, NEW.curriculum_version_id);
            IF NOT public.catalogue_curriculum_is_admitted(NEW.curriculum_version_id) THEN
                RAISE EXCEPTION 'publication requires admitted curriculum' USING ERRCODE = '23514';
            END IF;
            FOR run IN SELECT g.* FROM public.paper_draft_candidates d
                JOIN public.question_candidates c ON c.id = d.candidate_id
                JOIN public.generation_runs g ON g.id = c.generation_run_id
                WHERE d.paper_id = NEW.paper_id AND d.paper_version = NEW.version
                ORDER BY g.id
            LOOP
                found_run := true;
                IF NOT public.generation_context_lineage_is_current(
                    run.curriculum_version_id, run.knowledge_chunk_ids,
                    run.historical_question_ids, run.context_snapshot, true
                ) THEN
                    RAISE EXCEPTION 'publication requires current verified knowledge lineage'
                        USING ERRCODE = '23514';
                END IF;
            END LOOP;
            IF NOT found_run THEN
                RAISE EXCEPTION 'publication requires verified generation lineage'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER aa_publication_verified_lineage BEFORE INSERT ON published_paper_versions
        FOR EACH ROW EXECUTE FUNCTION public.enforce_publication_verified_lineage()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM knowledge_chunks WHERE source_candidate_id IS NOT NULL)
                OR EXISTS (SELECT 1 FROM historical_questions WHERE source_candidate_id IS NOT NULL)
            THEN RAISE EXCEPTION 'cannot discard verified knowledge lineage'; END IF;
        END $$
        """
    )
    op.execute("DROP TRIGGER aa_publication_verified_lineage ON published_paper_versions")
    op.execute("DROP FUNCTION public.enforce_publication_verified_lineage()")
    op.execute("DROP TRIGGER aa_review_transition_verified_lineage ON question_candidates")
    op.execute("DROP TRIGGER aa_review_verified_lineage ON question_candidates")
    op.execute("DROP TRIGGER aa_validation_verified_lineage ON validation_runs")
    op.execute("DROP FUNCTION public.enforce_validation_review_verified_lineage()")
    op.execute("DROP TRIGGER aa_generation_verified_lineage ON generation_runs")
    op.execute("DROP FUNCTION public.enforce_generation_verified_lineage()")
    op.execute(
        "DROP FUNCTION public.generation_context_lineage_is_current"
        "(uuid, jsonb, jsonb, jsonb, boolean)"
    )
    op.execute("DROP TRIGGER aa_verified_embedding_lineage ON knowledge_embeddings")
    op.execute("DROP FUNCTION public.enforce_verified_embedding_lineage()")
    _replace_document_trust(verified=False)
    for table in ("knowledge_chunks", "historical_questions"):
        op.execute(sa.text(f"DROP TRIGGER aa_verified_lineage ON {table}"))
        op.execute(sa.text(f"DROP TRIGGER verified_lineage_no_truncate ON {table}"))
        op.drop_constraint(f"fk_{table}_source_candidate", table, type_="foreignkey")
    op.execute("DROP FUNCTION public.enforce_verified_knowledge_lineage()")
    op.execute("DROP FUNCTION public.lock_knowledge_record_lineage(text, uuid)")
    op.execute("DROP FUNCTION public.knowledge_record_is_eligible(text, uuid)")
    op.execute("DROP FUNCTION public.lock_knowledge_source_lineage(uuid, integer, uuid)")
    op.execute(
        "DROP FUNCTION public.knowledge_source_lineage_is_current(uuid, integer, uuid, uuid, text)"
    )
    for table in ("knowledge_chunks", "historical_questions"):
        op.drop_column(table, "source_candidate_id")
