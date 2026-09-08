import asyncio
import hashlib
import threading
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Literal, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents import page_reading_jobs as reading_jobs
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.fidelity_models import (
    PageGroundTruthModel,
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
    SourceReadJobModel,
)
from exam_guru_api.documents.fidelity_queries import confirm_source_page, get_review_workspace
from exam_guru_api.documents.fidelity_schemas import PageConfirmRequest
from exam_guru_api.documents.fidelity_service import (
    FidelitySourceNotFoundError,
    PageFidelityConflictError,
    PageFidelityService,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.ocr import OCRConfigError, OCRInputError
from exam_guru_api.documents.page_reading import (
    PageReadingCandidate,
    PageReadingConfiguration,
    PageReadingResult,
    PageReadingSession,
)
from exam_guru_api.documents.page_reading_jobs import (
    SourceReadClaim,
    claim_source_read,
    queue_source_read,
    recover_source_reads,
    run_source_read,
)
from exam_guru_api.documents.service import SourceDocumentService
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.infrastructure.object_storage import ObjectStorage, ObjectStorageOperationError
from tests.test_tesseract_file_input import source_pdf

ACTOR = UUID(int=81002)


@pytest.fixture(scope="module")
def reading_database_url() -> Iterator[str]:
    with PostgresContainer(
        image="pgvector/pgvector:0.8.6-pg18-trixie",
        username="exam_guru",
        password=uuid4().hex,
        dbname="page_reading_test",
        driver="asyncpg",
    ) as postgres:
        url = postgres.get_connection_url()
        upgrade_database(url)
        yield url


@asynccontextmanager
async def database(url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class SourceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.opens = 0

    @contextmanager
    def open_source(self, key: str) -> Iterator[BinaryIO]:
        assert key.startswith("sources/")
        self.opens += 1
        with self.path.open("rb") as source:
            yield source

    def get_bytes(self, key: str) -> bytes:
        raise AssertionError("whole-PDF bytes access is forbidden")


async def add_source(session: AsyncSession, path: Path, *, pages: int | None) -> UUID:
    document_id = uuid4()
    checksum = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
    size_bytes = (await asyncio.to_thread(path.stat)).st_size
    session.add(
        SourceDocumentModel(
            id=document_id,
            checksum_sha256=checksum,
            object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
            original_filename="page-reading-fixture.pdf",
            content_type="application/pdf",
            size_bytes=size_bytes,
            document_type=SourceDocumentType.TEACHER_GUIDE,
            created_by=ACTOR,
            updated_by=ACTOR,
            original_page_count=pages,
        )
    )
    await session.commit()
    return document_id


class RecordingDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.dispatched: list[UUID] = []
        self.fail = fail

    def dispatch(self, job_id: UUID) -> str:
        self.dispatched.append(job_id)
        if self.fail:
            raise ConnectionError("queue unavailable")
        return str(uuid4())


class FixtureReader:
    def __init__(
        self, pages: int, *, fail_on: int | None = None, ocr_failure: bool = False
    ) -> None:
        self.page_count = pages
        self.calls: list[int] = []
        self.fail_on = fail_on
        self.ocr_failure = ocr_failure
        self.started: threading.Event | None = None
        self.release: threading.Event | None = None

    @contextmanager
    def open(
        self, source: BinaryIO, *, configuration: PageReadingConfiguration
    ) -> Iterator["FixtureReader"]:
        assert source.fileno() >= 0
        yield self

    def read_page(self, page_number: int) -> PageReadingResult:
        self.calls.append(page_number)
        if self.started is not None and self.release is not None:
            self.started.set()
            assert self.release.wait(10)
        if page_number == self.fail_on:
            raise RuntimeError("simulated worker crash")
        candidates: tuple[PageReadingCandidate, ...] = (
            PageReadingCandidate(
                raw_text=f"Read the question {page_number}",
                method="native",
                provenance={"engine": "fixture", "engine_version": "1", "languages": ["en"]},
            ),
        )
        if self.ocr_failure:
            candidates += (
                PageReadingCandidate(
                    raw_text="",
                    method="ocr",
                    provenance={
                        "engine": "tesseract-cli",
                        "engine_version": "5.4.1",
                        "languages": ["si"],
                        "ocr_languages": ["sin", "eng"],
                        "failure_code": "ocr_timeout",
                    },
                ),
            )
        return PageReadingResult(
            page_number=page_number,
            candidates=candidates,
            failure_code="ocr_timeout" if self.ocr_failure else None,
        )


async def candidate_count(session: AsyncSession, document_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(PageTextCandidateModel)
            .where(PageTextCandidateModel.document_id == document_id)
        )
        or 0
    )


@pytest.mark.integration
def test_seventeen_pages_complete_across_batches_with_duplicate_delivery_and_failed_dispatch(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "seventeen.pdf", pages=17)
        storage = SourceStore(path)
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=17)
                job = await queue_source_read(session, doc_id, actor_id=ACTOR)
                duplicate = await queue_source_read(session, doc_id, actor_id=ACTOR)
                assert duplicate.id == job.id
                job_id = job.id
            results = []
            for _ in range(3):
                async with sessions() as session:
                    results.append(await run_source_read(session, job_id, storage=storage))
            assert [result.next_page for result in results] == [9, 17, 18]
            assert [result.status for result in results] == ["queued", "queued", "completed"]
            assert [result.pages_processed for result in results] == [8, 8, 1]
            async with sessions() as session:
                repeated = await run_source_read(session, job_id, storage=storage)
                assert not repeated.claimed
                assert await candidate_count(session, doc_id) == 17
                assert not await PageFidelityService(session).document_is_verified(doc_id)
                assert not list(
                    await session.scalars(
                        select(PageReviewStateModel).where(
                            PageReviewStateModel.document_id == doc_id,
                            PageReviewStateModel.state == "verified",
                        )
                    )
                )
                job = await queue_source_read(session, doc_id, actor_id=ACTOR)
                dispatcher = RecordingDispatcher(fail=True)
                recovered = await recover_source_reads(
                    session, dispatcher, now=datetime.now(UTC) + timedelta(minutes=10)
                )
                assert recovered.failures >= 1
                assert job.id in dispatcher.dispatched
                current = await session.get(SourceReadJobModel, job.id, populate_existing=True)
                assert current is not None
                assert current.status == "queued"
        assert storage.opens == 3

    asyncio.run(scenario())


@pytest.mark.integration
def test_crash_recovery_continues_at_first_uncommitted_page_not_full_batch(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "resume.pdf", pages=17)
        reader = FixtureReader(17, fail_on=4)
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=17)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
                with pytest.raises(RuntimeError, match="simulated worker crash"):
                    await run_source_read(session, job_id, storage=SourceStore(path), reader=reader)
            async with sessions() as session:
                job = await session.get(SourceReadJobModel, job_id)
                assert job is not None
                assert job.next_page == 4
                assert job.status == "running"
                assert await candidate_count(session, doc_id) == 3
                dispatcher = RecordingDispatcher()
                await recover_source_reads(
                    session, dispatcher, now=datetime.now(UTC) + timedelta(hours=1)
                )
                assert job_id in dispatcher.dispatched
            reader.fail_on = None
            for _ in range(2):
                async with sessions() as session:
                    result = await run_source_read(
                        session, job_id, storage=SourceStore(path), reader=reader
                    )
            assert result.status == "completed"
            assert reader.calls == [1, 2, 3, 4, *range(4, 18)]
            async with sessions() as session:
                assert await candidate_count(session, doc_id) == 17

    asyncio.run(scenario())


