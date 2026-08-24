import asyncio
from typing import cast
from uuid import UUID

import pytest
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    Provenance,
    QuestionType,
    ReviewState,
)
from exam_guru_api.knowledge.embeddings import (
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.knowledge.repository import (
    ConcurrentKnowledgeVersionError,
    EmbeddingSourceConflictError,
    EmbeddingSpaceConflictError,
    KnowledgeRecordNotFoundError,
    SqlAlchemyKnowledgeRepository,
)
from exam_guru_api.knowledge.service import KnowledgePersistenceService

ACTOR_ID = UUID(int=900)


class ScriptedScalarSession:
    def __init__(self, *responses: object | None) -> None:
        self.responses = list(responses)

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.responses.pop(0)


def test_question_and_chunk_models_round_trip_domain_records() -> None:
    provenance = Provenance(
        source_document_id=UUID(int=1),
        page_number=2,
        source_block_id=UUID(int=3),
    )
    question = HistoricalQuestion(
        id=UUID(int=10),
        curriculum_version_id=UUID(int=11),
        year=2020,
        paper_code="P1",
        question_number="1",
        text="Reviewed historical question",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=2,
        provenance=provenance,
        review_state=ReviewState.REVIEWED,
        competency_id=UUID(int=12),
        skill_id=UUID(int=13),
    )
    chunk = KnowledgeChunk(
        id=UUID(int=20),
        curriculum_version_id=UUID(int=11),
        chunk_type=ChunkType.EXPLANATION,
        text="Reviewed curriculum explanation",
        educational_boundary="Unit 1 / concept 2",
        sequence=0,
        provenance=provenance,
        review_state=ReviewState.REVIEWED,
        competency_id=UUID(int=12),
        skill_id=UUID(int=13),
    )

    question_model = HistoricalQuestionModel.from_domain(question, actor_id=ACTOR_ID)
    chunk_model = KnowledgeChunkModel.from_domain(chunk, actor_id=ACTOR_ID)

    assert question_model.to_domain() == question
    assert chunk_model.to_domain() == chunk
    assert question_model.created_by == question_model.updated_by == ACTOR_ID
    assert chunk_model.created_by == chunk_model.updated_by == ACTOR_ID
    assert question_model.version == chunk_model.version == 0
    assert HistoricalQuestionModel.__table__.c.version.server_default is not None
    assert KnowledgeChunkModel.__table__.c.version.server_default is not None


def test_embedding_configuration_round_trip_and_vector_column_use_pgvector() -> None:
    config = EmbeddingConfig(
        provider="fixture-provider",
        model="fixture-model",
        dimension=3,
        version="v1",
        config_fingerprint="fixture-config-v1",
    )

    model = EmbeddingConfigurationModel.from_domain(
        UUID(int=30),
        config,
        actor_id=ACTOR_ID,
    )

    assert model.to_domain() == config
    assert model.created_by == model.updated_by == ACTOR_ID
    vector_type = KnowledgeEmbeddingModel.__table__.c.embedding.type
    assert isinstance(vector_type, Vector)
    assert vector_type.dim is None


def test_repository_reports_missing_records_and_invalid_embedding_targets() -> None:
    async def exercise() -> None:
        repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, None))
        )
        with pytest.raises(KnowledgeRecordNotFoundError):
            await repository.get_question(UUID(int=100))
        with pytest.raises(KnowledgeRecordNotFoundError):
            await repository.get_chunk(UUID(int=101))

    asyncio.run(exercise())
    with pytest.raises(ValueError, match="exactly one knowledge target"):
        SqlAlchemyKnowledgeRepository._embedding_target(None, None)
    with pytest.raises(ValueError, match="exactly one knowledge target"):
        SqlAlchemyKnowledgeRepository._embedding_target(UUID(int=1), UUID(int=2))


