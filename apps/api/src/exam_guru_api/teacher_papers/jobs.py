from __future__ import annotations

import asyncio
from typing import Protocol, cast
from uuid import UUID

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import TEACHER_PAPER_ACTOR_MAX_EXECUTION_SECONDS, Settings
from exam_guru_api.generation.jobs import DramatiqGenerationDispatcher
from exam_guru_api.generation.runtime import create_generation_runtime
from exam_guru_api.retrieval.embeddings import create_embedding_provider_registry
from exam_guru_api.validation.pipeline import build_default_pipeline

PAPER_GENERATION_QUEUE_NAME = "teacher-paper-generation"
PAPER_GENERATION_JOB_MAX_RETRIES = 0
PAPER_GENERATION_JOB_TIME_LIMIT_MS = TEACHER_PAPER_ACTOR_MAX_EXECUTION_SECONDS * 1_000


class PaperGenerationDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> str: ...


class _QueuedMessage(Protocol):
    message_id: str


class _PaperActor(Protocol):
    def send(self, job_id: str) -> _QueuedMessage: ...


async def _advance_teacher_paper(job_id: UUID) -> None:
    from exam_guru_api.infrastructure.resources import create_resources
    from exam_guru_api.teacher_papers.service import TeacherPaperWorkerService

    settings = Settings()
    resources = create_resources(settings)
    try:
        async with resources.session_factory() as session:
            await TeacherPaperWorkerService(
                session,
                DramatiqPaperGenerationDispatcher(),
                DramatiqGenerationDispatcher(),
                create_generation_runtime(settings),
                create_embedding_provider_registry(settings),
                build_default_pipeline(),
                actor_lease_seconds=settings.teacher_paper_actor_lease_seconds,
            ).advance(job_id)
    finally:
        await resources.close()


async def _recover_teacher_papers() -> None:
    from exam_guru_api.infrastructure.resources import create_resources
    from exam_guru_api.teacher_papers.service import TeacherPaperRecoveryService

    settings = Settings()
    resources = create_resources(settings)
    try:
        async with resources.session_factory() as session:
            await TeacherPaperRecoveryService(
                session,
                DramatiqPaperGenerationDispatcher(),
                batch_size=settings.teacher_paper_recovery_batch_size,
                actor_lease_seconds=settings.teacher_paper_actor_lease_seconds,
            ).recover()
    finally:
        await resources.close()


@dramatiq.actor(
    queue_name=PAPER_GENERATION_QUEUE_NAME,
    max_retries=PAPER_GENERATION_JOB_MAX_RETRIES,
    time_limit=PAPER_GENERATION_JOB_TIME_LIMIT_MS,
)
def advance_teacher_paper(job_id: str) -> None:
    asyncio.run(_advance_teacher_paper(UUID(job_id)))


@dramatiq.actor(
    queue_name=PAPER_GENERATION_QUEUE_NAME,
    max_retries=PAPER_GENERATION_JOB_MAX_RETRIES,
    time_limit=PAPER_GENERATION_JOB_TIME_LIMIT_MS,
)
def recover_teacher_papers() -> None:
    asyncio.run(_recover_teacher_papers())


_DEFAULT_PAPER_ACTOR = cast(_PaperActor, advance_teacher_paper)


class DramatiqPaperGenerationDispatcher:
    def __init__(self, actor: _PaperActor = _DEFAULT_PAPER_ACTOR) -> None:
        self._actor = actor

    def dispatch(self, job_id: UUID) -> str:
        return self._actor.send(str(job_id)).message_id


class DeterministicPaperGenerationDispatcher:
    def __init__(self, message_id: str = "deterministic-teacher-paper-message") -> None:
        self._message_id = message_id
        self.dispatched: list[UUID] = []

    def dispatch(self, job_id: UUID) -> str:
        self.dispatched.append(job_id)
        return self._message_id


def create_paper_generation_dispatcher(settings: Settings) -> DramatiqPaperGenerationDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    advance_teacher_paper.broker = broker
    recover_teacher_papers.broker = broker
    broker.declare_actor(advance_teacher_paper)
    broker.declare_actor(recover_teacher_papers)
    return DramatiqPaperGenerationDispatcher()
