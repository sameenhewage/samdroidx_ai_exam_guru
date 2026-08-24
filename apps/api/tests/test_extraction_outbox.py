import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.extraction_outbox import (
    ExtractionQueueAttachment,
    ExtractionRecoveryPolicy,
    ExtractionRecoveryService,
    SqlAlchemyExtractionOutboxRepository,
    validate_extraction_queue_message_id,
)
from exam_guru_api.documents.models import SourceDocumentModel

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class RecoverySession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class RecoveryRepository:
    def __init__(self, documents: tuple[SourceDocumentModel, ...]) -> None:
        self.documents = documents
        self.lock_calls: list[tuple[datetime, int]] = []
        self.attachments: list[tuple[UUID, str]] = []

    async def lock_recoverable_documents(
        self,
        *,
        started_before: datetime,
        limit: int,
    ) -> tuple[SourceDocumentModel, ...]:
        self.lock_calls.append((started_before, limit))
        return self.documents

    async def attach_queue_message(
        self,
        document_id: UUID,
        message_id: str,
    ) -> ExtractionQueueAttachment:
        self.attachments.append((document_id, message_id))
        document = next(item for item in self.documents if item.id == document_id)
        document.extraction_queue_message_id = message_id
        return ExtractionQueueAttachment(document=document, attached=True)


class PartiallyFailingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
        self.calls.append((document_id, actor_id))
        if len(self.calls) == 2:
            raise RuntimeError("private valkey transport diagnostic raw-payload")
        return f"message-{document_id}"


def pending_document(identifier: int, actor_identifier: int) -> SourceDocumentModel:
    return SourceDocumentModel(
        id=UUID(int=identifier),
        checksum_sha256=f"{identifier:064x}",
        object_key=f"sources/{identifier}.pdf",
        original_filename=f"{identifier}.pdf",
        content_type="application/pdf",
        size_bytes=10,
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        curriculum_version_id=None,
        year=None,
        paper_code=None,
        extraction_attempt_count=1,
        extraction_queue_message_id=None,
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
        extraction_started_at=NOW - timedelta(minutes=5),
        extraction_completed_at=None,
        created_by=UUID(int=actor_identifier),
        updated_by=UUID(int=actor_identifier),
        created_at=NOW - timedelta(minutes=10),
        updated_at=NOW - timedelta(minutes=5),
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "has space",
        "line\nbreak",
        "x" * 129,
        123,
    ],
)
def test_queue_message_identity_validation_is_bounded(value: object) -> None:
    with pytest.raises(ValueError, match="queue message id"):
        validate_extraction_queue_message_id(cast(str, value))

    assert validate_extraction_queue_message_id("message-123_ABC") == "message-123_ABC"


@pytest.mark.parametrize(
    "values",
    [
        (0, 5),
        (101, 5),
        (10, 0),
        (10, 3_601),
        (True, 5),
        (10, True),
    ],
)
def test_extraction_recovery_policy_rejects_unbounded_values(values: tuple[object, object]) -> None:
    with pytest.raises(ValueError, match="extraction recovery"):
        ExtractionRecoveryPolicy(
            batch_size=cast(int, values[0]),
            outbox_min_age_seconds=cast(int, values[1]),
        )


def test_extraction_recovery_uses_persisted_actor_audits_safely_and_commits_once() -> None:
    async def exercise() -> None:
        first = pending_document(880_001, 881_001)
        second = pending_document(880_002, 881_002)
        repository = RecoveryRepository((first, second))
        session = RecoverySession()
        dispatcher = PartiallyFailingDispatcher()
        service = ExtractionRecoveryService(
            cast(AsyncSession, session),
            dispatcher,
            ExtractionRecoveryPolicy(batch_size=12, outbox_min_age_seconds=7),
        )
        service._repository = cast(object, repository)  # type: ignore[assignment]

        result = await service.recover(now=NOW)

        assert result.scanned == 2
        assert result.dispatched == 1
        assert result.failures == 1
        assert repository.lock_calls == [(NOW - timedelta(seconds=7), 12)]
        assert dispatcher.calls == [
            (first.id, first.updated_by),
            (second.id, second.updated_by),
        ]
        assert repository.attachments == [(first.id, f"message-{first.id}")]
        assert session.commits == 1

        audits = [item for item in session.added if isinstance(item, AdminAuditEventModel)]
        assert [item.action for item in audits] == [
            "source_document.extraction_redispatched",
            "source_document.extraction_redispatch_failed",
        ]
        assert audits[0].actor_id == first.updated_by
        assert audits[1].actor_id == second.updated_by
        assert audits[1].payload == {
            "attempt": 1,
            "failure_code": "queue_dispatch_failed",
            "recovery": True,
        }
        assert "private" not in repr(audits[1].payload)
        assert "raw-payload" not in repr(audits[1].payload)

    asyncio.run(exercise())


def test_extraction_recovery_treats_invalid_broker_identity_as_sanitized_failure() -> None:
    class InvalidDispatcher:
        def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
            del document_id, actor_id
            return "invalid message id"

    async def exercise() -> None:
        document = pending_document(880_003, 881_003)
        repository = RecoveryRepository((document,))
        session = RecoverySession()
        service = ExtractionRecoveryService(
            cast(AsyncSession, session),
            InvalidDispatcher(),
            ExtractionRecoveryPolicy(batch_size=1, outbox_min_age_seconds=5),
        )
        service._repository = cast(object, repository)  # type: ignore[assignment]

        result = await service.recover(now=NOW)

        assert result.failures == 1
        assert result.dispatched == 0
        assert repository.attachments == []
        assert session.commits == 1

    asyncio.run(exercise())


def test_extraction_recovery_rejects_wrong_policy_type() -> None:
    with pytest.raises(TypeError, match="ExtractionRecoveryPolicy"):
        ExtractionRecoveryService(
            cast(AsyncSession, RecoverySession()),
            PartiallyFailingDispatcher(),
            cast(ExtractionRecoveryPolicy, object()),
        )


def test_outbox_repository_reports_a_disappeared_document_after_lost_attach_cas() -> None:
    class MissingSession:
        async def scalar(self, _statement: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> None:
            return None

    async def exercise() -> None:
        repository = SqlAlchemyExtractionOutboxRepository(cast(AsyncSession, MissingSession()))
        with pytest.raises(LookupError, match=str(UUID(int=999))):
            await repository.attach_queue_message(UUID(int=999), "message-id")

    asyncio.run(exercise())
