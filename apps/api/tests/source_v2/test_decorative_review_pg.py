"""Decorative regions: what a reviewer may do with page furniture, and what they are told.

The bug this pins was reported from the real pilot. `p186-r000` (a running
header) and `p186-r007` (a folio) are `decorative`, the review screen offered
the ordinary green text-confirm button for both, and pressing it produced:

    region p186-r007 is decorative; use confirm-visual or reclassify it first

Two things were wrong. Decorative content can never become Verified Source
Content at all, so "confirm-visual" is a dead end — that endpoint accepts only
`visual_only` and `visual_with_text`. And the reviewer's actual options,
exclude or reclassify, were not the ones named.

The guard itself is not relaxed anywhere here. Everything below asserts that
decorative stays unverifiable and that the reviewer is pointed at a door that
opens.

Synthetic fixtures only, on a page number no real material uses, inside a
transaction that is always rolled back. The live pilot review is not a fixture.

    $env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=..."
    uv run pytest tests/source_v2/test_decorative_review_pg.py -q
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
from exam_guru_api.source_v2.domain import SourceV2Error

pytest.importorskip("asyncpg")
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
SHA = "9" * 64
CROP = "8" * 64
REGION = "r-folio"
#: What a running header or folio actually carries. Printed, but not source.
FURNITURE_TEXT = "171"


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


async def seed(
    session: AsyncSession,
    *,
    kind: str = "decorative",
    region_type: str = "decorative",
    text_value: str = FURNITURE_TEXT,
) -> repository.PageHeader:
    """One disposable page carrying one region of the requested kind."""

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
            "number": 600_000 + uuid.uuid4().int % 90_000,
            "sha": SHA,
            "layout": '{"regions": [{"id": "r-folio", "bbox": [1, 2, 3, 4]}]}',
        },
    )
    await session.execute(
        text("""
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, origin, text, reason,
               state, is_current, source_kind, proposed_source_kind, crop_sha256, abstained)
            values (:id, :page, :region, :region_type, 1, 'machine', :text,
                    'primary reading by the executing agent from the canonical crop',
                    'unverified', true, :kind, :kind, :crop, false)
        """),
        {
            "id": uuid.uuid4(),
            "page": page_id,
            "region": REGION,
            "region_type": region_type,
            "text": text_value,
            "kind": kind,
            "crop": CROP,
        },
    )
    return await repository.get_page(session, page_id)


async def current(session: AsyncSession, page: repository.PageHeader) -> repository.RegionRow:
    regions = await repository.list_regions(session, page.page_id)
    return next(row for row in regions if row.region_id == REGION)


async def verified_rows(session: AsyncSession, page: repository.PageHeader) -> int:
    return int(
        (
            await session.execute(
                text("select count(1) from source_v2_verified_regions where page_id = :page"),
                {"page": page.page_id},
            )
        ).scalar_one()
    )


async def actions(session: AsyncSession, page: repository.PageHeader) -> list[str]:
    return [
        row[0]
        for row in (
            await session.execute(
                text(
                    "select action from source_v2_review_events"
                    " where page_id = :page order by created_at"
                ),
                {"page": page.page_id},
            )
        ).all()
    ]


def run(scenario: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the decorative review tests")

    async def main() -> Any:
        async with rolled_back_session() as session:
            return await scenario(session)

    return asyncio.run(main())


# --- /confirm refuses decorative, and says something useful ------------------


def test_confirm_on_decorative_is_refused_with_accurate_guidance() -> None:
    """The message names the two actions that exist, and no dead end."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        row = await current(session, page)
        with pytest.raises(SourceV2Error) as refusal:
            await repository.confirm(
                session,
                page=page,
                region_id=REGION,
                candidate_id=row.candidate_id,
                revision=row.revision,
                reviewer_id=uuid.uuid4(),
                compared_with_image_sha256=SHA,
            )
        message = str(refusal.value)
        assert REGION in message
        assert "cannot be verified as source content" in message
        assert "exclude it, or reclassify it first" in message
        # The old wording sent the reviewer to an endpoint that cannot accept
        # a decorative region at all. That is the bug, not a wording nit.
        assert "confirm-visual" not in message

    run(scenario)


