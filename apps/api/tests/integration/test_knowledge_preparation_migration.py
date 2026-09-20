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
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.workspace_fixtures import ADMIN, database_session

pytestmark = pytest.mark.integration


def test_preparation_migration_never_backfills_and_preserves_old_units_across_empty_reapply() -> (
    None
):
    async def seed(url: str) -> UUID:
        async with database_session(url) as session:
            document_id, trusted_id, _curriculum = await verified_source(session)
            await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            return document_id

    async def history(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'sources',(SELECT jsonb_agg(to_jsonb(d) ORDER BY id) FROM source_documents d),
                    'trusted',(SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                        FROM trusted_page_knowledge t),
                    'pages',(SELECT jsonb_agg(to_jsonb(p) ORDER BY page_number)
                        FROM source_understanding_pages p),
                    'units',(SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM knowledge_units u),
                    'projections',(SELECT jsonb_agg(to_jsonb(p) ORDER BY id)
                        FROM knowledge_projections p),
                    'audits',(SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM admin_audit_events a))
            """)
            )

    async def inspect(url: str) -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM material_knowledge_requests")
                    )
                    == 0
                )
                assert (
                    await connection.scalar(text("SELECT count(*) FROM knowledge_preparation_jobs"))
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

    async def enroll(url: str, document_id: UUID) -> None:
        from exam_guru_api.documents.understanding_service import PageUnderstandingService

        from exam_guru_api.knowledge.preparation_requests import MaterialKnowledgeRequestRecorder

        async with database_session(url) as session:
            await PageUnderstandingService(
                session, preparation_recorder=MaterialKnowledgeRequestRecorder(session)
            ).exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=2,
                expected_version=0,
                confirm_exclusion=True,
                reason="Unneeded synthetic sibling page",
            )

    async def prepare(url: str) -> None:
        from exam_guru_api.knowledge.preparation_jobs import (
            discover_knowledge_preparation,
            run_knowledge_preparation_job,
        )

        async with database_session(url) as session:
            identifiers = await discover_knowledge_preparation(session)
            assert len(identifiers) == 1
            result = await run_knowledge_preparation_job(session, identifiers[0])
            assert result.status == "succeeded"

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="preparation_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            configuration = _config_for_database(url)
            command.upgrade(configuration, "0051_programme_eval_replay")
            document_id = asyncio.run(seed(url))
            before = asyncio.run(history(url))
            command.upgrade(configuration, "head")
            asyncio.run(inspect(url))
            assert asyncio.run(history(url)) == before
            command.downgrade(configuration, "0051_programme_eval_replay")
            assert asyncio.run(history(url)) == before
            command.upgrade(configuration, "head")
            asyncio.run(inspect(url))
            assert asyncio.run(history(url)) == before
            asyncio.run(enroll(url, document_id))
            retained = asyncio.run(history(url))
            with pytest.raises(
                DBAPIError, match="cannot discard material knowledge preparation history"
            ):
                command.downgrade(configuration, "0051_programme_eval_replay")
            assert asyncio.run(history(url)) == retained
            asyncio.run(prepare(url))
            retained = asyncio.run(history(url))
            with pytest.raises(
                DBAPIError, match="cannot discard material knowledge preparation history"
            ):
                command.downgrade(configuration, "0051_programme_eval_replay")
            assert asyncio.run(history(url)) == retained
            assert_database_schema_current(url)
