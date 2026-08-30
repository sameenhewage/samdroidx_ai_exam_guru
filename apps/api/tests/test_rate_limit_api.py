from collections.abc import Iterator
from typing import Never, cast
from uuid import UUID

from fastapi.testclient import TestClient
from httpx2 import Response

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.auth.rate_limits import (
    RateLimitDecision,
    RateLimiterUnavailableError,
    RateLimitScope,
)
from exam_guru_api.documents.jobs import ExtractionDispatcher
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.main import create_app

ACTOR_ID = UUID("22222222-2222-2222-2222-222222222222")
RESOURCE_ID = UUID("33333333-3333-3333-3333-333333333333")
OTHER_ID = UUID("44444444-4444-4444-4444-444444444444")
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
VALID_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(ACTOR_ID, frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(UUID(int=999), frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class SideEffectResources:
    def __init__(self) -> None:
        self.database_session_calls = 0
        self.closed = False

    def session_factory(self) -> Never:
        self.database_session_calls += 1
        raise AssertionError("rate-limited request reached the database session factory")

    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class FailOnUseStorage:
    def __init__(self) -> None:
        self.calls = 0

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> Never:
        del key, data, content_type
        self.calls += 1
        raise AssertionError("rate-limited request reached object storage")

    def get_bytes(self, key: str) -> Never:
        del key
        self.calls += 1
        raise AssertionError("rate-limited request reached object storage")


class FailOnDispatch:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> Never:
        del document_id, actor_id
        self.calls += 1
        raise AssertionError("rate-limited request reached job dispatch")


class RecordingRateLimiter:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[tuple[UUID, RateLimitScope]] = []

    async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision:
        self.calls.append((principal_id, scope))
        if self.unavailable:
            try:
                raise RuntimeError("private-valkey-password internal-key count=123")
            except RuntimeError as error:
                raise RateLimiterUnavailableError from error
        return RateLimitDecision(allowed=False, retry_after_seconds=7)


def cost_control_client(
    limiter: RecordingRateLimiter,
) -> tuple[TestClient, SideEffectResources, FailOnUseStorage, FailOnDispatch]:
    resources = SideEffectResources()
    storage = FailOnUseStorage()
    dispatcher = FailOnDispatch()
    application = create_app(
        identity_provider=StaticIdentityProvider(),
        resource_factory=lambda _: resources,
        object_storage=cast(ObjectStorage, storage),
        extraction_dispatcher=cast(ExtractionDispatcher, dispatcher),
        rate_limiter=limiter,
    )
    return TestClient(application), resources, storage, dispatcher


def retrieval_request() -> dict[str, object]:
    return {
        "query": "square perimeter",
        "scope": {
            "grade": 5,
            "exam_id": str(RESOURCE_ID),
            "medium_id": str(OTHER_ID),
            "subject_id": str(UUID(int=5)),
            "curriculum_version_id": str(UUID(int=6)),
            "unit_ids": [str(UUID(int=7))],
            "lesson_ids": [str(UUID(int=8))],
            "taxonomy": {"competency_id": str(UUID(int=9))},
        },
        "embedding_config": {
            "provider": "deterministic",
            "model": "fixture",
            "dimension": 3,
            "version": "v1",
            "config_fingerprint": "fixture-v1",
        },
        "limits": {
            "candidate_limit": 5,
            "top_k": 3,
            "max_context_items": 3,
            "max_context_characters": 1_000,
            "max_context_item_characters": 500,
        },
    }


def costly_requests(client: TestClient) -> Iterator[tuple[Response, RateLimitScope]]:
    admin_curriculum = f"/api/v1/admin/curricula/{RESOURCE_ID}"
    yield (
        client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "syllabus"},
            files={"file": ("source.pdf", VALID_PDF, "application/pdf")},
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.SOURCE_UPLOAD,
    )
    yield (
        client.post(
            f"/api/v1/admin/source-documents/{RESOURCE_ID}/extract",
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.EXTRACTION_TRIGGER,
    )
    yield (
        client.post(
            f"{admin_curriculum}/embedding-jobs",
            json={"historical_question_ids": [str(OTHER_ID)], "knowledge_chunk_ids": []},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "same-request"},
        ),
        RateLimitScope.EMBEDDING_JOB_CREATE,
    )
    yield (
        client.post(
            "/api/v1/admin/retrieval/explore",
            json=retrieval_request(),
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.RETRIEVAL_EXPLORE,
    )
    yield (
        client.post(
            f"{admin_curriculum}/generation-runs",
            json={
                "paper_blueprint_id": str(OTHER_ID),
                "slot_id": "slot-1",
                "knowledge_chunk_ids": [str(RESOURCE_ID)],
                "historical_question_ids": [],
            },
            headers={**ADMIN_HEADERS, "Idempotency-Key": "generation-create"},
        ),
        RateLimitScope.GENERATION_CREATE_RETRY,
    )
    yield (
        client.post(
            f"{admin_curriculum}/generation-runs/{OTHER_ID}/retry",
            headers={**ADMIN_HEADERS, "Idempotency-Key": "generation-retry"},
        ),
        RateLimitScope.GENERATION_CREATE_RETRY,
    )
    yield (
        client.post(
            f"{admin_curriculum}/validation-runs",
            json={"generation_run_id": str(OTHER_ID)},
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.VALIDATION_RUN,
    )
    yield (
        client.post(
            "/api/v1/admin/subject-quality/eval-runs",
            json={"case_ids": [str(OTHER_ID)]},
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.VALIDATION_RUN,
    )
    yield (
        client.post(
            f"{admin_curriculum}/papers/{OTHER_ID}/publish",
            json={"expected_version": 1},
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.PAPER_PUBLISH_ARCHIVE,
    )
    yield (
        client.post(
            f"{admin_curriculum}/papers/{OTHER_ID}/archive",
            json={"expected_version": 1, "reason": "Retired."},
            headers=ADMIN_HEADERS,
        ),
        RateLimitScope.PAPER_PUBLISH_ARCHIVE,
    )


def test_all_costly_routes_fail_before_database_job_or_storage_side_effects() -> None:
    limiter = RecordingRateLimiter()
    test_client, resources, storage, dispatcher = cost_control_client(limiter)

    with test_client as client:
        observed = list(costly_requests(client))
        duplicate = client.post(
            f"/api/v1/admin/curricula/{RESOURCE_ID}/embedding-jobs",
            json={"historical_question_ids": [str(OTHER_ID)], "knowledge_chunk_ids": []},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "same-request"},
        )

    expected_scopes = [scope for _, scope in observed]
    for response, expected_scope in observed:
        assert response.status_code == 429
        assert response.json() == {
            "detail": {"code": "rate_limit_exceeded", "scope": expected_scope.value}
        }
        assert response.headers["Retry-After"] == "7"
    assert duplicate.status_code == 429
    assert limiter.calls == [
        *((ACTOR_ID, scope) for scope in expected_scopes),
        (ACTOR_ID, RateLimitScope.EMBEDDING_JOB_CREATE),
    ]
    assert resources.database_session_calls == 0
    assert storage.calls == 0
    assert dispatcher.calls == 0
    assert resources.closed


def test_authentication_and_permission_rejection_happen_before_cost_control() -> None:
    limiter = RecordingRateLimiter()
    test_client, resources, storage, dispatcher = cost_control_client(limiter)
    path = f"/api/v1/admin/source-documents/{RESOURCE_ID}/extract"

    with test_client as client:
        unauthenticated = client.post(path)
        forbidden = client.post(path, headers=REVIEWER_HEADERS)

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert limiter.calls == []
    assert resources.database_session_calls == 0
    assert storage.calls == 0
    assert dispatcher.calls == 0


def test_rate_limiter_failure_is_503_fail_closed_and_sanitized() -> None:
    limiter = RecordingRateLimiter(unavailable=True)
    test_client, resources, storage, dispatcher = cost_control_client(limiter)

    with test_client as client:
        response = client.post(
            f"/api/v1/admin/source-documents/{RESOURCE_ID}/extract",
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "rate_limiter_unavailable"}}
    assert "private-valkey-password" not in response.text
    assert "internal-key" not in response.text
    assert "Retry-After" not in response.headers
    assert resources.database_session_calls == 0
    assert storage.calls == 0
    assert dispatcher.calls == 0


def test_openapi_documents_cost_controls_only_for_allowlisted_mutations() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    costly_operations = (
        ("/api/v1/admin/source-documents", "post"),
        ("/api/v1/admin/source-documents/{document_id}/extract", "post"),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs",
            "post",
        ),
        ("/api/v1/admin/retrieval/explore", "post"),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs",
            "post",
        ),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs/"
            "{generation_run_id}/retry",
            "post",
        ),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/validation-runs",
            "post",
        ),
        ("/api/v1/admin/subject-quality/eval-runs", "post"),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/publish",
            "post",
        ),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/archive",
            "post",
        ),
    )
    for path, method in costly_operations:
        responses = paths[path][method]["responses"]
        assert {"429", "503"} <= responses.keys()
        rate_limited = responses["429"]
        assert rate_limited["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/RateLimitExceededResponse"
        }
        assert rate_limited["headers"]["Retry-After"]["schema"] == {
            "minimum": 1.0,
            "type": "integer",
        }

    read_operations = (
        ("/api/v1/admin/source-documents", "get"),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs",
            "get",
        ),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs",
            "get",
        ),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/validation-runs",
            "get",
        ),
        ("/api/v1/admin/curricula/{curriculum_version_id}/papers", "get"),
        (
            "/api/v1/admin/curricula/{curriculum_version_id}/papers/{paper_id}/archive",
            "get",
        ),
    )
    for path, method in read_operations:
        assert "429" not in paths[path][method]["responses"]

    scopes = schema["components"]["schemas"]["RateLimitScope"]["enum"]
    assert scopes == [scope.value for scope in RateLimitScope]
    unavailable = schema["components"]["schemas"]["RateLimiterUnavailableDetail"]
    assert unavailable["properties"]["code"]["const"] == "rate_limiter_unavailable"
