import asyncio
import hashlib
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.extraction import (
    ExtractedBlock,
    ExtractedPage,
    InvalidExtractionTransitionError,
    NativeExtractionResult,
)
from exam_guru_api.documents.extraction_outbox import SqlAlchemyExtractionOutboxRepository
from exam_guru_api.documents.extraction_service import (
    ConcurrentReviewVersionError,
    DocumentExtractionService,
    ExtractedBlockNotFoundError,
    ExtractionDocumentNotFoundError,
    ExtractionPersistenceResult,
    ExtractionSourceIntegrityError,
    ReviewNotActiveError,
    SourcePageNotFoundError,
)
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.infrastructure.object_storage import ObjectStorage, StoredObject
from tests.test_operational_telemetry import telemetry

ACTOR_ID = UUID(int=82_000)
DOCUMENT_ID = UUID(int=82_001)
SOURCE_DATA = b"immutable source bytes"


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class StubSession:
    def __init__(self, document: SourceDocumentModel | None) -> None:
        self.document = document
        self.scalar_result: object | None = None
        self.scalar_rows: list[object] = []
        self.added: list[object] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self.executions = 0

    async def get(self, *_args: object, **_kwargs: object) -> SourceDocumentModel | None:
        return self.document

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_result

    async def scalars(self, _statement: object) -> ScalarRows:
        return ScalarRows(self.scalar_rows)

    def add(self, model: object) -> None:
        self.added.append(model)

    def add_all(self, models: list[object]) -> None:
        self.added.extend(models)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, _model: object) -> None:
        return None

    async def execute(self, _statement: object) -> None:
        self.executions += 1


class StaticStorage:
    def __init__(self, data: bytes | Exception) -> None:
        self.data = data

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        raise AssertionError((key, data, content_type))

    def get_bytes(self, _key: str) -> bytes:
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class NeverExtractor:
    def extract(self, _data: bytes) -> NativeExtractionResult:
        raise AssertionError("integrity failures must not reach the extractor")


def document(status: ExtractionStatus) -> SourceDocumentModel:
    now = datetime.now(UTC)
    has_result = status in {
        ExtractionStatus.EXTRACTED,
        ExtractionStatus.IN_REVIEW,
        ExtractionStatus.TRUSTED,
    }
    return SourceDocumentModel(
        id=DOCUMENT_ID,
        checksum_sha256=hashlib.sha256(SOURCE_DATA).hexdigest(),
        object_key="sources/fixture.pdf",
        original_filename="fixture.pdf",
        content_type="application/pdf",
        size_bytes=len(SOURCE_DATA),
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=status,
        curriculum_version_id=None,
        year=None,
        paper_code=None,
        extraction_attempt_count=1 if status is not ExtractionStatus.UPLOADED else 0,
        extractor="pymupdf" if has_result else None,
        extractor_version="fixture-version" if has_result else None,
        extracted_page_count=1 if has_result else None,
        extracted_block_count=1 if has_result else None,
        extracted_character_count=4 if has_result else None,
        native_text_page_ratio=1.0 if has_result else None,
        needs_ocr=False if has_result else None,
        extraction_failure_code="malformed_pdf" if status is ExtractionStatus.FAILED else None,
        extraction_started_at=now if status is not ExtractionStatus.UPLOADED else None,
        extraction_completed_at=(
            now
            if status
            in {
                ExtractionStatus.EXTRACTED,
                ExtractionStatus.IN_REVIEW,
                ExtractionStatus.TRUSTED,
                ExtractionStatus.FAILED,
            }
            else None
        ),
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )


def extraction_result() -> NativeExtractionResult:
    block = ExtractedBlock(
        page_number=1,
        reading_order=0,
        bbox=(1.0, 2.0, 3.0, 4.0),
        text="text",
    )
    return NativeExtractionResult(
        engine="pymupdf",
        engine_version="fixture-version",
        pages=(ExtractedPage(page_number=1, text="text", blocks=(block,)),),
        page_count=1,
        character_count=4,
        native_text_page_ratio=1.0,
        needs_ocr=False,
    )


def service(
    session: StubSession,
    storage: StaticStorage | None = None,
) -> DocumentExtractionService:
    return DocumentExtractionService(
        cast(AsyncSession, session),
        cast(ObjectStorage, storage or StaticStorage(SOURCE_DATA)),
        NeverExtractor(),
    )


