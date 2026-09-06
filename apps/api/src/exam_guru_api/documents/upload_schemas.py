from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exam_guru_api.documents.domain import SourceDocumentType, validate_pdf_upload
from exam_guru_api.documents.schemas import SourceIntakeMetadata

UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
UPLOAD_RECEIPT_PAGE_SIZE = 64
MAX_UPLOAD_INTEGER = 2**63 - 1
UploadChecksum = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class UploadStatus(StrEnum):
    UPLOADING = "uploading"
    PENDING = "pending"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceUploadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    request_id: UUID | None = None
    filename: str = Field(strict=True, min_length=5, max_length=255)
    size_bytes: int = Field(strict=True, ge=5, le=MAX_UPLOAD_INTEGER)
    document_type: SourceDocumentType
    intake_metadata: SourceIntakeMetadata = Field(default_factory=SourceIntakeMetadata)
    expected_checksum_sha256: UploadChecksum | None = None
    curriculum_version_id: UUID | None = None
    unit_id: UUID | None = None
    lesson_id: UUID | None = None
    year: int | None = Field(default=None, strict=True, ge=1900, le=2100)
    paper_code: str | None = Field(default=None, strict=True, min_length=1, max_length=64)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return validate_pdf_upload(
            filename=value, content_type="application/pdf", data=b"%PDF-", max_bytes=5
        ).filename

    @field_validator("paper_code")
    @classmethod
    def validate_paper_code(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or not value.isprintable()):
            raise ValueError("paper code must be trimmed and printable")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.unit_id is not None and self.curriculum_version_id is None:
            raise ValueError("unit_id requires curriculum_version_id")
        if self.lesson_id is not None and self.unit_id is None:
            raise ValueError("lesson_id requires unit_id")
        return self


class SourceUploadCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_version: int | None = Field(default=None, strict=True, ge=0, le=MAX_UPLOAD_INTEGER)


class SourceUploadChunkReceipt(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    offset: int = Field(ge=0, le=MAX_UPLOAD_INTEGER)
    size_bytes: int = Field(ge=1, le=UPLOAD_CHUNK_BYTES)
    checksum_sha256: UploadChecksum


class SourceUploadChunkPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    upload_id: UUID
    next_offset: int = Field(ge=0, le=MAX_UPLOAD_INTEGER)
    receipts: tuple[SourceUploadChunkReceipt, ...] = Field(max_length=UPLOAD_RECEIPT_PAGE_SIZE)
    next_receipt_offset: int | None = Field(default=None, ge=0, le=MAX_UPLOAD_INTEGER)


class SourceUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    request_id: UUID | None = None
    filename: str
    size_bytes: int
    document_type: SourceDocumentType
    intake_metadata: SourceIntakeMetadata
    status: UploadStatus
    next_offset: int
    chunk_size_bytes: int = UPLOAD_CHUNK_BYTES
    verified_bytes: int
    version: int
    expected_checksum_sha256: str | None = None
    checksum_sha256: str | None = None
    document_id: UUID | None = None
    source_read_job_id: UUID | None = None
    deduplicated: bool = False
    likely_metadata_duplicate_of_id: UUID | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
