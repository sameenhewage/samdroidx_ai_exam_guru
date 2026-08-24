import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from exam_guru_api.core.config import Settings
from exam_guru_api.generation import jobs
from exam_guru_api.generation.jobs import (
    GENERATION_JOB_MAX_RETRIES,
    GENERATION_JOB_TIME_LIMIT_MS,
    GENERATION_QUEUE_NAME,
    DeterministicGenerationDispatcher,
    DramatiqGenerationDispatcher,
    GenerationDispatcher,
    create_generation_dispatcher,
)

JOB_ID = UUID(int=930_001)
RUN_ID = UUID(int=930_002)


class StubMessage:
    message_id = "generation-broker-message"


class RecordingActor:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, job_id: str, run_id: str) -> StubMessage:
        self.sent.append((job_id, run_id))
        return StubMessage()


def test_generation_dispatchers_enqueue_only_durable_serializable_ids() -> None:
    actor = RecordingActor()
    dispatcher = DramatiqGenerationDispatcher(actor)

    assert dispatcher.dispatch(JOB_ID, RUN_ID) == "generation-broker-message"
    assert actor.sent == [(str(JOB_ID), str(RUN_ID))]

    deterministic: GenerationDispatcher = DeterministicGenerationDispatcher("deterministic-id")
    assert deterministic.dispatch(JOB_ID, RUN_ID) == "deterministic-id"
    assert deterministic.dispatch(JOB_ID, RUN_ID) == "deterministic-id"
    assert deterministic.dispatched == [(JOB_ID, RUN_ID), (JOB_ID, RUN_ID)]  # type: ignore[attr-defined]


def test_generation_actor_has_a_dedicated_bounded_nonduplicating_policy() -> None:
    assert jobs.generate_question.queue_name == GENERATION_QUEUE_NAME
    assert jobs.generate_question.queue_name != "default"
    assert jobs.generate_question.options == {
        "max_retries": GENERATION_JOB_MAX_RETRIES,
        "time_limit": GENERATION_JOB_TIME_LIMIT_MS,
    }
    assert jobs.recover_generation_jobs.queue_name == GENERATION_QUEUE_NAME
    assert jobs.recover_generation_jobs.options == {
        "max_retries": GENERATION_JOB_MAX_RETRIES,
        "time_limit": GENERATION_JOB_TIME_LIMIT_MS,
    }
    assert GENERATION_JOB_MAX_RETRIES == 0
    assert GENERATION_JOB_TIME_LIMIT_MS > 0


def test_generation_dispatcher_factory_and_worker_register_the_actor() -> None:
    dispatcher = create_generation_dispatcher(Settings())
    assert isinstance(dispatcher, DramatiqGenerationDispatcher)
    assert jobs.generate_question.actor_name in jobs.generate_question.broker.get_declared_actors()

    worker = importlib.import_module("exam_guru_api.worker")
    registered = worker.broker.get_actor(jobs.generate_question.actor_name)
    recovery = worker.broker.get_actor(jobs.recover_generation_jobs.actor_name)
    assert registered is jobs.generate_question
    assert registered.queue_name == GENERATION_QUEUE_NAME
    assert recovery is jobs.recover_generation_jobs
    assert recovery.queue_name == GENERATION_QUEUE_NAME


def test_generation_actor_builds_worker_dependencies_and_always_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.generation import run_service, runtime
    from exam_guru_api.infrastructure import resources as resources_module

    settings = Settings(environment="test")
    session = object()
    runtime_registry = object()
    calls: list[tuple[UUID, UUID]] = []

    class StubResources:
        def __init__(self) -> None:
            self.closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield session

        async def close(self) -> None:
            self.closed = True

    class StubWorker:
        def __init__(self, actual_session: object, actual_runtime: object) -> None:
            assert actual_session is session
            assert actual_runtime is runtime_registry

        async def process(self, job_id: UUID, run_id: UUID) -> bool:
            calls.append((job_id, run_id))
            return True

    resources = StubResources()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(
        resources_module,
        "create_resources",
        lambda actual: resources if actual is settings else None,
    )
    monkeypatch.setattr(
        runtime,
        "create_generation_runtime",
        lambda actual: runtime_registry if actual is settings else None,
    )
    monkeypatch.setattr(run_service, "GenerationWorkerService", StubWorker)

    jobs.generate_question(str(JOB_ID), str(RUN_ID))

    assert calls == [(JOB_ID, RUN_ID)]
    assert resources.closed is True


def test_generation_recovery_actor_uses_internal_bounded_settings_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.generation import run_service
    from exam_guru_api.infrastructure import resources as resources_module

    settings = Settings.model_validate(
        {
            "environment": "test",
            "generation_recovery_batch_size": 7,
            "generation_outbox_min_age_seconds": 11,
            "generation_worker_lease_seconds": 700,
        }
    )
    session = object()
    dispatcher = object()
    policies: list[object] = []

    class StubResources:
        def __init__(self) -> None:
            self.closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield session

        async def close(self) -> None:
            self.closed = True

    class StubRecoveryService:
        def __init__(
            self,
            actual_session: object,
            actual_dispatcher: object,
            policy: object,
        ) -> None:
            assert actual_session is session
            assert actual_dispatcher is dispatcher
            policies.append(policy)

        async def recover(self) -> object:
            return object()

    resources = StubResources()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "DramatiqGenerationDispatcher", lambda: dispatcher)
    monkeypatch.setattr(
        resources_module,
        "create_resources",
        lambda actual: resources if actual is settings else None,
    )
    monkeypatch.setattr(run_service, "GenerationRecoveryService", StubRecoveryService)

    jobs.recover_generation_jobs()

    assert len(policies) == 1
    policy = policies[0]
    assert policy.batch_size == 7  # type: ignore[attr-defined]
    assert policy.outbox_min_age_seconds == 11  # type: ignore[attr-defined]
    assert policy.worker_lease_seconds == 700  # type: ignore[attr-defined]
    assert resources.closed is True
