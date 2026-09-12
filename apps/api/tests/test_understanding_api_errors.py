import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.api.routes.understanding as routes
from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Permission, Principal
from exam_guru_api.documents.page_images import PageImageError
from exam_guru_api.documents.understanding_jobs import UnderstandingJobNotFoundError
from exam_guru_api.documents.understanding_runtime import UnderstandingRuntime
from exam_guru_api.documents.understanding_service import (
    UnderstandingConflictError,
    UnderstandingPageSnapshot,
    UnderstandingSourceError,
)
from tests.test_document_understanding_provider import request as fixture_request

ADMIN = Principal(UUID(int=3001), frozenset({AdminRole.ADMIN}))


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AuthorizationError(ADMIN.subject_id, Permission.SOURCE_WRITE), 403, "permission_denied"),
        (UnderstandingSourceError("private detail"), 404, "source_understanding_not_found"),
        (UnderstandingJobNotFoundError("private detail"), 404, "source_understanding_not_found"),
        (UnderstandingConflictError("private detail"), 409, "source_understanding_conflict"),
        (
            IntegrityError("private SQL", None, RuntimeError("private detail")),
            409,
            "source_understanding_conflict",
        ),
        (ValueError("private source"), 422, "invalid_source_understanding_request"),
        (PageImageError("source_original_unavailable"), 503, "source_original_unavailable"),
    ],
)
def test_api_domain_failures_are_rolled_back_and_sanitized(
    error: Exception, status: int, code: str
) -> None:
    session = AsyncMock(spec=AsyncSession)

    async def operation() -> None:
        raise error

    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes._run(session, operation))
    assert caught.value.status_code == status
    assert cast(object, caught.value.detail) == {"code": code}
    session.rollback.assert_awaited_once()


def test_missing_queue_configuration_is_explicit_and_read_only() -> None:
    app = FastAPI()
    request = Request({"type": "http", "headers": [], "app": app})
    with pytest.raises(HTTPException) as caught:
        routes.get_understanding_dispatcher(request)
    assert caught.value.status_code == 503
    assert cast(object, caught.value.detail) == {"code": "source_understanding_queue_unavailable"}


def test_page_and_job_response_mapping_keeps_current_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = fixture_request()
    document_id = fixture.source.document_id
    page = UnderstandingPageSnapshot(document_id, 1, 0, "unprocessed", None, None, None)
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(
        routes,
        "PageUnderstandingService",
        lambda _: SimpleNamespace(get_page=AsyncMock(return_value=page)),
    )
    monkeypatch.setattr(
        routes,
        "UnderstandingJobService",
        lambda _: SimpleNamespace(latest_for_page=AsyncMock(return_value=None)),
    )
    app = FastAPI()
    app.state.understanding_runtime = object()
    request = Request({"type": "http", "headers": [], "app": app})
    response = asyncio.run(routes.get_page_understanding(document_id, 1, request, ADMIN, session))
    assert response.provider_available
    assert response.document_id == document_id
    snapshot = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        page_number=1,
        status="failed",
        version=1,
        expected_page_version=1,
        attempts=0,
        retry_depth=0,
        candidate_id=None,
        run_id=None,
        failure_code="source_understanding_provider_unconfigured",
        accounting=None,
        profile=fixture.profile,
        budget=fixture.budget,
    )
    monkeypatch.setattr(
        routes,
        "UnderstandingJobService",
        lambda _: SimpleNamespace(get=AsyncMock(return_value=snapshot)),
    )
    job = asyncio.run(routes.get_understanding_job(snapshot.id, ADMIN, session))
    assert job.id == snapshot.id
    assert job.failure_code == snapshot.failure_code


@pytest.mark.parametrize("status", ["queued", "failed"])
def test_only_queued_jobs_are_dispatched(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    fixture = fixture_request()
    session = AsyncMock(spec=AsyncSession)
    snapshot = SimpleNamespace(
        id=uuid4(),
        document_id=fixture.source.document_id,
        page_number=1,
        status=status,
        version=int(status == "failed"),
        expected_page_version=1,
        attempts=0,
        retry_depth=0,
        candidate_id=None,
        run_id=None,
        failure_code="source_understanding_input_failed" if status == "failed" else None,
        accounting=None,
        profile=fixture.profile,
        budget=fixture.budget,
    )
    monkeypatch.setattr(
        routes,
        "UnderstandingJobService",
        lambda _: SimpleNamespace(create=AsyncMock(return_value=snapshot)),
    )
    runtime = UnderstandingRuntime(fixture.profile, fixture.budget, AsyncMock())
    body = routes.UnderstandingJobCreateRequest(
        request_id=uuid4(), expected_version=0, reason="Synthetic replay"
    )
    dispatched: list[UUID] = []

    class Dispatcher:
        def dispatch(self, identifier: UUID) -> str:
            dispatched.append(identifier)
            return "accepted"

    dispatcher = Dispatcher()
    result = asyncio.run(
        routes.create_understanding_job(
            fixture.source.document_id, 1, body, ADMIN, session, runtime, dispatcher
        )
    )
    assert result.status == status
    assert dispatched == ([snapshot.id] if status == "queued" else [])
