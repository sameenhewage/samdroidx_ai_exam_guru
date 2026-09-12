import asyncio
from uuid import UUID, uuid4

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
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_knowledge_units_postgres import verified_source

pytestmark = pytest.mark.integration


def test_knowledge_unit_migration_preserves_verified_history_and_refuses_derived_data_loss() -> (
    None
):
    async def seed(url: str) -> tuple[UUID, UUID, UUID]:
        async with database_session(url) as session:
            return await verified_source(session)

    async def source_snapshot(url: str, document_id: UUID) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'source',(SELECT to_jsonb(d) FROM source_documents d WHERE id=:id),
                    'pages',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.page_number)
                        FROM source_understanding_pages p WHERE document_id=:id),
                    'runs',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_runs r WHERE document_id=:id),
                    'candidates',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
                        FROM source_understanding_candidates c WHERE document_id=:id),
                    'regions',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_regions r WHERE document_id=:id),
                    'reports',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_reports r WHERE document_id=:id),
                    'decisions',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_decisions r WHERE document_id=:id),
                    'trusted',(SELECT jsonb_agg(to_jsonb(k) ORDER BY k.id)
                        FROM trusted_page_knowledge k WHERE document_id=:id),
                    'audit',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id)
                        FROM admin_audit_events a WHERE resource_id=:id))
            """),
                {"id": document_id},
            )

    async def derived_snapshot(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'units',(SELECT jsonb_agg(to_jsonb(u) ORDER BY u.id) FROM knowledge_units u),
                    'links',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.unit_id,r.ordinal)
                        FROM knowledge_unit_regions r),
                    'projections',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id)
                        FROM knowledge_projections p))
            """)
            )

    async def metadata_difference(url: str) -> list[object]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: compare_metadata(MigrationContext.configure(sync), get_metadata())
                )
        finally:
            await engine.dispose()

    async def prepare(url: str, document_id: UUID, trusted_id: UUID) -> None:
        async with database_session(url) as session:
            await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="knowledge_unit_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            configuration = _config_for_database(url)
            command.upgrade(configuration, "0045_understanding_review")
            document_id, trusted_id, _curriculum_id = asyncio.run(seed(url))
            before = asyncio.run(source_snapshot(url, document_id))
            command.upgrade(configuration, "head")
            assert asyncio.run(source_snapshot(url, document_id)) == before
            assert asyncio.run(metadata_difference(url)) == []
            command.downgrade(configuration, "0045_understanding_review")
            assert asyncio.run(source_snapshot(url, document_id)) == before
            command.upgrade(configuration, "head")
            assert asyncio.run(source_snapshot(url, document_id)) == before
            asyncio.run(prepare(url, document_id, trusted_id))
            history = asyncio.run(derived_snapshot(url))
            source_history = asyncio.run(source_snapshot(url, document_id))
            with pytest.raises(
                DBAPIError, match="cannot discard verified knowledge derivation history"
            ):
                command.downgrade(configuration, "0045_understanding_review")
            assert asyncio.run(derived_snapshot(url)) == history
            assert asyncio.run(source_snapshot(url, document_id)) == source_history
            assert_database_schema_current(url)
