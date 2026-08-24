from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import MIN_EMBEDDING_WORKER_LEASE_SECONDS
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.embedding_job_repository import (
    EmbeddingJobNotFoundError,
    EmbeddingSourceRecord,
    SqlAlchemyEmbeddingJobRepository,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig, EmbeddingContractError
from exam_guru_api.knowledge.models import EmbeddingJobModel, EmbeddingJobStatus
from exam_guru_api.knowledge.repository import (
    EmbeddingSourceConflictError,
    EmbeddingSpaceConflictError,
    KnowledgeRecordNotFoundError,
)
from exam_guru_api.knowledge.service import (
    EmbeddingRequiresReviewedRecordError,
    KnowledgePersistenceService,
)
from exam_guru_api.observability import OperationalTelemetry, get_operational_telemetry
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
)

_EMBEDDING_JOB_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/embedding-jobs")


class EmbeddingDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> str: ...


class EmbeddingCurriculumNotFoundError(LookupError):
    pass


class EmbeddingSourceNotFoundError(LookupError):
    pass


class EmbeddingSourceNotReviewedError(ValueError):
    pass


class EmbeddingSourceIdentityError(RuntimeError):
    pass


class EmbeddingIdempotencyConflictError(RuntimeError):
    pass


class EmbeddingQueueUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingJobCreationResult:
    job: EmbeddingJobModel
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class EmbeddingRecoveryPolicy:
    batch_size: int = 50
    outbox_min_age_seconds: int = 5
    worker_lease_seconds: int = 600

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= 100:
            raise ValueError("embedding recovery batch size must be between 1 and 100")
        if not 1 <= self.outbox_min_age_seconds <= 3_600:
            raise ValueError("embedding recovery outbox age must be between 1 and 3600 seconds")
        if (
            not isinstance(self.worker_lease_seconds, int)
            or isinstance(self.worker_lease_seconds, bool)
            or not MIN_EMBEDDING_WORKER_LEASE_SECONDS <= self.worker_lease_seconds <= 86_400
        ):
            raise ValueError(
                "embedding recovery worker lease must exceed actor execution and be bounded"
            )


@dataclass(frozen=True, slots=True)
class EmbeddingRecoveryResult:
    outbox_scanned: int
    outbox_dispatched: int
    outbox_failures: int
    claims_scanned: int
    claims_expired: int


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _source_fingerprint(records: tuple[EmbeddingSourceRecord, ...]) -> str:
    return _fingerprint(
        [
            {
                "id": str(record.id),
                "kind": record.kind,
                "source_text_sha256": hashlib.sha256(record.text.encode()).hexdigest(),
                "version": record.version,
            }
            for record in records
        ]
    )


def _config_snapshot(config: EmbeddingConfig) -> dict[str, object]:
    return {
        "config_fingerprint": config.config_fingerprint,
        "dimension": config.dimension,
        "model": config.model,
        "provider": config.provider,
        "version": config.version,
    }


