import sqlalchemy as sa
from alembic import op

revision = "0045_understanding_review"
down_revision = "0044_understanding_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_understanding_candidates",
        sa.Column("parent_candidate_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_understanding_candidate_parent",
        "source_understanding_candidates",
        "source_understanding_candidates",
        ["parent_candidate_id", "document_id", "page_number"],
        ["id", "document_id", "page_number"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_understanding_candidate_parent",
        "source_understanding_candidates",
        "parent_candidate_id IS NULL OR parent_candidate_id<>id",
    )
    op.execute("""
        CREATE FUNCTION public.validate_understanding_correction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent public.source_understanding_candidates%ROWTYPE;
            page public.source_understanding_pages%ROWTYPE;
            run public.source_understanding_runs%ROWTYPE;
            parent_run public.source_understanding_runs%ROWTYPE;
            audit public.admin_audit_events%ROWTYPE;
            content jsonb; identity jsonb;
        BEGIN
            IF NEW.method<>'human' THEN
                IF NEW.parent_candidate_id IS NOT NULL THEN
                    RAISE EXCEPTION 'only an explicit human correction can declare a parent'
                        USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            SELECT * INTO parent FROM public.source_understanding_candidates
                WHERE id=NEW.parent_candidate_id;
            IF NOT FOUND OR parent.id=NEW.id
                OR (parent.document_id,parent.page_number,parent.source_sha256,parent.image_sha256)
                    IS DISTINCT FROM
                   (NEW.document_id,NEW.page_number,NEW.source_sha256,NEW.image_sha256)
            THEN RAISE EXCEPTION 'correction requires its exact source parent'
                USING ERRCODE='23514'; END IF;
            SELECT * INTO page FROM public.source_understanding_pages
                WHERE document_id=NEW.document_id AND page_number=NEW.page_number FOR UPDATE;
            IF NOT FOUND OR page.current_candidate_id IS DISTINCT FROM parent.id
                OR page.active_job_id IS NOT NULL OR page.state='excluded'
                OR NEW.revision<>page.candidate_revision+1
            THEN RAISE EXCEPTION 'correction must extend the current editable page'
                USING ERRCODE='23514'; END IF;
            SELECT * INTO run FROM public.source_understanding_runs WHERE id=NEW.run_id;
            SELECT * INTO parent_run FROM public.source_understanding_runs WHERE id=parent.run_id;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF audit.id IS NULL OR audit.id IS DISTINCT FROM run.audit_event_id
                OR audit.actor_id<>NEW.created_by
                OR audit.payload->>'parent_candidate_id' IS DISTINCT FROM parent.id::text
                OR jsonb_typeof(audit.payload->'reason') IS DISTINCT FROM 'string'
                OR length(btrim(audit.payload->>'reason')) NOT BETWEEN 1 AND 2000
                OR btrim(audit.payload->>'reason') IS DISTINCT FROM audit.payload->>'reason'
                OR run.image_metadata IS DISTINCT FROM parent_run.image_metadata
                OR run.provider_profile IS NOT NULL OR run.budget IS NOT NULL
                OR run.accounting IS NOT NULL
            THEN RAISE EXCEPTION 'human correction requires distinct audited provenance'
                USING ERRCODE='23514'; END IF;
            content:=jsonb_build_object('schema_version','page-understanding.v1',
                'observation',NEW.observation,'education',NEW.educational_understanding,
                'uncertainties',NEW.uncertainties);
            identity:=jsonb_build_object('schema_version','understanding-correction.v1',
                'source',jsonb_build_object('document_id',NEW.document_id::text,
                    'page_number',NEW.page_number,'source_sha256',NEW.source_sha256,
                    'image_sha256',NEW.image_sha256),
                'parent_candidate_id',parent.id::text,'parent_fingerprint',parent.fingerprint,
                'expected_version',page.version,
                'content_fingerprint',public.source_understanding_fingerprint(content),
                'reason',audit.payload->'reason');
            IF run.request_fingerprint IS DISTINCT FROM
                public.source_understanding_fingerprint(identity)
            THEN RAISE EXCEPTION 'correction request must bind its complete versioned identity'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_correction_trigger
            BEFORE INSERT ON public.source_understanding_candidates
            FOR EACH ROW EXECUTE FUNCTION public.validate_understanding_correction();
    """)
    op.execute("""
        CREATE FUNCTION public.guard_understanding_request_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE job public.source_understanding_jobs%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'source-understanding-request:'||NEW.created_by::text||':'||NEW.request_id::text,0));
            IF TG_TABLE_NAME='source_understanding_jobs' THEN
                IF EXISTS (SELECT 1 FROM public.source_understanding_runs
                    WHERE created_by=NEW.created_by AND request_id=NEW.request_id)
                THEN RAISE EXCEPTION 'recorded understanding requests cannot become new jobs'
                    USING ERRCODE='23514'; END IF;
            ELSE
                SELECT * INTO job FROM public.source_understanding_jobs
                    WHERE created_by=NEW.created_by AND request_id=NEW.request_id;
                IF FOUND AND (NEW.method<>'visual_ai'
                    OR (NEW.document_id,NEW.page_number,NEW.source_sha256,
                        NEW.provider_profile,NEW.budget,NEW.request_fingerprint)
                        IS DISTINCT FROM
                       (job.document_id,job.page_number,job.source_sha256,
                        job.profile,job.budget,job.provider_request_key))
                THEN RAISE EXCEPTION 'understanding request identity belongs to another operation'
                    USING ERRCODE='23514'; END IF;
            END IF;
            RETURN NEW;
        END; $$;
    """)
    for table in ("source_understanding_jobs", "source_understanding_runs"):
        op.execute(f"""
            CREATE TRIGGER guard_understanding_request_identity_trigger
                BEFORE INSERT ON public.{table}
                FOR EACH ROW EXECUTE FUNCTION public.guard_understanding_request_identity();
        """)
    op.execute(
        "DROP TRIGGER validate_understanding_page_trigger ON public.source_understanding_pages"
    )
    op.execute("""
        CREATE TRIGGER validate_understanding_page_insert_trigger
            BEFORE INSERT ON public.source_understanding_pages
            FOR EACH ROW EXECUTE FUNCTION public.validate_understanding_page();
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_page_trigger
            BEFORE UPDATE ON public.source_understanding_pages
            FOR EACH ROW WHEN (OLD.state<>'excluded' AND NEW.state<>'excluded')
            EXECUTE FUNCTION public.validate_understanding_page();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_lifecycle() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE;
            audit public.admin_audit_events%ROWTYPE;
            report public.source_understanding_reports%ROWTYPE;
            reopen_state text;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.event_id;
            SELECT * INTO report FROM public.source_understanding_reports
                WHERE id=NEW.current_report_id;
            IF OLD.current_candidate_id IS NULL THEN reopen_state:='unprocessed';
            ELSIF report.id IS NULL OR EXISTS (SELECT 1
                FROM jsonb_array_elements(report.payload->'findings') finding
                WHERE finding->>'severity'='block') THEN reopen_state:='needs_reprocessing';
            ELSE reopen_state:='needs_human_review'; END IF;
            IF source.id IS NULL OR source.original_page_count IS NULL
                OR NEW.page_number>source.original_page_count OR NOT source.active_for_ai
                OR source.quarantined_for_teacher_use
                OR (NEW.document_id,NEW.page_number,NEW.current_candidate_id,NEW.current_report_id,
                    NEW.candidate_revision,NEW.trusted_revision)
                    IS DISTINCT FROM
                   (OLD.document_id,OLD.page_number,OLD.current_candidate_id,OLD.current_report_id,
                    OLD.candidate_revision,OLD.trusted_revision)
                OR OLD.active_job_id IS NOT NULL OR NEW.active_job_id IS NOT NULL
                OR NEW.current_trusted_id IS NOT NULL OR NEW.version<>OLD.version+1
                OR audit.id IS NULL OR audit.actor_id<>NEW.updated_by
                OR audit.resource_type<>'page_understanding' OR audit.resource_id<>NEW.document_id
                OR audit.payload->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR audit.payload->'previous_version' IS DISTINCT FROM to_jsonb(OLD.version)
                OR audit.payload->'version' IS DISTINCT FROM to_jsonb(NEW.version)
                OR audit.payload->>'source_sha256' IS DISTINCT FROM source.checksum_sha256
                OR audit.payload->>'candidate_id' IS DISTINCT FROM NEW.current_candidate_id::text
                OR audit.payload->>'report_id' IS DISTINCT FROM NEW.current_report_id::text
                OR audit.payload->>'previous_trusted_knowledge_id'
                    IS DISTINCT FROM OLD.current_trusted_id::text
                OR audit.payload->'trusted_knowledge_id' IS DISTINCT FROM 'null'::jsonb
                OR jsonb_typeof(audit.payload->'reason') IS DISTINCT FROM 'string'
                OR length(btrim(audit.payload->>'reason')) NOT BETWEEN 1 AND 2000
                OR btrim(audit.payload->>'reason') IS DISTINCT FROM audit.payload->>'reason'
            THEN RAISE EXCEPTION 'understanding lifecycle requires exact audited source identity'
                USING ERRCODE='23514'; END IF;
            IF NEW.state='excluded' AND OLD.state<>'excluded' THEN
                IF audit.action<>'page_understanding.excluded'
                    OR audit.payload->'confirmed_exclusion' IS DISTINCT FROM 'true'::jsonb
                THEN RAISE EXCEPTION 'page exclusion requires an explicit decision'
                    USING ERRCODE='23514'; END IF;
            ELSIF OLD.state='excluded' AND NEW.state=reopen_state THEN
                IF audit.action<>'page_understanding.reopened'
                    OR audit.payload->'confirmed_reopen' IS DISTINCT FROM 'true'::jsonb
                THEN RAISE EXCEPTION 'reopening requires a fresh explicit review decision'
                    USING ERRCODE='23514'; END IF;
            ELSE
                RAISE EXCEPTION 'excluded pages cannot regain trust or be silently replaced'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_lifecycle_trigger
            BEFORE UPDATE ON public.source_understanding_pages
            FOR EACH ROW WHEN (OLD.state='excluded' OR NEW.state='excluded')
            EXECUTE FUNCTION public.validate_understanding_lifecycle();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.source_understanding_candidates
                    WHERE parent_candidate_id IS NOT NULL)
                OR EXISTS (SELECT 1 FROM public.admin_audit_events
                    WHERE action IN ('page_understanding.excluded','page_understanding.reopened'))
            THEN RAISE EXCEPTION 'cannot discard document understanding review history'; END IF;
        END; $$;
    """)
    for table in ("source_understanding_jobs", "source_understanding_runs"):
        op.execute(f"DROP TRIGGER guard_understanding_request_identity_trigger ON public.{table}")
    op.execute("DROP FUNCTION public.guard_understanding_request_identity()")
    op.execute(
        "DROP TRIGGER validate_understanding_lifecycle_trigger ON public.source_understanding_pages"
    )
    op.execute("DROP FUNCTION public.validate_understanding_lifecycle()")
    op.execute(
        "DROP TRIGGER validate_understanding_page_insert_trigger "
        "ON public.source_understanding_pages"
    )
    op.execute(
        "DROP TRIGGER validate_understanding_page_trigger ON public.source_understanding_pages"
    )
    op.execute("""
        CREATE TRIGGER validate_understanding_page_trigger
            BEFORE INSERT OR UPDATE ON public.source_understanding_pages
            FOR EACH ROW EXECUTE FUNCTION public.validate_understanding_page();
    """)
    op.execute(
        "DROP TRIGGER validate_understanding_correction_trigger "
        "ON public.source_understanding_candidates"
    )
    op.execute("DROP FUNCTION public.validate_understanding_correction()")
    op.drop_constraint(
        "ck_understanding_candidate_parent", "source_understanding_candidates", type_="check"
    )
    op.drop_constraint(
        "fk_understanding_candidate_parent", "source_understanding_candidates", type_="foreignkey"
    )
    op.drop_column("source_understanding_candidates", "parent_candidate_id")
