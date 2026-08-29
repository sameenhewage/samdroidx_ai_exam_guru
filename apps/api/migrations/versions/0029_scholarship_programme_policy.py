from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_scholarship_programme_policy"
down_revision: str | None = "0028_teacher_pilot_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_POLICY_SNAPSHOT_BYTES = 1_048_576

_PROGRAMME_JOB_FUNCTION = """
CREATE OR REPLACE FUNCTION enforce_teacher_paper_job_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    resolved_grade integer;
    resolved_medium text;
    resolved_subject text;
    resolved_assessment text;
    programme assessment_programme_policy_versions%ROWTYPE;
    intent_schema text;
    paper_type text;
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
        OR jsonb_typeof(NEW.teacher_intent->'target') <> 'object'
        OR jsonb_typeof(NEW.teacher_intent->'scope') <> 'object'
        OR NEW.resolution_snapshot->>'schema' <> 'teacher-paper-resolution.v1'
        OR NEW.resolution_snapshot->'curriculum'->>'id' <> NEW.curriculum_version_id::text
    THEN
        RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
            USING ERRCODE = '23514';
    END IF;

    intent_schema := NEW.teacher_intent->>'schema';
    paper_type := NEW.teacher_intent->'target'->>'paper_type';
    IF intent_schema = 'teacher-paper-intent.v1' THEN
        IF jsonb_typeof(NEW.teacher_intent->'target'->'grade') <> 'number'
            OR (NEW.teacher_intent->'target'->>'grade')::integer <> resolved_grade
            OR NEW.teacher_intent->'target'->>'medium' <> resolved_medium
            OR NEW.teacher_intent->'target'->>'subject' <> resolved_subject
            OR COALESCE(NEW.teacher_intent->'target'->>'assessment_programme', '')
                <> resolved_assessment
            OR NEW.paper_settings->>'schema' <> 'teacher-paper-settings.v1'
            OR jsonb_typeof(NEW.paper_settings->'question_count') <> 'number'
            OR (NEW.paper_settings->>'question_count')::integer <> NEW.slot_count
        THEN
            RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
                USING ERRCODE = '23514';
        END IF;
    ELSIF intent_schema = 'teacher-paper-intent.v2' THEN
        IF jsonb_typeof(NEW.teacher_intent->'target'->'grade') <> 'number'
            OR (NEW.teacher_intent->'target'->>'grade')::integer <> resolved_grade
            OR NEW.teacher_intent->'target'->>'medium' <> resolved_medium
            OR NEW.paper_settings->>'schema' <> 'teacher-paper-settings.v2'
            OR jsonb_typeof(NEW.paper_settings->'mcq_count') <> 'number'
            OR jsonb_typeof(NEW.paper_settings->'written_count') <> 'number'
            OR jsonb_typeof(NEW.paper_settings->'structured_count') <> 'number'
            OR (NEW.paper_settings->>'mcq_count')::integer
                + (NEW.paper_settings->>'written_count')::integer
                + (NEW.paper_settings->>'structured_count')::integer <> NEW.slot_count
            OR NEW.paper_settings->>'paper_name' <> NEW.title
        THEN
            RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
                USING ERRCODE = '23514';
        END IF;
        IF paper_type = 'subject_practice' THEN
            IF NEW.teacher_intent->'target'->>'subject' <> resolved_subject
                OR NEW.teacher_intent->'target' ? 'term'
                OR NEW.teacher_intent->'target' ? 'scholarship_mode'
                OR NEW.resolution_snapshot ? 'programme'
            THEN
                RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF paper_type = 'scholarship_practice' THEN
            SELECT * INTO programme
            FROM assessment_programme_policy_versions
            WHERE id = (NEW.resolution_snapshot->'programme'->>'policy_id')::uuid
                AND state = 'reviewed';
            IF programme.id IS NULL
                OR NEW.teacher_intent->'target' ? 'subject'
                OR NEW.teacher_intent->'target' ? 'term'
                OR NEW.teacher_intent->'target'->>'scholarship_mode'
                    NOT IN ('paper_i', 'paper_ii', 'full')
                OR NEW.teacher_intent->'scope'->>'kind' <> 'programme'
                OR programme.anchor_curriculum_version_id <> NEW.curriculum_version_id
                OR programme.programme_exam_configuration_id <> NEW.exam_configuration_id
                OR programme.medium_id <> NEW.medium_id
                OR programme.version <> NEW.resolution_snapshot->'programme'->>'policy_version'
                OR programme.content_hash <> NEW.resolution_snapshot->'programme'->>'content_hash'
                OR programme.code <> NEW.resolution_snapshot->'programme'->>'policy_code'
                OR NEW.teacher_intent->'target'->>'scholarship_mode'
                    <> NEW.resolution_snapshot->'programme'->>'mode'
            THEN
                RAISE EXCEPTION 'teacher paper programme resolution is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'teacher paper type is unavailable'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'teacher paper intent schema is unavailable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""

_SUBJECT_JOB_FUNCTION = """
CREATE OR REPLACE FUNCTION enforce_teacher_paper_job_insert()
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
        OR jsonb_typeof(NEW.teacher_intent->'target') <> 'object'
        OR jsonb_typeof(NEW.teacher_intent->'scope') <> 'object'
        OR NEW.resolution_snapshot->>'schema' <> 'teacher-paper-resolution.v1'
        OR NEW.resolution_snapshot->'curriculum'->>'id' <> NEW.curriculum_version_id::text
        OR NOT COALESCE(
            (
                NEW.teacher_intent->>'schema' = 'teacher-paper-intent.v1'
                AND jsonb_typeof(NEW.teacher_intent->'target'->'grade') = 'number'
                AND (NEW.teacher_intent->'target'->>'grade')::integer = resolved_grade
                AND NEW.teacher_intent->'target'->>'medium' = resolved_medium
                AND NEW.teacher_intent->'target'->>'subject' = resolved_subject
                AND COALESCE(NEW.teacher_intent->'target'->>'assessment_programme', '')
                    = resolved_assessment
                AND NEW.paper_settings->>'schema' = 'teacher-paper-settings.v1'
                AND jsonb_typeof(NEW.paper_settings->'question_count') = 'number'
                AND (NEW.paper_settings->>'question_count')::integer = NEW.slot_count
            )
            OR
            (
                NEW.teacher_intent->>'schema' = 'teacher-paper-intent.v2'
                AND NEW.teacher_intent->'target'->>'paper_type' = 'subject_practice'
                AND NOT (NEW.teacher_intent->'target' ? 'term')
                AND NOT (NEW.teacher_intent->'target' ? 'scholarship_mode')
                AND jsonb_typeof(NEW.teacher_intent->'target'->'grade') = 'number'
                AND (NEW.teacher_intent->'target'->>'grade')::integer = resolved_grade
                AND NEW.teacher_intent->'target'->>'medium' = resolved_medium
                AND NEW.teacher_intent->'target'->>'subject' = resolved_subject
                AND NEW.paper_settings->>'schema' = 'teacher-paper-settings.v2'
                AND jsonb_typeof(NEW.paper_settings->'mcq_count') = 'number'
                AND jsonb_typeof(NEW.paper_settings->'written_count') = 'number'
                AND jsonb_typeof(NEW.paper_settings->'structured_count') = 'number'
                AND (NEW.paper_settings->>'mcq_count')::integer
                    + (NEW.paper_settings->>'written_count')::integer
                    + (NEW.paper_settings->>'structured_count')::integer = NEW.slot_count
                AND NEW.paper_settings->>'paper_name' = NEW.title
            ),
            FALSE
        )
    THEN
        RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "assessment_programme_policy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("programme_exam_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("medium_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("paper_i_profile_version", sa.String(128), nullable=False),
        sa.Column("paper_ii_profile_version", sa.String(128), nullable=False),
        sa.Column("paper_i_weight", sa.Integer(), nullable=False),
        sa.Column("paper_ii_weight", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["programme_exam_configuration_id"],
            ["exam_configurations.id"],
            name="fk_assessment_programme_policy_exam",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medium_id"],
            ["media.id"],
            name="fk_assessment_programme_policy_medium",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_assessment_programme_policy_anchor_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "code",
            "version",
            "medium_id",
            name="uq_assessment_programme_policy_identity",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_assessment_programme_policy_fingerprint",
        ),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_assessment_programme_policy_code",
        ),
        sa.CheckConstraint(
            "version = btrim(version) AND char_length(version) BETWEEN 1 AND 128",
            name="ck_assessment_programme_policy_version",
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND char_length(title) BETWEEN 1 AND 255",
            name="ck_assessment_programme_policy_title",
        ),
        sa.CheckConstraint(
            "paper_i_profile_version = btrim(paper_i_profile_version) "
            "AND char_length(paper_i_profile_version) BETWEEN 1 AND 128 "
            "AND paper_ii_profile_version = btrim(paper_ii_profile_version) "
            "AND char_length(paper_ii_profile_version) BETWEEN 1 AND 128",
            name="ck_assessment_programme_policy_profiles",
        ),
        sa.CheckConstraint(
            "paper_i_weight BETWEEN 1 AND 100 AND paper_ii_weight BETWEEN 1 AND 100",
            name="ck_assessment_programme_policy_weights",
        ),
        sa.CheckConstraint(
            "(state = 'draft' AND lock_version = 0 AND review_snapshot IS NULL "
            "AND content_hash IS NULL AND reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(state = 'reviewed' AND lock_version = 1 "
            "AND jsonb_typeof(review_snapshot) = 'object' "
            f"AND pg_column_size(review_snapshot) <= {_MAX_POLICY_SNAPSHOT_BYTES} "
            "AND content_hash ~ '^[0-9a-f]{64}$' AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL) OR "
            "(state = 'retired' AND lock_version = 2 "
            "AND jsonb_typeof(review_snapshot) = 'object' "
            f"AND pg_column_size(review_snapshot) <= {_MAX_POLICY_SNAPSHOT_BYTES} "
            "AND content_hash ~ '^[0-9a-f]{64}$' AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL)",
            name="ck_assessment_programme_policy_state",
        ),
    )
    op.create_index(
        "uq_assessment_programme_policy_active",
        "assessment_programme_policy_versions",
        ["code", "medium_id"],
        unique=True,
        postgresql_where=sa.text("state = 'reviewed'"),
    )
    op.create_index(
        "ix_assessment_programme_policy_lookup",
        "assessment_programme_policy_versions",
        ["programme_exam_configuration_id", "medium_id", "state"],
    )

    op.create_table(
        "assessment_programme_policy_scopes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("part", sa.String(16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("anchor_curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_unit_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_lesson_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_competency_id", sa.Uuid(), nullable=False),
        sa.Column("anchor_skill_id", sa.Uuid(), nullable=True),
        sa.Column("anchor_sub_skill_id", sa.Uuid(), nullable=True),
        sa.Column("anchor_learning_concept_id", sa.Uuid(), nullable=True),
        sa.Column("source_grade", sa.Integer(), nullable=False),
        sa.Column("source_exam_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("source_medium_id", sa.Uuid(), nullable=False),
        sa.Column("source_subject_id", sa.Uuid(), nullable=False),
        sa.Column("source_curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_unit_id", sa.Uuid(), nullable=True),
        sa.Column("source_lesson_id", sa.Uuid(), nullable=True),
        sa.Column("source_competency_id", sa.Uuid(), nullable=False),
        sa.Column("source_skill_id", sa.Uuid(), nullable=True),
        sa.Column("source_sub_skill_id", sa.Uuid(), nullable=True),
        sa.Column("source_learning_concept_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["assessment_programme_policy_versions.id"],
            name="fk_assessment_programme_policy_scope_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_unit_id", "anchor_curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_programme_scope_anchor_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_lesson_id", "anchor_unit_id", "anchor_curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_programme_scope_anchor_lesson",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_competency_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_competency",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_skill_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_skill",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_sub_skill_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_sub_skill",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["anchor_learning_concept_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_learning_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_exam_configuration_id"],
            ["exam_configurations.id"],
            name="fk_programme_scope_source_exam",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_medium_id"],
            ["media.id"],
            name="fk_programme_scope_source_medium",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_subject_id"],
            ["subjects.id"],
            name="fk_programme_scope_source_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_programme_scope_source_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_unit_id", "source_curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_programme_scope_source_unit",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_lesson_id", "source_unit_id", "source_curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_programme_scope_source_lesson",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_competency_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_competency",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_skill_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_skill",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_sub_skill_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_sub_skill",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_learning_concept_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_learning_concept",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "policy_version_id",
            "part",
            "ordinal",
            name="uq_assessment_programme_policy_scope_ordinal",
        ),
        sa.CheckConstraint("part IN ('paper_i', 'paper_ii')", name="ck_programme_scope_part"),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 64", name="ck_programme_scope_ordinal"),
        sa.CheckConstraint("source_grade BETWEEN 1 AND 13", name="ck_programme_scope_grade"),
        sa.CheckConstraint(
            "(anchor_skill_id IS NOT NULL OR (anchor_sub_skill_id IS NULL "
            "AND anchor_learning_concept_id IS NULL)) AND "
            "(anchor_sub_skill_id IS NOT NULL OR anchor_learning_concept_id IS NULL)",
            name="ck_programme_scope_anchor_taxonomy_shape",
        ),
        sa.CheckConstraint(
            "(source_skill_id IS NOT NULL OR (source_sub_skill_id IS NULL "
            "AND source_learning_concept_id IS NULL)) AND "
            "(source_sub_skill_id IS NOT NULL OR source_learning_concept_id IS NULL)",
            name="ck_programme_scope_source_taxonomy_shape",
        ),
        sa.CheckConstraint(
            "(source_unit_id IS NULL AND source_lesson_id IS NULL) OR source_unit_id IS NOT NULL",
            name="ck_programme_scope_source_learning_shape",
        ),
    )
    op.create_index(
        "ix_assessment_programme_policy_scope_source",
        "assessment_programme_policy_scopes",
        ["policy_version_id", "part", "source_curriculum_version_id"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_assessment_programme_policy_version_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            programme_grade integer;
            anchor_exam_id uuid;
            anchor_medium_id uuid;
        BEGIN
            SELECT exam.grade, anchor.exam_configuration_id, anchor.medium_id
            INTO programme_grade, anchor_exam_id, anchor_medium_id
            FROM exam_configurations AS exam
            JOIN media AS medium ON medium.id = NEW.medium_id
            JOIN curriculum_versions AS anchor ON anchor.id = NEW.anchor_curriculum_version_id
            WHERE exam.id = NEW.programme_exam_configuration_id
                AND exam.active AND medium.active AND anchor.active;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'assessment programme policy anchor is unavailable'
                    USING ERRCODE = '23514';
            END IF;
            IF anchor_exam_id <> NEW.programme_exam_configuration_id
                OR anchor_medium_id <> NEW.medium_id
                OR programme_grade < 1 OR programme_grade > 13
            THEN
                RAISE EXCEPTION 'assessment programme policy anchor is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.state <> 'draft' OR NEW.lock_version <> 0
                OR NEW.review_snapshot IS NOT NULL OR NEW.content_hash IS NOT NULL
                OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'assessment programme policy must start as a clean draft'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_assessment_programme_policy_scope_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            policy_row assessment_programme_policy_versions%ROWTYPE;
            resolved_grade integer;
            resolved_exam_id uuid;
            resolved_medium_id uuid;
            resolved_subject_id uuid;
        BEGIN
            SELECT * INTO policy_row
            FROM assessment_programme_policy_versions
            WHERE id = NEW.policy_version_id;
            SELECT exam.grade, source.exam_configuration_id, source.medium_id, source.subject_id
            INTO resolved_grade, resolved_exam_id, resolved_medium_id, resolved_subject_id
            FROM curriculum_versions AS source
            JOIN exam_configurations AS exam ON exam.id = source.exam_configuration_id
            JOIN media AS medium ON medium.id = source.medium_id
            JOIN subjects AS subject ON subject.id = source.subject_id
            WHERE source.id = NEW.source_curriculum_version_id
                AND source.active AND exam.active AND medium.active AND subject.active;
            IF policy_row.id IS NULL OR policy_row.state <> 'draft'
                OR NEW.anchor_curriculum_version_id <> policy_row.anchor_curriculum_version_id
                OR NOT FOUND OR NEW.source_grade <> resolved_grade
                OR NEW.source_exam_configuration_id <> resolved_exam_id
                OR NEW.source_medium_id <> resolved_medium_id
                OR NEW.source_subject_id <> resolved_subject_id
                OR NEW.source_medium_id <> policy_row.medium_id
            THEN
                RAISE EXCEPTION 'assessment programme policy scope is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_assessment_programme_policy_version_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.programme_exam_configuration_id <> OLD.programme_exam_configuration_id
                OR NEW.medium_id <> OLD.medium_id
                OR NEW.anchor_curriculum_version_id <> OLD.anchor_curriculum_version_id
                OR NEW.request_fingerprint <> OLD.request_fingerprint
                OR NEW.code <> OLD.code OR NEW.version <> OLD.version OR NEW.title <> OLD.title
                OR NEW.paper_i_profile_version <> OLD.paper_i_profile_version
                OR NEW.paper_ii_profile_version <> OLD.paper_ii_profile_version
                OR NEW.paper_i_weight <> OLD.paper_i_weight
                OR NEW.paper_ii_weight <> OLD.paper_ii_weight
                OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at
            THEN
                RAISE EXCEPTION 'assessment programme policy identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.lock_version <> OLD.lock_version + 1 THEN
                RAISE EXCEPTION 'assessment programme policy requires monotonic versions'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.state = 'draft' AND NEW.state = 'reviewed' THEN
                IF NEW.review_snapshot IS NULL OR NEW.content_hash IS NULL
                    OR NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL
                THEN
                    RAISE EXCEPTION 'reviewed assessment programme policy needs evidence'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.state = 'reviewed' AND NEW.state = 'retired' THEN
                IF NEW.review_snapshot IS DISTINCT FROM OLD.review_snapshot
                    OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
                    OR NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by
                    OR NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
                THEN
                    RAISE EXCEPTION 'retired assessment programme policy keeps review evidence'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'invalid assessment programme policy transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_assessment_programme_policy_version_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'assessment programme policy versions cannot be deleted'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_assessment_programme_policy_scope_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'assessment programme policy scopes are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_assessment_programme_policy_version_insert_trigger
        BEFORE INSERT ON assessment_programme_policy_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_assessment_programme_policy_version_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_assessment_programme_policy_scope_insert_trigger
        BEFORE INSERT ON assessment_programme_policy_scopes
        FOR EACH ROW EXECUTE FUNCTION enforce_assessment_programme_policy_scope_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_assessment_programme_policy_version_update_trigger
        BEFORE UPDATE ON assessment_programme_policy_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_assessment_programme_policy_version_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_assessment_programme_policy_version_delete_trigger
        BEFORE DELETE ON assessment_programme_policy_versions
        FOR EACH ROW EXECUTE FUNCTION reject_assessment_programme_policy_version_delete()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_assessment_programme_policy_scope_mutation_trigger
        BEFORE UPDATE OR DELETE ON assessment_programme_policy_scopes
        FOR EACH ROW EXECUTE FUNCTION reject_assessment_programme_policy_scope_mutation()
        """
    )
    op.execute(_PROGRAMME_JOB_FUNCTION)


def downgrade() -> None:
    op.execute(_SUBJECT_JOB_FUNCTION)
    op.execute(
        "DROP TRIGGER reject_assessment_programme_policy_scope_mutation_trigger "
        "ON assessment_programme_policy_scopes"
    )
    op.execute(
        "DROP TRIGGER reject_assessment_programme_policy_version_delete_trigger "
        "ON assessment_programme_policy_versions"
    )
    op.execute(
        "DROP TRIGGER enforce_assessment_programme_policy_version_update_trigger "
        "ON assessment_programme_policy_versions"
    )
    op.execute(
        "DROP TRIGGER enforce_assessment_programme_policy_scope_insert_trigger "
        "ON assessment_programme_policy_scopes"
    )
    op.execute(
        "DROP TRIGGER enforce_assessment_programme_policy_version_insert_trigger "
        "ON assessment_programme_policy_versions"
    )
    op.execute("DROP FUNCTION reject_assessment_programme_policy_scope_mutation()")
    op.execute("DROP FUNCTION reject_assessment_programme_policy_version_delete()")
    op.execute("DROP FUNCTION enforce_assessment_programme_policy_version_update()")
    op.execute("DROP FUNCTION enforce_assessment_programme_policy_scope_insert()")
    op.execute("DROP FUNCTION enforce_assessment_programme_policy_version_insert()")
    op.drop_index(
        "ix_assessment_programme_policy_scope_source",
        table_name="assessment_programme_policy_scopes",
    )
    op.drop_table("assessment_programme_policy_scopes")
    op.drop_index(
        "ix_assessment_programme_policy_lookup",
        table_name="assessment_programme_policy_versions",
    )
    op.drop_index(
        "uq_assessment_programme_policy_active",
        table_name="assessment_programme_policy_versions",
    )
    op.drop_table("assessment_programme_policy_versions")
