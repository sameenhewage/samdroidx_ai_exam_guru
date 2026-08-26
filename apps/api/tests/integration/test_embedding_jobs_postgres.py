import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest
from dramatiq import Worker
from dramatiq.brokers.redis import RedisBroker
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.auth.rate_limits import NoOpRateLimiter
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
from exam_guru_api.knowledge import embedding_jobs as jobs
from exam_guru_api.knowledge.domain import ChunkType, QuestionType, ReviewState
from exam_guru_api.knowledge.embedding_job_repository import SqlAlchemyEmbeddingJobRepository
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingRecoveryPolicy,
    EmbeddingRecoveryResult,
    EmbeddingRecoveryService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import (
    EMBEDDING_QUEUE_NAME,
    DeterministicEmbeddingDispatcher,
    EmbeddingDispatcher,
    create_embedding_dispatcher,
)
from exam_guru_api.knowledge.embeddings import DeterministicEmbeddingProvider, EmbeddingConfig
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    EmbeddingJobModel,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.main import create_app
from exam_guru_api.retrieval.embeddings import (
    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
    EmbeddingProvider,
    EmbeddingProviderRegistry,
)
from exam_guru_api.retrieval.explorer import RetrievalExplorerService
from exam_guru_api.retrieval.schemas import RetrievalExploreRequest

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
VALKEY_IMAGE = "valkey/valkey:9.1.1-alpine3.24"
ADMIN_ID = UUID(int=1_820_001)
REVIEWER_ID = UUID(int=1_820_002)
CURRICULUM_ID = UUID(int=1_820_010)
OTHER_CURRICULUM_ID = UUID(int=1_820_011)
EXAM_ID = UUID(int=1_820_012)
MEDIUM_ID = UUID(int=1_820_013)
OTHER_EXAM_ID = UUID(int=1_820_014)
OTHER_MEDIUM_ID = UUID(int=1_820_015)
COMPETENCY_ID = UUID(int=1_820_020)
OTHER_COMPETENCY_ID = UUID(int=1_820_021)
CONFLICT_COMPETENCY_ID = UUID(int=1_820_022)
QUESTION_BASIC_ID = UUID(int=1_820_101)
CHUNK_BASIC_ID = UUID(int=1_820_102)
QUESTION_PARTIAL_ID = UUID(int=1_820_103)
CHUNK_PARTIAL_ID = UUID(int=1_820_104)
CHUNK_CONFLICT_ID = UUID(int=1_820_105)
CHUNK_RETRIEVAL_ID = UUID(int=1_820_106)
CHUNK_VALKEY_ID = UUID(int=1_820_107)
QUESTION_UNREVIEWED_ID = UUID(int=1_820_108)
OTHER_CHUNK_ID = UUID(int=1_820_109)
CHUNK_CONCURRENT_ID = UUID(int=1_820_110)
CHUNK_LOCK_FAILURE_ID = UUID(int=1_820_111)
CHUNK_STALE_PARTIAL_A_ID = UUID(int=1_820_112)
CHUNK_STALE_PARTIAL_B_ID = UUID(int=1_820_113)
CHUNK_LEASE_STALE_ID = UUID(int=1_820_114)
CHUNK_LEASE_BOUNDARY_ID = UUID(int=1_820_115)
CHUNK_LEASE_FRESH_ID = UUID(int=1_820_116)
CHUNK_RECOVERY_A_ID = UUID(int=1_820_117)
CHUNK_RECOVERY_B_ID = UUID(int=1_820_118)
CHUNK_LATE_WORKER_ID = UUID(int=1_820_119)
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
BASE_PATH = f"/api/v1/admin/curricula/{CURRICULUM_ID}/embedding-jobs"


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(subject_id=ADMIN_ID, roles=frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(subject_id=REVIEWER_ID, roles=frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class DatabaseTestResources:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        await self.engine.dispose()


class RecordingProvider:
    def __init__(self, *, fail_call: int | None = None) -> None:
        self.calls: list[str] = []
        self._fail_call = fail_call
        self._lock = threading.Lock()

    def embed(self, value: str, config: EmbeddingConfig):  # type: ignore[no-untyped-def]
        with self._lock:
            self.calls.append(value)
            call_number = len(self.calls)
        if call_number == self._fail_call:
            raise RuntimeError("provider secret must never escape")
        return DeterministicEmbeddingProvider().embed(value, config)


class BlockingCountingProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.first_call_started = threading.Event()
        self.second_call_started = threading.Event()
        self.release_first_call = threading.Event()
        self._lock = threading.Lock()

    def embed(self, value: str, config: EmbeddingConfig):  # type: ignore[no-untyped-def]
        with self._lock:
            self.calls.append(value)
            call_number = len(self.calls)
        if call_number == 1:
            self.first_call_started.set()
            if not self.release_first_call.wait(timeout=10):
                raise RuntimeError("test provider release timed out")
        else:
            self.second_call_started.set()
        return DeterministicEmbeddingProvider().embed(value, config)


class BlockingFailProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.call_started = threading.Event()
        self.release_call = threading.Event()

    def embed(self, value: str, config: EmbeddingConfig):  # type: ignore[no-untyped-def]
        del config
        self.calls.append(value)
        self.call_started.set()
        if not self.release_call.wait(timeout=10):
            raise RuntimeError("test provider release timed out")
        raise RuntimeError("provider secret must never escape")


class FailOnceDispatcher:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def dispatch(self, job_id: UUID) -> str:
        self.calls.append(job_id)
        if len(self.calls) == 1:
            raise RuntimeError("redis password must never escape")
        return "recovered-message"


class BarrierDispatcher:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.calls: list[UUID] = []
        self._lock = threading.Lock()

    def dispatch(self, job_id: UUID) -> str:
        with self._lock:
            self.calls.append(job_id)
            number = len(self.calls)
        self.barrier.wait(timeout=10)
        return f"race-message-{number}"


@dataclass(frozen=True, slots=True)
class Seed:
    database_url: str
    valkey_url: str
    texts: dict[UUID, str]


async def _seed_curriculum(
    session: AsyncSession,
    *,
    curriculum_id: UUID,
    exam_id: UUID,
    medium_id: UUID,
    competency_id: UUID,
    suffix: str,
) -> None:
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code=f"G5E-{suffix.upper()}",
                name=f"Embedding exam {suffix}",
                grade=5,
                active=True,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            ),
            MediumModel(
                id=medium_id,
                code=f"em-{suffix}",
                name=f"Embedding medium {suffix}",
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
            code=f"EMBED-{suffix.upper()}",
            title=f"Embedding curriculum {suffix}",
            active=True,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    session.add(
        TaxonomyNodeModel.from_domain(
            TaxonomyNode(
                id=competency_id,
                curriculum_version_id=curriculum_id,
                level=TaxonomyLevel.COMPETENCY,
                code="C1",
                title="Numbers competency",
                review_state=TaxonomyReviewState.REVIEWED,
            ),
            ADMIN_ID,
        )
    )
    await session.flush()


async def _seed_source(
    session: AsyncSession,
    *,
    offset: int,
    curriculum_id: UUID,
    document_type: SourceDocumentType,
    value: str,
) -> tuple[UUID, UUID]:
    document_id = UUID(int=1_821_000 + offset * 3)
    page_id = UUID(int=1_821_001 + offset * 3)
    block_id = UUID(int=1_821_002 + offset * 3)
    document = SourceDocumentModel(
        id=document_id,
        checksum_sha256=sha256(f"embedding-source-{offset}".encode()).hexdigest(),
        object_key=f"sources/embedding-{offset}.pdf",
        original_filename=f"embedding-{offset}.pdf",
        content_type="application/pdf",
        size_bytes=len(value.encode()),
        document_type=document_type,
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        curriculum_version_id=curriculum_id,
        year=2020 if document_type is SourceDocumentType.PAST_PAPER else None,
        paper_code="P1" if document_type is SourceDocumentType.PAST_PAPER else None,
        extraction_attempt_count=1,
        extraction_started_at=datetime.now(UTC),
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
            raw_text=value,
            reviewed_text=value,
            character_count=len(value),
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
            raw_text=value,
            reviewed_text=value,
            character_count=len(value),
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
    document.extracted_character_count = len(value)
    document.native_text_page_ratio = 1.0
    document.needs_ocr = False
    document.ocr_page_count = 0
    document.extraction_config = {}
    document.extraction_completed_at = datetime.now(UTC)
    await session.flush()
    document.extraction_status = ExtractionStatus.IN_REVIEW
    await session.flush()
    document.extraction_status = ExtractionStatus.TRUSTED
    await session.flush()
    return document_id, block_id


async def _seed_question(
    session: AsyncSession,
    *,
    identifier: UUID,
    offset: int,
    value: str,
    review_state: ReviewState = ReviewState.REVIEWED,
) -> None:
    document_id, block_id = await _seed_source(
        session,
        offset=offset,
        curriculum_id=CURRICULUM_ID,
        document_type=SourceDocumentType.PAST_PAPER,
        value=value,
    )
    session.add(
        HistoricalQuestionModel(
            id=identifier,
            curriculum_version_id=CURRICULUM_ID,
            year=2020,
            paper_code="P1",
            question_number=f"Q{offset}",
            text=value,
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            source_document_id=document_id,
            page_number=1,
            source_block_id=block_id,
            review_state=review_state,
            competency_id=COMPETENCY_ID,
            version=0,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()


async def _seed_chunk(
    session: AsyncSession,
    *,
    identifier: UUID,
    offset: int,
    value: str,
    curriculum_id: UUID = CURRICULUM_ID,
    competency_id: UUID = COMPETENCY_ID,
) -> None:
    document_id, block_id = await _seed_source(
        session,
        offset=offset,
        curriculum_id=curriculum_id,
        document_type=SourceDocumentType.SYLLABUS,
        value=value,
    )
    session.add(
        KnowledgeChunkModel(
            id=identifier,
            curriculum_version_id=curriculum_id,
            chunk_type=ChunkType.EXPLANATION,
            text=value,
            educational_boundary="Numbers",
            sequence=0,
            source_document_id=document_id,
            page_number=1,
            source_block_id=block_id,
            review_state=ReviewState.REVIEWED,
            competency_id=competency_id,
            version=0,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()


@pytest.fixture(scope="module")
def embedding_seed() -> Iterator[Seed]:
    credentials = ("exam_guru", "embedding-jobs-only")
    with (
        PostgresContainer(
            image=PGVECTOR_IMAGE,
            username=credentials[0],
            password=credentials[1],
            dbname="exam_guru_embedding_jobs_test",
            driver="asyncpg",
        ) as postgres,
        RedisContainer(image=VALKEY_IMAGE) as valkey,
    ):
        database_url = postgres.get_connection_url()
        valkey_url = f"redis://{valkey.get_container_host_ip()}:{valkey.get_exposed_port(6379)}/0"
        upgrade_database(database_url)
        assert_database_schema_current(database_url)
        texts = {
            QUESTION_BASIC_ID: "What is the value of 7 plus 5?",
            CHUNK_BASIC_ID: "Addition combines two quantities into a total.",
            QUESTION_PARTIAL_ID: "What is the value of 9 minus 4?",
            CHUNK_PARTIAL_ID: "Subtraction finds a difference between quantities.",
            CHUNK_CONFLICT_ID: "A triangle has three straight sides.",
            CHUNK_RETRIEVAL_ID: "A square perimeter is the sum of four equal side lengths.",
            CHUNK_VALKEY_ID: "Ten ones make one group of ten.",
            QUESTION_UNREVIEWED_ID: "This record is still awaiting review.",
            OTHER_CHUNK_ID: "This record belongs to another curriculum.",
            CHUNK_CONCURRENT_ID: "Concurrency uses one durable source-row lock.",
            CHUNK_LOCK_FAILURE_ID: "Provider failure releases the durable source-row lock.",
            CHUNK_STALE_PARTIAL_A_ID: "A stale partial job already embedded this first source.",
            CHUNK_STALE_PARTIAL_B_ID: "A stale partial retry must embed this second source.",
            CHUNK_LEASE_STALE_ID: "A strictly old claim expires.",
            CHUNK_LEASE_BOUNDARY_ID: "A claim exactly at the lease boundary stays claimed.",
            CHUNK_LEASE_FRESH_ID: "A fresh claim stays claimed.",
            CHUNK_RECOVERY_A_ID: "Concurrent recovery claim A.",
            CHUNK_RECOVERY_B_ID: "Concurrent recovery claim B.",
            CHUNK_LATE_WORKER_ID: "A late expired worker must roll back this embedding.",
        }

        async def seed() -> None:
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                await _seed_curriculum(
                    session,
                    curriculum_id=CURRICULUM_ID,
                    exam_id=EXAM_ID,
                    medium_id=MEDIUM_ID,
                    competency_id=COMPETENCY_ID,
                    suffix="one",
                )
                session.add(
                    TaxonomyNodeModel.from_domain(
                        TaxonomyNode(
                            id=CONFLICT_COMPETENCY_ID,
                            curriculum_version_id=CURRICULUM_ID,
                            level=TaxonomyLevel.COMPETENCY,
                            code="C2",
                            title="Geometry competency",
                            review_state=TaxonomyReviewState.REVIEWED,
                        ),
                        ADMIN_ID,
                    )
                )
                await session.flush()
                await _seed_curriculum(
                    session,
                    curriculum_id=OTHER_CURRICULUM_ID,
                    exam_id=OTHER_EXAM_ID,
                    medium_id=OTHER_MEDIUM_ID,
                    competency_id=OTHER_COMPETENCY_ID,
                    suffix="two",
                )
                await _seed_question(
                    session,
                    identifier=QUESTION_BASIC_ID,
                    offset=1,
                    value=texts[QUESTION_BASIC_ID],
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_BASIC_ID,
                    offset=2,
                    value=texts[CHUNK_BASIC_ID],
                )
                await _seed_question(
                    session,
                    identifier=QUESTION_PARTIAL_ID,
                    offset=3,
                    value=texts[QUESTION_PARTIAL_ID],
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_PARTIAL_ID,
                    offset=4,
                    value=texts[CHUNK_PARTIAL_ID],
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_CONFLICT_ID,
                    offset=5,
                    value=texts[CHUNK_CONFLICT_ID],
                    competency_id=CONFLICT_COMPETENCY_ID,
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_RETRIEVAL_ID,
                    offset=6,
                    value=texts[CHUNK_RETRIEVAL_ID],
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_VALKEY_ID,
                    offset=7,
                    value=texts[CHUNK_VALKEY_ID],
                )
                await _seed_question(
                    session,
                    identifier=QUESTION_UNREVIEWED_ID,
                    offset=8,
                    value=texts[QUESTION_UNREVIEWED_ID],
                    review_state=ReviewState.IN_REVIEW,
                )
                await _seed_chunk(
                    session,
                    identifier=OTHER_CHUNK_ID,
                    offset=9,
                    value=texts[OTHER_CHUNK_ID],
                    curriculum_id=OTHER_CURRICULUM_ID,
                    competency_id=OTHER_COMPETENCY_ID,
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_CONCURRENT_ID,
                    offset=10,
                    value=texts[CHUNK_CONCURRENT_ID],
                )
                await _seed_chunk(
                    session,
                    identifier=CHUNK_LOCK_FAILURE_ID,
                    offset=11,
                    value=texts[CHUNK_LOCK_FAILURE_ID],
                )
                for offset, identifier in enumerate(
                    (
                        CHUNK_STALE_PARTIAL_A_ID,
                        CHUNK_STALE_PARTIAL_B_ID,
                        CHUNK_LEASE_STALE_ID,
                        CHUNK_LEASE_BOUNDARY_ID,
                        CHUNK_LEASE_FRESH_ID,
                        CHUNK_RECOVERY_A_ID,
                        CHUNK_RECOVERY_B_ID,
                        CHUNK_LATE_WORKER_ID,
                    ),
                    start=12,
                ):
                    await _seed_chunk(
                        session,
                        identifier=identifier,
                        offset=offset,
                        value=texts[identifier],
                    )
                await session.commit()
            await engine.dispose()

        asyncio.run(seed())
        yield Seed(database_url=database_url, valkey_url=valkey_url, texts=texts)


@pytest.mark.integration
def test_embedding_job_migration_has_exact_durable_columns_function_and_triggers(
    embedding_seed: Seed,
) -> None:
    async def inspect() -> tuple[set[str], set[str], set[str], str | None]:
        engine = create_async_engine(embedding_seed.database_url)
        async with engine.connect() as connection:
            columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'embedding_jobs'"
                    )
                )
            )
            constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'embedding_jobs'::regclass"
                    )
                )
            )
            triggers = set(
                await connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE tgrelid = 'embedding_jobs'::regclass AND NOT tgisinternal"
                    )
                )
            )
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        await engine.dispose()
        return columns, constraints, triggers, cast(str | None, revision)

    columns, constraints, triggers, revision = asyncio.run(inspect())
    assert revision == "0023_teacher_first_multi_grade_foundation"
    assert columns == {
        "id",
        "curriculum_version_id",
        "retry_of_job_id",
        "retry_depth",
        "historical_question_ids",
        "knowledge_chunk_ids",
        "idempotency_key_hash",
        "request_fingerprint",
        "source_fingerprint",
        "provider",
        "model",
        "dimension",
        "embedding_version",
        "config_fingerprint",
        "status",
        "version",
        "queue_message_id",
        "requested_count",
        "embedded_count",
        "deduplicated_count",
        "failure_code",
        "created_by",
        "created_at",
        "updated_at",
        "claimed_at",
        "completed_at",
    }
    assert {
        "uq_embedding_jobs_actor_idempotency",
        "fk_embedding_jobs_retry_curriculum",
        "ck_embedding_jobs_record_ids",
        "ck_embedding_jobs_fingerprints",
        "ck_embedding_jobs_counts",
        "ck_embedding_jobs_retry_depth",
        "ck_embedding_jobs_state_data",
    } <= constraints
    assert triggers == {
        "enforce_embedding_job_insert_trigger",
        "enforce_embedding_job_retry_lineage_insert_trigger",
        "enforce_embedding_job_update_trigger",
        "reject_embedding_job_delete_trigger",
        "reject_embedding_job_retry_depth_update_trigger",
    }


