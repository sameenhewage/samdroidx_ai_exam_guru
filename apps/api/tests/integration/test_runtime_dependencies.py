import asyncio
import hashlib
import json
from collections.abc import Iterator

import pytest
from alembic import command
from dramatiq import Worker
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
    upgrade_database,
)
from exam_guru_api.main import create_app
from exam_guru_api.worker import create_broker

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
VALKEY_IMAGE = "valkey/valkey:9.1.1-alpine3.24"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="integration-only",
        dbname="exam_guru_test",
        driver="asyncpg",
    ) as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="module")
def valkey_url() -> Iterator[str]:
    with RedisContainer(image=VALKEY_IMAGE) as valkey:
        host = valkey.get_container_host_ip()
        port = valkey.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.mark.integration
def test_clean_database_migration_enables_pgvector(database_url: str) -> None:
    upgrade_database(database_url)
    assert_database_schema_current(database_url)

    async def read_database_state() -> tuple[
        str | None,
        str | None,
        set[str],
        set[str],
        set[str],
        set[str],
        set[str],
    ]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            vector_version = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            migration_revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            metadata_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'historical_questions' "
                        "AND column_name IN ('media_references', 'options', 'answer', "
                        "'marking_guidance', 'marking_data', 'question_archetype', "
                        "'difficulty_label', 'difficulty_confidence', 'difficulty_source')"
                    )
                )
            )
            metadata_constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'historical_questions'::regclass "
                        "AND conname LIKE 'ck_historical_questions_metadata_%'"
                    )
                )
            )
            blueprint_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'paper_blueprints'"
                    )
                )
            )
            blueprint_constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'paper_blueprints'::regclass"
                    )
                )
            )
            blueprint_triggers = set(
                await connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger "
                        "WHERE tgrelid = 'paper_blueprints'::regclass "
                        "AND NOT tgisinternal"
                    )
                )
            )
        await engine.dispose()
        return (
            vector_version,
            migration_revision,
            metadata_columns,
            metadata_constraints,
            blueprint_columns,
            blueprint_constraints,
            blueprint_triggers,
        )

    (
        vector_version,
        migration_revision,
        metadata_columns,
        metadata_constraints,
        blueprint_columns,
        blueprint_constraints,
        blueprint_triggers,
    ) = asyncio.run(read_database_state())

    assert vector_version == "0.8.6"
    assert migration_revision == "0016_published_papers"
    assert blueprint_columns == {
        "id",
        "curriculum_version_id",
        "analytics_run_id",
        "blueprint_id",
        "schema_version",
        "algorithm_version",
        "config_version",
        "seed",
        "total_marks",
        "slot_count",
        "specification_fingerprint",
        "input_fingerprint",
        "result_fingerprint",
        "specification",
        "blueprint",
        "taxonomy_snapshot",
        "created_by",
        "created_at",
    }
    assert {
        "fk_paper_blueprints_curriculum_version",
        "fk_paper_blueprints_analytics_curriculum",
        "uq_paper_blueprints_blueprint_id",
        "uq_paper_blueprints_input_fingerprint",
        "ck_paper_blueprints_specification_shape",
        "ck_paper_blueprints_specification_size",
        "ck_paper_blueprints_blueprint_shape",
        "ck_paper_blueprints_blueprint_size",
        "ck_paper_blueprints_taxonomy_snapshot",
    } <= blueprint_constraints
    assert blueprint_triggers == {"reject_paper_blueprint_mutation_trigger"}
    assert metadata_columns == {
        "media_references",
        "options",
        "answer",
        "marking_guidance",
        "marking_data",
        "question_archetype",
        "difficulty_label",
        "difficulty_confidence",
        "difficulty_source",
    }
    assert {
        "ck_historical_questions_metadata_media_references",
        "ck_historical_questions_metadata_options",
        "ck_historical_questions_metadata_answer",
        "ck_historical_questions_metadata_marking_guidance",
        "ck_historical_questions_metadata_marking_data",
        "ck_historical_questions_metadata_question_archetype",
        "ck_historical_questions_metadata_difficulty_evidence",
        "ck_historical_questions_metadata_difficulty_confidence",
    } <= metadata_constraints


