import asyncio
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session, get_object_storage
from exam_guru_api.api.routes.studio_safety import router
from exam_guru_api.auth.api import get_current_principal
from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import MaterialStatus
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    FixtureProvenanceEvidence,
    FixtureProvenanceMismatchError,
    FixtureQuarantineConflictError,
    FixtureQuarantineResult,
    InvalidFixtureQuarantineReviewError,
    SourceDocumentNotFoundError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

PROJECT = "ai-exam-guru-e2e-studio-safety"
ACTOR = UUID(int=101)
DOCUMENT_ID = UUID(int=20)
UPLOAD_AUDIT_ID = UUID(int=22)
CHECKSUM = "a" * 64
ADMIN = Principal(subject_id=ACTOR, roles=frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(subject_id=UUID(int=102), roles=frozenset({AdminRole.REVIEWER}))
PREFIX = "/api/v1/admin/studio-safety"
QUARANTINE_PATH = f"{PREFIX}/source-documents/{DOCUMENT_ID}/quarantine"
RESTORE_PATH = f"{PREFIX}/source-documents/{DOCUMENT_ID}/restore"


class EmptyRows:
    def all(self) -> list[object]:
        return []


class QuerySession:
    def __init__(self) -> None:
        self.statements: list[Select[tuple[object, ...]]] = []

    async def execute(self, statement: Select[tuple[object, ...]]) -> EmptyRows:
        self.statements.append(statement)
        return EmptyRows()

    async def scalars(self, statement: Select[tuple[object, ...]]) -> EmptyRows:
        self.statements.append(statement)
        return EmptyRows()


def query_service(session: QuerySession) -> SourceDocumentService:
    return SourceDocumentService(
        cast(AsyncSession, session), cast(ObjectStorage, object()), max_upload_bytes=1024
    )


def test_source_fixture_quarantine_is_persisted_and_defaults_false() -> None:
    column = SourceDocumentModel.__table__.c.quarantined_for_teacher_use
    assert not column.nullable
    assert column.default is not None
    assert column.default.arg is False
    assert column.server_default is not None
    assert str(column.server_default.arg) == "false"
    assert "original_page_count" in SourceDocumentModel.__table__.c


@pytest.mark.parametrize("status", [None, *MaterialStatus])
def test_material_list_and_exact_id_detail_filter_quarantine_in_sql(
    status: MaterialStatus | None,
) -> None:
    session = QuerySession()
    asyncio.run(query_service(session).list_materials(document_id=UUID(int=21), status=status))
    sql = str(
        session.statements[0].compile(dialect=cast(Callable[[], Dialect], postgresql.dialect)())
    )
    where = sql.split("WHERE", 1)[1]
    assert "source_documents.quarantined_for_teacher_use IS false" in where
    assert "source_documents.id =" in where


def test_grade_summary_excludes_quarantine_before_aggregation() -> None:
    session = QuerySession()
    summary = asyncio.run(query_service(session).grade_summary())
    assert len(summary) == 13
    sql = str(
        session.statements[0].compile(dialect=cast(Callable[[], Dialect], postgresql.dialect)())
    )
    assert "WHERE source_documents.quarantined_for_teacher_use IS false GROUP BY" in " ".join(
        sql.split()
    )


def test_advanced_source_history_is_not_filtered_by_quarantine() -> None:
    session = QuerySession()
    asyncio.run(query_service(session).list_documents(document_id=UUID(int=21)))
    sql = str(
        session.statements[0].compile(dialect=cast(Callable[[], Dialect], postgresql.dialect)())
    )
    assert "quarantined_for_teacher_use" not in sql.split("WHERE", 1)[1]


def test_runtime_identity_setting_is_explicit_and_test_only() -> None:
    assert Settings(_env_file=None, environment="test").test_runtime_id is None
    assert (
        Settings(_env_file=None, environment="test", test_runtime_id=PROJECT).test_runtime_id
        == PROJECT
    )
    with pytest.raises(ValidationError, match="test_runtime_id requires the test environment"):
        Settings(_env_file=None, environment="local", test_runtime_id=PROJECT)


@pytest.mark.parametrize(
    "identifier", ["", "ai-exam-guru", "wrong-project", PROJECT + "\n", "x" * 100]
)
def test_runtime_identity_setting_rejects_nonisolated_names(identifier: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="test", test_runtime_id=identifier)


def test_normal_source_defaults_do_not_classify_fixtures_from_actor_or_name() -> None:
    document = SourceDocumentModel(
        id=UUID(int=20),
        original_filename="E2E test material.pdf",
        created_by=UUID(int=101),
        updated_by=UUID(int=101),
        created_at=datetime.now(UTC),
    )
    assert document.quarantined_for_teacher_use is not True


class SafetySession:
    def __init__(self) -> None:
        self.document: SourceDocumentModel | None = SourceDocumentModel(
            id=DOCUMENT_ID,
            checksum_sha256=CHECKSUM,
            object_key=f"sources/aa/{CHECKSUM}.pdf",
            original_filename="Reviewed exact fixture.pdf",
            content_type="application/pdf",
            size_bytes=100,
            document_type=SourceDocumentType.TEACHER_GUIDE,
            extraction_status=ExtractionStatus.TRUSTED,
            active_for_ai=True,
            quarantined_for_teacher_use=False,
            metadata_scope_version=0,
            metadata_review_required=False,
            original_page_count=1,
            created_by=ACTOR,
            updated_by=ACTOR,
        )
        self.upload: AdminAuditEventModel | None = AdminAuditEventModel(
            id=UPLOAD_AUDIT_ID,
            action="source_document.uploaded",
            actor_id=ACTOR,
            resource_type="source_document",
            resource_id=DOCUMENT_ID,
            payload={"checksum_sha256": CHECKSUM},
        )
        self.added: list[AdminAuditEventModel] = []
        self.gets: list[tuple[type[object], UUID, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0

    async def get(
        self, model: type[object], identifier: UUID, **kwargs: object
    ) -> SourceDocumentModel | AdminAuditEventModel | None:
        self.gets.append((model, identifier, kwargs))
        if model is SourceDocumentModel:
            return self.document if identifier == DOCUMENT_ID else None
        if model is AdminAuditEventModel:
            return self.upload if identifier == UPLOAD_AUDIT_ID else None
        raise AssertionError(model)

    def add(self, event: AdminAuditEventModel) -> None:
        self.added.append(event)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, _document: SourceDocumentModel) -> None:
        return None


def evidence_payload() -> dict[str, object]:
    return {
        "source_document_id": str(DOCUMENT_ID),
        "checksum_sha256": CHECKSUM,
        "upload_audit_event_id": str(UPLOAD_AUDIT_ID),
        "fixture_reference": "reviewed-manifest/source-fixture-20",
        "observed_evidence": ["Exact PDF contents match the recorded synthetic test passage"],
    }


def review_payload(*, restore: bool = False, version: int = 0) -> dict[str, object]:
    return {
        "confirmation": (
            "restore_exact_source_fixture" if restore else "quarantine_exact_source_fixture"
        ),
        "reason": "Exact source identity and fixture provenance manually reviewed",
        "expected_version": version,
        "provenance_evidence": evidence_payload(),
    }


def change(
    session: SafetySession,
    *,
    quarantined: bool = True,
    version: int = 0,
    principal: Principal = ADMIN,
    evidence: FixtureProvenanceEvidence | None = None,
    confirmation: str | None = None,
    reason: str = "Exact source identity and fixture provenance manually reviewed",
) -> FixtureQuarantineResult:
    return asyncio.run(
        SourceDocumentService(
            cast(AsyncSession, session), cast(ObjectStorage, object()), max_upload_bytes=1024
        ).change_fixture_quarantine(
            DOCUMENT_ID,
            quarantined=quarantined,
            principal=principal,
            expected_version=version,
            reason=reason,
            confirmation=confirmation
            or (
                "quarantine_exact_source_fixture" if quarantined else "restore_exact_source_fixture"
            ),
            provenance_evidence=evidence
            or FixtureProvenanceEvidence.model_validate(evidence_payload()),
        )
    )


def safety_client(
    session: SafetySession | None = None,
    *,
    settings: Settings | None = None,
    principal: Principal | None = ADMIN,
) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin")
    app.state.settings = settings or Settings(_env_file=None, environment="test")

    async def inert_session() -> AsyncSession:
        if session is None:
            raise AssertionError("runtime identity must not access persistence")
        return cast(AsyncSession, session)

    async def inert_storage() -> ObjectStorage:
        return cast(ObjectStorage, object())

    async def identity() -> Principal:
        assert principal is not None
        return principal

    app.dependency_overrides[get_database_session] = inert_session
    app.dependency_overrides[get_object_storage] = inert_storage
    if principal is not None:
        app.dependency_overrides[get_current_principal] = identity
    return TestClient(app)


def test_quarantine_versions_exact_document_and_preserves_trust_originals_and_evidence() -> None:
    session = SafetySession()
    supplied_evidence = FixtureProvenanceEvidence.model_validate(evidence_payload())
    result = change(session, evidence=supplied_evidence)
    document = result.document
    assert document.quarantined_for_teacher_use
    assert not document.active_for_ai
    assert document.metadata_scope_version == 1
    assert document.extraction_status is ExtractionStatus.TRUSTED
    assert document.metadata_review_required is False
    assert document.checksum_sha256 == CHECKSUM
    assert document.object_key == f"sources/aa/{CHECKSUM}.pdf"
    assert document.original_page_count == 1
    assert document.removal_reason == review_payload()["reason"]
    assert document.removed_by == ACTOR
    assert document.removed_at is not None
    assert session.commits == 1
    assert session.gets[0] == (
        SourceDocumentModel,
        DOCUMENT_ID,
        {"with_for_update": True, "populate_existing": True},
    )
    assert len(session.added) == 1
    audit = session.added[0]
    assert audit.id == result.audit_event_id
    assert audit.action == "source_document.fixture_quarantined"
    assert audit.actor_id == ACTOR
    assert audit.resource_type == "source_document"
    assert audit.resource_id == DOCUMENT_ID
    assert audit.payload == {
        "confirmation": "quarantine_exact_source_fixture",
        "reason": review_payload()["reason"],
        "provenance_evidence": evidence_payload(),
        "previous_version": 0,
        "version": 1,
        "from": {"quarantined_for_teacher_use": False, "active_for_ai": True},
        "to": {"quarantined_for_teacher_use": True, "active_for_ai": False},
    }
    assert supplied_evidence.model_dump(mode="json") == evidence_payload()


def test_restoration_is_explicit_audited_and_never_reactivates_or_trusts() -> None:
    session = SafetySession()
    assert session.document is not None
    session.document.extraction_status = ExtractionStatus.UPLOADED
    session.document.metadata_review_required = True
    session.document.intake_metadata = {"candidate_grade": 5}
    change(session)
    removed_at = session.document.removed_at
    restored = change(session, quarantined=False, version=1).document
    assert not restored.quarantined_for_teacher_use
    assert not restored.active_for_ai
    assert restored.metadata_scope_version == 2
    assert restored.extraction_status is ExtractionStatus.UPLOADED
    assert restored.metadata_review_required
    assert restored.intake_metadata == {"candidate_grade": 5}
    assert restored.removed_at == removed_at
    assert len(session.added) == 2
    assert session.added[1].action == "source_document.fixture_restored"
    assert session.added[1].payload["confirmation"] == "restore_exact_source_fixture"
    assert session.added[1].payload["from"] == {
        "quarantined_for_teacher_use": True,
        "active_for_ai": False,
    }
    assert session.added[1].payload["to"] == {
        "quarantined_for_teacher_use": False,
        "active_for_ai": False,
    }


def test_quarantine_of_an_already_removed_source_preserves_its_removal_history() -> None:
    session = SafetySession()
    assert session.document is not None
    removed_at = datetime.now(UTC)
    session.document.active_for_ai = False
    session.document.removal_reason = "Previously removed for incorrect grade"
    session.document.removed_by = UUID(int=200)
    session.document.removed_at = removed_at
    result = change(session)
    assert result.document.quarantined_for_teacher_use
    assert result.document.removal_reason == "Previously removed for incorrect grade"
    assert result.document.removed_by == UUID(int=200)
    assert result.document.removed_at == removed_at
    assert result.document.metadata_scope_version == 1


def test_quarantine_service_requires_source_write_permission_before_data_access() -> None:
    session = SafetySession()
    with pytest.raises(AuthorizationError):
        change(session, principal=REVIEWER)
    assert not session.gets
    assert not session.added
    assert session.commits == 0


@pytest.mark.parametrize("reason", ["", " ", " padded", "bad\nreason", "r" * 513])
def test_quarantine_service_rejects_unreviewed_reasons_without_mutation(reason: str) -> None:
    session = SafetySession()
    with pytest.raises(InvalidFixtureQuarantineReviewError):
        change(session, reason=reason)
    assert not session.gets
    assert not session.added


@pytest.mark.parametrize("confirmation", ["yes", "restore_exact_source_fixture"])
def test_confirmation_must_match_the_explicit_action(confirmation: str) -> None:
    session = SafetySession()
    with pytest.raises(InvalidFixtureQuarantineReviewError):
        change(session, confirmation=confirmation)
    assert not session.gets


def test_quarantine_rejects_stale_versions_and_duplicate_transitions_without_new_audit() -> None:
    session = SafetySession()
    change(session)
    with pytest.raises(ConcurrentMaterialScopeVersionError):
        change(session, version=0)
    with pytest.raises(FixtureQuarantineConflictError):
        change(session, version=1)
    assert session.commits == 1
    assert len(session.added) == 1


@pytest.mark.parametrize(
    "field", ["source_document_id", "checksum_sha256", "upload_audit_event_id"]
)
def test_evidence_must_match_the_exact_document_and_immutable_upload_audit(field: str) -> None:
    session = SafetySession()
    payload = evidence_payload()
    payload[field] = "b" * 64 if field == "checksum_sha256" else str(UUID(int=900))
    with pytest.raises(FixtureProvenanceMismatchError):
        change(session, evidence=FixtureProvenanceEvidence.model_validate(payload))
    assert not session.added
    assert session.commits == 0
    assert session.document is not None
    assert not session.document.quarantined_for_teacher_use


@pytest.mark.parametrize("field", ["resource_id", "resource_type", "action", "payload"])
def test_quarantine_cannot_reuse_an_unrelated_audit_event(field: str) -> None:
    session = SafetySession()
    assert session.upload is not None
    invalid: dict[str, object] = {
        "resource_id": UUID(int=900),
        "resource_type": "curriculum",
        "action": "source_document.scope_corrected",
        "payload": {"checksum_sha256": "b" * 64},
    }
    setattr(session.upload, field, invalid[field])
    with pytest.raises(FixtureProvenanceMismatchError):
        change(session)
    assert not session.added


@pytest.mark.parametrize("payload", [None, [], "checksum-only", True])
def test_quarantine_rejects_nonobject_upload_audit_payload(payload: object) -> None:
    session = SafetySession()
    assert session.upload is not None
    session.upload.payload = cast(dict[str, object], payload)
    with pytest.raises(FixtureProvenanceMismatchError):
        change(session)
    assert not session.added
    assert session.commits == 0


def test_quarantine_requires_an_existing_exact_document() -> None:
    session = SafetySession()
    session.document = None
    with pytest.raises(SourceDocumentNotFoundError):
        change(session)
    assert not session.added


def test_normal_material_restore_cannot_bypass_fixture_quarantine() -> None:
    session = SafetySession()
    change(session)
    with pytest.raises(SourceDocumentNotFoundError):
        asyncio.run(
            SourceDocumentService(
                cast(AsyncSession, session), cast(ObjectStorage, object()), max_upload_bytes=1024
            ).restore_to_ai_use(DOCUMENT_ID, expected_version=1, actor_id=ACTOR)
        )
    assert session.document is not None
    assert not session.document.active_for_ai
    assert len(session.added) == 1


@pytest.mark.parametrize("path", [QUARANTINE_PATH, RESTORE_PATH, f"{PREFIX}/runtime-identity"])
@pytest.mark.parametrize(("principal", "expected"), [(None, 401), (REVIEWER, 403)])
def test_safety_api_requires_admin_source_write(
    path: str, principal: Principal | None, expected: int
) -> None:
    session = SafetySession()
    with safety_client(session, principal=principal) as client:
        response = (
            client.get(path)
            if path.endswith("runtime-identity")
            else client.post(path, json=review_payload())
        )
    assert response.status_code == expected
    assert not session.gets
    assert not session.added


def test_safety_api_quarantine_and_restore_return_versioned_audit_receipts() -> None:
    session = SafetySession()
    with safety_client(session) as client:
        response = client.post(QUARANTINE_PATH, json=review_payload())
        assert response.status_code == 200
        assert response.json() == {
            "id": str(DOCUMENT_ID),
            "quarantined_for_teacher_use": True,
            "active_for_ai": False,
            "metadata_scope_version": 1,
            "audit_event_id": str(session.added[0].id),
        }
        assert response.headers["cache-control"] == "private, no-store"
        restored = client.post(RESTORE_PATH, json=review_payload(restore=True, version=1))
        assert restored.status_code == 200
        assert restored.json()["quarantined_for_teacher_use"] is False
        assert restored.json()["active_for_ai"] is False
        assert restored.json()["metadata_scope_version"] == 2


@pytest.mark.parametrize(
    "override",
    [
        {"confirmation": True},
        {"confirmation": "restore_exact_source_fixture"},
        {"reason": " "},
        {"expected_version": True},
        {"expected_version": -1},
        {"expected_version": "0"},
        {"provenance_evidence": {"actor_id": str(ACTOR)}},
        {"provenance_evidence": {"filename": "test.pdf"}},
        {"actor_id": str(ACTOR)},
        {"document_ids": [str(DOCUMENT_ID)]},
        {"provenance_evidence": {**evidence_payload(), "observed_evidence": []}},
        {"provenance_evidence": {**evidence_payload(), "fixture_reference": " "}},
        {"provenance_evidence": {**evidence_payload(), "actor_id": str(ACTOR)}},
        {"provenance_evidence": {**evidence_payload(), "checksum_sha256": "bad"}},
    ],
)
def test_safety_api_rejects_blind_bulk_or_actor_name_only_classification(
    override: dict[str, object],
) -> None:
    session = SafetySession()
    with safety_client(session) as client:
        response = client.post(QUARANTINE_PATH, json={**review_payload(), **override})
    assert response.status_code == 422
    assert not session.gets
    assert not session.added


@pytest.mark.parametrize(
    "missing", ["confirmation", "reason", "expected_version", "provenance_evidence"]
)
def test_safety_api_requires_all_review_fields(missing: str) -> None:
    session = SafetySession()
    payload = review_payload()
    del payload[missing]
    with safety_client(session) as client:
        response = client.post(QUARANTINE_PATH, json=payload)
    assert response.status_code == 422
    assert not session.gets


def test_safety_api_returns_conflicts_without_an_extra_audit() -> None:
    session = SafetySession()
    with safety_client(session) as client:
        assert client.post(QUARANTINE_PATH, json=review_payload()).status_code == 200
        stale = client.post(RESTORE_PATH, json=review_payload(restore=True, version=0))
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "material_version_conflict"
        duplicate = client.post(QUARANTINE_PATH, json=review_payload(version=1))
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "fixture_quarantine_state_conflict"
    assert len(session.added) == 1


def test_runtime_identity_is_authoritative_minimal_and_never_reads_or_writes_source_data() -> None:
    settings = Settings(_env_file=None, environment="test", test_runtime_id=PROJECT)
    with safety_client(settings=settings) as client:
        response = client.get(f"{PREFIX}/runtime-identity")
    assert response.status_code == 200
    assert response.json() == {"application_env": "test", "test_runtime_id": PROJECT}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_local_identity_cannot_be_forged_by_caller_headers_or_query() -> None:
    with safety_client(settings=Settings(_env_file=None, environment="local")) as client:
        response = client.get(
            f"{PREFIX}/runtime-identity?application_env=test&test_runtime_id={PROJECT}",
            headers={"EXAM_GURU_ENVIRONMENT": "test", "EXAM_GURU_TEST_RUNTIME_ID": PROJECT},
        )
    assert response.status_code == 200
    assert response.json() == {"application_env": "local"}


def test_test_environment_without_runtime_id_cannot_claim_isolated_identity() -> None:
    with safety_client(settings=Settings(_env_file=None, environment="test")) as client:
        response = client.get(f"{PREFIX}/runtime-identity")
    assert response.json() == {"application_env": "test"}


def test_wrapper_overrides_forged_runtime_id_and_scopes_cleanup_to_its_own_project(
    tmp_path: Path,
) -> None:
    log = tmp_path / "calls"
    for name, body in {
        "docker": (
            'printf \'docker %s runtime=%s env=%s\\n\' "$*" "$EXAM_GURU_TEST_RUNTIME_ID" '
            '"$EXAM_GURU_ENVIRONMENT" >> "$CALL_LOG"\n'
            "case \"$*\" in *tesseract*) printf 'eng\\nsin\\ntam\\n';; esac\n"
        ),
        "curl": "exit 0\n",
        "npm": (
            "printf 'npm runtime=%s project=%s isolated=%s\\n' \"$EXAM_GURU_TEST_RUNTIME_ID\" "
            '"$E2E_COMPOSE_PROJECT_NAME" "$E2E_RUNTIME_ISOLATED" >> "$CALL_LOG"\n'
        ),
    }.items():
        executable = tmp_path / name
        executable.write_text("#!/bin/sh\n" + body)
        executable.chmod(0o700)
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["/bin/bash", "scripts/run_isolated_e2e.sh"],
        cwd=root,
        env={
            "PATH": f"{tmp_path}:{os.defpath}",
            "TMPDIR": str(tmp_path),
            "CALL_LOG": str(log),
            "E2E_COMPOSE_PROJECT_NAME": PROJECT,
            "EXAM_GURU_TEST_RUNTIME_ID": "forged",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert all("forged" not in call for call in calls)
    assert all(f"--project-name {PROJECT}" in call for call in calls if call.startswith("docker"))
    assert f"npm runtime={PROJECT} project={PROJECT} isolated=true" in calls
    assert any(
        f"down --volumes --remove-orphans runtime={PROJECT} env=test" in call for call in calls
    )
    compose = (root / "compose.yaml").read_text()
    before_api, api_and_after = compose.split("\n  api:\n", 1)
    api, after_api = api_and_after.split("\n  worker:\n", 1)
    setting = "EXAM_GURU_TEST_RUNTIME_ID"
    assert setting not in before_api
    assert setting not in after_api
    assert f"{setting}: ${{{setting}:-}}" in api


def test_normal_compose_empty_runtime_id_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAM_GURU_TEST_RUNTIME_ID", "")
    settings = Settings(_env_file=None, environment="local")
    assert settings.test_runtime_id is None


@pytest.mark.parametrize("version", [True, -1, "0", 2_147_483_647])
def test_quarantine_service_rejects_invalid_versions_before_data_access(version: object) -> None:
    session = SafetySession()
    with pytest.raises(InvalidFixtureQuarantineReviewError):
        change(session, version=cast(int, version))
    assert not session.gets
    assert not session.added


def test_quarantine_service_revalidates_constructed_evidence_before_data_access() -> None:
    session = SafetySession()
    invalid = FixtureProvenanceEvidence.model_validate(evidence_payload()).model_copy(
        update={"observed_evidence": ()}
    )
    with pytest.raises(InvalidFixtureQuarantineReviewError):
        change(session, evidence=invalid)
    assert not session.gets
    assert not session.added


@pytest.mark.parametrize("restore", [False, True])
def test_safety_api_provenance_mismatch_is_sanitized(restore: bool) -> None:
    session = SafetySession()
    if restore:
        change(session)
    payload = review_payload(restore=restore, version=int(restore))
    payload["provenance_evidence"] = {**evidence_payload(), "checksum_sha256": "b" * 64}
    with safety_client(session) as client:
        response = client.post(RESTORE_PATH if restore else QUARANTINE_PATH, json=payload)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "fixture_provenance_mismatch"}}
    assert len(session.added) == int(restore)


@pytest.mark.parametrize("restore", [False, True])
def test_missing_source_api_error_never_exposes_provenance_or_creates_an_audit(
    restore: bool,
) -> None:
    session = SafetySession()
    session.document = None
    with safety_client(session) as client:
        response = client.post(
            RESTORE_PATH if restore else QUARANTINE_PATH, json=review_payload(restore=restore)
        )
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "source_document_not_found"}}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert len(session.gets) == 1
    assert session.gets[0][0] is SourceDocumentModel
    assert session.gets[0][1] == DOCUMENT_ID
    assert session.commits == 0
    assert not session.added
    assert CHECKSUM not in response.text


