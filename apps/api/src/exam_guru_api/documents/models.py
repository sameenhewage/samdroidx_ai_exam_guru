from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.curriculum.models import AuditColumns
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.infrastructure.database import Base

_DOCUMENT_TYPES_SQL = ", ".join(f"'{value.value}'" for value in SourceDocumentType)
_EXTRACTION_STATUSES_SQL = ", ".join(f"'{value.value}'" for value in ExtractionStatus)
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


class SourceDocumentModel(AuditColumns, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint(
            f"document_type IN ({_DOCUMENT_TYPES_SQL})",
            name="source_document_type",
        ),
        CheckConstraint(
            f"extraction_status IN ({_EXTRACTION_STATUSES_SQL})",
            name="extraction_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_source_document_positive_size"),
        CheckConstraint(
            "year IS NULL OR year BETWEEN 1900 AND 2100",
            name="ck_source_document_year",
        ),
        CheckConstraint(
            "extraction_attempt_count >= 0",
            name="ck_source_document_extraction_attempt_count",
        ),
        CheckConstraint(
            "native_text_page_ratio IS NULL OR native_text_page_ratio BETWEEN 0.0 AND 1.0",
            name="ck_source_document_native_text_page_ratio",
        ),
        CheckConstraint(
            "(extracted_page_count IS NULL OR extracted_page_count > 0) "
            "AND (extracted_block_count IS NULL OR extracted_block_count >= 0) "
            "AND (extracted_character_count IS NULL OR extracted_character_count >= 0)",
            name="ck_source_document_extraction_metric_counts",
        ),
        CheckConstraint(
            "ocr_page_count IS NULL OR (ocr_page_count >= 0 "
            "AND extracted_page_count IS NOT NULL "
            "AND ocr_page_count <= extracted_page_count)",
            name="ck_source_document_ocr_page_count",
        ),
        CheckConstraint(
            "extraction_config IS NULL OR ocr_extraction_config_is_bounded(extraction_config)",
            name="ck_source_document_extraction_config",
        ),
        CheckConstraint(
            "(extractor IS NULL AND extractor_version IS NULL) OR "
            "(extractor = btrim(extractor) AND length(extractor) > 0 "
            "AND extractor_version = btrim(extractor_version) "
            "AND length(extractor_version) > 0)",
            name="ck_source_document_extractor_metadata",
        ),
        CheckConstraint(
            "extraction_failure_code IS NULL OR "
            "(extraction_failure_code = btrim(extraction_failure_code) "
            "AND length(extraction_failure_code) > 0)",
            name="ck_source_document_extraction_failure_code",
        ),
        CheckConstraint(
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
            name="ck_source_document_extraction_state_data",
        ),
        Index("ix_source_documents_status", "extraction_status"),
        Index("ix_source_documents_curriculum", "curriculum_version_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[SourceDocumentType] = mapped_column(
        Enum(
            SourceDocumentType,
            name="source_document_type",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=32,
        ),
        nullable=False,
    )
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        Enum(
            ExtractionStatus,
            name="extraction_status",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=32,
        ),
        nullable=False,
        default=ExtractionStatus.UPLOADED,
        server_default=ExtractionStatus.UPLOADED.value,
    )
    curriculum_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    extractor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extracted_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_block_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_character_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    native_text_page_ratio: Mapped[float | None] = mapped_column(Double, nullable=True)
    needs_ocr: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ocr_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extraction_config: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    extraction_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    extraction_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class SourcePageModel(AuditColumns, Base):
    __tablename__ = "source_pages"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "page_number",
            name="uq_source_pages_document_page",
        ),
        UniqueConstraint(
            "id",
            "source_document_id",
            "page_number",
            name="uq_source_pages_identity_provenance",
        ),
        CheckConstraint("page_number > 0", name="ck_source_pages_positive_page_number"),
        CheckConstraint(
            "extractor = btrim(extractor) AND length(extractor) > 0 "
            "AND extractor_version = btrim(extractor_version) "
            "AND length(extractor_version) > 0",
            name="ck_source_pages_extractor_metadata",
        ),
        CheckConstraint(
            "ocr_scalar_config_is_bounded(extraction_config)",
            name="ck_source_pages_extraction_config",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0",
            name="ck_source_pages_confidence",
        ),
        CheckConstraint(
            "character_count = char_length(raw_text)",
            name="ck_source_pages_character_count",
        ),
        CheckConstraint("block_count >= 0", name="ck_source_pages_block_count"),
        CheckConstraint("version >= 0", name="ck_source_pages_version"),
        Index("ix_source_pages_document", "source_document_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_config: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    confidence: Mapped[float | None] = mapped_column(Double, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ExtractedBlockModel(AuditColumns, Base):
    __tablename__ = "extracted_blocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_page_id", "source_document_id", "page_number"],
            ["source_pages.id", "source_pages.source_document_id", "source_pages.page_number"],
            name="fk_extracted_blocks_source_page_provenance",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_page_id",
            "reading_order",
            name="uq_extracted_blocks_page_reading_order",
        ),
        CheckConstraint("page_number > 0", name="ck_extracted_blocks_positive_page_number"),
        CheckConstraint("reading_order >= 0", name="ck_extracted_blocks_reading_order"),
        CheckConstraint(
            "extractor = btrim(extractor) AND length(extractor) > 0 "
            "AND extractor_version = btrim(extractor_version) "
            "AND length(extractor_version) > 0",
            name="ck_extracted_blocks_extractor_metadata",
        ),
        CheckConstraint(
            "ocr_scalar_config_is_bounded(extraction_config)",
            name="ck_extracted_blocks_extraction_config",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0",
            name="ck_extracted_blocks_confidence",
        ),
        CheckConstraint(
            "length(raw_text) > 0 AND character_count = char_length(raw_text)",
            name="ck_extracted_blocks_character_count",
        ),
        CheckConstraint(
            "(bbox_x0 IS NULL AND bbox_y0 IS NULL AND bbox_x1 IS NULL AND bbox_y1 IS NULL) "
            "OR (bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL "
            "AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL "
            "AND bbox_x0 <= bbox_x1 AND bbox_y0 <= bbox_y1)",
            name="ck_extracted_blocks_bbox",
        ),
        CheckConstraint("version >= 0", name="ck_extracted_blocks_version"),
        Index(
            "ix_extracted_blocks_document_page_order",
            "source_document_id",
            "page_number",
            "reading_order",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_page_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_config: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    confidence: Mapped[float | None] = mapped_column(Double, nullable=True)
    bbox_x0: Mapped[float | None] = mapped_column(Double, nullable=True)
    bbox_y0: Mapped[float | None] = mapped_column(Double, nullable=True)
    bbox_x1: Mapped[float | None] = mapped_column(Double, nullable=True)
    bbox_y1: Mapped[float | None] = mapped_column(Double, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if (
            self.bbox_x0 is None
            or self.bbox_y0 is None
            or self.bbox_x1 is None
            or self.bbox_y1 is None
        ):
            return None
        return self.bbox_x0, self.bbox_y0, self.bbox_x1, self.bbox_y1
