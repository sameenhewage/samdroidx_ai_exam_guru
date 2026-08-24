from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
)
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.models import HistoricalQuestionModel, KnowledgeChunkModel

from .models import (
    GenerationAttemptModel,
    GenerationJobModel,
    GenerationJobStatus,
    GenerationRunModel,
    GenerationRunStatus,
)


@dataclass(frozen=True, slots=True)
class GenerationScopeRecord:
    curriculum_version_id: UUID
    exam_id: UUID
    medium_id: UUID
    grade: int
    medium: str
    curriculum_active: bool
    exam_active: bool
    medium_active: bool


@dataclass(frozen=True, slots=True)
class GenerationContextRecord:
    record_kind: str
    id: UUID
    curriculum_version_id: UUID
    text: str
    version: int
    review_state: ReviewState
    competency_id: UUID | None
    skill_id: UUID | None
    sub_skill_id: UUID | None
    learning_concept_id: UUID | None
    source_document_id: UUID
    source_curriculum_version_id: UUID | None
    source_checksum_sha256: str
    source_status: ExtractionStatus
    page_number: int
    source_block_id: UUID | None


@dataclass(frozen=True, slots=True)
class GenerationRunWrite:
    id: UUID
    curriculum_version_id: UUID
    paper_blueprint_id: UUID
    retry_of_run_id: UUID | None
    slot_id: str
    idempotency_key_hash: str
    request_fingerprint: str
    blueprint_version: str
    blueprint_snapshot: dict[str, object]
    blueprint_slot_snapshot: dict[str, object]
    knowledge_chunk_ids: list[str]
    historical_question_ids: list[str]
    context_snapshot: dict[str, object]
    prompt_id: str
    prompt_version: str
    provider: str
    provider_version: str
    model: str
    model_version: str
    retrieval_version: str
    schema_version: str
    pricing_version: str
    input_microusd_per_million_tokens: int
    output_microusd_per_million_tokens: int
    generation_parameters: dict[str, object]
    max_attempts: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_microusd: int
    created_by: UUID

    def values(self) -> dict[str, object]:
        return {
            **{field: getattr(self, field) for field in self.__dataclass_fields__},
            "status": GenerationRunStatus.PENDING.value,
            "version": 0,
            "attempt_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_microusd": 0,
            "latency_ms": 0,
        }


@dataclass(frozen=True, slots=True)
class StoredGeneration:
    run: GenerationRunModel
    job: GenerationJobModel
    created: bool


@dataclass(frozen=True, slots=True)
class GenerationAttemptAccounting:
    attempt_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_microusd: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class GenerationClaimRecord:
    run: GenerationRunModel
    job: GenerationJobModel


class GenerationRunNotFoundError(LookupError):
    pass


class GenerationJobNotFoundError(LookupError):
    pass


class SqlAlchemyGenerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_scope(self, curriculum_version_id: UUID) -> GenerationScopeRecord | None:
        row = (
            await self._session.execute(
                select(
                    CurriculumVersionModel.id,
                    ExamConfigurationModel.id,
                    MediumModel.id,
                    ExamConfigurationModel.grade,
                    MediumModel.code,
                    CurriculumVersionModel.active,
                    ExamConfigurationModel.active,
                    MediumModel.active,
                )
                .join(
                    ExamConfigurationModel,
                    ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
                )
                .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
                .where(CurriculumVersionModel.id == curriculum_version_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return GenerationScopeRecord(
            curriculum_version_id=row[0],
            exam_id=row[1],
            medium_id=row[2],
            grade=row[3],
            medium=row[4],
            curriculum_active=row[5],
            exam_active=row[6],
            medium_active=row[7],
        )

    async def get_blueprint(
        self,
        curriculum_version_id: UUID,
        paper_blueprint_id: UUID,
    ) -> PaperBlueprintModel | None:
        model = await self._session.scalar(
            select(PaperBlueprintModel).where(
                PaperBlueprintModel.id == paper_blueprint_id,
                PaperBlueprintModel.curriculum_version_id == curriculum_version_id,
            )
        )
        return model if isinstance(model, PaperBlueprintModel) else None

    async def list_context_records(
        self,
        knowledge_chunk_ids: tuple[UUID, ...],
        historical_question_ids: tuple[UUID, ...],
    ) -> tuple[GenerationContextRecord, ...]:
        records: list[GenerationContextRecord] = []
        if knowledge_chunk_ids:
            rows = await self._session.execute(
                select(KnowledgeChunkModel, SourceDocumentModel)
                .join(
                    SourceDocumentModel,
                    SourceDocumentModel.id == KnowledgeChunkModel.source_document_id,
                )
                .where(KnowledgeChunkModel.id.in_(knowledge_chunk_ids))
            )
            records.extend(self._chunk_context(chunk, source) for chunk, source in rows.all())
        if historical_question_ids:
            rows = await self._session.execute(
                select(HistoricalQuestionModel, SourceDocumentModel)
                .join(
                    SourceDocumentModel,
                    SourceDocumentModel.id == HistoricalQuestionModel.source_document_id,
                )
                .where(HistoricalQuestionModel.id.in_(historical_question_ids))
            )
            records.extend(
                self._question_context(question, source) for question, source in rows.all()
            )
        return tuple(records)

    async def store_run(
        self,
        write: GenerationRunWrite,
        *,
        job_id: UUID,
    ) -> StoredGeneration:
        run = await self._session.scalar(
            insert(GenerationRunModel)
            .values(**write.values())
            .on_conflict_do_nothing()
            .returning(GenerationRunModel)
        )
        if run is None:
            run = await self._session.scalar(
                select(GenerationRunModel).where(
                    GenerationRunModel.created_by == write.created_by,
                    GenerationRunModel.idempotency_key_hash == write.idempotency_key_hash,
                )
            )
            if run is None:
                raise RuntimeError("idempotent generation winner was not found")
            job = await self._session.scalar(
                select(GenerationJobModel).where(GenerationJobModel.generation_run_id == run.id)
            )
            if job is None:
                raise RuntimeError("idempotent generation job was not found")
            return StoredGeneration(run=run, job=job, created=False)

        job = GenerationJobModel(
            id=job_id,
            generation_run_id=run.id,
            curriculum_version_id=run.curriculum_version_id,
            status=GenerationJobStatus.QUEUED.value,
            version=0,
            queue_message_id=None,
            failure_code=None,
            created_by=write.created_by,
            claimed_at=None,
            completed_at=None,
        )
        self._session.add(job)
        await self._session.flush()
        return StoredGeneration(run=run, job=job, created=True)

    async def attach_queue_message(
        self,
        job_id: UUID,
        message_id: str,
    ) -> GenerationJobModel:
        job = await self._session.scalar(
            update(GenerationJobModel)
            .where(
                GenerationJobModel.id == job_id,
                GenerationJobModel.status == GenerationJobStatus.QUEUED.value,
                GenerationJobModel.queue_message_id.is_(None),
            )
            .values(
                queue_message_id=message_id,
                version=GenerationJobModel.version + 1,
            )
            .returning(GenerationJobModel)
        )
        if job is None:
            job = await self._session.get(GenerationJobModel, job_id)
        if job is None:
            raise GenerationJobNotFoundError(job_id)
        return job

    async def fail_dispatch(
        self,
        run_id: UUID,
        job_id: UUID,
        *,
        completed_at: datetime,
        failure_code: str,
    ) -> GenerationJobModel:
        run = await self._session.scalar(
            update(GenerationRunModel)
            .where(
                GenerationRunModel.id == run_id,
                GenerationRunModel.status == GenerationRunStatus.PENDING.value,
                GenerationRunModel.version == 0,
            )
            .values(
                status=GenerationRunStatus.FAILED.value,
                version=1,
                completed_at=completed_at,
                failure_code=failure_code,
            )
            .returning(GenerationRunModel)
        )
        job = await self._session.scalar(
            update(GenerationJobModel)
            .where(
                GenerationJobModel.id == job_id,
                GenerationJobModel.status == GenerationJobStatus.QUEUED.value,
            )
            .values(
                status=GenerationJobStatus.FAILED.value,
                version=GenerationJobModel.version + 1,
                completed_at=completed_at,
                failure_code=failure_code,
            )
            .returning(GenerationJobModel)
        )
        if run is None or job is None:
            raise RuntimeError("generation dispatch failure lost its CAS race")
        return job

    async def lock_recoverable_outbox_jobs(
        self,
        *,
        created_before: datetime,
        limit: int,
    ) -> tuple[GenerationJobModel, ...]:
        return tuple(
            await self._session.scalars(
                select(GenerationJobModel)
                .where(
                    GenerationJobModel.status == GenerationJobStatus.QUEUED.value,
                    GenerationJobModel.queue_message_id.is_(None),
                    GenerationJobModel.created_at < created_before,
                )
                .order_by(GenerationJobModel.created_at, GenerationJobModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    async def lock_expired_claims(
        self,
        *,
        claimed_before: datetime,
        limit: int,
    ) -> tuple[GenerationClaimRecord, ...]:
        rows = (
            await self._session.execute(
                select(GenerationRunModel, GenerationJobModel)
                .join(
                    GenerationJobModel,
                    GenerationJobModel.generation_run_id == GenerationRunModel.id,
                )
                .where(
                    GenerationRunModel.status == GenerationRunStatus.RUNNING.value,
                    GenerationJobModel.status == GenerationJobStatus.CLAIMED.value,
                    GenerationJobModel.claimed_at.is_not(None),
                    GenerationJobModel.claimed_at < claimed_before,
                )
                .order_by(GenerationJobModel.claimed_at, GenerationJobModel.id)
                .limit(limit)
                .with_for_update(
                    of=(GenerationRunModel, GenerationJobModel),
                    skip_locked=True,
                )
            )
        ).all()
        return tuple(GenerationClaimRecord(run=run, job=job) for run, job in rows)

    async def lock_active_completion(
        self,
        run_id: UUID,
        job_id: UUID,
    ) -> GenerationClaimRecord | None:
        row = (
            await self._session.execute(
                select(GenerationRunModel, GenerationJobModel)
                .join(
                    GenerationJobModel,
                    GenerationJobModel.generation_run_id == GenerationRunModel.id,
                )
                .where(
                    GenerationRunModel.id == run_id,
                    GenerationRunModel.status == GenerationRunStatus.RUNNING.value,
                    GenerationJobModel.id == job_id,
                    GenerationJobModel.status == GenerationJobStatus.CLAIMED.value,
                )
                .with_for_update(of=(GenerationRunModel, GenerationJobModel))
            )
        ).one_or_none()
        if row is None:
            return None
        return GenerationClaimRecord(run=row[0], job=row[1])

    async def get_attempt_accounting(self, run_id: UUID) -> GenerationAttemptAccounting:
        row = (
            await self._session.execute(
                select(
                    func.count(GenerationAttemptModel.id),
                    func.coalesce(func.sum(GenerationAttemptModel.input_tokens), 0),
                    func.coalesce(func.sum(GenerationAttemptModel.output_tokens), 0),
                    func.coalesce(func.sum(GenerationAttemptModel.total_tokens), 0),
                    func.coalesce(func.sum(GenerationAttemptModel.cost_microusd), 0),
                    func.coalesce(func.sum(GenerationAttemptModel.latency_ms), 0),
                ).where(GenerationAttemptModel.generation_run_id == run_id)
            )
        ).one()
        return GenerationAttemptAccounting(
            attempt_count=int(row[0]),
            input_tokens=int(row[1]),
            output_tokens=int(row[2]),
            total_tokens=int(row[3]),
            cost_microusd=int(row[4]),
            latency_ms=int(row[5]),
        )

    async def expire_claim(
        self,
        claim: GenerationClaimRecord,
        accounting: GenerationAttemptAccounting,
        *,
        completed_at: datetime,
    ) -> bool:
        run = await self._session.scalar(
            update(GenerationRunModel)
            .where(
                GenerationRunModel.id == claim.run.id,
                GenerationRunModel.status == GenerationRunStatus.RUNNING.value,
                GenerationRunModel.version == claim.run.version,
            )
            .values(
                status=GenerationRunStatus.FAILED.value,
                version=GenerationRunModel.version + 1,
                completed_at=completed_at,
                failure_code="worker_lease_expired",
                result_attempt_id=None,
                attempt_count=accounting.attempt_count,
                input_tokens=accounting.input_tokens,
                output_tokens=accounting.output_tokens,
                total_tokens=accounting.total_tokens,
                cost_microusd=accounting.cost_microusd,
                latency_ms=accounting.latency_ms,
                candidate=None,
                disposition=None,
            )
            .returning(GenerationRunModel)
        )
        if run is None:
            return False
        job = await self._session.scalar(
            update(GenerationJobModel)
            .where(
                GenerationJobModel.id == claim.job.id,
                GenerationJobModel.status == GenerationJobStatus.CLAIMED.value,
                GenerationJobModel.version == claim.job.version,
            )
            .values(
                status=GenerationJobStatus.FAILED.value,
                version=GenerationJobModel.version + 1,
                completed_at=completed_at,
                failure_code="worker_lease_expired",
            )
            .returning(GenerationJobModel)
        )
        if job is None:
            await self._session.rollback()
            return False
        return True

    async def get_run(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
    ) -> GenerationRunModel:
        run = await self._session.scalar(
            select(GenerationRunModel).where(
                GenerationRunModel.id == run_id,
                GenerationRunModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if run is None:
            raise GenerationRunNotFoundError(run_id)
        return run

    async def get_job(
        self,
        curriculum_version_id: UUID,
        job_id: UUID,
    ) -> GenerationJobModel:
        job = await self._session.scalar(
            select(GenerationJobModel).where(
                GenerationJobModel.id == job_id,
                GenerationJobModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if job is None:
            raise GenerationJobNotFoundError(job_id)
        return job

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[GenerationRunModel, ...]:
        return tuple(
            await self._session.scalars(
                select(GenerationRunModel)
                .where(GenerationRunModel.curriculum_version_id == curriculum_version_id)
                .order_by(GenerationRunModel.created_at.desc(), GenerationRunModel.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    async def list_attempts(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[GenerationAttemptModel, ...]:
        await self.get_run(curriculum_version_id, run_id)
        return tuple(
            await self._session.scalars(
                select(GenerationAttemptModel)
                .where(GenerationAttemptModel.generation_run_id == run_id)
                .order_by(GenerationAttemptModel.attempt_number)
                .offset(offset)
                .limit(limit)
            )
        )

    @staticmethod
    def _chunk_context(
        chunk: KnowledgeChunkModel,
        source: SourceDocumentModel,
    ) -> GenerationContextRecord:
        return GenerationContextRecord(
            record_kind="knowledge_chunk",
            id=chunk.id,
            curriculum_version_id=chunk.curriculum_version_id,
            text=chunk.text,
            version=chunk.version,
            review_state=chunk.review_state,
            competency_id=chunk.competency_id,
            skill_id=chunk.skill_id,
            sub_skill_id=chunk.sub_skill_id,
            learning_concept_id=chunk.learning_concept_id,
            source_document_id=chunk.source_document_id,
            source_curriculum_version_id=source.curriculum_version_id,
            source_checksum_sha256=source.checksum_sha256,
            source_status=source.extraction_status,
            page_number=chunk.page_number,
            source_block_id=chunk.source_block_id,
        )

    @staticmethod
    def _question_context(
        question: HistoricalQuestionModel,
        source: SourceDocumentModel,
    ) -> GenerationContextRecord:
        return GenerationContextRecord(
            record_kind="historical_question",
            id=question.id,
            curriculum_version_id=question.curriculum_version_id,
            text=question.text,
            version=question.version,
            review_state=question.review_state,
            competency_id=question.competency_id,
            skill_id=question.skill_id,
            sub_skill_id=question.sub_skill_id,
            learning_concept_id=question.learning_concept_id,
            source_document_id=question.source_document_id,
            source_curriculum_version_id=source.curriculum_version_id,
            source_checksum_sha256=source.checksum_sha256,
            source_status=source.extraction_status,
            page_number=question.page_number,
            source_block_id=question.source_block_id,
        )
