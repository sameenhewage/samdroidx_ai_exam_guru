from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_understanding_jobs"
down_revision: str | None = "0043_document_understanding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_REQUEST_SNAPSHOT = """jsonb_build_object(
    'document_id',document_id::text,'page_number',page_number,'source_sha256',source_sha256,
    'expected_version',expected_page_version-1,'profile',profile,'budget',budget,'reason',reason,
    'retry_of_job_id',retry_of_job_id::text)"""


def upgrade() -> None:
    op.create_table(
        "source_understanding_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("expected_page_version", sa.Integer(), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "retry_of_job_id",
            sa.Uuid(),
            sa.ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("retry_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_request_key", sa.String(64), nullable=True),
        sa.Column("image_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("accounting", postgresql.JSONB(), nullable=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("source_understanding_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "audit_event_id",
            sa.Uuid(),
            sa.ForeignKey(
                "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
            ),
            nullable=False,
        ),
        sa.UniqueConstraint("created_by", "request_id", name="uq_understanding_job_request"),
        sa.UniqueConstraint("id", "document_id", "page_number", name="uq_understanding_job_source"),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_job_candidate",
        ),
        sa.CheckConstraint(
            "page_number>0 AND expected_page_version>0 AND version>=0",
            name="ck_understanding_job_versions",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 3 AND retry_depth BETWEEN 0 AND 3",
            name="ck_understanding_job_retries",
        ),
        sa.CheckConstraint(
            "(retry_of_job_id IS NULL)=(retry_depth=0)", name="ck_understanding_job_retry_parent"
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_understanding_job_hashes",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','unknown')",
            name="ck_understanding_job_status",
        ),
        sa.CheckConstraint(
            "(status='running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status<>'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_understanding_job_lease",
        ),
        sa.CheckConstraint(
            f"request_fingerprint=public.source_understanding_fingerprint({_JOB_REQUEST_SNAPSHOT})",
            name="ck_understanding_job_request_hash",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded','failed','unknown'))=(completed_at IS NOT NULL)",
            name="ck_understanding_job_completed",
        ),
        sa.CheckConstraint(
            "(status IN ('failed','unknown'))=(failure_code IS NOT NULL)",
            name="ck_understanding_job_failure",
        ),
        sa.CheckConstraint(
            "(status='succeeded')=(candidate_id IS NOT NULL)", name="ck_understanding_job_candidate"
        ),
        sa.CheckConstraint(
            "status<>'succeeded' OR (run_id IS NOT NULL AND accounting IS NOT NULL)",
            name="ck_understanding_job_success",
        ),
        sa.CheckConstraint(
            "(provider_started_at IS NULL AND provider_request_key IS NULL) OR "
            "(provider_started_at IS NOT NULL AND image_metadata IS NOT NULL "
            "AND provider_request_key IS NOT NULL AND provider_request_key ~ '^[0-9a-f]{64}$')",
            name="ck_understanding_job_dispatch",
        ),
        sa.CheckConstraint(
            "accounting IS NULL OR provider_started_at IS NOT NULL",
            name="ck_understanding_job_accounted",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(profile)='object' AND octet_length(profile::text)<=16384",
            name="ck_understanding_job_profile",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(budget)='object' AND octet_length(budget::text)<=4096",
            name="ck_understanding_job_budget",
        ),
        sa.CheckConstraint(
            "image_metadata IS NULL OR (jsonb_typeof(image_metadata)='object' "
            "AND octet_length(image_metadata::text)<=65536)",
            name="ck_understanding_job_image",
        ),
        sa.CheckConstraint(
            "accounting IS NULL OR (jsonb_typeof(accounting)='object' "
            "AND octet_length(accounting::text)<=4096)",
            name="ck_understanding_job_accounting",
        ),
        sa.CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 2000 AND reason=btrim(reason) "
            "AND reason !~ '^[[:space:]]*$|[[:cntrl:]]'",
            name="ck_understanding_job_reason",
        ),
    )
    op.create_index(
        "ix_understanding_job_recovery",
        "source_understanding_jobs",
        ["status", "lease_expires_at", "created_at"],
    )
    op.create_index(
        "ix_understanding_job_page_history",
        "source_understanding_jobs",
        ["document_id", "page_number", "expected_page_version"],
    )
    op.create_index(
        "uq_understanding_job_active_page",
        "source_understanding_jobs",
        ["document_id", "page_number"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.add_column(
        "source_understanding_pages", sa.Column("active_job_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_understanding_page_job",
        "source_understanding_pages",
        "source_understanding_jobs",
        ["active_job_id", "document_id", "page_number"],
        ["id", "document_id", "page_number"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_understanding_page_job",
        "source_understanding_pages",
        "(state='processing')=(active_job_id IS NOT NULL)",
    )
    _job_guards()
    _page_guard()
    _run_guard()


def _job_guards() -> None:
    op.execute("""
        CREATE FUNCTION public.validate_understanding_job() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE;
            audit public.admin_audit_events%ROWTYPE;
            parent public.source_understanding_jobs%ROWTYPE;
            run public.source_understanding_runs%ROWTYPE;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id;
            IF NOT FOUND OR source.checksum_sha256<>NEW.source_sha256
                OR source.original_page_count IS NULL OR NEW.page_number>source.original_page_count
            THEN RAISE EXCEPTION 'understanding job requires its immutable original page'
                USING ERRCODE='23514'; END IF;
            IF TG_OP='INSERT' THEN
                IF NOT source.active_for_ai OR source.quarantined_for_teacher_use
                    OR NEW.status<>'queued' OR NEW.version<>0 OR NEW.attempts<>0
                    OR NEW.provider_started_at IS NOT NULL OR NEW.image_metadata IS NOT NULL
                    OR NEW.accounting IS NOT NULL OR NEW.run_id IS NOT NULL
                THEN RAISE EXCEPTION 'understanding job must begin as an unexecuted queued request'
                    USING ERRCODE='23514'; END IF;
                IF NEW.retry_of_job_id IS NOT NULL THEN
                    SELECT * INTO parent FROM public.source_understanding_jobs
                        WHERE id=NEW.retry_of_job_id;
                    IF NOT FOUND OR parent.status NOT IN ('failed','unknown')
                        OR parent.created_by<>NEW.created_by OR parent.document_id<>NEW.document_id
                        OR parent.page_number<>NEW.page_number
                        OR parent.source_sha256<>NEW.source_sha256
                        OR NEW.retry_depth<>parent.retry_depth+1
                    THEN RAISE EXCEPTION 'understanding retry requires matching terminal lineage'
                        USING ERRCODE='23514'; END IF;
                END IF;
            ELSE
                IF OLD.status IN ('succeeded','failed','unknown') OR NEW.version<>OLD.version+1
                    OR (NEW.id,NEW.document_id,NEW.page_number,NEW.source_sha256,NEW.request_id,
                        NEW.request_fingerprint,NEW.expected_page_version,NEW.profile,NEW.budget,
                        NEW.reason,NEW.retry_of_job_id,NEW.retry_depth,NEW.created_by,NEW.created_at)
                    IS DISTINCT FROM
                       (OLD.id,OLD.document_id,OLD.page_number,OLD.source_sha256,OLD.request_id,
                        OLD.request_fingerprint,OLD.expected_page_version,OLD.profile,OLD.budget,
                        OLD.reason,OLD.retry_of_job_id,OLD.retry_depth,OLD.created_by,OLD.created_at)
                THEN RAISE EXCEPTION 'understanding job history and terminal outcomes are immutable'
                    USING ERRCODE='23514'; END IF;
                IF NEW.status='running' THEN
                    IF NOT source.active_for_ai OR source.quarantined_for_teacher_use
                        OR (OLD.status='queued' AND (NEW.attempts<>OLD.attempts+1
                            OR NEW.provider_started_at IS NOT NULL))
                        OR (OLD.status='running' AND (NEW.attempts<>OLD.attempts
                            OR NEW.lease_token IS DISTINCT FROM OLD.lease_token
                            OR OLD.provider_started_at IS NOT NULL))
                    THEN RAISE EXCEPTION 'understanding claim or dispatch is invalid'
                        USING ERRCODE='23514'; END IF;
                ELSIF NEW.status='queued' THEN
                    IF OLD.status<>'running' OR OLD.provider_started_at IS NOT NULL
                        OR NEW.attempts<>OLD.attempts OR OLD.lease_expires_at>clock_timestamp()
                    THEN RAISE EXCEPTION 'only an expired undispatched claim can be requeued'
                        USING ERRCODE='23514'; END IF;
                ELSIF NEW.attempts<>OLD.attempts THEN
                    RAISE EXCEPTION 'terminal accounting cannot change attempt count'
                        USING ERRCODE='23514';
                END IF;
                IF OLD.provider_started_at IS NOT NULL AND
                    (NEW.provider_started_at,NEW.image_metadata,NEW.provider_request_key)
                    IS DISTINCT FROM
                    (OLD.provider_started_at,OLD.image_metadata,OLD.provider_request_key)
                THEN RAISE EXCEPTION 'dispatched image identity is immutable'
                    USING ERRCODE='23514'; END IF;
            END IF;
            IF NEW.run_id IS NOT NULL THEN
                SELECT * INTO run FROM public.source_understanding_runs WHERE id=NEW.run_id;
                IF NOT FOUND OR run.document_id<>NEW.document_id OR run.page_number<>NEW.page_number
                    OR run.source_sha256<>NEW.source_sha256 OR run.request_id<>NEW.request_id
                    OR run.created_by<>NEW.created_by OR run.provider_profile<>NEW.profile
                    OR run.request_fingerprint IS DISTINCT FROM NEW.provider_request_key
                    OR run.budget<>NEW.budget OR run.accounting IS DISTINCT FROM NEW.accounting
                    OR run.image_metadata IS DISTINCT FROM NEW.image_metadata
                    OR run.outcome<>NEW.status
                THEN RAISE EXCEPTION 'understanding job outcome must bind its immutable run'
                    USING ERRCODE='23514'; END IF;
            END IF;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF NOT FOUND OR audit.actor_id<>NEW.created_by
                OR audit.resource_type<>'page_understanding' OR audit.resource_id<>NEW.document_id
                OR audit.action IS DISTINCT FROM 'page_understanding.job_'||NEW.status
                OR audit.payload->>'job_id' IS DISTINCT FROM NEW.id::text
                OR audit.payload->'job_version' IS DISTINCT FROM to_jsonb(NEW.version)
                OR audit.payload->>'status' IS DISTINCT FROM NEW.status
            THEN RAISE EXCEPTION 'understanding job transition requires matching audit evidence'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_job_trigger
            BEFORE INSERT OR UPDATE ON public.source_understanding_jobs
            FOR EACH ROW EXECUTE FUNCTION public.validate_understanding_job();
    """)
    op.execute("""
        CREATE TRIGGER immutable_understanding_job_delete
            BEFORE DELETE ON public.source_understanding_jobs
            FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE TRIGGER immutable_understanding_job_truncate
            BEFORE TRUNCATE ON public.source_understanding_jobs
            FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)


