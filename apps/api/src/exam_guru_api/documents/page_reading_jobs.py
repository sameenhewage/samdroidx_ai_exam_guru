from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import BinaryIO, Protocol, cast
from uuid import UUID, uuid4

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.fidelity_models import (
    PageReviewStateModel,
    PageTextCandidateModel,
    SourceReadJobModel,
)
from exam_guru_api.documents.fidelity_service import (
    FidelitySourceNotFoundError,
    PageFidelityConflictError,
    PageFidelityService,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.ocr import OCRConfigError, OCRInputError
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    SourceImageIdentity,
    create_page_image_artifacts,
)
from exam_guru_api.documents.page_reading import (
    FilePageReader,
    PageReader,
    PageReadingConfiguration,
    PageReadingResult,
    PageReadingSession,
)
from exam_guru_api.infrastructure.object_storage import (
    ObjectStorageOperationError,
    create_object_storage,
)
from exam_guru_api.infrastructure.resources import create_resources

SOURCE_READ_QUEUE_NAME = "source-page-reading"
SOURCE_READ_BATCH_SIZE = 8
SOURCE_READ_EXECUTION_SECONDS = 300
SOURCE_READ_LEASE_SECONDS = 600
SOURCE_READ_TIME_LIMIT_MS = 360_000
SOURCE_READ_RECOVERY_BATCH_SIZE = 100
SOURCE_READ_OUTBOX_MIN_AGE_SECONDS = 30
_MAX_PAGE_NUMBER = 2_147_483_646
_PROTECTED_STATES = frozenset({"verified", "excluded", "processing"})
_logger = logging.getLogger(__name__)


class SourceReadStorage(Protocol):
    def open_source(self, key: str) -> AbstractContextManager[BinaryIO]: ...


class SourceReadDispatcher(Protocol):
    def dispatch(self, job_id: UUID) -> str: ...


class SourceReadJobNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class SourceReadClaim:
    id: UUID
    document_id: UUID
    page_number: int | None
    next_page: int
    expected_page_version: int | None
    status: str
    attempts: int
    version: int
    configuration: dict[str, object] = field(repr=False)
    lease_token: UUID | None
    requested_by: UUID
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class SourceReadResult:
    job_id: UUID
    claimed: bool
    status: str
    next_page: int
    pages_processed: int
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class SourceReadRecoveryResult:
    enqueued: int
    failures: int


def _now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source reading timestamps must be timezone aware")
    return value.astimezone(UTC)


