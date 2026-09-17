import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055_source_consensus"
down_revision = "0054_source_reading_stages"
branch_labels = None
depends_on = None


def _created() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "source_witness_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("pass_number", sa.Integer(), nullable=False),
        sa.Column("reader", sa.String(16), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("event", sa.String(24), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_created(),
        sa.UniqueConstraint("job_id", "pass_number", "event", name="uq_source_witness_event"),
        sa.CheckConstraint("pass_number>=0 AND pass_number<512", name="ck_source_witness_pass"),
        sa.CheckConstraint("reader IN ('qwen','openai','layout')", name="ck_source_witness_reader"),
        sa.CheckConstraint(
            "event IN ('requested','provider_completed','parsed','failed','cache_hit')",
            name="ck_source_witness_event",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=2097152 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_source_witness_payload",
        ),
    )
    op.create_index(
        "ix_source_witness_input", "source_witness_events", ["reader", "input_fingerprint", "event"]
    )
    op.create_table(
        "source_machine_candidates",
        sa.Column(
            "candidate_id",
            sa.Uuid(),
            sa.ForeignKey("source_understanding_candidates.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_created(),
        sa.UniqueConstraint("job_id", name="uq_source_machine_job"),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_source_machine_payload",
        ),
    )
    op.execute("""
        CREATE FUNCTION public.validate_source_witness_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE j public.source_understanding_jobs%ROWTYPE;
            p public.source_understanding_pages%ROWTYPE;
            d public.source_documents%ROWTYPE;
            a public.admin_audit_events%ROWTYPE;
            previous public.source_witness_events%ROWTYPE;
            expected_configuration text; input_source jsonb; render jsonb;
        BEGIN
            SELECT * INTO j FROM public.source_understanding_jobs WHERE id=NEW.job_id;
            SELECT * INTO p FROM public.source_understanding_pages
                WHERE document_id=j.document_id AND page_number=j.page_number;
            SELECT * INTO d FROM public.source_documents WHERE id=j.document_id;
            SELECT * INTO a FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            expected_configuration:=CASE WHEN NEW.reader='qwen'
                THEN public.source_understanding_fingerprint(j.profile->'qwen')
                ELSE public.source_understanding_fingerprint(j.profile) END;
            input_source:=jsonb_build_object(
                'document_id',j.document_id,'source_sha256',j.source_sha256,
                'page_number',j.page_number,'image_sha256',j.image_metadata->>'sha256');
            IF j.id IS NULL OR j.status<>'running'
                OR j.lease_token IS DISTINCT FROM NEW.lease_token
                OR j.lease_expires_at<=clock_timestamp() OR j.provider_started_at IS NULL
                OR p.active_job_id IS DISTINCT FROM j.id OR p.version<>j.expected_page_version
                OR NOT d.active_for_ai OR d.quarantined_for_teacher_use
                OR d.checksum_sha256<>j.source_sha256
                OR j.profile->'qwen' IS NULL
                OR j.profile->>'prompt_version'<>'qwen-openai-source-consensus.v1'
                OR NEW.created_by<>j.created_by OR a.actor_id IS DISTINCT FROM j.created_by
                OR a.resource_type IS DISTINCT FROM 'source_witness'
                OR a.resource_id IS DISTINCT FROM j.document_id
                OR a.action IS DISTINCT FROM 'source_witness.'||NEW.event
                OR a.payload IS DISTINCT FROM jsonb_build_object(
                    'job_id',j.id,'pass_number',NEW.pass_number,'reader',NEW.reader,
                    'input_fingerprint',NEW.input_fingerprint,'fingerprint',NEW.fingerprint)
                OR NEW.payload->>'schema_version' IS DISTINCT FROM 'source-witness-event.v1'
                OR NEW.payload->>'reader' IS DISTINCT FROM NEW.reader
                OR NEW.payload->>'event' IS DISTINCT FROM NEW.event
                OR NEW.payload->'pass_number' IS DISTINCT FROM to_jsonb(NEW.pass_number)
                OR NEW.payload->'input'->'source' IS DISTINCT FROM input_source
                OR NEW.payload->>'input_fingerprint' IS DISTINCT FROM NEW.input_fingerprint
                OR public.source_understanding_fingerprint(NEW.payload->'input')
                    IS DISTINCT FROM NEW.input_fingerprint
                OR NEW.payload->>'reader_configuration_fingerprint'
                    IS DISTINCT FROM expected_configuration
            THEN RAISE EXCEPTION 'source witness identity, lease or audit mismatch'; END IF;
            render:=NEW.payload->'input'->'render_metadata';
            IF render IS NOT NULL AND render<>'null'::jsonb AND (
                render->>'document_id' IS DISTINCT FROM j.document_id::text
                OR render->>'source_checksum_sha256' IS DISTINCT FROM j.source_sha256
                OR render->'page_number' IS DISTINCT FROM to_jsonb(j.page_number)
                OR render->'dpi' IS DISTINCT FROM NEW.payload->'input'->'render_dpi'
                OR render->>'source_object_key' IS DISTINCT FROM d.object_key
                OR render->'source_size_bytes' IS DISTINCT FROM to_jsonb(d.size_bytes))
            THEN RAISE EXCEPTION 'source witness render changed'; END IF;
            SELECT * INTO previous FROM public.source_witness_events
                WHERE job_id=j.id AND pass_number=NEW.pass_number AND event='requested';
            IF NEW.event NOT IN ('requested','cache_hit') AND (
                previous.id IS NULL OR previous.reader<>NEW.reader
                OR previous.input_fingerprint<>NEW.input_fingerprint
                OR previous.payload->>'reader_configuration_fingerprint'<>expected_configuration)
            THEN RAISE EXCEPTION 'source witness completion lacks exact request'; END IF;
            IF NEW.event='parsed' AND (
                NOT EXISTS(SELECT 1 FROM public.source_witness_events
                    WHERE job_id=j.id AND pass_number=NEW.pass_number
                        AND event='provider_completed')
                OR NEW.payload->'reading'->>'input_fingerprint'
                    IS DISTINCT FROM NEW.input_fingerprint
                OR NEW.payload->'reading'->'reader'->>'reader' IS DISTINCT FROM NEW.reader
                OR NEW.payload->'reading'->'reader'->>'configuration_fingerprint'
                    IS DISTINCT FROM expected_configuration
                OR public.source_understanding_fingerprint(NEW.payload->'reading')
                    IS DISTINCT FROM NEW.payload->>'reading_fingerprint')
            THEN RAISE EXCEPTION 'source witness parsed evidence mismatch'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_source_witness_event_trigger
            BEFORE INSERT ON public.source_witness_events
            FOR EACH ROW EXECUTE FUNCTION public.validate_source_witness_event();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_machine_source_candidate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE j public.source_understanding_jobs%ROWTYPE;
            c public.source_understanding_candidates%ROWTYPE;
            a public.admin_audit_events%ROWTYPE; pair jsonb; ready boolean;
        BEGIN
            SELECT * INTO j FROM public.source_understanding_jobs WHERE id=NEW.job_id;
            SELECT * INTO c FROM public.source_understanding_candidates WHERE id=NEW.candidate_id;
            SELECT * INTO a FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF j.id IS NULL OR c.id IS NULL OR j.profile->'qwen' IS NULL
                OR j.document_id<>c.document_id OR j.page_number<>c.page_number
                OR j.source_sha256<>c.source_sha256 OR j.image_metadata->>'sha256'<>c.image_sha256
                OR c.method<>'visual_ai'
                OR c.educational_understanding<>jsonb_build_object('claims','[]'::jsonb)
                OR NEW.created_by<>j.created_by OR a.actor_id IS DISTINCT FROM j.created_by
                OR a.resource_type IS DISTINCT FROM 'source_machine_candidate'
                OR a.resource_id IS DISTINCT FROM j.document_id
                OR a.action IS DISTINCT FROM 'source_machine_candidate.created'
                OR a.payload IS DISTINCT FROM jsonb_build_object(
                    'job_id',j.id,'candidate_id',c.id,'fingerprint',NEW.fingerprint)
                OR NEW.payload->>'schema_version' IS DISTINCT FROM 'machine-source-candidate.v1'
                OR NEW.payload->'human_verified' IS DISTINCT FROM 'false'::jsonb
                OR NEW.payload->'source' IS DISTINCT FROM jsonb_build_object(
                    'document_id',c.document_id,'source_sha256',c.source_sha256,
                    'page_number',c.page_number,'image_sha256',c.image_sha256)
                OR NEW.payload->'content'->'observation' IS DISTINCT FROM c.observation
                OR NEW.payload->'content'->'uncertainties' IS DISTINCT FROM c.uncertainties
                OR NEW.payload->'geometry'->>'image_sha256' IS DISTINCT FROM c.image_sha256
            THEN RAISE EXCEPTION 'machine source identity or evidence mismatch'; END IF;
            FOR pair IN SELECT value FROM jsonb_array_elements(NEW.payload->'consensus') LOOP
                IF pair->'source' IS DISTINCT FROM NEW.payload->'source'
                    OR NOT EXISTS(SELECT 1 FROM public.source_witness_events
                        WHERE job_id=j.id AND reader='qwen' AND event IN ('parsed','cache_hit')
                        AND input_fingerprint=pair->>'input_fingerprint'
                        AND payload->>'reading_fingerprint'=pair->'witness_fingerprints'->>0)
                    OR NOT EXISTS(SELECT 1 FROM public.source_witness_events
                        WHERE job_id=j.id AND reader='openai' AND event IN ('parsed','cache_hit')
                        AND input_fingerprint=pair->>'input_fingerprint'
                        AND payload->>'reading_fingerprint'=pair->'witness_fingerprints'->>1)
                THEN RAISE EXCEPTION 'machine source lacks both independent witnesses'; END IF;
            END LOOP;
            ready:=jsonb_array_length(NEW.payload->'consensus')>0
                AND jsonb_array_length(NEW.payload->'failures')=0
                AND jsonb_array_length(NEW.payload->'geometry'->'findings')=0
                AND jsonb_array_length(NEW.payload->'content'->'uncertainties')=0
                AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.payload->'consensus') p
                    WHERE p->>'state' NOT IN ('validated','resolved_by_reread'));
            IF (NEW.payload->>'state'='machine_ready') IS DISTINCT FROM ready
            THEN RAISE EXCEPTION 'machine quality gate contradicts source evidence'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_machine_source_candidate_trigger
            BEFORE INSERT ON public.source_machine_candidates
            FOR EACH ROW EXECUTE FUNCTION public.validate_machine_source_candidate();
    """)
    op.execute("""
        CREATE FUNCTION public.require_machine_source_review()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS(SELECT 1 FROM public.source_understanding_candidates c
                JOIN public.source_understanding_runs r ON r.id=c.run_id
                WHERE c.id=NEW.candidate_id AND c.method='visual_ai'
                    AND r.provider_profile->'qwen' IS NOT NULL)
                AND NOT EXISTS(SELECT 1 FROM public.source_machine_candidates m
                    WHERE m.candidate_id=NEW.candidate_id AND m.payload->>'state'='machine_ready')
            THEN
                RAISE EXCEPTION 'unresolved machine source requires correction before confirmation';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER require_machine_source_review_trigger
            BEFORE INSERT ON public.verified_source_contents
            FOR EACH ROW EXECUTE FUNCTION public.require_machine_source_review();
    """)
    for table in ("source_witness_events", "source_machine_candidates"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_{table}_truncate BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM public.source_witness_events)
                OR EXISTS(SELECT 1 FROM public.source_machine_candidates)
            THEN RAISE EXCEPTION 'cannot discard retained source consensus evidence'; END IF;
        END; $$;
    """)
    op.execute(
        "DROP TRIGGER require_machine_source_review_trigger ON public.verified_source_contents"
    )
    op.execute("DROP FUNCTION public.require_machine_source_review()")
    op.drop_table("source_machine_candidates")
    op.drop_table("source_witness_events")
    op.execute("DROP FUNCTION public.validate_machine_source_candidate()")
    op.execute("DROP FUNCTION public.validate_source_witness_event()")