def test_missing_document_has_a_stable_typed_error() -> None:
    missing_id = UUID(int=82_999)
    session = StubSession(None)

    with pytest.raises(ExtractionDocumentNotFoundError) as raised:
        asyncio.run(service(session).extract_native(missing_id, actor_id=ACTOR_ID))

    assert raised.value.document_id == missing_id
    assert str(raised.value) == str(missing_id)


def test_queue_claim_commits_before_dispatch_and_pending_replay_is_idempotent() -> None:
    model = document(ExtractionStatus.UPLOADED)
    session = StubSession(model)
    extraction_service = service(session)

    queued = asyncio.run(extraction_service.queue_extraction(DOCUMENT_ID, actor_id=ACTOR_ID))
    replayed = asyncio.run(extraction_service.queue_extraction(DOCUMENT_ID, actor_id=ACTOR_ID))
    claim = asyncio.run(extraction_service._preclaimed(DOCUMENT_ID))

    assert queued.status is ExtractionStatus.EXTRACTION_PENDING
    assert queued.deduplicated is False
    assert queued.queue_message_id is None
    assert replayed.status is ExtractionStatus.EXTRACTION_PENDING
    assert replayed.deduplicated is True
    assert replayed.queue_message_id is None
    assert model.extraction_attempt_count == 1
    assert session.executions == 2
    assert session.commits == 3
    assert not isinstance(claim, ExtractionPersistenceResult)
    assert claim.object_key == model.object_key

    model.extraction_status = ExtractionStatus.EXTRACTED
    model.extractor = "pymupdf"
    model.extractor_version = "fixture"
    model.extracted_page_count = 1
    model.extracted_block_count = 1
    model.extracted_character_count = 4
    model.native_text_page_ratio = 1.0
    model.needs_ocr = False
    completed = asyncio.run(extraction_service._preclaimed(DOCUMENT_ID))
    assert isinstance(completed, ExtractionPersistenceResult)
    assert completed.deduplicated is True

    model.extraction_status = ExtractionStatus.UPLOADED
    with pytest.raises(InvalidExtractionTransitionError):
        asyncio.run(extraction_service._preclaimed(DOCUMENT_ID))


