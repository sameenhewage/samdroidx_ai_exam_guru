from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018_embedding_jobs"
down_revision: str | None = "0017_ocr_worker_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


def upgrade() -> None:
    _create_uuid_array_validator()
    _create_embedding_jobs_table()
    _create_embedding_job_lifecycle_triggers()


def _create_uuid_array_validator() -> None:
    op.execute(
        """
        CREATE FUNCTION embedding_job_uuid_array_valid(candidate jsonb, maximum_items integer)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
        DECLARE
            item_text text;
            item_uuid uuid;
            previous_uuid uuid := NULL;
        BEGIN
            IF maximum_items < 0
                OR jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) > maximum_items
            THEN
                RETURN FALSE;
            END IF;

            FOR item_text IN
                SELECT value #>> '{}'
                FROM jsonb_array_elements(candidate)
            LOOP
                IF item_text IS NULL
                    OR item_text !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                        '[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN
                    RETURN FALSE;
                END IF;
                item_uuid := item_text::uuid;
                IF item_uuid::text <> item_text
                    OR (previous_uuid IS NOT NULL AND item_uuid <= previous_uuid)
                THEN
                    RETURN FALSE;
                END IF;
                previous_uuid := item_uuid;
            END LOOP;
            RETURN TRUE;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN FALSE;
        END;
        $$
        """
    )


