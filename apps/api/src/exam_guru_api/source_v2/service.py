"""Import layout + Machine Candidates into Source V2 storage, and record decisions.

The importer is deliberately dumb about content: it moves what the offline
pipeline produced into the tables that enforce the rules. It never invents a
reading, never marks anything verified, and is idempotent on
(document, page, rendered image sha256) so a re-run cannot fork the evidence.

A re-render changes the page sha256, which is a *new* page: prior verification
does not follow it. That is the point.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from exam_guru_api.source_v2.domain import ReviewAction, SourceV2Error, StaleReviewError

MACHINE = "machine"
HUMAN_CORRECTION = "human-correction"


class Rows(Protocol):
    """The narrow database surface this service needs."""

    def execute(self, query: str, params: tuple = ()) -> Any: ...


@dataclass(frozen=True)
class ImportedPage:
    page_id: UUID
    document_id: UUID
    page_number: int
    image_sha256: str
    regions: int
    reader_rows: int
    reused: bool


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def import_page(
    rows: Rows,
    *,
    document_id: UUID,
    page_number: int,
    language: str,
    layout: dict,
    candidates: list[dict],
    reader_results: dict[str, list[dict]] | None = None,
) -> ImportedPage:
    """Store one page's layout, reader evidence and Machine Candidates.

    `layout` is the `page-layout.schema.json` payload; `candidates` are the
    regions from `candidates/page-NNN.json`.
    """

    image_sha256 = layout["image_sha256"]
    existing = rows.execute(
        "select id, image_sha256 from source_v2_pages "
        "where document_id = %s and page_number = %s",
        (document_id, page_number),
    ).fetchone()
    if existing is not None:
        if existing[1] != image_sha256:
            raise SourceV2Error(
                f"page {page_number} is already stored against a different render "
                f"({existing[1][:12]}…); re-rendering creates a new page, it does not "
                "silently replace verified evidence"
            )
        return ImportedPage(
            page_id=existing[0],
            document_id=document_id,
            page_number=page_number,
            image_sha256=image_sha256,
            regions=0,
            reader_rows=0,
            reused=True,
        )

    page_id = uuid4()
    rows.execute(
        """
        insert into source_v2_pages
          (id, document_id, page_number, image_sha256, dpi, width, height,
           language, detector_version, layout)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            page_id,
            document_id,
            page_number,
            image_sha256,
            float(layout["dpi"]),
            int(layout["width"]),
            int(layout["height"]),
            language,
            layout["detector_version"],
            _json(layout),
        ),
    )

    reader_rows = 0
    for reader, results in (reader_results or {}).items():
        for result in results:
            rows.execute(
                """
                insert into source_v2_reader_candidates
                  (id, page_id, region_id, reader, text, abstained, failure, seconds, signals)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    page_id,
                    result["region_id"],
                    reader,
                    result.get("text", ""),
                    bool(result.get("abstained")),
                    result.get("failure"),
                    float(result.get("seconds", 0.0)),
                    _json(
                        {
                            key: result[key]
                            for key in (
                                "repetition",
                                "structural_repetition",
                                "foreign_script",
                                "peak_vram_bytes",
                            )
                            if key in result
                        }
                    ),
                ),
            )
            reader_rows += 1

    for region in candidates:
        rows.execute(
            """
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, origin, text, abstained,
               chosen_reader, reason, critical_conflict, agreement_ratio, disagreement,
               state, is_current)
            values (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, 'unverified', true)
            """,
            (
                uuid4(),
                page_id,
                region["region_id"],
                region["region_type"],
                MACHINE,
                region.get("text", ""),
                bool(region.get("abstained")),
                region.get("chosen_reader"),
                region.get("reason", "")[:400],
                bool(region.get("critical_conflict")),
                float(region.get("agreement_ratio", 1.0)),
                _json(region.get("disagreement", {})),
            ),
        )

    return ImportedPage(
        page_id=page_id,
        document_id=document_id,
        page_number=page_number,
        image_sha256=image_sha256,
        regions=len(candidates),
        reader_rows=reader_rows,
        reused=False,
    )


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _current(rows: Rows, page_id: UUID, region_id: str) -> tuple:
    row = rows.execute(
        """
        select id, revision, text, abstained, region_type
        from source_v2_machine_candidates
        where page_id = %s and region_id = %s and is_current
        """,
        (page_id, region_id),
    ).fetchone()
    if row is None:
        raise SourceV2Error(f"no current candidate for region {region_id}")
    return row


def _check_current(rows: Rows, page_id: UUID, region_id: str, candidate_id: UUID, revision: int):
    row = _current(rows, page_id, region_id)
    if row[0] != candidate_id or row[1] != revision:
        raise StaleReviewError(
            f"region {region_id} moved on: the current candidate is {row[0]} revision {row[1]}"
        )
    return row


def _page_sha(rows: Rows, page_id: UUID) -> str:
    row = rows.execute(
        "select image_sha256 from source_v2_pages where id = %s", (page_id,)
    ).fetchone()
    if row is None:
        raise SourceV2Error(f"unknown page {page_id}")
    return row[0]


def _event(
    rows: Rows,
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
    rows.execute(
        """
        insert into source_v2_review_events
          (id, page_id, region_id, candidate_id, candidate_revision, action, reviewer_id,
           compared_with_image_sha256, note, corrected_text)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            page_id,
            region_id,
            candidate_id,
            revision,
            str(action),
            reviewer_id,
            sha,
            note,
            corrected_text,
        ),
    )
    return event_id


