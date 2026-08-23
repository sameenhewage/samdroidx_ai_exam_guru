import hashlib

import pytest

from exam_guru_api.documents.domain import (
    SourceDocumentType,
    UploadValidationError,
    UploadViolation,
    validate_pdf_upload,
)

VALID_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"


def test_valid_pdf_upload_has_deterministic_identity_and_storage_key() -> None:
    upload = validate_pdf_upload(
        filename="grade-5-syllabus.pdf",
        content_type="application/pdf",
        data=VALID_PDF,
        max_bytes=1_024,
    )

    checksum = hashlib.sha256(VALID_PDF).hexdigest()
    assert upload.filename == "grade-5-syllabus.pdf"
    assert upload.checksum_sha256 == checksum
    assert upload.object_key == f"sources/{checksum[:2]}/{checksum}.pdf"
    assert upload.size_bytes == len(VALID_PDF)


@pytest.mark.parametrize(
    ("filename", "content_type", "data", "max_bytes", "violation"),
    [
        ("empty.pdf", "application/pdf", b"", 1_024, UploadViolation.EMPTY_FILE),
        (
            "oversized.pdf",
            "application/pdf",
            VALID_PDF,
            len(VALID_PDF) - 1,
            UploadViolation.FILE_TOO_LARGE,
        ),
        (
            "spoofed.pdf",
            "application/pdf",
            b"not a pdf",
            1_024,
            UploadViolation.INVALID_PDF_SIGNATURE,
        ),
        (
            "prefixed.pdf",
            "application/pdf",
            b" \n%PDF-1.7\nspoofed prefix",
            1_024,
            UploadViolation.INVALID_PDF_SIGNATURE,
        ),
        (
            "paper.pdf",
            "text/plain",
            VALID_PDF,
            1_024,
            UploadViolation.UNSUPPORTED_MEDIA_TYPE,
        ),
        (
            "../paper.pdf",
            "application/pdf",
            VALID_PDF,
            1_024,
            UploadViolation.UNSAFE_FILENAME,
        ),
        (
            "folder\\paper.pdf",
            "application/pdf",
            VALID_PDF,
            1_024,
            UploadViolation.UNSAFE_FILENAME,
        ),
        (
            "paper\x00.pdf",
            "application/pdf",
            VALID_PDF,
            1_024,
            UploadViolation.UNSAFE_FILENAME,
        ),
        (
            "paper.txt",
            "application/pdf",
            VALID_PDF,
            1_024,
            UploadViolation.UNSAFE_FILENAME,
        ),
    ],
)
def test_invalid_pdf_upload_is_rejected(
    filename: str,
    content_type: str,
    data: bytes,
    max_bytes: int,
    violation: UploadViolation,
) -> None:
    with pytest.raises(UploadValidationError) as raised:
        validate_pdf_upload(
            filename=filename,
            content_type=content_type,
            data=data,
            max_bytes=max_bytes,
        )

    assert raised.value.violation is violation


def test_source_document_types_cover_priority_one_sources() -> None:
    assert {document_type.value for document_type in SourceDocumentType} == {
        "syllabus",
        "teacher_guide",
        "past_paper",
        "marking_scheme",
        "evaluation_report",
        "other_approved",
    }