def _create_embedding_jobs_table() -> None:
    op.create_table(
        "embedding_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("retry_of_job_id", sa.Uuid(), nullable=True),
        sa.Column("historical_question_ids", JSONB(), nullable=False),
        sa.Column("knowledge_chunk_ids", JSONB(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(71), nullable=False),
        sa.Column("request_fingerprint", sa.String(71), nullable=False),
        sa.Column("source_fingerprint", sa.String(71), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_version", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queue_message_id", sa.String(128), nullable=True),
        sa.Column("requested_count", sa.Integer(), nullable=False),
        sa.Column("embedded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deduplicated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_embedding_jobs_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_job_id", "curriculum_version_id"],
            ["embedding_jobs.id", "embedding_jobs.curriculum_version_id"],
            name="fk_embedding_jobs_retry_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_embedding_jobs_id_curriculum",
        ),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key_hash",
            name="uq_embedding_jobs_actor_idempotency",
        ),
        sa.CheckConstraint(
            "retry_of_job_id IS NULL OR retry_of_job_id <> id",
            name="ck_embedding_jobs_retry_not_self",
        ),
        sa.CheckConstraint(
            "embedding_job_uuid_array_valid(historical_question_ids, 100) AND "
            "embedding_job_uuid_array_valid(knowledge_chunk_ids, 100) AND "
            "jsonb_array_length(historical_question_ids) + "
            "jsonb_array_length(knowledge_chunk_ids) BETWEEN 1 AND 100",
            name="ck_embedding_jobs_record_ids",
        ),
        sa.CheckConstraint(
            f"idempotency_key_hash ~ '{_FINGERPRINT_SQL}' AND "
            f"request_fingerprint ~ '{_FINGERPRINT_SQL}' AND "
            f"source_fingerprint ~ '{_FINGERPRINT_SQL}'",
            name="ck_embedding_jobs_fingerprints",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} = btrim({column_name}) AND "
                f"char_length({column_name}) BETWEEN 1 AND {maximum} AND "
                f"{column_name} !~ '[[:space:][:cntrl:]]'",
                name=f"ck_embedding_jobs_{column_name}",
            )
            for column_name, maximum in (
                ("provider", 64),
                ("model", 128),
                ("embedding_version", 64),
                ("config_fingerprint", 128),
            )
        ),
        sa.CheckConstraint(
            "dimension BETWEEN 1 AND 4096",
            name="ck_embedding_jobs_dimension",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'succeeded', 'failed') AND version >= 0",
            name="ck_embedding_jobs_status_version",
        ),
        sa.CheckConstraint(
            "queue_message_id IS NULL OR (queue_message_id = btrim(queue_message_id) AND "
            "char_length(queue_message_id) BETWEEN 1 AND 128 AND "
            "queue_message_id !~ '[[:space:][:cntrl:]]')",
            name="ck_embedding_jobs_queue_message_id",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_embedding_jobs_failure_code",
        ),
        sa.CheckConstraint(
            "requested_count = jsonb_array_length(historical_question_ids) + "
            "jsonb_array_length(knowledge_chunk_ids) AND "
            "requested_count BETWEEN 1 AND 100 AND "
            "embedded_count BETWEEN 0 AND requested_count AND "
            "deduplicated_count BETWEEN 0 AND requested_count AND "
            "embedded_count + deduplicated_count <= requested_count",
            name="ck_embedding_jobs_counts",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND "
            "(claimed_at IS NULL OR claimed_at >= created_at) AND "
            "(completed_at IS NULL OR (claimed_at IS NOT NULL AND completed_at >= claimed_at))",
            name="ck_embedding_jobs_timestamps",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND completed_at IS NULL AND "
            "failure_code IS NULL AND embedded_count = 0 AND deduplicated_count = 0) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND completed_at IS NULL AND "
            "failure_code IS NULL) OR "
            "(status = 'succeeded' AND claimed_at IS NOT NULL AND completed_at IS NOT NULL AND "
            "failure_code IS NULL AND embedded_count + deduplicated_count = requested_count) OR "
            "(status = 'failed' AND claimed_at IS NOT NULL AND completed_at IS NOT NULL AND "
            "failure_code IS NOT NULL)",
            name="ck_embedding_jobs_state_data",
        ),
    )
    op.create_index(
        "ix_embedding_jobs_curriculum_created",
        "embedding_jobs",
        ["curriculum_version_id", "created_at", "id"],
    )
    op.create_index(
        "ix_embedding_jobs_curriculum_status_created",
        "embedding_jobs",
        ["curriculum_version_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_embedding_jobs_status_created",
        "embedding_jobs",
        ["status", "created_at", "id"],
    )
    op.create_index("ix_embedding_jobs_retry_of", "embedding_jobs", ["retry_of_job_id"])


def _create_embedding_job_lifecycle_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_embedding_job_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status <> 'queued'
                OR NEW.version <> 0
                OR NEW.queue_message_id IS NOT NULL
                OR NEW.embedded_count <> 0
                OR NEW.deduplicated_count <> 0
                OR NEW.failure_code IS NOT NULL
                OR NEW.claimed_at IS NOT NULL
                OR NEW.completed_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'embedding jobs must start as unclaimed queued version zero'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(NEW.historical_question_ids) AS requested(id)
                LEFT JOIN historical_questions AS question
                    ON question.id = requested.id::uuid
                    AND question.curriculum_version_id = NEW.curriculum_version_id
                    AND question.review_state = 'reviewed'
                WHERE question.id IS NULL
            ) OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(NEW.knowledge_chunk_ids) AS requested(id)
                LEFT JOIN knowledge_chunks AS chunk
                    ON chunk.id = requested.id::uuid
                    AND chunk.curriculum_version_id = NEW.curriculum_version_id
                    AND chunk.review_state = 'reviewed'
                WHERE chunk.id IS NULL
            ) THEN
                RAISE EXCEPTION 'embedding job records must be reviewed and curriculum scoped'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_embedding_job_insert_trigger
        BEFORE INSERT ON embedding_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_embedding_job_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_embedding_job_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.retry_of_job_id IS DISTINCT FROM OLD.retry_of_job_id
                OR NEW.historical_question_ids <> OLD.historical_question_ids
                OR NEW.knowledge_chunk_ids <> OLD.knowledge_chunk_ids
                OR NEW.idempotency_key_hash <> OLD.idempotency_key_hash
                OR NEW.request_fingerprint <> OLD.request_fingerprint
                OR NEW.source_fingerprint <> OLD.source_fingerprint
                OR NEW.provider <> OLD.provider
                OR NEW.model <> OLD.model
                OR NEW.dimension <> OLD.dimension
                OR NEW.embedding_version <> OLD.embedding_version
                OR NEW.config_fingerprint <> OLD.config_fingerprint
                OR NEW.requested_count <> OLD.requested_count
                OR NEW.created_by <> OLD.created_by
                OR NEW.created_at <> OLD.created_at
            THEN
                RAISE EXCEPTION 'embedding job request and configuration are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status IN ('succeeded', 'failed') THEN
                RAISE EXCEPTION 'terminal embedding jobs are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version <> OLD.version + 1 OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'embedding job version must increment exactly once'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.queue_message_id IS NOT NULL
                AND NEW.queue_message_id IS DISTINCT FROM OLD.queue_message_id
            THEN
                RAISE EXCEPTION 'embedding job queue identity is immutable once attached'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.status = 'queued' AND NEW.status = 'queued' THEN
                IF OLD.queue_message_id IS NOT NULL
                    OR NEW.queue_message_id IS NULL
                    OR NEW.claimed_at IS NOT NULL
                    OR NEW.completed_at IS NOT NULL
                    OR NEW.failure_code IS NOT NULL
                    OR NEW.embedded_count <> 0
                    OR NEW.deduplicated_count <> 0
                THEN
                    RAISE EXCEPTION 'queued embedding jobs only permit message attachment'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status = 'queued' AND NEW.status = 'claimed' THEN
                IF NEW.queue_message_id IS DISTINCT FROM OLD.queue_message_id
                    OR NEW.claimed_at IS NULL
                    OR NEW.completed_at IS NOT NULL
                    OR NEW.failure_code IS NOT NULL
                    OR NEW.embedded_count <> 0
                    OR NEW.deduplicated_count <> 0
                THEN
                    RAISE EXCEPTION 'invalid embedding job claim'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status = 'claimed' AND NEW.status = 'claimed' THEN
                IF NEW.queue_message_id IS DISTINCT FROM OLD.queue_message_id
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.completed_at IS NOT NULL
                    OR NEW.failure_code IS NOT NULL
                    OR NEW.embedded_count < OLD.embedded_count
                    OR NEW.deduplicated_count < OLD.deduplicated_count
                    OR NEW.embedded_count + NEW.deduplicated_count
                        <> OLD.embedded_count + OLD.deduplicated_count + 1
                THEN
                    RAISE EXCEPTION 'invalid embedding job progress update'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status = 'claimed' AND NEW.status IN ('succeeded', 'failed') THEN
                IF NEW.queue_message_id IS DISTINCT FROM OLD.queue_message_id
                    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                    OR NEW.completed_at IS NULL
                    OR NEW.embedded_count <> OLD.embedded_count
                    OR NEW.deduplicated_count <> OLD.deduplicated_count
                THEN
                    RAISE EXCEPTION 'invalid embedding job completion'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'invalid embedding job state transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_embedding_job_update_trigger
        BEFORE UPDATE ON embedding_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_embedding_job_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_embedding_job_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'embedding jobs are append-preserved records'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_embedding_job_delete_trigger
        BEFORE DELETE ON embedding_jobs
        FOR EACH ROW EXECUTE FUNCTION reject_embedding_job_delete()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reject_embedding_job_delete_trigger ON embedding_jobs")
    op.execute("DROP FUNCTION reject_embedding_job_delete()")
    op.execute("DROP TRIGGER enforce_embedding_job_update_trigger ON embedding_jobs")
    op.execute("DROP FUNCTION enforce_embedding_job_update()")
    op.execute("DROP TRIGGER enforce_embedding_job_insert_trigger ON embedding_jobs")
    op.execute("DROP FUNCTION enforce_embedding_job_insert()")
    op.drop_index("ix_embedding_jobs_retry_of", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_status_created", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_curriculum_status_created", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_curriculum_created", table_name="embedding_jobs")
    op.drop_table("embedding_jobs")
    op.execute("DROP FUNCTION embedding_job_uuid_array_valid(jsonb, integer)")
