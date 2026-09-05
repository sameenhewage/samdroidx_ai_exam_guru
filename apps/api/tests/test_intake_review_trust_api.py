from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session, get_object_storage
from exam_guru_api.api.routes.documents import router
from exam_guru_api.auth.api import get_current_principal
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.documents.extraction_service import (
    DocumentExtractionService,
    ExtractionPersistenceResult,
    ExtractionTrustBlockedError,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage


def test_font_risk_trust_error_returns_http_409_without_accessing_real_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = UUID(int=930_001)
    actor_id = UUID(int=930_002)
    calls: list[tuple[UUID, UUID]] = []

    async def blocked_trust(
        _service: DocumentExtractionService,
        requested_document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        calls.append((requested_document_id, actor_id))
        raise ExtractionTrustBlockedError("font_risk")

    async def synthetic_principal() -> Principal:
        return Principal(subject_id=actor_id, roles=frozenset({AdminRole.ADMIN}))

    async def inert_session() -> AsyncSession:
        # Any unexpected persistence access fails: this object has no database methods.
        return cast(AsyncSession, object())

    async def inert_storage() -> ObjectStorage:
        return cast(ObjectStorage, object())

    monkeypatch.setattr(DocumentExtractionService, "trust_document", blocked_trust)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin")
    app.dependency_overrides[get_current_principal] = synthetic_principal
    app.dependency_overrides[get_database_session] = inert_session
    app.dependency_overrides[get_object_storage] = inert_storage

    with TestClient(app) as client:
        response = client.post(f"/api/v1/admin/source-documents/{document_id}/trust")

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "extraction_trust_blocked", "reason_code": "font_risk"}
    }
    assert calls == [(document_id, actor_id)]
