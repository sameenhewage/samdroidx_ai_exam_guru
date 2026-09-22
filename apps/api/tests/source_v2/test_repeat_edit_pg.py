"""Human correction is a chain, not a single shot.

The reviewer reported that "text correction appears to work only once". The
symptom was in the review screen, but the guarantee it depends on belongs
here: a reviewer must be able to correct a correction, an unbounded number of
times, before or after a confirmation, and every intermediate revision must
survive as history rather than being overwritten in place.

What that means concretely, and what each test below pins:

    machine r1 -> human r2 -> human r3 -> human r4 -> confirm r4

- revisions are monotonic and each child names its parent;
- exactly one candidate is current, and it is the last one;
- every correction event stays, because the events are the audit trail;
- confirming r4 verifies r4 and nothing else;
- r1, r2 and r3 can no longer be confirmed - a browser holding a stale
  revision must be refused, not silently allowed to verify old text;
- editing a verified revision withdraws the verification and produces the
  next unverified child, so nothing is ever verified by omission.

Synthetic fixtures only, on a page number no real material uses, inside a
transaction that is always rolled back. The live pilot review is not a
fixture and must not move.

    $env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=..."
    uv run pytest tests/source_v2/test_repeat_edit_pg.py -q
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import text

from exam_guru_api.source_v2 import repository
from exam_guru_api.source_v2.domain import StaleReviewError

pytest.importorskip("asyncpg")
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
SHA = "7" * 64
REGION = "r-chain"
MACHINE = "ක්‍රියාකාරකම් 11"
#: Four readings of the same heading, as a reviewer would refine it.
HUMAN = (
    "ක්‍රියාකාරකම 11",
    "ක්‍රියාකාරකම 11 — ශාක විවිධත්වය",
    "ක්‍රියාකාරකම 11 — ශාකවල විවිධත්වය",
)


def async_url(dsn: str) -> str:
    """`host=... port=...` keyword DSN as an asyncpg SQLAlchemy URL."""

    parts = dict(item.split("=", 1) for item in dsn.split() if "=" in item)
    return (
        f"postgresql+asyncpg://{parts['user']}:{parts['password']}"
        f"@{parts['host']}:{parts.get('port', '5432')}/{parts['dbname']}"
    )


@asynccontextmanager
async def rolled_back_session() -> AsyncIterator[AsyncSession]:
    """A session whose work is always rolled back. The live Studio is not a fixture."""

    engine = create_async_engine(async_url(DSN or ""))
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def seed(session: AsyncSession) -> repository.PageHeader:
    """One disposable page carrying one machine-read text region."""

    document = (await session.execute(text("select id from source_documents limit 1"))).first()
    if document is None:
        pytest.skip("no source_documents row to attach a Source V2 page to")
    page_id = uuid.uuid4()
    await session.execute(
        text("""
            insert into source_v2_pages
              (id, document_id, page_number, image_sha256, dpi, width, height,
               language, detector_version, layout)
            values (:id, :doc, :number, :sha, 300, 2480, 3509, 'sinhala', 'test',
                    cast(:layout as jsonb))
        """),
        {
            "id": page_id,
            "doc": document[0],
            # Far outside any real teacher-guide page range, and unique per
            # run, so nothing here can collide with reviewed material.
            "number": 610_000 + uuid.uuid4().int % 80_000,
            "sha": SHA,
            "layout": '{"regions": [{"id": "r-chain", "bbox": [1, 2, 3, 4]}]}',
        },
    )
    await session.execute(
        text("""
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, origin, text, reason,
               state, is_current, source_kind, proposed_source_kind, abstained)
            values (:id, :page, :region, 'text', 1, 'machine', :text,
                    'primary reading by the executing agent from the canonical crop',
                    'unverified', true, 'text_only', 'text_only', false)
        """),
        {"id": uuid.uuid4(), "page": page_id, "region": REGION, "text": MACHINE},
    )
    return await repository.get_page(session, page_id)


async def current(session: AsyncSession, page: repository.PageHeader) -> repository.RegionRow:
    regions = await repository.list_regions(session, page.page_id)
    return next(row for row in regions if row.region_id == REGION)


async def revisions(session: AsyncSession, page: repository.PageHeader) -> list[tuple]:
    """Every candidate ever written for the region, oldest first."""

    return [
        tuple(row)
        for row in (
            await session.execute(
                text(
                    "select revision, origin, text, state, is_current, id, parent_id"
                    " from source_v2_machine_candidates"
                    " where page_id = :page and region_id = :region order by revision"
                ),
                {"page": page.page_id, "region": REGION},
            )
        ).all()
    ]


async def events(session: AsyncSession, page: repository.PageHeader) -> list[tuple]:
    return [
        tuple(row)
        for row in (
            await session.execute(
                text(
                    "select action, candidate_revision, corrected_text"
                    " from source_v2_review_events"
                    " where page_id = :page order by created_at"
                ),
                {"page": page.page_id},
            )
        ).all()
    ]


async def verified(session: AsyncSession, page: repository.PageHeader) -> list[tuple]:
    return [
        tuple(row)
        for row in (
            await session.execute(
                text(
                    "select candidate_revision, text from source_v2_verified_regions"
                    " where page_id = :page"
                ),
                {"page": page.page_id},
            )
        ).all()
    ]


async def correct_chain(
    session: AsyncSession, page: repository.PageHeader, texts: tuple[str, ...]
) -> list[repository.RegionRow]:
    """Apply `texts` one after another, each against whatever is current."""

    seen = []
    for body in texts:
        row = await current(session, page)
        seen.append(row)
        await repository.correct(
            session,
            page=page,
            region_id=REGION,
            candidate_id=row.candidate_id,
            revision=row.revision,
            reviewer_id=uuid.uuid4(),
            corrected_text=body,
        )
    return seen


def run(scenario: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the repeat-edit tests")

    async def main() -> Any:
        async with rolled_back_session() as session:
            return await scenario(session)

    return asyncio.run(main())


# --- the chain itself -------------------------------------------------------


def test_three_corrections_build_a_monotonic_parent_chain() -> None:
    """r1 -> r2 -> r3 -> r4, each child naming the revision it replaced."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        await correct_chain(session, page, HUMAN)

        rows = await revisions(session, page)
        assert [row[0] for row in rows] == [1, 2, 3, 4]
        assert [row[1] for row in rows] == [
            "machine",
            "human-correction",
            "human-correction",
            "human-correction",
        ]
        assert [row[2] for row in rows] == [MACHINE, *HUMAN]
        # Each revision names its parent, so the chain is reconstructable
        # from the rows alone rather than from the ordering.
        assert rows[0][6] is None
        for child, parent in zip(rows[1:], rows[:-1], strict=True):
            assert child[6] == parent[5]

    run(scenario)


