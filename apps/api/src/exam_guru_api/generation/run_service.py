from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.blueprints.domain import BlueprintSlot, TaxonomyTarget
from exam_guru_api.blueprints.serialization import deserialize_blueprint
from exam_guru_api.core.config import MIN_GENERATION_WORKER_LEASE_SECONDS
from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.generation.domain import (
    CandidateDisposition,
    ContextProvenance,
    GeneratedQuestion,
    GenerationAccounting,
    GenerationContractError,
    GenerationIdentity,
    GenerationParameters,
    GenerationRequest,
    GenerationResult,
    GenerationVersions,
    ProvenanceContext,
    RetrievedContextItem,
)
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.models import (
    GenerationAttemptModel,
    GenerationAttemptStatus,
    GenerationJobModel,
    GenerationJobStatus,
    GenerationRunModel,
    GenerationRunStatus,
)
from exam_guru_api.generation.ports import GenerationProvider, ProviderError, ProviderFailureCode
from exam_guru_api.generation.repository import (
    GenerationAttemptAccounting,
    GenerationClaimRecord,
    GenerationContextRecord,
    GenerationRunWrite,
    SqlAlchemyGenerationRepository,
)
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    GenerationRuntimeUnavailableError,
    RegisteredGenerationConfig,
)
from exam_guru_api.generation.service import (
    GenerationBudgetExceededError,
    GenerationOrchestrationError,
    GenerationResultCache,
    GenerationRetry,
    GenerationRetryExhaustedError,
    GenerationService,
    RetryScheduler,
)
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.observability import OperationalTelemetry, get_operational_telemetry

_GENERATION_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/generation-runs")


class GenerationBlueprintNotFoundError(LookupError):
    pass


class GenerationSlotNotFoundError(LookupError):
    pass


class GenerationCurriculumNotFoundError(LookupError):
    pass


class GenerationCurriculumInactiveError(ValueError):
    pass


class GenerationBlueprintScopeMismatchError(ValueError):
    pass


class GenerationContextNotFoundError(LookupError):
    pass


class GenerationContextCrossCurriculumError(ValueError):
    pass


class GenerationContextNotReviewedError(ValueError):
    pass


class GenerationContextSourceUntrustedError(ValueError):
    pass


class GenerationContextTaxonomyMismatchError(ValueError):
    pass


class GenerationContextLimitError(ValueError):
    pass


class GenerationIdempotencyConflictError(RuntimeError):
    pass


class GenerationRetryStateError(RuntimeError):
    pass


class GenerationRetryLimitExceededError(RuntimeError):
    pass


class GenerationQueueUnavailableError(RuntimeError):
    pass


class GenerationRegisteredConfigMismatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GenerationCreationResult:
    run: GenerationRunModel
    job: GenerationJobModel
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class GenerationRecoveryPolicy:
    batch_size: int
    outbox_min_age_seconds: int
    worker_lease_seconds: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or not 1 <= self.batch_size <= 100
        ):
            raise ValueError("batch_size must be between 1 and 100")
        if (
            not isinstance(self.outbox_min_age_seconds, int)
            or isinstance(self.outbox_min_age_seconds, bool)
            or not 1 <= self.outbox_min_age_seconds <= 3_600
        ):
            raise ValueError("outbox_min_age_seconds must be between 1 and 3600")
        if (
            not isinstance(self.worker_lease_seconds, int)
            or isinstance(self.worker_lease_seconds, bool)
            or not MIN_GENERATION_WORKER_LEASE_SECONDS <= self.worker_lease_seconds <= 86_400
        ):
            raise ValueError(
                "worker_lease_seconds must exceed maximum generation execution and be bounded"
            )


@dataclass(frozen=True, slots=True)
class GenerationRecoveryResult:
    outbox_scanned: int
    outbox_dispatched: int
    outbox_failures: int
    claims_scanned: int
    claims_expired: int


