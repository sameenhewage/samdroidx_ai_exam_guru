import asyncio
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embedding_job_service import EmbeddingJobService
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.knowledge.unit_models import KnowledgeUnitModel
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry
from tests.integration.test_knowledge_unit_review_postgres import reviewable_unit
from tests.integration.test_material_knowledge_preparation_api import (
    materials_client as materials_client,
)
from tests.integration.workspace_fixtures import (
    ADMIN_HEADERS,
    REVIEWER_HEADERS,
    database_session,
)
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


async def material_unit(session: AsyncSession) -> tuple[UUID, UUID, UUID, UUID]:
    unit_id, projection_id, competency_id = await reviewable_unit(session)
    row = await session.get(KnowledgeUnitModel, unit_id)
    assert row is not None
    return row.document_id, unit_id, projection_id, competency_id


def review_body(competency_id: UUID, **changes: object) -> dict[str, object]:
    return {
        "expected_version": 0,
        "state": "reviewed",
        "confirmed_mapping": True,
        "competency_id": str(competency_id),
        "reason": "Reviewed this unit's curriculum mapping in Materials",
        **changes,
    }


def disabled_configuration(client: TestClient) -> None:
    state = cast(FastAPI, client.app).state
    state.settings = Settings(environment="local")
    state.embedding_provider_registry = EmbeddingProviderRegistry({})


def test_material_review_api_is_atomic_explicit_and_available_without_a_provider(
    materials_client: TestClient,
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await material_unit(session)

    document_id, unit_id, _projection_id, competency_id = asyncio.run(seed())
    disabled_configuration(materials_client)
    create = AsyncMock(side_effect=AssertionError("HTTP cannot create embedding jobs"))
    monkeypatch.setattr(EmbeddingJobService, "create", create)
    path = f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}"
    initial = materials_client.get(path, headers=REVIEWER_HEADERS)
    assert initial.status_code == 200
    assert initial.json()["indexing"] == {
        "intent_id": None,
        "version": None,
        "status": "not_requested",
        "ready": False,
        "retry_allowed": False,
    }
    body = review_body(competency_id)
    denied = materials_client.post(path + "/curriculum-review", headers=REVIEWER_HEADERS, json=body)
    assert denied.status_code == 403
    saved = materials_client.post(path + "/curriculum-review", headers=ADMIN_HEADERS, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["workspace"]["review"]["version"] == 1
    assert saved.json()["workspace"]["eligible"] is True
    assert saved.json()["indexing"]["status"] == "waiting_configuration"
    assert saved.json()["indexing"]["ready"] is False
    assert saved.json()["indexing"]["retry_allowed"] is False
    assert saved.headers["Cache-Control"] == "private, no-store"
    assert saved.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    replay = materials_client.post(path + "/curriculum-review", headers=ADMIN_HEADERS, json=body)
    assert replay.status_code == 200
    assert replay.json() == saved.json()
    assert not create.mock_calls

    async def inspect() -> None:
        async with database_session(workspace_database_url) as session:
            rows = list(
                (
                    await session.execute(
                        text("SELECT * FROM material_knowledge_index_intents WHERE unit_id=:id"),
                        {"id": unit_id},
                    )
                ).mappings()
            )
            assert len(rows) == 1
            intent = rows[0]
            assert intent["review_id"] == UUID(saved.json()["workspace"]["review"]["id"])
            assert intent["attempt_number"] == 0
            assert intent["dispatch_key"] is None
            assert intent["config_snapshot"] is None
            assert intent["embedding_job_id"] is None
            assert await session.scalar(select(func.count()).select_from(EmbeddingJobModel)) == 0
            audit = await session.get(AdminAuditEventModel, intent["audit_event_id"])
            assert audit is not None
            assert audit.action == "material_knowledge_index.requested"

    asyncio.run(inspect())


def test_material_review_intent_migration_exists_without_backfill(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(text("SELECT to_regclass('material_knowledge_index_intents')"))
                is not None
            )

    asyncio.run(check())


def test_material_review_intent_failure_rolls_back_the_review_and_both_audits(
    materials_client: TestClient,
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService

    async def seed() -> tuple[UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await material_unit(session)

    document_id, unit_id, _projection_id, competency_id = asyncio.run(seed())
    disabled_configuration(materials_client)
    monkeypatch.setattr(
        MaterialKnowledgeReviewService,
        "_record_intent",
        AsyncMock(side_effect=RuntimeError("private intent insertion failure")),
    )
    result = materials_client.post(
        f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}/curriculum-review",
        headers=ADMIN_HEADERS,
        json=review_body(competency_id),
    )
    assert result.status_code == 503
    assert "private intent" not in result.text
    assert result.headers["cache-control"] == "private, no-store"

    async def inspect() -> None:
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.unit_id == unit_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == unit_id,
                        AdminAuditEventModel.action.like("verified_knowledge.mapping_%"),
                    )
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM material_knowledge_index_intents WHERE unit_id=:id"),
                    {"id": unit_id},
                )
                == 0
            )

    asyncio.run(inspect())