@pytest.mark.integration
def test_review_candidate_migration_is_normalized_deferred_and_has_no_materialized_bank(
    database_url: str,
) -> None:
    upgrade_database(database_url)

    async def inspect() -> tuple[
        set[str],
        set[str],
        set[str],
        set[str],
        dict[str, str],
        int,
    ]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            tables = set(
                await connection.scalars(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name IN "
                        "('question_candidates', 'question_candidate_revisions', "
                        "'candidate_review_events')"
                    )
                )
            )
            candidate_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'question_candidates'"
                    )
                )
            )
            triggers = set(
                await connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                        "tgrelid IN ('question_candidates'::regclass, "
                        "'question_candidate_revisions'::regclass, "
                        "'candidate_review_events'::regclass)"
                    )
                )
            )
            deferred_constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint WHERE contype = 't' "
                        "AND condeferrable AND condeferred AND conrelid IN "
                        "('question_candidates'::regclass, "
                        "'question_candidate_revisions'::regclass, "
                        "'candidate_review_events'::regclass)"
                    )
                )
            )
            bound_constraints = {
                str(row[0]): str(row[1])
                for row in (
                    await connection.execute(
                        text(
                            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname IN ('ck_question_candidates_state_version_revision', "
                            "'ck_question_candidate_revisions_identity', "
                            "'ck_candidate_review_events_bounds')"
                        )
                    )
                ).all()
            }
            materialized_bank_count = int(
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_class WHERE relkind = 'm' "
                        "AND relname LIKE '%question%bank%'"
                    )
                )
                or 0
            )
        await engine.dispose()
        return (
            tables,
            candidate_columns,
            triggers,
            deferred_constraints,
            bound_constraints,
            materialized_bank_count,
        )

    tables, columns, triggers, deferred, bounds, materialized_bank_count = asyncio.run(inspect())
    assert tables == {
        "question_candidates",
        "question_candidate_revisions",
        "candidate_review_events",
    }
    assert columns == {
        "id",
        "curriculum_version_id",
        "generation_run_id",
        "generation_attempt_id",
        "validation_run_id",
        "paper_blueprint_id",
        "blueprint_id",
        "blueprint_version",
        "blueprint_slot_id",
        "state",
        "version",
        "current_revision",
        "generation_lineage",
        "validation_evidence",
        "created_by",
        "created_at",
    }
    assert {
        "enforce_question_candidate_insert_trigger",
        "enforce_question_candidate_update_trigger",
        "reject_question_candidate_delete_trigger",
        "enforce_question_candidate_revision_insert_trigger",
        "reject_question_candidate_revision_mutation_trigger",
        "enforce_candidate_review_event_insert_trigger",
        "reject_candidate_review_event_mutation_trigger",
    } <= triggers
    assert deferred == {
        "enforce_question_candidates_complete_trigger",
        "enforce_question_candidate_revisions_complete_trigger",
        "enforce_candidate_review_events_complete_trigger",
    }
    assert "32" in bounds["ck_question_candidates_state_version_revision"]
    assert "1000" not in bounds["ck_question_candidates_state_version_revision"]
    assert "32" in bounds["ck_question_candidate_revisions_identity"]
    assert "1000" not in bounds["ck_question_candidate_revisions_identity"]
    assert "35" in bounds["ck_candidate_review_events_bounds"]
    assert "32" in bounds["ck_candidate_review_events_bounds"]
    assert "1003" not in bounds["ck_candidate_review_events_bounds"]
    assert materialized_bank_count == 0


@pytest.mark.integration
def test_review_candidate_migration_downgrades_cleanly_and_reapplies(
    database_url: str,
) -> None:
    command.downgrade(_config_for_database(database_url), "0014_validation_runs")

    async def candidate_table_exists() -> bool:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT to_regclass('public.question_candidates') IS NOT NULL")
            )
        await engine.dispose()
        return bool(exists)

    assert not asyncio.run(candidate_table_exists())
    upgrade_database(database_url)
    assert asyncio.run(candidate_table_exists())
    assert_database_schema_current(database_url)