@pytest.mark.integration
def test_concurrent_claims_have_one_lease_and_expiry_fences_old_token(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "lease.pdf")
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id

            async def claim() -> SourceReadClaim | None:
                async with sessions() as session:
                    return await claim_source_read(session, job_id)

            claims = await asyncio.gather(claim(), claim())
            assert sum(value is not None for value in claims) == 1
            previous = next(value for value in claims if value is not None)
            async with sessions() as session:
                recovered = await claim_source_read(
                    session, job_id, now=datetime.now(UTC) + timedelta(hours=1)
                )
                assert recovered is not None
                assert recovered.lease_token != previous.lease_token
                assert recovered.next_page == 1
                assert recovered.attempts == 2

    asyncio.run(scenario())


@pytest.mark.integration
def test_native_and_failed_ocr_candidates_commit_with_cursor_and_stay_untrusted(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "timeout.pdf", pages=17)
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=17)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            for _ in range(3):
                async with sessions() as session:
                    result = await run_source_read(
                        session,
                        job_id,
                        storage=SourceStore(path),
                        reader=FixtureReader(17, ocr_failure=True),
                    )
            assert result.status == "failed"
            assert result.next_page == 18
            async with sessions() as session:
                assert await candidate_count(session, doc_id) == 34
                states = list(
                    await session.scalars(
                        select(PageReviewStateModel).where(
                            PageReviewStateModel.document_id == doc_id
                        )
                    )
                )
                assert len(states) == 17
                assert {state.state for state in states} == {"failed"}
                assert not await PageFidelityService(session).document_is_verified(doc_id)
                job = await session.get(SourceReadJobModel, job_id)
                assert job is not None
                assert job.failure_code == "ocr_timeout"
                native = await session.scalar(
                    select(PageTextCandidateModel).where(
                        PageTextCandidateModel.document_id == doc_id,
                        PageTextCandidateModel.page_number == 1,
                        PageTextCandidateModel.method == "native",
                    )
                )
                assert native is not None
                assert native.raw_text_utf8 == b"Read the question 1"
                assert native.provenance["job_id"] == str(job_id)
                assert native.provenance["attempt"] == 1

    asyncio.run(scenario())


@pytest.mark.integration
def test_error_between_native_and_ocr_rolls_back_both_candidates_and_cursor(
    reading_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = cast(
        Callable[..., Awaitable[PageReviewStateModel]], PageFidelityService.record_candidate
    )

    async def interrupted(
        self: PageFidelityService, *args: object, **kwargs: object
    ) -> PageReviewStateModel:
        assert kwargs.get("commit") is False
        if kwargs.get("method") == "ocr":
            raise RuntimeError("crash between candidates")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PageFidelityService, "record_candidate", interrupted)

    async def scenario() -> None:
        path = source_pdf(tmp_path / "atomic.pdf")
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
                with pytest.raises(RuntimeError, match="crash between candidates"):
                    await run_source_read(
                        session,
                        job_id,
                        storage=SourceStore(path),
                        reader=FixtureReader(1, ocr_failure=True),
                    )
            async with sessions() as session:
                assert await candidate_count(session, doc_id) == 0
                job = await session.get(SourceReadJobModel, job_id)
                assert job is not None
                assert job.next_page == 1

    asyncio.run(scenario())


