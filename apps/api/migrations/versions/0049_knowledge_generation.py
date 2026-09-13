import sqlalchemy as sa
from alembic import op

revision = "0049_knowledge_generation"
down_revision = "0048_projection_embeddings"
branch_labels = None
depends_on = None


def _constraints(*, knowledge: bool) -> None:
    ids = "coalesce(context_snapshot->'knowledge_projection_ids','[]'::jsonb)"
    additional = f" + jsonb_array_length({ids})" if knowledge else ""
    validation = f"generation_uuid_array_valid({ids},16) AND " if knowledge else ""
    op.drop_constraint("ck_generation_runs_context_ids", "generation_runs", type_="check")
    op.drop_constraint("ck_generation_runs_context_snapshot", "generation_runs", type_="check")
    op.create_check_constraint(
        "ck_generation_runs_context_ids",
        "generation_runs",
        "generation_uuid_array_valid(knowledge_chunk_ids,16) AND "
        "generation_uuid_array_valid(historical_question_ids,16) AND "
        + validation
        + "jsonb_array_length(knowledge_chunk_ids)+jsonb_array_length(historical_question_ids)"
        + additional
        + " BETWEEN 1 AND 16",
    )
    op.create_check_constraint(
        "ck_generation_runs_context_snapshot",
        "generation_runs",
        "jsonb_typeof(context_snapshot)='object' AND context_snapshot ?& ARRAY['items','trust'] "
        "AND jsonb_typeof(context_snapshot->'items')='array' "
        "AND jsonb_array_length(context_snapshot->'items')="
        "jsonb_array_length(knowledge_chunk_ids)+jsonb_array_length(historical_question_ids)"
        + additional
        + " AND context_snapshot->>'trust'='untrusted_data' "
        "AND pg_column_size(context_snapshot)<=262144",
    )


def _lineage_triggers(*, knowledge: bool) -> None:
    predicate = (
        "generation_knowledge_context_is_current"
        if knowledge
        else "generation_context_lineage_is_current"
    )
    for statement in (
        """
        CREATE OR REPLACE FUNCTION public.enforce_generation_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' OR NEW.status='succeeded' THEN
                IF NOT public.%(predicate)s(
                    NEW.curriculum_version_id,NEW.knowledge_chunk_ids,
                    NEW.historical_question_ids,NEW.context_snapshot,true)
                THEN RAISE EXCEPTION 'generation requires current verified knowledge lineage'
                    USING ERRCODE='23514'; END IF;
            END IF;
            RETURN NEW;
        END; $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.enforce_validation_review_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run public.generation_runs%%ROWTYPE;
        BEGIN
            SELECT * INTO run FROM public.generation_runs WHERE id=NEW.generation_run_id
                AND curriculum_version_id=NEW.curriculum_version_id FOR SHARE;
            IF NOT FOUND OR NOT public.%(predicate)s(
                run.curriculum_version_id,run.knowledge_chunk_ids,
                run.historical_question_ids,run.context_snapshot,true)
            THEN RAISE EXCEPTION 'validation and review require current verified knowledge lineage'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
        """,
        """
        CREATE OR REPLACE FUNCTION public.enforce_publication_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run public.generation_runs%%ROWTYPE; found_run boolean:=false;
        BEGIN
            PERFORM public.lock_knowledge_source_lineage(NULL,NULL,NEW.curriculum_version_id);
            IF NOT public.catalogue_curriculum_is_admitted(NEW.curriculum_version_id) THEN
                RAISE EXCEPTION 'publication requires admitted curriculum' USING ERRCODE='23514';
            END IF;
            FOR run IN SELECT g.* FROM public.paper_draft_candidates d
                JOIN public.question_candidates c ON c.id=d.candidate_id
                JOIN public.generation_runs g ON g.id=c.generation_run_id
                WHERE d.paper_id=NEW.paper_id AND d.paper_version=NEW.version ORDER BY g.id
            LOOP
                found_run:=true;
                IF NOT public.%(predicate)s(
                    run.curriculum_version_id,run.knowledge_chunk_ids,
                    run.historical_question_ids,run.context_snapshot,true)
                THEN RAISE EXCEPTION 'publication requires current verified knowledge lineage'
                    USING ERRCODE='23514'; END IF;
            END LOOP;
            IF NOT found_run THEN RAISE EXCEPTION 'publication requires verified generation lineage'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
        """,
    ):
        op.execute(sa.DDL(statement, context={"predicate": predicate}))