def test_failed_queue_retry_starts_one_new_attempt_and_clears_old_queue_identity() -> None:
    model = document(ExtractionStatus.FAILED)
    model.extraction_queue_message_id = "old-attempt-message"
    session = StubSession(model)

    queued = asyncio.run(service(session).queue_extraction(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert queued.status is ExtractionStatus.EXTRACTION_PENDING
    assert queued.queue_message_id is None
    assert model.extraction_queue_message_id is None
    assert model.extraction_attempt_count == 2
    assert session.executions == 2
    assert session.commits == 1


def test_queue_attachment_maps_disappeared_document_and_dispatch_failure_is_audited() -> None:
    class MissingOutboxRepository:
        async def attach_queue_message(self, document_id: UUID, message_id: str) -> object:
            del document_id, message_id
            raise LookupError

    missing_session = StubSession(document(ExtractionStatus.EXTRACTION_PENDING))
    missing_service = service(missing_session)
    missing_service._outbox_repository = cast(
        SqlAlchemyExtractionOutboxRepository,
        MissingOutboxRepository(),
    )

    with pytest.raises(ExtractionDocumentNotFoundError):
        asyncio.run(
            missing_service.attach_queue_message(
                DOCUMENT_ID,
                "message-id",
                actor_id=ACTOR_ID,
            )
        )

    pending = document(ExtractionStatus.EXTRACTION_PENDING)
    pending.extraction_queue_message_id = None
    pending_session = StubSession(pending)

    asyncio.run(
        service(pending_session).record_queue_dispatch_failure(
            DOCUMENT_ID,
            actor_id=ACTOR_ID,
        )
    )

    audit = pending_session.added[-1]
    assert isinstance(audit, AdminAuditEventModel)
    assert audit.action == "source_document.extraction_dispatch_failed"
    assert audit.payload == {
        "attempt": 1,
        "failure_code": "queue_dispatch_failed",
    }
    assert pending_session.rollbacks == 1
    assert pending_session.commits == 1

    pending.extraction_queue_message_id = "already-attached"
    healed_session = StubSession(pending)
    asyncio.run(
        service(healed_session).record_queue_dispatch_failure(
            DOCUMENT_ID,
            actor_id=ACTOR_ID,
        )
    )
    healed_audit = healed_session.added[-1]
    assert isinstance(healed_audit, AdminAuditEventModel)
    assert healed_audit.action == "source_document.extraction_dispatch_failed"
    assert healed_audit.payload["failure_code"] == "queue_dispatch_failed"
    assert healed_session.rollbacks == 1
    assert healed_session.commits == 1


def test_direct_worker_claim_respects_queue_attempt_identity_and_pending_idempotency() -> None:
    failed = document(ExtractionStatus.FAILED)
    failed.extraction_queue_message_id = "old-attempt-message"
    failed_session = StubSession(failed)

    claim = asyncio.run(service(failed_session)._claim(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert not isinstance(claim, ExtractionPersistenceResult)
    assert failed.extraction_attempt_count == 2
    assert failed.extraction_queue_message_id is None

    pending = document(ExtractionStatus.EXTRACTION_PENDING)
    pending.extraction_queue_message_id = "current-attempt-message"
    pending_session = StubSession(pending)

    duplicate_claim = asyncio.run(
        service(pending_session)._claim(DOCUMENT_ID, actor_id=UUID(int=82_002))
    )

    assert not isinstance(duplicate_claim, ExtractionPersistenceResult)
    assert pending.extraction_attempt_count == 1
    assert pending.extraction_queue_message_id == "current-attempt-message"
    assert pending_session.executions == 0
    assert pending_session.commits == 1


@pytest.mark.parametrize(
    ("storage_value", "error_type", "failure_code"),
    [
        (b"tampered", ExtractionSourceIntegrityError, "source_object_integrity"),
        (RuntimeError("storage unavailable"), RuntimeError, "unexpected_error"),
    ],
)
def test_source_read_failures_are_safely_persisted(
    storage_value: bytes | Exception,
    error_type: type[Exception],
    failure_code: str,
) -> None:
    model = document(ExtractionStatus.UPLOADED)
    session = StubSession(model)
    extraction_service = service(session, StaticStorage(storage_value))
    operational, telemetry_logger, _tracer = telemetry()
    extraction_service._telemetry = operational

    with pytest.raises(error_type):
        asyncio.run(
            extraction_service.extract_native(
                DOCUMENT_ID,
                actor_id=ACTOR_ID,
            )
        )

    assert model.extraction_status is ExtractionStatus.FAILED
    assert model.extraction_attempt_count == 1
    assert model.extraction_failure_code == failure_code
    assert model.extraction_completed_at is not None
    assert session.rollbacks == 1
    assert session.commits == 2
    assert telemetry_logger.records == [
        (
            "Operational event",
            {
                "event_name": "extraction.terminal",
                "outcome": "failed",
                "failure_code": failure_code,
                "status": "failed",
                "attempt_count": 1,
                "page_count": 0,
                "block_count": 0,
                "ocr_page_count": 0,
            },
        )
    ]
    assert "storage unavailable" not in str(telemetry_logger.records)


def test_begin_review_retry_is_idempotent() -> None:
    model = document(ExtractionStatus.IN_REVIEW)
    session = StubSession(model)

    result = asyncio.run(service(session).begin_review(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert result.status is ExtractionStatus.IN_REVIEW
    assert result.deduplicated is True
    assert result.page_count == 1
    assert result.block_count == 1
    assert session.commits == 0


@pytest.mark.parametrize("status", [ExtractionStatus.IN_REVIEW, ExtractionStatus.TRUSTED])
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("needs_ocr", True, "needs_ocr"),
        ("active_for_ai", False, "inactive_source"),
        ("metadata_review_required", True, "metadata_review_required"),
        ("extraction_config", {"native": {"config": {"font_risk": True}}}, "font_risk"),
        ("extraction_config", {"font_risk": True}, "font_risk"),
        ("extracted_character_count", 0, "empty_extraction"),
    ],
)
def test_trust_fails_closed_for_unresolved_source_risks(
    status: ExtractionStatus,
    field: str,
    value: object,
    reason: str,
) -> None:
    from exam_guru_api.documents import extraction_service

    model = document(status)
    setattr(model, field, value)
    session = StubSession(model)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(service(session).trust_document(DOCUMENT_ID, actor_id=ACTOR_ID))
    assert type(raised.value).__name__ == "ExtractionTrustBlockedError"
    assert isinstance(raised.value, extraction_service.ExtractionTrustBlockedError)
    assert raised.value.reason_code == reason
    assert str(raised.value) == reason
    assert model.extraction_status is status
    assert session.added == []
    assert session.commits == 0


def test_trust_reviewed_document_is_forward_only_and_idempotent() -> None:
    model = document(ExtractionStatus.IN_REVIEW)
    session = StubSession(model)

    trusted = asyncio.run(service(session).trust_document(DOCUMENT_ID, actor_id=ACTOR_ID))
    retried = asyncio.run(service(session).trust_document(DOCUMENT_ID, actor_id=ACTOR_ID))

    assert trusted.status is ExtractionStatus.TRUSTED
    assert trusted.deduplicated is False
    assert retried.status is ExtractionStatus.TRUSTED
    assert retried.deduplicated is True
    assert session.commits == 1


def test_review_listing_and_error_boundaries() -> None:
    reviewed = document(ExtractionStatus.IN_REVIEW)
    session = StubSession(reviewed)
    now = datetime.now(UTC)
    page = SourcePageModel(
        id=UUID(int=83_000),
        source_document_id=DOCUMENT_ID,
        page_number=1,
        extractor="pymupdf",
        extractor_version="fixture",
        raw_text="raw",
        reviewed_text=None,
        character_count=3,
        block_count=1,
        version=1,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )
    block = ExtractedBlockModel(
        id=UUID(int=83_001),
        source_page_id=page.id,
        source_document_id=DOCUMENT_ID,
        page_number=1,
        reading_order=0,
        extractor="pymupdf",
        extractor_version="fixture",
        bbox_x0=0,
        bbox_y0=0,
        bbox_x1=1,
        bbox_y1=1,
        raw_text="raw",
        reviewed_text=None,
        character_count=3,
        version=1,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )

    session.scalar_rows = [page]
    assert asyncio.run(service(session).list_pages(DOCUMENT_ID)) == [page]
    session.scalar_rows = [block]
    assert asyncio.run(service(session).list_blocks(DOCUMENT_ID, page_number=1)) == [block]

    missing = StubSession(None)
    with pytest.raises(ExtractionDocumentNotFoundError):
        asyncio.run(service(missing).list_pages(DOCUMENT_ID))
    with pytest.raises(ExtractionDocumentNotFoundError):
        asyncio.run(service(missing).list_blocks(DOCUMENT_ID, page_number=1))

    inactive = StubSession(document(ExtractionStatus.EXTRACTED))
    with pytest.raises(ReviewNotActiveError):
        asyncio.run(
            service(inactive).correct_page(
                DOCUMENT_ID,
                page_number=1,
                reviewed_text="reviewed",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )
    with pytest.raises(ReviewNotActiveError):
        asyncio.run(
            service(inactive).correct_block(
                DOCUMENT_ID,
                page_number=1,
                reading_order=0,
                reviewed_text="reviewed",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )

    session.scalar_result = None
    with pytest.raises(SourcePageNotFoundError):
        asyncio.run(
            service(session).correct_page(
                DOCUMENT_ID,
                page_number=1,
                reviewed_text="reviewed",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )
    with pytest.raises(ExtractedBlockNotFoundError):
        asyncio.run(
            service(session).correct_block(
                DOCUMENT_ID,
                page_number=1,
                reading_order=0,
                reviewed_text="reviewed",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )

    session.scalar_result = page
    with pytest.raises(ConcurrentReviewVersionError):
        asyncio.run(
            service(session).correct_page(
                DOCUMENT_ID,
                page_number=1,
                reviewed_text="reviewed",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )
    session.scalar_result = block
    with pytest.raises(ConcurrentReviewVersionError):
        asyncio.run(
            service(session).correct_block(
                DOCUMENT_ID,
                page_number=1,
                reading_order=0,
                reviewed_text="reviewed",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )


def test_late_success_wins_over_a_concurrent_failure_and_duplicate_success_is_ignored() -> None:
    failed = document(ExtractionStatus.FAILED)
    failed_session = StubSession(failed)

    recovered = asyncio.run(
        service(failed_session)._persist_result(
            DOCUMENT_ID,
            extraction_result(),
            actor_id=ACTOR_ID,
        )
    )

    assert recovered.status is ExtractionStatus.EXTRACTED
    assert recovered.deduplicated is False
    assert failed.extraction_failure_code is None
    assert failed_session.flushes == 3

    final_session = StubSession(failed)
    duplicate = asyncio.run(
        service(final_session)._persist_result(
            DOCUMENT_ID,
            extraction_result(),
            actor_id=ACTOR_ID,
        )
    )
    asyncio.run(
        service(final_session)._record_failure(
            DOCUMENT_ID,
            RuntimeError("slower worker failed"),
            actor_id=ACTOR_ID,
        )
    )

    assert duplicate.status is ExtractionStatus.EXTRACTED
    assert duplicate.deduplicated is True
    assert failed.extraction_status is ExtractionStatus.EXTRACTED
    assert final_session.commits == 0
