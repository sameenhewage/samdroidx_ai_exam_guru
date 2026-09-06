from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_source_page_fidelity"
down_revision: str | None = "0032_source_intake_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_documents", sa.Column("original_page_count", sa.Integer(), nullable=True))
    op.execute("UPDATE source_documents SET original_page_count = extracted_page_count")
    op.execute("SET CONSTRAINTS enforce_source_intake_audit_trigger IMMEDIATE")
    op.execute("SET CONSTRAINTS enforce_source_intake_audit_trigger DEFERRED")
    op.create_check_constraint(
        "ck_source_document_original_pages",
        "source_documents",
        "original_page_count IS NULL OR original_page_count > 0",
    )
    op.execute("""
        CREATE FUNCTION preserve_original_page_count() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.original_page_count IS NOT NULL
                AND NEW.original_page_count IS DISTINCT FROM OLD.original_page_count THEN
                RAISE EXCEPTION 'original page count is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER preserve_original_page_count_trigger
        BEFORE UPDATE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION preserve_original_page_count();
    """)
    op.create_table(
        "source_page_text_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("parent_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("raw_text_utf8", sa.LargeBinary(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("can_confirm", sa.Boolean(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "document_id", "page_number", name="uq_page_candidate_identity"),
        sa.ForeignKeyConstraint(
            ["parent_candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_page_candidate_parent",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("page_number > 0", name="ck_page_candidate_page"),
        sa.CheckConstraint(
            "method IN ('native','legacy','ocr','human')", name="ck_page_candidate_method"
        ),
        sa.CheckConstraint(
            "octet_length(raw_text_utf8) <= 4194304", name="ck_page_candidate_raw_size"
        ),
        sa.CheckConstraint(
            "normalized_text IS NULL OR (octet_length(normalized_text) <= 4194304 "
            "AND normalized_text = normalize(normalized_text, NFC))",
            name="ck_page_candidate_normalized_text",
        ),
        sa.CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_page_candidate_hash"),
        sa.CheckConstraint(
            "jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 65536 "
            "AND jsonb_typeof(diagnostics) = 'object' AND octet_length(diagnostics::text) <= 65536",
            name="ck_page_candidate_metadata",
        ),
    )
    op.create_index(
        "ix_page_candidates_history",
        "source_page_text_candidates",
        ["document_id", "page_number", "created_at"],
    )
    op.create_table(
        "source_page_review_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "document_id", "page_number", "version", name="uq_page_review_event_version"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_page_review_event_candidate",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("page_number > 0 AND version > 0", name="ck_page_review_event_version"),
        sa.CheckConstraint(
            "action IN ('candidate_recorded','confirmed','edited','excluded','reread_requested',"
            "'reread_failed','reference_verified')",
            name="ck_page_review_event_action",
        ),
        sa.CheckConstraint(
            "state IN ('needs_review','verified','excluded','processing','failed')",
            name="ck_page_review_event_state",
        ),
        sa.CheckConstraint(
            "length(reason) BETWEEN 1 AND 2000 AND octet_length(payload::text) <= 65536 "
            "AND jsonb_typeof(payload) = 'object'",
            name="ck_page_review_event_payload",
        ),
    )
    op.create_table(
        "source_page_review_states",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("state", sa.String(16), server_default="pending", nullable=False),
        sa.Column("current_candidate_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey(
                "source_page_review_events.id",
                ondelete="RESTRICT",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["current_candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_page_review_current_candidate",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("page_number > 0 AND version >= 0", name="ck_page_review_state_version"),
        sa.CheckConstraint(
            "state IN ('pending','needs_review','verified','excluded','processing','failed')",
            name="ck_page_review_state",
        ),
        sa.CheckConstraint(
            "(version = 0 AND state = 'pending' AND event_id IS NULL "
            "AND current_candidate_id IS NULL) "
            "OR (version > 0 AND event_id IS NOT NULL)",
            name="ck_page_review_state_event",
        ),
        sa.CheckConstraint(
            "state <> 'verified' OR current_candidate_id IS NOT NULL",
            name="ck_page_review_verified_candidate",
        ),
    )
    op.create_index(
        "ix_page_review_state", "source_page_review_states", ["document_id", "state", "page_number"]
    )
    op.create_table(
        "source_fidelity_benchmarks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("selection", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("name", name="uq_source_fidelity_benchmark_name"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 160", name="ck_source_benchmark_name"),
        sa.CheckConstraint(
            "jsonb_typeof(selection) = 'object' AND octet_length(selection::text) <= 65536",
            name="ck_source_benchmark_selection",
        ),
    )
    op.create_table(
        "source_fidelity_benchmark_pages",
        sa.Column(
            "benchmark_id",
            sa.Uuid(),
            sa.ForeignKey("source_fidelity_benchmarks.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column("categories", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("page_number > 0", name="ck_source_benchmark_page"),
        sa.CheckConstraint(
            "jsonb_typeof(categories) = 'array' "
            "AND jsonb_array_length(categories) BETWEEN 1 AND 32",
            name="ck_source_benchmark_categories",
        ),
    )
    op.create_table(
        "source_page_ground_truth",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("benchmark_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "review_event_id",
            sa.Uuid(),
            sa.ForeignKey("source_page_review_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("benchmark_id", "review_event_id", name="uq_page_ground_truth_event"),
        sa.ForeignKeyConstraint(
            ["benchmark_id", "document_id", "page_number"],
            [
                "source_fidelity_benchmark_pages.benchmark_id",
                "source_fidelity_benchmark_pages.document_id",
                "source_fidelity_benchmark_pages.page_number",
            ],
            name="fk_ground_truth_benchmark_page",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_ground_truth_candidate",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source_checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_ground_truth_source_hash"
        ),
        sa.CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_ground_truth_text_hash"),
    )
    op.create_table(
        "source_read_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("next_page", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expected_page_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number > 0", name="ck_source_read_job_page"
        ),
        sa.CheckConstraint(
            "next_page > 0 AND attempts >= 0 AND version >= 0", name="ck_source_read_job_progress"
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','superseded')",
            name="ck_source_read_job_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(configuration) = 'object' AND octet_length(configuration::text) <= 65536",
            name="ck_source_read_job_configuration",
        ),
    )
    op.create_index(
        "uq_active_source_read_job",
        "source_read_jobs",
        ["document_id", "page_number"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index(
        "ix_source_read_job_recovery", "source_read_jobs", ["status", "lease_expires_at"]
    )
    statements = (
        """
        CREATE FUNCTION reject_source_fidelity_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'source fidelity evidence is append only' USING ERRCODE = '23514';
        END; $$;
        """,
        """
        CREATE FUNCTION validate_source_page_candidate() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE total integer;
        BEGIN
            SELECT original_page_count INTO total FROM source_documents
                WHERE id = NEW.document_id FOR SHARE;
            IF total IS NULL OR NEW.page_number > total THEN
                RAISE EXCEPTION 'source page requires a verified original page count'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.normalized_text IS NOT NULL AND NEW.text_sha256 IS DISTINCT FROM
                encode(sha256(convert_to(NEW.normalized_text, 'UTF8')), 'hex') THEN
                RAISE EXCEPTION 'candidate text hash mismatch' USING ERRCODE = '23514';
            END IF;
            IF NEW.can_confirm AND
                (NEW.normalized_text IS NULL OR btrim(NEW.normalized_text) = '') THEN
                RAISE EXCEPTION 'empty or unsafe candidate cannot be confirmed'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
        """,
        """
        CREATE TRIGGER validate_source_page_candidate_trigger
            BEFORE INSERT ON source_page_text_candidates
            FOR EACH ROW EXECUTE FUNCTION validate_source_page_candidate();
        """,
        """
        CREATE FUNCTION validate_page_review_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE total integer;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'page review history cannot be deleted' USING ERRCODE = '23514';
            END IF;
            SELECT original_page_count INTO total FROM source_documents
                WHERE id = NEW.document_id;
            IF total IS NULL OR NEW.page_number > total THEN
                RAISE EXCEPTION 'review must reference an original page' USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.version <> 0 OR NEW.state <> 'pending' THEN
                    RAISE EXCEPTION 'page review must start pending' USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.document_id <> OLD.document_id OR NEW.page_number <> OLD.page_number
                OR NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'page review version must increment exactly once'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
        """,
        """
        CREATE TRIGGER validate_page_review_transition_trigger
            BEFORE INSERT OR UPDATE OR DELETE ON source_page_review_states
            FOR EACH ROW EXECUTE FUNCTION validate_page_review_transition();
        """,
        """
        CREATE FUNCTION validate_page_review_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            event source_page_review_events%ROWTYPE;
            candidate source_page_text_candidates%ROWTYPE;
        BEGIN
            IF NEW.version = 0 THEN RETURN NEW; END IF;
            SELECT * INTO event FROM source_page_review_events WHERE id = NEW.event_id;
            IF NOT FOUND OR event.document_id <> NEW.document_id
                OR event.page_number <> NEW.page_number
                OR event.version <> NEW.version OR event.state <> NEW.state
                OR event.candidate_id IS DISTINCT FROM NEW.current_candidate_id THEN
                RAISE EXCEPTION 'page review requires matching immutable evidence'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.state = 'verified' THEN
                SELECT * INTO candidate FROM source_page_text_candidates
                    WHERE id = NEW.current_candidate_id;
                IF NOT FOUND OR NOT candidate.can_confirm
                    OR event.action NOT IN ('confirmed','reference_verified')
                    OR event.payload->>'text_sha256' IS DISTINCT FROM candidate.text_sha256
                    OR event.payload->>'compared_with_original' IS DISTINCT FROM 'true' THEN
                    RAISE EXCEPTION 'verified page requires exact candidate confirmation'
                        USING ERRCODE = '23514';
                END IF;
                IF event.action = 'reference_verified' AND NOT EXISTS (
                    SELECT 1 FROM source_page_ground_truth g
                    JOIN source_documents d ON d.id = g.document_id
                    WHERE g.id::text = event.payload->>'ground_truth_id'
                    AND g.document_id = NEW.document_id AND g.page_number = NEW.page_number
                    AND g.source_checksum_sha256 = d.checksum_sha256
                    AND g.text_sha256 = candidate.text_sha256
                ) THEN
                    RAISE EXCEPTION 'automatic verification requires exact adjudicated reference'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END; $$;
        """,
        """
        CREATE CONSTRAINT TRIGGER validate_page_review_evidence_trigger AFTER INSERT OR UPDATE
            ON source_page_review_states DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION validate_page_review_evidence();
        """,
        """
        CREATE FUNCTION validate_page_ground_truth() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM source_page_review_events e
                JOIN source_page_text_candidates c ON c.id = e.candidate_id
                JOIN source_documents d ON d.id = e.document_id
                WHERE e.id = NEW.review_event_id AND e.action = 'confirmed' AND e.state = 'verified'
                AND e.actor_id = NEW.reviewer_id AND e.document_id = NEW.document_id
                AND e.page_number = NEW.page_number AND e.candidate_id = NEW.candidate_id
                AND c.can_confirm AND c.text_sha256 = NEW.text_sha256
                AND e.payload->>'compared_with_original' = 'true'
                AND e.payload->>'text_sha256' = c.text_sha256
                AND d.checksum_sha256 = NEW.source_checksum_sha256
            ) THEN
                RAISE EXCEPTION 'ground truth requires explicit source comparison evidence'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$;
        """,
        """
        CREATE TRIGGER validate_page_ground_truth_trigger BEFORE INSERT ON source_page_ground_truth
            FOR EACH ROW EXECUTE FUNCTION validate_page_ground_truth();
        """,
        """
        CREATE FUNCTION source_page_fidelity_is_current(
            source_id uuid, number integer, candidate uuid DEFAULT NULL
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM source_page_review_states s
                JOIN source_page_text_candidates c ON c.id = s.current_candidate_id
                WHERE s.document_id = source_id AND s.page_number = number AND s.state = 'verified'
                AND c.can_confirm AND (candidate IS NULL OR candidate = c.id)
            );
        $$;
        """,
    )
    for statement in statements:
        op.execute(statement)
    for table in (
        "source_page_text_candidates",
        "source_page_review_events",
        "source_fidelity_benchmarks",
        "source_fidelity_benchmark_pages",
        "source_page_ground_truth",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_source_fidelity_mutation()"
            )
        )
    for table in (
        "source_page_text_candidates",
        "source_page_review_events",
        "source_page_review_states",
        "source_fidelity_benchmarks",
        "source_fidelity_benchmark_pages",
        "source_page_ground_truth",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION reject_source_fidelity_mutation()"
            )
        )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM source_page_text_candidates)
                OR EXISTS (SELECT 1 FROM source_page_review_events)
                OR EXISTS (SELECT 1 FROM source_fidelity_benchmarks)
                OR EXISTS (SELECT 1 FROM source_read_jobs) THEN
                RAISE EXCEPTION 'cannot discard source fidelity evidence';
            END IF;
        END; $$;
    """)
    op.execute("DROP FUNCTION source_page_fidelity_is_current(uuid, integer, uuid)")
    for table in (
        "source_read_jobs",
        "source_page_ground_truth",
        "source_fidelity_benchmark_pages",
        "source_fidelity_benchmarks",
        "source_page_review_states",
        "source_page_review_events",
        "source_page_text_candidates",
    ):
        op.drop_table(table)
    for name in (
        "validate_page_ground_truth",
        "validate_page_review_evidence",
        "validate_page_review_transition",
        "validate_source_page_candidate",
        "reject_source_fidelity_mutation",
    ):
        op.execute(sa.text(f"DROP FUNCTION {name}()"))
    op.execute("DROP TRIGGER preserve_original_page_count_trigger ON source_documents")
    op.execute("DROP FUNCTION preserve_original_page_count()")
    op.drop_constraint("ck_source_document_original_pages", "source_documents")
    op.drop_column("source_documents", "original_page_count")
