import asyncio
import hashlib
import threading
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.blueprints.domain import TaxonomyTarget
from exam_guru_api.blueprints.service import BlueprintGenerationService
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    get_catalogue_admission,
    record_catalogue_admission,
)
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.fidelity_models import PageReviewStateModel
from exam_guru_api.documents.fidelity_service import PageFidelityService
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.documents.page_reading_jobs import queue_source_read
from exam_guru_api.generation.domain import GenerationRequest, GenerationResult
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.generation.repository import SqlAlchemyGenerationRepository
from exam_guru_api.generation.run_service import (
    GenerationContextNotFoundError,
    GenerationContextSourceUntrustedError,
    GenerationCreationResult,
    GenerationRunService,
    GenerationWorkerService,
    _context_snapshot,
)
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry, create_generation_runtime
from exam_guru_api.infrastructure.migrations import _config_for_database, upgrade_database
from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    Provenance,
    QuestionType,
    ReviewState,
)
from exam_guru_api.knowledge.embedding_job_repository import SqlAlchemyEmbeddingJobRepository
from exam_guru_api.knowledge.embeddings import EmbeddingConfig, EmbeddingResult
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.knowledge.repository import SourceImportConflictError
from exam_guru_api.knowledge.service import (
    ActiveKnowledgeSourceRequiredError,
    KnowledgePersistenceService,
    SourceImportResult,
    TrustedKnowledgeSourceRequiredError,
)
from exam_guru_api.papers.models import QuestionCandidateModel
from exam_guru_api.papers.publication_service import PaperPublicationService
from exam_guru_api.papers.review_service import ReviewCandidateService
from exam_guru_api.retrieval.domain import RetrievalScope, TaxonomyScope
from exam_guru_api.retrieval.repository import PostgresHybridRetrievalRepository
from exam_guru_api.validation.domain import (
    FindingEvidence,
    FindingStatus,
    ValidationFinding,
    ValidationInput,
)
from exam_guru_api.validation.models import ValidationRunModel
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.service import (
    ValidationGenerationIntegrityError,
    ValidationRunService,
)
from exam_guru_api.validation.validators import SchemaCompletenessValidator
from tests.test_blueprint_domain import make_uniform_specification

pytestmark = pytest.mark.integration
ACTOR = Principal(UUID(int=35001), frozenset({AdminRole.ADMIN}))
SOURCE_TEXT = "Fractions split a whole into equal parts.\nCafé uses NFC.\nWhich answer is correct?"
CONFIG = EmbeddingConfig("deterministic", "lineage", 3, "v1", "lineage-v1")
EVIDENCE = (
    "DISPOSABLE synthetic fixture evidence; pipeline mechanics only, not real educational approval."
)


@dataclass(frozen=True)
class Seed:
    document_id: UUID
    candidate_id: UUID
    block_id: UUID
    scope: RetrievalScope

    def chunk(
        self, *, sequence: int = 0, content: str = "Fractions split a whole into equal parts."
    ) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=uuid4(),
            curriculum_version_id=self.scope.curriculum_version_id,
            chunk_type=ChunkType.EXPLANATION,
            text=content,
            educational_boundary="Fractions",
            sequence=sequence,
            provenance=Provenance(self.document_id, 1, self.block_id),
            competency_id=self.scope.taxonomy.competency_id,
            review_state=ReviewState.REVIEWED,
        )


@pytest.fixture(scope="module")
def lineage_database_url() -> Iterator[str]:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="disposable_verified_lineage",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            upgrade_database(url)
            yield url


async def approve_synthetic_curriculum(
    session: AsyncSession, curriculum_id: UUID, *, actor_id: UUID
) -> None:
    current = await get_catalogue_admission(session, curriculum_id)
    await record_catalogue_admission(
        session,
        curriculum_id,
        AdmissionDecisionRequest(
            state="approved",
            expected_version=current.version,
            expected_scope_fingerprint=current.scope_fingerprint,
            educational_approval=True,
            reason=EVIDENCE,
            source_reference="Disposable synthetic test curriculum",
            evidence=(EVIDENCE,),
        ),
        principal=Principal(actor_id, frozenset({AdminRole.ADMIN})),
    )
    await session.commit()


async def verify_synthetic_page(
    session: AsyncSession,
    document_id: UUID,
    source_text: str,
    *,
    actor_id: UUID,
    page_number: int = 1,
) -> UUID:
    fidelity = PageFidelityService(session)
    state = await fidelity.record_candidate(
        document_id,
        page_number,
        raw_text=source_text,
        method="native",
        actor_id=actor_id,
        provenance={"engine": "synthetic-fixture", "version": "1", "evidence": EVIDENCE},
    )
    candidate_id = state.current_candidate_id
    assert candidate_id is not None
    await fidelity.confirm_page(
        document_id,
        page_number,
        candidate_id=candidate_id,
        expected_version=state.version,
        actor_id=actor_id,
        reason=EVIDENCE,
    )
    return candidate_id


async def seed_curriculum_scope(session: AsyncSession, *, admitted: bool = True) -> RetrievalScope:
    exam_id, medium_id, subject_id, curriculum_id, competency_id = (uuid4() for _ in range(5))
    audit = {"created_by": ACTOR.subject_id, "updated_by": ACTOR.subject_id}
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code="G5-C" + uuid4().hex[:12].upper(),
                name="Grade 5 Scholarship",
                grade=5,
                active=True,
                **audit,
            ),
            MediumModel(
                id=medium_id, code="en" + uuid4().hex[:10], name="English", active=True, **audit
            ),
            SubjectModel(
                id=subject_id,
                code="MATH-C" + uuid4().hex[:12].upper(),
                name="Mathematics",
                active=True,
                **audit,
            ),
        ]
    )
    await session.flush()
    session.add(
        CurriculumVersionModel(
            id=curriculum_id,
            exam_configuration_id=exam_id,
            medium_id=medium_id,
            subject_id=subject_id,
            code="2026",
            title="Grade 5 Mathematics",
            active=True,
            **audit,
        )
    )
    await session.flush()
    session.add(
        TaxonomyNodeModel.from_domain(
            TaxonomyNode(
                id=competency_id,
                curriculum_version_id=curriculum_id,
                level=TaxonomyLevel.COMPETENCY,
                code="FRACTIONS",
                title="Fractions",
                review_state=TaxonomyReviewState.REVIEWED,
            ),
            ACTOR.subject_id,
        )
    )
    await session.flush()
    if admitted:
        await admission(session, curriculum_id, "approved")
    return RetrievalScope(
        grade=5,
        exam_id=exam_id,
        medium_id=medium_id,
        subject_id=subject_id,
        curriculum_version_id=curriculum_id,
        taxonomy=TaxonomyScope(competency_id=competency_id),
    )