def _registry(provider: object | None = None) -> EmbeddingProviderRegistry:
    return EmbeddingProviderRegistry(
        {
            "deterministic": cast(
                EmbeddingProvider,
                provider or DeterministicEmbeddingProvider(),
            )
        }
    )


def _client(
    seed: Seed,
    dispatcher: EmbeddingDispatcher,
    *,
    provider: object | None = None,
    settings: object | None = None,
) -> TestClient:
    from exam_guru_api.core.config import Settings

    resolved = cast(
        Settings,
        settings
        or Settings(
            environment="test",
            database_url=SecretStr(seed.database_url),
            valkey_url=SecretStr(seed.valkey_url),
        ),
    )
    return TestClient(
        create_app(
            settings=resolved,
            identity_provider=StaticIdentityProvider(),
            resource_factory=lambda _: DatabaseTestResources(seed.database_url),
            embedding_dispatcher=dispatcher,
            embedding_provider_registry=_registry(provider),
            rate_limiter=NoOpRateLimiter(),
        )
    )


def _payload(question_id: UUID, chunk_id: UUID) -> dict[str, list[str]]:
    return {
        "historical_question_ids": [str(question_id)],
        "knowledge_chunk_ids": [str(chunk_id)],
    }


def _chunk_payload(chunk_id: UUID) -> dict[str, list[str]]:
    return {
        "historical_question_ids": [],
        "knowledge_chunk_ids": [str(chunk_id)],
    }


