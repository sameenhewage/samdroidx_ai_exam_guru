import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.blueprints.service import BlueprintGenerationService
from exam_guru_api.core.config import Settings
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.repository import SqlAlchemyGenerationRepository
from exam_guru_api.generation.run_service import GenerationRunService
from exam_guru_api.generation.runtime import create_generation_runtime
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    configure_database_url_from_environment,
)
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import DeterministicEmbeddingProvider
from exam_guru_api.retrieval.embeddings import (
    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
    EmbeddingProviderRegistry,
)
from tests.integration.test_generation_runs_api import (
    ADMIN_ID,
    ALLOWED_CHUNK_ID,
    ALLOWED_QUESTION_ID,
    seed_context,
    seed_curricula,
)
from tests.test_blueprint_domain import CURRICULUM_VERSION_ID, make_uniform_specification


def test_versioned_migrations_do_not_import_mutable_application_modules() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    coupled = {
        path.name
        for path in versions.glob("*.py")
        if "from exam_guru_api" in path.read_text(encoding="utf-8")
        or "import exam_guru_api" in path.read_text(encoding="utf-8")
    }

    assert coupled == set()


def test_alembic_keeps_configured_url_without_environment_override() -> None:
    config = Config()
    config.set_main_option("sqlalchemy.url", "postgresql+asyncpg://configured/app")

    configure_database_url_from_environment(config, {})

    assert config.get_main_option("sqlalchemy.url") == "postgresql+asyncpg://configured/app"


def test_alembic_uses_database_url_from_environment() -> None:
    config = Config()

    configure_database_url_from_environment(
        config,
        {"EXAM_GURU_DATABASE_URL": "postgresql+asyncpg://service:p%40ss@postgres/app"},
    )

    assert (
        config.get_main_option("sqlalchemy.url")
        == "postgresql+asyncpg://service:p%40ss@postgres/app"
    )


