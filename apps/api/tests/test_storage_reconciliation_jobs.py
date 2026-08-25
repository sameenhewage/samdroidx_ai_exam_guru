import asyncio
from contextlib import AbstractAsyncContextManager

import pytest

from exam_guru_api.core.config import Settings
from exam_guru_api.storage_reconciliation import jobs
from exam_guru_api.storage_reconciliation.service import ReconciliationExecution


class SessionContext(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        del args


class SessionFactory:
    def __call__(self) -> SessionContext:
        return SessionContext()


class FakeResources:
    def __init__(self, close_error: Exception | None = None) -> None:
        self.session_factory = SessionFactory()
        self.close_error = close_error
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeStorage:
    def __init__(self, close_error: Exception | None = None) -> None:
        self.close_error = close_error
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def test_reconciliation_actor_is_bounded_non_retrying_and_registered_with_worker() -> None:
    from exam_guru_api import worker

    assert jobs.reconcile_source_objects.options == {
        "max_retries": 0,
        "time_limit": jobs.RECONCILIATION_TIME_LIMIT_MS,
    }
    assert jobs.reconcile_source_objects.queue_name == jobs.RECONCILIATION_QUEUE_NAME
    assert (
        worker.broker.get_actor(jobs.reconcile_source_objects.actor_name)
        is jobs.reconcile_source_objects
    )


def test_reconciliation_actor_builds_configured_service_and_closes_storage_and_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        storage_reconciliation_interval_seconds=7200,
        storage_reconciliation_grace_seconds=172800,
        storage_reconciliation_max_objects_per_run=77,
        storage_reconciliation_apply_tags=True,
    )
    resources = FakeResources()
    storage = FakeStorage()
    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, repository: object, actual_storage: object, policy: object) -> None:
            captured.update(
                repository=repository,
                storage=actual_storage,
                policy=policy,
            )

        async def reconcile(self) -> ReconciliationExecution:
            captured["reconciled"] = True
            return ReconciliationExecution(skipped=True, run=None)

    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda actual: resources)
    monkeypatch.setattr(jobs, "create_object_storage", lambda actual: storage)
    monkeypatch.setattr(jobs, "SqlAlchemyStorageReconciliationRepository", lambda session: session)
    monkeypatch.setattr(jobs, "StorageReconciliationService", FakeService)

    asyncio.run(jobs._reconcile_source_objects())

    policy = captured["policy"]
    assert policy.interval_seconds == 7200  # type: ignore[attr-defined]
    assert policy.grace_seconds == 172800  # type: ignore[attr-defined]
    assert policy.max_objects_per_run == 77  # type: ignore[attr-defined]
    assert policy.apply_tags is True  # type: ignore[attr-defined]
    assert captured["storage"] is storage
    assert captured["reconciled"] is True
    assert storage.closed is True
    assert resources.closed is True


def test_reconciliation_actor_closes_both_resources_when_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = FakeResources()
    storage = FakeStorage()

    class FailingService:
        def __init__(self, *_args: object) -> None:
            pass

        async def reconcile(self) -> ReconciliationExecution:
            raise RuntimeError("raw provider detail must not be logged")

    monkeypatch.setattr(jobs, "Settings", lambda: Settings(environment="test"))
    monkeypatch.setattr(jobs, "create_resources", lambda actual: resources)
    monkeypatch.setattr(jobs, "create_object_storage", lambda actual: storage)
    monkeypatch.setattr(jobs, "SqlAlchemyStorageReconciliationRepository", lambda session: session)
    monkeypatch.setattr(jobs, "StorageReconciliationService", FailingService)

    with pytest.raises(jobs.StorageReconciliationActorError) as raised:
        asyncio.run(jobs._reconcile_source_objects())

    assert str(raised.value) == "storage_reconciliation_failed"
    assert raised.value.__cause__ is None
    assert "raw provider detail" not in str(raised.value)
    assert storage.closed is True
    assert resources.closed is True


@pytest.mark.parametrize("failing_resource", ["storage", "resources"])
def test_reconciliation_actor_sanitizes_close_failures_and_attempts_both_closes(
    failing_resource: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_error = RuntimeError("private endpoint and object key from close")
    resources = FakeResources(private_error if failing_resource == "resources" else None)
    storage = FakeStorage(private_error if failing_resource == "storage" else None)

    class SuccessfulService:
        def __init__(self, *_args: object) -> None:
            pass

        async def reconcile(self) -> ReconciliationExecution:
            return ReconciliationExecution(skipped=True, run=None)

    monkeypatch.setattr(jobs, "Settings", lambda: Settings(environment="test"))
    monkeypatch.setattr(jobs, "create_resources", lambda actual: resources)
    monkeypatch.setattr(jobs, "create_object_storage", lambda actual: storage)
    monkeypatch.setattr(jobs, "SqlAlchemyStorageReconciliationRepository", lambda session: session)
    monkeypatch.setattr(jobs, "StorageReconciliationService", SuccessfulService)

    with pytest.raises(jobs.StorageReconciliationActorError) as raised:
        asyncio.run(jobs._reconcile_source_objects())

    assert str(raised.value) == "storage_reconciliation_failed"
    assert "private" not in str(raised.value)
    assert storage.closed is True
    assert resources.closed is True


@pytest.mark.parametrize("failing_factory", ["resources", "storage"])
def test_reconciliation_actor_sanitizes_factory_failures(
    failing_factory: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = FakeResources()
    private_error = RuntimeError("private factory endpoint")

    def create_resources(_settings: Settings) -> FakeResources:
        if failing_factory == "resources":
            raise private_error
        return resources

    def create_storage(_settings: Settings) -> FakeStorage:
        if failing_factory == "storage":
            raise private_error
        return FakeStorage()

    monkeypatch.setattr(jobs, "Settings", lambda: Settings(environment="test"))
    monkeypatch.setattr(jobs, "create_resources", create_resources)
    monkeypatch.setattr(jobs, "create_object_storage", create_storage)

    with pytest.raises(jobs.StorageReconciliationActorError) as raised:
        asyncio.run(jobs._reconcile_source_objects())

    assert str(raised.value) == "storage_reconciliation_failed"
    assert "private" not in str(raised.value)
    assert resources.closed is (failing_factory == "storage")


def test_actor_entrypoint_runs_the_async_reconciler(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def reconcile() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(jobs, "_reconcile_source_objects", reconcile)

    jobs.reconcile_source_objects()

    assert calls == 1