def _integer(value: int, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("source reading argument is out of range")


def _snapshot(job: SourceReadJobModel) -> SourceReadClaim:
    return SourceReadClaim(
        id=job.id,
        document_id=job.document_id,
        page_number=job.page_number,
        next_page=job.next_page,
        expected_page_version=job.expected_page_version,
        status=job.status,
        attempts=job.attempts,
        version=job.version,
        configuration=dict(job.configuration),
        lease_token=job.lease_token,
        requested_by=job.requested_by,
        failure_code=job.failure_code,
    )


def _result(job: SourceReadClaim, *, claimed: bool, pages_processed: int) -> SourceReadResult:
    return SourceReadResult(
        job_id=job.id,
        claimed=claimed,
        status=job.status,
        next_page=job.next_page,
        pages_processed=pages_processed,
        failure_code=job.failure_code,
    )


async def _current_result(
    session: AsyncSession, job_id: UUID, *, claimed: bool, pages_processed: int
) -> SourceReadResult:
    job = await session.get(SourceReadJobModel, job_id, populate_existing=True)
    if job is None:
        raise SourceReadJobNotFoundError("source_read_job_not_found")
    result = _result(_snapshot(job), claimed=claimed, pages_processed=pages_processed)
    await session.commit()
    return result


async def queue_source_read(
    session: AsyncSession,
    document_id: UUID,
    *,
    actor_id: UUID,
    page_number: int | None = None,
    expected_page_version: int | None = None,
    configuration: PageReadingConfiguration | None = None,
    reason: str = "Requested a fresh reading of the original source page",
) -> SourceReadJobModel:
    if page_number is None:
        if expected_page_version is not None:
            raise ValueError("a page version requires a selected page")
    else:
        _integer(page_number, minimum=1, maximum=_MAX_PAGE_NUMBER)
        if expected_page_version is None:
            raise ValueError("an explicit reread requires the current page version")
        _integer(expected_page_version, minimum=0, maximum=_MAX_PAGE_NUMBER)
    selected = configuration or PageReadingConfiguration()
    if page_number is not None:
        selected = replace(selected, force_ocr=True)
    encoded = PageReadingConfiguration.from_dict(selected.to_dict()).to_dict()
    try:
        document = await session.get(
            SourceDocumentModel, document_id, with_for_update=True, populate_existing=True
        )
        if document is None:
            raise FidelitySourceNotFoundError("source_document_not_found")
        if not document.active_for_ai:
            raise PageFidelityConflictError("source_document_removed")
        if page_number is not None and (
            document.original_page_count is None or page_number > document.original_page_count
        ):
            raise FidelitySourceNotFoundError("source_page_not_found")
        active = await session.scalar(
            select(SourceReadJobModel)
            .where(
                SourceReadJobModel.document_id == document_id,
                SourceReadJobModel.page_number.is_not_distinct_from(page_number),
                SourceReadJobModel.status.in_(("queued", "running")),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        fidelity = PageFidelityService(session)
        if active is not None:
            if active.configuration != encoded:
                raise PageFidelityConflictError("source_read_configuration_conflict")
            if page_number is not None:
                state = await fidelity._state(document_id, page_number)
                if (
                    active.expected_page_version != cast(int, expected_page_version) + 1
                    or state.version != active.expected_page_version
                    or state.state != "processing"
                ):
                    raise PageFidelityConflictError("source_page_version_conflict")
            await session.commit()
            return active
        job_id = uuid4()
        if page_number is not None:
            state = await fidelity._state(document_id, page_number)
            fidelity._version(state, cast(int, expected_page_version))
            await fidelity._advance(
                state,
                candidate_id=state.current_candidate_id,
                action="reread_requested",
                target="processing",
                actor_id=actor_id,
                reason=reason,
                payload={"job_id": str(job_id), "automatic_verification": False},
            )
            expected_page_version = state.version
        now = _now()
        job = SourceReadJobModel(
            id=job_id,
            document_id=document_id,
            page_number=page_number,
            next_page=page_number or 1,
            expected_page_version=expected_page_version,
            status="queued",
            attempts=0,
            version=0,
            configuration=encoded,
            requested_by=actor_id,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="source_read.queued",
                resource_type="source_read_job",
                resource_id=job_id,
                payload={"document_id": str(document_id), "page_number": page_number},
            )
        )
        await session.commit()
        return job
    except BaseException:
        await session.rollback()
        raise


async def claim_source_read(
    session: AsyncSession, job_id: UUID, *, now: datetime | None = None
) -> SourceReadClaim | None:
    current = _now(now)
    job = await session.scalar(
        update(SourceReadJobModel)
        .where(
            SourceReadJobModel.id == job_id,
            or_(
                SourceReadJobModel.status == "queued",
                and_(
                    SourceReadJobModel.status == "running",
                    or_(
                        SourceReadJobModel.lease_expires_at <= current,
                        SourceReadJobModel.lease_expires_at.is_(None),
                    ),
                ),
            ),
        )
        .values(
            status="running",
            attempts=SourceReadJobModel.attempts + 1,
            version=SourceReadJobModel.version + 1,
            lease_token=uuid4(),
            lease_expires_at=current + timedelta(seconds=SOURCE_READ_LEASE_SECONDS),
            updated_at=current,
        )
        .returning(SourceReadJobModel)
        .execution_options(populate_existing=True)
    )
    claim = _snapshot(job) if job is not None else None
    await session.commit()
    return claim


def _lease_predicate(claim: SourceReadClaim, now: datetime) -> ColumnElement[bool]:
    return and_(
        SourceReadJobModel.id == claim.id,
        SourceReadJobModel.status == "running",
        SourceReadJobModel.version == claim.version,
        SourceReadJobModel.next_page == claim.next_page,
        SourceReadJobModel.lease_token == claim.lease_token,
        SourceReadJobModel.lease_expires_at > now,
    )


async def _owned_source(
    session: AsyncSession, claim: SourceReadClaim
) -> SourceDocumentModel | None:
    document = await session.get(
        SourceDocumentModel, claim.document_id, with_for_update=True, populate_existing=True
    )
    job = await session.scalar(
        select(SourceReadJobModel)
        .where(_lease_predicate(claim, _now()))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return document if job is not None else None


async def _save_progress(
    session: AsyncSession,
    claim: SourceReadClaim,
    *,
    status: str,
    next_page: int,
    failure_code: str | None,
) -> SourceReadClaim | None:
    now = _now()
    job = await session.scalar(
        update(SourceReadJobModel)
        .where(_lease_predicate(claim, now))
        .values(
            status=status,
            next_page=next_page,
            failure_code=failure_code,
            version=SourceReadJobModel.version + 1,
            lease_token=claim.lease_token if status == "running" else None,
            lease_expires_at=now + timedelta(seconds=SOURCE_READ_LEASE_SECONDS)
            if status == "running"
            else None,
            updated_at=now,
        )
        .returning(SourceReadJobModel)
        .execution_options(populate_existing=True)
    )
    if job is None:
        await session.rollback()
        return None
    progress = _snapshot(job)
    await session.commit()
    return progress


async def _finish(
    session: AsyncSession,
    claim: SourceReadClaim,
    *,
    status: str,
    failure_code: str | None = None,
) -> SourceReadClaim | None:
    document = await _owned_source(session, claim)
    if document is None:
        await session.rollback()
        return None
    if not document.active_for_ai:
        status = "superseded"
    if claim.page_number is not None:
        state = await session.get(
            PageReviewStateModel,
            (claim.document_id, claim.page_number),
            with_for_update=True,
            populate_existing=True,
        )
        if (
            state is None
            or state.version != claim.expected_page_version
            or state.state != "processing"
        ):
            status = "superseded"
        elif status == "failed":
            await PageFidelityService(session)._advance(
                state,
                candidate_id=state.current_candidate_id,
                action="reread_failed",
                target="failed",
                actor_id=claim.requested_by,
                reason="The requested source reading could not be completed",
                payload={"job_id": str(claim.id), "failure_code": failure_code},
            )
    return await _save_progress(
        session,
        claim,
        status=status,
        next_page=claim.next_page,
        failure_code=claim.failure_code or failure_code,
    )


async def _check_page_count(
    session: AsyncSession, claim: SourceReadClaim, count: int
) -> SourceReadClaim | None:
    _integer(count, minimum=1, maximum=_MAX_PAGE_NUMBER)
    document = await _owned_source(session, claim)
    if document is None:
        await session.rollback()
        return None
    if not document.active_for_ai:
        return await _finish(session, claim, status="superseded")
    if document.original_page_count is None:
        document.original_page_count = count
    elif document.original_page_count != count:
        return await _finish(
            session, claim, status="failed", failure_code="source_page_count_mismatch"
        )
    await session.commit()
    return claim


async def _protected_page(session: AsyncSession, state: PageReviewStateModel | None) -> bool:
    if state is None:
        return False
    if state.state in _PROTECTED_STATES:
        return True
    return (
        await session.scalar(
            select(PageTextCandidateModel.method).where(
                PageTextCandidateModel.id == state.current_candidate_id
            )
        )
        == "human"
    )


async def _page_version(session: AsyncSession, claim: SourceReadClaim) -> int | None:
    state = await session.get(
        PageReviewStateModel, (claim.document_id, claim.next_page), populate_existing=True
    )
    version: int | None = state.version if state is not None else 0
    if claim.page_number is None:
        if await _protected_page(session, state):
            version = None
    elif (
        state is None or state.version != claim.expected_page_version or state.state != "processing"
    ):
        version = None
    await session.commit()
    return version


async def _commit_page(
    session: AsyncSession,
    claim: SourceReadClaim,
    result: PageReadingResult | None,
    *,
    expected_version: int | None,
    last_page: int,
) -> tuple[SourceReadClaim | None, bool]:
    document = await _owned_source(session, claim)
    if document is None:
        await session.rollback()
        return None, False
    if not document.active_for_ai:
        return await _finish(session, claim, status="superseded"), False
    state = await session.get(
        PageReviewStateModel,
        (claim.document_id, claim.next_page),
        with_for_update=True,
        populate_existing=True,
    )
    version = state.version if state is not None else 0
    accepted = result is not None and version == expected_version
    if claim.page_number is None and await _protected_page(session, state):
        accepted = False
    if claim.page_number is not None and not accepted:
        return await _finish(session, claim, status="superseded"), False
    failure = claim.failure_code
    if accepted and result is not None:
        if (
            result.page_number != claim.next_page
            or not 1 <= len(result.candidates) <= 2
            or any(candidate.method not in {"native", "ocr"} for candidate in result.candidates)
            or (
                result.failure_code is not None
                and (
                    not result.failure_code.isascii()
                    or not result.failure_code.replace("_", "").isalnum()
                    or len(result.failure_code) > 64
                )
            )
        ):
            raise ValueError("source page reader returned an invalid result")
        fidelity = PageFidelityService(session)
        for candidate in result.candidates:
            state = await fidelity.record_candidate(
                claim.document_id,
                claim.next_page,
                raw_text=candidate.raw_text,
                method=candidate.method,
                actor_id=claim.requested_by,
                provenance={
                    **candidate.provenance,
                    "job_id": str(claim.id),
                    "attempt": claim.attempts,
                    "page_number": claim.next_page,
                    "automatic_verification": False,
                },
                expected_version=version,
                commit=False,
            )
            version = state.version
        if result.failure_code is not None and state is not None:
            await fidelity._advance(
                state,
                candidate_id=state.current_candidate_id,
                action="reread_failed",
                target="failed",
                actor_id=claim.requested_by,
                reason="Source reading requires attention before review",
                payload={"job_id": str(claim.id), "failure_code": result.failure_code},
            )
            failure = failure or result.failure_code
    next_page = claim.next_page + 1
    status = ("failed" if failure else "completed") if next_page > last_page else "running"
    progress = await _save_progress(
        session, claim, status=status, next_page=next_page, failure_code=failure
    )
    return progress, accepted and progress is not None


@asynccontextmanager
async def _open_reading(
    storage: SourceReadStorage,
    key: str,
    reader: PageReader,
    configuration: PageReadingConfiguration,
) -> AsyncIterator[tuple[PageReadingSession, int, ThreadPoolExecutor]]:
    loop = asyncio.get_running_loop()
    stack = ExitStack()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="source-page-reader")

    def open_session() -> tuple[PageReadingSession, int]:
        source = stack.enter_context(storage.open_source(key))
        opened = stack.enter_context(reader.open(source, configuration=configuration))
        return opened, opened.page_count

    try:
        opened, count = await loop.run_in_executor(executor, open_session)
        yield opened, count, executor
    finally:
        try:
            await asyncio.shield(loop.run_in_executor(executor, stack.close))
        finally:
            executor.shutdown(wait=False)


async def run_source_read(
    session: AsyncSession,
    job_id: UUID,
    *,
    storage: SourceReadStorage,
    reader: PageReader | None = None,
    image_artifacts: PageImageArtifacts | None = None,
) -> SourceReadResult:
    claim = await claim_source_read(session, job_id)
    processed = 0
    if claim is None:
        return await _current_result(session, job_id, claimed=False, pages_processed=0)
    deadline = time.monotonic() + SOURCE_READ_EXECUTION_SECONDS
    try:
        try:
            configuration = PageReadingConfiguration.from_dict(claim.configuration)
        except (TypeError, ValueError):
            await _finish(
                session, claim, status="failed", failure_code="reading_configuration_invalid"
            )
            return await _current_result(session, job_id, claimed=True, pages_processed=0)
        source = await session.get(SourceDocumentModel, claim.document_id, populate_existing=True)
        if source is None:
            raise FidelitySourceNotFoundError("source_document_not_found")
        key = source.object_key
        if not source.active_for_ai:
            await _finish(session, claim, status="superseded")
            return await _current_result(session, job_id, claimed=True, pages_processed=0)
        on_render = (
            image_artifacts.observer(
                SourceImageIdentity(
                    source.id,
                    source.checksum_sha256,
                    source.object_key,
                    source.size_bytes,
                    source.original_page_count,
                    source.original_filename,
                )
            )
            if image_artifacts is not None and reader is None
            else None
        )
        await session.commit()
        selected = reader or FilePageReader(execution_deadline=deadline, on_render=on_render)
        async with _open_reading(storage, key, selected, configuration) as (
            opened,
            count,
            executor,
        ):
            current = await _check_page_count(session, claim, count)
            if current is None or current.status != "running":
                return await _current_result(session, job_id, claimed=True, pages_processed=0)
            last_page = claim.page_number or count
            for _ in range(SOURCE_READ_BATCH_SIZE):
                if time.monotonic() >= deadline:
                    break
                version = await _page_version(session, claim)
                result = (
                    await asyncio.get_running_loop().run_in_executor(
                        executor, opened.read_page, claim.next_page
                    )
                    if version is not None
                    else None
                )
                current, accepted = await _commit_page(
                    session, claim, result, expected_version=version, last_page=last_page
                )
                processed += int(accepted)
                if current is None or current.status != "running":
                    return await _current_result(
                        session, job_id, claimed=True, pages_processed=processed
                    )
                claim = current
        await _finish(session, claim, status="queued")
    except (OCRInputError, OCRConfigError, ObjectStorageOperationError, OSError) as error:
        await session.rollback()
        code = "source_input_rejected" if isinstance(error, OCRInputError) else "source_unavailable"
        if isinstance(error, OCRConfigError):
            code = "reading_configuration_invalid"
        await _finish(session, claim, status="failed", failure_code=code)
    except BaseException:
        await session.rollback()
        raise
    return await _current_result(session, job_id, claimed=True, pages_processed=processed)


async def dispatch_source_read(job_id: UUID, dispatcher: SourceReadDispatcher) -> bool:
    try:
        await asyncio.to_thread(dispatcher.dispatch, job_id)
    except Exception:
        _logger.error("source page reading dispatch failed")
        return False
    return True


async def recover_source_reads(
    session: AsyncSession,
    dispatcher: SourceReadDispatcher,
    *,
    now: datetime | None = None,
    batch_size: int = SOURCE_READ_RECOVERY_BATCH_SIZE,
    outbox_min_age_seconds: int = SOURCE_READ_OUTBOX_MIN_AGE_SECONDS,
) -> SourceReadRecoveryResult:
    _integer(batch_size, minimum=1, maximum=1000)
    _integer(outbox_min_age_seconds, minimum=1, maximum=3600)
    current = _now(now)
    jobs = list(
        await session.scalars(
            select(SourceReadJobModel)
            .where(
                or_(
                    and_(
                        SourceReadJobModel.status == "queued",
                        SourceReadJobModel.updated_at
                        <= current - timedelta(seconds=outbox_min_age_seconds),
                    ),
                    and_(
                        SourceReadJobModel.status == "running",
                        or_(
                            SourceReadJobModel.lease_expires_at <= current,
                            SourceReadJobModel.lease_expires_at.is_(None),
                        ),
                    ),
                )
            )
            .order_by(SourceReadJobModel.updated_at, SourceReadJobModel.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
    identifiers = []
    for job in jobs:
        identifiers.append(job.id)
        job.status = "queued"
        job.lease_token = None
        job.lease_expires_at = None
        job.version += 1
        job.updated_at = current
    await session.commit()
    enqueued = 0
    for job_id in identifiers:
        enqueued += int(await dispatch_source_read(job_id, dispatcher))
    return SourceReadRecoveryResult(enqueued=enqueued, failures=len(identifiers) - enqueued)


async def _read_source(job_id: UUID) -> None:
    settings = Settings()
    resources = create_resources(settings)
    storage = None
    try:
        storage = create_object_storage(settings)
        async with resources.session_factory() as session:
            result = await run_source_read(
                session,
                job_id,
                storage=storage,
                image_artifacts=create_page_image_artifacts(settings),
            )
        if result.claimed and result.status == "queued":
            await dispatch_source_read(job_id, DramatiqSourceReadDispatcher())
    finally:
        try:
            if storage is not None:
                storage.close()
        finally:
            await resources.close()


async def _recover_source_read_jobs() -> None:
    resources = create_resources(Settings())
    try:
        async with resources.session_factory() as session:
            await recover_source_reads(session, DramatiqSourceReadDispatcher())
    finally:
        await resources.close()


@dramatiq.actor(
    queue_name=SOURCE_READ_QUEUE_NAME, max_retries=0, time_limit=SOURCE_READ_TIME_LIMIT_MS
)
def read_source(job_id: str) -> None:
    asyncio.run(_read_source(UUID(job_id)))


@dramatiq.actor(
    queue_name=SOURCE_READ_QUEUE_NAME, max_retries=0, time_limit=SOURCE_READ_TIME_LIMIT_MS
)
def recover_source_read_jobs() -> None:
    asyncio.run(_recover_source_read_jobs())


class _QueuedMessage(Protocol):
    message_id: str


class _SourceReadActor(Protocol):
    def send(self, job_id: str) -> _QueuedMessage: ...


class DramatiqSourceReadDispatcher:
    def __init__(self, actor: _SourceReadActor | None = None) -> None:
        self._actor = actor or cast(_SourceReadActor, read_source)

    def dispatch(self, job_id: UUID) -> str:
        return self._actor.send(str(job_id)).message_id


def create_source_read_dispatcher(settings: Settings) -> DramatiqSourceReadDispatcher:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    for actor in (read_source, recover_source_read_jobs):
        actor.broker = broker
        broker.declare_actor(actor)
    return DramatiqSourceReadDispatcher()
