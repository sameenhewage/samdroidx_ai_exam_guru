import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes import material_knowledge as routes
from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Permission, Principal
from exam_guru_api.auth.ports import (
    AuthenticationError,
    AuthenticationFailureCode,
    IdentityProvider,
)
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge import material_review
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import (
    MaterialKnowledgeError,
    MaterialKnowledgeIndexRetryRequest,
    MaterialKnowledgeNotFoundError,
    intent_fingerprint,
)
from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService
from exam_guru_api.knowledge.unit_models import KnowledgeUnitModel
from exam_guru_api.knowledge.unit_review import (
    KnowledgeReviewRequest,
    KnowledgeUnitReview,
    KnowledgeUnitReviewError,
    KnowledgeUnitReviewService,
)
from exam_guru_api.knowledge.unit_service import _unit
from exam_guru_api.knowledge.units import derive_knowledge_units, project_knowledge_unit
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry
from tests.test_document_understanding_verification import approve, candidate
from tests.test_knowledge_units import scope

ADMIN = Principal(UUID(int=88501), frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(UUID(int=88502), frozenset({AdminRole.REVIEWER}))
NO_ROLES = Principal(UUID(int=88503), frozenset())
DOCUMENT_ID = UUID(int=88504)
UNIT_ID = UUID(int=88505)
COMPETENCY_ID = UUID(int=88506)
PRIVATE = "private-source-text-and-provider-payload"
BASE_PATH = f"/api/v1/admin/materials/{DOCUMENT_ID}/knowledge-units"
UNIT_PATH = f"{BASE_PATH}/{UNIT_ID}"
ADMIN_HEADERS = {"Authorization": "Bearer material-boundary-test"}


def retry_request() -> MaterialKnowledgeIndexRetryRequest:
    return MaterialKnowledgeIndexRetryRequest(
        expected_version=0, confirmed_retry=True, reason="Explicitly retry this indexing attempt"
    )


def review_request() -> KnowledgeReviewRequest:
    return KnowledgeReviewRequest(
        expected_version=0,
        state="reviewed",
        confirmed_mapping=True,
        competency_id=COMPETENCY_ID,
        reason="Reviewed the source-bound curriculum mapping",
    )


def assert_private_error(response: Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert PRIVATE not in response.text
    assert PRIVATE not in str(response.headers)


def assert_no_writes(session: AsyncMock) -> None:
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.delete.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


def intent_snapshot(intent: MaterialKnowledgeIndexIntentModel) -> dict[str, object]:
    return deepcopy(
        {column.name: getattr(intent, column.name) for column in intent.__table__.columns}
    )


@pytest.fixture
def session() -> AsyncMock:
    value = AsyncMock(spec=AsyncSession)
    value.no_autoflush = nullcontext()
    return value


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="local", retrieval_embedding_provider=None, _env_file=None)


@pytest.fixture
def providers() -> EmbeddingProviderRegistry:
    return EmbeddingProviderRegistry({})


@pytest.fixture
def identity() -> AsyncMock:
    value = AsyncMock(spec=IdentityProvider)
    value.authenticate.return_value = ADMIN
    return value


@pytest.fixture
def client(
    session: AsyncMock,
    identity: AsyncMock,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
) -> Iterator[TestClient]:
    app = FastAPI()
    app.state.identity_provider = identity
    app.state.settings = settings
    app.state.embedding_provider_registry = providers

    async def database() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_database_session] = database
    app.include_router(routes.router, prefix="/api/v1/admin")
    with TestClient(app) as result:
        yield result


@pytest.fixture
def unit_row() -> KnowledgeUnitModel:
    trusted = approve(candidate())
    unit = derive_knowledge_units(trusted, scope(trusted, grade=7))[1]
    return KnowledgeUnitModel.from_domain(
        unit, actor_id=ADMIN.subject_id, audit_event_id=UUID(int=88507)
    )


@pytest.fixture
def document(unit_row: KnowledgeUnitModel) -> SourceDocumentModel:
    return SourceDocumentModel(
        id=unit_row.document_id,
        curriculum_version_id=unit_row.curriculum_version_id,
        quarantined_for_teacher_use=False,
    )


