import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054_source_reading_stages"
down_revision = "0053_material_knowledge_review"
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
        "verified_source_contents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("page_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_created(),
        sa.UniqueConstraint(
            "document_id", "page_number", "revision", name="uq_verified_source_revision"
        ),
        sa.UniqueConstraint(
            "candidate_id", "page_version", name="uq_verified_source_candidate_version"
        ),
        sa.UniqueConstraint("id", "document_id", "page_number", name="uq_verified_source_identity"),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_verified_source_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "candidate_id", "document_id", "page_number"],
            [
                "source_understanding_reports.id",
                "source_understanding_reports.candidate_id",
                "source_understanding_reports.document_id",
                "source_understanding_reports.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_verified_source_report",
        ),
        sa.CheckConstraint(
            "page_number>0 AND page_version>0 AND revision>0", name="ck_verified_source_versions"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=2097152 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_verified_source_payload",
        ),
    )
    op.create_table(
        "source_reading_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("pass_number", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(24), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_created(),
        sa.UniqueConstraint("job_id", "pass_number", "event", name="uq_source_reading_event"),
        sa.CheckConstraint("pass_number>=0 AND pass_number<64", name="ck_source_reading_pass"),
        sa.CheckConstraint(
            "event IN ('requested','provider_completed','failed')", name="ck_source_reading_event"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=2097152 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_source_reading_event_payload",
        ),
    )
    op.create_table(
        "source_educational_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "verified_source_id",
            sa.Uuid(),
            sa.ForeignKey("verified_source_contents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("accounting", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        *_created(),
        sa.UniqueConstraint("created_by", "request_id", name="uq_source_education_request"),
        sa.CheckConstraint("version>=0", name="ck_source_education_version"),
        sa.CheckConstraint(
            "status IN ('queued','running','provider_completed','failed','unknown')",
            name="ck_source_education_status",
        ),
        sa.CheckConstraint(
            "(status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_source_education_lease",
        ),
        sa.CheckConstraint(
            "(status='provider_completed')=(content IS NOT NULL) "
            "AND (status NOT IN ('failed','unknown'))=(failure_code IS NULL)",
            name="ck_source_education_result",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(profile)='object' AND octet_length(profile::text)<=16384 "
            "AND jsonb_typeof(budget)='object' AND octet_length(budget::text)<=4096",
            name="ck_source_education_configuration",
        ),
        sa.CheckConstraint(
            "content IS NULL OR (jsonb_typeof(content)='object' "
            "AND octet_length(content::text)<=1048576)",
            name="ck_source_education_content",
        ),
        sa.CheckConstraint(
            "accounting IS NULL OR (jsonb_typeof(accounting)='object' "
            "AND octet_length(accounting::text)<=4096)",
            name="ck_source_education_accounting",
        ),
    )
    op.execute("""
        CREATE FUNCTION public.verified_source_content_is_current(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS(SELECT 1 FROM public.verified_source_contents v
                JOIN public.source_understanding_pages p ON p.document_id=v.document_id
                    AND p.page_number=v.page_number AND p.current_candidate_id=v.candidate_id
                    AND p.current_report_id=v.report_id AND p.active_job_id IS NULL
                    AND p.state<>'excluded'
                    AND (p.version=v.page_version
                        OR (p.state='verified' AND p.version=v.page_version+1))
                JOIN public.source_documents d ON d.id=v.document_id
                WHERE v.id=identifier AND d.active_for_ai AND NOT d.quarantined_for_teacher_use
                    AND d.checksum_sha256=v.payload->'source'->>'source_sha256');
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.source_education_is_current(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS(SELECT 1 FROM public.source_educational_analyses a
                WHERE a.verified_source_id=identifier AND a.status='provider_completed'
                    AND public.verified_source_content_is_current(identifier));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.validate_verified_source_content() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE p public.source_understanding_pages%ROWTYPE;
            c public.source_understanding_candidates%ROWTYPE;
            r public.source_understanding_reports%ROWTYPE;
            d public.source_documents%ROWTYPE; a public.admin_audit_events%ROWTYPE;
            expected_content jsonb; decision jsonb; next_revision integer;
        BEGIN
            SELECT * INTO d FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            SELECT * INTO p FROM public.source_understanding_pages
                WHERE document_id=NEW.document_id AND page_number=NEW.page_number FOR UPDATE;
            SELECT * INTO c FROM public.source_understanding_candidates WHERE id=NEW.candidate_id;
            SELECT * INTO r FROM public.source_understanding_reports WHERE id=NEW.report_id;
            SELECT * INTO a FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            SELECT coalesce(max(revision),0)+1 INTO next_revision
                FROM public.verified_source_contents
                WHERE document_id=NEW.document_id AND page_number=NEW.page_number;
            expected_content:=jsonb_build_object('schema_version','source-read-candidate.v1',
                'observation',c.observation,'uncertainties',c.uncertainties);
            decision:=NEW.payload->'decision';
            IF d.id IS NULL OR p.document_id IS NULL OR c.id IS NULL OR r.id IS NULL
                OR NOT d.active_for_ai OR d.quarantined_for_teacher_use
                OR c.source_sha256<>d.checksum_sha256 OR p.active_job_id IS NOT NULL
                OR p.state='excluded' OR p.current_candidate_id IS DISTINCT FROM c.id
                OR p.current_report_id IS DISTINCT FROM r.id OR NEW.page_version<>p.version
                OR NEW.revision<>next_revision
                OR c.educational_understanding IS DISTINCT FROM '{"claims":[]}'::jsonb
                OR NEW.payload->>'schema_version' IS DISTINCT FROM 'verified-source-content.v1'
                OR NEW.payload->>'id' IS DISTINCT FROM NEW.id::text
                OR NEW.payload->>'candidate_id' IS DISTINCT FROM c.id::text
                OR NEW.payload->>'candidate_fingerprint' IS DISTINCT FROM c.fingerprint
                OR NEW.payload->>'report_fingerprint' IS DISTINCT FROM r.fingerprint
                OR NEW.payload->'revision' IS DISTINCT FROM to_jsonb(NEW.revision)
                OR NEW.payload->'page_version' IS DISTINCT FROM to_jsonb(NEW.page_version)
                OR NEW.payload->'source' IS DISTINCT FROM jsonb_build_object(
                    'document_id',d.id::text,'source_sha256',d.checksum_sha256,
                    'page_number',NEW.page_number,'image_sha256',c.image_sha256)
                OR NEW.payload->'content' IS DISTINCT FROM expected_content
                OR decision->>'content_fingerprint'
                    IS DISTINCT FROM public.source_understanding_fingerprint(expected_content)
                OR decision->>'policy_version' IS DISTINCT FROM 'source-fidelity-verification.v1'
                OR decision->>'actor_id' IS DISTINCT FROM NEW.created_by::text
                OR decision->'compared_with_original' IS DISTINCT FROM 'true'::jsonb
                OR r.payload->>'source_checker_version'
                    IS DISTINCT FROM 'source-fidelity-v2/rules-2/ucd-15.0.0'
                OR EXISTS(SELECT 1 FROM jsonb_array_elements(r.payload->'findings') x
                    WHERE x->>'severity'='block')
                OR NOT public.source_understanding_keys_match(
                    decision->'reviewed_region_keys',(SELECT jsonb_agg(x->'key')
                        FROM jsonb_array_elements(c.observation->'regions') x),true)
                OR NOT public.source_understanding_keys_match(
                    decision->'resolved_uncertainty_keys',
                    (SELECT coalesce(jsonb_agg(x->'key'),'[]'::jsonb)
                        FROM jsonb_array_elements(c.uncertainties) x),true)
                OR jsonb_typeof(decision->'reason') IS DISTINCT FROM 'string'
                OR length(btrim(decision->>'reason')) NOT BETWEEN 1 AND 2000
                OR decision->>'reason'<>btrim(decision->>'reason')
                OR decision->>'reason' ~ '[[:cntrl:]]'
                OR a.id IS NULL OR a.actor_id<>NEW.created_by OR a.resource_id<>NEW.document_id
                OR a.resource_type<>'verified_source_content'
                OR a.action<>'source_reading.verified'
                OR a.payload->>'verified_source_id' IS DISTINCT FROM NEW.id::text
                OR a.payload->>'fingerprint' IS DISTINCT FROM NEW.fingerprint
                OR EXISTS(SELECT 1 FROM jsonb_array_elements(c.observation->'regions') region,
                    jsonb_array_elements(coalesce(
                        nullif(region->'table','null'::jsonb)->'cells','[]'::jsonb)) cell
                    WHERE cell->>'state'='unreadable')
            THEN RAISE EXCEPTION 'verified source requires exact current human comparison evidence'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_verified_source_content_trigger
            BEFORE INSERT ON public.verified_source_contents
            FOR EACH ROW EXECUTE FUNCTION public.validate_verified_source_content();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_source_reading_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE j public.source_understanding_jobs%ROWTYPE; a public.admin_audit_events%ROWTYPE;
            previous public.source_reading_events%ROWTYPE; image jsonb;
        BEGIN
            SELECT * INTO j FROM public.source_understanding_jobs WHERE id=NEW.job_id FOR UPDATE;
            SELECT * INTO a FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF j.id IS NULL OR j.status<>'running' OR j.lease_token IS DISTINCT FROM NEW.lease_token
                OR j.created_by<>NEW.created_by OR j.provider_started_at IS NULL
                OR NEW.payload->'pass_number' IS DISTINCT FROM to_jsonb(NEW.pass_number)
                OR NEW.payload->>'event' IS DISTINCT FROM NEW.event
                OR NEW.payload->>'model' IS DISTINCT FROM j.profile->>'model_version'
                OR NEW.payload->>'prompt_version' IS DISTINCT FROM j.profile->>'prompt_version'
                OR NEW.payload->>'detail' IS DISTINCT FROM 'original'
                OR jsonb_typeof(NEW.payload->'images') IS DISTINCT FROM 'array'
                OR jsonb_array_length(NEW.payload->'images') NOT BETWEEN 1 AND 16
                OR a.id IS NULL OR a.actor_id<>NEW.created_by
                OR a.resource_type<>'source_reading_pass'
                OR a.resource_id<>j.document_id OR a.action<>('source_reading.'||NEW.event)
                OR a.payload->>'fingerprint' IS DISTINCT FROM NEW.fingerprint
                OR a.payload->>'job_id' IS DISTINCT FROM NEW.job_id::text
            THEN RAISE EXCEPTION 'source reading event requires its current owned provider request'
                USING ERRCODE='23514'; END IF;
            FOR image IN SELECT * FROM jsonb_array_elements(NEW.payload->'images') LOOP
                IF image->>'parent_sha256' IS DISTINCT FROM j.image_metadata->>'sha256'
                    OR coalesce(image->>'sha256','') !~ '^[0-9a-f]{64}$'
                    OR (image->>'left')::integer<0 OR (image->>'top')::integer<0
                    OR (image->>'width')::integer<1 OR (image->>'height')::integer<1
                    OR (image->>'left')::integer+(image->>'width')::integer
                        >(j.image_metadata->>'width')::integer
                    OR (image->>'top')::integer+(image->>'height')::integer
                        >(j.image_metadata->>'height')::integer
                THEN RAISE EXCEPTION 'reading crop must remain inside its exact original image'
                    USING ERRCODE='23514'; END IF;
            END LOOP;
            SELECT * INTO previous FROM public.source_reading_events
                WHERE job_id=NEW.job_id AND pass_number=NEW.pass_number AND event='requested';
            IF NEW.event<>'requested' AND (previous.id IS NULL
                OR previous.payload->>'request_fingerprint'
                    IS DISTINCT FROM NEW.payload->>'request_fingerprint'
                OR previous.payload->'images' IS DISTINCT FROM NEW.payload->'images'
                OR EXISTS(SELECT 1 FROM public.source_reading_events WHERE job_id=NEW.job_id
                    AND pass_number=NEW.pass_number AND event<>'requested'))
            THEN RAISE EXCEPTION 'reading completion must bind its one recorded request'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_source_reading_event_trigger
            BEFORE INSERT ON public.source_reading_events
            FOR EACH ROW EXECUTE FUNCTION public.validate_source_reading_event();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_source_education()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE v public.verified_source_contents%ROWTYPE; a public.admin_audit_events%ROWTYPE;
        BEGIN
            SELECT * INTO v FROM public.verified_source_contents WHERE id=NEW.verified_source_id;
            IF v.id IS NULL THEN
                RAISE EXCEPTION 'educational analysis requires verified source content'
                    USING ERRCODE='23514';
            END IF;
            PERFORM 1 FROM public.source_documents WHERE id=v.document_id FOR SHARE;
            PERFORM 1 FROM public.source_understanding_pages WHERE document_id=v.document_id
                AND page_number=v.page_number FOR UPDATE;
            IF TG_OP='INSERT' THEN
                IF NEW.status<>'queued' OR NEW.version<>0
                    OR NOT public.verified_source_content_is_current(v.id)
                    OR NEW.provider_started_at IS NOT NULL OR NEW.content IS NOT NULL
                    OR NEW.accounting IS NOT NULL
                THEN
                    RAISE EXCEPTION
                        'educational analysis starts only after current source verification'
                        USING ERRCODE='23514';
                END IF;
            ELSE
                IF OLD.status NOT IN ('queued','running') OR NEW.version<>OLD.version+1
                    OR (NEW.id,NEW.verified_source_id,NEW.request_id,NEW.request_fingerprint,
                        NEW.profile,NEW.budget,NEW.created_by,NEW.created_at,NEW.reason)
                        IS DISTINCT FROM
                       (OLD.id,OLD.verified_source_id,OLD.request_id,OLD.request_fingerprint,
                        OLD.profile,OLD.budget,OLD.created_by,OLD.created_at,OLD.reason)
                    OR (OLD.status='queued' AND NEW.status NOT IN ('running','failed'))
                    OR (OLD.status='running'
                        AND NEW.status NOT IN ('running','provider_completed','failed','unknown'))
                THEN
                    RAISE EXCEPTION 'educational analysis history and input identity are immutable'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.profile->>'schema_version' IS DISTINCT FROM 'educational-analysis.v1'
                OR NEW.profile->>'prompt_version' IS DISTINCT FROM 'verified-source-education.v1'
                OR NEW.request_fingerprint
                    IS DISTINCT FROM public.source_understanding_fingerprint(jsonb_build_object(
                        'verified_source_id',v.id::text,'verified_source_fingerprint',v.fingerprint,
                        'profile',NEW.profile,'budget',NEW.budget,'reason',NEW.reason))
                OR (NEW.status IN ('running','provider_completed')
                    AND NOT public.verified_source_content_is_current(v.id))
                OR (NEW.status='provider_completed' AND (
                    NEW.accounting IS NULL OR NEW.provider_started_at IS NULL
                    OR NEW.content->>'schema_version' IS DISTINCT FROM 'educational-analysis.v1'
                    OR jsonb_typeof(NEW.content->'education') IS DISTINCT FROM 'object'
                    OR NEW.content-ARRAY['schema_version','education','uncertainties']
                        <>'{}'::jsonb))
            THEN
                RAISE EXCEPTION
                    'educational output must bind current verified source and its approved profile'
                    USING ERRCODE='23514';
            END IF;
            SELECT * INTO a FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF a.id IS NULL OR a.actor_id<>NEW.created_by
                OR a.resource_type<>'source_educational_analysis'
                OR a.resource_id<>NEW.id OR a.action<>('source_education.'||NEW.status)
                OR a.payload->'version' IS DISTINCT FROM to_jsonb(NEW.version)
                OR a.payload->>'verified_source_id' IS DISTINCT FROM NEW.verified_source_id::text
                OR a.payload->>'request_fingerprint' IS DISTINCT FROM NEW.request_fingerprint
            THEN RAISE EXCEPTION 'educational analysis requires its versioned source audit'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_source_education_trigger
            BEFORE INSERT OR UPDATE ON public.source_educational_analyses
            FOR EACH ROW EXECUTE FUNCTION public.validate_source_education();
    """)
    op.execute("""
        CREATE FUNCTION public.require_verified_source_before_trusted_knowledge()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS(SELECT 1 FROM public.verified_source_contents v
                WHERE v.document_id=NEW.document_id AND v.page_number=NEW.page_number
                    AND v.candidate_id=NEW.candidate_id
                    AND public.source_education_is_current(v.id))
            THEN
                RAISE EXCEPTION
                    'verified source and later educational analysis are required '
                    'before trusted knowledge' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER require_verified_source_before_trusted_knowledge_trigger
            BEFORE INSERT ON public.trusted_page_knowledge
            FOR EACH ROW
            EXECUTE FUNCTION public.require_verified_source_before_trusted_knowledge();
    """)
    for table in ("verified_source_contents", "source_reading_events"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )
    for table in (
        "verified_source_contents",
        "source_reading_events",
        "source_educational_analyses",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table}_truncate BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )
    op.execute(
        "CREATE TRIGGER immutable_source_education_delete "
        "BEFORE DELETE ON public.source_educational_analyses "
        "FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
    )
    _currentness(True)


