import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.knowledge.domain import HistoricalQuestion, KnowledgeChunk, ReviewState
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)

_CONFIGURATION_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/embedding-configurations")
_EMBEDDING_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/knowledge-embeddings")


class SourceImportConflictError(RuntimeError):
    def __init__(self, record_type: str, source_document_id: UUID, source_key: str) -> None:
        self.record_type = record_type
        self.source_document_id = source_document_id
        self.source_key = source_key
        super().__init__(
            f"conflicting {record_type} import for source {source_document_id}/{source_key}"
        )


class KnowledgeRecordNotFoundError(LookupError):
    def __init__(self, record_type: str, record_id: UUID) -> None:
        self.record_type = record_type
        self.record_id = record_id
        super().__init__(f"{record_type} not found: {record_id}")


class EmbeddingSpaceConflictError(RuntimeError):
    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        super().__init__(
            "embedding provider/model/version/fingerprint already exists with another dimension"
        )


class EmbeddingSourceConflictError(RuntimeError):
    def __init__(self, record_id: UUID, configuration_id: UUID) -> None:
        self.record_id = record_id
        self.configuration_id = configuration_id
        super().__init__(
            f"embedding source changed for {record_id} in configuration {configuration_id}"
        )


@dataclass(frozen=True, slots=True)
class RepositoryImportResult[KnowledgeRecordT: (HistoricalQuestion, KnowledgeChunk)]:
    record: KnowledgeRecordT
    created: bool


@dataclass(frozen=True, slots=True)
class RepositoryEmbeddingResult:
    id: UUID
    configuration_id: UUID
    source_text_sha256: str
    vector: tuple[float, ...]
    created: bool


class SqlAlchemyKnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def import_question(
        self,
        question: HistoricalQuestion,
        *,
        actor_id: UUID,
    ) -> RepositoryImportResult[HistoricalQuestion]:
        values = self._question_values(question, actor_id)
        inserted_id = await self._session.scalar(
            insert(HistoricalQuestionModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_historical_questions_source_question")
            .returning(HistoricalQuestionModel.id)
        )
        if inserted_id is not None:
            return RepositoryImportResult(record=question, created=True)

        existing = await self._session.scalar(
            select(HistoricalQuestionModel).where(
                HistoricalQuestionModel.source_document_id
                == question.provenance.source_document_id,
                HistoricalQuestionModel.question_number == question.question_number,
            )
        )
        if existing is None or not self._same_question_import(existing, question):
            raise SourceImportConflictError(
                "historical_question",
                question.provenance.source_document_id,
                question.question_number,
            )
        return RepositoryImportResult(record=existing.to_domain(), created=False)

    async def import_chunk(
        self,
        chunk: KnowledgeChunk,
        *,
        actor_id: UUID,
    ) -> RepositoryImportResult[KnowledgeChunk]:
        values = self._chunk_values(chunk, actor_id)
        inserted_id = await self._session.scalar(
            insert(KnowledgeChunkModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_knowledge_chunks_source_sequence")
            .returning(KnowledgeChunkModel.id)
        )
        if inserted_id is not None:
            return RepositoryImportResult(record=chunk, created=True)

        existing = await self._session.scalar(
            select(KnowledgeChunkModel).where(
                KnowledgeChunkModel.source_document_id == chunk.provenance.source_document_id,
                KnowledgeChunkModel.sequence == chunk.sequence,
            )
        )
        if existing is None or not self._same_chunk_import(existing, chunk):
            raise SourceImportConflictError(
                "knowledge_chunk",
                chunk.provenance.source_document_id,
                str(chunk.sequence),
            )
        return RepositoryImportResult(record=existing.to_domain(), created=False)

    async def get_question(
        self,
        question_id: UUID,
        *,
        for_update: bool = False,
    ) -> HistoricalQuestionModel:
        statement = select(HistoricalQuestionModel).where(HistoricalQuestionModel.id == question_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        if model is None:
            raise KnowledgeRecordNotFoundError("historical_question", question_id)
        return model

    async def get_chunk(
        self,
        chunk_id: UUID,
        *,
        for_update: bool = False,
    ) -> KnowledgeChunkModel:
        statement = select(KnowledgeChunkModel).where(KnowledgeChunkModel.id == chunk_id)
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        if model is None:
            raise KnowledgeRecordNotFoundError("knowledge_chunk", chunk_id)
        return model

    async def update_question_review(
        self,
        model: HistoricalQuestionModel,
        target: ReviewState,
        *,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        model.review_state = target
        model.updated_by = actor_id
        await self._session.flush()
        return model.to_domain()

    async def update_chunk_review(
        self,
        model: KnowledgeChunkModel,
        target: ReviewState,
        *,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        model.review_state = target
        model.updated_by = actor_id
        await self._session.flush()
        return model.to_domain()

    async def update_question_classification(
        self,
        model: HistoricalQuestionModel,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        self._set_classification(
            model,
            competency_id=competency_id,
            skill_id=skill_id,
            sub_skill_id=sub_skill_id,
            learning_concept_id=learning_concept_id,
            actor_id=actor_id,
        )
        await self._session.flush()
        return model.to_domain()

    async def update_chunk_classification(
        self,
        model: KnowledgeChunkModel,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        self._set_classification(
            model,
            competency_id=competency_id,
            skill_id=skill_id,
            sub_skill_id=sub_skill_id,
            learning_concept_id=learning_concept_id,
            actor_id=actor_id,
        )
        await self._session.flush()
        return model.to_domain()

    async def get_or_create_embedding_configuration(
        self,
        config: EmbeddingConfig,
        *,
        actor_id: UUID,
    ) -> tuple[EmbeddingConfigurationModel, bool]:
        configuration_id = self.configuration_id(config)
        inserted_id = await self._session.scalar(
            insert(EmbeddingConfigurationModel)
            .values(
                id=configuration_id,
                provider=config.provider,
                model=config.model,
                dimension=config.dimension,
                version=config.version,
                config_fingerprint=config.config_fingerprint,
                created_by=actor_id,
                updated_by=actor_id,
            )
            .on_conflict_do_nothing(constraint="uq_embedding_configurations_space")
            .returning(EmbeddingConfigurationModel.id)
        )
        if inserted_id is not None:
            model = EmbeddingConfigurationModel.from_domain(configuration_id, config, actor_id)
            return model, True

        existing = await self._session.scalar(
            select(EmbeddingConfigurationModel).where(
                EmbeddingConfigurationModel.provider == config.provider,
                EmbeddingConfigurationModel.model == config.model,
                EmbeddingConfigurationModel.version == config.version,
                EmbeddingConfigurationModel.config_fingerprint == config.config_fingerprint,
            )
        )
        if existing is None or existing.to_domain() != config:
            raise EmbeddingSpaceConflictError(config)
        return existing, False

    async def store_embedding(
        self,
        *,
        historical_question_id: UUID | None,
        knowledge_chunk_id: UUID | None,
        config: EmbeddingConfigurationModel,
        source_text_sha256: str,
        vector: tuple[float, ...],
        actor_id: UUID,
    ) -> RepositoryEmbeddingResult:
        target_type, target_id = self._embedding_target(
            historical_question_id,
            knowledge_chunk_id,
        )
        embedding_id = uuid5(
            _EMBEDDING_NAMESPACE,
            f"{target_type}\0{target_id}\0{config.id}\0{source_text_sha256}",
        )
        statement = insert(KnowledgeEmbeddingModel).values(
            id=embedding_id,
            historical_question_id=historical_question_id,
            knowledge_chunk_id=knowledge_chunk_id,
            embedding_configuration_id=config.id,
            embedding_dimension=config.dimension,
            source_text_sha256=source_text_sha256,
            embedding=vector,
            created_by=actor_id,
        )
        if historical_question_id is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=[
                    KnowledgeEmbeddingModel.historical_question_id,
                    KnowledgeEmbeddingModel.embedding_configuration_id,
                ],
                index_where=KnowledgeEmbeddingModel.historical_question_id.is_not(None),
            )
            target_clause = KnowledgeEmbeddingModel.historical_question_id == historical_question_id
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=[
                    KnowledgeEmbeddingModel.knowledge_chunk_id,
                    KnowledgeEmbeddingModel.embedding_configuration_id,
                ],
                index_where=KnowledgeEmbeddingModel.knowledge_chunk_id.is_not(None),
            )
            target_clause = KnowledgeEmbeddingModel.knowledge_chunk_id == knowledge_chunk_id

        inserted_id = await self._session.scalar(statement.returning(KnowledgeEmbeddingModel.id))
        if inserted_id is not None:
            return RepositoryEmbeddingResult(
                id=embedding_id,
                configuration_id=config.id,
                source_text_sha256=source_text_sha256,
                vector=vector,
                created=True,
            )

        existing = await self._session.scalar(
            select(KnowledgeEmbeddingModel).where(
                target_clause,
                KnowledgeEmbeddingModel.embedding_configuration_id == config.id,
            )
        )
        if existing is None or existing.source_text_sha256 != source_text_sha256:
            raise EmbeddingSourceConflictError(target_id, config.id)
        return RepositoryEmbeddingResult(
            id=existing.id,
            configuration_id=config.id,
            source_text_sha256=existing.source_text_sha256,
            vector=existing.vector,
            created=False,
        )

    @staticmethod
    def configuration_id(config: EmbeddingConfig) -> UUID:
        identity = json.dumps(
            {
                "config_fingerprint": config.config_fingerprint,
                "dimension": config.dimension,
                "model": config.model,
                "provider": config.provider,
                "version": config.version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return uuid5(_CONFIGURATION_NAMESPACE, identity)

    @staticmethod
    def _embedding_target(
        historical_question_id: UUID | None,
        knowledge_chunk_id: UUID | None,
    ) -> tuple[str, UUID]:
        if historical_question_id is None:
            if knowledge_chunk_id is None:
                raise ValueError("an embedding must have exactly one knowledge target")
            return "knowledge_chunk", knowledge_chunk_id
        if knowledge_chunk_id is not None:
            raise ValueError("an embedding must have exactly one knowledge target")
        return "historical_question", historical_question_id

    @staticmethod
    def _question_values(question: HistoricalQuestion, actor_id: UUID) -> dict[str, object]:
        return {
            "id": question.id,
            "curriculum_version_id": question.curriculum_version_id,
            "year": question.year,
            "paper_code": question.paper_code,
            "question_number": question.question_number,
            "text": question.text,
            "question_type": question.question_type,
            "marks": question.marks,
            "source_document_id": question.provenance.source_document_id,
            "page_number": question.provenance.page_number,
            "source_block_id": question.provenance.source_block_id,
            "review_state": question.review_state,
            "competency_id": question.competency_id,
            "skill_id": question.skill_id,
            "sub_skill_id": question.sub_skill_id,
            "learning_concept_id": question.learning_concept_id,
            "created_by": actor_id,
            "updated_by": actor_id,
        }

    @staticmethod
    def _chunk_values(chunk: KnowledgeChunk, actor_id: UUID) -> dict[str, object]:
        return {
            "id": chunk.id,
            "curriculum_version_id": chunk.curriculum_version_id,
            "chunk_type": chunk.chunk_type,
            "text": chunk.text,
            "educational_boundary": chunk.educational_boundary,
            "sequence": chunk.sequence,
            "source_document_id": chunk.provenance.source_document_id,
            "page_number": chunk.provenance.page_number,
            "source_block_id": chunk.provenance.source_block_id,
            "review_state": chunk.review_state,
            "competency_id": chunk.competency_id,
            "skill_id": chunk.skill_id,
            "sub_skill_id": chunk.sub_skill_id,
            "learning_concept_id": chunk.learning_concept_id,
            "created_by": actor_id,
            "updated_by": actor_id,
        }

    @staticmethod
    def _same_question_import(
        existing: HistoricalQuestionModel,
        candidate: HistoricalQuestion,
    ) -> bool:
        persisted = existing.to_domain()
        return (
            persisted.curriculum_version_id,
            persisted.year,
            persisted.paper_code,
            persisted.question_number,
            persisted.text,
            persisted.question_type,
            persisted.marks,
            persisted.provenance,
        ) == (
            candidate.curriculum_version_id,
            candidate.year,
            candidate.paper_code,
            candidate.question_number,
            candidate.text,
            candidate.question_type,
            candidate.marks,
            candidate.provenance,
        )

    @staticmethod
    def _same_chunk_import(existing: KnowledgeChunkModel, candidate: KnowledgeChunk) -> bool:
        persisted = existing.to_domain()
        return (
            persisted.curriculum_version_id,
            persisted.chunk_type,
            persisted.text,
            persisted.educational_boundary,
            persisted.sequence,
            persisted.provenance,
        ) == (
            candidate.curriculum_version_id,
            candidate.chunk_type,
            candidate.text,
            candidate.educational_boundary,
            candidate.sequence,
            candidate.provenance,
        )

    @staticmethod
    def _set_classification(
        model: HistoricalQuestionModel | KnowledgeChunkModel,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        actor_id: UUID,
    ) -> None:
        model.competency_id = competency_id
        model.skill_id = skill_id
        model.sub_skill_id = sub_skill_id
        model.learning_concept_id = learning_concept_id
        model.updated_by = actor_id