def _fail_embedding_job_for_retry(seed: Seed, job_id: UUID) -> bool:
    async def process() -> bool:
        engine = create_async_engine(seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return await EmbeddingWorkerService(session, _registry(), None).process(job_id)
        finally:
            await engine.dispose()

    return asyncio.run(process())


def _run_embedding_worker(seed: Seed, job_id: UUID, provider: object) -> bool:
    async def process() -> bool:
        engine = create_async_engine(seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                return await EmbeddingWorkerService(
                    session,
                    _registry(provider),
                    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
                ).process(job_id)
        finally:
            await engine.dispose()

    return asyncio.run(process())


async def _read_jobs_and_embedding_count(
    seed: Seed,
    job_ids: tuple[UUID, UUID],
    chunk_id: UUID,
) -> tuple[EmbeddingJobModel, EmbeddingJobModel, int]:
    engine = create_async_engine(seed.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            first = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, job_ids[0]))
            second = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, job_ids[1]))
            count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeEmbeddingModel)
                    .where(KnowledgeEmbeddingModel.knowledge_chunk_id == chunk_id)
                )
                or 0
            )
            return first, second, count
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_api_authorization_scope_idempotency_listing_and_sanitized_contract(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("api-message")
    payload = _payload(QUESTION_BASIC_ID, CHUNK_BASIC_ID)
    with _client(embedding_seed, dispatcher) as client:
        assert client.post(BASE_PATH, json=payload).status_code == 401
        assert (
            client.post(
                BASE_PATH,
                json=payload,
                headers={**REVIEWER_HEADERS, "Idempotency-Key": "reviewer-forbidden"},
            ).status_code
            == 403
        )
        created = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-api-basic"},
        )
        repeated = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-api-basic"},
        )
        changed = client.post(
            BASE_PATH,
            json={"historical_question_ids": [str(QUESTION_BASIC_ID)], "knowledge_chunk_ids": []},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-api-basic"},
        )
        nonreviewed = client.post(
            BASE_PATH,
            json={
                "historical_question_ids": [str(QUESTION_UNREVIEWED_ID)],
                "knowledge_chunk_ids": [],
            },
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-nonreviewed"},
        )
        cross_scope = client.post(
            BASE_PATH,
            json={"historical_question_ids": [], "knowledge_chunk_ids": [str(OTHER_CHUNK_ID)]},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-cross-scope"},
        )
        job_id = created.json()["id"]
        read = client.get(f"{BASE_PATH}/{job_id}", headers=REVIEWER_HEADERS)
        idor = client.get(
            f"/api/v1/admin/curricula/{OTHER_CURRICULUM_ID}/embedding-jobs/{job_id}",
            headers=REVIEWER_HEADERS,
        )
        listed = client.get(
            BASE_PATH,
            params={"status": "queued", "limit": 1, "offset": 0},
            headers=REVIEWER_HEADERS,
        )

    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["queue_message_id"] == "api-message"
    assert body["configuration"] == {
        "provider": DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG.provider,
        "model": DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG.model,
        "dimension": DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG.dimension,
        "version": DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG.version,
        "config_fingerprint": DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG.config_fingerprint,
    }
    assert body["counts"] == {"requested": 2, "embedded": 0, "deduplicated": 0}
    assert "text" not in str(body).lower()
    assert "vector" not in str(body).lower()
    assert repeated.status_code == 202
    assert repeated.json()["id"] == body["id"]
    assert repeated.json()["deduplicated"] is True
    assert dispatcher.dispatched == [UUID(body["id"])]
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "embedding_idempotency_conflict"
    assert nonreviewed.status_code == 422
    assert nonreviewed.json()["detail"]["code"] == "embedding_source_not_reviewed"
    assert cross_scope.status_code == 404
    assert cross_scope.json()["detail"]["code"] == "embedding_source_not_found"
    assert read.status_code == 200
    assert idor.status_code == 404
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]

    from exam_guru_api.core.config import Settings

    staging_settings = Settings(
        environment="staging",
        database_url=SecretStr(embedding_seed.database_url),
        valkey_url=SecretStr(embedding_seed.valkey_url),
    )
    with _client(
        embedding_seed,
        dispatcher,
        settings=staging_settings,
    ) as staging_client:
        staging_read = staging_client.get(f"{BASE_PATH}/{body['id']}", headers=REVIEWER_HEADERS)
    assert staging_read.status_code == 200
    assert staging_read.json()["id"] == body["id"]

    async def audit() -> None:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            events = tuple(
                await session.scalars(
                    select(AdminAuditEventModel).where(
                        AdminAuditEventModel.resource_id == UUID(body["id"])
                    )
                )
            )
        await engine.dispose()
        assert [event.action for event in events] == ["embedding_job.created"]
        rendered = str([event.payload for event in events]).lower()
        assert "provider secret" not in rendered
        assert "vector" not in rendered
        assert embedding_seed.texts[QUESTION_BASIC_ID].lower() not in rendered

    asyncio.run(audit())


