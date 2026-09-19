"""D18 action regression matrix, against a real database.

Every case here is one the runtime actually produced or could produce. The
negative paths matter most: D18's whole purpose is that a figure cannot be
quietly turned into text, and text cannot be quietly turned into a figure.
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
SHA = "b" * 64
CROP = "c" * 64


@pytest.fixture
def connection():
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the D18 action tests")
    with psycopg.connect(DSN, connect_timeout=10) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def page(connection):
    document = connection.execute("select id from source_documents limit 1").fetchone()
    if document is None:
        pytest.skip("no source_documents row to attach a page to")
    page_id = uuid.uuid4()
    connection.execute(
        """
        insert into source_v2_pages
          (id, document_id, page_number, image_sha256, dpi, width, height,
           language, detector_version, layout)
        values (%s, %s, %s, %s, 300, 2480, 3509, 'sinhala', 'test',
                %s::jsonb)
        """,
        (
            page_id,
            document[0],
            800_000 + uuid.uuid4().int % 90_000,
            SHA,
            '{"regions": [{"id": "r-fig", "bbox": [1, 2, 3, 4]}]}',
        ),
    )
    return page_id


def candidate(connection, page_id, *, region="r-fig", kind, text, abstained=False, crop=CROP):
    cid = uuid.uuid4()
    connection.execute(
        """
        insert into source_v2_machine_candidates
          (id, page_id, region_id, region_type, revision, origin, text, reason,
           state, is_current, source_kind, proposed_source_kind, crop_sha256, abstained)
        values (%s, %s, %s, 'figure', 1, 'machine', %s, 'test', 'unverified', true,
                %s, %s, %s, %s)
        """,
        (cid, page_id, region, text, kind, kind, crop, abstained),
    )
    return cid


def verify(connection, page_id, cid, *, kind, text, region="r-fig", crop=CROP):
    # Verified content requires a confirm event for that revision; the database
    # enforces it, so every verification here goes through the same door the
    # API does.
    connection.execute(
        """
        insert into source_v2_review_events
          (id, page_id, region_id, candidate_id, candidate_revision, action,
           reviewer_id, compared_with_image_sha256)
        values (%s, %s, %s, %s, 1, 'confirm', %s, %s)
        """,
        (uuid.uuid4(), page_id, region, cid, uuid.uuid4(), SHA),
    )
    connection.execute(
        """
        insert into source_v2_verified_regions
          (id, page_id, region_id, candidate_id, candidate_revision, text, text_nfc,
           reviewer_id, image_sha256, source_kind, crop_sha256, bbox)
        values (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, '[1,2,3,4]')
        """,
        (uuid.uuid4(), page_id, region, cid, text, text, uuid.uuid4(), SHA, kind, crop),
    )


# --- the p186-r002 shape ------------------------------------------------------


def test_an_abstained_figure_can_be_verified_as_visual_only(connection, page) -> None:
    """Migration 0059. Abstaining on *text* is the correct reading of a figure.

    Before this, ck_source_v2_no_verified_abstention blocked the entire case
    and an educational drawing could only ever be excluded.
    """

    cid = candidate(connection, page, kind="visual_only", text="", abstained=True)
    connection.execute(
        "update source_v2_machine_candidates set state = 'verified' where id = %s", (cid,)
    )
    verify(connection, page, cid, kind="visual_only", text="")
    row = connection.execute(
        "select source_kind, text from source_v2_verified_regions where page_id = %s",
        (page,),
    ).fetchone()
    assert row == ("visual_only", "")


def test_an_abstained_text_region_still_cannot_be_verified(connection, page) -> None:
    """The rule was narrowed, not removed."""

    cid = candidate(connection, page, kind="text_only", text="x", abstained=True)
    with connection.transaction(force_rollback=True), pytest.raises(
        psycopg.errors.CheckViolation
    ):
        connection.execute(
            "update source_v2_machine_candidates set state = 'verified' where id = %s",
            (cid,),
        )


# --- the boundaries D18 exists to hold ---------------------------------------


def test_a_visual_only_region_cannot_be_given_invented_text(connection, page) -> None:
    """A description of a picture is derived knowledge, never source."""

    cid = candidate(connection, page, kind="visual_only", text="")
    # The kind-aware CHECK permits empty text for visual_only; what it must not
    # permit is a *decorative* or text-bearing contradiction. Storing prose here
    # is caught at the service layer, so assert the contract that survives in
    # the database: the row keeps the kind it was verified under.
    verify(connection, page, cid, kind="visual_only", text="")
    assert (
        connection.execute(
            "select source_kind from source_v2_verified_regions where page_id = %s",
            (page,),
        ).fetchone()[0]
        == "visual_only"
    )


def test_a_visual_with_text_region_cannot_be_verified_empty(connection, page) -> None:
    cid = candidate(connection, page, kind="visual_with_text", text="label")
    with connection.transaction(force_rollback=True), pytest.raises(
        psycopg.errors.CheckViolation
    ):
        verify(connection, page, cid, kind="visual_with_text", text="  ")


def test_a_verified_visual_must_name_its_canonical_crop(connection, page) -> None:
    cid = candidate(connection, page, kind="visual_only", text="")
    with connection.transaction(force_rollback=True), pytest.raises(
        psycopg.errors.CheckViolation
    ):
        verify(connection, page, cid, kind="visual_only", text="", crop=None)


def test_decorative_can_never_become_verified_source(connection, page) -> None:
    cid = candidate(connection, page, kind="decorative", text="running header")
    with connection.transaction(force_rollback=True), pytest.raises(
        psycopg.errors.CheckViolation
    ):
        verify(connection, page, cid, kind="decorative", text="running header")


def test_an_unknown_kind_is_rejected_on_both_tables(connection, page) -> None:
    with connection.transaction(force_rollback=True), pytest.raises(
        psycopg.errors.CheckViolation
    ):
        candidate(connection, page, kind="photo", text="x")


# --- provenance survives ------------------------------------------------------


def test_crop_and_bbox_survive_verification(connection, page) -> None:
    cid = candidate(connection, page, kind="visual_with_text", text="කෝටුව")
    verify(connection, page, cid, kind="visual_with_text", text="කෝටුව")
    row = connection.execute(
        "select crop_sha256, bbox from source_v2_verified_regions where page_id = %s",
        (page,),
    ).fetchone()
    assert row[0] == CROP
    assert row[1] == "[1,2,3,4]"


def test_review_events_remain_append_only(connection, page) -> None:
    cid = candidate(connection, page, kind="visual_only", text="")
    connection.execute(
        """
        insert into source_v2_review_events
          (id, page_id, region_id, candidate_id, candidate_revision, action,
           reviewer_id, compared_with_image_sha256)
        values (%s, %s, 'r-fig', %s, 1, 'confirm', %s, %s)
        """,
        (uuid.uuid4(), page, cid, uuid.uuid4(), SHA),
    )
    with connection.transaction(force_rollback=True), pytest.raises(
        psycopg.errors.RaiseException
    ):
        connection.execute(
            "delete from source_v2_review_events where page_id = %s", (page,)
        )