@pytest.mark.integration
def test_single_reread_invalidates_verified_head_and_duplicate_request_is_idempotent(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "reread.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            fidelity = PageFidelityService(session)
            state = await fidelity.record_candidate(
                doc_id,
                1,
                raw_text="Read the question",
                method="native",
                actor_id=ACTOR,
                provenance={"languages": ["en"]},
            )
            state = await fidelity.confirm_page(
                doc_id,
                1,
                candidate_id=state.current_candidate_id,
                expected_version=state.version,
                actor_id=ACTOR,
                reason="Compared the original",
            )
            version = state.version
            job = await queue_source_read(
                session,
                doc_id,
                page_number=1,
                expected_page_version=version,
                actor_id=ACTOR,
                reason="Read the original page again",
            )
            duplicate = await queue_source_read(
                session,
                doc_id,
                page_number=1,
                expected_page_version=version,
                actor_id=ACTOR,
                reason="Read the original page again",
            )
            assert duplicate.id == job.id
            await session.refresh(state)
            assert state.state == "processing"
            assert state.version == version + 1
            assert job.expected_page_version == state.version
            assert not await fidelity.page_is_verified(doc_id, 1)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageReviewEventModel)
                    .where(
                        PageReviewEventModel.document_id == doc_id,
                        PageReviewEventModel.action == "reread_requested",
                    )
                )
                == 1
            )
            await fidelity.exclude_page(
                doc_id,
                1,
                expected_version=state.version,
                actor_id=ACTOR,
                reason="This page must not be used",
            )
            result = await run_source_read(
                session, job.id, storage=SourceStore(path), reader=FixtureReader(1)
            )
            assert result.status == "superseded"
            await session.refresh(state)
            assert state.state == "excluded"
            assert await candidate_count(session, doc_id) == 1

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("manual_action", ["edit", "exclude"])
def test_manual_change_during_read_cannot_be_overwritten_by_stale_worker(
    reading_database_url: str, tmp_path: Path, manual_action: str
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "manual.pdf")
        reader = FixtureReader(1)
        reader.started = threading.Event()
        reader.release = threading.Event()
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                state = await PageFidelityService(session).record_candidate(
                    doc_id,
                    1,
                    raw_text="Read the original question",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"languages": ["en"]},
                )
                expected = state.version
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id

            async def work() -> object:
                async with sessions() as session:
                    return await run_source_read(
                        session, job_id, storage=SourceStore(path), reader=reader
                    )

            task = asyncio.create_task(work())
            assert await asyncio.to_thread(reader.started.wait, 5)
            try:
                async with sessions() as session:
                    fidelity = PageFidelityService(session)
                    if manual_action == "edit":
                        changed = await fidelity.edit_page(
                            doc_id,
                            1,
                            text="Manually corrected source",
                            expected_version=expected,
                            actor_id=ACTOR,
                            reason="Compared source",
                        )
                    else:
                        changed = await fidelity.exclude_page(
                            doc_id,
                            1,
                            expected_version=expected,
                            actor_id=ACTOR,
                            reason="Do not use this page",
                        )
                    changed_id, changed_version = changed.current_candidate_id, changed.version
            finally:
                reader.release.set()
            await task
            async with sessions() as session:
                current_state = await session.get(PageReviewStateModel, (doc_id, 1))
                assert current_state is not None
                assert current_state.version == changed_version
                assert current_state.current_candidate_id == changed_id
                assert current_state.state == (
                    "needs_review" if manual_action == "edit" else "excluded"
                )
                assert await candidate_count(session, doc_id) == (
                    2 if manual_action == "edit" else 1
                )

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("pages", [19, 1001])
def test_large_file_backed_source_finishes_without_a_total_page_or_attempt_cap(
    reading_database_url: str, tmp_path: Path, pages: int
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "large-source.pdf", pages=pages)
        storage = SourceStore(path)
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=pages)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            total = 0
            for invocation in range((pages + 7) // 8):
                async with sessions() as session:
                    result = await run_source_read(session, job_id, storage=storage)
                    assert result.claimed
                    assert 1 <= result.pages_processed <= 8
                    total += result.pages_processed
                    assert result.next_page == min((invocation + 1) * 8, pages) + 1
            assert total == pages
            assert result.status == "completed"
            assert storage.opens == (pages + 7) // 8
            async with sessions() as session:
                assert await candidate_count(session, doc_id) == pages
                assert not await PageFidelityService(session).document_is_verified(doc_id)
                last = await session.scalar(
                    select(PageTextCandidateModel).where(
                        PageTextCandidateModel.document_id == doc_id,
                        PageTextCandidateModel.page_number == pages,
                    )
                )
                assert last is not None
                assert str(pages).encode() in last.raw_text_utf8

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("selected_page", [1, 2, 3], ids=["verified", "excluded", "human"])
def test_whole_document_reprocessing_protects_reviewed_work_but_explicit_reread_keeps_history(
    reading_database_url: str, tmp_path: Path, selected_page: int
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "reviewed.pdf", pages=4)
        reader = FixtureReader(4)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=4)
            fidelity = PageFidelityService(session)
            protected: dict[int, tuple[int, UUID | None, UUID | None, str]] = {}
            for number in (1, 2, 3):
                state = await fidelity.record_candidate(
                    doc_id,
                    number,
                    raw_text="Read the question",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"languages": ["en"]},
                )
                if number == 1:
                    state = await fidelity.confirm_page(
                        doc_id,
                        number,
                        candidate_id=state.current_candidate_id,
                        expected_version=state.version,
                        actor_id=ACTOR,
                        reason="Compared original",
                    )
                elif number == 2:
                    state = await fidelity.exclude_page(
                        doc_id,
                        number,
                        expected_version=state.version,
                        actor_id=ACTOR,
                        reason="Not educational content",
                    )
                else:
                    state = await fidelity.edit_page(
                        doc_id,
                        number,
                        text="Read the manually corrected question",
                        expected_version=state.version,
                        actor_id=ACTOR,
                        reason="Corrected after original comparison",
                    )
                protected[number] = (
                    state.version,
                    state.current_candidate_id,
                    state.event_id,
                    state.state,
                )
            original_events = [
                (
                    event.id,
                    event.candidate_id,
                    event.version,
                    event.state,
                    event.action,
                    event.payload,
                )
                for event in await session.scalars(
                    select(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == doc_id)
                    .order_by(PageReviewEventModel.page_number, PageReviewEventModel.version)
                )
            ]
            for attempt in (1, 2):
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
                result = await run_source_read(
                    session, job_id, storage=SourceStore(path), reader=reader
                )
                assert result.status == "completed"
                assert result.pages_processed == 1
                assert reader.calls == [4] * attempt
                assert await candidate_count(session, doc_id) == 4 + attempt
                for number, expected in protected.items():
                    current = await session.get(
                        PageReviewStateModel, (doc_id, number), populate_existing=True
                    )
                    assert current is not None
                    assert (
                        current.version,
                        current.current_candidate_id,
                        current.event_id,
                        current.state,
                    ) == expected
            version, previous_id, _, _ = protected[selected_page]
            with pytest.raises(PageFidelityConflictError, match="source_page_version_conflict"):
                await queue_source_read(
                    session,
                    doc_id,
                    page_number=selected_page,
                    expected_page_version=version - 1,
                    actor_id=ACTOR,
                )
            reread = await queue_source_read(
                session,
                doc_id,
                page_number=selected_page,
                expected_page_version=version,
                actor_id=ACTOR,
                reason="Explicitly read the protected original again",
            )
            duplicate = await queue_source_read(
                session,
                doc_id,
                page_number=selected_page,
                expected_page_version=version,
                actor_id=ACTOR,
            )
            assert duplicate.id == reread.id
            assert reread.configuration["force_ocr"] is True
            current = await session.get(
                PageReviewStateModel, (doc_id, selected_page), populate_existing=True
            )
            assert current is not None
            assert current.state == "processing"
            assert current.version == version + 1
            assert current.current_candidate_id == previous_id
            assert not await fidelity.page_is_verified(doc_id, selected_page)
            result = await run_source_read(
                session, reread.id, storage=SourceStore(path), reader=reader
            )
            assert result.status == "completed"
            assert result.pages_processed == 1
            await session.refresh(current)
            assert current.state == "needs_review"
            assert current.version == version + 2
            assert current.current_candidate_id != previous_id
            assert not await fidelity.page_is_verified(doc_id, selected_page)
            assert reader.calls == [4, 4, selected_page]
            assert await candidate_count(session, doc_id) == 7
            replacement = await session.get(PageTextCandidateModel, current.current_candidate_id)
            previous = await session.get(PageTextCandidateModel, previous_id)
            assert replacement is not None
            assert previous is not None
            assert replacement.parent_candidate_id == previous_id
            assert previous.method == ("human" if selected_page == 3 else "native")
            retained_events = list(
                await session.scalars(
                    select(PageReviewEventModel)
                    .where(PageReviewEventModel.id.in_([event[0] for event in original_events]))
                    .order_by(PageReviewEventModel.page_number, PageReviewEventModel.version)
                )
            )
            assert [
                (
                    event.id,
                    event.candidate_id,
                    event.version,
                    event.state,
                    event.action,
                    event.payload,
                )
                for event in retained_events
            ] == original_events
            page_events = list(
                await session.scalars(
                    select(PageReviewEventModel)
                    .where(
                        PageReviewEventModel.document_id == doc_id,
                        PageReviewEventModel.page_number == selected_page,
                    )
                    .order_by(PageReviewEventModel.version)
                )
            )
            assert [event.action for event in page_events[-2:]] == [
                "reread_requested",
                "candidate_recorded",
            ]
            assert [event.version for event in page_events[-2:]] == [version + 1, version + 2]

    asyncio.run(scenario())


@pytest.mark.integration
def test_recovered_lease_fences_old_worker_candidates_and_terminal_updates(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "stale-lease.pdf")
        old_reader = FixtureReader(1, ocr_failure=True)
        old_reader.started = threading.Event()
        old_reader.release = threading.Event()
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id

            async def old_work() -> None:
                async with sessions() as session:
                    await run_source_read(
                        session, job_id, storage=SourceStore(path), reader=old_reader
                    )

            task = asyncio.create_task(old_work())
            assert await asyncio.to_thread(old_reader.started.wait, 5)
            try:
                async with sessions() as session:
                    await recover_source_reads(
                        session,
                        RecordingDispatcher(),
                        now=datetime.now(UTC) + timedelta(hours=1),
                    )
                async with sessions() as session:
                    result = await run_source_read(
                        session, job_id, storage=SourceStore(path), reader=FixtureReader(1)
                    )
                    assert result.status == "completed"
            finally:
                old_reader.release.set()
            await task
            async with sessions() as session:
                job = await session.get(SourceReadJobModel, job_id)
                assert job is not None
                assert job.status == "completed"
                assert job.failure_code is None
                assert job.next_page == 2
                assert job.attempts == 2
                assert await candidate_count(session, doc_id) == 1
                state = await session.get(PageReviewStateModel, (doc_id, 1))
                assert state is not None
                assert state.state == "needs_review"

    asyncio.run(scenario())