class EmbeddingJobService:
    def __init__(
        self,
        session: AsyncSession,
        providers: EmbeddingProviderRegistry,
        dispatcher: EmbeddingDispatcher,
        active_config: EmbeddingConfig,
    ) -> None:
        self._session = session
        self._providers = providers
        self._dispatcher = dispatcher
        self._active_config = active_config
        self._repository = SqlAlchemyEmbeddingJobRepository(session)

    async def create(
        self,
        curriculum_version_id: UUID,
        *,
        historical_question_ids: tuple[UUID, ...],
        knowledge_chunk_ids: tuple[UUID, ...],
        idempotency_key: str,
        actor_id: UUID,
    ) -> EmbeddingJobCreationResult:
        self._validate_idempotency_key(idempotency_key)
        self._providers.ensure_provider(self._active_config)
        if not await self._repository.curriculum_exists(curriculum_version_id):
            raise EmbeddingCurriculumNotFoundError(curriculum_version_id)

        question_ids = tuple(sorted(historical_question_ids, key=lambda value: value.int))
        chunk_ids = tuple(sorted(knowledge_chunk_ids, key=lambda value: value.int))
        records = await self._repository.load_sources(question_ids, chunk_ids)
        self._validate_sources(curriculum_version_id, question_ids, chunk_ids, records)
        await self._prevalidate_existing_embeddings(curriculum_version_id, records)

        source_fingerprint = _source_fingerprint(records)
        request_fingerprint = _fingerprint(
            {
                "configuration": _config_snapshot(self._active_config),
                "curriculum_version_id": str(curriculum_version_id),
                "historical_question_ids": [str(value) for value in question_ids],
                "knowledge_chunk_ids": [str(value) for value in chunk_ids],
                "source_fingerprint": source_fingerprint,
            }
        )
        idempotency_key_hash = _fingerprint(idempotency_key)
        job_id = uuid5(
            _EMBEDDING_JOB_NAMESPACE,
            f"{actor_id}\0{idempotency_key_hash}",
        )
        retry = await self._repository.latest_failed_retry(
            curriculum_version_id=curriculum_version_id,
            actor_id=actor_id,
            request_fingerprint=request_fingerprint,
        )
        config = self._active_config
        stored = await self._repository.store_job(
            {
                "id": job_id,
                "curriculum_version_id": curriculum_version_id,
                "retry_of_job_id": None if retry is None else retry.id,
                "historical_question_ids": [str(value) for value in question_ids],
                "knowledge_chunk_ids": [str(value) for value in chunk_ids],
                "idempotency_key_hash": idempotency_key_hash,
                "request_fingerprint": request_fingerprint,
                "source_fingerprint": source_fingerprint,
                "provider": config.provider,
                "model": config.model,
                "dimension": config.dimension,
                "embedding_version": config.version,
                "config_fingerprint": config.config_fingerprint,
                "status": EmbeddingJobStatus.QUEUED.value,
                "version": 0,
                "queue_message_id": None,
                "requested_count": len(records),
                "embedded_count": 0,
                "deduplicated_count": 0,
                "failure_code": None,
                "created_by": actor_id,
                "claimed_at": None,
                "completed_at": None,
            }
        )
        if not stored.created and not self._same_request(
            stored.job,
            curriculum_version_id=curriculum_version_id,
            question_ids=question_ids,
            chunk_ids=chunk_ids,
            request_fingerprint=request_fingerprint,
        ):
            raise EmbeddingIdempotencyConflictError(idempotency_key_hash)

        if stored.created:
            self._audit_created(stored.job)
            await self._session.commit()

        job = stored.job
        if job.status == EmbeddingJobStatus.QUEUED.value and job.queue_message_id is None:
            job = await self._dispatch(job)
        return EmbeddingJobCreationResult(job=job, deduplicated=not stored.created)

    async def _prevalidate_existing_embeddings(
        self,
        curriculum_version_id: UUID,
        records: tuple[EmbeddingSourceRecord, ...],
    ) -> None:
        persistence = KnowledgePersistenceService(self._session)
        for record in records:
            if record.kind == "historical_question":
                await persistence.question_embedding_exists(
                    curriculum_version_id,
                    record.id,
                    self._active_config,
                )
            else:
                await persistence.chunk_embedding_exists(
                    curriculum_version_id,
                    record.id,
                    self._active_config,
                )

    async def _dispatch(self, job: EmbeddingJobModel) -> EmbeddingJobModel:
        try:
            message_id = self._dispatcher.dispatch(job.id)
            self._validate_message_id(message_id)
        except Exception as error:
            self._audit_dispatch_failed(job)
            await self._session.commit()
            raise EmbeddingQueueUnavailableError from error
        attached = await self._repository.attach_queue_message(job.id, message_id)
        await self._session.commit()
        return attached

    @staticmethod
    def _validate_sources(
        curriculum_version_id: UUID,
        question_ids: tuple[UUID, ...],
        chunk_ids: tuple[UUID, ...],
        records: tuple[EmbeddingSourceRecord, ...],
    ) -> None:
        requested = {
            *(("historical_question", identifier) for identifier in question_ids),
            *(("knowledge_chunk", identifier) for identifier in chunk_ids),
        }
        found = {(record.kind, record.id) for record in records}
        if requested != found or any(
            record.curriculum_version_id != curriculum_version_id for record in records
        ):
            raise EmbeddingSourceNotFoundError
        if any(record.review_state is not ReviewState.REVIEWED for record in records):
            raise EmbeddingSourceNotReviewedError

    @staticmethod
    def _same_request(
        job: EmbeddingJobModel,
        *,
        curriculum_version_id: UUID,
        question_ids: tuple[UUID, ...],
        chunk_ids: tuple[UUID, ...],
        request_fingerprint: str,
    ) -> bool:
        return (
            job.curriculum_version_id == curriculum_version_id
            and job.historical_question_ids == [str(value) for value in question_ids]
            and job.knowledge_chunk_ids == [str(value) for value in chunk_ids]
            and job.request_fingerprint == request_fingerprint
        )

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not value
            or value != value.strip()
            or len(value) > 128
            or any(character.isspace() or not character.isprintable() for character in value)
        ):
            raise EmbeddingIdempotencyConflictError("invalid idempotency key")

    @staticmethod
    def _validate_message_id(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
            or any(character.isspace() or not character.isprintable() for character in value)
        ):
            raise ValueError("invalid queue message id")

    def _audit_created(self, job: EmbeddingJobModel) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action="embedding_job.created",
                resource_type="embedding_job",
                resource_id=job.id,
                payload={
                    "curriculum_version_id": str(job.curriculum_version_id),
                    "retry_of_job_id": (
                        None if job.retry_of_job_id is None else str(job.retry_of_job_id)
                    ),
                    "requested_count": job.requested_count,
                    "provider": job.provider,
                    "model": job.model,
                    "dimension": job.dimension,
                    "embedding_version": job.embedding_version,
                    "config_fingerprint": job.config_fingerprint,
                    "status": job.status,
                },
            )
        )

    def _audit_dispatch_failed(self, job: EmbeddingJobModel) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action="embedding_job.dispatch_failed",
                resource_type="embedding_job",
                resource_id=job.id,
                payload={"failure_code": "queue_dispatch_failed"},
            )
        )


