from alembic import op

revision = "0051_programme_eval_replay"
down_revision = "0050_programme_knowledge_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_subject_quality_eval_runs_runner", "subject_quality_eval_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_subject_quality_eval_runs_runner",
        "subject_quality_eval_runs",
        "runner_version IN ('subject-quality-eval-runner.v1','subject-quality-eval-runner.v2')",
    )
    op.execute("""
        CREATE FUNCTION public.enforce_subject_quality_programme_replay()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE g public.generation_runs%ROWTYPE;
            v public.validation_runs%ROWTYPE;
            p public.assessment_programme_policy_versions%ROWTYPE;
            expected jsonb; scopes jsonb; field_name text;
        BEGIN
            SELECT * INTO g FROM public.generation_runs WHERE id=NEW.generation_run_id;
            IF g.id IS NULL THEN
                RAISE EXCEPTION 'programme replay generation is absent' USING ERRCODE='23514';
            END IF;
            IF NOT (g.context_snapshot ? 'programme_binding') THEN
                IF NEW.replay_input_snapshot ? 'programme_context' THEN
                    RAISE EXCEPTION 'programme replay cannot invent a binding'
                        USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            SELECT * INTO v FROM public.validation_runs WHERE id=NEW.validation_run_id;
            SELECT * INTO p FROM public.assessment_programme_policy_versions
                WHERE id=(g.context_snapshot->'programme_binding'->>'policy_id')::uuid;
            IF v.id IS NULL OR v.generation_run_id<>g.id OR p.id IS NULL
                OR p.state NOT IN ('reviewed','retired')
                OR p.content_hash IS DISTINCT FROM
                    g.context_snapshot->'programme_binding'->>'policy_content_hash'
                OR p.content_hash IS DISTINCT FROM
                    public.source_understanding_fingerprint(p.review_snapshot)
            THEN RAISE EXCEPTION 'programme replay requires retained reviewed policy evidence'
                USING ERRCODE='23514'; END IF;
            SELECT jsonb_object_agg(item->>'context_id',item->'retrieval_scope') INTO scopes
                FROM jsonb_array_elements(g.context_snapshot->'items') item;
            expected:=jsonb_build_object('schema_version','programme-replay-evidence.v1',
                'generation_run_id',g.id::text,'medium',jsonb_build_object(
                    'id',NEW.medium_id::text,'code',NEW.medium_code),
                'binding',g.context_snapshot->'programme_binding','policy_snapshot',p.review_snapshot,
                'slot',g.blueprint_slot_snapshot,'filters',g.context_snapshot->'retrieval_filters',
                'sources',scopes);
            IF NEW.replay_input_snapshot->'programme_context' IS DISTINCT FROM expected THEN
                RAISE EXCEPTION
                    'programme replay must preserve its exact generation policy evidence'
                    USING ERRCODE='23514';
            END IF;
            FOREACH field_name IN ARRAY ARRAY['blueprint','subject_scope','generated_scope',
                'context_scope_bindings','grounding_sources','duplicate_references','generation']
            LOOP
                IF NEW.replay_input_snapshot->field_name IS DISTINCT FROM
                    v.input_snapshot->field_name THEN
                    RAISE EXCEPTION 'programme replay must preserve validated source evidence'
                        USING ERRCODE='23514';
                END IF;
            END LOOP;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER enforce_subject_quality_programme_replay_trigger
            BEFORE INSERT ON public.subject_quality_feedback FOR EACH ROW
            EXECUTE FUNCTION public.enforce_subject_quality_programme_replay();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.subject_quality_feedback
                WHERE replay_input_snapshot ? 'programme_context')
                OR EXISTS (SELECT 1 FROM public.subject_quality_eval_runs
                    WHERE runner_version='subject-quality-eval-runner.v2')
            THEN RAISE EXCEPTION 'cannot discard programme evaluation replay history'
                USING ERRCODE='23514'; END IF;
        END; $$;
    """)
    op.execute(
        "DROP TRIGGER enforce_subject_quality_programme_replay_trigger "
        "ON public.subject_quality_feedback"
    )
    op.execute("DROP FUNCTION public.enforce_subject_quality_programme_replay()")
    op.drop_constraint(
        "ck_subject_quality_eval_runs_runner", "subject_quality_eval_runs", type_="check"
    )
    op.create_check_constraint(
        "ck_subject_quality_eval_runs_runner",
        "subject_quality_eval_runs",
        "runner_version = 'subject-quality-eval-runner.v1'",
    )
