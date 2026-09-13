import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.router import api_router
from exam_guru_api.auth.domain import Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.rate_limits import NoOpRateLimiter
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import SourceDocumentService
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.knowledge.unit_models import KnowledgeProjectionModel, KnowledgeUnitModel
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    ADMIN_HEADERS,
    REVIEWER_HEADERS,
    StaticIdentityProvider,
    add_curriculum,
    add_source,
    admit_curriculum,
    database_session,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.test_understanding_review_postgres import source_candidate

pytestmark = pytest.mark.integration


class SummaryIdentityProvider(StaticIdentityProvider):
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "no-permissions":
            return Principal(UUID(int=87001), frozenset())
        return await super().authenticate(access_token)


@pytest.fixture
def materials_client(workspace_database_url: str) -> Iterator[TestClient]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(workspace_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def database() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_database_session] = database
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.identity_provider = SummaryIdentityProvider()
    app.state.rate_limiter = NoOpRateLimiter()
    app.include_router(api_router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client


async def request_rows(session: AsyncSession, document_id: UUID) -> list[Any]:
    assert (
        await session.scalar(text("SELECT to_regclass('material_knowledge_requests')")) is not None
    )
    return list(
        (
            await session.execute(
                text("SELECT * FROM material_knowledge_requests WHERE document_id=:id"),
                {"id": document_id},
            )
        ).mappings()
    )


@pytest.mark.parametrize("metadata_first", [False, True])
def test_normal_verify_atomically_enrolls_without_derivation_in_either_metadata_order(
    materials_client: TestClient,
    workspace_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_first: bool,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID, object]:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, _artifacts = await source_candidate(
                session, tmp_path
            )
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            return document_id, candidate_id, curriculum_id, storage

    document_id, candidate_id, curriculum_id, storage = asyncio.run(seed())

    async def confirm_metadata() -> None:
        async with database_session(workspace_database_url) as session:
            await SourceDocumentService(
                session, Mock(spec=ObjectStorage), max_upload_bytes=1024
            ).correct_scope(
                document_id,
                curriculum_version_id=curriculum_id,
                unit_id=None,
                lesson_id=None,
                expected_version=0,
                actor_id=ADMIN.subject_id,
                confirm_intake_metadata=True,
            )

    if metadata_first:
        asyncio.run(confirm_metadata())
    state = cast(FastAPI, materials_client.app).state
    state.object_storage = storage
    state.settings = Settings(environment="test", storage_root=str(tmp_path))
    prepare = AsyncMock(side_effect=AssertionError("HTTP must not derive knowledge"))
    monkeypatch.setattr(KnowledgeUnitService, "prepare_page", prepare)
    path = f"/api/v1/admin/materials/{document_id}/pages/1/understanding"
    body = {
        "candidate_id": str(candidate_id),
        "expected_version": 1,
        "compared_with_original": True,
        "reviewed_region_keys": ["fixture"],
        "accepted_claim_keys": [],
        "resolved_uncertainty_keys": ["fixture_only"],
        "reason": "Compared the original synthetic page for Materials",
    }
    assert (
        materials_client.post(path + "/verify", headers=REVIEWER_HEADERS, json=body).status_code
        == 403
    )
    verified = materials_client.post(path + "/verify", headers=ADMIN_HEADERS, json=body)
    assert verified.status_code == 200
    assert not prepare.mock_calls

    async def inspect_enrollment() -> None:
        async with database_session(workspace_database_url) as session:
            rows = await request_rows(session, document_id)
            assert len(rows) == 1
            row = rows[0]
            source = await session.get(SourceDocumentModel, document_id)
            assert source is not None
            assert row["source_sha256"] == source.checksum_sha256
            assert row["requested_by"] == ADMIN.subject_id
            event = await session.get(AdminAuditEventModel, row["source_audit_event_id"])
            assert event is not None
            assert event.action == "page_understanding.verified"
            assert event.payload["trusted_knowledge_id"] == verified.json()["id"]
            own = await session.get(AdminAuditEventModel, row["audit_event_id"])
            assert own is not None
            assert own.action == "material_knowledge.requested"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM knowledge_preparation_jobs WHERE document_id=:id"),
                    {"id": document_id},
                )
                == 0
            )

    asyncio.run(inspect_enrollment())
    if not metadata_first:
        before = materials_client.get(
            f"/api/v1/admin/materials/{document_id}/knowledge-preparation", headers=REVIEWER_HEADERS
        )
        assert before.status_code == 200
        assert before.json()["status"] == "waiting"
        assert before.json()["source_ready"] is True
        assert before.json()["scope_ready"] is False
        asyncio.run(confirm_metadata())
    after = materials_client.get(
        f"/api/v1/admin/materials/{document_id}/knowledge-preparation", headers=REVIEWER_HEADERS
    )
    assert after.status_code == 200
    assert after.json()["requested"] is True
    assert after.json()["source_ready"] is True
    assert after.json()["scope_ready"] is True
    assert after.json()["pending_pages"] == 1
    assert after.json()["prepared_pages"] == 0


