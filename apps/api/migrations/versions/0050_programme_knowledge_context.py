import sqlalchemy as sa
from alembic import op

revision = "0050_programme_knowledge_context"
down_revision = "0049_knowledge_generation"
branch_labels = None
depends_on = None


def _lineage_triggers(*, programme: bool) -> None:
    context = {
        "predicate": "generation_programme_context_is_current"
        if programme
        else "generation_knowledge_context_is_current",
        "new_slot": ",NEW.blueprint_slot_snapshot" if programme else "",
        "run_slot": ",run.blueprint_slot_snapshot" if programme else "",
    }
    for statement in (
        """
        CREATE OR REPLACE FUNCTION public.enforce_generation_verified_lineage()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' OR NEW.status='succeeded' THEN
                IF NOT public.%(predicate)s(NEW.curriculum_version_id,NEW.knowledge_chunk_ids,
                    NEW.historical_question_ids,NEW.context_snapshot%(new_slot)s,true)
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
            IF NOT FOUND OR NOT public.%(predicate)s(run.curriculum_version_id,
                run.knowledge_chunk_ids,run.historical_question_ids,
                run.context_snapshot%(run_slot)s,true)
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
                IF NOT public.%(predicate)s(run.curriculum_version_id,run.knowledge_chunk_ids,
                    run.historical_question_ids,run.context_snapshot%(run_slot)s,true)
                THEN RAISE EXCEPTION 'publication requires current verified knowledge lineage'
                    USING ERRCODE='23514'; END IF;
            END LOOP;
            IF NOT found_run THEN RAISE EXCEPTION 'publication requires verified generation lineage'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
        """,
    ):
        op.execute(sa.DDL(statement, context=context))


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.programme_source_scope(s public.assessment_programme_policy_scopes)
        RETURNS jsonb LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT jsonb_build_object(
                'curriculum_version_id',(s).source_curriculum_version_id::text,
                'grade',(s).source_grade,'exam_id',(s).source_exam_configuration_id::text,
                'medium_id',(s).source_medium_id::text,'subject_id',(s).source_subject_id::text,
                'unit_ids',CASE WHEN (s).source_unit_id IS NULL THEN '[]'::jsonb
                    ELSE jsonb_build_array((s).source_unit_id::text) END,
                'lesson_ids',CASE WHEN (s).source_lesson_id IS NULL THEN '[]'::jsonb
                    ELSE jsonb_build_array((s).source_lesson_id::text) END,
                'taxonomy',jsonb_build_object('competency_id',(s).source_competency_id::text,
                    'skill_id',(s).source_skill_id::text,'sub_skill_id',(s).source_sub_skill_id::text,
                    'learning_concept_id',(s).source_learning_concept_id::text));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.generation_legacy_record_scope(kind text, selected uuid)
        RETURNS jsonb LANGUAGE sql STABLE AS $$
            WITH source AS (
                SELECT curriculum_version_id,unit_id,lesson_id,competency_id,skill_id,
                    sub_skill_id,learning_concept_id FROM public.knowledge_chunks
                    WHERE kind='knowledge_chunk' AND id=selected
                UNION ALL
                SELECT curriculum_version_id,unit_id,lesson_id,competency_id,skill_id,
                    sub_skill_id,learning_concept_id FROM public.historical_questions
                    WHERE kind='historical_question' AND id=selected
            )
            SELECT jsonb_build_object('curriculum_version_id',cv.id::text,'grade',e.grade,
                'exam_id',e.id::text,'medium_id',cv.medium_id::text,'subject_id',cv.subject_id::text,
                'unit_ids',CASE WHEN s.unit_id IS NULL THEN '[]'::jsonb
                    ELSE jsonb_build_array(s.unit_id::text) END,
                'lesson_ids',CASE WHEN s.lesson_id IS NULL THEN '[]'::jsonb
                    ELSE jsonb_build_array(s.lesson_id::text) END,
                'taxonomy',jsonb_build_object('competency_id',s.competency_id::text,
                    'skill_id',s.skill_id::text,'sub_skill_id',s.sub_skill_id::text,
                    'learning_concept_id',s.learning_concept_id::text))
            FROM source s JOIN public.curriculum_versions cv ON cv.id=s.curriculum_version_id
                JOIN public.exam_configurations e ON e.id=cv.exam_configuration_id;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.programme_learning_scope_is_current(
            curriculum_id uuid,unit_id uuid,lesson_id uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT (unit_id IS NULL AND lesson_id IS NULL) OR EXISTS (
                SELECT 1 FROM public.curriculum_units u WHERE u.id=unit_id
                    AND u.curriculum_version_id=curriculum_id AND u.active
                    AND (lesson_id IS NULL OR EXISTS (SELECT 1 FROM public.curriculum_lessons l
                        WHERE l.id=lesson_id AND l.unit_id=u.id
                            AND l.curriculum_version_id=curriculum_id AND l.active)));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.programme_taxonomy_path_is_current(
            curriculum_id uuid,competency uuid,skill uuid,sub_skill uuid,concept uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.taxonomy_nodes c
                LEFT JOIN public.taxonomy_nodes s ON s.id=skill
                LEFT JOIN public.taxonomy_nodes ss ON ss.id=sub_skill
                LEFT JOIN public.taxonomy_nodes lc ON lc.id=concept
                WHERE c.id=competency AND c.curriculum_version_id=curriculum_id
                    AND c.level='competency' AND c.active AND c.review_state='reviewed'
                    AND (skill IS NULL OR (s.curriculum_version_id=curriculum_id
                        AND s.level='skill' AND s.parent_id=c.id
                        AND s.active AND s.review_state='reviewed'))
                    AND (sub_skill IS NULL OR (skill IS NOT NULL
                        AND ss.curriculum_version_id=curriculum_id
                        AND ss.level='sub_skill' AND ss.parent_id=s.id
                        AND ss.active AND ss.review_state='reviewed'))
                    AND (concept IS NULL OR (sub_skill IS NOT NULL
                        AND lc.curriculum_version_id=curriculum_id
                        AND lc.level='learning_concept' AND lc.parent_id=ss.id
                        AND lc.active AND lc.review_state='reviewed')));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.bound_programme_scopes(curriculum_id uuid,binding jsonb,
            filters jsonb,slot jsonb,lock_sources boolean)
        RETURNS jsonb LANGUAGE plpgsql AS $$
        DECLARE p public.assessment_programme_policy_versions%ROWTYPE;
            m public.assessment_programme_policy_scopes%ROWTYPE;
            first_mapping public.assessment_programme_policy_scopes%ROWTYPE;
            cv public.curriculum_versions%ROWTYPE; exam public.exam_configurations%ROWTYPE;
            ids jsonb; expected_ids jsonb; expected_scopes jsonb:='[]'::jsonb;
            source_scope jsonb; anchor jsonb; target jsonb; label text; policy_version text;
            selected_count integer:=0; total_count integer; expected_scope_snapshot jsonb;
            scope_snapshot jsonb; wanted_nodes uuid[];
        BEGIN
            IF jsonb_typeof(binding) IS DISTINCT FROM 'object'
                OR NOT (binding ?& ARRAY[
                    'schema_version','policy_id','policy_content_hash','scope_ids'])
                OR binding-ARRAY[
                    'schema_version','policy_id','policy_content_hash','scope_ids']<>'{}'::jsonb
                OR binding->>'schema_version' IS DISTINCT FROM 'knowledge-programme-binding.v1'
                OR jsonb_typeof(binding->'policy_id') IS DISTINCT FROM 'string'
                OR jsonb_typeof(binding->'policy_content_hash') IS DISTINCT FROM 'string'
                OR NOT public.generation_uuid_array_valid(binding->'scope_ids',64)
                OR jsonb_array_length(binding->'scope_ids')=0
                OR jsonb_typeof(slot) IS DISTINCT FROM 'object'
            THEN RETURN NULL; END IF;
            ids:=binding->'scope_ids';
            IF lock_sources THEN
                PERFORM public.lock_knowledge_source_lineage(NULL,NULL,curriculum_id);
                SELECT * INTO p FROM public.assessment_programme_policy_versions
                    WHERE id=(binding->>'policy_id')::uuid FOR SHARE;
            ELSE
                SELECT * INTO p FROM public.assessment_programme_policy_versions
                    WHERE id=(binding->>'policy_id')::uuid;
            END IF;
            SELECT * INTO cv FROM public.curriculum_versions WHERE id=curriculum_id;
            SELECT * INTO exam FROM public.exam_configurations WHERE id=cv.exam_configuration_id;
            IF p.id IS NULL OR p.state<>'reviewed' OR p.anchor_curriculum_version_id<>curriculum_id
                OR binding->>'policy_id' IS DISTINCT FROM p.id::text
                OR p.content_hash IS DISTINCT FROM binding->>'policy_content_hash'
                OR p.content_hash IS DISTINCT FROM
                    public.source_understanding_fingerprint(p.review_snapshot)
                OR p.review_snapshot->>'id' IS DISTINCT FROM p.id::text
                OR p.review_snapshot->>'version' IS DISTINCT FROM p.version
                OR p.programme_exam_configuration_id<>exam.id OR p.medium_id<>cv.medium_id
                OR NOT public.catalogue_curriculum_is_admitted(curriculum_id)
            THEN RETURN NULL; END IF;
            SELECT count(*) INTO total_count FROM public.assessment_programme_policy_scopes
                WHERE policy_version_id=p.id;
            IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events a
                WHERE a.resource_id=p.id AND a.resource_type='assessment_programme_policy'
                    AND a.action='assessment_programme_policy.reviewed'
                    AND a.actor_id=p.reviewed_by AND a.payload->>'content_hash'=p.content_hash
                    AND a.payload->'scope_count'=to_jsonb(total_count)) THEN RETURN NULL; END IF;
            anchor:=slot->'generation_constraints'->'curriculum_scope';
            target:=slot->'taxonomy_target';
            IF anchor->>'curriculum_version_id' IS DISTINCT FROM curriculum_id::text
                OR anchor->'grade' IS DISTINCT FROM to_jsonb(exam.grade)
                OR anchor->>'grade' IS DISTINCT FROM exam.grade::text
                OR anchor->>'subject_id' IS DISTINCT FROM cv.subject_id::text
                OR NOT EXISTS (SELECT 1 FROM public.media WHERE id=cv.medium_id
                    AND code=anchor->>'medium' AND active)
                OR NOT public.generation_uuid_array_valid(anchor->'unit_ids',256)
                OR NOT public.generation_uuid_array_valid(anchor->'lesson_ids',256)
            THEN RETURN NULL; END IF;
            FOR m IN SELECT * FROM public.assessment_programme_policy_scopes
                WHERE policy_version_id=p.id AND ids ? id::text ORDER BY part,ordinal
            LOOP
                selected_count:=selected_count+1;
                IF selected_count=1 THEN first_mapping:=m; END IF;
                IF lock_sources THEN
                    PERFORM public.lock_knowledge_source_lineage(
                        NULL,NULL,m.source_curriculum_version_id);
                    PERFORM public.lock_knowledge_review_taxonomy(
                        m.anchor_unit_id,m.anchor_lesson_id,m.anchor_competency_id,
                        m.anchor_skill_id,m.anchor_sub_skill_id,m.anchor_learning_concept_id);
                    PERFORM public.lock_knowledge_review_taxonomy(
                        m.source_unit_id,m.source_lesson_id,m.source_competency_id,
                        m.source_skill_id,m.source_sub_skill_id,m.source_learning_concept_id);
                END IF;
                IF m.anchor_curriculum_version_id<>curriculum_id
                    OR m.anchor_lesson_id<>first_mapping.anchor_lesson_id
                    OR m.part<>first_mapping.part
                    OR slot->>'section_id' IS DISTINCT FROM m.part||'-'||(slot->>'question_type')
                    OR NOT (anchor->'unit_ids' ? m.anchor_unit_id::text)
                    OR NOT (anchor->'lesson_ids' ? m.anchor_lesson_id::text)
                    OR target IS DISTINCT FROM jsonb_build_object(
                        'competency_id',m.anchor_competency_id::text,'skill_id',m.anchor_skill_id::text,
                        'sub_skill_id',m.anchor_sub_skill_id::text,
                        'learning_concept_id',m.anchor_learning_concept_id::text)
                    OR NOT public.programme_learning_scope_is_current(curriculum_id,
                        m.anchor_unit_id,m.anchor_lesson_id)
                    OR NOT public.programme_taxonomy_path_is_current(curriculum_id,
                        m.anchor_competency_id,m.anchor_skill_id,
                        m.anchor_sub_skill_id,m.anchor_learning_concept_id)
                THEN RETURN NULL; END IF;
                source_scope:=public.programme_source_scope(m);
                IF NOT public.catalogue_curriculum_is_admitted(m.source_curriculum_version_id)
                    OR NOT public.programme_learning_scope_is_current(
                        m.source_curriculum_version_id,m.source_unit_id,m.source_lesson_id)
                    OR NOT public.programme_taxonomy_path_is_current(
                        m.source_curriculum_version_id,m.source_competency_id,m.source_skill_id,
                        m.source_sub_skill_id,m.source_learning_concept_id)
                    OR NOT EXISTS (SELECT 1 FROM public.curriculum_versions c
                        JOIN public.exam_configurations e ON e.id=c.exam_configuration_id
                        WHERE c.id=m.source_curriculum_version_id
                            AND e.id=m.source_exam_configuration_id
                            AND e.grade=m.source_grade AND c.medium_id=m.source_medium_id
                            AND c.subject_id=m.source_subject_id AND c.medium_id=p.medium_id)
                THEN RETURN NULL; END IF;
                IF NOT (expected_scopes @> jsonb_build_array(source_scope)) THEN
                    expected_scopes:=expected_scopes||jsonb_build_array(source_scope);
                END IF;
                expected_scope_snapshot:=jsonb_build_object('id',m.id::text,'part',m.part,
                    'ordinal',m.ordinal,'anchor',jsonb_build_object(
                        'curriculum_version_id',m.anchor_curriculum_version_id::text,
                        'unit_id',m.anchor_unit_id::text,'lesson_id',m.anchor_lesson_id::text,
                        'competency_id',m.anchor_competency_id::text,'skill_id',m.anchor_skill_id::text,
                        'sub_skill_id',m.anchor_sub_skill_id::text,
                        'learning_concept_id',m.anchor_learning_concept_id::text),
                    'source',jsonb_build_object('grade',m.source_grade,
                        'exam_configuration_id',m.source_exam_configuration_id::text,
                        'medium_id',m.source_medium_id::text,'subject_id',m.source_subject_id::text,
                        'curriculum_version_id',m.source_curriculum_version_id::text,
                        'unit_id',m.source_unit_id::text,'lesson_id',m.source_lesson_id::text,
                        'competency_id',m.source_competency_id::text,'skill_id',m.source_skill_id::text,
                        'sub_skill_id',m.source_sub_skill_id::text,
                        'learning_concept_id',m.source_learning_concept_id::text));
                SELECT value INTO scope_snapshot
                    FROM jsonb_array_elements(p.review_snapshot->'scopes')
                    WHERE value->>'id'=m.id::text;
                IF scope_snapshot IS DISTINCT FROM expected_scope_snapshot THEN RETURN NULL; END IF;
            END LOOP;
            IF selected_count<>jsonb_array_length(ids) OR selected_count=0 THEN RETURN NULL; END IF;
            SELECT jsonb_agg(id::text ORDER BY id) INTO expected_ids
                FROM public.assessment_programme_policy_scopes
                WHERE policy_version_id=p.id AND part=first_mapping.part
                    AND anchor_lesson_id=first_mapping.anchor_lesson_id
                    AND anchor_competency_id=first_mapping.anchor_competency_id
                    AND anchor_skill_id IS NOT DISTINCT FROM first_mapping.anchor_skill_id
                    AND anchor_sub_skill_id IS NOT DISTINCT FROM first_mapping.anchor_sub_skill_id
                    AND anchor_learning_concept_id IS NOT DISTINCT FROM
                        first_mapping.anchor_learning_concept_id;
            IF ids IS DISTINCT FROM expected_ids THEN RETURN NULL; END IF;
            wanted_nodes:=array_remove(ARRAY[first_mapping.anchor_competency_id,
                first_mapping.anchor_skill_id,first_mapping.anchor_sub_skill_id,
                first_mapping.anchor_learning_concept_id],NULL);
            IF lock_sources THEN
                PERFORM 1 FROM public.taxonomy_nodes
                    WHERE id=ANY(wanted_nodes) ORDER BY id FOR SHARE;
            END IF;
            SELECT string_agg(t.title,' / ' ORDER BY n.ordinality) INTO label
                FROM unnest(wanted_nodes) WITH ORDINALITY n(id,ordinality)
                JOIN public.taxonomy_nodes t ON t.id=n.id
                WHERE t.curriculum_version_id=curriculum_id
                    AND t.active AND t.review_state='reviewed';
            IF (SELECT count(*) FROM public.taxonomy_nodes WHERE id=ANY(wanted_nodes)
                AND curriculum_version_id=curriculum_id AND active AND review_state='reviewed')
                <>cardinality(wanted_nodes) THEN RETURN NULL; END IF;
            policy_version:='programme:'||public.source_understanding_fingerprint(jsonb_build_object(
                'policy_id',p.id::text,'policy_version',p.version,'content_hash',p.content_hash,
                'part',first_mapping.part,'anchor_target',label));
            IF filters IS DISTINCT FROM jsonb_build_object('kind','scope_set',
                'policy_version',policy_version,'scopes',expected_scopes) THEN RETURN NULL; END IF;
            RETURN expected_scopes;
        EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN RETURN NULL;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.generation_programme_context_is_current(curriculum_id uuid,
            chunk_ids jsonb,question_ids jsonb,snapshot jsonb,slot jsonb,
            lock_sources boolean DEFAULT false)
        RETURNS boolean LANGUAGE plpgsql AS $$
        DECLARE scopes jsonb; item jsonb; selected_scope jsonb; actual_scope jsonb;
            projection_ids jsonb; identifier uuid; kind text; identity text;
            seen text[]:=ARRAY[]::text[]; allowed boolean; source_curriculum uuid; document_id uuid;
        BEGIN
            IF NOT (snapshot ? 'programme_binding')
                AND snapshot->>'schema_version' IS DISTINCT FROM 'generation-knowledge-context.v2'
            THEN RETURN public.generation_knowledge_context_is_current(
                curriculum_id,chunk_ids,question_ids,snapshot,lock_sources); END IF;
            IF snapshot->>'schema_version' IS DISTINCT FROM 'generation-knowledge-context.v2'
                OR snapshot->>'trust' IS DISTINCT FROM 'untrusted_data'
                OR jsonb_typeof(snapshot->'items') IS DISTINCT FROM 'array'
                OR snapshot-ARRAY['schema_version','trust','items','knowledge_projection_ids',
                    'retrieval_filters','programme_binding']<>'{}'::jsonb
                OR NOT public.generation_uuid_array_valid(chunk_ids,16)
                OR NOT public.generation_uuid_array_valid(question_ids,16)
                OR NOT public.generation_uuid_array_valid(snapshot->'knowledge_projection_ids',16)
                OR jsonb_array_length(snapshot->'knowledge_projection_ids')=0
                OR octet_length(snapshot::text)>262144 THEN RETURN false; END IF;
            projection_ids:=snapshot->'knowledge_projection_ids';
            IF jsonb_array_length(snapshot->'items') NOT BETWEEN 1 AND 16
                OR jsonb_array_length(snapshot->'items')<>jsonb_array_length(chunk_ids)
                    +jsonb_array_length(question_ids)+jsonb_array_length(projection_ids)
            THEN RETURN false; END IF;
            scopes:=public.bound_programme_scopes(curriculum_id,snapshot->'programme_binding',
                snapshot->'retrieval_filters',slot,lock_sources);
            IF scopes IS NULL THEN RETURN false; END IF;
            IF lock_sources THEN
                FOR document_id IN SELECT DISTINCT u.document_id FROM public.knowledge_units u
                    JOIN public.knowledge_projections p ON p.unit_id=u.id
                    WHERE projection_ids ? p.id::text ORDER BY u.document_id
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
                actual_scope:=CASE WHEN kind='knowledge_projection' THEN item->'retrieval_scope'
                    ELSE public.generation_legacy_record_scope(kind,identifier) END;
                IF actual_scope IS NULL OR item->'retrieval_scope' IS DISTINCT FROM actual_scope
                    OR item->'taxonomy' IS DISTINCT FROM actual_scope->'taxonomy'
                    OR item->'learning_scope' IS DISTINCT FROM jsonb_build_object(
                        'unit_id',actual_scope->'unit_ids'->>0,
                        'lesson_id',actual_scope->'lesson_ids'->>0)
                THEN RETURN false; END IF;
                source_curriculum:=(actual_scope->>'curriculum_version_id')::uuid;
                allowed:=false;
                FOR selected_scope IN SELECT value FROM jsonb_array_elements(scopes) LOOP
                    allowed:=allowed OR public.knowledge_generation_scope_matches(
                        selected_scope,actual_scope);
                END LOOP;
                IF allowed IS NOT TRUE THEN RETURN false; END IF;
                IF kind='knowledge_projection' THEN
                    IF NOT (projection_ids ? identifier::text)
                        OR NOT public.generation_knowledge_context_is_current(source_curriculum,
                            '[]'::jsonb,'[]'::jsonb,jsonb_build_object(
                                'schema_version','generation-knowledge-context.v1',
                                'trust','untrusted_data','knowledge_projection_ids',jsonb_build_array(identifier::text),
                                'items',jsonb_build_array(item),'retrieval_filters',
                                    jsonb_build_object('kind','scope','scope',actual_scope)),lock_sources)
                    THEN RETURN false; END IF;
                ELSIF kind IN ('knowledge_chunk','historical_question') THEN
                    IF item ? 'knowledge_evidence'
                        OR (kind='knowledge_chunk' AND NOT (chunk_ids ? identifier::text))
                        OR (kind='historical_question' AND NOT (question_ids ? identifier::text))
                        OR NOT public.generation_context_lineage_is_current(source_curriculum,
                            CASE WHEN kind='knowledge_chunk'
                                THEN jsonb_build_array(identifier::text) ELSE '[]'::jsonb END,
                            CASE WHEN kind='historical_question'
                                THEN jsonb_build_array(identifier::text) ELSE '[]'::jsonb END,
                            jsonb_build_object('trust','untrusted_data','items',jsonb_build_array(item)),lock_sources)
                    THEN RETURN false; END IF;
                ELSE RETURN false;
                END IF;
            END LOOP;
            RETURN true;
        EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN RETURN false;
        END; $$;
    """)
    _lineage_triggers(programme=True)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.generation_runs
                WHERE context_snapshot ? 'programme_binding')
            THEN RAISE EXCEPTION 'cannot discard programme knowledge context history'
                USING ERRCODE='23514'; END IF;
        END; $$;
    """)
    _lineage_triggers(programme=False)
    op.execute(
        "DROP FUNCTION public.generation_programme_context_is_current"
        "(uuid,jsonb,jsonb,jsonb,jsonb,boolean)"
    )
    op.execute("DROP FUNCTION public.bound_programme_scopes(uuid,jsonb,jsonb,jsonb,boolean)")
    op.execute("DROP FUNCTION public.programme_learning_scope_is_current(uuid,uuid,uuid)")
    op.execute("DROP FUNCTION public.programme_taxonomy_path_is_current(uuid,uuid,uuid,uuid,uuid)")
    op.execute("DROP FUNCTION public.generation_legacy_record_scope(text,uuid)")
    op.execute(
        "DROP FUNCTION public.programme_source_scope(public.assessment_programme_policy_scopes)"
    )
