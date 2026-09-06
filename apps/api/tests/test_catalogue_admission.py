import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes import catalogue_admission as routes
from exam_guru_api.auth.api import get_current_principal
from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Permission, Principal
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    CatalogueAdmissionConflictError,
    CatalogueAdmissionDecision,
    CatalogueAdmissionReview,
    CatalogueApprovalError,
    CatalogueScopeNotFoundError,
    CatalogueScopeSnapshot,
    _review_response,
    catalogue_scope_fingerprint,
    get_catalogue_admission,
    list_catalogue_admission_history,
    list_material_catalogue,
    record_catalogue_admission,
    scope_labels_are_approvable,
)
from exam_guru_api.curriculum.domain import LEGACY_UNCLASSIFIED_SUBJECT_ID

ADMIN = Principal(UUID(int=34), frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(UUID(int=35), frozenset({AdminRole.REVIEWER}))
CURRICULUM_ID = UUID(int=3401)
PATH = f"/api/v1/admin/curriculum-versions/{CURRICULUM_ID}/admission"


def scope() -> CatalogueScopeSnapshot:
    return CatalogueScopeSnapshot(
        curriculum_version_id=CURRICULUM_ID,
        curriculum_code="2026",
        curriculum_title="Mathematics syllabus 2026",
        exam_configuration_id=UUID(int=3402),
        exam_configuration_code="G7",
        exam_configuration_name="School Grade 7",
        grade=7,
        medium_id=UUID(int=3403),
        medium_code="en",
        medium_name="English",
        subject_id=UUID(int=3404),
        subject_code="MATHEMATICS",
        subject_name="Mathematics",
    )


def decision_body() -> dict[str, object]:
    return {
        "state": "approved",
        "expected_version": 0,
        "expected_scope_fingerprint": catalogue_scope_fingerprint(scope()),
        "educational_approval": True,
        "reason": "Reviewed the educational scope against the curriculum source.",
        "source_reference": "Reviewed local curriculum register, Mathematics 2026, pages 1-3",
        "evidence": ["The source confirms Grade 7, English medium and Mathematics."],
    }


def decision() -> CatalogueAdmissionDecision:
    return CatalogueAdmissionDecision(
        id=UUID(int=3405),
        curriculum_version_id=CURRICULUM_ID,
        version=1,
        state="approved",
        scope_fingerprint=catalogue_scope_fingerprint(scope()),
        scope_snapshot=scope(),
        educational_approval=True,
        reason="Reviewed against the curriculum register.",
        source_reference="Local curriculum register, pages 1-3",
        evidence=("Grade, medium, subject and curriculum identity were checked.",),
        actor_id=ADMIN.subject_id,
        decided_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("curriculum_version_id", UUID(int=3410)),
        ("curriculum_code", "2027"),
        ("curriculum_title", "Mathematics syllabus 2027"),
        ("exam_configuration_id", UUID(int=3411)),
        ("exam_configuration_code", "G8"),
        ("exam_configuration_name", "School Grade 8"),
        ("grade", 8),
        ("medium_id", UUID(int=3412)),
        ("medium_code", "si"),
        ("medium_name", "Sinhala"),
        ("subject_id", UUID(int=3413)),
        ("subject_code", "SCIENCE"),
        ("subject_name", "Science"),
    ],
)
def test_scope_fingerprint_binds_every_label_code_and_parent(field: str, value: object) -> None:
    original = scope()
    changed = original.model_copy(update={field: value})
    fingerprint = catalogue_scope_fingerprint(original)

    assert fingerprint.startswith("sha256:")
    assert len(fingerprint) == 71
    assert fingerprint == catalogue_scope_fingerprint(
        CatalogueScopeSnapshot(**original.model_dump())
    )
    assert fingerprint != catalogue_scope_fingerprint(changed)


