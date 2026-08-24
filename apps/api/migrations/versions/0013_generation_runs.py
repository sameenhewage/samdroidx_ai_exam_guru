from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013_generation_runs"
down_revision: str | None = "0012_paper_blueprints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_BLUEPRINT_BYTES = 2_097_152
_MAX_SLOT_BYTES = 131_072
_MAX_CONTEXT_BYTES = 262_144
_MAX_CANDIDATE_BYTES = 131_072
_MAX_TOKENS = 30_000_000
_MAX_COST = 3_000_000_000_000
_MAX_LATENCY_MS = 259_200_000
_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_paper_blueprints_id_curriculum",
        "paper_blueprints",
        ["id", "curriculum_version_id"],
    )
    op.execute(
        """
        CREATE FUNCTION generation_uuid_array_valid(candidate jsonb, maximum_items integer)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) > maximum_items
            THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR item #>> '{}' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                        '[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;
            RETURN jsonb_array_length(candidate) = (
                SELECT count(DISTINCT value) FROM jsonb_array_elements(candidate)
            );
        END;
        $$
        """
    )
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("paper_blueprint_id", sa.Uuid(), nullable=False),
        sa.Column("retry_of_run_id", sa.Uuid(), nullable=True),
        sa.Column("slot_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(71), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("blueprint_version", sa.String(128), nullable=False),
        sa.Column("blueprint_snapshot", JSONB(), nullable=False),
        sa.Column("blueprint_slot_snapshot", JSONB(), nullable=False),
        sa.Column("knowledge_chunk_ids", JSONB(), nullable=False),
        sa.Column("historical_question_ids", JSONB(), nullable=False),
        sa.Column("context_snapshot", JSONB(), nullable=False),
        sa.Column("prompt_id", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("retrieval_version", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("pricing_version", sa.String(128), nullable=False),
        sa.Column("input_microusd_per_million_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_microusd_per_million_tokens", sa.BigInteger(), nullable=False),
        sa.Column("generation_parameters", JSONB(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("max_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("result_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("candidate", JSONB(), nullable=True),
        sa.Column("disposition", sa.String(32), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_generation_runs_curriculum_version_id_curriculum_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_generation_runs_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_run_id"],
            ["generation_runs.id"],
            name="fk_generation_runs_retry_of_run_id_generation_runs",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key_hash",
            name="uq_generation_runs_actor_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_generation_runs_id_curriculum",
        ),
        sa.CheckConstraint(
            f"request_fingerprint ~ '{_FINGERPRINT_SQL}'",
            name="ck_generation_runs_request_fingerprint",
        ),
        sa.CheckConstraint(
            f"idempotency_key_hash ~ '{_FINGERPRINT_SQL}'",
            name="ck_generation_runs_idempotency_key_hash",
        ),
        sa.CheckConstraint(
            "slot_id = btrim(slot_id) AND length(slot_id) BETWEEN 1 AND 128",
            name="ck_generation_runs_slot_id",
        ),
        *(
            sa.CheckConstraint(
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
        sa.CheckConstraint(
            "generation_uuid_array_valid(knowledge_chunk_ids, 16) AND "
            "generation_uuid_array_valid(historical_question_ids, 16) AND "
            "jsonb_array_length(knowledge_chunk_ids) + "
            "jsonb_array_length(historical_question_ids) BETWEEN 1 AND 16",
            name="ck_generation_runs_context_ids",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blueprint_snapshot) = 'object' AND "
            f"pg_column_size(blueprint_snapshot) <= {_MAX_BLUEPRINT_BYTES}",
            name="ck_generation_runs_blueprint_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blueprint_slot_snapshot) = 'object' AND "
            f"pg_column_size(blueprint_slot_snapshot) <= {_MAX_SLOT_BYTES} AND "
            "blueprint_slot_snapshot->>'slot_id' = slot_id",
            name="ck_generation_runs_blueprint_slot_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(context_snapshot) = 'object' AND "
            "context_snapshot ?& ARRAY['items', 'trust'] AND "
            "jsonb_typeof(context_snapshot->'items') = 'array' AND "
            "jsonb_array_length(context_snapshot->'items') = "
            "jsonb_array_length(knowledge_chunk_ids) + "
            "jsonb_array_length(historical_question_ids) AND "
            "context_snapshot->>'trust' = 'untrusted_data' AND "
            f"pg_column_size(context_snapshot) <= {_MAX_CONTEXT_BYTES}",
            name="ck_generation_runs_context_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(generation_parameters) = 'object' AND "
            "generation_parameters ?& ARRAY['temperature', 'max_output_tokens', 'seed']",
            name="ck_generation_runs_parameters",
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 3 AND "
            f"max_input_tokens BETWEEN 1 AND {_MAX_TOKENS} AND "
            f"max_output_tokens BETWEEN 1 AND {_MAX_TOKENS} AND "
            f"max_cost_microusd BETWEEN 1 AND {_MAX_COST}",
            name="ck_generation_runs_budgets",
        ),
        sa.CheckConstraint(
            "input_microusd_per_million_tokens BETWEEN 0 AND 100000000000 AND "
            "output_microusd_per_million_tokens BETWEEN 0 AND 100000000000",
            name="ck_generation_runs_pricing",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed') AND version >= 0",
            name="ck_generation_runs_status_version",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 3 AND "
            f"input_tokens BETWEEN 0 AND {_MAX_TOKENS} AND "
            f"output_tokens BETWEEN 0 AND {_MAX_TOKENS} AND "
            "total_tokens = input_tokens + output_tokens AND "
            f"cost_microusd BETWEEN 0 AND {_MAX_COST} AND "
            f"latency_ms BETWEEN 0 AND {_MAX_LATENCY_MS}",
            name="ck_generation_runs_accounting",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "length(failure_code) BETWEEN 1 AND 64)",
            name="ck_generation_runs_failure_code",
        ),
        sa.CheckConstraint(
            "candidate IS NULL OR (jsonb_typeof(candidate) = 'object' AND "
            f"pg_column_size(candidate) <= {_MAX_CANDIDATE_BYTES})",
            name="ck_generation_runs_candidate",
        ),
        sa.CheckConstraint(
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
    )
    op.create_index(
        "ix_generation_runs_curriculum_created",
        "generation_runs",
        ["curriculum_version_id", "created_at", "id"],
    )
    op.create_index(
        "ix_generation_runs_status_created",
        "generation_runs",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_generation_runs_retry_of",
        "generation_runs",
        ["retry_of_run_id"],
    )

    op.create_table(
        "generation_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("retry_of_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("provider_idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("retry_after_ms", sa.Integer(), nullable=True),
        sa.Column("accounting_known", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("candidate", JSONB(), nullable=True),
        sa.Column("disposition", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            name="fk_generation_attempts_generation_run_id_generation_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_attempt_id"],
            ["generation_attempts.id"],
            name="fk_generation_attempts_retry_of_attempt_id_generation_attempts",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "generation_run_id",
            "attempt_number",
            name="uq_generation_attempts_run_number",
        ),
        sa.UniqueConstraint(
            "id",
            "generation_run_id",
            name="uq_generation_attempts_id_run",
        ),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 3",
            name="ck_generation_attempts_number",
        ),
        sa.CheckConstraint(
            "provider_idempotency_key = btrim(provider_idempotency_key) AND "
            "length(provider_idempotency_key) BETWEEN 1 AND 128",
            name="ck_generation_attempts_idempotency_key",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_generation_attempts_status",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "length(failure_code) BETWEEN 1 AND 64)",
            name="ck_generation_attempts_failure_code",
        ),
        sa.CheckConstraint(
            "retry_after_ms IS NULL OR retry_after_ms BETWEEN 0 AND 3600000",
            name="ck_generation_attempts_retry_after",
        ),
        sa.CheckConstraint(
            "(accounting_known AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND "
            "total_tokens = input_tokens + output_tokens AND cost_microusd IS NOT NULL) OR "
            "(NOT accounting_known AND input_tokens IS NULL AND output_tokens IS NULL AND "
            "total_tokens IS NULL AND cost_microusd IS NULL)",
            name="ck_generation_attempts_accounting_presence",
        ),
        sa.CheckConstraint(
            f"(input_tokens IS NULL OR input_tokens BETWEEN 0 AND {_MAX_TOKENS}) AND "
            f"(output_tokens IS NULL OR output_tokens BETWEEN 0 AND {_MAX_TOKENS}) AND "
            f"(total_tokens IS NULL OR total_tokens BETWEEN 0 AND {_MAX_TOKENS}) AND "
            f"(cost_microusd IS NULL OR cost_microusd BETWEEN 0 AND {_MAX_COST}) AND "
            f"latency_ms BETWEEN 0 AND {_MAX_LATENCY_MS}",
            name="ck_generation_attempts_accounting_bounds",
        ),
        sa.CheckConstraint(
            "candidate IS NULL OR (jsonb_typeof(candidate) = 'object' AND "
            f"pg_column_size(candidate) <= {_MAX_CANDIDATE_BYTES})",
            name="ck_generation_attempts_candidate",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND failure_code IS NULL AND retry_after_ms IS NULL AND "
            "accounting_known AND candidate IS NOT NULL AND "
            "disposition = 'requires_validation') OR "
            "(status = 'failed' AND failure_code IS NOT NULL AND candidate IS NULL AND "
            "disposition IS NULL)",
            name="ck_generation_attempts_state_data",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="ck_generation_attempts_timestamps",
        ),
    )
    op.create_index(
        "ix_generation_attempts_run_number",
        "generation_attempts",
        ["generation_run_id", "attempt_number"],
    )
    op.create_foreign_key(
        "fk_generation_runs_result_attempt",
        "generation_runs",
        "generation_attempts",
        ["result_attempt_id", "id"],
        ["id", "generation_run_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queue_message_id", sa.String(128), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_generation_jobs_curriculum_version_id_curriculum_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_generation_jobs_run_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("generation_run_id", name="uq_generation_jobs_run"),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'succeeded', 'failed') AND version >= 0",
            name="ck_generation_jobs_status_version",
        ),
        sa.CheckConstraint(
            "queue_message_id IS NULL OR (queue_message_id = btrim(queue_message_id) AND "
            "length(queue_message_id) BETWEEN 1 AND 128)",
            name="ck_generation_jobs_message_id",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "length(failure_code) BETWEEN 1 AND 64)",
            name="ck_generation_jobs_failure_code",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND completed_at IS NULL AND "
            "failure_code IS NULL) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND completed_at IS NULL AND "
            "failure_code IS NULL) OR "
            "(status = 'succeeded' AND claimed_at IS NOT NULL AND completed_at IS NOT NULL AND "
            "completed_at >= claimed_at AND failure_code IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_generation_jobs_state_data",
        ),
    )
    op.create_index(
        "ix_generation_jobs_curriculum_created",
        "generation_jobs",
        ["curriculum_version_id", "created_at", "id"],
    )
    op.create_index(
        "ix_generation_jobs_status_created",
        "generation_jobs",
        ["status", "created_at", "id"],
    )
    _create_triggers()


def _create_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_generation_run_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status <> 'pending' OR NEW.version <> 0
                OR NEW.started_at IS NOT NULL OR NEW.completed_at IS NOT NULL
                OR NEW.failure_code IS NOT NULL OR NEW.result_attempt_id IS NOT NULL
                OR NEW.candidate IS NOT NULL OR NEW.disposition IS NOT NULL
                OR NEW.attempt_count <> 0 OR NEW.input_tokens <> 0 OR NEW.output_tokens <> 0
                OR NEW.total_tokens <> 0 OR NEW.cost_microusd <> 0 OR NEW.latency_ms <> 0
            THEN
                RAISE EXCEPTION 'generation runs must start pending at version zero'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_generation_run_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            persisted_attempt_count bigint;
            persisted_input_tokens bigint;
            persisted_output_tokens bigint;
            persisted_total_tokens bigint;
            persisted_cost_microusd bigint;
            persisted_latency_ms bigint;
            result_status text;
            result_candidate jsonb;
            result_disposition text;
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.paper_blueprint_id IS DISTINCT FROM OLD.paper_blueprint_id
                OR NEW.retry_of_run_id IS DISTINCT FROM OLD.retry_of_run_id
                OR NEW.slot_id IS DISTINCT FROM OLD.slot_id
                OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
                OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
                OR NEW.blueprint_version IS DISTINCT FROM OLD.blueprint_version
                OR NEW.blueprint_snapshot IS DISTINCT FROM OLD.blueprint_snapshot
                OR NEW.blueprint_slot_snapshot IS DISTINCT FROM OLD.blueprint_slot_snapshot
                OR NEW.knowledge_chunk_ids IS DISTINCT FROM OLD.knowledge_chunk_ids
                OR NEW.historical_question_ids IS DISTINCT FROM OLD.historical_question_ids
                OR NEW.context_snapshot IS DISTINCT FROM OLD.context_snapshot
                OR NEW.prompt_id IS DISTINCT FROM OLD.prompt_id
                OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
                OR NEW.provider IS DISTINCT FROM OLD.provider
                OR NEW.provider_version IS DISTINCT FROM OLD.provider_version
                OR NEW.model IS DISTINCT FROM OLD.model
                OR NEW.model_version IS DISTINCT FROM OLD.model_version
                OR NEW.retrieval_version IS DISTINCT FROM OLD.retrieval_version
                OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
                OR NEW.pricing_version IS DISTINCT FROM OLD.pricing_version
                OR NEW.input_microusd_per_million_tokens IS DISTINCT FROM
                    OLD.input_microusd_per_million_tokens
                OR NEW.output_microusd_per_million_tokens IS DISTINCT FROM
                    OLD.output_microusd_per_million_tokens
                OR NEW.generation_parameters IS DISTINCT FROM OLD.generation_parameters
                OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts
                OR NEW.max_input_tokens IS DISTINCT FROM OLD.max_input_tokens
                OR NEW.max_output_tokens IS DISTINCT FROM OLD.max_output_tokens
                OR NEW.max_cost_microusd IS DISTINCT FROM OLD.max_cost_microusd
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'generation request snapshots are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'generation run version must increment by one'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                (OLD.status = 'pending' AND NEW.status IN ('running', 'failed'))
                OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed'))
            ) THEN
                RAISE EXCEPTION 'invalid generation run transition'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status IN ('succeeded', 'failed') THEN
                SELECT
                    count(*),
                    coalesce(sum(input_tokens), 0),
                    coalesce(sum(output_tokens), 0),
                    coalesce(sum(total_tokens), 0),
                    coalesce(sum(cost_microusd), 0),
                    coalesce(sum(latency_ms), 0)
                INTO
                    persisted_attempt_count,
                    persisted_input_tokens,
                    persisted_output_tokens,
                    persisted_total_tokens,
                    persisted_cost_microusd,
                    persisted_latency_ms
                FROM generation_attempts
                WHERE generation_run_id = NEW.id;
                IF NEW.attempt_count <> persisted_attempt_count
                    OR NEW.input_tokens <> persisted_input_tokens
                    OR NEW.output_tokens <> persisted_output_tokens
                    OR NEW.total_tokens <> persisted_total_tokens
                    OR NEW.cost_microusd <> persisted_cost_microusd
                    OR NEW.latency_ms <> persisted_latency_ms
                THEN
                    RAISE EXCEPTION 'generation run accounting does not match attempts'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF NEW.status = 'succeeded' THEN
                SELECT status, candidate, disposition
                INTO result_status, result_candidate, result_disposition
                FROM generation_attempts
                WHERE id = NEW.result_attempt_id AND generation_run_id = NEW.id;
                IF result_status IS DISTINCT FROM 'succeeded'
                    OR result_candidate IS DISTINCT FROM NEW.candidate
                    OR result_disposition IS DISTINCT FROM 'requires_validation'
                    OR NEW.disposition IS DISTINCT FROM 'requires_validation'
                THEN
                    RAISE EXCEPTION 'generation result must reference a validation-required attempt'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_generation_run_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'generation runs are durable and cannot be deleted'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_generation_run_insert_trigger
        BEFORE INSERT ON generation_runs
        FOR EACH ROW EXECUTE FUNCTION enforce_generation_run_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_generation_run_update_trigger
        BEFORE UPDATE ON generation_runs
        FOR EACH ROW EXECUTE FUNCTION enforce_generation_run_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_generation_run_delete_trigger
        BEFORE DELETE ON generation_runs
        FOR EACH ROW EXECUTE FUNCTION reject_generation_run_delete()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_generation_attempt_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            run_status text;
            prior_count bigint;
            predecessor_run_id uuid;
            predecessor_number integer;
            predecessor_key text;
        BEGIN
            SELECT status INTO run_status FROM generation_runs WHERE id = NEW.generation_run_id;
            IF run_status IS DISTINCT FROM 'running' THEN
                RAISE EXCEPTION 'attempts can only complete for a running generation run'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*) INTO prior_count
            FROM generation_attempts WHERE generation_run_id = NEW.generation_run_id;
            IF NEW.attempt_number <> prior_count + 1 THEN
                RAISE EXCEPTION 'generation attempt numbers must be contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.attempt_number = 1 THEN
                IF NEW.retry_of_attempt_id IS NOT NULL THEN
                    RAISE EXCEPTION 'first generation attempt cannot have a predecessor'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT generation_run_id, attempt_number, provider_idempotency_key
                INTO predecessor_run_id, predecessor_number, predecessor_key
                FROM generation_attempts WHERE id = NEW.retry_of_attempt_id;
                IF predecessor_run_id IS DISTINCT FROM NEW.generation_run_id
                    OR predecessor_number IS DISTINCT FROM NEW.attempt_number - 1
                    OR predecessor_key IS DISTINCT FROM NEW.provider_idempotency_key
                THEN
                    RAISE EXCEPTION 'generation retry lineage is invalid'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_generation_attempt_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'completed generation attempts are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_generation_attempt_insert_trigger
        BEFORE INSERT ON generation_attempts
        FOR EACH ROW EXECUTE FUNCTION enforce_generation_attempt_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_generation_attempt_mutation_trigger
        BEFORE UPDATE OR DELETE ON generation_attempts
        FOR EACH ROW EXECUTE FUNCTION reject_generation_attempt_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_generation_job_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status <> 'queued' OR NEW.version <> 0
                OR NEW.queue_message_id IS NOT NULL OR NEW.failure_code IS NOT NULL
                OR NEW.claimed_at IS NOT NULL OR NEW.completed_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'generation jobs must start queued at version zero'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_generation_job_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.generation_run_id IS DISTINCT FROM OLD.generation_run_id
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'generation job identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'generation job version must increment by one'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = OLD.status THEN
                IF OLD.status <> 'queued'
                    OR OLD.queue_message_id IS NOT NULL
                    OR NEW.queue_message_id IS NULL
                    OR NEW.failure_code IS DISTINCT FROM OLD.failure_code
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
                THEN
                    RAISE EXCEPTION 'invalid generation job metadata update'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.queue_message_id IS DISTINCT FROM OLD.queue_message_id
                OR NOT (
                    (OLD.status = 'queued' AND NEW.status IN ('claimed', 'failed'))
                    OR (OLD.status = 'claimed' AND NEW.status IN ('succeeded', 'failed'))
                )
            THEN
                RAISE EXCEPTION 'invalid generation job transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_generation_job_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'generation jobs are durable and cannot be deleted'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_generation_job_insert_trigger
        BEFORE INSERT ON generation_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_generation_job_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_generation_job_update_trigger
        BEFORE UPDATE ON generation_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_generation_job_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_generation_job_delete_trigger
        BEFORE DELETE ON generation_jobs
        FOR EACH ROW EXECUTE FUNCTION reject_generation_job_delete()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reject_generation_job_delete_trigger ON generation_jobs")
    op.execute("DROP TRIGGER enforce_generation_job_update_trigger ON generation_jobs")
    op.execute("DROP TRIGGER enforce_generation_job_insert_trigger ON generation_jobs")
    op.execute("DROP FUNCTION reject_generation_job_delete()")
    op.execute("DROP FUNCTION enforce_generation_job_update()")
    op.execute("DROP FUNCTION enforce_generation_job_insert()")
    op.execute("DROP TRIGGER reject_generation_attempt_mutation_trigger ON generation_attempts")
    op.execute("DROP TRIGGER enforce_generation_attempt_insert_trigger ON generation_attempts")
    op.execute("DROP FUNCTION reject_generation_attempt_mutation()")
    op.execute("DROP FUNCTION enforce_generation_attempt_insert()")
    op.execute("DROP TRIGGER reject_generation_run_delete_trigger ON generation_runs")
    op.execute("DROP TRIGGER enforce_generation_run_update_trigger ON generation_runs")
    op.execute("DROP TRIGGER enforce_generation_run_insert_trigger ON generation_runs")
    op.execute("DROP FUNCTION reject_generation_run_delete()")
    op.execute("DROP FUNCTION enforce_generation_run_update()")
    op.execute("DROP FUNCTION enforce_generation_run_insert()")
    op.drop_index("ix_generation_jobs_status_created", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_curriculum_created", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_constraint(
        "fk_generation_runs_result_attempt",
        "generation_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_generation_attempts_run_number", table_name="generation_attempts")
    op.drop_table("generation_attempts")
    op.drop_index("ix_generation_runs_retry_of", table_name="generation_runs")
    op.drop_index("ix_generation_runs_status_created", table_name="generation_runs")
    op.drop_index("ix_generation_runs_curriculum_created", table_name="generation_runs")
    op.drop_table("generation_runs")
    op.execute("DROP FUNCTION generation_uuid_array_valid(jsonb, integer)")
    op.drop_constraint(
        "uq_paper_blueprints_id_curriculum",
        "paper_blueprints",
        type_="unique",
    )
