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
from exam_guru_api.source_v2.visual_description import (
    DescriptionRefusedError,
    assert_describable,
    validate,
    validate_labels,
)

HUMAN_CORRECTION = "human-correction"

# Where the immutable rendered pages live inside the API container. Bound
# read-only by compose; rendered evidence is never written through the API.
RENDER_ROOT = Path(os.environ.get("EXAM_GURU_SOURCE_V2_RENDER_ROOT", "/source-content"))


class PageNotFoundError(SourceV2Error):
    """The requested Source V2 page does not exist."""


class CropNotFoundError(SourceV2Error):
    """No canonical crop on disk matches what this region was read from."""


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
    #: The text printed inside the crop. Source, and only that.
    text: str
    abstained: bool
    reason: str
    state: str
    bbox: list[int] | None
    verified_text: str | None
    source_kind: str = "undecided"
    proposed_source_kind: str | None = None
    crop_sha256: str | None = None
    #: Derived knowledge about the picture. Never source (D18).
    visual_description: str | None = None
    detected_labels: tuple[str, ...] = ()


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
                       c.abstained, c.reason, c.state, v.text,
                       c.source_kind, c.proposed_source_kind, c.crop_sha256,
                       -- The verified copy wins where one exists: it is the
                       -- description attached to the verification a human made.
                       coalesce(v.visual_description, c.visual_description),
                       coalesce(v.detected_labels, c.detected_labels)
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
    boxes = {region.get("id"): region.get("bbox") for region in (layout.get("regions") or [])}
    return [
        RegionRow(
            region_id=row[0],
            region_type=row[1],
            candidate_id=row[2],
            revision=row[3],
            origin=row[4],
            text=row[5],
            abstained=row[6],
            reason=row[7],
            state=row[8],
            bbox=boxes.get(row[0]),
            verified_text=row[9],
            source_kind=row[10],
            proposed_source_kind=row[11],
            crop_sha256=row[12],
            visual_description=row[13],
            detected_labels=_labels(row[14]),
        )
        for row in rows
    ]


def _labels(raw: object) -> tuple[str, ...]:
    """Whatever JSONB holds, read as an ordered list of label strings."""

    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if str(item).strip())