async def seed(session: AsyncSession, *, admitted: bool = True, trusted: bool = False) -> Seed:
    scope = await seed_curriculum_scope(session, admitted=admitted)
    curriculum_id = scope.curriculum_version_id
    audit = {"created_by": ACTOR.subject_id, "updated_by": ACTOR.subject_id}
    document_id, page_id, block_id = (uuid4() for _ in range(3))
    checksum = hashlib.sha256(document_id.bytes).hexdigest()
    document = SourceDocumentModel(
        id=document_id,
        curriculum_version_id=curriculum_id,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum}.pdf",
        original_filename="disposable-synthetic-lineage.pdf",
        content_type="application/pdf",
        size_bytes=100,
        document_type=SourceDocumentType.PAST_PAPER,
        year=2020,
        paper_code="P1",
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        extraction_attempt_count=1,
        extraction_started_at=datetime.now(UTC),
        original_page_count=2,
        metadata_review_required=False,
        active_for_ai=True,
        **audit,
    )
    session.add(document)
    await session.flush()
    session.add(
        SourcePageModel(
            id=page_id,
            source_document_id=document_id,
            page_number=1,
            extractor="synthetic-fixture",
            extractor_version="v1",
            raw_text=SOURCE_TEXT,
            reviewed_text=SOURCE_TEXT,
            character_count=len(SOURCE_TEXT),
            block_count=1,
            **audit,
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
            extractor="synthetic-fixture",
            extractor_version="v1",
            bbox_x0=0.0,
            bbox_y0=0.0,
            bbox_x1=1.0,
            bbox_y1=1.0,
            raw_text=SOURCE_TEXT,
            reviewed_text=SOURCE_TEXT,
            character_count=len(SOURCE_TEXT),
            **audit,
        )
    )
    await session.flush()
    document.extraction_status = ExtractionStatus.EXTRACTED
    document.extractor, document.extractor_version = "synthetic-fixture", "v1"
    document.extracted_page_count, document.extracted_block_count = 1, 1
    document.extracted_character_count, document.native_text_page_ratio = len(SOURCE_TEXT), 1.0
    document.needs_ocr, document.ocr_page_count, document.extraction_config = False, 0, {}
    document.extraction_completed_at = datetime.now(UTC)
    await session.flush()
    document.extraction_status = ExtractionStatus.IN_REVIEW
    await session.flush()
    if trusted:
        document.extraction_status = ExtractionStatus.TRUSTED
    await session.commit()
    fidelity = PageFidelityService(session)
    state = await fidelity.record_candidate(
        document_id,
        1,
        raw_text=SOURCE_TEXT,
        method="native",
        actor_id=ACTOR.subject_id,
        provenance={"engine": "synthetic-fixture", "version": "1", "evidence": EVIDENCE},
    )
    candidate_id = state.current_candidate_id
    assert candidate_id is not None
    await fidelity.confirm_page(
        document_id,
        1,
        candidate_id=candidate_id,
        expected_version=state.version,
        actor_id=ACTOR.subject_id,
        reason=EVIDENCE,
    )
    return Seed(document_id, candidate_id, block_id, scope)


async def admission(session: AsyncSession, curriculum_id: UUID, state: str) -> None:
    current = await get_catalogue_admission(session, curriculum_id)
    await record_catalogue_admission(
        session,
        curriculum_id,
        AdmissionDecisionRequest.model_validate(
            {
                "state": state,
                "expected_version": current.version,
                "expected_scope_fingerprint": current.scope_fingerprint,
                "educational_approval": state == "approved",
                "reason": EVIDENCE,
                "source_reference": "Disposable synthetic lineage test source",
                "evidence": [EVIDENCE],
            }
        ),
        principal=ACTOR,
    )
    await session.commit()


