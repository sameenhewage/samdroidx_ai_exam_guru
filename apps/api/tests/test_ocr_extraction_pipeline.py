import asyncio
import hashlib
from collections.abc import Callable, Mapping
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.extraction import (
    ExtractedBlock,
    ExtractedPage,
    NativeExtractionResult,
    PyMuPdfExtractor,
    ocr_page_numbers,
    page_requires_ocr,
)
from exam_guru_api.documents.extraction_service import (
    DocumentExtractionService,
    OCRPipelineError,
)
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.documents.ocr import (
    MalformedOCROutputError,
    OCRBlock,
    OCRConfigValue,
    OCRContractError,
    OCRPage,
    OCRRequest,
    OCRResult,
)
from exam_guru_api.documents.tesseract_ocr import (
    TesseractConfigError,
    TesseractInputError,
    TesseractInputViolation,
    TesseractMalformedOutputError,
    TesseractOutputLimitError,
    TesseractProcessError,
    TesseractTimeoutError,
    TesseractUnavailableError,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage, StoredObject

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000271")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000272")
PDF_BYTES = b"%PDF-1.7 exact fixture bytes"
CHECKSUM = hashlib.sha256(PDF_BYTES).hexdigest()
NATIVE_CONFIG: dict[str, OCRConfigValue] = {"max_pages": 10, "sort_blocks": True}
OCR_CONFIG: dict[str, OCRConfigValue] = {
    "dpi": 300,
    "language": "sin+eng",
    "output_format": "tsv",
}


def page(
    page_number: int,
    text: str,
    *,
    image_coverage: float = 0.0,
) -> ExtractedPage:
    blocks = (
        (
            ExtractedBlock(
                page_number=page_number,
                reading_order=0,
                bbox=(1.0, 2.0, 30.0, 40.0),
                text=text,
            ),
        )
        if text
        else ()
    )
    return ExtractedPage(
        page_number=page_number,
        text=text,
        blocks=blocks,
        largest_image_coverage=image_coverage,
    )


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (page(1, ""), True),
        (page(1, "x" * 199, image_coverage=0.8), True),
        (page(1, "x" * 200, image_coverage=0.8), False),
        (page(1, "x", image_coverage=0.799), False),
        (page(1, "usable native text"), False),
    ],
)
def test_page_ocr_routing_rule_has_exact_shared_boundaries(
    candidate: ExtractedPage,
    expected: bool,
) -> None:
    assert page_requires_ocr(candidate) is expected


def test_ocr_page_selection_is_complete_ascending_and_uses_the_shared_rule() -> None:
    pages = (
        page(1, "native first"),
        page(2, ""),
        page(3, "overlay", image_coverage=0.95),
        page(4, "x" * 200, image_coverage=1.0),
    )

    assert ocr_page_numbers(pages) == (2, 3)


@pytest.mark.parametrize("confidence", [cast(float, "invalid"), True, float("inf"), 1.01])
def test_first_party_extraction_values_reject_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        ExtractedBlock(
            page_number=1,
            reading_order=0,
            bbox=None,
            text="text",
            confidence=confidence,
        )
    with pytest.raises(ValueError, match="confidence"):
        ExtractedPage(page_number=1, text="text", blocks=(), confidence=confidence)


@pytest.mark.parametrize(
    "build",
    [
        lambda: NativeExtractionResult(
            engine=cast(str, 1),
            engine_version="1",
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
        ),
        lambda: NativeExtractionResult(
            engine=" engine",
            engine_version="1",
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
        ),
        lambda: NativeExtractionResult(
            engine="",
            engine_version="1",
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
        ),
        lambda: NativeExtractionResult(
            engine="x" * 65,
            engine_version="1",
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
        ),
        lambda: NativeExtractionResult(
            engine="unsafe\x00engine",
            engine_version="1",
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
        ),
        lambda: NativeExtractionResult(
            engine="fixture",
            engine_version="v" * 129,
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
        ),
        lambda: NativeExtractionResult(
            engine="fixture",
            engine_version="1",
            pages=(page(1, "text"),),
            page_count=1,
            character_count=4,
            native_text_page_ratio=1.0,
            needs_ocr=False,
            ocr_page_count=1,
        ),
    ],
)
def test_global_extraction_provenance_is_bounded_and_consistent(
    build: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match=r"extraction engine|OCR page"):
        build()


@pytest.mark.parametrize("max_pages", [cast(int, "10"), True, 0, 1_001])
def test_native_extractor_page_limit_configuration_is_bounded(max_pages: int) -> None:
    with pytest.raises(ValueError, match="max_pages"):
        PyMuPdfExtractor(max_pages=max_pages)