def test_only_the_last_revision_is_current() -> None:
    """Exactly one current candidate, and no edit overwrote an older one."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        before = await revisions(session, page)
        await correct_chain(session, page, HUMAN)
        rows = await revisions(session, page)

        assert [row[4] for row in rows] == [False, False, False, True]
        assert sum(1 for row in rows if row[4]) == 1
        # The superseded rows keep their own text; a correction writes a new
        # row rather than editing the one the reviewer was looking at.
        assert rows[0][2] == before[0][2] == MACHINE
        assert (await current(session, page)).text == HUMAN[-1]

    run(scenario)


def test_every_correction_event_is_retained() -> None:
    """The audit trail is the events. None of them is replaced by the next."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        await correct_chain(session, page, HUMAN)

        assert await events(session, page) == [
            ("correct", 1, HUMAN[0]),
            ("correct", 2, HUMAN[1]),
            ("correct", 3, HUMAN[2]),
        ]

    run(scenario)


def test_there_is_no_cap_on_the_number_of_corrections() -> None:
    """Twelve in a row, because "you already edited once" is not a rule."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        many = tuple(f"{HUMAN[0]} v{index}" for index in range(2, 14))
        await correct_chain(session, page, many)

        rows = await revisions(session, page)
        assert [row[0] for row in rows] == list(range(1, 14))
        assert (await current(session, page)).revision == 13
        assert len(await events(session, page)) == 12

    run(scenario)


# --- confirming applies to the current revision and only that one -----------


def test_confirming_the_last_revision_verifies_that_revision_only() -> None:
    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        await correct_chain(session, page, HUMAN)
        row = await current(session, page)
        assert row.revision == 4

        await repository.confirm(
            session,
            page=page,
            region_id=REGION,
            candidate_id=row.candidate_id,
            revision=row.revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=SHA,
        )

        assert await verified(session, page) == [(4, HUMAN[-1])]
        rows = await revisions(session, page)
        assert [row[3] for row in rows] == [
            "unverified",
            "unverified",
            "unverified",
            "verified",
        ]
        assert (await current(session, page)).verified_text == HUMAN[-1]

    run(scenario)


def test_no_superseded_revision_can_still_be_confirmed() -> None:
    """A browser holding r1, r2 or r3 is refused, not quietly obeyed."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        stale = await correct_chain(session, page, HUMAN)
        assert [row.revision for row in stale] == [1, 2, 3]

        for row in stale:
            with pytest.raises(StaleReviewError) as refusal:
                await repository.confirm(
                    session,
                    page=page,
                    region_id=REGION,
                    candidate_id=row.candidate_id,
                    revision=row.revision,
                    reviewer_id=uuid.uuid4(),
                    compared_with_image_sha256=SHA,
                )
            assert "moved on" in str(refusal.value)
            # And the refusal changed nothing.
            assert await verified(session, page) == []

        # Correcting from a stale revision is refused for the same reason.
        with pytest.raises(StaleReviewError):
            await repository.correct(
                session,
                page=page,
                region_id=REGION,
                candidate_id=stale[0].candidate_id,
                revision=stale[0].revision,
                reviewer_id=uuid.uuid4(),
                corrected_text="from a tab left open yesterday",
            )
        assert len(await revisions(session, page)) == 4

    run(scenario)