@pytest.mark.parametrize("replay_historical_exclusion", [False, True])
def test_normal_last_sibling_exclusion_and_idempotent_replay_enroll_once(
    materials_client: TestClient, workspace_database_url: str, replay_historical_exclusion: bool
) -> None:
    async def seed() -> UUID:
        async with database_session(workspace_database_url) as session:
            document_id, _trusted_id, _curriculum = await verified_source(session, resolved=False)
            if replay_historical_exclusion:
                await PageUnderstandingService(session).exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=2,
                    expected_version=0,
                    confirm_exclusion=True,
                    reason="Exclude the synthetic sibling",
                )
            return document_id

    document_id = asyncio.run(seed())
    path = f"/api/v1/admin/materials/{document_id}/pages/2/understanding/exclude"
    body = {
        "expected_version": 0,
        "confirm_exclusion": True,
        "reason": "Exclude the synthetic sibling",
    }
    for _ in range(2):
        response = materials_client.post(path, headers=ADMIN_HEADERS, json=body)
        assert response.status_code == 200
        assert response.json()["state"] == "excluded"

    async def inspect_enrollment() -> None:
        async with database_session(workspace_database_url) as session:
            rows = await request_rows(session, document_id)
            assert len(rows) == 1
            event = await session.get(AdminAuditEventModel, rows[0]["source_audit_event_id"])
            assert event is not None
            assert event.action == "page_understanding.excluded"
            assert event.payload["page_number"] == 2
            assert (
                await session.scalar(
                    select(func.source_understanding_document_is_resolved(document_id))
                )
                is True
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == document_id,
                        AdminAuditEventModel.action == "material_knowledge.requested",
                    )
                )
                == 1
            )

    asyncio.run(inspect_enrollment())


def test_summary_is_private_authorized_exact_read_only_and_never_enrolls_legacy_sources(
    materials_client: TestClient, workspace_database_url: str
) -> None:
    async def seed() -> tuple[UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum = await verified_source(session)
            await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            legacy_id = await add_source(session, legacy_trusted=True, total=1, extracted_count=1)
            return document_id, legacy_id

    document_id, legacy_id = asyncio.run(seed())
    path = f"/api/v1/admin/materials/{document_id}/knowledge-preparation"
    for _ in range(2):
        response = materials_client.get(path, headers=REVIEWER_HEADERS)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert response.json() == {
            "document_id": str(document_id),
            "requested": False,
            "source_ready": True,
            "scope_ready": True,
            "status": "not_requested",
            "verified_pages": 1,
            "prepared_pages": 1,
            "unit_count": 2,
            "projection_count": 2,
            "pending_pages": 0,
            "failed_pages": 0,
        }
    legacy = materials_client.get(
        f"/api/v1/admin/materials/{legacy_id}/knowledge-preparation", headers=ADMIN_HEADERS
    )
    assert legacy.status_code == 200
    assert legacy.json()["verified_pages"] == 0
    assert legacy.json()["requested"] is False
    assert legacy.json()["unit_count"] == 0
    for headers, identifier, status in [
        ({}, document_id, 401),
        ({"Authorization": "Bearer no-permissions"}, document_id, 403),
        (ADMIN_HEADERS, uuid4(), 404),
        (ADMIN_HEADERS, "invalid-document", 422),
    ]:
        error = materials_client.get(
            f"/api/v1/admin/materials/{identifier}/knowledge-preparation", headers=headers
        )
        assert error.status_code == status
        assert error.headers["Cache-Control"] == "private, no-store"

    async def unchanged() -> None:
        async with database_session(workspace_database_url) as session:
            assert await request_rows(session, document_id) == []
            assert await request_rows(session, legacy_id) == []
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeProjectionModel)
                    .join(
                        KnowledgeUnitModel,
                        KnowledgeUnitModel.id == KnowledgeProjectionModel.unit_id,
                    )
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 2
            )

    asyncio.run(unchanged())


def test_summary_openapi_contract_has_only_the_agreed_teacher_fields(
    materials_client: TestClient,
) -> None:
    schema = materials_client.get("/openapi.json").json()
    summary = schema["components"]["schemas"]["MaterialKnowledgePreparationResponse"]
    assert set(summary["properties"]) == {
        "document_id",
        "requested",
        "source_ready",
        "scope_ready",
        "status",
        "verified_pages",
        "prepared_pages",
        "unit_count",
        "projection_count",
        "pending_pages",
        "failed_pages",
    }
    assert summary["properties"]["status"]["enum"] == [
        "not_requested",
        "waiting",
        "preparing",
        "prepared",
        "needs_attention",
        "removed",
    ]
