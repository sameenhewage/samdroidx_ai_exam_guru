import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.documents.understanding_jobs as jobs
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.page_images import PageImageError
from exam_guru_api.documents.understanding_runtime import create_understanding_runtime
from exam_guru_api.documents.understanding_service import UnderstandingConflictError
from exam_guru_api.generation.domain import GenerationAccounting
from tests.test_document_understanding_provider import request as fixture_request


class Resources:
    def __init__(self) -> None:
        self.closed = False
        self.session = object()

    @asynccontextmanager
    async def session_factory(self) -> AsyncIterator[object]:
        yield self.session

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("failure", [None, "storage", "processing"])
def test_understanding_worker_closes_private_resources_on_every_path(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    settings = Settings(
        environment="test",
        document_understanding_provider="deterministic",
        document_understanding_fixture_runtime_id="ai-exam-guru-e2e-understanding-worker",
    )
    runtime = create_understanding_runtime(settings)
    assert runtime is not None
    resources = Resources()
    closed: list[bool] = []
    storage = SimpleNamespace(close=lambda: closed.append(True))
    identifier = uuid4()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)
    monkeypatch.setattr(jobs, "create_understanding_runtime", lambda _: runtime)
    monkeypatch.setattr(jobs, "create_page_image_artifacts", lambda _: object())

    def create_storage(_: Settings) -> object:
        if failure == "storage":
            raise RuntimeError("controlled storage failure")
        return storage

    async def input_factory(*args: Any, **kwargs: Any) -> Any:
        return lambda _: None

    async def process(session: object, job_id: UUID, **kwargs: Any) -> None:
        assert session is resources.session
        assert job_id == identifier
        assert kwargs["runtime"] is runtime
        if failure == "processing":
            raise RuntimeError("controlled processing failure")

    monkeypatch.setattr(jobs, "create_object_storage", create_storage)
    monkeypatch.setattr(jobs, "_prepare_job_input_factory", input_factory)
    monkeypatch.setattr(jobs, "run_understanding_job", process)
    if failure is None:
        asyncio.run(jobs._execute_understanding_job(identifier))
    else:
        with pytest.raises(RuntimeError, match="controlled"):
            asyncio.run(jobs._execute_understanding_job(identifier))
    assert resources.closed
    assert closed == ([] if failure == "storage" else [True])


def test_disabled_runtime_never_opens_originals_or_creates_a_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test")
    resources = Resources()
    calls: list[UUID] = []
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)

    async def fail_queued(session: object, job_id: UUID) -> None:
        assert session is resources.session
        calls.append(job_id)

    def forbidden_storage(_: Settings) -> object:
        raise AssertionError("disabled understanding must not open private originals")

    monkeypatch.setattr(jobs, "_fail_unconfigured_job", fail_queued)
    monkeypatch.setattr(jobs, "create_object_storage", forbidden_storage)
    identifier = uuid4()
    asyncio.run(jobs._execute_understanding_job(identifier))
    assert calls == [identifier]
    assert resources.closed


def test_recovery_worker_only_dispatches_and_does_not_build_model_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        document_understanding_recovery_batch_size=7,
        document_understanding_outbox_min_age_seconds=9,
    )
    resources = Resources()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)

    async def recover(session: object, dispatcher: object, **kwargs: Any) -> None:
        assert session is resources.session
        calls.append(kwargs)

    def forbidden_runtime(_: Settings) -> object:
        raise AssertionError("recovery must not construct a paid model")

    monkeypatch.setattr(jobs, "create_understanding_runtime", forbidden_runtime)
    monkeypatch.setattr(jobs, "recover_understanding_jobs", recover)
    asyncio.run(jobs._recover_understanding_jobs())
    assert calls == [{"batch_size": 7, "min_age_seconds": 9}]
    assert resources.closed