@pytest.mark.integration
def test_0022_retry_depth_backfills_downgrades_cleanly_and_rejects_invalid_legacy_graphs() -> None:
    credentials = ("exam_guru", "migration-retry-depth-only")
    with PostgresContainer(
        image="pgvector/pgvector:0.8.6-pg18-trixie",
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_retry_depth_migration_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        config = _config_for_database(database_url)
        command.upgrade(config, "head")

        async def seed_lineages() -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as session:
                    await seed_curricula(session)
                    await seed_context(session)
                    await session.commit()
                    specification = replace(
                        make_uniform_specification((1,), 1),
                        generation_policy=replace(
                            make_uniform_specification((1,), 1).generation_policy,
                            response_language="en-LK",
                        ),
                    )
                    blueprint = await BlueprintGenerationService(session).create_blueprint(
                        CURRICULUM_VERSION_ID,
                        specification,
                        seed=2_200,
                        analytics_run_id=None,
                        actor_id=ADMIN_ID,
                    )
                    slots = cast(list[dict[str, object]], blueprint.record.blueprint["slots"])
                    runtime = create_generation_runtime(Settings(environment="test"))
                    generation = GenerationRunService(
                        session,
                        runtime,
                        DeterministicGenerationDispatcher("migration-generation"),
                    )
                    generation_ids: list[UUID] = []
                    predecessor: UUID | None = None
                    for depth in range(3):
                        result = (
                            await generation.create(
                                CURRICULUM_VERSION_ID,
                                paper_blueprint_id=blueprint.record.id,
                                slot_id=str(slots[0]["slot_id"]),
                                knowledge_chunk_ids=(ALLOWED_CHUNK_ID,),
                                historical_question_ids=(ALLOWED_QUESTION_ID,),
                                idempotency_key=f"migration-generation-{depth}",
                                actor_id=ADMIN_ID,
                            )
                            if predecessor is None
                            else await generation.retry(
                                CURRICULUM_VERSION_ID,
                                predecessor,
                                idempotency_key=f"migration-generation-{depth}",
                                actor_id=ADMIN_ID,
                            )
                        )
                        current_run_id = result.run.id
                        generation_ids.append(current_run_id)
                        await SqlAlchemyGenerationRepository(session).fail_dispatch(
                            current_run_id,
                            result.job.id,
                            completed_at=datetime.now(UTC),
                            failure_code="migration_fixture_failure",
                        )
                        await session.commit()
                        session.expire_all()
                        predecessor = current_run_id

                    providers = EmbeddingProviderRegistry(
                        {"deterministic": DeterministicEmbeddingProvider()}
                    )
                    embedding_ids: list[UUID] = []
                    for depth in range(3):
                        embedding_result = await EmbeddingJobService(
                            session,
                            providers,
                            DeterministicEmbeddingDispatcher("migration-embedding"),
                            DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
                        ).create(
                            CURRICULUM_VERSION_ID,
                            historical_question_ids=(ALLOWED_QUESTION_ID,),
                            knowledge_chunk_ids=(ALLOWED_CHUNK_ID,),
                            idempotency_key=f"migration-embedding-{depth}",
                            actor_id=ADMIN_ID,
                        )
                        embedding_ids.append(embedding_result.job.id)
                        assert await EmbeddingWorkerService(session, providers, None).process(
                            embedding_result.job.id
                        )
                    return tuple(generation_ids), tuple(embedding_ids)
            finally:
                await engine.dispose()

        generation_ids, embedding_ids = asyncio.run(seed_lineages())
        command.downgrade(config, "0021_storage_reconciliation")

        async def inspect_downgrade() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    columns = set(
                        await connection.scalars(
                            text(
                                "SELECT table_name || '.' || column_name "
                                "FROM information_schema.columns "
                                "WHERE table_name IN ('generation_runs', 'embedding_jobs') "
                                "AND column_name = 'retry_depth'"
                            )
                        )
                    )
                    triggers = set(
                        await connection.scalars(
                            text(
                                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                                "tgname LIKE '%retry_depth%'"
                            )
                        )
                    )
                assert columns == set()
                assert triggers == set()
            finally:
                await engine.dispose()

        asyncio.run(inspect_downgrade())
        command.upgrade(config, "head")

        async def inspect_backfill() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    generation_depths = tuple(
                        await connection.scalars(
                            text(
                                "SELECT retry_depth FROM generation_runs "
                                "WHERE id = ANY(:ids) ORDER BY retry_depth"
                            ).bindparams(ids=list(generation_ids))
                        )
                    )
                    embedding_depths = tuple(
                        await connection.scalars(
                            text(
                                "SELECT retry_depth FROM embedding_jobs "
                                "WHERE id = ANY(:ids) ORDER BY retry_depth"
                            ).bindparams(ids=list(embedding_ids))
                        )
                    )
                assert generation_depths == (0, 1, 2)
                assert embedding_depths == (0, 1, 2)
            finally:
                await engine.dispose()

        asyncio.run(inspect_backfill())
        command.downgrade(config, "0021_storage_reconciliation")

        async def add_over_depth_legacy_rows() -> tuple[UUID, UUID]:
            engine = create_async_engine(database_url)
            child_three = UUID(int=2_299_003)
            child_four = UUID(int=2_299_004)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "ALTER TABLE generation_runs DISABLE TRIGGER "
                            "enforce_generation_run_insert_trigger"
                        )
                    )
                    for identifier, predecessor in (
                        (child_three, generation_ids[-1]),
                        (child_four, child_three),
                    ):
                        await connection.execute(
                            text(
                                "INSERT INTO generation_runs "
                                "SELECT (jsonb_populate_record(NULL::generation_runs, "
                                "to_jsonb(source_row) || jsonb_build_object("
                                "'id', CAST(:identifier AS text), "
                                "'retry_of_run_id', CAST(:predecessor AS text), "
                                "'idempotency_key_hash', CAST(:key_hash AS text)))).* "
                                "FROM generation_runs AS source_row WHERE id = :source_id"
                            ),
                            {
                                "identifier": str(identifier),
                                "predecessor": str(predecessor),
                                "key_hash": f"sha256:{identifier.int:064x}",
                                "source_id": predecessor,
                            },
                        )
                    await connection.execute(
                        text(
                            "ALTER TABLE generation_runs ENABLE TRIGGER "
                            "enforce_generation_run_insert_trigger"
                        )
                    )
                return child_three, child_four
            finally:
                await engine.dispose()

        child_three, child_four = asyncio.run(add_over_depth_legacy_rows())
        with pytest.raises(DBAPIError):
            command.upgrade(config, "head")

        async def replace_over_depth_with_cycle() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "ALTER TABLE generation_runs DISABLE TRIGGER "
                            "reject_generation_run_delete_trigger"
                        )
                    )
                    await connection.execute(
                        text("DELETE FROM generation_runs WHERE id IN (:third, :fourth)"),
                        {"third": child_three, "fourth": child_four},
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE generation_runs ENABLE TRIGGER "
                            "reject_generation_run_delete_trigger"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE generation_runs DISABLE TRIGGER "
                            "enforce_generation_run_update_trigger"
                        )
                    )
                    await connection.execute(
                        text("UPDATE generation_runs SET retry_of_run_id = :last WHERE id = :root"),
                        {"last": generation_ids[-1], "root": generation_ids[0]},
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE generation_runs ENABLE TRIGGER "
                            "enforce_generation_run_update_trigger"
                        )
                    )
            finally:
                await engine.dispose()

        asyncio.run(replace_over_depth_with_cycle())
        with pytest.raises(DBAPIError):
            command.upgrade(config, "head")
