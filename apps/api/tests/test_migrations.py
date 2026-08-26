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
def test_0023_multigrade_backfill_clean_downgrade_and_scope_loss_guard() -> None:
    credentials = ("exam_guru", "migration-multigrade-only")
    with PostgresContainer(
        image="pgvector/pgvector:0.8.6-pg18-trixie",
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_multigrade_migration_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        config = _config_for_database(database_url)
        command.upgrade(config, "0022_provider_job_retry_depth")
        exam_id = UUID(int=23_001)
        medium_id = UUID(int=23_002)
        curriculum_id = UUID(int=23_003)
        actor_id = UUID(int=23_004)
        source_id = UUID(int=23_005)

        async def seed_legacy_scope() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO exam_configurations "
                            "(id, code, name, grade, created_by, updated_by) "
                            "VALUES (:id, 'LEGACY-G5', 'Legacy Grade 5', 5, :actor, :actor)"
                        ),
                        {"id": exam_id, "actor": actor_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO media (id, code, name, created_by, updated_by) "
                            "VALUES (:id, 'en', 'English', :actor, :actor)"
                        ),
                        {"id": medium_id, "actor": actor_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO curriculum_versions "
                            "(id, exam_configuration_id, medium_id, code, title, "
                            "created_by, updated_by) VALUES "
                            "(:id, :exam, :medium, 'LEGACY-V1', 'Legacy curriculum', "
                            ":actor, :actor)"
                        ),
                        {
                            "id": curriculum_id,
                            "exam": exam_id,
                            "medium": medium_id,
                            "actor": actor_id,
                        },
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO source_documents "
                            "(id, checksum_sha256, object_key, original_filename, content_type, "
                            "size_bytes, document_type, curriculum_version_id, "
                            "created_by, updated_by) VALUES "
                            "(:id, :checksum, 'sources/legacy.pdf', 'legacy.pdf', "
                            "'application/pdf', 10, 'syllabus', :curriculum, :actor, :actor)"
                        ),
                        {
                            "id": source_id,
                            "checksum": "c" * 64,
                            "curriculum": curriculum_id,
                            "actor": actor_id,
                        },
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed_legacy_scope())
        command.upgrade(config, "head")

        async def inspect_backfill_and_grade_range() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    row = (
                        await connection.execute(
                            text(
                                "SELECT cv.id, s.code FROM curriculum_versions cv "
                                "JOIN subjects s ON s.id = cv.subject_id WHERE cv.id = :id"
                            ),
                            {"id": curriculum_id},
                        )
                    ).one()
                    assert row == (curriculum_id, "LEGACY_UNCLASSIFIED")
                    source_scope = (
                        await connection.execute(
                            text(
                                "SELECT id, unit_id, lesson_id, active_for_ai, "
                                "metadata_scope_version FROM source_documents WHERE id = :id"
                            ),
                            {"id": source_id},
                        )
                    ).one()
                    assert source_scope == (source_id, None, None, True, 0)
                    grade_seven_id = UUID(int=23_007)
                    await connection.execute(
                        text(
                            "INSERT INTO exam_configurations "
                            "(id, code, name, grade, created_by, updated_by) "
                            "VALUES (:id, 'GRADE-7', 'Grade 7', 7, :actor, :actor)"
                        ),
                        {"id": grade_seven_id, "actor": actor_id},
                    )
                    await connection.execute(
                        text("DELETE FROM exam_configurations WHERE id = :id"),
                        {"id": grade_seven_id},
                    )
            finally:
                await engine.dispose()

        asyncio.run(inspect_backfill_and_grade_range())
        command.downgrade(config, "0022_provider_job_retry_depth")

        async def inspect_clean_downgrade() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    preserved = await connection.scalar(
                        text("SELECT id FROM curriculum_versions WHERE id = :id"),
                        {"id": curriculum_id},
                    )
                    preserved_source = await connection.scalar(
                        text("SELECT id FROM source_documents WHERE id = :id"),
                        {"id": source_id},
                    )
                    subjects_regclass = await connection.scalar(
                        text("SELECT to_regclass('public.subjects')")
                    )
                assert preserved == curriculum_id
                assert preserved_source == source_id
                assert subjects_regclass is None
            finally:
                await engine.dispose()

        asyncio.run(inspect_clean_downgrade())
        command.upgrade(config, "head")

        async def seed_new_scope() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO exam_configurations "
                            "(id, code, name, grade, created_by, updated_by) "
                            "VALUES (:id, 'GRADE-7-KEPT', 'Grade 7 kept', 7, :actor, :actor)"
                        ),
                        {"id": UUID(int=23_107), "actor": actor_id},
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed_new_scope())
        with pytest.raises(DBAPIError):
            command.downgrade(config, "0022_provider_job_retry_depth")


