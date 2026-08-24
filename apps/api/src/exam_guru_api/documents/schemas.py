from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType


class ExtractionJobResponse(BaseModel):
    document_id: UUID
    message_id: str
    status: ExtractionStatus


class ReviewedTextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed_text: str = Field(min_length=1, max_length=1_000_000)
    expected_version: int = Field(ge=0)


class SourcePageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_document_id: UUID
    page_number: int
    extractor: str
    extractor_version: str
    extraction_config: dict[str, object]
    confidence: float | None = Field(default=None, ge=0, le=1)
    raw_text: str
    reviewed_text: str | None
    character_count: int
    block_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class ExtractedBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_page_id: UUID
    source_document_id: UUID
    page_number: int
    reading_order: int
    extractor: str
    extractor_version: str
    extraction_config: dict[str, object]
    confidence: float | None = Field(default=None, ge=0, le=1)
    bbox: tuple[float, float, float, float] | None
    raw_text: str
    reviewed_text: str | None
    character_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class SourceDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    checksum_sha256: str
    original_filename: str
    content_type: str
    size_bytes: int
    document_type: SourceDocumentType
    extraction_status: ExtractionStatus
    curriculum_version_id: UUID | None
    year: int | None
    paper_code: str | None
    extraction_attempt_count: int
    extractor: str | None
    extractor_version: str | None
    extracted_page_count: int | None
    extracted_block_count: int | None
    extracted_character_count: int | None
    native_text_page_ratio: float | None
    needs_ocr: bool | None
    ocr_page_count: int | None
    extraction_config: dict[str, object] | None
    extraction_failure_code: str | None
    extraction_started_at: datetime | None
    extraction_completed_at: datetime | None
    created_at: datetime
    deduplicated: bool = False
