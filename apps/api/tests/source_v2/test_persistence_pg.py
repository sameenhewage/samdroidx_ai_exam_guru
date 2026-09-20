"""Source V2 database guarantees, proved against a real PostgreSQL.

These assert that the *database* refuses the dangerous states, not that the
application remembers to. Skipped when no database is reachable; never
weakened into a fake, because a fake cannot prove a trigger fires.

    $env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=..."
    uv run pytest tests/source_v2/test_persistence_pg.py -q
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
SHA = "a" * 64
OTHER_SHA = "b" * 64


@pytest.fixture
def connection():
    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the Source V2 persistence tests")
    with psycopg.connect(DSN, connect_timeout=10) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def page(connection):
    """A Source V2 page bound to whichever real document is available."""

    document = connection.execute("select id from source_documents limit 1").fetchone()
    if document is None:
        pytest.skip("no source_documents row to attach a Source V2 page to")
    page_id = uuid.uuid4()
    connection.execute(
        """
        insert into source_v2_pages
          (id, document_id, page_number, image_sha256, dpi, width, height,
           language, detector_version, layout)
        values (%s, %s, %s, %s, 300, 2480, 3509, 'sinhala', 'test', '{}'::jsonb)
        """,
        (page_id, document[0], 900_000 + uuid.uuid4().int % 90_000, SHA),
    )
    return page_id


def add_candidate(connection, page_id, region_id="p156-r002", revision=1, current=True, text="පෙළ"):
    candidate_id = uuid.uuid4()
    connection.execute(
        """
        insert into source_v2_machine_candidates
          (id, page_id, region_id, region_type, revision, origin, text, reason,
           state, is_current)
        values (%s, %s, %s, 'text', %s, 'machine', %s, 'test', 'unverified', %s)
        """,
        (candidate_id, page_id, region_id, revision, text, current),
    )
    return candidate_id


def confirm_event(connection, page_id, region_id, candidate_id, revision, sha=SHA):
    connection.execute(
        """
        insert into source_v2_review_events
          (id, page_id, region_id, candidate_id, candidate_revision, action,
           reviewer_id, compared_with_image_sha256)
        values (%s, %s, %s, %s, %s, 'confirm', %s, %s)
        """,
        (uuid.uuid4(), page_id, region_id, candidate_id, revision, uuid.uuid4(), sha),
    )


def test_only_one_candidate_per_region_can_be_current(connection, page) -> None:
    add_candidate(connection, page, revision=1, current=True)
    # A savepoint, not a rollback: the page fixture must survive the refusal.
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.UniqueViolation):
        add_candidate(connection, page, revision=2, current=True)


def test_a_superseded_revision_may_coexist_when_not_current(connection, page) -> None:
    add_candidate(connection, page, revision=1, current=False)
    add_candidate(connection, page, revision=2, current=True)
    rows = connection.execute(
        "select count(1) from source_v2_machine_candidates where page_id = %s", (page,)
    ).fetchone()
    assert rows[0] == 2


def test_review_events_cannot_be_rewritten_or_deleted(connection, page) -> None:
    candidate = add_candidate(connection, page)
    confirm_event(connection, page, "p156-r002", candidate, 1)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.RaiseException):
        connection.execute("update source_v2_review_events set note = 'tampered'")
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.RaiseException):
        connection.execute("delete from source_v2_review_events")


def test_an_exclusion_without_a_reason_is_refused(connection, page) -> None:
    candidate = add_candidate(connection, page)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            """
                insert into source_v2_review_events
                  (id, page_id, region_id, candidate_id, candidate_revision, action,
                   reviewer_id, compared_with_image_sha256, note)
                values (%s, %s, 'p156-r002', %s, 1, 'exclude', %s, %s, '   ')
                """,
            (uuid.uuid4(), page, candidate, uuid.uuid4(), SHA),
        )


def insert_verified(connection, page_id, region_id, candidate_id, revision, sha=SHA, text="පෙළ"):
    connection.execute(
        """
        insert into source_v2_verified_regions
          (id, page_id, region_id, candidate_id, candidate_revision, text, text_nfc,
           reviewer_id, image_sha256)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (uuid.uuid4(), page_id, region_id, candidate_id, revision, text, text, uuid.uuid4(), sha),
    )


def test_verified_content_requires_a_matching_confirm_event(connection, page) -> None:
    candidate = add_candidate(connection, page)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.RaiseException):
        insert_verified(connection, page, "p156-r002", candidate, 1)


def test_verified_content_must_cite_the_current_page_image(connection, page) -> None:
    candidate = add_candidate(connection, page)
    confirm_event(connection, page, "p156-r002", candidate, 1, sha=OTHER_SHA)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.RaiseException):
        insert_verified(connection, page, "p156-r002", candidate, 1, sha=OTHER_SHA)


def test_verified_content_is_accepted_once_it_is_properly_witnessed(connection, page) -> None:
    candidate = add_candidate(connection, page)
    confirm_event(connection, page, "p156-r002", candidate, 1)
    insert_verified(connection, page, "p156-r002", candidate, 1)
    row = connection.execute(
        "select text from source_v2_verified_regions where page_id = %s", (page,)
    ).fetchone()
    assert row[0] == "පෙළ"


def test_empty_verified_text_is_refused(connection, page) -> None:
    candidate = add_candidate(connection, page)
    confirm_event(connection, page, "p156-r002", candidate, 1)
    with connection.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        insert_verified(connection, page, "p156-r002", candidate, 1, text="   ")


def test_a_review_event_cannot_cite_another_regions_candidate(connection, page) -> None:
    candidate = add_candidate(connection, page, region_id="p156-r002")
    violation = psycopg.errors.ForeignKeyViolation
    with connection.transaction(force_rollback=True), pytest.raises(violation):
        confirm_event(connection, page, "p156-r009", candidate, 1)
