import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from threading import Barrier
from typing import cast
from uuid import UUID

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.auth.rate_limits import (
    NoOpRateLimiter,
    RateLimitDecision,
    RateLimiter,
    RateLimitScope,
)
from exam_guru_api.core.config import Settings
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
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.generation.domain import GenerationRequest, GenerationResult
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationJobModel, GenerationRunModel
from exam_guru_api.generation.ports import ProviderError, ProviderFailureCode
from exam_guru_api.generation.run_service import GenerationWorkerService
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry, create_generation_runtime
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
    upgrade_database,
)
from exam_guru_api.knowledge.domain import ChunkType, ReviewState
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.main import create_app
from exam_guru_api.retrieval.embeddings import (
    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
    create_embedding_provider_registry,
)
from exam_guru_api.subject_quality.models import (
    SubjectQualityEvalCaseVersionModel,
    SubjectQualityEvalResultModel,
    SubjectQualityEvalRunModel,
    SubjectQualityFeedbackModel,
)
from exam_guru_api.subject_quality.service import (
    SubjectQualityFeedbackPersistenceError,
    SubjectQualityFeedbackService,
)
from exam_guru_api.teacher_papers.jobs import DeterministicPaperGenerationDispatcher
from exam_guru_api.teacher_papers.models import (
    TeacherPaperJobModel,
    TeacherPaperMarkingConfirmationModel,
    TeacherPaperSlotModel,
    TeacherPaperSlotRunModel,
)
from exam_guru_api.teacher_papers.service import (
    TeacherPaperRecoveryService,
    TeacherPaperWorkerService,
)
from exam_guru_api.validation.domain import (
    FindingEvidence,
    FindingStatus,
    ValidationFinding,
    ValidationInput,
)
from exam_guru_api.validation.models import ValidationFindingModel
from exam_guru_api.validation.pipeline import ValidationPipeline, build_default_pipeline

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
VALKEY_IMAGE = "valkey/valkey:9.1.1-alpine3.24"
ADMIN_ID = UUID(int=25_100_001)
REVIEWER_ID = UUID(int=25_100_002)
EXAM_ID = UUID(int=25_100_003)
MEDIUM_ID = UUID(int=25_100_004)
SUBJECT_ID = UUID(int=25_100_005)
CURRICULUM_ID = UUID(int=25_100_006)
UNIT_ID = UUID(int=25_100_007)
COMPETENCY_ID = UUID(int=25_100_008)
LESSON_IDS = tuple(UUID(int=25_101_000 + index) for index in range(1, 4))
SKILL_IDS = tuple(UUID(int=25_102_000 + index) for index in range(1, 4))
CHUNK_IDS = tuple(UUID(int=25_103_000 + index) for index in range(1, 4))
EMBEDDING_CONFIGURATION_ID = UUID(int=25_104_001)
ADMIN_HEADERS = {"Authorization": "Bearer admin-token", "Idempotency-Key": "paper-key-1"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}


