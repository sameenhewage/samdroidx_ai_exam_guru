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

    async def read_database_state() -> tuple[
        str | None,
        str | None,
        set[str],
        set[str],
        set[str],
        set[str],
        set[str],
    ]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            migration_revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            metadata_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'historical_questions' "
                        "AND column_name IN ('media_references', 'options', 'answer', "
                        "'marking_guidance', 'marking_data', 'question_archetype', "
                        "'difficulty_label', 'difficulty_confidence', 'difficulty_source')"
                    )
                )
            )
            metadata_constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'historical_questions'::regclass "
                        "AND conname LIKE 'ck_historical_questions_metadata_%'"
                    )
                )
            )
            blueprint_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'paper_blueprints'"
                    )
                )
            )
            blueprint_constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'paper_blueprints'::regclass"
                    )
                )
            )
            blueprint_triggers = set(
                await connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE tgrelid = 'paper_blueprints'::regclass "
                        "AND NOT tgisinternal"
                    )
                )
            )
        await engine.dispose()
        return (
            vector_version,
            migration_revision,
            metadata_columns,
            metadata_constraints,
            blueprint_columns,
            blueprint_constraints,
            blueprint_triggers,
        )

    (
        vector_version,
        migration_revision,
        metadata_columns,
        metadata_constraints,
        blueprint_columns,
        blueprint_constraints,
        blueprint_triggers,
    ) = asyncio.run(read_database_state())

    assert vector_version == "0.8.6"
    assert migration_revision == "0012_paper_blueprints"
    assert blueprint_columns == {
        "id",
        "curriculum_version_id",
        "analytics_run_id",
        "blueprint_id",
        "schema_version",
        "algorithm_version",
        "config_version",
        "seed",
        "total_marks",
        "slot_count",
        "specification_fingerprint",
        "input_fingerprint",
        "result_fingerprint",
        "specification",
        "blueprint",
        "taxonomy_snapshot",
        "created_by",
        "created_at",
    }
    assert {
        "fk_paper_blueprints_curriculum_version",
        "fk_paper_blueprints_analytics_curriculum",
        "uq_paper_blueprints_blueprint_id",
        "uq_paper_blueprints_input_fingerprint",
        "ck_paper_blueprints_specification_shape",
        "ck_paper_blueprints_specification_size",
        "ck_paper_blueprints_blueprint_shape",
        "ck_paper_blueprints_blueprint_size",
        "ck_paper_blueprints_taxonomy_snapshot",
    } <= blueprint_constraints
    assert blueprint_triggers == {"reject_paper_blueprint_mutation_trigger"}
    assert metadata_columns == {
        "media_references",
        "options",
        "answer",
        "marking_guidance",
        "marking_data",
        "question_archetype",
        "difficulty_label",
        "difficulty_confidence",
        "difficulty_source",
    }
    assert {
        "ck_historical_questions_metadata_media_references",
        "ck_historical_questions_metadata_options",
        "ck_historical_questions_metadata_answer",
        "ck_historical_questions_metadata_marking_guidance",
        "ck_historical_questions_metadata_marking_data",
        "ck_historical_questions_metadata_question_archetype",
        "ck_historical_questions_metadata_difficulty_evidence",
        "ck_historical_questions_metadata_difficulty_confidence",
    } <= metadata_constraints


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