@pytest.mark.integration
def test_0023_direct_sql_enforces_learning_scope_and_material_cas() -> None:
    credentials = ("exam_guru", "direct-scope-constraints")
    with PostgresContainer(
        image="pgvector/pgvector:0.8.6-pg18-trixie",
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_direct_scope_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        command.upgrade(_config_for_database(database_url), "head")
        actor_id = UUID(int=23_200)
        subject_id = UUID(int=23_201)
        exam_id = UUID(int=23_202)
        medium_id = UUID(int=23_203)
        curriculum_id = UUID(int=23_204)
        unit_a = UUID(int=23_205)
        unit_b = UUID(int=23_206)
        lesson_id = UUID(int=23_207)
        document_id = UUID(int=23_208)

        async def seed_valid_scope() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO subjects (id, code, name, created_by, updated_by) "
                            "VALUES (:id, 'MATHEMATICS', 'Mathematics', :actor, :actor)"
                        ),
                        {"id": subject_id, "actor": actor_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO exam_configurations "
                            "(id, code, name, grade, created_by, updated_by) "
                            "VALUES (:id, 'SCHOOL-G7', 'School Grade 7', 7, :actor, :actor)"
                        ),
                        {"id": exam_id, "actor": actor_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO media (id, code, name, created_by, updated_by) "
                            "VALUES (:id, 'en', 'English', :actor, :actor)"
                        ),
                        {"id": medium_id, "actor": actor_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO curriculum_versions "
                            "(id, exam_configuration_id, medium_id, subject_id, code, title, "
                            "created_by, updated_by) VALUES "
                            "(:id, :exam, :medium, :subject, 'G7-MATH-V1', "
                            "'Grade 7 Mathematics', :actor, :actor)"
                        ),
                        {
                            "id": curriculum_id,
                            "exam": exam_id,
                            "medium": medium_id,
                            "subject": subject_id,
                            "actor": actor_id,
                        },
                    )
                    for unit_id, code, ordinal in (
                        (unit_a, "UNIT-A", 1),
                        (unit_b, "UNIT-B", 2),
                    ):
                        await connection.execute(
                            text(
                                "INSERT INTO curriculum_units "
                                "(id, curriculum_version_id, code, title, ordinal, "
                                "created_by, updated_by) VALUES "
                                "(:id, :curriculum, :code, :code, :ordinal, :actor, :actor)"
                            ),
                            {
                                "id": unit_id,
                                "curriculum": curriculum_id,
                                "code": code,
                                "ordinal": ordinal,
                                "actor": actor_id,
                            },
                        )
                    await connection.execute(
                        text(
                            "INSERT INTO curriculum_lessons "
                            "(id, curriculum_version_id, unit_id, code, title, ordinal, "
                            "created_by, updated_by) VALUES "
                            "(:id, :curriculum, :unit, 'LESSON-1', 'Lesson 1', 1, "
                            ":actor, :actor)"
                        ),
                        {
                            "id": lesson_id,
                            "curriculum": curriculum_id,
                            "unit": unit_a,
                            "actor": actor_id,
                        },
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO source_documents "
                            "(id, checksum_sha256, object_key, original_filename, content_type, "
                            "size_bytes, document_type, curriculum_version_id, unit_id, lesson_id, "
                            "created_by, updated_by) VALUES "
                            "(:id, :checksum, :object_key, 'lesson.pdf', 'application/pdf', 10, "
                            "'syllabus', :curriculum, :unit, :lesson, :actor, :actor)"
                        ),
                        {
                            "id": document_id,
                            "checksum": "a" * 64,
                            "object_key": "sources/direct-scope.pdf",
                            "curriculum": curriculum_id,
                            "unit": unit_a,
                            "lesson": lesson_id,
                            "actor": actor_id,
                        },
                    )
            finally:
                await engine.dispose()

        asyncio.run(seed_valid_scope())

        async def invalid_lesson_scope() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO source_documents "
                            "(id, checksum_sha256, object_key, original_filename, content_type, "
                            "size_bytes, document_type, curriculum_version_id, unit_id, lesson_id, "
                            "created_by, updated_by) VALUES "
                            "(:id, :checksum, :object_key, 'wrong.pdf', 'application/pdf', 10, "
                            "'syllabus', :curriculum, :unit, :lesson, :actor, :actor)"
                        ),
                        {
                            "id": UUID(int=23_209),
                            "checksum": "b" * 64,
                            "object_key": "sources/wrong-scope.pdf",
                            "curriculum": curriculum_id,
                            "unit": unit_b,
                            "lesson": lesson_id,
                            "actor": actor_id,
                        },
                    )
            finally:
                await engine.dispose()

        with pytest.raises(DBAPIError):
            asyncio.run(invalid_lesson_scope())

        async def stale_use_state_update() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE source_documents SET active_for_ai = FALSE, "
                            "removal_reason = 'Wrong grade', removed_by = :actor, "
                            "removed_at = now() WHERE id = :id"
                        ),
                        {"id": document_id, "actor": actor_id},
                    )
            finally:
                await engine.dispose()

        with pytest.raises(DBAPIError):
            asyncio.run(stale_use_state_update())

        async def valid_cas_update() -> tuple[bool, int]:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE source_documents SET active_for_ai = FALSE, "
                            "removal_reason = 'Wrong grade', removed_by = :actor, "
                            "removed_at = now(), metadata_scope_version = 1 WHERE id = :id"
                        ),
                        {"id": document_id, "actor": actor_id},
                    )
                    row = (
                        await connection.execute(
                            text(
                                "SELECT active_for_ai, metadata_scope_version "
                                "FROM source_documents WHERE id = :id"
                            ),
                            {"id": document_id},
                        )
                    ).one()
                    return row[0], row[1]
            finally:
                await engine.dispose()

        assert asyncio.run(valid_cas_update()) == (False, 1)


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
