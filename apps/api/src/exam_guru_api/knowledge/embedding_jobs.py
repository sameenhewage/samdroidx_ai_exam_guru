from __future__ import annotations

import asyncio
from typing import Protocol, cast
from uuid import UUID

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import EMBEDDING_ACTOR_MAX_EXECUTION_SECONDS, Settings
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    create_active_embedding_config,
    create_embedding_provider_registry,
)

EMBEDDING_QUEUE_NAME = "knowledge-embedding"
EMBEDDING_JOB_MAX_RETRIES = 0
EMBEDDING_JOB_TIME_LIMIT_MS = EMBEDDING_ACTOR_MAX_EXECUTION_SECONDS * 1_000


class EmbeddingDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> str: ...


class _QueuedMessage(Protocol):
    message_id: str


class _EmbeddingActor(Protocol):
    def send(self, job_id: str) -> _QueuedMessage: ...


async def _process_embedding_job(job_id: UUID) -> None:
    from exam_guru_api.infrastructure.resources import create_resources
    from exam_guru_api.knowledge.embedding_job_service import EmbeddingWorkerService

    settings = Settings()
    resources = create_resources(settings)
    try:
        try:
            active_config = create_active_embedding_config(settings)
        except ActiveEmbeddingConfigUnavailableError:
            active_config = None
        async with resources.session_factory() as session:
            await EmbeddingWorkerService(
                session,
                create_embedding_provider_registry(settings),
                active_config,
            ).process(job_id)
    finally:
        await resources.close()


async def _recover_embedding_jobs() -> None:
    from exam_guru_api.infrastructure.resources import create_resources
    from exam_guru_api.knowledge.embedding_job_service import (
        EmbeddingRecoveryPolicy,
        EmbeddingRecoveryService,
    )

    settings = Settings()
    resources = create_resources(settings)
    try:
        policy = EmbeddingRecoveryPolicy(
            batch_size=settings.embedding_recovery_batch_size,
            outbox_min_age_seconds=settings.embedding_outbox_min_age_seconds,
            worker_lease_seconds=settings.embedding_worker_lease_seconds,
        )
        async with resources.session_factory() as session:
            await EmbeddingRecoveryService(
                session,
                DramatiqEmbeddingDispatcher(),
                policy,
            ).recover()
    finally:
        await resources.close()


@dramatiq.actor(
    queue_name=EMBEDDING_QUEUE_NAME,
    max_retries=EMBEDDING_JOB_MAX_RETRIES,
    time_limit=EMBEDDING_JOB_TIME_LIMIT_MS,
)
def ingest_embeddings(job_id: str) -> None:
    asyncio.run(_process_embedding_job(UUID(job_id)))


@dramatiq.actor(
    queue_name=EMBEDDING_QUEUE_NAME,
    max_retries=EMBEDDING_JOB_MAX_RETRIES,
    time_limit=EMBEDDING_JOB_TIME_LIMIT_MS,
)
def recover_embedding_jobs() -> None:
    asyncio.run(_recover_embedding_jobs())


_DEFAULT_EMBEDDING_ACTOR = cast(_EmbeddingActor, ingest_embeddings)


class DramatiqEmbeddingDispatcher:
    def __init__(self, actor: _EmbeddingActor = _DEFAULT_EMBEDDING_ACTOR) -> None:
        self._actor = actor

    def dispatch(self, job_id: UUID) -> str:
        return self._actor.send(str(job_id)).message_id


class DeterministicEmbeddingDispatcher:
    def __init__(self, message_id: str = "deterministic-embedding-message-id") -> None:
        self._message_id = message_id
        self.dispatched: list[UUID] = []

    def dispatch(self, job_id: UUID) -> str:
        self.dispatched.append(job_id)
        return self._message_id


def create_embedding_dispatcher(settings: Settings) -> DramatiqEmbeddingDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    ingest_embeddings.broker = broker
    recover_embedding_jobs.broker = broker
    broker.declare_actor(ingest_embeddings)
    broker.declare_actor(recover_embedding_jobs)
    return DramatiqEmbeddingDispatcher()
