import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from exam_guru_api.core.config import Settings
from exam_guru_api.teacher_papers import jobs
from exam_guru_api.teacher_papers.jobs import (
    PAPER_GENERATION_JOB_MAX_RETRIES,
    PAPER_GENERATION_JOB_TIME_LIMIT_MS,
    PAPER_GENERATION_QUEUE_NAME,
    DeterministicPaperGenerationDispatcher,
    DramatiqPaperGenerationDispatcher,
    PaperGenerationDispatcher,
    create_paper_generation_dispatcher,
)

JOB_ID = UUID(int=25_901)


class StubMessage:
    message_id = "paper-generation-message"


class RecordingActor:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, job_id: str) -> StubMessage:
        self.sent.append(job_id)
        return StubMessage()


def test_paper_dispatchers_enqueue_only_a_durable_job_identifier() -> None:
    actor = RecordingActor()
    dispatcher = DramatiqPaperGenerationDispatcher(actor)

    assert dispatcher.dispatch(JOB_ID) == "paper-generation-message"
    assert actor.sent == [str(JOB_ID)]

    deterministic: PaperGenerationDispatcher = DeterministicPaperGenerationDispatcher("fixed")
    assert deterministic.dispatch(JOB_ID) == "fixed"
    assert deterministic.dispatch(JOB_ID) == "fixed"
    assert deterministic.dispatched == [JOB_ID, JOB_ID]  # type: ignore[attr-defined]


def test_paper_actors_are_bounded_and_never_replay_provider_work_automatically() -> None:
    assert jobs.advance_teacher_paper.queue_name == PAPER_GENERATION_QUEUE_NAME
    assert jobs.advance_teacher_paper.options == {
        "max_retries": PAPER_GENERATION_JOB_MAX_RETRIES,
        "time_limit": PAPER_GENERATION_JOB_TIME_LIMIT_MS,
    }
    assert jobs.recover_teacher_papers.queue_name == PAPER_GENERATION_QUEUE_NAME
    assert jobs.recover_teacher_papers.options == {
        "max_retries": PAPER_GENERATION_JOB_MAX_RETRIES,
        "time_limit": PAPER_GENERATION_JOB_TIME_LIMIT_MS,
    }
    assert PAPER_GENERATION_JOB_MAX_RETRIES == 0
    assert PAPER_GENERATION_JOB_TIME_LIMIT_MS > 0


def test_dispatcher_factory_and_worker_register_both_paper_actors() -> None:
    dispatcher = create_paper_generation_dispatcher(Settings(environment="test"))
    assert isinstance(dispatcher, DramatiqPaperGenerationDispatcher)
    declared = jobs.advance_teacher_paper.broker.get_declared_actors()
    assert jobs.advance_teacher_paper.actor_name in declared
    assert jobs.recover_teacher_papers.actor_name in declared

    worker = importlib.import_module("exam_guru_api.worker")
    assert (
        worker.broker.get_actor(jobs.advance_teacher_paper.actor_name) is jobs.advance_teacher_paper
    )
    assert (
        worker.broker.get_actor(jobs.recover_teacher_papers.actor_name)
        is jobs.recover_teacher_papers
    )


def test_paper_actor_builds_private_worker_dependencies_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.infrastructure import resources as resources_module
    from exam_guru_api.teacher_papers import service

    settings = Settings(environment="test")
    session = object()
    dispatcher = object()
    generation_dispatcher = object()
    runtime = object()
    embedding_registry = object()
    pipeline = object()
    calls: list[UUID] = []

    class StubResources:
        closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield session

        async def close(self) -> None:
            self.closed = True

    class StubWorker:
        def __init__(
            self,
            actual_session: object,
            actual_dispatcher: object,
            actual_generation_dispatcher: object,
            actual_runtime: object,
            actual_embeddings: object,
            actual_pipeline: object,
            *,
            actor_lease_seconds: int,
        ) -> None:
            assert actor_lease_seconds == settings.teacher_paper_actor_lease_seconds
            assert (
                actual_session,
                actual_dispatcher,
                actual_generation_dispatcher,
                actual_runtime,
                actual_embeddings,
                actual_pipeline,
            ) == (
                session,
                dispatcher,
                generation_dispatcher,
                runtime,
                embedding_registry,
                pipeline,
            )

        async def advance(self, job_id: UUID) -> bool:
            calls.append(job_id)
            return True

    resources = StubResources()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(resources_module, "create_resources", lambda actual: resources)
    monkeypatch.setattr(jobs, "DramatiqPaperGenerationDispatcher", lambda: dispatcher)
    monkeypatch.setattr(jobs, "DramatiqGenerationDispatcher", lambda: generation_dispatcher)
    monkeypatch.setattr(jobs, "create_generation_runtime", lambda actual: runtime)
    monkeypatch.setattr(
        jobs, "create_embedding_provider_registry", lambda actual: embedding_registry
    )
    monkeypatch.setattr(jobs, "build_default_pipeline", lambda: pipeline)
    monkeypatch.setattr(service, "TeacherPaperWorkerService", StubWorker)

    jobs.advance_teacher_paper(str(JOB_ID))

    assert calls == [JOB_ID]
    assert resources.closed is True


def test_paper_recovery_actor_runs_a_bounded_recovery_pass_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.infrastructure import resources as resources_module
    from exam_guru_api.teacher_papers import service

    settings = Settings(
        environment="test",
        teacher_paper_recovery_batch_size=7,
        teacher_paper_actor_lease_seconds=601,
    )
    session = object()
    dispatcher = object()
    seen: list[tuple[int, int]] = []

    class StubResources:
        closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield session

        async def close(self) -> None:
            self.closed = True

    class StubRecovery:
        def __init__(
            self,
            actual_session: object,
            actual_dispatcher: object,
            *,
            batch_size: int,
            actor_lease_seconds: int,
        ) -> None:
            assert actual_session is session
            assert actual_dispatcher is dispatcher
            seen.append((batch_size, actor_lease_seconds))

        async def recover(self) -> object:
            return object()

    resources = StubResources()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(resources_module, "create_resources", lambda actual: resources)
    monkeypatch.setattr(jobs, "DramatiqPaperGenerationDispatcher", lambda: dispatcher)
    monkeypatch.setattr(service, "TeacherPaperRecoveryService", StubRecovery)

    jobs.recover_teacher_papers()

    assert seen == [(7, 601)]
    assert resources.closed is True
