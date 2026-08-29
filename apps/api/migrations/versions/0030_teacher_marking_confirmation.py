"""Persist explicit teacher confirmation of generated marking guidance.

Revision ID: 0030_teacher_marking_confirmation
Revises: 0029_scholarship_programme_policy
Create Date: 2026-08-29 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_teacher_marking_confirmation"
down_revision: str | None = "0029_scholarship_programme_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION review_candidate_content_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            option_ids text[] := ARRAY[]::text[];
            option_id text;
            answer_value text;
            allocated_marks integer := 0;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'question_type', 'stem', 'options', 'answer', 'explanation', 'marks',
                    'marking_guide'
                ]
                OR candidate - ARRAY[
                    'question_type', 'stem', 'options', 'answer', 'explanation', 'marks',
                    'marking_guide', 'marking_point_marks'
                ] <> '{}'::jsonb
                OR jsonb_typeof(candidate->'question_type') <> 'string'
                OR candidate->>'question_type' NOT IN (
                    'multiple_choice', 'short_answer', 'structured', 'structured_response'
                )
                OR jsonb_typeof(candidate->'stem') <> 'string'
                OR candidate->>'stem' <> btrim(candidate->>'stem')
                OR char_length(candidate->>'stem') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'answer') <> 'string'
                OR candidate->>'answer' <> btrim(candidate->>'answer')
                OR char_length(candidate->>'answer') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'explanation') <> 'string'
                OR candidate->>'explanation' <> btrim(candidate->>'explanation')
                OR char_length(candidate->>'explanation') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'marks') <> 'number'
                OR candidate->>'marks' !~ '^[0-9]+$'
                OR (candidate->>'marks')::integer NOT BETWEEN 1 AND 100
                OR jsonb_typeof(candidate->'options') <> 'array'
                OR jsonb_array_length(candidate->'options') > 16
                OR jsonb_typeof(candidate->'marking_guide') <> 'array'
                OR jsonb_array_length(candidate->'marking_guide') NOT BETWEEN 1 AND 64
            THEN
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'options')
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY['option_id', 'text']
                    OR item - ARRAY['option_id', 'text'] <> '{}'::jsonb
                    OR jsonb_typeof(item->'option_id') <> 'string'
                    OR jsonb_typeof(item->'text') <> 'string'
                    OR item->>'option_id' <> btrim(item->>'option_id')
                    OR char_length(item->>'option_id') NOT BETWEEN 1 AND 128
                    OR item->>'text' <> btrim(item->>'text')
                    OR char_length(item->>'text') NOT BETWEEN 1 AND 8192
                THEN
                    RETURN FALSE;
                END IF;
                option_id := item->>'option_id';
                IF option_id = ANY(option_ids) THEN
                    RETURN FALSE;
                END IF;
                option_ids := array_append(option_ids, option_id);
            END LOOP;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'marking_guide')
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR item #>> '{}' <> btrim(item #>> '{}')
                    OR char_length(item #>> '{}') NOT BETWEEN 1 AND 8192
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;

            IF candidate ? 'marking_point_marks' THEN
                IF jsonb_typeof(candidate->'marking_point_marks') <> 'array'
                    OR jsonb_array_length(candidate->'marking_point_marks')
                        <> jsonb_array_length(candidate->'marking_guide')
                THEN
                    RETURN FALSE;
                END IF;
                FOR item IN
                    SELECT value FROM jsonb_array_elements(candidate->'marking_point_marks')
                LOOP
                    IF jsonb_typeof(item) <> 'number'
                        OR item #>> '{}' !~ '^[0-9]+$'
                        OR (item #>> '{}')::integer NOT BETWEEN 1 AND 100
                    THEN
                        RETURN FALSE;
                    END IF;
                    allocated_marks := allocated_marks + (item #>> '{}')::integer;
                END LOOP;
                IF allocated_marks <> (candidate->>'marks')::integer THEN
                    RETURN FALSE;
                END IF;
            END IF;

            IF candidate->>'question_type' = 'multiple_choice' THEN
                answer_value := candidate->>'answer';
                IF array_length(option_ids, 1) IS NULL
                    OR array_length(option_ids, 1) NOT BETWEEN 2 AND 16
                    OR NOT answer_value = ANY(option_ids)
                THEN
                    RETURN FALSE;
                END IF;
            END IF;
            RETURN TRUE;
        EXCEPTION WHEN numeric_value_out_of_range THEN
            RETURN FALSE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION review_candidate_initial_content_matches(
            content jsonb,
            generated jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            criterion jsonb;
            ordinal integer := 0;
            parsed jsonb;
        BEGIN
            IF content->>'question_type' IS DISTINCT FROM generated->>'question_type'
                OR content->>'stem' IS DISTINCT FROM generated->>'stem'
                OR content->'options' IS DISTINCT FROM generated->'options'
                OR content->>'explanation' IS DISTINCT FROM generated->'answer'->>'explanation'
                OR content->'marks' IS DISTINCT FROM generated->'marking'->'total_marks'
                OR jsonb_typeof(generated->'answer') <> 'object'
                OR jsonb_typeof(generated->'marking') <> 'object'
                OR jsonb_typeof(generated->'marking'->'criteria') <> 'array'
                OR jsonb_typeof(content->'marking_point_marks') <> 'array'
                OR jsonb_array_length(content->'marking_guide') <>
                    jsonb_array_length(generated->'marking'->'criteria')
                OR jsonb_array_length(content->'marking_point_marks') <>
                    jsonb_array_length(generated->'marking'->'criteria')
            THEN
                RETURN FALSE;
            END IF;
            IF jsonb_typeof(generated->'answer'->'correct_option_id') = 'string' THEN
                IF content->>'answer' IS DISTINCT FROM
                    generated->'answer'->>'correct_option_id'
                THEN
                    RETURN FALSE;
                END IF;
            ELSIF generated->'answer'->'correct_option_id' = 'null'::jsonb THEN
                BEGIN
                    parsed := (content->>'answer')::jsonb;
                EXCEPTION WHEN others THEN
                    RETURN FALSE;
                END;
                IF parsed IS DISTINCT FROM generated->'answer'->'accepted_responses' THEN
                    RETURN FALSE;
                END IF;
            ELSE
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(content->'marking_guide')
            LOOP
                criterion := generated->'marking'->'criteria'->ordinal;
                IF item #>> '{}' IS DISTINCT FROM criterion->>'description'
                    OR content->'marking_point_marks'->ordinal
                        IS DISTINCT FROM criterion->'marks'
                THEN
                    RETURN FALSE;
                END IF;
                ordinal := ordinal + 1;
            END LOOP;
            RETURN TRUE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_question_candidate_revision_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate question_candidates%ROWTYPE;
            existing_count integer;
            generated_content jsonb;
            original_content jsonb;
        BEGIN
            SELECT * INTO aggregate
            FROM question_candidates WHERE id = NEW.candidate_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'question candidate revision aggregate does not exist'
                    USING ERRCODE = '23503';
            END IF;
            SELECT count(*) INTO existing_count
            FROM question_candidate_revisions WHERE candidate_id = NEW.candidate_id;
            IF NEW.revision <> existing_count + 1 THEN
                RAISE EXCEPTION 'question candidate revisions must be contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.revision = 1 THEN
                IF aggregate.state <> 'validated' OR aggregate.version <> 2
                    OR aggregate.current_revision <> 1 OR NEW.candidate_version <> 2
                THEN
                    RAISE EXCEPTION 'initial candidate revision requires validated v2 aggregate'
                        USING ERRCODE = '23514';
                END IF;
                SELECT candidate INTO generated_content
                FROM generation_runs WHERE id = aggregate.generation_run_id;
                IF generated_content IS NULL
                    OR NOT review_candidate_initial_content_matches(NEW.content, generated_content)
                THEN
                    RAISE EXCEPTION 'initial candidate revision differs from generation result'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                IF aggregate.state <> 'in_review'
                    OR aggregate.version <> NEW.candidate_version
                    OR aggregate.current_revision <> NEW.revision
                THEN
                    RAISE EXCEPTION 'review edit revision does not match aggregate CAS state'
                        USING ERRCODE = '23514';
                END IF;
                SELECT content INTO original_content
                FROM question_candidate_revisions
                WHERE candidate_id = NEW.candidate_id AND revision = 1;
                IF original_content IS NULL
                    OR NEW.content->>'question_type' IS DISTINCT FROM
                        original_content->>'question_type'
                THEN
                    RAISE EXCEPTION 'review edit cannot change generated question type'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.create_table(
        "teacher_paper_marking_confirmations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("paper_job_id", sa.Uuid(), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("review_candidate_version", sa.Integer(), nullable=False),
        sa.Column("marking_fingerprint", sa.String(71), nullable=False),
        sa.Column("total_marks", sa.Integer(), nullable=False),
        sa.Column("criteria_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["slot_id", "paper_job_id"],
            ["teacher_paper_slots.id", "teacher_paper_slots.paper_job_id"],
            name="fk_teacher_paper_marking_confirmations_slot_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_teacher_paper_marking_confirmations_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "slot_id",
            "candidate_id",
            "candidate_revision",
            "marking_fingerprint",
            name="uq_teacher_paper_marking_confirmation_content",
        ),
        sa.CheckConstraint(
            "candidate_revision BETWEEN 1 AND 32 AND review_candidate_version BETWEEN 3 AND 35",
            name="ck_teacher_paper_marking_confirmation_revision",
        ),
        sa.CheckConstraint(
            "marking_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_teacher_paper_marking_confirmation_fingerprint",
        ),
        sa.CheckConstraint(
            "total_marks BETWEEN 1 AND 100 AND criteria_count BETWEEN 1 AND 64",
            name="ck_teacher_paper_marking_confirmation_summary",
        ),
    )
    op.create_index(
        "ix_teacher_paper_marking_confirmations_slot",
        "teacher_paper_marking_confirmations",
        ["slot_id", "candidate_id", "candidate_revision"],
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_marking_confirmation_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            slot_row teacher_paper_slots%ROWTYPE;
            candidate_row question_candidates%ROWTYPE;
            revision_content jsonb;
        BEGIN
            SELECT * INTO slot_row
            FROM teacher_paper_slots
            WHERE id = NEW.slot_id AND paper_job_id = NEW.paper_job_id;
            IF NOT FOUND OR slot_row.curriculum_version_id <> NEW.curriculum_version_id
                OR slot_row.current_candidate_id <> NEW.candidate_id
                OR slot_row.status NOT IN ('in_review', 'approved')
            THEN
                RAISE EXCEPTION 'marking confirmation is outside the current review slot'
                    USING ERRCODE = '23514';
            END IF;

            SELECT * INTO candidate_row
            FROM question_candidates
            WHERE id = NEW.candidate_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF NOT FOUND OR candidate_row.current_revision <> NEW.candidate_revision
                OR candidate_row.version <> NEW.review_candidate_version
                OR candidate_row.state NOT IN ('in_review', 'approved')
            THEN
                RAISE EXCEPTION 'marking confirmation does not match the current candidate'
                    USING ERRCODE = '23514';
            END IF;

            SELECT content INTO revision_content
            FROM question_candidate_revisions
            WHERE candidate_id = NEW.candidate_id
                AND revision = NEW.candidate_revision;
            IF NOT FOUND OR jsonb_typeof(revision_content) <> 'object'
                OR (revision_content->>'marks')::integer <> NEW.total_marks
                OR jsonb_typeof(revision_content->'marking_guide') <> 'array'
                OR jsonb_array_length(revision_content->'marking_guide') <> NEW.criteria_count
                OR jsonb_typeof(revision_content->'marking_point_marks') <> 'array'
                OR jsonb_array_length(revision_content->'marking_point_marks')
                    <> NEW.criteria_count
                OR (
                    SELECT sum((value #>> '{}')::integer)
                    FROM jsonb_array_elements(revision_content->'marking_point_marks')
                ) <> NEW.total_marks
                OR NEW.marking_fingerprint IS DISTINCT FROM 'sha256:' || encode(
                    sha256(
                        convert_to(
                            paper_canonical_jsonb(
                                jsonb_build_object(
                                    'marks', revision_content->'marks',
                                    'marking_guide', revision_content->'marking_guide',
                                    'marking_point_marks',
                                    revision_content->'marking_point_marks'
                                )
                            ),
                            'UTF8'
                        )
                    ),
                    'hex'
                )
            THEN
                RAISE EXCEPTION 'marking confirmation summary does not match candidate content'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_teacher_marking_confirmation_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'teacher marking confirmations are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_teacher_marking_confirmation_insert
        BEFORE INSERT ON teacher_paper_marking_confirmations
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_marking_confirmation_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_teacher_marking_confirmation_immutable
        BEFORE UPDATE OR DELETE ON teacher_paper_marking_confirmations
        FOR EACH ROW EXECUTE FUNCTION reject_teacher_marking_confirmation_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_marking_confirmation_on_draft()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM question_candidates AS candidate
                JOIN teacher_paper_slot_runs AS slot_run
                    ON slot_run.generation_run_id = candidate.generation_run_id
                JOIN teacher_paper_slots AS slot ON slot.id = slot_run.slot_id
                WHERE candidate.id = NEW.candidate_id
                    AND (
                        slot.status <> 'approved'
                        OR slot.current_candidate_id IS DISTINCT FROM candidate.id
                        OR NOT EXISTS (
                            SELECT 1
                            FROM teacher_paper_marking_confirmations AS confirmation
                            WHERE confirmation.slot_id = slot.id
                                AND confirmation.candidate_id = NEW.candidate_id
                                AND confirmation.candidate_revision = NEW.candidate_revision
                        )
                    )
            ) THEN
                RAISE EXCEPTION
                    'teacher paper draft requires current approved candidate and confirmed marking'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_marking_confirmation_on_draft_trigger
        BEFORE INSERT ON paper_draft_candidates
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_marking_confirmation_on_draft()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_question_candidate_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.generation_run_id IS DISTINCT FROM OLD.generation_run_id
                OR NEW.generation_attempt_id IS DISTINCT FROM OLD.generation_attempt_id
                OR NEW.validation_run_id IS DISTINCT FROM OLD.validation_run_id
                OR NEW.paper_blueprint_id IS DISTINCT FROM OLD.paper_blueprint_id
                OR NEW.blueprint_id IS DISTINCT FROM OLD.blueprint_id
                OR NEW.blueprint_version IS DISTINCT FROM OLD.blueprint_version
                OR NEW.blueprint_slot_id IS DISTINCT FROM OLD.blueprint_slot_id
                OR NEW.generation_lineage IS DISTINCT FROM OLD.generation_lineage
                OR NEW.validation_evidence IS DISTINCT FROM OLD.validation_evidence
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'question candidate upstream lineage is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.state IN ('approved', 'rejected') THEN
                RAISE EXCEPTION 'terminal question candidates are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.state = 'approved'
                AND EXISTS (
                    SELECT 1
                    FROM teacher_paper_slots AS slot
                    WHERE slot.current_candidate_id = NEW.id
                        AND NOT EXISTS (
                            SELECT 1
                            FROM teacher_paper_marking_confirmations AS confirmation
                            WHERE confirmation.slot_id = slot.id
                                AND confirmation.candidate_id = NEW.id
                                AND confirmation.candidate_revision = NEW.current_revision
                        )
                )
            THEN
                RAISE EXCEPTION 'teacher paper marking requires explicit confirmation'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                OLD.state = 'validated' AND OLD.version = 2 AND OLD.current_revision = 1
                    AND NEW.state = 'in_review' AND NEW.version = 3
                    AND NEW.current_revision = 1
                OR OLD.state = 'in_review' AND NEW.state = 'in_review'
                    AND NEW.version = OLD.version + 1
                    AND NEW.current_revision = OLD.current_revision + 1
                OR OLD.state = 'in_review' AND NEW.state IN ('approved', 'rejected')
                    AND NEW.version = OLD.version + 1
                    AND NEW.current_revision = OLD.current_revision
            ) THEN
                RAISE EXCEPTION 'invalid question candidate transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_question_candidate_revision_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate question_candidates%ROWTYPE;
            existing_count integer;
            generated_content jsonb;
            original_content jsonb;
        BEGIN
            SELECT * INTO aggregate
            FROM question_candidates WHERE id = NEW.candidate_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'question candidate revision aggregate does not exist'
                    USING ERRCODE = '23503';
            END IF;
            SELECT count(*) INTO existing_count
            FROM question_candidate_revisions WHERE candidate_id = NEW.candidate_id;
            IF NEW.revision <> existing_count + 1 THEN
                RAISE EXCEPTION 'question candidate revisions must be contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.revision = 1 THEN
                IF aggregate.state <> 'validated' OR aggregate.version <> 2
                    OR aggregate.current_revision <> 1 OR NEW.candidate_version <> 2
                THEN
                    RAISE EXCEPTION 'initial candidate revision requires validated v2 aggregate'
                        USING ERRCODE = '23514';
                END IF;
                SELECT candidate INTO generated_content
                FROM generation_runs WHERE id = aggregate.generation_run_id;
                IF generated_content IS NULL
                    OR NOT review_candidate_initial_content_matches(NEW.content, generated_content)
                THEN
                    RAISE EXCEPTION 'initial candidate revision differs from generation result'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                IF aggregate.state <> 'in_review'
                    OR aggregate.version <> NEW.candidate_version
                    OR aggregate.current_revision <> NEW.revision
                THEN
                    RAISE EXCEPTION 'review edit revision does not match aggregate CAS state'
                        USING ERRCODE = '23514';
                END IF;
                SELECT content INTO original_content
                FROM question_candidate_revisions
                WHERE candidate_id = NEW.candidate_id AND revision = 1;
                IF original_content IS NULL
                    OR NEW.content->>'question_type' IS DISTINCT FROM
                        original_content->>'question_type'
                    OR NEW.content->'marks' IS DISTINCT FROM original_content->'marks'
                THEN
                    RAISE EXCEPTION 'review edit cannot change generated type or marks'
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
        CREATE OR REPLACE FUNCTION enforce_question_candidate_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.generation_run_id IS DISTINCT FROM OLD.generation_run_id
                OR NEW.generation_attempt_id IS DISTINCT FROM OLD.generation_attempt_id
                OR NEW.validation_run_id IS DISTINCT FROM OLD.validation_run_id
                OR NEW.paper_blueprint_id IS DISTINCT FROM OLD.paper_blueprint_id
                OR NEW.blueprint_id IS DISTINCT FROM OLD.blueprint_id
                OR NEW.blueprint_version IS DISTINCT FROM OLD.blueprint_version
                OR NEW.blueprint_slot_id IS DISTINCT FROM OLD.blueprint_slot_id
                OR NEW.generation_lineage IS DISTINCT FROM OLD.generation_lineage
                OR NEW.validation_evidence IS DISTINCT FROM OLD.validation_evidence
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'question candidate upstream lineage is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.state IN ('approved', 'rejected') THEN
                RAISE EXCEPTION 'terminal question candidates are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                OLD.state = 'validated' AND OLD.version = 2 AND OLD.current_revision = 1
                    AND NEW.state = 'in_review' AND NEW.version = 3
                    AND NEW.current_revision = 1
                OR OLD.state = 'in_review' AND NEW.state = 'in_review'
                    AND NEW.version = OLD.version + 1
                    AND NEW.current_revision = OLD.current_revision + 1
                OR OLD.state = 'in_review' AND NEW.state IN ('approved', 'rejected')
                    AND NEW.version = OLD.version + 1
                    AND NEW.current_revision = OLD.current_revision
            ) THEN
                RAISE EXCEPTION 'invalid question candidate transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS enforce_teacher_marking_confirmation_on_draft_trigger "
        "ON paper_draft_candidates"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_teacher_marking_confirmation_on_draft()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_teacher_marking_confirmation_immutable "
        "ON teacher_paper_marking_confirmations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_teacher_marking_confirmation_insert "
        "ON teacher_paper_marking_confirmations"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_teacher_marking_confirmation_mutation()")
    op.execute("DROP FUNCTION IF EXISTS enforce_teacher_marking_confirmation_insert()")
    op.drop_index(
        "ix_teacher_paper_marking_confirmations_slot",
        table_name="teacher_paper_marking_confirmations",
    )
    op.drop_table("teacher_paper_marking_confirmations")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION review_candidate_initial_content_matches(
            content jsonb,
            generated jsonb
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            criterion jsonb;
            ordinal integer := 0;
            parsed jsonb;
        BEGIN
            IF content->>'question_type' IS DISTINCT FROM generated->>'question_type'
                OR content->>'stem' IS DISTINCT FROM generated->>'stem'
                OR content->'options' IS DISTINCT FROM generated->'options'
                OR content->>'explanation' IS DISTINCT FROM generated->'answer'->>'explanation'
                OR content->'marks' IS DISTINCT FROM generated->'marking'->'total_marks'
                OR jsonb_typeof(generated->'answer') <> 'object'
                OR jsonb_typeof(generated->'marking') <> 'object'
                OR jsonb_typeof(generated->'marking'->'criteria') <> 'array'
                OR jsonb_array_length(content->'marking_guide') <>
                    jsonb_array_length(generated->'marking'->'criteria')
            THEN
                RETURN FALSE;
            END IF;
            IF jsonb_typeof(generated->'answer'->'correct_option_id') = 'string' THEN
                IF content->>'answer' IS DISTINCT FROM
                    generated->'answer'->>'correct_option_id'
                THEN
                    RETURN FALSE;
                END IF;
            ELSIF generated->'answer'->'correct_option_id' = 'null'::jsonb THEN
                BEGIN
                    parsed := (content->>'answer')::jsonb;
                EXCEPTION WHEN others THEN
                    RETURN FALSE;
                END;
                IF parsed IS DISTINCT FROM generated->'answer'->'accepted_responses' THEN
                    RETURN FALSE;
                END IF;
            ELSE
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(content->'marking_guide')
            LOOP
                criterion := generated->'marking'->'criteria'->ordinal;
                BEGIN
                    parsed := (item #>> '{}')::jsonb;
                EXCEPTION WHEN others THEN
                    RETURN FALSE;
                END;
                IF parsed IS DISTINCT FROM criterion THEN
                    RETURN FALSE;
                END IF;
                ordinal := ordinal + 1;
            END LOOP;
            RETURN TRUE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION review_candidate_content_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            option_ids text[] := ARRAY[]::text[];
            option_id text;
            answer_value text;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'question_type', 'stem', 'options', 'answer', 'explanation', 'marks',
                    'marking_guide'
                ]
                OR candidate - ARRAY[
                    'question_type', 'stem', 'options', 'answer', 'explanation', 'marks',
                    'marking_guide'
                ] <> '{}'::jsonb
                OR jsonb_typeof(candidate->'question_type') <> 'string'
                OR candidate->>'question_type' NOT IN (
                    'multiple_choice', 'short_answer', 'structured', 'structured_response'
                )
                OR jsonb_typeof(candidate->'stem') <> 'string'
                OR candidate->>'stem' <> btrim(candidate->>'stem')
                OR char_length(candidate->>'stem') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'answer') <> 'string'
                OR candidate->>'answer' <> btrim(candidate->>'answer')
                OR char_length(candidate->>'answer') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'explanation') <> 'string'
                OR candidate->>'explanation' <> btrim(candidate->>'explanation')
                OR char_length(candidate->>'explanation') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'marks') <> 'number'
                OR candidate->>'marks' !~ '^[0-9]+$'
                OR (candidate->>'marks')::integer NOT BETWEEN 1 AND 100
                OR jsonb_typeof(candidate->'options') <> 'array'
                OR jsonb_array_length(candidate->'options') > 16
                OR jsonb_typeof(candidate->'marking_guide') <> 'array'
                OR jsonb_array_length(candidate->'marking_guide') NOT BETWEEN 1 AND 64
            THEN
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'options')
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY['option_id', 'text']
                    OR item - ARRAY['option_id', 'text'] <> '{}'::jsonb
                    OR jsonb_typeof(item->'option_id') <> 'string'
                    OR jsonb_typeof(item->'text') <> 'string'
                    OR item->>'option_id' <> btrim(item->>'option_id')
                    OR char_length(item->>'option_id') NOT BETWEEN 1 AND 128
                    OR item->>'text' <> btrim(item->>'text')
                    OR char_length(item->>'text') NOT BETWEEN 1 AND 8192
                THEN
                    RETURN FALSE;
                END IF;
                option_id := item->>'option_id';
                IF option_id = ANY(option_ids) THEN
                    RETURN FALSE;
                END IF;
                option_ids := array_append(option_ids, option_id);
            END LOOP;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'marking_guide')
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR item #>> '{}' <> btrim(item #>> '{}')
                    OR char_length(item #>> '{}') NOT BETWEEN 1 AND 8192
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;

            IF candidate->>'question_type' = 'multiple_choice' THEN
                answer_value := candidate->>'answer';
                IF array_length(option_ids, 1) IS NULL
                    OR array_length(option_ids, 1) NOT BETWEEN 2 AND 16
                    OR NOT answer_value = ANY(option_ids)
                THEN
                    RETURN FALSE;
                END IF;
            END IF;
            RETURN TRUE;
        EXCEPTION WHEN numeric_value_out_of_range THEN
            RETURN FALSE;
        END;
        $$
        """
    )