@pytest.mark.integration
def test_concurrent_explicit_rereads_create_one_job_and_one_processing_event(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "duplicate-reread.pdf")
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)

            async def enqueue() -> UUID:
                async with sessions() as session:
                    return (
                        await queue_source_read(
                            session,
                            doc_id,
                            page_number=1,
                            expected_page_version=0,
                            actor_id=ACTOR,
                            reason="Read this page",
                        )
                    ).id

            first, second = await asyncio.gather(enqueue(), enqueue())
            assert first == second
            async with sessions() as session:
                state = await session.get(PageReviewStateModel, (doc_id, 1))
                assert state is not None
                assert state.version == 1
                assert state.state == "processing"
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PageReviewEventModel)
                        .where(PageReviewEventModel.document_id == doc_id)
                    )
                    == 1
                )

    asyncio.run(scenario())


@pytest.mark.integration
def test_initial_delivery_does_not_overwrite_a_human_edit_saved_after_queueing(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "already-edited.pdf")
        reader = FixtureReader(1)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            state = await PageFidelityService(session).edit_page(
                doc_id,
                1,
                text="Manually compared question",
                expected_version=0,
                actor_id=ACTOR,
                reason="Corrected before background delivery",
            )
            candidate_id, version = state.current_candidate_id, state.version
            result = await run_source_read(
                session, job_id, storage=SourceStore(path), reader=reader
            )
            assert result.status == "completed"
            assert result.pages_processed == 0
            assert reader.calls == []
            await session.refresh(state)
            assert state.current_candidate_id == candidate_id
            assert state.version == version
            assert await candidate_count(session, doc_id) == 1

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("known_pages", [None, 2])
def test_original_page_count_is_discovered_once_and_mismatches_fail_closed(
    reading_database_url: str, tmp_path: Path, known_pages: int | None
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "count.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=known_pages)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            result = await run_source_read(session, job_id, storage=SourceStore(path))
            document = await session.get(SourceDocumentModel, doc_id, populate_existing=True)
            assert document is not None
            if known_pages is None:
                assert result.status == "completed"
                assert document.original_page_count == 1
                assert await candidate_count(session, doc_id) == 1
            else:
                assert result.status == "failed"
                assert result.failure_code == "source_page_count_mismatch"
                assert document.original_page_count == known_pages
                assert await candidate_count(session, doc_id) == 0

    asyncio.run(scenario())


@pytest.mark.integration
def test_expired_lease_alone_prevents_publication_before_another_worker_claims(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "expired.pdf")
        reader = FixtureReader(1)
        reader.started = threading.Event()
        reader.release = threading.Event()
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id

            async def work() -> None:
                async with sessions() as session:
                    await run_source_read(session, job_id, storage=SourceStore(path), reader=reader)

            task = asyncio.create_task(work())
            assert await asyncio.to_thread(reader.started.wait, 5)
            try:
                async with sessions() as session:
                    job = await session.get(SourceReadJobModel, job_id)
                    assert job is not None
                    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    await session.commit()
            finally:
                reader.release.set()
            await task
            async with sessions() as session:
                current = await session.get(SourceReadJobModel, job_id)
                assert current is not None
                assert current.status == "running"
                assert current.next_page == 1
                assert await candidate_count(session, doc_id) == 0

    asyncio.run(scenario())


async def remove_source(session: AsyncSession, document_id: UUID) -> None:
    document = await session.get(SourceDocumentModel, document_id)
    assert document is not None
    await SourceDocumentService(
        session, cast(ObjectStorage, object()), max_upload_bytes=1024
    ).remove_from_ai_use(
        document_id,
        reason="The selected source must no longer be used",
        expected_version=document.metadata_scope_version,
        actor_id=ACTOR,
    )


@pytest.mark.integration
@pytest.mark.parametrize("known_pages", [None, 1])
def test_reread_queue_rejects_pages_missing_from_the_original_without_inventing_state(
    reading_database_url: str, tmp_path: Path, known_pages: int | None
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "absent-page.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=known_pages)
            with pytest.raises(FidelitySourceNotFoundError, match="source_page_not_found"):
                await queue_source_read(
                    session, doc_id, actor_id=ACTOR, page_number=2, expected_page_version=0
                )
            assert not session.in_transaction()
            assert await session.get(PageReviewStateModel, (doc_id, 2)) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceReadJobModel)
                    .where(SourceReadJobModel.document_id == doc_id)
                )
                == 0
            )

    asyncio.run(scenario())


@pytest.mark.integration
def test_removed_source_cannot_queue_any_new_reading(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "removed-queue.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            await remove_source(session, doc_id)
            with pytest.raises(PageFidelityConflictError, match="source_document_removed"):
                await queue_source_read(session, doc_id, actor_id=ACTOR)
            assert not session.in_transaction()
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceReadJobModel)
                    .where(SourceReadJobModel.document_id == doc_id)
                )
                == 0
            )
            assert await candidate_count(session, doc_id) == 0

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("page_number", [None, 1])
def test_duplicate_queue_requests_cannot_change_the_active_job_configuration(
    reading_database_url: str, tmp_path: Path, page_number: int | None
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "configuration-conflict.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job = await queue_source_read(
                session,
                doc_id,
                actor_id=ACTOR,
                page_number=page_number,
                expected_page_version=0 if page_number else None,
            )
            job_id, initial_configuration = job.id, dict(job.configuration)
            with pytest.raises(
                PageFidelityConflictError, match="source_read_configuration_conflict"
            ):
                await queue_source_read(
                    session,
                    doc_id,
                    actor_id=ACTOR,
                    page_number=page_number,
                    expected_page_version=0 if page_number else None,
                    configuration=PageReadingConfiguration(expected_languages=("ta",)),
                )
            assert not session.in_transaction()
            current = await session.get(SourceReadJobModel, job_id)
            assert current is not None
            assert current.configuration == initial_configuration
            assert current.status == "queued"
            assert current.version == 0
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceReadJobModel)
                    .where(SourceReadJobModel.document_id == doc_id)
                )
                == 1
            )

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("intervening_exclusion", [False, True])
def test_duplicate_reread_requires_the_same_request_revision_and_current_processing_head(
    reading_database_url: str, tmp_path: Path, intervening_exclusion: bool
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "revision-conflict.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (
                await queue_source_read(
                    session, doc_id, actor_id=ACTOR, page_number=1, expected_page_version=0
                )
            ).id
            if intervening_exclusion:
                await PageFidelityService(session).exclude_page(
                    doc_id, 1, expected_version=1, actor_id=ACTOR, reason="Do not use this page"
                )
            with pytest.raises(PageFidelityConflictError, match="source_page_version_conflict"):
                await queue_source_read(
                    session,
                    doc_id,
                    actor_id=ACTOR,
                    page_number=1,
                    expected_page_version=0 if intervening_exclusion else 1,
                )
            state = await session.get(PageReviewStateModel, (doc_id, 1))
            assert state is not None
            assert state.state == ("excluded" if intervening_exclusion else "processing")
            assert state.version == (2 if intervening_exclusion else 1)
            current = await session.get(SourceReadJobModel, job_id)
            assert current is not None
            assert current.expected_page_version == 1
            assert await candidate_count(session, doc_id) == 0

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("reason", ["  ", "Unsafe\nreason"])
def test_invalid_reread_reason_rolls_back_the_provisional_page_state_and_job(
    reading_database_url: str, tmp_path: Path, reason: str
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "invalid-reason.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            with pytest.raises(ValueError, match="review reason"):
                await queue_source_read(
                    session,
                    doc_id,
                    actor_id=ACTOR,
                    page_number=1,
                    expected_page_version=0,
                    reason=reason,
                )
            assert not session.in_transaction()
            assert await session.get(PageReviewStateModel, (doc_id, 1)) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceReadJobModel)
                    .where(SourceReadJobModel.document_id == doc_id)
                )
                == 0
            )

    asyncio.run(scenario())