class GenerationRunService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: GenerationRuntimeRegistry,
        dispatcher: GenerationDispatcher,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._dispatcher = dispatcher
        self._repository = SqlAlchemyGenerationRepository(session)

    async def create(
        self,
        curriculum_version_id: UUID,
        *,
        paper_blueprint_id: UUID,
        slot_id: str,
        knowledge_chunk_ids: tuple[UUID, ...],
        historical_question_ids: tuple[UUID, ...],
        idempotency_key: str,
        actor_id: UUID,
        retry_of_run_id: UUID | None = None,
    ) -> GenerationCreationResult:
        if (
            not idempotency_key
            or idempotency_key != idempotency_key.strip()
            or len(idempotency_key) > 128
            or any(
                character.isspace() or not character.isprintable() for character in idempotency_key
            )
        ):
            raise GenerationIdempotencyConflictError("invalid idempotency key")
        config = self._runtime.active_config
        scope = await self._repository.get_scope(curriculum_version_id)
        if scope is None:
            raise GenerationCurriculumNotFoundError(curriculum_version_id)
        if not (scope.curriculum_active and scope.exam_active and scope.medium_active):
            raise GenerationCurriculumInactiveError(curriculum_version_id)

        predecessor: GenerationRunModel | None = None
        if retry_of_run_id is not None:
            predecessor = await self._repository.get_run(
                curriculum_version_id,
                retry_of_run_id,
            )
            if predecessor.status != GenerationRunStatus.FAILED.value:
                raise GenerationRetryStateError(retry_of_run_id)
            if predecessor.retry_depth >= MAX_PROVIDER_JOB_RETRY_DEPTH:
                raise GenerationRetryLimitExceededError(retry_of_run_id)

        blueprint_model = await self._repository.get_blueprint(
            curriculum_version_id,
            paper_blueprint_id,
        )
        if blueprint_model is None:
            raise GenerationBlueprintNotFoundError(paper_blueprint_id)
        blueprint = deserialize_blueprint(blueprint_model.blueprint)
        if (
            blueprint.curriculum_scope.curriculum_version_id != curriculum_version_id
            or blueprint.curriculum_scope.grade != scope.grade
            or blueprint.curriculum_scope.medium != scope.medium
            or blueprint.version.blueprint_id != blueprint_model.blueprint_id
        ):
            raise GenerationBlueprintScopeMismatchError(paper_blueprint_id)
        slot = next((item for item in blueprint.slots if item.slot_id == slot_id), None)
        if slot is None:
            raise GenerationSlotNotFoundError(slot_id)

        canonical_chunk_ids = tuple(sorted(knowledge_chunk_ids, key=lambda value: value.int))
        canonical_question_ids = tuple(sorted(historical_question_ids, key=lambda value: value.int))
        records = await self._repository.list_context_records(
            canonical_chunk_ids,
            canonical_question_ids,
        )
        canonical_records = self._validate_context_records(
            curriculum_version_id,
            slot,
            canonical_chunk_ids,
            canonical_question_ids,
            records,
        )
        context, context_snapshot = _context_snapshot(canonical_records)
        del context
        slot_snapshot = _slot_snapshot(blueprint_model.blueprint, slot_id)

        parameters: dict[str, object] = {
            "temperature": config.parameters.temperature,
            "max_output_tokens": config.parameters.max_output_tokens,
            "seed": config.parameters.seed,
        }
        request_payload = {
            "paper_blueprint_id": str(paper_blueprint_id),
            "blueprint_version": blueprint.version.blueprint_id,
            "blueprint_snapshot": blueprint_model.blueprint,
            "slot_id": slot_id,
            "blueprint_slot_snapshot": slot_snapshot,
            "knowledge_chunk_ids": [str(value) for value in canonical_chunk_ids],
            "historical_question_ids": [str(value) for value in canonical_question_ids],
            "context_snapshot": context_snapshot,
            "versions": {
                "prompt_id": config.prompt.prompt_id,
                "prompt_version": config.prompt.version,
                "provider": config.provider,
                "provider_version": config.provider_version,
                "model": config.model,
                "model_version": config.model_version,
                "retrieval_version": config.retrieval_version,
                "schema_version": config.prompt.schema_version,
                "pricing_version": config.pricing.pricing_version,
            },
            "pricing": {
                "input_microusd_per_million_tokens": (
                    config.pricing.input_microusd_per_million_tokens
                ),
                "output_microusd_per_million_tokens": (
                    config.pricing.output_microusd_per_million_tokens
                ),
            },
            "parameters": parameters,
            "budgets": {
                "max_attempts": config.budgets.max_attempts,
                "max_input_tokens": config.budgets.max_total_input_tokens,
                "max_output_tokens": config.budgets.max_total_output_tokens,
                "max_cost_microusd": config.budgets.max_total_cost_microusd,
            },
        }
        request_fingerprint = _fingerprint(request_payload)
        idempotency_key_hash = _fingerprint(
            {"actor_id": str(actor_id), "idempotency_key": idempotency_key}
        )
        run_id = uuid5(_GENERATION_NAMESPACE, idempotency_key_hash)
        job_id = uuid5(_GENERATION_NAMESPACE, f"{run_id}:job")
        write = GenerationRunWrite(
            id=run_id,
            curriculum_version_id=curriculum_version_id,
            paper_blueprint_id=paper_blueprint_id,
            retry_of_run_id=retry_of_run_id,
            retry_depth=0 if predecessor is None else predecessor.retry_depth + 1,
            slot_id=slot_id,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
            blueprint_version=blueprint.version.blueprint_id,
            blueprint_snapshot=blueprint_model.blueprint,
            blueprint_slot_snapshot=slot_snapshot,
            knowledge_chunk_ids=[str(value) for value in canonical_chunk_ids],
            historical_question_ids=[str(value) for value in canonical_question_ids],
            context_snapshot=context_snapshot,
            prompt_id=config.prompt.prompt_id,
            prompt_version=config.prompt.version,
            provider=config.provider,
            provider_version=config.provider_version,
            model=config.model,
            model_version=config.model_version,
            retrieval_version=config.retrieval_version,
            schema_version=config.prompt.schema_version,
            pricing_version=config.pricing.pricing_version,
            input_microusd_per_million_tokens=(config.pricing.input_microusd_per_million_tokens),
            output_microusd_per_million_tokens=(config.pricing.output_microusd_per_million_tokens),
            generation_parameters=parameters,
            max_attempts=config.budgets.max_attempts,
            max_input_tokens=config.budgets.max_total_input_tokens,
            max_output_tokens=config.budgets.max_total_output_tokens,
            max_cost_microusd=config.budgets.max_total_cost_microusd,
            created_by=actor_id,
        )
        if predecessor is not None and not _same_generation_retry_request(predecessor, write):
            raise GenerationRetryStateError(predecessor.id)
        stored = await self._repository.store_run(write, job_id=job_id)
        if not stored.created and (
            stored.run.id != run_id
            or stored.run.curriculum_version_id != curriculum_version_id
            or stored.run.paper_blueprint_id != paper_blueprint_id
            or stored.run.slot_id != slot_id
            or stored.run.request_fingerprint != request_fingerprint
            or stored.run.retry_of_run_id != retry_of_run_id
            or stored.run.retry_depth != write.retry_depth
        ):
            raise GenerationIdempotencyConflictError(idempotency_key_hash)

        if stored.created:
            self._audit_created(stored.run)
            await self._session.commit()

        job = stored.job
        if job.status == GenerationJobStatus.QUEUED.value and job.queue_message_id is None:
            job = await self._dispatch_job(stored.run, job)
        return GenerationCreationResult(
            stored.run,
            job,
            deduplicated=not stored.created,
        )

    async def retry(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
        *,
        idempotency_key: str,
        actor_id: UUID,
    ) -> GenerationCreationResult:
        original = await self._repository.get_run(curriculum_version_id, run_id)
        if original.status != GenerationRunStatus.FAILED.value:
            raise GenerationRetryStateError(run_id)
        if original.retry_depth >= MAX_PROVIDER_JOB_RETRY_DEPTH:
            raise GenerationRetryLimitExceededError(run_id)
        return await self.create(
            curriculum_version_id,
            paper_blueprint_id=original.paper_blueprint_id,
            slot_id=original.slot_id,
            knowledge_chunk_ids=tuple(UUID(value) for value in original.knowledge_chunk_ids),
            historical_question_ids=tuple(
                UUID(value) for value in original.historical_question_ids
            ),
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            retry_of_run_id=original.id,
        )

    async def get_run(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
    ) -> GenerationRunModel:
        return await self._repository.get_run(curriculum_version_id, run_id)

    async def get_job(
        self,
        curriculum_version_id: UUID,
        job_id: UUID,
    ) -> GenerationJobModel:
        return await self._repository.get_job(curriculum_version_id, job_id)

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[GenerationRunModel, ...]:
        if await self._repository.get_scope(curriculum_version_id) is None:
            raise GenerationCurriculumNotFoundError(curriculum_version_id)
        return await self._repository.list_runs(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        )

    async def list_attempts(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[GenerationAttemptModel, ...]:
        return await self._repository.list_attempts(
            curriculum_version_id,
            run_id,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _validate_context_records(
        curriculum_version_id: UUID,
        slot: BlueprintSlot,
        chunk_ids: tuple[UUID, ...],
        question_ids: tuple[UUID, ...],
        records: tuple[GenerationContextRecord, ...],
    ) -> tuple[GenerationContextRecord, ...]:
        requested = {
            *(("knowledge_chunk", value) for value in chunk_ids),
            *(("historical_question", value) for value in question_ids),
        }
        found = {(record.record_kind, record.id) for record in records}
        if requested != found:
            raise GenerationContextNotFoundError(requested - found)
        ordered = tuple(sorted(records, key=lambda item: (item.record_kind, item.id.int)))
        for record in ordered:
            if (
                record.curriculum_version_id != curriculum_version_id
                or record.source_curriculum_version_id != curriculum_version_id
            ):
                raise GenerationContextCrossCurriculumError(record.id)
            if record.review_state is not ReviewState.REVIEWED:
                raise GenerationContextNotReviewedError(record.id)
            if (
                record.source_status is not ExtractionStatus.TRUSTED
                or record.source_block_id is None
            ):
                raise GenerationContextSourceUntrustedError(record.id)
            if not _taxonomy_matches(record, slot.taxonomy_target):
                raise GenerationContextTaxonomyMismatchError(record.id)
        return ordered

    async def _dispatch_job(
        self,
        run: GenerationRunModel,
        job: GenerationJobModel,
    ) -> GenerationJobModel:
        try:
            message_id = self._dispatcher.dispatch(job.id, run.id)
        except Exception as error:
            self._audit_dispatch_failed(run, job)
            await self._session.commit()
            raise GenerationQueueUnavailableError from error
        attached = await self._repository.attach_queue_message(job.id, message_id)
        await self._session.commit()
        return attached

    def _audit_created(self, run: GenerationRunModel) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=run.created_by,
                action=(
                    "generation_run.created"
                    if run.retry_of_run_id is None
                    else "generation_run.retry_created"
                ),
                resource_type="generation_run",
                resource_id=run.id,
                payload={
                    "curriculum_version_id": str(run.curriculum_version_id),
                    "paper_blueprint_id": str(run.paper_blueprint_id),
                    "retry_of_run_id": (
                        str(run.retry_of_run_id) if run.retry_of_run_id is not None else None
                    ),
                    "retry_depth": run.retry_depth,
                    "slot_id": run.slot_id,
                    "request_fingerprint": run.request_fingerprint,
                    "context_count": len(run.context_snapshot["items"]),  # type: ignore[arg-type]
                    "prompt_id": run.prompt_id,
                    "prompt_version": run.prompt_version,
                    "provider": run.provider,
                    "provider_version": run.provider_version,
                    "model": run.model,
                    "model_version": run.model_version,
                    "retrieval_version": run.retrieval_version,
                    "schema_version": run.schema_version,
                    "pricing_version": run.pricing_version,
                    "status": run.status,
                },
            )
        )

    def _audit_dispatch_failed(
        self,
        run: GenerationRunModel,
        job: GenerationJobModel,
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=run.created_by,
                action="generation_job.dispatch_failed",
                resource_type="generation_run",
                resource_id=run.id,
                payload={
                    "failure_code": "queue_dispatch_failed",
                    "job_id": str(job.id),
                },
            )
        )