def _page_guard() -> None:
    op.execute(
        "DROP TRIGGER validate_understanding_page_trigger ON public.source_understanding_pages"
    )
    op.execute(
        "ALTER FUNCTION public.validate_understanding_page() "
        "RENAME TO validate_understanding_page_v1"
    )
    op.execute("""
        CREATE FUNCTION public.validate_understanding_page() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE;
            candidate public.source_understanding_candidates%ROWTYPE;
            trusted public.trusted_page_knowledge%ROWTYPE;
            audit public.admin_audit_events%ROWTYPE;
            job public.source_understanding_jobs%ROWTYPE;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            IF NOT FOUND OR source.original_page_count IS NULL
                OR NEW.page_number>source.original_page_count
                OR (NEW.state<>'needs_reprocessing' AND
                    (NOT source.active_for_ai OR source.quarantined_for_teacher_use))
            THEN RAISE EXCEPTION 'understanding page requires its original source'
                USING ERRCODE='23514'; END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.version<>0 OR NEW.candidate_revision<>0 OR NEW.trusted_revision<>0
                THEN RAISE EXCEPTION 'understanding page must start unverified'
                    USING ERRCODE='23514'; END IF;
                RETURN NEW;
            END IF;
            IF NEW.document_id<>OLD.document_id OR NEW.page_number<>OLD.page_number
                OR NEW.version<>OLD.version+1
            THEN RAISE EXCEPTION 'understanding page requires its next version'
                USING ERRCODE='23514'; END IF;
            IF NEW.active_job_id IS NOT NULL THEN
                SELECT * INTO job FROM public.source_understanding_jobs WHERE id=NEW.active_job_id;
                IF NOT FOUND OR OLD.active_job_id IS NOT NULL OR job.status<>'queued'
                    OR job.expected_page_version<>NEW.version OR job.created_by<>NEW.updated_by
                    OR NEW.current_trusted_id IS NOT NULL
                    OR (NEW.current_candidate_id,NEW.current_report_id,NEW.candidate_revision,
                        NEW.trusted_revision) IS DISTINCT FROM
                       (OLD.current_candidate_id,OLD.current_report_id,OLD.candidate_revision,
                        OLD.trusted_revision)
                THEN RAISE EXCEPTION 'processing requires an explicit current queued job'
                    USING ERRCODE='23514'; END IF;
            ELSIF OLD.active_job_id IS NOT NULL AND
                NEW.current_candidate_id IS NOT DISTINCT FROM OLD.current_candidate_id THEN
                SELECT * INTO job FROM public.source_understanding_jobs WHERE id=OLD.active_job_id;
                IF NOT FOUND OR job.status NOT IN ('failed','unknown')
                    OR NEW.state<>'needs_reprocessing' OR NEW.current_trusted_id IS NOT NULL
                    OR (NEW.current_report_id,NEW.candidate_revision,NEW.trusted_revision)
                        IS DISTINCT FROM
                       (OLD.current_report_id,OLD.candidate_revision,OLD.trusted_revision)
                THEN RAISE EXCEPTION 'failed analysis cannot grant or restore source trust'
                    USING ERRCODE='23514'; END IF;
            ELSE
                SELECT * INTO candidate FROM public.source_understanding_candidates
                    WHERE id=NEW.current_candidate_id;
                IF NOT FOUND OR candidate.document_id<>NEW.document_id
                    OR candidate.page_number<>NEW.page_number
                    OR candidate.source_sha256<>source.checksum_sha256
                    OR candidate.revision<>NEW.candidate_revision OR NEW.current_report_id IS NULL
                THEN RAISE EXCEPTION 'understanding page requires its exact candidate and report'
                    USING ERRCODE='23514'; END IF;
                IF NEW.current_candidate_id IS DISTINCT FROM OLD.current_candidate_id THEN
                    IF NEW.candidate_revision<>OLD.candidate_revision+1
                        OR NEW.trusted_revision<>OLD.trusted_revision
                        OR NEW.state NOT IN ('needs_human_review','needs_reprocessing','corrected')
                        OR NEW.current_trusted_id IS NOT NULL
                    THEN RAISE EXCEPTION 'new observations cannot retain active source trust'
                        USING ERRCODE='23514'; END IF;
                    IF OLD.active_job_id IS NOT NULL THEN
                        SELECT * INTO job FROM public.source_understanding_jobs
                            WHERE id=OLD.active_job_id;
                        IF job.status<>'running' OR job.provider_started_at IS NULL
                            OR job.lease_expires_at<=clock_timestamp()
                            OR NOT EXISTS (SELECT 1 FROM public.source_understanding_runs r
                                WHERE r.id=candidate.run_id AND r.request_id=job.request_id
                                    AND r.created_by=job.created_by
                                    AND r.provider_profile=job.profile
                                    AND r.budget=job.budget)
                        THEN
                            RAISE EXCEPTION
                                'only the current dispatched job can deliver its result'
                                USING ERRCODE='23514';
                        END IF;
                    END IF;
                ELSE
                    SELECT * INTO trusted FROM public.trusted_page_knowledge
                        WHERE id=NEW.current_trusted_id;
                    IF NOT FOUND OR NEW.state<>'verified'
                        OR NEW.candidate_revision<>OLD.candidate_revision
                        OR trusted.revision<>NEW.trusted_revision
                        OR NEW.trusted_revision<>OLD.trusted_revision+1
                    THEN RAISE EXCEPTION 'verification must advance its immutable trusted revision'
                        USING ERRCODE='23514'; END IF;
                END IF;
            END IF;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.event_id;
            IF NOT FOUND OR audit.actor_id<>NEW.updated_by
                OR audit.resource_type<>'page_understanding' OR audit.resource_id<>NEW.document_id
                OR audit.payload->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR audit.payload->'version' IS DISTINCT FROM to_jsonb(NEW.version)
                OR audit.payload->>'candidate_id' IS DISTINCT FROM NEW.current_candidate_id::text
                OR audit.payload->>'report_id' IS DISTINCT FROM NEW.current_report_id::text
                OR audit.payload->>'trusted_knowledge_id'
                    IS DISTINCT FROM NEW.current_trusted_id::text
            THEN RAISE EXCEPTION 'understanding page change requires matching audit evidence'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_page_trigger
            BEFORE INSERT OR UPDATE ON public.source_understanding_pages
            FOR EACH ROW EXECUTE FUNCTION public.validate_understanding_page();
    """)


