import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings
from exam_guru_api.documents import upload_jobs as jobs
from exam_guru_api.documents.resumable_uploads import ResumableUploadError, ResumableUploadService
from exam_guru_api.documents.upload_schemas import SourceUploadResponse, UploadStatus
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.infrastructure.private_artifacts import PrivateUploadArtifacts

UPLOAD_ID = UUID(int=36031)
DOCUMENT_ID = UUID(int=36032)
READ_ID = UUID(int=36033)


class Message:
    message_id = "upload-fixture-message"


class Actor:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, upload_id: str) -> Message:
        self.sent.append(upload_id)
        if self.fail:
            raise ConnectionError("PRIVATE transport detail")
        return Message()


class Dispatcher:
    def __init__(self, *, fail_on: UUID | None = None) -> None:
        self.dispatched: list[UUID] = []
        self.fail_on = fail_on

    def dispatch(self, identifier: UUID) -> str:
        self.dispatched.append(identifier)
        if identifier == self.fail_on:
            raise ConnectionError("PRIVATE queue detail")
        return "upload-fixture-message"


class Resources:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.closed = False

    @asynccontextmanager
    async def session_factory(self) -> AsyncIterator[AsyncSession]:
        yield self.session

    async def close(self) -> None:
        self.closed = True


class Storage:
    def __init__(self, *, fail_close: bool = False) -> None:
        self.closed = False
        self.fail_close = fail_close

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("fixture close failure")