def _same_generation_retry_request(
    predecessor: GenerationRunModel,
    candidate: GenerationRunWrite,
) -> bool:
    return (
        predecessor.curriculum_version_id == candidate.curriculum_version_id
        and predecessor.paper_blueprint_id == candidate.paper_blueprint_id
        and predecessor.slot_id == candidate.slot_id
        and predecessor.request_fingerprint == candidate.request_fingerprint
        and predecessor.blueprint_version == candidate.blueprint_version
        and predecessor.blueprint_snapshot == candidate.blueprint_snapshot
        and predecessor.blueprint_slot_snapshot == candidate.blueprint_slot_snapshot
        and predecessor.knowledge_chunk_ids == candidate.knowledge_chunk_ids
        and predecessor.historical_question_ids == candidate.historical_question_ids
        and predecessor.context_snapshot == candidate.context_snapshot
        and predecessor.prompt_id == candidate.prompt_id
        and predecessor.prompt_version == candidate.prompt_version
        and predecessor.provider == candidate.provider
        and predecessor.provider_version == candidate.provider_version
        and predecessor.model == candidate.model
        and predecessor.model_version == candidate.model_version
        and predecessor.retrieval_version == candidate.retrieval_version
        and predecessor.schema_version == candidate.schema_version
        and predecessor.pricing_version == candidate.pricing_version
        and predecessor.input_microusd_per_million_tokens
        == candidate.input_microusd_per_million_tokens
        and predecessor.output_microusd_per_million_tokens
        == candidate.output_microusd_per_million_tokens
        and predecessor.generation_parameters == candidate.generation_parameters
        and predecessor.max_attempts == candidate.max_attempts
        and predecessor.max_input_tokens == candidate.max_input_tokens
        and predecessor.max_output_tokens == candidate.max_output_tokens
        and predecessor.max_cost_microusd == candidate.max_cost_microusd
    )


