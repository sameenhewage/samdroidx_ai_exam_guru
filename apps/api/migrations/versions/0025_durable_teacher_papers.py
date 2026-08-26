from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_durable_teacher_papers"
down_revision: str | None = "0024_subject_quality_validation_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_INTENT_BYTES = 32_768
_MAX_SETTINGS_BYTES = 8_192
_MAX_RESOLUTION_BYTES = 262_144
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_TOKENS = 100_000_000
_MAX_REGENERATIONS = 2


def _reject_mutation_trigger(table_name: str, trigger_name: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE DELETE ON {table_name}
        FOR EACH ROW EXECUTE FUNCTION reject_teacher_paper_mutation()
        """
    )


def upgrade() -> None:
    op.create_table(
        "teacher_paper_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paper_reference", sa.String(17), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(71), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("exam_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("medium_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_intent", postgresql.JSONB(), nullable=False),
        sa.Column("paper_settings", postgresql.JSONB(), nullable=False),
        sa.Column("resolution_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("paper_blueprint_id", sa.Uuid(), nullable=True),
        sa.Column("practice_paper_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("slot_count", sa.Integer(), nullable=False),
        sa.Column("generated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("failure_detail", sa.String(1024), nullable=True),
        sa.Column("actor_token", sa.Uuid(), nullable=True),
        sa.Column("actor_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_message_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_teacher_paper_jobs_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exam_configuration_id"],
            ["exam_configurations.id"],
            name="fk_teacher_paper_jobs_exam",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medium_id"],
            ["media.id"],
            name="fk_teacher_paper_jobs_medium",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name="fk_teacher_paper_jobs_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_teacher_paper_jobs_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["practice_paper_id", "curriculum_version_id"],
            ["practice_papers.id", "practice_papers.curriculum_version_id"],
            name="fk_teacher_paper_jobs_practice_paper_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("paper_reference", name="uq_teacher_paper_jobs_reference"),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key_hash",
            name="uq_teacher_paper_jobs_actor_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_teacher_paper_jobs_id_curriculum",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^sha256:[0-9a-f]{64}$' AND "
            "idempotency_key_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_teacher_paper_jobs_fingerprints",
        ),
        sa.CheckConstraint(
            "paper_reference ~ '^EGP-[0-9A-F]{4}-[0-9A-F]{8}$'",
            name="ck_teacher_paper_jobs_reference",
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND char_length(title) BETWEEN 1 AND 512",
            name="ck_teacher_paper_jobs_title",
        ),
        sa.CheckConstraint(
            "status IN ('preparing', 'generating', 'checking_answers', "
            "'ready_for_review', 'failed') AND version >= 0",
            name="ck_teacher_paper_jobs_status_version",
        ),
        sa.CheckConstraint(
            "slot_count BETWEEN 1 AND 50 AND generated_count BETWEEN 0 AND slot_count AND "
            "validated_count BETWEEN 0 AND slot_count AND candidate_count BETWEEN 0 AND slot_count "
            "AND approved_count BETWEEN 0 AND slot_count AND failed_count BETWEEN 0 AND slot_count",
            name="ck_teacher_paper_jobs_counts",
        ),
        sa.CheckConstraint(
            f"total_tokens BETWEEN 0 AND {_MAX_TOKENS} AND "
            f"cost_microusd BETWEEN 0 AND {_MAX_COST_MICROUSD} AND "
            f"max_cost_microusd BETWEEN 1 AND {_MAX_COST_MICROUSD} AND "
            "cost_microusd <= max_cost_microusd",
            name="ck_teacher_paper_jobs_cost",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(teacher_intent) = 'object' AND "
            f"pg_column_size(teacher_intent) <= {_MAX_INTENT_BYTES}",
            name="ck_teacher_paper_jobs_intent",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(paper_settings) = 'object' AND "
            f"pg_column_size(paper_settings) <= {_MAX_SETTINGS_BYTES}",
            name="ck_teacher_paper_jobs_settings",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resolution_snapshot) = 'object' AND "
            f"pg_column_size(resolution_snapshot) <= {_MAX_RESOLUTION_BYTES}",
            name="ck_teacher_paper_jobs_resolution",
        ),
        sa.CheckConstraint(
            "(actor_token IS NULL AND actor_lease_expires_at IS NULL) OR "
            "(actor_token IS NOT NULL AND actor_lease_expires_at IS NOT NULL)",
            name="ck_teacher_paper_jobs_actor_lease",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "char_length(failure_code) BETWEEN 1 AND 128)",
            name="ck_teacher_paper_jobs_failure_code",
        ),
        sa.CheckConstraint(
            "failure_detail IS NULL OR (failure_detail = btrim(failure_detail) AND "
            "char_length(failure_detail) BETWEEN 1 AND 1024)",
            name="ck_teacher_paper_jobs_failure_detail",
        ),
        sa.CheckConstraint(
            "dispatch_message_id IS NULL OR (dispatch_message_id = btrim(dispatch_message_id) "
            "AND char_length(dispatch_message_id) BETWEEN 1 AND 128)",
            name="ck_teacher_paper_jobs_dispatch_message",
        ),
    )
    op.create_index(
        "ix_teacher_paper_jobs_status_updated",
        "teacher_paper_jobs",
        ["status", "updated_at", "id"],
    )
    op.create_index(
        "ix_teacher_paper_jobs_created_by_created",
        "teacher_paper_jobs",
        ["created_by", "created_at", "id"],
    )
    op.create_index(
        "ix_teacher_paper_jobs_curriculum_created",
        "teacher_paper_jobs",
        ["curriculum_version_id", "created_at", "id"],
    )

    op.create_table(
        "teacher_paper_slots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paper_job_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("blueprint_slot_id", sa.String(128), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("competency_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=True),
        sa.Column("sub_skill_id", sa.Uuid(), nullable=True),
        sa.Column("learning_concept_id", sa.Uuid(), nullable=True),
        sa.Column("current_generation_run_id", sa.Uuid(), nullable=True),
        sa.Column("current_validation_run_id", sa.Uuid(), nullable=True),
        sa.Column("current_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regeneration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requires_revalidation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["paper_job_id", "curriculum_version_id"],
            ["teacher_paper_jobs.id", "teacher_paper_jobs.curriculum_version_id"],
            name="fk_teacher_paper_slots_job_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_teacher_paper_slots_unit_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_teacher_paper_slots_lesson_scope",
            ondelete="RESTRICT",
        ),
        *(
            sa.ForeignKeyConstraint(
                [column_name, "curriculum_version_id"],
                ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
                name=f"fk_teacher_paper_slots_{label}_curriculum",
                ondelete="RESTRICT",
            )
            for column_name, label in (
                ("competency_id", "competency"),
                ("skill_id", "skill"),
                ("sub_skill_id", "sub_skill"),
                ("learning_concept_id", "concept"),
            )
        ),
        sa.ForeignKeyConstraint(
            ["current_generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_teacher_paper_slots_generation_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_validation_run_id", "curriculum_version_id"],
            ["validation_runs.id", "validation_runs.curriculum_version_id"],
            name="fk_teacher_paper_slots_validation_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_teacher_paper_slots_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "paper_job_id", name="uq_teacher_paper_slots_id_job"),
        sa.UniqueConstraint("paper_job_id", "ordinal", name="uq_teacher_paper_slots_job_ordinal"),
        sa.UniqueConstraint(
            "paper_job_id",
            "blueprint_slot_id",
            name="uq_teacher_paper_slots_job_blueprint_slot",
        ),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 50", name="ck_teacher_paper_slots_ordinal"),
        sa.CheckConstraint(
            "lesson_number BETWEEN 1 AND 10000",
            name="ck_teacher_paper_slots_lesson_number",
        ),
        sa.CheckConstraint(
            "blueprint_slot_id = btrim(blueprint_slot_id) AND "
            "char_length(blueprint_slot_id) BETWEEN 1 AND 128",
            name="ck_teacher_paper_slots_blueprint_slot",
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'checking_answers', 'awaiting_review', 'in_review', "
            "'approved', 'rejected', 'revalidation_required', 'failed') AND version >= 0",
            name="ck_teacher_paper_slots_status_version",
        ),
        sa.CheckConstraint(
            f"regeneration_count BETWEEN 0 AND {_MAX_REGENERATIONS}",
            name="ck_teacher_paper_slots_regeneration_count",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "char_length(failure_code) BETWEEN 1 AND 128)",
            name="ck_teacher_paper_slots_failure_code",
        ),
    )
    op.create_index(
        "ix_teacher_paper_slots_job_ordinal",
        "teacher_paper_slots",
        ["paper_job_id", "ordinal"],
    )
    op.create_index(
        "ix_teacher_paper_slots_current_generation",
        "teacher_paper_slots",
        ["current_generation_run_id"],
    )
    op.create_index(
        "ix_teacher_paper_slots_current_candidate",
        "teacher_paper_slots",
        ["current_candidate_id"],
    )

    op.create_table(
        "teacher_paper_slot_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paper_job_id", sa.Uuid(), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("slot_ordinal", sa.Integer(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["paper_job_id"],
            ["teacher_paper_jobs.id"],
            name="fk_teacher_paper_slot_runs_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["slot_id", "paper_job_id"],
            ["teacher_paper_slots.id", "teacher_paper_slots.paper_job_id"],
            name="fk_teacher_paper_slot_runs_slot_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_teacher_paper_slot_runs_generation_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("generation_run_id", name="uq_teacher_paper_slot_runs_generation"),
        sa.UniqueConstraint("slot_id", "sequence", name="uq_teacher_paper_slot_runs_slot_sequence"),
        sa.CheckConstraint(
            "slot_ordinal BETWEEN 1 AND 50",
            name="ck_teacher_paper_slot_runs_ordinal",
        ),
        sa.CheckConstraint("sequence BETWEEN 1 AND 3", name="ck_teacher_paper_slot_runs_sequence"),
        sa.CheckConstraint(
            "reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 1024",
            name="ck_teacher_paper_slot_runs_reason",
        ),
    )
    op.create_index(
        "ix_teacher_paper_slot_runs_job_slot",
        "teacher_paper_slot_runs",
        ["paper_job_id", "slot_ordinal", "sequence"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_teacher_paper_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'teacher paper aggregate history cannot be deleted or rewritten'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_paper_job_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            resolved_grade integer;
            resolved_medium text;
            resolved_subject text;
            resolved_assessment text;
        BEGIN
            IF NEW.status <> 'preparing' OR NEW.version <> 0
                OR NEW.paper_blueprint_id IS NOT NULL OR NEW.practice_paper_id IS NOT NULL
                OR NEW.generated_count <> 0 OR NEW.validated_count <> 0
                OR NEW.candidate_count <> 0 OR NEW.approved_count <> 0 OR NEW.failed_count <> 0
                OR NEW.total_tokens <> 0 OR NEW.cost_microusd <> 0
                OR NEW.failure_code IS NOT NULL OR NEW.failure_detail IS NOT NULL
                OR NEW.completed_at IS NOT NULL OR NEW.actor_token IS NOT NULL
                OR NEW.actor_lease_expires_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'teacher paper jobs must begin in a clean preparing state'
                    USING ERRCODE = '23514';
            END IF;

            SELECT exam.grade, medium.code, subject.code, exam.code
            INTO resolved_grade, resolved_medium, resolved_subject, resolved_assessment
            FROM curriculum_versions AS curriculum
            JOIN exam_configurations AS exam ON exam.id = curriculum.exam_configuration_id
            JOIN media AS medium ON medium.id = curriculum.medium_id
            JOIN subjects AS subject ON subject.id = curriculum.subject_id
            WHERE curriculum.id = NEW.curriculum_version_id
                AND exam.id = NEW.exam_configuration_id
                AND medium.id = NEW.medium_id
                AND subject.id = NEW.subject_id
                AND curriculum.active AND exam.active AND medium.active AND subject.active;
            IF NOT FOUND
                OR NEW.teacher_intent->>'schema' <> 'teacher-paper-intent.v1'
                OR jsonb_typeof(NEW.teacher_intent->'target') <> 'object'
                OR jsonb_typeof(NEW.teacher_intent->'scope') <> 'object'
                OR (NEW.teacher_intent->'target'->>'grade')::integer <> resolved_grade
                OR NEW.teacher_intent->'target'->>'medium' <> resolved_medium
                OR NEW.teacher_intent->'target'->>'subject' <> resolved_subject
                OR COALESCE(NEW.teacher_intent->'target'->>'assessment_programme', '')
                    <> resolved_assessment
                OR NEW.paper_settings->>'schema' <> 'teacher-paper-settings.v1'
                OR (NEW.paper_settings->>'question_count')::integer <> NEW.slot_count
                OR NEW.resolution_snapshot->>'schema' <> 'teacher-paper-resolution.v1'
                OR NEW.resolution_snapshot->'curriculum'->>'id'
                    <> NEW.curriculum_version_id::text
            THEN
                RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_paper_job_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id OR NEW.paper_reference <> OLD.paper_reference
                OR NEW.created_by <> OLD.created_by
                OR NEW.idempotency_key_hash <> OLD.idempotency_key_hash
                OR NEW.request_fingerprint <> OLD.request_fingerprint
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.exam_configuration_id <> OLD.exam_configuration_id
                OR NEW.medium_id <> OLD.medium_id OR NEW.subject_id <> OLD.subject_id
                OR NEW.teacher_intent IS DISTINCT FROM OLD.teacher_intent
                OR NEW.paper_settings IS DISTINCT FROM OLD.paper_settings
                OR NEW.resolution_snapshot IS DISTINCT FROM OLD.resolution_snapshot
                OR NEW.title <> OLD.title OR NEW.slot_count <> OLD.slot_count
                OR NEW.max_cost_microusd <> OLD.max_cost_microusd
                OR NEW.created_at <> OLD.created_at
            THEN
                RAISE EXCEPTION 'teacher paper request and resolved scope are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version <> OLD.version + 1 OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'teacher paper jobs require monotonic CAS versions'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.paper_blueprint_id IS NOT NULL
                AND NEW.paper_blueprint_id IS DISTINCT FROM OLD.paper_blueprint_id
            THEN
                RAISE EXCEPTION 'teacher paper blueprint lineage is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.practice_paper_id IS NOT NULL
                AND NEW.practice_paper_id IS DISTINCT FROM OLD.practice_paper_id
            THEN
                RAISE EXCEPTION 'teacher paper draft lineage is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                NEW.status = OLD.status
                OR (OLD.status = 'preparing' AND NEW.status IN ('generating', 'failed'))
                OR (OLD.status = 'generating' AND NEW.status IN ('checking_answers', 'failed'))
                OR (OLD.status = 'checking_answers'
                    AND NEW.status IN ('ready_for_review', 'failed', 'generating'))
                OR (OLD.status IN ('ready_for_review', 'failed') AND NEW.status = 'generating')
            ) THEN
                RAISE EXCEPTION 'invalid teacher paper job transition'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status IN ('generating', 'checking_answers', 'ready_for_review')
                AND NEW.paper_blueprint_id IS NULL
            THEN
                RAISE EXCEPTION 'active teacher paper jobs require a blueprint'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'ready_for_review' AND (
                NEW.candidate_count <> NEW.slot_count OR NEW.failed_count <> 0
                OR NEW.completed_at IS NULL OR NEW.failure_code IS NOT NULL
                OR NEW.failure_detail IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'ready teacher papers require complete candidate coverage'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'failed' AND (
                NEW.failure_code IS NULL OR NEW.failure_detail IS NULL OR NEW.completed_at IS NULL
            ) THEN
                RAISE EXCEPTION 'failed teacher papers require bounded failure evidence'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status IN ('preparing', 'generating', 'checking_answers') AND (
                NEW.failure_code IS NOT NULL OR NEW.failure_detail IS NOT NULL
                OR NEW.completed_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'active teacher papers cannot retain terminal state fields'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_paper_slot_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            job_row teacher_paper_jobs%ROWTYPE;
            mapped_node uuid;
        BEGIN
            SELECT * INTO job_row FROM teacher_paper_jobs WHERE id = NEW.paper_job_id;
            mapped_node := COALESCE(
                NEW.learning_concept_id, NEW.sub_skill_id, NEW.skill_id, NEW.competency_id
            );
            IF NOT FOUND OR job_row.curriculum_version_id <> NEW.curriculum_version_id
                OR job_row.paper_blueprint_id IS NULL OR NEW.ordinal > job_row.slot_count
                OR NEW.status <> 'generating' OR NEW.version <> 0
                OR NEW.current_generation_run_id IS NULL
                OR NEW.current_validation_run_id IS NOT NULL OR NEW.current_candidate_id IS NOT NULL
                OR NEW.regeneration_count <> 0 OR NEW.requires_revalidation
                OR NEW.failure_code IS NOT NULL
                OR NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(
                        (
                            SELECT blueprint FROM paper_blueprints
                            WHERE id = job_row.paper_blueprint_id
                        )->'slots'
                    ) AS slot
                    WHERE slot->>'slot_id' = NEW.blueprint_slot_id
                        AND (slot->>'ordinal')::integer = NEW.ordinal
                        AND slot->'taxonomy_target'->>'competency_id' = NEW.competency_id::text
                        AND slot->'taxonomy_target'->>'skill_id'
                            IS NOT DISTINCT FROM NEW.skill_id::text
                        AND slot->'taxonomy_target'->>'sub_skill_id'
                            IS NOT DISTINCT FROM NEW.sub_skill_id::text
                        AND slot->'taxonomy_target'->>'learning_concept_id'
                            IS NOT DISTINCT FROM NEW.learning_concept_id::text
                )
                OR NOT EXISTS (
                    SELECT 1 FROM curriculum_lesson_taxonomy_mappings
                    WHERE lesson_id = NEW.lesson_id AND unit_id = NEW.unit_id
                        AND curriculum_version_id = NEW.curriculum_version_id
                        AND taxonomy_node_id = mapped_node
                )
            THEN
                RAISE EXCEPTION 'teacher paper slot lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION enforce_teacher_paper_slot_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id OR NEW.paper_job_id <> OLD.paper_job_id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.ordinal <> OLD.ordinal OR NEW.blueprint_slot_id <> OLD.blueprint_slot_id
                OR NEW.unit_id <> OLD.unit_id OR NEW.lesson_id <> OLD.lesson_id
                OR NEW.lesson_number <> OLD.lesson_number
                OR NEW.competency_id <> OLD.competency_id
                OR NEW.skill_id IS DISTINCT FROM OLD.skill_id
                OR NEW.sub_skill_id IS DISTINCT FROM OLD.sub_skill_id
                OR NEW.learning_concept_id IS DISTINCT FROM OLD.learning_concept_id
                OR NEW.created_at <> OLD.created_at
            THEN
                RAISE EXCEPTION 'teacher paper slot scope and blueprint lineage are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version <> OLD.version + 1 OR NEW.updated_at < OLD.updated_at
                OR NEW.regeneration_count NOT IN (
                    OLD.regeneration_count, OLD.regeneration_count + 1
                )
                OR NEW.regeneration_count > {_MAX_REGENERATIONS}
            THEN
                RAISE EXCEPTION 'teacher paper slots require bounded monotonic CAS updates'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.current_generation_run_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM generation_runs AS run
                JOIN teacher_paper_jobs AS job ON job.id = NEW.paper_job_id
                WHERE run.id = NEW.current_generation_run_id
                    AND run.curriculum_version_id = NEW.curriculum_version_id
                    AND run.paper_blueprint_id = job.paper_blueprint_id
                    AND run.slot_id = NEW.blueprint_slot_id
            ) THEN
                RAISE EXCEPTION 'teacher paper slot generation lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.current_validation_run_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM validation_runs
                WHERE id = NEW.current_validation_run_id
                    AND curriculum_version_id = NEW.curriculum_version_id
                    AND generation_run_id = NEW.current_generation_run_id
            ) THEN
                RAISE EXCEPTION 'teacher paper slot validation lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.current_candidate_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM question_candidates
                WHERE id = NEW.current_candidate_id
                    AND curriculum_version_id = NEW.curriculum_version_id
                    AND generation_run_id = NEW.current_generation_run_id
                    AND validation_run_id = NEW.current_validation_run_id
            ) THEN
                RAISE EXCEPTION 'teacher paper slot candidate lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'generating' AND (
                NEW.current_validation_run_id IS NOT NULL OR NEW.current_candidate_id IS NOT NULL
                OR NEW.requires_revalidation OR NEW.failure_code IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'generating teacher paper slots have invalid state data'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status IN ('awaiting_review', 'in_review', 'approved', 'rejected') AND (
                NEW.current_validation_run_id IS NULL OR NEW.current_candidate_id IS NULL
                OR NEW.requires_revalidation OR NEW.failure_code IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'reviewable teacher paper slots require current validation'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'revalidation_required' AND (
                NEW.current_candidate_id IS NULL OR NOT NEW.requires_revalidation
            ) THEN
                RAISE EXCEPTION 'edited teacher paper slots require revalidation'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'failed' AND NEW.failure_code IS NULL THEN
                RAISE EXCEPTION 'failed teacher paper slots require failure evidence'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """  # noqa: S608 - interpolation is a migration-owned integer bound
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_paper_slot_run_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            slot_row teacher_paper_slots%ROWTYPE;
            job_blueprint uuid;
            expected_sequence integer;
        BEGIN
            SELECT * INTO slot_row FROM teacher_paper_slots
            WHERE id = NEW.slot_id AND paper_job_id = NEW.paper_job_id;
            SELECT paper_blueprint_id INTO job_blueprint FROM teacher_paper_jobs
            WHERE id = NEW.paper_job_id;
            SELECT COALESCE(max(sequence), 0) + 1 INTO expected_sequence
            FROM teacher_paper_slot_runs WHERE slot_id = NEW.slot_id;
            IF slot_row.id IS NULL OR job_blueprint IS NULL
                OR slot_row.ordinal <> NEW.slot_ordinal
                OR slot_row.curriculum_version_id <> NEW.curriculum_version_id
                OR slot_row.current_generation_run_id <> NEW.generation_run_id
                OR NEW.sequence <> expected_sequence
                OR NOT EXISTS (
                    SELECT 1 FROM generation_runs
                    WHERE id = NEW.generation_run_id
                        AND curriculum_version_id = NEW.curriculum_version_id
                        AND paper_blueprint_id = job_blueprint
                        AND slot_id = slot_row.blueprint_slot_id
                )
            THEN
                RAISE EXCEPTION 'teacher paper slot run lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_job_insert_trigger
        BEFORE INSERT ON teacher_paper_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_job_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_job_update_trigger
        BEFORE UPDATE ON teacher_paper_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_job_update()
        """
    )
    _reject_mutation_trigger(
        "teacher_paper_jobs",
        "reject_teacher_paper_job_delete_trigger",
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_slot_insert_trigger
        BEFORE INSERT ON teacher_paper_slots
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_slot_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_slot_update_trigger
        BEFORE UPDATE ON teacher_paper_slots
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_slot_update()
        """
    )
    _reject_mutation_trigger(
        "teacher_paper_slots",
        "reject_teacher_paper_slot_delete_trigger",
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_slot_run_insert_trigger
        BEFORE INSERT ON teacher_paper_slot_runs
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_slot_run_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_teacher_paper_slot_run_mutation_trigger
        BEFORE UPDATE OR DELETE ON teacher_paper_slot_runs
        FOR EACH ROW EXECUTE FUNCTION reject_teacher_paper_mutation()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    aggregate_count = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM teacher_paper_jobs) + "
            "(SELECT count(*) FROM teacher_paper_slots) + "
            "(SELECT count(*) FROM teacher_paper_slot_runs)"
        )
    ).scalar_one()
    if aggregate_count:
        raise RuntimeError("refusing to downgrade 0025 while durable teacher paper lineage exists")

    op.execute(
        "DROP TRIGGER reject_teacher_paper_slot_run_mutation_trigger ON teacher_paper_slot_runs"
    )
    op.execute(
        "DROP TRIGGER enforce_teacher_paper_slot_run_insert_trigger ON teacher_paper_slot_runs"
    )
    op.execute("DROP TRIGGER reject_teacher_paper_slot_delete_trigger ON teacher_paper_slots")
    op.execute("DROP TRIGGER enforce_teacher_paper_slot_update_trigger ON teacher_paper_slots")
    op.execute("DROP TRIGGER enforce_teacher_paper_slot_insert_trigger ON teacher_paper_slots")
    op.execute("DROP TRIGGER reject_teacher_paper_job_delete_trigger ON teacher_paper_jobs")
    op.execute("DROP TRIGGER enforce_teacher_paper_job_update_trigger ON teacher_paper_jobs")
    op.execute("DROP TRIGGER enforce_teacher_paper_job_insert_trigger ON teacher_paper_jobs")
    op.execute("DROP FUNCTION enforce_teacher_paper_slot_run_insert()")
    op.execute("DROP FUNCTION enforce_teacher_paper_slot_update()")
    op.execute("DROP FUNCTION enforce_teacher_paper_slot_insert()")
    op.execute("DROP FUNCTION enforce_teacher_paper_job_update()")
    op.execute("DROP FUNCTION enforce_teacher_paper_job_insert()")
    op.execute("DROP FUNCTION reject_teacher_paper_mutation()")
    op.drop_table("teacher_paper_slot_runs")
    op.drop_table("teacher_paper_slots")
    op.drop_table("teacher_paper_jobs")
