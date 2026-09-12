import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import exam_guru_api.documents.understanding_jobs as jobs
from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.understanding import router
from exam_guru_api.auth.rate_limits import NoOpRateLimiter, RateLimitDecision, RateLimitScope
from exam_guru_api.documents.understanding_runtime import UnderstandingRuntime
from tests.integration.test_document_understanding_postgres import page_input
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN_HEADERS,
    REVIEWER_HEADERS,
    StaticIdentityProvider,
    database_session,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_understanding_jobs_postgres import CountingProvider

pytestmark = pytest.mark.integration


class Dispatcher:
    def __init__(self) -> None:
        self.ids: list[UUID] = []

    def dispatch(self, identifier: UUID) -> str:
        self.ids.append(identifier)
        return str(identifier)


@pytest.fixture
def client(workspace_database_url: str) -> Iterator[TestClient]:
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
    app.state.identity_provider = StaticIdentityProvider()
    app.state.rate_limiter = NoOpRateLimiter()
    app.state.understanding_runtime = None
    app.state.understanding_dispatcher = Dispatcher()
    app.include_router(router, prefix="/api/v1/admin")
    with TestClient(app) as connection:
        yield connection


def seed(url: str) -> tuple[UUID, UnderstandingRuntime, CountingProvider]:
    async def create() -> tuple[UUID, UnderstandingRuntime, CountingProvider]:
        async with database_session(url) as session:
            request, result, _metadata = await page_input(session)
            provider = CountingProvider(result)
            return (
                request.source.document_id,
                UnderstandingRuntime(request.profile, request.budget, provider),
                provider,
            )

    return asyncio.run(create())


def test_page_inspection_never_queues_paid_analysis(
    client: TestClient, workspace_database_url: str
) -> None:
    identifier, _runtime, provider = seed(workspace_database_url)
    response = client.get(
        f"/api/v1/admin/materials/{identifier}/pages/1/understanding", headers=REVIEWER_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["state"] == "unprocessed"
    assert response.json()["provider_available"] is False
    assert response.headers["Cache-Control"] == "private, no-store"
    assert cast(FastAPI, client.app).state.understanding_dispatcher.ids == []
    assert provider.calls == 0


def test_analysis_requires_configuration_authority_and_an_explicit_versioned_command(
    client: TestClient, workspace_database_url: str
) -> None:
    identifier, runtime, provider = seed(workspace_database_url)
    url = f"/api/v1/admin/materials/{identifier}/pages/1/understanding/jobs"
    body = {"request_id": str(uuid4()), "expected_version": 0, "reason": "Synthetic API analysis"}
    assert client.post(url, headers=ADMIN_HEADERS, json=body).status_code == 503
    cast(FastAPI, client.app).state.understanding_runtime = runtime
    assert client.post(url, headers=REVIEWER_HEADERS, json=body).status_code == 403
    assert (
        client.post(url, headers=ADMIN_HEADERS, json={**body, "verified": True}).status_code == 422
    )
    created = client.post(url, headers=ADMIN_HEADERS, json=body)
    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    assert created.json()["accounting"] is None
    assert provider.calls == 0
    replay = client.post(url, headers=ADMIN_HEADERS, json=body)
    assert replay.status_code == 202
    assert replay.json()["id"] == created.json()["id"]
    status = client.get(
        f"/api/v1/admin/materials/understanding/jobs/{created.json()['id']}",
        headers=REVIEWER_HEADERS,
    )
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    changed = client.post(url, headers=ADMIN_HEADERS, json={**body, "reason": "Changed body"})
    assert changed.status_code == 409


def test_reloaded_page_retains_its_last_analysis_result_without_a_browser_checkpoint(
    client: TestClient, workspace_database_url: str
) -> None:
    identifier, runtime, provider = seed(workspace_database_url)
    cast(FastAPI, client.app).state.understanding_runtime = runtime
    url = f"/api/v1/admin/materials/{identifier}/pages/1/understanding"
    created = client.post(
        url + "/jobs",
        headers=ADMIN_HEADERS,
        json={"request_id": str(uuid4()), "expected_version": 0, "reason": "Synthetic reload"},
    )
    job_id = UUID(created.json()["id"])

    async def fail() -> None:
        async with database_session(workspace_database_url) as session:
            await jobs._failure(
                session, job_id, None, code="source_understanding_provider_unconfigured"
            )

    asyncio.run(fail())
    reloaded = client.get(url, headers=REVIEWER_HEADERS)
    assert reloaded.status_code == 200
    assert reloaded.json()["active_job_id"] is None
    assert reloaded.json()["latest_job"]["id"] == str(job_id)
    assert reloaded.json()["latest_job"]["status"] == "failed"
    assert (
        reloaded.json()["latest_job"]["failure_code"]
        == "source_understanding_provider_unconfigured"
    )
    assert provider.calls == 0


def test_analysis_cost_limit_is_separate_and_private(
    client: TestClient, workspace_database_url: str
) -> None:
    identifier, runtime, _provider = seed(workspace_database_url)
    scopes: list[RateLimitScope] = []

    class Limiter:
        async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision:
            scopes.append(scope)
            return RateLimitDecision(False, 7)

    cast(FastAPI, client.app).state.understanding_runtime = runtime
    cast(FastAPI, client.app).state.rate_limiter = Limiter()
    response = client.post(
        f"/api/v1/admin/materials/{identifier}/pages/1/understanding/jobs",
        headers=ADMIN_HEADERS,
        json={"request_id": str(uuid4()), "expected_version": 0, "reason": "Synthetic limit"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert scopes == [RateLimitScope.DOCUMENT_UNDERSTANDING]
    assert cast(FastAPI, client.app).state.understanding_dispatcher.ids == []