class GenerationRecoveryService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: GenerationDispatcher,
        policy: GenerationRecoveryPolicy,
    ) -> None:
        if not isinstance(policy, GenerationRecoveryPolicy):
            raise TypeError("policy must be GenerationRecoveryPolicy")
        self._session = session
        self._dispatcher = dispatcher
        self._policy = policy
        self._repository = SqlAlchemyGenerationRepository(session)

    async def recover(self, *, now: datetime | None = None) -> GenerationRecoveryResult:
        active_now = datetime.now(UTC) if now is None else now
        outbox_jobs = await self._repository.lock_recoverable_outbox_jobs(
            created_before=active_now - timedelta(seconds=self._policy.outbox_min_age_seconds),
            limit=self._policy.batch_size,
        )
        outbox_dispatched = 0
        outbox_failures = 0
        for job in outbox_jobs:
            try:
                message_id = self._dispatcher.dispatch(job.id, job.generation_run_id)
            except Exception:
                outbox_failures += 1
                self._audit_redispatch_failed(job)
                continue
            await self._repository.attach_queue_message(job.id, message_id)
            outbox_dispatched += 1
            self._audit_redispatched(job)

        claims = await self._repository.lock_expired_claims(
            claimed_before=active_now - timedelta(seconds=self._policy.worker_lease_seconds),
            limit=self._policy.batch_size,
        )
        claims_expired = 0
        for claim in claims:
            accounting = await self._repository.get_attempt_accounting(claim.run.id)
            if await self._repository.expire_claim(
                claim,
                accounting,
                completed_at=active_now,
            ):
                claims_expired += 1
                self._audit_lease_expired(claim, accounting)

        await self._session.commit()
        return GenerationRecoveryResult(
            outbox_scanned=len(outbox_jobs),
            outbox_dispatched=outbox_dispatched,
            outbox_failures=outbox_failures,
            claims_scanned=len(claims),
            claims_expired=claims_expired,
        )

    def _audit_redispatched(self, job: GenerationJobModel) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action="generation_job.redispatched",
                resource_type="generation_run",
                resource_id=job.generation_run_id,
                payload={"job_id": str(job.id), "recovery": True},
            )
        )

    def _audit_redispatch_failed(self, job: GenerationJobModel) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action="generation_job.redispatch_failed",
                resource_type="generation_run",
                resource_id=job.generation_run_id,
                payload={
                    "failure_code": "queue_dispatch_failed",
                    "job_id": str(job.id),
                    "recovery": True,
                },
            )
        )

    def _audit_lease_expired(
        self,
        claim: GenerationClaimRecord,
        accounting: GenerationAttemptAccounting,
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=claim.run.created_by,
                action="generation_run.worker_lease_expired",
                resource_type="generation_run",
                resource_id=claim.run.id,
                payload={
                    "failure_code": "worker_lease_expired",
                    "job_id": str(claim.job.id),
                    "attempt_count": accounting.attempt_count,
                    "input_tokens": accounting.input_tokens,
                    "output_tokens": accounting.output_tokens,
                    "total_tokens": accounting.total_tokens,
                    "cost_microusd": accounting.cost_microusd,
                    "latency_ms": accounting.latency_ms,
                },
            )
        )