def upgrade() -> None:
    _constraints(knowledge=True)
    op.execute("""
        CREATE FUNCTION public.knowledge_generation_scope_matches(selected jsonb, actual jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE name text;
        BEGIN
            IF jsonb_typeof(selected) IS DISTINCT FROM 'object'
                OR jsonb_typeof(selected->'taxonomy') IS DISTINCT FROM 'object'
                OR jsonb_typeof(selected->'unit_ids') IS DISTINCT FROM 'array'
                OR jsonb_typeof(selected->'lesson_ids') IS DISTINCT FROM 'array'
                OR selected->>'grade' IS DISTINCT FROM actual->>'grade'
            THEN RETURN false; END IF;
            FOREACH name IN ARRAY ARRAY[
                'grade','exam_id','medium_id','subject_id','curriculum_version_id'
            ] LOOP
                IF selected->name IS DISTINCT FROM actual->name THEN RETURN false; END IF;
            END LOOP;
            FOREACH name IN ARRAY ARRAY['unit_ids','lesson_ids'] LOOP
                IF jsonb_array_length(selected->name)>0 AND
                    (jsonb_array_length(actual->name)=0 OR NOT ((actual->name)<@(selected->name)))
                THEN RETURN false; END IF;
            END LOOP;
            FOREACH name IN ARRAY ARRAY[
                'competency_id','skill_id','sub_skill_id','learning_concept_id'
            ] LOOP
                IF selected->'taxonomy'->name IS DISTINCT FROM 'null'::jsonb
                    AND selected->'taxonomy'->name IS DISTINCT FROM actual->'taxonomy'->name
                THEN RETURN false; END IF;
            END LOOP;
            RETURN true;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.generation_knowledge_context_is_current(
            curriculum_id uuid, chunk_ids jsonb, question_ids jsonb, snapshot jsonb,
            lock_sources boolean DEFAULT false
        ) RETURNS boolean LANGUAGE plpgsql AS $$
        DECLARE projection_ids jsonb; item jsonb; kind text; identifier uuid; identity text;
            seen text[]:=ARRAY[]::text[]; document_id uuid; reference jsonb; evidence jsonb;
            p public.knowledge_projections%ROWTYPE; u public.knowledge_units%ROWTYPE;
            r public.knowledge_unit_reviews%ROWTYPE; cv public.curriculum_versions%ROWTYPE;
            exam public.exam_configurations%ROWTYPE; expected_scope jsonb; filters jsonb;
            allowed boolean; expected_provenance jsonb;
        BEGIN
            IF NOT (snapshot ? 'knowledge_projection_ids') THEN
                RETURN public.generation_context_lineage_is_current(curriculum_id,chunk_ids,
                    question_ids,snapshot,lock_sources);
            END IF;
            projection_ids:=snapshot->'knowledge_projection_ids';
            IF NOT public.generation_uuid_array_valid(projection_ids,16)
                OR NOT public.generation_uuid_array_valid(chunk_ids,16)
                OR NOT public.generation_uuid_array_valid(question_ids,16)
                OR jsonb_array_length(projection_ids)=0
                OR snapshot->>'schema_version' IS DISTINCT FROM 'generation-knowledge-context.v1'
                OR snapshot->>'trust' IS DISTINCT FROM 'untrusted_data'
                OR jsonb_typeof(snapshot->'items') IS DISTINCT FROM 'array'
                OR octet_length(snapshot::text)>262144
                OR NOT public.catalogue_curriculum_is_admitted(curriculum_id)
            THEN RETURN false; END IF;
            IF jsonb_array_length(snapshot->'items') NOT BETWEEN 1 AND 16
                OR jsonb_array_length(snapshot->'items')<>jsonb_array_length(projection_ids)
                    +jsonb_array_length(chunk_ids)+jsonb_array_length(question_ids)
            THEN RETURN false; END IF;
            filters:=snapshot->'retrieval_filters';
            IF jsonb_typeof(filters) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
            IF lock_sources THEN
                PERFORM public.lock_knowledge_source_lineage(NULL,NULL,curriculum_id);
                FOR document_id IN SELECT DISTINCT source.document_id
                    FROM public.knowledge_units source
                    JOIN public.knowledge_projections projected ON projected.unit_id=source.id
                    WHERE projection_ids ? projected.id::text ORDER BY source.document_id
                LOOP PERFORM public.lock_knowledge_unit_source(document_id); END LOOP;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(snapshot->'items')
                ORDER BY value->>'record_kind',value->>'record_id'
            LOOP
                kind:=item->>'record_kind'; identifier:=(item->>'record_id')::uuid;
                identity:=kind||':'||identifier::text;
                IF identity IS NULL OR identity=ANY(seen)
                    OR item->>'context_id' IS DISTINCT FROM identity
                    OR item->>'trust' IS DISTINCT FROM 'untrusted_data' THEN RETURN false; END IF;
                seen:=array_append(seen,identity);
                IF kind IN ('knowledge_chunk','historical_question') THEN
                    IF item ? 'knowledge_evidence'
                        OR (kind='knowledge_chunk' AND NOT (chunk_ids ? identifier::text))
                        OR (kind='historical_question' AND NOT (question_ids ? identifier::text))
                        OR NOT public.generation_context_lineage_is_current(curriculum_id,
                            CASE WHEN kind='knowledge_chunk'
                                THEN jsonb_build_array(identifier::text) ELSE '[]'::jsonb END,
                            CASE WHEN kind='historical_question'
                                THEN jsonb_build_array(identifier::text) ELSE '[]'::jsonb END,
                            jsonb_build_object('items',jsonb_build_array(item),
                                'trust','untrusted_data'),lock_sources)
                    THEN RETURN false; END IF;
                    CONTINUE;
                END IF;
                IF kind IS DISTINCT FROM 'knowledge_projection'
                    OR NOT (projection_ids ? identifier::text) THEN RETURN false; END IF;
                SELECT * INTO p FROM public.knowledge_projections WHERE id=identifier;
                IF NOT FOUND THEN RETURN false; END IF;
                SELECT * INTO u FROM public.knowledge_units WHERE id=p.unit_id;
                SELECT * INTO r FROM public.knowledge_unit_reviews
                    WHERE id=(item->'knowledge_evidence'->'reference'->>'review_id')::uuid;
                IF r.id IS NULL OR r.unit_id<>u.id THEN RETURN false; END IF;
                IF lock_sources THEN
                    IF NOT public.lock_projection_embedding_source(p.id,r.id)
                        THEN RETURN false; END IF;
                ELSIF NOT public.knowledge_projection_is_current(p.id)
                    OR NOT public.knowledge_unit_review_is_eligible(r.id) THEN RETURN false;
                END IF;
                reference:=jsonb_build_object('schema_version','knowledge-projection-reference.v1',
                    'projection_id',p.id::text,'projection_fingerprint',p.fingerprint,
                    'unit_id',u.id::text,'unit_fingerprint',u.fingerprint,
                    'trusted_page_id',u.trusted_page_id::text,
                    'trusted_fingerprint',u.payload->>'trusted_fingerprint','review_id',r.id::text,
                    'review_fingerprint',r.fingerprint,'review_version',r.version);
                evidence:=jsonb_build_object('schema_version','knowledge-evidence.v1',
                    'unit',u.payload,'reference',reference);
                expected_provenance:=jsonb_build_object('source_document_id',u.document_id::text,
                    'source_version','sha256:'||(u.payload->'source'->>'source_sha256'),
                    'page_number',u.page_number,'chunk_id',p.id::text,'source_block_id',NULL,
                    'source_candidate_id',NULL,'source_candidate_sha256',NULL);
                IF item->'knowledge_evidence' IS DISTINCT FROM evidence
                    OR item->>'text' IS DISTINCT FROM p.text
                    OR jsonb_typeof(item->'record_version') IS DISTINCT FROM 'number'
                    OR item->>'record_version' IS DISTINCT FROM r.version::text
                    OR item->'provenance' IS DISTINCT FROM expected_provenance
                THEN RETURN false; END IF;
                SELECT * INTO cv FROM public.curriculum_versions WHERE id=u.curriculum_version_id;
                SELECT * INTO exam FROM public.exam_configurations
                    WHERE id=cv.exam_configuration_id;
                expected_scope:=jsonb_build_object('curriculum_version_id',cv.id::text,
                    'exam_id',exam.id::text,'grade',exam.grade,
                    'medium_id',cv.medium_id::text,'subject_id',cv.subject_id::text,
                    'unit_ids',CASE WHEN r.curriculum_unit_id IS NULL THEN '[]'::jsonb
                        ELSE jsonb_build_array(r.curriculum_unit_id::text) END,
                    'lesson_ids',CASE WHEN r.lesson_id IS NULL THEN '[]'::jsonb
                        ELSE jsonb_build_array(r.lesson_id::text) END,
                    'taxonomy',jsonb_build_object('competency_id',r.competency_id::text,
                        'skill_id',r.skill_id::text,'sub_skill_id',r.sub_skill_id::text,
                        'learning_concept_id',r.learning_concept_id::text));
                IF item->'retrieval_scope' IS DISTINCT FROM expected_scope
                    OR item->'taxonomy' IS DISTINCT FROM expected_scope->'taxonomy'
                    OR item->'learning_scope' IS DISTINCT FROM jsonb_build_object(
                        'unit_id',r.curriculum_unit_id::text,'lesson_id',r.lesson_id::text)
                THEN RETURN false; END IF;
                allowed:=false;
                IF filters->>'kind'='scope' THEN
                    allowed:=(filters->'scope'->>'curriculum_version_id'=curriculum_id::text)
                        AND public.knowledge_generation_scope_matches(
                            filters->'scope',expected_scope);
                END IF;
                IF allowed IS NOT TRUE THEN RETURN false; END IF;
            END LOOP;
            RETURN true;
        EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN RETURN false;
        END; $$;
    """)
    _lineage_triggers(knowledge=True)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.generation_runs
                WHERE context_snapshot ? 'knowledge_projection_ids')
            THEN RAISE EXCEPTION 'cannot discard structured knowledge generation history'
                USING ERRCODE='23514'; END IF;
        END; $$;
    """)
    _lineage_triggers(knowledge=False)
    _constraints(knowledge=False)
    op.execute(
        "DROP FUNCTION public.generation_knowledge_context_is_current"
        "(uuid,jsonb,jsonb,jsonb,boolean)"
    )
    op.execute("DROP FUNCTION public.knowledge_generation_scope_matches(jsonb,jsonb)")