def test_material_knowledge_list_is_private_bounded_and_read_only(
    materials_client: TestClient, workspace_database_url: str
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await material_unit(session)

    document_id, unit_id, _projection_id, _competency = asyncio.run(seed())
    disabled_configuration(materials_client)
    path = f"/api/v1/admin/materials/{document_id}/knowledge-units"
    first = materials_client.get(path + "?limit=1&offset=0", headers=REVIEWER_HEADERS)
    second = materials_client.get(path + "?limit=1&offset=1", headers=REVIEWER_HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 2
    assert first.json()["source_current"] is True
    assert first.json()["items"][0]["unit_id"] != second.json()["items"][0]["unit_id"]
    assert first.json()["items"][0]["sequence"] < second.json()["items"][0]["sequence"]
    assert set(first.json()["items"][0]) == {
        "unit_id",
        "page_number",
        "sequence",
        "source_curriculum_unit_id",
        "source_lesson_id",
        "source_unit_title",
        "source_lesson_title",
        "review",
        "has_projection",
        "indexing",
    }
    assert "observed" not in first.text
    assert materials_client.get(path + "?limit=26", headers=ADMIN_HEADERS).status_code == 422
    assert materials_client.get(path + "?offset=-1", headers=ADMIN_HEADERS).status_code == 422
    assert materials_client.get(path + "?offset=20", headers=ADMIN_HEADERS).json()["items"] == []
    for headers, identifier, status in [
        ({}, document_id, 401),
        ({"Authorization": "Bearer no-permissions"}, document_id, 403),
        (ADMIN_HEADERS, uuid4(), 404),
        (ADMIN_HEADERS, "invalid", 422),
    ]:
        result = materials_client.get(
            f"/api/v1/admin/materials/{identifier}/knowledge-units", headers=headers
        )
        assert result.status_code == status
        assert result.headers["cache-control"] == "private, no-store"
    foreign = materials_client.get(
        f"/api/v1/admin/materials/{uuid4()}/knowledge-units/{unit_id}", headers=ADMIN_HEADERS
    )
    assert foreign.status_code == 404

    async def inspect() -> None:
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM material_knowledge_index_intents "
                        "WHERE document_id=:id"
                    ),
                    {"id": document_id},
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM material_knowledge_requests WHERE document_id=:id"),
                    {"id": document_id},
                )
                == 0
            )

    asyncio.run(inspect())


def test_material_review_openapi_exact_contract(materials_client: TestClient) -> None:
    schema = materials_client.get("/openapi.json").json()
    models = schema["components"]["schemas"]
    assert set(models["MaterialKnowledgeIndexingStatus"]["properties"]) == {
        "intent_id",
        "version",
        "status",
        "ready",
        "retry_allowed",
    }
    assert models["MaterialKnowledgeIndexingStatus"]["properties"]["status"]["enum"] == [
        "not_requested",
        "waiting_configuration",
        "pending",
        "queued",
        "ready",
        "needs_attention",
        "superseded",
        "not_searchable",
        "configuration_changed",
    ]
    assert set(models["MaterialKnowledgeIndexRetryRequest"]["properties"]) == {
        "expected_version",
        "reason",
        "confirmed_retry",
    }
    assert set(models["MaterialKnowledgeUnitWorkspace"]["properties"]) == {"workspace", "indexing"}
    assert set(models["MaterialKnowledgeUnitsResponse"]["properties"]) == {
        "document_id",
        "curriculum_version_id",
        "source_current",
        "total",
        "limit",
        "offset",
        "items",
    }


def test_material_indexing_retry_api_preserves_review_and_only_binds_one_explicit_attempt(
    materials_client: TestClient,
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge.embedding_job_service import EmbeddingWorkerService
    from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
    from exam_guru_api.knowledge.material_indexing import promote_material_index_intent
    from tests.integration.test_material_knowledge_indexing_postgres import (
        indexing_runtime,
        reviewed_intent,
    )

    settings, providers = indexing_runtime()

    async def seed() -> tuple[UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            dispatcher = DeterministicEmbeddingDispatcher()
            await promote_material_index_intent(
                session,
                identifier,
                settings=settings,
                providers=providers,
                dispatcher=dispatcher,
            )
            assert await EmbeddingWorkerService(session, providers, None).process(
                dispatcher.dispatched[0]
            )
            return document_id, unit_id

    document_id, unit_id = asyncio.run(seed())
    state = cast(FastAPI, materials_client.app).state
    state.settings, state.embedding_provider_registry = settings, providers
    path = f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}"
    before = materials_client.get(path, headers=ADMIN_HEADERS)
    assert before.status_code == 200
    assert before.json()["indexing"]["retry_allowed"] is True
    create = AsyncMock(side_effect=AssertionError("Retry HTTP must not create or dispatch jobs"))
    monkeypatch.setattr(EmbeddingJobService, "create", create)
    body = {
        "expected_version": before.json()["indexing"]["version"],
        "reason": "Explicitly retry the known unexecuted-provider failure",
        "confirmed_retry": True,
    }
    denied = materials_client.post(path + "/indexing-retry", headers=REVIEWER_HEADERS, json=body)
    assert denied.status_code == 403
    saved = materials_client.post(path + "/indexing-retry", headers=ADMIN_HEADERS, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "private, no-store"
    assert saved.json()["workspace"]["review"] == before.json()["workspace"]["review"]
    assert saved.json()["indexing"]["version"] == body["expected_version"] + 1
    assert saved.json()["indexing"]["status"] == "pending"
    assert saved.json()["indexing"]["retry_allowed"] is False
    stale = materials_client.post(path + "/indexing-retry", headers=ADMIN_HEADERS, json=body)
    assert stale.status_code == 409
    assert not create.mock_calls
