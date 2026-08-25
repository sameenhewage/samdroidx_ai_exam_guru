from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

MAX_PROVIDER_JOB_RETRY_DEPTH = 3

revision: str = "0022_provider_job_retry_depth"
down_revision: str | None = "0021_storage_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("retry_depth", sa.Integer(), nullable=True),
    )
    op.add_column(
        "embedding_jobs",
        sa.Column("retry_depth", sa.Integer(), nullable=True),
    )

    op.execute("ALTER TABLE generation_runs DISABLE TRIGGER enforce_generation_run_update_trigger")
    op.execute("ALTER TABLE embedding_jobs DISABLE TRIGGER enforce_embedding_job_update_trigger")
    _backfill_retry_depth("generation_runs", "retry_of_run_id")
    _backfill_retry_depth("embedding_jobs", "retry_of_job_id")
    op.execute("ALTER TABLE generation_runs ENABLE TRIGGER enforce_generation_run_update_trigger")
    op.execute("ALTER TABLE embedding_jobs ENABLE TRIGGER enforce_embedding_job_update_trigger")

    for table_name in ("generation_runs", "embedding_jobs"):
        op.alter_column(
            table_name,
            "retry_depth",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="0",
        )
        op.create_check_constraint(
            f"ck_{table_name}_retry_depth",
            table_name,
            f"retry_depth BETWEEN 0 AND {MAX_PROVIDER_JOB_RETRY_DEPTH}",
        )

    op.drop_index("ix_generation_runs_retry_of", table_name="generation_runs")
    op.create_index(
        "uq_generation_runs_retry_of",
        "generation_runs",
        ["retry_of_run_id"],
        unique=True,
        postgresql_where=sa.text("retry_of_run_id IS NOT NULL"),
    )
    op.drop_index("ix_embedding_jobs_retry_of", table_name="embedding_jobs")
    op.create_index(
        "uq_embedding_jobs_retry_of",
        "embedding_jobs",
        ["retry_of_job_id"],
        unique=True,
        postgresql_where=sa.text("retry_of_job_id IS NOT NULL"),
    )

    _create_generation_retry_triggers()
    _create_embedding_retry_triggers()


