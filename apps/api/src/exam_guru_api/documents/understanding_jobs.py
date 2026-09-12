from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import anyio
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AdminRole, Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import DOCUMENT_UNDERSTANDING_ACTOR_MAX_EXECUTION_SECONDS, Settings
from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH
from exam_guru_api.documents.fidelity_models import PageReviewStateModel, PageTextCandidateModel
from exam_guru_api.documents.fidelity_service import _reason
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceCandidateImageMetadata,
    SourceImageStorage,
    create_page_image_artifacts,
    load_material_source,
)
from exam_guru_api.documents.understanding_contracts import _canonical_json
from exam_guru_api.documents.understanding_models import (
    DocumentUnderstandingJobModel,
    DocumentUnderstandingRunModel,
    ObservationCandidateModel,
    PageUnderstandingStateModel,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingBudget,
    UnderstandingProviderError,
    UnderstandingProviderProfile,
    UnderstandingProviderResult,
    UnderstandingRequest,
    understanding_request_key,
)
from exam_guru_api.documents.understanding_runtime import (
    PreparedUnderstandingInput,
    UnderstandingRuntime,
    create_understanding_runtime,
    prepare_understanding_input,
)
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
)
from exam_guru_api.generation.domain import GenerationAccounting
from exam_guru_api.infrastructure.object_storage import create_object_storage
from exam_guru_api.infrastructure.resources import create_resources

UNDERSTANDING_QUEUE_NAME = "source-document-understanding"
UNDERSTANDING_TIME_LIMIT_MS = DOCUMENT_UNDERSTANDING_ACTOR_MAX_EXECUTION_SECONDS * 1000
_logger = logging.getLogger(__name__)


class UnderstandingJobNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class UnderstandingJobSnapshot:
    id: UUID
    document_id: UUID
    page_number: int
    source_sha256: str
    request_id: UUID
    created_by: UUID
    status: str
    version: int
    expected_page_version: int
    profile: UnderstandingProviderProfile
    budget: UnderstandingBudget
    accounting: GenerationAccounting | None
    candidate_id: UUID | None
    run_id: UUID | None
    failure_code: str | None
    lease_token: UUID | None
    attempts: int
    retry_depth: int


def _snapshot(job: DocumentUnderstandingJobModel) -> UnderstandingJobSnapshot:
    return UnderstandingJobSnapshot(
        job.id,
        job.document_id,
        job.page_number,
        job.source_sha256,
        job.request_id,
        job.created_by,
        job.status,
        job.version,
        job.expected_page_version,
        UnderstandingProviderProfile.model_validate_json(json.dumps(job.profile)),
        UnderstandingBudget.model_validate_json(json.dumps(job.budget)),
        None
        if job.accounting is None
        else GenerationAccounting(**cast(dict[str, Any], job.accounting)),
        job.candidate_id,
        job.run_id,
        job.failure_code,
        job.lease_token,
        job.attempts,
        job.retry_depth,
    )


def _known_accounting(value: object) -> GenerationAccounting | None:
    if not isinstance(value, GenerationAccounting):
        return None
    try:
        return GenerationAccounting(**asdict(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _principal(job: DocumentUnderstandingJobModel) -> Principal:
    return Principal(job.created_by, frozenset({AdminRole.ADMIN}))


def _request_fingerprint(
    document_id: UUID,
    page_number: int,
    checksum: str,
    expected_version: int,
    runtime: UnderstandingRuntime,
    reason: str,
    retry_of_job_id: UUID | None,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "document_id": str(document_id),
                "page_number": page_number,
                "source_sha256": checksum,
                "expected_version": expected_version,
                "profile": runtime.profile.model_dump(mode="json"),
                "budget": runtime.budget.model_dump(mode="json"),
                "reason": reason,
                "retry_of_job_id": None if retry_of_job_id is None else str(retry_of_job_id),
            }
        ).encode()
    ).hexdigest()


