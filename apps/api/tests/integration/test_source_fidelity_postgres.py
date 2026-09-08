import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.fidelity_models import (
    PageGroundTruthModel,
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
)
from exam_guru_api.documents.fidelity_queries import (
    get_source_page_candidate,
    list_source_benchmarks,
)
from exam_guru_api.documents.fidelity_service import (
    FidelitySourceNotFoundError,
    PageFidelityConflictError,
    PageFidelityService,
    PageVerificationBlockedError,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_reading_jobs import queue_source_read
from exam_guru_api.infrastructure.migrations import upgrade_database

ACTOR = UUID(int=81001)


@pytest.fixture(scope="module")
def fidelity_database_url() -> Iterator[str]:
    with PostgresContainer(
        image="pgvector/pgvector:0.8.6-pg18-trixie",
        username="exam_guru",
        password=uuid4().hex,
        dbname="source_fidelity_test",
        driver="asyncpg",
    ) as postgres:
        url = postgres.get_connection_url()
        upgrade_database(url)
        yield url


async def add_source(session: AsyncSession) -> UUID:
    identifier = uuid4()
    checksum = hashlib.sha256(identifier.bytes).hexdigest()
    document = SourceDocumentModel(
        id=identifier,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        original_filename="fidelity.pdf",
        content_type="application/pdf",
        size_bytes=100,
        document_type=SourceDocumentType.TEACHER_GUIDE,
        created_by=ACTOR,
        updated_by=ACTOR,
        original_page_count=2,
    )
    session.add(document)
    await session.commit()
    return identifier


@asynccontextmanager
async def fidelity_session(url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def add_sql_candidate(
    session: AsyncSession,
    document_id: UUID,
    *,
    method: str,
    diagnostics: dict[str, object],
    normalized_text: str | None = "Read the original",
    can_confirm: bool = True,
    provenance: dict[str, object] | None = None,
) -> PageTextCandidateModel:
    raw = (normalized_text if normalized_text is not None else "Unsafe original").encode()
    candidate = PageTextCandidateModel(
        id=uuid4(),
        document_id=document_id,
        page_number=1,
        method=method,
        raw_text_utf8=raw,
        normalized_text=normalized_text,
        text_sha256=hashlib.sha256(raw).hexdigest(),
        can_confirm=can_confirm,
        provenance=provenance if provenance is not None else {"engine": "sql-fixture"},
        diagnostics=diagnostics,
        created_by=ACTOR,
    )
    session.add(candidate)
    await session.commit()
    return candidate


async def confirm_sql_candidate(
    session: AsyncSession,
    candidate: PageTextCandidateModel,
    *,
    action: str = "confirmed",
    payload: dict[str, object] | None = None,
) -> PageReviewEventModel:
    state = await session.get(PageReviewStateModel, (candidate.document_id, candidate.page_number))
    if state is None:
        state = PageReviewStateModel(
            document_id=candidate.document_id,
            page_number=candidate.page_number,
            version=0,
            state="pending",
        )
        session.add(state)
        await session.flush()
    event = PageReviewEventModel(
        id=uuid4(),
        document_id=candidate.document_id,
        page_number=candidate.page_number,
        version=state.version + 1,
        candidate_id=candidate.id,
        action=action,
        state="verified",
        actor_id=ACTOR,
        reason="Explicit original comparison in an isolated SQL fixture",
        payload=payload
        if payload is not None
        else {"compared_with_original": True, "text_sha256": candidate.text_sha256},
    )
    session.add(event)
    await session.flush()
    state.version = event.version
    state.state = event.state
    state.current_candidate_id = candidate.id
    state.event_id = event.id
    await session.commit()
    return event


@pytest.mark.integration
@pytest.mark.parametrize("method", ["native", "legacy", "ocr", "human"])
@pytest.mark.parametrize(
    "diagnostics",
    [{"algorithm_version": "source-fidelity-v1/14.0.0"}, {}, {"algorithm_version": None}],
    ids=["v1", "missing_version", "null_version"],
)
def test_sql_confirmation_rejects_stale_or_unversioned_candidates(
    fidelity_database_url: str, method: str, diagnostics: dict[str, object]
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            candidate = await add_sql_candidate(
                session, document_id, method=method, diagnostics=diagnostics
            )
            candidate_id = candidate.id
            with pytest.raises(IntegrityError, match="exact candidate confirmation"):
                await confirm_sql_candidate(session, candidate)
            await session.rollback()
            assert (
                await session.scalar(
                    text("SELECT public.source_candidate_is_confirmable(:candidate)"),
                    {"candidate": candidate_id},
                )
                is False
            )
            assert (
                await session.scalar(
                    text("SELECT public.source_page_fidelity_is_current(:source, 1, :candidate)"),
                    {"source": document_id, "candidate": candidate_id},
                )
                is False
            )
            retained = await session.get(PageTextCandidateModel, candidate_id)
            assert retained is not None
            assert retained.can_confirm is True
            assert retained.diagnostics == diagnostics
            assert retained.raw_text_utf8 == b"Read the original"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == document_id)
                )
                == 0
            )

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("method", ["native", "legacy", "ocr", "human"])
def test_sql_v2_candidate_requires_confirmation_before_it_is_current(
    fidelity_database_url: str, method: str
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            candidate = await add_sql_candidate(
                session,
                document_id,
                method=method,
                diagnostics={"algorithm_version": "source-fidelity-v2/14.0.0"},
            )
            assert (
                await session.scalar(
                    text("SELECT public.source_candidate_is_confirmable(:candidate)"),
                    {"candidate": candidate.id},
                )
                is True
            )
            current = text("SELECT public.source_page_fidelity_is_current(:source, 1, :candidate)")
            identity = {"source": document_id, "candidate": candidate.id}
            assert await session.scalar(current, identity) is False
            await confirm_sql_candidate(session, candidate)
            assert await session.scalar(current, identity) is True
            assert await session.scalar(current, {**identity, "candidate": None}) is True
            assert await session.scalar(current, {**identity, "candidate": uuid4()}) is False
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageGroundTruthModel)
                    .where(PageGroundTruthModel.document_id == document_id)
                )
                == 0
            )

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("invalid_evidence", ["action", "hash", "comparison", "reference"])
def test_sql_v2_confirmation_retains_original_evidence_guards(
    fidelity_database_url: str, invalid_evidence: str
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            candidate = await add_sql_candidate(
                session,
                document_id,
                method="human",
                diagnostics={"algorithm_version": "source-fidelity-v2/14.0.0"},
            )
            payload: dict[str, object] = {
                "compared_with_original": invalid_evidence != "comparison",
                "text_sha256": "0" * 64 if invalid_evidence == "hash" else candidate.text_sha256,
            }
            action = "confirmed"
            if invalid_evidence == "action":
                action = "edited"
            elif invalid_evidence == "reference":
                action = "reference_verified"
                payload["ground_truth_id"] = str(uuid4())
            with pytest.raises(IntegrityError, match=r"confirmation|adjudicated reference"):
                await confirm_sql_candidate(session, candidate, action=action, payload=payload)
            await session.rollback()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("normalized_text", "can_confirm", "provenance"),
    [
        ("Read the original", False, {}),
        (None, False, {}),
        ("", False, {}),
        (" \t\n", True, {}),
        ("\u00a0\u2007\u202f", True, {}),
        ("Read the original", True, {"failure_code": "render_failed"}),
        ("Read the original", True, {"failure_code": None}),
        ("Read the original", True, {"failure_code": ""}),
    ],
    ids=[
        "blocked",
        "unsafe",
        "empty",
        "whitespace",
        "nonbreaking_whitespace",
        "failure",
        "null_failure",
        "empty_failure",
    ],
)
def test_sql_v2_candidate_gate_fails_closed_for_unusable_evidence(
    fidelity_database_url: str,
    normalized_text: str | None,
    can_confirm: bool,
    provenance: dict[str, object],
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            candidate = await add_sql_candidate(
                session,
                document_id,
                method="human",
                diagnostics={"algorithm_version": "source-fidelity-v2/14.0.0"},
                normalized_text=normalized_text,
                can_confirm=can_confirm,
                provenance=provenance,
            )
            assert (
                await session.scalar(
                    text("SELECT public.source_candidate_is_confirmable(:candidate)"),
                    {"candidate": candidate.id},
                )
                is False
            )
            with pytest.raises(IntegrityError, match="exact candidate confirmation"):
                await confirm_sql_candidate(session, candidate)
            await session.rollback()

    asyncio.run(scenario())


@pytest.mark.integration
def test_sql_candidate_gate_returns_false_for_missing_identity_in_readonly_transaction(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            for candidate_id in (None, uuid4()):
                assert (
                    await session.scalar(
                        text("SELECT public.source_candidate_is_confirmable(:candidate)"),
                        {"candidate": candidate_id},
                    )
                    is False
                )

    asyncio.run(scenario())


@pytest.mark.integration
def test_page_confirmation_is_versioned_and_edits_invalidate_it(fidelity_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                document_id = await add_source(session)
                service = PageFidelityService(session)
                first = await service.record_candidate(
                    document_id,
                    1,
                    raw_text="ගණිතය 2 \u00d7 3 = 6",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"engine": "fixture", "version": "1", "languages": ["si"]},
                )
                assert first.state == "needs_review"
                assert not await service.page_is_verified(document_id, 1)
                candidate_id = first.current_candidate_id
                assert candidate_id is not None
                confirmed = await service.confirm_page(
                    document_id,
                    1,
                    candidate_id=candidate_id,
                    expected_version=first.version,
                    actor_id=ACTOR,
                    reason="Compared every line with the original page",
                )
                assert confirmed.state == "verified"
                assert await service.page_is_verified(document_id, 1)
                assert not await service.document_is_verified(document_id)
                previous_version = confirmed.version
                edited = await service.edit_page(
                    document_id,
                    1,
                    text="ගණිතය 2 \u00d7 3 = 7",
                    reason="Correct the source transcription",
                    expected_version=previous_version,
                    actor_id=ACTOR,
                )
                assert edited.state == "needs_review"
                assert not await service.page_is_verified(document_id, 1)
                assert edited.current_candidate_id != candidate_id
                original = await session.get(PageTextCandidateModel, candidate_id)
                assert original is not None
                assert original.raw_text_utf8.decode() == "ගණිතය 2 \u00d7 3 = 6"
                with pytest.raises(PageFidelityConflictError):
                    await service.confirm_page(
                        document_id,
                        1,
                        candidate_id=candidate_id,
                        expected_version=previous_version,
                        actor_id=ACTOR,
                        reason="Stale review",
                    )
                await session.rollback()
                events = list(
                    await session.scalars(
                        select(PageReviewEventModel)
                        .where(PageReviewEventModel.document_id == document_id)
                        .order_by(PageReviewEventModel.version)
                    )
                )
                assert [event.action for event in events] == [
                    "candidate_recorded",
                    "confirmed",
                    "edited",
                ]
                assert (
                    await session.scalar(select(func.count()).select_from(PageGroundTruthModel))
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_unsafe_raw_candidates_are_preserved_but_cannot_be_confirmed(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                document_id = await add_source(session)
                service = PageFidelityService(session)
                state = await service.record_candidate(
                    document_id,
                    1,
                    raw_text="broken\x00\ue000text",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"engine": "fixture", "version": "1", "fonts": ["FMAbhaya"]},
                )
                candidate = await session.get(PageTextCandidateModel, state.current_candidate_id)
                assert candidate is not None
                assert candidate.raw_text_utf8 == b"broken\x00\xee\x80\x80text"
                candidate_id = candidate.id
                with pytest.raises(PageVerificationBlockedError):
                    await service.confirm_page(
                        document_id,
                        1,
                        candidate_id=candidate_id,
                        expected_version=state.version,
                        actor_id=ACTOR,
                        reason="Confidence was high",
                    )
                await session.rollback()
                with pytest.raises(IntegrityError):
                    await session.execute(
                        update(PageTextCandidateModel)
                        .where(PageTextCandidateModel.id == candidate_id)
                        .values(normalized_text="replacement")
                    )
                await session.rollback()
                await session.execute(
                    update(PageReviewStateModel)
                    .where(PageReviewStateModel.document_id == document_id)
                    .values(state="verified", version=2)
                )
                with pytest.raises(IntegrityError):
                    await session.commit()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_candidate_and_batch_progress_can_share_one_atomic_transaction(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                document_id = await add_source(session)
                await PageFidelityService(session).record_candidate(
                    document_id,
                    1,
                    raw_text="Atomic candidate",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"engine": "fixture", "version": "1"},
                    commit=False,
                )
                await session.rollback()
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PageTextCandidateModel)
                        .where(PageTextCandidateModel.document_id == document_id)
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PageReviewStateModel)
                        .where(PageReviewStateModel.document_id == document_id)
                    )
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_original_page_count_cannot_be_shrunk_to_bypass_unreviewed_pages(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                document_id = await add_source(session)
                with pytest.raises(IntegrityError):
                    await session.execute(
                        update(SourceDocumentModel)
                        .where(SourceDocumentModel.id == document_id)
                        .values(original_page_count=1)
                    )
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_review_states_cannot_invent_pages_outside_the_original(fidelity_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                document_id = await add_source(session)
                session.add(
                    PageReviewStateModel(
                        document_id=document_id,
                        page_number=3,
                        state="pending",
                        version=0,
                    )
                )
                with pytest.raises(IntegrityError, match="original page"):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_ground_truth_is_protected_against_truncate(fidelity_database_url: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with engine.begin() as connection:
                with pytest.raises(IntegrityError, match="append only"):
                    await connection.execute(text("TRUNCATE source_page_ground_truth"))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_exclusion_and_benchmark_ground_truth_require_explicit_decisions(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(fidelity_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                document_id = await add_source(session)
                service = PageFidelityService(session)
                benchmark_id = await service.create_benchmark(
                    name=f"real-page-review-{uuid4()}",
                    pages=((document_id, 1, ("maths", "sinhala")),),
                    actor_id=ACTOR,
                    selection={"method": "coverage_fixture_not_quality_evidence"},
                )
                first = await service.record_candidate(
                    document_id,
                    1,
                    raw_text="5 + 2 = 7",
                    method="ocr",
                    actor_id=ACTOR,
                    provenance={"engine": "fixture", "version": "1", "languages": ["en"]},
                )
                second = await service.record_candidate(
                    document_id,
                    2,
                    raw_text="",
                    method="native",
                    actor_id=ACTOR,
                    provenance={"engine": "fixture", "version": "1"},
                )
                assert (
                    await session.scalar(select(func.count()).select_from(PageGroundTruthModel))
                    == 0
                )
                confirmed = await service.confirm_page(
                    document_id,
                    1,
                    candidate_id=first.current_candidate_id,
                    expected_version=first.version,
                    actor_id=ACTOR,
                    reason="Exact source comparison",
                )
                excluded = await service.exclude_page(
                    document_id,
                    2,
                    expected_version=second.version,
                    actor_id=ACTOR,
                    reason="Original page is blank",
                )
                assert excluded.state == "excluded"
                assert not await service.page_is_verified(document_id, 2)
                assert await service.document_is_verified(document_id)
                truths = list(
                    await session.scalars(
                        select(PageGroundTruthModel).where(
                            PageGroundTruthModel.benchmark_id == benchmark_id
                        )
                    )
                )
                assert len(truths) == 1
                assert truths[0].candidate_id == confirmed.current_candidate_id
                assert truths[0].reviewer_id == ACTOR
                with pytest.raises(IntegrityError):
                    await session.execute(
                        text("DELETE FROM source_page_ground_truth WHERE id = :id"),
                        {"id": truths[0].id},
                    )
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("commit", [False, True])
def test_identical_candidate_delivery_deduplicates_without_a_new_revision_or_audit(
    fidelity_database_url: str, commit: bool
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            service = PageFidelityService(session)
            provenance: dict[str, object] = {
                "engine": "fixture",
                "languages": ["en"],
                "source_checksum_sha256": "untrusted",
            }
            first = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the original",
                method="native",
                actor_id=ACTOR,
                provenance=provenance,
            )
            candidate_id, event_id, version = (
                first.current_candidate_id,
                first.event_id,
                first.version,
            )
            replay = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the original",
                method="native",
                actor_id=ACTOR,
                provenance=provenance,
                commit=commit,
            )
            assert session.in_transaction() is not commit
            assert (replay.current_candidate_id, replay.event_id, replay.version) == (
                candidate_id,
                event_id,
                version,
            )
            assert replay.state == "needs_review"
            candidate = await session.get(PageTextCandidateModel, candidate_id)
            document = await session.get(SourceDocumentModel, document_id)
            assert candidate is not None
            assert document is not None
            assert candidate.provenance["source_checksum_sha256"] == document.checksum_sha256
            for model in (PageTextCandidateModel, PageReviewEventModel):
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.document_id == document_id)
                    )
                    == 1
                )
            assert not await service.page_is_verified(document_id, 1)

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("changed_field", ["raw_text", "method", "provenance"])
def test_candidate_deduplication_requires_identical_content_method_and_provenance(
    fidelity_database_url: str, changed_field: str
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            service = PageFidelityService(session)
            first = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the original",
                method="native",
                actor_id=ACTOR,
                provenance={"engine": "first", "languages": ["en"]},
            )
            previous_id, previous_version = first.current_candidate_id, first.version
            changed = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the corrected original"
                if changed_field == "raw_text"
                else "Read the original",
                method="ocr" if changed_field == "method" else "native",
                actor_id=ACTOR,
                provenance={
                    "engine": "second" if changed_field == "provenance" else "first",
                    "languages": ["en"],
                },
            )
            assert changed.current_candidate_id != previous_id
            assert changed.version == previous_version + 1
            assert changed.state == "needs_review"
            candidate = await session.get(PageTextCandidateModel, changed.current_candidate_id)
            assert candidate is not None
            assert candidate.parent_candidate_id == previous_id
            previous = await session.get(PageTextCandidateModel, previous_id)
            assert previous is not None
            assert previous.raw_text_utf8 == b"Read the original"
            assert previous.method == "native"
            assert previous.provenance["engine"] == "first"
            assert not await service.page_is_verified(document_id, 1)

    asyncio.run(scenario())


