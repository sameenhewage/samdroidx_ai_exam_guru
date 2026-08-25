import asyncio
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
    upgrade_database,
)
from exam_guru_api.infrastructure.object_storage import (
    APP_ORPHAN_CANDIDATE_TAG,
    APP_ORPHAN_DETECTED_AT_TAG,
    ObjectPage,
    ObjectTagMutation,
    S3ObjectStorage,
    create_object_storage,
)
from exam_guru_api.storage_reconciliation.models import FindingStatus, ReconciliationRunStatus
from exam_guru_api.storage_reconciliation.repository import (
    ReconciliationLeaseLostError,
    SqlAlchemyStorageReconciliationRepository,
)
from exam_guru_api.storage_reconciliation.service import (
    ReconciliationExecution,
    ReconciliationLease,
    ReconciliationPolicy,
    ReconciliationRunRecord,
    StorageReconciliationService,
)

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"
ACTOR_ID = UUID("00000000-0000-0000-0000-000000002100")


def source_key(character: str) -> str:
    checksum = character * 64
    return f"sources/{checksum[:2]}/{checksum}.pdf"


@pytest.fixture(scope="module")
def reconciliation_runtime() -> Iterator[tuple[str, S3ObjectStorage]]:
    credentials = ("exam_guru", "reconciliation-" + "only")
    with (
        PostgresContainer(
            image=PGVECTOR_IMAGE,
            username=credentials[0],
            password=credentials[1],
            dbname="exam_guru_reconciliation_test",
            driver="asyncpg",
        ) as postgres,
        DockerContainer(MINIO_IMAGE)
        .with_env("MINIO_ROOT_USER", "reconciliation-access")
        .with_env("MINIO_ROOT_PASSWORD", "reconciliation-" + "secret")
        .with_command("server /data --console-address :9001")
        .with_exposed_ports(9000)
        .waiting_for(LogMessageWaitStrategy("API:")) as minio,
    ):
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        assert_database_schema_current(database_url)
        storage = create_object_storage(
            Settings(
                environment="test",
                object_storage_endpoint_url=(
                    f"http://{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"
                ),
                object_storage_access_key=SecretStr("reconciliation-access"),
                object_storage_secret_key=SecretStr("reconciliation-secret"),
                object_storage_bucket="exam-guru-reconciliation",
            )
        )
        storage.ensure_bucket()
        try:
            yield database_url, storage
        finally:
            storage.close()