def test_repository_atomic_updates_report_actual_version_after_cas_loss() -> None:
    async def exercise() -> None:
        question = HistoricalQuestion(
            id=UUID(int=110),
            curriculum_version_id=UUID(int=111),
            year=2021,
            paper_code="P1",
            question_number="1",
            text="Question",
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            provenance=Provenance(UUID(int=112), 1, UUID(int=113)),
            version=3,
        )
        chunk = KnowledgeChunk(
            id=UUID(int=120),
            curriculum_version_id=question.curriculum_version_id,
            chunk_type=ChunkType.EXPLANATION,
            text="Chunk",
            educational_boundary="Unit",
            sequence=0,
            provenance=question.provenance,
            version=3,
        )
        question_model = HistoricalQuestionModel.from_domain(question, ACTOR_ID)
        chunk_model = KnowledgeChunkModel.from_domain(chunk, ACTOR_ID)

        question_review_repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, question_model))
        )
        with pytest.raises(ConcurrentKnowledgeVersionError, match="found 3"):
            await question_review_repository.update_question_review(
                question.curriculum_version_id,
                question.id,
                ReviewState.IN_REVIEW,
                expected_version=2,
                actor_id=ACTOR_ID,
            )

        chunk_review_repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, chunk_model))
        )
        with pytest.raises(ConcurrentKnowledgeVersionError, match="found 3"):
            await chunk_review_repository.update_chunk_review(
                chunk.curriculum_version_id,
                chunk.id,
                ReviewState.IN_REVIEW,
                expected_version=2,
                actor_id=ACTOR_ID,
            )

        question_classification_repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, question_model))
        )
        with pytest.raises(ConcurrentKnowledgeVersionError, match="found 3"):
            await question_classification_repository.update_question_classification(
                question.curriculum_version_id,
                question.id,
                competency_id=None,
                skill_id=None,
                sub_skill_id=None,
                learning_concept_id=None,
                expected_version=2,
                actor_id=ACTOR_ID,
            )

        chunk_classification_repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, chunk_model))
        )
        with pytest.raises(ConcurrentKnowledgeVersionError, match="found 3"):
            await chunk_classification_repository.update_chunk_classification(
                chunk.curriculum_version_id,
                chunk.id,
                competency_id=None,
                skill_id=None,
                sub_skill_id=None,
                learning_concept_id=None,
                expected_version=2,
                actor_id=ACTOR_ID,
            )

    asyncio.run(exercise())


def test_repository_rejects_embedding_space_and_source_conflicts() -> None:
    async def exercise() -> None:
        config = EmbeddingConfig(
            provider="fixture-provider",
            model="fixture-model",
            dimension=3,
            version="v1",
            config_fingerprint="fixture-config-v1",
        )
        configuration = EmbeddingConfigurationModel.from_domain(
            UUID(int=200),
            config,
            actor_id=ACTOR_ID,
        )
        configuration_repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, None))
        )
        with pytest.raises(EmbeddingSpaceConflictError):
            await configuration_repository.get_or_create_embedding_configuration(
                config,
                actor_id=ACTOR_ID,
            )

        existing = KnowledgeEmbeddingModel(
            id=UUID(int=201),
            historical_question_id=UUID(int=202),
            knowledge_chunk_id=None,
            embedding_configuration_id=configuration.id,
            embedding_dimension=3,
            source_text_sha256="b" * 64,
            embedding=[0.1, 0.2, 0.3],
            created_by=ACTOR_ID,
        )
        embedding_repository = SqlAlchemyKnowledgeRepository(
            cast(AsyncSession, ScriptedScalarSession(None, existing))
        )
        with pytest.raises(EmbeddingSourceConflictError):
            await embedding_repository.store_embedding(
                historical_question_id=existing.historical_question_id,
                knowledge_chunk_id=None,
                config=configuration,
                source_text_sha256="a" * 64,
                vector=(0.1, 0.2, 0.3),
                actor_id=ACTOR_ID,
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "result",
    [
        EmbeddingResult(
            vector=(0.1,),
            config=EmbeddingConfig(" provider", "model", 1, "v1", "fingerprint"),
        ),
        EmbeddingResult(
            vector=(float("inf"),),
            config=EmbeddingConfig("provider", "model", 1, "v1", "fingerprint"),
        ),
    ],
)
def test_service_rejects_invalid_embedding_configuration_and_values(
    result: EmbeddingResult,
) -> None:
    service = KnowledgePersistenceService(cast(AsyncSession, object()))

    with pytest.raises(EmbeddingContractError):
        asyncio.run(service.store_chunk_embedding(UUID(int=300), result, actor_id=ACTOR_ID))
