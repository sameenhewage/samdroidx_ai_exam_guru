import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.infrastructure.migrations import (
    assert_database_schema_current,
    upgrade_database,
)
from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    Provenance,
    QuestionType,
    ReviewState,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig, EmbeddingResult
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.knowledge.repository import (
    EmbeddingSpaceConflictError,
    KnowledgeRecordNotFoundError,
    SourceImportConflictError,
)
from exam_guru_api.knowledge.service import (
    EmbeddingDimensionMismatchError,
    EmbeddingRequiresReviewedRecordError,
    FinalKnowledgeRecordError,
    KnowledgePersistenceService,
    TrustedKnowledgeSourceRequiredError,
)

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ACTOR_ID = UUID(int=90_000)


@dataclass(frozen=True, slots=True)
class SeededSource:
    curriculum_version_id: UUID
    competency_id: UUID
    skill_id: UUID
    source_document_id: UUID
    source_block_id: UUID


@pytest.fixture(scope="module")
def knowledge_database_url() -> Iterator[str]:
    credentials = ("exam_guru", "knowledge-" + "only")
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_knowledge_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        assert_database_schema_current(database_url)
        yield database_url


async def seed_curriculum(session: AsyncSession, offset: int) -> tuple[UUID, UUID, UUID]:
    exam_id = UUID(int=100_000 + offset)
    medium_id = UUID(int=110_000 + offset)
    curriculum_version_id = UUID(int=120_000 + offset)
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code=f"G5K-{offset}",
                name="Grade 5 Scholarship Examination",
                grade=5,
                active=True,
                created_by=ACTOR_ID,
                updated_by=ACTOR_ID,
            ),
            MediumModel(
                id=medium_id,
                code=f"k{offset}",
                name=f"Knowledge medium {offset}",
                active=True,
                created_by=ACTOR_ID,
                updated_by=ACTOR_ID,
            ),
        ]
    )
    await session.flush()
    session.add(
        CurriculumVersionModel(
            id=curriculum_version_id,
            exam_configuration_id=exam_id,
            medium_id=medium_id,
            code=f"K-{offset}",
            title=f"Knowledge curriculum {offset}",
            active=True,
            created_by=ACTOR_ID,
            updated_by=ACTOR_ID,
        )
    )
    await session.flush()

    competency = TaxonomyNode(
        id=UUID(int=130_000 + offset),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Competency 1",
        review_state=TaxonomyReviewState.REVIEWED,
    )
    skill = TaxonomyNode(
        id=UUID(int=140_000 + offset),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.SKILL,
        code="S1",
        title="Skill 1",
        parent_id=competency.id,
        review_state=TaxonomyReviewState.REVIEWED,
    )
    session.add(TaxonomyNodeModel.from_domain(competency, ACTOR_ID))
    await session.flush()
    session.add(TaxonomyNodeModel.from_domain(skill, ACTOR_ID))
    await session.flush()
    return curriculum_version_id, competency.id, skill.id


async def seed_trusted_source(
    session: AsyncSession,
    *,
    offset: int,
    curriculum_version_id: UUID,
    document_type: SourceDocumentType,
    finalize_review: bool = True,
) -> tuple[UUID, UUID]:
    document_id = UUID(int=150_000 + offset)
    page_id = UUID(int=160_000 + offset)
    block_id = UUID(int=170_000 + offset)
    text = f"Reviewed source block {offset}"
    document = SourceDocumentModel(
        id=document_id,
        checksum_sha256=sha256(f"source-{offset}".encode()).hexdigest(),
        object_key=f"sources/knowledge-{offset}.pdf",
        original_filename=f"knowledge-{offset}.pdf",
        content_type="application/pdf",
        size_bytes=100 + offset,
        document_type=document_type,
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        curriculum_version_id=curriculum_version_id,
        year=2020 if document_type is SourceDocumentType.PAST_PAPER else None,
        paper_code="P1" if document_type is SourceDocumentType.PAST_PAPER else None,
        extraction_attempt_count=1,
        extraction_started_at=datetime.now(UTC),
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
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
            created_by=ACTOR_ID,
            updated_by=ACTOR_ID,
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
            created_by=ACTOR_ID,
            updated_by=ACTOR_ID,
        )
    )
    await session.flush()

    if finalize_review:
        document.extraction_status = ExtractionStatus.EXTRACTED
        document.extractor = "fixture"
        document.extractor_version = "v1"
        document.extracted_page_count = 1
        document.extracted_block_count = 1
        document.extracted_character_count = len(text)
        document.native_text_page_ratio = 1.0
        document.needs_ocr = False
        document.extraction_completed_at = datetime.now(UTC)
        await session.flush()
        document.extraction_status = ExtractionStatus.IN_REVIEW
        await session.flush()
        document.extraction_status = ExtractionStatus.TRUSTED
        await session.flush()
    return document_id, block_id