@dataclass(frozen=True, slots=True)
class _CompletedAttempt:
    identity: GenerationIdentity
    status: GenerationAttemptStatus
    failure_code: str | None
    retry_after_ms: int | None
    accounting: GenerationAccounting | None
    latency_ms: int
    candidate: dict[str, object] | None
    started_at: datetime
    completed_at: datetime


class _RecordingProvider:
    def __init__(self, provider: GenerationProvider) -> None:
        self._provider = provider
        self.completed: list[_CompletedAttempt] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started_at = datetime.now(UTC)
        started_ns = time.monotonic_ns()
        try:
            result = self._provider.generate(request)
        except ProviderError as error:
            completed_at = datetime.now(UTC)
            accounting_value = getattr(error, "accounting", None)
            accounting = (
                accounting_value if isinstance(accounting_value, GenerationAccounting) else None
            )
            self.completed.append(
                _CompletedAttempt(
                    identity=request.identity,
                    status=GenerationAttemptStatus.FAILED,
                    failure_code=error.code.value,
                    retry_after_ms=error.retry_after_ms,
                    accounting=accounting,
                    latency_ms=(
                        accounting.latency_ms
                        if accounting is not None
                        else _elapsed_latency_ms(started_ns)
                    ),
                    candidate=None,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
            raise
        except Exception as error:
            completed_at = datetime.now(UTC)
            sanitized = ProviderError(
                ProviderFailureCode.UNAVAILABLE,
                identity=request.identity,
            )
            self.completed.append(
                _CompletedAttempt(
                    identity=request.identity,
                    status=GenerationAttemptStatus.FAILED,
                    failure_code=sanitized.code.value,
                    retry_after_ms=None,
                    accounting=None,
                    latency_ms=_elapsed_latency_ms(started_ns),
                    candidate=None,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
            raise sanitized from error
        completed_at = datetime.now(UTC)
        self.completed.append(
            _CompletedAttempt(
                identity=request.identity,
                status=GenerationAttemptStatus.SUCCEEDED,
                failure_code=None,
                retry_after_ms=None,
                accounting=result.accounting,
                latency_ms=result.accounting.latency_ms,
                candidate=_candidate_snapshot(result.question),
                started_at=started_at,
                completed_at=completed_at,
            )
        )
        return result


class _RunResultCache:
    def __init__(self) -> None:
        self._result: GenerationResult | None = None

    def get(self, canonical_request: GenerationRequest) -> GenerationResult | None:
        del canonical_request
        return self._result

    def put_if_absent(
        self,
        canonical_request: GenerationRequest,
        result: GenerationResult,
    ) -> GenerationResult:
        del canonical_request
        if self._result is None:
            self._result = result
        return self._result


class _WorkerRetryScheduler:
    def __init__(self, sleep: Callable[[float], None]) -> None:
        self._sleep = sleep

    def schedule(self, retry: GenerationRetry) -> None:
        self._sleep(retry.delay_ms / 1_000)


class _DeterministicAttemptIds:
    def __init__(self, run_id: UUID) -> None:
        self._run_id = run_id
        self._next_attempt_number = 2

    def __call__(self) -> UUID:
        attempt_id = _attempt_id(self._run_id, self._next_attempt_number)
        self._next_attempt_number += 1
        return attempt_id


class GenerationWorkerService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: GenerationRuntimeRegistry,
        *,
        sleep: Callable[[float], None] = time.sleep,
        telemetry: OperationalTelemetry | None = None,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._sleep = sleep
        self._telemetry = telemetry or get_operational_telemetry()
        self._repository = SqlAlchemyGenerationRepository(session)

    async def process(self, job_id: UUID, run_id: UUID) -> bool:
        run = await self._claim(job_id, run_id)
        if run is None:
            return False

        recorder: _RecordingProvider | None = None
        result: GenerationResult | None = None
        failure_code: str | None = None
        try:
            config = self._matching_config(run)
            request = _generation_request(run, config)
            recorder = _RecordingProvider(self._runtime.build_provider(config))
            cache: GenerationResultCache = _RunResultCache()
            scheduler: RetryScheduler = _WorkerRetryScheduler(self._sleep)
            result = GenerationService(
                request_factory=lambda: request,
                provider=recorder,
                result_cache=cache,
                retry_scheduler=scheduler,
                config=config.budgets,
                attempt_id_factory=_DeterministicAttemptIds(run.id),
            ).generate()
        except GenerationBudgetExceededError as error:
            failure_code = f"budget_exceeded_{error.dimension.value}"
        except GenerationRetryExhaustedError:
            failure_code = "provider_retries_exhausted"
        except ProviderError as error:
            failure_code = f"provider_{error.code.value}"
        except GenerationOrchestrationError:
            failure_code = "generation_orchestration_failed"
        except (GenerationRegisteredConfigMismatchError, GenerationRuntimeUnavailableError):
            failure_code = "generation_config_unavailable"
        except Exception:
            failure_code = "generation_internal_error"

        completed = () if recorder is None else tuple(recorder.completed)
        completion = await self._complete(
            run,
            job_id,
            completed,
            result=result,
            failure_code=failure_code,
        )
        return completion is not False

    async def _claim(self, job_id: UUID, run_id: UUID) -> GenerationRunModel | None:
        now = datetime.now(UTC)
        run = await self._session.scalar(
            update(GenerationRunModel)
            .where(
                GenerationRunModel.id == run_id,
                GenerationRunModel.status == GenerationRunStatus.PENDING.value,
                GenerationRunModel.version == 0,
            )
            .values(
                status=GenerationRunStatus.RUNNING.value,
                version=1,
                started_at=now,
            )
            .returning(GenerationRunModel)
        )
        if run is None:
            await self._session.rollback()
            return None
        job = await self._session.scalar(
            update(GenerationJobModel)
            .where(
                GenerationJobModel.id == job_id,
                GenerationJobModel.generation_run_id == run_id,
                GenerationJobModel.status == GenerationJobStatus.QUEUED.value,
            )
            .values(
                status=GenerationJobStatus.CLAIMED.value,
                version=GenerationJobModel.version + 1,
                claimed_at=now,
            )
            .returning(GenerationJobModel)
        )
        if job is None:
            await self._session.rollback()
            return None
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=run.created_by,
                action="generation_run.started",
                resource_type="generation_run",
                resource_id=run.id,
                payload={"job_id": str(job.id), "version": run.version},
            )
        )
        await self._session.commit()
        return run

    def _matching_config(self, run: GenerationRunModel) -> RegisteredGenerationConfig:
        config = self._runtime.active_config
        parameters = config.parameters
        budgets = config.budgets
        if (
            run.prompt_id != config.prompt.prompt_id
            or run.prompt_version != config.prompt.version
            or run.schema_version != config.prompt.schema_version
            or run.provider != config.provider
            or run.provider_version != config.provider_version
            or run.model != config.model
            or run.model_version != config.model_version
            or run.retrieval_version != config.retrieval_version
            or run.pricing_version != config.pricing.pricing_version
            or run.input_microusd_per_million_tokens
            != config.pricing.input_microusd_per_million_tokens
            or run.output_microusd_per_million_tokens
            != config.pricing.output_microusd_per_million_tokens
            or run.generation_parameters
            != {
                "temperature": parameters.temperature,
                "max_output_tokens": parameters.max_output_tokens,
                "seed": parameters.seed,
            }
            or run.max_attempts != budgets.max_attempts
            or run.max_input_tokens != budgets.max_total_input_tokens
            or run.max_output_tokens != budgets.max_total_output_tokens
            or run.max_cost_microusd != budgets.max_total_cost_microusd
        ):
            raise GenerationRegisteredConfigMismatchError(
                "persisted generation configuration is no longer registered"
            )
        return config

    async def _complete(
        self,
        run: GenerationRunModel,
        job_id: UUID,
        completed: tuple[_CompletedAttempt, ...],
        *,
        result: GenerationResult | None,
        failure_code: str | None,
    ) -> bool:
        active = await self._repository.lock_active_completion(run.id, job_id)
        if active is None:
            await self._session.rollback()
            return False
        run = active.run
        for item in completed:
            accounting = item.accounting
            attempt = GenerationAttemptModel(
                id=item.identity.attempt_id,
                generation_run_id=run.id,
                attempt_number=item.identity.attempt_number,
                retry_of_attempt_id=item.identity.retry_of_attempt_id,
                provider_idempotency_key=item.identity.idempotency_key,
                status=item.status.value,
                failure_code=item.failure_code,
                retry_after_ms=item.retry_after_ms,
                accounting_known=accounting is not None,
                input_tokens=None if accounting is None else accounting.input_tokens,
                output_tokens=None if accounting is None else accounting.output_tokens,
                total_tokens=None if accounting is None else accounting.total_tokens,
                cost_microusd=None if accounting is None else accounting.cost_microusd,
                latency_ms=item.latency_ms,
                candidate=item.candidate,
                disposition=(
                    CandidateDisposition.REQUIRES_VALIDATION.value
                    if item.status is GenerationAttemptStatus.SUCCEEDED
                    else None
                ),
                started_at=item.started_at,
                completed_at=item.completed_at,
            )
            self._session.add(attempt)
            await self._session.flush()
            self._session.add(
                AdminAuditEventModel(
                    id=uuid4(),
                    actor_id=run.created_by,
                    action="generation_attempt.completed",
                    resource_type="generation_run",
                    resource_id=run.id,
                    payload={
                        "attempt_id": str(attempt.id),
                        "attempt_number": attempt.attempt_number,
                        "retry_of_attempt_id": (
                            str(attempt.retry_of_attempt_id)
                            if attempt.retry_of_attempt_id is not None
                            else None
                        ),
                        "status": attempt.status,
                        "failure_code": attempt.failure_code,
                        "accounting_known": attempt.accounting_known,
                    },
                )
            )

        input_tokens = sum(
            item.accounting.input_tokens for item in completed if item.accounting is not None
        )
        output_tokens = sum(
            item.accounting.output_tokens for item in completed if item.accounting is not None
        )
        cost_microusd = sum(
            item.accounting.cost_microusd for item in completed if item.accounting is not None
        )
        latency_ms = sum(item.latency_ms for item in completed)
        now = datetime.now(UTC)
        succeeded = result is not None and failure_code is None
        terminal_run = await self._session.scalar(
            update(GenerationRunModel)
            .where(
                GenerationRunModel.id == run.id,
                GenerationRunModel.status == GenerationRunStatus.RUNNING.value,
                GenerationRunModel.version == run.version,
            )
            .values(
                status=(
                    GenerationRunStatus.SUCCEEDED.value
                    if succeeded
                    else GenerationRunStatus.FAILED.value
                ),
                version=2,
                completed_at=now,
                failure_code=None if succeeded else (failure_code or "generation_internal_error"),
                result_attempt_id=(
                    result.request.identity.attempt_id if result is not None and succeeded else None
                ),
                attempt_count=len(completed),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cost_microusd=cost_microusd,
                latency_ms=latency_ms,
                candidate=(
                    _candidate_snapshot(result.question)
                    if result is not None and succeeded
                    else None
                ),
                disposition=(CandidateDisposition.REQUIRES_VALIDATION.value if succeeded else None),
            )
            .returning(GenerationRunModel)
        )
        terminal_job = await self._session.scalar(
            update(GenerationJobModel)
            .where(
                GenerationJobModel.id == job_id,
                GenerationJobModel.generation_run_id == run.id,
                GenerationJobModel.status == GenerationJobStatus.CLAIMED.value,
                GenerationJobModel.version == active.job.version,
            )
            .values(
                status=(
                    GenerationJobStatus.SUCCEEDED.value
                    if succeeded
                    else GenerationJobStatus.FAILED.value
                ),
                version=GenerationJobModel.version + 1,
                completed_at=now,
                failure_code=None if succeeded else (failure_code or "generation_internal_error"),
            )
            .returning(GenerationJobModel)
        )
        if terminal_run is None or terminal_job is None:
            await self._session.rollback()
            raise RuntimeError("generation completion lost its CAS race")
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=run.created_by,
                action=("generation_run.succeeded" if succeeded else "generation_run.failed"),
                resource_type="generation_run",
                resource_id=run.id,
                payload={
                    "job_id": str(job_id),
                    "status": terminal_run.status,
                    "failure_code": terminal_run.failure_code,
                    "attempt_count": terminal_run.attempt_count,
                    "input_tokens": terminal_run.input_tokens,
                    "output_tokens": terminal_run.output_tokens,
                    "total_tokens": terminal_run.total_tokens,
                    "cost_microusd": terminal_run.cost_microusd,
                    "latency_ms": terminal_run.latency_ms,
                    "disposition": terminal_run.disposition,
                },
            )
        )
        await self._session.commit()
        self._telemetry.generation_terminal(
            status=terminal_run.status,
            failure_code=terminal_run.failure_code,
            attempt_count=terminal_run.attempt_count,
            input_tokens=terminal_run.input_tokens,
            output_tokens=terminal_run.output_tokens,
            total_tokens=terminal_run.total_tokens,
            cost_microusd=terminal_run.cost_microusd,
            latency_ms=terminal_run.latency_ms,
        )
        return True


