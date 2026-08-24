from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0017_ocr_worker_pipeline"
down_revision: str | None = "0016_published_papers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_CONFIG_JSON_BYTES = 64 * 1024
_EXTRACTION_RESULT_COLUMNS_NULL_SQL = (
    "extractor IS NULL AND extractor_version IS NULL "
    "AND extracted_page_count IS NULL AND extracted_block_count IS NULL "
    "AND extracted_character_count IS NULL AND native_text_page_ratio IS NULL "
    "AND needs_ocr IS NULL AND ocr_page_count IS NULL AND extraction_config IS NULL"
)
_EXTRACTION_RESULT_COLUMNS_PRESENT_SQL = (
    "extractor IS NOT NULL AND extractor_version IS NOT NULL "
    "AND extracted_page_count IS NOT NULL AND extracted_block_count IS NOT NULL "
    "AND extracted_character_count IS NOT NULL AND native_text_page_ratio IS NOT NULL "
    "AND needs_ocr IS NOT NULL AND ocr_page_count IS NOT NULL "
    "AND extraction_config IS NOT NULL"
)
_LEGACY_EXTRACTION_RESULT_COLUMNS_NULL_SQL = (
    "extractor IS NULL AND extractor_version IS NULL "
    "AND extracted_page_count IS NULL AND extracted_block_count IS NULL "
    "AND extracted_character_count IS NULL AND native_text_page_ratio IS NULL "
    "AND needs_ocr IS NULL"
)
_LEGACY_EXTRACTION_RESULT_COLUMNS_PRESENT_SQL = (
    "extractor IS NOT NULL AND extractor_version IS NOT NULL "
    "AND extracted_page_count IS NOT NULL AND extracted_block_count IS NOT NULL "
    "AND extracted_character_count IS NOT NULL AND native_text_page_ratio IS NOT NULL "
    "AND needs_ocr IS NOT NULL"
)


def upgrade() -> None:
    _create_config_validation_functions()
    _add_columns_and_backfill()
    _replace_constraints()
    _replace_provenance_functions(include_ocr=True)
    _create_document_ocr_provenance_trigger()


def _create_config_validation_functions() -> None:
    extraction_config_function_sql = f"""
        CREATE FUNCTION ocr_extraction_config_is_bounded(config jsonb)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
            SELECT jsonb_typeof(config) = 'object'
                AND pg_column_size(config) <= {_MAX_CONFIG_JSON_BYTES}
                AND octet_length(config::text) <= {_MAX_CONFIG_JSON_BYTES}
        $$
        """
    op.execute(extraction_config_function_sql)
    scalar_config_function_sql = f"""
        CREATE FUNCTION ocr_scalar_config_is_bounded(config jsonb)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
            SELECT jsonb_typeof(config) = 'object'
                AND pg_column_size(config) <= {_MAX_CONFIG_JSON_BYTES}
                AND octet_length(config::text) <= {_MAX_CONFIG_JSON_BYTES}
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_each(config) AS entry(key, value)
                    WHERE jsonb_typeof(entry.value) NOT IN ('string', 'number', 'boolean', 'null')
                )
        $$
        """  # noqa: S608
    op.execute(scalar_config_function_sql)