@pytest.mark.parametrize("status", ["queued", "running"])
def test_worker_input_failure_only_finalizes_its_unclaimed_job(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    settings = Settings(
        environment="test",
        document_understanding_provider="deterministic",
        document_understanding_fixture_runtime_id="ai-exam-guru-e2e-worker-input",
    )
    resources = Resources()
    session = AsyncMock()
    resources.session = session
    failure = AsyncMock()
    identifier = uuid4()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)
    monkeypatch.setattr(
        jobs, "create_object_storage", lambda _: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(jobs, "create_page_image_artifacts", lambda _: None)
    monkeypatch.setattr(
        jobs,
        "_prepare_job_input_factory",
        AsyncMock(side_effect=PageImageError("source_original_unavailable")),
    )
    monkeypatch.setattr(
        jobs, "_locked", AsyncMock(return_value=(SimpleNamespace(status=status), None, None))
    )
    monkeypatch.setattr(jobs, "_failure", failure)
    asyncio.run(jobs._execute_understanding_job(identifier))
    session.rollback.assert_awaited_once()
    assert failure.await_count == int(status == "queued")
    assert resources.closed


def test_actor_entrypoints_forward_only_the_opaque_job_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute, recover = AsyncMock(), AsyncMock()
    monkeypatch.setattr(jobs, "_execute_understanding_job", execute)
    monkeypatch.setattr(jobs, "_recover_understanding_jobs", recover)
    identifier = uuid4()
    jobs.understand_source_page(str(identifier))
    jobs.recover_understanding_page_jobs()
    execute.assert_awaited_once_with(identifier)
    recover.assert_awaited_once_with()


@pytest.mark.parametrize("mode", ["exception", "empty", "invalid", "success"])
def test_dispatch_failure_does_not_discard_queued_work(
    mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    class Dispatcher:
        def dispatch(self, job_id: UUID) -> str:
            if mode == "exception":
                raise RuntimeError("private-queue-payload")
            if mode == "invalid":
                return cast(str, None)
            return "ok" if mode == "success" else ""

    assert asyncio.run(jobs.dispatch_understanding_job(uuid4(), Dispatcher())) == (
        mode == "success"
    )
    assert "private-queue-payload" not in caplog.text


def test_invalid_accounting_objects_are_not_persisted_as_known_usage() -> None:
    assert jobs._known_accounting(object()) is None
    accounting = GenerationAccounting(
        input_tokens=1, output_tokens=1, total_tokens=2, cost_microusd=1, latency_ms=1
    )
    object.__setattr__(accounting, "total_tokens", 99)
    assert jobs._known_accounting(accounting) is None


def test_missing_locked_dependencies_and_saved_results_fail_closed() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = SimpleNamespace(document_id=uuid4(), page_number=1)
    session.scalar.return_value = None
    with pytest.raises(jobs.UnderstandingJobNotFoundError):
        asyncio.run(jobs._locked(session, uuid4()))
    with pytest.raises(UnderstandingConflictError):
        asyncio.run(
            jobs._complete_saved_run(
                session,
                cast(Any, object()),
                cast(Any, object()),
                cast(Any, SimpleNamespace(id=uuid4(), accounting=None)),
            )
        )


@pytest.mark.parametrize("value", [True, 0, 300, 86401])
def test_worker_rejects_invalid_lease_before_database_access(value: Any) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="lease"):
        asyncio.run(
            jobs.run_understanding_job(
                session,
                uuid4(),
                runtime=cast(Any, None),
                input_factory=cast(Any, None),
                lease_seconds=value,
            )
        )
    assert not session.mock_calls


@pytest.mark.parametrize(("batch", "age"), [(True, 1), (0, 1), (101, 1), (1, 0), (1, 3601)])
def test_recovery_limits_are_validated_before_database_access(batch: Any, age: int) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="limits"):
        asyncio.run(
            jobs.recover_understanding_jobs(
                session, cast(Any, object()), batch_size=batch, min_age_seconds=age
            )
        )
    assert not session.mock_calls


@pytest.mark.parametrize("mode", ["already_finished", "unacknowledged", "transport_failure"])
def test_recovery_skips_completed_work_and_keeps_dispatch_failures_retryable(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    identifier = uuid4()
    session.scalars.return_value = SimpleNamespace(all=lambda: [identifier])
    job = SimpleNamespace(status="succeeded" if mode == "already_finished" else "queued")
    monkeypatch.setattr(jobs, "_locked", AsyncMock(return_value=(job, None, None)))
    calls: list[UUID] = []

    class Dispatcher:
        def dispatch(self, job_id: UUID) -> str:
            calls.append(job_id)
            if mode == "transport_failure":
                raise RuntimeError("private queue detail")
            return ""

    result = asyncio.run(jobs.recover_understanding_jobs(session, Dispatcher()))
    assert result.enqueued == 0
    assert result.failures == int(mode != "already_finished")
    assert calls == ([] if mode == "already_finished" else [identifier])


def test_failed_orphan_job_does_not_rewrite_an_unowned_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_request()
    session = AsyncMock(spec=AsyncSession)
    job = SimpleNamespace(
        id=uuid4(),
        document_id=fixture.source.document_id,
        page_number=1,
        source_sha256=fixture.source.source_sha256,
        request_id=uuid4(),
        created_by=uuid4(),
        status="queued",
        version=0,
        expected_page_version=1,
        profile=fixture.profile.model_dump(mode="json"),
        budget=fixture.budget.model_dump(mode="json"),
        accounting=None,
        candidate_id=None,
        run_id=None,
        failure_code=None,
        lease_token=None,
        attempts=0,
        retry_depth=0,
        provider_started_at=None,
        image_metadata=None,
    )
    page = SimpleNamespace(
        active_job_id=None,
        version=0,
        current_candidate_id=None,
        current_report_id=None,
        current_trusted_id=None,
    )
    monkeypatch.setattr(jobs, "_locked", AsyncMock(return_value=(job, page, None)))
    result = asyncio.run(
        jobs._failure(session, job.id, None, code="source_understanding_source_changed")
    )
    assert result.status == "failed"
    assert page.version == 0
    assert page.active_job_id is None


def test_understanding_actor_envelopes_have_no_automatic_paid_retries() -> None:
    assert jobs.understand_source_page.options["max_retries"] == 0
    assert jobs.recover_understanding_page_jobs.options["max_retries"] == 0
    assert jobs.understand_source_page.options["time_limit"] == 300000
    sent: list[str] = []

    class Actor:
        def send(self, job_id: str) -> Any:
            sent.append(job_id)
            return SimpleNamespace(message_id="fixture-message")

    identifier = uuid4()
    assert jobs.DramatiqUnderstandingDispatcher(Actor()).dispatch(identifier) == "fixture-message"
    assert sent == [str(identifier)]