def _context_snapshot(
    records: tuple[GenerationContextRecord, ...],
) -> tuple[ProvenanceContext, dict[str, object]]:
    items: list[RetrievedContextItem] = []
    snapshots: list[dict[str, object]] = []
    for record in records:
        context_id = f"{record.record_kind}:{record.id}"
        provenance = ContextProvenance(
            source_document_id=str(record.source_document_id),
            source_version=f"sha256:{record.source_checksum_sha256}",
            page_number=record.page_number,
            chunk_id=str(record.id),
        )
        items.append(
            RetrievedContextItem(
                context_id=context_id,
                text=record.text,
                provenance=provenance,
            )
        )
        snapshots.append(
            {
                "context_id": context_id,
                "record_kind": record.record_kind,
                "record_id": str(record.id),
                "record_version": record.version,
                "text": record.text,
                "trust": "untrusted_data",
                "provenance": {
                    "source_document_id": provenance.source_document_id,
                    "source_version": provenance.source_version,
                    "page_number": provenance.page_number,
                    "chunk_id": provenance.chunk_id,
                    "source_block_id": str(record.source_block_id),
                },
                "taxonomy": {
                    "competency_id": _optional_uuid(record.competency_id),
                    "skill_id": _optional_uuid(record.skill_id),
                    "sub_skill_id": _optional_uuid(record.sub_skill_id),
                    "learning_concept_id": _optional_uuid(record.learning_concept_id),
                },
            }
        )
    try:
        context = ProvenanceContext(items=tuple(items))
    except GenerationContractError as error:
        raise GenerationContextLimitError from error
    return context, {"items": snapshots, "trust": "untrusted_data"}