class StubSession:
    def __init__(self, document: SourceDocumentModel) -> None:
        self.document = document
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.executions = 0

    async def get(self, *_args: object, **_kwargs: object) -> SourceDocumentModel:
        return self.document

    def add(self, model: object) -> None:
        self.added.append(model)

    def add_all(self, models: list[object]) -> None:
        self.added.extend(models)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, _statement: object) -> None:
        self.executions += 1


class StaticStorage:
    def get_bytes(self, key: str) -> bytes:
        assert key == "sources/exact.pdf"
        return PDF_BYTES

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        raise AssertionError((key, data, content_type))


class StaticNativeExtractor:
    def __init__(self, result: NativeExtractionResult) -> None:
        self.result = result
        self.calls: list[bytes] = []

    def extract(self, data: bytes) -> NativeExtractionResult:
        self.calls.append(data)
        return self.result


class RecordingOCR:
    def __init__(self, result: OCRResult | Exception | object) -> None:
        self.result = result
        self.requests: list[OCRRequest] = []

    def extract(self, request: OCRRequest) -> OCRResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return cast(OCRResult, self.result)


def uploaded_document() -> SourceDocumentModel:
    return SourceDocumentModel(
        id=DOCUMENT_ID,
        checksum_sha256=CHECKSUM,
        object_key="sources/exact.pdf",
        original_filename="exact.pdf",
        content_type="application/pdf",
        size_bytes=len(PDF_BYTES),
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.UPLOADED,
        curriculum_version_id=None,
        year=None,
        paper_code=None,
        extraction_attempt_count=0,
        extractor=None,
        extractor_version=None,
        extracted_page_count=None,
        extracted_block_count=None,
        extracted_character_count=None,
        native_text_page_ratio=None,
        needs_ocr=None,
        ocr_page_count=None,
        extraction_config=None,
        extraction_failure_code=None,
        extraction_started_at=None,
        extraction_completed_at=None,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )


def native_result(*pages: ExtractedPage) -> NativeExtractionResult:
    return NativeExtractionResult(
        engine="pymupdf",
        engine_version="1.28.2",
        pages=pages,
        page_count=len(pages),
        character_count=sum(len(item.text) for item in pages),
        native_text_page_ratio=sum(bool(item.text) for item in pages) / len(pages),
        needs_ocr=bool(ocr_page_numbers(pages)),
        config=NATIVE_CONFIG,
    )


def service(
    native: NativeExtractionResult,
    *,
    ocr: RecordingOCR | None,
) -> tuple[DocumentExtractionService, StubSession, StaticNativeExtractor]:
    session = StubSession(uploaded_document())
    extractor = StaticNativeExtractor(native)
    return (
        DocumentExtractionService(
            cast(AsyncSession, session),
            cast(ObjectStorage, StaticStorage()),
            extractor,
            ocr_port=ocr,
        ),
        session,
        extractor,
    )


def persisted(session: StubSession) -> tuple[list[SourcePageModel], list[ExtractedBlockModel]]:
    return (
        [item for item in session.added if isinstance(item, SourcePageModel)],
        [item for item in session.added if isinstance(item, ExtractedBlockModel)],
    )


def test_mixed_document_calls_ocr_once_with_exact_identity_and_merges_provenance() -> None:
    native = native_result(page(1, "native one"), page(2, ""), page(3, "native three"))
    ocr = RecordingOCR(
        OCRResult(
            engine="fixture-ocr",
            engine_version="2.1",
            config=OCR_CONFIG,
            pages=(
                OCRPage(
                    page_number=2,
                    text="OCR two",
                    blocks=(
                        OCRBlock(
                            page_number=2,
                            reading_order=0,
                            text="OCR two",
                            bbox=(5.0, 6.0, 50.0, 60.0),
                            confidence=0.87,
                        ),
                    ),
                    confidence=0.87,
                ),
            ),
        )
    )
    extraction_service, session, extractor = service(native, ocr=ocr)

    result = asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert result.status is ExtractionStatus.EXTRACTED
    assert extractor.calls == [PDF_BYTES]
    assert len(ocr.requests) == 1
    request = ocr.requests[0]
    assert request.source_document_id == str(DOCUMENT_ID)
    assert request.source_checksum_sha256 == CHECKSUM
    assert request.content == PDF_BYTES
    assert request.media_type == "application/pdf"
    assert request.page_numbers == (2,)

    pages, blocks = persisted(session)
    assert [item.page_number for item in pages] == [1, 2, 3]
    assert [item.raw_text for item in pages] == ["native one", "OCR two", "native three"]
    assert [item.extractor for item in pages] == ["pymupdf", "fixture-ocr", "pymupdf"]
    assert pages[0].extraction_config == NATIVE_CONFIG
    assert pages[1].extraction_config == OCR_CONFIG
    assert pages[1].confidence == pytest.approx(0.87)
    assert [(item.page_number, item.extractor) for item in blocks] == [
        (1, "pymupdf"),
        (2, "fixture-ocr"),
        (3, "pymupdf"),
    ]
    assert blocks[1].extraction_config == OCR_CONFIG
    assert blocks[1].confidence == pytest.approx(0.87)
    assert session.document.extractor == "hybrid"
    assert session.document.extractor_version == "1"
    assert session.document.ocr_page_count == 1
    assert session.document.needs_ocr is False
    assert session.document.extraction_config == {
        "mode": "hybrid",
        "native": {
            "config": NATIVE_CONFIG,
            "engine": "pymupdf",
            "version": "1.28.2",
        },
        "ocr": {
            "config": OCR_CONFIG,
            "engine": "fixture-ocr",
            "version": "2.1",
        },
        "ocr_page_numbers": [2],
    }