class EmbeddingJobReadService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = SqlAlchemyEmbeddingJobRepository(session)

    async def get(self, curriculum_version_id: UUID, job_id: UUID) -> EmbeddingJobModel:
        return await self._repository.get_job(curriculum_version_id, job_id)

    async def list(
        self,
        curriculum_version_id: UUID,
        *,
        status: EmbeddingJobStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[EmbeddingJobModel, ...]:
        if not await self._repository.curriculum_exists(curriculum_version_id):
            raise EmbeddingCurriculumNotFoundError(curriculum_version_id)
        return await self._repository.list_jobs(
            curriculum_version_id,
            status=status,
            limit=limit,
            offset=offset,
        )


class EmbeddingWorkerService:
    def __init__(
        self,
        session: AsyncSession,
        providers: EmbeddingProviderRegistry,
        active_config: EmbeddingConfig | None,
        *,
        telemetry: OperationalTelemetry | None = None,
    ) -> None:
        self._session = session
        self._providers = providers
        self._active_config = active_config
        self._telemetry = telemetry or get_operational_telemetry()
        self._repository = SqlAlchemyEmbeddingJobRepository(session)

    async def process(self, job_id: UUID) -> bool:
        job = await self._claim(job_id)
        if job is None:
            return False

        failure_code: str | None = None
        try:
            if job.embedding_config != self._active_config:
                raise ActiveEmbeddingConfigUnavailableError
            self._providers.ensure_provider(job.embedding_config)
            records = await self._repository.load_sources(
                tuple(UUID(value) for value in job.historical_question_ids),
                tuple(UUID(value) for value in job.knowledge_chunk_ids),
            )
            EmbeddingJobService._validate_sources(
                job.curriculum_version_id,
                tuple(UUID(value) for value in job.historical_question_ids),
                tuple(UUID(value) for value in job.knowledge_chunk_ids),
                records,
            )
            if _source_fingerprint(records) != job.source_fingerprint:
                raise EmbeddingSourceIdentityError
            for record in records:
                job = await self._process_record(job, record)
            if job.embedded_count + job.deduplicated_count != job.requested_count:
                raise RuntimeError("embedding progress did not cover the request")
        except ActiveEmbeddingConfigUnavailableError:
            failure_code = "embedding_config_unavailable"
        except EmbeddingProviderUnavailableError:
            failure_code = "embedding_provider_unavailable"
        except (EmbeddingSourceConflictError, EmbeddingSourceIdentityError):
            failure_code = "embedding_source_conflict"
        except EmbeddingSpaceConflictError:
            failure_code = "embedding_config_conflict"
        except (
            EmbeddingSourceNotFoundError,
            EmbeddingSourceNotReviewedError,
            EmbeddingRequiresReviewedRecordError,
            KnowledgeRecordNotFoundError,
        ):
            failure_code = "embedding_source_invalid"
        except EmbeddingContractError:
            failure_code = "embedding_contract_error"
        except Exception:
            failure_code = "embedding_internal_error"

        if failure_code is not None:
            return await self._complete_failure(job_id, failure_code)
        return await self._complete(job, succeeded=True, failure_code=None)

    async def _claim(self, job_id: UUID) -> EmbeddingJobModel | None:
        now = datetime.now(UTC)
        job = await self._repository.claim(job_id, claimed_at=now)
        if job is None:
            await self._session.rollback()
            return None
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action="embedding_job.claimed",
                resource_type="embedding_job",
                resource_id=job.id,
                payload={"requested_count": job.requested_count, "version": job.version},
            )
        )
        await self._session.commit()
        return job

    async def _process_record(
        self,
        job: EmbeddingJobModel,
        record: EmbeddingSourceRecord,
    ) -> EmbeddingJobModel:
        persistence = KnowledgePersistenceService(self._session)
        if record.kind == "historical_question":
            exists = await persistence.question_embedding_exists(
                job.curriculum_version_id,
                record.id,
                job.embedding_config,
                for_update=True,
            )
        else:
            exists = await persistence.chunk_embedding_exists(
                job.curriculum_version_id,
                record.id,
                job.embedding_config,
                for_update=True,
            )

        embedded = False
        if not exists:
            result = self._providers.embed_source(record.text, job.embedding_config)
            if record.kind == "historical_question":
                stored = await persistence.store_curriculum_question_embedding(
                    job.curriculum_version_id,
                    record.id,
                    result,
                    actor_id=job.created_by,
                    commit=False,
                )
            else:
                stored = await persistence.store_curriculum_chunk_embedding(
                    job.curriculum_version_id,
                    record.id,
                    result,
                    actor_id=job.created_by,
                    commit=False,
                )
            embedded = not stored.deduplicated
        progressed = await self._repository.advance_progress(
            job.id,
            expected_version=job.version,
            embedded=embedded,
            updated_at=datetime.now(UTC),
        )
        if progressed is None:
            raise RuntimeError("embedding progress lost its CAS race")
        await self._session.commit()
        return progressed

    async def _complete_failure(self, job_id: UUID, failure_code: str) -> bool:
        await self._session.rollback()
        job = await self._repository.get_job_unscoped(job_id)
        if job is None or job.status != EmbeddingJobStatus.CLAIMED.value:
            return False
        return await self._complete(job, succeeded=False, failure_code=failure_code)

    async def _complete(
        self,
        job: EmbeddingJobModel,
        *,
        succeeded: bool,
        failure_code: str | None,
    ) -> bool:
        terminal = await self._repository.complete(
            job.id,
            expected_version=job.version,
            succeeded=succeeded,
            failure_code=failure_code,
            completed_at=datetime.now(UTC),
        )
        if terminal is None:
            await self._session.rollback()
            return False
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=terminal.created_by,
                action=("embedding_job.succeeded" if succeeded else "embedding_job.failed"),
                resource_type="embedding_job",
                resource_id=terminal.id,
                payload={
                    "status": terminal.status,
                    "failure_code": terminal.failure_code,
                    "requested_count": terminal.requested_count,
                    "embedded_count": terminal.embedded_count,
                    "deduplicated_count": terminal.deduplicated_count,
                },
            )
        )
        await self._session.commit()
        self._telemetry.embedding_terminal(
            status=terminal.status,
            failure_code=terminal.failure_code,
            requested_count=terminal.requested_count,
            embedded_count=terminal.embedded_count,
            deduplicated_count=terminal.deduplicated_count,
        )
        return True