def test_synthetic_scope_codes_do_not_randomly_match_reserved_markers(
    lineage_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifiers = iter(range(1, 100))
    base = UUID("e2e00000-0000-4000-8000-000000000000").int
    monkeypatch.setattr(f"{__name__}.uuid4", lambda: UUID(int=base + next(identifiers)))

    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                scope = await seed_curriculum_scope(session)
                assert (
                    await get_catalogue_admission(session, scope.curriculum_version_id)
                ).admitted
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_verified_page_can_supply_nfc_chunks_while_document_needs_review(
    lineage_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source = await seed(session)
                persistence = KnowledgePersistenceService(session)
                imported = await persistence.import_chunk(source.chunk(), actor_id=ACTOR.subject_id)
                binding = await session.scalar(
                    text("SELECT source_candidate_id FROM knowledge_chunks WHERE id=:id"),
                    {"id": imported.record.id},
                )
                assert binding == source.candidate_id
                assert not await PageFidelityService(session).document_is_verified(
                    source.document_id
                )
                nfc = await persistence.import_chunk(
                    source.chunk(sequence=1, content="Cafe\u0301 uses NFC."),
                    actor_id=ACTOR.subject_id,
                )
                assert nfc.record.text == "Café uses NFC."
                await persistence.store_chunk_embedding(
                    imported.record.id,
                    EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                    actor_id=ACTOR.subject_id,
                )
                candidates = await PostgresHybridRetrievalRepository(
                    session, embedding_config=CONFIG
                ).retrieve_candidates(
                    query="Fractions", query_vector=(1.0, 0.0, 0.0), filters=source.scope
                )
                assert imported.record.id in {
                    item.record.chunk_id for item in candidates.lexical_candidates
                }
                assert imported.record.id in {
                    item.record.chunk_id for item in candidates.vector_candidates
                }
                contexts = await SqlAlchemyGenerationRepository(session).list_context_records(
                    (imported.record.id,), ()
                )
                validated = GenerationRunService._validate_context_records(
                    source.scope, (imported.record.id,), (), contexts
                )
                _, snapshot = _context_snapshot(validated, retrieval_filters=source.scope)
                assert str(source.candidate_id) in str(snapshot)
                assert hashlib.sha256(SOURCE_TEXT.encode()).hexdigest() in str(snapshot)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["knowledge_chunk", "historical_question"])
def test_import_binds_server_candidate_not_caller_candidate(
    lineage_database_url: str, kind: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source = await seed(session)
                forged = Provenance(
                    source.document_id, 1, source.block_id, source_candidate_id=uuid4()
                )
                persistence = KnowledgePersistenceService(session)
                result: SourceImportResult[KnowledgeChunk] | SourceImportResult[HistoricalQuestion]
                if kind == "knowledge_chunk":
                    result = await persistence.import_chunk(
                        replace(source.chunk(), provenance=forged), actor_id=ACTOR.subject_id
                    )
                else:
                    question = HistoricalQuestion(
                        id=uuid4(),
                        curriculum_version_id=source.scope.curriculum_version_id,
                        year=2020,
                        paper_code="P1",
                        question_number="1",
                        text="Which answer is correct?",
                        question_type=QuestionType.SHORT_ANSWER,
                        marks=1,
                        provenance=forged,
                    )
                    result = await persistence.import_question(question, actor_id=ACTOR.subject_id)
                assert result.record.provenance.source_candidate_id == source.candidate_id
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "problem",
    [
        "unbound",
        "wrong_candidate",
        "wrong_text",
        "unapproved",
        "unresolved",
        "removed",
        "reread",
        "excluded",
        "unverified",
        "stale",
    ],
)
def test_direct_database_import_cannot_bypass_lineage(
    lineage_database_url: str, problem: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source = await seed(
                    session, admitted=problem != "unapproved", trusted=problem != "unresolved"
                )
                candidate_id: UUID | None = source.candidate_id
                if problem == "unbound":
                    candidate_id = None
                elif problem == "wrong_candidate":
                    candidate_id = (await seed(session)).candidate_id
                elif problem == "unresolved":
                    await session.execute(
                        update(SourceDocumentModel)
                        .where(SourceDocumentModel.id == source.document_id)
                        .values(
                            metadata_review_required=True,
                            metadata_scope_version=SourceDocumentModel.metadata_scope_version + 1,
                        )
                    )
                    await session.commit()
                elif problem in {"excluded", "unverified", "stale", "removed", "reread"}:
                    await invalidate(session, source, problem)
                model = KnowledgeChunkModel.from_domain(
                    source.chunk(
                        content="Invented paraphrase"
                        if problem == "wrong_text"
                        else "Fractions split a whole into equal parts."
                    ),
                    ACTOR.subject_id,
                )
                model.source_candidate_id = candidate_id
                session.add(model)
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def invalidate(session: AsyncSession, source: Seed, action: str) -> None:
    service = PageFidelityService(session)
    state = await session.get(PageReviewStateModel, (source.document_id, 1), populate_existing=True)
    assert state is not None
    if action == "reread":
        await queue_source_read(
            session,
            source.document_id,
            page_number=1,
            expected_page_version=state.version,
            actor_id=ACTOR.subject_id,
            reason=EVIDENCE,
        )
    elif action in {"removed", "unresolved"}:
        values: dict[str, object] = {
            "metadata_scope_version": SourceDocumentModel.metadata_scope_version + 1
        }
        if action == "removed":
            values.update(
                active_for_ai=False,
                removal_reason=EVIDENCE,
                removed_by=ACTOR.subject_id,
                removed_at=datetime.now(UTC),
            )
        else:
            values["metadata_review_required"] = True
        await session.execute(
            update(SourceDocumentModel)
            .where(SourceDocumentModel.id == source.document_id)
            .values(**values)
        )
        await session.commit()
    elif action == "excluded":
        await service.exclude_page(
            source.document_id,
            1,
            expected_version=state.version,
            actor_id=ACTOR.subject_id,
            reason=EVIDENCE,
        )
    else:
        new = await service.edit_page(
            source.document_id,
            1,
            text=SOURCE_TEXT,
            expected_version=state.version,
            actor_id=ACTOR.subject_id,
            reason=EVIDENCE,
        )
        if action == "stale":
            await service.confirm_page(
                source.document_id,
                1,
                candidate_id=new.current_candidate_id,
                expected_version=new.version,
                actor_id=ACTOR.subject_id,
                reason=EVIDENCE,
            )


@pytest.mark.parametrize(
    "change", ["excluded", "unverified", "stale", "unapproved", "removed", "unresolved", "reread"]
)
def test_stale_records_remain_immutable_but_not_eligible(
    lineage_database_url: str, change: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source = await seed(session)
                persistence = KnowledgePersistenceService(session)
                chunk = (
                    await persistence.import_chunk(source.chunk(), actor_id=ACTOR.subject_id)
                ).record
                await persistence.store_chunk_embedding(
                    chunk.id,
                    EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                    actor_id=ACTOR.subject_id,
                )
                if change == "unapproved":
                    await admission(session, source.scope.curriculum_version_id, "quarantined")
                else:
                    await invalidate(session, source, change)
                error = (
                    ActiveKnowledgeSourceRequiredError
                    if change == "removed"
                    else TrustedKnowledgeSourceRequiredError
                )
                with pytest.raises(error):
                    await persistence.store_chunk_embedding(
                        chunk.id,
                        EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                        actor_id=ACTOR.subject_id,
                    )
                await session.rollback()
                stored = await session.get(KnowledgeChunkModel, chunk.id)
                assert stored is not None
                assert stored.review_state is ReviewState.REVIEWED
                assert stored.source_candidate_id == source.candidate_id
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(KnowledgeEmbeddingModel)
                        .where(KnowledgeEmbeddingModel.knowledge_chunk_id == chunk.id)
                    )
                    == 1
                )
                assert (
                    await SqlAlchemyEmbeddingJobRepository(session).load_sources((), (chunk.id,))
                    == ()
                )
                candidates = await PostgresHybridRetrievalRepository(
                    session, embedding_config=CONFIG
                ).retrieve_candidates(
                    query="Fractions", query_vector=(1.0, 0.0, 0.0), filters=source.scope
                )
                assert not candidates.lexical_candidates
                assert not candidates.vector_candidates
                contexts = await SqlAlchemyGenerationRepository(session).list_context_records(
                    (chunk.id,), ()
                )
                with pytest.raises(
                    (GenerationContextSourceUntrustedError, GenerationContextNotFoundError)
                ):
                    GenerationRunService._validate_context_records(
                        source.scope, (chunk.id,), (), contexts
                    )
                if change == "stale":
                    with pytest.raises(SourceImportConflictError):
                        await persistence.import_chunk(source.chunk(), actor_id=ACTOR.subject_id)
                    await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def create_lineage_run(
    session: AsyncSession, source: Seed, chunk: KnowledgeChunk
) -> GenerationCreationResult:
    medium = await session.get(MediumModel, source.scope.medium_id)
    assert medium is not None
    specification = make_uniform_specification((1,), 1)
    specification = replace(
        specification,
        curriculum_scope=replace(
            specification.curriculum_scope,
            curriculum_version_id=source.scope.curriculum_version_id,
            subject_id=source.scope.subject_id,
            medium=medium.code,
        ),
        taxonomy_requirements=(
            replace(
                specification.taxonomy_requirements[0],
                target=TaxonomyTarget(competency_id=source.scope.taxonomy.competency_id),
            ),
        ),
    )
    blueprint = await BlueprintGenerationService(session).create_blueprint(
        source.scope.curriculum_version_id,
        specification,
        seed=35,
        analytics_run_id=None,
        actor_id=ACTOR.subject_id,
    )
    slot = cast(list[dict[str, object]], blueprint.record.blueprint["slots"])[0]
    return await GenerationRunService(
        session,
        create_generation_runtime(Settings(environment="test")),
        DeterministicGenerationDispatcher(),
    ).create(
        source.scope.curriculum_version_id,
        paper_blueprint_id=blueprint.record.id,
        slot_id=str(slot["slot_id"]),
        knowledge_chunk_ids=(chunk.id,),
        historical_question_ids=(),
        idempotency_key=uuid4().hex,
        actor_id=ACTOR.subject_id,
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "candidate_id",
        "candidate_sha256",
        "text",
        "record_id",
        "record_version",
        "source_document_id",
        "source_block_id",
        "source_version",
        "page_number",
        "context_id",
        "trust",
        "ids",
        "legacy",
        "extra_provenance",
    ],
)
def test_database_generation_function_checks_exact_persisted_lineage(
    lineage_database_url: str, corruption: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source = await seed(session)
                chunk = (
                    await KnowledgePersistenceService(session).import_chunk(
                        source.chunk(), actor_id=ACTOR.subject_id
                    )
                ).record
                created = await create_lineage_run(session, source, chunk)
                repository = SqlAlchemyGenerationRepository(session)
                assert await repository.context_lineage_is_current(created.run)
                assert await repository.context_lineage_is_current(created.run, lock_sources=True)
                await session.commit()
                snapshot = deepcopy(created.run.context_snapshot)
                item = cast(list[dict[str, object]], snapshot["items"])[0]
                provenance = cast(dict[str, object], item["provenance"])
                ids = list(created.run.knowledge_chunk_ids)
                if corruption == "ids":
                    ids = [str(uuid4())]
                elif corruption == "legacy":
                    provenance.pop("source_candidate_id")
                    provenance.pop("source_candidate_sha256")
                elif corruption == "extra_provenance":
                    provenance["extra"] = True
                elif corruption in {"candidate_id", "candidate_sha256"}:
                    provenance["source_" + corruption] = str(uuid4())
                elif corruption in {
                    "source_document_id",
                    "source_block_id",
                    "source_version",
                    "page_number",
                }:
                    provenance[corruption] = str(uuid4())
                else:
                    item[corruption] = str(uuid4())
                forged = GenerationRunModel(
                    curriculum_version_id=source.scope.curriculum_version_id,
                    knowledge_chunk_ids=ids,
                    historical_question_ids=[],
                    context_snapshot=snapshot,
                )
                assert not await repository.context_lineage_is_current(forged)
                assert not await repository.context_lineage_is_current(forged, lock_sources=True)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["insert", "success"])