@dataclass(frozen=True, slots=True)
class Seed:
    database_url: str
    valkey_url: str

    @property
    def settings(self) -> Settings:
        return Settings(
            environment="test",
            database_url=SecretStr(self.database_url),
            valkey_url=SecretStr(self.valkey_url),
        )


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(ADMIN_ID, frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(REVIEWER_ID, frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class DenyGenerationRateLimiter:
    async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision:
        assert principal_id in {ADMIN_ID, REVIEWER_ID}
        assert scope is RateLimitScope.GENERATION_CREATE_RETRY
        return RateLimitDecision(allowed=False, retry_after_seconds=17)


class UnavailableGenerationProvider:
    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise ProviderError(ProviderFailureCode.UNAVAILABLE, identity=request.identity)


class AlwaysFailValidator:
    validator_id = "aggregate-test-fail"
    validator_version = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        return (
            ValidationFinding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code="subject.math.answer_mismatch",
                status=FindingStatus.FAIL,
                message="Deterministic fixture answer mismatch.",
                evidence=(
                    FindingEvidence(
                        location="$.candidate.answer",
                        expected="fixture-correct",
                        observed="fixture-wrong",
                    ),
                ),
            ),
        )


def request_payload(
    *,
    scope: dict[str, object] | None = None,
    question_count: int = 3,
    subject: str = "MATHEMATICS",
) -> dict[str, object]:
    return {
        "target": {
            "grade": 5,
            "medium": "si",
            "paper_type": "subject_practice",
            "subject": subject,
        },
        "scope": scope or {"kind": "lesson_range", "start_lesson": 1, "end_lesson": 3},
        "settings": {
            "paper_name": "Grade 5 Mathematics practice",
            "mcq_count": question_count,
            "written_count": 0,
            "structured_count": 0,
            "duration_minutes": 45,
            "difficulty": "balanced",
        },
    }


@contextmanager
def api_client(
    seed: Seed,
    paper_dispatcher: DeterministicPaperGenerationDispatcher,
    generation_dispatcher: DeterministicGenerationDispatcher,
    *,
    pipeline: ValidationPipeline | None = None,
    rate_limiter: RateLimiter | None = None,
) -> Iterator[TestClient]:
    with TestClient(
        create_app(
            settings=seed.settings,
            identity_provider=StaticIdentityProvider(),
            paper_generation_dispatcher=paper_dispatcher,
            generation_dispatcher=generation_dispatcher,
            generation_runtime_registry=create_generation_runtime(seed.settings),
            embedding_provider_registry=create_embedding_provider_registry(seed.settings),
            validation_pipeline=pipeline,
            rate_limiter=rate_limiter or NoOpRateLimiter(),
        )
    ) as client:
        yield client


async def add_reviewed_lesson_source(
    session: AsyncSession,
    *,
    index: int,
    lesson_id: UUID,
    skill_id: UUID,
    chunk_id: UUID,
    curriculum_id: UUID = CURRICULUM_ID,
    unit_id: UUID = UNIT_ID,
    competency_id: UUID = COMPETENCY_ID,
    grade: int = 5,
) -> None:
    document_id = UUID(int=25_110_000 + index)
    page_id = UUID(int=25_111_000 + index)
    block_id = UUID(int=25_112_000 + index)
    text_value = f"Reviewed Grade {grade} mathematics lesson {index} evidence about number {index}."
    now = datetime.now(UTC)
    document = SourceDocumentModel(
        id=document_id,
        checksum_sha256=sha256(f"teacher-paper-source-{index}".encode()).hexdigest(),
        object_key=f"sources/grade7-maths-{index}.pdf",
        original_filename=f"grade7-maths-lesson-{index}.pdf",
        content_type="application/pdf",
        size_bytes=1_000,
        document_type=SourceDocumentType.TEACHER_GUIDE,
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        curriculum_version_id=curriculum_id,
        unit_id=unit_id,
        lesson_id=lesson_id,
        active_for_ai=True,
        metadata_scope_version=0,
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
            extractor="teacher-paper-fixture",
            extractor_version="v1",
            raw_text=text_value,
            reviewed_text=text_value,
            character_count=len(text_value),
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
            extractor="teacher-paper-fixture",
            extractor_version="v1",
            bbox_x0=0.0,
            bbox_y0=0.0,
            bbox_x1=1.0,
            bbox_y1=1.0,
            raw_text=text_value,
            reviewed_text=text_value,
            character_count=len(text_value),
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    document.extraction_status = ExtractionStatus.EXTRACTED
    document.extractor = "teacher-paper-fixture"
    document.extractor_version = "v1"
    document.extracted_page_count = 1
    document.extracted_block_count = 1
    document.extracted_character_count = len(text_value)
    document.native_text_page_ratio = 1.0
    document.needs_ocr = False
    document.ocr_page_count = 0
    document.extraction_config = {}
    document.extraction_completed_at = now
    await session.flush()
    document.extraction_status = ExtractionStatus.IN_REVIEW
    await session.flush()
    document.extraction_status = ExtractionStatus.TRUSTED
    await session.flush()
    session.add(
        KnowledgeChunkModel(
            id=chunk_id,
            curriculum_version_id=curriculum_id,
            chunk_type=ChunkType.EXPLANATION,
            text=text_value,
            educational_boundary=f"Grade {grade} Mathematics Lesson {index}",
            sequence=0,
            source_document_id=document_id,
            unit_id=unit_id,
            lesson_id=lesson_id,
            page_number=1,
            source_block_id=block_id,
            review_state=ReviewState.REVIEWED,
            competency_id=competency_id,
            skill_id=skill_id,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
    embedding = create_embedding_provider_registry(seed_settings()).embed_source(text_value, config)
    session.add(
        KnowledgeEmbeddingModel(
            id=UUID(int=25_113_000 + index),
            historical_question_id=None,
            knowledge_chunk_id=chunk_id,
            embedding_configuration_id=EMBEDDING_CONFIGURATION_ID,
            embedding_dimension=config.dimension,
            source_text_sha256=sha256(text_value.encode()).hexdigest(),
            embedding=list(embedding.vector),
            created_by=ADMIN_ID,
        )
    )
    await session.flush()


def seed_settings() -> Settings:
    return Settings(environment="test")


async def seed_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            session.add_all(
                [
                    SubjectModel(
                        id=SUBJECT_ID,
                        code="MATHEMATICS",
                        name="Mathematics",
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    ),
                    ExamConfigurationModel(
                        id=EXAM_ID,
                        code="SCHOOL-G5",
                        name="School Grade 5",
                        grade=5,
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    ),
                    MediumModel(
                        id=MEDIUM_ID,
                        code="si",
                        name="Sinhala",
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    ),
                ]
            )
            await session.flush()
            session.add(
                CurriculumVersionModel(
                    id=CURRICULUM_ID,
                    exam_configuration_id=EXAM_ID,
                    medium_id=MEDIUM_ID,
                    subject_id=SUBJECT_ID,
                    code="G5-MATH-V1",
                    title="Grade 5 Mathematics",
                    active=True,
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                )
            )
            await session.flush()
            session.add(
                CurriculumUnitModel(
                    id=UNIT_ID,
                    curriculum_version_id=CURRICULUM_ID,
                    code="NUMBERS",
                    title="Numbers",
                    ordinal=1,
                    active=True,
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                )
            )
            session.add(
                TaxonomyNodeModel(
                    id=COMPETENCY_ID,
                    curriculum_version_id=CURRICULUM_ID,
                    parent_id=None,
                    level=TaxonomyLevel.COMPETENCY,
                    code="C1",
                    title="Number competency",
                    active=True,
                    review_state=TaxonomyReviewState.REVIEWED,
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                )
            )
            await session.flush()
            for index, (lesson_id, skill_id) in enumerate(
                zip(LESSON_IDS, SKILL_IDS, strict=True), 1
            ):
                session.add(
                    TaxonomyNodeModel(
                        id=skill_id,
                        curriculum_version_id=CURRICULUM_ID,
                        parent_id=COMPETENCY_ID,
                        level=TaxonomyLevel.SKILL,
                        code=f"S{index}",
                        title=f"Lesson {index} skill",
                        active=True,
                        review_state=TaxonomyReviewState.REVIEWED,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                session.add(
                    CurriculumLessonModel(
                        id=lesson_id,
                        curriculum_version_id=CURRICULUM_ID,
                        unit_id=UNIT_ID,
                        code=f"LESSON-{index}",
                        title=("Whole numbers", "Factors and multiples", "Fractions")[index - 1],
                        ordinal=index,
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
            await session.flush()
            for lesson_id, skill_id in zip(LESSON_IDS, SKILL_IDS, strict=True):
                session.add(
                    CurriculumLessonTaxonomyMappingModel(
                        lesson_id=lesson_id,
                        curriculum_version_id=CURRICULUM_ID,
                        unit_id=UNIT_ID,
                        taxonomy_node_id=skill_id,
                        created_by=ADMIN_ID,
                    )
                )
            session.add(
                EmbeddingConfigurationModel.from_domain(
                    EMBEDDING_CONFIGURATION_ID,
                    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
                    ADMIN_ID,
                )
            )
            await session.flush()
            for index, values in enumerate(zip(LESSON_IDS, SKILL_IDS, CHUNK_IDS, strict=True), 1):
                await add_reviewed_lesson_source(
                    session,
                    index=index,
                    lesson_id=values[0],
                    skill_id=values[1],
                    chunk_id=values[2],
                )
            await session.commit()
    finally:
        await engine.dispose()


async def seed_scholarship_supporting_scopes(
    database_url: str,
) -> dict[int, tuple[UUID, UUID, UUID, UUID, UUID]]:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    seeded: dict[int, tuple[UUID, UUID, UUID, UUID, UUID]] = {}
    try:
        async with sessions() as session:
            for grade in (3, 4):
                exam_id = UUID(int=25_120_000 + grade)
                curriculum_id = UUID(int=25_121_000 + grade)
                unit_id = UUID(int=25_122_000 + grade)
                lesson_id = UUID(int=25_123_000 + grade)
                competency_id = UUID(int=25_124_000 + grade)
                skill_id = UUID(int=25_125_000 + grade)
                chunk_id = UUID(int=25_126_000 + grade)
                session.add(
                    ExamConfigurationModel(
                        id=exam_id,
                        code=f"SCHOOL-G{grade}",
                        name=f"School Grade {grade}",
                        grade=grade,
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.flush()
                session.add(
                    CurriculumVersionModel(
                        id=curriculum_id,
                        exam_configuration_id=exam_id,
                        medium_id=MEDIUM_ID,
                        subject_id=SUBJECT_ID,
                        code=f"G{grade}-MATH-V1",
                        title=f"Grade {grade} Mathematics",
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.flush()
                session.add(
                    CurriculumUnitModel(
                        id=unit_id,
                        curriculum_version_id=curriculum_id,
                        code="NUMBERS",
                        title="Numbers",
                        ordinal=1,
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                session.add(
                    TaxonomyNodeModel(
                        id=competency_id,
                        curriculum_version_id=curriculum_id,
                        parent_id=None,
                        level=TaxonomyLevel.COMPETENCY,
                        code="C1",
                        title="Number competency",
                        active=True,
                        review_state=TaxonomyReviewState.REVIEWED,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.flush()
                session.add(
                    TaxonomyNodeModel(
                        id=skill_id,
                        curriculum_version_id=curriculum_id,
                        parent_id=competency_id,
                        level=TaxonomyLevel.SKILL,
                        code="S1",
                        title="Number skill",
                        active=True,
                        review_state=TaxonomyReviewState.REVIEWED,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                session.add(
                    CurriculumLessonModel(
                        id=lesson_id,
                        curriculum_version_id=curriculum_id,
                        unit_id=unit_id,
                        code="LESSON-1",
                        title="Whole numbers",
                        ordinal=1,
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.flush()
                session.add(
                    CurriculumLessonTaxonomyMappingModel(
                        lesson_id=lesson_id,
                        curriculum_version_id=curriculum_id,
                        unit_id=unit_id,
                        taxonomy_node_id=skill_id,
                        created_by=ADMIN_ID,
                    )
                )
                await add_reviewed_lesson_source(
                    session,
                    index=100 + grade,
                    lesson_id=lesson_id,
                    skill_id=skill_id,
                    chunk_id=chunk_id,
                    curriculum_id=curriculum_id,
                    unit_id=unit_id,
                    competency_id=competency_id,
                    grade=grade,
                )
                seeded[grade] = (
                    curriculum_id,
                    unit_id,
                    lesson_id,
                    competency_id,
                    skill_id,
                )
            await session.commit()
    finally:
        await engine.dispose()
    return seeded


@pytest.fixture(scope="module")
def aggregate_seed() -> Iterator[Seed]:
    with (
        PostgresContainer(
            image=PGVECTOR_IMAGE,
            username="exam_guru",
            password="teacher-paper-integration-only",  # pragma: allowlist secret
            dbname="exam_guru_teacher_paper_test",
            driver="asyncpg",
        ) as postgres,
        RedisContainer(image=VALKEY_IMAGE) as valkey,
    ):
        seed = Seed(
            database_url=postgres.get_connection_url(),
            valkey_url=(
                f"redis://{valkey.get_container_host_ip()}:{valkey.get_exposed_port(6379)}/0"
            ),
        )
        upgrade_database(seed.database_url)
        assert_database_schema_current(seed.database_url)
        asyncio.run(seed_database(seed.database_url))
        yield seed


async def advance_and_run_slots(
    seed: Seed,
    job_id: UUID,
    paper_dispatcher: DeterministicPaperGenerationDispatcher,
    generation_dispatcher: DeterministicGenerationDispatcher,
    *,
    pipeline: ValidationPipeline | None = None,
) -> TeacherPaperJobModel:
    engine = create_async_engine(seed.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = seed.settings
    runtime = create_generation_runtime(settings)
    embeddings = create_embedding_provider_registry(settings)
    active_pipeline = pipeline or build_default_pipeline()
    try:
        async with sessions() as session:
            worker = TeacherPaperWorkerService(
                session,
                paper_dispatcher,
                generation_dispatcher,
                runtime,
                embeddings,
                active_pipeline,
            )
            assert await worker.advance(job_id) is True
        async with sessions() as session:
            rows = (
                await session.execute(
                    select(GenerationJobModel.id, GenerationRunModel.id)
                    .join(
                        TeacherPaperSlotRunModel,
                        TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
                    )
                    .join(
                        GenerationJobModel,
                        GenerationJobModel.generation_run_id == GenerationRunModel.id,
                    )
                    .where(TeacherPaperSlotRunModel.paper_job_id == job_id)
                    .order_by(TeacherPaperSlotRunModel.slot_ordinal)
                )
            ).all()
        for generation_job_id, generation_run_id in rows:
            async with sessions() as session:
                assert await GenerationWorkerService(
                    session, runtime, sleep=lambda _: None
                ).process(
                    generation_job_id,
                    generation_run_id,
                )
        async with sessions() as session:
            recovery = TeacherPaperRecoveryService(
                session,
                paper_dispatcher,
                batch_size=20,
                actor_lease_seconds=601,
            )
            result = await recovery.recover()
            if result.dispatched < 1:
                stalled = await session.get(TeacherPaperJobModel, job_id)
                assert stalled is not None
                raise AssertionError((stalled.status, stalled.failure_code, stalled.failure_detail))
        async with sessions() as session:
            worker = TeacherPaperWorkerService(
                session,
                paper_dispatcher,
                generation_dispatcher,
                runtime,
                embeddings,
                active_pipeline,
            )
            assert await worker.advance(job_id) is True
        async with sessions() as session:
            stored = await session.get(TeacherPaperJobModel, job_id)
            assert stored is not None
            return stored
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_grade5_lessons_one_to_three_runs_off_request_thread_and_reaches_review(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        options_response = client.get(
            "/api/v1/admin/paper-generation/options",
            headers={"Authorization": "Bearer reviewer-token"},
        )
        assert options_response.status_code == 200
        options = options_response.json()
        assert options["grades"] == [5]
        assert any(item["code"] == "si" and item["label"] == "Sinhala" for item in options["media"])
        assert [item["code"] for item in options["paper_types"]] == [
            "subject_practice",
            "term_test",
            "scholarship_practice",
        ]
        assert [item["code"] for item in options["scholarship_modes"]] == [
            "paper_i",
            "paper_ii",
            "full",
        ]
        maths = next(item for item in options["subjects"] if item["code"] == "MATHEMATICS")
        assert [item["number"] for item in maths["lessons"]] == [1, 2, 3]
        assert "technical_curriculum_id" not in maths
        curricula = client.get(
            "/api/v1/admin/paper-generation/curricula",
            params={
                "grade": 5,
                "medium": "si",
                "subject": "MATHEMATICS",
                "assessment_programme": "SCHOOL-G5",
            },
            headers={"Authorization": "Bearer reviewer-token"},
        )
        assert curricula.status_code == 200
        assert curricula.json()["items"] == [
            {
                "assessment_programme": "SCHOOL-G5",
                "assessment_label": "School Grade 5",
                "code": "G5-MATH-V1",
                "label": "Grade 5 Mathematics",
            }
        ]
        lessons = client.get(
            "/api/v1/admin/paper-generation/lessons",
            params={
                "grade": 5,
                "medium": "si",
                "subject": "MATHEMATICS",
                "assessment_programme": "SCHOOL-G5",
            },
            headers={"Authorization": "Bearer reviewer-token"},
        )
        assert lessons.status_code == 200
        assert [item["label"] for item in lessons.json()["lessons"]] == [
            "Lesson 1 — Whole numbers",
            "Lesson 2 — Factors and multiples",
            "Lesson 3 — Fractions",
        ]

        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers=ADMIN_HEADERS,
        )
        assert created.status_code == 202
        body = created.json()
        assert body["status"] == "preparing"
        assert body["paper_reference"].startswith("EGP-")
        assert body["scope_summary"] == "Lessons 1\u20133"
        assert body["counts"]["requested"] == 3
        job_id = UUID(body["job_id"])
        assert paper_dispatcher.dispatched == [job_id]

        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
            )
        )

        async def persisted_failure_findings() -> list[tuple[str, str]]:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return [
                        (code, finding_status)
                        for code, finding_status in (
                            await session.execute(
                                select(
                                    ValidationFindingModel.code,
                                    ValidationFindingModel.status,
                                ).where(ValidationFindingModel.status == "fail")
                            )
                        ).all()
                    ]
            finally:
                await engine.dispose()

        assert terminal.status == "ready_for_review", (
            terminal.failure_code,
            terminal.failure_detail,
            asyncio.run(persisted_failure_findings()),
        )
        assert terminal.generated_count == 3
        assert terminal.validated_count == 3
        assert terminal.candidate_count == 3

        async def direct_delete_is_rejected() -> None:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text("DELETE FROM teacher_paper_jobs WHERE id = :job_id"),
                        {"job_id": job_id},
                    )
            finally:
                await engine.dispose()

        with pytest.raises(DBAPIError):
            asyncio.run(direct_delete_is_rejected())

        fetched = client.get(
            f"/api/v1/admin/paper-generation/jobs/{job_id}",
            headers={"Authorization": "Bearer reviewer-token"},
        )
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "ready_for_review"
        listed = client.get(
            "/api/v1/admin/review-papers",
            headers=REVIEWER_HEADERS,
        )
        assert listed.status_code == 200
        assert any(item["id"] == str(job_id) for item in listed.json()["items"])
        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        assert detail.status_code == 200
        review = detail.json()
        assert review["paper_reference"] == body["paper_reference"]
        assert review["grade"] == 5
        assert review["subject"] == "Mathematics"
        assert review["scope_summary"] == "Lessons 1\u20133"
        assert len(review["questions"]) == 3
        assert {question["scope"]["lesson"] for question in review["questions"]} == {
            "Lesson 1 — Whole numbers",
            "Lesson 2 — Factors and multiples",
            "Lesson 3 — Fractions",
        }
        assert all(question["sources"] for question in review["questions"])
        assert all(
            question["sources"][0]["filename"].startswith("grade7-maths")
            for question in review["questions"]
        )
        assert all(
            question["validation"]["status"] in {"ready", "needs_attention"}
            for question in review["questions"]
        )
        assert all(
            question["technical_details"]["provider"] == "deterministic-fake"
            for question in review["questions"]
        )

        for question in review["questions"]:
            question_id = question["id"]
            started = client.post(
                f"/api/v1/admin/review-papers/{job_id}/questions/{question_id}/start",
                json={"expected_version": question["version"]},
                headers=REVIEWER_HEADERS,
            )
            assert started.status_code == 200
            if question["number"] == 1:
                started_candidate_id = UUID(started.json()["technical_details"]["candidate_id"])

                async def bypass_marking_confirmation_is_rejected(
                    candidate_id: UUID = started_candidate_id,
                ) -> None:
                    engine = create_async_engine(aggregate_seed.database_url)
                    try:
                        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                            with pytest.raises(DBAPIError):
                                await session.execute(
                                    text(
                                        "UPDATE question_candidates SET state = 'approved', "
                                        "version = version + 1 WHERE id = :candidate_id"
                                    ),
                                    {"candidate_id": candidate_id},
                                )
                            await session.rollback()
                    finally:
                        await engine.dispose()

                asyncio.run(bypass_marking_confirmation_is_rejected())
                unconfirmed = client.post(
                    f"/api/v1/admin/review-papers/{job_id}/questions/{question_id}/approve",
                    json={"expected_version": started.json()["version"], "note": None},
                    headers=REVIEWER_HEADERS,
                )
                assert unconfirmed.status_code == 422
            approved = client.post(
                f"/api/v1/admin/review-papers/{job_id}/questions/{question_id}/approve",
                json={
                    "expected_version": started.json()["version"],
                    "marking_confirmed": True,
                    "note": None,
                },
                headers=REVIEWER_HEADERS,
            )
            assert approved.status_code == 200
            assert approved.json()["marking_confirmation"]["confirmed"] is True

        async def confirmation_counts() -> tuple[int, int]:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    confirmations = cast(
                        int,
                        await session.scalar(
                            select(func.count(TeacherPaperMarkingConfirmationModel.id)).where(
                                TeacherPaperMarkingConfirmationModel.paper_job_id == job_id
                            )
                        ),
                    )
                    audit_events = cast(
                        int,
                        await session.scalar(
                            select(func.count(AdminAuditEventModel.id)).where(
                                AdminAuditEventModel.action == "teacher_paper.marking_confirmed",
                                AdminAuditEventModel.payload["paper_job_id"].as_string()
                                == str(job_id),
                            )
                        ),
                    )
                    return confirmations, audit_events
            finally:
                await engine.dispose()

        requested = body["counts"]["requested"]
        assert asyncio.run(confirmation_counts()) == (requested, requested)

        async def marking_confirmation_is_append_only() -> None:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    confirmation_id = await session.scalar(
                        select(TeacherPaperMarkingConfirmationModel.id).where(
                            TeacherPaperMarkingConfirmationModel.paper_job_id == job_id
                        )
                    )
                    assert confirmation_id is not None
                    with pytest.raises(DBAPIError):
                        await session.execute(
                            text(
                                "UPDATE teacher_paper_marking_confirmations "
                                "SET total_marks = total_marks + 1 WHERE id = :confirmation_id"
                            ),
                            {"confirmation_id": confirmation_id},
                        )
                    await session.rollback()
                    with pytest.raises(DBAPIError):
                        await session.execute(
                            text(
                                "INSERT INTO teacher_paper_marking_confirmations ("
                                "id, paper_job_id, slot_id, curriculum_version_id, "
                                "candidate_id, candidate_revision, review_candidate_version, "
                                "marking_fingerprint, total_marks, criteria_count, confirmed_by"
                                ") SELECT gen_random_uuid(), paper_job_id, slot_id, "
                                "curriculum_version_id, candidate_id, candidate_revision, "
                                "review_candidate_version, 'sha256:' || repeat('0', 64), "
                                "total_marks, criteria_count, confirmed_by "
                                "FROM teacher_paper_marking_confirmations "
                                "WHERE id = :confirmation_id"
                            ),
                            {"confirmation_id": confirmation_id},
                        )
                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(marking_confirmation_is_append_only())

        draft = client.post(
            f"/api/v1/admin/review-papers/{job_id}/create-draft",
            json={
                "expected_version": client.get(
                    f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS
                ).json()["version"]
            },
            headers=REVIEWER_HEADERS,
        )
        assert draft.status_code == 201
        assert draft.json()["paper_job_id"] == str(job_id)
        assert draft.json()["paper_id"] == draft.json()["draft_id"]
        assert draft.json()["paper_reference"] == body["paper_reference"]
        assert draft.json()["publication_path"].endswith(
            f"/{CURRICULUM_ID}/papers/{draft.json()['draft_id']}"
        )

        async def draft_lineage_cannot_race_back_to_generation() -> None:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    with pytest.raises(DBAPIError, match="ready-for-review job"):
                        await session.execute(
                            text(
                                "UPDATE teacher_paper_jobs SET status = 'generating', "
                                "completed_at = NULL, version = version + 1, updated_at = now() "
                                "WHERE id = :job_id"
                            ),
                            {"job_id": job_id},
                        )
                    await session.rollback()
                    with pytest.raises(DBAPIError, match="immutable after draft creation"):
                        await session.execute(
                            text(
                                "UPDATE teacher_paper_slots SET status = 'generating', "
                                "current_validation_run_id = NULL, current_candidate_id = NULL, "
                                "version = version + 1, updated_at = now() "
                                "WHERE paper_job_id = :job_id"
                            ),
                            {"job_id": job_id},
                        )
                    await session.rollback()
            finally:
                await engine.dispose()

        asyncio.run(draft_lineage_cannot_race_back_to_generation())


@pytest.mark.integration
def test_selected_lessons_preserve_exact_non_contiguous_scope_through_worker(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "selected_lessons", "lesson_numbers": [1, 3]},
                question_count=2,
            ),
            headers={
                **ADMIN_HEADERS,
                "Idempotency-Key": "teacher-paper-selected-lessons-exact",
            },
        )
        assert created.status_code == 202
        assert created.json()["scope_summary"] == "Lessons 1 and 3"
        job_id = UUID(created.json()["job_id"])

        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
            )
        )
        assert terminal.status == "ready_for_review"
        fetched = client.get(
            f"/api/v1/admin/paper-generation/jobs/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        assert fetched.status_code == 200
        assert {slot["lesson"] for slot in fetched.json()["slots"]} == {
            "Lesson 1 — Whole numbers",
            "Lesson 3 — Fractions",
        }
        assert "Lesson 2" not in str(fetched.json())


@pytest.mark.integration
def test_reviewed_scholarship_policy_generates_each_mode_without_cross_grade_leakage(
    aggregate_seed: Seed,
) -> None:
    supporting = asyncio.run(seed_scholarship_supporting_scopes(aggregate_seed.database_url))
    grade3 = supporting[3]
    grade4 = supporting[4]
    policy_payload = {
        "programme_exam_configuration_id": str(EXAM_ID),
        "medium_id": str(MEDIUM_ID),
        "anchor_curriculum_version_id": str(CURRICULUM_ID),
        "code": "G5-SCHOLARSHIP",
        "version": "integration.v1",
        "title": "Grade 5 Scholarship",
        "paper_i_profile_version": "ability.integration.v1",
        "paper_ii_profile_version": "coverage.integration.v1",
        "paper_i_weight": 1,
        "paper_ii_weight": 1,
        "scopes": [
            {
                "part": "paper_i",
                "ordinal": 1,
                "anchor_unit_id": str(UNIT_ID),
                "anchor_lesson_id": str(LESSON_IDS[0]),
                "anchor_competency_id": str(COMPETENCY_ID),
                "anchor_skill_id": str(SKILL_IDS[0]),
                "source_curriculum_version_id": str(CURRICULUM_ID),
                "source_unit_id": str(UNIT_ID),
                "source_lesson_id": str(LESSON_IDS[0]),
                "source_competency_id": str(COMPETENCY_ID),
                "source_skill_id": str(SKILL_IDS[0]),
            },
            *(
                {
                    "part": "paper_ii",
                    "ordinal": ordinal,
                    "anchor_unit_id": str(UNIT_ID),
                    "anchor_lesson_id": str(LESSON_IDS[1]),
                    "anchor_competency_id": str(COMPETENCY_ID),
                    "anchor_skill_id": str(SKILL_IDS[1]),
                    "source_curriculum_version_id": str(values[0]),
                    "source_unit_id": str(values[1]),
                    "source_lesson_id": str(values[2]),
                    "source_competency_id": str(values[3]),
                    "source_skill_id": str(values[4]),
                }
                for ordinal, values in enumerate(
                    (
                        grade3,
                        grade4,
                        (
                            CURRICULUM_ID,
                            UNIT_ID,
                            LESSON_IDS[1],
                            COMPETENCY_ID,
                            SKILL_IDS[1],
                        ),
                    ),
                    start=1,
                )
            ),
        ],
    }
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created_policy = client.post(
            "/api/v1/admin/paper-generation/programme-policies",
            json=policy_payload,
            headers={"Authorization": "Bearer admin-token"},
        )
        assert created_policy.status_code == 201, created_policy.text
        policy = created_policy.json()
        reviewed_policy = client.post(
            f"/api/v1/admin/paper-generation/programme-policies/{policy['id']}/review",
            json={"expected_version": policy["lock_version"]},
            headers=REVIEWER_HEADERS,
        )
        assert reviewed_policy.status_code == 200, reviewed_policy.text
        assert reviewed_policy.json()["state"] == "reviewed"

        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json={
                "target": {
                    "grade": 5,
                    "medium": "si",
                    "paper_type": "scholarship_practice",
                    "scholarship_mode": "full",
                },
                "scope": {"kind": "programme"},
                "settings": {
                    "paper_name": "Full Scholarship Practice",
                    "mcq_count": 2,
                    "written_count": 0,
                    "structured_count": 0,
                    "duration_minutes": 60,
                    "difficulty": "balanced",
                },
            },
            headers={
                "Authorization": "Bearer admin-token",
                "Idempotency-Key": "full-scholarship-integration",
            },
        )
        assert created.status_code == 202, created.text
        job_id = UUID(created.json()["job_id"])
        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
            )
        )

        async def persisted_failures() -> tuple[tuple[str, str], ...]:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return tuple(
                        (
                            finding.code,
                            finding.status,
                        )
                        for finding in await session.scalars(
                            select(ValidationFindingModel).where(
                                ValidationFindingModel.status == "fail"
                            )
                        )
                    )
            finally:
                await engine.dispose()

        assert terminal.status == "ready_for_review", (
            terminal.failure_code,
            terminal.failure_detail,
            asyncio.run(persisted_failures()),
        )
        assert terminal.slot_count == 2

        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        assert detail.status_code == 200
        assert detail.json()["scope_summary"] == ("Full Scholarship Practice — Paper I + Paper II")
        assert all(
            sum(question["content"]["marking_point_marks"]) == question["content"]["marks"]
            and question["sources"]
            and question["validation"]["status"] in {"ready", "needs_attention"}
            for question in detail.json()["questions"]
        )
        mode_job_ids = {"full": job_id}
        for mode, label in (
            ("paper_i", "Paper I Scholarship Practice"),
            ("paper_ii", "Paper II Scholarship Practice"),
        ):
            mode_created = client.post(
                "/api/v1/admin/paper-generation/jobs",
                json={
                    "target": {
                        "grade": 5,
                        "medium": "si",
                        "paper_type": "scholarship_practice",
                        "scholarship_mode": mode,
                    },
                    "scope": {"kind": "programme"},
                    "settings": {
                        "paper_name": label,
                        "mcq_count": 2,
                        "written_count": 0,
                        "structured_count": 0,
                        "duration_minutes": 30,
                        "difficulty": "balanced",
                    },
                },
                headers={
                    "Authorization": "Bearer admin-token",
                    "Idempotency-Key": f"{mode}-scholarship-integration",
                },
            )
            assert mode_created.status_code == 202, mode_created.text
            mode_job_id = UUID(mode_created.json()["job_id"])
            mode_terminal = asyncio.run(
                advance_and_run_slots(
                    aggregate_seed,
                    mode_job_id,
                    paper_dispatcher,
                    generation_dispatcher,
                )
            )
            assert mode_terminal.status == "ready_for_review", (
                mode,
                mode_terminal.failure_code,
                mode_terminal.failure_detail,
            )
            assert mode_terminal.slot_count == 2
            mode_detail = client.get(
                f"/api/v1/admin/review-papers/{mode_job_id}",
                headers=REVIEWER_HEADERS,
            )
            assert mode_detail.status_code == 200
            assert len(mode_detail.json()["questions"]) == 2
            assert all(
                sum(question["content"]["marking_point_marks"]) == question["content"]["marks"]
                and question["sources"]
                for question in mode_detail.json()["questions"]
            )
            mode_job_ids[mode] = mode_job_id

    async def generation_scope_snapshots(
        selected_job_id: UUID,
    ) -> tuple[dict[str, object], ...]:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                runs = tuple(
                    await session.scalars(
                        select(GenerationRunModel)
                        .join(
                            TeacherPaperSlotModel,
                            TeacherPaperSlotModel.current_generation_run_id
                            == GenerationRunModel.id,
                        )
                        .where(TeacherPaperSlotModel.paper_job_id == selected_job_id)
                        .order_by(TeacherPaperSlotModel.ordinal)
                    )
                )
                return tuple(run.context_snapshot for run in runs)
        finally:
            await engine.dispose()

    expected_scope_grades = {
        "paper_i": {(5,)},
        "paper_ii": {(3, 4, 5)},
        "full": {(5,), (3, 4, 5)},
    }
    for mode, selected_job_id in mode_job_ids.items():
        snapshots = asyncio.run(generation_scope_snapshots(selected_job_id))
        scope_grades = {
            tuple(
                scope["grade"]
                for scope in cast(
                    list[dict[str, object]],
                    cast(dict[str, object], snapshot["retrieval_filters"])["scopes"],
                )
            )
            for snapshot in snapshots
        }
        assert scope_grades == expected_scope_grades[mode]

    async def mutate_policy(statement: str, identifier: str) -> None:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text(statement), {"identifier": UUID(identifier)})
        finally:
            await engine.dispose()

    with pytest.raises(DBAPIError):
        asyncio.run(
            mutate_policy(
                "UPDATE assessment_programme_policy_versions SET title = 'tampered' "
                "WHERE id = :identifier",
                policy["id"],
            )
        )
    with pytest.raises(DBAPIError):
        asyncio.run(
            mutate_policy(
                "DELETE FROM assessment_programme_policy_scopes WHERE id = :identifier",
                policy["scopes"][0]["id"],
            )
        )


@pytest.mark.integration
def test_grade_five_term_modes_fail_closed_without_reviewed_coverage_policy(
    aggregate_seed: Seed,
) -> None:
    async def job_count() -> int:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return cast(int, await session.scalar(select(func.count(TeacherPaperJobModel.id))))
        finally:
            await engine.dispose()

    before = asyncio.run(job_count())
    with api_client(
        aggregate_seed,
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
    ) as client:
        for term in ("term_1", "term_2", "term_3"):
            response = client.post(
                "/api/v1/admin/paper-generation/jobs",
                json={
                    "target": {
                        "grade": 5,
                        "medium": "si",
                        "paper_type": "term_test",
                        "subject": "MATHEMATICS",
                        "term": term,
                    },
                    "scope": {"kind": "full_term"},
                    "settings": {
                        "paper_name": f"Grade 5 {term} test",
                        "mcq_count": 2,
                        "written_count": 1,
                        "structured_count": 0,
                        "duration_minutes": 30,
                        "difficulty": "balanced",
                    },
                },
                headers={
                    "Authorization": "Bearer admin-token",
                    "Idempotency-Key": f"unreviewed-{term}-coverage",
                },
            )
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == ("paper_generation_term_policy_unavailable")
    assert asyncio.run(job_count()) == before


@pytest.mark.integration
def test_partial_provider_failure_is_readable_duplicate_advance_is_safe_and_retry_is_bounded(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "lesson_range", "start_lesson": 1, "end_lesson": 2},
                question_count=2,
            ),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "partial-failure-key"},
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])

        async def fail_one_slot() -> tuple[int, int]:
            engine = create_async_engine(aggregate_seed.database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            settings = aggregate_seed.settings
            runtime = create_generation_runtime(settings)
            embeddings = create_embedding_provider_registry(settings)
            pipeline = build_default_pipeline()
            try:
                async with sessions() as session:
                    aggregate_worker = TeacherPaperWorkerService(
                        session,
                        paper_dispatcher,
                        generation_dispatcher,
                        runtime,
                        embeddings,
                        pipeline,
                    )
                    assert await aggregate_worker.advance(job_id) is True
                # At-least-once aggregate delivery observes pending slot workers and creates no
                # duplicate generation lineage or provider call.
                async with sessions() as session:
                    aggregate_worker = TeacherPaperWorkerService(
                        session,
                        paper_dispatcher,
                        generation_dispatcher,
                        runtime,
                        embeddings,
                        pipeline,
                    )
                    assert await aggregate_worker.advance(job_id) is True
                async with sessions() as session:
                    rows = (
                        await session.execute(
                            select(GenerationJobModel.id, GenerationRunModel.id)
                            .join(
                                TeacherPaperSlotRunModel,
                                TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
                            )
                            .join(
                                GenerationJobModel,
                                GenerationJobModel.generation_run_id == GenerationRunModel.id,
                            )
                            .where(TeacherPaperSlotRunModel.paper_job_id == job_id)
                            .order_by(TeacherPaperSlotRunModel.slot_ordinal)
                        )
                    ).all()
                    assert len(rows) == 2
                async with sessions() as session:
                    assert await GenerationWorkerService(
                        session,
                        runtime,
                        sleep=lambda _: None,
                    ).process(*rows[0])
                failing_runtime = GenerationRuntimeRegistry(
                    runtime.active_config,
                    provider_factory=lambda _: UnavailableGenerationProvider(),
                )
                async with sessions() as session:
                    assert await GenerationWorkerService(
                        session,
                        failing_runtime,
                        sleep=lambda _: None,
                    ).process(*rows[1])
                async with sessions() as session:
                    aggregate_worker = TeacherPaperWorkerService(
                        session,
                        paper_dispatcher,
                        generation_dispatcher,
                        runtime,
                        embeddings,
                        pipeline,
                    )
                    assert await aggregate_worker.advance(job_id) is True
                async with sessions() as session:
                    count = int(
                        await session.scalar(
                            select(func.count(TeacherPaperSlotRunModel.id)).where(
                                TeacherPaperSlotRunModel.paper_job_id == job_id
                            )
                        )
                        or 0
                    )
                    job = await session.get(TeacherPaperJobModel, job_id)
                    assert job is not None
                    return count, job.version
            finally:
                await engine.dispose()

        lineage_count, failed_version = asyncio.run(fail_one_slot())
        assert lineage_count == 2
        failed = client.get(
            f"/api/v1/admin/paper-generation/jobs/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["counts"] == {
            "requested": 2,
            "generated": 1,
            "validated": 0,
            "candidates": 0,
            "approved": 0,
            "failed": 1,
        }
        retry = client.post(
            f"/api/v1/admin/paper-generation/jobs/{job_id}/retry",
            json={"expected_version": failed_version},
            headers={
                "Authorization": "Bearer admin-token",
                "Idempotency-Key": "partial-failure-retry-1",
            },
        )
        assert retry.status_code == 202
        assert retry.json()["status"] == "generating"

        async def complete_retry() -> TeacherPaperJobModel:
            engine = create_async_engine(aggregate_seed.database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            settings = aggregate_seed.settings
            runtime = create_generation_runtime(settings)
            embeddings = create_embedding_provider_registry(settings)
            try:
                async with sessions() as session:
                    row = (
                        await session.execute(
                            select(GenerationJobModel.id, GenerationRunModel.id)
                            .join(
                                TeacherPaperSlotRunModel,
                                TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
                            )
                            .join(
                                GenerationJobModel,
                                GenerationJobModel.generation_run_id == GenerationRunModel.id,
                            )
                            .where(
                                TeacherPaperSlotRunModel.paper_job_id == job_id,
                                GenerationRunModel.retry_of_run_id.is_not(None),
                            )
                        )
                    ).one()
                async with sessions() as session:
                    assert await GenerationWorkerService(
                        session,
                        runtime,
                        sleep=lambda _: None,
                    ).process(*row)
                async with sessions() as session:
                    worker = TeacherPaperWorkerService(
                        session,
                        paper_dispatcher,
                        generation_dispatcher,
                        runtime,
                        embeddings,
                        build_default_pipeline(),
                    )
                    assert await worker.advance(job_id) is True
                async with sessions() as session:
                    job = await session.get(TeacherPaperJobModel, job_id)
                    assert job is not None
                    return job
            finally:
                await engine.dispose()

        terminal = asyncio.run(complete_retry())
        assert terminal.status == "ready_for_review"
        assert terminal.candidate_count == 2
        assert terminal.failed_count == 0


@pytest.mark.integration
def test_full_subject_is_idempotent_under_racing_requests_and_does_not_leak_scope(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    barrier = Barrier(2)

    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:

        def submit() -> tuple[int, dict[str, object]]:
            barrier.wait()
            response = client.post(
                "/api/v1/admin/paper-generation/jobs",
                json=request_payload(scope={"kind": "full_subject"}, question_count=1),
                headers={
                    "Authorization": "Bearer admin-token",
                    "Idempotency-Key": "racing-full-subject-key",
                },
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: submit(), range(2)))

    assert {status for status, _ in results} == {202}, results
    assert len({body["job_id"] for _, body in results}) == 1
    assert sorted(cast(bool, body["deduplicated"]) for _, body in results) == [False, True]
    assert len(set(paper_dispatcher.dispatched)) == 1

    job_id = UUID(str(results[0][1]["job_id"]))
    terminal = asyncio.run(
        advance_and_run_slots(
            aggregate_seed,
            job_id,
            paper_dispatcher,
            generation_dispatcher,
        )
    )
    assert terminal.status == "ready_for_review"
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}",
            headers=REVIEWER_HEADERS,
        ).json()
        question = detail["questions"][0]
        started = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/start",
            json={"expected_version": question["version"]},
            headers=REVIEWER_HEADERS,
        )
        assert started.status_code == 200
        rejected = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/reject",
            json={
                "expected_version": started.json()["version"],
                "reason_code": "other_quality_issue",
                "note": "Exercise the explicit aggregate rejection path.",
            },
            headers=REVIEWER_HEADERS,
        )
        assert rejected.status_code == 200
        assert rejected.json()["review_state"] == "rejected"

    async def inspect_context() -> tuple[list[str], int]:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                run = await session.scalar(
                    select(GenerationRunModel)
                    .join(
                        TeacherPaperSlotRunModel,
                        TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
                    )
                    .where(TeacherPaperSlotRunModel.paper_job_id == job_id)
                )
                assert run is not None
                context_items = cast(
                    list[dict[str, object]],
                    run.context_snapshot["items"],
                )
                lesson_ids = [
                    str(cast(dict[str, object], item["learning_scope"])["lesson_id"])
                    for item in context_items
                ]
                count = int(
                    await session.scalar(
                        select(func.count(TeacherPaperJobModel.id)).where(
                            TeacherPaperJobModel.id == job_id
                        )
                    )
                    or 0
                )
                return lesson_ids, count
        finally:
            await engine.dispose()

    context_lessons, aggregate_count = asyncio.run(inspect_context())
    assert aggregate_count == 1
    assert len(set(context_lessons)) == 1
    assert UUID(context_lessons[0]) in LESSON_IDS


@pytest.mark.integration
def test_ambiguous_unmapped_missing_and_no_context_fail_with_stable_safe_errors(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()

    async def add_ambiguous_curriculum() -> UUID:
        curriculum_id = UUID(int=25_120_001)
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add(
                    CurriculumVersionModel(
                        id=curriculum_id,
                        exam_configuration_id=EXAM_ID,
                        medium_id=MEDIUM_ID,
                        subject_id=SUBJECT_ID,
                        code="G5-MATH-V2",
                        title="Ambiguous Grade 5 Mathematics",
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.commit()
            return curriculum_id
        finally:
            await engine.dispose()

    ambiguous_id = asyncio.run(add_ambiguous_curriculum())
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        ambiguous = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "ambiguous-key"},
        )
        assert ambiguous.status_code == 409
        assert ambiguous.json()["detail"]["code"] == "paper_generation_curriculum_ambiguous"
        missing = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(subject="SCIENCE"),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "missing-key"},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "paper_generation_curriculum_not_found"

    async def remove_ambiguity_and_unmap() -> None:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                ambiguous = await session.get(CurriculumVersionModel, ambiguous_id)
                assert ambiguous is not None
                ambiguous.active = False
                mapping = await session.scalar(
                    select(CurriculumLessonTaxonomyMappingModel).where(
                        CurriculumLessonTaxonomyMappingModel.lesson_id == LESSON_IDS[1]
                    )
                )
                assert mapping is not None
                await session.delete(mapping)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(remove_ambiguity_and_unmap())
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        unmapped = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "unmapped-key"},
        )
        assert unmapped.status_code == 422
        assert unmapped.json()["detail"]["code"] == "paper_generation_lesson_unmapped"

    async def restore_mapping() -> None:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add(
                    CurriculumLessonTaxonomyMappingModel(
                        lesson_id=LESSON_IDS[1],
                        curriculum_version_id=CURRICULUM_ID,
                        unit_id=UNIT_ID,
                        taxonomy_node_id=SKILL_IDS[1],
                        created_by=ADMIN_ID,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(restore_mapping())


@pytest.mark.integration
def test_subject_fail_blocks_candidate_warn_enters_review_and_edit_requires_regeneration(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    default = build_default_pipeline()
    failing = ValidationPipeline(
        validators=(*default.validators, AlwaysFailValidator()),
        version="teacher-paper-failing-pipeline.v1",
        subject_router=default.subject_router,
    )
    with api_client(
        aggregate_seed,
        paper_dispatcher,
        generation_dispatcher,
        pipeline=failing,
    ) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "lesson_range", "start_lesson": 1, "end_lesson": 1},
                question_count=1,
            ),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "subject-fail-key"},
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
                pipeline=failing,
            )
        )
        assert terminal.status == "failed"
        assert terminal.failure_code == "paper_generation_validation_failed"
        assert terminal.candidate_count == 0
        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        assert detail.status_code == 200
        question = detail.json()["questions"][0]
        assert question["validation"]["status"] == "failed_check"
        assert question["technical_details"]["candidate_id"] is None
        assert any(
            finding["code"] == "subject.math.answer_mismatch"
            for finding in question["technical_details"]["validator_findings"]
        )

    # The passing/default pipeline produces a review candidate. Editing it invalidates approval;
    # regeneration is the bounded path to a fresh generation + canonical validation lineage.
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "lesson_range", "start_lesson": 1, "end_lesson": 1},
                question_count=1,
            ),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "edit-revalidate-key"},
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
            )
        )
        assert terminal.status == "ready_for_review"
        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS
        ).json()
        question = detail["questions"][0]
        old_validation_run_id = question["technical_details"]["validation_run_id"]
        started = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/start",
            json={"expected_version": question["version"]},
            headers=REVIEWER_HEADERS,
        )
        assert started.status_code == 200
        current = started.json()
        content = current["content"]
        content["stem"] = "A teacher edit that requires a fresh canonical validation."
        content["marks"] = 2
        content["marking_guide"] = [
            content["marking_guide"][0],
            "Explains why the selected answer is supported.",
        ]
        content["marking_point_marks"] = [1, 1]
        edit_payload = {
            "expected_version": current["version"],
            "reason_code": "ambiguous_wording",
            "note": "Clarify wording and revalidate before approval.",
            "content": content,
        }
        edited = client.patch(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}",
            json=edit_payload,
            headers=REVIEWER_HEADERS,
        )
        assert edited.status_code == 200
        assert edited.json()["requires_revalidation"] is True
        feedback_id = edited.json()["quality_feedback_id"]
        duplicate_edit = client.patch(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}",
            json=edit_payload,
            headers=REVIEWER_HEADERS,
        )
        assert duplicate_edit.status_code == 409
        feedback_list = client.get(
            "/api/v1/admin/subject-quality/feedback",
            params={"candidate_id": question["technical_details"]["candidate_id"], "limit": 10},
            headers=REVIEWER_HEADERS,
        )
        assert feedback_list.status_code == 200
        assert feedback_list.json()["total"] == 1
        edit_feedback = feedback_list.json()["items"][0]
        assert edit_feedback["id"] == feedback_id
        assert edit_feedback["action"] == "edit"
        assert edit_feedback["reason_code"] == "ambiguous_wording"
        assert edit_feedback["original_content"]["stem"] == question["content"]["stem"]
        assert edit_feedback["current_content"] == content
        assert edit_feedback["findings_at_action"]["validation_run_id"] == old_validation_run_id
        assert edit_feedback["scope"]["curriculum_version_id"] == str(CURRICULUM_ID)
        blocked = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/approve",
            json={
                "expected_version": edited.json()["version"],
                "marking_confirmed": True,
                "note": None,
            },
            headers=REVIEWER_HEADERS,
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "review_question_revalidation_required"
        regenerated = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/regenerate",
            json={
                "expected_version": edited.json()["aggregate_slot_version"],
                "reason_code": "answer_incorrect",
                "note": "Generate and validate a replacement after the edit.",
            },
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "regenerate-edited-question"},
        )
        assert regenerated.status_code == 202
        assert regenerated.json()["status"] == "generating"
        assert regenerated.json()["quality_feedback_id"]
        replacement_run_id = UUID(regenerated.json()["question_id"])
        feedback_after_regeneration = client.get(
            "/api/v1/admin/subject-quality/feedback",
            params={"candidate_id": question["technical_details"]["candidate_id"], "limit": 10},
            headers=REVIEWER_HEADERS,
        ).json()
        assert feedback_after_regeneration["total"] == 2
        assert {item["action"] for item in feedback_after_regeneration["items"]} == {
            "edit",
            "regenerate",
        }

        async def complete_regeneration() -> TeacherPaperJobModel:
            engine = create_async_engine(aggregate_seed.database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            settings = aggregate_seed.settings
            runtime = create_generation_runtime(settings)
            try:
                async with sessions() as session:
                    generation_job_id = await session.scalar(
                        select(GenerationJobModel.id).where(
                            GenerationJobModel.generation_run_id == replacement_run_id
                        )
                    )
                    assert generation_job_id is not None
                async with sessions() as session:
                    assert await GenerationWorkerService(
                        session,
                        runtime,
                        sleep=lambda _: None,
                    ).process(generation_job_id, replacement_run_id)
                async with sessions() as session:
                    worker = TeacherPaperWorkerService(
                        session,
                        paper_dispatcher,
                        generation_dispatcher,
                        runtime,
                        create_embedding_provider_registry(settings),
                        build_default_pipeline(),
                    )
                    assert await worker.advance(job_id) is True
                async with sessions() as session:
                    job = await session.get(TeacherPaperJobModel, job_id)
                    assert job is not None
                    return job
            finally:
                await engine.dispose()

        replacement_job = asyncio.run(complete_regeneration())
        assert replacement_job.status == "ready_for_review"
        refreshed = client.get(
            f"/api/v1/admin/review-papers/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        assert refreshed.status_code == 200
        replacement = refreshed.json()["questions"][0]
        assert replacement["id"] == str(replacement_run_id)
        assert replacement["requires_revalidation"] is False
        assert replacement["technical_details"]["validation_run_id"] != old_validation_run_id


@pytest.mark.integration
def test_feedback_failure_rolls_back_candidate_revision_event_and_slot_together(
    aggregate_seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "lesson_range", "start_lesson": 1, "end_lesson": 1},
                question_count=1,
            ),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "quality-feedback-rollback-key"},
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
            )
        )
        assert terminal.status == "ready_for_review"
        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS
        ).json()
        question = detail["questions"][0]
        started = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/start",
            json={"expected_version": question["version"]},
            headers=REVIEWER_HEADERS,
        )
        assert started.status_code == 200
        before = started.json()
        edited_content = dict(before["content"])
        edited_content["stem"] = "This edit must roll back when feedback cannot be appended."

        async def fail_feedback(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise SubjectQualityFeedbackPersistenceError("injected failure")

        monkeypatch.setattr(SubjectQualityFeedbackService, "record_action", fail_feedback)
        failed = client.patch(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}",
            json={
                "expected_version": before["version"],
                "reason_code": "ambiguous_wording",
                "note": "This note must not survive a rollback.",
                "content": edited_content,
            },
            headers=REVIEWER_HEADERS,
        )
        assert failed.status_code == 409
        assert failed.json()["detail"]["code"] == "quality_feedback_persistence_conflict"
        refreshed = client.get(
            f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS
        ).json()["questions"][0]
        assert refreshed["version"] == before["version"]
        assert refreshed["aggregate_slot_version"] == before["aggregate_slot_version"]
        assert refreshed["content"] == before["content"]
        candidate_id = UUID(question["technical_details"]["candidate_id"])

    async def feedback_count() -> int:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return int(
                    await session.scalar(
                        select(func.count(SubjectQualityFeedbackModel.id)).where(
                            SubjectQualityFeedbackModel.candidate_id == candidate_id
                        )
                    )
                    or 0
                )
        finally:
            await engine.dispose()

    assert asyncio.run(feedback_count()) == 0


