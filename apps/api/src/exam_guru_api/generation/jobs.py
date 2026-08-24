from __future__ import annotations

import asyncio
from typing import Protocol, cast
from uuid import UUID

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings

GENERATION_QUEUE_NAME = "question-generation"
GENERATION_JOB_MAX_RETRIES = 0
GENERATION_JOB_TIME_LIMIT_MS = 5 * 60 * 1_000


class GenerationDispatcher(Protocol):
    def dispatch(self, job_id: UUID, run_id: UUID) -> str: ...


class _QueuedMessage(Protocol):
    message_id: str


class _GenerationActor(Protocol):
    def send(self, job_id: str, run_id: str) -> _QueuedMessage: ...


async def _process_generation_job(job_id: UUID, run_id: UUID) -> None:
    from exam_guru_api.generation.run_service import GenerationWorkerService
    from exam_guru_api.generation.runtime import create_generation_runtime
    from exam_guru_api.infrastructure.resources import create_resources

    settings = Settings()
    resources = create_resources(settings)
    try:
        async with resources.session_factory() as session:
            await GenerationWorkerService(
                session,
                create_generation_runtime(settings),
            ).process(job_id, run_id)
    finally:
        await resources.close()


@dramatiq.actor(
    queue_name=GENERATION_QUEUE_NAME,
    max_retries=GENERATION_JOB_MAX_RETRIES,
    time_limit=GENERATION_JOB_TIME_LIMIT_MS,
)
def generate_question(job_id: str, run_id: str) -> None:
    asyncio.run(_process_generation_job(UUID(job_id), UUID(run_id)))


_DEFAULT_GENERATION_ACTOR = cast(_GenerationActor, generate_question)


class DramatiqGenerationDispatcher:
    def __init__(self, actor: _GenerationActor = _DEFAULT_GENERATION_ACTOR) -> None:
        self._actor = actor

    def dispatch(self, job_id: UUID, run_id: UUID) -> str:
        return self._actor.send(str(job_id), str(run_id)).message_id


class DeterministicGenerationDispatcher:
    def __init__(self, message_id: str = "deterministic-generation-message-id") -> None:
        self._message_id = message_id
        self.dispatched: list[tuple[UUID, UUID]] = []

    def dispatch(self, job_id: UUID, run_id: UUID) -> str:
        self.dispatched.append((job_id, run_id))
        return self._message_id


def create_generation_dispatcher(settings: Settings) -> DramatiqGenerationDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    generate_question.broker = broker
    broker.declare_actor(generate_question)
    return DramatiqGenerationDispatcher()
