import hashlib
import math
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.knowledge.domain import (
    HistoricalQuestion,
    KnowledgeChunk,
    ReviewState,
    transition_review_state,
)
from exam_guru_api.knowledge.embeddings import (
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.knowledge.repository import (
    SqlAlchemyKnowledgeRepository,
)


class FinalKnowledgeRecordError(RuntimeError):
    def __init__(self, record_id: UUID, state: ReviewState) -> None:
        self.record_id = record_id
        self.state = state
        super().__init__(f"cannot change classification of {state.value} record {record_id}")


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
                    "source_block_id": str(result.record.provenance.source_block_id),
                    "question_number": result.record.question_number,
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
                    "source_block_id": str(result.record.provenance.source_block_id),
                    "sequence": result.record.sequence,
                },
            )
            await self._session.commit()
        return SourceImportResult(record=result.record, deduplicated=not result.created)

    async def classify_question(
        self,
        question_id: UUID,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        model = await self._repository.get_question(question_id, for_update=True)
        current = model.to_domain()
        classification = (competency_id, skill_id, sub_skill_id, learning_concept_id)
        if self._classification(current) == classification:
            return current
        self._ensure_classification_mutable(current)
        updated = await self._repository.update_question_classification(
            model,
            competency_id=competency_id,
            skill_id=skill_id,
            sub_skill_id=sub_skill_id,
            learning_concept_id=learning_concept_id,
            actor_id=actor_id,
        )
        self._audit_classification(current, updated, actor_id=actor_id)
        await self._session.commit()
        return updated

    async def classify_chunk(
        self,
        chunk_id: UUID,
        *,
        competency_id: UUID | None,
        skill_id: UUID | None,
        sub_skill_id: UUID | None,
        learning_concept_id: UUID | None,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        model = await self._repository.get_chunk(chunk_id, for_update=True)
        current = model.to_domain()
        classification = (competency_id, skill_id, sub_skill_id, learning_concept_id)
        if self._classification(current) == classification:
            return current
        self._ensure_classification_mutable(current)
        updated = await self._repository.update_chunk_classification(
            model,
            competency_id=competency_id,
            skill_id=skill_id,
            sub_skill_id=sub_skill_id,
            learning_concept_id=learning_concept_id,
            actor_id=actor_id,
        )
        self._audit_classification(current, updated, actor_id=actor_id)
        await self._session.commit()
        return updated

    async def transition_question_review(
        self,
        question_id: UUID,
        target: ReviewState,
        *,
        actor_id: UUID,
    ) -> HistoricalQuestion:
        model = await self._repository.get_question(question_id, for_update=True)
        current = model.to_domain()
        transitioned = transition_review_state(current.review_state, target)
        if transitioned is current.review_state:
            return current
        candidate = replace(current, review_state=transitioned)
        updated = await self._repository.update_question_review(
            model,
            candidate.review_state,
            actor_id=actor_id,
        )
        self._audit_review_transition(current, updated, actor_id=actor_id)
        await self._session.commit()
        return updated

    async def transition_chunk_review(
        self,
        chunk_id: UUID,
        target: ReviewState,
        *,
        actor_id: UUID,
    ) -> KnowledgeChunk:
        model = await self._repository.get_chunk(chunk_id, for_update=True)
        current = model.to_domain()
        transitioned = transition_review_state(current.review_state, target)
        if transitioned is current.review_state:
            return current
        candidate = replace(current, review_state=transitioned)
        updated = await self._repository.update_chunk_review(
            model,
            candidate.review_state,
            actor_id=actor_id,
        )
        self._audit_review_transition(current, updated, actor_id=actor_id)
        await self._session.commit()
        return updated

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
                    "version": config.version,
                    "config_fingerprint": config.config_fingerprint,
                    "source_text_sha256": stored.source_text_sha256,
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
            },
        )

    @staticmethod
    def _classification_payload(
        record: HistoricalQuestion | KnowledgeChunk,
    ) -> dict[str, str | None]:
        return {
            "competency_id": str(record.competency_id) if record.competency_id else None,
            "skill_id": str(record.skill_id) if record.skill_id else None,
            "sub_skill_id": str(record.sub_skill_id) if record.sub_skill_id else None,
            "learning_concept_id": (
                str(record.learning_concept_id) if record.learning_concept_id else None
            ),
        }

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