async def seed_foundation(
    session: AsyncSession,
    offset: int,
    *,
    document_type: SourceDocumentType = SourceDocumentType.PAST_PAPER,
) -> SeededSource:
    curriculum_version_id, competency_id, skill_id = await seed_curriculum(session, offset)
    source_document_id, source_block_id = await seed_trusted_source(
        session,
        offset=offset,
        curriculum_version_id=curriculum_version_id,
        document_type=document_type,
    )
    await session.commit()
    return SeededSource(
        curriculum_version_id=curriculum_version_id,
        competency_id=competency_id,
        skill_id=skill_id,
        source_document_id=source_document_id,
        source_block_id=source_block_id,
    )


def question_record(seed: SeededSource, *, record_id: UUID) -> HistoricalQuestion:
    return HistoricalQuestion(
        id=record_id,
        curriculum_version_id=seed.curriculum_version_id,
        year=2020,
        paper_code="P1",
        question_number="1",
        text="Which answer is correct?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=2,
        provenance=Provenance(
            source_document_id=seed.source_document_id,
            page_number=1,
            source_block_id=seed.source_block_id,
        ),
    )


def chunk_record(seed: SeededSource, *, record_id: UUID, sequence: int = 0) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=record_id,
        curriculum_version_id=seed.curriculum_version_id,
        chunk_type=ChunkType.EXPLANATION,
        text="A meaningful reviewed educational explanation.",
        educational_boundary="Competency 1 / Skill 1 / explanation",
        sequence=sequence,
        provenance=Provenance(
            source_document_id=seed.source_document_id,
            page_number=1,
            source_block_id=seed.source_block_id,
        ),
    )