class PausingOpenReader(FixtureReader):
    @contextmanager
    def open(
        self, source: BinaryIO, *, configuration: PageReadingConfiguration
    ) -> Iterator[FixtureReader]:
        assert self.started is not None
        assert self.release is not None
        self.started.set()
        assert self.release.wait(10)
        with super().open(source, configuration=configuration) as opened:
            yield opened


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["before_delivery", "opening", "reading"])
def test_source_removal_fences_worker_at_each_external_io_boundary(
    reading_database_url: str, tmp_path: Path, phase: str
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "removed-running.pdf")
        reader = PausingOpenReader(1) if phase == "opening" else FixtureReader(1)
        reader.started, reader.release = threading.Event(), threading.Event()
        storage = SourceStore(path)
        async with database(reading_database_url) as sessions:
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
                if phase == "before_delivery":
                    await remove_source(session, doc_id)

            async def work() -> reading_jobs.SourceReadResult:
                async with sessions() as worker:
                    return await run_source_read(worker, job_id, storage=storage, reader=reader)

            task = asyncio.create_task(work())
            if phase != "before_delivery":
                try:
                    assert await asyncio.to_thread(reader.started.wait, 5)
                    async with sessions() as session:
                        await remove_source(session, doc_id)
                finally:
                    reader.release.set()
            result = await task
            assert result.status == "superseded"
            assert result.pages_processed == 0
            assert result.next_page == 1
            assert storage.opens == (0 if phase == "before_delivery" else 1)
            async with sessions() as session:
                assert await candidate_count(session, doc_id) == 0
                assert await session.get(PageReviewStateModel, (doc_id, 1)) is None
                document = await session.get(SourceDocumentModel, doc_id)
                assert document is not None
                assert not document.active_for_ai
                assert document.original_page_count == 1
                job = await session.get(SourceReadJobModel, job_id)
                assert job is not None
                assert job.lease_token is None
                assert job.lease_expires_at is None

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("configuration", [{"unexpected": True}, {"force_ocr": "yes"}, {"ocr": []}])
def test_invalid_persisted_configuration_fails_without_replacing_the_review_candidate(
    reading_database_url: str, tmp_path: Path, configuration: dict[str, object]
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "malformed-config.pdf")
        storage = SourceStore(path)
        reader = FixtureReader(1)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            fidelity = PageFidelityService(session)
            state = await fidelity.edit_page(
                doc_id,
                1,
                text="Manually compared original",
                expected_version=0,
                actor_id=ACTOR,
                reason="Corrected transcription",
            )
            candidate_id = state.current_candidate_id
            job = await queue_source_read(
                session, doc_id, actor_id=ACTOR, page_number=1, expected_page_version=state.version
            )
            job_id, processing_version = job.id, state.version
            job.configuration = configuration
            await session.commit()
            result = await run_source_read(session, job_id, storage=storage, reader=reader)
            assert result.status == "failed"
            assert result.failure_code == "reading_configuration_invalid"
            assert result.pages_processed == 0
            assert result.next_page == 1
            assert storage.opens == 0
            assert reader.calls == []
            await session.refresh(state)
            assert state.state == "failed"
            assert state.version == processing_version + 1
            assert state.current_candidate_id == candidate_id
            assert await candidate_count(session, doc_id) == 1
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            workspace = await get_review_workspace(
                session, doc_id, principal=Principal(ACTOR, frozenset({AdminRole.ADMIN}))
            )
            assert workspace.page is not None
            assert workspace.page.state == "failed"
            assert not workspace.page.can_confirm
            assert workspace.page.candidate_id == candidate_id
            assert workspace.page.system_text == "Manually compared original"
            assert workspace.metadata_review_required
            assert not workspace.ready_for_ai
            assert workspace.progress.verified_pages == 0
            assert workspace.progress.remaining_pages == 1
            assert not session.new
            assert not session.dirty

    asyncio.run(scenario())


