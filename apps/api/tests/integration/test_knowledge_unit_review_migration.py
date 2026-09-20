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
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from tests.integration.test_knowledge_unit_review_postgres import reviewable_unit
from tests.integration.workspace_fixtures import ADMIN, database_session

pytestmark = pytest.mark.integration


def test_unit_review_migration_preserves_derived_data_and_refuses_review_history_loss() -> None:
    async def seed(url: str) -> tuple[UUID, UUID, UUID]:
        async with database_session(url) as session:
            return await reviewable_unit(session)

    async def snapshot(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'units',(SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM knowledge_units u),
                    'projections',(SELECT jsonb_agg(to_jsonb(p) ORDER BY id)
                        FROM knowledge_projections p),
                    'links',(SELECT jsonb_agg(to_jsonb(r) ORDER BY unit_id,ordinal)
                        FROM knowledge_unit_regions r),
                    'trusted',(SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                        FROM trusted_page_knowledge t),
                    'audit',(SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM admin_audit_events a))
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

    async def review(url: str, unit_id: UUID, competency_id: UUID) -> UUID:
        async with database_session(url) as session:
            value = await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Synthetic mapping migration proof",
                ),
            )
            return value.id

    async def reviews(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM knowledge_unit_reviews r")
            )

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="knowledge_review_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            config = _config_for_database(url)
            command.upgrade(config, "0046_knowledge_units")
            unit_id, _projection_id, competency_id = asyncio.run(seed(url))
            before = asyncio.run(snapshot(url))
            command.upgrade(config, "head")
            assert asyncio.run(snapshot(url)) == before
            assert asyncio.run(schema_difference(url)) == []
            command.downgrade(config, "0046_knowledge_units")
            assert asyncio.run(snapshot(url)) == before
            command.upgrade(config, "head")
            assert asyncio.run(snapshot(url)) == before
            asyncio.run(review(url, unit_id, competency_id))
            after = asyncio.run(snapshot(url))
            review_history = asyncio.run(reviews(url))
            with pytest.raises(DBAPIError, match="cannot discard knowledge unit review history"):
                command.downgrade(config, "0046_knowledge_units")
            assert asyncio.run(snapshot(url)) == after
            assert asyncio.run(reviews(url)) == review_history
            assert_database_schema_current(url)
