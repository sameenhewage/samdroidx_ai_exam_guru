"""Migration 0061 upgrades and reverses cleanly on a scratch database.

A throwaway database is created beside whatever `EXAM_GURU_TEST_DSN` points
at, so nothing here touches the Studio's own data. The reversal is exercised
because forward-only (D8) is about never *rewriting history*, not about
shipping a migration that cannot be undone on a scratch box.

The constraint tests below matter more than they look. `visual_description`
and `detected_labels` are the columns that keep a machine's description of a
picture out of `text`; if the database will accept a blank description or a
bare string where a list belongs, the review screen ends up showing an empty
"machine-generated" section that reads as answered.

    $env:EXAM_GURU_TEST_DSN="host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru password=..."
    uv run pytest tests/source_v2/test_migration_0061_pg.py -q
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text

from exam_guru_api.infrastructure.migrations import _config_for_database

pytest.importorskip("asyncpg")
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

DSN = os.environ.get("EXAM_GURU_TEST_DSN")
PREVIOUS = "0060_source_v2_single_reader"
TARGET = "0061_source_v2_description"
TABLES = ("source_v2_machine_candidates", "source_v2_verified_regions")
NEW_COLUMNS = {"visual_description", "detected_labels"}


def _url(dbname: str) -> str:
    parts = dict(item.split("=", 1) for item in (DSN or "").split() if "=" in item)
    return (
        f"postgresql+asyncpg://{parts['user']}:{parts['password']}"
        f"@{parts['host']}:{parts.get('port', '5432')}/{dbname}"
    )


def _run(dbname: str, work: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        engine = create_async_engine(_url(dbname))
        try:
            async with engine.begin() as connection:
                return await work(connection)
        finally:
            await engine.dispose()

    return asyncio.run(main())


def _administer(statement: str) -> None:
    async def main() -> None:
        parts = dict(item.split("=", 1) for item in (DSN or "").split() if "=" in item)
        engine = create_async_engine(_url(parts["dbname"]), isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(main())


@pytest.fixture
def scratch_database():
    """A disposable database. Dropped whatever the test does."""

    if not DSN:
        pytest.skip("set EXAM_GURU_TEST_DSN to run the 0061 migration test")
    name = f"source_v2_0061_{uuid.uuid4().hex[:12]}"
    try:
        _administer(f'create database "{name}"')
    except DBAPIError as error:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot create a scratch database here: {error}")
    try:
        yield name
    finally:
        _administer(f'drop database if exists "{name}" with (force)')


def _columns(dbname: str, table: str) -> set[str]:
    async def work(connection: AsyncConnection) -> set[str]:
        rows = await connection.execute(
            text("select column_name from information_schema.columns where table_name = :table"),
            {"table": table},
        )
        return {row[0] for row in rows}

    return _run(dbname, work)


def _checks(dbname: str, table: str) -> set[str]:
    async def work(connection: AsyncConnection) -> set[str]:
        rows = await connection.execute(
            text(
                "select conname from pg_constraint"
                " where conrelid = cast(:table as regclass) and contype = 'c'"
            ),
            {"table": table},
        )
        return {row[0] for row in rows}

    return _run(dbname, work)


async def _seed_region(connection: AsyncConnection) -> uuid.UUID:
    """A page and one figure region, on a schema that predates 0061."""

    document = uuid.uuid4()
    actor = uuid.uuid4()
    await connection.execute(
        text(
            "insert into source_documents (id, checksum_sha256, object_key,"
            " original_filename, content_type, size_bytes, document_type,"
            " created_by, updated_by)"
            " values (:id, :sha, :key, 'scratch.pdf', 'application/pdf', 1,"
            " 'teacher_guide', :actor, :actor)"
        ),
        {"id": document, "key": f"scratch/{document}", "sha": "0" * 64, "actor": actor},
    )
    page_id = uuid.uuid4()
    await connection.execute(
        text(
            "insert into source_v2_pages (id, document_id, page_number, image_sha256, dpi,"
            " width, height, language, detector_version, layout)"
            " values (:id, :doc, 1, :sha, 300, 2480, 3509, 'sinhala', 'test', '{}'::jsonb)"
        ),
        {"id": page_id, "doc": document, "sha": "a" * 64},
    )
    await connection.execute(
        text(
            "insert into source_v2_machine_candidates (id, page_id, region_id, region_type,"
            " revision, origin, text, reason, state, is_current)"
            " values (:id, :page, 'r-fig', 'figure', 1, 'machine', '', 'pre-0061',"
            " 'unverified', true)"
        ),
        {"id": uuid.uuid4(), "page": page_id},
    )
    return page_id


def test_0061_adds_the_two_new_concepts_and_reverses_cleanly(scratch_database: str) -> None:
    config = _config_for_database(_url(scratch_database))

    command.upgrade(config, PREVIOUS)
    for table in TABLES:
        assert NEW_COLUMNS.isdisjoint(_columns(scratch_database, table))

    command.upgrade(config, TARGET)
    for table in TABLES:
        # `text` keeps its own meaning. The description sits beside it, never
        # inside it, which is the entire point of the migration.
        assert {"text", *NEW_COLUMNS} <= _columns(scratch_database, table)
        assert {
            f"ck_{table}_description_not_blank",
            f"ck_{table}_detected_labels_array",
        } <= _checks(scratch_database, table)

    command.downgrade(config, PREVIOUS)
    for table in TABLES:
        assert NEW_COLUMNS.isdisjoint(_columns(scratch_database, table))
        assert "text" in _columns(scratch_database, table)

    command.upgrade(config, "head")
    for table in TABLES:
        assert _columns(scratch_database, table) >= NEW_COLUMNS


def test_a_pre_0061_row_keeps_its_text_and_gains_an_empty_label_list(
    scratch_database: str,
) -> None:
    """Existing rows assert nothing about descriptions, and say so honestly."""

    config = _config_for_database(_url(scratch_database))
    command.upgrade(config, PREVIOUS)
    page_id = _run(scratch_database, _seed_region)

    command.upgrade(config, TARGET)

    async def read(connection: AsyncConnection) -> tuple:
        row = await connection.execute(
            text(
                "select visual_description, detected_labels, text, reason"
                " from source_v2_machine_candidates where page_id = :page"
            ),
            {"page": page_id},
        )
        return tuple(row.one())

    assert _run(scratch_database, read) == (None, [], "", "pre-0061")


@pytest.mark.parametrize(
    ("description", "labels"),
    [
        ("   ", "[]"),
        ("", "[]"),
        (None, '"a-bare-string"'),
        (None, "{}"),
        (None, "null"),
    ],
)
def test_the_database_refuses_a_blank_description_and_a_non_list_of_labels(
    scratch_database: str, description: str | None, labels: str
) -> None:
    command.upgrade(_config_for_database(_url(scratch_database)), TARGET)
    page_id = _run(scratch_database, _seed_region)

    async def insert(connection: AsyncConnection) -> None:
        await connection.execute(
            text(
                "insert into source_v2_machine_candidates (id, page_id, region_id,"
                " region_type, revision, origin, text, reason, state, is_current,"
                " visual_description, detected_labels)"
                " values (:id, :page, :region, 'figure', 1, 'machine', '', 'test',"
                " 'unverified', true, :description, cast(:labels as jsonb))"
            ),
            {
                "id": uuid.uuid4(),
                "page": page_id,
                "region": f"r-{uuid.uuid4().hex[:6]}",
                "description": description,
                "labels": labels,
            },
        )

    with pytest.raises(IntegrityError):
        _run(scratch_database, insert)


def test_a_real_description_and_label_list_are_accepted(scratch_database: str) -> None:
    command.upgrade(_config_for_database(_url(scratch_database)), TARGET)
    page_id = _run(scratch_database, _seed_region)

    async def write_and_read(connection: AsyncConnection) -> tuple:
        await connection.execute(
            text(
                "update source_v2_machine_candidates"
                " set visual_description = :description,"
                "     detected_labels = cast(:labels as jsonb)"
                " where page_id = :page"
            ),
            {
                "page": page_id,
                "description": "වළලු චුම්බකයක් දැක්වේ.",
                "labels": '["චුම්බකය"]',
            },
        )
        row = await connection.execute(
            text(
                "select visual_description, detected_labels, text"
                " from source_v2_machine_candidates where page_id = :page"
            ),
            {"page": page_id},
        )
        return tuple(row.one())

    assert _run(scratch_database, write_and_read) == (
        "වළලු චුම්බකයක් දැක්වේ.",
        ["චුම්බකය"],
        "",
    )