@pytest.mark.parametrize(
    "label",
    [
        "E2E Mathematics",
        "Mathematics fixture 1724000000",
        "Internal curriculum",
        "Smoke syllabus",
        "Synthetic curriculum",
        "E2E123456",
        "\uff25\uff12\uff25 Mathematics",
        "00000000-0000-4000-8000-000000000123",
        "sha256:" + "a" * 64,
        "Mathematics\nCurriculum",
        " Mathematics",
        "---",
        "_",
        "\u200d",
        "\u00a0Mathematics",
    ],
)
@pytest.mark.parametrize(
    "field",
    ["exam_configuration_name", "medium_name", "subject_name", "curriculum_title"],
)
def test_explicit_fixture_or_internal_labels_cannot_be_approved(field: str, label: str) -> None:
    assert not scope_labels_are_approvable(scope().model_copy(update={field: label}))


@pytest.mark.parametrize("label", ["Mathematics 1724000000", "2026", "ගණිතය", "கணிதம்"])
def test_numbers_and_local_language_do_not_infer_fixture_origin(label: str) -> None:
    assert scope_labels_are_approvable(scope().model_copy(update={"curriculum_title": label}))


def test_exact_migration_sentinel_is_blocked_even_if_renamed() -> None:
    assert not scope_labels_are_approvable(
        scope().model_copy(update={"subject_id": LEGACY_UNCLASSIFIED_SUBJECT_ID})
    )
    assert scope_labels_are_approvable(scope().model_copy(update={"subject_code": "2026"}))


@pytest.mark.parametrize(
    "field", ["curriculum_code", "exam_configuration_code", "medium_code", "subject_code"]
)
def test_hidden_fixture_codes_cannot_be_laundered_with_readable_labels(field: str) -> None:
    assert not scope_labels_are_approvable(scope().model_copy(update={field: "E2E-2026"}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "active"),
        ("expected_version", -1),
        ("expected_version", True),
        ("expected_version", "0"),
        ("expected_scope_fingerprint", "a" * 64),
        ("educational_approval", False),
        ("educational_approval", 1),
        ("educational_approval", "true"),
        ("reason", " "),
        ("reason", "Reason\nwith hidden control"),
        ("reason", "a" * 1025),
        ("source_reference", ""),
        ("source_reference", "a" * 1025),
        ("evidence", []),
        ("evidence", [" "]),
        ("evidence", ["Evidence\x00control"]),
        ("evidence", ["a" * 1025]),
        ("evidence", ["Evidence"] * 17),
        ("actor_id", str(REVIEWER.subject_id)),
        ("candidate_subject_label", "Mathematics"),
    ],
)
def test_decisions_require_bounded_explicit_evidence_and_strict_cas(
    field: str, value: object
) -> None:
    payload = decision_body() | {field: value}
    with pytest.raises(ValidationError):
        AdmissionDecisionRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "state",
        "expected_version",
        "expected_scope_fingerprint",
        "educational_approval",
        "reason",
        "source_reference",
        "evidence",
    ],
)
def test_no_approval_or_evidence_is_defaulted(field: str) -> None:
    payload = decision_body()
    del payload[field]
    with pytest.raises(ValidationError):
        AdmissionDecisionRequest.model_validate(payload)


@pytest.mark.parametrize("state", ["rejected", "quarantined"])
def test_negative_decisions_are_explicit_and_never_educational_approval(state: str) -> None:
    payload = decision_body() | {"state": state}
    with pytest.raises(ValidationError):
        AdmissionDecisionRequest.model_validate(payload)
    request = AdmissionDecisionRequest.model_validate(payload | {"educational_approval": False})
    assert request.state == state


def test_legitimate_labels_alone_are_unreviewed_and_never_admitted() -> None:
    review = _review_response(scope(), active_chain=True, latest=None)
    assert review.version == 0
    assert review.state == "unreviewed"
    assert review.latest_decision is None
    assert not review.admitted


@pytest.mark.parametrize("state", ["rejected", "quarantined"])
def test_latest_negative_decision_hides_otherwise_approved_scope(state: str) -> None:
    latest = decision().model_copy(update={"state": state, "educational_approval": False})
    review = _review_response(scope(), active_chain=True, latest=latest)
    assert not review.admitted
    assert review.state == state


