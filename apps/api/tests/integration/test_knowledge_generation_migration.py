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

from exam_guru_api.core.config import Settings
from exam_guru_api.generation.runtime import create_generation_runtime
from exam_guru_api.infrastructure.database import get_metadata
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from exam_guru_api.papers.publication_service import PaperPublicationService
from exam_guru_api.papers.review_service import ReviewCandidateService
from exam_guru_api.validation.service import ValidationRunService
from tests.integration.test_knowledge_generation_postgres import create_knowledge_run
from tests.integration.test_projection_retrieval_postgres import indexed_projection
from tests.integration.test_verified_knowledge_lineage_postgres import (
    ACTOR,
    EVIDENCE,
    SyntheticPassValidator,
    succeeded_lineage_run,
    synthetic_validation_pipeline,
)
from tests.integration.workspace_fixtures import database_session

pytestmark = pytest.mark.integration


def test_generation_migration_preserves_old_history_and_refuses_new_history_loss() -> None:
    async def seed_legacy(url: str) -> None:
        async with database_session(url) as session:
            source, generated = await succeeded_lineage_run(session)
            curriculum_id, run_id = source.scope.curriculum_version_id, generated.run.id
            report = await ValidationRunService(
                session, synthetic_validation_pipeline(SyntheticPassValidator())
            ).create(curriculum_id, generation_run_id=run_id, actor_id=ACTOR.subject_id)
            review = ReviewCandidateService(session)
            await review.create(curriculum_id, validation_run_id=report.run.id, principal=ACTOR)
            await review.start_review(curriculum_id, run_id, expected_version=2, principal=ACTOR)
            await review.approve(
                curriculum_id, run_id, expected_version=3, note=EVIDENCE, principal=ACTOR
            )
            publication = PaperPublicationService(session)
            draft = await publication.create_draft(
                curriculum_id,
                paper_blueprint_id=generated.run.paper_blueprint_id,
                title="Synthetic legacy publication preservation",
                candidate_ids=(run_id,),
                idempotency_key=uuid4().hex,
                principal=ACTOR,
            )
            await publication.publish(
                curriculum_id, draft.record.paper.id, expected_version=1, principal=ACTOR
            )

    async def snapshot(url: str) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'runs',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id) FROM generation_runs r),
                    'attempts',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id)
                        FROM generation_attempts a),
                    'validation',(SELECT jsonb_agg(to_jsonb(v) ORDER BY v.id)
                        FROM validation_runs v),
                    'candidates',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
                        FROM question_candidates c),
                    'published',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.paper_id,p.version)
                        FROM published_paper_versions p),
                    'audit',(SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM admin_audit_events e))
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

    async def seed_structured(url: str) -> None:
        async with database_session(url) as session:
            source = await indexed_projection(session, grade=5)
            await create_knowledge_run(
                session, source, create_generation_runtime(Settings(environment="test"))
            )

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="knowledge_generation_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            config = _config_for_database(url)
            command.upgrade(config, "0048_projection_embeddings")
            asyncio.run(seed_legacy(url))
            before = asyncio.run(snapshot(url))
            command.upgrade(config, "head")
            assert asyncio.run(snapshot(url)) == before
            assert asyncio.run(schema_difference(url)) == []
            command.downgrade(config, "0048_projection_embeddings")
            assert asyncio.run(snapshot(url)) == before
            command.upgrade(config, "head")
            assert asyncio.run(snapshot(url)) == before
            asyncio.run(seed_structured(url))
            after = asyncio.run(snapshot(url))
            command.downgrade(config, "0049_knowledge_generation")
            assert asyncio.run(snapshot(url)) == after
            command.upgrade(config, "head")
            assert asyncio.run(snapshot(url)) == after
            with pytest.raises(
                DBAPIError, match="cannot discard structured knowledge generation history"
            ):
                command.downgrade(config, "0048_projection_embeddings")
            assert asyncio.run(snapshot(url)) == after
            assert_database_schema_current(url)