@pytest.mark.integration
def test_storage_reconciliation_migration_constraints_transitions_and_clean_downgrade(
    reconciliation_runtime: tuple[str, S3ObjectStorage],
) -> None:
    database_url, _ = reconciliation_runtime
    candidate_key = source_key("a")
    run_id = "00000000-0000-0000-0000-000000002111"

    async def inspect_and_probe() -> tuple[
        set[str],
        set[str],
        set[str],
        set[str],
        set[str],
        int,
        str | None,
    ]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                state_columns = set(
                    await connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'storage_reconciliation_state'"
                        )
                    )
                )
                state_constraints = set(
                    await connection.scalars(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'storage_reconciliation_state'::regclass"
                        )
                    )
                )
                run_columns = set(
                    await connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'storage_reconciliation_runs'"
                        )
                    )
                )
                finding_columns = set(
                    await connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'storage_orphan_findings'"
                        )
                    )
                )
                triggers = set(
                    await connection.scalars(
                        text(
                            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                            "tgrelid IN ('storage_reconciliation_state'::regclass, "
                            "'storage_reconciliation_runs'::regclass, "
                            "'storage_orphan_findings'::regclass)"
                        )
                    )
                )
                singleton_count = int(
                    await connection.scalar(
                        text("SELECT count(*) FROM storage_reconciliation_state")
                    )
                    or 0
                )
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO storage_reconciliation_runs ("
                        "id, started_at, completed_at, status, apply_tags, grace_seconds, "
                        "max_objects, scanned_count, referenced_count, candidate_count, "
                        "resolved_count, tagged_count, failure_count, truncated, failure_code"
                        ") VALUES ("
                        ":id, now(), now(), 'completed', false, 86400, 1000, "
                        "1, 0, 1, 0, 0, 0, false, NULL)"
                    ),
                    {"id": run_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO storage_orphan_findings ("
                        "object_key, checksum_sha256, first_seen_at, last_seen_at, "
                        "candidate_since, size_bytes, object_last_modified_at, status, "
                        "tag_status, tag_updated_at, resolved_at, failure_code, "
                        "created_at, updated_at) VALUES ("
                        ":key, :checksum, now() - interval '1 hour', now(), "
                        "now() - interval '1 hour', 9, now() - interval '2 days', "
                        "'candidate', 'not_requested', NULL, NULL, NULL, now(), now())"
                    ),
                    {"key": candidate_key, "checksum": "a" * 64},
                )
                await connection.execute(
                    text(
                        "UPDATE storage_orphan_findings SET status = 'resolved', "
                        "resolved_at = now(), tag_status = 'removed', "
                        "tag_updated_at = candidate_since, updated_at = now() "
                        "WHERE object_key = :key"
                    ),
                    {"key": candidate_key},
                )
                await connection.execute(
                    text(
                        "UPDATE storage_orphan_findings SET status = 'candidate', "
                        "candidate_since = now(), resolved_at = NULL, last_seen_at = now(), "
                        "updated_at = now() WHERE object_key = :key"
                    ),
                    {"key": candidate_key},
                )
            return (
                state_columns,
                state_constraints,
                run_columns,
                finding_columns,
                triggers,
                singleton_count,
                revision,
            )
        finally:
            await engine.dispose()

    (
        state_columns,
        state_constraints,
        run_columns,
        finding_columns,
        triggers,
        singleton_count,
        revision,
    ) = asyncio.run(inspect_and_probe())
    assert state_columns == {
        "singleton_id",
        "last_started_at",
        "last_completed_at",
        "continuation_cursor",
        "lease_token",
        "lease_acquired_at",
        "lease_expires_at",
    }
    assert {
        "ck_storage_reconciliation_state_singleton",
        "ck_storage_reconciliation_state_cursor",
        "ck_storage_reconciliation_state_timestamps",
        "ck_storage_reconciliation_state_lease_shape",
    } <= state_constraints
    assert run_columns == {
        "id",
        "started_at",
        "completed_at",
        "status",
        "apply_tags",
        "grace_seconds",
        "max_objects",
        "scanned_count",
        "referenced_count",
        "candidate_count",
        "resolved_count",
        "tagged_count",
        "failure_count",
        "truncated",
        "failure_code",
    }
    assert finding_columns == {
        "object_key",
        "checksum_sha256",
        "first_seen_at",
        "last_seen_at",
        "candidate_since",
        "size_bytes",
        "object_last_modified_at",
        "status",
        "tag_status",
        "tag_updated_at",
        "resolved_at",
        "failure_code",
        "created_at",
        "updated_at",
    }
    assert triggers == {
        "enforce_storage_reconciliation_state_mutation_trigger",
        "reject_storage_reconciliation_run_mutation_trigger",
        "enforce_storage_orphan_finding_mutation_trigger",
    }
    assert singleton_count == 1
    assert revision == "0021_storage_reconciliation"

    async def execute(statement: str, values: dict[str, object]) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text(statement), values)
        finally:
            await engine.dispose()

    async def rejected(statement: str, values: dict[str, object]) -> None:
        engine = create_async_engine(database_url)
        try:
            with pytest.raises(DBAPIError):
                async with engine.begin() as connection:
                    await connection.execute(text(statement), values)
        finally:
            await engine.dispose()

    asyncio.run(
        rejected(
            "UPDATE storage_reconciliation_runs SET scanned_count = 2 WHERE id = :id",
            {"id": run_id},
        )
    )
    asyncio.run(
        rejected(
            "DELETE FROM storage_reconciliation_runs WHERE id = :id",
            {"id": run_id},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_orphan_findings SET first_seen_at = now() WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        rejected(
            "DELETE FROM storage_orphan_findings WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_orphan_findings SET status = 'resolved', "
            "resolved_at = first_seen_at - interval '1 second', updated_at = now() "
            "WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        execute(
            "UPDATE storage_orphan_findings SET status = 'resolved', resolved_at = now(), "
            "last_seen_at = now(), updated_at = now() WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_orphan_findings SET resolved_at = resolved_at + interval '1 second', "
            "last_seen_at = last_seen_at + interval '2 seconds', "
            "updated_at = updated_at + interval '2 seconds' WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        execute(
            "UPDATE storage_orphan_findings SET status = 'candidate', candidate_since = now(), "
            "resolved_at = NULL, last_seen_at = now(), updated_at = now() "
            "WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_orphan_findings SET tag_status = 'applied', "
            "tag_updated_at = first_seen_at - interval '1 second', updated_at = now() "
            "WHERE object_key = :key",
            {"key": candidate_key},
        )
    )
    asyncio.run(
        rejected(
            "INSERT INTO storage_reconciliation_runs ("
            "id, started_at, completed_at, status, apply_tags, grace_seconds, max_objects, "
            "scanned_count, referenced_count, candidate_count, resolved_count, tagged_count, "
            "failure_count, truncated, failure_code) VALUES ("
            ":id, now(), now(), 'failed', false, 86400, 1000, 0, 0, 0, 0, 0, 0, "
            "false, 'object_storage_list_failed')",
            {"id": "00000000-0000-0000-0000-000000002112"},
        )
    )
    asyncio.run(
        rejected(
            "INSERT INTO storage_orphan_findings ("
            "object_key, checksum_sha256, first_seen_at, last_seen_at, candidate_since, "
            "size_bytes, object_last_modified_at, status, tag_status, created_at, updated_at"
            ") VALUES ('sources/../unsafe.pdf', :checksum, now(), now(), now(), 1, now(), "
            "'candidate', 'not_requested', now(), now())",
            {"checksum": "b" * 64},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_reconciliation_state SET continuation_cursor = :cursor "
            "WHERE singleton_id = 1",
            {"cursor": "x" * 2_049},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_reconciliation_state SET continuation_cursor = :cursor "
            "WHERE singleton_id = 1",
            {"cursor": "unsafe\nopaque-cursor"},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_reconciliation_state SET last_started_at = now(), "
            "last_completed_at = now() - interval '1 second' WHERE singleton_id = 1",
            {},
        )
    )
    asyncio.run(
        rejected(
            "UPDATE storage_reconciliation_state SET lease_token = :token WHERE singleton_id = 1",
            {"token": "00000000-0000-0000-0000-000000002199"},
        )
    )
    asyncio.run(
        rejected(
            "DELETE FROM storage_reconciliation_state WHERE singleton_id = 1",
            {},
        )
    )

    command.downgrade(_config_for_database(database_url), "0020_restore_safe_json")

    async def tables_after_downgrade() -> set[str]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return set(
                    await connection.scalars(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_name LIKE 'storage_%'"
                        )
                    )
                )
        finally:
            await engine.dispose()

    assert asyncio.run(tables_after_downgrade()) == set()
    upgrade_database(database_url)
    assert_database_schema_current(database_url)


