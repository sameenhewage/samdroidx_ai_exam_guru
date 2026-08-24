import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.analytics.models import AnalyticsRunModel
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.infrastructure.migrations import assert_database_schema_current, upgrade_database
from exam_guru_api.knowledge.domain import DifficultyLabel, QuestionType, ReviewState
from exam_guru_api.knowledge.models import HistoricalQuestionModel
from exam_guru_api.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ADMIN_ID = UUID(int=910_001)
REVIEWER_ID = UUID(int=910_002)
CURRICULUM_ID = UUID(int=910_010)
OTHER_CURRICULUM_ID = UUID(int=910_011)
COMPETENCY_ID = UUID(int=910_020)
SKILL_A = UUID(int=910_021)
SKILL_B = UUID(int=910_022)
PATH = f"/api/v1/admin/curricula/{CURRICULUM_ID}/analytics/runs"
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(ADMIN_ID, frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(REVIEWER_ID, frozenset({AdminRole.REVIEWER}))
        if access_token == "no-role-token":
            return Principal(UUID(int=910_003), frozenset())
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class DatabaseResources:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        await self.engine.dispose()


def api_client(database_url: str) -> TestClient:
    return TestClient(
        create_app(
            identity_provider=StaticIdentityProvider(),
            resource_factory=lambda _: DatabaseResources(database_url),
        )
    )


async def seed_curriculum(
    session: AsyncSession,
    *,
    curriculum_id: UUID,
    offset: int,
    competency_id: UUID,
    skill_ids: tuple[UUID, ...],
) -> None:
    exam_id = UUID(int=911_000 + offset)
    medium_id = UUID(int=912_000 + offset)
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code=f"G5A-{offset}",
                name="Grade 5 Scholarship Examination",
                grade=5,
                active=True,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            ),
            MediumModel(
                id=medium_id,
                code=f"an{offset}",
                name=f"Analytics medium {offset}",
                active=True,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            ),
        ]
    )
    await session.flush()
    session.add(
        CurriculumVersionModel(
            id=curriculum_id,
            exam_configuration_id=exam_id,
            medium_id=medium_id,
            code=f"AN-{offset}",
            title=f"Analytics curriculum {offset}",
            active=True,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    session.add(
        TaxonomyNodeModel.from_domain(
            TaxonomyNode(
                competency_id,
                curriculum_id,
                TaxonomyLevel.COMPETENCY,
                "C1",
                "Number competency",
                review_state=TaxonomyReviewState.REVIEWED,
            ),
            ADMIN_ID,
        )
    )
    await session.flush()
    for index, skill_id in enumerate(skill_ids, start=1):
        session.add(
            TaxonomyNodeModel.from_domain(
                TaxonomyNode(
                    skill_id,
                    curriculum_id,
                    TaxonomyLevel.SKILL,
                    f"S{index}",
                    f"Skill {index}",
                    parent_id=competency_id,
                    review_state=TaxonomyReviewState.REVIEWED,
                ),
                ADMIN_ID,
            )
        )
        await session.flush()


async def seed_paper(
    session: AsyncSession,
    *,
    curriculum_id: UUID,
    competency_id: UUID,
    skill_ids: tuple[UUID, ...],
    year: int,
    offset: int,
    include_incomplete: bool = False,
) -> tuple[UUID, ...]:
    document_id = UUID(int=920_000 + offset)
    page_id = UUID(int=930_000 + offset)
    block_id = UUID(int=940_000 + offset)
    text = f"Reviewed historical paper {year}"
    checksum = sha256(f"analytics-source-{curriculum_id}-{year}".encode()).hexdigest()
    now = datetime.now(UTC)
    document = SourceDocumentModel(
        id=document_id,
        checksum_sha256=checksum,
        object_key=f"sources/analytics-{offset}-{year}.pdf",
        original_filename=f"analytics-{year}.pdf",
        content_type="application/pdf",
        size_bytes=1_000,
        document_type=SourceDocumentType.PAST_PAPER,
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        curriculum_version_id=curriculum_id,
        year=year,
        paper_code=f"P{year}",
        extraction_attempt_count=1,
        extraction_started_at=now,
        created_by=ADMIN_ID,
        updated_by=ADMIN_ID,
    )
    session.add(document)
    await session.flush()
    session.add(
        SourcePageModel(
            id=page_id,
            source_document_id=document_id,
            page_number=1,
            extractor="fixture",
            extractor_version="v1",
            raw_text=text,
            reviewed_text=text,
            character_count=len(text),
            block_count=1,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    session.add(
        ExtractedBlockModel(
            id=block_id,
            source_page_id=page_id,
            source_document_id=document_id,
            page_number=1,
            reading_order=0,
            extractor="fixture",
            extractor_version="v1",
            bbox_x0=0.0,
            bbox_y0=0.0,
            bbox_x1=1.0,
            bbox_y1=1.0,
            raw_text=text,
            reviewed_text=text,
            character_count=len(text),
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    document.extraction_status = ExtractionStatus.EXTRACTED
    document.extractor = "fixture"
    document.extractor_version = "v1"
    document.extracted_page_count = 1
    document.extracted_block_count = 1
    document.extracted_character_count = len(text)
    document.native_text_page_ratio = 1.0
    document.needs_ocr = False
    document.extraction_completed_at = now
    await session.flush()
    document.extraction_status = ExtractionStatus.IN_REVIEW
    await session.flush()
    document.extraction_status = ExtractionStatus.TRUSTED
    await session.flush()

    question_ids: list[UUID] = []
    for index, skill_id in enumerate(skill_ids, start=1):
        question_id = UUID(int=950_000 + offset * 10 + index)
        question_ids.append(question_id)
        session.add(
            HistoricalQuestionModel(
                id=question_id,
                curriculum_version_id=curriculum_id,
                year=year,
                paper_code=f"P{year}",
                question_number=str(index),
                text=f"Reviewed question {year}/{index}",
                question_type=QuestionType.MULTIPLE_CHOICE,
                marks=9 if index == 1 else 1,
                difficulty_label=DifficultyLabel.MEDIUM,
                difficulty_confidence=0.9,
                difficulty_source="reviewer_confirmed",
                source_document_id=document_id,
                page_number=1,
                source_block_id=block_id,
                review_state=ReviewState.REVIEWED,
                competency_id=competency_id,
                skill_id=skill_id,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            )
        )
    if include_incomplete:
        session.add_all(
            [
                HistoricalQuestionModel(
                    id=UUID(int=950_000 + offset * 10 + 3),
                    curriculum_version_id=curriculum_id,
                    year=year,
                    paper_code=f"P{year}",
                    question_number="incomplete",
                    text="Reviewed but missing difficulty evidence",
                    question_type=QuestionType.SHORT_ANSWER,
                    marks=1,
                    source_document_id=document_id,
                    page_number=1,
                    source_block_id=block_id,
                    review_state=ReviewState.REVIEWED,
                    competency_id=competency_id,
                    skill_id=skill_ids[0],
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                ),
                HistoricalQuestionModel(
                    id=UUID(int=950_000 + offset * 10 + 4),
                    curriculum_version_id=curriculum_id,
                    year=year,
                    paper_code=f"P{year}",
                    question_number="draft",
                    text="Complete metadata but not reviewed",
                    question_type=QuestionType.SHORT_ANSWER,
                    marks=1,
                    difficulty_label=DifficultyLabel.EASY,
                    difficulty_confidence=0.8,
                    difficulty_source="reviewer_confirmed",
                    source_document_id=document_id,
                    page_number=1,
                    source_block_id=block_id,
                    review_state=ReviewState.DRAFT,
                    competency_id=competency_id,
                    skill_id=skill_ids[0],
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                ),
            ]
        )
    await session.flush()
    return tuple(question_ids)


@pytest.fixture(scope="module")
def analytics_database_url() -> Iterator[str]:
    credentials = ("exam_guru", "analytics-only")
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_analytics_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        assert_database_schema_current(database_url)

        async def seed() -> None:
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                await seed_curriculum(
                    session,
                    curriculum_id=CURRICULUM_ID,
                    offset=1,
                    competency_id=COMPETENCY_ID,
                    skill_ids=(SKILL_A, SKILL_B),
                )
                included_ids: list[UUID] = []
                for offset, year in enumerate(range(2018, 2022), start=1):
                    included_ids.extend(
                        await seed_paper(
                            session,
                            curriculum_id=CURRICULUM_ID,
                            competency_id=COMPETENCY_ID,
                            skill_ids=(SKILL_A, SKILL_B),
                            year=year,
                            offset=offset,
                            include_incomplete=year == 2021,
                        )
                    )
                other_competency = UUID(int=910_030)
                other_skill = UUID(int=910_031)
                await seed_curriculum(
                    session,
                    curriculum_id=OTHER_CURRICULUM_ID,
                    offset=2,
                    competency_id=other_competency,
                    skill_ids=(other_skill,),
                )
                await seed_paper(
                    session,
                    curriculum_id=OTHER_CURRICULUM_ID,
                    competency_id=other_competency,
                    skill_ids=(other_skill,),
                    year=2021,
                    offset=20,
                )
                await session.commit()
            await engine.dispose()

        asyncio.run(seed())
        yield database_url


@pytest.mark.integration
def test_analytics_api_persists_idempotent_exact_visible_backtest_with_auth_and_audit(
    analytics_database_url: str,
) -> None:
    request = {
        "minimum_training_years": 2,
        "top_k_skills": 1,
        "meaningful_improvement": {"numerator": 1, "denominator": 100},
    }
    with api_client(analytics_database_url) as client:
        unauthenticated = client.post(PATH, json=request)
        forbidden = client.post(PATH, json=request, headers=REVIEWER_HEADERS)
        created = client.post(PATH, json=request, headers=ADMIN_HEADERS)
        duplicate = client.post(PATH, json=request, headers=ADMIN_HEADERS)
        listed = client.get(PATH, headers=REVIEWER_HEADERS)
        fetched = client.get(f"{PATH}/{created.json()['id']}", headers=REVIEWER_HEADERS)
        no_role_read = client.get(
            f"{PATH}/{created.json()['id']}",
            headers={"Authorization": "Bearer no-role-token"},
        )
        unbounded = client.get(PATH, params={"limit": 101}, headers=REVIEWER_HEADERS)
        cross_path = (
            f"/api/v1/admin/curricula/{OTHER_CURRICULUM_ID}/analytics/runs/{created.json()['id']}"
        )
        cross_curriculum = client.get(cross_path, headers=REVIEWER_HEADERS)
        other_run = client.post(
            f"/api/v1/admin/curricula/{OTHER_CURRICULUM_ID}/analytics/runs",
            json=request,
            headers=ADMIN_HEADERS,
        )
        unknown_run = client.post(
            f"/api/v1/admin/curricula/{UUID(int=999_999_999)}/analytics/runs",
            json=request,
            headers=ADMIN_HEADERS,
        )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert no_role_read.status_code == 403
    assert unbounded.status_code == 422
    assert created.status_code == 201
    assert duplicate.status_code == 200
    body = created.json()
    assert duplicate.json()["id"] == body["id"]
    assert duplicate.json()["deduplicated"] is True
    assert body["deduplicated"] is False
    assert 0 <= body["compute_duration_ms"] < 2_000
    assert body["data_quality"]["considered_count"] == 10
    assert body["data_quality"]["included_count"] == 8
    assert body["data_quality"]["excluded_count"] == 2
    reasons = {item["reason"]: item["count"] for item in body["data_quality"]["exclusions"]}
    assert reasons == {"incomplete_difficulty_evidence": 1, "not_reviewed": 1}
    assert len(body["input"]["observation_ids"]) == 8
    assert all(source["source_version"].startswith("sha256:") for source in body["sources"])
    assert body["versions"] == {
        "statistics": "historical-distributions-v1",
        "practice_priority": "deterministic-practice-priority-v1",
        "baseline": "syllabus-balanced-baseline-v1",
        "backtest": "rolling-heldout-backtest-v1",
    }
    backtest = body["result"]["backtest"]
    assert backtest["aggregate"]["mean_baseline_score"] == {
        "numerator": 13,
        "denominator": 15,
    }
    assert all(window["leakage_audit"]["passed"] for window in backtest["windows"])
    assert all(
        set(window["leakage_audit"]["training_observation_ids"]).isdisjoint(
            window["leakage_audit"]["heldout_observation_ids"]
        )
        for window in backtest["windows"]
    )
    assert backtest["windows"][0]["baseline_run"]["algorithm_version"] == (
        "syllabus-balanced-baseline-v1"
    )
    assert "future exam certainty" in " ".join(backtest["limitations"])
    assert "practice" in backtest["recommendation"]["language"].casefold()
    assert fetched.status_code == 200
    assert fetched.json() == {**body, "deduplicated": False}
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]
    assert listed.json()[0]["baseline_algorithm_version"] == "syllabus-balanced-baseline-v1"
    assert cross_curriculum.status_code == 404
    assert cross_curriculum.json() == {"detail": {"code": "analytics_run_not_found"}}
    assert other_run.status_code == 422
    assert other_run.json()["detail"] == {
        "code": "analytics_insufficient_history",
        "required_year_count": 3,
        "available_years": [2021],
        "data_quality": {
            "considered_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "exclusions": [],
        },
    }
    assert unknown_run.status_code == 404
    assert unknown_run.json() == {"detail": {"code": "curriculum_version_not_found"}}

    async def inspect() -> None:
        engine = create_async_engine(analytics_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(AnalyticsRunModel)) == 1
            audits = tuple(
                await session.scalars(
                    select(AdminAuditEventModel).where(
                        AdminAuditEventModel.action == "analytics.run.created"
                    )
                )
            )
            assert len(audits) == 1
            assert audits[0].resource_id == UUID(body["id"])
            assert audits[0].actor_id == ADMIN_ID

            async def mutate_run() -> None:
                await session.execute(
                    update(AnalyticsRunModel)
                    .where(AnalyticsRunModel.id == UUID(body["id"]))
                    .values(result_fingerprint="sha256:" + "0" * 64)
                )
                await session.flush()

            with pytest.raises(IntegrityError):
                await mutate_run()
            await session.rollback()

            async def remove_run() -> None:
                await session.execute(
                    delete(AnalyticsRunModel).where(AnalyticsRunModel.id == UUID(body["id"]))
                )
                await session.flush()

            with pytest.raises(IntegrityError):
                await remove_run()
            await session.rollback()
        await engine.dispose()

    asyncio.run(inspect())