@pytest.mark.integration
def test_source_import_review_and_reembedding_are_idempotent_and_versioned(
    knowledge_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(knowledge_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            seed = await seed_foundation(session, 1)
            service = KnowledgePersistenceService(session)
            question = question_record(seed, record_id=UUID(int=180_001))
            chunk = chunk_record(seed, record_id=UUID(int=190_001))

            imported_question = await service.import_question(question, actor_id=ACTOR_ID)
            duplicate_question = await service.import_question(
                replace(question, id=UUID(int=180_002)),
                actor_id=ACTOR_ID,
            )
            imported_chunk = await service.import_chunk(chunk, actor_id=ACTOR_ID)
            duplicate_chunk = await service.import_chunk(
                replace(chunk, id=UUID(int=190_002)),
                actor_id=ACTOR_ID,
            )

            assert imported_question.deduplicated is False
            assert duplicate_question.deduplicated is True
            assert duplicate_question.record.id == question.id
            assert imported_chunk.deduplicated is False
            assert duplicate_chunk.deduplicated is True
            assert duplicate_chunk.record.id == chunk.id

            with pytest.raises(SourceImportConflictError):
                await service.import_question(
                    replace(question, id=UUID(int=180_003), text="Conflicting source text"),
                    actor_id=ACTOR_ID,
                )
            with pytest.raises(SourceImportConflictError):
                await service.import_chunk(
                    replace(chunk, id=UUID(int=190_004), text="Conflicting chunk text"),
                    actor_id=ACTOR_ID,
                )

            classified_question = await service.classify_question(
                seed.curriculum_version_id,
                question.id,
                competency_id=seed.competency_id,
                skill_id=seed.skill_id,
                sub_skill_id=None,
                learning_concept_id=None,
                expected_version=0,
                actor_id=ACTOR_ID,
            )
            classified_chunk = await service.classify_chunk(
                seed.curriculum_version_id,
                chunk.id,
                competency_id=seed.competency_id,
                skill_id=seed.skill_id,
                sub_skill_id=None,
                learning_concept_id=None,
                expected_version=0,
                actor_id=ACTOR_ID,
            )
            assert classified_question.competency_id == seed.competency_id
            assert classified_chunk.skill_id == seed.skill_id
            assert (
                await service.classify_question(
                    seed.curriculum_version_id,
                    question.id,
                    competency_id=seed.competency_id,
                    skill_id=seed.skill_id,
                    sub_skill_id=None,
                    learning_concept_id=None,
                    expected_version=1,
                    actor_id=ACTOR_ID,
                )
                == classified_question
            )
            assert (
                await service.classify_chunk(
                    seed.curriculum_version_id,
                    chunk.id,
                    competency_id=seed.competency_id,
                    skill_id=seed.skill_id,
                    sub_skill_id=None,
                    learning_concept_id=None,
                    expected_version=1,
                    actor_id=ACTOR_ID,
                )
                == classified_chunk
            )

            await service.transition_question_review(
                seed.curriculum_version_id,
                question.id,
                ReviewState.IN_REVIEW,
                expected_version=1,
                actor_id=ACTOR_ID,
            )
            reviewed_question = await service.transition_question_review(
                seed.curriculum_version_id,
                question.id,
                ReviewState.REVIEWED,
                expected_version=2,
                actor_id=ACTOR_ID,
            )
            await service.transition_chunk_review(
                seed.curriculum_version_id,
                chunk.id,
                ReviewState.IN_REVIEW,
                expected_version=1,
                actor_id=ACTOR_ID,
            )
            reviewed_chunk = await service.transition_chunk_review(
                seed.curriculum_version_id,
                chunk.id,
                ReviewState.REVIEWED,
                expected_version=2,
                actor_id=ACTOR_ID,
            )
            repeated_review = await service.transition_chunk_review(
                seed.curriculum_version_id,
                chunk.id,
                ReviewState.REVIEWED,
                expected_version=3,
                actor_id=ACTOR_ID,
            )
            repeated_question_review = await service.transition_question_review(
                seed.curriculum_version_id,
                question.id,
                ReviewState.REVIEWED,
                expected_version=3,
                actor_id=ACTOR_ID,
            )
            assert reviewed_question.review_state is ReviewState.REVIEWED
            assert reviewed_chunk.review_state is ReviewState.REVIEWED
            assert repeated_question_review == reviewed_question
            assert repeated_review == reviewed_chunk
            with pytest.raises(FinalKnowledgeRecordError):
                await service.classify_question(
                    seed.curriculum_version_id,
                    question.id,
                    competency_id=seed.competency_id,
                    skill_id=None,
                    sub_skill_id=None,
                    learning_concept_id=None,
                    expected_version=3,
                    actor_id=ACTOR_ID,
                )

            config_v1 = EmbeddingConfig(
                provider="fixture-provider",
                model="fixture-model",
                dimension=3,
                version="v1",
                config_fingerprint="fixture-space-v1",
            )
            config_v2 = replace(
                config_v1,
                version="v2",
                config_fingerprint="fixture-space-v2",
            )
            vector_v1 = EmbeddingResult(vector=(0.1, 0.2, 0.3), config=config_v1)
            vector_v2 = EmbeddingResult(vector=(0.2, 0.3, 0.4), config=config_v2)

            first_embedding = await service.store_chunk_embedding(
                chunk.id,
                vector_v1,
                actor_id=ACTOR_ID,
            )
            repeated_embedding = await service.store_chunk_embedding(
                chunk.id,
                vector_v1,
                actor_id=ACTOR_ID,
            )
            versioned_embedding = await service.store_chunk_embedding(
                chunk.id,
                vector_v2,
                actor_id=ACTOR_ID,
            )
            question_embedding = await service.store_question_embedding(
                question.id,
                vector_v1,
                actor_id=ACTOR_ID,
            )
            question_with_metadata = await service.get_question(
                seed.curriculum_version_id,
                question.id,
            )
            chunk_with_metadata = await service.get_chunk(
                seed.curriculum_version_id,
                chunk.id,
            )

            assert first_embedding.deduplicated is False
            assert repeated_embedding.deduplicated is True
            assert repeated_embedding.id == first_embedding.id
            assert versioned_embedding.id != first_embedding.id
            assert versioned_embedding.configuration_id != first_embedding.configuration_id
            assert question_embedding.deduplicated is False
            assert len(question_with_metadata.embedding_configurations) == 1
            assert question_with_metadata.embedding_configurations[0].provider == "fixture-provider"
            assert len(chunk_with_metadata.embedding_configurations) == 2
            assert chunk_with_metadata.embedding_status.value == "embedded"

            with pytest.raises(EmbeddingDimensionMismatchError):
                await service.store_chunk_embedding(
                    chunk.id,
                    EmbeddingResult(vector=(0.1, 0.2), config=config_v1),
                    actor_id=ACTOR_ID,
                )
            with pytest.raises(EmbeddingSpaceConflictError):
                await service.store_chunk_embedding(
                    chunk.id,
                    EmbeddingResult(
                        vector=(0.1, 0.2, 0.3, 0.4),
                        config=replace(config_v1, dimension=4),
                    ),
                    actor_id=ACTOR_ID,
                )

            draft_chunk = chunk_record(seed, record_id=UUID(int=190_003), sequence=1)
            await service.import_chunk(draft_chunk, actor_id=ACTOR_ID)
            with pytest.raises(EmbeddingRequiresReviewedRecordError):
                await service.store_chunk_embedding(
                    draft_chunk.id,
                    vector_v1,
                    actor_id=ACTOR_ID,
                )
            with pytest.raises(KnowledgeRecordNotFoundError):
                await service.transition_question_review(
                    seed.curriculum_version_id,
                    UUID(int=999_001),
                    ReviewState.IN_REVIEW,
                    expected_version=0,
                    actor_id=ACTOR_ID,
                )
            with pytest.raises(KnowledgeRecordNotFoundError):
                await service.transition_chunk_review(
                    seed.curriculum_version_id,
                    UUID(int=999_002),
                    ReviewState.IN_REVIEW,
                    expected_version=0,
                    actor_id=ACTOR_ID,
                )

        async with sessions() as session:
            question_count = await session.scalar(select(func.count(HistoricalQuestionModel.id)))
            chunk_count = await session.scalar(select(func.count(KnowledgeChunkModel.id)))
            configs = list(
                await session.scalars(
                    select(EmbeddingConfigurationModel).order_by(
                        EmbeddingConfigurationModel.version
                    )
                )
            )
            embeddings = list(
                await session.scalars(
                    select(KnowledgeEmbeddingModel).order_by(KnowledgeEmbeddingModel.id)
                )
            )
            dimensions = set(
                await session.scalars(select(func.vector_dims(KnowledgeEmbeddingModel.embedding)))
            )
            self_distance = await session.scalar(
                select(KnowledgeEmbeddingModel.embedding.cosine_distance([0.1, 0.2, 0.3])).where(
                    KnowledgeEmbeddingModel.id == first_embedding.id
                )
            )
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action)
                    .where(
                        AdminAuditEventModel.resource_id.in_(
                            [question.id, chunk.id, draft_chunk.id]
                        )
                    )
                    .order_by(AdminAuditEventModel.created_at, AdminAuditEventModel.id)
                )
            )

        await engine.dispose()
        assert question_count == 1
        assert chunk_count == 2
        assert len(configs) == 2
        assert len(embeddings) == 3
        assert dimensions == {3}
        assert self_distance == pytest.approx(0.0, abs=1e-6)
        assert sum(action == "knowledge.chunk.embedded" for action in actions) == 2
        assert sum(action == "knowledge.question.imported" for action in actions) == 1
        assert all(len(embedding.vector) == 3 for embedding in embeddings)

    asyncio.run(exercise())


