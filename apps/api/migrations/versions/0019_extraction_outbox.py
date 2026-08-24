from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_extraction_outbox"
down_revision: str | None = "0018_embedding_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column("extraction_queue_message_id", sa.String(128), nullable=True),
    )
    # Existing uploaded, pending, failed, and completed rows have no durable broker identity.
    # NULL preserves that fact instead of fabricating queue provenance during migration.
    op.create_check_constraint(
        "ck_source_document_extraction_queue_message_id",
        "source_documents",
        "extraction_queue_message_id IS NULL OR "
        "(extraction_queue_message_id = btrim(extraction_queue_message_id) AND "
        "char_length(extraction_queue_message_id) BETWEEN 1 AND 128 AND "
        "extraction_queue_message_id !~ '[[:space:][:cntrl:]]')",
    )
    op.create_check_constraint(
        "ck_source_document_extraction_queue_state",
        "source_documents",
        "extraction_queue_message_id IS NULL OR "
        "(extraction_status <> 'uploaded' AND extraction_attempt_count > 0)",
    )
    op.create_index(
        "ix_source_documents_extraction_outbox",
        "source_documents",
        ["extraction_started_at", "id"],
        postgresql_where=sa.text(
            "extraction_status = 'extraction_pending' AND extraction_queue_message_id IS NULL"
        ),
    )
    op.execute(
        """
        CREATE FUNCTION enforce_source_document_extraction_queue_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.extraction_queue_message_id IS NULL
                AND NEW.extraction_queue_message_id IS NOT NULL
                AND NOT (
                    OLD.extraction_status = 'extraction_pending'
                    AND NEW.extraction_status = 'extraction_pending'
                    AND NEW.extraction_attempt_count = OLD.extraction_attempt_count
                )
            THEN
                RAISE EXCEPTION 'extraction queue identity can only attach to a pending attempt'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.extraction_queue_message_id IS NOT NULL
                AND NEW.extraction_queue_message_id IS DISTINCT
                    FROM OLD.extraction_queue_message_id
                AND NOT (
                    OLD.extraction_status = 'failed'
                    AND NEW.extraction_status = 'extraction_pending'
                    AND NEW.extraction_attempt_count = OLD.extraction_attempt_count + 1
                    AND NEW.extraction_queue_message_id IS NULL
                )
            THEN
                RAISE EXCEPTION 'extraction queue identity is immutable during an attempt'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.extraction_status = 'failed'
                AND NEW.extraction_status = 'extraction_pending'
                AND NEW.extraction_attempt_count > OLD.extraction_attempt_count
                AND NEW.extraction_queue_message_id IS NOT NULL
            THEN
                RAISE EXCEPTION 'a new extraction queue attempt must clear its old identity'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_source_document_extraction_queue_identity_trigger
        BEFORE UPDATE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_source_document_extraction_queue_identity()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER enforce_source_document_extraction_queue_identity_trigger ON source_documents"
    )
    op.execute("DROP FUNCTION enforce_source_document_extraction_queue_identity()")
    op.drop_index("ix_source_documents_extraction_outbox", table_name="source_documents")
    op.drop_constraint(
        "ck_source_document_extraction_queue_state",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_extraction_queue_message_id",
        "source_documents",
        type_="check",
    )
    op.drop_column("source_documents", "extraction_queue_message_id")
