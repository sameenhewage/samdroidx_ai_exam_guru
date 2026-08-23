from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType


class SourceDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    checksum_sha256: str
    object_key: str
    original_filename: str
    content_type: str
    size_bytes: int
    document_type: SourceDocumentType
    extraction_status: ExtractionStatus
    curriculum_version_id: UUID | None
    year: int | None
    paper_code: str | None
    created_at: datetime
    deduplicated: bool = False
