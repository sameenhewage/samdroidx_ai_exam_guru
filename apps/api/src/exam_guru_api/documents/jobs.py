from __future__ import annotations

import asyncio
from typing import Protocol, cast
from uuid import UUID

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings
from exam_guru_api.documents.extraction import PyMuPdfExtractor
from exam_guru_api.documents.extraction_service import DocumentExtractionService
from exam_guru_api.infrastructure.object_storage import create_object_storage
from exam_guru_api.infrastructure.resources import create_resources

EXTRACTION_QUEUE_NAME = "document-extraction"
EXTRACTION_MAX_RETRIES = 3
EXTRACTION_MIN_BACKOFF_MS = 5_000
EXTRACTION_MAX_BACKOFF_MS = 60_000
EXTRACTION_TIME_LIMIT_MS = 5 * 60 * 1_000
NATIVE_EXTRACTION_MAX_PAGES = 1_000


class ExtractionDispatcher(Protocol):
    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str: ...


class _QueuedMessage(Protocol):
    message_id: str


class _ExtractionActor(Protocol):
    def send(self, document_id: str, actor_id: str) -> _QueuedMessage: ...


async def _extract_document(document_id: UUID, *, actor_id: UUID) -> None:
    settings = Settings()
    resources = create_resources(settings)
    try:
        object_storage = create_object_storage(settings)
        extractor = PyMuPdfExtractor(max_pages=NATIVE_EXTRACTION_MAX_PAGES)
        async with resources.session_factory() as session:
            await DocumentExtractionService(
                session,
                object_storage,
                extractor,
            ).extract_native(document_id, actor_id=actor_id, preclaimed=True)
    finally:
        await resources.close()


@dramatiq.actor(
    queue_name=EXTRACTION_QUEUE_NAME,
    max_retries=EXTRACTION_MAX_RETRIES,
    min_backoff=EXTRACTION_MIN_BACKOFF_MS,
    max_backoff=EXTRACTION_MAX_BACKOFF_MS,
    time_limit=EXTRACTION_TIME_LIMIT_MS,
)
def extract_document(document_id: str, actor_id: str) -> None:
    asyncio.run(_extract_document(UUID(document_id), actor_id=UUID(actor_id)))


_DEFAULT_EXTRACTION_ACTOR = cast(_ExtractionActor, extract_document)


class DramatiqExtractionDispatcher:
    def __init__(
        self,
        actor: _ExtractionActor = _DEFAULT_EXTRACTION_ACTOR,
    ) -> None:
        self._actor = actor

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
        message = self._actor.send(str(document_id), str(actor_id))
        return message.message_id


def create_extraction_dispatcher(settings: Settings) -> DramatiqExtractionDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    extract_document.broker = broker
    broker.declare_actor(extract_document)
    return DramatiqExtractionDispatcher()


class DeterministicExtractionDispatcher:
    def __init__(self, message_id: str = "deterministic-extraction-message-id") -> None:
        self._message_id = message_id
        self.dispatched: list[tuple[UUID, UUID]] = []

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
        self.dispatched.append((document_id, actor_id))
        return self._message_id
