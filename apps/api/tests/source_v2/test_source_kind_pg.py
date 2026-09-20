"""D18 guarantees enforced by PostgreSQL, not by call-site discipline.

Skipped when no database is reachable; never replaced by a fake, because a
fake cannot prove a CHECK constraint fires.
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
SHA = "a" * 64


@pytest.fixture
def connection():
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the D18 persistence tests")
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
        values (%s, %s, %s, %s, 300, 2480, 3509, 'sinhala', 'test', '{}'::jsonb)
        """,
        (page_id, document[0], 700_000 + uuid.uuid4().int % 90_000, SHA),
    )
    return page_id


def candidate(connection, page_id, region_id="p186-r002", kind="visual_only", text=""):
    cid = uuid.uuid4()
    connection.execute(
        """
        insert into source_v2_machine_candidates
          (id, page_id, region_id, region_type, revision, origin, text, reason,
           state, is_current, source_kind, proposed_source_kind)
        values (%s, %s, %s, 'figure', 1, 'machine', %s, 'test', 'unverified', true, %s, %s)
        """,
        (cid, page_id, region_id, text, kind, kind),
    )
    return cid


def confirm(connection, page_id, region_id, cid):
    connection.execute(
        """
        insert into source_v2_review_events
          (id, page_id, region_id, candidate_id, candidate_revision, action,
           reviewer_id, compared_with_image_sha256)
        values (%s, %s, %s, %s, 1, 'confirm', %s, %s)
        """,
        (uuid.uuid4(), page_id, region_id, cid, uuid.uuid4(), SHA),
    )


def verify(connection, page_id, region_id, cid, *, kind, text, crop=SHA, bbox="[1,2,3,4]"):
    connection.execute(
        """
        insert into source_v2_verified_regions
          (id, page_id, region_id, candidate_id, candidate_revision, text, text_nfc,
           reviewer_id, image_sha256, source_kind, crop_sha256, bbox)
        values (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid.uuid4(),
            page_id,
            region_id,
            cid,
            text,
            text,
            uuid.uuid4(),
            SHA,
            kind,
            crop,
            bbox,
        ),
    )


def test_a_visual_only_region_becomes_verified_with_no_text_at_all(connection, page) -> None:
    """The p186-r002 case: an educational drawing is source content."""

    cid = candidate(connection, page)
    confirm(connection, page, "p186-r002", cid)
    verify(connection, page, "p186-r002", cid, kind="visual_only", text="")
    row = connection.execute(
        "select source_kind, text, crop_sha256 from source_v2_verified_regions where page_id = %s",
        (page,),
    ).fetchone()
    assert row[0] == "visual_only"
    assert row[1] == ""
    assert row[2] == SHA


def test_a_visual_region_without_its_canonical_crop_is_refused(connection, page) -> None:
    cid = candidate(connection, page)
    confirm(connection, page, "p186-r002", cid)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        verify(connection, page, "p186-r002", cid, kind="visual_only", text="", crop=None)


def test_a_text_only_region_still_requires_text(connection, page) -> None:
    cid = candidate(connection, page, kind="text_only", text="x")
    confirm(connection, page, "p186-r002", cid)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        verify(connection, page, "p186-r002", cid, kind="text_only", text="   ")


def test_a_visual_with_text_region_requires_text(connection, page) -> None:
    cid = candidate(connection, page, kind="visual_with_text", text="කෝටුව")
    confirm(connection, page, "p186-r002", cid)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        verify(connection, page, "p186-r002", cid, kind="visual_with_text", text="")


def test_decorative_can_never_become_verified_source_content(connection, page) -> None:
    cid = candidate(connection, page, kind="decorative", text="header")
    confirm(connection, page, "p186-r002", cid)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        verify(connection, page, "p186-r002", cid, kind="decorative", text="header")


def test_an_unknown_source_kind_is_rejected(connection, page) -> None:
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        candidate(connection, page, kind="photograph")


def test_legacy_rows_default_to_undecided_not_to_an_invented_kind(connection, page) -> None:
    """Pre-D18 rows were produced by a text-only pipeline; calling them
    'text_only' would assert an educational judgement nobody made."""

    default = connection.execute(
        "select column_default from information_schema.columns"
        " where table_name = 'source_v2_verified_regions' and column_name = 'source_kind'"
    ).fetchone()[0]
    assert "undecided" in default