def test_full_scan_uses_ocr_global_identity_and_keeps_empty_target_flagged() -> None:
    native = native_result(page(1, ""), page(2, ""))
    ocr = RecordingOCR(
        OCRResult(
            engine="fixture-ocr",
            engine_version="2.1",
            config=OCR_CONFIG,
            pages=(
                OCRPage(page_number=1, text="OCR one"),
                OCRPage(page_number=2, text=""),
            ),
        )
    )
    extraction_service, session, _ = service(native, ocr=ocr)

    asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

    pages, blocks = persisted(session)
    assert [item.page_number for item in pages] == [1, 2]
    assert [item.raw_text for item in pages] == ["OCR one", ""]
    assert blocks == []
    assert session.document.extractor == "fixture-ocr"
    assert session.document.extractor_version == "2.1"
    assert session.document.ocr_page_count == 2
    assert session.document.needs_ocr is True


def test_native_only_and_unconfigured_scan_preserve_native_result_without_ocr_failure() -> None:
    for native in (
        native_result(page(1, "complete native")),
        native_result(page(1, "")),
    ):
        extraction_service, session, _ = service(native, ocr=None)

        asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

        pages, _ = persisted(session)
        assert pages[0].raw_text == native.pages[0].text
        assert session.document.extractor == "pymupdf"
        assert session.document.ocr_page_count == 0
        assert session.document.needs_ocr is native.needs_ocr
        assert session.document.extraction_config == {
            "mode": "native",
            "native": {
                "config": NATIVE_CONFIG,
                "engine": "pymupdf",
                "version": "1.28.2",
            },
        }


@pytest.mark.parametrize(
    ("ocr_result", "failure_code"),
    [
        (
            OCRResult(
                engine="fixture-ocr",
                engine_version="2.1",
                config=OCR_CONFIG,
                pages=(OCRPage(page_number=2, text="wrong page"),),
            ),
            "ocr_result_mismatch",
        ),
        (object(), "ocr_malformed_output"),
        (RuntimeError("provider command leaked output: SECRET"), "ocr_process"),
    ],
)
def test_configured_ocr_failure_is_sanitized_and_leaves_no_persisted_result(
    ocr_result: OCRResult | Exception | object,
    failure_code: str,
) -> None:
    native = native_result(page(1, ""))
    ocr = RecordingOCR(ocr_result)
    extraction_service, session, _ = service(native, ocr=ocr)

    with pytest.raises(OCRPipelineError) as raised:
        asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert raised.value.failure_code == failure_code
    assert session.document.extraction_status is ExtractionStatus.FAILED
    assert session.document.extraction_failure_code == failure_code
    assert session.document.extractor is None
    assert session.document.extraction_config is None
    assert session.document.ocr_page_count is None
    assert persisted(session) == ([], [])
    failure_events = [
        item
        for item in session.added
        if getattr(item, "action", None) == "source_document.extraction_failed"
    ]
    assert len(failure_events) == 1
    failure_event = cast(AdminAuditEventModel, failure_events[0])
    assert failure_event.payload == {"attempt": 1, "failure_code": failure_code}
    assert "SECRET" not in repr(failure_event.payload)


