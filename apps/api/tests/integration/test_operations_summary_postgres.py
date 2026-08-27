import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.generation.models import GenerationRunModel, GenerationRunStatus
from exam_guru_api.infrastructure.migrations import assert_database_schema_current, upgrade_database
from exam_guru_api.knowledge.models import EmbeddingJobModel, EmbeddingJobStatus
from exam_guru_api.main import create_app
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
PATH = "/api/v1/admin/operations/summary"
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
START = datetime(2026, 3, 1, tzinfo=UTC)
END = START + timedelta(days=1)
ACTOR_ID = UUID(int=8_100_001)
CURRICULUM_ID = UUID(int=8_100_002)
BLUEPRINT_ID = UUID(int=8_100_003)
CONTEXT_ID = UUID(int=8_100_004)


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(ACTOR_ID, frozenset({AdminRole.ADMIN}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class DatabaseResources:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        await self.engine.dispose()


@pytest.fixture(scope="module")
def operations_database_url() -> Iterator[str]:
    credentials = ("exam_guru", "operations-integration-only")
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username=credentials[0],
        password=credentials[1],  # pragma: allowlist secret
        dbname="exam_guru_operations_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        assert_database_schema_current(database_url)
        yield database_url


def generation_run(
    *,
    run_id: UUID,
    status: GenerationRunStatus,
    created_at: datetime,
    attempt_count: int,
    input_tokens: int,
    output_tokens: int,
    cost_microusd: int,
    latency_ms: int,
    failure_code: str | None,
    completed_at: datetime | None = None,
) -> GenerationRunModel:
    succeeded = status is GenerationRunStatus.SUCCEEDED
    return GenerationRunModel(
        id=run_id,
        curriculum_version_id=CURRICULUM_ID,
        paper_blueprint_id=BLUEPRINT_ID,
        retry_of_run_id=None,
        retry_depth=0,
        slot_id=f"slot-{run_id.int}",
        idempotency_key_hash=f"sha256:{run_id.int:064x}",
        request_fingerprint=f"sha256:{run_id.int + 100:064x}",
        blueprint_version="blueprint-v1",
        blueprint_snapshot={},
        blueprint_slot_snapshot={"slot_id": f"slot-{run_id.int}"},
        knowledge_chunk_ids=[str(CONTEXT_ID)],
        historical_question_ids=[],
        context_snapshot={"items": [{}], "trust": "untrusted_data"},
        prompt_id="grade5-question",
        prompt_version="v1",
        provider="fixture",
        provider_version="v1",
        model="fixture-model",
        model_version="v1",
        retrieval_version="v1",
        schema_version="v1",
        pricing_version="v1",
        input_microusd_per_million_tokens=1,
        output_microusd_per_million_tokens=1,
        generation_parameters={"temperature": 0, "max_output_tokens": 100, "seed": 1},
        max_attempts=3,
        max_input_tokens=100,
        max_output_tokens=100,
        max_cost_microusd=10_000,
        status=status.value,
        version=2 if status is not GenerationRunStatus.PENDING else 0,
        started_at=created_at if status is not GenerationRunStatus.PENDING else None,
        completed_at=(
            completed_at or created_at + timedelta(seconds=1)
            if status is not GenerationRunStatus.PENDING
            else None
        ),
        failure_code=failure_code,
        result_attempt_id=UUID(int=run_id.int + 1_000) if succeeded else None,
        attempt_count=attempt_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_microusd=cost_microusd,
        latency_ms=latency_ms,
        candidate={} if succeeded else None,
        disposition="requires_validation" if succeeded else None,
        created_by=ACTOR_ID,
        created_at=created_at,
    )


def semantic_validation_run(
    *,
    run_id: UUID,
    generation_run_id: UUID,
    created_at: datetime,
    overall_status: str,
) -> ValidationRunModel:
    input_fingerprint = f"{run_id.int:064x}"
    candidate_fingerprint = f"{run_id.int + 1:064x}"
    generation_result_fingerprint = f"{generation_run_id.int + 2:064x}"
    attempt_id = UUID(int=generation_run_id.int + 1_000)
    return ValidationRunModel(
        id=run_id,
        curriculum_version_id=CURRICULUM_ID,
        generation_run_id=generation_run_id,
        generation_attempt_id=attempt_id,
        pipeline_version=f"semantic-operations.v{run_id.int}",
        pipeline_fingerprint=f"{run_id.int + 3:064x}",
        input_schema_version="question-validation-input.v3",
        report_schema_version="question-validation-report.v3",
        generation_result_fingerprint=generation_result_fingerprint,
        input_fingerprint=input_fingerprint,
        candidate_fingerprint=candidate_fingerprint,
        report_fingerprint=f"{run_id.int + 4:064x}",
        overall_status=overall_status,
        input_snapshot={
            "schema_version": "question-validation-input.v3",
            "trust": "server_reconstructed",
            "generation": {
                "generation_run_id": str(generation_run_id),
                "generation_attempt_id": str(attempt_id),
                "generation_result_fingerprint": generation_result_fingerprint,
            },
            "candidate": {},
            "candidate_fingerprint": candidate_fingerprint,
            "input_fingerprint": input_fingerprint,
            "blueprint": {},
            "grounding_sources": [{}],
            "duplicate_references": [],
            "subject_scope": {
                "trust": "server_owned",
                "grade": 5,
                "medium": "en",
                "subject_id": str(UUID(int=8_300_001)),
                "subject_code": "SCIENCE",
                "curriculum_version_id": str(CURRICULUM_ID),
                "unit_ids": [],
                "lesson_ids": [],
            },
            "generated_scope": {},
            "context_scope_bindings": [],
        },
        validator_lineage=[
            {
                "validator_id": "grounded-factual-subject",
                "validator_version": "2.0.0",
            }
        ],
        limitations=["Fixture operational validation."],
        finding_count=1,
        validator_count=1,
        grounding_source_count=1,
        duplicate_reference_count=0,
        created_by=ACTOR_ID,
        created_at=created_at,
    )


def semantic_finding(
    *,
    finding_id: UUID,
    run_id: UUID,
    status: str,
    claim_statuses: tuple[str, ...],
    failure_code: str | None,
    attempted: bool,
    accounting: tuple[int, int, int, int, int] | None,
) -> ValidationFindingModel:
    claims = [
        {
            "claim_id": f"claim-{index + 1}",
            "claim_type": "answer" if index == 0 else "explanation",
            "location": "$.candidate.answer" if index == 0 else "$.candidate.answer.explanation",
            "status": claim_status,
            "summary": f"Claim {index + 1} is {claim_status}.",
            "evidence_refs": [
                {
                    "context_id": "context-01",
                    "source_document_id": "source-01",
                    "page_number": 7,
                }
            ]
            if claim_status in {"supported", "contradicted"}
            else [],
        }
        for index, claim_status in enumerate(claim_statuses)
    ]
    details: dict[str, object] = {
        "schema_version": "semantic-verification.v1",
        "decomposition_version": "deterministic-factual-claims.v1",
        "call_attempted": attempted,
        "failure_code": failure_code,
        "status": status,
        "summary": f"Semantic verification is {status}.",
        "claims": claims,
        "lineage": {
            "verifier_id": "semantic-fixture",
            "verifier_version": "1.0.0",
            "prompt_version": "semantic.v1",
            "provider": "fixture",
            "provider_version": "1.0.0",
            "model": "fixture-model",
            "model_version": "fixture-model-v1",
            "pricing_version": "fixture-pricing-v1",
        }
        if attempted
        else None,
        "accounting": {
            "input_tokens": accounting[0],
            "output_tokens": accounting[1],
            "total_tokens": accounting[2],
            "cost_microusd": accounting[3],
            "latency_ms": accounting[4],
        }
        if accounting is not None
        else None,
    }
    finding_status = "pass" if status == "supported" else "warn"
    code = (
        "subject.factual.grounded"
        if status == "supported"
        else "subject.factual.unsupported_claim"
        if status == "insufficient_evidence"
        else "subject.factual.verifier_unavailable"
    )
    return ValidationFindingModel(
        id=finding_id,
        validation_run_id=run_id,
        ordinal=0,
        validator_id="grounded-factual-subject",
        validator_version="2.0.0",
        code=code,
        status=finding_status,
        message="Fixture semantic finding.",
        evidence=[
            {
                "location": "$.semantic_verification",
                "expected": "reviewed evidence",
                "observed": status,
                "details": details,
            }
        ],
        evidence_count=1,
        created_at=START,
    )


def embedding_job(
    *,
    job_id: UUID,
    status: EmbeddingJobStatus,
    created_at: datetime,
    completed_at: datetime | None = None,
    failure_code: str | None = None,
) -> EmbeddingJobModel:
    claimed_at = None if status is EmbeddingJobStatus.QUEUED else created_at + timedelta(seconds=1)
    return EmbeddingJobModel(
        id=job_id,
        curriculum_version_id=CURRICULUM_ID,
        retry_of_job_id=None,
        retry_depth=0,
        historical_question_ids=[str(CONTEXT_ID)],
        knowledge_chunk_ids=[],
        idempotency_key_hash=f"sha256:{job_id.int:064x}",
        request_fingerprint=f"sha256:{job_id.int + 100:064x}",
        source_fingerprint=f"sha256:{job_id.int + 200:064x}",
        provider="fixture",
        model="fixture-model",
        dimension=3,
        embedding_version="v1",
        config_fingerprint="fixture-v1",
        status=status.value,
        version=0 if status is EmbeddingJobStatus.QUEUED else 1,
        queue_message_id=None,
        requested_count=1,
        embedded_count=0,
        deduplicated_count=0,
        failure_code=failure_code,
        created_by=ACTOR_ID,
        created_at=created_at,
        updated_at=completed_at or claimed_at or created_at,
        claimed_at=claimed_at,
        completed_at=completed_at,
    )


@pytest.mark.integration
@pytest.mark.filterwarnings(
    "error:SELECT statement has a cartesian product:sqlalchemy.exc.SAWarning"
)
def test_postgres_summary_aggregates_known_generation_costs_statuses_and_failures(
    operations_database_url: str,
) -> None:
    async def seed() -> None:
        engine = create_async_engine(operations_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                await session.execute(text("SET LOCAL session_replication_role = replica"))
                session.add_all(
                    [
                        generation_run(
                            run_id=UUID(int=8_100_100),
                            status=GenerationRunStatus.SUCCEEDED,
                            created_at=START + timedelta(hours=1),
                            attempt_count=1,
                            input_tokens=100,
                            output_tokens=40,
                            cost_microusd=17,
                            latency_ms=80,
                            failure_code=None,
                        ),
                        generation_run(
                            run_id=UUID(int=8_100_200),
                            status=GenerationRunStatus.FAILED,
                            created_at=START + timedelta(hours=2),
                            attempt_count=2,
                            input_tokens=50,
                            output_tokens=10,
                            cost_microusd=9,
                            latency_ms=120,
                            failure_code="provider_timeout",
                        ),
                        generation_run(
                            run_id=UUID(int=8_100_300),
                            status=GenerationRunStatus.FAILED,
                            created_at=END + timedelta(hours=1),
                            attempt_count=1,
                            input_tokens=999,
                            output_tokens=999,
                            cost_microusd=999,
                            latency_ms=999,
                            failure_code="excluded_failure",
                        ),
                    ]
                )
                await session.flush()
                await session.execute(text("SET LOCAL session_replication_role = origin"))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    application = create_app(
        identity_provider=StaticIdentityProvider(),
        resource_factory=lambda _: DatabaseResources(operations_database_url),
    )
    with TestClient(application) as client:
        response = client.get(
            PATH,
            headers=ADMIN_HEADERS,
            params={"start": START.isoformat(), "end": END.isoformat()},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["generation"] == {
        "run_count": 2,
        "status_counts": {"pending": 0, "running": 0, "succeeded": 1, "failed": 1},
        "failure_codes": [{"code": "provider_timeout", "count": 1}],
        "attempt_count": 3,
        "input_tokens": 150,
        "output_tokens": 50,
        "total_tokens": 200,
        "cost_microusd": 26,
        "latency_ms": {"total": 200, "average": 100, "maximum": 120},
    }
    assert body["data_bounds"] == {
        "earliest_observed_at": "2026-03-01T01:00:01Z",
        "latest_observed_at": "2026-03-01T02:00:01Z",
    }
    assert body["validation"]["run_count"] == 0
    assert body["extraction"]["document_count"] == 0
    assert body["embedding"]["job_count"] == 0
    assert body["object_storage"]["reconciliation"] == {
        "run_count": 0,
        "scanned_count": 0,
        "referenced_count": 0,
        "candidate_count": 0,
        "resolved_count": 0,
        "tagged_count": 0,
        "failure_count": 0,
        "truncated_run_count": 0,
        "current_candidate_count": 0,
        "last_completed_at": None,
        "failure_codes": [],
    }
    assert body["practice_papers"]["paper_count"] == 0


@pytest.mark.integration
def test_postgres_summary_aggregates_semantic_verifier_claims_cost_and_failures(
    operations_database_url: str,
) -> None:
    window_start = datetime(2026, 5, 1, tzinfo=UTC)
    window_end = window_start + timedelta(days=1)
    parent_run_id = UUID(int=8_300_100)
    cases = (
        (
            8_300_201,
            "pass",
            "supported",
            ("supported", "supported", "supported"),
            None,
            True,
            (100, 20, 120, 31, 80),
        ),
        (
            8_300_202,
            "warn",
            "unavailable",
            ("unavailable", "unavailable"),
            "timeout",
            True,
            (50, 10, 60, 9, 120),
        ),
        (
            8_300_203,
            "warn",
            "unavailable",
            ("unavailable", "unavailable", "unavailable"),
            "not_configured",
            False,
            None,
        ),
        (
            8_300_204,
            "warn",
            "insufficient_evidence",
            ("supported", "insufficient_evidence"),
            None,
            True,
            (30, 5, 35, 4, 60),
        ),
    )

    async def seed() -> None:
        engine = create_async_engine(operations_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                await session.execute(text("SET LOCAL session_replication_role = replica"))
                session.add(
                    generation_run(
                        run_id=parent_run_id,
                        status=GenerationRunStatus.SUCCEEDED,
                        created_at=window_start + timedelta(minutes=30),
                        attempt_count=1,
                        input_tokens=1,
                        output_tokens=1,
                        cost_microusd=1,
                        latency_ms=1,
                        failure_code=None,
                    )
                )
                for offset, case in enumerate(cases, start=1):
                    (
                        raw_id,
                        overall_status,
                        semantic_status,
                        claims,
                        failure,
                        attempted,
                        accounting,
                    ) = case
                    run_id = UUID(int=raw_id)
                    session.add(
                        semantic_validation_run(
                            run_id=run_id,
                            generation_run_id=parent_run_id,
                            created_at=window_start + timedelta(hours=offset),
                            overall_status=overall_status,
                        )
                    )
                    session.add(
                        semantic_finding(
                            finding_id=UUID(int=raw_id + 1_000),
                            run_id=run_id,
                            status=semantic_status,
                            claim_statuses=claims,
                            failure_code=failure,
                            attempted=attempted,
                            accounting=accounting,
                        )
                    )
                await session.flush()
                await session.execute(text("SET LOCAL session_replication_role = origin"))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    application = create_app(
        identity_provider=StaticIdentityProvider(),
        resource_factory=lambda _: DatabaseResources(operations_database_url),
    )
    with TestClient(application) as client:
        response = client.get(
            PATH,
            headers=ADMIN_HEADERS,
            params={"start": window_start.isoformat(), "end": window_end.isoformat()},
        )

    assert response.status_code == 200
    assert response.json()["semantic_verifier"] == {
        "record_count": 4,
        "attempt_count": 3,
        "accounted_count": 3,
        "status_counts": {
            "supported": 1,
            "contradicted": 0,
            "insufficient_evidence": 1,
            "unavailable": 2,
        },
        "failure_codes": [
            {"code": "not_configured", "count": 1},
            {"code": "timeout", "count": 1},
        ],
        "claim_count": 10,
        "claim_status_counts": {
            "supported": 4,
            "contradicted": 0,
            "insufficient_evidence": 1,
            "unavailable": 5,
        },
        "input_tokens": 180,
        "output_tokens": 35,
        "total_tokens": 215,
        "cost_microusd": 44,
        "latency_ms": {"total": 260, "average": 86, "maximum": 120},
    }


@pytest.mark.integration
def test_terminal_generation_and_embedding_work_is_observed_at_completion(
    operations_database_url: str,
) -> None:
    window_start = datetime(2026, 4, 1, tzinfo=UTC)
    window_end = window_start + timedelta(days=1)

    async def seed() -> None:
        engine = create_async_engine(operations_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                await session.execute(text("SET LOCAL session_replication_role = replica"))
                session.add_all(
                    [
                        generation_run(
                            run_id=UUID(int=8_200_100),
                            status=GenerationRunStatus.FAILED,
                            created_at=window_start - timedelta(days=2),
                            completed_at=window_start + timedelta(hours=1),
                            attempt_count=1,
                            input_tokens=20,
                            output_tokens=5,
                            cost_microusd=31,
                            latency_ms=45,
                            failure_code="terminal_timeout",
                        ),
                        generation_run(
                            run_id=UUID(int=8_200_200),
                            status=GenerationRunStatus.FAILED,
                            created_at=window_start - timedelta(days=3),
                            completed_at=window_end + timedelta(hours=1),
                            attempt_count=1,
                            input_tokens=999,
                            output_tokens=999,
                            cost_microusd=999,
                            latency_ms=999,
                            failure_code="excluded_after_end",
                        ),
                        generation_run(
                            run_id=UUID(int=8_200_300),
                            status=GenerationRunStatus.PENDING,
                            created_at=window_start + timedelta(hours=2),
                            attempt_count=0,
                            input_tokens=0,
                            output_tokens=0,
                            cost_microusd=0,
                            latency_ms=0,
                            failure_code=None,
                        ),
                        embedding_job(
                            job_id=UUID(int=8_200_400),
                            status=EmbeddingJobStatus.FAILED,
                            created_at=window_start - timedelta(days=2),
                            completed_at=window_start + timedelta(minutes=90),
                            failure_code="embedding_provider_unavailable",
                        ),
                        embedding_job(
                            job_id=UUID(int=8_200_500),
                            status=EmbeddingJobStatus.FAILED,
                            created_at=window_start - timedelta(days=3),
                            completed_at=window_end + timedelta(hours=1),
                            failure_code="embedding_excluded_after_end",
                        ),
                        embedding_job(
                            job_id=UUID(int=8_200_600),
                            status=EmbeddingJobStatus.CLAIMED,
                            created_at=window_start + timedelta(hours=4),
                        ),
                    ]
                )
                await session.flush()
                await session.execute(text("SET LOCAL session_replication_role = origin"))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    application = create_app(
        identity_provider=StaticIdentityProvider(),
        resource_factory=lambda _: DatabaseResources(operations_database_url),
    )
    with TestClient(application) as client:
        response = client.get(
            PATH,
            headers=ADMIN_HEADERS,
            params={"start": window_start.isoformat(), "end": window_end.isoformat()},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["generation"] == {
        "run_count": 2,
        "status_counts": {"pending": 1, "running": 0, "succeeded": 0, "failed": 1},
        "failure_codes": [{"code": "terminal_timeout", "count": 1}],
        "attempt_count": 1,
        "input_tokens": 20,
        "output_tokens": 5,
        "total_tokens": 25,
        "cost_microusd": 31,
        "latency_ms": {"total": 45, "average": 22, "maximum": 45},
    }
    assert body["embedding"] == {
        "job_count": 2,
        "status_counts": {"queued": 0, "claimed": 1, "succeeded": 0, "failed": 1},
        "failure_codes": [{"code": "embedding_provider_unavailable", "count": 1}],
        "requested_count": 2,
        "embedded_count": 0,
        "deduplicated_count": 0,
    }
    assert body["data_bounds"] == {
        "earliest_observed_at": "2026-04-01T01:00:00Z",
        "latest_observed_at": "2026-04-01T04:00:00Z",
    }
    assert "excluded_after_end" not in str(body)