def test_confirm_on_visual_only_still_points_at_confirm_visual() -> None:
    """The other half of the split: visual_only *does* have a visual action."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session, kind="visual_only", region_type="figure", text_value="")
        row = await current(session, page)
        with pytest.raises(SourceV2Error) as refusal:
            await repository.confirm(
                session,
                page=page,
                region_id=REGION,
                candidate_id=row.candidate_id,
                revision=row.revision,
                reviewer_id=uuid.uuid4(),
                compared_with_image_sha256=SHA,
            )
        message = str(refusal.value)
        assert message == (
            f"region {REGION} is visual_only; use confirm-visual or reclassify it first"
        )

    run(scenario)


# --- /confirm-visual cannot be used as a side door ---------------------------


def test_confirm_visual_refuses_decorative_content() -> None:
    """`confirm_visual` writes `source_kind`, so it could reclassify silently."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        row = await current(session, page)
        with pytest.raises(SourceV2Error) as refusal:
            await repository.confirm_visual(
                session,
                page=page,
                region_id=REGION,
                candidate_id=row.candidate_id,
                revision=row.revision,
                reviewer_id=uuid.uuid4(),
                compared_with_image_sha256=SHA,
                source_kind="visual_only",
            )
        assert "cannot be verified as source content" in str(refusal.value)
        after = await current(session, page)
        # Nothing was reclassified on the way past.
        assert after.source_kind == "decorative"
        assert after.state == "unverified"
        assert await verified_rows(session, page) == 0

    run(scenario)


def test_decorative_can_never_produce_a_verified_row() -> None:
    """Every verification door, tried in turn, and the region stays unverified."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        row = await current(session, page)
        with pytest.raises(SourceV2Error):
            await repository.confirm(
                session,
                page=page,
                region_id=REGION,
                candidate_id=row.candidate_id,
                revision=row.revision,
                reviewer_id=uuid.uuid4(),
                compared_with_image_sha256=SHA,
            )
        with pytest.raises(SourceV2Error):
            await repository.confirm_visual(
                session,
                page=page,
                region_id=REGION,
                candidate_id=row.candidate_id,
                revision=row.revision,
                reviewer_id=uuid.uuid4(),
                compared_with_image_sha256=SHA,
                source_kind="visual_with_text",
                text_value=FURNITURE_TEXT,
            )
        assert await verified_rows(session, page) == 0
        assert await actions(session, page) == [], "a refusal must author no review event"
        assert (await current(session, page)).state == "unverified"

    run(scenario)


# --- the two doors that do open ----------------------------------------------


def test_exclude_resolves_decorative_content() -> None:
    """Agreeing with the machine is the ordinary resolution for page furniture."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        row = await current(session, page)
        await repository.exclude(
            session,
            page=page,
            region_id=REGION,
            candidate_id=row.candidate_id,
            revision=row.revision,
            reviewer_id=uuid.uuid4(),
            note="running header, not source content",
        )
        after = await current(session, page)
        assert after.state == "excluded"
        assert await verified_rows(session, page) == 0
        assert await actions(session, page) == ["exclude"]
        counts = await repository.progress(session, page.page_id)
        assert counts["excluded"] == 1
        assert counts["unverified"] == 0

    run(scenario)


def test_reclassify_is_append_only_and_leaves_it_unverified() -> None:
    """Disagreeing is a recorded human decision, not a verification."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        row = await current(session, page)
        await repository.reclassify(
            session,
            page=page,
            region_id=REGION,
            candidate_id=row.candidate_id,
            revision=row.revision,
            reviewer_id=uuid.uuid4(),
            source_kind="text_only",
            note="this is a lesson heading, not a running header",
        )
        after = await current(session, page)
        assert after.source_kind == "text_only"
        # The machine's proposal survives, so the disagreement stays visible.
        assert after.proposed_source_kind == "decorative"
        assert after.state == "unverified", "reclassifying is not verifying"
        assert after.revision == row.revision, "the reading did not change"
        assert await verified_rows(session, page) == 0
        assert await actions(session, page) == ["correct"]
        note = (
            await session.execute(
                text(
                    "select note from source_v2_review_events"
                    " where page_id = :page order by created_at"
                ),
                {"page": page.page_id},
            )
        ).scalar_one()
        assert note.startswith("reclassified as text_only:")

    run(scenario)


def test_the_reviewer_can_verify_only_after_reclassifying() -> None:
    """The route out of the dead end, end to end."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session)
        row = await current(session, page)
        await repository.reclassify(
            session,
            page=page,
            region_id=REGION,
            candidate_id=row.candidate_id,
            revision=row.revision,
            reviewer_id=uuid.uuid4(),
            source_kind="text_only",
            note="printed lesson number, part of the source text",
        )
        reclassified = await current(session, page)
        await repository.confirm(
            session,
            page=page,
            region_id=REGION,
            candidate_id=reclassified.candidate_id,
            revision=reclassified.revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=SHA,
        )
        assert await verified_rows(session, page) == 1
        assert await actions(session, page) == ["correct", "confirm"]
        stored = (
            await session.execute(
                text(
                    "select source_kind, text from source_v2_verified_regions where page_id = :page"
                ),
                {"page": page.page_id},
            )
        ).first()
        assert stored == ("text_only", FURNITURE_TEXT)

    run(scenario)
