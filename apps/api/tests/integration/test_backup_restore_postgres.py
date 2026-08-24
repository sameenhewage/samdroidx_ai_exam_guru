import asyncio
import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.papers.serialization import (
    publication_content_hash,
    reconstruct_published_snapshot,
)
from tests.integration.test_generation_runs_api import (
    ADMIN_HEADERS,
    ADMIN_ID,
    ALLOWED_CHUNK_ID,
    GenerationSeed,
    api_client,
    generation_seed,  # noqa: F401 - imported fixture is discovered by pytest
)
from tests.integration.test_paper_publication_api import (
    create_approved_candidate,
    paper_draft_path,
    papers_path,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
BACKUP_SCRIPT = REPOSITORY_ROOT / "scripts" / "ops" / "backup_postgres.sh"
RESTORE_SCRIPT = REPOSITORY_ROOT / "scripts" / "ops" / "restore_postgres.sh"
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
EMBEDDING_CONFIGURATION_ID = UUID(int=990_001)
EMBEDDING_ID = UUID(int=990_002)
CRITICAL_TABLES = (
    "admin_audit_events",
    "candidate_review_events",
    "curriculum_versions",
    "embedding_configurations",
    "extracted_blocks",
    "generation_attempts",
    "generation_runs",
    "historical_questions",
    "knowledge_chunks",
    "knowledge_embeddings",
    "paper_blueprints",
    "practice_papers",
    "published_paper_versions",
    "question_candidates",
    "source_documents",
    "source_pages",
    "validation_findings",
    "validation_runs",
)


def _postgresql_18_tools() -> Mapping[str, str]:
    tools: dict[str, str] = {}
    for name in ("pg_dump", "pg_restore", "psql"):
        candidates = [
            Path("/usr/lib/postgresql/18/bin") / name,
            Path(shutil.which(name) or ""),
        ]
        executable = next(
            (
                candidate
                for candidate in candidates
                if candidate.is_file() and os.access(candidate, os.X_OK)
            ),
            None,
        )
        if executable is None:
            pytest.skip(
                "backup/restore integration requires installed pg_dump, pg_restore, and "
                "psql client binaries"
            )
        version = subprocess.run(  # noqa: S603 - executable is an installed PG client
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        match = re.search(r"PostgreSQL\) (\d+)", version)
        if match is None or int(match.group(1)) != 18:
            pytest.fail(
                f"{name} must be PostgreSQL 18 to test the PostgreSQL 18 recovery target; "
                f"found {version.strip()}"
            )
        tools[name] = str(executable)
    return tools


def _escape_pgpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _libpq_environment(
    database_url: str,
    password_file: Path,
    tools: Mapping[str, str],
) -> dict[str, str]:
    url = make_url(database_url)
    assert url.host is not None
    assert url.port is not None
    assert url.username is not None
    assert url.database is not None
    assert url.password is not None
    password_file.write_text(
        ":".join(
            _escape_pgpass(value)
            for value in (
                url.host,
                str(url.port),
                url.database,
                url.username,
                url.password,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    password_file.chmod(0o600)
    environment = {
        **os.environ,
        "PGHOST": url.host,
        "PGPORT": str(url.port),
        "PGUSER": url.username,
        "PGDATABASE": url.database,
        "PGPASSFILE": str(password_file),
        "PG_DUMP_BIN": tools["pg_dump"],
        "PG_RESTORE_BIN": tools["pg_restore"],
        "PSQL_BIN": tools["psql"],
    }
    environment.pop("PGPASSWORD", None)
    return environment


def _run_script(
    script: Path,
    *arguments: str,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script and test arguments
        ["/bin/bash", str(script), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


async def _seed_embedding(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            chunk = await session.get(KnowledgeChunkModel, ALLOWED_CHUNK_ID)
            assert chunk is not None
            session.add(
                EmbeddingConfigurationModel(
                    id=EMBEDDING_CONFIGURATION_ID,
                    provider="recovery-fixture",
                    model="deterministic-3d",
                    dimension=3,
                    version="v1",
                    config_fingerprint="sha256:" + "a" * 64,
                    created_by=ADMIN_ID,
                    updated_by=ADMIN_ID,
                )
            )
            await session.flush()
            session.add(
                KnowledgeEmbeddingModel(
                    id=EMBEDDING_ID,
                    historical_question_id=None,
                    knowledge_chunk_id=chunk.id,
                    embedding_configuration_id=EMBEDDING_CONFIGURATION_ID,
                    embedding_dimension=3,
                    source_text_sha256=hashlib.sha256(chunk.text.encode()).hexdigest(),
                    embedding=[0.25, 0.5, 0.75],
                    created_by=ADMIN_ID,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _table_counts(database_url: str) -> dict[str, int]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            counts: dict[str, int] = {}
            for table_name in CRITICAL_TABLES:
                counts[table_name] = int(
                    await connection.scalar(
                        text(f'SELECT count(*) FROM "{table_name}"')  # noqa: S608
                    )
                    or 0
                )
            return counts
    finally:
        await engine.dispose()


async def _user_relation_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return int(
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_class AS relation "
                        "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
                        "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
                        "AND namespace.nspname !~ '^pg_toast' "
                        "AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                    )
                )
                or 0
            )
    finally:
        await engine.dispose()


async def _restored_evidence(database_url: str) -> dict[str, object]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            server_version = int(await connection.scalar(text("SHOW server_version_num")) or 0)
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            migration_revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            source_rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT object_key, checksum_sha256, size_bytes "
                            "FROM source_documents ORDER BY object_key"
                        )
                    )
                )
                .mappings()
                .all()
            )
            embedding_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT embedding_dimension, source_text_sha256, "
                            "vector_dims(embedding) AS actual_dimension "
                            "FROM knowledge_embeddings WHERE id = :embedding_id"
                        ),
                        {"embedding_id": EMBEDDING_ID},
                    )
                )
                .mappings()
                .one()
            )
            publication_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT paper_id, version, previous_version, supersedes_content_hash, "
                            "snapshot, content_hash, published_by, "
                            "snapshot = paper_expected_publication_snapshot("
                            "paper_id, curriculum_version_id, version) AS authoritative_snapshot, "
                            "content_hash = encode(sha256(convert_to("
                            "paper_canonical_jsonb(snapshot), 'UTF8')), 'hex') AS sql_hash_matches "
                            "FROM published_paper_versions ORDER BY paper_id, version LIMIT 1"
                        )
                    )
                )
                .mappings()
                .one()
            )
            trigger_names = set(
                await connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE NOT tgisinternal AND tgrelid IN "
                        "('admin_audit_events'::regclass, "
                        "'published_paper_versions'::regclass)"
                    )
                )
            )
    finally:
        await engine.dispose()

    snapshot = publication_row["snapshot"]
    content_hash = cast(str, publication_row["content_hash"])
    reconstructed = reconstruct_published_snapshot(
        snapshot,
        content_hash=content_hash,
        published_by=UUID(str(publication_row["published_by"])),
        previous_version=cast(int | None, publication_row["previous_version"]),
        supersedes_content_hash=cast(str | None, publication_row["supersedes_content_hash"]),
    )
    return {
        "server_version": server_version,
        "vector_version": vector_version,
        "migration_revision": migration_revision,
        "source_rows": source_rows,
        "embedding_row": embedding_row,
        "publication_hash": content_hash,
        "python_hash": publication_content_hash(snapshot),
        "reconstructed_hash": reconstructed.content_hash,
        "authoritative_snapshot": publication_row["authoritative_snapshot"],
        "sql_hash_matches": publication_row["sql_hash_matches"],
        "trigger_names": trigger_names,
    }


