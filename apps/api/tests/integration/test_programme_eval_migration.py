import asyncio
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from exam_guru_api.subject_quality import service as quality_service
from exam_guru_api.teacher_papers.jobs import DeterministicPaperGenerationDispatcher
from tests.integration.test_fidelity_workspace_postgres import database_session
from tests.integration.test_teacher_paper_aggregate import (
    ADMIN_HEADERS,
    REVIEWER_HEADERS,
    Seed,
    advance_and_run_slots,
    api_client,
    request_payload,
)
from tests.integration.test_teacher_paper_aggregate import aggregate_seed as aggregate_seed

pytestmark = pytest.mark.integration


def test_eval_runner_upgrade_preserves_v1_history_and_refuses_v2_history_loss(
    aggregate_seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_dispatcher, generation_dispatcher = (
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
    )
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            headers={**ADMIN_HEADERS, "Idempotency-Key": "eval-history-" + str(uuid4())},
            json=request_payload(
                client, scope={"kind": "selected_lessons", "lesson_numbers": [1]}, question_count=1
            ),
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
    terminal = asyncio.run(
        advance_and_run_slots(aggregate_seed, job_id, paper_dispatcher, generation_dispatcher)
    )
    assert terminal.status == "ready_for_review"
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        question = client.get(
            f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS
        ).json()["questions"][0]
        regenerated = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/regenerate",
            headers={
                **REVIEWER_HEADERS,
                "Idempotency-Key": "eval-history-regenerate-" + str(uuid4()),
            },
            json={
                "expected_version": question["aggregate_slot_version"],
                "reason_code": "answer_incorrect",
                "note": "Synthetic legacy eval history",
            },
        )
        assert regenerated.status_code == 202
        feedback_id = regenerated.json()["quality_feedback_id"]
        records = client.get(
            "/api/v1/admin/subject-quality/feedback",
            headers=REVIEWER_HEADERS,
            params={"candidate_id": question["technical_details"]["candidate_id"]},
        ).json()["items"]
        feedback = next(item for item in records if item["id"] == feedback_id)
        findings = feedback["findings_at_action"]
        promoted = client.post(
            f"/api/v1/admin/subject-quality/feedback/{feedback_id}/promote",
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "eval-history-promote-" + str(uuid4())},
            json={
                "expected_status": findings["overall_status"],
                "expected_finding_codes": sorted(
                    item["code"] for item in findings["findings"] if item["status"] != "pass"
                ),
                "defect_category": "answer_correctness",
            },
        )
        assert promoted.status_code == 201
        case_id = promoted.json()["eval_case_id"]
        assert (
            client.post(
                f"/api/v1/admin/subject-quality/eval-cases/{case_id}/approve",
                headers=ADMIN_HEADERS,
                json={"expected_version": 1},
            ).status_code
            == 200
        )
        with monkeypatch.context() as old_runtime:
            old_runtime.setattr(
                quality_service, "EVAL_RUNNER_VERSION", "subject-quality-eval-runner.v1"
            )
            old = client.post(
                "/api/v1/admin/subject-quality/eval-runs",
                headers=ADMIN_HEADERS,
                json={"case_ids": [case_id]},
            )
        assert old.status_code == 201
        old_result = old.json()
        assert old_result["runner_version"] == "subject-quality-eval-runner.v1"

    async def snapshot() -> object:
        async with database_session(aggregate_seed.database_url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'feedback',(SELECT jsonb_agg(to_jsonb(f) ORDER BY f.id)
                        FROM subject_quality_feedback f),
                    'cases',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.eval_case_id,c.version)
                        FROM subject_quality_eval_case_versions c),
                    'runs',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM subject_quality_eval_runs r),
                    'results',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM subject_quality_eval_results r),
                    'audit',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM admin_audit_events a))
            """)
            )

    before = asyncio.run(snapshot())
    config = _config_for_database(aggregate_seed.database_url)
    command.downgrade(config, "0050_programme_knowledge_context")
    assert asyncio.run(snapshot()) == before
    command.upgrade(config, "head")
    assert asyncio.run(snapshot()) == before
    assert_database_schema_current(aggregate_seed.database_url)
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        current = client.post(
            "/api/v1/admin/subject-quality/eval-runs",
            headers=ADMIN_HEADERS,
            json={"case_ids": [case_id]},
        )
        assert current.status_code == 201
        assert current.json()["runner_version"] == "subject-quality-eval-runner.v2"
        assert current.json()["run_id"] != old_result["run_id"]
        historical = client.get(
            f"/api/v1/admin/subject-quality/eval-runs/{old_result['run_id']}",
            headers=REVIEWER_HEADERS,
        )
        assert historical.status_code == 200
        assert historical.json()["runner_version"] == "subject-quality-eval-runner.v1"
        assert historical.json()["request_fingerprint"] == old_result["request_fingerprint"]
    retained = asyncio.run(snapshot())
    with pytest.raises(DBAPIError, match="cannot discard programme evaluation replay history"):
        command.downgrade(config, "0050_programme_knowledge_context")
    assert asyncio.run(snapshot()) == retained