def test_approved_scope_is_hidden_when_inactive_stale_or_unreadable() -> None:
    assert _review_response(scope(), active_chain=True, latest=decision()).admitted
    assert not _review_response(scope(), active_chain=False, latest=decision()).admitted
    stale = _review_response(
        scope().model_copy(update={"subject_name": "Science"}),
        active_chain=True,
        latest=decision(),
    )
    assert stale.stale
    assert not stale.admitted
    invalid = scope().model_copy(update={"subject_name": "E2E Subject"})
    latest = decision().model_copy(
        update={
            "scope_fingerprint": catalogue_scope_fingerprint(invalid),
            "scope_snapshot": invalid,
        }
    )
    assert not _review_response(invalid, active_chain=True, latest=latest).admitted


@pytest.mark.parametrize("principal", [REVIEWER, Principal(UUID(int=36), frozenset())])
def test_nonwriters_cannot_access_persistence_when_bypassing_routes(principal: Principal) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(AuthorizationError) as rejected:
        asyncio.run(
            record_catalogue_admission(
                cast(AsyncSession, session),
                CURRICULUM_ID,
                AdmissionDecisionRequest.model_validate(decision_body()),
                principal=principal,
            )
        )
    assert rejected.value.subject_id == principal.subject_id
    assert rejected.value.permission is Permission.TAXONOMY_WRITE
    assert session.mock_calls == []


def application(principal: Principal | None) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/admin")
    if principal is not None:
        app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_database_session] = lambda: cast(AsyncSession, AsyncMock())
    return app


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_reviewer_cannot_change_admission(method: str) -> None:
    with TestClient(application(REVIEWER)) as client:
        response = client.request(method, PATH, json=decision_body())
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "permission_denied"


def test_catalogue_and_single_review_require_authentication() -> None:
    with TestClient(application(None)) as client:
        assert client.get("/api/v1/admin/material-catalogue").status_code == 401
        assert client.get(PATH).status_code == 401


def test_teacher_catalogue_is_empty_without_explicit_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(routes, "list_material_catalogue", listing)
    with TestClient(application(REVIEWER)) as client:
        response = client.get("/api/v1/admin/material-catalogue")
    assert response.status_code == 200
    assert response.json() == []
    listing.assert_awaited_once()


def test_single_resource_review_never_autoapproves_candidate_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review = _review_response(scope(), active_chain=True, latest=None)
    reading = AsyncMock(return_value=review)
    writing = AsyncMock()
    monkeypatch.setattr(routes, "get_catalogue_admission", reading)
    monkeypatch.setattr(routes, "record_catalogue_admission", writing)
    with TestClient(application(ADMIN)) as client:
        response = client.get(PATH)
    assert response.status_code == 200
    parsed = CatalogueAdmissionReview.model_validate(response.json())
    assert not parsed.admitted
    assert parsed.state == "unreviewed"
    writing.assert_not_awaited()


