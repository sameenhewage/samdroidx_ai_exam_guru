from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base

MAX_GENERATION_CONTEXT_REFERENCES = 16
MAX_GENERATION_ATTEMPTS = 3
MAX_GENERATION_BLUEPRINT_SNAPSHOT_BYTES = 2_097_152
MAX_GENERATION_SLOT_SNAPSHOT_BYTES = 131_072
MAX_GENERATION_CONTEXT_SNAPSHOT_BYTES = 262_144
MAX_GENERATION_CANDIDATE_BYTES = 131_072
MAX_GENERATION_TOKENS = 30_000_000
MAX_GENERATION_COST_MICROUSD = 3_000_000_000_000
MAX_GENERATION_LATENCY_MS = 259_200_000
_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


class GenerationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationAttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationJobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_RUN_STATES_SQL = ", ".join(f"'{value.value}'" for value in GenerationRunStatus)
_ATTEMPT_STATES_SQL = ", ".join(f"'{value.value}'" for value in GenerationAttemptStatus)
_JOB_STATES_SQL = ", ".join(f"'{value.value}'" for value in GenerationJobStatus)


class GenerationRunModel(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_generation_runs_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_attempt_id", "id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_generation_runs_result_attempt",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "created_by",
            "idempotency_key_hash",
            name="uq_generation_runs_actor_idempotency",
        ),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_generation_runs_id_curriculum",
        ),
        CheckConstraint(
            f"request_fingerprint ~ '{_FINGERPRINT_SQL}'",
            name="ck_generation_runs_request_fingerprint",
        ),
        CheckConstraint(
            f"idempotency_key_hash ~ '{_FINGERPRINT_SQL}'",
            name="ck_generation_runs_idempotency_key_hash",
        ),
        CheckConstraint(
            "slot_id = btrim(slot_id) AND length(slot_id) BETWEEN 1 AND 128",
            name="ck_generation_runs_slot_id",
        ),
        *(
            CheckConstraint(
                f"{column_name} = btrim({column_name}) AND length({column_name}) > 0",
                name=f"ck_generation_runs_{column_name}",
            )
            for column_name in (
                "blueprint_version",
                "prompt_id",
                "prompt_version",
                "provider",
                "provider_version",
                "model",
                "model_version",
                "retrieval_version",
                "schema_version",
                "pricing_version",
            )
        ),
        CheckConstraint(
            "generation_uuid_array_valid(knowledge_chunk_ids, 16) AND "
            "generation_uuid_array_valid(historical_question_ids, 16) AND "
            "jsonb_array_length(knowledge_chunk_ids) + "
            "jsonb_array_length(historical_question_ids) BETWEEN 1 AND 16",
            name="ck_generation_runs_context_ids",
        ),
        CheckConstraint(
            f"jsonb_typeof(blueprint_snapshot) = 'object' AND "
            f"pg_column_size(blueprint_snapshot) <= {MAX_GENERATION_BLUEPRINT_SNAPSHOT_BYTES}",
            name="ck_generation_runs_blueprint_snapshot",
        ),
        CheckConstraint(
            f"jsonb_typeof(blueprint_slot_snapshot) = 'object' AND "
            f"pg_column_size(blueprint_slot_snapshot) <= {MAX_GENERATION_SLOT_SNAPSHOT_BYTES} AND "
            "blueprint_slot_snapshot->>'slot_id' = slot_id",
            name="ck_generation_runs_blueprint_slot_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(context_snapshot) = 'object' AND "
            "context_snapshot ?& ARRAY['items', 'trust'] AND "
            "jsonb_typeof(context_snapshot->'items') = 'array' AND "
            "jsonb_array_length(context_snapshot->'items') = "
            "jsonb_array_length(knowledge_chunk_ids) + "
            "jsonb_array_length(historical_question_ids) AND "
            "context_snapshot->>'trust' = 'untrusted_data' AND "
            f"pg_column_size(context_snapshot) <= {MAX_GENERATION_CONTEXT_SNAPSHOT_BYTES}",
            name="ck_generation_runs_context_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(generation_parameters) = 'object' AND "
            "generation_parameters ?& ARRAY['temperature', 'max_output_tokens', 'seed']",
            name="ck_generation_runs_parameters",
        ),
        CheckConstraint(
            f"max_attempts BETWEEN 1 AND {MAX_GENERATION_ATTEMPTS} AND "
            f"max_input_tokens BETWEEN 1 AND {MAX_GENERATION_TOKENS} AND "
            f"max_output_tokens BETWEEN 1 AND {MAX_GENERATION_TOKENS} AND "
            f"max_cost_microusd BETWEEN 1 AND {MAX_GENERATION_COST_MICROUSD}",
            name="ck_generation_runs_budgets",
        ),
        CheckConstraint(
            "input_microusd_per_million_tokens BETWEEN 0 AND 100000000000 AND "
            "output_microusd_per_million_tokens BETWEEN 0 AND 100000000000",
            name="ck_generation_runs_pricing",
        ),
        CheckConstraint(
            f"status IN ({_RUN_STATES_SQL}) AND version >= 0",
            name="ck_generation_runs_status_version",
        ),
        CheckConstraint(
            f"attempt_count BETWEEN 0 AND {MAX_GENERATION_ATTEMPTS} AND "
            f"input_tokens BETWEEN 0 AND {MAX_GENERATION_TOKENS} AND "
            f"output_tokens BETWEEN 0 AND {MAX_GENERATION_TOKENS} AND "
            "total_tokens = input_tokens + output_tokens AND "
            f"cost_microusd BETWEEN 0 AND {MAX_GENERATION_COST_MICROUSD} AND "
            f"latency_ms BETWEEN 0 AND {MAX_GENERATION_LATENCY_MS}",
            name="ck_generation_runs_accounting",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "length(failure_code) BETWEEN 1 AND 64)",
            name="ck_generation_runs_failure_code",
        ),
        CheckConstraint(
            f"candidate IS NULL OR (jsonb_typeof(candidate) = 'object' AND "
            f"pg_column_size(candidate) <= {MAX_GENERATION_CANDIDATE_BYTES})",
            name="ck_generation_runs_candidate",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL AND "
            "failure_code IS NULL AND result_attempt_id IS NULL AND candidate IS NULL AND "
            "disposition IS NULL AND attempt_count = 0 AND input_tokens = 0 AND "
            "output_tokens = 0 AND total_tokens = 0 AND cost_microusd = 0 AND latency_ms = 0) "
            "OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL AND "
            "failure_code IS NULL AND result_attempt_id IS NULL AND candidate IS NULL AND "
            "disposition IS NULL AND attempt_count = 0 AND input_tokens = 0 AND "
            "output_tokens = 0 AND total_tokens = 0 AND cost_microusd = 0 AND latency_ms = 0) "
            "OR (status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND completed_at >= started_at AND failure_code IS NULL AND "
            "result_attempt_id IS NOT NULL AND candidate IS NOT NULL AND "
            "disposition = 'requires_validation' AND attempt_count > 0) "
            "OR (status = 'failed' AND completed_at IS NOT NULL AND failure_code IS NOT NULL "
            "AND result_attempt_id IS NULL AND candidate IS NULL AND disposition IS NULL)",
            name="ck_generation_runs_state_data",
        ),
        Index(
            "ix_generation_runs_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
        Index("ix_generation_runs_status_created", "status", "created_at", "id"),
        Index("ix_generation_runs_retry_of", "retry_of_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    paper_blueprint_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    retry_of_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    slot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    blueprint_version: Mapped[str] = mapped_column(String(128), nullable=False)
    blueprint_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    blueprint_slot_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    knowledge_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    historical_question_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    context_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_microusd_per_million_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_microusd_per_million_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation_parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_attempt_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    cost_microusd: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    latency_ms: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    candidate: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
    disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class GenerationAttemptModel(Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "generation_run_id",
            "attempt_number",
            name="uq_generation_attempts_run_number",
        ),
        UniqueConstraint(
            "id",
            "generation_run_id",
            name="uq_generation_attempts_id_run",
        ),
        CheckConstraint(
            f"attempt_number BETWEEN 1 AND {MAX_GENERATION_ATTEMPTS}",
            name="ck_generation_attempts_number",
        ),
        CheckConstraint(
            "provider_idempotency_key = btrim(provider_idempotency_key) AND "
            "length(provider_idempotency_key) BETWEEN 1 AND 128",
            name="ck_generation_attempts_idempotency_key",
        ),
        CheckConstraint(
            f"status IN ({_ATTEMPT_STATES_SQL})",
            name="ck_generation_attempts_status",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "length(failure_code) BETWEEN 1 AND 64)",
            name="ck_generation_attempts_failure_code",
        ),
        CheckConstraint(
            "retry_after_ms IS NULL OR retry_after_ms BETWEEN 0 AND 3600000",
            name="ck_generation_attempts_retry_after",
        ),
        CheckConstraint(
            "(accounting_known AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND "
            "total_tokens = input_tokens + output_tokens AND cost_microusd IS NOT NULL) OR "
            "(NOT accounting_known AND input_tokens IS NULL AND output_tokens IS NULL AND "
            "total_tokens IS NULL AND cost_microusd IS NULL)",
            name="ck_generation_attempts_accounting_presence",
        ),
        CheckConstraint(
            f"(input_tokens IS NULL OR input_tokens BETWEEN 0 AND {MAX_GENERATION_TOKENS}) AND "
            f"(output_tokens IS NULL OR output_tokens BETWEEN 0 AND {MAX_GENERATION_TOKENS}) AND "
            f"(total_tokens IS NULL OR total_tokens BETWEEN 0 AND {MAX_GENERATION_TOKENS}) AND "
            f"(cost_microusd IS NULL OR cost_microusd BETWEEN 0 AND "
            f"{MAX_GENERATION_COST_MICROUSD}) AND "
            f"latency_ms BETWEEN 0 AND {MAX_GENERATION_LATENCY_MS}",
            name="ck_generation_attempts_accounting_bounds",
        ),
        CheckConstraint(
            f"candidate IS NULL OR (jsonb_typeof(candidate) = 'object' AND "
            f"pg_column_size(candidate) <= {MAX_GENERATION_CANDIDATE_BYTES})",
            name="ck_generation_attempts_candidate",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND failure_code IS NULL AND retry_after_ms IS NULL AND "
            "accounting_known AND candidate IS NOT NULL AND "
            "disposition = 'requires_validation') OR "
            "(status = 'failed' AND failure_code IS NOT NULL AND candidate IS NULL AND "
            "disposition IS NULL)",
            name="ck_generation_attempts_state_data",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="ck_generation_attempts_timestamps",
        ),
        Index("ix_generation_attempts_run_number", "generation_run_id", "attempt_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    generation_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_of_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_attempts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_after_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accounting_known: Mapped[bool] = mapped_column(Boolean, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    candidate: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
    disposition: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationJobModel(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_generation_jobs_run_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("generation_run_id", name="uq_generation_jobs_run"),
        CheckConstraint(
            f"status IN ({_JOB_STATES_SQL}) AND version >= 0",
            name="ck_generation_jobs_status_version",
        ),
        CheckConstraint(
            "queue_message_id IS NULL OR (queue_message_id = btrim(queue_message_id) AND "
            "length(queue_message_id) BETWEEN 1 AND 128)",
            name="ck_generation_jobs_message_id",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "length(failure_code) BETWEEN 1 AND 64)",
            name="ck_generation_jobs_failure_code",
        ),
        CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND completed_at IS NULL AND "
            "failure_code IS NULL) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND completed_at IS NULL AND "
            "failure_code IS NULL) OR "
            "(status = 'succeeded' AND claimed_at IS NOT NULL AND completed_at IS NOT NULL AND "
            "completed_at >= claimed_at AND failure_code IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_generation_jobs_state_data",
        ),
        Index("ix_generation_jobs_curriculum_created", "curriculum_version_id", "created_at", "id"),
        Index("ix_generation_jobs_status_created", "status", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    generation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    queue_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