# --- editing after a verification -------------------------------------------


def test_editing_a_verified_revision_withdraws_it_and_opens_a_child() -> None:
    """Verification is reversible, and reversing it is recorded."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        await correct_chain(session, page, HUMAN[:1])
        confirmed = await current(session, page)
        await repository.confirm(
            session,
            page=page,
            region_id=REGION,
            candidate_id=confirmed.candidate_id,
            revision=confirmed.revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=SHA,
        )
        assert await verified(session, page) == [(2, HUMAN[0])]

        await repository.correct(
            session,
            page=page,
            region_id=REGION,
            candidate_id=confirmed.candidate_id,
            revision=confirmed.revision,
            reviewer_id=uuid.uuid4(),
            corrected_text=HUMAN[1],
        )

        # The verified row is gone: nothing may stay verified once the text
        # it was verified against has been superseded.
        assert await verified(session, page) == []
        row = await current(session, page)
        assert (row.revision, row.origin, row.text, row.state) == (
            3,
            "human-correction",
            HUMAN[1],
            "unverified",
        )
        assert row.verified_text is None
        # The confirmation it withdrew is still in the history.
        assert [event[0] for event in await events(session, page)] == [
            "correct",
            "confirm",
            "correct",
        ]

    run(scenario)


def test_a_verified_revision_can_be_edited_twice_before_reconfirming() -> None:
    """The exact sequence the reviewer said was impossible."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        await correct_chain(session, page, HUMAN[:1])
        first = await current(session, page)
        await repository.confirm(
            session,
            page=page,
            region_id=REGION,
            candidate_id=first.candidate_id,
            revision=first.revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=SHA,
        )

        # Edit again, then edit again *before* reconfirming.
        await correct_chain(session, page, HUMAN[1:])
        assert await verified(session, page) == []

        final = await current(session, page)
        assert (final.revision, final.text, final.state) == (4, HUMAN[-1], "unverified")
        await repository.confirm(
            session,
            page=page,
            region_id=REGION,
            candidate_id=final.candidate_id,
            revision=final.revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=SHA,
        )

        assert await verified(session, page) == [(4, HUMAN[-1])]
        assert [event[0] for event in await events(session, page)] == [
            "correct",
            "confirm",
            "correct",
            "correct",
            "confirm",
        ]
        # Only one verified row exists, and it names the final revision.
        assert (await current(session, page)).verified_text == HUMAN[-1]

    run(scenario)