@pytest.fixture
def review_snapshot(unit_row: KnowledgeUnitModel) -> KnowledgeUnitReview:
    return KnowledgeUnitReview(
        id=UUID(int=88508),
        unit_id=unit_row.id,
        unit_fingerprint=unit_row.fingerprint,
        curriculum_version_id=unit_row.curriculum_version_id,
        version=1,
        actor_id=ADMIN.subject_id,
        **review_request().model_dump(exclude={"expected_version"}),
    )


@pytest.fixture
def intent(
    unit_row: KnowledgeUnitModel, review_snapshot: KnowledgeUnitReview
) -> MaterialKnowledgeIndexIntentModel:
    unit = _unit(unit_row)
    projection = project_knowledge_unit(unit)
    value: dict[str, object] = {
        "schema_version": "material-knowledge-index-input.v1",
        "document_id": str(unit_row.document_id),
        "unit_id": str(unit.id),
        "unit_fingerprint": unit.fingerprint,
        "page_number": unit.source.page_number,
        "source_sha256": unit.source.source_sha256,
        "candidate_id": str(unit.candidate_id),
        "trusted_page_id": str(unit.trusted_page_id),
        "trusted_fingerprint": unit.trusted_fingerprint,
        "scope": unit.scope.model_dump(mode="json"),
        "scope_fingerprint": unit_row.scope_fingerprint,
        "derivation_version": unit.derivation_version,
        "curriculum_version_id": str(unit.scope.curriculum_version_id),
        "review_id": str(review_snapshot.id),
        "review_version": review_snapshot.version,
        "review_fingerprint": review_snapshot.fingerprint,
        "projection_id": str(projection.id),
        "projection_fingerprint": projection.fingerprint,
        "projection_text_sha256": projection.text_sha256,
        "transformation_version": projection.transformation_version,
    }
    now = datetime(2026, 9, 13, tzinfo=UTC)
    return MaterialKnowledgeIndexIntentModel(
        id=UUID(int=88509),
        document_id=unit_row.document_id,
        unit_id=unit_row.id,
        review_id=review_snapshot.id,
        review_version=review_snapshot.version,
        review_fingerprint=review_snapshot.fingerprint,
        curriculum_version_id=unit_row.curriculum_version_id,
        projection_id=projection.id,
        input_snapshot=value,
        input_fingerprint=intent_fingerprint(value),
        requested_by=ADMIN.subject_id,
        status="pending",
        version=0,
        attempt_number=0,
        dispatch_key=None,
        config_snapshot=None,
        config_snapshot_fingerprint=None,
        embedding_job_id=None,
        failure_code=None,
        event="requested",
        reason=review_snapshot.reason,
        confirmed_retry=False,
        updated_by=ADMIN.subject_id,
        previous_audit_event_id=None,
        audit_event_id=UUID(int=88510),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("version", [0, 2_147_483_645])
def test_retry_body_preserves_strict_first_party_values_and_wire_json(version: int) -> None:
    request = MaterialKnowledgeIndexRetryRequest(
        expected_version=version, confirmed_retry=True, reason="නැවත උත්සාහ කරන්න"
    )
    assert routes._retry_body(request) == request
    assert routes._retry_body(request.model_dump(mode="json")) == request


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": True},
        {"expected_version": -1},
        {"expected_version": 2_147_483_646},
        {"confirmed_retry": 1},
        {"reason": PRIVATE + "\x00"},
        {"provider_payload": PRIVATE},
    ],
)
def test_retry_body_revalidates_copied_instances_instead_of_trusting_their_type(
    change: dict[str, object],
) -> None:
    tampered = retry_request().model_copy(update=change)
    with pytest.raises(ValidationError) as caught:
        routes._retry_body(tampered)
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (
            AuthorizationError(ADMIN.subject_id, Permission.SOURCE_READ),
            403,
            "permission_denied",
        ),
        (MaterialKnowledgeNotFoundError(PRIVATE), 404, "material_knowledge_not_found"),
        (
            KnowledgeUnitReviewError("knowledge_review_version_conflict"),
            409,
            "knowledge_review_version_conflict",
        ),
        (
            KnowledgeUnitReviewError("knowledge_review_source_not_current"),
            409,
            "knowledge_review_source_not_current",
        ),
        (
            KnowledgeUnitReviewError("knowledge_review_taxonomy_invalid"),
            409,
            "knowledge_review_taxonomy_invalid",
        ),
        (
            MaterialKnowledgeError("material_indexing_version_conflict"),
            409,
            "material_indexing_version_conflict",
        ),
        (
            MaterialKnowledgeError("material_indexing_source_not_current"),
            409,
            "material_indexing_source_not_current",
        ),
        (
            MaterialKnowledgeError("material_indexing_retry_not_allowed"),
            409,
            "material_indexing_retry_not_allowed",
        ),
        (
            MaterialKnowledgeError("material_indexing_retry_requires_confirmation"),
            409,
            "material_indexing_retry_requires_confirmation",
        ),
        (
            MaterialKnowledgeError("material_indexing_binding_invalid"),
            503,
            "material_knowledge_unavailable",
        ),
        (
            KnowledgeUnitReviewError("stored_knowledge_review_invalid"),
            503,
            "material_knowledge_unavailable",
        ),
        (MaterialKnowledgeError(PRIVATE), 503, "material_knowledge_unavailable"),
        (KnowledgeUnitReviewError(PRIVATE), 503, "material_knowledge_unavailable"),
        (
            MaterialKnowledgeError("knowledge_review_version_conflict: " + PRIVATE),
            503,
            "material_knowledge_unavailable",
        ),
        (
            IntegrityError("INSERT " + PRIVATE, {"source": PRIVATE}, RuntimeError(PRIVATE)),
            409,
            "material_knowledge_conflict",
        ),
        (ValueError(PRIVATE), 503, "material_knowledge_unavailable"),
        (RuntimeError(PRIVATE), 503, "material_knowledge_unavailable"),
    ],
)
def test_http_error_classification_is_allowlisted_private_and_rolls_back(
    client: TestClient,
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    code: str,
) -> None:
    workspace = AsyncMock(side_effect=error)
    monkeypatch.setattr(MaterialKnowledgeReviewService, "get_workspace", workspace)
    response = client.get(UNIT_PATH, headers=ADMIN_HEADERS)
    assert_private_error(response, status, code)
    session.rollback.assert_awaited_once()
    assert_no_writes(session)
    assert session.mock_calls == [call.rollback()]