@pytest.mark.integration
def test_confirmation_replay_cannot_duplicate_ground_truth_or_advance_the_review(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            service = PageFidelityService(session)
            benchmark = await service.create_benchmark(
                name=f"Idempotent comparison {uuid4()}",
                pages=((document_id, 1, ("maths",)),),
                actor_id=ACTOR,
                selection={"method": "deterministic_contract_fixture"},
            )
            state = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the original",
                method="native",
                actor_id=ACTOR,
                provenance={"languages": ["en"]},
            )
            confirmed = await service.confirm_page(
                document_id,
                1,
                candidate_id=state.current_candidate_id,
                expected_version=state.version,
                actor_id=ACTOR,
                reason="Compared every line",
            )
            candidate_id, event_id, version = (
                confirmed.current_candidate_id,
                confirmed.event_id,
                confirmed.version,
            )
            replay = await service.confirm_page(
                document_id,
                1,
                candidate_id=candidate_id,
                expected_version=version,
                actor_id=ACTOR,
                reason="Retry the same comparison",
            )
            assert (replay.current_candidate_id, replay.event_id, replay.version) == (
                candidate_id,
                event_id,
                version,
            )
            assert replay.state == "verified"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageGroundTruthModel)
                    .where(PageGroundTruthModel.benchmark_id == benchmark)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == document_id)
                )
                == 2
            )

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("selection", ["none", "old", "unknown"])
def test_confirmation_requires_the_current_candidate_even_at_the_current_version(
    fidelity_database_url: str, selection: str
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            service = PageFidelityService(session)
            first = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the original",
                method="native",
                actor_id=ACTOR,
                provenance={},
            )
            original_id = first.current_candidate_id
            current = await service.edit_page(
                document_id,
                1,
                text="Read the corrected original",
                reason="Fixed transcription",
                expected_version=first.version,
                actor_id=ACTOR,
            )
            candidate_id, version = current.current_candidate_id, current.version
            selected = {"none": None, "old": original_id, "unknown": uuid4()}[selection]
            with pytest.raises(PageFidelityConflictError, match="source_candidate_changed"):
                await service.confirm_page(
                    document_id,
                    1,
                    candidate_id=selected,
                    expected_version=version,
                    actor_id=ACTOR,
                    reason="Must not confirm a different reading",
                )
            await session.rollback()
            stored = await session.get(PageReviewStateModel, (document_id, 1))
            assert stored is not None
            assert stored.current_candidate_id == candidate_id
            assert stored.version == version
            assert stored.state == "needs_review"
            assert not await service.page_is_verified(document_id, 1)

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("review_state", ["excluded", "processing"])
def test_nonreviewable_pages_cannot_be_confirmed_even_with_a_confirmable_candidate(
    fidelity_database_url: str, review_state: str
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            service = PageFidelityService(session)
            state = await service.record_candidate(
                document_id,
                1,
                raw_text="Read the original",
                method="native",
                actor_id=ACTOR,
                provenance={"languages": ["en"]},
            )
            if review_state == "excluded":
                await service.exclude_page(
                    document_id,
                    1,
                    expected_version=state.version,
                    actor_id=ACTOR,
                    reason="Not educational source content",
                )
            else:
                await queue_source_read(
                    session,
                    document_id,
                    actor_id=ACTOR,
                    page_number=1,
                    expected_page_version=state.version,
                )
            candidate_id, version = state.current_candidate_id, state.version
            with pytest.raises(
                PageVerificationBlockedError, match="source_page_not_awaiting_review"
            ):
                await service.confirm_page(
                    document_id,
                    1,
                    candidate_id=candidate_id,
                    expected_version=version,
                    actor_id=ACTOR,
                    reason="Cannot authorize unavailable review",
                )
            if review_state == "processing":
                with pytest.raises(PageFidelityConflictError, match="page_reading_in_progress"):
                    await service.edit_page(
                        document_id,
                        1,
                        text="Cannot race an explicit reread",
                        reason="Compared source",
                        expected_version=version,
                        actor_id=ACTOR,
                    )
            await session.rollback()
            stored = await session.get(PageReviewStateModel, (document_id, 1))
            assert stored is not None
            assert stored.state == review_state
            assert stored.version == version
            assert stored.current_candidate_id == candidate_id
            assert not await service.page_is_verified(document_id, 1)

    asyncio.run(scenario())


@pytest.mark.integration
def test_invalid_utf8_candidate_is_displayed_safely_and_remains_immutable_and_untrusted(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            raw = b"Read \xff the original"
            candidate_id = uuid4()
            session.add(
                PageTextCandidateModel(
                    id=candidate_id,
                    document_id=document_id,
                    page_number=1,
                    method="legacy",
                    raw_text_utf8=raw,
                    normalized_text=None,
                    text_sha256=hashlib.sha256(raw).hexdigest(),
                    can_confirm=False,
                    provenance={"engine": "legacy-import"},
                    diagnostics={},
                    created_by=ACTOR,
                )
            )
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            view = await get_source_page_candidate(
                session,
                document_id,
                1,
                candidate_id,
                principal=Principal(ACTOR, frozenset({AdminRole.ADMIN})),
            )
            assert view.candidate_id == candidate_id
            assert view.state == "failed"
            assert not view.can_confirm
            assert "surrogate" in view.risk_codes
            assert "\\udcff" in view.system_text
            assert "\udcff" not in view.system_text
            assert view.preview_url == f"/api/v1/admin/materials/{document_id}/pages/1/image"
            assert (
                await session.scalar(
                    select(PageTextCandidateModel.raw_text_utf8).where(
                        PageTextCandidateModel.id == candidate_id
                    )
                )
                == raw
            )
            assert await session.get(PageReviewStateModel, (document_id, 1)) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageReviewEventModel)
                    .where(PageReviewEventModel.document_id == document_id)
                )
                == 0
            )
            assert not session.new
            assert not session.dirty

    asyncio.run(scenario())