@pytest.mark.parametrize("restore", [False, True])
def test_domain_review_rejection_is_sanitized_after_authorized_api_dispatch(
    restore: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SafetySession()
    rejection = AsyncMock(
        side_effect=InvalidFixtureQuarantineReviewError(
            f"private provenance {CHECKSUM} reviewed-manifest/source-fixture-20"
        )
    )
    monkeypatch.setattr(SourceDocumentService, "change_fixture_quarantine", rejection)
    with safety_client(session) as client:
        response = client.post(
            RESTORE_PATH if restore else QUARANTINE_PATH, json=review_payload(restore=restore)
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_fixture_quarantine_review"}}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    rejection.assert_awaited_once()
    assert rejection.await_args is not None
    assert rejection.await_args.args == (DOCUMENT_ID,)
    assert rejection.await_args.kwargs["principal"] is ADMIN
    assert rejection.await_args.kwargs["quarantined"] is (not restore)
    assert (
        rejection.await_args.kwargs["confirmation"]
        == review_payload(restore=restore)["confirmation"]
    )
    assert (
        rejection.await_args.kwargs["provenance_evidence"].model_dump(mode="json")
        == evidence_payload()
    )
    assert CHECKSUM not in response.text
    assert "reviewed-manifest" not in response.text
    assert session.commits == 0
    assert not session.gets
    assert not session.added


@pytest.mark.parametrize("restore", [False, True])
def test_quarantine_commit_failure_rolls_back_without_refresh_or_internal_error_details(
    restore: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SafetySession()
    if restore:
        change(session)
    supplied = FixtureProvenanceEvidence.model_validate(evidence_payload())
    snapshot = supplied.model_dump(mode="json")
    commit = AsyncMock(
        side_effect=IntegrityError(
            "UPDATE private_source_identity",
            {"checksum": CHECKSUM},
            RuntimeError("private audit conflict"),
        )
    )
    refresh = AsyncMock()
    monkeypatch.setattr(session, "commit", commit)
    monkeypatch.setattr(session, "refresh", refresh)
    with pytest.raises(FixtureQuarantineConflictError) as rejected:
        change(session, quarantined=not restore, version=int(restore), evidence=supplied)
    commit.assert_awaited_once()
    refresh.assert_not_awaited()
    assert session.rollbacks == 1
    assert str(rejected.value) == ""
    assert rejected.value.__cause__ is None
    assert rejected.value.__suppress_context__
    assert supplied.model_dump(mode="json") == snapshot
    assert session.added[-1].payload["provenance_evidence"] == snapshot
    assert session.added[-1].payload["previous_version"] == int(restore)
    assert session.added[-1].payload["version"] == int(restore) + 1


@pytest.mark.parametrize("restore", [False, True])
def test_commit_failure_api_returns_conflict_not_a_success_receipt(
    restore: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SafetySession()
    if restore:
        change(session)
    commit = AsyncMock(
        side_effect=IntegrityError(
            "private SQL",
            {"checksum_sha256": CHECKSUM},
            RuntimeError("private storage and audit proof"),
        )
    )
    monkeypatch.setattr(session, "commit", commit)
    with safety_client(session) as client:
        response = client.post(
            RESTORE_PATH if restore else QUARANTINE_PATH,
            json=review_payload(restore=restore, version=int(restore)),
        )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "fixture_quarantine_state_conflict"}}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert session.rollbacks == 1
    commit.assert_awaited_once()
    assert CHECKSUM not in response.text
    assert "private" not in response.text
