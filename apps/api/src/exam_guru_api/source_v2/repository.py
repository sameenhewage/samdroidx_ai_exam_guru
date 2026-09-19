"""Async Source V2 storage for the HTTP layer.

The offline importer in `service.py` runs synchronously against psycopg; the
API runs on an async SQLAlchemy session. Rather than duplicate the rules, both
go through the same guard functions in `domain.py`, and the SQL lives here once
for the async path.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.source_v2.domain import (
    ReviewAction,
    SourceV2Error,
    StaleReviewError,
)
from exam_guru_api.source_v2.source_kind import SourceKind, propose

HUMAN_CORRECTION = "human-correction"

# Where the immutable rendered pages live inside the API container. Bound
# read-only by compose; rendered evidence is never written through the API.
RENDER_ROOT = Path(os.environ.get("EXAM_GURU_SOURCE_V2_RENDER_ROOT", "/source-content"))


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
    source_kind: str = "undecided"
    proposed_source_kind: str | None = None
    crop_sha256: str | None = None


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
                       c.agreement_ratio, c.disagreement, c.state, v.text,
                       c.source_kind, c.proposed_source_kind, c.crop_sha256
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
            source_kind=row[14],
            proposed_source_kind=row[15],
            crop_sha256=row[16],
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
    facts = await _region_facts(session, page.page_id, region_id)
    if facts["source_kind"] in {"visual_only", "decorative"}:
        # Confirming *text* on a region that carries none would either store an
        # empty verified row or invite someone to invent a description. Both
        # are wrong; the reviewer needs the visual action or a reclassification.
        raise SourceV2Error(
            f"region {region_id} is {facts['source_kind']}; use confirm-visual "
            "or reclassify it first"
        )
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
               reviewer_id, image_sha256, verified_at, source_kind, crop_sha256, bbox)
            values (:id, :page_id, :region_id, :candidate_id, :revision, :text, :text_nfc,
                    :reviewer_id, :sha, :verified_at, :source_kind, :crop_sha256, :bbox)
        """),
        {
            "id": verified_id,
            "source_kind": facts["source_kind"],
            "crop_sha256": facts["crop_sha256"],
            "bbox": facts["bbox"],
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


async def _region_facts(session: AsyncSession, page_id: UUID, region_id: str) -> dict:
    """Kind, canonical crop and geometry for the current candidate."""

    row = (
        await session.execute(
            text(
                "select source_kind, crop_sha256, bbox from source_v2_machine_candidates"
                " where page_id = :page_id and region_id = :region_id and is_current"
            ),
            {"page_id": page_id, "region_id": region_id},
        )
    ).first()
    if row is None:
        raise SourceV2Error(f"no current candidate for region {region_id}")
    bbox = row[2]
    return {
        "source_kind": row[0],
        "crop_sha256": row[1],
        "bbox": json.dumps(bbox) if bbox is not None and not isinstance(bbox, str) else bbox,
    }


async def confirm_visual(
    session: AsyncSession,
    *,
    page: PageHeader,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    compared_with_image_sha256: str,
    source_kind: str,
    text_value: str | None = None,
    note: str | None = None,
) -> UUID:
    """Verify an educational figure *as a figure* (D18).

    A drawing with no printed text is real source content. Before D18 the only
    available action was Exclude, which quietly discarded it.
    """

    if compared_with_image_sha256 != page.image_sha256:
        raise StaleReviewError(
            "confirmation must cite the rendered page that was actually compared"
        )
    if source_kind not in {"visual_only", "visual_with_text"}:
        raise SourceV2Error(f"{source_kind} is not a visual source kind")
    await _require_current(session, page.page_id, region_id, candidate_id, revision)
    facts = await _region_facts(session, page.page_id, region_id)
    if not facts["crop_sha256"]:
        raise SourceV2Error(
            f"region {region_id} has no canonical crop; a verified visual must name "
            "the image it was confirmed against"
        )

    body = "" if source_kind == "visual_only" else unicodedata.normalize(
        "NFC", (text_value or "").strip()
    )
    if source_kind == "visual_with_text" and not body:
        raise SourceV2Error(
            "visual_with_text must carry the printed labels; confirm it as "
            "visual_only if the figure has no text"
        )
    if source_kind == "visual_only" and (text_value or "").strip():
        # Text supplied for a visual-only region means the reviewer saw labels.
        # Silently dropping them would lose source; silently keeping them would
        # contradict the declared kind.
        raise SourceV2Error(
            "text was supplied for a visual_only region; confirm it as "
            "visual_with_text instead"
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
        note=note or f"confirmed as {source_kind}",
    )
    await session.execute(
        text(
            "update source_v2_machine_candidates set state = 'verified',"
            " source_kind = :kind, text = :text"
            " where page_id = :page_id and region_id = :region_id and is_current"
        ),
        {"kind": source_kind, "text": body, "page_id": page.page_id, "region_id": region_id},
    )
    verified_id = uuid4()
    await session.execute(
        text("""
            insert into source_v2_verified_regions
              (id, page_id, region_id, candidate_id, candidate_revision, text, text_nfc,
               reviewer_id, image_sha256, verified_at, source_kind, crop_sha256, bbox)
            values (:id, :page_id, :region_id, :candidate_id, :revision, :text, :text,
                    :reviewer_id, :sha, now(), :kind, :crop, :bbox)
        """),
        {
            "id": verified_id,
            "page_id": page.page_id,
            "region_id": region_id,
            "candidate_id": candidate_id,
            "revision": revision,
            "text": body,
            "reviewer_id": reviewer_id,
            "sha": page.image_sha256,
            "kind": source_kind,
            "crop": facts["crop_sha256"],
            "bbox": facts["bbox"],
        },
    )
    return verified_id


async def reclassify(
    session: AsyncSession,
    *,
    page: PageHeader,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    source_kind: str,
    note: str,
) -> None:
    """The reviewer disagrees with the proposed kind.

    Recorded as its own review event and never applied by the machine. The
    proposal is left untouched so the disagreement stays visible.
    """

    await _require_current(session, page.page_id, region_id, candidate_id, revision)
    await _append_event(
        session,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.CORRECT,
        reviewer_id=reviewer_id,
        sha=page.image_sha256,
        note=f"reclassified as {source_kind}: {note}",
    )
    await session.execute(
        text(
            "update source_v2_machine_candidates set source_kind = :kind"
            " where page_id = :page_id and region_id = :region_id and is_current"
        ),
        {"kind": source_kind, "page_id": page.page_id, "region_id": region_id},
    )


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


async def import_page(
    session: AsyncSession,
    *,
    document_id: UUID,
    page_number: int,
    language: str,
    image_sha256: str,
    width: int,
    height: int,
    dpi: float,
    detector_version: str,
    layout: dict,
    candidates: list[dict],
    reader_results: list[dict],
    refresh: bool = False,
) -> dict:
    """Ingest one page of Source Factory output.

    Idempotent on (document, page, rendered image sha256). A *different*
    render of the same page number is refused rather than silently replacing
    evidence: a re-render is a new page and verification does not follow it.
    With `refresh`, a re-run of the readers supersedes the current reading with
    a new revision instead of being skipped; nothing is ever deleted.
    """

    existing = (
        await session.execute(
            text(
                "select id, image_sha256 from source_v2_pages"
                " where document_id = :document_id and page_number = :page_number"
            ),
            {"document_id": document_id, "page_number": page_number},
        )
    ).first()
    if existing is not None:
        if existing[1] != image_sha256:
            raise SourceV2Error(
                f"page {page_number} is already stored against a different render "
                f"({existing[1][:12]}…); a re-render is a new page"
            )
        if not refresh:
            return {
                "page_id": existing[0],
                "page_number": page_number,
                "regions": 0,
                "reader_rows": 0,
                "reused": True,
            }
        return await _supersede(session, existing[0], page_number, candidates)

    page_id = uuid4()
    await session.execute(
        text("""
            insert into source_v2_pages
              (id, document_id, page_number, image_sha256, dpi, width, height,
               language, detector_version, layout)
            values (:id, :document_id, :page_number, :sha, :dpi, :width, :height,
                    :language, :detector_version, cast(:layout as jsonb))
        """),
        {
            "id": page_id,
            "document_id": document_id,
            "page_number": page_number,
            "sha": image_sha256,
            "dpi": dpi,
            "width": width,
            "height": height,
            "language": language,
            "detector_version": detector_version,
            "layout": json.dumps(layout, ensure_ascii=False),
        },
    )
    for result in reader_results:
        await session.execute(
            text("""
                insert into source_v2_reader_candidates
                  (id, page_id, region_id, reader, text, abstained, failure, seconds, signals)
                values (:id, :page_id, :region_id, :reader, :text, :abstained, :failure,
                        :seconds, cast(:signals as jsonb))
                on conflict (page_id, region_id, reader) do nothing
            """),
            {
                "id": uuid4(),
                "page_id": page_id,
                "region_id": result["region_id"],
                "reader": result["reader"],
                "text": result.get("text", ""),
                "abstained": bool(result.get("abstained")),
                "failure": result.get("failure"),
                "seconds": float(result.get("seconds", 0.0)),
                "signals": json.dumps(result.get("signals", {}), ensure_ascii=False),
            },
        )
    for region in candidates:
        await session.execute(
            text("""
                insert into source_v2_machine_candidates
                  (id, page_id, region_id, region_type, revision, origin, text, abstained,
                   chosen_reader, reason, critical_conflict, agreement_ratio, disagreement,
                   state, is_current)
                values (:id, :page_id, :region_id, :region_type, 1, 'machine', :text,
                        :abstained, :chosen_reader, :reason, :critical_conflict,
                        :agreement_ratio, cast(:disagreement as jsonb), 'unverified', true)
            """),
            {
                "id": uuid4(),
                "page_id": page_id,
                "region_id": region["region_id"],
                "region_type": region["region_type"],
                "text": unicodedata.normalize("NFC", region.get("text", "")),
                "abstained": bool(region.get("abstained")),
                "chosen_reader": region.get("chosen_reader"),
                "reason": (region.get("reason") or "")[:400],
                "critical_conflict": bool(region.get("critical_conflict")),
                "agreement_ratio": float(region.get("agreement_ratio", 1.0)),
                "disagreement": json.dumps(region.get("disagreement", {}), ensure_ascii=False),
            },
        )
    return {
        "page_id": page_id,
        "page_number": page_number,
        "regions": len(candidates),
        "reader_rows": len(reader_results),
        "reused": False,
    }


async def _supersede(
    session: AsyncSession, page_id: UUID, page_number: int, candidates: list[dict]
) -> dict:
    superseded = 0
    withdrawn = 0
    added = 0
    for region in candidates:
        current = (
            await session.execute(
                text(
                    "select id, revision, text, chosen_reader, source_kind, crop_sha256"
                    " from source_v2_machine_candidates"
                    " where page_id = :page_id and region_id = :region_id and is_current"
                ),
                {"page_id": page_id, "region_id": region["region_id"]},
            )
        ).first()
        proposed = unicodedata.normalize("NFC", region.get("text", ""))
        if current is None:
            # A re-run found a region the previous one missed. It has no
            # history to supersede, so it starts at revision 1 like any other.
            await _insert_candidate(session, page_id, region, revision=1, parent_id=None)
            added += 1
            continue
        # Identical text is normally nothing to do. But if the *source* of that
        # text has been renamed - an earlier provenance label that no longer
        # exists - the row would silently keep attributing the reading to
        # something that is not in the pipeline any more. Supersede it so the
        # attribution stays true, without touching any verification: the text
        # did not change, so nothing a reviewer confirmed has changed either.
        # D18: a row created before source kinds existed carries 'undecided'
        # and no crop. Refreshing that is not a text change, so it must not
        # supersede a revision or withdraw anybody's verification.
        kind = str(
            SourceKind(region["source_kind"])
            if region.get("source_kind")
            else propose(
                region["region_type"], has_text=bool((region.get("text") or "").strip())
            )
        )
        attribution_stale = (
            current[3] != region.get("chosen_reader")
            or current[4] != kind
            or current[5] != region.get("crop_sha256")
        )
        if current[2] == proposed and not attribution_stale:
            continue
        if current[2] == proposed and attribution_stale:
            await session.execute(
                text(
                    "update source_v2_machine_candidates"
                    " set chosen_reader = :reader, crop_sha256 = :crop,"
                    "     proposed_source_kind = :kind,"
                    # Never overwrite a kind a human already settled.
                    "     source_kind = case when source_kind = 'undecided'"
                    "                        then :kind else source_kind end"
                    " where id = :id"
                ),
                {
                    "reader": region.get("chosen_reader"),
                    "crop": region.get("crop_sha256"),
                    "kind": kind,
                    "id": current[0],
                },
            )
            superseded += 1
            continue
        await session.execute(
            text(
                "update source_v2_machine_candidates"
                " set is_current = false, state = 'unverified' where id = :id"
            ),
            {"id": current[0]},
        )
        removed = (
            await session.execute(
                text(
                    "delete from source_v2_verified_regions"
                    " where page_id = :page_id and region_id = :region_id returning id"
                ),
                {"page_id": page_id, "region_id": region["region_id"]},
            )
        ).all()
        withdrawn += len(removed)
        await _insert_candidate(
            session, page_id, region, revision=current[1] + 1, parent_id=current[0]
        )
        superseded += 1
    return {
        "page_id": page_id,
        "page_number": page_number,
        "regions": added,
        "reader_rows": 0,
        "reused": True,
        "superseded": superseded,
        "verifications_withdrawn": withdrawn,
    }


async def _insert_candidate(
    session: AsyncSession,
    page_id: UUID,
    region: dict,
    *,
    revision: int,
    parent_id: UUID | None,
) -> None:
    supplied = region.get("source_kind")
    proposed = (
        SourceKind(supplied)
        if supplied
        else propose(
            region["region_type"],
            has_text=bool((region.get("text") or "").strip()),
        )
    )
    await session.execute(
        text("""
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, parent_id, origin, text,
               abstained, chosen_reader, reason, critical_conflict, agreement_ratio,
               disagreement, state, is_current, source_kind, proposed_source_kind,
               crop_sha256)
            values (:id, :page_id, :region_id, :region_type, :revision, :parent_id,
                    'machine', :text, :abstained, :chosen_reader, :reason,
                    :critical_conflict, :agreement_ratio, cast(:disagreement as jsonb),
                    'unverified', true, :source_kind, :proposed_source_kind,
                    :crop_sha256)
        """),
        {
            "id": uuid4(),
            "page_id": page_id,
            "region_id": region["region_id"],
            "region_type": region["region_type"],
            "revision": revision,
            "parent_id": parent_id,
            "text": unicodedata.normalize("NFC", region.get("text", "")),
            "abstained": bool(region.get("abstained")),
            "chosen_reader": region.get("chosen_reader"),
            "reason": (region.get("reason") or "")[:400],
            "critical_conflict": bool(region.get("critical_conflict")),
            "agreement_ratio": float(region.get("agreement_ratio", 1.0)),
            "disagreement": json.dumps(region.get("disagreement", {}), ensure_ascii=False),
            # D18: the machine proposes from deterministic evidence only. The
            # proposal is kept beside the working value so a later human
            # reclassification is visible rather than silent.
            "source_kind": str(proposed),
            "proposed_source_kind": str(proposed),
            "crop_sha256": region.get("crop_sha256"),
        },
    )


def rendered_page_bytes(page: PageHeader, *, root: Path | None = None) -> bytes:
    """The exact render the readers saw, verified against the stored checksum.

    The checksum is re-computed on every read. If the file on disk is not the
    render this page was measured from, serving it would let a reviewer confirm
    a reading against a different image.
    """

    base = root or RENDER_ROOT
    matches = sorted(base.glob(f"**/rendered/page-{page.page_number:03d}.png"))
    for path in matches:
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() == page.image_sha256:
            return payload
    raise PageNotFoundError(
        f"no render matching {page.image_sha256[:12]}… for page {page.page_number} under {base}"
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