def test_forged_secret_bearing_ocr_config_fails_before_persistence_without_leakage() -> None:
    sensitive_key = "provider.api_key"
    sensitive_value = "must-never-reach-database-or-audit"
    forged = OCRResult(
        engine="fixture-ocr",
        engine_version="2.1",
        config={"language": "fixture"},
        pages=(OCRPage(page_number=1, text="OCR text"),),
    )
    object.__setattr__(forged, "config", {sensitive_key: sensitive_value})
    extraction_service, session, _ = service(
        native_result(page(1, "")),
        ocr=RecordingOCR(forged),
    )

    with pytest.raises(OCRPipelineError) as raised:
        asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert raised.value.failure_code == "ocr_contract"
    assert session.document.extraction_status is ExtractionStatus.FAILED
    assert session.document.extraction_config is None
    assert persisted(session) == ([], [])
    assert sensitive_key not in repr(session.added)
    assert sensitive_value not in repr(session.added)


def test_extracted_audit_records_bounded_mode_metadata_without_text_or_config() -> None:
    native = native_result(page(1, ""))
    ocr = RecordingOCR(
        OCRResult(
            engine="fixture-ocr",
            engine_version="2.1",
            config=OCR_CONFIG,
            pages=(OCRPage(page_number=1, text="sensitive OCR body"),),
        )
    )
    extraction_service, session, _ = service(native, ocr=ocr)

    asyncio.run(extraction_service.extract_native(DOCUMENT_ID, actor_id=ACTOR_ID))

    events = [
        item
        for item in session.added
        if getattr(item, "action", None) == "source_document.extracted"
    ]
    assert len(events) == 1
    event = cast(AdminAuditEventModel, events[0])
    payload: Mapping[str, object] = event.payload
    assert payload["mode"] == "ocr"
    assert payload["ocr_page_count"] == 1
    assert payload["ocr_engine"] == "fixture-ocr"
    assert payload["ocr_engine_version"] == "2.1"
    assert "sensitive OCR body" not in repr(payload)
    assert str(OCR_CONFIG["language"]) not in repr(payload)


def test_runtime_ocr_result_revalidation_rejects_forged_contract_and_shape() -> None:
    invalid_contract = OCRResult(
        engine="fixture-ocr",
        engine_version="1",
        config={},
        pages=(OCRPage(page_number=1, text="text", confidence=0.9),),
    )
    object.__setattr__(invalid_contract.pages[0], "confidence", 2.0)
    with pytest.raises(OCRPipelineError) as contract_error:
        DocumentExtractionService._validated_ocr_result(invalid_contract)
    assert contract_error.value.failure_code == "ocr_contract"

    malformed = OCRResult(
        engine="fixture-ocr",
        engine_version="1",
        config={},
        pages=(OCRPage(page_number=1, text="text"),),
    )
    object.__setattr__(malformed, "pages", (object(),))
    with pytest.raises(OCRPipelineError) as malformed_error:
        DocumentExtractionService._validated_ocr_result(malformed)
    assert malformed_error.value.failure_code == "ocr_malformed_output"


@pytest.mark.parametrize(
    "config",
    [
        {"not_json": object()},
        {"oversized": "x" * (64 * 1024)},
        cast(Mapping[str, object], ["not", "an", "object"]),
    ],
)
def test_document_extraction_config_rejects_non_json_oversized_or_non_object_values(
    config: Mapping[str, object],
) -> None:
    with pytest.raises(OCRPipelineError) as raised:
        DocumentExtractionService._bounded_extraction_config(config)
    assert raised.value.failure_code == "ocr_contract"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OCRPipelineError("ocr_result_mismatch"), "ocr_result_mismatch"),
        (TesseractConfigError("bad config"), "ocr_config"),
        (
            TesseractInputError(TesseractInputViolation.MALFORMED_PDF),
            "ocr_input",
        ),
        (TesseractUnavailableError(), "ocr_unavailable"),
        (
            TesseractTimeoutError(operation="ocr", timeout_seconds=1),
            "ocr_timeout",
        ),
        (
            TesseractOutputLimitError(operation="ocr", max_output_bytes=1),
            "ocr_output_limit",
        ),
        (TesseractMalformedOutputError("malformed"), "ocr_malformed_output"),
        (MalformedOCROutputError("untyped"), "ocr_malformed_output"),
        (OCRContractError("contract"), "ocr_contract"),
        (TesseractProcessError(operation="ocr", returncode=1), "ocr_process"),
        (RuntimeError("unknown provider failure"), "ocr_process"),
    ],
)
def test_every_ocr_failure_class_has_a_stable_sanitized_code(
    error: Exception,
    expected: str,
) -> None:
    assert DocumentExtractionService._ocr_failure_code(error) == expected