async def _assert_append_only_mutations_are_rejected(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "UPDATE admin_audit_events SET action = 'tampered' "
                        "WHERE id = (SELECT id FROM admin_audit_events LIMIT 1)"
                    )
                )
            await transaction.rollback()
        async with engine.connect() as connection:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "DELETE FROM published_paper_versions WHERE (paper_id, version) = "
                        "(SELECT paper_id, version FROM published_paper_versions LIMIT 1)"
                    )
                )
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.backup_restore
def test_custom_backup_restores_critical_evidence_into_an_empty_postgresql_18_target(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    seed = cast(GenerationSeed, request.getfixturevalue("generation_seed"))
    tools = _postgresql_18_tools()
    asyncio.run(_seed_embedding(seed.database_url))
    candidate_id, dispatcher, runtime = create_approved_candidate(
        seed,
        key="backup-restore-candidate",
        stem="Six mangoes are joined by seven oranges; which option gives the total?",
    )
    create_body = {
        "paper_blueprint_id": str(seed.paper_blueprint_id),
        "title": "Recovery Evidence Practice Paper",
        "candidate_ids": [str(candidate_id)],
    }
    with api_client(seed, dispatcher, runtime=runtime) as client:
        created = client.post(
            paper_draft_path(),
            json=create_body,
            headers={**ADMIN_HEADERS, "Idempotency-Key": "backup-restore-paper"},
        )
        assert created.status_code == 201, created.text
        paper_id = created.json()["paper_id"]
        published = client.post(
            f"{papers_path()}/{paper_id}/publish",
            json={"expected_version": 1},
            headers=ADMIN_HEADERS,
        )
        assert published.status_code == 200, published.text
        expected_publication_hash = published.json()["content_hash"]

    source_counts = asyncio.run(_table_counts(seed.database_url))
    assert all(source_counts[table_name] > 0 for table_name in CRITICAL_TABLES)
    backup_directory = tmp_path / "postgres-backup"
    source_environment = _libpq_environment(
        seed.database_url,
        tmp_path / "source.pgpass",
        tools,
    )
    backup = _run_script(
        BACKUP_SCRIPT,
        "--destination",
        str(backup_directory),
        environment=source_environment,
    )
    assert backup.returncode == 0, backup.stderr
    assert (backup_directory / "database.dump").read_bytes().startswith(b"PGDMP")

    target_credentials = ("exam_guru_restore", "restore-integration-only")
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username=target_credentials[0],
        password=target_credentials[1],  # pragma: allowlist secret
        dbname="exam_guru_restore_test",
        driver="asyncpg",
    ) as target:
        target_url = target.get_connection_url()
        target_environment = _libpq_environment(
            target_url,
            tmp_path / "target.pgpass",
            tools,
        )
        dry_run = _run_script(
            RESTORE_SCRIPT,
            "--backup-dir",
            str(backup_directory),
            environment=target_environment,
        )
        assert dry_run.returncode == 0, dry_run.stderr
        assert asyncio.run(_user_relation_count(target_url)) == 0

        restore = _run_script(
            RESTORE_SCRIPT,
            "--backup-dir",
            str(backup_directory),
            "--execute",
            "--confirm-empty-target",
            "RESTORE:exam_guru_restore_test",
            environment=target_environment,
        )
        assert restore.returncode == 0, restore.stderr

        assert_database_schema_current(target_url)
        expected_head = ScriptDirectory.from_config(
            _config_for_database(target_url)
        ).get_current_head()
        evidence = asyncio.run(_restored_evidence(target_url))
        restored_counts = asyncio.run(_table_counts(target_url))
        asyncio.run(_assert_append_only_mutations_are_rejected(target_url))

    assert restored_counts == source_counts
    assert cast(int, evidence["server_version"]) >= 180_000
    assert evidence["vector_version"] == "0.8.6"
    assert evidence["migration_revision"] == expected_head
    source_rows = cast(list[Mapping[str, object]], evidence["source_rows"])
    assert source_rows
    assert all(
        len(cast(str, row["checksum_sha256"])) == 64
        and int(cast(int, row["size_bytes"])) > 0
        and cast(str, row["object_key"]).startswith("sources/")
        for row in source_rows
    )
    embedding_row = cast(Mapping[str, object], evidence["embedding_row"])
    assert embedding_row["embedding_dimension"] == embedding_row["actual_dimension"] == 3
    assert len(cast(str, embedding_row["source_text_sha256"])) == 64
    assert evidence["authoritative_snapshot"] is True
    assert evidence["sql_hash_matches"] is True
    assert evidence["publication_hash"] == expected_publication_hash
    assert evidence["python_hash"] == expected_publication_hash
    assert evidence["reconstructed_hash"] == expected_publication_hash
    assert {
        "trg_admin_audit_events_append_only",
        "reject_published_paper_version_mutation_trigger",
    } <= cast(set[str], evidence["trigger_names"])