class RejectedReader:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.source: BinaryIO | None = None

    def open(
        self, source: BinaryIO, *, configuration: PageReadingConfiguration
    ) -> AbstractContextManager[PageReadingSession]:
        self.source = source
        raise self.error


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "failure_code"),
    [
        (OCRInputError("private unsafe file details"), "source_input_rejected"),
        (OCRConfigError("private model configuration"), "reading_configuration_invalid"),
        (ObjectStorageOperationError("private storage details"), "source_unavailable"),
        (OSError("private local path"), "source_unavailable"),
    ],
)
def test_external_reader_failures_close_source_and_persist_only_sanitized_failure_codes(
    reading_database_url: str, tmp_path: Path, error: Exception, failure_code: str
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "rejected-source.pdf")
        reader = RejectedReader(error)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (
                await queue_source_read(
                    session, doc_id, actor_id=ACTOR, page_number=1, expected_page_version=0
                )
            ).id
            result = await run_source_read(
                session, job_id, storage=SourceStore(path), reader=reader
            )
            assert result.status == "failed"
            assert result.failure_code == failure_code
            assert result.pages_processed == 0
            assert result.next_page == 1
            assert reader.source is not None
            assert reader.source.closed
            assert await candidate_count(session, doc_id) == 0
            state = await session.get(PageReviewStateModel, (doc_id, 1))
            assert state is not None
            assert state.state == "failed"
            assert state.current_candidate_id is None
            event = await session.get(PageReviewEventModel, state.event_id)
            assert event is not None
            assert event.action == "reread_failed"
            assert event.payload == {"job_id": str(job_id), "failure_code": failure_code}
            assert "private" not in str(event.payload)

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("selected_page", [None, 1])
def test_execution_deadline_requeues_only_unfinished_work_and_preserves_processing_revision(
    reading_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected_page: int | None,
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "batch-deadline.pdf", pages=3)
        reader = FixtureReader(3)
        storage = SourceStore(path)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=3)
            job_id = (
                await queue_source_read(
                    session,
                    doc_id,
                    actor_id=ACTOR,
                    page_number=selected_page,
                    expected_page_version=0 if selected_page else None,
                )
            ).id
            ticks = iter([0.0, 300.0] if selected_page else [0.0, 0.0, 300.0])
            with monkeypatch.context() as clock:
                clock.setattr(reading_jobs, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
                result = await run_source_read(session, job_id, storage=storage, reader=reader)
            assert result.status == "queued"
            assert result.pages_processed == (0 if selected_page else 1)
            assert result.next_page == (1 if selected_page else 2)
            assert reader.calls == ([] if selected_page else [1])
            job = await session.get(SourceReadJobModel, job_id)
            assert job is not None
            assert job.lease_token is None
            assert job.lease_expires_at is None
            if selected_page:
                state = await session.get(PageReviewStateModel, (doc_id, 1))
                assert state is not None
                assert state.version == job.expected_page_version == 1
                assert state.state == "processing"
            resumed = await run_source_read(session, job_id, storage=storage, reader=reader)
            assert resumed.status == "completed"
            assert reader.calls == ([1] if selected_page else [1, 2, 3])
            assert await candidate_count(session, doc_id) == (1 if selected_page else 3)
            assert not await PageFidelityService(session).document_is_verified(doc_id)

    asyncio.run(scenario())


class ResultReader(FixtureReader):
    def __init__(self, result: PageReadingResult) -> None:
        super().__init__(1)
        self.result = result

    def read_page(self, page_number: int) -> PageReadingResult:
        self.calls.append(page_number)
        return self.result


def ranked_reading(preferred_index: int) -> PageReadingResult:
    safe_text = "Read the cafe\u0301 question carefully"
    return PageReadingResult(
        page_number=1,
        candidates=(
            PageReadingCandidate(
                raw_text=safe_text if preferred_index == 0 else "\ufffd",
                method="native",
                provenance={"engine": "fixture", "engine_version": "1", "languages": ["en"]},
            ),
            PageReadingCandidate(
                raw_text=safe_text if preferred_index == 1 else "\ufffd",
                method="ocr",
                provenance={
                    "engine": "tesseract-cli",
                    "engine_version": "5.4.1",
                    "languages": ["en"],
                    "page_segmentation_mode": 3,
                },
            ),
            PageReadingCandidate(
                raw_text="",
                method="ocr",
                provenance={
                    "engine": "tesseract-cli",
                    "engine_version": "5.4.1",
                    "languages": ["en"],
                    "page_segmentation_mode": 6,
                    "failure_code": "ocr_timeout",
                },
            ),
        ),
        preferred_index=preferred_index,
    )


@pytest.mark.integration
@pytest.mark.parametrize("preferred_index", [0, 1], ids=["native-first", "ocr-middle"])
def test_preferred_not_last_candidate_retains_all_evidence_and_requires_original_comparison(
    reading_database_url: str, tmp_path: Path, preferred_index: int
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "ranked-selection.pdf")
        reading = ranked_reading(preferred_index)
        reader = ResultReader(reading)
        principal = Principal(ACTOR, frozenset({AdminRole.ADMIN}))
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            result = await run_source_read(
                session, job_id, storage=SourceStore(path), reader=reader
            )
            assert result.status == "completed"
            assert result.failure_code is None
            assert result.pages_processed == 1
            assert result.next_page == 2
            assert await candidate_count(session, doc_id) == 3
            events = list(
                await session.scalars(
                    select(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == doc_id)
                    .order_by(PageReviewEventModel.version)
                )
            )
            assert [event.version for event in events] == [1, 2, 3, 4]
            assert [event.action for event in events] == ["candidate_recorded"] * 4
            candidate_ids = [event.candidate_id for event in events[:3]]
            assert len(set(candidate_ids)) == 3
            assert events[-1].candidate_id == candidate_ids[preferred_index]
            assert [event.state for event in events[:3]] == [
                "needs_review" if index == preferred_index else "failed" for index in range(3)
            ]
            document = await session.get(SourceDocumentModel, doc_id)
            assert document is not None
            for index, expected in enumerate(reading.candidates):
                candidate = await session.get(PageTextCandidateModel, candidate_ids[index])
                assert candidate is not None
                assert candidate.raw_text_utf8 == expected.raw_text.encode("utf-8")
                assert candidate.method == expected.method
                assert candidate.parent_candidate_id == (
                    candidate_ids[index - 1] if index else None
                )
                assert candidate.can_confirm is (index == preferred_index)
                algorithm_version = candidate.diagnostics["algorithm_version"]
                assert isinstance(algorithm_version, str)
                assert algorithm_version.startswith("source-fidelity-v2/")
                assert candidate.provenance == {
                    **expected.provenance,
                    "job_id": str(job_id),
                    "attempt": 1,
                    "page_number": 1,
                    "automatic_verification": False,
                    "source_checksum_sha256": document.checksum_sha256,
                }
            chosen = await session.get(PageTextCandidateModel, candidate_ids[preferred_index])
            assert chosen is not None
            normalized = unicodedata.normalize("NFC", reading.candidates[preferred_index].raw_text)
            assert chosen.normalized_text == normalized
            assert chosen.raw_text_utf8 != normalized.encode()
            assert chosen.text_sha256 == hashlib.sha256(normalized.encode()).hexdigest()
            assert events[-1].payload == {
                "job_id": str(job_id),
                "text_sha256": chosen.text_sha256,
                "candidate_ids": [str(value) for value in candidate_ids],
                "selection_strategy": "source-fidelity-ranked-v2",
                "automatic_verification": False,
            }
            state = await session.get(PageReviewStateModel, (doc_id, 1))
            assert state is not None
            assert state.state == "needs_review"
            assert state.version == 4
            assert state.current_candidate_id == chosen.id
            assert state.event_id == events[-1].id
            selected_id, selected_version = chosen.id, state.version
            fidelity = PageFidelityService(session)
            assert not await fidelity.page_is_verified(doc_id, 1)
            assert not await fidelity.document_is_verified(doc_id)
            assert not await session.scalar(func.public.source_page_fidelity_is_current(doc_id, 1))
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageGroundTruthModel)
                    .where(PageGroundTruthModel.document_id == doc_id)
                )
                == 0
            )
            workspace = await get_review_workspace(session, doc_id, principal=principal)
            assert workspace.page is not None
            assert workspace.page.state == "needs_review"
            assert workspace.page.candidate_id == selected_id
            assert workspace.page.system_text == normalized
            assert workspace.page.can_confirm
            assert {item.id for item in workspace.page.history} == set(candidate_ids)
            assert [item.id for item in workspace.page.history if item.is_current] == [selected_id]
            assert workspace.progress.verified_pages == 0
            assert not workspace.ready_for_ai
            repeated = await run_source_read(
                session, job_id, storage=SourceStore(path), reader=reader
            )
            assert not repeated.claimed
            assert repeated.pages_processed == 0
            assert reader.calls == [1]
            assert await candidate_count(session, doc_id) == 3
            with pytest.raises(ValueError, match="explicit comparison with the original"):
                await confirm_source_page(
                    session,
                    doc_id,
                    1,
                    PageConfirmRequest.model_construct(
                        expected_version=selected_version,
                        candidate_id=selected_id,
                        compared_with_original=cast(Literal[True], False),
                        reason="Selection alone is not original comparison",
                    ),
                    principal=principal,
                )
            with pytest.raises(PageFidelityConflictError, match="source_page_version_conflict"):
                await fidelity.confirm_page(
                    doc_id,
                    1,
                    candidate_id=selected_id,
                    expected_version=selected_version - 1,
                    actor_id=ACTOR,
                    reason="The pre-selection review version must not confirm",
                )
            await session.rollback()
            assert not await fidelity.page_is_verified(doc_id, 1)
            confirmed = await confirm_source_page(
                session,
                doc_id,
                1,
                PageConfirmRequest(
                    expected_version=selected_version,
                    candidate_id=selected_id,
                    compared_with_original=True,
                    reason="Explicitly compared every line with the original page",
                ),
                principal=principal,
            )
            assert confirmed.state == "verified"
            assert confirmed.version == selected_version + 1
            assert confirmed.candidate_id == selected_id
            assert await fidelity.page_is_verified(doc_id, 1)
            assert await candidate_count(session, doc_id) == 3
            final_events = list(
                await session.scalars(
                    select(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == doc_id)
                    .order_by(PageReviewEventModel.version)
                )
            )
            assert [event.action for event in final_events] == ["candidate_recorded"] * 4 + [
                "confirmed"
            ]
            assert final_events[-1].payload["compared_with_original"] is True

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid",
    [
        "page",
        "empty",
        "excess",
        "method",
        "unicode_code",
        "punctuation_code",
        "long_code",
        "preferred_true",
        "preferred_false",
        "preferred_negative",
        "preferred_out_of_range",
        "preferred_float",
        "preferred_string",
    ],
)
def test_worker_rejects_malformed_reader_results_before_any_candidate_or_cursor_is_committed(
    reading_database_url: str, tmp_path: Path, invalid: str
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "invalid-result.pdf")
        result = FixtureReader(1).read_page(1)
        invalid_result = {
            "page": replace(result, page_number=2),
            "empty": replace(result, candidates=()),
            "excess": replace(result, candidates=result.candidates * 4),
            "method": replace(result, candidates=(replace(result.candidates[0], method="human"),)),
            "unicode_code": replace(result, failure_code="වැරදි"),
            "punctuation_code": replace(result, failure_code="private/path"),
            "long_code": replace(result, failure_code="x" * 65),
            "preferred_true": replace(
                result, candidates=ranked_reading(0).candidates, preferred_index=True
            ),
            "preferred_false": replace(result, preferred_index=False),
            "preferred_negative": replace(result, preferred_index=-1),
            "preferred_out_of_range": replace(result, preferred_index=len(result.candidates)),
            "preferred_float": replace(result, preferred_index=cast(int, 0.0)),
            "preferred_string": replace(result, preferred_index=cast(int, "0")),
        }[invalid]
        reader = ResultReader(invalid_result)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            with pytest.raises(ValueError, match="reader returned an invalid result"):
                await run_source_read(session, job_id, storage=SourceStore(path), reader=reader)
            assert not session.in_transaction()
            assert await candidate_count(session, doc_id) == 0
            assert await session.get(PageReviewStateModel, (doc_id, 1)) is None
            job = await session.get(SourceReadJobModel, job_id)
            assert job is not None
            assert job.status == "running"
            assert job.next_page == 1
            assert job.failure_code is None
            assert job.attempts == 1
            assert reader.calls == [1]

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("violation", "message", "written_count"),
    [
        ("missing_current", "source reader did not record its candidate", 2),
        ("missing_preferred", "selected reading candidate is missing", 3),
    ],
)
def test_candidate_persistence_contract_violations_roll_back_without_changing_source_or_trust(
    reading_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    violation: str,
    message: str,
    written_count: int,
) -> None:
    original_record = cast(
        Callable[..., Awaitable[PageReviewStateModel]], PageFidelityService.record_candidate
    )

    async def scenario() -> None:
        path = source_pdf(tmp_path / "candidate-contract.pdf", pages=2)
        original_bytes = await asyncio.to_thread(path.read_bytes)
        reader = ResultReader(ranked_reading(1))
        reader.page_count = 2
        storage = SourceStore(path)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=2)
            fidelity = PageFidelityService(session)
            for number in (1, 2):
                state = await fidelity.record_candidate(
                    doc_id,
                    number,
                    raw_text=f"Read the original question {number}",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"languages": ["en"]},
                )
                if number == 2:
                    await fidelity.confirm_page(
                        doc_id,
                        number,
                        candidate_id=state.current_candidate_id,
                        expected_version=state.version,
                        actor_id=ACTOR,
                        reason="Compared this unaffected page with the original",
                    )
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            queries = [
                select(SourceDocumentModel.__table__).where(SourceDocumentModel.id == doc_id),
                select(PageTextCandidateModel.__table__)
                .where(PageTextCandidateModel.document_id == doc_id)
                .order_by(PageTextCandidateModel.id),
                select(PageReviewEventModel.__table__)
                .where(PageReviewEventModel.document_id == doc_id)
                .order_by(PageReviewEventModel.id),
                select(PageReviewStateModel.__table__)
                .where(PageReviewStateModel.document_id == doc_id)
                .order_by(PageReviewStateModel.page_number),
                select(PageGroundTruthModel.__table__)
                .where(PageGroundTruthModel.document_id == doc_id)
                .order_by(PageGroundTruthModel.id),
                select(AdminAuditEventModel.__table__)
                .where(AdminAuditEventModel.payload["document_id"].astext == str(doc_id))
                .order_by(AdminAuditEventModel.id),
            ]

            async def snapshot(check: AsyncSession) -> list[list[dict[str, object]]]:
                return [
                    [dict(row) for row in (await check.execute(query)).mappings()]
                    for query in queries
                ]

            before = await snapshot(session)
            written: list[UUID] = []
            injected: list[str] = []
            original_get = cast(Callable[..., Awaitable[object]], session.get)

            async def record_with_broken_head(
                self: PageFidelityService, *args: object, **kwargs: object
            ) -> PageReviewStateModel:
                assert kwargs["commit"] is False
                recorded = await original_record(self, *args, **kwargs)
                assert recorded.current_candidate_id is not None
                written.append(recorded.current_candidate_id)
                assert await candidate_count(session, doc_id) == 2 + len(written)
                if violation == "missing_current" and len(written) == 2:
                    # Simulate a recorder losing its head after flushing real candidate evidence.
                    recorded.current_candidate_id = None
                    injected.append(violation)
                return recorded

            async def get_with_missing_preferred(
                entity: object, ident: object, **kwargs: object
            ) -> object:
                value = await original_get(entity, ident, **kwargs)
                if (
                    violation == "missing_preferred"
                    and entity is PageTextCandidateModel
                    and len(written) == 3
                    and ident == written[1]
                ):
                    # Only the final selection lookup fails; all candidate writes really occurred.
                    assert isinstance(value, PageTextCandidateModel)
                    injected.append(violation)
                    return None
                return value

            with monkeypatch.context() as failure:
                failure.setattr(PageFidelityService, "record_candidate", record_with_broken_head)
                failure.setattr(session, "get", get_with_missing_preferred)
                with pytest.raises(ValueError, match=message):
                    await run_source_read(session, job_id, storage=storage, reader=reader)
            assert not session.in_transaction()
            assert injected == [violation]
            assert len(written) == written_count
            assert reader.calls == [1]
            assert storage.opens == 1
            # Check committed evidence through a fresh session, not the worker's identity map.
            async with sessions() as check:
                assert await snapshot(check) == before
                for candidate_id in written:
                    assert await check.get(PageTextCandidateModel, candidate_id) is None
                checking_fidelity = PageFidelityService(check)
                assert not await checking_fidelity.page_is_verified(doc_id, 1)
                assert await checking_fidelity.page_is_verified(doc_id, 2)
                assert not await checking_fidelity.document_is_verified(doc_id)
                job = await check.get(SourceReadJobModel, job_id)
                assert job is not None
                assert job.status == "running"
                assert job.next_page == 1
                assert job.version == 1
                assert job.attempts == 1
                assert job.failure_code is None
                assert job.lease_token is not None
                repeated = await run_source_read(check, job_id, storage=storage, reader=reader)
                assert not repeated.claimed
                assert repeated.pages_processed == 0
                assert repeated.next_page == 1
                assert await snapshot(check) == before
            assert reader.calls == [1]
            assert storage.opens == 1
            assert await asyncio.to_thread(path.read_bytes) == original_bytes

    asyncio.run(scenario())