def _add_columns_and_backfill() -> None:
    op.add_column("source_documents", sa.Column("ocr_page_count", sa.Integer(), nullable=True))
    op.add_column(
        "source_documents",
        sa.Column("extraction_config", JSONB(), nullable=True),
    )
    op.add_column(
        "source_pages",
        sa.Column(
            "extraction_config",
            JSONB(),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("source_pages", sa.Column("confidence", sa.Double(), nullable=True))
    op.add_column(
        "extracted_blocks",
        sa.Column(
            "extraction_config",
            JSONB(),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("extracted_blocks", sa.Column("confidence", sa.Double(), nullable=True))

    # Every pre-0017 extraction was native-only. Empty config is honest legacy provenance;
    # assigning an OCR provider or confidence retroactively would fabricate evidence.
    op.execute(
        """
        UPDATE source_documents
        SET ocr_page_count = 0, extraction_config = '{}'::jsonb
        WHERE extraction_status IN ('extracted', 'in_review', 'trusted')
        """
    )
    op.execute("UPDATE source_pages SET extraction_config = '{}'::jsonb")
    op.execute("UPDATE extracted_blocks SET extraction_config = '{}'::jsonb")
    op.alter_column("source_pages", "extraction_config", nullable=False)
    op.alter_column("extracted_blocks", "extraction_config", nullable=False)

    op.drop_constraint("ck_extracted_blocks_bbox", "extracted_blocks", type_="check")
    for column_name in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1"):
        op.alter_column("extracted_blocks", column_name, existing_type=sa.Double(), nullable=True)


def _replace_constraints() -> None:
    op.drop_constraint(
        "ck_source_document_extraction_state_data",
        "source_documents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_source_document_ocr_page_count",
        "source_documents",
        "ocr_page_count IS NULL OR (ocr_page_count >= 0 "
        "AND extracted_page_count IS NOT NULL "
        "AND ocr_page_count <= extracted_page_count)",
    )
    op.create_check_constraint(
        "ck_source_document_extraction_config",
        "source_documents",
        "extraction_config IS NULL OR ocr_extraction_config_is_bounded(extraction_config)",
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
    op.create_check_constraint(
        "ck_source_pages_extraction_config",
        "source_pages",
        "ocr_scalar_config_is_bounded(extraction_config)",
    )
    op.create_check_constraint(
        "ck_source_pages_confidence",
        "source_pages",
        "confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0",
    )
    op.create_check_constraint(
        "ck_extracted_blocks_extraction_config",
        "extracted_blocks",
        "ocr_scalar_config_is_bounded(extraction_config)",
    )
    op.create_check_constraint(
        "ck_extracted_blocks_confidence",
        "extracted_blocks",
        "confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0",
    )
    op.create_check_constraint(
        "ck_extracted_blocks_bbox",
        "extracted_blocks",
        "(bbox_x0 IS NULL AND bbox_y0 IS NULL AND bbox_x1 IS NULL AND bbox_y1 IS NULL) "
        "OR (bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL "
        "AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL "
        "AND bbox_x0 <= bbox_x1 AND bbox_y0 <= bbox_y1)",
    )


def _create_document_ocr_provenance_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_source_document_ocr_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.extraction_status IN ('extracted', 'in_review', 'trusted')
                AND (
                    NEW.ocr_page_count IS DISTINCT FROM OLD.ocr_page_count
                    OR NEW.extraction_config IS DISTINCT FROM OLD.extraction_config
                )
            THEN
                RAISE EXCEPTION 'source document OCR provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_source_document_ocr_provenance_trigger
        BEFORE UPDATE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_source_document_ocr_provenance()
        """
    )


def _replace_provenance_functions(*, include_ocr: bool) -> None:
    page_ocr_checks = (
        """
                    OR NEW.extraction_config IS DISTINCT FROM OLD.extraction_config
                    OR NEW.confidence IS DISTINCT FROM OLD.confidence"""
        if include_ocr
        else ""
    )
    block_ocr_checks = (
        """
                    OR NEW.extraction_config IS DISTINCT FROM OLD.extraction_config
                    OR NEW.confidence IS DISTINCT FROM OLD.confidence"""
        if include_ocr
        else ""
    )
    bbox_checks = (
        """
                    OR NEW.bbox_x0 IS DISTINCT FROM OLD.bbox_x0
                    OR NEW.bbox_y0 IS DISTINCT FROM OLD.bbox_y0
                    OR NEW.bbox_x1 IS DISTINCT FROM OLD.bbox_x1
                    OR NEW.bbox_y1 IS DISTINCT FROM OLD.bbox_y1"""
        if include_ocr
        else """
                    OR NEW.bbox_x0 <> OLD.bbox_x0
                    OR NEW.bbox_y0 <> OLD.bbox_y0
                    OR NEW.bbox_x1 <> OLD.bbox_x1
                    OR NEW.bbox_y1 <> OLD.bbox_y1"""
    )
    page_provenance_function_sql = f"""
        CREATE OR REPLACE FUNCTION enforce_source_page_provenance()
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
                    OR NEW.extractor_version <> OLD.extractor_version{page_ocr_checks}
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
        """  # noqa: S608
    op.execute(page_provenance_function_sql)
    block_provenance_function_sql = f"""
        CREATE OR REPLACE FUNCTION enforce_extracted_block_provenance()
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
                    OR NEW.extractor_version <> OLD.extractor_version{block_ocr_checks}{bbox_checks}
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
        """  # noqa: S608
    op.execute(block_provenance_function_sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER enforce_source_document_ocr_provenance_trigger ON source_documents")
    op.execute("DROP FUNCTION enforce_source_document_ocr_provenance()")
    op.execute("DROP TRIGGER enforce_extracted_block_provenance_trigger ON extracted_blocks")
    _replace_provenance_functions(include_ocr=False)

    op.drop_constraint("ck_extracted_blocks_confidence", "extracted_blocks", type_="check")
    op.drop_constraint(
        "ck_extracted_blocks_extraction_config",
        "extracted_blocks",
        type_="check",
    )
    op.drop_constraint("ck_source_pages_confidence", "source_pages", type_="check")
    op.drop_constraint("ck_source_pages_extraction_config", "source_pages", type_="check")
    op.drop_constraint("ck_extracted_blocks_bbox", "extracted_blocks", type_="check")

    # The legacy schema required a bbox. Downgrade represents an unavailable OCR bbox as
    # the legacy zero rectangle rather than failing the schema rollback.
    op.execute(
        """
        UPDATE extracted_blocks
        SET bbox_x0 = 0.0, bbox_y0 = 0.0, bbox_x1 = 0.0, bbox_y1 = 0.0
        WHERE bbox_x0 IS NULL OR bbox_y0 IS NULL OR bbox_x1 IS NULL OR bbox_y1 IS NULL
        """
    )
    for column_name in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1"):
        op.alter_column("extracted_blocks", column_name, existing_type=sa.Double(), nullable=False)
    op.create_check_constraint(
        "ck_extracted_blocks_bbox",
        "extracted_blocks",
        "bbox_x0 <= bbox_x1 AND bbox_y0 <= bbox_y1",
    )

    op.drop_column("extracted_blocks", "confidence")
    op.drop_column("extracted_blocks", "extraction_config")
    op.drop_column("source_pages", "confidence")
    op.drop_column("source_pages", "extraction_config")

    op.drop_constraint(
        "ck_source_document_extraction_state_data",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_extraction_config",
        "source_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_document_ocr_page_count",
        "source_documents",
        type_="check",
    )
    op.drop_column("source_documents", "extraction_config")
    op.drop_column("source_documents", "ocr_page_count")
    op.create_check_constraint(
        "ck_source_document_extraction_state_data",
        "source_documents",
        "(extraction_status = 'uploaded' AND extraction_attempt_count = 0 "
        "AND extraction_started_at IS NULL AND extraction_completed_at IS NULL "
        f"AND extraction_failure_code IS NULL AND {_LEGACY_EXTRACTION_RESULT_COLUMNS_NULL_SQL}) "
        "OR (extraction_status = 'extraction_pending' AND extraction_attempt_count > 0 "
        "AND extraction_started_at IS NOT NULL AND extraction_completed_at IS NULL "
        f"AND extraction_failure_code IS NULL AND {_LEGACY_EXTRACTION_RESULT_COLUMNS_NULL_SQL}) "
        "OR (extraction_status = 'failed' AND extraction_attempt_count > 0 "
        "AND extraction_started_at IS NOT NULL AND extraction_completed_at IS NOT NULL "
        "AND extraction_failure_code IS NOT NULL AND "
        f"{_LEGACY_EXTRACTION_RESULT_COLUMNS_NULL_SQL}) "
        "OR (extraction_status IN ('extracted', 'in_review', 'trusted') "
        "AND extraction_attempt_count > 0 AND extraction_started_at IS NOT NULL "
        "AND extraction_completed_at IS NOT NULL AND extraction_failure_code IS NULL "
        f"AND {_LEGACY_EXTRACTION_RESULT_COLUMNS_PRESENT_SQL})",
    )

    op.execute(
        """
        CREATE TRIGGER enforce_extracted_block_provenance_trigger
        BEFORE INSERT OR UPDATE OR DELETE ON extracted_blocks
        FOR EACH ROW EXECUTE FUNCTION enforce_extracted_block_provenance()
        """
    )
    op.execute("DROP FUNCTION ocr_scalar_config_is_bounded(jsonb)")
    op.execute("DROP FUNCTION ocr_extraction_config_is_bounded(jsonb)")