@pytest.mark.integration
def test_embedding_automatic_retry_chain_is_bounded_and_replay_deduplicates_first(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("embedding-depth-message")
    request = {
        "historical_question_ids": [str(QUESTION_BASIC_ID)],
        "knowledge_chunk_ids": [str(CHUNK_RECOVERY_A_ID), str(CHUNK_RECOVERY_B_ID)],
    }
    chain: list[str] = []
    keys = ["embedding-depth-root", *(f"embedding-depth-retry-{value}" for value in range(1, 4))]

    for expected_depth, key in enumerate(keys):
        with _client(embedding_seed, dispatcher) as client:
            created = client.post(
                BASE_PATH,
                json=request,
                headers={**ADMIN_HEADERS, "Idempotency-Key": key},
            )
        assert created.status_code == 202, created.text
        body = created.json()
        assert body["retry_depth"] == expected_depth
        assert body["retry_of_job_id"] == (None if expected_depth == 0 else chain[-1])
        chain.append(body["id"])
        assert _fail_embedding_job_for_retry(embedding_seed, UUID(body["id"]))

    with _client(embedding_seed, dispatcher) as client:
        replay = client.post(
            BASE_PATH,
            json=request,
            headers={**ADMIN_HEADERS, "Idempotency-Key": keys[-1]},
        )
        fourth = client.post(
            BASE_PATH,
            json=request,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-depth-retry-4"},
        )

    assert replay.status_code == 202
    assert replay.json()["id"] == chain[-1]
    assert replay.json()["deduplicated"] is True
    assert replay.json()["retry_depth"] == 3
    assert fourth.status_code == 409
    assert fourth.json()["detail"]["code"] == "embedding_retry_limit_exceeded"
    assert len(dispatcher.dispatched) == 4


@pytest.mark.integration
def test_concurrent_embedding_retry_fork_dispatches_exactly_one_child(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("embedding-fork-message")
    request = {
        "historical_question_ids": [str(QUESTION_PARTIAL_ID)],
        "knowledge_chunk_ids": [str(CHUNK_LEASE_BOUNDARY_ID), str(CHUNK_LEASE_FRESH_ID)],
    }
    with _client(embedding_seed, dispatcher) as client:
        root = client.post(
            BASE_PATH,
            json=request,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-fork-root"},
        )
    root_id = UUID(root.json()["id"])
    assert _fail_embedding_job_for_retry(embedding_seed, root_id)
    dispatcher.dispatched.clear()
    barrier = threading.Barrier(2)

    with _client(embedding_seed, dispatcher) as client:

        def retry_once(index: int) -> tuple[int, dict[str, object]]:
            barrier.wait()
            response = client.post(
                BASE_PATH,
                json=request,
                headers={**ADMIN_HEADERS, "Idempotency-Key": f"embedding-fork-child-{index}"},
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(retry_once, range(2)))

    assert sorted(status_code for status_code, _ in results) == [202, 409]
    assert len(dispatcher.dispatched) == 1

    async def child_count() -> int:
        engine = create_async_engine(embedding_seed.database_url)
        try:
            async with engine.connect() as connection:
                return int(
                    await connection.scalar(
                        select(func.count())
                        .select_from(EmbeddingJobModel)
                        .where(EmbeddingJobModel.retry_of_job_id == root_id)
                    )
                    or 0
                )
        finally:
            await engine.dispose()

    assert asyncio.run(child_count()) == 1


@pytest.mark.integration
def test_queue_failure_leaves_recoverable_outbox_and_same_request_redispatches(
    embedding_seed: Seed,
) -> None:
    dispatcher = FailOnceDispatcher()
    payload = {"historical_question_ids": [], "knowledge_chunk_ids": [str(CHUNK_CONFLICT_ID)]}
    headers = {**ADMIN_HEADERS, "Idempotency-Key": "embedding-queue-failure"}
    with _client(embedding_seed, dispatcher) as client:
        failed = client.post(BASE_PATH, json=payload, headers=headers)
        recovered = client.post(BASE_PATH, json=payload, headers=headers)

    assert failed.status_code == 503
    assert failed.json() == {"detail": {"code": "embedding_queue_unavailable"}}
    assert "redis password" not in failed.text
    assert recovered.status_code == 202
    assert recovered.json()["status"] == "queued"
    assert recovered.json()["queue_message_id"] == "recovered-message"
    assert recovered.json()["deduplicated"] is True
    assert dispatcher.calls == [UUID(recovered.json()["id"]), UUID(recovered.json()["id"])]


@pytest.mark.integration
def test_idempotency_race_converges_and_duplicate_messages_call_provider_once(
    embedding_seed: Seed,
) -> None:
    dispatcher = BarrierDispatcher()
    payload = _payload(QUESTION_BASIC_ID, CHUNK_BASIC_ID)
    headers = {**ADMIN_HEADERS, "Idempotency-Key": "embedding-race"}

    def submit() -> tuple[int, dict[str, object]]:
        with _client(embedding_seed, dispatcher) as client:
            response = client.post(BASE_PATH, json=payload, headers=headers)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: submit(), range(2)))

    assert [status for status, _ in results] == [202, 202], results
    assert len({body["id"] for _, body in results}) == 1
    assert sorted(bool(body["deduplicated"]) for _, body in results) == [False, True]
    assert len(dispatcher.calls) == 2

    provider = RecordingProvider()
    job_id = UUID(cast(str, results[0][1]["id"]))

    async def process_duplicate_messages() -> tuple[bool, bool, EmbeddingJobModel]:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as first_session:
            first = await EmbeddingWorkerService(
                first_session,
                _registry(provider),
                DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
            ).process(job_id)
        async with sessions() as second_session:
            second = await EmbeddingWorkerService(
                second_session,
                _registry(provider),
                DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
            ).process(job_id)
            model = cast(EmbeddingJobModel, await second_session.get(EmbeddingJobModel, job_id))
        await engine.dispose()
        return first, second, model

    first, second, model = asyncio.run(process_duplicate_messages())
    assert first is True
    assert second is False
    assert provider.calls == [
        embedding_seed.texts[QUESTION_BASIC_ID],
        embedding_seed.texts[CHUNK_BASIC_ID],
    ]
    assert model.status == "succeeded"
    assert (model.requested_count, model.embedded_count, model.deduplicated_count) == (2, 2, 0)


