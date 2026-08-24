from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.models import SourceDocumentModel

MAX_EXTRACTION_QUEUE_MESSAGE_ID_LENGTH = 128


class ExtractionDispatcher(Protocol):
    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str: ...


@dataclass(frozen=True, slots=True)
class ExtractionQueueAttachment:
    document: SourceDocumentModel
    attached: bool


@dataclass(frozen=True, slots=True)
class ExtractionRecoveryPolicy:
    batch_size: int = 50
    outbox_min_age_seconds: int = 5

    def __post_init__(self) -> None:
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or not 1 <= self.batch_size <= 100
        ):
            raise ValueError("extraction recovery batch size must be between 1 and 100")
        if (
            not isinstance(self.outbox_min_age_seconds, int)
            or isinstance(self.outbox_min_age_seconds, bool)
            or not 1 <= self.outbox_min_age_seconds <= 3_600
        ):
            raise ValueError("extraction recovery outbox age must be between 1 and 3600 seconds")


@dataclass(frozen=True, slots=True)
class ExtractionRecoveryResult:
    scanned: int
    dispatched: int
    failures: int


def validate_extraction_queue_message_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_EXTRACTION_QUEUE_MESSAGE_ID_LENGTH
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise ValueError("invalid extraction queue message id")
    return value


class SqlAlchemyExtractionOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def attach_queue_message(
        self,
        document_id: UUID,
        message_id: str,
    ) -> ExtractionQueueAttachment:
        document = await self._session.scalar(
            update(SourceDocumentModel)
            .where(
                SourceDocumentModel.id == document_id,
                SourceDocumentModel.extraction_status == ExtractionStatus.EXTRACTION_PENDING,
                SourceDocumentModel.extraction_queue_message_id.is_(None),
            )
            .values(extraction_queue_message_id=message_id)
            .returning(SourceDocumentModel)
        )
        if document is not None:
            return ExtractionQueueAttachment(document=document, attached=True)
        existing = await self._session.get(
            SourceDocumentModel,
            document_id,
            populate_existing=True,
        )
        if existing is None:
            raise LookupError(document_id)
        return ExtractionQueueAttachment(document=existing, attached=False)

    async def lock_recoverable_documents(
        self,
        *,
        started_before: datetime,
        limit: int,
    ) -> tuple[SourceDocumentModel, ...]:
        return tuple(
            await self._session.scalars(
                select(SourceDocumentModel)
                .where(
                    SourceDocumentModel.extraction_status == ExtractionStatus.EXTRACTION_PENDING,
                    SourceDocumentModel.extraction_queue_message_id.is_(None),
                    SourceDocumentModel.extraction_started_at < started_before,
                )
                .order_by(
                    SourceDocumentModel.extraction_started_at,
                    SourceDocumentModel.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )


class ExtractionRecoveryService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: ExtractionDispatcher,
        policy: ExtractionRecoveryPolicy,
    ) -> None:
        if not isinstance(policy, ExtractionRecoveryPolicy):
            raise TypeError("policy must be ExtractionRecoveryPolicy")
        self._session = session
        self._dispatcher = dispatcher
        self._policy = policy
        self._repository = SqlAlchemyExtractionOutboxRepository(session)

    async def recover(self, *, now: datetime | None = None) -> ExtractionRecoveryResult:
        active_now = datetime.now(UTC) if now is None else now
        documents = await self._repository.lock_recoverable_documents(
            started_before=active_now - timedelta(seconds=self._policy.outbox_min_age_seconds),
            limit=self._policy.batch_size,
        )
        dispatched = 0
        failures = 0
        for document in documents:
            try:
                message_id = validate_extraction_queue_message_id(
                    self._dispatcher.dispatch(document.id, actor_id=document.updated_by)
                )
            except Exception:
                failures += 1
                self._audit_recovery(document, succeeded=False)
                continue
            attachment = await self._repository.attach_queue_message(document.id, message_id)
            if attachment.attached:
                dispatched += 1
                self._audit_recovery(attachment.document, succeeded=True)

        await self._session.commit()
        return ExtractionRecoveryResult(
            scanned=len(documents),
            dispatched=dispatched,
            failures=failures,
        )

    def _audit_recovery(self, document: SourceDocumentModel, *, succeeded: bool) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=document.updated_by,
                action=(
                    "source_document.extraction_redispatched"
                    if succeeded
                    else "source_document.extraction_redispatch_failed"
                ),
                resource_type="source_document",
                resource_id=document.id,
                payload={
                    "attempt": document.extraction_attempt_count,
                    "failure_code": None if succeeded else "queue_dispatch_failed",
                    "recovery": True,
                },
            )
        )
