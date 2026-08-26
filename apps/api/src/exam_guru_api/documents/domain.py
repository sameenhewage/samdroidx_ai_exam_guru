import hashlib
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class SourceDocumentType(StrEnum):
    SYLLABUS = "syllabus"
    TEACHER_GUIDE = "teacher_guide"
    PAST_PAPER = "past_paper"
    MARKING_SCHEME = "marking_scheme"
    EVALUATION_REPORT = "evaluation_report"
    OTHER_APPROVED = "other_approved"


class ExtractionStatus(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTION_PENDING = "extraction_pending"
    EXTRACTED = "extracted"
    IN_REVIEW = "in_review"
    TRUSTED = "trusted"
    FAILED = "failed"


class MaterialUseState(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class UploadViolation(StrEnum):
    EMPTY_FILE = "empty_file"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_PDF_SIGNATURE = "invalid_pdf_signature"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    UNSAFE_FILENAME = "unsafe_filename"


class UploadValidationError(ValueError):
    def __init__(self, violation: UploadViolation) -> None:
        self.violation = violation
        super().__init__(violation.value)


@dataclass(frozen=True, slots=True)
class ValidatedPdfUpload:
    filename: str
    checksum_sha256: str
    object_key: str
    size_bytes: int
    data: bytes


def validate_pdf_upload(
    *,
    filename: str,
    content_type: str,
    data: bytes,
    max_bytes: int,
) -> ValidatedPdfUpload:
    if not data:
        raise UploadValidationError(UploadViolation.EMPTY_FILE)
    if len(data) > max_bytes:
        raise UploadValidationError(UploadViolation.FILE_TOO_LARGE)
    if content_type.casefold().strip() != "application/pdf":
        raise UploadValidationError(UploadViolation.UNSUPPORTED_MEDIA_TYPE)

    normalized_filename = unicodedata.normalize("NFC", filename)
    unsafe_filename = (
        not normalized_filename
        or normalized_filename != filename
        or "/" in normalized_filename
        or "\\" in normalized_filename
        or not normalized_filename.casefold().endswith(".pdf")
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized_filename)
    )
    if unsafe_filename:
        raise UploadValidationError(UploadViolation.UNSAFE_FILENAME)
    if not data.startswith(b"%PDF-"):
        raise UploadValidationError(UploadViolation.INVALID_PDF_SIGNATURE)

    checksum = hashlib.sha256(data).hexdigest()
    return ValidatedPdfUpload(
        filename=normalized_filename,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        size_bytes=len(data),
        data=data,
    )
