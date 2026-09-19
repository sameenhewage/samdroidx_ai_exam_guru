"""Import real Machine Candidates and drive the human gate against PostgreSQL.

Uses the actual candidate JSON produced offline for Grade 5 Sinhala page 156
when it is present on this machine, and falls back to an equivalent inline
payload otherwise, so the test proves the wiring everywhere but prefers real
source shape where it exists.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from exam_guru_api.source_v2.domain import SourceV2Error, StaleReviewError
from exam_guru_api.source_v2.service import (
    confirm,
    correct,
    exclude,
    import_page,
    page_progress,
)

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
STUDIO = (
    Path(__file__).resolve().parents[4]
    / ".exam-guru-data"
    / "source-content"
    / "grade-05"
    / "sinhala"
    / "mawbasa-teacher-guide"
)
SHA = "c" * 64


def fallback_layout() -> dict:
    return {
        "schema_version": "1.0.0",
        "document_id": "mawbasa-teacher-guide",
        "page_number": 156,
        "width": 2480,
        "height": 3509,
        "dpi": 300.0,
        "image_sha256": SHA,
        "detector_version": "source-v2-layout-test",
        "regions": [],
    }


def fallback_candidates() -> list[dict]:
    return [
        {
            "region_id": "p156-r001",
            "region_type": "heading",
            "text": "ක්‍රියාකාරකම 11",
            "abstained": False,
            "chosen_reader": "sinhala-deepseek",
            "reason": "selected by measured rank",
            "critical_conflict": False,
            "agreement_ratio": 1.0,
            "disagreement": {},
        },
        {
            "region_id": "p156-r002",
            "region_type": "text",
            "text": "පාසල් වත්තේ හෝ ආසන්න පරිසරයේ හෝ",
            "abstained": False,
            "chosen_reader": "sinhala-deepseek",
            "reason": "selected by measured rank; 45 token conflicts",
            "critical_conflict": True,
            "agreement_ratio": 0.6,
            "disagreement": {"readers": ["sinhala-deepseek", "sinhala-lightonocr"]},
        },
        {
            "region_id": "p156-r003",
            "region_type": "decorative",
            "text": "",
            "abstained": True,
            "chosen_reader": None,
            "reason": "no witness was trustworthy for this region",
            "critical_conflict": False,
            "agreement_ratio": 0.0,
            "disagreement": {},
        },
    ]


def real_payload() -> tuple[dict, list[dict]] | None:
    layout_path = STUDIO / "layout" / "regions" / "page-156.json"
    candidate_path = STUDIO / "candidates" / "page-156.json"
    if not (layout_path.exists() and candidate_path.exists()):
        return None
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    regions = json.loads(candidate_path.read_text(encoding="utf-8"))["regions"]
    return layout, regions


@pytest.fixture
def connection():
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the Source V2 service tests")
    with psycopg.connect(DSN, connect_timeout=10) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def document(connection):
    row = connection.execute("select id from source_documents limit 1").fetchone()
    if row is None:
        pytest.skip("no source_documents row to attach a Source V2 page to")
    return row[0]


@pytest.fixture
def imported(connection, document):
    payload = real_payload()
    layout, candidates = payload if payload else (fallback_layout(), fallback_candidates())
    layout = dict(layout)
    # A unique page number per run keeps repeated runs independent without
    # touching anything already stored.
    page_number = 900_000 + uuid.uuid4().int % 90_000
    layout["page_number"] = page_number
    return (
        import_page(
            connection,
            document_id=document,
            page_number=page_number,
            language="sinhala",
            layout=layout,
            candidates=candidates,
            reader_results={
                "sinhala-deepseek": [
                    {"region_id": candidates[0]["region_id"], "text": "x", "seconds": 54.1}
                ]
            },
        ),
        candidates,
    )


def first_confirmable(connection, page_id):
    row = connection.execute(
        """
        select region_id, id, revision from source_v2_machine_candidates
        where page_id = %s and is_current and not abstained and btrim(text) <> ''
        order by region_id limit 1
        """,
        (page_id,),
    ).fetchone()
    assert row is not None, "the imported page has nothing confirmable"
    return row


def test_import_stores_regions_and_is_idempotent(connection, document, imported) -> None:
    page, candidates = imported
    assert page.regions == len(candidates)
    assert page.reader_rows == 1
    again = import_page(
        connection,
        document_id=document,
        page_number=page.page_number,
        language="sinhala",
        layout={
            "image_sha256": page.image_sha256,
            "dpi": 300.0,
            "width": 2480,
            "height": 3509,
            "detector_version": "x",
        },
        candidates=candidates,
    )
    assert again.reused
    assert again.page_id == page.page_id


def test_a_different_render_is_refused_rather_than_silently_replacing(
    connection, document, imported
) -> None:
    page, candidates = imported
    with pytest.raises(SourceV2Error):
        import_page(
            connection,
            document_id=document,
            page_number=page.page_number,
            language="sinhala",
            layout={
                "image_sha256": "d" * 64,
                "dpi": 300.0,
                "width": 2480,
                "height": 3509,
                "detector_version": "x",
            },
            candidates=candidates,
        )


def test_confirm_creates_verified_content_and_moves_progress(connection, imported) -> None:
    page, _ = imported
    region_id, candidate_id, revision = first_confirmable(connection, page.page_id)
    before = page_progress(connection, page.page_id)
    confirm(
        connection,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        reviewer_id=uuid.uuid4(),
        compared_with_image_sha256=page.image_sha256,
    )
    after = page_progress(connection, page.page_id)
    assert after["verified"] == before["verified"] + 1
    stored = connection.execute(
        "select text from source_v2_verified_regions where page_id = %s and region_id = %s",
        (page.page_id, region_id),
    ).fetchone()
    assert stored is not None
    assert stored[0].strip()


def test_confirm_must_cite_the_page_that_was_compared(connection, imported) -> None:
    page, _ = imported
    region_id, candidate_id, revision = first_confirmable(connection, page.page_id)
    with pytest.raises(StaleReviewError):
        confirm(
            connection,
            page_id=page.page_id,
            region_id=region_id,
            candidate_id=candidate_id,
            revision=revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256="e" * 64,
        )


def test_correction_withdraws_verification_and_awaits_a_new_confirmation(
    connection, imported
) -> None:
    page, _ = imported
    region_id, candidate_id, revision = first_confirmable(connection, page.page_id)
    confirm(
        connection,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        reviewer_id=uuid.uuid4(),
        compared_with_image_sha256=page.image_sha256,
    )
    child = correct(
        connection,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        reviewer_id=uuid.uuid4(),
        corrected_text="නිවැරදි කළ පෙළ",
    )
    verified = connection.execute(
        "select count(1) from source_v2_verified_regions where page_id = %s and region_id = %s",
        (page.page_id, region_id),
    ).fetchone()
    assert verified[0] == 0, "a correction must withdraw the previous verification"
    current = connection.execute(
        "select id, revision, origin, state from source_v2_machine_candidates "
        "where page_id = %s and region_id = %s and is_current",
        (page.page_id, region_id),
    ).fetchone()
    assert current[0] == child
    assert current[1] == revision + 1
    assert current[2] == "human-correction"
    assert current[3] == "unverified"


def test_the_superseded_candidate_can_no_longer_be_confirmed(connection, imported) -> None:
    page, _ = imported
    region_id, candidate_id, revision = first_confirmable(connection, page.page_id)
    correct(
        connection,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        reviewer_id=uuid.uuid4(),
        corrected_text="සංශෝධිත",
    )
    with pytest.raises(StaleReviewError):
        confirm(
            connection,
            page_id=page.page_id,
            region_id=region_id,
            candidate_id=candidate_id,
            revision=revision,
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=page.image_sha256,
        )


def test_exclusion_requires_a_reason_and_counts_as_resolved(connection, imported) -> None:
    page, _ = imported
    region_id, candidate_id, revision = first_confirmable(connection, page.page_id)
    with pytest.raises(SourceV2Error):
        exclude(
            connection,
            page_id=page.page_id,
            region_id=region_id,
            candidate_id=candidate_id,
            revision=revision,
            reviewer_id=uuid.uuid4(),
            note="  ",
        )
    exclude(
        connection,
        page_id=page.page_id,
        region_id=region_id,
        candidate_id=candidate_id,
        revision=revision,
        reviewer_id=uuid.uuid4(),
        note="running header, not source content",
    )
    assert page_progress(connection, page.page_id)["excluded"] >= 1


def test_an_abstained_region_cannot_be_confirmed(connection, imported) -> None:
    page, _ = imported
    row = connection.execute(
        """
        select region_id, id, revision from source_v2_machine_candidates
        where page_id = %s and is_current and (abstained or btrim(text) = '')
        limit 1
        """,
        (page.page_id,),
    ).fetchone()
    if row is None:
        pytest.skip("this page has no abstained region")
    with pytest.raises(SourceV2Error):
        confirm(
            connection,
            page_id=page.page_id,
            region_id=row[0],
            candidate_id=row[1],
            revision=row[2],
            reviewer_id=uuid.uuid4(),
            compared_with_image_sha256=page.image_sha256,
        )