@pytest.mark.integration
def test_benchmark_listing_past_the_last_page_is_empty_in_a_readonly_transaction(
    fidelity_database_url: str,
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            assert (
                await list_source_benchmarks(
                    session,
                    principal=Principal(ACTOR, frozenset({AdminRole.REVIEWER})),
                    limit=1,
                    offset=100000,
                )
                == []
            )
            assert not session.new
            assert not session.dirty

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("selection", ["unknown", "other_page", "other_document"])
def test_candidate_query_fails_closed_for_missing_or_cross_source_identity_without_writing(
    fidelity_database_url: str, selection: str
) -> None:
    async def scenario() -> None:
        async with fidelity_session(fidelity_database_url) as session:
            document_id = await add_source(session)
            other_document_id = await add_source(session)
            state = await PageFidelityService(session).record_candidate(
                document_id,
                1,
                raw_text="Private original reading",
                method="native",
                actor_id=ACTOR,
                provenance={"languages": ["en"]},
            )
            candidate_id, version = state.current_candidate_id, state.version
            assert candidate_id is not None
            selected_document, selected_page, selected_candidate = {
                "unknown": (document_id, 1, uuid4()),
                "other_page": (document_id, 2, candidate_id),
                "other_document": (other_document_id, 1, candidate_id),
            }[selection]
            await session.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(FidelitySourceNotFoundError, match=r"^source_candidate_not_found$"):
                await get_source_page_candidate(
                    session,
                    selected_document,
                    selected_page,
                    selected_candidate,
                    principal=Principal(ACTOR, frozenset({AdminRole.ADMIN})),
                )
            await session.refresh(state)
            assert state.current_candidate_id == candidate_id
            assert state.version == version
            assert state.state == "needs_review"
            assert await session.get(PageReviewStateModel, (other_document_id, 1)) is None
            assert not session.new
            assert not session.dirty

    asyncio.run(scenario())
