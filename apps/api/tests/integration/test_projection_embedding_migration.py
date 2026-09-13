import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.infrastructure.database import get_metadata
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.retrieval.embeddings import DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
from tests.integration.test_embedding_jobs_postgres import (
    ADMIN_ID,
    CHUNK_BASIC_ID,
    COMPETENCY_ID,
    CURRICULUM_ID,
    EXAM_ID,
    MEDIUM_ID,
    _registry,
    _seed_chunk,
    _seed_curriculum,
)
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_knowledge_projection_embeddings_postgres import projection_source

pytestmark = pytest.mark.integration


def test_projection_embedding_migration_preserves_legacy_vectors_jobs_and_blocks_history_loss() -> (
    None
):
    async def seed_legacy(url: str) -> None:
        async with database_session(url) as session:
            await _seed_curriculum(
                session,
                curriculum_id=CURRICULUM_ID,
                exam_id=EXAM_ID,
                medium_id=MEDIUM_ID,
                competency_id=COMPETENCY_ID,
                suffix="projection",
            )
            await _seed_chunk(
                session,
                identifier=CHUNK_BASIC_ID,
                offset=901,
                value="Reviewed legacy source evidence.",
            )
            await session.commit()
            config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
            registry = _registry()
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                CURRICULUM_ID,
                historical_question_ids=(),
                knowledge_chunk_ids=(CHUNK_BASIC_ID,),
                idempotency_key="legacy-vector-preservation",
                actor_id=ADMIN_ID,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)

    async def snapshot(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'vectors',(SELECT jsonb_agg(to_jsonb(e)||jsonb_build_object(
                        'knowledge_projection_id',to_jsonb(e)->'knowledge_projection_id')
                        ORDER BY e.id) FROM knowledge_embeddings e),
                    'jobs',(SELECT jsonb_agg(to_jsonb(j)||jsonb_build_object(
                        'knowledge_projection_ids',coalesce(to_jsonb(j)->'knowledge_projection_ids',
                            '[]'::jsonb)) ORDER BY j.id)
                        FROM embedding_jobs j),
                    'chunks',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id) FROM knowledge_chunks c),
                    'audit',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM admin_audit_events a))
            """)
            )

    async def schema_difference(url: str) -> list[object]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: compare_metadata(MigrationContext.configure(sync), get_metadata())
                )
        finally:
            await engine.dispose()

    async def seed_projection(url: str) -> None:
        async with database_session(url) as session:
            _unit_id, projection_id, curriculum_id = await projection_source(session)
            config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
            registry = _registry()
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="projection-vector-preservation",
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="projection_embedding_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            configuration = _config_for_database(url)
            command.upgrade(configuration, "head")
            asyncio.run(seed_legacy(url))
            before = asyncio.run(snapshot(url))
            assert asyncio.run(schema_difference(url)) == []
            command.downgrade(configuration, "0047_knowledge_unit_review")
            assert asyncio.run(snapshot(url)) == before
            command.upgrade(configuration, "head")
            assert asyncio.run(snapshot(url)) == before
            asyncio.run(seed_projection(url))
            after = asyncio.run(snapshot(url))
            with pytest.raises(DBAPIError, match="cannot discard projection embedding history"):
                command.downgrade(configuration, "0047_knowledge_unit_review")
            assert asyncio.run(snapshot(url)) == after
            assert_database_schema_current(url)