@pytest.mark.integration
def test_distinct_overlapping_jobs_lock_source_and_call_provider_once(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("overlap-message")
    payload = _chunk_payload(CHUNK_CONCURRENT_ID)
    with _client(embedding_seed, dispatcher) as client:
        first_response = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-overlap-first"},
        )
        second_response = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-overlap-second"},
        )
    first_id = UUID(first_response.json()["id"])
    second_id = UUID(second_response.json()["id"])
    assert first_id != second_id

    provider = BlockingCountingProvider()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_run_embedding_worker, embedding_seed, first_id, provider)
        assert provider.first_call_started.wait(timeout=5)
        second = executor.submit(_run_embedding_worker, embedding_seed, second_id, provider)
        try:
            duplicate_call = provider.second_call_started.wait(timeout=1)
        finally:
            provider.release_first_call.set()
        assert first.result(timeout=10) is True
        assert second.result(timeout=10) is True

    first_job, second_job, embedding_count = asyncio.run(
        _read_jobs_and_embedding_count(
            embedding_seed,
            (first_id, second_id),
            CHUNK_CONCURRENT_ID,
        )
    )
    assert duplicate_call is False
    assert provider.calls == [embedding_seed.texts[CHUNK_CONCURRENT_ID]]
    assert first_job.status == second_job.status == "succeeded"
    assert (first_job.embedded_count, first_job.deduplicated_count) == (1, 0)
    assert (second_job.embedded_count, second_job.deduplicated_count) == (0, 1)
    assert embedding_count == 1


@pytest.mark.integration
def test_provider_failure_rolls_back_source_lock_and_waiting_job_converges(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("failure-lock-message")
    payload = _chunk_payload(CHUNK_LOCK_FAILURE_ID)
    with _client(embedding_seed, dispatcher) as client:
        failed_response = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-lock-failure"},
        )
        waiting_response = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-lock-waiter"},
        )
    failed_id = UUID(failed_response.json()["id"])
    waiting_id = UUID(waiting_response.json()["id"])

    failing_provider = BlockingFailProvider()
    healthy_provider = RecordingProvider()
    with ThreadPoolExecutor(max_workers=2) as executor:
        failed = executor.submit(
            _run_embedding_worker,
            embedding_seed,
            failed_id,
            failing_provider,
        )
        assert failing_provider.call_started.wait(timeout=5)
        waiting = executor.submit(
            _run_embedding_worker,
            embedding_seed,
            waiting_id,
            healthy_provider,
        )
        try:
            time.sleep(1)
            called_while_locked = tuple(healthy_provider.calls)
        finally:
            failing_provider.release_call.set()
        assert failed.result(timeout=10) is True
        assert waiting.result(timeout=10) is True

    failed_job, waiting_job, embedding_count = asyncio.run(
        _read_jobs_and_embedding_count(
            embedding_seed,
            (failed_id, waiting_id),
            CHUNK_LOCK_FAILURE_ID,
        )
    )
    assert called_while_locked == ()
    assert failing_provider.calls == [embedding_seed.texts[CHUNK_LOCK_FAILURE_ID]]
    assert healthy_provider.calls == [embedding_seed.texts[CHUNK_LOCK_FAILURE_ID]]
    assert failed_job.status == "failed"
    assert failed_job.failure_code == "embedding_provider_unavailable"
    assert "secret" not in failed_job.failure_code
    assert (failed_job.embedded_count, failed_job.deduplicated_count) == (0, 0)
    assert waiting_job.status == "succeeded"
    assert (waiting_job.embedded_count, waiting_job.deduplicated_count) == (1, 0)
    assert embedding_count == 1


@pytest.mark.integration
def test_stale_claim_recovery_is_strict_preserves_partial_counts_and_retry_converges(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("stale-claim-message")
    partial_payload = {
        "historical_question_ids": [],
        "knowledge_chunk_ids": [
            str(CHUNK_STALE_PARTIAL_A_ID),
            str(CHUNK_STALE_PARTIAL_B_ID),
        ],
    }
    with _client(embedding_seed, dispatcher) as client:
        partial_response = client.post(
            BASE_PATH,
            json=partial_payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-stale-partial"},
        )
        boundary_response = client.post(
            BASE_PATH,
            json=_chunk_payload(CHUNK_LEASE_BOUNDARY_ID),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-lease-boundary"},
        )
        fresh_response = client.post(
            BASE_PATH,
            json=_chunk_payload(CHUNK_LEASE_FRESH_ID),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-lease-fresh"},
        )
    partial_id = UUID(partial_response.json()["id"])
    boundary_id = UUID(boundary_response.json()["id"])
    fresh_id = UUID(fresh_response.json()["id"])

    async def expire_stale() -> tuple[datetime, EmbeddingRecoveryResult]:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            partial_claimed_at = datetime.now(UTC)
            async with sessions() as session:
                repository = SqlAlchemyEmbeddingJobRepository(session)
                partial = await repository.claim(partial_id, claimed_at=partial_claimed_at)
                assert partial is not None
                await session.commit()
                records = await repository.load_sources(
                    (),
                    (CHUNK_STALE_PARTIAL_A_ID, CHUNK_STALE_PARTIAL_B_ID),
                )
                progressed = await EmbeddingWorkerService(
                    session,
                    _registry(),
                    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
                )._process_record(partial, records[0])
                assert (progressed.embedded_count, progressed.deduplicated_count) == (1, 0)

            active_now = partial_claimed_at + timedelta(seconds=601)
            strict_boundary = active_now - timedelta(seconds=600)
            async with sessions() as session:
                repository = SqlAlchemyEmbeddingJobRepository(session)
                assert await repository.claim(boundary_id, claimed_at=strict_boundary) is not None
                assert (
                    await repository.claim(
                        fresh_id,
                        claimed_at=strict_boundary + timedelta(microseconds=1),
                    )
                    is not None
                )
                await session.commit()

            async with sessions() as session:
                recovered = await EmbeddingRecoveryService(
                    session,
                    DeterministicEmbeddingDispatcher("unused"),
                    EmbeddingRecoveryPolicy(
                        batch_size=10,
                        outbox_min_age_seconds=1,
                        worker_lease_seconds=600,
                    ),
                ).recover(now=active_now)
                partial = cast(
                    EmbeddingJobModel,
                    await session.get(EmbeddingJobModel, partial_id),
                )
                boundary = cast(
                    EmbeddingJobModel,
                    await session.get(EmbeddingJobModel, boundary_id),
                )
                fresh = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, fresh_id))
                assert recovered.claims_scanned == recovered.claims_expired == 1
                assert partial.status == "failed"
                assert partial.failure_code == "worker_lease_expired"
                assert (
                    partial.requested_count,
                    partial.embedded_count,
                    partial.deduplicated_count,
                ) == (2, 1, 0)
                assert boundary.status == fresh.status == "claimed"
                audits = tuple(
                    await session.scalars(
                        select(AdminAuditEventModel).where(
                            AdminAuditEventModel.resource_id == partial_id,
                            AdminAuditEventModel.action == "embedding_job.worker_lease_expired",
                        )
                    )
                )
                assert len(audits) == 1
                assert audits[0].payload == {
                    "failure_code": "worker_lease_expired",
                    "requested_count": 2,
                    "embedded_count": 1,
                    "deduplicated_count": 0,
                }
            return active_now, recovered
        finally:
            await engine.dispose()

    active_now, _ = asyncio.run(expire_stale())

    with _client(embedding_seed, dispatcher) as client:
        retry_response = client.post(
            BASE_PATH,
            json=partial_payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-stale-partial-retry"},
        )
    retry_id = UUID(retry_response.json()["id"])
    assert retry_response.json()["retry_of_job_id"] == str(partial_id)

    provider = RecordingProvider()
    assert _run_embedding_worker(embedding_seed, retry_id, provider)

    async def assert_retry_and_cleanup() -> None:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                retry = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, retry_id))
                embedding_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(KnowledgeEmbeddingModel)
                        .where(
                            KnowledgeEmbeddingModel.knowledge_chunk_id.in_(
                                (CHUNK_STALE_PARTIAL_A_ID, CHUNK_STALE_PARTIAL_B_ID)
                            )
                        )
                    )
                    or 0
                )
                assert retry.status == "succeeded"
                assert (retry.requested_count, retry.embedded_count, retry.deduplicated_count) == (
                    2,
                    1,
                    1,
                )
                assert embedding_count == 2
            async with sessions() as session:
                cleanup = await EmbeddingRecoveryService(
                    session,
                    DeterministicEmbeddingDispatcher("unused-cleanup"),
                    EmbeddingRecoveryPolicy(
                        batch_size=10,
                        outbox_min_age_seconds=1,
                        worker_lease_seconds=600,
                    ),
                ).recover(now=active_now + timedelta(seconds=601))
                assert cleanup.claims_expired == 2
        finally:
            await engine.dispose()

    asyncio.run(assert_retry_and_cleanup())
    assert provider.calls == [embedding_seed.texts[CHUNK_STALE_PARTIAL_B_ID]]