def _slot_snapshot(blueprint_snapshot: dict[str, object], slot_id: str) -> dict[str, object]:
    slots = blueprint_snapshot.get("slots")
    if not isinstance(slots, list):
        raise GenerationBlueprintScopeMismatchError(slot_id)
    for item in slots:
        if isinstance(item, dict) and item.get("slot_id") == slot_id:
            return cast(dict[str, object], item)
    raise GenerationSlotNotFoundError(slot_id)


def _taxonomy_matches(record: GenerationContextRecord, target: TaxonomyTarget) -> bool:
    return (
        record.competency_id == target.competency_id
        and (target.skill_id is None or record.skill_id == target.skill_id)
        and (target.sub_skill_id is None or record.sub_skill_id == target.sub_skill_id)
        and (
            target.learning_concept_id is None
            or record.learning_concept_id == target.learning_concept_id
        )
    )


def _optional_uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _generation_request(
    run: GenerationRunModel,
    config: RegisteredGenerationConfig,
) -> GenerationRequest:
    blueprint = deserialize_blueprint(run.blueprint_snapshot)
    if blueprint.version.blueprint_id != run.blueprint_version:
        raise GenerationContractError("persisted blueprint version does not match its snapshot")
    slot = next((item for item in blueprint.slots if item.slot_id == run.slot_id), None)
    if slot is None:
        raise GenerationContractError("persisted generation slot is absent from its blueprint")
    return GenerationRequest(
        identity=GenerationIdentity(
            generation_id=run.id,
            attempt_id=_attempt_id(run.id, 1),
            idempotency_key=f"generation-{run.id.hex}",
            attempt_number=1,
        ),
        blueprint_version=blueprint.version,
        blueprint_slot=slot,
        context=_persisted_context(run.context_snapshot),
        versions=GenerationVersions(
            blueprint_version=run.blueprint_version,
            prompt_id=run.prompt_id,
            prompt_version=run.prompt_version,
            provider=run.provider,
            provider_version=run.provider_version,
            model=run.model,
            model_version=run.model_version,
            retrieval_version=run.retrieval_version,
            schema_version=run.schema_version,
        ),
        parameters=GenerationParameters(
            temperature=cast(float, run.generation_parameters["temperature"]),
            max_output_tokens=cast(int, run.generation_parameters["max_output_tokens"]),
            seed=cast(int | None, run.generation_parameters["seed"]),
        ),
    )