def upload_view(status: UploadStatus) -> SourceUploadResponse:
    now = datetime.now(UTC)
    return SourceUploadResponse(
        id=UPLOAD_ID,
        filename="source.pdf",
        size_bytes=42,
        document_type="syllabus",
        intake_metadata={},
        status=status,
        next_offset=42,
        verified_bytes=42 if status is UploadStatus.COMPLETED else 0,
        version=3,
        document_id=DOCUMENT_ID if status is UploadStatus.COMPLETED else None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("fail", [False, True])
def test_dispatch_sends_only_the_durable_upload_id_and_sanitizes_failure(
    fail: bool, caplog: pytest.LogCaptureFixture
) -> None:
    actor = Actor(fail=fail)
    dispatcher = jobs.DramatiqSourceUploadDispatcher(actor)
    assert asyncio.run(jobs.dispatch_source_upload(UPLOAD_ID, dispatcher)) is not fail
    assert actor.sent == [str(UPLOAD_ID)]
    assert "PRIVATE" not in caplog.text


def test_upload_actor_entrypoints_have_execution_budgets_below_their_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[UUID | str] = []

    async def finalize(upload_id: UUID) -> None:
        seen.append(upload_id)

    async def recover() -> None:
        seen.append("recover")

    monkeypatch.setattr(jobs, "_finalize_source_upload", finalize)
    monkeypatch.setattr(jobs, "_recover_source_upload_jobs", recover)
    jobs.finalize_source_upload(str(UPLOAD_ID))
    jobs.recover_source_upload_jobs()
    assert seen == [UPLOAD_ID, "recover"]
    for actor in (jobs.finalize_source_upload, jobs.recover_source_upload_jobs):
        assert actor.queue_name == "source-upload-finalization"
        assert actor.options == {"max_retries": 0, "time_limit": jobs.SOURCE_UPLOAD_TIME_LIMIT_MS}
    limits = jobs.create_upload_limits(Settings(environment="test"))
    assert jobs.SOURCE_UPLOAD_EXECUTION_SECONDS * 1000 < jobs.SOURCE_UPLOAD_TIME_LIMIT_MS
    assert limits.lease_seconds * 1000 > jobs.SOURCE_UPLOAD_TIME_LIMIT_MS


def test_upload_dispatcher_factory_registers_both_actors_without_sending() -> None:
    dispatcher = jobs.create_source_upload_dispatcher(Settings(environment="test"))
    assert isinstance(dispatcher, jobs.DramatiqSourceUploadDispatcher)
    broker = jobs.finalize_source_upload.broker
    try:
        assert broker.get_declared_actors() == {
            jobs.finalize_source_upload.actor_name,
            jobs.recover_source_upload_jobs.actor_name,
        }
        assert jobs.recover_source_upload_jobs.broker is broker
    finally:
        broker.close()


def test_worker_binds_upload_actors_alongside_existing_source_read_actors() -> None:
    from exam_guru_api.documents.page_reading_jobs import read_source, recover_source_read_jobs
    from exam_guru_api.worker import create_broker

    broker = create_broker(Settings(environment="test"))
    try:
        for actor in (
            jobs.finalize_source_upload,
            jobs.recover_source_upload_jobs,
            read_source,
            recover_source_read_jobs,
        ):
            assert broker.get_actor(actor.actor_name) is actor
            assert actor.broker is broker
    finally:
        broker.close()


@pytest.mark.parametrize(
    "status",
    [UploadStatus.PENDING, UploadStatus.FINALIZING, UploadStatus.FAILED, UploadStatus.COMPLETED],
)
@pytest.mark.parametrize("dispatch_fails", [False, True])
def test_finalizer_dispatches_only_a_durable_queued_read_after_service_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: UploadStatus, dispatch_fails: bool
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = READ_ID
    backend = AsyncMock(spec=ResumableUploadService)
    backend.finalize.return_value = upload_view(status)
    monkeypatch.setattr(jobs, "ResumableUploadService", lambda *_args, **_kwargs: backend)
    dispatcher = Dispatcher(fail_on=READ_ID if dispatch_fails else None)
    result = asyncio.run(
        jobs.run_source_upload_finalization(
            session,
            UPLOAD_ID,
            storage=cast(ObjectStorage, object()),
            artifacts=PrivateUploadArtifacts(root=tmp_path / "private"),
            limits=jobs.create_upload_limits(Settings(environment="test")),
            read_dispatcher=dispatcher,
            execution_deadline=123.0,
        )
    )
    assert result.status is status
    backend.finalize.assert_awaited_once_with(UPLOAD_ID, execution_deadline=123.0)
    assert dispatcher.dispatched == ([READ_ID] if status is UploadStatus.COMPLETED else [])
    if status is UploadStatus.COMPLETED:
        session.commit.assert_awaited_once()
    else:
        assert not session.mock_calls
    assert not (tmp_path / "private").exists()


def test_terminal_redelivery_does_not_queue_another_read_when_no_read_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None
    backend = AsyncMock(spec=ResumableUploadService)
    backend.finalize.return_value = upload_view(UploadStatus.COMPLETED)
    monkeypatch.setattr(jobs, "ResumableUploadService", lambda *_args, **_kwargs: backend)
    dispatcher = Dispatcher()
    asyncio.run(
        jobs.run_source_upload_finalization(
            session,
            UPLOAD_ID,
            storage=cast(ObjectStorage, object()),
            artifacts=PrivateUploadArtifacts(root=tmp_path / "private"),
            limits=jobs.create_upload_limits(Settings(environment="test")),
            read_dispatcher=dispatcher,
        )
    )
    assert not dispatcher.dispatched


def test_recovery_dispatches_bounded_pending_ids_with_failure_isolation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = AsyncMock(spec=ResumableUploadService)
    backend.pending_upload_ids.return_value = (UPLOAD_ID, UUID(int=36034))
    dispatcher = Dispatcher(fail_on=UPLOAD_ID)
    result = asyncio.run(jobs.recover_source_uploads(backend, dispatcher, batch_size=7))
    backend.pending_upload_ids.assert_awaited_once_with(
        limit=7, outbox_min_age_seconds=jobs.SOURCE_UPLOAD_OUTBOX_MIN_AGE_SECONDS
    )
    backend.finalize.assert_not_called()
    assert result.enqueued == 1
    assert result.failures == 1
    assert dispatcher.dispatched == [UPLOAD_ID, UUID(int=36034)]
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("failure", [None, "create", "run", "close"])
@pytest.mark.parametrize("recovery", [False, True])
def test_upload_actors_own_dependencies_and_always_close_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None, recovery: bool
) -> None:
    settings = Settings(environment="test", storage_root=str(tmp_path / "private"))
    session = AsyncMock(spec=AsyncSession)
    resources = Resources(session)
    storage = Storage(fail_close=failure == "close")
    called: list[str] = []

    def create_storage(actual: Settings) -> Storage:
        assert actual is settings
        if failure == "create":
            raise RuntimeError("fixture create failure")
        return storage

    async def finalize(
        actual_session: AsyncSession, upload_id: UUID, **values: object
    ) -> SourceUploadResponse:
        assert actual_session is session
        assert upload_id == UPLOAD_ID
        assert values["storage"] is storage
        assert isinstance(values["artifacts"], PrivateUploadArtifacts)
        assert isinstance(values["execution_deadline"], float)
        called.append("finalize")
        if failure == "run":
            raise RuntimeError("fixture run failure")
        return upload_view(UploadStatus.COMPLETED)

    async def recover(
        backend: ResumableUploadService, _dispatcher: object
    ) -> jobs.SourceUploadRecoveryResult:
        assert isinstance(backend, ResumableUploadService)
        called.append("recover")
        if failure == "run":
            raise RuntimeError("fixture run failure")
        return jobs.SourceUploadRecoveryResult(enqueued=0, failures=0)

    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda _settings: resources)
    monkeypatch.setattr(jobs, "create_object_storage", create_storage)
    monkeypatch.setattr(jobs, "run_source_upload_finalization", finalize)
    monkeypatch.setattr(jobs, "recover_source_uploads", recover)
    operation = (
        jobs._recover_source_upload_jobs() if recovery else jobs._finalize_source_upload(UPLOAD_ID)
    )
    if failure is None:
        asyncio.run(operation)
    else:
        with pytest.raises(RuntimeError, match=f"fixture {failure} failure"):
            asyncio.run(operation)
    assert called == ([] if failure == "create" else ["recover" if recovery else "finalize"])
    assert resources.closed
    assert storage.closed is (failure != "create")
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize(
    "field", ["source_upload_max_owner_staged_bytes", "source_upload_max_staged_bytes"]
)
@pytest.mark.parametrize("value", [0, -1, True, 2**63])
def test_quota_settings_validate_operational_disk_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "test", field: value})


