"""Async Source V2 storage for the HTTP layer.

The offline importer in `service.py` runs synchronously against psycopg; the
API runs on an async SQLAlchemy session. Rather than duplicate the rules, both
go through the same guard functions in `domain.py`, and the SQL lives here once
for the async path.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.source_v2.domain import (
    ReviewAction,
    SourceV2Error,
    StaleReviewError,
)

HUMAN_CORRECTION = "human-correction"


class PageNotFoundError(SourceV2Error):
    """The requested Source V2 page does not exist."""


@dataclass(frozen=True)
class PageHeader:
    page_id: UUID
    document_id: UUID
    page_number: int
    image_sha256: str
    width: int
    height: int
    dpi: float
    language: str
    detector_version: str


@dataclass(frozen=True)
class RegionRow:
    region_id: str
    region_type: str
    candidate_id: UUID
    revision: int
    origin: str
    text: str
    abstained: bool
    chosen_reader: str | None
    reason: str
    critical_conflict: bool
    agreement_ratio: float
    disagreement: dict
    state: str
    bbox: list[int] | None
    verified_text: str | None


async def get_page(session: AsyncSession, page_id: UUID) -> PageHeader:
    row = (
        await session.execute(
            text(
                "select id, document_id, page_number, image_sha256, width, height, dpi,"
                " language, detector_version from source_v2_pages where id = :page_id"
            ),
            {"page_id": page_id},
        )
    ).first()
    if row is None:
        raise PageNotFoundError(f"unknown Source V2 page {page_id}")
    return PageHeader(*row)


async def find_page(
    session: AsyncSession, document_id: UUID, page_number: int
) -> PageHeader | None:
    row = (
        await session.execute(
            text(
                "select id, document_id, page_number, image_sha256, width, height, dpi,"
                " language, detector_version from source_v2_pages"
                " where document_id = :document_id and page_number = :page_number"
            ),
            {"document_id": document_id, "page_number": page_number},
        )
    ).first()
    return PageHeader(*row) if row is not None else None


async def list_regions(session: AsyncSession, page_id: UUID) -> list[RegionRow]:
    """Current candidate per region, with any verified text and the layout bbox."""

    rows = (
        await session.execute(
            text("""
                select c.region_id, c.region_type, c.id, c.revision, c.origin, c.text,
                       c.abstained, c.chosen_reader, c.reason, c.critical_conflict,
                       c.agreement_ratio, c.disagreement, c.state, v.text
                from source_v2_machine_candidates c
                left join source_v2_verified_regions v
                       on v.page_id = c.page_id and v.region_id = c.region_id
                where c.page_id = :page_id and c.is_current
                order by c.region_id
            """),
            {"page_id": page_id},
        )
    ).all()
    layout = (
        await session.execute(
            text("select layout from source_v2_pages where id = :page_id"),
            {"page_id": page_id},
        )
    ).scalar_one_or_none() or {}
    boxes = {
        region.get("id"): region.get("bbox") for region in (layout.get("regions") or [])
    }
    return [
        RegionRow(
            region_id=row[0],
            region_type=row[1],
            candidate_id=row[2],
            revision=row[3],
            origin=row[4],
            text=row[5],
            abstained=row[6],
            chosen_reader=row[7],
            reason=row[8],
            critical_conflict=row[9],
            agreement_ratio=row[10],
            disagreement=row[11] or {},
            state=row[12],
            bbox=boxes.get(row[0]),
            verified_text=row[13],
        )
        for row in rows
    ]


async def reader_evidence(session: AsyncSession, page_id: UUID) -> dict[str, list[dict]]:
    rows = (
        await session.execute(
            text(
                "select region_id, reader, text, abstained, failure, seconds"
                " from source_v2_reader_candidates where page_id = :page_id"
                " order by region_id, reader"
            ),
            {"page_id": page_id},
        )
    ).all()
    evidence: dict[str, list[dict]] = {}
    for region_id, reader, body, abstained, failure, seconds in rows:
        evidence.setdefault(region_id, []).append(
            {
                "reader": reader,
                "text": body,
                "abstained": abstained,
                "failure": failure,
                "seconds": seconds,
            }
        )
    return evidence


async def progress(session: AsyncSession, page_id: UUID) -> dict[str, int]:
    rows = (
        await session.execute(
            text(
                "select state, count(1) from source_v2_machine_candidates"
                " where page_id = :page_id and is_current group by state"
            ),
            {"page_id": page_id},
        )
    ).all()
    counts = {"unverified": 0, "verified": 0, "excluded": 0} | dict(rows)
    counts["resolved"] = counts["verified"] + counts["excluded"]
    counts["total"] = counts["resolved"] + counts["unverified"]
    return counts


async def _current(session: AsyncSession, page_id: UUID, region_id: str):
    row = (
        await session.execute(
            text(
                "select id, revision, text, abstained, region_type"
                " from source_v2_machine_candidates"
                " where page_id = :page_id and region_id = :region_id and is_current"
            ),
            {"page_id": page_id, "region_id": region_id},
        )
    ).first()
    if row is None:
        raise SourceV2Error(f"no current candidate for region {region_id}")
    return row


async def _require_current(
    session: AsyncSession, page_id: UUID, region_id: str, candidate_id: UUID, revision: int
):
    row = await _current(session, page_id, region_id)
    if row[0] != candidate_id or row[1] != revision:
        raise StaleReviewError(
            f"region {region_id} moved on: the current candidate is {row[0]} revision {row[1]}"
        )
    return row


async def _append_event(
    session: AsyncSession,
    *,
    page_id: UUID,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    action: ReviewAction,
    reviewer_id: UUID,
    sha: str,
    note: str | None = None,
    corrected_text: str | None = None,
) -> UUID:
    event_id = uuid4()
    await session.execute(
        text("""
            insert into source_v2_review_events
              (id, page_id, region_id, candidate_id, candidate_revision, action, reviewer_id,
               compared_with_image_sha256, note, corrected_text)
            values (:id, :page_id, :region_id, :candidate_id, :revision, :action, :reviewer_id,
                    :sha, :note, :corrected_text)
        """),
        {
            "id": event_id,
            "page_id": page_id,
            "region_id": region_id,
            "candidate_id": candidate_id,
            "revision": revision,
            "action": str(action),
            "reviewer_id": reviewer_id,
            "sha": sha,
            "note": note,
            "corrected_text": corrected_text,
        },
    )
    return event_id


async def confirm(
    session: AsyncSession,
    *,
    page: PageHeader,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    compared_with_image_sha256: str,
    note: str | None = None,
) -> UUID:
    if compared_with_image_sha256 != page.image_sha256:
        raise StaleReviewError(
            "confirmation must cite the rendered page that was actually compared"
        )
    row = await _require_current(session, page.page_id, region_id, candidate_id, revision)
    body, abstained = row[2], row[3]
    if abstained or not body.strip():
        raise SourceV2Error(
            f"region {region_id} abstained or is empty; correct or exclude it instead"
        )
    await _append_event(
        session,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.CONFIRM,
        reviewer_id=reviewer_id,
        sha=page.image_sha256,
        note=note,
    )
    verified_id = uuid4()
    await session.execute(
        text("""
            insert into source_v2_verified_regions
              (id, page_id, region_id, candidate_id, candidate_revision, text, text_nfc,
               reviewer_id, image_sha256, verified_at)
            values (:id, :page_id, :region_id, :candidate_id, :revision, :text, :text_nfc,
                    :reviewer_id, :sha, :verified_at)
        """),
        {
            "id": verified_id,
            "page_id": page.page_id,
            "region_id": region_id,
            "candidate_id": candidate_id,
            "revision": revision,
            "text": body,
            "text_nfc": unicodedata.normalize("NFC", body),
            "reviewer_id": reviewer_id,
            "sha": page.image_sha256,
            "verified_at": datetime.now(UTC),
        },
    )
    await session.execute(
        text("update source_v2_machine_candidates set state = 'verified' where id = :id"),
        {"id": candidate_id},
    )
    return verified_id


async def correct(
    session: AsyncSession,
    *,
    page: PageHeader,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    corrected_text: str,
    note: str | None = None,
) -> UUID:
    """Store the reviewer's text as an unverified child candidate."""

    if not corrected_text.strip():
        raise SourceV2Error("a correction must contain text; use exclude to drop a region")
    row = await _require_current(session, page.page_id, region_id, candidate_id, revision)
    await _append_event(
        session,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.CORRECT,
        reviewer_id=reviewer_id,
        sha=page.image_sha256,
        note=note,
        corrected_text=corrected_text,
    )
    await session.execute(
        text(
            "delete from source_v2_verified_regions"
            " where page_id = :page_id and region_id = :region_id"
        ),
        {"page_id": page.page_id, "region_id": region_id},
    )
    await session.execute(
        text(
            "update source_v2_machine_candidates"
            " set is_current = false, state = 'unverified' where id = :id"
        ),
        {"id": candidate_id},
    )
    child_id = uuid4()
    await session.execute(
        text("""
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, parent_id, origin, text,
               abstained, chosen_reader, reason, state, is_current)
            values (:id, :page_id, :region_id, :region_type, :revision, :parent_id, :origin,
                    :text, false, null, :reason, 'unverified', true)
        """),
        {
            "id": child_id,
            "page_id": page.page_id,
            "region_id": region_id,
            "region_type": row[4],
            "revision": revision + 1,
            "parent_id": candidate_id,
            "origin": HUMAN_CORRECTION,
            "text": corrected_text,
            "reason": "human correction; awaiting confirmation",
        },
    )
    return child_id


