import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import anyio
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import and_, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.understanding_contracts import _canonical_bytes, _canonical_json
from exam_guru_api.documents.understanding_models import TrustedPageKnowledgeModel
from exam_guru_api.documents.understanding_verification import TrustedPageKnowledge
from exam_guru_api.infrastructure.resources import create_resources
from exam_guru_api.knowledge.preparation_models import (
    KnowledgePreparationJobModel,
    MaterialKnowledgeRequestModel,
)
from exam_guru_api.knowledge.unit_service import KnowledgePreparationError, KnowledgeUnitService
from exam_guru_api.knowledge.units import KnowledgeScope

PREPARATION_QUEUE_NAME = "material-knowledge-preparation"
PREPARATION_TIME_LIMIT_MS = 120_000
DERIVATION_VERSION = "page-region-components.v1"
TRANSFORMATION_VERSION = "source-observation-meaning.v1"
_TERMINAL = frozenset({"succeeded", "failed", "superseded"})
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KnowledgePreparationJobSnapshot:
    id: UUID
    document_id: UUID
    page_number: int
    status: str
    version: int
    attempts: int
    lease_token: UUID | None
    lease_expires_at: datetime | None
    failure_code: str | None
    unit_count: int | None
    projection_count: int | None
    claimed: bool = False