def confirm(
    rows: Rows,
    *,
    page_id: UUID,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    compared_with_image_sha256: str,
    note: str | None = None,
) -> UUID:
    """Accept the current reading after comparing it with the original page."""

    sha = _page_sha(rows, page_id)
    if compared_with_image_sha256 != sha:
        raise StaleReviewError(
            "confirmation must cite the rendered page that was actually compared"
        )
    row = _check_current(rows, page_id, region_id, candidate_id, revision)
    text, abstained = row[2], row[3]
    if abstained or not text.strip():
        raise SourceV2Error(
            f"region {region_id} abstained or is empty; correct or exclude it instead"
        )
    _event(
        rows,
        page_id=page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.CONFIRM,
        reviewer_id=reviewer_id,
        sha=sha,
        note=note,
    )
    verified_id = uuid4()
    rows.execute(
        """
        insert into source_v2_verified_regions
          (id, page_id, region_id, candidate_id, candidate_revision, text, text_nfc,
           reviewer_id, image_sha256, verified_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            verified_id,
            page_id,
            region_id,
            candidate_id,
            revision,
            text,
            _nfc(text),
            reviewer_id,
            sha,
            datetime.now(UTC),
        ),
    )
    rows.execute(
        "update source_v2_machine_candidates set state = 'verified' where id = %s",
        (candidate_id,),
    )
    return verified_id


def correct(
    rows: Rows,
    *,
    page_id: UUID,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    corrected_text: str,
    note: str | None = None,
) -> UUID:
    """Replace the reading with the reviewer's text as an unverified child.

    The reviewer still has to confirm the corrected reading against the page.
    Correcting is not verifying.
    """

    if not corrected_text.strip():
        raise SourceV2Error("a correction must contain text; use exclude to drop a region")
    sha = _page_sha(rows, page_id)
    row = _check_current(rows, page_id, region_id, candidate_id, revision)
    _event(
        rows,
        page_id=page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.CORRECT,
        reviewer_id=reviewer_id,
        sha=sha,
        note=note,
        corrected_text=corrected_text,
    )
    # Any earlier verification of this region is withdrawn: the text changed.
    rows.execute(
        "delete from source_v2_verified_regions where page_id = %s and region_id = %s",
        (page_id, region_id),
    )
    rows.execute(
        "update source_v2_machine_candidates set is_current = false, state = 'unverified' "
        "where id = %s",
        (candidate_id,),
    )
    child_id = uuid4()
    rows.execute(
        """
        insert into source_v2_machine_candidates
          (id, page_id, region_id, region_type, revision, parent_id, origin, text,
           abstained, chosen_reader, reason, state, is_current)
        values (%s, %s, %s, %s, %s, %s, %s, %s, false, null, %s, 'unverified', true)
        """,
        (
            child_id,
            page_id,
            region_id,
            row[4],
            revision + 1,
            candidate_id,
            HUMAN_CORRECTION,
            corrected_text,
            "human correction; awaiting confirmation",
        ),
    )
    return child_id


def exclude(
    rows: Rows,
    *,
    page_id: UUID,
    region_id: str,
    candidate_id: UUID,
    revision: int,
    reviewer_id: UUID,
    note: str,
) -> None:
    """Take a region out of use, keeping every trace of why."""

    if not note.strip():
        raise SourceV2Error("an exclusion must say why")
    sha = _page_sha(rows, page_id)
    _check_current(rows, page_id, region_id, candidate_id, revision)
    _event(
        rows,
        page_id=page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        action=ReviewAction.EXCLUDE,
        reviewer_id=reviewer_id,
        sha=sha,
        note=note,
    )
    rows.execute(
        "delete from source_v2_verified_regions where page_id = %s and region_id = %s",
        (page_id, region_id),
    )
    rows.execute(
        "update source_v2_machine_candidates set state = 'excluded' where id = %s",
        (candidate_id,),
    )


def page_progress(rows: Rows, page_id: UUID) -> dict[str, int]:
    """How much of a page a reviewer has actually decided."""

    result = rows.execute(
        """
        select state, count(1) from source_v2_machine_candidates
        where page_id = %s and is_current group by state
        """,
        (page_id,),
    ).fetchall()
    counts = {"unverified": 0, "verified": 0, "excluded": 0} | dict(result)
    counts["resolved"] = counts["verified"] + counts["excluded"]
    return counts