def _run_guard() -> None:
    op.execute(
        "DROP TRIGGER validate_understanding_run_trigger ON public.source_understanding_runs"
    )
    op.execute(
        "ALTER FUNCTION public.validate_understanding_run() RENAME TO validate_understanding_run_v1"
    )
    op.execute("""
        CREATE FUNCTION public.validate_understanding_run() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE; image jsonb;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            image := NEW.image_metadata;
            IF NOT FOUND OR source.checksum_sha256<>NEW.source_sha256
                OR source.original_page_count IS NULL OR NEW.page_number>source.original_page_count
                OR (NEW.outcome='succeeded' AND
                    (NOT source.active_for_ai OR source.quarantined_for_teacher_use))
                OR image->>'document_id' IS DISTINCT FROM NEW.document_id::text
                OR image->>'source_checksum_sha256' IS DISTINCT FROM NEW.source_sha256
                OR image->>'source_object_key' IS DISTINCT FROM source.object_key
                OR image->'source_size_bytes' IS DISTINCT FROM to_jsonb(source.size_bytes)
                OR image->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR image->>'sha256' IS DISTINCT FROM NEW.image_sha256
                OR image->>'content_type' IS DISTINCT FROM 'image/png'
                OR image->'artifact'->>'sha256' IS DISTINCT FROM NEW.image_sha256
                OR image->'artifact'->>'namespace' IS DISTINCT FROM 'fidelity-page-images'
            THEN RAISE EXCEPTION 'understanding run requires immutable original image lineage'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_run_trigger
            BEFORE INSERT ON public.source_understanding_runs
            FOR EACH ROW EXECUTE FUNCTION public.validate_understanding_run();
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE public.source_understanding_jobs IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM public.source_understanding_jobs) THEN
                RAISE EXCEPTION 'cannot discard document understanding job history'
                    USING ERRCODE='23514';
            END IF;
        END; $$;
    """)
    for table, name in (
        ("source_understanding_pages", "validate_understanding_page"),
        ("source_understanding_runs", "validate_understanding_run"),
    ):
        op.execute(f"DROP TRIGGER {name}_trigger ON public.{table}")
        op.execute(f"DROP FUNCTION public.{name}()")
        op.execute(f"ALTER FUNCTION public.{name}_v1() RENAME TO {name}")
        event = "INSERT OR UPDATE" if table == "source_understanding_pages" else "INSERT"
        op.execute(
            f"CREATE TRIGGER {name}_trigger BEFORE {event} ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{name}()"
        )
    op.drop_constraint(
        "fk_understanding_page_job", "source_understanding_pages", type_="foreignkey"
    )
    op.drop_constraint("ck_understanding_page_job", "source_understanding_pages", type_="check")
    op.drop_column("source_understanding_pages", "active_job_id")
    op.drop_table("source_understanding_jobs")
    op.execute("DROP FUNCTION public.validate_understanding_job()")
