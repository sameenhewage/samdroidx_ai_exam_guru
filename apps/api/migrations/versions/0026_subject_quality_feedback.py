from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_subject_quality_feedback"
down_revision: str | None = "0025_durable_teacher_papers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_CONTENT_BYTES = 131_072
_MAX_FINDINGS_BYTES = 524_288
_MAX_SCOPE_BYTES = 32_768
_MAX_PROVENANCE_BYTES = 131_072
_MAX_REPLAY_INPUT_BYTES = 8_388_608
_MAX_NOTE_CHARACTERS = 768
_MAX_EXPECTED_CODES = 32
_FINGERPRINT = "^[s][h][a]256:[0-9a-f]{64}$"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION subject_quality_code_array_valid(value jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            seen text[] := ARRAY[]::text[];
            code text;
        BEGIN
            IF jsonb_typeof(value) <> 'array'
                OR jsonb_array_length(value) > {_MAX_EXPECTED_CODES}
            THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT jsonb_array_elements(value)
            LOOP
                IF jsonb_typeof(item) <> 'string' THEN
                    RETURN FALSE;
                END IF;
                code := item #>> '{{}}';
                IF code !~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'
                    OR char_length(code) NOT BETWEEN 1 AND 128
                    OR code = ANY(seen)
                THEN
                    RETURN FALSE;
                END IF;
                seen := array_append(seen, code);
            END LOOP;
            RETURN TRUE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_subject_quality_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'subject-quality evidence and eval history are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )

    op.create_table(
        "subject_quality_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("teacher_paper_job_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_paper_slot_id", sa.Uuid(), nullable=False),
        sa.Column("slot_version", sa.Integer(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("medium_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("medium_code", sa.String(32), nullable=False),
        sa.Column("subject_code", sa.String(64), nullable=False),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("review_event_version", sa.Integer(), nullable=True),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("generation_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("validation_run_id", sa.Uuid(), nullable=False),
        sa.Column("replacement_generation_run_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("note", sa.String(_MAX_NOTE_CHARACTERS), nullable=True),
        sa.Column("original_content_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("current_content_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("findings_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("scope_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("provenance_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("replay_input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("prompt_version", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("retrieval_version", sa.String(128), nullable=False),
        sa.Column("validator_versions", postgresql.JSONB(), nullable=False),
        sa.Column("original_content_fingerprint", sa.String(71), nullable=False),
        sa.Column("current_content_fingerprint", sa.String(71), nullable=False),
        sa.Column("findings_fingerprint", sa.String(71), nullable=False),
        sa.Column("scope_fingerprint", sa.String(71), nullable=False),
        sa.Column("provenance_fingerprint", sa.String(71), nullable=False),
        sa.Column("feedback_fingerprint", sa.String(71), nullable=False),
        sa.Column("action_fingerprint", sa.String(71), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(71), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["teacher_paper_job_id", "curriculum_version_id"],
            ["teacher_paper_jobs.id", "teacher_paper_jobs.curriculum_version_id"],
            name="fk_subject_quality_feedback_job_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_paper_slot_id", "teacher_paper_job_id"],
            ["teacher_paper_slots.id", "teacher_paper_slots.paper_job_id"],
            name="fk_subject_quality_feedback_slot_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_subject_quality_feedback_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "candidate_revision"],
            ["question_candidate_revisions.candidate_id", "question_candidate_revisions.revision"],
            name="fk_subject_quality_feedback_candidate_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "review_event_version"],
            ["candidate_review_events.candidate_id", "candidate_review_events.candidate_version"],
            name="fk_subject_quality_feedback_review_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_subject_quality_feedback_generation_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_attempt_id", "generation_run_id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_subject_quality_feedback_generation_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "validation_run_id",
                "generation_run_id",
                "generation_attempt_id",
                "curriculum_version_id",
            ],
            [
                "validation_runs.id",
                "validation_runs.generation_run_id",
                "validation_runs.generation_attempt_id",
                "validation_runs.curriculum_version_id",
            ],
            name="fk_subject_quality_feedback_validation_lineage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_subject_quality_feedback_replacement_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medium_id"],
            ["media.id"],
            name="fk_subject_quality_feedback_medium",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_subject_quality_feedback_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_subject_quality_feedback_unit_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_subject_quality_feedback_lesson_scope",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("action_fingerprint", name="uq_subject_quality_feedback_action"),
        sa.UniqueConstraint("feedback_fingerprint", name="uq_subject_quality_feedback_fingerprint"),
        sa.CheckConstraint(
            "schema_version = 'subject-quality-feedback.v1'",
            name="ck_subject_quality_feedback_schema",
        ),
        sa.CheckConstraint(
            "action IN ('edit', 'reject', 'regenerate', 'approve')",
            name="ck_subject_quality_feedback_action",
        ),
        sa.CheckConstraint(
            "reason_code IN ('answer_incorrect', 'ambiguous_wording', 'outside_scope', "
            "'source_not_supported', 'marking_inconsistent', 'language_quality', "
            "'distractor_quality', 'duplicate_content', 'unsafe_content', "
            "'other_quality_issue', 'confirmed_quality')",
            name="ck_subject_quality_feedback_reason_code",
        ),
        sa.CheckConstraint(
            "(action = 'approve' AND reason_code = 'confirmed_quality' AND note IS NOT NULL) OR "
            "(action <> 'approve' AND reason_code <> 'confirmed_quality')",
            name="ck_subject_quality_feedback_approval_meaningful",
        ),
        sa.CheckConstraint(
            "note IS NULL OR (note = btrim(note) AND "
            f"char_length(note) BETWEEN 1 AND {_MAX_NOTE_CHARACTERS})",
            name="ck_subject_quality_feedback_note",
        ),
        sa.CheckConstraint(
            "grade BETWEEN 1 AND 13 AND lesson_number BETWEEN 1 AND 10000 AND "
            "candidate_revision BETWEEN 1 AND 32 AND candidate_version BETWEEN 2 AND 35 "
            "AND slot_version BETWEEN 0 AND 100000",
            name="ck_subject_quality_feedback_bounds",
        ),
        sa.CheckConstraint(
            "medium_code = btrim(medium_code) AND char_length(medium_code) BETWEEN 1 AND 32 AND "
            "subject_code = btrim(subject_code) AND char_length(subject_code) BETWEEN 1 AND 64",
            name="ck_subject_quality_feedback_codes",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} = btrim({column_name}) AND "
                f"char_length({column_name}) BETWEEN 1 AND 128",
                name=f"ck_subject_quality_feedback_{column_name}",
            )
            for column_name in (
                "prompt_version",
                "provider",
                "provider_version",
                "model",
                "model_version",
                "retrieval_version",
            )
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT}'",
                name=f"ck_subject_quality_feedback_{column_name}",
            )
            for column_name in (
                "original_content_fingerprint",
                "current_content_fingerprint",
                "findings_fingerprint",
                "scope_fingerprint",
                "provenance_fingerprint",
                "feedback_fingerprint",
                "action_fingerprint",
            )
        ),
        sa.CheckConstraint(
            f"idempotency_key_hash IS NULL OR idempotency_key_hash ~ '{_FINGERPRINT}'",
            name="ck_subject_quality_feedback_idempotency",
        ),
        sa.CheckConstraint(
            "review_candidate_content_valid(original_content_snapshot) AND "
            f"pg_column_size(original_content_snapshot) <= {_MAX_CONTENT_BYTES} AND "
            "review_candidate_content_valid(current_content_snapshot) AND "
            f"pg_column_size(current_content_snapshot) <= {_MAX_CONTENT_BYTES}",
            name="ck_subject_quality_feedback_content",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(findings_snapshot) = 'object' AND "
            "jsonb_typeof(findings_snapshot->'findings') = 'array' AND "
            f"pg_column_size(findings_snapshot) <= {_MAX_FINDINGS_BYTES}",
            name="ck_subject_quality_feedback_findings",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'object' AND "
            f"pg_column_size(scope_snapshot) <= {_MAX_SCOPE_BYTES}",
            name="ck_subject_quality_feedback_scope",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(provenance_snapshot) = 'array' AND "
            f"pg_column_size(provenance_snapshot) <= {_MAX_PROVENANCE_BYTES}",
            name="ck_subject_quality_feedback_provenance",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(replay_input_snapshot) = 'object' AND "
            "replay_input_snapshot->>'schema_version' = 'subject-quality-eval-input.v1' AND "
            f"pg_column_size(replay_input_snapshot) <= {_MAX_REPLAY_INPUT_BYTES}",
            name="ck_subject_quality_feedback_replay_input",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validator_versions) = 'array' AND "
            "jsonb_array_length(validator_versions) BETWEEN 1 AND 32",
            name="ck_subject_quality_feedback_validator_versions",
        ),
    )
    op.create_index(
        "ix_subject_quality_feedback_created",
        "subject_quality_feedback",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_subject_quality_feedback_candidate_created",
        "subject_quality_feedback",
        ["candidate_id", "created_at", "id"],
    )
    op.create_index(
        "ix_subject_quality_feedback_curriculum_created",
        "subject_quality_feedback",
        ["curriculum_version_id", "created_at", "id"],
    )

    op.create_table(
        "subject_quality_eval_case_versions",
        sa.Column("eval_case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("source_feedback_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("expected_status", sa.String(8), nullable=False),
        sa.Column("expected_finding_codes", postgresql.JSONB(), nullable=False),
        sa.Column("defect_category", sa.String(64), nullable=False),
        sa.Column("replay_input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("subject_scope_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("case_fingerprint", sa.String(71), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(71), nullable=False),
        sa.Column("promotion_request_fingerprint", sa.String(71), nullable=False),
        sa.Column("promoted_by", sa.Uuid(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "eval_case_id", "version", name="pk_subject_quality_eval_case_versions"
        ),
        sa.ForeignKeyConstraint(
            ["source_feedback_id"],
            ["subject_quality_feedback.id"],
            name="fk_subject_quality_eval_case_feedback",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id", "previous_version"],
            [
                "subject_quality_eval_case_versions.eval_case_id",
                "subject_quality_eval_case_versions.version",
            ],
            name="fk_subject_quality_eval_case_previous",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "eval_case_id",
            "version",
            "source_feedback_id",
            name="uq_subject_quality_eval_case_source_version",
        ),
        sa.CheckConstraint("version IN (1, 2)", name="ck_subject_quality_eval_case_version"),
        sa.CheckConstraint(
            "state IN ('draft', 'approved') AND expected_status IN ('pass', 'warn', 'fail')",
            name="ck_subject_quality_eval_case_states",
        ),
        sa.CheckConstraint(
            "subject_quality_code_array_valid(expected_finding_codes) AND "
            "((expected_status = 'pass' AND jsonb_array_length(expected_finding_codes) = 0) OR "
            "(expected_status <> 'pass' AND jsonb_array_length(expected_finding_codes) >= 1))",
            name="ck_subject_quality_eval_case_expected_codes",
        ),
        sa.CheckConstraint(
            "defect_category IN ('no_defect', 'answer_correctness', "
            "'multiple_correct_answers', 'marking_consistency', 'scope_alignment', "
            "'source_grounding', 'language_clarity', 'distractor_quality', "
            "'duplicate_content', 'security_residue', 'other')",
            name="ck_subject_quality_eval_case_defect_category",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(replay_input_snapshot) = 'object' AND "
            "replay_input_snapshot->>'schema_version' = 'subject-quality-eval-input.v1' AND "
            f"pg_column_size(replay_input_snapshot) <= {_MAX_REPLAY_INPUT_BYTES}",
            name="ck_subject_quality_eval_case_replay_input",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(subject_scope_snapshot) = 'object' AND "
            f"pg_column_size(subject_scope_snapshot) <= {_MAX_SCOPE_BYTES}",
            name="ck_subject_quality_eval_case_scope",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT}'",
                name=f"ck_subject_quality_eval_case_{column_name}",
            )
            for column_name in (
                "case_fingerprint",
                "idempotency_key_hash",
                "promotion_request_fingerprint",
            )
        ),
        sa.CheckConstraint(
            "(version = 1 AND previous_version IS NULL AND state = 'draft' AND "
            "approved_by IS NULL AND approved_at IS NULL) OR "
            "(version = 2 AND previous_version = 1 AND state = 'approved' AND "
            "approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by <> promoted_by)",
            name="ck_subject_quality_eval_case_lifecycle",
        ),
    )
    op.create_index(
        "uq_subject_quality_eval_case_feedback_promotion",
        "subject_quality_eval_case_versions",
        ["source_feedback_id"],
        unique=True,
        postgresql_where=sa.text("version = 1"),
    )
    op.create_index(
        "uq_subject_quality_eval_case_actor_idempotency",
        "subject_quality_eval_case_versions",
        ["promoted_by", "idempotency_key_hash"],
        unique=True,
        postgresql_where=sa.text("version = 1"),
    )
    op.create_index(
        "ix_subject_quality_eval_case_state_created",
        "subject_quality_eval_case_versions",
        ["state", "created_at", "eval_case_id"],
    )

    op.create_table(
        "subject_quality_eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("runner_version", sa.String(64), nullable=False),
        sa.Column("pipeline_version", sa.String(128), nullable=False),
        sa.Column("pipeline_fingerprint", sa.String(71), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("regression_count", sa.Integer(), nullable=False),
        sa.Column("unavailable_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("request_fingerprint", name="uq_subject_quality_eval_runs_request"),
        sa.CheckConstraint(
            "runner_version = 'subject-quality-eval-runner.v1'",
            name="ck_subject_quality_eval_runs_runner",
        ),
        sa.CheckConstraint(
            f"pipeline_fingerprint ~ '{_FINGERPRINT}' AND request_fingerprint ~ '{_FINGERPRINT}'",
            name="ck_subject_quality_eval_runs_fingerprints",
        ),
        sa.CheckConstraint(
            "pipeline_version = btrim(pipeline_version) AND "
            "char_length(pipeline_version) BETWEEN 1 AND 128",
            name="ck_subject_quality_eval_runs_pipeline",
        ),
        sa.CheckConstraint(
            "case_count BETWEEN 1 AND 100 AND passed_count BETWEEN 0 AND case_count AND "
            "regression_count BETWEEN 0 AND case_count AND "
            "unavailable_count BETWEEN 0 AND case_count AND "
            "passed_count + regression_count + unavailable_count = case_count",
            name="ck_subject_quality_eval_runs_counts",
        ),
    )
    op.create_index(
        "ix_subject_quality_eval_runs_created",
        "subject_quality_eval_runs",
        ["created_at", "id"],
    )

    op.create_table(
        "subject_quality_eval_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("eval_run_id", sa.Uuid(), nullable=False),
        sa.Column("eval_case_id", sa.Uuid(), nullable=False),
        sa.Column("eval_case_version", sa.Integer(), nullable=False),
        sa.Column("expected_status", sa.String(8), nullable=False),
        sa.Column("expected_finding_codes", postgresql.JSONB(), nullable=False),
        sa.Column("actual_status", sa.String(8), nullable=False),
        sa.Column("actual_finding_codes", postgresql.JSONB(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("pipeline_version", sa.String(128), nullable=False),
        sa.Column("pipeline_fingerprint", sa.String(71), nullable=False),
        sa.Column("validator_versions", postgresql.JSONB(), nullable=False),
        sa.Column("report_fingerprint", sa.String(71), nullable=False),
        sa.Column("result_fingerprint", sa.String(71), nullable=False),
        sa.Column("report_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["subject_quality_eval_runs.id"],
            name="fk_subject_quality_eval_results_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id", "eval_case_version"],
            [
                "subject_quality_eval_case_versions.eval_case_id",
                "subject_quality_eval_case_versions.version",
            ],
            name="fk_subject_quality_eval_results_case",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "eval_run_id", "eval_case_id", name="uq_subject_quality_eval_results_run_case"
        ),
        sa.UniqueConstraint(
            "result_fingerprint",
            name="uq_subject_quality_eval_results_fingerprint",
        ),
        sa.CheckConstraint(
            "eval_case_version = 2 AND expected_status IN ('pass', 'warn', 'fail') AND "
            "actual_status IN ('pass', 'warn', 'fail') AND "
            "outcome IN ('pass', 'regression', 'unavailable')",
            name="ck_subject_quality_eval_results_states",
        ),
        sa.CheckConstraint(
            "passed = (outcome = 'pass')",
            name="ck_subject_quality_eval_results_passed",
        ),
        sa.CheckConstraint(
            "subject_quality_code_array_valid(expected_finding_codes) AND "
            "subject_quality_code_array_valid(actual_finding_codes)",
            name="ck_subject_quality_eval_results_codes",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT}'",
                name=f"ck_subject_quality_eval_results_{column_name}",
            )
            for column_name in (
                "pipeline_fingerprint",
                "report_fingerprint",
                "result_fingerprint",
            )
        ),
        sa.CheckConstraint(
            "pipeline_version = btrim(pipeline_version) AND "
            "char_length(pipeline_version) BETWEEN 1 AND 128 AND "
            "jsonb_typeof(validator_versions) = 'array' AND "
            "jsonb_array_length(validator_versions) BETWEEN 1 AND 32",
            name="ck_subject_quality_eval_results_lineage",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(report_snapshot) = 'object' AND "
            "pg_column_size(report_snapshot) <= 524288",
            name="ck_subject_quality_eval_results_report",
        ),
    )
    op.create_index(
        "ix_subject_quality_eval_results_run_case",
        "subject_quality_eval_results",
        ["eval_run_id", "eval_case_id"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_subject_quality_feedback_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            candidate_row question_candidates%ROWTYPE;
            original_content jsonb;
            current_content jsonb;
            generation_row generation_runs%ROWTYPE;
            validation_row validation_runs%ROWTYPE;
            slot_row teacher_paper_slots%ROWTYPE;
            resolved_grade integer;
            resolved_medium text;
            resolved_subject text;
            expected_provenance jsonb;
            event_action text;
            event_actor uuid;
            event_revision integer;
        BEGIN
            SELECT * INTO candidate_row FROM question_candidates WHERE id = NEW.candidate_id;
            SELECT content INTO original_content FROM question_candidate_revisions
                WHERE candidate_id = NEW.candidate_id AND revision = 1;
            SELECT content INTO current_content FROM question_candidate_revisions
                WHERE candidate_id = NEW.candidate_id AND revision = NEW.candidate_revision;
            SELECT * INTO generation_row FROM generation_runs WHERE id = NEW.generation_run_id;
            SELECT * INTO validation_row FROM validation_runs WHERE id = NEW.validation_run_id;
            SELECT * INTO slot_row FROM teacher_paper_slots
                WHERE id = NEW.teacher_paper_slot_id AND paper_job_id = NEW.teacher_paper_job_id;
            SELECT exam.grade, medium.code, subject.code
            INTO resolved_grade, resolved_medium, resolved_subject
            FROM curriculum_versions AS curriculum
            JOIN exam_configurations AS exam ON exam.id = curriculum.exam_configuration_id
            JOIN media AS medium ON medium.id = curriculum.medium_id
            JOIN subjects AS subject ON subject.id = curriculum.subject_id
            WHERE curriculum.id = NEW.curriculum_version_id
                AND curriculum.medium_id = NEW.medium_id
                AND curriculum.subject_id = NEW.subject_id;
            SELECT COALESCE(jsonb_agg(item->'provenance' ORDER BY ordinal), '[]'::jsonb)
            INTO expected_provenance
            FROM jsonb_array_elements(generation_row.context_snapshot->'items')
                WITH ORDINALITY AS context_item(item, ordinal);

            IF candidate_row.id IS NULL OR generation_row.id IS NULL OR validation_row.id IS NULL
                OR slot_row.id IS NULL OR resolved_grade IS NULL
                OR candidate_row.curriculum_version_id <> NEW.curriculum_version_id
                OR candidate_row.generation_run_id <> NEW.generation_run_id
                OR candidate_row.generation_attempt_id <> NEW.generation_attempt_id
                OR candidate_row.validation_run_id <> NEW.validation_run_id
                OR NEW.candidate_version <> candidate_row.version
                OR original_content IS DISTINCT FROM NEW.original_content_snapshot
                OR current_content IS DISTINCT FROM NEW.current_content_snapshot
                OR slot_row.curriculum_version_id <> NEW.curriculum_version_id
                OR slot_row.version <> NEW.slot_version
                OR slot_row.unit_id <> NEW.unit_id OR slot_row.lesson_id <> NEW.lesson_id
                OR slot_row.lesson_number <> NEW.lesson_number
                OR resolved_grade <> NEW.grade OR resolved_medium <> NEW.medium_code
                OR resolved_subject <> NEW.subject_code
                OR generation_row.prompt_version <> NEW.prompt_version
                OR generation_row.provider <> NEW.provider
                OR generation_row.provider_version <> NEW.provider_version
                OR generation_row.model <> NEW.model
                OR generation_row.model_version <> NEW.model_version
                OR generation_row.retrieval_version <> NEW.retrieval_version
                OR validation_row.validator_lineage IS DISTINCT FROM NEW.validator_versions
                OR expected_provenance IS DISTINCT FROM NEW.provenance_snapshot
                OR NEW.scope_snapshot->>'curriculum_version_id' <> NEW.curriculum_version_id::text
                OR NEW.scope_snapshot->>'subject_id' <> NEW.subject_id::text
                OR NEW.scope_snapshot->>'unit_id' <> NEW.unit_id::text
                OR NEW.scope_snapshot->>'lesson_id' <> NEW.lesson_id::text
                OR NEW.replay_input_snapshot->'subject_scope'->>'curriculum_version_id'
                    <> NEW.curriculum_version_id::text
            THEN
                RAISE EXCEPTION 'subject-quality feedback scope or lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM teacher_paper_slot_runs
                WHERE slot_id = NEW.teacher_paper_slot_id
                    AND paper_job_id = NEW.teacher_paper_job_id
                    AND generation_run_id = NEW.generation_run_id
            ) THEN
                RAISE EXCEPTION 'feedback candidate is not part of the teacher paper slot lineage'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.action IN ('edit', 'reject', 'approve') THEN
                SELECT action, reviewer_id, revision
                INTO event_action, event_actor, event_revision
                FROM candidate_review_events
                WHERE candidate_id = NEW.candidate_id
                    AND candidate_version = NEW.review_event_version;
                IF event_action IS DISTINCT FROM (
                        CASE NEW.action
                            WHEN 'edit' THEN 'edited'
                            WHEN 'approve' THEN 'approved'
                            WHEN 'reject' THEN 'rejected'
                            ELSE NEW.action
                        END
                    )
                    OR event_actor IS DISTINCT FROM NEW.actor_id
                    OR event_revision IS DISTINCT FROM NEW.candidate_revision
                    OR NEW.replacement_generation_run_id IS NOT NULL
                THEN
                    RAISE EXCEPTION 'feedback does not match its review event'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.action = 'regenerate' THEN
                IF NEW.review_event_version IS NOT NULL
                    OR NEW.replacement_generation_run_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM teacher_paper_slot_runs
                        WHERE slot_id = NEW.teacher_paper_slot_id
                            AND paper_job_id = NEW.teacher_paper_job_id
                            AND generation_run_id = NEW.replacement_generation_run_id
                            AND requested_by = NEW.actor_id
                    )
                THEN
                    RAISE EXCEPTION 'regeneration feedback does not match its replacement run'
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
        CREATE FUNCTION enforce_subject_quality_eval_case_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            prior subject_quality_eval_case_versions%ROWTYPE;
            feedback_row subject_quality_feedback%ROWTYPE;
        BEGIN
            SELECT * INTO feedback_row FROM subject_quality_feedback
                WHERE id = NEW.source_feedback_id;
            IF feedback_row.id IS NULL
                OR NEW.replay_input_snapshot IS DISTINCT FROM feedback_row.replay_input_snapshot
                OR NEW.subject_scope_snapshot IS DISTINCT FROM feedback_row.scope_snapshot
            THEN
                RAISE EXCEPTION 'eval case must preserve its exact feedback snapshot'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version = 1 THEN
                RETURN NEW;
            END IF;
            SELECT * INTO prior FROM subject_quality_eval_case_versions
                WHERE eval_case_id = NEW.eval_case_id AND version = NEW.previous_version;
            IF prior.eval_case_id IS NULL
                OR prior.state <> 'draft' OR prior.version <> 1
                OR NEW.source_feedback_id <> prior.source_feedback_id
                OR NEW.expected_status <> prior.expected_status
                OR NEW.expected_finding_codes IS DISTINCT FROM prior.expected_finding_codes
                OR NEW.defect_category <> prior.defect_category
                OR NEW.replay_input_snapshot IS DISTINCT FROM prior.replay_input_snapshot
                OR NEW.subject_scope_snapshot IS DISTINCT FROM prior.subject_scope_snapshot
                OR NEW.case_fingerprint <> prior.case_fingerprint
                OR NEW.idempotency_key_hash <> prior.idempotency_key_hash
                OR NEW.promotion_request_fingerprint <> prior.promotion_request_fingerprint
                OR NEW.promoted_by <> prior.promoted_by
                OR NEW.created_at <> prior.created_at
            THEN
                RAISE EXCEPTION 'eval approval must append an exact immutable version'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_subject_quality_eval_result_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            run_row subject_quality_eval_runs%ROWTYPE;
            case_row subject_quality_eval_case_versions%ROWTYPE;
        BEGIN
            SELECT * INTO run_row FROM subject_quality_eval_runs WHERE id = NEW.eval_run_id;
            SELECT * INTO case_row FROM subject_quality_eval_case_versions
                WHERE eval_case_id = NEW.eval_case_id AND version = NEW.eval_case_version;
            IF run_row.id IS NULL THEN
                RAISE EXCEPTION 'eval result run lineage is missing' USING ERRCODE = '23514';
            END IF;
            IF case_row.eval_case_id IS NULL OR case_row.state <> 'approved' THEN
                RAISE EXCEPTION 'eval result requires an approved case' USING ERRCODE = '23514';
            END IF;
            IF NEW.pipeline_version <> run_row.pipeline_version
                OR NEW.pipeline_fingerprint <> run_row.pipeline_fingerprint
            THEN
                RAISE EXCEPTION 'eval result pipeline lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.expected_status <> case_row.expected_status
                OR NEW.expected_finding_codes IS DISTINCT FROM case_row.expected_finding_codes
            THEN
                RAISE EXCEPTION 'eval result expectation differs from its approved case'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_subject_quality_eval_run_complete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_id uuid;
            run_row subject_quality_eval_runs%ROWTYPE;
            result_count integer;
            pass_count integer;
            regression_count integer;
            unavailable_count integer;
        BEGIN
            IF TG_TABLE_NAME = 'subject_quality_eval_runs' THEN
                target_id := NEW.id;
            ELSE
                target_id := NEW.eval_run_id;
            END IF;
            SELECT * INTO run_row FROM subject_quality_eval_runs WHERE id = target_id;
            IF run_row.id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT count(*), count(*) FILTER (WHERE outcome = 'pass'),
                count(*) FILTER (WHERE outcome = 'regression'),
                count(*) FILTER (WHERE outcome = 'unavailable')
            INTO result_count, pass_count, regression_count, unavailable_count
            FROM subject_quality_eval_results WHERE eval_run_id = target_id;
            IF result_count <> run_row.case_count OR pass_count <> run_row.passed_count
                OR regression_count <> run_row.regression_count
                OR unavailable_count <> run_row.unavailable_count
            THEN
                RAISE EXCEPTION 'eval run result set is incomplete'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER enforce_subject_quality_feedback_insert_trigger
        BEFORE INSERT ON subject_quality_feedback
        FOR EACH ROW EXECUTE FUNCTION enforce_subject_quality_feedback_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_subject_quality_feedback_mutation_trigger
        BEFORE UPDATE OR DELETE ON subject_quality_feedback
        FOR EACH ROW EXECUTE FUNCTION reject_subject_quality_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_subject_quality_eval_case_insert_trigger
        BEFORE INSERT ON subject_quality_eval_case_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_subject_quality_eval_case_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_subject_quality_eval_case_mutation_trigger
        BEFORE UPDATE OR DELETE ON subject_quality_eval_case_versions
        FOR EACH ROW EXECUTE FUNCTION reject_subject_quality_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_subject_quality_eval_run_mutation_trigger
        BEFORE UPDATE OR DELETE ON subject_quality_eval_runs
        FOR EACH ROW EXECUTE FUNCTION reject_subject_quality_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_subject_quality_eval_result_insert_trigger
        BEFORE INSERT ON subject_quality_eval_results
        FOR EACH ROW EXECUTE FUNCTION enforce_subject_quality_eval_result_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_subject_quality_eval_result_mutation_trigger
        BEFORE UPDATE OR DELETE ON subject_quality_eval_results
        FOR EACH ROW EXECUTE FUNCTION reject_subject_quality_mutation()
        """
    )
    for table_name, operation in (
        ("subject_quality_eval_runs", "INSERT"),
        ("subject_quality_eval_results", "INSERT"),
    ):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER enforce_{table_name}_complete_trigger
            AFTER {operation} ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_subject_quality_eval_run_complete()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    row_count = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM subject_quality_feedback) + "
            "(SELECT count(*) FROM subject_quality_eval_case_versions) + "
            "(SELECT count(*) FROM subject_quality_eval_runs) + "
            "(SELECT count(*) FROM subject_quality_eval_results)"
        )
    ).scalar_one()
    if row_count:
        raise RuntimeError("refusing to downgrade 0026 while subject-quality evidence exists")

    op.execute(
        "DROP TRIGGER enforce_subject_quality_eval_results_complete_trigger "
        "ON subject_quality_eval_results"
    )
    op.execute(
        "DROP TRIGGER enforce_subject_quality_eval_runs_complete_trigger "
        "ON subject_quality_eval_runs"
    )
    op.execute(
        "DROP TRIGGER reject_subject_quality_eval_result_mutation_trigger "
        "ON subject_quality_eval_results"
    )
    op.execute(
        "DROP TRIGGER enforce_subject_quality_eval_result_insert_trigger "
        "ON subject_quality_eval_results"
    )
    op.execute(
        "DROP TRIGGER reject_subject_quality_eval_run_mutation_trigger ON subject_quality_eval_runs"
    )
    op.execute(
        "DROP TRIGGER reject_subject_quality_eval_case_mutation_trigger "
        "ON subject_quality_eval_case_versions"
    )
    op.execute(
        "DROP TRIGGER enforce_subject_quality_eval_case_insert_trigger "
        "ON subject_quality_eval_case_versions"
    )
    op.execute(
        "DROP TRIGGER reject_subject_quality_feedback_mutation_trigger ON subject_quality_feedback"
    )
    op.execute(
        "DROP TRIGGER enforce_subject_quality_feedback_insert_trigger ON subject_quality_feedback"
    )
    op.execute("DROP FUNCTION enforce_subject_quality_eval_run_complete()")
    op.execute("DROP FUNCTION enforce_subject_quality_eval_result_insert()")
    op.execute("DROP FUNCTION enforce_subject_quality_eval_case_insert()")
    op.execute("DROP FUNCTION enforce_subject_quality_feedback_insert()")
    op.drop_table("subject_quality_eval_results")
    op.drop_table("subject_quality_eval_runs")
    op.drop_index(
        "ix_subject_quality_eval_case_state_created",
        table_name="subject_quality_eval_case_versions",
    )
    op.drop_index(
        "uq_subject_quality_eval_case_actor_idempotency",
        table_name="subject_quality_eval_case_versions",
    )
    op.drop_index(
        "uq_subject_quality_eval_case_feedback_promotion",
        table_name="subject_quality_eval_case_versions",
    )
    op.drop_table("subject_quality_eval_case_versions")
    op.drop_table("subject_quality_feedback")
    op.execute("DROP FUNCTION reject_subject_quality_mutation()")
    op.execute("DROP FUNCTION subject_quality_code_array_valid(jsonb)")
