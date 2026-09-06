import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.teacher_papers import router
from exam_guru_api.auth.rate_limits import NoOpRateLimiter
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    CurriculumNotAdmittedError,
    get_catalogue_admission,
    record_catalogue_admission,
)
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumLessonTaxonomyMappingModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.generation.runtime import create_generation_runtime
from exam_guru_api.teacher_papers.jobs import DeterministicPaperGenerationDispatcher
from exam_guru_api.teacher_papers.models import TeacherPaperJobModel
from exam_guru_api.teacher_papers.repository import StoredTeacherPaperInsert, TeacherPaperRepository
from exam_guru_api.teacher_papers.schemas import TeacherPaperJobCreateRequest
from exam_guru_api.teacher_papers.service import TeacherPaperJobService
from tests.integration.test_catalogue_admission_postgres import (
    ADMIN,
    ADMIN_HEADERS,
    StaticIdentityProvider,
)
from tests.integration.test_catalogue_admission_postgres import (
    admission_database_url as admission_database_url,
)

pytestmark = pytest.mark.integration
BASE = "/api/v1/admin/paper-generation"
EVIDENCE = "Disposable synthetic workflow fixture, NOT real educational approval"


@dataclass(frozen=True)
class Scope:
    curriculum_id: UUID
    medium_id: UUID
    subject_id: UUID
    medium: str
    subject: str
    grade: int
    unit_id: UUID
    lesson_ids: tuple[UUID, ...]

    @property
    def query(self) -> dict[str, str | int]:
        return {"grade": self.grade, "medium": self.medium, "subject": self.subject}


@pytest.fixture
def teacher_client(admission_database_url: str) -> Iterator[TestClient]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(admission_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def database_session() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_database_session] = database_session
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.identity_provider = StaticIdentityProvider()
    app.state.rate_limiter = NoOpRateLimiter()
    app.state.paper_generation_dispatcher = DeterministicPaperGenerationDispatcher()
    app.state.generation_runtime_registry = create_generation_runtime(Settings(environment="test"))
    app.include_router(router, prefix=BASE)
    with TestClient(app) as client:
        yield client