class EmbeddingRecoveryService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: EmbeddingDispatcher,
        policy: EmbeddingRecoveryPolicy,
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._policy = policy
        self._repository = SqlAlchemyEmbeddingJobRepository(session)

    async def recover(self, *, now: datetime | None = None) -> EmbeddingRecoveryResult:
        active_now = datetime.now(UTC) if now is None else now
        outbox_jobs = await self._repository.lock_recoverable_outbox_jobs(
            created_before=active_now - timedelta(seconds=self._policy.outbox_min_age_seconds),
            limit=self._policy.batch_size,
        )
        outbox_dispatched = 0
        outbox_failures = 0
        for job in outbox_jobs:
            try:
                message_id = self._dispatcher.dispatch(job.id)
                EmbeddingJobService._validate_message_id(message_id)
            except Exception:
                outbox_failures += 1
                self._audit_recovery(job, succeeded=False)
                continue
            await self._repository.attach_queue_message(job.id, message_id)
            outbox_dispatched += 1
            self._audit_recovery(job, succeeded=True)

        claims = await self._repository.lock_expired_claims(
            claimed_before=active_now - timedelta(seconds=self._policy.worker_lease_seconds),
            limit=self._policy.batch_size,
        )
        claims_expired = 0
        for claim in claims:
            terminal = await self._repository.expire_claim(
                claim,
                completed_at=active_now,
            )
            if terminal is not None:
                claims_expired += 1
                self._audit_lease_expired(terminal)

        await self._session.commit()
        return EmbeddingRecoveryResult(
            outbox_scanned=len(outbox_jobs),
            outbox_dispatched=outbox_dispatched,
            outbox_failures=outbox_failures,
            claims_scanned=len(claims),
            claims_expired=claims_expired,
        )

    def _audit_recovery(self, job: EmbeddingJobModel, *, succeeded: bool) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action=(
                    "embedding_job.redispatched" if succeeded else "embedding_job.redispatch_failed"
                ),
                resource_type="embedding_job",
                resource_id=job.id,
                payload={
                    "recovery": True,
                    "failure_code": None if succeeded else "queue_dispatch_failed",
                },
            )
        )

    def _audit_lease_expired(self, job: EmbeddingJobModel) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action="embedding_job.worker_lease_expired",
                resource_type="embedding_job",
                resource_id=job.id,
                payload={
                    "failure_code": "worker_lease_expired",
                    "requested_count": job.requested_count,
                    "embedded_count": job.embedded_count,
                    "deduplicated_count": job.deduplicated_count,
                },
            )
        )


__all__ = [
    "EmbeddingCurriculumNotFoundError",
    "EmbeddingIdempotencyConflictError",
    "EmbeddingJobNotFoundError",
    "EmbeddingJobReadService",
    "EmbeddingJobService",
    "EmbeddingQueueUnavailableError",
    "EmbeddingRecoveryPolicy",
    "EmbeddingRecoveryResult",
    "EmbeddingRecoveryService",
    "EmbeddingSourceNotFoundError",
    "EmbeddingSourceNotReviewedError",
    "EmbeddingWorkerService",
]