@pytest.mark.integration
def test_published_paper_migration_is_normalized_bounded_deferred_and_hash_compatible(
    database_url: str,
) -> None:
    upgrade_database(database_url)
    unicode_payload = {
        "blueprint": {"slot_ids": ["ප්‍රශ්නය-1"], "title": "ශිෂ්‍යත්ව පුහුණුව"},
        "marks": 7,
        "published": True,
    }
    canonical = json.dumps(
        unicode_payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected_digest = hashlib.sha256(canonical).hexdigest()

    async def inspect() -> tuple[
        set[str],
        dict[str, set[str]],
        set[str],
        set[str],
        int,
        str,
        str,
    ]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            paper_tables = {
                "practice_papers",
                "paper_draft_versions",
                "paper_draft_candidates",
                "published_paper_versions",
                "paper_archive_events",
            }
            tables = set(
                await connection.scalars(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
                    ),
                    {"tables": sorted(paper_tables)},
                )
            )
            columns = {
                table_name: set(
                    await connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = :table_name"
                        ),
                        {"table_name": table_name},
                    )
                )
                for table_name in paper_tables
            }
            triggers = set(
                await connection.scalars(
                    text(
                        "SELECT trigger.tgname FROM pg_trigger AS trigger "
                        "JOIN pg_class AS relation ON relation.oid = trigger.tgrelid "
                        "WHERE NOT trigger.tgisinternal AND relation.relname = ANY(:tables)"
                    ),
                    {"tables": sorted(paper_tables)},
                )
            )
            deferred = set(
                await connection.scalars(
                    text(
                        "SELECT constraint_row.conname FROM pg_constraint AS constraint_row "
                        "JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid "
                        "WHERE constraint_row.contype = 't' "
                        "AND constraint_row.condeferrable AND constraint_row.condeferred "
                        "AND relation.relname = ANY(:tables)"
                    ),
                    {"tables": sorted(paper_tables)},
                )
            )
            materialized_view_count = int(
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_class WHERE relkind = 'm' "
                        "AND relname LIKE ANY(ARRAY['%paper%', '%question%bank%'])"
                    )
                )
                or 0
            )
            database_digest = str(
                await connection.scalar(
                    text(
                        "SELECT encode(sha256(convert_to(paper_canonical_jsonb("
                        "CAST(:payload AS jsonb)), 'UTF8')), 'hex')"
                    ),
                    {
                        "payload": json.dumps(
                            unicode_payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    },
                )
            )
            snapshot_bound = str(
                await connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'ck_published_paper_versions_snapshot_bound'"
                    )
                )
            )
        await engine.dispose()
        return (
            tables,
            columns,
            triggers,
            deferred,
            materialized_view_count,
            database_digest,
            snapshot_bound,
        )

    tables, columns, triggers, deferred, view_count, database_digest, snapshot_bound = asyncio.run(
        inspect()
    )
    assert tables == {
        "practice_papers",
        "paper_draft_versions",
        "paper_draft_candidates",
        "published_paper_versions",
        "paper_archive_events",
    }
    assert {
        "id",
        "curriculum_version_id",
        "paper_blueprint_id",
        "blueprint_id",
        "blueprint_version",
        "state",
        "current_version",
        "idempotency_key_hash",
        "create_request_fingerprint",
        "created_by",
        "created_at",
        "updated_by",
        "updated_at",
    } == columns["practice_papers"]
    assert {"paper_id", "curriculum_version_id", "version", "title"} <= columns[
        "paper_draft_versions"
    ]
    assert {
        "paper_id",
        "curriculum_version_id",
        "paper_version",
        "ordinal",
        "blueprint_slot_id",
        "candidate_id",
        "candidate_version",
        "candidate_revision",
    } <= columns["paper_draft_candidates"]
    assert {"snapshot", "content_hash", "published_by", "published_at"} <= columns[
        "published_paper_versions"
    ]
    assert {"reason", "archived_by", "archived_at"} <= columns["paper_archive_events"]
    assert {
        "enforce_practice_paper_insert_trigger",
        "enforce_practice_paper_update_trigger",
        "reject_practice_paper_delete_trigger",
        "enforce_paper_draft_version_insert_trigger",
        "reject_paper_draft_version_mutation_trigger",
        "enforce_paper_draft_candidate_insert_trigger",
        "reject_paper_draft_candidate_mutation_trigger",
        "enforce_published_paper_version_insert_trigger",
        "reject_published_paper_version_mutation_trigger",
        "enforce_paper_archive_event_insert_trigger",
        "reject_paper_archive_event_mutation_trigger",
    } <= triggers
    assert deferred == {
        "enforce_practice_papers_complete_trigger",
        "enforce_paper_draft_versions_complete_trigger",
        "enforce_paper_draft_candidates_complete_trigger",
        "enforce_published_paper_versions_complete_trigger",
        "enforce_paper_archive_events_complete_trigger",
    }
    assert view_count == 0
    assert database_digest == expected_digest
    assert "octet_length(paper_canonical_jsonb(snapshot))" in snapshot_bound
    assert "33554432" in snapshot_bound