@pytest.mark.integration
def test_real_postgres_and_minio_candidate_reference_resolution_and_tag_preservation(
    reconciliation_runtime: tuple[str, S3ObjectStorage],
) -> None:
    database_url, storage = reconciliation_runtime
    referenced_key = source_key("c")
    orphan_key = source_key("d")
    storage.put_immutable(referenced_key, b"referenced", content_type="application/pdf")
    storage.put_immutable(orphan_key, b"orphan", content_type="application/pdf")
    storage._client.put_object_tagging(
        Bucket=storage._bucket,
        Key=orphan_key,
        Tagging={"TagSet": [{"Key": "operator-retention", "Value": "keep"}]},
    )

    async def seed_reference(key: str, character: str, identifier: UUID) -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO source_documents ("
                        "id, checksum_sha256, object_key, original_filename, content_type, "
                        "size_bytes, document_type, extraction_status, created_by, updated_by"
                        ") VALUES ("
                        ":id, :checksum, :key, :filename, 'application/pdf', 10, "
                        "'syllabus', 'uploaded', :actor, :actor)"
                    ),
                    {
                        "id": identifier,
                        "checksum": character * 64,
                        "key": key,
                        "filename": f"{character}.pdf",
                        "actor": ACTOR_ID,
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(seed_reference(referenced_key, "c", UUID(int=2_201)))
    first_now = datetime.now(UTC) + timedelta(hours=2)

    async def reconcile(at: datetime) -> ReconciliationExecution:
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return await StorageReconciliationService(
                    SqlAlchemyStorageReconciliationRepository(session),
                    storage,
                    ReconciliationPolicy(
                        interval_seconds=300,
                        grace_seconds=3_600,
                        max_objects_per_run=100,
                        apply_tags=True,
                    ),
                    now=lambda: at,
                ).reconcile()
        finally:
            await engine.dispose()

    first = asyncio.run(reconcile(first_now))
    assert first.run is not None
    assert first.run.scanned_count == 2
    assert first.run.referenced_count == 1
    assert first.run.candidate_count == 1
    assert first.run.resolved_count == 0

    first_tags = {
        item["Key"]: item["Value"]
        for item in storage._client.get_object_tagging(
            Bucket=storage._bucket,
            Key=orphan_key,
        )["TagSet"]
    }
    assert first_tags["operator-retention"] == "keep"
    assert first_tags[APP_ORPHAN_CANDIDATE_TAG] == "true"
    assert APP_ORPHAN_DETECTED_AT_TAG in first_tags

    asyncio.run(seed_reference(orphan_key, "d", UUID(int=2_202)))
    second = asyncio.run(reconcile(first_now + timedelta(seconds=301)))
    assert second.run is not None
    assert second.run.referenced_count == 2
    assert second.run.candidate_count == 0
    assert second.run.resolved_count == 1

    async def acquire_inside_interval() -> ReconciliationLease | None:
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return await SqlAlchemyStorageReconciliationRepository(session).acquire_lease(
                    now=first_now + timedelta(seconds=302),
                    interval_seconds=300,
                    lease_seconds=301,
                )
        finally:
            await engine.dispose()

    assert asyncio.run(acquire_inside_interval()) is None

    second_tags = {
        item["Key"]: item["Value"]
        for item in storage._client.get_object_tagging(
            Bucket=storage._bucket,
            Key=orphan_key,
        )["TagSet"]
    }
    assert second_tags == {"operator-retention": "keep"}

    async def read_persisted() -> tuple[str, int, int]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                status = await connection.scalar(
                    text("SELECT status FROM storage_orphan_findings WHERE object_key = :key"),
                    {"key": orphan_key},
                )
                run_count = int(
                    await connection.scalar(
                        text("SELECT count(*) FROM storage_reconciliation_runs")
                    )
                    or 0
                )
                candidate_count = int(
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM storage_orphan_findings "
                            "WHERE status = 'candidate'"
                        )
                    )
                    or 0
                )
                return str(status), run_count, candidate_count
        finally:
            await engine.dispose()

    status, run_count, candidate_count = asyncio.run(read_persisted())
    assert status == FindingStatus.RESOLVED.value
    assert run_count >= 2
    assert candidate_count == 0


