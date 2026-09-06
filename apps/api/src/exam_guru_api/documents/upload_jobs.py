from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings, StorageBackend
from exam_guru_api.documents.fidelity_models import SourceReadJobModel
from exam_guru_api.documents.page_reading_jobs import (
    DramatiqSourceReadDispatcher,
    SourceReadDispatcher,
    dispatch_source_read,
)
from exam_guru_api.documents.resumable_uploads import (
    ResumableUploadError,
    ResumableUploadService,
    UploadLimits,
)
from exam_guru_api.documents.upload_schemas import (
    MAX_UPLOAD_INTEGER,
    SourceUploadResponse,
    UploadStatus,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage, create_object_storage
from exam_guru_api.infrastructure.private_artifacts import PrivateUploadArtifacts
from exam_guru_api.infrastructure.resources import create_resources

SOURCE_UPLOAD_QUEUE_NAME = "source-upload-finalization"
SOURCE_UPLOAD_EXECUTION_SECONDS = 900
SOURCE_UPLOAD_TIME_LIMIT_MS = 960_000
SOURCE_UPLOAD_LEASE_SECONDS = 1_200
SOURCE_UPLOAD_RECOVERY_BATCH_SIZE = 100
SOURCE_UPLOAD_OUTBOX_MIN_AGE_SECONDS = 30
_logger = logging.getLogger(__name__)


class SourceUploadDispatcher(Protocol):
    def dispatch(self, upload_id: UUID) -> str: ...


@dataclass(frozen=True, slots=True)
class SourceUploadRecoveryResult:
    enqueued: int
    failures: int


def create_upload_limits(settings: Settings) -> UploadLimits:
    return UploadLimits(
        max_total_bytes=MAX_UPLOAD_INTEGER,
        max_owner_staged_bytes=settings.source_upload_max_owner_staged_bytes,
        max_staged_bytes=settings.source_upload_max_staged_bytes,
        max_active_sessions_per_owner=settings.source_upload_max_active_sessions_per_owner,
        lease_seconds=SOURCE_UPLOAD_LEASE_SECONDS,
    )


def create_upload_artifacts(settings: Settings) -> PrivateUploadArtifacts | None:
    if settings.storage_backend is not StorageBackend.LOCAL:
        return None
    return PrivateUploadArtifacts(root=f"{settings.storage_root}/.source-uploads")


async def dispatch_source_upload(upload_id: UUID, dispatcher: SourceUploadDispatcher) -> bool:
    try:
        await asyncio.to_thread(dispatcher.dispatch, upload_id)
    except Exception:
        _logger.error("source upload finalization dispatch failed")
        return False
    return True


async def run_source_upload_finalization(
    session: AsyncSession,
    upload_id: UUID,
    *,
    storage: ObjectStorage,
    artifacts: PrivateUploadArtifacts,
    limits: UploadLimits,
    read_dispatcher: SourceReadDispatcher,
    execution_deadline: float | None = None,
) -> SourceUploadResponse:
    deadline = (
        time.monotonic() + SOURCE_UPLOAD_EXECUTION_SECONDS
        if execution_deadline is None
        else execution_deadline
    )
    result = await ResumableUploadService(session, storage, artifacts, limits=limits).finalize(
        upload_id, execution_deadline=deadline
    )
    if result.status is UploadStatus.COMPLETED and result.document_id is not None:
        job_id = await session.scalar(
            select(SourceReadJobModel.id)
            .where(
                SourceReadJobModel.document_id == result.document_id,
                SourceReadJobModel.page_number.is_(None),
                SourceReadJobModel.status == "queued",
            )
            .order_by(SourceReadJobModel.created_at, SourceReadJobModel.id)
            .limit(1)
        )
        await session.commit()
        if job_id is not None:
            await dispatch_source_read(job_id, read_dispatcher)
    return result


async def recover_source_uploads(
    service: ResumableUploadService,
    dispatcher: SourceUploadDispatcher,
    *,
    batch_size: int = SOURCE_UPLOAD_RECOVERY_BATCH_SIZE,
) -> SourceUploadRecoveryResult:
    identifiers = await service.pending_upload_ids(
        limit=batch_size, outbox_min_age_seconds=SOURCE_UPLOAD_OUTBOX_MIN_AGE_SECONDS
    )
    enqueued = 0
    for upload_id in identifiers:
        enqueued += int(await dispatch_source_upload(upload_id, dispatcher))
    return SourceUploadRecoveryResult(enqueued=enqueued, failures=len(identifiers) - enqueued)


async def _finalize_source_upload(upload_id: UUID) -> None:
    deadline = time.monotonic() + SOURCE_UPLOAD_EXECUTION_SECONDS
    settings = Settings()
    artifacts = create_upload_artifacts(settings)
    if artifacts is None:
        raise ResumableUploadError("source_upload_storage_unsupported", 503)
    resources = create_resources(settings)
    storage = None
    try:
        storage = create_object_storage(settings)
        async with resources.session_factory() as session:
            await run_source_upload_finalization(
                session,
                upload_id,
                storage=storage,
                artifacts=artifacts,
                limits=create_upload_limits(settings),
                read_dispatcher=DramatiqSourceReadDispatcher(),
                execution_deadline=deadline,
            )
    finally:
        try:
            if storage is not None:
                storage.close()
        finally:
            await resources.close()


async def _recover_source_upload_jobs() -> None:
    settings = Settings()
    artifacts = create_upload_artifacts(settings)
    if artifacts is None:
        return
    resources = create_resources(settings)
    storage = None
    try:
        storage = create_object_storage(settings)
        async with resources.session_factory() as session:
            await recover_source_uploads(
                ResumableUploadService(
                    session, storage, artifacts, limits=create_upload_limits(settings)
                ),
                DramatiqSourceUploadDispatcher(),
            )
    finally:
        try:
            if storage is not None:
                storage.close()
        finally:
            await resources.close()


@dramatiq.actor(
    queue_name=SOURCE_UPLOAD_QUEUE_NAME, max_retries=0, time_limit=SOURCE_UPLOAD_TIME_LIMIT_MS
)
def finalize_source_upload(upload_id: str) -> None:
    asyncio.run(_finalize_source_upload(UUID(upload_id)))


@dramatiq.actor(
    queue_name=SOURCE_UPLOAD_QUEUE_NAME, max_retries=0, time_limit=SOURCE_UPLOAD_TIME_LIMIT_MS
)
def recover_source_upload_jobs() -> None:
    asyncio.run(_recover_source_upload_jobs())


class _QueuedMessage(Protocol):
    message_id: str


class _SourceUploadActor(Protocol):
    def send(self, upload_id: str) -> _QueuedMessage: ...


class DramatiqSourceUploadDispatcher:
    def __init__(self, actor: _SourceUploadActor | None = None) -> None:
        self._actor = actor or cast(_SourceUploadActor, finalize_source_upload)

    def dispatch(self, upload_id: UUID) -> str:
        return self._actor.send(str(upload_id)).message_id


def create_source_upload_dispatcher(settings: Settings) -> DramatiqSourceUploadDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    for actor in (finalize_source_upload, recover_source_upload_jobs):
        actor.broker = broker
        broker.declare_actor(actor)
    return DramatiqSourceUploadDispatcher()