@pytest.mark.integration
def test_concurrent_recoverers_skip_locked_and_expire_each_claim_once(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("concurrent-recovery-message")
    with _client(embedding_seed, dispatcher) as client:
        first_response = client.post(
            BASE_PATH,
            json=_chunk_payload(CHUNK_RECOVERY_A_ID),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-recovery-a"},
        )
        second_response = client.post(
            BASE_PATH,
            json=_chunk_payload(CHUNK_RECOVERY_B_ID),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-recovery-b"},
        )
    job_ids = (UUID(first_response.json()["id"]), UUID(second_response.json()["id"]))

    async def exercise() -> None:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        barrier = threading.Barrier(2)
        claimed_at = datetime.now(UTC)
        active_now = claimed_at + timedelta(seconds=601)

        class BarrierRepository(SqlAlchemyEmbeddingJobRepository):
            async def lock_expired_claims(
                self,
                *,
                claimed_before: datetime,
                limit: int,
            ) -> tuple[EmbeddingJobModel, ...]:
                records = await super().lock_expired_claims(
                    claimed_before=claimed_before,
                    limit=limit,
                )
                await asyncio.to_thread(barrier.wait, 10)
                return records

        async def recover_once() -> EmbeddingRecoveryResult:
            async with sessions() as session:
                service = EmbeddingRecoveryService(
                    session,
                    DeterministicEmbeddingDispatcher("unused"),
                    EmbeddingRecoveryPolicy(
                        batch_size=1,
                        outbox_min_age_seconds=1,
                        worker_lease_seconds=600,
                    ),
                )
                service._repository = BarrierRepository(session)
                return await service.recover(now=active_now)

        try:
            async with sessions() as session:
                repository = SqlAlchemyEmbeddingJobRepository(session)
                for job_id in job_ids:
                    assert await repository.claim(job_id, claimed_at=claimed_at) is not None
                await session.commit()

            results = await asyncio.gather(recover_once(), recover_once())
            assert sorted((result.claims_scanned, result.claims_expired) for result in results) == [
                (1, 1),
                (1, 1),
            ]

            async with sessions() as session:
                jobs_by_id = {
                    job.id: job
                    for job in await session.scalars(
                        select(EmbeddingJobModel).where(EmbeddingJobModel.id.in_(job_ids))
                    )
                }
                assert set(jobs_by_id) == set(job_ids)
                assert all(job.status == "failed" for job in jobs_by_id.values())
                audits = tuple(
                    await session.scalars(
                        select(AdminAuditEventModel).where(
                            AdminAuditEventModel.resource_id.in_(job_ids),
                            AdminAuditEventModel.action == "embedding_job.worker_lease_expired",
                        )
                    )
                )
                assert {audit.resource_id for audit in audits} == set(job_ids)
                assert len(audits) == 2
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_expired_late_worker_rolls_back_embedding_audit_and_progress(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("late-worker-message")
    with _client(embedding_seed, dispatcher) as client:
        response = client.post(
            BASE_PATH,
            json=_chunk_payload(CHUNK_LATE_WORKER_ID),
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-late-worker"},
        )
    job_id = UUID(response.json()["id"])
    provider = BlockingCountingProvider()

    async def expire_claim() -> EmbeddingRecoveryResult:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                job = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, job_id))
                assert job.status == "claimed"
                assert job.claimed_at is not None
                return await EmbeddingRecoveryService(
                    session,
                    DeterministicEmbeddingDispatcher("unused"),
                    EmbeddingRecoveryPolicy(
                        batch_size=10,
                        outbox_min_age_seconds=1,
                        worker_lease_seconds=600,
                    ),
                ).recover(now=job.claimed_at + timedelta(seconds=601))
        finally:
            await engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as executor:
        late = executor.submit(_run_embedding_worker, embedding_seed, job_id, provider)
        assert provider.first_call_started.wait(timeout=5)
        try:
            recovered = asyncio.run(expire_claim())
            assert recovered.claims_scanned == recovered.claims_expired == 1
        finally:
            provider.release_first_call.set()
        assert late.result(timeout=10) is False

    async def assert_rolled_back() -> None:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                job = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, job_id))
                embedding_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(KnowledgeEmbeddingModel)
                        .where(KnowledgeEmbeddingModel.knowledge_chunk_id == CHUNK_LATE_WORKER_ID)
                    )
                    or 0
                )
                embedded_audits = tuple(
                    await session.scalars(
                        select(AdminAuditEventModel).where(
                            AdminAuditEventModel.resource_id == CHUNK_LATE_WORKER_ID,
                            AdminAuditEventModel.action == "knowledge.chunk.embedded",
                        )
                    )
                )
                lease_audits = tuple(
                    await session.scalars(
                        select(AdminAuditEventModel).where(
                            AdminAuditEventModel.resource_id == job_id,
                            AdminAuditEventModel.action == "embedding_job.worker_lease_expired",
                        )
                    )
                )
                assert job.status == "failed"
                assert job.failure_code == "worker_lease_expired"
                assert (job.embedded_count, job.deduplicated_count) == (0, 0)
                assert embedding_count == 0
                assert embedded_audits == ()
                assert len(lease_audits) == 1
        finally:
            await engine.dispose()

    asyncio.run(assert_rolled_back())
    assert provider.calls == [embedding_seed.texts[CHUNK_LATE_WORKER_ID]]


