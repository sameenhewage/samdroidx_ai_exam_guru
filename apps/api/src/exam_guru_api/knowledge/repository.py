import json
from dataclasses import dataclass, replace
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from exam_guru_api.knowledge.domain import (
    ChunkType,
    EmbeddingConfigurationMetadata,
    HistoricalQuestion,
    KnowledgeChunk,
    QuestionType,
    ReviewState,
    marking_data_to_dict,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)

_CONFIGURATION_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/embedding-configurations")
_EMBEDDING_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/knowledge-embeddings")


def _equals_if_provided[ValueT](
    column: InstrumentedAttribute[ValueT],
    value: ValueT | None,
) -> ColumnElement[bool]:
    if value is None:
        return true()
    return column == value


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


class ConcurrentKnowledgeVersionError(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected knowledge version {expected}, found {actual}")


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
        inserted = await self._session.scalar(
            insert(HistoricalQuestionModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_historical_questions_source_question")
            .returning(HistoricalQuestionModel)
        )
        if inserted is not None:
            record = (await self._questions_with_embedding_metadata((inserted,)))[0]
            return RepositoryImportResult(record=record, created=True)

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
        record = (await self._questions_with_embedding_metadata((existing,)))[0]
        return RepositoryImportResult(record=record, created=False)

    async def import_chunk(
        self,
        chunk: KnowledgeChunk,
        *,
        actor_id: UUID,
    ) -> RepositoryImportResult[KnowledgeChunk]:
        values = self._chunk_values(chunk, actor_id)
        inserted = await self._session.scalar(
            insert(KnowledgeChunkModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_knowledge_chunks_source_sequence")
            .returning(KnowledgeChunkModel)
        )
        if inserted is not None:
            record = (await self._chunks_with_embedding_metadata((inserted,)))[0]
            return RepositoryImportResult(record=record, created=True)

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
        record = (await self._chunks_with_embedding_metadata((existing,)))[0]
        return RepositoryImportResult(record=record, created=False)

    async def get_question(
        self,
        question_id: UUID,
        *,
        curriculum_version_id: UUID | None = None,
        for_update: bool = False,
    ) -> HistoricalQuestionModel:
        statement = select(HistoricalQuestionModel).where(
            HistoricalQuestionModel.id == question_id,
            _equals_if_provided(
                HistoricalQuestionModel.curriculum_version_id,
                curriculum_version_id,
            ),
        )
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
        curriculum_version_id: UUID | None = None,
        for_update: bool = False,
    ) -> KnowledgeChunkModel:
        statement = select(KnowledgeChunkModel).where(
            KnowledgeChunkModel.id == chunk_id,
            _equals_if_provided(
                KnowledgeChunkModel.curriculum_version_id,
                curriculum_version_id,
            ),
        )
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement)
        if model is None:
            raise KnowledgeRecordNotFoundError("knowledge_chunk", chunk_id)
        return model

    async def get_question_record(
        self,
        curriculum_version_id: UUID,
        question_id: UUID,
    ) -> HistoricalQuestion:
        model = await self.get_question(
            question_id,
            curriculum_version_id=curriculum_version_id,
        )
        return (await self._questions_with_embedding_metadata((model,)))[0]

    async def get_chunk_record(
        self,
        curriculum_version_id: UUID,
        chunk_id: UUID,
    ) -> KnowledgeChunk:
        model = await self.get_chunk(
            chunk_id,
            curriculum_version_id=curriculum_version_id,
        )
        return (await self._chunks_with_embedding_metadata((model,)))[0]

    async def list_questions(
        self,
        curriculum_version_id: UUID,
        *,
        review_state: ReviewState | None,
        source_document_id: UUID | None,
        competency_id: UUID | None,
        question_type: QuestionType | None,
        year: int | None,
        paper_code: str | None,
        limit: int,
        offset: int,
    ) -> tuple[HistoricalQuestion, ...]:
        models = tuple(
            await self._session.scalars(
                select(HistoricalQuestionModel)
                .where(
                    HistoricalQuestionModel.curriculum_version_id == curriculum_version_id,
                    _equals_if_provided(
                        HistoricalQuestionModel.review_state,
                        review_state,
                    ),
                    _equals_if_provided(
                        HistoricalQuestionModel.source_document_id,
                        source_document_id,
                    ),
                    _equals_if_provided(
                        HistoricalQuestionModel.competency_id,
                        competency_id,
                    ),
                    _equals_if_provided(
                        HistoricalQuestionModel.question_type,
                        question_type,
                    ),
                    _equals_if_provided(HistoricalQuestionModel.year, year),
                    _equals_if_provided(HistoricalQuestionModel.paper_code, paper_code),
                )
                .order_by(
                    HistoricalQuestionModel.year.desc(),
                    HistoricalQuestionModel.paper_code,
                    HistoricalQuestionModel.question_number,
                    HistoricalQuestionModel.id,
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return await self._questions_with_embedding_metadata(models)

    async def list_chunks(
        self,
        curriculum_version_id: UUID,
        *,
        review_state: ReviewState | None,
        source_document_id: UUID | None,
        competency_id: UUID | None,
        chunk_type: ChunkType | None,
        limit: int,
        offset: int,
    ) -> tuple[KnowledgeChunk, ...]:
        models = tuple(
            await self._session.scalars(
                select(KnowledgeChunkModel)
                .where(
                    KnowledgeChunkModel.curriculum_version_id == curriculum_version_id,
                    _equals_if_provided(KnowledgeChunkModel.review_state, review_state),
                    _equals_if_provided(
                        KnowledgeChunkModel.source_document_id,
                        source_document_id,
                    ),
                    _equals_if_provided(
                        KnowledgeChunkModel.competency_id,
                        competency_id,
                    ),
                    _equals_if_provided(KnowledgeChunkModel.chunk_type, chunk_type),
                )
                .order_by(
                    KnowledgeChunkModel.source_document_id,
                    KnowledgeChunkModel.sequence,
                    KnowledgeChunkModel.id,
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return await self._chunks_with_embedding_metadata(models)

    async def update_question_review(
        self,
        curriculum_version_id: UUID,
        question_id: UUID,
        target: ReviewState,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        model = await self._session.scalar(
            update(HistoricalQuestionModel)
            .where(
                HistoricalQuestionModel.id == question_id,
                HistoricalQuestionModel.curriculum_version_id == curriculum_version_id,
                HistoricalQuestionModel.version == expected_version,
            )
            .values(
                review_state=target,
                version=HistoricalQuestionModel.version + 1,
                updated_at=func.now(),
                updated_by=actor_id,
            )
            .returning(HistoricalQuestionModel)
        )
        if model is None:
            current = await self.get_question(
                question_id,
                curriculum_version_id=curriculum_version_id,
            )
            raise ConcurrentKnowledgeVersionError(expected_version, current.version)
        return model.to_domain()

    async def update_chunk_review(
        self,
        curriculum_version_id: UUID,
        chunk_id: UUID,
        target: ReviewState,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        model = await self._session.scalar(
            update(KnowledgeChunkModel)
            .where(
                KnowledgeChunkModel.id == chunk_id,
                KnowledgeChunkModel.curriculum_version_id == curriculum_version_id,
                KnowledgeChunkModel.version == expected_version,
            )
            .values(
                review_state=target,
                version=KnowledgeChunkModel.version + 1,
                updated_at=func.now(),
                updated_by=actor_id,
            )
            .returning(KnowledgeChunkModel)
        )
        if model is None:
            current = await self.get_chunk(
                chunk_id,
                curriculum_version_id=curriculum_version_id,
            )
            raise ConcurrentKnowledgeVersionError(expected_version, current.version)
        return model.to_domain()

    async def update_question_classification(
        self,
        curriculum_version_id: UUID,
        question_id: UUID,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        expected_version: int,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        model = await self._session.scalar(
            update(HistoricalQuestionModel)
            .where(
                HistoricalQuestionModel.id == question_id,
                HistoricalQuestionModel.curriculum_version_id == curriculum_version_id,
                HistoricalQuestionModel.version == expected_version,
            )
            .values(
                competency_id=competency_id,
                skill_id=skill_id,
                sub_skill_id=sub_skill_id,
                learning_concept_id=learning_concept_id,
                version=HistoricalQuestionModel.version + 1,
                updated_at=func.now(),
                updated_by=actor_id,
            )
            .returning(HistoricalQuestionModel)
        )
        if model is None:
            current = await self.get_question(
                question_id,
                curriculum_version_id=curriculum_version_id,
            )
            raise ConcurrentKnowledgeVersionError(expected_version, current.version)
        return model.to_domain()

    async def update_chunk_classification(
        self,
        curriculum_version_id: UUID,
        chunk_id: UUID,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        expected_version: int,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        model = await self._session.scalar(
            update(KnowledgeChunkModel)
            .where(
                KnowledgeChunkModel.id == chunk_id,
                KnowledgeChunkModel.curriculum_version_id == curriculum_version_id,
                KnowledgeChunkModel.version == expected_version,
            )
            .values(
                competency_id=competency_id,
                skill_id=skill_id,
                sub_skill_id=sub_skill_id,
                learning_concept_id=learning_concept_id,
                version=KnowledgeChunkModel.version + 1,
                updated_at=func.now(),
                updated_by=actor_id,
            )
            .returning(KnowledgeChunkModel)
        )
        if model is None:
            current = await self.get_chunk(
                chunk_id,
                curriculum_version_id=curriculum_version_id,
            )
            raise ConcurrentKnowledgeVersionError(expected_version, current.version)
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

    async def find_embedding(
        self,
        *,
        historical_question_id: UUID | None,
        knowledge_chunk_id: UUID | None,
        config: EmbeddingConfig,
    ) -> KnowledgeEmbeddingModel | None:
        _, target_id = self._embedding_target(historical_question_id, knowledge_chunk_id)
        configuration = await self._session.scalar(
            select(EmbeddingConfigurationModel).where(
                EmbeddingConfigurationModel.provider == config.provider,
                EmbeddingConfigurationModel.model == config.model,
                EmbeddingConfigurationModel.version == config.version,
                EmbeddingConfigurationModel.config_fingerprint == config.config_fingerprint,
            )
        )
        if configuration is None:
            return None
        if configuration.to_domain() != config:
            raise EmbeddingSpaceConflictError(config)
        target_clause = (
            KnowledgeEmbeddingModel.historical_question_id == target_id
            if historical_question_id is not None
            else KnowledgeEmbeddingModel.knowledge_chunk_id == target_id
        )
        model = await self._session.scalar(
            select(KnowledgeEmbeddingModel).where(
                target_clause,
                KnowledgeEmbeddingModel.embedding_configuration_id == configuration.id,
            )
        )
        return model if isinstance(model, KnowledgeEmbeddingModel) else None

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

    async def _questions_with_embedding_metadata(
        self,
        models: tuple[HistoricalQuestionModel, ...],
    ) -> tuple[HistoricalQuestion, ...]:
        configurations: dict[UUID, list[EmbeddingConfigurationMetadata]] = {
            model.id: [] for model in models
        }
        rows = (
            await self._session.execute(
                select(
                    KnowledgeEmbeddingModel.historical_question_id,
                    EmbeddingConfigurationModel,
                )
                .join(
                    EmbeddingConfigurationModel,
                    KnowledgeEmbeddingModel.embedding_configuration_id
                    == EmbeddingConfigurationModel.id,
                )
                .where(KnowledgeEmbeddingModel.historical_question_id.in_(tuple(configurations)))
                .order_by(
                    KnowledgeEmbeddingModel.historical_question_id,
                    EmbeddingConfigurationModel.provider,
                    EmbeddingConfigurationModel.model,
                    EmbeddingConfigurationModel.version,
                    EmbeddingConfigurationModel.id,
                )
            )
        ).all()
        for question_id, configuration in rows:
            configurations[cast(UUID, question_id)].append(
                self._configuration_metadata(configuration)
            )
        return tuple(
            replace(
                model.to_domain(),
                embedding_configurations=tuple(configurations[model.id]),
            )
            for model in models
        )

    async def _chunks_with_embedding_metadata(
        self,
        models: tuple[KnowledgeChunkModel, ...],
    ) -> tuple[KnowledgeChunk, ...]:
        configurations: dict[UUID, list[EmbeddingConfigurationMetadata]] = {
            model.id: [] for model in models
        }
        rows = (
            await self._session.execute(
                select(
                    KnowledgeEmbeddingModel.knowledge_chunk_id,
                    EmbeddingConfigurationModel,
                )
                .join(
                    EmbeddingConfigurationModel,
                    KnowledgeEmbeddingModel.embedding_configuration_id
                    == EmbeddingConfigurationModel.id,
                )
                .where(KnowledgeEmbeddingModel.knowledge_chunk_id.in_(tuple(configurations)))
                .order_by(
                    KnowledgeEmbeddingModel.knowledge_chunk_id,
                    EmbeddingConfigurationModel.provider,
                    EmbeddingConfigurationModel.model,
                    EmbeddingConfigurationModel.version,
                    EmbeddingConfigurationModel.id,
                )
            )
        ).all()
        for chunk_id, configuration in rows:
            configurations[cast(UUID, chunk_id)].append(self._configuration_metadata(configuration))
        return tuple(
            replace(
                model.to_domain(),
                embedding_configurations=tuple(configurations[model.id]),
            )
            for model in models
        )

    @staticmethod
    def _configuration_metadata(
        model: EmbeddingConfigurationModel,
    ) -> EmbeddingConfigurationMetadata:
        return EmbeddingConfigurationMetadata(
            id=model.id,
            provider=model.provider,
            model=model.model,
            dimension=model.dimension,
            version=model.version,
            config_fingerprint=model.config_fingerprint,
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
            "unit_id": question.unit_id,
            "lesson_id": question.lesson_id,
            "year": question.year,
            "paper_code": question.paper_code,
            "question_number": question.question_number,
            "text": question.text,
            "question_type": question.question_type,
            "marks": question.marks,
            "media_references": (
                list(question.media_references) if question.media_references is not None else None
            ),
            "options": list(question.options) if question.options is not None else None,
            "answer": question.answer,
            "marking_guidance": question.marking_guidance,
            "marking_data": marking_data_to_dict(question.marking_data),
            "question_archetype": question.question_archetype,
            "difficulty_label": question.difficulty_label,
            "difficulty_confidence": question.difficulty_confidence,
            "difficulty_source": question.difficulty_source,
            "source_document_id": question.provenance.source_document_id,
            "page_number": question.provenance.page_number,
            "source_block_id": question.provenance.source_block_id,
            "review_state": question.review_state,
            "competency_id": question.competency_id,
            "skill_id": question.skill_id,
            "sub_skill_id": question.sub_skill_id,
            "learning_concept_id": question.learning_concept_id,
            "version": question.version,
            "created_by": actor_id,
            "updated_by": actor_id,
        }

    @staticmethod
    def _chunk_values(chunk: KnowledgeChunk, actor_id: UUID) -> dict[str, object]:
        return {
            "id": chunk.id,
            "curriculum_version_id": chunk.curriculum_version_id,
            "unit_id": chunk.unit_id,
            "lesson_id": chunk.lesson_id,
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
            "version": chunk.version,
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
            persisted.unit_id,
            persisted.lesson_id,
            persisted.year,
            persisted.paper_code,
            persisted.question_number,
            persisted.text,
            persisted.question_type,
            persisted.marks,
            persisted.media_references,
            persisted.options,
            persisted.answer,
            persisted.marking_guidance,
            persisted.marking_data,
            persisted.question_archetype,
            persisted.difficulty_label,
            persisted.difficulty_confidence,
            persisted.difficulty_source,
            persisted.provenance,
        ) == (
            candidate.curriculum_version_id,
            candidate.unit_id,
            candidate.lesson_id,
            candidate.year,
            candidate.paper_code,
            candidate.question_number,
            candidate.text,
            candidate.question_type,
            candidate.marks,
            candidate.media_references,
            candidate.options,
            candidate.answer,
            candidate.marking_guidance,
            candidate.marking_data,
            candidate.question_archetype,
            candidate.difficulty_label,
            candidate.difficulty_confidence,
            candidate.difficulty_source,
            candidate.provenance,
        )

    @staticmethod
    def _same_chunk_import(existing: KnowledgeChunkModel, candidate: KnowledgeChunk) -> bool:
        persisted = existing.to_domain()
        return (
            persisted.curriculum_version_id,
            persisted.unit_id,
            persisted.lesson_id,
            persisted.chunk_type,
            persisted.text,
            persisted.educational_boundary,
            persisted.sequence,
            persisted.provenance,
        ) == (
            candidate.curriculum_version_id,
            candidate.unit_id,
            candidate.lesson_id,
            candidate.chunk_type,
            candidate.text,
            candidate.educational_boundary,
            candidate.sequence,
            candidate.provenance,
        )
