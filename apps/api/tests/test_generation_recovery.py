import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.generation.models import GenerationJobModel, GenerationRunModel
from exam_guru_api.generation.repository import (
    GenerationAttemptAccounting,
    GenerationClaimRecord,
)
from exam_guru_api.generation.run_service import (
    GenerationRecoveryPolicy,
    GenerationRecoveryService,
)
from tests.test_generation_repository import job_model, run_model

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class RecoverySession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class RecoveryRepository:
    def __init__(
        self,
        outbox_jobs: tuple[GenerationJobModel, ...],
        claims: tuple[GenerationClaimRecord, ...],
        accounting: GenerationAttemptAccounting,
        *,
        expire_result: bool = True,
    ) -> None:
        self.outbox_jobs = outbox_jobs
        self.claims = claims
        self.accounting = accounting
        self.expire_result = expire_result
        self.outbox_calls: list[tuple[datetime, int]] = []
        self.claim_calls: list[tuple[datetime, int]] = []
        self.attachments: list[tuple[UUID, str]] = []
        self.expirations: list[
            tuple[GenerationRunModel, GenerationJobModel, GenerationAttemptAccounting, datetime]
        ] = []

    async def lock_recoverable_outbox_jobs(
        self,
        *,
        created_before: datetime,
        limit: int,
    ) -> tuple[GenerationJobModel, ...]:
        self.outbox_calls.append((created_before, limit))
        return self.outbox_jobs

    async def attach_queue_message(self, job_id: UUID, message_id: str) -> GenerationJobModel:
        self.attachments.append((job_id, message_id))
        job = next(item for item in self.outbox_jobs if item.id == job_id)
        job.queue_message_id = message_id
        job.version += 1
        return job

    async def lock_expired_claims(
        self,
        *,
        claimed_before: datetime,
        limit: int,
    ) -> tuple[GenerationClaimRecord, ...]:
        self.claim_calls.append((claimed_before, limit))
        return self.claims

    async def get_attempt_accounting(self, run_id: UUID) -> GenerationAttemptAccounting:
        del run_id
        return self.accounting

    async def expire_claim(
        self,
        claim: GenerationClaimRecord,
        accounting: GenerationAttemptAccounting,
        *,
        completed_at: datetime,
    ) -> bool:
        self.expirations.append((claim.run, claim.job, accounting, completed_at))
        return self.expire_result


class PartiallyFailingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def dispatch(self, job_id: UUID, run_id: UUID) -> str:
        self.calls.append((job_id, run_id))
        if len(self.calls) == 2:
            raise RuntimeError("raw valkey credential and transport failure")
        return f"message-{job_id}"


def queued_job(identifier: int, run_identifier: int) -> GenerationJobModel:
    value = job_model()
    value.id = UUID(int=identifier)
    value.generation_run_id = UUID(int=run_identifier)
    value.created_at = NOW - timedelta(minutes=5)
    return value


def stale_claim() -> GenerationClaimRecord:
    run = run_model()
    run.status = "running"
    run.version = 1
    run.started_at = NOW - timedelta(minutes=20)
    job = job_model()
    job.status = "claimed"
    job.version = 2
    job.claimed_at = NOW - timedelta(minutes=20)
    return GenerationClaimRecord(run=run, job=job)


def test_recovery_policy_rejects_unbounded_batch_age_or_unsafe_lease() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        GenerationRecoveryPolicy(batch_size=0, outbox_min_age_seconds=5, worker_lease_seconds=600)
    with pytest.raises(ValueError, match="outbox_min_age_seconds"):
        GenerationRecoveryPolicy(batch_size=10, outbox_min_age_seconds=0, worker_lease_seconds=600)
    with pytest.raises(ValueError, match="worker_lease_seconds"):
        GenerationRecoveryPolicy(batch_size=10, outbox_min_age_seconds=5, worker_lease_seconds=364)


def test_recovery_service_redrives_outbox_expires_stale_claims_and_audits_safely() -> None:
    async def exercise() -> None:
        first = queued_job(980_001, 980_101)
        second = queued_job(980_002, 980_102)
        claim = stale_claim()
        accounting = GenerationAttemptAccounting(
            attempt_count=1,
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
            cost_microusd=7,
            latency_ms=9,
        )
        repository = RecoveryRepository((first, second), (claim,), accounting)
        dispatcher = PartiallyFailingDispatcher()
        session = RecoverySession()
        policy = GenerationRecoveryPolicy(
            batch_size=12,
            outbox_min_age_seconds=7,
            worker_lease_seconds=600,
        )
        service = GenerationRecoveryService(
            cast(AsyncSession, session),
            dispatcher,
            policy,
        )
        service._repository = cast(object, repository)  # type: ignore[assignment]

        result = await service.recover(now=NOW)

        assert result.outbox_scanned == 2
        assert result.outbox_dispatched == 1
        assert result.outbox_failures == 1
        assert result.claims_scanned == 1
        assert result.claims_expired == 1
        assert repository.outbox_calls == [(NOW - timedelta(seconds=7), 12)]
        assert repository.claim_calls == [(NOW - timedelta(seconds=600), 12)]
        assert repository.attachments == [(first.id, f"message-{first.id}")]
        assert repository.expirations == [(claim.run, claim.job, accounting, NOW)]
        assert session.commits == 1

        audits = [item for item in session.added if isinstance(item, AdminAuditEventModel)]
        assert [item.action for item in audits] == [
            "generation_job.redispatched",
            "generation_job.redispatch_failed",
            "generation_run.worker_lease_expired",
        ]
        assert audits[1].payload == {
            "failure_code": "queue_dispatch_failed",
            "job_id": str(second.id),
            "recovery": True,
        }
        assert audits[2].payload["failure_code"] == "worker_lease_expired"
        assert audits[2].payload["attempt_count"] == 1
        assert "credential" not in str(audits[1].payload)

    asyncio.run(exercise())


def test_recovery_service_rejects_wrong_policy_and_ignores_lost_expiration_cas() -> None:
    session = RecoverySession()
    dispatcher = PartiallyFailingDispatcher()
    with pytest.raises(TypeError, match="GenerationRecoveryPolicy"):
        GenerationRecoveryService(
            cast(AsyncSession, session),
            dispatcher,
            cast(GenerationRecoveryPolicy, object()),
        )

    async def exercise() -> None:
        claim = stale_claim()
        accounting = GenerationAttemptAccounting(0, 0, 0, 0, 0, 0)
        repository = RecoveryRepository((), (claim,), accounting, expire_result=False)
        service = GenerationRecoveryService(
            cast(AsyncSession, session),
            dispatcher,
            GenerationRecoveryPolicy(10, 5, 600),
        )
        service._repository = cast(object, repository)  # type: ignore[assignment]

        result = await service.recover()

        assert result.claims_scanned == 1
        assert result.claims_expired == 0
        assert not any(
            isinstance(item, AdminAuditEventModel)
            and item.action == "generation_run.worker_lease_expired"
            for item in session.added
        )

    asyncio.run(exercise())
