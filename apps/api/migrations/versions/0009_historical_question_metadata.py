from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009_historical_question_metadata"
down_revision: str | None = "0008_knowledge_record_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIFFICULTY_LABELS_SQL = "'easy', 'medium', 'hard'"
_MAX_MEDIA_REFERENCES = 32
_MAX_MEDIA_REFERENCE_CHARACTERS = 2_048
_MIN_QUESTION_OPTIONS = 2
_MAX_QUESTION_OPTIONS = 8
_MAX_QUESTION_OPTION_CHARACTERS = 2_000
_MAX_ANSWER_CHARACTERS = 8_000
_MAX_MARKING_GUIDANCE_CHARACTERS = 16_000
_MAX_MARKING_DATA_BYTES = 65_536
_MAX_QUESTION_ARCHETYPE_CHARACTERS = 128
_MAX_DIFFICULTY_SOURCE_CHARACTERS = 128


def _create_metadata_validation_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION historical_question_text_array_valid(
            candidate jsonb,
            minimum_items integer,
            maximum_items integer,
            maximum_characters integer
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            item_text text;
            item_count integer;
            distinct_item_count integer;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) NOT BETWEEN minimum_items AND maximum_items
            THEN
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'string' THEN
                    RETURN FALSE;
                END IF;
                item_text := item #>> '{}';
                IF item_text <> btrim(item_text)
                    OR char_length(item_text) NOT BETWEEN 1 AND maximum_characters
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;

            SELECT count(*), count(DISTINCT value)
            INTO item_count, distinct_item_count
            FROM jsonb_array_elements(candidate);
            RETURN item_count = distinct_item_count;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION historical_question_exact_answer_valid(
            candidate_options jsonb,
            candidate_answer text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        BEGIN
            IF jsonb_typeof(candidate_options) <> 'array' THEN
                RETURN FALSE;
            END IF;
            RETURN EXISTS (
                SELECT 1
                FROM jsonb_array_elements(candidate_options) AS option_value
                WHERE option_value #>> '{}' = candidate_answer
            );
        END;
        $$
        """
    )


def _create_versioned_lifecycle_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_historical_question_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.source_document_id <> OLD.source_document_id
                OR NEW.page_number <> OLD.page_number
                OR NEW.source_block_id IS DISTINCT FROM OLD.source_block_id
                OR NEW.year <> OLD.year
                OR NEW.paper_code <> OLD.paper_code
                OR NEW.question_number <> OLD.question_number
                OR NEW.media_references IS DISTINCT FROM OLD.media_references
                OR NEW.options IS DISTINCT FROM OLD.options
                OR NEW.answer IS DISTINCT FROM OLD.answer
                OR NEW.marking_guidance IS DISTINCT FROM OLD.marking_guidance
                OR NEW.marking_data IS DISTINCT FROM OLD.marking_data
                OR NEW.question_archetype IS DISTINCT FROM OLD.question_archetype
                OR NEW.difficulty_label IS DISTINCT FROM OLD.difficulty_label
                OR NEW.difficulty_confidence IS DISTINCT FROM OLD.difficulty_confidence
                OR NEW.difficulty_source IS DISTINCT FROM OLD.difficulty_source
                OR NEW.created_at <> OLD.created_at
                OR NEW.created_by <> OLD.created_by
            THEN
                RAISE EXCEPTION 'historical question provenance and source metadata are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final historical questions are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'historical question version must increment by one'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state <> OLD.review_state AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state = 'in_review')
                OR (OLD.review_state = 'in_review'
                    AND NEW.review_state IN ('reviewed', 'rejected'))
            ) THEN
                RAISE EXCEPTION 'invalid historical question review transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def _restore_versioned_lifecycle_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_historical_question_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.source_document_id <> OLD.source_document_id
                OR NEW.page_number <> OLD.page_number
                OR NEW.source_block_id IS DISTINCT FROM OLD.source_block_id
                OR NEW.year <> OLD.year
                OR NEW.paper_code <> OLD.paper_code
                OR NEW.question_number <> OLD.question_number
                OR NEW.created_at <> OLD.created_at
                OR NEW.created_by <> OLD.created_by
            THEN
                RAISE EXCEPTION 'historical question provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final historical questions are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'historical question version must increment by one'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state <> OLD.review_state AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state = 'in_review')
                OR (OLD.review_state = 'in_review'
                    AND NEW.review_state IN ('reviewed', 'rejected'))
            ) THEN
                RAISE EXCEPTION 'invalid historical question review transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
    op.add_column("historical_questions", sa.Column("media_references", JSONB(), nullable=True))
    op.add_column("historical_questions", sa.Column("options", JSONB(), nullable=True))
    op.add_column("historical_questions", sa.Column("answer", sa.Text(), nullable=True))
    op.add_column("historical_questions", sa.Column("marking_guidance", sa.Text(), nullable=True))
    op.add_column("historical_questions", sa.Column("marking_data", JSONB(), nullable=True))
    op.add_column(
        "historical_questions",
        sa.Column("question_archetype", sa.String(128), nullable=True),
    )
    op.add_column(
        "historical_questions",
        sa.Column(
            "difficulty_label",
            sa.Enum(
                "easy",
                "medium",
                "hard",
                name="historical_question_difficulty_label",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "historical_questions",
        sa.Column("difficulty_confidence", sa.Double(), nullable=True),
    )
    op.add_column(
        "historical_questions",
        sa.Column("difficulty_source", sa.String(128), nullable=True),
    )
    _create_metadata_validation_functions()
    op.create_check_constraint(
        "ck_historical_questions_metadata_media_references",
        "historical_questions",
        "media_references IS NULL OR historical_question_text_array_valid("
        f"media_references, 1, {_MAX_MEDIA_REFERENCES}, {_MAX_MEDIA_REFERENCE_CHARACTERS})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_options",
        "historical_questions",
        "options IS NULL OR historical_question_text_array_valid("
        f"options, {_MIN_QUESTION_OPTIONS}, {_MAX_QUESTION_OPTIONS}, "
        f"{_MAX_QUESTION_OPTION_CHARACTERS})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_answer",
        "historical_questions",
        "answer IS NULL OR (answer = btrim(answer) AND "
        f"char_length(answer) BETWEEN 1 AND {_MAX_ANSWER_CHARACTERS})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_marking_guidance",
        "historical_questions",
        "marking_guidance IS NULL OR (marking_guidance = btrim(marking_guidance) AND "
        "char_length(marking_guidance) BETWEEN 1 AND "
        f"{_MAX_MARKING_GUIDANCE_CHARACTERS})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_marking_data",
        "historical_questions",
        "marking_data IS NULL OR (jsonb_typeof(marking_data) = 'object' AND "
        "marking_data <> '{}'::jsonb AND "
        f"pg_column_size(marking_data) <= {_MAX_MARKING_DATA_BYTES})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_question_archetype",
        "historical_questions",
        "question_archetype IS NULL OR (question_archetype = btrim(question_archetype) AND "
        f"char_length(question_archetype) BETWEEN 1 AND {_MAX_QUESTION_ARCHETYPE_CHARACTERS})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_difficulty_evidence",
        "historical_questions",
        "(difficulty_label IS NULL AND difficulty_confidence IS NULL AND "
        "difficulty_source IS NULL) OR (difficulty_label IS NOT NULL AND "
        "difficulty_confidence IS NOT NULL AND difficulty_source IS NOT NULL AND "
        f"difficulty_label IN ({_DIFFICULTY_LABELS_SQL}) AND "
        "difficulty_source = btrim(difficulty_source) AND "
        f"char_length(difficulty_source) BETWEEN 1 AND {_MAX_DIFFICULTY_SOURCE_CHARACTERS})",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_difficulty_confidence",
        "historical_questions",
        "difficulty_confidence IS NULL OR (difficulty_confidence BETWEEN 0.0 AND 1.0 AND "
        "difficulty_confidence NOT IN ('NaN'::double precision, "
        "'Infinity'::double precision, '-Infinity'::double precision))",
    )
    op.create_check_constraint(
        "ck_historical_questions_metadata_mcq_answer",
        "historical_questions",
        "question_type <> 'multiple_choice' OR options IS NULL OR answer IS NULL OR "
        "historical_question_exact_answer_valid(options, answer)",
    )
    _create_versioned_lifecycle_function()


def downgrade() -> None:
    _restore_versioned_lifecycle_function()
    for constraint_name in (
        "ck_historical_questions_metadata_mcq_answer",
        "ck_historical_questions_metadata_difficulty_confidence",
        "ck_historical_questions_metadata_difficulty_evidence",
        "ck_historical_questions_metadata_question_archetype",
        "ck_historical_questions_metadata_marking_data",
        "ck_historical_questions_metadata_marking_guidance",
        "ck_historical_questions_metadata_answer",
        "ck_historical_questions_metadata_options",
        "ck_historical_questions_metadata_media_references",
    ):
        op.drop_constraint(constraint_name, "historical_questions", type_="check")
    for column_name in (
        "difficulty_source",
        "difficulty_confidence",
        "difficulty_label",
        "question_archetype",
        "marking_data",
        "marking_guidance",
        "answer",
        "options",
        "media_references",
    ):
        op.drop_column("historical_questions", column_name)
    op.execute("DROP FUNCTION historical_question_exact_answer_valid(jsonb, text)")
    op.execute(
        "DROP FUNCTION historical_question_text_array_valid(jsonb, integer, integer, integer)"
    )