@pytest.mark.integration
def test_concurrent_source_import_converges_on_one_question(
    knowledge_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(knowledge_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            seed = await seed_foundation(session, 4)
        question = question_record(seed, record_id=UUID(int=180_020))

        async def import_once(record_id: UUID) -> bool:
            async with sessions() as session:
                result = await KnowledgePersistenceService(session).import_question(
                    replace(question, id=record_id),
                    actor_id=ACTOR_ID,
                )
                return result.deduplicated

        deduplicated = await asyncio.gather(
            import_once(UUID(int=180_020)),
            import_once(UUID(int=180_021)),
        )
        async with sessions() as session:
            count = await session.scalar(
                select(func.count(HistoricalQuestionModel.id)).where(
                    HistoricalQuestionModel.source_document_id == seed.source_document_id
                )
            )
        await engine.dispose()

        assert sorted(deduplicated) == [False, True]
        assert count == 1

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_enforces_provenance_review_taxonomy_and_vector_space_invariants(
    knowledge_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = create_async_engine(knowledge_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            first = await seed_foundation(session, 2)
            second = await seed_foundation(session, 3)
            pending_document_id, pending_block_id = await seed_trusted_source(
                session,
                offset=5,
                curriculum_version_id=first.curriculum_version_id,
                document_type=SourceDocumentType.SYLLABUS,
                finalize_review=False,
            )
            await session.commit()
            service = KnowledgePersistenceService(session)
            untrusted_chunk = replace(
                chunk_record(first, record_id=UUID(int=190_012)),
                provenance=Provenance(
                    source_document_id=pending_document_id,
                    page_number=1,
                    source_block_id=pending_block_id,
                ),
            )
            with pytest.raises(TrustedKnowledgeSourceRequiredError):
                await service.import_chunk(untrusted_chunk, actor_id=ACTOR_ID)
            await session.rollback()

            versioned_draft = chunk_record(
                first,
                record_id=UUID(int=190_013),
                sequence=2,
            )
            await service.import_chunk(versioned_draft, actor_id=ACTOR_ID)
            draft_model = await session.get(KnowledgeChunkModel, versioned_draft.id)
            assert draft_model is not None
            draft_model.text = "Unversioned direct mutation"
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            nonzero_initial_version = replace(
                chunk_record(first, record_id=UUID(int=190_014), sequence=3),
                version=1,
            )
            session.add(KnowledgeChunkModel.from_domain(nonzero_initial_version, ACTOR_ID))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            reviewed = replace(
                question_record(first, record_id=UUID(int=180_010)),
                review_state=ReviewState.REVIEWED,
                competency_id=first.competency_id,
                skill_id=first.skill_id,
            )
            await service.import_question(reviewed, actor_id=ACTOR_ID)
            embedding = await service.store_question_embedding(
                reviewed.id,
                EmbeddingResult(
                    vector=(0.1, 0.2, 0.3),
                    config=EmbeddingConfig(
                        provider="guard-provider",
                        model="guard-model",
                        dimension=3,
                        version="v1",
                        config_fingerprint="guard-space-v1",
                    ),
                ),
                actor_id=ACTOR_ID,
            )

            model = await session.get(HistoricalQuestionModel, reviewed.id)
            assert model is not None
            model.source_document_id = second.source_document_id
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            model = await session.get(HistoricalQuestionModel, reviewed.id)
            assert model is not None
            model.review_state = ReviewState.DRAFT
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            cross_curriculum_chunk = replace(
                chunk_record(first, record_id=UUID(int=190_010)),
                review_state=ReviewState.REVIEWED,
                competency_id=second.competency_id,
            )
            session.add(KnowledgeChunkModel.from_domain(cross_curriculum_chunk, ACTOR_ID))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            wrong_block_chunk = replace(
                chunk_record(first, record_id=UUID(int=190_011)),
                provenance=Provenance(
                    source_document_id=first.source_document_id,
                    page_number=1,
                    source_block_id=second.source_block_id,
                ),
                review_state=ReviewState.REVIEWED,
                competency_id=first.competency_id,
            )
            session.add(KnowledgeChunkModel.from_domain(wrong_block_chunk, ACTOR_ID))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            session.add(
                KnowledgeEmbeddingModel(
                    id=UUID(int=200_010),
                    historical_question_id=reviewed.id,
                    knowledge_chunk_id=None,
                    embedding_configuration_id=embedding.configuration_id,
                    embedding_dimension=3,
                    source_text_sha256=sha256(reviewed.text.encode()).hexdigest(),
                    embedding=[0.1, 0.2],
                    created_by=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            configuration = await session.get(
                EmbeddingConfigurationModel,
                embedding.configuration_id,
            )
            assert configuration is not None
            session.add(
                EmbeddingConfigurationModel.from_domain(
                    UUID(int=200_011),
                    replace(configuration.to_domain(), dimension=4),
                    actor_id=ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            configuration = await session.get(
                EmbeddingConfigurationModel,
                embedding.configuration_id,
            )
            assert configuration is not None
            configuration.model = "mutated-model"
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            persisted_embedding = await session.get(KnowledgeEmbeddingModel, embedding.id)
            assert persisted_embedding is not None
            persisted_embedding.source_text_sha256 = "f" * 64
            with pytest.raises(IntegrityError):
                await session.commit()

        await engine.dispose()

    asyncio.run(exercise())
