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
from exam_guru_api.infrastructure.migrations import _config_for_database
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_knowledge_unit_review_postgres import reviewable_unit
from tests.integration.test_material_knowledge_indexing_postgres import reviewed_intent

pytestmark = pytest.mark.integration


def test_material_index_migration_preserves_old_reviews_and_guards_downgrade() -> None:
    async def seed(url: str) -> None:
        async with database_session(url) as session:
            unit_id, _projection, competency_id = await reviewable_unit(session)
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Old technical review without Materials intent",
                ),
            )

    async def history(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'reviews',(SELECT jsonb_agg(to_jsonb(r) ORDER BY id)
                        FROM knowledge_unit_reviews r),
                    'units',(SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM knowledge_units u),
                    'audits',(SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM admin_audit_events a))
            """)
            )

    async def inspect(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM material_knowledge_index_intents")
                    )
                    == 0
                )
                assert (
                    await connection.run_sync(
                        lambda sync: compare_metadata(
                            MigrationContext.configure(sync), get_metadata()
                        )
                    )
                    == []
                )
        finally:
            await engine.dispose()

    async def enroll(url: str) -> None:
        async with database_session(url) as session:
            await reviewed_intent(session)

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="material_review_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            configuration = _config_for_database(url)
            command.upgrade(configuration, "0052_knowledge_preparation")
            asyncio.run(seed(url))
            before = asyncio.run(history(url))
            command.upgrade(configuration, "head")
            asyncio.run(inspect(url))
            assert asyncio.run(history(url)) == before
            command.downgrade(configuration, "0052_knowledge_preparation")
            assert asyncio.run(history(url)) == before
            command.upgrade(configuration, "head")
            asyncio.run(inspect(url))
            asyncio.run(enroll(url))
            retained = asyncio.run(history(url))
            with pytest.raises(
                DBAPIError, match="cannot discard material knowledge indexing history"
            ):
                command.downgrade(configuration, "0052_knowledge_preparation")
            assert asyncio.run(history(url)) == retained


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE material_knowledge_index_intents SET input_snapshot='{}'::jsonb WHERE id=:id",
        "UPDATE material_knowledge_index_intents SET requested_by=gen_random_uuid() WHERE id=:id",
        "UPDATE material_knowledge_index_intents SET review_id=gen_random_uuid() WHERE id=:id",
        "UPDATE material_knowledge_index_intents SET projection_id=NULL WHERE id=:id",
        "UPDATE material_knowledge_index_intents "
        "SET status='dispatching',version=version+1 WHERE id=:id",
        "UPDATE material_knowledge_index_intents "
        "SET attempt_number=5,version=version+1 WHERE id=:id",
        "UPDATE material_knowledge_index_intents SET version=version+2 WHERE id=:id",
        "UPDATE material_knowledge_index_intents SET version=version+1 WHERE id=:id",
        "DELETE FROM material_knowledge_index_intents WHERE id=:id",
        "TRUNCATE material_knowledge_index_intents",
    ],
)
def test_material_index_identity_cas_nullable_and_audit_guards_reject_low_level_sql(
    workspace_database_url: str,
    mutation: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            _document, _unit, identifier = await reviewed_intent(session)
            before = await session.scalar(
                text("SELECT to_jsonb(i) FROM material_knowledge_index_intents i WHERE id=:id"),
                {"id": identifier},
            )
            await session.rollback()

            async def mutate() -> None:
                await session.execute(text(mutation), {"id": identifier})
                await session.commit()

            with pytest.raises(DBAPIError):
                await mutate()
            await session.rollback()
            assert (
                await session.scalar(
                    text("SELECT to_jsonb(i) FROM material_knowledge_index_intents i WHERE id=:id"),
                    {"id": identifier},
                )
                == before
            )

    asyncio.run(check())