@pytest.mark.parametrize(
    ("method", "path", "principal", "status", "code"),
    [
        ("GET", BASE_PATH, None, 401, "authentication_required"),
        ("GET", UNIT_PATH, None, 401, "authentication_required"),
        ("POST", UNIT_PATH + "/curriculum-review", None, 401, "authentication_required"),
        ("POST", UNIT_PATH + "/indexing-retry", None, 401, "authentication_required"),
        ("GET", BASE_PATH, NO_ROLES, 403, "permission_denied"),
        ("GET", UNIT_PATH, NO_ROLES, 403, "permission_denied"),
        ("POST", UNIT_PATH + "/curriculum-review", REVIEWER, 403, "permission_denied"),
        ("POST", UNIT_PATH + "/indexing-retry", REVIEWER, 403, "permission_denied"),
    ],
)
def test_http_authorization_fails_before_database_or_service_work(
    client: TestClient,
    session: AsyncMock,
    identity: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    principal: Principal | None,
    status: int,
    code: str,
) -> None:
    factory = Mock(side_effect=AssertionError("Unauthorised requests must not construct services"))
    monkeypatch.setattr(routes, "MaterialKnowledgeReviewService", factory)
    identity.authenticate.return_value = principal
    request = retry_request() if path.endswith("indexing-retry") else review_request()
    response = client.request(
        method,
        path,
        headers={} if principal is None else ADMIN_HEADERS,
        json=request.model_dump(mode="json") if method == "POST" else None,
    )
    assert_private_error(response, status, code)
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
        identity.authenticate.assert_not_awaited()
    factory.assert_not_called()
    assert not session.mock_calls