@pytest.mark.integration
def test_feedback_promotion_second_reviewer_cas_export_replay_and_append_only_guards(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "lesson_range", "start_lesson": 1, "end_lesson": 2},
                question_count=2,
            ),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "quality-feedback-approval-key"},
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
        terminal = asyncio.run(
            advance_and_run_slots(
                aggregate_seed,
                job_id,
                paper_dispatcher,
                generation_dispatcher,
            )
        )
        assert terminal.status == "ready_for_review"
        detail = client.get(
            f"/api/v1/admin/review-papers/{job_id}",
            headers=REVIEWER_HEADERS,
        ).json()
        question = detail["questions"][0]
        started = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/start",
            json={"expected_version": question["version"]},
            headers=REVIEWER_HEADERS,
        )
        assert started.status_code == 200
        approved = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/approve",
            json={
                "expected_version": started.json()["version"],
                "marking_confirmed": True,
                "note": "Checked the answer, marking, wording, and reviewed source.",
            },
            headers=REVIEWER_HEADERS,
        )
        assert approved.status_code == 200, approved.json()
        feedback_id = UUID(approved.json()["quality_feedback_id"])

        listed = client.get(
            "/api/v1/admin/subject-quality/feedback",
            params={"candidate_id": question["technical_details"]["candidate_id"], "limit": 10},
            headers=REVIEWER_HEADERS,
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        feedback = listed.json()["items"][0]
        assert feedback["id"] == str(feedback_id)
        assert feedback["action"] == "approve"
        assert feedback["reason_code"] == "confirmed_quality"
        assert feedback["note"] == "Checked the answer, marking, wording, and reviewed source."
        assert feedback["original_content"] == feedback["current_content"]
        assert feedback["scope"] | {"lesson_id": None, "lesson_number": None} == {
            "grade": 5,
            "medium": "si",
            "subject_code": "MATHEMATICS",
            "curriculum_version_id": str(CURRICULUM_ID),
            "unit_id": str(UNIT_ID),
            "lesson_id": None,
            "lesson_number": None,
        }
        assert feedback["scope"]["lesson_number"] in {1, 2}
        assert feedback["scope"]["lesson_id"] == str(
            LESSON_IDS[feedback["scope"]["lesson_number"] - 1]
        )
        assert feedback["lineage"]["candidate_id"] == question["technical_details"]["candidate_id"]
        assert feedback["lineage"]["generation_run_id"] == question["id"]
        assert (
            feedback["lineage"]["validation_run_id"]
            == question["technical_details"]["validation_run_id"]
        )
        assert feedback["lineage"]["prompt_version"]
        assert feedback["lineage"]["model_version"]
        assert feedback["lineage"]["retrieval_version"]
        assert feedback["lineage"]["validator_versions"]
        assert feedback["findings_at_action"]["findings"]
        assert feedback["fingerprints"]["feedback"].startswith("sha256:")

        expected_codes = sorted(
            finding["code"]
            for finding in feedback["findings_at_action"]["findings"]
            if finding["status"] != "pass"
        )
        promotion_body = {
            "expected_status": feedback["findings_at_action"]["overall_status"],
            "expected_finding_codes": expected_codes,
            "defect_category": "no_defect",
        }
        promote_headers = {
            **REVIEWER_HEADERS,
            "Idempotency-Key": "promote-confirmed-quality-1",
        }
        promoted = client.post(
            f"/api/v1/admin/subject-quality/feedback/{feedback_id}/promote",
            json=promotion_body,
            headers=promote_headers,
        )
        assert promoted.status_code == 201
        assert promoted.json()["state"] == "draft"
        assert promoted.json()["version"] == 1
        assert promoted.json()["deduplicated"] is False
        eval_case_id = UUID(promoted.json()["eval_case_id"])

        duplicate = client.post(
            f"/api/v1/admin/subject-quality/feedback/{feedback_id}/promote",
            json=promotion_body,
            headers=promote_headers,
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["eval_case_id"] == str(eval_case_id)
        assert duplicate.json()["deduplicated"] is True
        listed_cases = client.get(
            "/api/v1/admin/subject-quality/eval-cases",
            params={"limit": 10, "offset": 0},
            headers=REVIEWER_HEADERS,
        )
        assert listed_cases.status_code == 200
        assert listed_cases.json()["total"] == 1
        feedback_after_promotion = client.get(
            "/api/v1/admin/subject-quality/feedback",
            params={"candidate_id": question["technical_details"]["candidate_id"], "limit": 10},
            headers=REVIEWER_HEADERS,
        ).json()["items"][0]
        assert feedback_after_promotion["promoted_eval_case_id"] == str(eval_case_id)

        same_reviewer = client.post(
            f"/api/v1/admin/subject-quality/eval-cases/{eval_case_id}/approve",
            json={"expected_version": 1},
            headers=REVIEWER_HEADERS,
        )
        assert same_reviewer.status_code == 409
        assert same_reviewer.json()["detail"]["code"] == "eval_case_second_reviewer_required"

        barrier = Barrier(2)

        def approve_case() -> tuple[int, dict[str, object]]:
            barrier.wait()
            response = client.post(
                f"/api/v1/admin/subject-quality/eval-cases/{eval_case_id}/approve",
                json={"expected_version": 1},
                headers={"Authorization": "Bearer admin-token"},
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            approvals = tuple(executor.map(lambda _: approve_case(), range(2)))
        assert sorted(status_code for status_code, _ in approvals) == [200, 409]
        approved_case = next(body for status_code, body in approvals if status_code == 200)
        assert approved_case["state"] == "approved"
        assert approved_case["version"] == 2

        second_question = detail["questions"][1]
        second_started = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{second_question['id']}/start",
            json={"expected_version": second_question["version"]},
            headers=REVIEWER_HEADERS,
        )
        assert second_started.status_code == 200
        second_approved = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{second_question['id']}/approve",
            json={
                "expected_version": second_started.json()["version"],
                "marking_confirmed": True,
                "note": "Confirmed as a second stable quality-evaluation example.",
            },
            headers=REVIEWER_HEADERS,
        )
        assert second_approved.status_code == 200
        second_feedback_id = UUID(second_approved.json()["quality_feedback_id"])
        second_feedback = client.get(
            "/api/v1/admin/subject-quality/feedback",
            params={
                "candidate_id": second_question["technical_details"]["candidate_id"],
                "limit": 10,
            },
            headers=REVIEWER_HEADERS,
        ).json()["items"][0]
        second_expected_codes = sorted(
            finding["code"]
            for finding in second_feedback["findings_at_action"]["findings"]
            if finding["status"] != "pass"
        )
        second_promoted = client.post(
            f"/api/v1/admin/subject-quality/feedback/{second_feedback_id}/promote",
            json={
                "expected_status": second_feedback["findings_at_action"]["overall_status"],
                "expected_finding_codes": second_expected_codes,
                "defect_category": "no_defect",
            },
            headers={
                **REVIEWER_HEADERS,
                "Idempotency-Key": "promote-confirmed-quality-2",
            },
        )
        assert second_promoted.status_code == 201
        second_eval_case_id = UUID(second_promoted.json()["eval_case_id"])
        second_case_approved = client.post(
            f"/api/v1/admin/subject-quality/eval-cases/{second_eval_case_id}/approve",
            json={"expected_version": 1},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert second_case_approved.status_code == 200

        exported = client.get(
            "/api/v1/admin/subject-quality/eval-cases/export",
            params={"limit": 10, "offset": 0},
            headers=REVIEWER_HEADERS,
        )
        assert exported.status_code == 200
        export_body = exported.json()
        assert export_body["schema_version"] == "subject-quality-eval-export.v1"
        exported_case = next(
            item for item in export_body["cases"] if item["eval_case_id"] == str(eval_case_id)
        )
        assert exported_case["expected"]["status"] == promotion_body["expected_status"]
        assert "note" not in str(exported_case).lower()
        assert "pdf_bytes" not in str(exported_case).lower()
        assert "secret" not in str(exported_case).lower()
        assert (
            client.get(
                "/api/v1/admin/subject-quality/eval-cases/export",
                params={"limit": 101},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/v1/admin/subject-quality/eval-cases/export",
                params={"offset": 100_001},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 422
        )
        assert client.get("/api/v1/admin/subject-quality/feedback", headers={}).status_code == 401
        missing_id = UUID(int=26_999_999)
        assert (
            client.post(
                f"/api/v1/admin/subject-quality/feedback/{missing_id}/promote",
                json=promotion_body,
                headers={**REVIEWER_HEADERS, "Idempotency-Key": "missing-feedback"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/admin/subject-quality/eval-cases/{missing_id}/approve",
                json={"expected_version": 1},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/admin/subject-quality/eval-runs",
                json={"case_ids": [str(missing_id)]},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/admin/subject-quality/eval-runs/{missing_id}",
                headers=REVIEWER_HEADERS,
            ).status_code
            == 404
        )

        replayed = client.post(
            "/api/v1/admin/subject-quality/eval-runs",
            json={"case_ids": [str(eval_case_id)]},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert replayed.status_code == 201, replayed.json()
        assert replayed.json()["runner_version"] == "subject-quality-eval-runner.v1"
        assert replayed.json()["results"][0]["outcome"] == "unavailable"
        assert replayed.json()["results"][0]["passed"] is False
        assert replayed.json()["results"][0]["fingerprint"].startswith("sha256:")
        assert replayed.json()["results"][0]["validator_versions"]
        run_id = UUID(replayed.json()["run_id"])
        fetched_run = client.get(
            f"/api/v1/admin/subject-quality/eval-runs/{run_id}",
            headers=REVIEWER_HEADERS,
        )
        assert fetched_run.status_code == 200
        assert fetched_run.json()["request_fingerprint"] == replayed.json()["request_fingerprint"]
        duplicate_run = client.post(
            "/api/v1/admin/subject-quality/eval-runs",
            json={"case_ids": [str(eval_case_id)]},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert duplicate_run.status_code == 201
        assert duplicate_run.json()["deduplicated"] is True
        overlapping_run = client.post(
            "/api/v1/admin/subject-quality/eval-runs",
            json={"case_ids": [str(eval_case_id), str(second_eval_case_id)]},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert overlapping_run.status_code == 201, overlapping_run.json()
        assert len(overlapping_run.json()["results"]) == 2
        first_overlapping_result = next(
            result
            for result in overlapping_run.json()["results"]
            if result["eval_case_id"] == str(eval_case_id)
        )
        assert (
            first_overlapping_result["fingerprint"] != replayed.json()["results"][0]["fingerprint"]
        )

    async def inspect_and_attack_append_only_state() -> tuple[int, int, int, int]:
        engine = create_async_engine(aggregate_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                counts = (
                    int(
                        await session.scalar(
                            select(func.count(SubjectQualityFeedbackModel.id)).where(
                                SubjectQualityFeedbackModel.id == feedback_id
                            )
                        )
                        or 0
                    ),
                    int(
                        await session.scalar(
                            select(
                                func.count(SubjectQualityEvalCaseVersionModel.eval_case_id)
                            ).where(SubjectQualityEvalCaseVersionModel.eval_case_id == eval_case_id)
                        )
                        or 0
                    ),
                    int(
                        await session.scalar(
                            select(func.count(SubjectQualityEvalRunModel.id)).where(
                                SubjectQualityEvalRunModel.id == run_id
                            )
                        )
                        or 0
                    ),
                    int(
                        await session.scalar(
                            select(func.count(SubjectQualityEvalResultModel.id)).where(
                                SubjectQualityEvalResultModel.eval_run_id == run_id
                            )
                        )
                        or 0
                    ),
                )
            attacks = (
                (
                    "UPDATE subject_quality_feedback SET note = 'rewritten' WHERE id = :id",
                    feedback_id,
                ),
                ("DELETE FROM subject_quality_feedback WHERE id = :id", feedback_id),
                (
                    "UPDATE subject_quality_eval_case_versions SET state = 'draft' "
                    "WHERE eval_case_id = :id AND version = 2",
                    eval_case_id,
                ),
                (
                    "DELETE FROM subject_quality_eval_case_versions WHERE eval_case_id = :id",
                    eval_case_id,
                ),
                ("DELETE FROM subject_quality_eval_runs WHERE id = :id", run_id),
                ("DELETE FROM subject_quality_eval_results WHERE eval_run_id = :id", run_id),
            )
            for statement, identifier in attacks:
                async with sessions() as session:
                    with pytest.raises(DBAPIError):
                        await session.execute(text(statement), {"id": identifier})
                    await session.rollback()
            return counts
        finally:
            await engine.dispose()

    assert asyncio.run(inspect_and_attack_append_only_state()) == (1, 2, 1, 1)


@pytest.mark.integration
def test_exact_slot_without_active_reviewed_context_fails_safely_without_generic_fallback(
    aggregate_seed: Seed,
) -> None:
    lesson_id = UUID(int=25_130_001)
    skill_id = UUID(int=25_130_002)

    async def add_unbacked_lesson() -> None:
        engine = create_async_engine(aggregate_seed.database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add(
                    TaxonomyNodeModel(
                        id=skill_id,
                        curriculum_version_id=CURRICULUM_ID,
                        parent_id=COMPETENCY_ID,
                        level=TaxonomyLevel.SKILL,
                        code="S4",
                        title="Unbacked lesson skill",
                        active=True,
                        review_state=TaxonomyReviewState.REVIEWED,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                session.add(
                    CurriculumLessonModel(
                        id=lesson_id,
                        curriculum_version_id=CURRICULUM_ID,
                        unit_id=UNIT_ID,
                        code="LESSON-4",
                        title="No reviewed source",
                        ordinal=4,
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.flush()
                session.add(
                    CurriculumLessonTaxonomyMappingModel(
                        lesson_id=lesson_id,
                        curriculum_version_id=CURRICULUM_ID,
                        unit_id=UNIT_ID,
                        taxonomy_node_id=skill_id,
                        created_by=ADMIN_ID,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(add_unbacked_lesson())
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(
                scope={"kind": "lesson_range", "start_lesson": 4, "end_lesson": 4},
                question_count=1,
            ),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "no-context-key"},
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])

        async def advance() -> TeacherPaperJobModel:
            engine = create_async_engine(aggregate_seed.database_url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    settings = aggregate_seed.settings
                    worker = TeacherPaperWorkerService(
                        session,
                        paper_dispatcher,
                        generation_dispatcher,
                        create_generation_runtime(settings),
                        create_embedding_provider_registry(settings),
                        build_default_pipeline(),
                    )
                    assert await worker.advance(job_id) is True
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    job = await session.get(TeacherPaperJobModel, job_id)
                    assert job is not None
                    return job
            finally:
                await engine.dispose()

        failed = asyncio.run(advance())
        assert failed.status == "failed"
        assert failed.failure_code == "paper_generation_context_unavailable"
        assert generation_dispatcher.dispatched == []


@pytest.mark.integration
def test_guarded_downgrade_refuses_to_destroy_quality_and_teacher_lineage(
    aggregate_seed: Seed,
) -> None:
    with pytest.raises(RuntimeError, match="subject-quality evidence"):
        command.downgrade(
            _config_for_database(aggregate_seed.database_url),
            "0024_subject_quality_validation_scope",
        )
    assert_database_schema_current(aggregate_seed.database_url)


@pytest.mark.integration
def test_teacher_paper_commands_enforce_authentication_authorization_idempotency_and_rate_limits(
    aggregate_seed: Seed,
) -> None:
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        unauthenticated = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={"Idempotency-Key": "unauthenticated-key"},
        )
        assert unauthenticated.status_code == 401
        forbidden = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={
                "Authorization": "Bearer reviewer-token",
                "Idempotency-Key": "reviewer-forbidden-key",
            },
        )
        assert forbidden.status_code == 403
        missing_key = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={"Authorization": "Bearer admin-token"},
        )
        assert missing_key.status_code == 422
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "manual-advance-key"},
        )
        assert created.status_code == 202
        advanced = client.post(
            f"/api/v1/admin/paper-generation/jobs/{created.json()['job_id']}/advance",
            json={"expected_version": created.json()["version"]},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert advanced.status_code == 202
        assert advanced.json()["version"] == created.json()["version"] + 1

    with api_client(
        aggregate_seed,
        paper_dispatcher,
        generation_dispatcher,
        rate_limiter=DenyGenerationRateLimiter(),
    ) as client:
        limited = client.post(
            "/api/v1/admin/paper-generation/jobs",
            json=request_payload(),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "limited-key"},
        )
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "17"
        assert limited.json()["detail"] == {
            "code": "rate_limit_exceeded",
            "scope": "generation_create_retry",
        }
