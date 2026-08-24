import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from exam_guru_api.api.dependencies import get_retrieval_explorer_service
from exam_guru_api.api.routes.retrieval import explore_retrieval
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.main import create_app
from exam_guru_api.retrieval.domain import RetrievalContractError, RetrievalScope
from exam_guru_api.retrieval.embeddings import EmbeddingProviderUnavailableError
from exam_guru_api.retrieval.explorer import (
    EmbeddingConfigurationNotFoundError,
    RetrievalExploreLimits,
    RetrievalExplorerService,
    RetrievalScopeNotFoundError,
)
from exam_guru_api.retrieval.schemas import RetrievalExploreRequest, RetrievalExploreResponse

EXPLORE_PATH = "/api/v1/admin/retrieval/explore"
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
DENIED_HEADERS = {"Authorization": "Bearer denied-token"}


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(subject_id=UUID(int=1), roles=frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(subject_id=UUID(int=2), roles=frozenset({AdminRole.REVIEWER}))
        if access_token == "denied-token":
            return Principal(subject_id=UUID(int=3), roles=frozenset())
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class StubResources:
    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FailingExplorer:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[tuple[str, RetrievalScope, EmbeddingConfig, RetrievalExploreLimits]] = []

    async def explore(
        self,
        *,
        query: str,
        scope: RetrievalScope,
        embedding_config: EmbeddingConfig,
        limits: RetrievalExploreLimits,
    ) -> object:
        self.calls.append((query, scope, embedding_config, limits))
        raise self.error


class SuccessfulExplorer:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, RetrievalScope, EmbeddingConfig, RetrievalExploreLimits]] = []

    async def explore(
        self,
        *,
        query: str,
        scope: RetrievalScope,
        embedding_config: EmbeddingConfig,
        limits: RetrievalExploreLimits,
    ) -> object:
        self.calls.append((query, scope, embedding_config, limits))
        return self.result


def _payload() -> dict[str, object]:
    return {
        "query": "square perimeter",
        "scope": {
            "grade": 5,
            "exam_id": str(UUID(int=10)),
            "medium_id": str(UUID(int=11)),
            "curriculum_version_id": str(UUID(int=12)),
            "taxonomy": {"competency_id": str(UUID(int=13))},
        },
        "embedding_config": {
            "provider": "deterministic",
            "model": "grade5-fixture",
            "dimension": 3,
            "version": "v1",
            "config_fingerprint": "grade5-fixture-v1-d3",
        },
        "limits": {
            "candidate_limit": 10,
            "top_k": 3,
            "max_context_items": 2,
            "max_context_characters": 500,
            "max_context_item_characters": 300,
        },
    }


@contextmanager
def _client(explorer: FailingExplorer) -> Iterator[TestClient]:
    app = create_app(
        identity_provider=StaticIdentityProvider(),
        resource_factory=lambda _: StubResources(),
    )
    app.dependency_overrides[get_retrieval_explorer_service] = lambda: cast(
        RetrievalExplorerService, explorer
    )
    with TestClient(app) as client:
        yield client


def test_retrieval_route_converts_successful_domain_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain_result = object()
    expected_response = cast(RetrievalExploreResponse, object())
    converted: list[object] = []

    def convert(result: object) -> RetrievalExploreResponse:
        converted.append(result)
        return expected_response

    monkeypatch.setattr(RetrievalExploreResponse, "from_domain", staticmethod(convert))
    explorer = SuccessfulExplorer(domain_result)
    request = RetrievalExploreRequest.model_validate(_payload())

    response = asyncio.run(
        explore_retrieval(
            request,
            Principal(subject_id=UUID(int=1), roles=frozenset({AdminRole.ADMIN})),
            cast(RetrievalExplorerService, explorer),
        )
    )

    assert response is expected_response
    assert converted == [domain_result]
    assert explorer.calls == [
        (
            "square perimeter",
            request.scope.to_domain(),
            request.embedding_config.to_domain(),
            request.limits.to_domain(),
        )
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            EmbeddingConfigurationNotFoundError(),
            404,
            "embedding_configuration_not_found",
        ),
        (RetrievalScopeNotFoundError(), 404, "retrieval_scope_not_found"),
        (RetrievalContractError("unsafe internal detail"), 422, "invalid_retrieval_request"),
        (
            EmbeddingProviderUnavailableError(),
            503,
            "embedding_provider_unavailable",
        ),
    ],
)
def test_retrieval_api_returns_stable_leakage_safe_failures(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    explorer = FailingExplorer(error)
    with _client(explorer) as client:
        response = client.post(EXPLORE_PATH, json=_payload(), headers=REVIEWER_HEADERS)

    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": code}}
    assert "unsafe internal detail" not in response.text
    assert len(explorer.calls) == 1


def test_retrieval_api_enforces_permission_before_exploration() -> None:
    explorer = FailingExplorer(AssertionError("must not run"))
    with _client(explorer) as client:
        unauthenticated = client.post(EXPLORE_PATH, json=_payload())
        forbidden = client.post(EXPLORE_PATH, json=_payload(), headers=DENIED_HEADERS)

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["detail"]["code"] == "authentication_required"
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": {"code": "permission_denied"}}
    assert explorer.calls == []


def test_retrieval_api_rejects_vectors_and_bounds_before_exploration() -> None:
    explorer = FailingExplorer(AssertionError("must not run"))
    with _client(explorer) as client:
        with_vector = client.post(
            EXPLORE_PATH,
            json={**_payload(), "query_vector": [1.0, 0.0, 0.0]},
            headers=ADMIN_HEADERS,
        )
        unbounded = _payload()
        cast(dict[str, object], unbounded["limits"])["candidate_limit"] = 101
        over_limit = client.post(EXPLORE_PATH, json=unbounded, headers=ADMIN_HEADERS)

    assert with_vector.status_code == 422
    assert over_limit.status_code == 422
    assert explorer.calls == []