def test_unsupported_s3_workers_never_create_database_or_local_staging_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private"
    settings = Settings(
        environment="test",
        storage_backend="s3",
        storage_root=str(root),
        object_storage_endpoint_url="http://localhost:9000",
        object_storage_access_key="fixture-access",
        object_storage_secret_key="fixture-secret",
        object_storage_bucket="fixture-sources",
        object_storage_region="us-east-1",
    )

    def forbidden(_settings: Settings) -> None:
        raise AssertionError("unsupported resumable storage must fail before resource access")

    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", forbidden)
    monkeypatch.setattr(jobs, "create_object_storage", forbidden)
    with pytest.raises(ResumableUploadError, match="source_upload_storage_unsupported") as raised:
        asyncio.run(jobs._finalize_source_upload(UPLOAD_ID))
    assert raised.value.status_code == 503
    asyncio.run(jobs._recover_source_upload_jobs())
    assert not root.exists()


def test_upload_budgets_are_environment_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAM_GURU_SOURCE_UPLOAD_MAX_OWNER_STAGED_BYTES", str(12 * 1024**3))
    monkeypatch.setenv("EXAM_GURU_SOURCE_UPLOAD_MAX_STAGED_BYTES", str(48 * 1024**3))
    monkeypatch.setenv("EXAM_GURU_SOURCE_UPLOAD_MAX_ACTIVE_SESSIONS_PER_OWNER", "4")
    limits = jobs.create_upload_limits(Settings(environment="test"))
    assert limits.max_owner_staged_bytes == 12 * 1024**3
    assert limits.max_staged_bytes == 48 * 1024**3
    assert limits.max_active_sessions_per_owner == 4