def _backfill_retry_depth(table_name: str, predecessor_column: str) -> None:
    op.execute(
        f"""
        WITH RECURSIVE retry_lineage AS (
            SELECT
                root.id,
                0::integer AS retry_depth,
                ARRAY[root.id]::uuid[] AS visited
            FROM {table_name} AS root
            WHERE root.{predecessor_column} IS NULL

            UNION ALL

            SELECT
                child.id,
                parent.retry_depth + 1,
                parent.visited || child.id
            FROM {table_name} AS child
            JOIN retry_lineage AS parent
                ON child.{predecessor_column} = parent.id
            WHERE NOT child.id = ANY(parent.visited)
        )
        UPDATE {table_name} AS target
        SET retry_depth = retry_lineage.retry_depth
        FROM retry_lineage
        WHERE target.id = retry_lineage.id
        """  # noqa: S608 - table and column names are migration-owned constants
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM {table_name} AS child
                LEFT JOIN {table_name} AS predecessor
                    ON predecessor.id = child.{predecessor_column}
                WHERE child.{predecessor_column} IS NOT NULL
                    AND predecessor.id IS NULL
            ) THEN
                RAISE EXCEPTION '{table_name} retry lineage contains an orphan'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (SELECT 1 FROM {table_name} WHERE retry_depth IS NULL) THEN
                RAISE EXCEPTION '{table_name} retry lineage contains a cycle or unreachable row'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM {table_name}
                WHERE retry_depth > {MAX_PROVIDER_JOB_RETRY_DEPTH}
            ) THEN
                RAISE EXCEPTION '{table_name} retry lineage exceeds the provider-job retry limit'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """  # noqa: S608 - table and column names are migration-owned constants
    )


def _create_generation_retry_triggers() -> None:
    op.execute(
        f"""
        CREATE FUNCTION enforce_generation_run_retry_lineage_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            predecessor generation_runs%ROWTYPE;
        BEGIN
            IF NEW.retry_of_run_id IS NULL THEN
                IF NEW.retry_depth <> 0 THEN
                    RAISE EXCEPTION 'root generation runs must have retry depth zero'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            SELECT * INTO predecessor
            FROM generation_runs
            WHERE id = NEW.retry_of_run_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'generation retry predecessor does not exist'
                    USING ERRCODE = '23514';
            END IF;
            IF predecessor.status IS DISTINCT FROM 'failed' THEN
                RAISE EXCEPTION 'generation retry predecessor must be failed'
                    USING ERRCODE = '23514';
            END IF;
            IF predecessor.retry_depth >= {MAX_PROVIDER_JOB_RETRY_DEPTH}
                OR NEW.retry_depth IS DISTINCT FROM predecessor.retry_depth + 1
            THEN
                RAISE EXCEPTION 'generation retry depth is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.curriculum_version_id IS DISTINCT FROM predecessor.curriculum_version_id
                OR NEW.paper_blueprint_id IS DISTINCT FROM predecessor.paper_blueprint_id
                OR NEW.slot_id IS DISTINCT FROM predecessor.slot_id
                OR NEW.request_fingerprint IS DISTINCT FROM predecessor.request_fingerprint
                OR NEW.blueprint_version IS DISTINCT FROM predecessor.blueprint_version
                OR NEW.blueprint_snapshot IS DISTINCT FROM predecessor.blueprint_snapshot
                OR NEW.blueprint_slot_snapshot IS DISTINCT FROM predecessor.blueprint_slot_snapshot
                OR NEW.knowledge_chunk_ids IS DISTINCT FROM predecessor.knowledge_chunk_ids
                OR NEW.historical_question_ids IS DISTINCT FROM
                    predecessor.historical_question_ids
                OR NEW.context_snapshot IS DISTINCT FROM predecessor.context_snapshot
                OR NEW.prompt_id IS DISTINCT FROM predecessor.prompt_id
                OR NEW.prompt_version IS DISTINCT FROM predecessor.prompt_version
                OR NEW.provider IS DISTINCT FROM predecessor.provider
                OR NEW.provider_version IS DISTINCT FROM predecessor.provider_version
                OR NEW.model IS DISTINCT FROM predecessor.model
                OR NEW.model_version IS DISTINCT FROM predecessor.model_version
                OR NEW.retrieval_version IS DISTINCT FROM predecessor.retrieval_version
                OR NEW.schema_version IS DISTINCT FROM predecessor.schema_version
                OR NEW.pricing_version IS DISTINCT FROM predecessor.pricing_version
                OR NEW.input_microusd_per_million_tokens IS DISTINCT FROM
                    predecessor.input_microusd_per_million_tokens
                OR NEW.output_microusd_per_million_tokens IS DISTINCT FROM
                    predecessor.output_microusd_per_million_tokens
                OR NEW.generation_parameters IS DISTINCT FROM predecessor.generation_parameters
                OR NEW.max_attempts IS DISTINCT FROM predecessor.max_attempts
                OR NEW.max_input_tokens IS DISTINCT FROM predecessor.max_input_tokens
                OR NEW.max_output_tokens IS DISTINCT FROM predecessor.max_output_tokens
                OR NEW.max_cost_microusd IS DISTINCT FROM predecessor.max_cost_microusd
            THEN
                RAISE EXCEPTION 'generation retry request snapshot is incompatible'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """  # noqa: S608 - retry limit is a first-party integer constant
    )
    op.execute(
        """
        CREATE TRIGGER enforce_generation_run_retry_lineage_insert_trigger
        BEFORE INSERT ON generation_runs
        FOR EACH ROW EXECUTE FUNCTION enforce_generation_run_retry_lineage_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_generation_run_retry_depth_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.retry_depth IS DISTINCT FROM OLD.retry_depth THEN
                RAISE EXCEPTION 'generation retry depth is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_generation_run_retry_depth_update_trigger
        BEFORE UPDATE ON generation_runs
        FOR EACH ROW EXECUTE FUNCTION reject_generation_run_retry_depth_update()
        """
    )


def _create_embedding_retry_triggers() -> None:
    op.execute(
        f"""
        CREATE FUNCTION enforce_embedding_job_retry_lineage_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            predecessor embedding_jobs%ROWTYPE;
        BEGIN
            IF NEW.retry_of_job_id IS NULL THEN
                IF NEW.retry_depth <> 0 THEN
                    RAISE EXCEPTION 'root embedding jobs must have retry depth zero'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            SELECT * INTO predecessor
            FROM embedding_jobs
            WHERE id = NEW.retry_of_job_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'embedding retry predecessor does not exist'
                    USING ERRCODE = '23514';
            END IF;
            IF predecessor.status IS DISTINCT FROM 'failed' THEN
                RAISE EXCEPTION 'embedding retry predecessor must be failed'
                    USING ERRCODE = '23514';
            END IF;
            IF predecessor.retry_depth >= {MAX_PROVIDER_JOB_RETRY_DEPTH}
                OR NEW.retry_depth IS DISTINCT FROM predecessor.retry_depth + 1
            THEN
                RAISE EXCEPTION 'embedding retry depth is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.curriculum_version_id IS DISTINCT FROM predecessor.curriculum_version_id
                OR NEW.created_by IS DISTINCT FROM predecessor.created_by
                OR NEW.request_fingerprint IS DISTINCT FROM predecessor.request_fingerprint
                OR NEW.historical_question_ids IS DISTINCT FROM
                    predecessor.historical_question_ids
                OR NEW.knowledge_chunk_ids IS DISTINCT FROM predecessor.knowledge_chunk_ids
                OR NEW.source_fingerprint IS DISTINCT FROM predecessor.source_fingerprint
                OR NEW.provider IS DISTINCT FROM predecessor.provider
                OR NEW.model IS DISTINCT FROM predecessor.model
                OR NEW.dimension IS DISTINCT FROM predecessor.dimension
                OR NEW.embedding_version IS DISTINCT FROM predecessor.embedding_version
                OR NEW.config_fingerprint IS DISTINCT FROM predecessor.config_fingerprint
                OR NEW.requested_count IS DISTINCT FROM predecessor.requested_count
            THEN
                RAISE EXCEPTION 'embedding retry request snapshot is incompatible'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """  # noqa: S608 - retry limit is a first-party integer constant
    )
    op.execute(
        """
        CREATE TRIGGER enforce_embedding_job_retry_lineage_insert_trigger
        BEFORE INSERT ON embedding_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_embedding_job_retry_lineage_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_embedding_job_retry_depth_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.retry_depth IS DISTINCT FROM OLD.retry_depth THEN
                RAISE EXCEPTION 'embedding retry depth is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_embedding_job_retry_depth_update_trigger
        BEFORE UPDATE ON embedding_jobs
        FOR EACH ROW EXECUTE FUNCTION reject_embedding_job_retry_depth_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reject_embedding_job_retry_depth_update_trigger ON embedding_jobs")
    op.execute("DROP FUNCTION reject_embedding_job_retry_depth_update()")
    op.execute("DROP TRIGGER enforce_embedding_job_retry_lineage_insert_trigger ON embedding_jobs")
    op.execute("DROP FUNCTION enforce_embedding_job_retry_lineage_insert()")
    op.execute("DROP TRIGGER reject_generation_run_retry_depth_update_trigger ON generation_runs")
    op.execute("DROP FUNCTION reject_generation_run_retry_depth_update()")
    op.execute(
        "DROP TRIGGER enforce_generation_run_retry_lineage_insert_trigger ON generation_runs"
    )
    op.execute("DROP FUNCTION enforce_generation_run_retry_lineage_insert()")

    op.drop_index("uq_embedding_jobs_retry_of", table_name="embedding_jobs")
    op.create_index(
        "ix_embedding_jobs_retry_of",
        "embedding_jobs",
        ["retry_of_job_id"],
    )
    op.drop_index("uq_generation_runs_retry_of", table_name="generation_runs")
    op.create_index(
        "ix_generation_runs_retry_of",
        "generation_runs",
        ["retry_of_run_id"],
    )

    for table_name in ("embedding_jobs", "generation_runs"):
        op.drop_constraint(
            f"ck_{table_name}_retry_depth",
            table_name,
            type_="check",
        )
        op.drop_column(table_name, "retry_depth")