class BlockingEmptyStorage:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release
        self.calls = 0

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        del max_keys, continuation_token
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=5)
        return ObjectPage(objects=(), is_truncated=False, next_continuation_token=None)

    def merge_reconciliation_tags(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("empty scan cannot tag")


class ProviderMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    def list_source_objects(self, **_kwargs: object) -> ObjectPage:
        self.calls += 1
        raise AssertionError("duplicate actor called object storage")

    def merge_reconciliation_tags(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("duplicate actor called object storage tags")


@pytest.mark.integration
def test_concurrent_reconciliation_actors_share_db_lease_before_any_provider_call(
    reconciliation_runtime: tuple[str, S3ObjectStorage],
) -> None:
    database_url, _ = reconciliation_runtime
    started = threading.Event()
    release = threading.Event()
    first_storage = BlockingEmptyStorage(started, release)
    second_storage = ProviderMustNotRun()
    active_now = datetime.now(UTC) + timedelta(days=2)
    policy = ReconciliationPolicy(
        interval_seconds=300,
        grace_seconds=3_600,
        max_objects_per_run=1,
        apply_tags=False,
    )

    async def exercise() -> tuple[ReconciliationExecution, ReconciliationExecution]:
        first_engine = create_async_engine(database_url)
        second_engine = create_async_engine(database_url)
        first_sessions = async_sessionmaker(first_engine, expire_on_commit=False)
        second_sessions = async_sessionmaker(second_engine, expire_on_commit=False)
        try:
            async with first_sessions() as first_session, second_sessions() as second_session:
                first_task = asyncio.create_task(
                    StorageReconciliationService(
                        SqlAlchemyStorageReconciliationRepository(first_session),
                        first_storage,  # type: ignore[arg-type]
                        policy,
                        now=lambda: active_now,
                    ).reconcile()
                )
                assert await asyncio.to_thread(started.wait, 5)
                second_result = await StorageReconciliationService(
                    SqlAlchemyStorageReconciliationRepository(second_session),
                    second_storage,  # type: ignore[arg-type]
                    policy,
                    now=lambda: active_now,
                ).reconcile()
                release.set()
                first_result = await first_task
                return first_result, second_result
        finally:
            release.set()
            await first_engine.dispose()
            await second_engine.dispose()

    first_result, second_result = asyncio.run(exercise())
    assert first_result.skipped is False
    assert first_result.run is not None
    persisted_run = first_result.run
    assert second_result.skipped is True
    assert first_storage.calls == 1
    assert second_storage.calls == 0

    async def reject_lost_lease() -> None:
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                with pytest.raises(ReconciliationLeaseLostError):
                    await SqlAlchemyStorageReconciliationRepository(session).complete_run(
                        lease_token=UUID(int=2_999),
                        run=persisted_run,
                        findings=(),
                        continuation_cursor=None,
                    )
        finally:
            await engine.dispose()

    asyncio.run(reject_lost_lease())


@pytest.mark.integration
def test_expired_hard_crash_lease_retries_the_same_persisted_cursor(
    reconciliation_runtime: tuple[str, S3ObjectStorage],
) -> None:
    database_url, _ = reconciliation_runtime
    base = datetime.now(UTC) + timedelta(days=4)

    async def acquire(at: datetime) -> ReconciliationLease | None:
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return await SqlAlchemyStorageReconciliationRepository(session).acquire_lease(
                    now=at,
                    interval_seconds=300,
                    lease_seconds=301,
                )
        finally:
            await engine.dispose()

    async def complete(
        lease: ReconciliationLease,
        *,
        started_at: datetime,
        continuation_cursor: str | None,
        truncated: bool,
    ) -> None:
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                await SqlAlchemyStorageReconciliationRepository(session).complete_run(
                    lease_token=lease.token,
                    run=ReconciliationRunRecord(
                        id=lease.token,
                        started_at=started_at,
                        completed_at=started_at + timedelta(seconds=1),
                        status=ReconciliationRunStatus.COMPLETED,
                        apply_tags=False,
                        grace_seconds=3_600,
                        max_objects=1,
                        scanned_count=1 if truncated else 0,
                        referenced_count=0,
                        candidate_count=0,
                        resolved_count=0,
                        tagged_count=0,
                        failure_count=0,
                        truncated=truncated,
                        failure_code=None,
                    ),
                    findings=(),
                    continuation_cursor=continuation_cursor,
                )
        finally:
            await engine.dispose()

    first = asyncio.run(acquire(base))
    assert first is not None
    assert first.continuation_cursor is None
    asyncio.run(
        complete(
            first,
            started_at=base,
            continuation_cursor="opaque-resume-cursor",
            truncated=True,
        )
    )

    crashed = asyncio.run(acquire(base + timedelta(seconds=301)))
    assert crashed is not None
    assert crashed.continuation_cursor == "opaque-resume-cursor"

    retry = asyncio.run(acquire(base + timedelta(seconds=603)))
    assert retry is not None
    assert retry.token != crashed.token
    assert retry.continuation_cursor == "opaque-resume-cursor"
    asyncio.run(
        complete(
            retry,
            started_at=base + timedelta(seconds=603),
            continuation_cursor=None,
            truncated=False,
        )
    )


class RecordingDelegatingStorage:
    def __init__(self, storage: S3ObjectStorage) -> None:
        self.storage = storage
        self.calls: list[tuple[int, str | None]] = []
        self.key_groups: list[tuple[str, ...]] = []

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        self.calls.append((max_keys, continuation_token))
        result = self.storage.list_source_objects(
            max_keys=max_keys,
            continuation_token=continuation_token,
        )
        self.key_groups.append(tuple(item.key for item in result.objects))
        return result

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        raise AssertionError((key, candidate_detected_at))


@pytest.mark.integration
def test_real_postgres_minio_cursor_scans_disjoint_pages_clears_and_restarts(
    reconciliation_runtime: tuple[str, S3ObjectStorage],
) -> None:
    database_url, storage = reconciliation_runtime
    new_keys = tuple(source_key(character) for character in ("4", "5", "6", "7", "8"))
    for index, key in enumerate(new_keys):
        storage.put_immutable(key, f"cursor-{index}".encode(), content_type="application/pdf")

    recording_storage = RecordingDelegatingStorage(storage)
    base = datetime.now(UTC) + timedelta(days=5)
    policy = ReconciliationPolicy(
        interval_seconds=300,
        grace_seconds=3_600,
        max_objects_per_run=3,
        apply_tags=False,
    )

    async def reconcile(at: datetime) -> ReconciliationExecution:
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return await StorageReconciliationService(
                    SqlAlchemyStorageReconciliationRepository(session),
                    recording_storage,
                    policy,
                    now=lambda: at,
                ).reconcile()
        finally:
            await engine.dispose()

    async def read_cursor() -> str | None:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return cast(
                    str | None,
                    await connection.scalar(
                        text(
                            "SELECT continuation_cursor FROM storage_reconciliation_state "
                            "WHERE singleton_id = 1"
                        )
                    ),
                )
        finally:
            await engine.dispose()

    first = asyncio.run(reconcile(base))
    first_cursor = asyncio.run(read_cursor())
    second = asyncio.run(reconcile(base + timedelta(seconds=301)))
    second_cursor = asyncio.run(read_cursor())
    third = asyncio.run(reconcile(base + timedelta(seconds=602)))
    cleared_cursor = asyncio.run(read_cursor())
    fourth = asyncio.run(reconcile(base + timedelta(seconds=903)))

    assert all(result.run is not None for result in (first, second, third, fourth))
    assert [len(group) for group in recording_storage.key_groups] == [3, 3, 1, 3]
    assert not (set(recording_storage.key_groups[0]) & set(recording_storage.key_groups[1]))
    assert not (set(recording_storage.key_groups[0]) & set(recording_storage.key_groups[2]))
    assert not (set(recording_storage.key_groups[1]) & set(recording_storage.key_groups[2]))
    assert set().union(*(set(group) for group in recording_storage.key_groups[:3])) == {
        source_key("c"),
        source_key("d"),
        *new_keys,
    }
    assert recording_storage.key_groups[3] == recording_storage.key_groups[0]
    assert first_cursor is not None
    assert second_cursor is not None
    assert cleared_cursor is None
    assert recording_storage.calls[0][1] is None
    assert recording_storage.calls[1][1] == first_cursor
    assert recording_storage.calls[2][1] == second_cursor
    assert recording_storage.calls[3][1] is None
