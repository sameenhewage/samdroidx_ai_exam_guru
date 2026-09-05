from __future__ import annotations

import asyncio
import time
from typing import Protocol, cast
from uuid import UUID

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import (
    EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS,
    EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS,
    Settings,
)
from exam_guru_api.documents.extraction import PyMuPdfExtractor
from exam_guru_api.documents.extraction_service import DocumentExtractionService
from exam_guru_api.documents.ocr import OCRPort
from exam_guru_api.documents.tesseract_ocr import TesseractCliOCRAdapter, TesseractOCRConfig
from exam_guru_api.infrastructure.object_storage import create_object_storage
from exam_guru_api.infrastructure.resources import create_resources

EXTRACTION_QUEUE_NAME = "document-extraction"
EXTRACTION_MAX_RETRIES = 3
EXTRACTION_MIN_BACKOFF_MS = 5_000
EXTRACTION_MAX_BACKOFF_MS = 60_000
EXTRACTION_TIME_LIMIT_MS = EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS * 1_000
EXTRACTION_RECOVERY_MAX_RETRIES = 0
EXTRACTION_RECOVERY_TIME_LIMIT_MS = EXTRACTION_TIME_LIMIT_MS
NATIVE_EXTRACTION_MAX_PAGES = 1_000


class ExtractionDispatcher(Protocol):
    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str: ...


class _QueuedMessage(Protocol):
    message_id: str


class _ExtractionActor(Protocol):
    def send(self, document_id: str, actor_id: str) -> _QueuedMessage: ...


def create_ocr_port(
    settings: Settings,
    *,
    execution_deadline: float | None = None,
) -> OCRPort | None:
    if settings.ocr_provider is None:
        return None
    selected_languages = tuple(settings.ocr_tesseract_language.split("+"))
    return TesseractCliOCRAdapter(
        execution_deadline=execution_deadline,
        config=TesseractOCRConfig(
            executable=settings.ocr_tesseract_executable,
            language=settings.ocr_tesseract_language,
            allowed_languages=selected_languages,
            max_source_bytes=settings.ocr_tesseract_max_source_bytes,
            max_pages=settings.ocr_tesseract_max_pages,
            dpi=settings.ocr_tesseract_dpi,
            batch_size=settings.ocr_tesseract_batch_size,
            timeout_seconds=settings.ocr_tesseract_timeout_seconds,
            page_segmentation_mode=settings.ocr_tesseract_page_segmentation_mode,
            max_pixels_per_page=settings.ocr_tesseract_max_pixels_per_page,
            max_command_output_bytes=settings.ocr_tesseract_max_command_output_bytes,
        ),
    )


async def _extract_document(document_id: UUID, *, actor_id: UUID) -> None:
    execution_deadline = time.monotonic() + EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS
    settings = Settings()
    resources = create_resources(settings)
    object_storage = None
    try:
        object_storage = create_object_storage(settings)
        extractor = PyMuPdfExtractor(max_pages=NATIVE_EXTRACTION_MAX_PAGES)
        ocr_port = create_ocr_port(
            settings,
            execution_deadline=execution_deadline - EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS,
        )
        async with resources.session_factory() as session:
            await DocumentExtractionService(
                session,
                object_storage,
                extractor,
                ocr_port=ocr_port,
                ocr_max_pages=settings.ocr_tesseract_max_pages,
                ocr_timeout_seconds=settings.ocr_tesseract_timeout_seconds,
                execution_deadline=execution_deadline,
            ).extract_native(document_id, actor_id=actor_id, preclaimed=True)
    finally:
        try:
            if object_storage is not None:
                object_storage.close()
        finally:
            await resources.close()


async def _recover_extraction_jobs() -> None:
    from exam_guru_api.documents.extraction_outbox import (
        ExtractionRecoveryPolicy,
        ExtractionRecoveryService,
    )

    settings = Settings()
    resources = create_resources(settings)
    try:
        policy = ExtractionRecoveryPolicy(
            batch_size=settings.extraction_recovery_batch_size,
            outbox_min_age_seconds=settings.extraction_outbox_min_age_seconds,
        )
        async with resources.session_factory() as session:
            await ExtractionRecoveryService(
                session,
                DramatiqExtractionDispatcher(),
                policy,
            ).recover()
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


@dramatiq.actor(
    queue_name=EXTRACTION_QUEUE_NAME,
    max_retries=EXTRACTION_RECOVERY_MAX_RETRIES,
    time_limit=EXTRACTION_RECOVERY_TIME_LIMIT_MS,
)
def recover_extraction_jobs() -> None:
    asyncio.run(_recover_extraction_jobs())


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
    recover_extraction_jobs.broker = broker
    broker.declare_actor(extract_document)
    broker.declare_actor(recover_extraction_jobs)
    return DramatiqExtractionDispatcher()


class DeterministicExtractionDispatcher:
    def __init__(self, message_id: str = "deterministic-extraction-message-id") -> None:
        self._message_id = message_id
        self.dispatched: list[tuple[UUID, UUID]] = []

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
        self.dispatched.append((document_id, actor_id))
        return self._message_id