@pytest.mark.integration
def test_published_paper_versions_are_bounded_to_32_in_every_persisted_record(
    database_url: str,
) -> None:
    upgrade_database(database_url)

    async def inspect() -> dict[str, str]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname IN ("
                        "'ck_practice_papers_state_version', "
                        "'ck_paper_draft_versions_version', "
                        "'ck_paper_draft_candidates_paper_bounds', "
                        "'ck_published_paper_versions_version', "
                        "'ck_paper_archive_events_version')"
                    )
                )
            ).all()
        await engine.dispose()
        return {str(name): str(definition) for name, definition in rows}

    bounds = asyncio.run(inspect())
    assert set(bounds) == {
        "ck_practice_papers_state_version",
        "ck_paper_draft_versions_version",
        "ck_paper_draft_candidates_paper_bounds",
        "ck_published_paper_versions_version",
        "ck_paper_archive_events_version",
    }
    assert all("32" in definition for definition in bounds.values())
    assert all("1000" not in definition for definition in bounds.values())


@pytest.mark.integration
def test_published_paper_migration_downgrades_cleanly_and_reapplies(database_url: str) -> None:
    command.downgrade(_config_for_database(database_url), "0015_review_candidates")

    async def paper_tables_exist() -> bool:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            count = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name IN "
                    "('practice_papers', 'paper_draft_versions', 'paper_draft_candidates', "
                    "'published_paper_versions', 'paper_archive_events')"
                )
            )
        await engine.dispose()
        return int(count or 0) > 0

    assert not asyncio.run(paper_tables_exist())
    upgrade_database(database_url)
    assert asyncio.run(paper_tables_exist())
    assert_database_schema_current(database_url)


@pytest.mark.integration
def test_generation_migration_has_durable_state_and_append_only_attempt_triggers(
    database_url: str,
) -> None:
    upgrade_database(database_url)

    async def inspect() -> tuple[set[str], set[str], set[str], set[str]]:
        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            run_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'generation_runs'"
                    )
                )
            )
            attempt_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'generation_attempts'"
                    )
                )
            )
            job_columns = set(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'generation_jobs'"
                    )
                )
            )
            triggers = set(
                await connection.scalars(
                    text(
                        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND "
                        "tgrelid IN ('generation_runs'::regclass, "
                        "'generation_attempts'::regclass, 'generation_jobs'::regclass)"
                    )
                )
            )
        await engine.dispose()
        return run_columns, attempt_columns, job_columns, triggers

    run_columns, attempt_columns, job_columns, triggers = asyncio.run(inspect())
    assert {
        "request_fingerprint",
        "blueprint_snapshot",
        "blueprint_slot_snapshot",
        "context_snapshot",
        "prompt_version",
        "provider_version",
        "model_version",
        "retrieval_version",
        "schema_version",
        "pricing_version",
        "max_attempts",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_microusd",
        "attempt_count",
        "total_tokens",
        "cost_microusd",
        "latency_ms",
        "status",
        "version",
        "failure_code",
    } <= run_columns
    assert {
        "attempt_number",
        "retry_of_attempt_id",
        "provider_idempotency_key",
        "failure_code",
        "retry_after_ms",
        "accounting_known",
        "total_tokens",
        "cost_microusd",
        "latency_ms",
        "candidate",
    } <= attempt_columns
    assert {"generation_run_id", "queue_message_id", "status", "version"} <= job_columns
    assert triggers == {
        "enforce_generation_run_insert_trigger",
        "enforce_generation_run_update_trigger",
        "reject_generation_run_delete_trigger",
        "enforce_generation_attempt_insert_trigger",
        "reject_generation_attempt_mutation_trigger",
        "enforce_generation_job_insert_trigger",
        "enforce_generation_job_update_trigger",
        "reject_generation_job_delete_trigger",
    }


@pytest.mark.integration
def test_readiness_connects_to_postgresql_and_valkey(
    database_url: str,
    valkey_url: str,
) -> None:
    settings = Settings(
        database_url=SecretStr(database_url),
        valkey_url=SecretStr(valkey_url),
    )

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "valkey": "ok"},
    }


@pytest.mark.integration
def test_worker_starts_with_valkey(valkey_url: str) -> None:
    settings = Settings(valkey_url=SecretStr(valkey_url))
    broker = create_broker(settings)
    broker.declare_queue("default")
    worker = Worker(broker, worker_threads=1, worker_timeout=100)

    try:
        worker.start()
        assert broker.do_qsize("default") == 0
    finally:
        worker.stop(timeout=5_000)
        broker.close()
