import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.catalogue_admission import router
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    CatalogueAdmissionConflictError,
    CurriculumNotAdmittedError,
    admitted_curriculum_predicate,
    catalogue_scope_fingerprint,
    get_catalogue_admission,
    list_catalogue_admission_history,
    list_material_catalogue,
    record_catalogue_admission,
    require_admitted_curriculum,
)
from exam_guru_api.curriculum.admission_models import (
    CatalogueAdmissionCurrentModel,
    CatalogueAdmissionDecisionModel,
)
from exam_guru_api.curriculum.domain import LEGACY_UNCLASSIFIED_SUBJECT_ID
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import (
    FixtureProvenanceEvidence,
    FixtureQuarantineConflictError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.migrations import (
    assert_database_schema_current,
    upgrade_database,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

pytestmark = pytest.mark.integration
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ADMIN = Principal(UUID(int=34), frozenset({AdminRole.ADMIN}))
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
CATALOGUE_PATH = "/api/v1/admin/material-catalogue"


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return ADMIN
        if access_token == "reviewer-token":
            return Principal(UUID(int=35), frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


@pytest.fixture(scope="module")
def admission_database_url() -> Iterator[str]:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image=PGVECTOR_IMAGE,
            username="exam_guru",
            password=uuid4().hex,
            dbname="exam_guru_catalogue_admission_test",
            driver="asyncpg",
        ) as postgres:
            database_url = postgres.get_connection_url()
            upgrade_database(database_url)
            assert_database_schema_current(database_url)
            yield database_url


@pytest.fixture
def admission_client(admission_database_url: str) -> Iterator[TestClient]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(admission_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def database_session() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        _app.dependency_overrides[get_database_session] = database_session
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.identity_provider = StaticIdentityProvider()
    app.include_router(router, prefix="/api/v1/admin")
    with TestClient(app) as client:
        yield client


def admission_path(curriculum_id: UUID) -> str:
    return f"/api/v1/admin/curriculum-versions/{curriculum_id}/admission"


def seed_scope(
    database_url: str, *, title: str = "Mathematics syllabus 2026", sentinel: bool = False
) -> UUID:
    async def seed() -> UUID:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                exam = ExamConfigurationModel(
                    id=uuid4(),
                    code="G7-" + uuid4().hex[:20].upper(),
                    name="School Grade 7",
                    grade=7,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                medium = MediumModel(
                    id=uuid4(),
                    code="m" + uuid4().hex[:15],
                    name="English",
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                subject = SubjectModel(
                    id=uuid4(),
                    code="MATHEMATICS-" + uuid4().hex.upper(),
                    name="Mathematics",
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                session.add_all([exam, medium, subject])
                await session.flush()
                curriculum_id = uuid4()
                session.add(
                    CurriculumVersionModel(
                        id=curriculum_id,
                        exam_configuration_id=exam.id,
                        medium_id=medium.id,
                        subject_id=LEGACY_UNCLASSIFIED_SUBJECT_ID if sentinel else subject.id,
                        code="2026",
                        title=title,
                        active=True,
                        created_by=ADMIN.subject_id,
                        updated_by=ADMIN.subject_id,
                    )
                )
                await session.commit()
                return curriculum_id
        finally:
            await engine.dispose()

    return asyncio.run(seed())


def review(client: TestClient, curriculum_id: UUID) -> dict[str, Any]:
    response = client.get(admission_path(curriculum_id), headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def request_body(current: dict[str, Any], state: str = "approved") -> dict[str, Any]:
    return {
        "state": state,
        "expected_version": current["version"],
        "expected_scope_fingerprint": current["scope_fingerprint"],
        "educational_approval": state == "approved",
        "reason": "Explicit educational scope review."
        if state == "approved"
        else "Audited removal from educational use.",
        "source_reference": "Local curriculum review register, Mathematics, pages 1-3",
        "evidence": [
            "Reviewed grade, medium, subject and curriculum identity against the register."
        ],
    }


def approve(client: TestClient, curriculum_id: UUID) -> dict[str, Any]:
    response = client.post(
        admission_path(curriculum_id),
        json=request_body(review(client, curriculum_id)),
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def listed_ids(client: TestClient) -> set[UUID]:
    response = client.get(CATALOGUE_PATH, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return {UUID(item["curriculum_version_id"]) for item in response.json()}


def execute(database_url: str, statement: Any) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                await session.execute(statement)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_legitimate_unapproved_scope_is_absent_and_approval_preserves_real_labels(
    admission_database_url: str,
    admission_client: TestClient,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    initial = review(admission_client, curriculum_id)
    assert initial["state"] == "unreviewed"
    assert initial["version"] == 0
    assert not initial["admitted"]
    assert curriculum_id not in listed_ids(admission_client)
    approved = approve(admission_client, curriculum_id)
    assert approved["admitted"]
    assert approved["version"] == 1
    entries = admission_client.get(CATALOGUE_PATH, headers=ADMIN_HEADERS).json()
    entry = next(item for item in entries if item["curriculum_version_id"] == str(curriculum_id))
    assert entry["grade"] == 7
    assert entry["grade_label"] == "Grade 7"
    assert entry["exam_configuration_name"] == "School Grade 7"
    assert entry["medium_name"] == "English"
    assert entry["subject_name"] == "Mathematics"
    assert entry["curriculum_title"] == "Mathematics syllabus 2026"
    assert "scope_fingerprint" not in entry
    assert "curriculum_code" not in entry

    async def verify() -> None:
        engine = create_async_engine(admission_database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                admitted = await require_admitted_curriculum(session, curriculum_id)
                assert admitted.curriculum_version_id == curriculum_id
                assert catalogue_scope_fingerprint(admitted) == approved["scope_fingerprint"]
                selected = await session.scalar(
                    select(CurriculumVersionModel.id).where(
                        CurriculumVersionModel.id == curriculum_id,
                        admitted_curriculum_predicate(CurriculumVersionModel.id),
                    )
                )
                assert selected == curriculum_id
                pointer = await session.get(CatalogueAdmissionCurrentModel, curriculum_id)
                assert pointer is not None
                decision = await session.get(CatalogueAdmissionDecisionModel, pointer.decision_id)
                assert decision is not None
                audit = await session.get(AdminAuditEventModel, decision.audit_event_id)
                assert audit is not None
                assert audit.actor_id == ADMIN.subject_id
                assert audit.payload["scope_fingerprint"] == approved["scope_fingerprint"]
                assert audit.payload["educational_approval"] is True
        finally:
            await engine.dispose()

    asyncio.run(verify())


@pytest.mark.parametrize(
    "title",
    [
        "E2E Mathematics",
        "Mathematics fixture 1724000000",
        "Internal curriculum",
        "\uff25\uff12\uff25 Curriculum",
    ],
)
def test_fixture_labels_need_explicit_quarantine_and_cannot_be_approved(
    admission_database_url: str,
    admission_client: TestClient,
    title: str,
) -> None:
    curriculum_id = seed_scope(admission_database_url, title=title)
    initial = review(admission_client, curriculum_id)
    assert initial["state"] == "unreviewed"
    rejected = admission_client.post(
        admission_path(curriculum_id), json=request_body(initial), headers=ADMIN_HEADERS
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "catalogue_labels_not_approvable"
    assert review(admission_client, curriculum_id)["version"] == 0
    quarantined = admission_client.post(
        admission_path(curriculum_id),
        json=request_body(initial, "quarantined"),
        headers=ADMIN_HEADERS,
    )
    assert quarantined.status_code == 201
    assert quarantined.json()["state"] == "quarantined"
    assert curriculum_id not in listed_ids(admission_client)


def test_migration_sentinel_cannot_be_admitted(
    admission_database_url: str, admission_client: TestClient
) -> None:
    curriculum_id = seed_scope(admission_database_url, sentinel=True)
    response = admission_client.post(
        admission_path(curriculum_id),
        json=request_body(review(admission_client, curriculum_id)),
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422
    assert curriculum_id not in listed_ids(admission_client)


@pytest.mark.parametrize("title", ["2026", "Mathematics 1724000000", "ගණිතය", "கணிதம்"])
def test_numeric_version_and_shared_actor_do_not_imply_fixture_origin(
    admission_database_url: str,
    admission_client: TestClient,
    title: str,
) -> None:
    curriculum_id = seed_scope(admission_database_url, title=title)
    assert review(admission_client, curriculum_id)["state"] == "unreviewed"
    assert approve(admission_client, curriculum_id)["admitted"]


@pytest.mark.parametrize("resource", ["curriculum", "exam", "medium", "subject"])
def test_each_inactive_parent_blocks_listing_helper_and_fresh_approval(
    admission_database_url: str,
    admission_client: TestClient,
    resource: str,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    approved = approve(admission_client, curriculum_id)
    scope = approved["scope"]
    models: dict[
        str,
        tuple[
            type[CurriculumVersionModel | ExamConfigurationModel | MediumModel | SubjectModel], UUID
        ],
    ] = {
        "curriculum": (CurriculumVersionModel, curriculum_id),
        "exam": (ExamConfigurationModel, UUID(scope["exam_configuration_id"])),
        "medium": (MediumModel, UUID(scope["medium_id"])),
        "subject": (SubjectModel, UUID(scope["subject_id"])),
    }
    model, resource_id = models[resource]
    execute(
        admission_database_url, update(model).where(model.id == resource_id).values(active=False)
    )
    assert curriculum_id not in listed_ids(admission_client)
    assert not review(admission_client, curriculum_id)["admitted"]
    response = admission_client.post(
        admission_path(curriculum_id),
        json=request_body(review(admission_client, curriculum_id)),
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "catalogue_scope_inactive"
    assert_helper_rejects(admission_database_url, curriculum_id)


def assert_helper_rejects(database_url: str, curriculum_id: UUID | None) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                with pytest.raises(CurriculumNotAdmittedError):
                    await require_admitted_curriculum(session, curriculum_id)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_label_or_parent_change_invalidates_approval_without_rewriting_history(
    admission_database_url: str,
    admission_client: TestClient,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    approved = approve(admission_client, curriculum_id)
    subject_id = UUID(approved["scope"]["subject_id"])
    execute(
        admission_database_url,
        update(SubjectModel)
        .where(SubjectModel.id == subject_id)
        .values(name="Mathematics revised"),
    )
    stale = review(admission_client, curriculum_id)
    assert stale["stale"]
    assert stale["latest_decision"]["scope_snapshot"]["subject_name"] == "Mathematics"
    assert curriculum_id not in listed_ids(admission_client)
    assert_helper_rejects(admission_database_url, curriculum_id)
    response = admission_client.post(
        admission_path(curriculum_id), json=request_body(approved), headers=ADMIN_HEADERS
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "catalogue_scope_changed"
    assert approve(admission_client, curriculum_id)["version"] == 2
    other = seed_scope(admission_database_url)
    other_scope = review(admission_client, other)["scope"]
    execute(
        admission_database_url,
        update(CurriculumVersionModel)
        .where(CurriculumVersionModel.id == curriculum_id)
        .values(medium_id=UUID(other_scope["medium_id"])),
    )
    assert curriculum_id not in listed_ids(admission_client)
    assert review(admission_client, curriculum_id)["stale"]


def test_cas_quarantine_rejection_and_explicit_restore_preserve_audit_history(
    admission_database_url: str,
    admission_client: TestClient,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    initial = review(admission_client, curriculum_id)
    approved = approve(admission_client, curriculum_id)
    conflict = admission_client.post(
        admission_path(curriculum_id), json=request_body(initial), headers=ADMIN_HEADERS
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "catalogue_admission_conflict"
    quarantined = admission_client.patch(
        admission_path(curriculum_id),
        json=request_body(approved, "quarantined"),
        headers=ADMIN_HEADERS,
    )
    assert quarantined.status_code == 200
    assert not quarantined.json()["admitted"]
    assert curriculum_id not in listed_ids(admission_client)
    assert_helper_rejects(admission_database_url, curriculum_id)
    missing_approval = request_body(quarantined.json()) | {"educational_approval": False}
    assert (
        admission_client.patch(
            admission_path(curriculum_id), json=missing_approval, headers=ADMIN_HEADERS
        ).status_code
        == 422
    )
    restored = approve(admission_client, curriculum_id)
    assert restored["version"] == 3
    rejected = admission_client.post(
        admission_path(curriculum_id),
        json=request_body(restored, "rejected"),
        headers=ADMIN_HEADERS,
    )
    assert rejected.status_code == 201
    assert curriculum_id not in listed_ids(admission_client)
    assert approve(admission_client, curriculum_id)["version"] == 5
    history = admission_client.get(
        admission_path(curriculum_id) + "/history", headers=ADMIN_HEADERS
    ).json()
    assert [item["version"] for item in history] == [5, 4, 3, 2, 1]
    assert [item["state"] for item in history] == [
        "approved",
        "rejected",
        "approved",
        "quarantined",
        "approved",
    ]
    assert all(item["actor_id"] == str(ADMIN.subject_id) for item in history)
    assert all(
        item["decided_at"] and item["evidence"] and item["source_reference"] for item in history
    )


def test_unknown_scope_and_unapproved_helper_fail_closed(
    admission_database_url: str,
    admission_client: TestClient,
) -> None:
    unknown = uuid4()
    response = admission_client.get(admission_path(unknown), headers=ADMIN_HEADERS)
    assert response.status_code == 404
    assert_helper_rejects(admission_database_url, unknown)
    assert_helper_rejects(admission_database_url, None)
    assert_helper_rejects(admission_database_url, seed_scope(admission_database_url))


def test_concurrent_first_reviews_have_exactly_one_cas_winner(admission_database_url: str) -> None:
    curriculum_id = seed_scope(admission_database_url)

    async def race() -> None:
        engine = create_async_engine(admission_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                current = await get_catalogue_admission(session, curriculum_id)
                request = AdmissionDecisionRequest.model_validate(
                    request_body(current.model_dump(mode="json"))
                )

            async def write() -> str:
                async with sessions() as session:
                    try:
                        await record_catalogue_admission(
                            session, curriculum_id, request, principal=ADMIN
                        )
                        await session.commit()
                        return "approved"
                    except CatalogueAdmissionConflictError:
                        await session.rollback()
                        return "conflict"

            assert sorted(await asyncio.gather(write(), write())) == ["approved", "conflict"]
            async with sessions() as session:
                decisions = list(
                    await session.scalars(
                        select(CatalogueAdmissionDecisionModel).where(
                            CatalogueAdmissionDecisionModel.curriculum_version_id == curriculum_id
                        )
                    )
                )
                assert len(decisions) == 1
                assert decisions[0].version == 1
        finally:
            await engine.dispose()

    asyncio.run(race())


def test_bare_sql_cannot_create_current_state_or_decisions_without_matching_audit(
    admission_database_url: str,
    admission_client: TestClient,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    initial = review(admission_client, curriculum_id)
    with pytest.raises(IntegrityError):
        execute(
            admission_database_url,
            insert(CatalogueAdmissionCurrentModel).values(
                curriculum_version_id=curriculum_id,
                version=1,
                decision_id=uuid4(),
            ),
        )
    with pytest.raises(IntegrityError):
        execute(
            admission_database_url,
            insert(CatalogueAdmissionDecisionModel).values(
                id=uuid4(),
                curriculum_version_id=curriculum_id,
                version=1,
                state="approved",
                scope_fingerprint=initial["scope_fingerprint"],
                scope_snapshot=initial["scope"],
                educational_approval=True,
                reason="Attempted unaudited educational approval.",
                source_reference="Local register",
                evidence=["Evidence"],
                actor_id=ADMIN.subject_id,
                audit_event_id=uuid4(),
            ),
        )
    assert review(admission_client, curriculum_id)["version"] == 0
    assert curriculum_id not in listed_ids(admission_client)


def test_decisions_and_current_pointer_cannot_be_rewritten_erased_or_rolled_back(
    admission_database_url: str,
    admission_client: TestClient,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    approved = approve(admission_client, curriculum_id)
    approved_id = UUID(approved["latest_decision"]["id"])
    quarantined = admission_client.post(
        admission_path(curriculum_id),
        json=request_body(approved, "quarantined"),
        headers=ADMIN_HEADERS,
    )
    assert quarantined.status_code == 201
    statements = [
        update(CatalogueAdmissionDecisionModel)
        .where(CatalogueAdmissionDecisionModel.id == approved_id)
        .values(reason="Rewritten audit"),
        delete(CatalogueAdmissionDecisionModel).where(
            CatalogueAdmissionDecisionModel.id == approved_id
        ),
        update(CatalogueAdmissionCurrentModel)
        .where(CatalogueAdmissionCurrentModel.curriculum_version_id == curriculum_id)
        .values(decision_id=approved_id, version=1),
        delete(CatalogueAdmissionCurrentModel).where(
            CatalogueAdmissionCurrentModel.curriculum_version_id == curriculum_id
        ),
        text("TRUNCATE catalogue_admission_current"),
        text("TRUNCATE catalogue_admission_decisions CASCADE"),
    ]
    for statement in statements:
        with pytest.raises(IntegrityError):
            execute(admission_database_url, statement)
    assert review(admission_client, curriculum_id)["state"] == "quarantined"
    assert curriculum_id not in listed_ids(admission_client)


def sql_review(
    database_url: str,
    curriculum_id: UUID,
    current: dict[str, Any],
    *,
    changes: dict[str, Any] | None = None,
    mismatched_audit: bool = False,
) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                decision_id, audit_id = uuid4(), uuid4()
                values = {
                    "id": decision_id,
                    "curriculum_version_id": curriculum_id,
                    "version": 1,
                    "state": "approved",
                    "scope_fingerprint": current["scope_fingerprint"],
                    "scope_snapshot": current["scope"],
                    "educational_approval": True,
                    "reason": "Explicit educational review.",
                    "source_reference": "Local review register",
                    "evidence": ["Reviewed curriculum scope."],
                    "actor_id": ADMIN.subject_id,
                    "audit_event_id": audit_id,
                } | (changes or {})
                payload = {
                    key: values[key]
                    for key in (
                        "version",
                        "state",
                        "scope_fingerprint",
                        "scope_snapshot",
                        "educational_approval",
                        "reason",
                        "source_reference",
                        "evidence",
                    )
                } | {"decision_id": str(decision_id), "previous_version": 0}
                session.add(
                    AdminAuditEventModel(
                        id=audit_id,
                        actor_id=uuid4() if mismatched_audit else ADMIN.subject_id,
                        action="catalogue_admission." + values["state"],
                        resource_type="curriculum_admission",
                        resource_id=curriculum_id,
                        payload=payload,
                    )
                )
                await session.flush()
                await session.execute(insert(CatalogueAdmissionDecisionModel).values(**values))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"scope_fingerprint": "sha256:" + "0" * 64},
        {"scope_snapshot": {}},
        {"educational_approval": False},
        {"reason": ""},
        {"source_reference": ""},
        {"evidence": []},
        {"evidence": {}},
        {"evidence": ["Evidence"] * 17},
        {"evidence": ["x" * 1025]},
        {"evidence": ["Hidden\ncontrol"]},
        {"evidence": ["\u00a0Evidence"]},
    ],
)
def test_matching_audit_alone_cannot_bypass_scope_cas_or_evidence_constraints(
    admission_database_url: str,
    admission_client: TestClient,
    changes: dict[str, Any],
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    current = review(admission_client, curriculum_id)
    with pytest.raises(IntegrityError):
        sql_review(admission_database_url, curriculum_id, current, changes=changes)
    assert review(admission_client, curriculum_id)["version"] == 0
    assert curriculum_id not in listed_ids(admission_client)


@pytest.mark.parametrize(
    "title",
    [
        "E2E Curriculum",
        "\uff25\uff12\uff25 Curriculum",
        "Internal curriculum",
        "---",
        "\u200d",
        "\u00a0Mathematics",
    ],
)
def test_bare_sql_approval_also_rejects_unreadable_or_fixture_labels(
    admission_database_url: str,
    admission_client: TestClient,
    title: str,
) -> None:
    curriculum_id = seed_scope(admission_database_url, title=title)
    current = review(admission_client, curriculum_id)
    with pytest.raises(IntegrityError):
        sql_review(admission_database_url, curriculum_id, current)
    assert review(admission_client, curriculum_id)["version"] == 0


def test_audit_actor_must_match_decision_actor(
    admission_database_url: str, admission_client: TestClient
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    current = review(admission_client, curriculum_id)
    with pytest.raises(IntegrityError):
        sql_review(admission_database_url, curriculum_id, current, mismatched_audit=True)
    assert review(admission_client, curriculum_id)["version"] == 0


@asynccontextmanager
async def direct_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


def test_history_reader_paginates_immutable_decisions_in_read_only_transaction(
    admission_database_url: str,
) -> None:
    curriculum_id = seed_scope(admission_database_url)
    empty_id = seed_scope(admission_database_url)

    async def scenario() -> None:
        async with direct_session(admission_database_url) as session:
            current = await get_catalogue_admission(session, curriculum_id)
            original_scope = current.scope
            for state in ("approved", "quarantined", "approved", "rejected"):
                current = await record_catalogue_admission(
                    session,
                    curriculum_id,
                    AdmissionDecisionRequest.model_validate(
                        request_body(current.model_dump(mode="json"), state)
                    ),
                    principal=ADMIN,
                )
                await session.commit()
            before = await session.scalar(select(func.count()).select_from(AdminAuditEventModel))
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            history = await list_catalogue_admission_history(session, curriculum_id)
            assert [(item.version, item.state) for item in history] == [
                (4, "rejected"),
                (3, "approved"),
                (2, "quarantined"),
                (1, "approved"),
            ]
            assert all(item.scope_snapshot == original_scope for item in history)
            assert all(item.actor_id == ADMIN.subject_id for item in history)
            assert all(
                item.source_reference and item.evidence and item.decided_at for item in history
            )
            earlier = await list_catalogue_admission_history(
                session, curriculum_id, before_version=3, limit=1
            )
            assert [item.version for item in earlier] == [2]
            assert [
                item.version
                for item in await list_catalogue_admission_history(session, curriculum_id, limit=0)
            ] == [4]
            assert [
                item.version
                for item in await list_catalogue_admission_history(
                    session, curriculum_id, limit=10000
                )
            ] == [4, 3, 2, 1]
            assert (
                await list_catalogue_admission_history(session, curriculum_id, before_version=1)
                == []
            )
            assert await list_catalogue_admission_history(session, empty_id) == []
            assert (
                await session.scalar(select(func.count()).select_from(AdminAuditEventModel))
                == before
            )

    asyncio.run(scenario())


def test_catalogue_reader_filters_scope_and_pages_without_mutation(
    admission_database_url: str,
) -> None:
    first_id = seed_scope(admission_database_url, title="Arithmetic curriculum")
    second_id = seed_scope(admission_database_url, title="Geometry curriculum")
    unapproved_id = seed_scope(admission_database_url, title="Pending curriculum")

    async def scenario() -> None:
        async with direct_session(admission_database_url) as session:
            first = await get_catalogue_admission(session, first_id)
            parent = await session.get(CurriculumVersionModel, second_id)
            pending = await session.get(CurriculumVersionModel, unapproved_id)
            assert parent is not None
            assert pending is not None
            for curriculum in (parent, pending):
                curriculum.exam_configuration_id = first.scope.exam_configuration_id
                curriculum.medium_id = first.scope.medium_id
                curriculum.subject_id = first.scope.subject_id
                curriculum.code = "GEOMETRY" if curriculum.id == second_id else "PENDING"
            await session.commit()
            for identifier in (first_id, second_id):
                current = await get_catalogue_admission(session, identifier)
                await record_catalogue_admission(
                    session,
                    identifier,
                    AdmissionDecisionRequest.model_validate(
                        request_body(current.model_dump(mode="json"))
                    ),
                    principal=ADMIN,
                )
                await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            entries = await list_material_catalogue(
                session,
                grade=7,
                medium_id=first.scope.medium_id,
                subject_id=first.scope.subject_id,
            )
            assert [entry.curriculum_version_id for entry in entries] == [first_id, second_id]
            assert all(entry.grade_label == "Grade 7" for entry in entries)
            assert all(
                entry.subject_name == "Mathematics" and entry.medium_name == "English"
                for entry in entries
            )
            assert all(entry.exam_configuration_name == "School Grade 7" for entry in entries)
            assert [entry.curriculum_title for entry in entries] == [
                "Arithmetic curriculum",
                "Geometry curriculum",
            ]
            page = await list_material_catalogue(
                session, medium_id=first.scope.medium_id, limit=1, offset=1
            )
            assert [entry.curriculum_version_id for entry in page] == [second_id]
            assert (
                await list_material_catalogue(session, medium_id=first.scope.medium_id, offset=2)
                == []
            )
            assert (
                await list_material_catalogue(session, grade=8, subject_id=first.scope.subject_id)
                == []
            )
            assert await list_material_catalogue(session, medium_id=uuid4()) == []
            assert await list_material_catalogue(session, subject_id=uuid4()) == []
            unapproved = await get_catalogue_admission(session, unapproved_id)
            assert not unapproved.admitted
            assert unapproved.version == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("restore", [False, True])
def test_source_quarantine_commit_conflicts_roll_back_source_and_preserve_catalogue_history(
    admission_database_url: str, restore: bool
) -> None:
    curriculum_id = seed_scope(admission_database_url)

    async def scenario() -> None:
        async with direct_session(admission_database_url) as session:
            current = await get_catalogue_admission(session, curriculum_id)
            admitted = await record_catalogue_admission(
                session,
                curriculum_id,
                AdmissionDecisionRequest.model_validate(
                    request_body(current.model_dump(mode="json"))
                ),
                principal=ADMIN,
            )
            await session.commit()
            source_id, upload_id = uuid4(), uuid4()
            checksum = hashlib.sha256(source_id.bytes).hexdigest()
            session.add(
                SourceDocumentModel(
                    id=source_id,
                    curriculum_version_id=curriculum_id,
                    checksum_sha256=checksum,
                    object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
                    original_filename="Disposable quarantine conflict fixture.pdf",
                    content_type="application/pdf",
                    size_bytes=100,
                    document_type=SourceDocumentType.TEACHER_GUIDE,
                    original_page_count=1,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            session.add(
                AdminAuditEventModel(
                    id=upload_id,
                    resource_type="source_document",
                    resource_id=source_id,
                    action="source_document.uploaded",
                    actor_id=ADMIN.subject_id,
                    payload={"checksum_sha256": checksum},
                )
            )
            await session.commit()
            evidence = FixtureProvenanceEvidence(
                source_document_id=source_id,
                checksum_sha256=checksum,
                upload_audit_event_id=upload_id,
                fixture_reference="Disposable quarantine rollback integration factory",
                observed_evidence=(
                    "Exact factory-generated source and immutable upload audit match",
                ),
            )
            service = SourceDocumentService(
                session, cast(ObjectStorage, object()), max_upload_bytes=1024
            )
            if restore:
                await service.change_fixture_quarantine(
                    source_id,
                    quarantined=True,
                    principal=ADMIN,
                    expected_version=0,
                    reason="Exact fixture provenance reviewed",
                    confirmation="quarantine_exact_source_fixture",
                    provenance_evidence=evidence,
                )
            version = int(restore)
            session.add(
                AdminAuditEventModel(
                    id=uuid4(),
                    actor_id=ADMIN.subject_id,
                    resource_type="source_document",
                    resource_id=source_id,
                    action="source_document.fixture_restored"
                    if restore
                    else "source_document.fixture_quarantined",
                    payload={"version": version + 1},
                )
            )
            await session.commit()
            source_query = text("SELECT to_jsonb(d) FROM source_documents d WHERE id=:id")
            before = await session.scalar(source_query, {"id": source_id})
            count_query = (
                select(func.count())
                .select_from(AdminAuditEventModel)
                .where(
                    AdminAuditEventModel.resource_type == "source_document",
                    AdminAuditEventModel.resource_id == source_id,
                )
            )
            audits = await session.scalar(count_query)
            with pytest.raises(FixtureQuarantineConflictError) as rejected:
                await service.change_fixture_quarantine(
                    source_id,
                    quarantined=not restore,
                    principal=ADMIN,
                    expected_version=version,
                    reason="Exact fixture provenance reviewed",
                    confirmation="restore_exact_source_fixture"
                    if restore
                    else "quarantine_exact_source_fixture",
                    provenance_evidence=evidence,
                )
            assert rejected.value.__cause__ is None
            assert rejected.value.__suppress_context__
            assert await session.scalar(source_query, {"id": source_id}) == before
            assert await session.scalar(count_query) == audits
            assert (
                await get_catalogue_admission(session, curriculum_id)
            ).model_dump() == admitted.model_dump()
            document = await session.get(SourceDocumentModel, source_id)
            assert document is not None
            assert document.quarantined_for_teacher_use is restore
            assert document.active_for_ai is (not restore)
            assert document.metadata_scope_version == version
            assert document.checksum_sha256 == checksum
            assert document.original_page_count == 1
            assert document.extraction_status.value == "uploaded"

    asyncio.run(scenario())
