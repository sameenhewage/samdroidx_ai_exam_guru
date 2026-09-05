import asyncio

import pytest

from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.extraction_service import OCRPipelineError
from exam_guru_api.documents.ocr import OCRPage, OCRResult, OCRTimeoutError, OCRUnavailableError
from tests.test_ocr_extraction_pipeline import (
    ACTOR_ID,
    DOCUMENT_ID,
    RecordingOCR,
    native_result,
    page,
    persisted,
    service,
)


@pytest.mark.parametrize(
    ("error", "failure_code"),
    [
        (OCRUnavailableError("private provider diagnostics"), "ocr_unavailable"),
        (OCRTimeoutError("private provider diagnostics"), "ocr_timeout"),
    ],
)
def test_transient_ocr_failure_must_not_make_a_source_permanently_nonretryable(
    error: Exception,
    failure_code: str,
) -> None:
    ocr = RecordingOCR(error)
    extraction_service, session, _ = service(
        native_result(page(1, "synthetic native evidence"), page(2, "")),
        ocr=ocr,
    )

    with pytest.raises(OCRPipelineError) as raised:
        asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert str(raised.value) == raised.value.failure_code == failure_code
    assert session.document.extraction_status is ExtractionStatus.FAILED
    assert session.document.extraction_failure_code == failure_code
    assert session.document.extraction_config is None
    assert session.document.extracted_page_count is None
    assert persisted(session) == ([], [])
    assert len(ocr.requests) == 1
    assert "private provider diagnostics" not in repr(session.added)

    queued = asyncio.run(extraction_service.queue_extraction(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert queued.status is ExtractionStatus.EXTRACTION_PENDING
    assert queued.deduplicated is False
    assert session.document.extraction_attempt_count == 2
    assert session.document.extraction_failure_code is None
    assert len(ocr.requests) == 1

    ocr.result = OCRResult(
        engine="fixture-ocr",
        engine_version="1",
        config={},
        pages=(OCRPage(page_number=2, text="recovered OCR evidence"),),
    )
    completed = asyncio.run(
        extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID, preclaimed=True)
    )

    assert completed.status is ExtractionStatus.EXTRACTED
    assert completed.page_count == 2
    assert session.document.needs_ocr is False
    assert session.document.extraction_attempt_count == 2
    assert len(ocr.requests) == 2
    assert [item.raw_text for item in persisted(session)[0]] == [
        "synthetic native evidence",
        "recovered OCR evidence",
    ]
