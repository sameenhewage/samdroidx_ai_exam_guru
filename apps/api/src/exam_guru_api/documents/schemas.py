from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exam_guru_api.documents.domain import (
    ExtractionStatus,
    MaterialUseState,
    SourceDocumentType,
)


class ExtractionJobResponse(BaseModel):
    document_id: UUID
    message_id: str
    status: ExtractionStatus


class ReviewedTextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed_text: str = Field(min_length=1, max_length=1_000_000)
    expected_version: int = Field(ge=0)


class MaterialRemoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=512)
    expected_version: int = Field(strict=True, ge=0)


class MaterialRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(strict=True, ge=0)


class MaterialScopeCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curriculum_version_id: UUID | None
    unit_id: UUID | None = None
    lesson_id: UUID | None = None
    expected_version: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_scope_shape(self) -> Self:
        if self.unit_id is not None and self.curriculum_version_id is None:
            raise ValueError("unit_id requires curriculum_version_id")
        if self.lesson_id is not None and self.unit_id is None:
            raise ValueError("lesson_id requires unit_id")
        return self


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
    subject_id: UUID | None = None
    unit_id: UUID | None
    lesson_id: UUID | None
    active_for_ai: bool
    use_state: MaterialUseState
    removal_reason: str | None
    removed_by: UUID | None
    removed_at: datetime | None
    metadata_scope_version: int
    year: int | None
    paper_code: str | None
    extraction_attempt_count: int
    extraction_queue_message_id: str | None = Field(default=None, max_length=128)
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
    likely_metadata_duplicate_of_id: UUID | None = None


class MaterialStatus(StrEnum):
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_AI = "ready_for_ai"
    REMOVED = "removed"


class MaterialListItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    grade: int | None
    subject_id: UUID | None
    subject: str | None
    medium: str | None
    curriculum: str | None
    unit: str | None
    lesson: str | None
    material_type: SourceDocumentType
    status: MaterialStatus
    year: int | None
    page_count: int | None
    uploaded_at: datetime
    metadata_scope_version: int


class MaterialGradeSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    grade: int = Field(ge=1, le=13)
    material_count: int = Field(ge=0)
    subject_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    needs_review_count: int = Field(ge=0)
    processing_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