def _persisted_context(snapshot: dict[str, object]) -> ProvenanceContext:
    raw_items = snapshot.get("items")
    if snapshot.get("trust") != "untrusted_data" or not isinstance(raw_items, list):
        raise GenerationContractError("persisted context snapshot is malformed")
    items: list[RetrievedContextItem] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise GenerationContractError("persisted context item is malformed")
        provenance = raw_item.get("provenance")
        if not isinstance(provenance, dict):
            raise GenerationContractError("persisted context provenance is malformed")
        items.append(
            RetrievedContextItem(
                context_id=cast(str, raw_item["context_id"]),
                text=cast(str, raw_item["text"]),
                provenance=ContextProvenance(
                    source_document_id=cast(str, provenance["source_document_id"]),
                    source_version=cast(str, provenance["source_version"]),
                    page_number=cast(int, provenance["page_number"]),
                    chunk_id=cast(str, provenance["chunk_id"]),
                ),
            )
        )
    return ProvenanceContext(items=tuple(items))


def _candidate_snapshot(question: GeneratedQuestion) -> dict[str, object]:
    return {
        "question_type": question.question_type.value,
        "stem": question.stem,
        "options": [
            {"option_id": option.option_id, "text": option.text} for option in question.options
        ],
        "answer": {
            "explanation": question.answer.explanation,
            "correct_option_id": question.answer.correct_option_id,
            "accepted_responses": list(question.answer.accepted_responses),
        },
        "marking": {
            "total_marks": question.marking.total_marks,
            "criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                    "marks": criterion.marks,
                }
                for criterion in question.marking.criteria
            ],
        },
    }


def _attempt_id(run_id: UUID, attempt_number: int) -> UUID:
    return uuid5(_GENERATION_NAMESPACE, f"{run_id}:attempt:{attempt_number}")


def _elapsed_latency_ms(started_ns: int) -> int:
    return min(259_200_000, max(0, (time.monotonic_ns() - started_ns) // 1_000_000))
