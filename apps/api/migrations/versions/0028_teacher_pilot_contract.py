from collections.abc import Sequence

from alembic import op

revision: str = "0028_teacher_pilot_contract"
down_revision: str | None = "0027_semantic_claim_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE_FUNCTION = """
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

_DOWNGRADE_FUNCTION = """
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
        OR NEW.resolution_snapshot->'curriculum'->>'id' <> NEW.curriculum_version_id::text
    THEN
        RAISE EXCEPTION 'teacher paper server resolution is inconsistent'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute(_UPGRADE_FUNCTION)


def downgrade() -> None:
    op.execute(_DOWNGRADE_FUNCTION)