async def exclude(
    session: AsyncSession,
    *,
    page: PageHeader,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    note: str,
) -> None:
    if not note.strip():
        raise SourceV2Error("an exclusion must say why")
    await _require_current(session, page.page_id, region_id, candidate_id, revision)
    await _append_event(
        session,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.EXCLUDE,
        reviewer_id=reviewer_id,
        sha=page.image_sha256,
        note=note,
    )
    await session.execute(
        text(
            "delete from source_v2_verified_regions"
            " where page_id = :page_id and region_id = :region_id"
        ),
        {"page_id": page.page_id, "region_id": region_id},
    )
    await session.execute(
        text("update source_v2_machine_candidates set state = 'excluded' where id = :id"),
        {"id": candidate_id},
    )


async def document_resolution(session: AsyncSession, document_id: UUID) -> dict:
    """Per-page counts used by the downstream gate."""

    rows = (
        await session.execute(
            text("""
                select p.page_number,
                       count(1) filter (where c.state = 'unverified') as unverified,
                       count(1) filter (where c.state = 'verified')   as verified,
                       count(1) filter (where c.state = 'excluded')   as excluded
                from source_v2_pages p
                join source_v2_machine_candidates c
                  on c.page_id = p.id and c.is_current
                where p.document_id = :document_id
                group by p.page_number
                order by p.page_number
            """),
            {"document_id": document_id},
        )
    ).all()
    return {
        int(number): {"unverified": u, "verified": v, "excluded": e}
        for number, u, v, e in rows
    }