def _snapshot(job: KnowledgePreparationJobModel) -> KnowledgePreparationJobSnapshot:
    return KnowledgePreparationJobSnapshot(
        job.id,
        job.document_id,
        job.page_number,
        job.status,
        job.version,
        job.attempts,
        job.lease_token,
        job.lease_expires_at,
        job.failure_code,
        job.unit_count,
        job.projection_count,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _limit(batch_size: int) -> None:
    if type(batch_size) is not int or not 1 <= batch_size <= 8:
        raise ValueError("knowledge preparation discovery limit must be between one and eight")


def _fingerprint(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _input(trusted: TrustedPageKnowledgeModel, scope: KnowledgeScope) -> dict[str, object]:
    page = TrustedPageKnowledge.model_validate_json(json.dumps(trusted.payload, ensure_ascii=False))
    if (
        page.id != trusted.id
        or hashlib.sha256(_canonical_bytes(page)).hexdigest() != trusted.fingerprint
    ):
        raise KnowledgePreparationError("trusted_page_fingerprint_invalid")
    return {
        "schema_version": "knowledge-preparation-input.v1",
        "document_id": str(trusted.document_id),
        "page_number": trusted.page_number,
        "source_sha256": page.source.source_sha256,
        "trusted_page_id": str(trusted.id),
        "trusted_page_fingerprint": trusted.fingerprint,
        "scope": scope.model_dump(mode="json"),
        "derivation_version": DERIVATION_VERSION,
        "transformation_version": TRANSFORMATION_VERSION,
    }


def _audit(job: KnowledgePreparationJobModel) -> AdminAuditEventModel:
    return AdminAuditEventModel(
        id=uuid4(),
        actor_id=job.requested_by,
        resource_type="knowledge_preparation_job",
        resource_id=job.document_id,
        action=f"knowledge_preparation.{job.status}",
        payload={
            "job_id": str(job.id),
            "request_id": str(job.request_id),
            "source_sha256": job.source_sha256,
            "source_audit_event_id": str(job.source_audit_event_id),
            "page_number": job.page_number,
            "trusted_page_id": str(job.trusted_page_id),
            "input_fingerprint": job.input_fingerprint,
            "scope_fingerprint": job.scope_fingerprint,
            "version": job.version,
            "status": job.status,
            "attempts": job.attempts,
            "lease_token": None if job.lease_token is None else str(job.lease_token),
            "failure_code": job.failure_code,
            "unit_count": job.unit_count,
            "projection_count": job.projection_count,
        },
    )


async def _source_lock(session: AsyncSession, document_id: UUID) -> None:
    await session.execute(text("SET LOCAL lock_timeout = '10s'"))
    await session.execute(text("SET LOCAL statement_timeout = '90s'"))
    await session.execute(select(func.lock_knowledge_unit_source(document_id)))


async def _locked(session: AsyncSession, job_id: UUID) -> KnowledgePreparationJobModel:
    document_id = await session.scalar(
        select(KnowledgePreparationJobModel.document_id).where(
            KnowledgePreparationJobModel.id == job_id
        )
    )
    if document_id is None:
        raise KnowledgePreparationError("knowledge_preparation_job_not_found")
    await _source_lock(session, document_id)
    job = await session.scalar(
        select(KnowledgePreparationJobModel)
        .where(KnowledgePreparationJobModel.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise KnowledgePreparationError("knowledge_preparation_job_not_found")
    return job


async def _eligibility(session: AsyncSession, job: KnowledgePreparationJobModel) -> str:
    scope = KnowledgeScope.model_validate_json(
        json.dumps(job.input_snapshot["scope"], ensure_ascii=False)
    )
    if (
        job.input_fingerprint != _fingerprint(job.input_snapshot)
        or job.scope_fingerprint != hashlib.sha256(_canonical_bytes(scope)).hexdigest()
    ):
        raise KnowledgePreparationError("knowledge_preparation_input_invalid")
    return cast(
        str,
        await session.scalar(
            select(
                func.knowledge_preparation_input_state(
                    job.trusted_page_id, literal(job.input_snapshot["scope"], type_=JSONB)
                )
            )
        ),
    )


async def _transition(
    session: AsyncSession,
    job: KnowledgePreparationJobModel,
    status: str,
    *,
    code: str | None = None,
    consume_attempt: bool = False,
    lease_seconds: int = 300,
    unit_count: int | None = None,
    projection_count: int | None = None,
) -> KnowledgePreparationJobSnapshot:
    job.status = status
    job.version += 1
    job.attempts += int(consume_attempt)
    job.updated_at = _now()
    job.failure_code = code
    job.lease_token = uuid4() if status == "running" else None
    job.lease_expires_at = (
        job.updated_at + timedelta(seconds=lease_seconds) if status == "running" else None
    )
    job.completed_at = job.updated_at if status in _TERMINAL else None
    job.unit_count, job.projection_count = unit_count, projection_count
    audit = _audit(job)
    session.add(audit)
    job.audit_event_id = audit.id
    await session.flush()
    return _snapshot(job)


async def discover_knowledge_preparation(
    session: AsyncSession, *, batch_size: int = 8
) -> tuple[UUID, ...]:
    _limit(batch_size)
    await session.execute(text("SET LOCAL statement_timeout = '90s'"))
    candidates = (
        (
            await session.execute(
                text("""
        WITH scopes AS MATERIALIZED (
            SELECT r.id AS request_id,r.document_id,
                public.knowledge_scope_snapshot(r.document_id) AS scope
            FROM public.material_knowledge_requests r
            WHERE public.source_understanding_document_is_resolved(r.document_id)
        )
        SELECT s.request_id,s.document_id,p.current_trusted_id AS trusted_id
        FROM scopes s JOIN public.source_understanding_pages p ON p.document_id=s.document_id
        WHERE s.scope IS NOT NULL AND p.state='verified'
            AND public.trusted_page_knowledge_is_current(p.current_trusted_id)
            AND NOT EXISTS (SELECT 1 FROM public.knowledge_preparation_jobs j
                WHERE j.trusted_page_id=p.current_trusted_id
                    AND j.scope_fingerprint=public.source_understanding_fingerprint(s.scope)
                    AND j.derivation_version=:derivation
                    AND j.transformation_version=:transformation)
        ORDER BY s.document_id,p.page_number LIMIT :batch_size
    """),
                {
                    "batch_size": batch_size,
                    "derivation": DERIVATION_VERSION,
                    "transformation": TRANSFORMATION_VERSION,
                },
            )
        )
        .mappings()
        .all()
    )
    await session.commit()
    created: list[UUID] = []
    for candidate in candidates:
        try:
            await _source_lock(session, candidate["document_id"])
            scope_payload = await session.scalar(
                select(func.knowledge_scope_snapshot(candidate["document_id"]))
            )
            if (
                scope_payload is None
                or await session.scalar(
                    select(
                        func.knowledge_preparation_input_state(
                            candidate["trusted_id"], literal(scope_payload, type_=JSONB)
                        )
                    )
                )
                != "eligible"
            ):
                await session.commit()
                continue
            scope = KnowledgeScope.model_validate_json(
                json.dumps(scope_payload, ensure_ascii=False)
            )
            scope_fingerprint = hashlib.sha256(_canonical_bytes(scope)).hexdigest()
            existing = await session.scalar(
                select(KnowledgePreparationJobModel.id).where(
                    KnowledgePreparationJobModel.trusted_page_id == candidate["trusted_id"],
                    KnowledgePreparationJobModel.scope_fingerprint == scope_fingerprint,
                    KnowledgePreparationJobModel.derivation_version == DERIVATION_VERSION,
                    KnowledgePreparationJobModel.transformation_version == TRANSFORMATION_VERSION,
                )
            )
            if existing is not None:
                await session.commit()
                continue
            request = await session.get(MaterialKnowledgeRequestModel, candidate["request_id"])
            trusted = await session.get(TrustedPageKnowledgeModel, candidate["trusted_id"])
            if request is None or trusted is None:
                raise KnowledgePreparationError("knowledge_preparation_input_unavailable")
            value = _input(trusted, scope)
            job = KnowledgePreparationJobModel(
                id=uuid4(),
                request_id=request.id,
                document_id=request.document_id,
                page_number=trusted.page_number,
                source_sha256=request.source_sha256,
                requested_by=request.requested_by,
                trusted_page_id=trusted.id,
                candidate_id=trusted.candidate_id,
                source_audit_event_id=trusted.audit_event_id,
                scope_fingerprint=scope_fingerprint,
                derivation_version=DERIVATION_VERSION,
                transformation_version=TRANSFORMATION_VERSION,
                input_snapshot=value,
                input_fingerprint=_fingerprint(value),
                status="queued",
                version=0,
                attempts=0,
            )
            audit = _audit(job)
            session.add(audit)
            job.audit_event_id = audit.id
            session.add(job)
            await session.commit()
            created.append(job.id)
        except Exception:
            await session.rollback()
            raise
    return tuple(created)


async def _defer(
    session: AsyncSession, job: KnowledgePreparationJobModel, eligibility: str
) -> KnowledgePreparationJobSnapshot:
    result = await _transition(
        session,
        job,
        eligibility,
        code="knowledge_preparation_superseded"
        if eligibility == "superseded"
        else "knowledge_preparation_unavailable",
    )
    await session.commit()
    return result


async def _claim(
    session: AsyncSession, job_id: UUID, *, lease_seconds: int
) -> KnowledgePreparationJobSnapshot:
    job = await _locked(session, job_id)
    if job.status not in {"queued", "deferred"}:
        result = _snapshot(job)
        await session.commit()
        return result
    eligibility = await _eligibility(session, job)
    if eligibility != "eligible":
        return await _defer(session, job, eligibility)
    result = await _transition(session, job, "running", lease_seconds=lease_seconds)
    await session.commit()
    return replace(result, claimed=True)


def _owns(job: KnowledgePreparationJobModel, claim: KnowledgePreparationJobSnapshot) -> bool:
    return (
        job.status == "running"
        and job.version == claim.version
        and job.lease_token == claim.lease_token
    )


async def _failed_claim(
    session: AsyncSession, claim: KnowledgePreparationJobSnapshot, code: str
) -> KnowledgePreparationJobSnapshot:
    job = await _locked(session, claim.id)
    if not _owns(job, claim) or job.lease_expires_at is None or job.lease_expires_at <= _now():
        result = _snapshot(job)
        await session.commit()
        return result
    result = await _transition(
        session,
        job,
        "failed" if job.attempts + 1 == 3 else "queued",
        code=code,
        consume_attempt=True,
    )
    await session.commit()
    return result


async def _execute_claim(
    session: AsyncSession, claim: KnowledgePreparationJobSnapshot
) -> KnowledgePreparationJobSnapshot:
    try:
        job = await _locked(session, claim.id)
        if not _owns(job, claim) or job.lease_expires_at is None or job.lease_expires_at <= _now():
            result = _snapshot(job)
            await session.commit()
            return result
        eligibility = await _eligibility(session, job)
        if eligibility != "eligible":
            return await _defer(session, job, eligibility)
        async with asyncio.timeout(90):
            prepared = await KnowledgeUnitService(session).prepare_page(
                principal=Principal(job.requested_by, frozenset({AdminRole.ADMIN})),
                document_id=job.document_id,
                page_number=job.page_number,
                expected_trusted_page_id=job.trusted_page_id,
                commit=False,
            )
            result = await _transition(
                session,
                job,
                "succeeded",
                consume_attempt=True,
                unit_count=len(prepared.units),
                projection_count=len(prepared.projections),
            )
            await session.commit()
            return result
    except Exception as error:
        await session.rollback()
        return await _failed_claim(
            session,
            claim,
            "knowledge_preparation_invalid"
            if isinstance(error, ValueError)
            else "knowledge_preparation_failed",
        )


async def run_knowledge_preparation_job(
    session: AsyncSession, job_id: UUID, *, lease_seconds: int = 300
) -> KnowledgePreparationJobSnapshot:
    if type(lease_seconds) is not int or not 121 <= lease_seconds <= 3600:
        raise ValueError("knowledge preparation lease must exceed the finite actor deadline")
    try:
        claim = await _claim(session, job_id, lease_seconds=lease_seconds)
        return await _execute_claim(session, claim) if claim.claimed else claim
    except BaseException:
        await session.rollback()
        raise


class KnowledgePreparationDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> str: ...


@dataclass(frozen=True, slots=True)
class KnowledgePreparationRecoveryResult:
    discovered: int
    enqueued: int
    failures: int


async def recover_knowledge_preparation_jobs(
    session: AsyncSession,
    dispatcher: KnowledgePreparationDispatcher,
    *,
    batch_size: int = 8,
    min_age_seconds: int = 5,
) -> KnowledgePreparationRecoveryResult:
    _limit(batch_size)
    if type(min_age_seconds) is not int or not 0 <= min_age_seconds <= 3600:
        raise ValueError("knowledge preparation recovery age limit is out of range")
    discovered = await discover_knowledge_preparation(session, batch_size=batch_size)
    now = _now()
    await session.execute(text("SET LOCAL statement_timeout = '90s'"))
    identifiers = tuple(
        (
            await session.scalars(
                select(KnowledgePreparationJobModel.id)
                .where(
                    or_(
                        and_(
                            KnowledgePreparationJobModel.status.in_(("queued", "deferred")),
                            KnowledgePreparationJobModel.updated_at
                            <= now - timedelta(seconds=min_age_seconds),
                        ),
                        and_(
                            KnowledgePreparationJobModel.status == "running",
                            KnowledgePreparationJobModel.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(KnowledgePreparationJobModel.updated_at, KnowledgePreparationJobModel.id)
                .limit(batch_size)
            )
        ).all()
    )
    await session.commit()
    enqueued = failures = 0
    for identifier in identifiers:
        try:
            job = await _locked(session, identifier)
            if (
                job.status == "running"
                and job.lease_expires_at is not None
                and job.lease_expires_at <= _now()
            ):
                await _transition(
                    session,
                    job,
                    "failed" if job.attempts + 1 == 3 else "queued",
                    code="knowledge_preparation_lease_expired",
                    consume_attempt=True,
                )
                await session.commit()
                job = await _locked(session, identifier)
            if job.status not in {"queued", "deferred"}:
                await session.commit()
                continue
            eligibility = await _eligibility(session, job)
            if eligibility != "eligible":
                await _defer(session, job, eligibility)
                continue
            await _transition(
                session, job, "queued", code=job.failure_code if job.status == "queued" else None
            )
            await session.commit()
            with anyio.fail_after(10):
                message_id = await anyio.to_thread.run_sync(
                    dispatcher.dispatch, identifier, abandon_on_cancel=True
                )
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("knowledge preparation dispatcher did not acknowledge delivery")
            enqueued += 1
        except Exception:
            await session.rollback()
            failures += 1
            _logger.error("material knowledge preparation recovery failed")
    return KnowledgePreparationRecoveryResult(len(discovered), enqueued, failures)


async def _execute_knowledge_preparation_job(job_id: UUID) -> None:
    resources = create_resources(Settings())
    try:
        async with resources.session_factory() as session:
            await run_knowledge_preparation_job(session, job_id)
    finally:
        await resources.close()


async def _recover_knowledge_preparation_jobs() -> None:
    settings = Settings()
    resources = create_resources(settings)
    broker = None
    try:
        broker = RedisBroker(
            url=settings.valkey_url.get_secret_value(),
            socket_connect_timeout=5,
            socket_timeout=5,
            retry=Retry(NoBackoff(), 0),
        )
        broker.declare_actor(prepare_knowledge_page)
        async with resources.session_factory() as session:
            await recover_knowledge_preparation_jobs(
                session, DramatiqKnowledgePreparationDispatcher(broker=broker)
            )
    finally:
        try:
            if broker is not None:
                broker.close()
        finally:
            await resources.close()


@dramatiq.actor(
    queue_name=PREPARATION_QUEUE_NAME, max_retries=0, time_limit=PREPARATION_TIME_LIMIT_MS
)
def prepare_knowledge_page(job_id: str) -> None:
    asyncio.run(_execute_knowledge_preparation_job(UUID(job_id)))


@dramatiq.actor(
    queue_name=PREPARATION_QUEUE_NAME, max_retries=0, time_limit=PREPARATION_TIME_LIMIT_MS
)
def recover_material_knowledge() -> None:
    asyncio.run(_recover_knowledge_preparation_jobs())


class _QueuedMessage(Protocol):
    @property
    def message_id(self) -> str: ...


class _PreparationActor(Protocol):
    def send(self, job_id: str) -> _QueuedMessage: ...


class _PreparationBroker(Protocol):
    def enqueue(self, message: dramatiq.Message[Any]) -> _QueuedMessage: ...


class DramatiqKnowledgePreparationDispatcher:
    def __init__(
        self,
        actor: _PreparationActor | None = None,
        *,
        broker: _PreparationBroker | None = None,
    ) -> None:
        self.actor = actor or cast(_PreparationActor, prepare_knowledge_page)
        self.broker = broker

    def dispatch(self, job_id: UUID) -> str:
        if self.broker is not None:
            return self.broker.enqueue(prepare_knowledge_page.message(str(job_id))).message_id
        return self.actor.send(str(job_id)).message_id