async def region_crop_sha256(session: AsyncSession, page_id: UUID, region_id: str) -> str:
    """The canonical crop checksum recorded for the current candidate.

    Read from the verified row first where one exists: that is the image the
    human actually compared against, and it is the checksum a verified visual
    is required to carry.
    """

    row = (
        await session.execute(
            text(
                "select coalesce(v.crop_sha256, c.crop_sha256)"
                " from source_v2_machine_candidates c"
                " left join source_v2_verified_regions v"
                "        on v.page_id = c.page_id and v.region_id = c.region_id"
                " where c.page_id = :page_id and c.region_id = :region_id and c.is_current"
            ),
            {"page_id": page_id, "region_id": region_id},
        )
    ).first()
    if row is None:
        raise PageNotFoundError(f"unknown region {region_id}")
    if not row[0]:
        raise CropNotFoundError(f"region {region_id} has no canonical crop")
    return str(row[0])


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
               reviewer_id, image_sha256, verified_at, source_kind, crop_sha256, bbox,
               visual_description, detected_labels)
            values (:id, :page_id, :region_id, :candidate_id, :revision, :text, :text_nfc,
                    :reviewer_id, :sha, :verified_at, :source_kind, :crop_sha256, :bbox,
                    :description, cast(:labels as jsonb))
        """),
        {
            "id": verified_id,
            "source_kind": facts["source_kind"],
            "crop_sha256": facts["crop_sha256"],
            "bbox": facts["bbox"],
            # Derived knowledge travels with the verification rather than
            # being re-read later from a candidate that may have moved on.
            "description": facts["visual_description"],
            "labels": facts["detected_labels"],
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
    """Kind, canonical crop, derived description and geometry for the current candidate."""

    row = (
        await session.execute(
            text(
                "select source_kind, crop_sha256, visual_description, detected_labels,"
                " text, state, proposed_source_kind from source_v2_machine_candidates"
                " where page_id = :page_id and region_id = :region_id and is_current"
            ),
            {"page_id": page_id, "region_id": region_id},
        )
    ).first()
    if row is None:
        raise SourceV2Error(f"no current candidate for region {region_id}")
    # Geometry belongs to the deterministic layout, not to the candidate. It is
    # copied onto the verified row so a verified visual keeps its own record of
    # where on the page it came from.
    layout = (
        await session.execute(
            text("select layout from source_v2_pages where id = :page_id"),
            {"page_id": page_id},
        )
    ).scalar_one_or_none() or {}
    bbox = next(
        (
            region.get("bbox")
            for region in (layout.get("regions") or [])
            if region.get("id") == region_id
        ),
        None,
    )
    return {
        "source_kind": row[0],
        "crop_sha256": row[1],
        "visual_description": row[2],
        "detected_labels": json.dumps(list(_labels(row[3])), ensure_ascii=False),
        "text": row[4],
        "state": row[5],
        "proposed_source_kind": row[6],
        "bbox": json.dumps(bbox) if bbox is not None else None,
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

    body = (
        ""
        if source_kind == "visual_only"
        else unicodedata.normalize("NFC", (text_value or "").strip())
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
            "text was supplied for a visual_only region; confirm it as visual_with_text instead"
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
               reviewer_id, image_sha256, verified_at, source_kind, crop_sha256, bbox,
               visual_description, detected_labels)
            values (:id, :page_id, :region_id, :candidate_id, :revision, :text, :text,
                    :reviewer_id, :sha, now(), :kind, :crop, :bbox,
                    :description, cast(:labels as jsonb))
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
            "description": facts["visual_description"],
            "labels": facts["detected_labels"],
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
    """The reviewer overrules the proposed kind.

    Recorded as its own review event and never applied by the machine. The
    machine's original proposal is left untouched so the difference between
    what was proposed and what the human decided stays visible.
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
    # Correcting the *printed text* says nothing about what the picture shows.
    # Dropping the description here is how a reviewer loses work they already
    # did, with nothing on screen to tell them it happened.
    facts = await _region_facts(session, page.page_id, region_id)
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
               abstained, reason, state, is_current, source_kind, proposed_source_kind,
               crop_sha256, visual_description, detected_labels)
            values (:id, :page_id, :region_id, :region_type, :revision, :parent_id, :origin,
                    :text, false, :reason, 'unverified', true, :kind, :proposed_kind,
                    :crop, :description, cast(:labels as jsonb))
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
            # The kind, the crop and the description describe the same region
            # and the same pixels. Only the transcription changed. The
            # machine's original proposal is carried, not re-derived, so a
            # human reclassification stays visible across revisions.
            "kind": facts["source_kind"],
            "proposed_kind": facts["proposed_source_kind"],
            "crop": facts["crop_sha256"],
            "description": facts["visual_description"],
            "labels": facts["detected_labels"],
        },
    )
    return child_id


