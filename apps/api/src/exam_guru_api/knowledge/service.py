import hashlib
import math
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    KnowledgeContractError,
    QuestionType,
    ReviewState,
    transition_review_state,
)
from exam_guru_api.knowledge.embeddings import (
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.knowledge.repository import (
    ConcurrentKnowledgeVersionError,
    SqlAlchemyKnowledgeRepository,
)


class FinalKnowledgeRecordError(RuntimeError):
    def __init__(self, record_id: UUID, state: ReviewState) -> None:
        self.record_id = record_id
        self.state = state
        super().__init__(f"cannot change classification of {state.value} record {record_id}")


class KnowledgeCurriculumNotFoundError(LookupError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(f"curriculum version not found: {curriculum_version_id}")


class KnowledgeSourceDocumentNotFoundError(LookupError):
    def __init__(self, source_document_id: UUID) -> None:
        self.source_document_id = source_document_id
        super().__init__(f"source document not found: {source_document_id}")


class KnowledgeSourceCurriculumMismatchError(ValueError):
    def __init__(self, source_document_id: UUID, curriculum_version_id: UUID) -> None:
        self.source_document_id = source_document_id
        self.curriculum_version_id = curriculum_version_id
        super().__init__(
            f"source document {source_document_id} does not belong to {curriculum_version_id}"
        )


class TrustedKnowledgeSourceRequiredError(ValueError):
    def __init__(self, source_document_id: UUID) -> None:
        self.source_document_id = source_document_id
        super().__init__(f"trusted source document required: {source_document_id}")


class KnowledgeSourceMetadataMismatchError(ValueError):
    def __init__(self, source_document_id: UUID) -> None:
        self.source_document_id = source_document_id
        super().__init__(f"historical question metadata does not match {source_document_id}")


class KnowledgeRecordNotReadyError(ValueError):
    def __init__(self, record_id: UUID) -> None:
        self.record_id = record_id
        super().__init__(f"knowledge record is not ready for review: {record_id}")


class EmbeddingDimensionMismatchError(ValueError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected embedding dimension {expected}, found {actual}")


class EmbeddingRequiresReviewedRecordError(RuntimeError):
    def __init__(self, record_id: UUID, state: ReviewState) -> None:
        self.record_id = record_id
        self.state = state
        super().__init__(f"embedding requires reviewed record {record_id}, found {state.value}")


@dataclass(frozen=True, slots=True)
class SourceImportResult[KnowledgeRecordT: (HistoricalQuestion, KnowledgeChunk)]:
    record: KnowledgeRecordT
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class StoredEmbedding:
    id: UUID
    configuration_id: UUID
    config: EmbeddingConfig
    source_text_sha256: str
    vector: tuple[float, ...]
    deduplicated: bool


class KnowledgePersistenceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SqlAlchemyKnowledgeRepository(session)

    async def import_question(
        self,
        question: HistoricalQuestion,
        *,
        actor_id: UUID,
    ) -> SourceImportResult[HistoricalQuestion]:
        await self._ensure_curriculum_exists(question.curriculum_version_id)
        await self._validate_source(question)
        result = await self._repository.import_question(question, actor_id=actor_id)
        if result.created:
            self._audit(
                action="knowledge.question.imported",
                actor_id=actor_id,
                resource_type="historical_question",
                resource_id=result.record.id,
                payload={
                    "curriculum_version_id": str(result.record.curriculum_version_id),
                    "source_document_id": str(result.record.provenance.source_document_id),
                    "page_number": result.record.provenance.page_number,
                    "source_block_id": self._optional_uuid(
                        result.record.provenance.source_block_id
                    ),
                    "question_number": result.record.question_number,
                    "historical_metadata": self._historical_metadata_audit_payload(result.record),
                    "version": result.record.version,
                },
            )
            await self._session.commit()
        return SourceImportResult(record=result.record, deduplicated=not result.created)

    async def import_chunk(
        self,
        chunk: KnowledgeChunk,
        *,
        actor_id: UUID,
    ) -> SourceImportResult[KnowledgeChunk]:
        await self._ensure_curriculum_exists(chunk.curriculum_version_id)
        await self._validate_source(chunk)
        result = await self._repository.import_chunk(chunk, actor_id=actor_id)
        if result.created:
            self._audit(
                action="knowledge.chunk.imported",
                actor_id=actor_id,
                resource_type="knowledge_chunk",
                resource_id=result.record.id,
                payload={
                    "curriculum_version_id": str(result.record.curriculum_version_id),
                    "source_document_id": str(result.record.provenance.source_document_id),
                    "page_number": result.record.provenance.page_number,
                    "source_block_id": self._optional_uuid(
                        result.record.provenance.source_block_id
                    ),
                    "sequence": result.record.sequence,
                    "version": result.record.version,
                },
            )
            await self._session.commit()
        return SourceImportResult(record=result.record, deduplicated=not result.created)

    async def list_questions(
        self,
        curriculum_version_id: UUID,
        *,
        review_state: ReviewState | None = None,
        source_document_id: UUID | None = None,
        competency_id: UUID | None = None,
        question_type: QuestionType | None = None,
        year: int | None = None,
        paper_code: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[HistoricalQuestion, ...]:
        await self._ensure_curriculum_exists(curriculum_version_id)
        return await self._repository.list_questions(
            curriculum_version_id,
            review_state=review_state,
            source_document_id=source_document_id,
            competency_id=competency_id,
            question_type=question_type,
            year=year,
            paper_code=paper_code,
            limit=limit,
            offset=offset,
        )

    async def list_chunks(
        self,
        curriculum_version_id: UUID,
        *,
        review_state: ReviewState | None = None,
        source_document_id: UUID | None = None,
        competency_id: UUID | None = None,
        chunk_type: ChunkType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[KnowledgeChunk, ...]:
        await self._ensure_curriculum_exists(curriculum_version_id)
        return await self._repository.list_chunks(
            curriculum_version_id,
            review_state=review_state,
            source_document_id=source_document_id,
            competency_id=competency_id,
            chunk_type=chunk_type,
            limit=limit,
            offset=offset,
        )

    async def get_question(
        self,
        curriculum_version_id: UUID,
        question_id: UUID,
    ) -> HistoricalQuestion:
        await self._ensure_curriculum_exists(curriculum_version_id)
        return await self._repository.get_question_record(curriculum_version_id, question_id)

    async def get_chunk(
        self,
        curriculum_version_id: UUID,
        chunk_id: UUID,
    ) -> KnowledgeChunk:
        await self._ensure_curriculum_exists(curriculum_version_id)
        return await self._repository.get_chunk_record(curriculum_version_id, chunk_id)

    async def classify_question(
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
        current = await self.get_question(curriculum_version_id, question_id)
        self._require_expected_version(current, expected_version)
        classification = (competency_id, skill_id, sub_skill_id, learning_concept_id)
        if self._classification(current) == classification:
            return current
        self._ensure_classification_mutable(current)
        updated = await self._repository.update_question_classification(
            curriculum_version_id,
            question_id,
            competency_id=competency_id,
            skill_id=skill_id,
            sub_skill_id=sub_skill_id,
            learning_concept_id=learning_concept_id,
            expected_version=expected_version,
            actor_id=actor_id,
        )
        enriched = await self._repository.get_question_record(curriculum_version_id, updated.id)
        self._audit_classification(current, enriched, actor_id=actor_id)
        await self._session.commit()
        return enriched

    async def classify_chunk(
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
        current = await self.get_chunk(curriculum_version_id, chunk_id)
        self._require_expected_version(current, expected_version)
        classification = (competency_id, skill_id, sub_skill_id, learning_concept_id)
        if self._classification(current) == classification:
            return current
        self._ensure_classification_mutable(current)
        updated = await self._repository.update_chunk_classification(
            curriculum_version_id,
            chunk_id,
            competency_id=competency_id,
            skill_id=skill_id,
            sub_skill_id=sub_skill_id,
            learning_concept_id=learning_concept_id,
            expected_version=expected_version,
            actor_id=actor_id,
        )
        enriched = await self._repository.get_chunk_record(curriculum_version_id, updated.id)
        self._audit_classification(current, enriched, actor_id=actor_id)
        await self._session.commit()
        return enriched

    async def transition_question_review(
        self,
        curriculum_version_id: UUID,
        question_id: UUID,
        target: ReviewState,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        current = await self.get_question(curriculum_version_id, question_id)
        self._require_expected_version(current, expected_version)
        transitioned = transition_review_state(current.review_state, target)
        if transitioned is current.review_state:
            return current
        self._ensure_review_candidate(current, transitioned)
        updated = await self._repository.update_question_review(
            curriculum_version_id,
            question_id,
            transitioned,
            expected_version=expected_version,
            actor_id=actor_id,
        )
        enriched = await self._repository.get_question_record(curriculum_version_id, updated.id)
        self._audit_review_transition(current, enriched, actor_id=actor_id)
        await self._session.commit()
        return enriched

    async def transition_chunk_review(
        self,
        curriculum_version_id: UUID,
        chunk_id: UUID,
        target: ReviewState,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        current = await self.get_chunk(curriculum_version_id, chunk_id)
        self._require_expected_version(current, expected_version)
        transitioned = transition_review_state(current.review_state, target)
        if transitioned is current.review_state:
            return current
        self._ensure_review_candidate(current, transitioned)
        updated = await self._repository.update_chunk_review(
            curriculum_version_id,
            chunk_id,
            transitioned,
            expected_version=expected_version,
            actor_id=actor_id,
        )
        enriched = await self._repository.get_chunk_record(curriculum_version_id, updated.id)
        self._audit_review_transition(current, enriched, actor_id=actor_id)
        await self._session.commit()
        return enriched

    async def store_question_embedding(
        self,
        question_id: UUID,
        result: EmbeddingResult,
        *,
        actor_id: UUID,
    ) -> StoredEmbedding:
        self._validate_embedding(result)
        question = (await self._repository.get_question(question_id, for_update=True)).to_domain()
        self._require_reviewed(question)
        return await self._store_embedding(
            historical_question_id=question.id,
            knowledge_chunk_id=None,
            text=question.text,
            record_version=question.version,
            result=result,
            actor_id=actor_id,
            resource_type="historical_question",
        )

    async def store_chunk_embedding(
        self,
        chunk_id: UUID,
        result: EmbeddingResult,
        *,
        actor_id: UUID,
    ) -> StoredEmbedding:
        self._validate_embedding(result)
        chunk = (await self._repository.get_chunk(chunk_id, for_update=True)).to_domain()
        self._require_reviewed(chunk)
        return await self._store_embedding(
            historical_question_id=None,
            knowledge_chunk_id=chunk.id,
            text=chunk.text,
            record_version=chunk.version,
            result=result,
            actor_id=actor_id,
            resource_type="knowledge_chunk",
        )

    async def _store_embedding(
        self,
        *,
        historical_question_id: UUID | None,
        knowledge_chunk_id: UUID | None,
        text: str,
        record_version: int,
        result: EmbeddingResult,
        actor_id: UUID,
        resource_type: str,
    ) -> StoredEmbedding:
        config, _ = await self._repository.get_or_create_embedding_configuration(
            result.config,
            actor_id=actor_id,
        )
        source_text_sha256 = hashlib.sha256(text.encode()).hexdigest()
        stored = await self._repository.store_embedding(
            historical_question_id=historical_question_id,
            knowledge_chunk_id=knowledge_chunk_id,
            config=config,
            source_text_sha256=source_text_sha256,
            vector=result.vector,
            actor_id=actor_id,
        )
        target_id = cast(UUID, historical_question_id or knowledge_chunk_id)
        if stored.created:
            self._audit(
                action=f"knowledge.{self._resource_action_name(resource_type)}.embedded",
                actor_id=actor_id,
                resource_type=resource_type,
                resource_id=target_id,
                payload={
                    "embedding_id": str(stored.id),
                    "embedding_configuration_id": str(stored.configuration_id),
                    "provider": config.provider,
                    "model": config.model,
                    "dimension": config.dimension,
                    "embedding_version": config.version,
                    "config_fingerprint": config.config_fingerprint,
                    "source_text_sha256": stored.source_text_sha256,
                    "version": record_version,
                },
            )
            await self._session.commit()
        return StoredEmbedding(
            id=stored.id,
            configuration_id=stored.configuration_id,
            config=config.to_domain(),
            source_text_sha256=stored.source_text_sha256,
            vector=stored.vector,
            deduplicated=not stored.created,
        )

    async def _ensure_curriculum_exists(self, curriculum_version_id: UUID) -> None:
        if await self._session.get(CurriculumVersionModel, curriculum_version_id) is None:
            raise KnowledgeCurriculumNotFoundError(curriculum_version_id)

    async def _validate_source(self, record: HistoricalQuestion | KnowledgeChunk) -> None:
        source_document_id = record.provenance.source_document_id
        document = await self._session.get(SourceDocumentModel, source_document_id)
        if document is None:
            raise KnowledgeSourceDocumentNotFoundError(source_document_id)
        if document.curriculum_version_id != record.curriculum_version_id:
            raise KnowledgeSourceCurriculumMismatchError(
                source_document_id,
                record.curriculum_version_id,
            )
        if document.extraction_status is not ExtractionStatus.TRUSTED:
            raise TrustedKnowledgeSourceRequiredError(source_document_id)
        if isinstance(record, HistoricalQuestion) and (
            document.document_type is not SourceDocumentType.PAST_PAPER
            or document.year != record.year
            or document.paper_code != record.paper_code
        ):
            raise KnowledgeSourceMetadataMismatchError(source_document_id)

    @staticmethod
    def _validate_embedding(result: EmbeddingResult) -> None:
        config = result.config
        fields = (config.provider, config.model, config.version, config.config_fingerprint)
        if (
            any(not value.strip() or value != value.strip() for value in fields)
            or len(config.provider) > 64
            or len(config.model) > 128
            or len(config.version) > 64
            or len(config.config_fingerprint) > 128
            or not 1 <= config.dimension <= 4096
        ):
            raise EmbeddingContractError("embedding configuration is invalid")
        if len(result.vector) != config.dimension:
            raise EmbeddingDimensionMismatchError(config.dimension, len(result.vector))
        if not all(math.isfinite(value) for value in result.vector):
            raise EmbeddingContractError("embedding vector must contain only finite values")

    @staticmethod
    def _require_reviewed(record: HistoricalQuestion | KnowledgeChunk) -> None:
        if record.review_state is not ReviewState.REVIEWED:
            raise EmbeddingRequiresReviewedRecordError(record.id, record.review_state)

    @staticmethod
    def _require_expected_version(
        record: HistoricalQuestion | KnowledgeChunk,
        expected_version: int,
    ) -> None:
        if record.version != expected_version:
            raise ConcurrentKnowledgeVersionError(expected_version, record.version)

    @staticmethod
    def _ensure_review_candidate(
        record: HistoricalQuestion | KnowledgeChunk,
        target: ReviewState,
    ) -> None:
        try:
            replace(record, review_state=target)
        except KnowledgeContractError as error:
            raise KnowledgeRecordNotReadyError(record.id) from error

    @staticmethod
    def _historical_metadata_audit_payload(question: HistoricalQuestion) -> dict[str, object]:
        return {
            "media_reference_count": (
                len(question.media_references) if question.media_references is not None else None
            ),
            "option_count": len(question.options) if question.options is not None else None,
            "answer_available": question.answer is not None,
            "marking_guidance_available": question.marking_guidance is not None,
            "marking_data_available": question.marking_data is not None,
            "question_archetype": question.question_archetype,
            "difficulty_label": (
                question.difficulty_label.value if question.difficulty_label is not None else None
            ),
            "difficulty_confidence": question.difficulty_confidence,
            "difficulty_source": question.difficulty_source,
        }

    @staticmethod
    def _classification(
        record: HistoricalQuestion | KnowledgeChunk,
    ) -> tuple[UUID | None, UUID | None, UUID | None, UUID | None]:
        return (
            record.competency_id,
            record.skill_id,
            record.sub_skill_id,
            record.learning_concept_id,
        )

    @staticmethod
    def _ensure_classification_mutable(record: HistoricalQuestion | KnowledgeChunk) -> None:
        if record.review_state in {ReviewState.REVIEWED, ReviewState.REJECTED}:
            raise FinalKnowledgeRecordError(record.id, record.review_state)

    def _audit_classification(
        self,
        previous: HistoricalQuestion | KnowledgeChunk,
        updated: HistoricalQuestion | KnowledgeChunk,
        *,
        actor_id: UUID,
    ) -> None:
        resource_type = self._resource_type(updated)
        self._audit(
            action=f"knowledge.{self._resource_action_name(resource_type)}.classified",
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=updated.id,
            payload={
                "from": self._classification_payload(previous),
                "to": self._classification_payload(updated),
                "previous_version": previous.version,
                "version": updated.version,
            },
        )

    def _audit_review_transition(
        self,
        previous: HistoricalQuestion | KnowledgeChunk,
        updated: HistoricalQuestion | KnowledgeChunk,
        *,
        actor_id: UUID,
    ) -> None:
        resource_type = self._resource_type(updated)
        self._audit(
            action=f"knowledge.{self._resource_action_name(resource_type)}.review_state_changed",
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=updated.id,
            payload={
                "from": previous.review_state.value,
                "to": updated.review_state.value,
                "previous_version": previous.version,
                "version": updated.version,
            },
        )

    @staticmethod
    def _classification_payload(
        record: HistoricalQuestion | KnowledgeChunk,
    ) -> dict[str, str | None]:
        return {
            "competency_id": KnowledgePersistenceService._optional_uuid(record.competency_id),
            "skill_id": KnowledgePersistenceService._optional_uuid(record.skill_id),
            "sub_skill_id": KnowledgePersistenceService._optional_uuid(record.sub_skill_id),
            "learning_concept_id": KnowledgePersistenceService._optional_uuid(
                record.learning_concept_id
            ),
        }

    @staticmethod
    def _optional_uuid(value: UUID | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _resource_type(record: HistoricalQuestion | KnowledgeChunk) -> str:
        if isinstance(record, HistoricalQuestion):
            return "historical_question"
        return "knowledge_chunk"

    @staticmethod
    def _resource_action_name(resource_type: str) -> str:
        return "question" if resource_type == "historical_question" else "chunk"

    def _audit(
        self,
        *,
        action: str,
        actor_id: UUID,
        resource_type: str,
        resource_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=payload,
            )
        )