def test_direct_generation_writes_cannot_bypass_invalidated_lineage(
    lineage_database_url: str, stage: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source = await seed(session)
                chunk = (
                    await KnowledgePersistenceService(session).import_chunk(
                        source.chunk(), actor_id=ACTOR.subject_id
                    )
                ).record
                created = await create_lineage_run(session, source, chunk)
                values = {
                    column.name: getattr(created.run, column.name)
                    for column in GenerationRunModel.__table__.columns
                }
                run_id = created.run.id
                await invalidate(session, source, "excluded")
                values["id"] = uuid4()
                values["idempotency_key_hash"] = "sha256:" + uuid4().hex * 2
                statement = (
                    insert(GenerationRunModel).values(**values)
                    if stage == "insert"
                    else update(GenerationRunModel)
                    .where(GenerationRunModel.id == run_id)
                    .values(status="succeeded", version=GenerationRunModel.version + 1)
                )
                with pytest.raises(IntegrityError, match="generation requires current verified"):
                    await session.execute(statement)
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["excluded", "unapproved"])
def test_generation_lineage_lock_serializes_page_and_catalogue_changes(
    lineage_database_url: str, change: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                source = await seed(session)
                chunk = (
                    await KnowledgePersistenceService(session).import_chunk(
                        source.chunk(), actor_id=ACTOR.subject_id
                    )
                ).record
                created = await create_lineage_run(session, source, chunk)
                repository = SqlAlchemyGenerationRepository(session)
                assert await repository.context_lineage_is_current(created.run, lock_sources=True)
                started = asyncio.Event()

                async def revoke() -> None:
                    async with sessions() as other:
                        started.set()
                        if change == "unapproved":
                            await admission(
                                other, source.scope.curriculum_version_id, "quarantined"
                            )
                        else:
                            await invalidate(other, source, change)

                task = asyncio.create_task(revoke())
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
                    await session.commit()
                    await asyncio.wait_for(task, timeout=5)
                    assert not await repository.context_lineage_is_current(created.run)
                finally:
                    await session.rollback()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_embedding_insert_waits_for_page_edit_then_rechecks_lineage(
    lineage_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                source = await seed(session)
                chunk = (
                    await KnowledgePersistenceService(session).import_chunk(
                        source.chunk(), actor_id=ACTOR.subject_id
                    )
                ).record
                configuration_id = uuid4()
                session.add(
                    EmbeddingConfigurationModel.from_domain(
                        configuration_id, replace(CONFIG, version=uuid4().hex), ACTOR.subject_id
                    )
                )
                await session.commit()
                await session.get(SourceDocumentModel, source.document_id, with_for_update=True)
                started = asyncio.Event()

                async def insert_embedding() -> None:
                    async with sessions() as other:
                        started.set()
                        other.add(
                            KnowledgeEmbeddingModel(
                                id=uuid4(),
                                knowledge_chunk_id=chunk.id,
                                embedding_configuration_id=configuration_id,
                                embedding_dimension=3,
                                source_text_sha256=hashlib.sha256(chunk.text.encode()).hexdigest(),
                                embedding=[1.0, 0.0, 0.0],
                                created_by=ACTOR.subject_id,
                            )
                        )
                        with pytest.raises(
                            IntegrityError, match="current verified knowledge lineage"
                        ):
                            await other.commit()
                        await other.rollback()

                task = asyncio.create_task(insert_embedding())
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
                    await invalidate(session, source, "excluded")
                    await asyncio.wait_for(task, timeout=5)
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(KnowledgeEmbeddingModel)
                            .where(KnowledgeEmbeddingModel.knowledge_chunk_id == chunk.id)
                        )
                        == 0
                    )
                finally:
                    await session.rollback()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("when", ["before_provider", "during_provider", "unchanged"])
