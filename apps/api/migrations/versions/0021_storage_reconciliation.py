from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_storage_reconciliation"
down_revision: str | None = "0020_restore_safe_json"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storage_reconciliation_state",
        sa.Column("singleton_id", sa.SmallInteger(), primary_key=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("continuation_cursor", sa.String(length=2048), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "singleton_id = 1",
            name="ck_storage_reconciliation_state_singleton",
        ),
        sa.CheckConstraint(
            "continuation_cursor IS NULL OR "
            "(char_length(continuation_cursor) BETWEEN 1 AND 2048 "
            "AND continuation_cursor !~ '[[:cntrl:]]')",
            name="ck_storage_reconciliation_state_cursor",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND ((last_started_at IS NULL "
            "AND last_completed_at IS NULL AND continuation_cursor IS NULL) OR "
            "(last_started_at IS NOT NULL AND last_completed_at IS NOT NULL "
            "AND last_completed_at >= last_started_at))) OR "
            "(lease_token IS NOT NULL AND last_started_at IS NOT NULL "
            "AND (last_completed_at IS NULL OR last_completed_at <= last_started_at))",
            name="ck_storage_reconciliation_state_timestamps",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND lease_acquired_at = last_started_at "
            "AND lease_expires_at > lease_acquired_at)",
            name="ck_storage_reconciliation_state_lease_shape",
        ),
    )
    op.execute("INSERT INTO storage_reconciliation_state (singleton_id) VALUES (1)")

    op.create_table(
        "storage_reconciliation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("apply_tags", sa.Boolean(), nullable=False),
        sa.Column("grace_seconds", sa.Integer(), nullable=False),
        sa.Column("max_objects", sa.Integer(), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("referenced_count", sa.Integer(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("resolved_count", sa.Integer(), nullable=False),
        sa.Column("tagged_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_storage_reconciliation_run_status",
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name="ck_storage_reconciliation_run_timestamps",
        ),
        sa.CheckConstraint(
            "grace_seconds BETWEEN 3600 AND 31536000",
            name="ck_storage_reconciliation_run_grace",
        ),
        sa.CheckConstraint(
            "max_objects BETWEEN 1 AND 10000",
            name="ck_storage_reconciliation_run_max_objects",
        ),
        sa.CheckConstraint(
            "scanned_count >= 0 AND referenced_count >= 0 AND candidate_count >= 0 "
            "AND resolved_count >= 0 AND tagged_count >= 0 AND failure_count >= 0 "
            "AND referenced_count <= scanned_count "
            "AND candidate_count + referenced_count <= scanned_count "
            "AND resolved_count <= referenced_count "
            "AND tagged_count <= candidate_count + referenced_count "
            "AND failure_count <= candidate_count + referenced_count + 1",
            name="ck_storage_reconciliation_run_counts",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND failure_code IS NULL) OR "
            "(status = 'failed' AND failure_code IS NOT NULL "
            "AND failure_count > 0 AND truncated)",
            name="ck_storage_reconciliation_run_outcome",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) "
            "AND char_length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code ~ '^[a-z][a-z0-9_.-]*$')",
            name="ck_storage_reconciliation_run_failure_code",
        ),
    )
    op.create_index(
        "ix_storage_reconciliation_runs_completed",
        "storage_reconciliation_runs",
        ["completed_at", "id"],
    )

    op.create_table(
        "storage_orphan_findings",
        sa.Column("object_key", sa.String(length=255), primary_key=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_last_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tag_status", sa.String(length=32), nullable=False),
        sa.Column("tag_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "object_key = 'sources/' || left(checksum_sha256, 2) || '/' "
            "|| checksum_sha256 || '.pdf' AND char_length(object_key) = 79",
            name="ck_storage_orphan_finding_object_key",
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_storage_orphan_finding_checksum",
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 0 AND 5497558138880",
            name="ck_storage_orphan_finding_size",
        ),
        sa.CheckConstraint(
            "first_seen_at <= candidate_since AND candidate_since <= last_seen_at "
            "AND created_at <= updated_at "
            "AND (resolved_at IS NULL OR resolved_at BETWEEN candidate_since AND last_seen_at) "
            "AND (tag_updated_at IS NULL OR tag_updated_at BETWEEN first_seen_at AND updated_at)",
            name="ck_storage_orphan_finding_timestamps",
        ),
        sa.CheckConstraint(
            "(status = 'candidate' AND resolved_at IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL)",
            name="ck_storage_orphan_finding_status",
        ),
        sa.CheckConstraint(
            "tag_status IN ('not_requested', 'applied', 'removed', 'capacity_conflict', 'failed')",
            name="ck_storage_orphan_finding_tag_status",
        ),
        sa.CheckConstraint(
            "(tag_status = 'not_requested' AND tag_updated_at IS NULL "
            "AND failure_code IS NULL) OR "
            "(tag_status IN ('applied', 'removed') AND tag_updated_at IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(tag_status IN ('capacity_conflict', 'failed') "
            "AND tag_updated_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_storage_orphan_finding_tag_outcome",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) "
            "AND char_length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code ~ '^[a-z][a-z0-9_.-]*$')",
            name="ck_storage_orphan_finding_failure_code",
        ),
    )
    op.create_index(
        "ix_storage_orphan_findings_status",
        "storage_orphan_findings",
        ["status", "last_seen_at"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_storage_reconciliation_state_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'storage reconciliation singleton cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.singleton_id IS DISTINCT FROM OLD.singleton_id THEN
                RAISE EXCEPTION 'storage reconciliation singleton identity is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.lease_token IS NOT NULL THEN
                IF NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token
                    OR NEW.lease_acquired_at IS DISTINCT FROM NEW.last_started_at
                    OR NEW.last_completed_at IS DISTINCT FROM OLD.last_completed_at
                    OR NEW.continuation_cursor IS DISTINCT FROM OLD.continuation_cursor
                    OR (
                        OLD.lease_token IS NOT NULL
                        AND OLD.lease_expires_at > NEW.lease_acquired_at
                    )
                    OR (
                        OLD.last_started_at IS NOT NULL
                        AND NEW.last_started_at <= OLD.last_started_at
                    )
                THEN
                    RAISE EXCEPTION 'invalid storage reconciliation lease acquisition'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.lease_token IS NOT NULL THEN
                IF NEW.last_started_at IS DISTINCT FROM OLD.last_started_at
                    OR NEW.last_completed_at IS NULL
                    OR NEW.last_completed_at < OLD.last_started_at
                    OR (
                        OLD.last_completed_at IS NOT NULL
                        AND NEW.last_completed_at < OLD.last_completed_at
                    )
                THEN
                    RAISE EXCEPTION 'invalid storage reconciliation lease completion'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'storage reconciliation state requires a leased transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_storage_reconciliation_state_mutation_trigger
        BEFORE UPDATE OR DELETE ON storage_reconciliation_state
        FOR EACH ROW EXECUTE FUNCTION enforce_storage_reconciliation_state_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION reject_storage_reconciliation_run_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'storage reconciliation runs are immutable'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_storage_reconciliation_run_mutation_trigger
        BEFORE UPDATE OR DELETE ON storage_reconciliation_runs
        FOR EACH ROW EXECUTE FUNCTION reject_storage_reconciliation_run_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_storage_orphan_finding_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'storage orphan findings cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.object_key IS DISTINCT FROM OLD.object_key
                OR NEW.checksum_sha256 IS DISTINCT FROM OLD.checksum_sha256
                OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'storage orphan finding identity is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.last_seen_at < OLD.last_seen_at OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'storage orphan finding timestamps cannot move backward'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.status = NEW.status THEN
                IF NEW.candidate_since IS DISTINCT FROM OLD.candidate_since THEN
                    RAISE EXCEPTION 'candidate episode cannot change without reopening'
                        USING ERRCODE = '23514';
                END IF;
                IF OLD.status = 'resolved'
                    AND NEW.resolved_at IS DISTINCT FROM OLD.resolved_at
                THEN
                    RAISE EXCEPTION 'resolution timestamp is immutable within an episode'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status = 'candidate' AND NEW.status = 'resolved' THEN
                IF NEW.candidate_since IS DISTINCT FROM OLD.candidate_since THEN
                    RAISE EXCEPTION 'resolution must preserve its candidate episode'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status = 'resolved' AND NEW.status = 'candidate' THEN
                IF NEW.candidate_since <= OLD.candidate_since THEN
                    RAISE EXCEPTION 'reopened candidate must start a new episode'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status IS DISTINCT FROM NEW.status THEN
                RAISE EXCEPTION 'invalid storage orphan finding transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_storage_orphan_finding_mutation_trigger
        BEFORE UPDATE OR DELETE ON storage_orphan_findings
        FOR EACH ROW EXECUTE FUNCTION enforce_storage_orphan_finding_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER enforce_storage_orphan_finding_mutation_trigger ON storage_orphan_findings"
    )
    op.execute("DROP FUNCTION enforce_storage_orphan_finding_mutation()")
    op.execute(
        "DROP TRIGGER reject_storage_reconciliation_run_mutation_trigger "
        "ON storage_reconciliation_runs"
    )
    op.execute("DROP FUNCTION reject_storage_reconciliation_run_mutation()")
    op.execute(
        "DROP TRIGGER enforce_storage_reconciliation_state_mutation_trigger "
        "ON storage_reconciliation_state"
    )
    op.execute("DROP FUNCTION enforce_storage_reconciliation_state_mutation()")
    op.drop_index("ix_storage_orphan_findings_status", table_name="storage_orphan_findings")
    op.drop_table("storage_orphan_findings")
    op.drop_index(
        "ix_storage_reconciliation_runs_completed",
        table_name="storage_reconciliation_runs",
    )
    op.drop_table("storage_reconciliation_runs")
    op.drop_table("storage_reconciliation_state")