def test_invalid_bearer_is_private_and_never_reaches_material_data(
    client: TestClient, session: AsyncMock, identity: AsyncMock
) -> None:
    identity.authenticate.side_effect = AuthenticationError(AuthenticationFailureCode.INVALID)
    response = client.get(UNIT_PATH, headers={"Authorization": "Bearer " + PRIVATE})
    assert_private_error(response, 401, "invalid_access_token")
    assert response.headers["www-authenticate"] == "Bearer"
    assert not session.mock_calls


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": True},
        {"expected_version": "0"},
        {"expected_version": 0.0},
        {"expected_version": -1},
        {"expected_version": 2_147_483_646},
        {"confirmed_retry": 1},
        {"confirmed_retry": "true"},
        {"reason": " "},
        {"reason": PRIVATE + "\nprovider response"},
        {"reason": PRIVATE + "\x00"},
        {"reason": "x" * 2001},
        {"config_snapshot": {"provider_payload": PRIVATE}},
    ],
)
def test_retry_http_rejects_malformed_body_without_echoing_it_or_accessing_the_database(
    client: TestClient, session: AsyncMock, change: dict[str, object]
) -> None:
    response = client.post(
        UNIT_PATH + "/indexing-retry",
        headers=ADMIN_HEADERS,
        json={**retry_request().model_dump(mode="json"), "reason": PRIVATE, **change},
    )
    assert_private_error(response, 422, "invalid_material_knowledge_request")
    assert not session.mock_calls


@pytest.mark.parametrize("missing", ["expected_version", "reason", "confirmed_retry"])
def test_retry_http_requires_version_reason_and_confirmation(
    client: TestClient, session: AsyncMock, missing: str
) -> None:
    body = retry_request().model_dump(mode="json")
    body.pop(missing)
    response = client.post(UNIT_PATH + "/indexing-retry", headers=ADMIN_HEADERS, json=body)
    assert_private_error(response, 422, "invalid_material_knowledge_request")
    assert not session.mock_calls


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/materials/" + PRIVATE + "/knowledge-units",
        BASE_PATH + "/" + PRIVATE,
        BASE_PATH + "?limit=0",
        BASE_PATH + "?limit=26",
        BASE_PATH + "?limit=true",
        BASE_PATH + "?offset=-1",
        BASE_PATH + "?offset=2147483648",
    ],
)
def test_http_identity_and_pagination_validation_is_private_and_precedes_database_access(
    client: TestClient, session: AsyncMock, path: str
) -> None:
    response = client.get(path, headers=ADMIN_HEADERS)
    assert_private_error(response, 422, "invalid_material_knowledge_request")
    assert not session.mock_calls