@pytest.mark.integration
def test_lease_expiry_during_candidate_persistence_rolls_back_candidates_state_and_cursor(
    reading_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "lost-cas.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            now = datetime.now(UTC)
            claim = await claim_source_read(session, job_id, now=now)
            assert claim is not None
            ticks = iter([now + timedelta(seconds=1), now + timedelta(seconds=600)])

            def clock(_value: datetime | None = None) -> datetime:
                return next(ticks)

            with monkeypatch.context() as expiry:
                expiry.setattr(reading_jobs, "_now", clock)
                current, accepted = await reading_jobs._commit_page(
                    session, claim, ranked_reading(1), expected_version=0, last_page=1
                )
            assert current is None
            assert not accepted
            assert not session.in_transaction()
            assert await candidate_count(session, doc_id) == 0
            assert await session.get(PageReviewStateModel, (doc_id, 1)) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == doc_id)
                )
                == 0
            )
            job = await session.get(SourceReadJobModel, job_id)
            assert job is not None
            assert job.status == "running"
            assert job.next_page == 1
            assert job.version == claim.version
            assert job.lease_token == claim.lease_token

    asyncio.run(scenario())


@pytest.mark.integration
def test_stale_claim_cannot_discover_page_count_or_finish_or_update_the_recovered_job(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "fenced-updates.pdf")
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=None)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            now = datetime.now(UTC)
            stale = await claim_source_read(session, job_id, now=now)
            current = await claim_source_read(session, job_id, now=now + timedelta(seconds=601))
            assert stale is not None
            assert current is not None
            assert await reading_jobs._check_page_count(session, stale, 1) is None
            assert not session.in_transaction()
            assert (
                await reading_jobs._finish(session, stale, status="failed", failure_code="stale")
                is None
            )
            assert not session.in_transaction()
            assert (
                await reading_jobs._save_progress(
                    session, stale, status="completed", next_page=2, failure_code=None
                )
                is None
            )
            assert not session.in_transaction()
            document = await session.get(SourceDocumentModel, doc_id)
            assert document is not None
            assert document.original_page_count is None
            job = await session.get(SourceReadJobModel, job_id)
            assert job is not None
            assert job.version == current.version
            assert job.lease_token == current.lease_token
            assert job.status == "running"
            assert job.next_page == 1
            assert job.failure_code is None

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("status", ["failed", "superseded"])
def test_terminal_job_delivery_cannot_restart_or_replace_a_canceled_reading(
    reading_database_url: str, tmp_path: Path, status: str
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "terminal-delivery.pdf")
        reader = FixtureReader(1)
        storage = SourceStore(path)
        async with database(reading_database_url) as sessions, sessions() as session:
            doc_id = await add_source(session, path, pages=1)
            job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id
            claim = await claim_source_read(session, job_id)
            assert claim is not None
            finished = await reading_jobs._finish(session, claim, status=status)
            assert finished is not None
            result = await run_source_read(session, job_id, storage=storage, reader=reader)
            assert not result.claimed
            assert result.status == status
            assert result.pages_processed == 0
            assert storage.opens == 0
            assert reader.calls == []
            assert await candidate_count(session, doc_id) == 0
            job = await session.get(SourceReadJobModel, job_id)
            assert job is not None
            assert job.attempts == claim.attempts
            assert job.version == finished.version

    asyncio.run(scenario())


