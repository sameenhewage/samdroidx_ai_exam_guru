import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_understanding_review_postgres import source_candidate

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("change", ["correction", "exclusion"])
def test_review_migration_preserves_prior_knowledge_and_refuses_lineage_loss(
    tmp_path: Path, change: str
) -> None:
    async def seed(url: str) -> UUID:
        async with database_session(url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            await PageUnderstandingService(session).verify_against_original(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                expected_version=1,
                compared_with_original=True,
                reviewed_region_keys=("fixture",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=("fixture_only",),
                reason="Synthetic migration preservation",
                storage=storage,
                artifacts=artifacts,
            )
            return document_id

    async def snapshot(url: str, document_id: UUID) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'source',(SELECT to_jsonb(d) FROM source_documents d WHERE id=:id),
                    'page',(SELECT to_jsonb(p)
                        FROM source_understanding_pages p WHERE document_id=:id),
                    'runs',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_runs r WHERE document_id=:id),
                    'candidates',(SELECT jsonb_agg(to_jsonb(c)-'parent_candidate_id' ORDER BY c.id)
                        FROM source_understanding_candidates c WHERE document_id=:id),
                    'parents',(SELECT jsonb_agg(jsonb_build_object('id',c.id,
                        'parent',to_jsonb(c)->'parent_candidate_id') ORDER BY c.id)
                        FROM source_understanding_candidates c WHERE document_id=:id),
                    'regions',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_regions r WHERE document_id=:id),
                    'reports',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_reports r WHERE document_id=:id),
                    'decisions',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.id)
                        FROM source_understanding_decisions d WHERE document_id=:id),
                    'knowledge',(SELECT jsonb_agg(to_jsonb(k) ORDER BY k.id)
                        FROM trusted_page_knowledge k WHERE document_id=:id),
                    'audit',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id)
                        FROM admin_audit_events a WHERE resource_id=:id))
            """),
                {"id": document_id},
            )

    async def mutate(url: str, document_id: UUID) -> None:
        from exam_guru_api.documents.page_images import PageImageArtifacts
        from tests.test_page_images import FileSourceStore

        async with database_session(url) as session:
            service = PageUnderstandingService(session)
            if change == "exclusion":
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=2,
                    confirm_exclusion=True,
                    reason="Retain this explicit exclusion",
                )
            else:
                page = await service.get_page(
                    principal=ADMIN, document_id=document_id, page_number=1
                )
                assert page.candidate is not None
                await service.correct(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    parent_candidate_id=page.candidate.id,
                    request_id=uuid4(),
                    expected_version=2,
                    content=page.candidate.content,
                    reason="Retain this explicit child revision",
                    storage=FileSourceStore(tmp_path / "synthetic-understanding.pdf"),
                    artifacts=PageImageArtifacts(root=tmp_path / "fidelity-page-images"),
                )

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="understanding_review_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            configuration = _config_for_database(url)
            command.upgrade(configuration, "head")
            identifier = asyncio.run(seed(url))
            before = asyncio.run(snapshot(url, identifier))
            command.downgrade(configuration, "0044_understanding_jobs")
            assert asyncio.run(snapshot(url, identifier)) == before
            command.upgrade(configuration, "head")
            assert asyncio.run(snapshot(url, identifier)) == before
            assert_database_schema_current(url)
            asyncio.run(mutate(url, identifier))
            after = asyncio.run(snapshot(url, identifier))
            with pytest.raises(
                DBAPIError, match="cannot discard document understanding review history"
            ):
                command.downgrade(configuration, "0044_understanding_jobs")
            assert asyncio.run(snapshot(url, identifier)) == after
            assert_database_schema_current(url)