@pytest.mark.parametrize("endpoint", ["curriculum-review", "indexing-retry"])
def test_failed_mutation_does_not_read_back_or_disclose_a_workspace(
    client: TestClient,
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    mutation = AsyncMock(side_effect=MaterialKnowledgeError(PRIVATE))
    readback = AsyncMock(side_effect=AssertionError("A failed mutation has no success readback"))
    monkeypatch.setattr(
        MaterialKnowledgeReviewService,
        "review" if endpoint == "curriculum-review" else "retry",
        mutation,
    )
    monkeypatch.setattr(MaterialKnowledgeReviewService, "get_workspace", readback)
    request = review_request() if endpoint == "curriculum-review" else retry_request()
    response = client.post(
        UNIT_PATH + "/" + endpoint, headers=ADMIN_HEADERS, json=request.model_dump(mode="json")
    )
    assert_private_error(response, 503, "material_knowledge_unavailable")
    assert mutation.await_args is not None
    assert mutation.await_args.kwargs["request"] == request
    assert mutation.await_args.kwargs["document_id"] == DOCUMENT_ID
    assert mutation.await_args.kwargs["unit_id"] == UNIT_ID
    readback.assert_not_awaited()
    session.rollback.assert_awaited_once()
    assert_no_writes(session)


@pytest.mark.parametrize("endpoint", ["", "/curriculum-review", "/indexing-retry"])
@pytest.mark.parametrize("missing", ["document", "unit"])
def test_missing_material_or_unit_is_an_opaque_404_and_cannot_write(
    client: TestClient,
    session: AsyncMock,
    endpoint: str,
    missing: str,
) -> None:
    session.scalar.side_effect = (
        [None]
        if missing == "document"
        else [SourceDocumentModel(id=DOCUMENT_ID, curriculum_version_id=UUID(int=88511)), None]
    )
    if endpoint:
        request = retry_request() if endpoint == "/indexing-retry" else review_request()
        response = client.post(
            UNIT_PATH + endpoint, headers=ADMIN_HEADERS, json=request.model_dump(mode="json")
        )
    else:
        response = client.get(UNIT_PATH, headers=ADMIN_HEADERS)
    assert_private_error(response, 404, "material_knowledge_not_found")
    assert session.rollback.await_count == (2 if endpoint else 1)
    assert session.scalar.await_count == (1 if missing == "document" else 2)
    assert_no_writes(session)


@pytest.mark.parametrize(
    ("limit", "offset"),
    [
        (True, 0),
        (1.0, 0),
        ("1", 0),
        (0, 0),
        (26, 0),
        (1, True),
        (1, 0.0),
        (1, "0"),
        (1, -1),
        (1, 2_147_483_648),
    ],
)
def test_service_pagination_is_strict_before_any_database_or_provider_work(
    session: AsyncMock,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
    limit: object,
    offset: object,
) -> None:
    lookup = Mock(
        side_effect=AssertionError("Invalid pagination cannot inspect provider configuration")
    )
    monkeypatch.setattr(material_review, "material_embedding_config", lookup)
    with pytest.raises(MaterialKnowledgeError, match=r"^invalid_material_knowledge_pagination$"):
        asyncio.run(
            MaterialKnowledgeReviewService(session).list_units(
                principal=REVIEWER,
                document_id=DOCUMENT_ID,
                settings=settings,
                providers=providers,
                limit=cast(int, limit),
                offset=cast(int, offset),
            )
        )
    lookup.assert_not_called()
    assert not session.mock_calls


@pytest.mark.parametrize(
    ("operation", "principal", "permission"),
    [
        ("list", NO_ROLES, Permission.SOURCE_READ),
        ("workspace", NO_ROLES, Permission.SOURCE_READ),
        ("review", NO_ROLES, Permission.SOURCE_READ),
        ("retry", NO_ROLES, Permission.SOURCE_READ),
        ("review", REVIEWER, Permission.KNOWLEDGE_WRITE),
        ("retry", REVIEWER, Permission.KNOWLEDGE_WRITE),
    ],
)
def test_service_authorization_precedes_input_validation_and_database_work(
    session: AsyncMock,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    operation: str,
    principal: Principal,
    permission: Permission,
) -> None:
    service = MaterialKnowledgeReviewService(session)

    async def invoke() -> None:
        if operation == "list":
            await service.list_units(
                principal=principal,
                document_id=DOCUMENT_ID,
                settings=settings,
                providers=providers,
                limit=0,
            )
        elif operation == "workspace":
            await service.get_workspace(
                principal=principal,
                document_id=DOCUMENT_ID,
                unit_id=UNIT_ID,
                settings=settings,
                providers=providers,
            )
        elif operation == "review":
            await service.review(
                principal=principal,
                document_id=DOCUMENT_ID,
                unit_id=UNIT_ID,
                request=review_request().model_copy(update={"expected_version": True}),
            )
        else:
            await service.retry(
                principal=principal,
                document_id=DOCUMENT_ID,
                unit_id=UNIT_ID,
                request=retry_request().model_copy(update={"confirmed_retry": 1}),
                settings=settings,
                providers=providers,
            )

    with pytest.raises(AuthorizationError) as caught:
        asyncio.run(invoke())
    assert caught.value.subject_id == principal.subject_id
    assert caught.value.permission is permission
    assert not session.mock_calls


@pytest.mark.parametrize("operation", ["review", "retry"])
@pytest.mark.parametrize(
    "change", [{"expected_version": True}, {"expected_version": -1}, {"reason": PRIVATE + "\x00"}]
)
def test_service_revalidates_request_instances_before_starting_a_transaction(
    session: AsyncMock,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    operation: str,
    change: dict[str, object],
) -> None:
    service = MaterialKnowledgeReviewService(session)

    async def invoke() -> None:
        if operation == "review":
            await service.review(
                principal=ADMIN,
                document_id=DOCUMENT_ID,
                unit_id=UNIT_ID,
                request=review_request().model_copy(update=change),
            )
        else:
            await service.retry(
                principal=ADMIN,
                document_id=DOCUMENT_ID,
                unit_id=UNIT_ID,
                request=retry_request().model_copy(update=change),
                settings=settings,
                providers=providers,
            )

    with pytest.raises(ValidationError) as caught:
        asyncio.run(invoke())
    assert PRIVATE not in str(caught.value)
    assert not session.mock_calls


@pytest.mark.parametrize("found", [False, True])
def test_document_lookup_excludes_quarantined_sources_and_refreshes_persisted_scope(
    session: AsyncMock, document: SourceDocumentModel, found: bool
) -> None:
    session.scalar.return_value = document if found else None
    service = MaterialKnowledgeReviewService(session)
    if found:
        assert asyncio.run(service._document(document.id)) is document
    else:
        with pytest.raises(MaterialKnowledgeNotFoundError, match=r"^source_document_not_found$"):
            asyncio.run(service._document(document.id))
    assert session.scalar.await_args is not None
    statement = session.scalar.await_args.args[0]
    assert statement.compare(
        select(SourceDocumentModel).where(
            SourceDocumentModel.id == document.id,
            SourceDocumentModel.quarantined_for_teacher_use.is_(False),
        )
    )
    assert statement.get_execution_options()["populate_existing"] is True
    assert [str(item.args[0]) for item in session.execute.await_args_list] == [
        "SET LOCAL statement_timeout = '30s'",
        "SET LOCAL lock_timeout = '5s'",
    ]
    assert_no_writes(session)
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("found", [False, True])
def test_unit_membership_requires_both_document_ownership_and_its_current_curriculum(
    session: AsyncMock, document: SourceDocumentModel, unit_row: KnowledgeUnitModel, found: bool
) -> None:
    session.scalar.side_effect = [document, unit_row if found else None]
    service = MaterialKnowledgeReviewService(session)
    if found:
        assert asyncio.run(service._member(document.id, unit_row.id)) is unit_row
    else:
        with pytest.raises(
            MaterialKnowledgeNotFoundError, match=r"^material_knowledge_unit_not_found$"
        ):
            asyncio.run(service._member(document.id, unit_row.id))
    assert session.scalar.await_args is not None
    statement = session.scalar.await_args.args[0]
    assert statement.compare(
        select(KnowledgeUnitModel).where(
            KnowledgeUnitModel.id == unit_row.id,
            KnowledgeUnitModel.document_id == document.id,
            KnowledgeUnitModel.curriculum_version_id == document.curriculum_version_id,
        )
    )
    assert statement.get_execution_options()["populate_existing"] is True
    assert session.scalar.await_count == 2
    assert_no_writes(session)
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("disappears_after_lock", [False, True])
def test_review_checks_membership_before_and_after_the_source_lock(
    session: AsyncMock,
    document: SourceDocumentModel,
    unit_row: KnowledgeUnitModel,
    monkeypatch: pytest.MonkeyPatch,
    disappears_after_lock: bool,
) -> None:
    session.scalar.side_effect = (
        [document, unit_row, document, None] if disappears_after_lock else [document, None]
    )
    delegated = AsyncMock(side_effect=AssertionError("Missing members cannot receive a review"))
    monkeypatch.setattr(KnowledgeUnitReviewService, "review", delegated)
    with pytest.raises(
        MaterialKnowledgeNotFoundError, match=r"^material_knowledge_unit_not_found$"
    ):
        asyncio.run(
            MaterialKnowledgeReviewService(session).review(
                principal=ADMIN,
                document_id=document.id,
                unit_id=unit_row.id,
                request=review_request(),
            )
        )
    locks = [
        item.args[0]
        for item in session.execute.await_args_list
        if "lock_knowledge_unit_source" in str(item.args[0])
    ]
    assert len(locks) == int(disappears_after_lock)
    if locks:
        assert locks[0].compare(select(func.lock_knowledge_unit_source(document.id)))
    assert session.scalar.await_count == (4 if disappears_after_lock else 2)
    delegated.assert_not_awaited()
    session.rollback.assert_awaited_once()
    assert_no_writes(session)


@pytest.mark.parametrize("current", [False, None])
def test_review_fails_closed_on_noncurrent_source_before_mapping_or_intent_writes(
    session: AsyncMock,
    document: SourceDocumentModel,
    unit_row: KnowledgeUnitModel,
    monkeypatch: pytest.MonkeyPatch,
    current: bool | None,
) -> None:
    session.scalar.side_effect = [document, unit_row, document, unit_row, current]
    delegated = AsyncMock(side_effect=AssertionError("Stale source cannot be reviewed"))
    monkeypatch.setattr(KnowledgeUnitReviewService, "review", delegated)
    with pytest.raises(MaterialKnowledgeError, match=r"^knowledge_review_source_not_current$"):
        asyncio.run(
            MaterialKnowledgeReviewService(session).review(
                principal=ADMIN,
                document_id=document.id,
                unit_id=unit_row.id,
                request=review_request(),
            )
        )
    assert session.scalar.await_args is not None
    assert session.scalar.await_args.args[0].compare(
        select(func.knowledge_unit_is_current(unit_row.id))
    )
    delegated.assert_not_awaited()
    session.rollback.assert_awaited_once()
    assert_no_writes(session)


@pytest.mark.parametrize("mismatch", [None, "review_fingerprint", "unit_id"])
def test_existing_intent_is_reused_only_for_the_identical_review_and_unit_binding(
    session: AsyncMock,
    document: SourceDocumentModel,
    unit_row: KnowledgeUnitModel,
    review_snapshot: KnowledgeUnitReview,
    intent: MaterialKnowledgeIndexIntentModel,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str | None,
) -> None:
    if mismatch == "review_fingerprint":
        intent.review_fingerprint = "f" * 64
    elif mismatch == "unit_id":
        intent.unit_id = UUID(int=88599)
    original = intent_snapshot(intent)
    session.scalar.side_effect = [document, unit_row, document, unit_row, True, intent, intent]
    delegated = AsyncMock(return_value=review_snapshot)
    monkeypatch.setattr(KnowledgeUnitReviewService, "review", delegated)
    service = MaterialKnowledgeReviewService(session)
    operation = service.review(
        principal=ADMIN, document_id=document.id, unit_id=unit_row.id, request=review_request()
    )
    if mismatch is None:
        assert asyncio.run(operation) == review_snapshot
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    else:
        with pytest.raises(MaterialKnowledgeError, match=r"^material_indexing_binding_invalid$"):
            asyncio.run(operation)
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once()
    delegated.assert_awaited_once_with(
        principal=ADMIN,
        unit_id=unit_row.id,
        request=review_request(),
        curriculum_version_id=document.curriculum_version_id,
        commit=False,
    )
    assert intent_snapshot(intent) == original
    assert session.scalar.await_count == 7
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize("invalid_input", [None, [], False, PRIVATE])
def test_invalid_index_input_rolls_back_review_instead_of_creating_unbound_intent(
    session: AsyncMock,
    document: SourceDocumentModel,
    unit_row: KnowledgeUnitModel,
    review_snapshot: KnowledgeUnitReview,
    monkeypatch: pytest.MonkeyPatch,
    invalid_input: object,
) -> None:
    session.scalar.side_effect = [
        document,
        unit_row,
        document,
        unit_row,
        True,
        None,
        None,
        invalid_input,
    ]
    delegated = AsyncMock(return_value=review_snapshot)
    monkeypatch.setattr(KnowledgeUnitReviewService, "review", delegated)
    with pytest.raises(MaterialKnowledgeError, match=r"^material_indexing_binding_invalid$"):
        asyncio.run(
            MaterialKnowledgeReviewService(session).review(
                principal=ADMIN,
                document_id=document.id,
                unit_id=unit_row.id,
                request=review_request(),
            )
        )
    assert delegated.await_args is not None
    assert delegated.await_args.kwargs["commit"] is False
    assert session.scalar.await_args is not None
    assert session.scalar.await_args.args[0].compare(
        select(func.material_knowledge_index_input(unit_row.id, review_snapshot.id))
    )
    session.rollback.assert_awaited_once()
    assert_no_writes(session)


def test_unconfirmed_retry_is_rejected_before_database_or_provider_access(
    session: AsyncMock,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = Mock(
        side_effect=AssertionError("Unconfirmed retry cannot inspect provider configuration")
    )
    monkeypatch.setattr(material_review, "material_embedding_config", lookup)
    with pytest.raises(
        MaterialKnowledgeError, match=r"^material_indexing_retry_requires_confirmation$"
    ):
        asyncio.run(
            MaterialKnowledgeReviewService(session).retry(
                principal=ADMIN,
                document_id=DOCUMENT_ID,
                unit_id=UNIT_ID,
                request=retry_request().model_copy(update={"confirmed_retry": False}),
                settings=settings,
                providers=providers,
            )
        )
    lookup.assert_not_called()
    assert not session.mock_calls


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("missing_intent", "material_indexing_retry_not_allowed"),
        ("locked_version_changed", "material_indexing_version_conflict"),
        ("source_not_current", "material_indexing_source_not_current"),
    ],
)
def test_retry_rechecks_intent_version_and_source_before_job_or_configuration_access(
    session: AsyncMock,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    document: SourceDocumentModel,
    unit_row: KnowledgeUnitModel,
    intent: MaterialKnowledgeIndexIntentModel,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    code: str,
) -> None:
    latest = MaterialKnowledgeIndexIntentModel(**intent_snapshot(intent))
    if failure == "locked_version_changed":
        intent.version = 1
    original = intent_snapshot(intent)
    session.scalar.side_effect = [
        document,
        unit_row,
        None if failure == "missing_intent" else latest,
        False,
    ]
    locked = AsyncMock(return_value=intent)
    job = AsyncMock(side_effect=AssertionError("Rejected retry cannot inspect or create a job"))
    lookup = Mock(
        side_effect=AssertionError("Rejected retry cannot inspect provider configuration")
    )
    transition = AsyncMock(side_effect=AssertionError("Rejected retry cannot mutate an intent"))
    monkeypatch.setattr(material_review, "locked_intent", locked)
    monkeypatch.setattr(material_review, "bound_job", job)
    monkeypatch.setattr(material_review, "material_embedding_config", lookup)
    monkeypatch.setattr(material_review, "index_transition", transition)
    with pytest.raises(MaterialKnowledgeError, match=r"^" + code + "$"):
        asyncio.run(
            MaterialKnowledgeReviewService(session).retry(
                principal=ADMIN,
                document_id=document.id,
                unit_id=unit_row.id,
                request=retry_request(),
                settings=settings,
                providers=providers,
            )
        )
    if failure == "missing_intent":
        locked.assert_not_awaited()
    else:
        locked.assert_awaited_once_with(session, intent.id)
    if failure == "source_not_current":
        assert session.scalar.await_args is not None
        assert session.scalar.await_args.args[0].compare(
            select(func.knowledge_unit_review_is_eligible(intent.review_id))
        )
    assert session.scalar.await_count == (4 if failure == "source_not_current" else 3)
    assert intent_snapshot(intent) == original
    job.assert_not_awaited()
    lookup.assert_not_called()
    transition.assert_not_awaited()
    session.rollback.assert_awaited_once()
    assert_no_writes(session)