@pytest.mark.integration
def test_source_disappearing_after_committed_claim_fails_without_recreating_it(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        path = source_pdf(tmp_path / "disappeared-source.pdf")
        storage = SourceStore(path)
        reader = FixtureReader(1)
        committed, release = asyncio.Event(), asyncio.Event()

        class PausingClaimSession(AsyncSession):
            async def commit(self) -> None:
                await super().commit()
                committed.set()
                await release.wait()

        engine = create_async_engine(reading_database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                doc_id = await add_source(session, path, pages=1)
                job_id = (await queue_source_read(session, doc_id, actor_id=ACTOR)).id

            async def work() -> None:
                async with PausingClaimSession(bind=engine, expire_on_commit=False) as worker:
                    await run_source_read(worker, job_id, storage=storage, reader=reader)

            task = asyncio.create_task(work())
            try:
                await asyncio.wait_for(committed.wait(), timeout=5)
                async with sessions() as cleanup:
                    # The unreviewed legacy fixture has no page/intake evidence. Delete its job
                    # first, as required by the real FK, to reproduce concurrent cleanup rather
                    # than mocking an impossible orphaned claim inside a single transaction.
                    await cleanup.execute(
                        delete(SourceReadJobModel).where(SourceReadJobModel.id == job_id)
                    )
                    await cleanup.execute(
                        delete(SourceDocumentModel).where(SourceDocumentModel.id == doc_id)
                    )
                    await cleanup.commit()
            finally:
                release.set()
            with pytest.raises(FidelitySourceNotFoundError, match="source_document_not_found"):
                await task
            assert storage.opens == 0
            assert reader.calls == []
            async with sessions() as session:
                assert await session.get(SourceDocumentModel, doc_id) is None
                assert await session.get(SourceReadJobModel, job_id) is None
                assert await candidate_count(session, doc_id) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_recovery_is_bounded_and_distinguishes_expired_live_and_recent_jobs(
    reading_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        async with database(reading_database_url) as sessions, sessions() as session:
            # An isolated historical clock keeps unrelated module fixtures outside the outbox
            # window while exercising the real recovery query and row-version updates.
            epoch = datetime(2001, 1, 1, tzinfo=UTC)
            current = epoch + timedelta(seconds=1000)
            identifiers: list[UUID] = []
            for index in range(5):
                path = source_pdf(tmp_path / f"recovery-{index}.pdf")
                doc_id = await add_source(session, path, pages=1)
                job = await queue_source_read(session, doc_id, actor_id=ACTOR)
                identifiers.append(job.id)
                job.updated_at = epoch + timedelta(seconds=index)
                await session.commit()
            expired = await claim_source_read(
                session, identifiers[1], now=epoch + timedelta(seconds=1)
            )
            unleased = await claim_source_read(
                session, identifiers[2], now=epoch + timedelta(seconds=2)
            )
            live = await claim_source_read(session, identifiers[3], now=current)
            assert expired is not None
            assert unleased is not None
            assert live is not None
            missing_lease = await session.get(SourceReadJobModel, identifiers[2])
            assert missing_lease is not None
            missing_lease.lease_expires_at = None
            recent = await session.get(SourceReadJobModel, identifiers[4])
            assert recent is not None
            recent.updated_at = current - timedelta(seconds=29)
            await session.commit()

            dispatcher = RecordingDispatcher()
            first = await recover_source_reads(session, dispatcher, now=current, batch_size=1)
            assert first.enqueued == 1
            assert first.failures == 0
            assert dispatcher.dispatched == [identifiers[0]]
            second = await recover_source_reads(session, dispatcher, now=current, batch_size=2)
            assert second.enqueued == 2
            assert second.failures == 0
            assert dispatcher.dispatched == identifiers[:3]
            for identifier, expected_version in zip(identifiers[:3], [1, 2, 2], strict=True):
                recovered = await session.get(
                    SourceReadJobModel, identifier, populate_existing=True
                )
                assert recovered is not None
                assert recovered.status == "queued"
                assert recovered.version == expected_version
                assert recovered.lease_token is None
                assert recovered.lease_expires_at is None
                assert recovered.next_page == 1
            active = await session.get(SourceReadJobModel, identifiers[3], populate_existing=True)
            assert active is not None
            assert active.status == "running"
            assert active.version == live.version
            assert active.lease_token == live.lease_token
            untouched = await session.get(
                SourceReadJobModel, identifiers[4], populate_existing=True
            )
            assert untouched is not None
            assert untouched.status == "queued"
            assert untouched.version == 0
            repeated = await recover_source_reads(session, dispatcher, now=current)
            assert repeated.enqueued == 0
            assert repeated.failures == 0
            assert dispatcher.dispatched == identifiers[:3]

    asyncio.run(scenario())
