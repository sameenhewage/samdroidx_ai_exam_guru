from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.models import (
    EmbeddingJobModel,
    EmbeddingJobStatus,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
)


class EmbeddingJobNotFoundError(LookupError):
    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__(f"embedding job not found: {job_id}")


class EmbeddingPersistenceConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingSourceRecord:
    kind: Literal["historical_question", "knowledge_chunk"]
    id: UUID
    curriculum_version_id: UUID
    review_state: ReviewState
    text: str
    version: int


@dataclass(frozen=True, slots=True)
class StoredEmbeddingJob:
    job: EmbeddingJobModel
    created: bool


class SqlAlchemyEmbeddingJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def curriculum_exists(self, curriculum_version_id: UUID) -> bool:
        return (
            await self._session.scalar(
                select(CurriculumVersionModel.id).where(
                    CurriculumVersionModel.id == curriculum_version_id
                )
            )
            is not None
        )

    async def load_sources(
        self,
        historical_question_ids: tuple[UUID, ...],
        knowledge_chunk_ids: tuple[UUID, ...],
    ) -> tuple[EmbeddingSourceRecord, ...]:
        """Return the global kind/UUID order workers use when acquiring source-row locks."""

        records: list[EmbeddingSourceRecord] = []
        if historical_question_ids:
            questions = tuple(
                await self._session.scalars(
                    select(HistoricalQuestionModel)
                    .where(HistoricalQuestionModel.id.in_(historical_question_ids))
                    .order_by(HistoricalQuestionModel.id)
                )
            )
            records.extend(
                EmbeddingSourceRecord(
                    kind="historical_question",
                    id=question.id,
                    curriculum_version_id=question.curriculum_version_id,
                    review_state=question.review_state,
                    text=question.text,
                    version=question.version,
                )
                for question in questions
            )
        if knowledge_chunk_ids:
            chunks = tuple(
                await self._session.scalars(
                    select(KnowledgeChunkModel)
                    .where(KnowledgeChunkModel.id.in_(knowledge_chunk_ids))
                    .order_by(KnowledgeChunkModel.id)
                )
            )
            records.extend(
                EmbeddingSourceRecord(
                    kind="knowledge_chunk",
                    id=chunk.id,
                    curriculum_version_id=chunk.curriculum_version_id,
                    review_state=chunk.review_state,
                    text=chunk.text,
                    version=chunk.version,
                )
                for chunk in chunks
            )
        return tuple(sorted(records, key=lambda item: (item.kind, item.id.int)))

    async def store_job(self, values: dict[str, object]) -> StoredEmbeddingJob:
        inserted = await self._session.scalar(
            insert(EmbeddingJobModel)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(EmbeddingJobModel)
        )
        if inserted is not None:
            return StoredEmbeddingJob(job=inserted, created=True)
        existing = await self._session.scalar(
            select(EmbeddingJobModel).where(
                EmbeddingJobModel.created_by == values["created_by"],
                EmbeddingJobModel.idempotency_key_hash == values["idempotency_key_hash"],
            )
        )
        if existing is None:
            raise EmbeddingPersistenceConflictError(
                "embedding insert conflicted outside actor idempotency"
            )
        return StoredEmbeddingJob(job=existing, created=False)

    async def get_idempotent_job(
        self,
        *,
        actor_id: UUID,
        idempotency_key_hash: str,
    ) -> EmbeddingJobModel | None:
        model = await self._session.scalar(
            select(EmbeddingJobModel).where(
                EmbeddingJobModel.created_by == actor_id,
                EmbeddingJobModel.idempotency_key_hash == idempotency_key_hash,
            )
        )
        return model if isinstance(model, EmbeddingJobModel) else None

    async def latest_failed_retry(
        self,
        *,
        curriculum_version_id: UUID,
        actor_id: UUID,
        request_fingerprint: str,
    ) -> EmbeddingJobModel | None:
        model = await self._session.scalar(
            select(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.curriculum_version_id == curriculum_version_id,
                EmbeddingJobModel.created_by == actor_id,
                EmbeddingJobModel.request_fingerprint == request_fingerprint,
                EmbeddingJobModel.status == EmbeddingJobStatus.FAILED.value,
            )
            .order_by(EmbeddingJobModel.completed_at.desc(), EmbeddingJobModel.id.desc())
            .limit(1)
        )
        return model if isinstance(model, EmbeddingJobModel) else None

    async def attach_queue_message(self, job_id: UUID, message_id: str) -> EmbeddingJobModel:
        job = await self._session.scalar(
            update(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.id == job_id,
                EmbeddingJobModel.status == EmbeddingJobStatus.QUEUED.value,
                EmbeddingJobModel.queue_message_id.is_(None),
            )
            .values(
                queue_message_id=message_id,
                version=EmbeddingJobModel.version + 1,
                updated_at=func.clock_timestamp(),
            )
            .returning(EmbeddingJobModel)
        )
        if job is None:
            job = await self._session.scalar(
                select(EmbeddingJobModel)
                .where(EmbeddingJobModel.id == job_id)
                .execution_options(populate_existing=True)
            )
        if job is None:
            raise EmbeddingJobNotFoundError(job_id)
        return job

    async def get_job(
        self,
        curriculum_version_id: UUID,
        job_id: UUID,
    ) -> EmbeddingJobModel:
        job = await self._session.scalar(
            select(EmbeddingJobModel).where(
                EmbeddingJobModel.id == job_id,
                EmbeddingJobModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if job is None:
            raise EmbeddingJobNotFoundError(job_id)
        return job

    async def get_job_unscoped(self, job_id: UUID) -> EmbeddingJobModel | None:
        return await self._session.get(EmbeddingJobModel, job_id)

    async def list_jobs(
        self,
        curriculum_version_id: UUID,
        *,
        status: EmbeddingJobStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[EmbeddingJobModel, ...]:
        statement = select(EmbeddingJobModel).where(
            EmbeddingJobModel.curriculum_version_id == curriculum_version_id
        )
        if status is not None:
            statement = statement.where(EmbeddingJobModel.status == status.value)
        return tuple(
            await self._session.scalars(
                statement.order_by(EmbeddingJobModel.created_at.desc(), EmbeddingJobModel.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    async def claim(self, job_id: UUID, *, claimed_at: datetime) -> EmbeddingJobModel | None:
        return await self._session.scalar(
            update(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.id == job_id,
                EmbeddingJobModel.status == EmbeddingJobStatus.QUEUED.value,
            )
            .values(
                status=EmbeddingJobStatus.CLAIMED.value,
                version=EmbeddingJobModel.version + 1,
                updated_at=claimed_at,
                claimed_at=claimed_at,
            )
            .returning(EmbeddingJobModel)
        )

    async def advance_progress(
        self,
        job_id: UUID,
        *,
        expected_version: int,
        embedded: bool,
        updated_at: datetime,
    ) -> EmbeddingJobModel | None:
        values: dict[str, object] = {
            "version": EmbeddingJobModel.version + 1,
            "updated_at": updated_at,
        }
        if embedded:
            values["embedded_count"] = EmbeddingJobModel.embedded_count + 1
        else:
            values["deduplicated_count"] = EmbeddingJobModel.deduplicated_count + 1
        return await self._session.scalar(
            update(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.id == job_id,
                EmbeddingJobModel.status == EmbeddingJobStatus.CLAIMED.value,
                EmbeddingJobModel.version == expected_version,
            )
            .values(**values)
            .returning(EmbeddingJobModel)
        )

    async def complete(
        self,
        job_id: UUID,
        *,
        expected_version: int,
        succeeded: bool,
        failure_code: str | None,
        completed_at: datetime,
    ) -> EmbeddingJobModel | None:
        return await self._session.scalar(
            update(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.id == job_id,
                EmbeddingJobModel.status == EmbeddingJobStatus.CLAIMED.value,
                EmbeddingJobModel.version == expected_version,
            )
            .values(
                status=(
                    EmbeddingJobStatus.SUCCEEDED.value
                    if succeeded
                    else EmbeddingJobStatus.FAILED.value
                ),
                version=EmbeddingJobModel.version + 1,
                updated_at=completed_at,
                completed_at=completed_at,
                failure_code=None if succeeded else failure_code,
            )
            .returning(EmbeddingJobModel)
        )

    async def lock_recoverable_outbox_jobs(
        self,
        *,
        created_before: datetime,
        limit: int,
    ) -> tuple[EmbeddingJobModel, ...]:
        return tuple(
            await self._session.scalars(
                select(EmbeddingJobModel)
                .where(
                    EmbeddingJobModel.status == EmbeddingJobStatus.QUEUED.value,
                    EmbeddingJobModel.queue_message_id.is_(None),
                    EmbeddingJobModel.created_at < created_before,
                )
                .order_by(EmbeddingJobModel.created_at, EmbeddingJobModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    async def lock_expired_claims(
        self,
        *,
        claimed_before: datetime,
        limit: int,
    ) -> tuple[EmbeddingJobModel, ...]:
        return tuple(
            await self._session.scalars(
                select(EmbeddingJobModel)
                .where(
                    EmbeddingJobModel.status == EmbeddingJobStatus.CLAIMED.value,
                    EmbeddingJobModel.claimed_at.is_not(None),
                    EmbeddingJobModel.claimed_at < claimed_before,
                )
                .order_by(EmbeddingJobModel.claimed_at, EmbeddingJobModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    async def expire_claim(
        self,
        job: EmbeddingJobModel,
        *,
        completed_at: datetime,
    ) -> EmbeddingJobModel | None:
        return await self._session.scalar(
            update(EmbeddingJobModel)
            .where(
                EmbeddingJobModel.id == job.id,
                EmbeddingJobModel.status == EmbeddingJobStatus.CLAIMED.value,
                EmbeddingJobModel.version == job.version,
            )
            .values(
                status=EmbeddingJobStatus.FAILED.value,
                version=EmbeddingJobModel.version + 1,
                updated_at=completed_at,
                completed_at=completed_at,
                failure_code="worker_lease_expired",
            )
            .returning(EmbeddingJobModel)
        )