def test_openapi_admission_contract_requires_cas_and_all_evidence_fields() -> None:
    schema = application(ADMIN).openapi()
    request = schema["components"]["schemas"]["AdmissionDecisionRequest"]
    assert set(request["required"]) == set(decision_body())
    assert request["additionalProperties"] is False
    assert schema["paths"][PATH.replace(str(CURRICULUM_ID), "{curriculum_version_id}")]["post"]
    assert schema["paths"]["/api/v1/admin/material-catalogue"]["get"]["responses"]["200"]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (CatalogueScopeNotFoundError(CURRICULUM_ID), 404, "catalogue_scope_not_found"),
        (CatalogueAdmissionConflictError(), 409, "catalogue_admission_conflict"),
        (CatalogueApprovalError("catalogue_scope_inactive"), 409, "catalogue_scope_inactive"),
        (
            CatalogueApprovalError("catalogue_labels_not_approvable"),
            422,
            "catalogue_labels_not_approvable",
        ),
        (
            AuthorizationError(REVIEWER.subject_id, Permission.TAXONOMY_WRITE),
            403,
            "permission_denied",
        ),
        (
            IntegrityError("INSERT", {}, RuntimeError("conflict")),
            409,
            "catalogue_admission_conflict",
        ),
    ],
)
def test_api_errors_rollback_without_exposing_database_details(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    session = AsyncMock()
    app = application(ADMIN)
    app.dependency_overrides[get_database_session] = lambda: session
    monkeypatch.setattr(routes, "record_catalogue_admission", AsyncMock(side_effect=error))
    with TestClient(app) as client:
        response = client.post(PATH, json=decision_body())
    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": code}}
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(("method", "status_code"), [("POST", 201), ("PATCH", 200)])
def test_both_write_routes_commit_explicit_decisions(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    status_code: int,
) -> None:
    session = AsyncMock()
    app = application(ADMIN)
    app.dependency_overrides[get_database_session] = lambda: session
    writing = AsyncMock(
        return_value=_review_response(scope(), active_chain=True, latest=decision())
    )
    monkeypatch.setattr(routes, "record_catalogue_admission", writing)
    with TestClient(app) as client:
        response = client.request(method, PATH, json=decision_body())
    assert response.status_code == status_code
    assert response.json()["admitted"]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    assert writing.await_args is not None
    assert writing.await_args.kwargs["principal"] == ADMIN


def test_review_history_is_read_only_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = AsyncMock(return_value=[decision()])
    monkeypatch.setattr(routes, "list_catalogue_admission_history", listing)
    with TestClient(application(REVIEWER)) as client:
        response = client.get(PATH + "/history", params={"limit": 10, "before_version": 2})
        assert client.get(PATH + "/history", params={"limit": 101}).status_code == 422
    assert response.status_code == 200
    assert response.json()[0]["version"] == 1
    assert listing.await_args is not None
    assert listing.await_args.kwargs == {"limit": 10, "before_version": 2}


def scoped_session(
    returned_scope: CatalogueScopeSnapshot | None, *, active_chain: bool = True
) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    rows = Mock()
    rows.mappings.return_value.one_or_none.return_value = (
        None
        if returned_scope is None
        else returned_scope.model_dump(exclude={"schema_version"}) | {"active_chain": active_chain}
    )
    session.execute.return_value = rows
    return session


@pytest.mark.parametrize("operation", ["review", "history", "write_missing", "write_incomplete"])
def test_missing_or_incomplete_catalogue_scope_fails_without_audit_or_writes(
    operation: str,
) -> None:
    session = scoped_session(None)
    session.scalar.return_value = CURRICULUM_ID if operation == "write_incomplete" else None

    async def read_or_write() -> None:
        if operation == "review":
            await get_catalogue_admission(cast(AsyncSession, session), CURRICULUM_ID)
        elif operation == "history":
            await list_catalogue_admission_history(cast(AsyncSession, session), CURRICULUM_ID)
        else:
            await record_catalogue_admission(
                cast(AsyncSession, session),
                CURRICULUM_ID,
                AdmissionDecisionRequest.model_validate(decision_body()),
                principal=ADMIN,
            )

    with pytest.raises(CatalogueScopeNotFoundError) as rejected:
        asyncio.run(read_or_write())
    assert rejected.value.curriculum_id == CURRICULUM_ID
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    if operation.startswith("write_"):
        session.scalar.assert_awaited_once()
        assert session.scalar.await_args is not None
        assert "FOR UPDATE" in str(session.scalar.await_args.args[0])
    else:
        session.scalar.assert_not_awaited()
    if operation == "write_missing":
        session.execute.assert_not_awaited()
    else:
        session.execute.assert_awaited_once()
        assert session.execute.await_args is not None
        query = str(session.execute.await_args.args[0])
        for parent in ("curriculum_versions", "exam_configurations", "media", "subjects"):
            assert parent in query
        if operation == "write_incomplete":
            assert "FOR UPDATE" in query
        else:
            assert "FOR UPDATE" not in query


@pytest.mark.parametrize(
    ("failure", "error_type", "code"),
    [
        ("version", CatalogueAdmissionConflictError, "catalogue_admission_conflict"),
        ("fingerprint", CatalogueAdmissionConflictError, "catalogue_scope_changed"),
        ("inactive", CatalogueApprovalError, "catalogue_scope_inactive"),
        ("private_labels", CatalogueApprovalError, "catalogue_labels_not_approvable"),
    ],
)
def test_direct_decision_rechecks_current_scope_version_and_approval_before_any_write(
    failure: str,
    error_type: type[CatalogueAdmissionConflictError | CatalogueApprovalError],
    code: str,
) -> None:
    current_scope = scope()
    if failure == "private_labels":
        current_scope = current_scope.model_copy(
            update={"curriculum_title": "Internal E2E fixture"}
        )
    session = scoped_session(current_scope, active_chain=failure != "inactive")
    session.scalar.side_effect = [CURRICULUM_ID, decision() if failure == "version" else None]
    payload = decision_body() | {
        "expected_scope_fingerprint": catalogue_scope_fingerprint(current_scope)
    }
    if failure == "fingerprint":
        payload["expected_scope_fingerprint"] = "sha256:" + "0" * 64
    request = AdmissionDecisionRequest.model_validate(payload)
    before = request.model_dump(mode="json")
    with pytest.raises(error_type) as rejected:
        asyncio.run(
            record_catalogue_admission(
                cast(AsyncSession, session), CURRICULUM_ID, request, principal=ADMIN
            )
        )
    assert isinstance(rejected.value, CatalogueAdmissionConflictError | CatalogueApprovalError)
    assert rejected.value.code == code
    assert request.model_dump(mode="json") == before
    assert session.scalar.await_count == 2
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(("limit", "expected"), [(-10, 1), (2, 2), (10000, 100)])
def test_history_limits_are_enforced_in_the_query_without_mutating_records(
    limit: int, expected: int
) -> None:
    session = scoped_session(scope())
    stored = decision()
    session.scalars.return_value = [stored]
    result = asyncio.run(
        list_catalogue_admission_history(
            cast(AsyncSession, session), CURRICULUM_ID, limit=limit, before_version=2
        )
    )
    assert [item.model_dump() for item in result] == [stored.model_dump()]
    session.scalars.assert_awaited_once()
    assert session.scalars.await_args is not None
    query = str(session.scalars.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "version < 2" in query
    assert "version DESC" in query
    assert f"LIMIT {expected}" in query
    assert "FOR UPDATE" not in query
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("limit", "offset", "bounded_limit", "bounded_offset"),
    [(-1, -9, 1, 0), (5, 2, 5, 2), (10000, 0, 1000, 0)],
)
def test_material_catalogue_limits_and_exact_readable_scope_fields(
    limit: int, offset: int, bounded_limit: int, bounded_offset: int
) -> None:
    original = scope()
    record = original.model_dump(exclude={"schema_version"}) | {"active_chain": True}
    session = AsyncMock(spec=AsyncSession)
    rows = Mock()
    rows.mappings.return_value = [record]
    session.execute.return_value = rows
    entries = asyncio.run(
        list_material_catalogue(
            cast(AsyncSession, session),
            grade=7,
            medium_id=original.medium_id,
            subject_id=original.subject_id,
            limit=limit,
            offset=offset,
        )
    )
    assert len(entries) == 1
    assert entries[0].model_dump() == {
        "curriculum_version_id": CURRICULUM_ID,
        "curriculum_title": original.curriculum_title,
        "exam_configuration_id": original.exam_configuration_id,
        "exam_configuration_name": original.exam_configuration_name,
        "grade": 7,
        "grade_label": "Grade 7",
        "medium_id": original.medium_id,
        "medium_name": "English",
        "subject_id": original.subject_id,
        "subject_name": "Mathematics",
    }
    assert session.execute.await_args is not None
    query = str(session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "catalogue_curriculum_is_admitted" in query
    assert "exam_configurations.grade = 7" in query
    assert "media.id =" in query
    assert "subjects.id =" in query
    assert f"LIMIT {bounded_limit}" in query
    assert f"OFFSET {bounded_offset}" in query
    assert "FOR UPDATE" not in query
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
