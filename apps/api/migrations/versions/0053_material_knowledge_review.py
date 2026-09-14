import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053_material_knowledge_review"
down_revision = "0052_knowledge_preparation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.material_index_config_valid(c jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
            SELECT coalesce(jsonb_typeof(c)='object' AND octet_length(c::text)<=2048
                AND c=jsonb_build_object('provider',c->'provider','model',c->'model',
                    'dimension',c->'dimension','version',c->'version',
                    'config_fingerprint',c->'config_fingerprint')
                AND jsonb_typeof(c->'provider')='string'
                AND c->>'provider' IN ('deterministic','openai')
                AND jsonb_typeof(c->'model')='string'
                AND length(c->>'model') BETWEEN 1 AND 128
                AND c->>'model'=btrim(c->>'model')
                AND jsonb_typeof(c->'version')='string'
                AND length(c->>'version') BETWEEN 1 AND 64
                AND c->>'version'=btrim(c->>'version')
                AND jsonb_typeof(c->'dimension')='number'
                AND c->>'dimension' ~ '^[1-9][0-9]{0,3}$'
                AND (c->>'dimension')::integer BETWEEN 1 AND 4096
                AND jsonb_typeof(c->'config_fingerprint')='string'
                AND c->>'config_fingerprint' ~ '^sha256:[0-9a-f]{64}$',false);
        $$;
    """)
    op.create_table(
        "material_knowledge_index_intents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *(
            sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=False)
            for name, target in (
                ("document_id", "source_documents.id"),
                ("unit_id", "knowledge_units.id"),
                ("review_id", "knowledge_unit_reviews.id"),
                ("curriculum_version_id", "curriculum_versions.id"),
            )
        ),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("review_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "projection_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_projections.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dispatch_key", sa.String(128), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("config_snapshot_fingerprint", sa.String(64), nullable=True),
        sa.Column(
            "embedding_job_id",
            sa.Uuid(),
            sa.ForeignKey("embedding_jobs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("confirmed_retry", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "previous_audit_event_id",
            sa.Uuid(),
            sa.ForeignKey("admin_audit_events.id", ondelete="RESTRICT"),
            nullable=True,
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
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("review_id", name="uq_material_index_review"),
        sa.UniqueConstraint("dispatch_key", name="uq_material_index_dispatch"),
        sa.UniqueConstraint("audit_event_id", name="uq_material_index_audit"),
        sa.CheckConstraint(
            "version BETWEEN 0 AND 2147483646 AND review_version BETWEEN 1 AND 2147483646 "
            "AND attempt_number BETWEEN 0 AND 4",
            name="ck_material_index_versions",
        ),
        sa.CheckConstraint(
            "status IN ('pending','waiting_configuration','dispatching','queued','ready',"
            "'needs_attention','superseded','not_searchable','configuration_changed')",
            name="ck_material_index_status",
        ),
        sa.CheckConstraint(
            "review_fingerprint ~ '^[0-9a-f]{64}$' AND input_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND input_fingerprint=public.source_understanding_fingerprint(input_snapshot) "
            "AND jsonb_typeof(input_snapshot)='object' "
            "AND octet_length(input_snapshot::text)<=16384",
            name="ck_material_index_input",
        ),
        sa.CheckConstraint(
            "((projection_id IS NULL AND status IN ('not_searchable','superseded') "
            "AND attempt_number=0) OR (projection_id IS NOT NULL AND status<>'not_searchable'))",
            name="ck_material_index_projection",
        ),
        sa.CheckConstraint(
            "((attempt_number=0 AND dispatch_key IS NULL AND config_snapshot IS NULL "
            "AND config_snapshot_fingerprint IS NULL AND embedding_job_id IS NULL) OR "
            "(attempt_number>0 AND dispatch_key IS NOT NULL AND config_snapshot IS NOT NULL "
            "AND config_snapshot_fingerprint IS NOT NULL AND projection_id IS NOT NULL "
            "AND dispatch_key='material-knowledge:'||id::text||':attempt:'||attempt_number::text "
            "AND config_snapshot_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND config_snapshot_fingerprint="
            "public.source_understanding_fingerprint(config_snapshot) "
            "AND public.material_index_config_valid(config_snapshot))) IS TRUE",
            name="ck_material_index_binding",
        ),
        sa.CheckConstraint(
            "(status NOT IN ('dispatching','queued','ready','configuration_changed') "
            "OR attempt_number>0) AND (status<>'queued' OR embedding_job_id IS NOT NULL) "
            "AND (status<>'pending' OR attempt_number=0)",
            name="ck_material_index_state_binding",
        ),
        sa.CheckConstraint(
            "((status IN ('pending','dispatching','queued','ready','not_searchable') "
            "AND failure_code IS NULL) OR "
            "(status='waiting_configuration' AND failure_code='configuration_unavailable') OR "
            "(status='configuration_changed' AND failure_code='configuration_changed') OR "
            "(status='superseded' AND failure_code='indexing_source_superseded') OR "
            "(status='needs_attention' AND failure_code IN ('indexing_job_failed',"
            "'indexing_outcome_unknown','indexing_retry_exhausted','indexing_binding_invalid'))) "
            "IS TRUE",
            name="ck_material_index_failure",
        ),
        sa.CheckConstraint(
            "event IN ('requested','not_searchable','dispatch_bound','retry_approved',"
            "'linked','observed','superseded') AND public.knowledge_review_reason_valid(reason) "
            "AND confirmed_retry=(event='retry_approved') AND updated_at>=created_at",
            name="ck_material_index_event",
        ),
    )
    op.create_index(
        "ix_material_index_recovery",
        "material_knowledge_index_intents",
        ["status", "updated_at", "id"],
    )
    op.create_index(
        "ix_material_index_unit", "material_knowledge_index_intents", ["unit_id", "review_version"]
    )
    op.create_index(
        "uq_material_index_active_unit",
        "material_knowledge_index_intents",
        ["unit_id"],
        unique=True,
        postgresql_where=sa.text("status<>'superseded'"),
    )
    _identity_functions()
    _binding_functions()
    _guards()


def _identity_functions() -> None:
    op.execute("""
        CREATE FUNCTION public.material_knowledge_index_input(uid uuid, rid uuid) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
            SELECT jsonb_build_object('schema_version','material-knowledge-index-input.v1',
                'document_id',u.document_id::text,'unit_id',u.id::text,
                'unit_fingerprint',u.fingerprint,'page_number',u.page_number,
                'source_sha256',u.payload->'source'->>'source_sha256',
                'candidate_id',u.candidate_id::text,'trusted_page_id',u.trusted_page_id::text,
                'trusted_fingerprint',u.payload->>'trusted_fingerprint',
                'scope',u.payload->'scope','scope_fingerprint',u.scope_fingerprint,
                'derivation_version',u.derivation_version,
                'curriculum_version_id',u.curriculum_version_id::text,
                'review_id',r.id::text,'review_version',r.version,'review_fingerprint',r.fingerprint,
                'projection_id',p.id::text,'projection_fingerprint',p.fingerprint,
                'projection_text_sha256',p.text_sha256,'transformation_version',p.transformation_version)
            FROM public.knowledge_units u JOIN public.knowledge_unit_reviews r ON r.unit_id=u.id
                LEFT JOIN public.knowledge_projections p ON p.unit_id=u.id
                    AND p.transformation_version='source-observation-meaning.v1'
            WHERE u.id=uid AND r.id=rid;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.material_index_has_vector(pid uuid, c jsonb) RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.knowledge_embeddings e
                JOIN public.knowledge_projections p ON p.id=e.knowledge_projection_id
                JOIN public.embedding_configurations cfg ON cfg.id=e.embedding_configuration_id
                WHERE p.id=pid AND e.source_text_sha256=p.text_sha256
                    AND cfg.provider=c->>'provider' AND cfg.model=c->>'model'
                    AND cfg.version=c->>'version'
                    AND cfg.config_fingerprint=c->>'config_fingerprint'
                    AND to_jsonb(cfg.dimension)=c->'dimension'
                    AND e.embedding_dimension=cfg.dimension
                    AND public.knowledge_projection_is_eligible(p.id));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.material_index_event(i public.material_knowledge_index_intents)
        RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
            SELECT jsonb_build_object('intent_id',i.id::text,'document_id',i.document_id::text,
                'unit_id',i.unit_id::text,'review_id',i.review_id::text,
                'review_version',i.review_version,'review_fingerprint',i.review_fingerprint,
                'projection_id',i.projection_id::text,'input_fingerprint',i.input_fingerprint,
                'requested_by',i.requested_by::text,'status',i.status,'version',i.version,
                'attempt_number',i.attempt_number,'dispatch_key',i.dispatch_key,
                'config_snapshot',i.config_snapshot,'config_snapshot_fingerprint',i.config_snapshot_fingerprint,
                'embedding_job_id',i.embedding_job_id::text,'failure_code',i.failure_code,
                'event',i.event,'reason',i.reason,'confirmed_retry',i.confirmed_retry,
                'previous_audit_event_id',i.previous_audit_event_id::text);
        $$;
    """)


def _binding_functions() -> None:
    op.execute("""
        CREATE FUNCTION public.material_index_job_matches(
            i public.material_knowledge_index_intents, jid uuid) RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.embedding_jobs j
                JOIN public.admin_audit_events a ON a.resource_id=j.id
                    AND a.resource_type='embedding_job' AND a.action='embedding_job.created'
                WHERE j.id=jid AND j.created_by=i.requested_by AND a.actor_id=i.requested_by
                    AND j.idempotency_key_hash='sha256:'||
                        public.source_understanding_fingerprint(to_jsonb(i.dispatch_key))
                    AND j.curriculum_version_id=i.curriculum_version_id
                    AND j.historical_question_ids='[]'::jsonb AND j.knowledge_chunk_ids='[]'::jsonb
                    AND j.knowledge_projection_ids=jsonb_build_array(i.projection_id::text)
                    AND j.requested_count=1
                    AND i.config_snapshot=jsonb_build_object('provider',j.provider,'model',j.model,
                        'dimension',j.dimension,'version',j.embedding_version,'config_fingerprint',j.config_fingerprint)
                    AND a.payload=jsonb_build_object(
                        'curriculum_version_id',j.curriculum_version_id::text,
                        'retry_of_job_id',j.retry_of_job_id::text,'retry_depth',j.retry_depth,
                        'requested_count',j.requested_count,'provider',j.provider,'model',j.model,
                        'dimension',j.dimension,'embedding_version',j.embedding_version,
                        'config_fingerprint',j.config_fingerprint,'status','queued',
                        'projection_sources',jsonb_build_array(jsonb_build_object(
                            'projection_id',i.projection_id::text,'lineage',jsonb_build_object(
                                'schema_version','projection-embedding-lineage.v1',
                                'projection_fingerprint',i.input_snapshot->>'projection_fingerprint',
                                'unit_id',i.unit_id::text,'unit_fingerprint',i.input_snapshot->>'unit_fingerprint',
                                'trusted_page_id',i.input_snapshot->>'trusted_page_id',
                                'trusted_fingerprint',i.input_snapshot->>'trusted_fingerprint',
                                'review_id',i.review_id::text,'review_fingerprint',i.review_fingerprint)))));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.material_index_retryable(
            i public.material_knowledge_index_intents, c jsonb) RETURNS boolean
        LANGUAGE plpgsql STABLE AS $$
        DECLARE j public.embedding_jobs%ROWTYPE; depth integer;
        BEGIN
            IF i.attempt_number NOT BETWEEN 1 AND 3 OR NOT public.material_index_config_valid(c)
                OR i.status IN ('pending','waiting_configuration','dispatching',
                    'superseded','not_searchable')
                OR NOT public.knowledge_unit_review_is_eligible(i.review_id)
                OR public.material_index_has_vector(i.projection_id,c)
            THEN RETURN false; END IF;
            SELECT * INTO j FROM public.embedding_jobs
                WHERE created_by=i.requested_by
                    AND idempotency_key_hash='sha256:'||
                        public.source_understanding_fingerprint(to_jsonb(i.dispatch_key));
            IF j.id IS NULL THEN
                RETURN i.embedding_job_id IS NULL AND (i.status='configuration_changed'
                    OR (i.status='ready' AND i.config_snapshot IS DISTINCT FROM c));
            END IF;
            IF NOT public.material_index_job_matches(i,j.id)
                OR (i.embedding_job_id IS NOT NULL AND i.embedding_job_id<>j.id)
            THEN RETURN false; END IF;
            IF j.status='succeeded' THEN RETURN i.config_snapshot IS DISTINCT FROM c; END IF;
            IF j.status<>'failed' OR j.failure_code NOT IN ('embedding_config_unavailable',
                'embedding_config_conflict','embedding_source_invalid','embedding_source_conflict',
                'embedding_contract_error') THEN RETURN false; END IF;
            SELECT max(retry_depth) INTO depth FROM public.embedding_jobs
                WHERE created_by=i.requested_by AND curriculum_version_id=i.curriculum_version_id
                    AND request_fingerprint=j.request_fingerprint AND status='failed';
            RETURN j.retry_depth<3 AND coalesce(depth,0)<3;
        END; $$;
    """)


def _guards() -> None:
    op.execute("""
        CREATE FUNCTION public.validate_material_index_intent() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE r public.knowledge_unit_reviews%ROWTYPE; expected jsonb; eligible boolean;
        BEGIN
            PERFORM public.lock_knowledge_unit_source(NEW.document_id);
            SELECT * INTO r FROM public.knowledge_unit_reviews WHERE id=NEW.review_id;
            IF r.id IS NULL THEN RAISE EXCEPTION
                'material indexing requires a review'
                    USING ERRCODE='23514'; END IF;
            PERFORM public.lock_knowledge_review_taxonomy(r.curriculum_unit_id,r.lesson_id,
                r.competency_id,r.skill_id,r.sub_skill_id,r.learning_concept_id);
            expected:=public.material_knowledge_index_input(NEW.unit_id,NEW.review_id);
            IF expected IS NULL OR NEW.input_snapshot IS DISTINCT FROM expected
                OR r.state<>'reviewed' OR NOT r.confirmed_mapping
                OR (NEW.document_id::text,NEW.curriculum_version_id::text,NEW.projection_id::text,
                    NEW.review_version,NEW.review_fingerprint,NEW.requested_by)
                IS DISTINCT FROM (expected->>'document_id',expected->>'curriculum_version_id',
                    expected->>'projection_id',r.version,r.fingerprint,r.created_by)
            THEN RAISE EXCEPTION
                'material indexing input differs from immutable reviewed evidence'
                    USING ERRCODE='23514'; END IF;
            eligible:=public.knowledge_unit_review_is_eligible(NEW.review_id);
            IF TG_OP='INSERT' THEN
                IF NOT eligible OR NEW.version<>0 OR NEW.attempt_number<>0
                    OR NEW.embedding_job_id IS NOT NULL OR NEW.dispatch_key IS NOT NULL
                    OR NEW.previous_audit_event_id IS NOT NULL OR NEW.updated_by<>NEW.requested_by
                    OR NEW.confirmed_retry OR NEW.reason<>r.reason
                    OR (NEW.projection_id IS NULL AND
                        (NEW.status<>'not_searchable' OR NEW.event<>'not_searchable'))
                    OR (NEW.projection_id IS NOT NULL AND
                        (NEW.status<>'pending' OR NEW.event<>'requested'))
                THEN RAISE EXCEPTION
                'material indexing requires an explicit unexecuted current review'
                    USING ERRCODE='23514'; END IF;
            ELSE
                IF (NEW.id,NEW.document_id,NEW.unit_id,NEW.review_id,NEW.review_version,
                    NEW.review_fingerprint,NEW.curriculum_version_id,NEW.projection_id,
                    NEW.input_snapshot,NEW.input_fingerprint,NEW.requested_by,NEW.created_at)
                    IS DISTINCT FROM (OLD.id,OLD.document_id,OLD.unit_id,
                    OLD.review_id,OLD.review_version,
                    OLD.review_fingerprint,OLD.curriculum_version_id,OLD.projection_id,
                    OLD.input_snapshot,OLD.input_fingerprint,OLD.requested_by,OLD.created_at)
                    OR NEW.version<>OLD.version+1 OR NEW.updated_at<OLD.updated_at
                    OR NEW.previous_audit_event_id IS DISTINCT FROM OLD.audit_event_id
                    OR NEW.audit_event_id=OLD.audit_event_id
                    OR NEW.event IN ('requested','not_searchable')
                THEN RAISE EXCEPTION
                'material indexing identity and audit chain are immutable'
                    USING ERRCODE='23514'; END IF;
                IF OLD.status='superseded' AND NOT (NEW.status='superseded' AND NEW.event='linked'
                    AND OLD.embedding_job_id IS NULL AND NEW.embedding_job_id IS NOT NULL)
                THEN RAISE EXCEPTION
                'superseded indexing authority cannot be reused'
                    USING ERRCODE='23514'; END IF;
                IF NEW.event IN ('dispatch_bound','retry_approved') THEN
                    IF NOT eligible OR NEW.status<>'dispatching' OR NEW.embedding_job_id IS NOT NULL
                        OR NEW.attempt_number<>OLD.attempt_number+1
                        OR (NEW.event='dispatch_bound' AND (OLD.attempt_number<>0
                            OR OLD.status NOT IN ('pending','waiting_configuration')))
                        OR (NEW.event='retry_approved' AND
                            NOT public.material_index_retryable(OLD,NEW.config_snapshot))
                    THEN RAISE EXCEPTION
                'material indexing dispatch or explicit retry is invalid'
                    USING ERRCODE='23514'; END IF;
                ELSIF (NEW.attempt_number,NEW.dispatch_key,
                    NEW.config_snapshot,NEW.config_snapshot_fingerprint)
                    IS DISTINCT FROM (OLD.attempt_number,OLD.dispatch_key,
                    OLD.config_snapshot,OLD.config_snapshot_fingerprint)
                THEN RAISE EXCEPTION
                'material indexing dispatch binding cannot change without explicit retry'
                    USING ERRCODE='23514'; END IF;
                IF NEW.event NOT IN ('retry_approved','linked')
                    AND NEW.embedding_job_id IS DISTINCT FROM OLD.embedding_job_id
                THEN RAISE EXCEPTION
                'material indexing job link is immutable'
                    USING ERRCODE='23514'; END IF;
                IF NEW.event='linked'
                    AND (OLD.embedding_job_id IS NOT NULL OR NEW.embedding_job_id IS NULL)
                THEN RAISE EXCEPTION
                'material indexing requires one exact job link'
                    USING ERRCODE='23514'; END IF;
                IF OLD.status='not_searchable' AND NEW.status<>'superseded'
                THEN RAISE EXCEPTION
                'decorative indexing intent cannot dispatch'
                    USING ERRCODE='23514'; END IF;
                IF NEW.status='superseded' AND eligible AND OLD.status<>'superseded'
                THEN RAISE EXCEPTION
                'supersession requires stale review or source'
                    USING ERRCODE='23514'; END IF;
                IF NEW.status<>'superseded' AND NOT eligible
                THEN RAISE EXCEPTION
                'material indexing requires current review and source'
                    USING ERRCODE='23514'; END IF;
                IF OLD.status IN ('needs_attention','configuration_changed','ready')
                    AND NEW.event NOT IN ('retry_approved','linked')
                    AND NEW.status NOT IN ('needs_attention','configuration_changed',
                        'ready','superseded')
                THEN RAISE EXCEPTION 'stopped indexing requires explicit retry approval'
                    USING ERRCODE='23514'; END IF;
                IF NEW.status='dispatching'
                    AND NEW.event NOT IN ('dispatch_bound','retry_approved','observed')
                THEN RAISE EXCEPTION
                'material indexing dispatch state is invalid'
                    USING ERRCODE='23514'; END IF;
            END IF;
            IF NEW.embedding_job_id IS NOT NULL
                AND NOT public.material_index_job_matches(NEW,NEW.embedding_job_id)
            THEN RAISE EXCEPTION
                'material indexing job differs from persisted dispatch binding'
                    USING ERRCODE='23514'; END IF;
            IF NEW.status='ready'
                AND NOT public.material_index_has_vector(NEW.projection_id,NEW.config_snapshot)
            THEN RAISE EXCEPTION
                'material indexing readiness requires a current exact vector'
                    USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.validate_material_index_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events a
                WHERE a.id=NEW.audit_event_id AND a.actor_id=NEW.updated_by
                    AND a.resource_type='material_knowledge_index' AND a.resource_id=NEW.unit_id
                    AND a.action='material_knowledge_index.'||NEW.event
                    AND a.payload=public.material_index_event(NEW))
            THEN RAISE EXCEPTION
                'material indexing requires exact append-only audit evidence'
                    USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_material_index_intent_trigger
            BEFORE INSERT OR UPDATE ON public.material_knowledge_index_intents
            FOR EACH ROW EXECUTE FUNCTION public.validate_material_index_intent();
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER validate_material_index_audit_trigger
            AFTER INSERT OR UPDATE ON public.material_knowledge_index_intents
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.validate_material_index_audit();
    """)
    op.execute("""
        CREATE TRIGGER immutable_material_index_delete
            BEFORE DELETE ON public.material_knowledge_index_intents
            FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE TRIGGER immutable_material_index_truncate
            BEFORE TRUNCATE ON public.material_knowledge_index_intents
            FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.material_knowledge_index_intents)
            THEN RAISE EXCEPTION
                'cannot discard material knowledge indexing history'
                    USING ERRCODE='23514'; END IF;
        END; $$;
    """)
    op.execute(
        "DROP FUNCTION public.material_index_retryable("
        "public.material_knowledge_index_intents,jsonb)"
    )
    op.execute(
        "DROP FUNCTION public.material_index_job_matches("
        "public.material_knowledge_index_intents,uuid)"
    )
    op.execute("DROP FUNCTION public.material_index_event(public.material_knowledge_index_intents)")
    op.drop_table("material_knowledge_index_intents")
    op.execute("DROP FUNCTION public.validate_material_index_audit()")
    op.execute("DROP FUNCTION public.validate_material_index_intent()")
    op.execute("DROP FUNCTION public.material_index_has_vector(uuid,jsonb)")
    op.execute("DROP FUNCTION public.material_knowledge_index_input(uuid,uuid)")
    op.execute("DROP FUNCTION public.material_index_config_valid(jsonb)")