async def describe(
    session: AsyncSession,
    *,
    page: PageHeader,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    description: str,
    detected_labels: list[str] | None = None,
) -> None:
    """Save what the picture shows. Derived knowledge, not a verification.

    Three things make this an ordinary edit rather than a review decision:
    it writes no review event, it never touches `state`, and it never touches
    `text`. A reviewer describing a figure has not re-checked the printed
    text, so pretending they confirmed anything would manufacture trust
    nobody gave (D5).

    Validation runs before the write, not after, and a refusal carries its
    findings. A description that quietly stores a measurement the crop does
    not print is worse than no description: a later reader cannot tell it was
    imported from elsewhere on the page.
    """

    await _require_current(session, page.page_id, region_id, candidate_id, revision)
    facts = await _region_facts(session, page.page_id, region_id)
    assert_describable(
        source_kind=facts["source_kind"],
        has_canonical_visual=bool(facts["crop_sha256"]),
    )

    labels = [label.strip() for label in (detected_labels or [])]
    body = unicodedata.normalize("NFC", description).strip()
    findings = validate(body, language=page.language, visible_text=facts["text"])
    findings.extend(validate_labels(labels, visible_text=facts["text"]))
    if findings:
        raise DescriptionRefusedError(
            "this description asserts something the crop does not show: "
            + "; ".join(str(finding) for finding in findings)
        )

    payload = {
        "page_id": page.page_id,
        "region_id": region_id,
        "description": body,
        "labels": json.dumps([label for label in labels if label], ensure_ascii=False),
    }
    await session.execute(
        text(
            "update source_v2_machine_candidates"
            " set visual_description = :description,"
            "     detected_labels = cast(:labels as jsonb)"
            " where page_id = :page_id and region_id = :region_id and is_current"
        ),
        payload,
    )
    # The verified row keeps its own copy, so the description a reviewer reads
    # beside verified source is the one stored against that verification.
    await session.execute(
        text(
            "update source_v2_verified_regions"
            " set visual_description = :description,"
            "     detected_labels = cast(:labels as jsonb)"
            " where page_id = :page_id and region_id = :region_id"
        ),
        payload,
    )


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
    refresh: bool = False,
) -> dict:
    """Ingest one page of Source Factory output.

    Idempotent on (document, page, rendered image sha256). A *different*
    render of the same page number is refused rather than silently replacing
    evidence: a re-render is a new page and verification does not follow it.
    With `refresh`, a fresh reading supersedes the current one with a new
    revision instead of being skipped; nothing is ever deleted.
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
    for region in candidates:
        # D18: a brand-new page proposes its source kinds here, exactly as a
        # refreshed one does in `_insert_candidate`. Leaving this path out is
        # how every region on a freshly published document arrived as
        # 'undecided' and could not be confirmed at all.
        supplied = region.get("source_kind")
        proposed_kind = str(
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
                  (id, page_id, region_id, region_type, revision, origin, text, abstained,
                   reason, state, is_current, source_kind, proposed_source_kind, crop_sha256)
                values (:id, :page_id, :region_id, :region_type, 1, 'machine', :text,
                        :abstained, :reason, 'unverified', true,
                        :source_kind, :proposed_source_kind, :crop_sha256)
            """),
            {
                "id": uuid4(),
                "page_id": page_id,
                "region_id": region["region_id"],
                "region_type": region["region_type"],
                "text": unicodedata.normalize("NFC", region.get("text", "")),
                "abstained": bool(region.get("abstained")),
                "reason": (region.get("reason") or "")[:400],
                "source_kind": proposed_kind,
                "proposed_source_kind": proposed_kind,
                "crop_sha256": region.get("crop_sha256"),
            },
        )
    return {
        "page_id": page_id,
        "page_number": page_number,
        "regions": len(candidates),
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
                    "select id, revision, text, source_kind, crop_sha256,"
                    "       visual_description, detected_labels"
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
        # Identical text is normally nothing to do. But if the row's
        # *provenance* has moved on - a different canonical crop, or a kind
        # proposal it predates - it would silently keep describing evidence
        # that is no longer the evidence. Refresh that in place, without
        # touching any verification: the text did not change, so nothing a
        # reviewer confirmed has changed either.
        # D18: a row created before source kinds existed carries 'undecided'
        # and no crop. Refreshing that is not a text change, so it must not
        # supersede a revision or withdraw anybody's verification.
        kind = str(
            SourceKind(region["source_kind"])
            if region.get("source_kind")
            else propose(region["region_type"], has_text=bool((region.get("text") or "").strip()))
        )
        provenance_stale = current[3] != kind or current[4] != region.get("crop_sha256")
        if current[2] == proposed and not provenance_stale:
            continue
        if current[2] == proposed and provenance_stale:
            await session.execute(
                text(
                    "update source_v2_machine_candidates"
                    " set crop_sha256 = :crop,"
                    "     proposed_source_kind = :kind,"
                    # Never overwrite a kind a human already settled.
                    "     source_kind = case when source_kind = 'undecided'"
                    "                        then :kind else source_kind end"
                    " where id = :id"
                ),
                {
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
        # A fresh transcription of the same pixels does not make an existing
        # description of those pixels false. Verification is withdrawn because
        # the *text* changed; the derived description is carried onto the new
        # revision so it is not silently lost by a re-import.
        await _insert_candidate(
            session,
            page_id,
            region,
            revision=current[1] + 1,
            parent_id=current[0],
            visual_description=current[5],
            detected_labels=current[6],
        )
        superseded += 1
    return {
        "page_id": page_id,
        "page_number": page_number,
        "regions": added,
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
    visual_description: str | None = None,
    detected_labels: object = None,
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
               abstained, reason, state, is_current, source_kind, proposed_source_kind,
               crop_sha256, visual_description, detected_labels)
            values (:id, :page_id, :region_id, :region_type, :revision, :parent_id,
                    'machine', :text, :abstained, :reason,
                    'unverified', true, :source_kind, :proposed_source_kind,
                    :crop_sha256, :visual_description, cast(:detected_labels as jsonb))
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
            "reason": (region.get("reason") or "")[:400],
            # D18: the machine proposes from deterministic evidence only. The
            # proposal is kept beside the working value so a later human
            # reclassification is visible rather than silent.
            "source_kind": str(proposed),
            "proposed_source_kind": str(proposed),
            "crop_sha256": region.get("crop_sha256"),
            # The importer never authors a description. It only carries one
            # that already existed for this region forward to the new revision.
            "visual_description": visual_description,
            "detected_labels": json.dumps(list(_labels(detected_labels)), ensure_ascii=False),
        },
    )


def rendered_page_bytes(page: PageHeader, *, root: Path | None = None) -> bytes:
    """The exact render the agent read, verified against the stored checksum.

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


def crop_filename(page_number: int, region_id: str) -> str:
    """`p186-r002` on page 186 is `crop-186-r002.png`.

    The region id carries the page it belongs to, but the *page number on the
    page row* is the authority: a crop named from a region id alone could be
    served for a page it was never cut from.
    """

    suffix = region_id.rsplit("-", 1)[-1]
    return f"crop-{page_number:03d}-{suffix}.png"


def crop_bytes(
    page: PageHeader,
    region_id: str,
    crop_sha256: str,
    *,
    root: Path | None = None,
) -> bytes:
    """The canonical crop this region was read from, checksum-verified.

    Exactly the contract `rendered_page_bytes` holds, for the same reason.
    D17 makes the crop the only image a region may be read from, by the agent
    transcribing it and by the reviewer confirming it; serving different
    pixels under the same name would let someone confirm a reading against an
    image it did not come from. The checksum is recomputed on every read, and
    a mismatch is a refusal, not a warning.

    The original crop stays available after extraction and confirmation.
    Derived text never replaces the image.
    """

    if not crop_sha256:
        raise CropNotFoundError(
            f"region {region_id} has no canonical crop; nothing can be served for it"
        )
    base = root or RENDER_ROOT
    name = crop_filename(page.page_number, region_id)
    for path in sorted(base.glob(f"**/crops/{name}")):
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() == crop_sha256:
            return payload
    raise CropNotFoundError(
        f"no crop matching {crop_sha256[:12]}… for region {region_id} "
        f"(expected {name}) under {base}"
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
        int(number): {"unverified": u, "verified": v, "excluded": e} for number, u, v, e in rows
    }
