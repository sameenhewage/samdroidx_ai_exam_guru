import asyncio
import json
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="teacher-paper-migration-only",  # pragma: allowlist secret
        dbname="exam_guru_teacher_paper_migration_test",
        driver="asyncpg",
    ) as postgres:
        yield postgres.get_connection_url()


@pytest.mark.integration
def test_0025_teacher_paper_aggregate_is_bounded_restrictive_append_only_and_cleanly_reversible(
    database_url: str,
) -> None:
    config = _config_for_database(database_url)
    command.upgrade(config, "head")
    assert_database_schema_current(database_url)

    async def inspect() -> tuple[str | None, set[str], set[str], set[str], set[str], set[str]]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                tables = set(
                    await connection.scalars(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_name IN "
                            "('teacher_paper_jobs', 'teacher_paper_slots', "
                            "'teacher_paper_slot_runs', 'teacher_paper_marking_confirmations', "
                            "'assessment_programme_policy_versions', "
                            "'assessment_programme_policy_scopes')"
                        )
                    )
                )
                job_columns = set(
                    await connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'teacher_paper_jobs'"
                        )
                    )
                )
                slot_columns = set(
                    await connection.scalars(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'teacher_paper_slots'"
                        )
                    )
                )
                triggers = set(
                    await connection.scalars(
                        text(
                            "SELECT trigger_row.tgname FROM pg_trigger AS trigger_row "
                            "JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid "
                            "WHERE NOT trigger_row.tgisinternal AND relation.relname IN "
                            "('teacher_paper_jobs', 'teacher_paper_slots', "
                            "'teacher_paper_slot_runs', 'teacher_paper_marking_confirmations', "
                            "'paper_draft_candidates', 'assessment_programme_policy_versions', "
                            "'assessment_programme_policy_scopes')"
                        )
                    )
                )
                non_restrict_foreign_keys = set(
                    await connection.scalars(
                        text(
                            "SELECT constraint_row.conname FROM pg_constraint AS constraint_row "
                            "JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid "
                            "WHERE constraint_row.contype = 'f' AND relation.relname IN "
                            "('teacher_paper_jobs', 'teacher_paper_slots', "
                            "'teacher_paper_slot_runs') AND constraint_row.confdeltype <> 'r'"
                        )
                    )
                )
            return (
                revision,
                tables,
                job_columns,
                slot_columns,
                triggers,
                non_restrict_foreign_keys,
            )
        finally:
            await engine.dispose()

    revision, tables, job_columns, slot_columns, triggers, non_restrict = asyncio.run(inspect())
    assert revision == "0032_source_intake_metadata"
    assert tables == {
        "assessment_programme_policy_scopes",
        "assessment_programme_policy_versions",
        "teacher_paper_jobs",
        "teacher_paper_marking_confirmations",
        "teacher_paper_slots",
        "teacher_paper_slot_runs",
    }
    assert {
        "id",
        "paper_reference",
        "created_by",
        "idempotency_key_hash",
        "request_fingerprint",
        "curriculum_version_id",
        "exam_configuration_id",
        "medium_id",
        "subject_id",
        "teacher_intent",
        "paper_settings",
        "resolution_snapshot",
        "paper_blueprint_id",
        "practice_paper_id",
        "status",
        "version",
        "slot_count",
        "generated_count",
        "validated_count",
        "candidate_count",
        "approved_count",
        "failed_count",
        "total_tokens",
        "cost_microusd",
        "max_cost_microusd",
        "failure_code",
        "failure_detail",
        "actor_token",
        "actor_lease_expires_at",
        "dispatch_message_id",
        "created_at",
        "updated_at",
        "completed_at",
    } <= job_columns
    assert {
        "id",
        "paper_job_id",
        "ordinal",
        "blueprint_slot_id",
        "unit_id",
        "lesson_id",
        "competency_id",
        "skill_id",
        "sub_skill_id",
        "learning_concept_id",
        "current_generation_run_id",
        "current_validation_run_id",
        "current_candidate_id",
        "status",
        "version",
        "regeneration_count",
        "requires_revalidation",
        "failure_code",
    } <= slot_columns
    assert non_restrict == set()
    assert {
        "enforce_assessment_programme_policy_scope_insert_trigger",
        "enforce_assessment_programme_policy_version_insert_trigger",
        "enforce_assessment_programme_policy_version_update_trigger",
        "enforce_teacher_paper_draft_job_state_trigger",
        "enforce_teacher_paper_draft_slot_immutability_trigger",
        "enforce_teacher_paper_job_insert_trigger",
        "enforce_teacher_paper_job_update_trigger",
        "reject_assessment_programme_policy_scope_mutation_trigger",
        "reject_assessment_programme_policy_version_delete_trigger",
        "reject_teacher_paper_job_delete_trigger",
        "enforce_teacher_marking_confirmation_on_draft_trigger",
        "trg_teacher_marking_confirmation_insert",
        "trg_teacher_marking_confirmation_immutable",
        "enforce_teacher_paper_slot_insert_trigger",
        "enforce_teacher_paper_slot_update_trigger",
        "reject_teacher_paper_slot_delete_trigger",
        "enforce_teacher_paper_slot_run_insert_trigger",
        "reject_teacher_paper_slot_run_mutation_trigger",
    } <= triggers

    async def function_definition(name: str) -> str:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                definition = await connection.scalar(
                    text(
                        "SELECT pg_get_functiondef(oid) FROM pg_proc "
                        "WHERE proname = :name AND pg_function_is_visible(oid)"
                    ),
                    {"name": name},
                )
                assert isinstance(definition, str)
                return definition
        finally:
            await engine.dispose()

    job_guard = asyncio.run(function_definition("enforce_teacher_paper_draft_job_state"))
    assert "NEW.practice_paper_id IS NOT NULL" in job_guard
    assert "NEW.status <> 'ready_for_review'" in job_guard
    slot_guard = asyncio.run(function_definition("enforce_teacher_paper_draft_slot_immutability"))
    assert "practice_paper_id IS NOT NULL" in slot_guard
    assert "immutable after draft creation" in slot_guard
    draft_guard = asyncio.run(function_definition("enforce_teacher_marking_confirmation_on_draft"))
    assert "slot.status <> 'approved'" in draft_guard
    assert "slot.current_candidate_id IS DISTINCT FROM candidate.id" in draft_guard
    assert "confirmation.slot_id = slot.id" in draft_guard

    command.downgrade(config, "0024_subject_quality_validation_scope")

    async def downgraded_review_content_contract_works() -> tuple[bool, bool]:
        criterion = {
            "criterion_id": "criterion-1",
            "description": "Award one mark for A.",
            "marks": 1,
        }
        generated = {
            "question_type": "multiple_choice",
            "stem": "Choose one.",
            "options": [
                {"option_id": "A", "text": "One"},
                {"option_id": "B", "text": "Two"},
            ],
            "answer": {
                "correct_option_id": "A",
                "accepted_responses": [],
                "explanation": "A is correct.",
            },
            "marking": {"total_marks": 1, "criteria": [criterion]},
        }
        content = {
            "question_type": "multiple_choice",
            "stem": "Choose one.",
            "options": generated["options"],
            "answer": "A",
            "explanation": "A is correct.",
            "marks": 1,
            "marking_guide": [
                json.dumps(
                    criterion,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ],
        }
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                valid = await connection.scalar(
                    text("SELECT review_candidate_content_valid(CAST(:content AS jsonb))"),
                    {"content": json.dumps(content)},
                )
                matches = await connection.scalar(
                    text(
                        "SELECT review_candidate_initial_content_matches("
                        "CAST(:content AS jsonb), CAST(:generated AS jsonb))"
                    ),
                    {
                        "content": json.dumps(content),
                        "generated": json.dumps(generated),
                    },
                )
                return bool(valid), bool(matches)
        finally:
            await engine.dispose()

    assert asyncio.run(downgraded_review_content_contract_works()) == (True, True)

    async def absent() -> bool:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                return (
                    await connection.scalar(
                        text("SELECT to_regclass('public.teacher_paper_jobs') IS NULL")
                    )
                ) is True
        finally:
            await engine.dispose()

    assert asyncio.run(absent())
    command.upgrade(config, "head")
    assert_database_schema_current(database_url)
