from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_extraction_persistence"
down_revision: str | None = "0005_source_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXTRACTION_RESULT_COLUMNS_NULL_SQL = (
    "extractor IS NULL AND extractor_version IS NULL "
    "AND extracted_page_count IS NULL AND extracted_block_count IS NULL "
    "AND extracted_character_count IS NULL AND native_text_page_ratio IS NULL "
    "AND needs_ocr IS NULL"
)
_EXTRACTION_RESULT_COLUMNS_PRESENT_SQL = (
    "extractor IS NOT NULL AND extractor_version IS NOT NULL "
    "AND extracted_page_count IS NOT NULL AND extracted_block_count IS NOT NULL "
    "AND extracted_character_count IS NOT NULL AND native_text_page_ratio IS NOT NULL "
    "AND needs_ocr IS NOT NULL"
)


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column(
            "extraction_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("source_documents", sa.Column("extractor", sa.String(64), nullable=True))
    op.add_column(
        "source_documents",
        sa.Column("extractor_version", sa.String(128), nullable=True),
    )
    op.add_column(
        "source_documents",
        sa.Column("extracted_page_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "source_documents",
        sa.Column("extracted_block_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "source_documents",
        sa.Column("extracted_character_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "source_documents",
        sa.Column("native_text_page_ratio", sa.Double(), nullable=True),
    )
    op.add_column("source_documents", sa.Column("needs_ocr", sa.Boolean(), nullable=True))
    op.add_column(
        "source_documents",
        sa.Column("extraction_failure_code", sa.String(64), nullable=True),
    )
    op.add_column(
        "source_documents",
        sa.Column("extraction_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "source_documents",
        sa.Column("extraction_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_source_document_extraction_attempt_count",
        "source_documents",
        "extraction_attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_source_document_native_text_page_ratio",
        "source_documents",
        "native_text_page_ratio IS NULL OR native_text_page_ratio BETWEEN 0.0 AND 1.0",
    )
    op.create_check_constraint(
        "ck_source_document_extraction_metric_counts",
        "source_documents",
        "(extracted_page_count IS NULL OR extracted_page_count > 0) "
        "AND (extracted_block_count IS NULL OR extracted_block_count >= 0) "
        "AND (extracted_character_count IS NULL OR extracted_character_count >= 0)",
    )
    op.create_check_constraint(
        "ck_source_document_extractor_metadata",
        "source_documents",
        "(extractor IS NULL AND extractor_version IS NULL) OR "
        "(extractor = btrim(extractor) AND length(extractor) > 0 "
        "AND extractor_version = btrim(extractor_version) "
        "AND length(extractor_version) > 0)",
    )
    op.create_check_constraint(
        "ck_source_document_extraction_failure_code",
        "source_documents",
        "extraction_failure_code IS NULL OR "
        "(extraction_failure_code = btrim(extraction_failure_code) "
        "AND length(extraction_failure_code) > 0)",
    )
    op.create_check_constraint(
        "ck_source_document_extraction_state_data",
        "source_documents",
        "(extraction_status = 'uploaded' AND extraction_attempt_count = 0 "
        "AND extraction_started_at IS NULL AND extraction_completed_at IS NULL "
        f"AND extraction_failure_code IS NULL AND {_EXTRACTION_RESULT_COLUMNS_NULL_SQL}) "
        "OR (extraction_status = 'extraction_pending' AND extraction_attempt_count > 0 "
        "AND extraction_started_at IS NOT NULL AND extraction_completed_at IS NULL "
        f"AND extraction_failure_code IS NULL AND {_EXTRACTION_RESULT_COLUMNS_NULL_SQL}) "
        "OR (extraction_status = 'failed' AND extraction_attempt_count > 0 "
        "AND extraction_started_at IS NOT NULL AND extraction_completed_at IS NOT NULL "
        f"AND extraction_failure_code IS NOT NULL AND {_EXTRACTION_RESULT_COLUMNS_NULL_SQL}) "
        "OR (extraction_status IN ('extracted', 'in_review', 'trusted') "
        "AND extraction_attempt_count > 0 AND extraction_started_at IS NOT NULL "
        "AND extraction_completed_at IS NOT NULL AND extraction_failure_code IS NULL "
        f"AND {_EXTRACTION_RESULT_COLUMNS_PRESENT_SQL})",
    )

    op.create_table(
        "source_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(128), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("reviewed_text", sa.Text(), nullable=True),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("block_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "page_number > 0",
            name="ck_source_pages_positive_page_number",
        ),
        sa.CheckConstraint(
            "extractor = btrim(extractor) AND length(extractor) > 0 "
            "AND extractor_version = btrim(extractor_version) "
            "AND length(extractor_version) > 0",
            name="ck_source_pages_extractor_metadata",
        ),
        sa.CheckConstraint(
            "character_count = char_length(raw_text)",
            name="ck_source_pages_character_count",
        ),
        sa.CheckConstraint("block_count >= 0", name="ck_source_pages_block_count"),
        sa.CheckConstraint("version >= 0", name="ck_source_pages_version"),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_source_pages_source_document",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_document_id",
            "page_number",
            name="uq_source_pages_document_page",
        ),
        sa.UniqueConstraint(
            "id",
            "source_document_id",
            "page_number",
            name="uq_source_pages_identity_provenance",
        ),
    )
    op.create_index(
        "ix_source_pages_document",
        "source_pages",
        ["source_document_id"],
    )

    op.create_table(
        "extracted_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_page_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(128), nullable=False),
        sa.Column("bbox_x0", sa.Double(), nullable=False),
        sa.Column("bbox_y0", sa.Double(), nullable=False),
        sa.Column("bbox_x1", sa.Double(), nullable=False),
        sa.Column("bbox_y1", sa.Double(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("reviewed_text", sa.Text(), nullable=True),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "page_number > 0",
            name="ck_extracted_blocks_positive_page_number",
        ),
        sa.CheckConstraint(
            "reading_order >= 0",
            name="ck_extracted_blocks_reading_order",
        ),
        sa.CheckConstraint(
            "extractor = btrim(extractor) AND length(extractor) > 0 "
            "AND extractor_version = btrim(extractor_version) "
            "AND length(extractor_version) > 0",
            name="ck_extracted_blocks_extractor_metadata",
        ),
        sa.CheckConstraint(
            "length(raw_text) > 0 AND character_count = char_length(raw_text)",
            name="ck_extracted_blocks_character_count",
        ),
        sa.CheckConstraint(
            "bbox_x0 <= bbox_x1 AND bbox_y0 <= bbox_y1",
            name="ck_extracted_blocks_bbox",
        ),
        sa.CheckConstraint("version >= 0", name="ck_extracted_blocks_version"),
        sa.ForeignKeyConstraint(
            ["source_page_id", "source_document_id", "page_number"],
            ["source_pages.id", "source_pages.source_document_id", "source_pages.page_number"],
            name="fk_extracted_blocks_source_page_provenance",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_page_id",
            "reading_order",
            name="uq_extracted_blocks_page_reading_order",
        ),
    )
    op.create_index(
        "ix_extracted_blocks_document_page_order",
        "extracted_blocks",
        ["source_document_id", "page_number", "reading_order"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_source_document_extraction_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            persisted_page_count bigint;
            persisted_block_count bigint;
            persisted_character_count bigint;
            first_page integer;
            last_page integer;
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.checksum_sha256 <> OLD.checksum_sha256
                OR NEW.object_key <> OLD.object_key
                OR NEW.content_type <> OLD.content_type
                OR NEW.size_bytes <> OLD.size_bytes
            THEN
                RAISE EXCEPTION 'source document identity is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.extraction_attempt_count < OLD.extraction_attempt_count THEN
                RAISE EXCEPTION 'extraction attempt count cannot decrease'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.extraction_attempt_count <> OLD.extraction_attempt_count
                AND NEW.extraction_status <> 'extraction_pending'
            THEN
                RAISE EXCEPTION 'only a pending extraction can increment attempt count'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.extraction_status <> OLD.extraction_status AND NOT (
                (OLD.extraction_status = 'uploaded'
                    AND NEW.extraction_status = 'extraction_pending')
                OR (OLD.extraction_status = 'extraction_pending'
                    AND NEW.extraction_status IN ('extracted', 'failed'))
                OR (OLD.extraction_status = 'failed'
                    AND NEW.extraction_status = 'extraction_pending')
                OR (OLD.extraction_status = 'extracted'
                    AND NEW.extraction_status = 'in_review')
                OR (OLD.extraction_status = 'in_review'
                    AND NEW.extraction_status = 'trusted')
            ) THEN
                RAISE EXCEPTION 'invalid source document extraction transition'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.extraction_status IN ('extracted', 'in_review', 'trusted') THEN
                SELECT
                    count(*),
                    min(page_number),
                    max(page_number),
                    coalesce(sum(character_count), 0),
                    coalesce(sum(block_count), 0)
                INTO
                    persisted_page_count,
                    first_page,
                    last_page,
                    persisted_character_count,
                    persisted_block_count
                FROM source_pages
                WHERE source_document_id = NEW.id;

                IF persisted_page_count = 0
                    OR persisted_page_count <> NEW.extracted_page_count
                    OR first_page <> 1
                    OR last_page <> persisted_page_count
                    OR persisted_character_count <> NEW.extracted_character_count
                    OR persisted_block_count <> NEW.extracted_block_count
                THEN
                    RAISE EXCEPTION 'source document extraction metrics do not match pages'
                        USING ERRCODE = '23514';
                END IF;

                SELECT count(*)
                INTO persisted_block_count
                FROM extracted_blocks
                WHERE source_document_id = NEW.id;

                IF persisted_block_count <> NEW.extracted_block_count OR EXISTS (
                    SELECT 1
                    FROM source_pages AS page
                    WHERE page.source_document_id = NEW.id
                      AND (
                          page.block_count <> (
                              SELECT count(*)
                              FROM extracted_blocks AS block
                              WHERE block.source_page_id = page.id
                          )
                          OR (
                              page.block_count > 0
                              AND (
                                  (
                                      SELECT min(reading_order)
                                      FROM extracted_blocks AS block
                                      WHERE block.source_page_id = page.id
                                  ) <> 0
                                  OR (
                                      SELECT max(reading_order)
                                      FROM extracted_blocks AS block
                                      WHERE block.source_page_id = page.id
                                  ) <> page.block_count - 1
                              )
                          )
                      )
                ) THEN
                    RAISE EXCEPTION 'extraction metrics or order do not match blocks'
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
        CREATE TRIGGER enforce_source_document_extraction_state_trigger
        BEFORE UPDATE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_source_document_extraction_state()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_source_page_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            document_status text;
        BEGIN
            SELECT extraction_status INTO document_status
            FROM source_documents
            WHERE id = coalesce(NEW.source_document_id, OLD.source_document_id);

            IF TG_OP = 'INSERT' AND document_status <> 'extraction_pending' THEN
                RAISE EXCEPTION 'source pages can only be inserted while extraction is pending'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF NEW.id <> OLD.id
                    OR NEW.source_document_id <> OLD.source_document_id
                    OR NEW.page_number <> OLD.page_number
                    OR NEW.extractor <> OLD.extractor
                    OR NEW.extractor_version <> OLD.extractor_version
                    OR NEW.raw_text <> OLD.raw_text
                    OR NEW.character_count <> OLD.character_count
                    OR NEW.block_count <> OLD.block_count
                THEN
                    RAISE EXCEPTION 'source page provenance and raw extraction are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.reviewed_text IS DISTINCT FROM OLD.reviewed_text
                    AND document_status <> 'in_review'
                THEN
                    RAISE EXCEPTION 'source page review text requires in-review state'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            IF TG_OP = 'DELETE' AND document_status <> 'extraction_pending' THEN
                RAISE EXCEPTION 'source pages can only be deleted while extraction is pending'
                    USING ERRCODE = '23514';
            END IF;

            RETURN coalesce(NEW, OLD);
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_source_page_provenance_trigger
        BEFORE INSERT OR UPDATE OR DELETE ON source_pages
        FOR EACH ROW EXECUTE FUNCTION enforce_source_page_provenance()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_extracted_block_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            document_status text;
        BEGIN
            SELECT extraction_status INTO document_status
            FROM source_documents
            WHERE id = coalesce(NEW.source_document_id, OLD.source_document_id);

            IF TG_OP = 'INSERT' AND document_status <> 'extraction_pending' THEN
                RAISE EXCEPTION 'blocks can only be inserted while extraction is pending'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF NEW.id <> OLD.id
                    OR NEW.source_page_id <> OLD.source_page_id
                    OR NEW.source_document_id <> OLD.source_document_id
                    OR NEW.page_number <> OLD.page_number
                    OR NEW.reading_order <> OLD.reading_order
                    OR NEW.extractor <> OLD.extractor
                    OR NEW.extractor_version <> OLD.extractor_version
                    OR NEW.bbox_x0 <> OLD.bbox_x0
                    OR NEW.bbox_y0 <> OLD.bbox_y0
                    OR NEW.bbox_x1 <> OLD.bbox_x1
                    OR NEW.bbox_y1 <> OLD.bbox_y1
                    OR NEW.raw_text <> OLD.raw_text
                    OR NEW.character_count <> OLD.character_count
                THEN
                    RAISE EXCEPTION 'block provenance and raw extraction are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.reviewed_text IS DISTINCT FROM OLD.reviewed_text
                    AND document_status <> 'in_review'
                THEN
                    RAISE EXCEPTION 'block review text requires in-review state'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            IF TG_OP = 'DELETE' AND document_status <> 'extraction_pending' THEN
                RAISE EXCEPTION 'blocks can only be deleted while extraction is pending'
                    USING ERRCODE = '23514';
            END IF;

            RETURN coalesce(NEW, OLD);
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_extracted_block_provenance_trigger
        BEFORE INSERT OR UPDATE OR DELETE ON extracted_blocks
        FOR EACH ROW EXECUTE FUNCTION enforce_extracted_block_provenance()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER enforce_extracted_block_provenance_trigger ON extracted_blocks")
    op.execute("DROP FUNCTION enforce_extracted_block_provenance()")
    op.execute("DROP TRIGGER enforce_source_page_provenance_trigger ON source_pages")
    op.execute("DROP FUNCTION enforce_source_page_provenance()")
    op.execute("DROP TRIGGER enforce_source_document_extraction_state_trigger ON source_documents")
    op.execute("DROP FUNCTION enforce_source_document_extraction_state()")

    op.drop_index(
        "ix_extracted_blocks_document_page_order",
        table_name="extracted_blocks",
    )
    op.drop_table("extracted_blocks")
    op.drop_index("ix_source_pages_document", table_name="source_pages")
    op.drop_table("source_pages")

    op.drop_constraint(
        "ck_source_document_extraction_state_data",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_extraction_failure_code",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_extractor_metadata",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_extraction_metric_counts",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_native_text_page_ratio",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_extraction_attempt_count",
        "source_documents",
        type_="check",
    )
    op.drop_column("source_documents", "extraction_completed_at")
    op.drop_column("source_documents", "extraction_started_at")
    op.drop_column("source_documents", "extraction_failure_code")
    op.drop_column("source_documents", "needs_ocr")
    op.drop_column("source_documents", "native_text_page_ratio")
    op.drop_column("source_documents", "extracted_character_count")
    op.drop_column("source_documents", "extracted_block_count")
    op.drop_column("source_documents", "extracted_page_count")
    op.drop_column("source_documents", "extractor_version")
    op.drop_column("source_documents", "extractor")
    op.drop_column("source_documents", "extraction_attempt_count")