def seed_scope(database_url: str, *, grade: int = 7, fixture_labels: bool = False) -> Scope:
    async def seed() -> Scope:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                suffix = uuid4().hex[:12]
                exam = ExamConfigurationModel(
                    id=uuid4(),
                    code=f"SCHOOL-{suffix}".upper(),
                    name=f"School Grade {grade}",
                    grade=grade,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                medium = MediumModel(
                    id=uuid4(),
                    code=f"m{suffix}",
                    name="Blueprint medium 712 private fixture" if fixture_labels else "English",
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                subject = SubjectModel(
                    id=uuid4(),
                    code=f"MATH-{suffix}".upper(),
                    name="Mathematics",
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                session.add_all([exam, medium, subject])
                await session.flush()
                curriculum = CurriculumVersionModel(
                    id=uuid4(),
                    exam_configuration_id=exam.id,
                    medium_id=medium.id,
                    subject_id=subject.id,
                    code="2026",
                    title=f"Grade {grade} Mathematics",
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                session.add(curriculum)
                await session.flush()
                unit = CurriculumUnitModel(
                    id=uuid4(),
                    curriculum_version_id=curriculum.id,
                    code="NUMBERS",
                    title="Numbers",
                    ordinal=1,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                competency = TaxonomyNodeModel(
                    id=uuid4(),
                    curriculum_version_id=curriculum.id,
                    parent_id=None,
                    level=TaxonomyLevel.COMPETENCY,
                    code="NUMBER",
                    title="Number skills",
                    review_state=TaxonomyReviewState.REVIEWED,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                session.add_all([unit, competency])
                await session.flush()
                lesson_ids = tuple(uuid4() for _ in range(3))
                for ordinal, (lesson_id, title) in enumerate(
                    zip(lesson_ids, ("Whole numbers", "Factors", "Fractions"), strict=True), 1
                ):
                    session.add(
                        CurriculumLessonModel(
                            id=lesson_id,
                            curriculum_version_id=curriculum.id,
                            unit_id=unit.id,
                            code=f"LESSON-{ordinal}",
                            title=title,
                            ordinal=ordinal,
                            active=True,
                            created_by=ADMIN.subject_id,
                            updated_by=ADMIN.subject_id,
                        )
                    )
                await session.flush()
                session.add_all(
                    [
                        CurriculumLessonTaxonomyMappingModel(
                            lesson_id=lesson_id,
                            curriculum_version_id=curriculum.id,
                            unit_id=unit.id,
                            taxonomy_node_id=competency.id,
                            created_by=ADMIN.subject_id,
                        )
                        for lesson_id in lesson_ids
                    ]
                )
                await session.commit()
                return Scope(
                    curriculum.id,
                    medium.id,
                    subject.id,
                    medium.code,
                    subject.code,
                    grade,
                    unit.id,
                    lesson_ids,
                )
        finally:
            await engine.dispose()

    return asyncio.run(seed())


def decide(database_url: str, scope: Scope, *, revoke: bool = False) -> None:
    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                review = await get_catalogue_admission(session, scope.curriculum_id)
                await record_catalogue_admission(
                    session,
                    scope.curriculum_id,
                    AdmissionDecisionRequest(
                        state="quarantined" if revoke else "approved",
                        expected_version=review.version,
                        expected_scope_fingerprint=review.scope_fingerprint,
                        educational_approval=not revoke,
                        reason=EVIDENCE,
                        source_reference=EVIDENCE,
                        evidence=(EVIDENCE,),
                    ),
                    principal=ADMIN,
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


def payload(scope: Scope, fingerprint: str) -> dict[str, object]:
    return {
        "target": {**scope.query, "paper_type": "subject_practice"},
        "source_scope_fingerprint": fingerprint,
        "scope": {"kind": "lesson_range", "start_lesson": 1, "end_lesson": 3},
        "settings": {
            "paper_name": f"Grade {scope.grade} Mathematics practice",
            "mcq_count": 3,
            "written_count": 0,
            "structured_count": 0,
            "duration_minutes": 45,
            "difficulty": "balanced",
        },
    }


def options(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{BASE}/options", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def lessons(client: TestClient, scope: Scope) -> dict[str, Any]:
    response = client.get(f"{BASE}/lessons", params=scope.query, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_unadmitted_catalogue_is_empty_without_exposing_private_configuration_labels(
    admission_database_url: str,
    teacher_client: TestClient,
) -> None:
    scope = seed_scope(admission_database_url, grade=5, fixture_labels=True)
    data = options(teacher_client)
    assert data["media"] == []
    assert data["subjects"] == []
    assert data["grades"] == []
    assert data["paper_types"] == []
    assert data["scholarship_modes"] == []
    assert "Blueprint medium 712 private fixture" not in str(data)
    response = teacher_client.get(f"{BASE}/curricula", params=scope.query, headers=ADMIN_HEADERS)
    assert response.json() == {"items": []}
    assert (
        teacher_client.get(f"{BASE}/lessons", params=scope.query, headers=ADMIN_HEADERS).status_code
        == 404
    )


@pytest.mark.parametrize("unavailable", ["unreviewed", "revoked", "stale", "inactive"])
def test_catalogue_and_preview_exclude_each_unavailable_scope(
    admission_database_url: str,
    teacher_client: TestClient,
    unavailable: str,
) -> None:
    scope = seed_scope(admission_database_url, grade=5)
    if unavailable != "unreviewed":
        decide(admission_database_url, scope)
    if unavailable == "revoked":
        decide(admission_database_url, scope, revoke=True)
    if unavailable in {"stale", "inactive"}:

        async def change() -> None:
            engine = create_async_engine(admission_database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    await session.execute(
                        update(SubjectModel)
                        .where(SubjectModel.id == scope.subject_id)
                        .values(
                            **(
                                {"name": "Mathematics revised"}
                                if unavailable == "stale"
                                else {"active": False}
                            )
                        )
                    )
                    await session.commit()
            finally:
                await engine.dispose()

        asyncio.run(change())
    data = options(teacher_client)
    assert all(item["code"] != scope.medium for item in data["media"])
    assert all(item["code"] != scope.subject for item in data["subjects"])
    assert teacher_client.get(
        f"{BASE}/curricula", params=scope.query, headers=ADMIN_HEADERS
    ).json() == {"items": []}
    assert (
        teacher_client.get(f"{BASE}/lessons", params=scope.query, headers=ADMIN_HEADERS).status_code
        == 404
    )
    response = teacher_client.post(
        f"{BASE}/jobs",
        json=payload(scope, "sha256:" + "0" * 64),
        headers={**ADMIN_HEADERS, "Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "paper_generation_curriculum_not_found"


def test_admitted_grade_seven_math_has_bound_readable_options_and_creates_exact_scope(
    admission_database_url: str,
    teacher_client: TestClient,
) -> None:
    scope = seed_scope(admission_database_url)
    decide(admission_database_url, scope)
    data = options(teacher_client)
    assert 7 in data["grades"]
    assert next(item for item in data["media"] if item["code"] == scope.medium) == {
        "code": scope.medium,
        "label": "English",
        "grades": [7],
    }
    subject = next(item for item in data["subjects"] if item["code"] == scope.subject)
    assert subject["grade"] == 7
    assert subject["medium"] == scope.medium
    assert subject["label"] == "Mathematics"
    assert subject["units"] == [{"code": "NUMBERS", "label": "Numbers"}]
    assert [lesson["number"] for lesson in subject["lessons"]] == [1, 2, 3]
    preview = lessons(teacher_client, scope)
    assert subject["curriculum"] == preview["curriculum"]
    assert subject["lessons"] == preview["lessons"]
    assert [item["code"] for item in data["paper_types"] if item["medium"] == scope.medium] == [
        "subject_practice"
    ]
    response = teacher_client.post(
        f"{BASE}/jobs",
        json=payload(scope, preview["curriculum"]["source_scope_fingerprint"]),
        headers={**ADMIN_HEADERS, "Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    assert response.json()["grade"] == 7
    assert response.json()["scope_summary"] == "Lessons 1\u20133"


def test_revoked_selection_is_rejected_at_application_boundary_without_creating_a_job(
    admission_database_url: str,
    teacher_client: TestClient,
) -> None:
    scope = seed_scope(admission_database_url)
    decide(admission_database_url, scope)
    preview = lessons(teacher_client, scope)
    request = payload(scope, preview["curriculum"]["source_scope_fingerprint"])
    decide(admission_database_url, scope, revoke=True)

    async def create() -> None:
        engine = create_async_engine(admission_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                before = await session.scalar(
                    select(func.count()).select_from(TeacherPaperJobModel)
                )
                service = TeacherPaperJobService(
                    session,
                    DeterministicPaperGenerationDispatcher(),
                    create_generation_runtime(Settings(environment="test")),
                )
                with pytest.raises(LookupError):
                    await service.create(
                        TeacherPaperJobCreateRequest.model_validate(request),
                        idempotency_key=uuid4().hex,
                        principal=ADMIN,
                    )
                assert (
                    await session.scalar(select(func.count()).select_from(TeacherPaperJobModel))
                    == before
                )
        finally:
            await engine.dispose()

    asyncio.run(create())


def test_readmitted_scope_and_changed_lesson_order_require_fresh_selection(
    admission_database_url: str,
    teacher_client: TestClient,
) -> None:
    scope = seed_scope(admission_database_url)
    decide(admission_database_url, scope)
    original = lessons(teacher_client, scope)["curriculum"]["source_scope_fingerprint"]
    decide(admission_database_url, scope, revoke=True)
    decide(admission_database_url, scope)
    refreshed = lessons(teacher_client, scope)["curriculum"]["source_scope_fingerprint"]
    assert refreshed != original
    response = teacher_client.post(
        f"{BASE}/jobs",
        json=payload(scope, original),
        headers={**ADMIN_HEADERS, "Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "paper_generation_catalogue_changed"

    async def change() -> None:
        engine = create_async_engine(admission_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await session.execute(
                    update(CurriculumLessonModel)
                    .where(CurriculumLessonModel.id == scope.lesson_ids[0])
                    .values(active=False)
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(change())
    response = teacher_client.post(
        f"{BASE}/jobs",
        json=payload(scope, refreshed),
        headers={**ADMIN_HEADERS, "Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "paper_generation_catalogue_changed"
    assert [lesson["label"] for lesson in lessons(teacher_client, scope)["lessons"]] == [
        "Lesson 1 — Factors",
        "Lesson 2 — Fractions",
    ]


async def wait_for_lock(session: AsyncSession, waiter: int, blocker: int) -> None:
    async with asyncio.timeout(5):
        for _ in range(500):
            if await session.scalar(
                text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
                {"blocker": blocker, "waiter": waiter},
            ):
                return
            await asyncio.sleep(0.01)
    raise AssertionError("The expected PostgreSQL admission lock was not observed")


@pytest.mark.parametrize("revocation_first", [False, True])
def test_creation_and_revocation_serialize_on_current_admission(
    admission_database_url: str,
    teacher_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    revocation_first: bool,
) -> None:
    scope = seed_scope(admission_database_url)
    decide(admission_database_url, scope)
    request = TeacherPaperJobCreateRequest.model_validate(
        payload(scope, lessons(teacher_client, scope)["curriculum"]["source_scope_fingerprint"])
    )

    async def race() -> None:
        engine = create_async_engine(admission_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        ready, finish = asyncio.Event(), asyncio.Event()
        original_insert = TeacherPaperRepository.insert_job

        async def paused_insert(
            repository: TeacherPaperRepository, values: dict[str, object]
        ) -> StoredTeacherPaperInsert:
            ready.set()
            await finish.wait()
            return await original_insert(repository, values)

        if not revocation_first:
            monkeypatch.setattr(TeacherPaperRepository, "insert_job", paused_insert)
        try:
            async with sessions() as creator, sessions() as revoker, sessions() as observer:
                creator_pid = await creator.scalar(select(func.pg_backend_pid()))
                revoker_pid = await revoker.scalar(select(func.pg_backend_pid()))
                assert isinstance(creator_pid, int)
                assert isinstance(revoker_pid, int)
                current = await get_catalogue_admission(revoker, scope.curriculum_id)
                decision = AdmissionDecisionRequest(
                    state="quarantined",
                    expected_version=current.version,
                    expected_scope_fingerprint=current.scope_fingerprint,
                    educational_approval=False,
                    reason=EVIDENCE,
                    source_reference=EVIDENCE,
                    evidence=(EVIDENCE,),
                )
                service = TeacherPaperJobService(
                    creator,
                    DeterministicPaperGenerationDispatcher(),
                    create_generation_runtime(Settings(environment="test")),
                )
                if revocation_first:
                    await record_catalogue_admission(
                        revoker, scope.curriculum_id, decision, principal=ADMIN
                    )
                    creation = asyncio.create_task(
                        service.create(request, idempotency_key=uuid4().hex, principal=ADMIN)
                    )
                    try:
                        await wait_for_lock(observer, creator_pid, revoker_pid)
                        await revoker.commit()
                        with pytest.raises(CurriculumNotAdmittedError):
                            await creation
                    finally:
                        await revoker.rollback()
                        if not creation.done():
                            creation.cancel()
                            await asyncio.gather(creation, return_exceptions=True)
                else:
                    creation = asyncio.create_task(
                        service.create(request, idempotency_key=uuid4().hex, principal=ADMIN)
                    )
                    revocation = None
                    try:
                        await asyncio.wait_for(ready.wait(), timeout=5)
                        revocation = asyncio.create_task(
                            record_catalogue_admission(
                                revoker, scope.curriculum_id, decision, principal=ADMIN
                            )
                        )
                        await wait_for_lock(observer, revoker_pid, creator_pid)
                        finish.set()
                        result = await creation
                        assert result.record.job.curriculum_version_id == scope.curriculum_id
                        await revocation
                        await revoker.commit()
                    finally:
                        finish.set()
                        for task in (creation, revocation):
                            if task is not None and not task.done():
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                await creator.rollback()
                count = await observer.scalar(
                    select(func.count())
                    .select_from(TeacherPaperJobModel)
                    .where(TeacherPaperJobModel.curriculum_version_id == scope.curriculum_id)
                )
                assert count == (0 if revocation_first else 1)
        finally:
            await engine.dispose()

    asyncio.run(race())
    assert teacher_client.get(
        f"{BASE}/curricula", params=scope.query, headers=ADMIN_HEADERS
    ).json() == {"items": []}
