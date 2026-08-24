import asyncio
from collections.abc import Iterator

import pytest
from dramatiq import Worker
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.migrations import assert_database_schema_current, upgrade_database
from exam_guru_api.main import create_app
from exam_guru_api.worker import create_broker

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
VALKEY_IMAGE = "valkey/valkey:9.1.1-alpine3.24"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="integration-only",
        dbname="exam_guru_test",
        driver="asyncpg",
    ) as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="module")
def valkey_url() -> Iterator[str]:
    with RedisContainer(image=VALKEY_IMAGE) as valkey:
        host = valkey.get_container_host_ip()
        port = valkey.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.mark.integration
def test_clean_database_migration_enables_pgvector(database_url: str) -> None:
    upgrade_database(database_url)
    assert_database_schema_current(database_url)

    async def read_database_state() -> tuple[str | None, str | None]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            migration_revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        await engine.dispose()
        return vector_version, migration_revision

    vector_version, migration_revision = asyncio.run(read_database_state())

    assert vector_version == "0.8.6"
    assert migration_revision == "0007_knowledge_foundation"


@pytest.mark.integration
def test_readiness_connects_to_postgresql_and_valkey(
    database_url: str,
    valkey_url: str,
) -> None:
    settings = Settings(
        database_url=SecretStr(database_url),
        valkey_url=SecretStr(valkey_url),
    )

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "valkey": "ok"},
    }


@pytest.mark.integration
def test_worker_starts_with_valkey(valkey_url: str) -> None:
    settings = Settings(valkey_url=SecretStr(valkey_url))
    broker = create_broker(settings)
    broker.declare_queue("default")
    worker = Worker(broker, worker_threads=1, worker_timeout=100)

    try:
        worker.start()
        assert broker.do_qsize("default") == 0
    finally:
        worker.stop(timeout=5_000)
        broker.close()
