import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052_knowledge_preparation"
down_revision = "0051_programme_eval_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_knowledge_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column(
            "source_audit_event_id",
            sa.Uuid(),
            sa.ForeignKey("admin_audit_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "audit_event_id",
            sa.Uuid(),
            sa.ForeignKey(
                "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", name="uq_material_knowledge_request_document"),
        sa.UniqueConstraint(
            "id",
            "document_id",
            "source_sha256",
            "requested_by",
            name="uq_material_knowledge_request_source",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_material_knowledge_request_hash"
        ),
    )
    op.create_table(
        "knowledge_preparation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("trusted_page_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_audit_event_id",
            sa.Uuid(),
            sa.ForeignKey("admin_audit_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope_fingerprint", sa.String(64), nullable=False),
        sa.Column("derivation_version", sa.String(64), nullable=False),
        sa.Column("transformation_version", sa.String(64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("unit_count", sa.Integer(), nullable=True),
        sa.Column("projection_count", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
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
        sa.UniqueConstraint(
            "trusted_page_id",
            "scope_fingerprint",
            "derivation_version",
            "transformation_version",
            name="uq_knowledge_preparation_input",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "document_id", "source_sha256", "requested_by"],
            [
                "material_knowledge_requests.id",
                "material_knowledge_requests.document_id",
                "material_knowledge_requests.source_sha256",
                "material_knowledge_requests.requested_by",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_preparation_request",
        ),
        sa.ForeignKeyConstraint(
            ["trusted_page_id", "candidate_id", "document_id", "page_number"],
            [
                "trusted_page_knowledge.id",
                "trusted_page_knowledge.candidate_id",
                "trusted_page_knowledge.document_id",
                "trusted_page_knowledge.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_preparation_trusted",
        ),
        sa.CheckConstraint(
            "page_number>0 AND version>=0 AND attempts BETWEEN 0 AND 3",
            name="ck_knowledge_preparation_versions",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','deferred','succeeded','failed','superseded')",
            name="ck_knowledge_preparation_status",
        ),
        sa.CheckConstraint(
            "derivation_version='page-region-components.v1' "
            "AND transformation_version='source-observation-meaning.v1'",
            name="ck_knowledge_preparation_transforms",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND scope_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND input_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_preparation_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=16384",
            name="ck_knowledge_preparation_input",
        ),
        sa.CheckConstraint(
            "input_fingerprint=public.source_understanding_fingerprint(input_snapshot) "
            "AND scope_fingerprint="
            "public.source_understanding_fingerprint(input_snapshot->'scope')",
            name="ck_knowledge_preparation_input_hash",
        ),
        sa.CheckConstraint(
            "(status='running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at>updated_at "
            "AND lease_expires_at<=updated_at+interval '3600 seconds') "
            "OR (status<>'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_knowledge_preparation_lease",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded','failed','superseded'))=(completed_at IS NOT NULL)",
            name="ck_knowledge_preparation_completed",
        ),
        sa.CheckConstraint(
            "((status='succeeded' AND unit_count BETWEEN 1 AND 128 "
            "AND projection_count BETWEEN 0 AND unit_count) "
            "OR (status<>'succeeded' AND unit_count IS NULL AND projection_count IS NULL)) IS TRUE",
            name="ck_knowledge_preparation_result",
        ),
        sa.CheckConstraint(
            "((status IN ('running','succeeded') AND failure_code IS NULL) "
            "OR (status='queued' AND (failure_code IS NULL OR failure_code IN "
            "('knowledge_preparation_failed','knowledge_preparation_invalid',"
            "'knowledge_preparation_lease_expired'))) "
            "OR (status='deferred' AND failure_code='knowledge_preparation_unavailable') "
            "OR (status='superseded' AND failure_code='knowledge_preparation_superseded') "
            "OR (status='failed' AND failure_code IN "
            "('knowledge_preparation_failed','knowledge_preparation_invalid',"
            "'knowledge_preparation_lease_expired'))) IS TRUE",
            name="ck_knowledge_preparation_failure",
        ),
    )
    op.create_index(
        "ix_knowledge_preparation_recovery",
        "knowledge_preparation_jobs",
        ["status", "updated_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_knowledge_preparation_document",
        "knowledge_preparation_jobs",
        ["document_id", "page_number"],
    )
    _functions()
    _request_guards()
    _job_guards()


def _functions() -> None:
    op.execute("""
        CREATE FUNCTION public.knowledge_preparation_input(trusted_id uuid, scope jsonb)
        RETURNS jsonb LANGUAGE sql STABLE AS $$
            SELECT jsonb_build_object('schema_version','knowledge-preparation-input.v1',
                'document_id',t.document_id::text,'page_number',t.page_number,
                'source_sha256',t.payload->'source'->>'source_sha256',
                'trusted_page_id',t.id::text,'trusted_page_fingerprint',t.fingerprint,
                'scope',scope,'derivation_version','page-region-components.v1',
                'transformation_version','source-observation-meaning.v1')
            FROM public.trusted_page_knowledge t WHERE t.id=trusted_id;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_preparation_input_state(trusted_id uuid, scope jsonb)
        RETURNS text LANGUAGE plpgsql STABLE AS $$
        DECLARE trusted public.trusted_page_knowledge%ROWTYPE;
            page public.source_understanding_pages%ROWTYPE;
            identity jsonb;
        BEGIN
            SELECT * INTO trusted FROM public.trusted_page_knowledge WHERE id=trusted_id;
            IF NOT FOUND THEN RETURN 'superseded'; END IF;
            SELECT * INTO page FROM public.source_understanding_pages
                WHERE document_id=trusted.document_id AND page_number=trusted.page_number;
            IF NOT FOUND OR page.state<>'verified'
                OR page.current_trusted_id IS DISTINCT FROM trusted_id
                OR page.current_candidate_id IS DISTINCT FROM trusted.candidate_id
            THEN RETURN 'superseded'; END IF;
            SELECT jsonb_build_object('schema_version','knowledge-scope.v2',
                'document_id',d.id::text,'source_sha256',d.checksum_sha256,
                'material_type',d.document_type,'year',d.year,'paper_code',d.paper_code,
                'metadata_scope_version',d.metadata_scope_version,
                'curriculum_version_id',d.curriculum_version_id::text,'grade',e.grade,
                'medium_id',cv.medium_id::text,'subject_id',cv.subject_id::text,
                'catalogue_decision_id',a.id::text,'catalogue_version',a.version,
                'catalogue_scope_fingerprint',a.scope_fingerprint,
                'curriculum_unit_id',d.unit_id::text,'lesson_id',d.lesson_id::text)
            INTO identity FROM public.source_documents d
                JOIN public.curriculum_versions cv ON cv.id=d.curriculum_version_id
                JOIN public.exam_configurations e ON e.id=cv.exam_configuration_id
                JOIN public.catalogue_admission_current c ON c.curriculum_version_id=cv.id
                JOIN public.catalogue_admission_decisions a ON a.id=c.decision_id
                    AND a.curriculum_version_id=c.curriculum_version_id AND a.version=c.version
                WHERE d.id=trusted.document_id;
            IF identity IS DISTINCT FROM scope THEN RETURN 'superseded'; END IF;
            IF public.knowledge_scope_snapshot(trusted.document_id) IS DISTINCT FROM scope
                OR NOT public.source_understanding_document_is_resolved(trusted.document_id)
                OR NOT public.trusted_page_knowledge_is_current(trusted_id)
            THEN RETURN 'deferred'; END IF;
            RETURN 'eligible';
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_preparation_page_counts(trusted_id uuid, scope_hash text)
        RETURNS TABLE(unit_count integer, projection_count integer, complete boolean)
        LANGUAGE sql STABLE AS $$
            WITH units AS MATERIALIZED (
                SELECT u.id,u.payload FROM public.knowledge_units u
                WHERE u.trusted_page_id=trusted_id AND u.scope_fingerprint=scope_hash
                    AND u.derivation_version='page-region-components.v1'
            ), counts AS (
                SELECT count(*)::integer AS units,
                    count(*) FILTER (WHERE public.knowledge_projection_text(u.payload)
                        IS NOT NULL)::integer AS searchable,
                    (SELECT count(*)::integer FROM public.knowledge_projections p
                        JOIN units u ON u.id=p.unit_id
                        WHERE p.transformation_version='source-observation-meaning.v1')
                        AS projections
                FROM units u
            )
            SELECT c.units,c.projections,coalesce(c.units>0 AND c.units=(
                SELECT jsonb_array_length(public.knowledge_unit_component_keys(t.payload))
                FROM public.trusted_page_knowledge t WHERE t.id=trusted_id)
                AND c.projections=c.searchable,false) FROM counts c;
        $$;
    """)


def _request_guards() -> None:
    op.execute("""
        CREATE FUNCTION public.validate_material_knowledge_request_source() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE;
            audit public.admin_audit_events%ROWTYPE;
            page public.source_understanding_pages%ROWTYPE;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.source_audit_event_id;
            SELECT * INTO page FROM public.source_understanding_pages
                WHERE document_id=NEW.document_id AND event_id=NEW.source_audit_event_id FOR SHARE;
            IF source.id IS NULL OR source.checksum_sha256<>NEW.source_sha256
                OR NOT source.active_for_ai OR source.quarantined_for_teacher_use
                OR audit.id IS NULL OR audit.actor_id<>NEW.requested_by
                OR audit.resource_type<>'page_understanding' OR audit.resource_id<>NEW.document_id
                OR audit.action NOT IN ('page_understanding.verified','page_understanding.excluded')
                OR page.document_id IS NULL OR page.updated_by<>NEW.requested_by
                OR audit.payload->'version' IS DISTINCT FROM to_jsonb(page.version)
                OR audit.payload->'page_number' IS DISTINCT FROM to_jsonb(page.page_number)
                OR page.page_number NOT BETWEEN 1 AND source.original_page_count
                OR (audit.action='page_understanding.verified' AND
                    (page.state<>'verified'
                        OR NOT public.trusted_page_knowledge_is_current(page.current_trusted_id)
                        OR audit.payload->>'trusted_knowledge_id'
                            IS DISTINCT FROM page.current_trusted_id::text))
                OR (audit.action='page_understanding.excluded' AND
                    (page.state<>'excluded'
                        OR audit.payload->'confirmed_exclusion' IS DISTINCT FROM 'true'::jsonb
                        OR audit.payload->>'source_sha256' IS DISTINCT FROM NEW.source_sha256))
            THEN RAISE EXCEPTION 'material knowledge request requires a matching source decision'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.validate_material_knowledge_request_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events a WHERE a.id=NEW.audit_event_id
                AND a.actor_id=NEW.requested_by AND a.resource_type='material_knowledge'
                AND a.resource_id=NEW.document_id AND a.action='material_knowledge.requested'
                AND a.payload=jsonb_build_object('request_id',NEW.id::text,
                    'source_sha256',NEW.source_sha256,'source_audit_event_id',NEW.source_audit_event_id::text))
            THEN RAISE EXCEPTION 'material knowledge request requires matching audit evidence'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_material_knowledge_request_source_trigger
            BEFORE INSERT ON public.material_knowledge_requests
            FOR EACH ROW EXECUTE FUNCTION public.validate_material_knowledge_request_source();
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER validate_material_knowledge_request_audit_trigger
            AFTER INSERT ON public.material_knowledge_requests DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.validate_material_knowledge_request_audit();
    """)
    op.execute("""
        CREATE TRIGGER immutable_material_knowledge_requests
            BEFORE UPDATE OR DELETE ON public.material_knowledge_requests
            FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE TRIGGER immutable_material_knowledge_requests_truncate
            BEFORE TRUNCATE ON public.material_knowledge_requests
            FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)


def _job_guards() -> None:
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_preparation_job() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE trusted public.trusted_page_knowledge%ROWTYPE;
            eligibility text;
        BEGIN
            PERFORM public.lock_knowledge_unit_source(NEW.document_id);
            SELECT * INTO trusted FROM public.trusted_page_knowledge WHERE id=NEW.trusted_page_id;
            IF trusted.id IS NULL OR NEW.input_snapshot IS DISTINCT FROM
                public.knowledge_preparation_input(NEW.trusted_page_id,NEW.input_snapshot->'scope')
                OR (NEW.document_id,NEW.page_number,NEW.candidate_id,
                    NEW.source_audit_event_id,NEW.source_sha256)
                    IS DISTINCT FROM (trusted.document_id,trusted.page_number,trusted.candidate_id,
                        trusted.audit_event_id,trusted.payload->'source'->>'source_sha256')
            THEN RAISE EXCEPTION 'knowledge preparation input must match immutable trusted evidence'
                USING ERRCODE='23514'; END IF;
            eligibility:=public.knowledge_preparation_input_state(
                NEW.trusted_page_id,NEW.input_snapshot->'scope');
            IF TG_OP='INSERT' THEN
                IF NEW.status<>'queued' OR NEW.version<>0 OR NEW.attempts<>0
                    OR NEW.failure_code IS NOT NULL OR eligibility<>'eligible'
                THEN RAISE EXCEPTION 'knowledge preparation requires an eligible unexecuted input'
                    USING ERRCODE='23514'; END IF;
            ELSE
                IF OLD.status IN ('succeeded','failed','superseded') OR NEW.version<>OLD.version+1
                    OR (NEW.id,NEW.request_id,NEW.document_id,NEW.page_number,NEW.source_sha256,
                        NEW.requested_by,NEW.trusted_page_id,NEW.candidate_id,NEW.source_audit_event_id,
                        NEW.scope_fingerprint,NEW.derivation_version,NEW.transformation_version,
                        NEW.input_snapshot,NEW.input_fingerprint,NEW.created_at)
                    IS DISTINCT FROM
                       (OLD.id,OLD.request_id,OLD.document_id,OLD.page_number,OLD.source_sha256,
                        OLD.requested_by,OLD.trusted_page_id,OLD.candidate_id,OLD.source_audit_event_id,
                        OLD.scope_fingerprint,OLD.derivation_version,OLD.transformation_version,
                        OLD.input_snapshot,OLD.input_fingerprint,OLD.created_at)
                THEN RAISE EXCEPTION
                    'knowledge preparation input and terminal history are immutable'
                    USING ERRCODE='23514'; END IF;
                IF NEW.status='running' THEN
                    IF OLD.status NOT IN ('queued','deferred') OR eligibility<>'eligible'
                        OR NEW.attempts<>OLD.attempts OR NEW.attempts>=3
                    THEN RAISE EXCEPTION 'knowledge preparation claim is invalid'
                        USING ERRCODE='23514'; END IF;
                ELSIF NEW.status IN ('deferred','superseded') THEN
                    IF NEW.status<>eligibility OR NEW.attempts<>OLD.attempts
                    THEN RAISE EXCEPTION
                        'knowledge preparation deferral requires changed eligibility'
                        USING ERRCODE='23514'; END IF;
                ELSIF OLD.status='running' THEN
                    IF NEW.attempts<>OLD.attempts+1
                        OR (NEW.status='failed')<>(NEW.attempts=3 AND NEW.status<>'succeeded')
                        OR (NEW.status='succeeded' AND
                            (eligibility<>'eligible' OR OLD.lease_expires_at<=clock_timestamp()))
                        OR (NEW.status IN ('queued','failed') AND NEW.failure_code IS NULL)
                        OR (NEW.failure_code='knowledge_preparation_lease_expired'
                            AND OLD.lease_expires_at>clock_timestamp())
                    THEN RAISE EXCEPTION 'knowledge preparation completion or retry is invalid'
                        USING ERRCODE='23514'; END IF;
                ELSIF NEW.status<>'queued' OR NEW.attempts<>OLD.attempts OR eligibility<>'eligible'
                THEN RAISE EXCEPTION 'knowledge preparation transition requires an eligible claim'
                    USING ERRCODE='23514'; END IF;
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_knowledge_preparation_job_trigger
            BEFORE INSERT OR UPDATE ON public.knowledge_preparation_jobs
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_preparation_job();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_preparation_job_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE counts record;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events a WHERE a.id=NEW.audit_event_id
                AND a.actor_id=NEW.requested_by AND a.resource_type='knowledge_preparation_job'
                AND a.resource_id=NEW.document_id AND a.action='knowledge_preparation.'||NEW.status
                AND a.payload=jsonb_build_object(
                    'job_id',NEW.id::text,'request_id',NEW.request_id::text,
                    'source_sha256',NEW.source_sha256,
                    'source_audit_event_id',NEW.source_audit_event_id::text,
                    'page_number',NEW.page_number,'trusted_page_id',NEW.trusted_page_id::text,
                    'input_fingerprint',NEW.input_fingerprint,'scope_fingerprint',NEW.scope_fingerprint,
                    'version',NEW.version,'status',NEW.status,'attempts',NEW.attempts,
                    'lease_token',NEW.lease_token::text,'failure_code',NEW.failure_code,
                    'unit_count',NEW.unit_count,'projection_count',NEW.projection_count))
            THEN RAISE EXCEPTION 'knowledge preparation requires matching audit evidence'
                USING ERRCODE='23514'; END IF;
            IF NEW.status='succeeded' THEN
                SELECT * INTO counts FROM public.knowledge_preparation_page_counts(
                    NEW.trusted_page_id,NEW.scope_fingerprint);
                IF NOT counts.complete OR NEW.unit_count IS DISTINCT FROM counts.unit_count
                    OR NEW.projection_count IS DISTINCT FROM counts.projection_count
                    OR public.knowledge_preparation_input_state(
                        NEW.trusted_page_id,NEW.input_snapshot->'scope')<>'eligible'
                THEN RAISE EXCEPTION
                    'knowledge preparation success requires complete current derivation'
                    USING ERRCODE='23514'; END IF;
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER validate_knowledge_preparation_job_audit_trigger
            AFTER INSERT OR UPDATE ON public.knowledge_preparation_jobs
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_preparation_job_audit();
    """)
    op.execute("""
        CREATE TRIGGER immutable_knowledge_preparation_job_delete
            BEFORE DELETE ON public.knowledge_preparation_jobs
            FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE TRIGGER immutable_knowledge_preparation_job_truncate
            BEFORE TRUNCATE ON public.knowledge_preparation_jobs
            FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.material_knowledge_requests)
                OR EXISTS (SELECT 1 FROM public.knowledge_preparation_jobs)
            THEN RAISE EXCEPTION 'cannot discard material knowledge preparation history'
                USING ERRCODE='23514'; END IF;
        END; $$;
    """)
    op.drop_table("knowledge_preparation_jobs")
    op.drop_table("material_knowledge_requests")
    for name in (
        "validate_knowledge_preparation_job_audit",
        "validate_knowledge_preparation_job",
        "validate_material_knowledge_request_audit",
        "validate_material_knowledge_request_source",
    ):
        op.execute(f"DROP FUNCTION public.{name}()")
    op.execute("DROP FUNCTION public.knowledge_preparation_page_counts(uuid,text)")
    op.execute("DROP FUNCTION public.knowledge_preparation_input_state(uuid,jsonb)")
    op.execute("DROP FUNCTION public.knowledge_preparation_input(uuid,jsonb)")