@pytest.mark.integration
def test_partial_provider_failure_then_new_key_retry_converges_without_duplicate_vectors(
    embedding_seed: Seed,
) -> None:
    provider = RecordingProvider(fail_call=2)
    dispatcher = DeterministicEmbeddingDispatcher("partial-message")
    payload = _payload(QUESTION_PARTIAL_ID, CHUNK_PARTIAL_ID)
    with _client(embedding_seed, dispatcher, provider=provider) as client:
        first_create = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-partial-first"},
        )
    first_id = UUID(first_create.json()["id"])

    async def first_process() -> EmbeddingJobModel:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            assert await EmbeddingWorkerService(
                session,
                _registry(provider),
                DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
            ).process(first_id)
            model = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, first_id))
        await engine.dispose()
        return model

    failed = asyncio.run(first_process())
    assert failed.status == "failed"
    assert failed.failure_code == "embedding_provider_unavailable"
    assert failed.embedded_count == 1
    assert "secret" not in failed.failure_code

    with _client(embedding_seed, dispatcher, provider=provider) as client:
        retry_create = client.post(
            BASE_PATH,
            json=payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-partial-retry"},
        )
    retry_id = UUID(retry_create.json()["id"])
    assert retry_create.json()["retry_of_job_id"] == str(first_id)

    async def retry_process() -> tuple[EmbeddingJobModel, int]:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            assert await EmbeddingWorkerService(
                session,
                _registry(provider),
                DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
            ).process(retry_id)
            model = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, retry_id))
            count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeEmbeddingModel)
                    .where(
                        (KnowledgeEmbeddingModel.historical_question_id == QUESTION_PARTIAL_ID)
                        | (KnowledgeEmbeddingModel.knowledge_chunk_id == CHUNK_PARTIAL_ID)
                    )
                )
                or 0
            )
        await engine.dispose()
        return model, count

    succeeded, vector_count = asyncio.run(retry_process())
    assert succeeded.status == "succeeded"
    assert (succeeded.requested_count, succeeded.embedded_count, succeeded.deduplicated_count) == (
        2,
        1,
        1,
    )
    assert len(provider.calls) == 3
    assert vector_count == 2


