from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_knowledge_record_versions"
down_revision: str | None = "0007_knowledge_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_versioned_lifecycle_functions() -> None:
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
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_knowledge_chunk_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.source_document_id <> OLD.source_document_id
                OR NEW.page_number <> OLD.page_number
                OR NEW.source_block_id IS DISTINCT FROM OLD.source_block_id
                OR NEW.sequence <> OLD.sequence
                OR NEW.created_at <> OLD.created_at
                OR NEW.created_by <> OLD.created_by
            THEN
                RAISE EXCEPTION 'knowledge chunk provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final knowledge chunks are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'knowledge chunk version must increment by one'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state <> OLD.review_state AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state = 'in_review')
                OR (OLD.review_state = 'in_review'
                    AND NEW.review_state IN ('reviewed', 'rejected'))
            ) THEN
                RAISE EXCEPTION 'invalid knowledge chunk review transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def _restore_unversioned_lifecycle_functions() -> None:
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
            THEN
                RAISE EXCEPTION 'historical question provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final historical questions are immutable'
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
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_knowledge_chunk_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.source_document_id <> OLD.source_document_id
                OR NEW.page_number <> OLD.page_number
                OR NEW.source_block_id IS DISTINCT FROM OLD.source_block_id
                OR NEW.sequence <> OLD.sequence
            THEN
                RAISE EXCEPTION 'knowledge chunk provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final knowledge chunks are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state <> OLD.review_state AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state = 'in_review')
                OR (OLD.review_state = 'in_review'
                    AND NEW.review_state IN ('reviewed', 'rejected'))
            ) THEN
                RAISE EXCEPTION 'invalid knowledge chunk review transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.add_column(
        "historical_questions",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_historical_questions_version",
        "historical_questions",
        "version >= 0",
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_knowledge_chunks_version",
        "knowledge_chunks",
        "version >= 0",
    )

    op.execute(
        """
        CREATE FUNCTION enforce_knowledge_initial_version()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.version <> 0 THEN
                RAISE EXCEPTION 'knowledge records must start at version zero'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_historical_question_initial_version_trigger
        BEFORE INSERT ON historical_questions
        FOR EACH ROW EXECUTE FUNCTION enforce_knowledge_initial_version()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_knowledge_chunk_initial_version_trigger
        BEFORE INSERT ON knowledge_chunks
        FOR EACH ROW EXECUTE FUNCTION enforce_knowledge_initial_version()
        """
    )
    _create_versioned_lifecycle_functions()


def downgrade() -> None:
    _restore_unversioned_lifecycle_functions()
    op.execute("DROP TRIGGER enforce_knowledge_chunk_initial_version_trigger ON knowledge_chunks")
    op.execute(
        "DROP TRIGGER enforce_historical_question_initial_version_trigger ON historical_questions"
    )
    op.execute("DROP FUNCTION enforce_knowledge_initial_version()")
    op.drop_constraint(
        "ck_knowledge_chunks_version",
        "knowledge_chunks",
        type_="check",
    )
    op.drop_column("knowledge_chunks", "version")
    op.drop_constraint(
        "ck_historical_questions_version",
        "historical_questions",
        type_="check",
    )
    op.drop_column("historical_questions", "version")