def test_generation_worker_rechecks_lineage_before_provider_and_final_persist(
    lineage_database_url: str, when: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        entered, release = threading.Event(), threading.Event()
        runtime = create_generation_runtime(Settings(environment="test"))
        provider = runtime.build_provider(runtime.active_config)
        calls: list[UUID] = []

        class PausedProvider:
            def generate(self, request: GenerationRequest) -> GenerationResult:
                calls.append(request.identity.generation_id)
                entered.set()
                if when == "during_provider":
                    assert release.wait(timeout=10)
                return provider.generate(request)

        worker_runtime = GenerationRuntimeRegistry(
            runtime.active_config, provider_factory=lambda _: PausedProvider()
        )
        try:
            async with sessions() as session:
                source = await seed(session)
                chunk = (
                    await KnowledgePersistenceService(session).import_chunk(
                        source.chunk(), actor_id=ACTOR.subject_id
                    )
                ).record
                created = await create_lineage_run(session, source, chunk)
                run_id, job_id = created.run.id, created.job.id
                if when == "before_provider":
                    await invalidate(session, source, "excluded")

            async def work() -> bool:
                worker_engine = create_async_engine(lineage_database_url)
                try:
                    async with async_sessionmaker(worker_engine, expire_on_commit=False)() as other:
                        return await GenerationWorkerService(
                            other, worker_runtime, sleep=lambda _: None
                        ).process(job_id, run_id)
                finally:
                    await worker_engine.dispose()

            task = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(work())))
            try:
                if when == "during_provider":
                    assert await asyncio.to_thread(entered.wait, 5)
                    async with sessions() as session:
                        await invalidate(session, source, "excluded")
                    release.set()
                assert await asyncio.wait_for(task, timeout=15)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
            async with sessions() as session:
                run = await session.get(GenerationRunModel, run_id)
                assert run is not None
                if when == "unchanged":
                    assert run.status == "succeeded"
                    assert run.candidate is not None
                    assert run.failure_code is None
                else:
                    assert run.status == "failed"
                    assert run.candidate is None
                    assert run.failure_code == "generation_source_invalid"
                expected_attempts = 0 if when == "before_provider" else 1
                assert len(calls) == expected_attempts
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(GenerationAttemptModel)
                        .where(GenerationAttemptModel.generation_run_id == run_id)
                    )
                    == expected_attempts
                )
        finally:
            release.set()
            await engine.dispose()

    asyncio.run(scenario())


