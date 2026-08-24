from collections.abc import Sequence

from alembic import op

revision: str = "0010_historical_answer_encoding"
down_revision: str | None = "0009_historical_question_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_historical_questions_metadata_mcq_answer",
        "historical_questions",
        type_="check",
    )
    op.execute("DROP FUNCTION historical_question_exact_answer_valid(jsonb, text)")


def downgrade() -> None:
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
    op.create_check_constraint(
        "ck_historical_questions_metadata_mcq_answer",
        "historical_questions",
        "question_type <> 'multiple_choice' OR options IS NULL OR answer IS NULL OR "
        "historical_question_exact_answer_valid(options, answer)",
    )