def _audit(
    job: DocumentUnderstandingJobModel,
    page: PageUnderstandingStateModel,
    *,
    status: str,
    version: int,
    page_version: int,
    clear_trust: bool = False,
) -> AdminAuditEventModel:
    return AdminAuditEventModel(
        id=uuid4(),
        actor_id=job.created_by,
        resource_type="page_understanding",
        resource_id=job.document_id,
        action=f"page_understanding.job_{status}",
        payload={
            "job_id": str(job.id),
            "job_version": version,
            "status": status,
            "page_number": job.page_number,
            "version": page_version,
            "candidate_id": None
            if page.current_candidate_id is None
            else str(page.current_candidate_id),
            "report_id": None if page.current_report_id is None else str(page.current_report_id),
            "trusted_knowledge_id": None
            if clear_trust or page.current_trusted_id is None
            else str(page.current_trusted_id),
            "request_id": str(job.request_id),
        },
    )


async def _locked(
    session: AsyncSession, job_id: UUID
) -> tuple[DocumentUnderstandingJobModel, PageUnderstandingStateModel, SourceDocumentModel]:
    reference = await session.get(DocumentUnderstandingJobModel, job_id, populate_existing=True)
    if reference is None:
        raise UnderstandingJobNotFoundError("source_understanding_job_not_found")
    source = await session.scalar(
        select(SourceDocumentModel)
        .where(SourceDocumentModel.id == reference.document_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    page = await session.scalar(
        select(PageUnderstandingStateModel)
        .where(
            PageUnderstandingStateModel.document_id == reference.document_id,
            PageUnderstandingStateModel.page_number == reference.page_number,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = await session.scalar(
        select(DocumentUnderstandingJobModel)
        .where(DocumentUnderstandingJobModel.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or page is None or job is None:
        raise UnderstandingJobNotFoundError("source_understanding_job_not_found")
    return job, page, source


class UnderstandingJobService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        document_id: UUID,
        page_number: int,
        expected_version: int,
        runtime: UnderstandingRuntime,
        reason: str,
        retry_of_job_id: UUID | None = None,
    ) -> UnderstandingJobSnapshot:
        authorize(principal, Permission.SOURCE_WRITE)
        reason = _reason(reason)
        try:
            service = PageUnderstandingService(self.session)
            source = await service._source(document_id, page_number, write=True)
            fingerprint = _request_fingerprint(
                document_id,
                page_number,
                source.checksum_sha256,
                expected_version,
                runtime,
                reason,
                retry_of_job_id,
            )
            page = await service._page(document_id, page_number, principal, create=True)
            existing = await self.session.scalar(
                select(DocumentUnderstandingJobModel).where(
                    DocumentUnderstandingJobModel.created_by == principal.subject_id,
                    DocumentUnderstandingJobModel.request_id == request_id,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise UnderstandingConflictError("source_understanding_request_conflict")
                result = _snapshot(existing)
                await self.session.commit()
                return result
            recorded = await self.session.scalar(
                select(DocumentUnderstandingRunModel.id).where(
                    DocumentUnderstandingRunModel.created_by == principal.subject_id,
                    DocumentUnderstandingRunModel.request_id == request_id,
                )
            )
            if recorded is not None:
                raise UnderstandingConflictError("source_understanding_request_conflict")
            service._version(page, expected_version)
            if page.active_job_id is not None or page.state in {"verified", "excluded"}:
                raise UnderstandingConflictError("source_understanding_page_protected")
            retry_depth = 0
            if retry_of_job_id is not None:
                parent = await self.session.get(DocumentUnderstandingJobModel, retry_of_job_id)
                if (
                    parent is None
                    or parent.created_by != principal.subject_id
                    or parent.document_id != document_id
                    or parent.page_number != page_number
                    or parent.status not in {"failed", "unknown"}
                    or parent.retry_depth >= MAX_PROVIDER_JOB_RETRY_DEPTH
                ):
                    raise UnderstandingConflictError("source_understanding_retry_conflict")
                retry_depth = parent.retry_depth + 1
            job = DocumentUnderstandingJobModel(
                id=uuid4(),
                document_id=document_id,
                page_number=page_number,
                source_sha256=source.checksum_sha256,
                request_id=request_id,
                request_fingerprint=fingerprint,
                expected_page_version=expected_version + 1,
                profile=runtime.profile.model_dump(mode="json"),
                budget=runtime.budget.model_dump(mode="json"),
                reason=reason,
                status="queued",
                version=0,
                attempts=0,
                retry_depth=retry_depth,
                retry_of_job_id=retry_of_job_id,
                created_by=principal.subject_id,
            )
            audit = _audit(
                job,
                page,
                status="queued",
                version=0,
                page_version=page.version + 1,
                clear_trust=True,
            )
            self.session.add(audit)
            await self.session.flush()
            job.audit_event_id = audit.id
            self.session.add(job)
            await self.session.flush()
            page.active_job_id = job.id
            page.current_trusted_id = None
            page.state = "processing"
            page.version += 1
            page.event_id = audit.id
            page.updated_by = principal.subject_id
            page.updated_at = datetime.now(UTC)
            await self.session.commit()
            return _snapshot(job)
        except Exception:
            await self.session.rollback()
            raise

    async def latest_for_page(
        self, *, principal: Principal, document_id: UUID, page_number: int, page_version: int
    ) -> UnderstandingJobSnapshot | None:
        authorize(principal, Permission.SOURCE_READ)
        await PageUnderstandingService(self.session)._source(document_id, page_number, write=False)
        job = await self.session.scalar(
            select(DocumentUnderstandingJobModel)
            .where(
                DocumentUnderstandingJobModel.document_id == document_id,
                DocumentUnderstandingJobModel.page_number == page_number,
                DocumentUnderstandingJobModel.expected_page_version <= page_version,
            )
            .order_by(
                DocumentUnderstandingJobModel.expected_page_version.desc(),
                DocumentUnderstandingJobModel.created_at.desc(),
                DocumentUnderstandingJobModel.id.desc(),
            )
            .limit(1)
        )
        return None if job is None else _snapshot(job)

    async def get(self, *, principal: Principal, job_id: UUID) -> UnderstandingJobSnapshot:
        authorize(principal, Permission.SOURCE_READ)
        job = await self.session.get(DocumentUnderstandingJobModel, job_id, populate_existing=True)
        if job is None:
            raise UnderstandingJobNotFoundError("source_understanding_job_not_found")
        await PageUnderstandingService(self.session)._source(
            job.document_id, job.page_number, write=False
        )
        return _snapshot(job)


async def _success(
    session: AsyncSession,
    job: DocumentUnderstandingJobModel,
    page: PageUnderstandingStateModel,
    run_id: UUID,
    candidate_id: UUID,
    accounting: GenerationAccounting,
) -> UnderstandingJobSnapshot:
    if job.status in {"succeeded", "failed", "unknown"}:
        result = _snapshot(job)
        await session.commit()
        return result
    audit = _audit(
        job, page, status="succeeded", version=job.version + 1, page_version=page.version
    )
    session.add(audit)
    await session.flush()
    job.status = "succeeded"
    job.version += 1
    job.audit_event_id = audit.id
    job.run_id = run_id
    job.candidate_id = candidate_id
    job.accounting = asdict(accounting)
    job.lease_token = None
    job.lease_expires_at = None
    job.completed_at = job.updated_at = datetime.now(UTC)
    await session.commit()
    return _snapshot(job)


async def _complete_saved_run(
    session: AsyncSession,
    job: DocumentUnderstandingJobModel,
    page: PageUnderstandingStateModel,
    run: DocumentUnderstandingRunModel,
) -> UnderstandingJobSnapshot:
    candidate = await session.scalar(
        select(ObservationCandidateModel).where(ObservationCandidateModel.run_id == run.id)
    )
    if candidate is None or run.accounting is None:
        raise UnderstandingConflictError("source_understanding_saved_result_incomplete")
    accounting = GenerationAccounting(**cast(dict[str, Any], run.accounting))
    return await _success(session, job, page, run.id, candidate.id, accounting)


async def _failure(
    session: AsyncSession,
    job_id: UUID,
    token: UUID | None,
    *,
    code: str,
    accounting: GenerationAccounting | None = None,
) -> UnderstandingJobSnapshot:
    accounting = _known_accounting(accounting)
    job, page, _source = await _locked(session, job_id)
    if job.status in {"succeeded", "failed", "unknown"} or job.lease_token != token:
        result = _snapshot(job)
        await session.commit()
        return result
    status = "unknown" if job.provider_started_at is not None and accounting is None else "failed"
    run_id = None
    if job.provider_started_at is not None and job.image_metadata is not None:
        run = await session.scalar(
            select(DocumentUnderstandingRunModel).where(
                DocumentUnderstandingRunModel.created_by == job.created_by,
                DocumentUnderstandingRunModel.request_id == job.request_id,
            )
        )
        if run is not None and run.outcome == "succeeded":
            return await _complete_saved_run(session, job, page, run)
        if run is None:
            metadata = SourceCandidateImageMetadata.model_validate(job.image_metadata)
            run_id = uuid4()
            event = AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                resource_type="page_understanding",
                resource_id=job.document_id,
                action="page_understanding.observed",
                payload={
                    "run_id": str(run_id),
                    "page_number": job.page_number,
                    "job_id": str(job.id),
                    "failure_code": code,
                },
            )
            session.add(event)
            await session.flush()
            run = DocumentUnderstandingRunModel(
                id=run_id,
                document_id=job.document_id,
                page_number=job.page_number,
                source_sha256=job.source_sha256,
                image_sha256=metadata.sha256,
                request_id=job.request_id,
                request_fingerprint=job.provider_request_key,
                method="visual_ai",
                outcome=status,
                failure_code=code,
                image_metadata=job.image_metadata,
                provider_profile=job.profile,
                budget=job.budget,
                accounting=None if accounting is None else asdict(accounting),
                created_by=job.created_by,
                audit_event_id=event.id,
            )
            session.add(run)
            await session.flush()
        run_id = run.id
    owns_page = page.active_job_id == job.id
    audit = _audit(
        job,
        page,
        status=status,
        version=job.version + 1,
        page_version=page.version + int(owns_page),
        clear_trust=owns_page,
    )
    session.add(audit)
    await session.flush()
    job.status = status
    job.version += 1
    job.audit_event_id = audit.id
    job.failure_code = code
    job.accounting = None if accounting is None else asdict(accounting)
    job.run_id = run_id
    job.lease_token = None
    job.lease_expires_at = None
    job.completed_at = job.updated_at = datetime.now(UTC)
    await session.flush()
    if owns_page:
        page.active_job_id = None
        page.current_trusted_id = None
        page.state = "needs_reprocessing"
        page.version += 1
        page.event_id = audit.id
        page.updated_by = job.created_by
        page.updated_at = datetime.now(UTC)
    await session.commit()
    return _snapshot(job)


async def run_understanding_job(
    session: AsyncSession,
    job_id: UUID,
    *,
    runtime: UnderstandingRuntime,
    input_factory: Callable[[UnderstandingJobSnapshot], PreparedUnderstandingInput],
    lease_seconds: int = 600,
) -> UnderstandingJobSnapshot:
    if type(lease_seconds) is not int or not 301 <= lease_seconds <= 86_400:
        raise ValueError("understanding lease must exceed the actor deadline")
    job, page, source = await _locked(session, job_id)
    if job.status != "queued":
        result = _snapshot(job)
        await session.commit()
        return result
    if (
        not source.active_for_ai
        or source.quarantined_for_teacher_use
        or page.active_job_id != job.id
        or page.version != job.expected_page_version
    ):
        return await _failure(session, job.id, None, code="source_understanding_source_changed")
    if (
        runtime.profile.model_dump(mode="json") != job.profile
        or runtime.budget.model_dump(mode="json") != job.budget
    ):
        return await _failure(
            session, job.id, None, code="source_understanding_configuration_changed"
        )
    audit = _audit(job, page, status="running", version=job.version + 1, page_version=page.version)
    session.add(audit)
    await session.flush()
    job.status = "running"
    job.attempts += 1
    job.version += 1
    job.audit_event_id = audit.id
    job.lease_token = uuid4()
    job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    job.updated_at = datetime.now(UTC)
    await session.commit()
    claim = _snapshot(job)
    try:
        prepared = await anyio.to_thread.run_sync(input_factory, claim)
        job, page, source = await _locked(session, job_id)
        if (
            job.status != "running"
            or job.lease_token != claim.lease_token
            or page.active_job_id != job.id
            or page.version != job.expected_page_version
        ):
            result = _snapshot(job)
            await session.commit()
            return result
        if job.lease_expires_at is None or job.lease_expires_at <= datetime.now(UTC):
            return await _failure(
                session, job.id, claim.lease_token, code="source_understanding_lease_expired"
            )
        try:
            request = UnderstandingRequest.model_validate(prepared.request)
            metadata = SourceCandidateImageMetadata.model_validate(
                prepared.image_metadata.model_dump(mode="json")
            )
            PageUnderstandingService._validate_image(source, request, metadata)
        except (AttributeError, TypeError, ValueError):
            return await _failure(
                session, job.id, claim.lease_token, code="source_understanding_image_mismatch"
            )
        identity = request.source
        if (
            identity.document_id != job.document_id
            or identity.page_number != job.page_number
            or identity.source_sha256 != job.source_sha256
            or request.profile != runtime.profile
            or request.budget != runtime.budget
        ):
            return await _failure(
                session, job.id, claim.lease_token, code="source_understanding_image_mismatch"
            )
        prepared = PreparedUnderstandingInput(request, metadata)
        audit = _audit(
            job, page, status="running", version=job.version + 1, page_version=page.version
        )
        session.add(audit)
        await session.flush()
        job.provider_started_at = datetime.now(UTC)
        job.provider_request_key = understanding_request_key(prepared.request)
        job.image_metadata = metadata.model_dump(mode="json")
        job.audit_event_id = audit.id
        job.version += 1
        job.updated_at = datetime.now(UTC)
        await session.commit()
    except PageImageError as error:
        await session.rollback()
        return await _failure(session, job_id, claim.lease_token, code=error.code)
    except Exception:
        await session.rollback()
        return await _failure(
            session, job_id, claim.lease_token, code="source_understanding_input_failed"
        )
    try:
        response = await anyio.to_thread.run_sync(runtime.provider.understand, prepared.request)
    except UnderstandingProviderError as error:
        return await _failure(
            session, job_id, claim.lease_token, code=error.code.value, accounting=error.accounting
        )
    except Exception:
        return await _failure(
            session, job_id, claim.lease_token, code="source_understanding_provider_unavailable"
        )
    try:
        response = UnderstandingProviderResult.model_validate(response)
    except (TypeError, ValueError):
        return await _failure(
            session,
            job_id,
            claim.lease_token,
            code="invalid_response",
            accounting=_known_accounting(getattr(response, "accounting", None)),
        )
    job, page, _source = await _locked(session, job_id)
    if (
        job.status != "running"
        or job.lease_token != claim.lease_token
        or page.active_job_id != job.id
        or page.version != job.expected_page_version
    ):
        return await _failure(
            session,
            job.id,
            claim.lease_token,
            code="source_understanding_source_changed",
            accounting=response.accounting,
        )
    if job.lease_expires_at is None or job.lease_expires_at <= datetime.now(UTC):
        return await _failure(
            session,
            job.id,
            claim.lease_token,
            code="source_understanding_lease_expired",
            accounting=response.accounting,
        )
    try:
        candidate = await PageUnderstandingService(session).record_result(
            principal=_principal(job),
            request_id=claim.request_id,
            expected_version=claim.expected_page_version,
            request=prepared.request,
            result=response,
            image_metadata=prepared.image_metadata,
            job_id=job_id,
        )
    except Exception:
        await session.rollback()
        return await _failure(
            session,
            job_id,
            claim.lease_token,
            code="source_understanding_result_rejected",
            accounting=response.accounting,
        )
    job, page, _source = await _locked(session, job_id)
    return await _success(session, job, page, candidate.run_id, candidate.id, response.accounting)


class UnderstandingJobDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> str: ...


@dataclass(frozen=True, slots=True)
class UnderstandingRecoveryResult:
    enqueued: int
    reconciled: int
    unknown: int
    failures: int


async def recover_understanding_jobs(
    session: AsyncSession,
    dispatcher: UnderstandingJobDispatcher,
    *,
    batch_size: int = 20,
    min_age_seconds: int = 5,
) -> UnderstandingRecoveryResult:
    if (
        type(batch_size) is not int
        or not 1 <= batch_size <= 100
        or type(min_age_seconds) is not int
        or not 1 <= min_age_seconds <= 3600
    ):
        raise ValueError("understanding recovery limits are out of range")
    now = datetime.now(UTC)
    identifiers = tuple(
        (
            await session.scalars(
                select(DocumentUnderstandingJobModel.id)
                .where(
                    or_(
                        and_(
                            DocumentUnderstandingJobModel.status == "queued",
                            DocumentUnderstandingJobModel.updated_at
                            <= now - timedelta(seconds=min_age_seconds),
                        ),
                        and_(
                            DocumentUnderstandingJobModel.status == "running",
                            DocumentUnderstandingJobModel.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(
                    DocumentUnderstandingJobModel.updated_at, DocumentUnderstandingJobModel.id
                )
                .limit(batch_size)
            )
        ).all()
    )
    await session.commit()
    enqueued = reconciled = unknown = failures = 0
    for identifier in identifiers:
        try:
            job, page, source = await _locked(session, identifier)
            if (
                job.status == "running"
                and job.lease_expires_at is not None
                and job.lease_expires_at <= now
            ):
                run = await session.scalar(
                    select(DocumentUnderstandingRunModel).where(
                        DocumentUnderstandingRunModel.created_by == job.created_by,
                        DocumentUnderstandingRunModel.request_id == job.request_id,
                    )
                )
                if run is not None and run.outcome == "succeeded":
                    await _complete_saved_run(session, job, page, run)
                    reconciled += 1
                    continue
                if job.provider_started_at is not None:
                    accounting = (
                        None
                        if run is None or run.accounting is None
                        else GenerationAccounting(**cast(dict[str, Any], run.accounting))
                    )
                    outcome = await _failure(
                        session,
                        job.id,
                        job.lease_token,
                        code="source_understanding_outcome_unknown"
                        if run is None
                        else cast(str, run.failure_code),
                        accounting=accounting,
                    )
                    unknown += int(outcome.status == "unknown")
                    continue
                if (
                    job.attempts >= 3
                    or not source.active_for_ai
                    or source.quarantined_for_teacher_use
                ):
                    await _failure(
                        session,
                        job.id,
                        job.lease_token,
                        code="source_understanding_claims_exhausted",
                    )
                    continue
                audit = _audit(
                    job, page, status="queued", version=job.version + 1, page_version=page.version
                )
                session.add(audit)
                await session.flush()
                job.status = "queued"
                job.version += 1
                job.audit_event_id = audit.id
                job.lease_token = None
                job.lease_expires_at = None
                job.updated_at = now
                await session.commit()
            elif job.status != "queued":
                await session.commit()
                continue
            else:
                await session.commit()
            message_id = await anyio.to_thread.run_sync(dispatcher.dispatch, identifier)
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("understanding dispatcher did not acknowledge the job")
            enqueued += 1
        except Exception:
            await session.rollback()
            failures += 1
    return UnderstandingRecoveryResult(enqueued, reconciled, unknown, failures)


async def _prepare_job_input_factory(
    session: AsyncSession,
    job_id: UUID,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts | None,
    runtime: UnderstandingRuntime,
) -> Callable[[UnderstandingJobSnapshot], PreparedUnderstandingInput]:
    job = await session.get(DocumentUnderstandingJobModel, job_id, populate_existing=True)
    if job is None:
        raise UnderstandingJobNotFoundError("source_understanding_job_not_found")
    source = await load_material_source(session, job.document_id, principal=_principal(job))
    metadata = await session.scalar(
        select(DocumentUnderstandingRunModel.image_metadata)
        .join(
            ObservationCandidateModel,
            ObservationCandidateModel.run_id == DocumentUnderstandingRunModel.id,
        )
        .join(
            PageUnderstandingStateModel,
            PageUnderstandingStateModel.current_candidate_id == ObservationCandidateModel.id,
        )
        .where(
            PageUnderstandingStateModel.document_id == job.document_id,
            PageUnderstandingStateModel.page_number == job.page_number,
        )
    )
    if metadata is not None:
        provenance: dict[str, object] | None = {
            "source_checksum_sha256": source.checksum_sha256,
            "page_image": metadata,
        }
    else:
        provenance = await session.scalar(
            select(PageTextCandidateModel.provenance)
            .join(
                PageReviewStateModel,
                PageReviewStateModel.current_candidate_id == PageTextCandidateModel.id,
            )
            .where(
                PageReviewStateModel.document_id == job.document_id,
                PageReviewStateModel.page_number == job.page_number,
            )
        )
    page_number = job.page_number
    await session.commit()
    return lambda _: prepare_understanding_input(
        source, page_number, provenance, storage, artifacts, runtime
    )


async def _fail_unconfigured_job(session: AsyncSession, job_id: UUID) -> None:
    job, _page, _source = await _locked(session, job_id)
    if job.status == "queued":
        await _failure(session, job.id, None, code="source_understanding_provider_unconfigured")
    else:
        await session.commit()


async def _execute_understanding_job(job_id: UUID) -> None:
    settings = Settings()
    resources = create_resources(settings)
    storage = None
    try:
        runtime = create_understanding_runtime(settings)
        async with resources.session_factory() as session:
            if runtime is None:
                await _fail_unconfigured_job(session, job_id)
                return
            storage = create_object_storage(settings)
            try:
                factory = await _prepare_job_input_factory(
                    session, job_id, storage, create_page_image_artifacts(settings), runtime
                )
            except PageImageError as error:
                await session.rollback()
                job, _page, _source = await _locked(session, job_id)
                if job.status == "queued":
                    await _failure(session, job_id, None, code=error.code)
                else:
                    await session.commit()
                return
            await run_understanding_job(
                session,
                job_id,
                runtime=runtime,
                input_factory=factory,
                lease_seconds=settings.document_understanding_worker_lease_seconds,
            )
    finally:
        try:
            if storage is not None:
                storage.close()
        finally:
            await resources.close()


async def _recover_understanding_jobs() -> None:
    settings = Settings()
    resources = create_resources(settings)
    try:
        async with resources.session_factory() as session:
            await recover_understanding_jobs(
                session,
                DramatiqUnderstandingDispatcher(),
                batch_size=settings.document_understanding_recovery_batch_size,
                min_age_seconds=settings.document_understanding_outbox_min_age_seconds,
            )
    finally:
        await resources.close()


@dramatiq.actor(
    queue_name=UNDERSTANDING_QUEUE_NAME, max_retries=0, time_limit=UNDERSTANDING_TIME_LIMIT_MS
)
def understand_source_page(job_id: str) -> None:
    asyncio.run(_execute_understanding_job(UUID(job_id)))


@dramatiq.actor(
    queue_name=UNDERSTANDING_QUEUE_NAME, max_retries=0, time_limit=UNDERSTANDING_TIME_LIMIT_MS
)
def recover_understanding_page_jobs() -> None:
    asyncio.run(_recover_understanding_jobs())


class _QueuedMessage(Protocol):
    message_id: str


class _UnderstandingActor(Protocol):
    def send(self, job_id: str) -> _QueuedMessage: ...


class DramatiqUnderstandingDispatcher:
    def __init__(self, actor: _UnderstandingActor | None = None) -> None:
        self._actor = actor or cast(_UnderstandingActor, understand_source_page)

    def dispatch(self, job_id: UUID) -> str:
        return self._actor.send(str(job_id)).message_id


def create_understanding_dispatcher(settings: Settings) -> DramatiqUnderstandingDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    for actor in (understand_source_page, recover_understanding_page_jobs):
        actor.broker = broker
        broker.declare_actor(actor)
    return DramatiqUnderstandingDispatcher()


async def dispatch_understanding_job(job_id: UUID, dispatcher: UnderstandingJobDispatcher) -> bool:
    try:
        message_id = await anyio.to_thread.run_sync(dispatcher.dispatch, job_id)
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("understanding queue did not acknowledge delivery")
    except Exception:
        _logger.error("source document understanding dispatch failed")
        return False
    return True