def test_migration_keeps_legacy_chunks_and_vectors_unbound_and_ineligible() -> None:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="disposable_legacy_lineage",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            command.upgrade(_config_for_database(url), "0034_catalogue_admission")
            chunk_id, vector_id, configuration_id = uuid4(), uuid4(), uuid4()

            async def legacy_fixture() -> tuple[RetrievalScope, UUID, UUID]:
                engine = create_async_engine(url)
                try:
                    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                        scope = await seed_curriculum_scope(session)
                        document_id, page_id, block_id = uuid4(), uuid4(), uuid4()
                        checksum = hashlib.sha256(document_id.bytes).hexdigest()
                        await session.execute(
                            text(
                                "INSERT INTO source_documents (id, checksum_sha256, object_key, "
                                "original_filename, content_type, size_bytes, document_type, "
                                "extraction_status, curriculum_version_id, year, paper_code, "
                                "extraction_attempt_count, extraction_started_at, "
                                "original_page_count, "
                                "metadata_review_required, created_by, updated_by) VALUES "
                                "(:id, :checksum, :key, 'legacy-lineage.pdf', 'application/pdf', "
                                "100, 'past_paper', 'extraction_pending', :curriculum, 2020, 'P1', "
                                "1, now(), 1, false, :actor, :actor)"
                            ),
                            {
                                "id": document_id,
                                "checksum": checksum,
                                "key": f"sources/{checksum}.pdf",
                                "curriculum": scope.curriculum_version_id,
                                "actor": ACTOR.subject_id,
                            },
                        )
                        audit = {"created_by": ACTOR.subject_id, "updated_by": ACTOR.subject_id}
                        session.add(
                            SourcePageModel(
                                id=page_id,
                                source_document_id=document_id,
                                page_number=1,
                                extractor="synthetic-fixture",
                                extractor_version="v1",
                                raw_text=SOURCE_TEXT,
                                reviewed_text=SOURCE_TEXT,
                                character_count=len(SOURCE_TEXT),
                                block_count=1,
                                **audit,
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
                                extractor="synthetic-fixture",
                                extractor_version="v1",
                                bbox_x0=0.0,
                                bbox_y0=0.0,
                                bbox_x1=1.0,
                                bbox_y1=1.0,
                                raw_text=SOURCE_TEXT,
                                reviewed_text=SOURCE_TEXT,
                                character_count=len(SOURCE_TEXT),
                                **audit,
                            )
                        )
                        await session.flush()
                        await session.execute(
                            text(
                                "UPDATE source_documents SET extraction_status='extracted', "
                                "extractor='synthetic-fixture', extractor_version='v1', "
                                "extracted_page_count=1, extracted_block_count=1, "
                                "extracted_character_count=:count, native_text_page_ratio=1.0, "
                                "needs_ocr=false, ocr_page_count=0, extraction_config='{}'::jsonb, "
                                "extraction_completed_at=now() WHERE id=:id"
                            ),
                            {"id": document_id, "count": len(SOURCE_TEXT)},
                        )
                        for status in ("in_review", "trusted"):
                            await session.execute(
                                text(
                                    "UPDATE source_documents SET extraction_status=:status "
                                    "WHERE id=:id"
                                ),
                                {"id": document_id, "status": status},
                            )
                        await session.execute(
                            text(
                                "INSERT INTO knowledge_chunks (id, curriculum_version_id, "
                                "chunk_type, text, educational_boundary, sequence, "
                                "source_document_id, page_number, source_block_id, review_state, "
                                "competency_id, created_by, updated_by) "
                                "VALUES (:id, :curriculum, 'explanation', :text, 'Fractions', 0, "
                                ":source, 1, :block, 'reviewed', :competency, :actor, :actor)"
                            ),
                            {
                                "id": chunk_id,
                                "curriculum": scope.curriculum_version_id,
                                "text": SOURCE_TEXT,
                                "source": document_id,
                                "block": block_id,
                                "competency": scope.taxonomy.competency_id,
                                "actor": ACTOR.subject_id,
                            },
                        )
                        session.add(
                            EmbeddingConfigurationModel.from_domain(
                                configuration_id, CONFIG, ACTOR.subject_id
                            )
                        )
                        await session.flush()
                        session.add(
                            KnowledgeEmbeddingModel(
                                id=vector_id,
                                knowledge_chunk_id=chunk_id,
                                embedding_configuration_id=configuration_id,
                                embedding_dimension=3,
                                source_text_sha256=hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
                                embedding=[1.0, 0.0, 0.0],
                                created_by=ACTOR.subject_id,
                            )
                        )
                        await session.commit()
                        return scope, document_id, block_id
                finally:
                    await engine.dispose()

            scope, document_id, block_id = asyncio.run(legacy_fixture())
            upgrade_database(url)

            async def check_preserved() -> None:
                engine = create_async_engine(url)
                try:
                    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                        chunk = await session.get(KnowledgeChunkModel, chunk_id)
                        assert chunk is not None
                        assert chunk.source_candidate_id is None
                        assert chunk.review_state is ReviewState.REVIEWED
                        candidate_id = await verify_synthetic_page(
                            session, document_id, SOURCE_TEXT, actor_id=ACTOR.subject_id
                        )
                        source = Seed(document_id, candidate_id, block_id, scope)
                        assert chunk.source_candidate_id is None
                        assert await session.get(KnowledgeEmbeddingModel, vector_id) is not None
                        assert (
                            await SqlAlchemyEmbeddingJobRepository(session).load_sources(
                                (), (chunk_id,)
                            )
                            == ()
                        )
                        candidates = await PostgresHybridRetrievalRepository(
                            session, embedding_config=CONFIG
                        ).retrieve_candidates(
                            query="Fractions", query_vector=(1.0, 0.0, 0.0), filters=source.scope
                        )
                        assert not candidates.lexical_candidates
                        assert not candidates.vector_candidates
                        contexts = await SqlAlchemyGenerationRepository(
                            session
                        ).list_context_records((chunk_id,), ())
                        with pytest.raises(GenerationContextSourceUntrustedError):
                            GenerationRunService._validate_context_records(
                                source.scope, (chunk_id,), (), contexts
                            )
                        with pytest.raises(IntegrityError, match="binding is immutable"):
                            await session.execute(
                                update(KnowledgeChunkModel)
                                .where(KnowledgeChunkModel.id == chunk_id)
                                .values(source_candidate_id=source.candidate_id, version=1)
                            )
                        await session.rollback()
                finally:
                    await engine.dispose()

            asyncio.run(check_preserved())