@pytest.mark.integration
def test_source_hash_conflict_is_rejected_before_job_commit(embedding_seed: Seed) -> None:
    config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG

    async def seed_conflict() -> int:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                configuration = await session.scalar(
                    select(EmbeddingConfigurationModel).where(
                        EmbeddingConfigurationModel.provider == config.provider,
                        EmbeddingConfigurationModel.model == config.model,
                        EmbeddingConfigurationModel.version == config.version,
                        EmbeddingConfigurationModel.config_fingerprint == config.config_fingerprint,
                    )
                )
                if configuration is None:
                    configuration = EmbeddingConfigurationModel.from_domain(
                        UUID(int=1_829_001), config, ADMIN_ID
                    )
                    session.add(configuration)
                    await session.flush()
                await session.execute(
                    text(
                        "ALTER TABLE knowledge_embeddings DISABLE TRIGGER "
                        "enforce_knowledge_embedding_integrity_trigger"
                    )
                )
                session.add(
                    KnowledgeEmbeddingModel(
                        id=UUID(int=1_829_002),
                        historical_question_id=None,
                        knowledge_chunk_id=CHUNK_CONFLICT_ID,
                        embedding_configuration_id=configuration.id,
                        embedding_dimension=config.dimension,
                        source_text_sha256="f" * 64,
                        embedding=[0.0] * config.dimension,
                        created_by=ADMIN_ID,
                    )
                )
                await session.flush()
                await session.execute(
                    text(
                        "ALTER TABLE knowledge_embeddings ENABLE TRIGGER "
                        "enforce_knowledge_embedding_integrity_trigger"
                    )
                )
                await session.commit()
                count = await session.scalar(select(func.count()).select_from(EmbeddingJobModel))
                return int(count or 0)
        finally:
            await engine.dispose()

    before = asyncio.run(seed_conflict())
    dispatcher = DeterministicEmbeddingDispatcher()
    with _client(embedding_seed, dispatcher) as client:
        response = client.post(
            BASE_PATH,
            json={"historical_question_ids": [], "knowledge_chunk_ids": [str(CHUNK_CONFLICT_ID)]},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-source-conflict"},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "embedding_source_conflict"
    assert dispatcher.dispatched == []

    async def count_jobs() -> int:
        engine = create_async_engine(embedding_seed.database_url)
        async with engine.connect() as connection:
            count = int(await connection.scalar(text("SELECT count(*) FROM embedding_jobs")) or 0)
        await engine.dispose()
        return count

    assert asyncio.run(count_jobs()) == before


@pytest.mark.integration
def test_database_rejects_job_mutation_delete_invalid_transition_and_unsorted_arrays(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("db-invariant-message")
    with _client(embedding_seed, dispatcher) as client:
        created = client.post(
            BASE_PATH,
            json={"historical_question_ids": [], "knowledge_chunk_ids": [str(CHUNK_RETRIEVAL_ID)]},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-db-invariants"},
        )
    job_id = UUID(created.json()["id"])
    lineage_payload = {
        "historical_question_ids": [str(QUESTION_PARTIAL_ID)],
        "knowledge_chunk_ids": [str(CHUNK_CONCURRENT_ID), str(CHUNK_STALE_PARTIAL_A_ID)],
    }
    with _client(embedding_seed, dispatcher) as client:
        failed_root = client.post(
            BASE_PATH,
            json=lineage_payload,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-db-lineage-root"},
        )
    failed_root_id = UUID(failed_root.json()["id"])
    assert _fail_embedding_job_for_retry(embedding_seed, failed_root_id)

    async def assert_invariants() -> None:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        statements = (
            update(EmbeddingJobModel)
            .where(EmbeddingJobModel.id == job_id)
            .values(provider="changed", version=EmbeddingJobModel.version + 1),
            update(EmbeddingJobModel)
            .where(EmbeddingJobModel.id == job_id)
            .values(status="succeeded", version=EmbeddingJobModel.version + 1),
            text("DELETE FROM embedding_jobs WHERE id = :job_id").bindparams(job_id=job_id),
        )
        async with sessions() as session:
            for statement in statements:
                with pytest.raises((DBAPIError, IntegrityError)):
                    await session.execute(statement)
                await session.rollback()

            current = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, job_id))
            invalid = EmbeddingJobModel(
                id=UUID(int=1_829_100),
                curriculum_version_id=CURRICULUM_ID,
                retry_of_job_id=None,
                retry_depth=0,
                historical_question_ids=[],
                knowledge_chunk_ids=[str(CHUNK_RETRIEVAL_ID), str(CHUNK_BASIC_ID)],
                idempotency_key_hash="sha256:" + "a" * 64,
                request_fingerprint="sha256:" + "b" * 64,
                source_fingerprint="sha256:" + "c" * 64,
                provider=current.provider,
                model=current.model,
                dimension=current.dimension,
                embedding_version=current.embedding_version,
                config_fingerprint=current.config_fingerprint,
                status="queued",
                version=0,
                queue_message_id=None,
                requested_count=2,
                embedded_count=0,
                deduplicated_count=0,
                failure_code=None,
                created_by=ADMIN_ID,
                claimed_at=None,
                completed_at=None,
            )
            session.add(invalid)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

        def clone(
            source: EmbeddingJobModel,
            *,
            identifier: UUID,
            retry_of_job_id: UUID | None,
            retry_depth: int,
            **overrides: object,
        ) -> EmbeddingJobModel:
            values: dict[str, object] = {
                "id": identifier,
                "curriculum_version_id": source.curriculum_version_id,
                "retry_of_job_id": retry_of_job_id,
                "retry_depth": retry_depth,
                "historical_question_ids": list(source.historical_question_ids),
                "knowledge_chunk_ids": list(source.knowledge_chunk_ids),
                "idempotency_key_hash": f"sha256:{identifier.int:064x}",
                "request_fingerprint": source.request_fingerprint,
                "source_fingerprint": source.source_fingerprint,
                "provider": source.provider,
                "model": source.model,
                "dimension": source.dimension,
                "embedding_version": source.embedding_version,
                "config_fingerprint": source.config_fingerprint,
                "status": "queued",
                "version": 0,
                "queue_message_id": None,
                "requested_count": source.requested_count,
                "embedded_count": 0,
                "deduplicated_count": 0,
                "failure_code": None,
                "created_by": source.created_by,
                "claimed_at": None,
                "completed_at": None,
            }
            values.update(overrides)
            return EmbeddingJobModel(**values)

        invalid_cases: tuple[tuple[UUID, UUID | None, int, dict[str, object]], ...] = (
            (failed_root_id, None, 1, {}),
            (failed_root_id, failed_root_id, 0, {}),
            (job_id, job_id, 1, {}),
            (
                failed_root_id,
                failed_root_id,
                1,
                {"request_fingerprint": "sha256:" + "f" * 64},
            ),
            (
                failed_root_id,
                failed_root_id,
                1,
                {"curriculum_version_id": OTHER_CURRICULUM_ID},
            ),
            (
                failed_root_id,
                failed_root_id,
                1,
                {"config_fingerprint": "changed-config-v2"},
            ),
            (
                failed_root_id,
                failed_root_id,
                1,
                {"created_by": REVIEWER_ID},
            ),
        )
        for offset, (template_id, predecessor_id, depth, overrides) in enumerate(
            invalid_cases, start=1
        ):
            async with sessions() as session:
                source = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, template_id))
                session.add(
                    clone(
                        source,
                        identifier=UUID(int=1_829_200 + offset),
                        retry_of_job_id=predecessor_id,
                        retry_depth=depth,
                        **overrides,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

        child_id = UUID(int=1_829_300)
        async with sessions() as session:
            source = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, failed_root_id))
            session.add(
                clone(
                    source,
                    identifier=child_id,
                    retry_of_job_id=failed_root_id,
                    retry_depth=1,
                )
            )
            await session.commit()

        async with sessions() as session:
            source = cast(EmbeddingJobModel, await session.get(EmbeddingJobModel, failed_root_id))
            session.add(
                clone(
                    source,
                    identifier=UUID(int=1_829_301),
                    retry_of_job_id=failed_root_id,
                    retry_depth=1,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

        for statement in (
            update(EmbeddingJobModel)
            .where(EmbeddingJobModel.id == child_id)
            .values(
                retry_depth=2,
                version=EmbeddingJobModel.version + 1,
                updated_at=func.clock_timestamp(),
            ),
            update(EmbeddingJobModel)
            .where(EmbeddingJobModel.id == failed_root_id)
            .values(
                retry_of_job_id=child_id,
                retry_depth=2,
                version=EmbeddingJobModel.version + 1,
                updated_at=func.clock_timestamp(),
            ),
        ):
            async with sessions() as session:
                with pytest.raises((DBAPIError, IntegrityError)):
                    await session.execute(statement)
                await session.rollback()
        await engine.dispose()

    asyncio.run(assert_invariants())


@pytest.mark.integration
def test_worker_persisted_configuration_drives_successful_generated_retrieval(
    embedding_seed: Seed,
) -> None:
    dispatcher = DeterministicEmbeddingDispatcher("retrieval-message")
    with _client(embedding_seed, dispatcher) as client:
        created = client.post(
            BASE_PATH,
            json={"historical_question_ids": [], "knowledge_chunk_ids": [str(CHUNK_RETRIEVAL_ID)]},
            headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-retrieval"},
        )
    job_id = UUID(created.json()["id"])

    async def process() -> None:
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            assert await EmbeddingWorkerService(
                session,
                _registry(),
                DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
            ).process(job_id)
        await engine.dispose()

    asyncio.run(process())
    with _client(embedding_seed, dispatcher) as client:
        job = client.get(f"{BASE_PATH}/{job_id}", headers=REVIEWER_HEADERS)
        retrieval_payload = {
            "query": "square perimeter",
            "scope": {
                "grade": 5,
                "exam_id": str(EXAM_ID),
                "medium_id": str(MEDIUM_ID),
                "curriculum_version_id": str(CURRICULUM_ID),
                "taxonomy": {"competency_id": str(COMPETENCY_ID)},
            },
            "embedding_config": job.json()["configuration"],
            "limits": {
                "candidate_limit": 5,
                "top_k": 2,
                "max_context_items": 1,
                "max_context_characters": 500,
                "max_context_item_characters": 500,
            },
        }
        retrieval = client.post(
            "/api/v1/admin/retrieval/explore",
            headers=REVIEWER_HEADERS,
            json=retrieval_payload,
        )

    async def retrieve_directly() -> None:
        request = RetrievalExploreRequest.model_validate(retrieval_payload)
        engine = create_async_engine(embedding_seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            await RetrievalExplorerService(session, _registry()).explore(
                query=request.query,
                scope=request.scope.to_domain(),
                embedding_config=request.embedding_config.to_domain(),
                limits=request.limits.to_domain(),
            )
        await engine.dispose()

    asyncio.run(retrieve_directly())
    assert job.json()["status"] == "succeeded"
    assert retrieval.status_code == 200, retrieval.text
    assert retrieval.json()["embedding_config"] == job.json()["configuration"]
    assert retrieval.json()["fused_candidates"][0]["chunk_id"] == str(CHUNK_RETRIEVAL_ID)


@pytest.mark.integration
def test_real_valkey_dispatch_and_worker_complete_deterministic_job(
    embedding_seed: Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.core.config import Settings

    settings = Settings(
        environment="test",
        database_url=SecretStr(embedding_seed.database_url),
        valkey_url=SecretStr(embedding_seed.valkey_url),
    )
    monkeypatch.setenv("EXAM_GURU_ENVIRONMENT", "test")
    monkeypatch.setenv("EXAM_GURU_DATABASE_URL", embedding_seed.database_url)
    monkeypatch.setenv("EXAM_GURU_VALKEY_URL", embedding_seed.valkey_url)
    dispatcher = create_embedding_dispatcher(settings)
    broker = cast(RedisBroker, jobs.ingest_embeddings.broker)
    worker = Worker(broker, worker_threads=1, worker_timeout=100)
    worker.start()
    try:
        with _client(embedding_seed, dispatcher, settings=settings) as client:
            created = client.post(
                BASE_PATH,
                json={"historical_question_ids": [], "knowledge_chunk_ids": [str(CHUNK_VALKEY_ID)]},
                headers={**ADMIN_HEADERS, "Idempotency-Key": "embedding-real-valkey"},
            )
            assert created.status_code == 202, created.text
            job_id = created.json()["id"]
            deadline = time.monotonic() + 20
            body: dict[str, object] = {}
            while time.monotonic() < deadline:
                response = client.get(f"{BASE_PATH}/{job_id}", headers=REVIEWER_HEADERS)
                body = response.json()
                if body["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.05)
        assert body["status"] == "succeeded"
        assert body["counts"] == {"requested": 1, "embedded": 1, "deduplicated": 0}
        assert broker.do_qsize(EMBEDDING_QUEUE_NAME) == 0
    finally:
        worker.stop(timeout=5_000)
        broker.close()
