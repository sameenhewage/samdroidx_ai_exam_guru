"""Saving a visual description against a real database.

Three things this proves that a unit test cannot:

* a description is an ordinary edit — it appends **no review event** and moves
  nobody's state, so it cannot manufacture the human confirmation D5 reserves
  for a person comparing the original page;
* a description written about the wrong thing is refused *before* it is
  stored, with its findings, rather than stored and believed later;
* correcting the printed text creates a new revision and the description
  survives it. Losing derived work silently on every correction is how a
  reviewer stops trusting the screen.

    $env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=..."
    uv run pytest tests/source_v2/test_description_pg.py -q
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
from exam_guru_api.source_v2.visual_description import DescriptionRefusedError

pytest.importorskip("asyncpg")
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
SHA = "f" * 64
CROP = "1" * 64

#: "A ring magnet, a paper butterfly and a piece of thread are shown."
SINHALA = "වළලු චුම්බකයක්, කඩදාසි සමනලයෙක් සහ නූල් කැබැල්ලක් දැක්වේ."
LABEL = "චුම්බකය"


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


async def seed(session: AsyncSession, *, text_value: str, kind: str) -> repository.PageHeader:
    """One page with one figure region, hung off a real source document."""

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
            "number": 700_000 + uuid.uuid4().int % 90_000,
            "sha": SHA,
            "layout": '{"regions": [{"id": "r-fig", "bbox": [1, 2, 3, 4]}]}',
        },
    )
    await session.execute(
        text("""
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, origin, text, reason,
               state, is_current, source_kind, proposed_source_kind, crop_sha256, abstained)
            values (:id, :page, 'r-fig', 'figure', 1, 'machine', :text,
                    'primary reading by the executing agent from the canonical crop',
                    'unverified', true, :kind, :kind, :crop, false)
        """),
        {
            "id": uuid.uuid4(),
            "page": page_id,
            "text": text_value,
            "kind": kind,
            "crop": CROP,
        },
    )
    return await repository.get_page(session, page_id)


async def current(session: AsyncSession, page: repository.PageHeader) -> repository.RegionRow:
    regions = await repository.list_regions(session, page.page_id)
    return next(row for row in regions if row.region_id == "r-fig")


async def events(session: AsyncSession, page: repository.PageHeader) -> int:
    return int(
        (
            await session.execute(
                text("select count(1) from source_v2_review_events where page_id = :page"),
                {"page": page.page_id},
            )
        ).scalar_one()
    )


async def confirmed_visual(
    session: AsyncSession, *, text_value: str, kind: str
) -> tuple[repository.PageHeader, repository.RegionRow]:
    page = await seed(session, text_value=text_value, kind=kind)
    row = await current(session, page)
    await repository.confirm_visual(
        session,
        page=page,
        region_id="r-fig",
        candidate_id=row.candidate_id,
        revision=row.revision,
        reviewer_id=uuid.uuid4(),
        compared_with_image_sha256=SHA,
        source_kind=kind,
        text_value=text_value or None,
    )
    return page, await current(session, page)


def run(scenario: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the visual-description database tests")

    async def main() -> Any:
        async with rolled_back_session() as session:
            return await scenario(session)

    return asyncio.run(main())


# --- an ordinary edit, never a verification ----------------------------------


def test_saving_a_description_creates_no_review_event() -> None:
    """Describing a figure is not confirming anything about it (D5)."""

    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value="", kind="visual_only")
        before = await events(session, page)
        await repository.describe(
            session,
            page=page,
            region_id="r-fig",
            candidate_id=row.candidate_id,
            revision=row.revision,
            description=SINHALA,
        )
        assert await events(session, page) == before
        after = await current(session, page)
        assert after.visual_description == SINHALA
        assert after.state == "verified"
        assert after.revision == row.revision

    run(scenario)


def test_a_description_never_becomes_the_regions_text() -> None:
    """`text` keeps one meaning: the text printed inside the crop."""

    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value="", kind="visual_only")
        await repository.describe(
            session,
            page=page,
            region_id="r-fig",
            candidate_id=row.candidate_id,
            revision=row.revision,
            description=SINHALA,
        )
        after = await current(session, page)
        assert after.text == ""
        assert after.verified_text == ""
        assert after.visual_description == SINHALA

    run(scenario)


# --- validation runs before the write ----------------------------------------


def test_english_on_sinhala_material_is_refused_and_nothing_is_stored() -> None:
    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value="", kind="visual_only")
        with pytest.raises(DescriptionRefusedError, match="wrong-language"):
            await repository.describe(
                session,
                page=page,
                region_id="r-fig",
                candidate_id=row.candidate_id,
                revision=row.revision,
                description="Line-art figure only (a foam block, a ring magnet).",
            )
        assert (await current(session, page)).visual_description is None

    run(scenario)


def test_a_measurement_the_crop_does_not_print_is_refused() -> None:
    """`(20 cm x 6 cm)` is in the materials list, not inside the drawing."""

    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value="", kind="visual_only")
        with pytest.raises(DescriptionRefusedError, match="unverifiable-measurement"):
            await repository.describe(
                session,
                page=page,
                region_id="r-fig",
                candidate_id=row.candidate_id,
                revision=row.revision,
                description=f"{SINHALA} රෙජිෆෝම් කැබැල්ල 20 cm x 6 cm වේ.",
            )
        assert (await current(session, page)).visual_description is None

    run(scenario)


def test_a_label_not_legible_in_the_crop_is_refused() -> None:
    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value=LABEL, kind="visual_with_text")
        with pytest.raises(DescriptionRefusedError, match="unverifiable-label"):
            await repository.describe(
                session,
                page=page,
                region_id="r-fig",
                candidate_id=row.candidate_id,
                revision=row.revision,
                description=SINHALA,
                detected_labels=[LABEL, "රෙජිෆෝම්"],
            )
        assert (await current(session, page)).detected_labels == ()

    run(scenario)


def test_labels_printed_inside_the_crop_are_stored_as_a_list() -> None:
    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value=LABEL, kind="visual_with_text")
        await repository.describe(
            session,
            page=page,
            region_id="r-fig",
            candidate_id=row.candidate_id,
            revision=row.revision,
            description=SINHALA,
            detected_labels=[LABEL],
        )
        assert (await current(session, page)).detected_labels == (LABEL,)

    run(scenario)


def test_an_unverified_visual_cannot_be_described_yet() -> None:
    """Derived knowledge is built on verified source, never ahead of it."""

    async def scenario(session: AsyncSession) -> None:
        page = await seed(session, text_value="", kind="visual_only")
        row = await current(session, page)
        with pytest.raises(DescriptionRefusedError, match="not verified"):
            await repository.describe(
                session,
                page=page,
                region_id="r-fig",
                candidate_id=row.candidate_id,
                revision=row.revision,
                description=SINHALA,
            )

    run(scenario)


def test_a_text_region_has_no_source_visual_to_describe() -> None:
    async def scenario(session: AsyncSession) -> None:
        page = await seed(session, text_value="පෙළ", kind="text_only")
        row = await current(session, page)
        with pytest.raises(DescriptionRefusedError, match="no source visual"):
            await repository.describe(
                session,
                page=page,
                region_id="r-fig",
                candidate_id=row.candidate_id,
                revision=row.revision,
                description=SINHALA,
            )

    run(scenario)


def test_a_stale_revision_cannot_be_described() -> None:
    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value="", kind="visual_only")
        with pytest.raises(SourceV2Error, match="moved on"):
            await repository.describe(
                session,
                page=page,
                region_id="r-fig",
                candidate_id=row.candidate_id,
                revision=row.revision + 1,
                description=SINHALA,
            )

    run(scenario)


# --- the description survives a new revision ---------------------------------


def test_a_description_survives_a_correction_revision() -> None:
    """Correcting the printed text says nothing about what the picture shows."""

    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value=LABEL, kind="visual_with_text")
        await repository.describe(
            session,
            page=page,
            region_id="r-fig",
            candidate_id=row.candidate_id,
            revision=row.revision,
            description=SINHALA,
            detected_labels=[LABEL],
        )
        described = await current(session, page)
        await repository.correct(
            session,
            page=page,
            region_id="r-fig",
            candidate_id=described.candidate_id,
            revision=described.revision,
            reviewer_id=uuid.uuid4(),
            corrected_text=f"{LABEL} නූල",
        )
        after = await current(session, page)
        # A correction withdraws verification, as it always has...
        assert after.revision == described.revision + 1
        assert after.state == "unverified"
        assert after.verified_text is None
        # ...and carries the derived knowledge and the crop onto the new
        # revision rather than dropping them on the floor.
        assert after.visual_description == SINHALA
        assert after.detected_labels == (LABEL,)
        assert after.crop_sha256 == CROP
        assert after.source_kind == "visual_with_text"

    run(scenario)


def test_a_re_import_of_new_text_keeps_the_existing_description() -> None:
    """A fresh transcription of the same pixels does not falsify the description."""

    async def scenario(session: AsyncSession) -> None:
        page, row = await confirmed_visual(session, text_value=LABEL, kind="visual_with_text")
        await repository.describe(
            session,
            page=page,
            region_id="r-fig",
            candidate_id=row.candidate_id,
            revision=row.revision,
            description=SINHALA,
            detected_labels=[LABEL],
        )
        result = await repository.import_page(
            session,
            document_id=page.document_id,
            page_number=page.page_number,
            language="sinhala",
            image_sha256=SHA,
            width=2480,
            height=3509,
            dpi=300,
            detector_version="test",
            layout={"regions": [{"id": "r-fig", "bbox": [1, 2, 3, 4]}]},
            candidates=[
                {
                    "region_id": "r-fig",
                    "region_type": "figure",
                    "text": f"{LABEL} නූල",
                    "abstained": False,
                    "reason": "re-read",
                    "source_kind": "visual_with_text",
                    "crop_sha256": CROP,
                }
            ],
            refresh=True,
        )
        assert result["verifications_withdrawn"] == 1
        after = await current(session, page)
        assert after.revision == row.revision + 1
        assert after.state == "unverified"
        assert after.visual_description == SINHALA
        assert after.detected_labels == (LABEL,)

    run(scenario)