class SyntheticPassValidator:
    validator_id = "disposable-lineage-check"
    validator_version = "v1"

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        self.calls += 1
        return (
            ValidationFinding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code="fixture.lineage",
                status=FindingStatus.PASS,
                message=EVIDENCE,
                evidence=(
                    FindingEvidence(
                        location="$.candidate",
                        expected="Synthetic pipeline fixture",
                        observed=validation_input.candidate_id,
                    ),
                ),
            ),
        )


def synthetic_validation_pipeline(validator: SyntheticPassValidator) -> ValidationPipeline:
    return ValidationPipeline(
        (validator, SchemaCompletenessValidator()), version="disposable-lineage-validation.v1"
    )


async def succeeded_lineage_run(session: AsyncSession) -> tuple[Seed, GenerationCreationResult]:
    source = await seed(session)
    chunk = (
        await KnowledgePersistenceService(session).import_chunk(
            source.chunk(), actor_id=ACTOR.subject_id
        )
    ).record
    created = await create_lineage_run(session, source, chunk)
    assert await GenerationWorkerService(
        session, create_generation_runtime(Settings(environment="test")), sleep=lambda _: None
    ).process(created.job.id, created.run.id)
    run = await session.get(GenerationRunModel, created.run.id, populate_existing=True)
    assert run is not None
    assert run.status == "succeeded"
    return source, created


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "change", ["excluded", "unverified", "stale", "reread", "removed", "unresolved", "unapproved"]
)
def test_fresh_validation_cannot_use_stale_sources_or_replay_cached_pass(
    lineage_database_url: str, existing: bool, change: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source, created = await succeeded_lineage_run(session)
                run_id, curriculum_id = created.run.id, source.scope.curriculum_version_id
                validator = SyntheticPassValidator()
                service = ValidationRunService(session, synthetic_validation_pipeline(validator))
                validation_id: UUID | None = None
                history: dict[str, object] | None = None
                if existing:
                    report = await service.create(
                        curriculum_id, generation_run_id=run_id, actor_id=ACTOR.subject_id
                    )
                    assert report.run.overall_status == "pass"
                    validation_id = report.run.id
                    history = deepcopy(report.run.input_snapshot)
                if change == "unapproved":
                    await admission(session, curriculum_id, "quarantined")
                else:
                    await invalidate(session, source, change)
                with pytest.raises(ValidationGenerationIntegrityError, match="current verified"):
                    await service.create(
                        curriculum_id, generation_run_id=run_id, actor_id=ACTOR.subject_id
                    )
                assert validator.calls == int(existing)
                count = await session.scalar(
                    select(func.count())
                    .select_from(ValidationRunModel)
                    .where(ValidationRunModel.generation_run_id == run_id)
                )
                assert count == int(existing)
                if validation_id is not None:
                    stored = await service.get_run(curriculum_id, validation_id)
                    assert stored.input_snapshot == history
                    assert stored.overall_status == "pass"
                    findings = await service.list_findings(
                        curriculum_id, validation_id, limit=10, offset=0
                    )
                    assert len(findings) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_direct_validation_insert_rechecks_current_lineage(lineage_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source, created = await succeeded_lineage_run(session)
                service = ValidationRunService(
                    session, synthetic_validation_pipeline(SyntheticPassValidator())
                )
                report = await service.create(
                    source.scope.curriculum_version_id,
                    generation_run_id=created.run.id,
                    actor_id=ACTOR.subject_id,
                )
                values = {
                    column.name: getattr(report.run, column.name)
                    for column in ValidationRunModel.__table__.columns
                }
                values.update(id=uuid4(), pipeline_version="disposable-stale-validation.v2")
                await invalidate(session, source, "excluded")
                with pytest.raises(IntegrityError, match="current verified"):
                    await session.execute(insert(ValidationRunModel).values(**values))
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["create", "start", "approve"])
def test_new_review_commands_cannot_reuse_invalidated_pass(
    lineage_database_url: str, stage: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source, created = await succeeded_lineage_run(session)
                curriculum_id, candidate_id = source.scope.curriculum_version_id, created.run.id
                validation = await ValidationRunService(
                    session, synthetic_validation_pipeline(SyntheticPassValidator())
                ).create(curriculum_id, generation_run_id=candidate_id, actor_id=ACTOR.subject_id)
                validation_id = validation.run.id
                review = ReviewCandidateService(session)
                if stage != "create":
                    await review.create(
                        curriculum_id, validation_run_id=validation_id, principal=ACTOR
                    )
                if stage == "approve":
                    await review.start_review(
                        curriculum_id, candidate_id, expected_version=2, principal=ACTOR
                    )
                await invalidate(session, source, "excluded")

                async def operation() -> None:
                    if stage == "create":
                        await review.create(
                            curriculum_id, validation_run_id=validation_id, principal=ACTOR
                        )
                    elif stage == "start":
                        await review.start_review(
                            curriculum_id, candidate_id, expected_version=2, principal=ACTOR
                        )
                    else:
                        await review.approve(
                            curriculum_id,
                            candidate_id,
                            expected_version=3,
                            note=EVIDENCE,
                            principal=ACTOR,
                        )

                with pytest.raises(IntegrityError, match="current verified"):
                    await operation()
                await session.rollback()
                record = await session.get(QuestionCandidateModel, candidate_id)
                if stage == "create":
                    assert record is None
                else:
                    assert record is not None
                    assert record.state == ("validated" if stage == "start" else "in_review")
                    assert (
                        await review.get(curriculum_id, candidate_id, principal=ACTOR)
                    ).candidate.id == candidate_id
                    if stage == "approve":
                        rejected = await review.reject(
                            curriculum_id,
                            candidate_id,
                            expected_version=3,
                            reason=EVIDENCE,
                            principal=ACTOR,
                        )
                        assert rejected.candidate.state == "rejected"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_source_invalidation_preserves_publication_history_but_blocks_new_version(
    lineage_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                source, created = await succeeded_lineage_run(session)
                curriculum_id, candidate_id = source.scope.curriculum_version_id, created.run.id
                validation = await ValidationRunService(
                    session, synthetic_validation_pipeline(SyntheticPassValidator())
                ).create(curriculum_id, generation_run_id=candidate_id, actor_id=ACTOR.subject_id)
                review = ReviewCandidateService(session)
                await review.create(
                    curriculum_id, validation_run_id=validation.run.id, principal=ACTOR
                )
                await review.start_review(
                    curriculum_id, candidate_id, expected_version=2, principal=ACTOR
                )
                await review.approve(
                    curriculum_id, candidate_id, expected_version=3, note=EVIDENCE, principal=ACTOR
                )
                publication = PaperPublicationService(session)
                draft = await publication.create_draft(
                    curriculum_id,
                    paper_blueprint_id=created.run.paper_blueprint_id,
                    title="Disposable source lineage paper",
                    candidate_ids=(candidate_id,),
                    idempotency_key=uuid4().hex,
                    principal=ACTOR,
                )
                paper_id = draft.record.paper.id
                published = await publication.publish(
                    curriculum_id, paper_id, expected_version=1, principal=ACTOR
                )
                frozen_snapshot = deepcopy(published.record.publication.snapshot)
                await invalidate(session, source, "excluded")
                historical = await publication.get_publication(
                    curriculum_id, paper_id, 1, principal=ACTOR
                )
                assert historical.publication.snapshot == frozen_snapshot
                await publication.revise(
                    curriculum_id,
                    paper_id,
                    expected_version=1,
                    candidate_ids=(candidate_id,),
                    title=None,
                    principal=ACTOR,
                )
                with pytest.raises(IntegrityError, match="current verified"):
                    await publication.publish(
                        curriculum_id, paper_id, expected_version=2, principal=ACTOR
                    )
                historical = await publication.get_publication(
                    curriculum_id, paper_id, 1, principal=ACTOR
                )
                assert historical.publication.snapshot == frozen_snapshot
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_validation_holds_source_lineage_until_report_commit(lineage_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        entered, release = threading.Event(), threading.Event()

        class PausedValidator(SyntheticPassValidator):
            def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
                entered.set()
                assert release.wait(timeout=10)
                return super().validate(validation_input)

        tasks: list[asyncio.Task[object]] = []
        validator = PausedValidator()
        pipeline = synthetic_validation_pipeline(validator)
        try:
            async with sessions() as session:
                source, created = await succeeded_lineage_run(session)
                run_id, curriculum_id = created.run.id, source.scope.curriculum_version_id

            async def validate() -> UUID:
                async with sessions() as session:
                    result = await ValidationRunService(session, pipeline).create(
                        curriculum_id, generation_run_id=run_id, actor_id=ACTOR.subject_id
                    )
                    assert result.run.overall_status == "pass"
                    return result.run.id

            validation_task = asyncio.create_task(validate())
            tasks.append(cast(asyncio.Task[object], validation_task))
            assert await asyncio.to_thread(entered.wait, 5)
            started = asyncio.Event()

            async def revoke() -> None:
                async with sessions() as session:
                    started.set()
                    await invalidate(session, source, "excluded")

            revoke_task = asyncio.create_task(revoke())
            tasks.append(cast(asyncio.Task[object], revoke_task))
            await asyncio.wait_for(started.wait(), timeout=5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(revoke_task), timeout=0.2)
            release.set()
            report_id = await asyncio.wait_for(validation_task, timeout=5)
            await asyncio.wait_for(revoke_task, timeout=5)
            async with sessions() as session:
                service = ValidationRunService(session, pipeline)
                assert (await service.get_run(curriculum_id, report_id)).overall_status == "pass"
                with pytest.raises(ValidationGenerationIntegrityError, match="current verified"):
                    await service.create(
                        curriculum_id, generation_run_id=run_id, actor_id=ACTOR.subject_id
                    )
            assert validator.calls == 1
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await engine.dispose()

    asyncio.run(scenario())


def test_validation_waits_for_prior_invalidation_and_rechecks_before_pipeline(
    lineage_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(lineage_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        validator = SyntheticPassValidator()
        try:
            async with sessions() as holder:
                source, created = await succeeded_lineage_run(holder)
                run_id, curriculum_id = created.run.id, source.scope.curriculum_version_id
                await holder.get(SourceDocumentModel, source.document_id, with_for_update=True)
                started = asyncio.Event()

                async def validate() -> None:
                    async with sessions() as session:
                        started.set()
                        service = ValidationRunService(
                            session, synthetic_validation_pipeline(validator)
                        )
                        with pytest.raises(
                            ValidationGenerationIntegrityError, match="current verified"
                        ):
                            await service.create(
                                curriculum_id, generation_run_id=run_id, actor_id=ACTOR.subject_id
                            )

                task = asyncio.create_task(validate())
                try:
                    await asyncio.wait_for(started.wait(), timeout=5)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
                    await invalidate(holder, source, "excluded")
                    await asyncio.wait_for(task, timeout=5)
                    assert validator.calls == 0
                    assert (
                        await holder.scalar(
                            select(func.count())
                            .select_from(ValidationRunModel)
                            .where(ValidationRunModel.generation_run_id == run_id)
                        )
                        == 0
                    )
                finally:
                    await holder.rollback()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