def _currentness(require_source: bool) -> None:
    trusted_gate = (
        """AND EXISTS(SELECT 1 FROM public.verified_source_contents v
        WHERE v.candidate_id=k.candidate_id AND public.source_education_is_current(v.id))"""
        if require_source
        else ""
    )
    legacy_gate = (
        """AND EXISTS(SELECT 1 FROM public.verified_source_contents v,
        jsonb_array_elements(v.payload->'content'->'observation'->'regions') region
        WHERE v.document_id=source_id AND v.page_number=number
            AND public.source_education_is_current(v.id)
            AND strpos(normalize(coalesce(region->>'exact_text',''),NFC),source_text)>0)"""
        if require_source
        else ""
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.trusted_page_knowledge_is_current(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS(SELECT 1 FROM public.trusted_page_knowledge k
                JOIN public.source_understanding_pages p ON p.document_id=k.document_id
                    AND p.page_number=k.page_number AND p.current_trusted_id=k.id
                    AND p.current_candidate_id=k.candidate_id AND p.state='verified'
                JOIN public.source_documents d ON d.id=k.document_id
                WHERE k.id=identifier AND d.active_for_ai AND NOT d.quarantined_for_teacher_use
                    AND d.checksum_sha256=k.payload->'source'->>'source_sha256'
                    AND k.payload->'decision'->>'policy_version'
                        ='page-understanding-verification.v1'
                    AND k.payload->'decision'->>'source_checker_version'
                        ='source-fidelity-v2/rules-2/ucd-15.0.0'
                    __SOURCE_GATE__);
        $$;
    """.replace("__SOURCE_GATE__", trusted_gate)
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.knowledge_source_lineage_is_current(
            source_id uuid, number integer, candidate_id uuid, curriculum_id uuid, source_text text
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT candidate_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.source_documents d
                JOIN public.source_page_text_candidates c ON c.document_id=d.id
                    AND c.page_number=number AND c.id=candidate_id
                WHERE d.id=source_id AND d.curriculum_version_id=curriculum_id AND d.active_for_ai
                    AND NOT d.metadata_review_required
                    AND public.catalogue_curriculum_is_admitted(curriculum_id)
                    AND public.source_document_fidelity_is_current(source_id)
                    AND public.source_page_fidelity_is_current(source_id,number,candidate_id)
                    AND c.normalized_text IS NOT NULL AND c.can_confirm
                    AND c.text_sha256=encode(sha256(convert_to(c.normalized_text,'UTF8')),'hex')
                    AND source_text=normalize(source_text,NFC) AND length(btrim(source_text))>0
                    AND strpos(c.normalized_text,source_text)>0 __SOURCE_GATE__);
        $$;
    """.replace("__SOURCE_GATE__", legacy_gate)
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.verified_source_contents)
            OR EXISTS(SELECT 1 FROM public.source_reading_events)
            OR EXISTS(SELECT 1 FROM public.source_educational_analyses)
        THEN RAISE EXCEPTION 'cannot discard source verification or reading history'; END IF;
    END; $$;""")
    _currentness(False)
    op.execute(
        "DROP TRIGGER require_verified_source_before_trusted_knowledge_trigger "
        "ON public.trusted_page_knowledge"
    )
    op.execute("DROP FUNCTION public.require_verified_source_before_trusted_knowledge()")
    for name in ("source_education_is_current", "verified_source_content_is_current"):
        op.execute(f"DROP FUNCTION public.{name}(uuid)")
    for table in (
        "source_educational_analyses",
        "source_reading_events",
        "verified_source_contents",
    ):
        op.drop_table(table)
    for name in (
        "validate_source_education",
        "validate_source_reading_event",
        "validate_verified_source_content",
    ):
        op.execute(f"DROP FUNCTION public.{name}()")
