from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034_catalogue_admission"
down_revision: str | None = "0033_source_page_fidelity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.catalogue_admission_text_valid(value text, maximum integer)
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT value IS NOT NULL AND char_length(value) BETWEEN 1 AND maximum
                AND value = btrim(value)
                AND normalize(value, NFKC) = btrim(normalize(value, NFKC))
                AND value !~ '(^[[:space:]])|([[:space:]]$)|[[:cntrl:]]'
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.catalogue_admission_evidence_valid(value jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE item jsonb;
        BEGIN
            IF jsonb_typeof(value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
            IF jsonb_array_length(value) NOT BETWEEN 1 AND 16 THEN RETURN false; END IF;
            FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(item) IS DISTINCT FROM 'string'
                    OR NOT public.catalogue_admission_text_valid(item #>> '{}', 1024)
                THEN RETURN false; END IF;
            END LOOP;
            RETURN true;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.catalogue_scope_snapshot(curriculum_id uuid)
        RETURNS jsonb LANGUAGE sql STABLE STRICT AS $$
            SELECT jsonb_build_object(
                'schema_version', 'catalogue-scope.v1',
                'curriculum_version_id', cv.id,
                'curriculum_code', cv.code,
                'curriculum_title', cv.title,
                'exam_configuration_id', exam.id,
                'exam_configuration_code', exam.code,
                'exam_configuration_name', exam.name,
                'grade', exam.grade,
                'medium_id', medium.id,
                'medium_code', medium.code,
                'medium_name', medium.name,
                'subject_id', subject.id,
                'subject_code', subject.code,
                'subject_name', subject.name
            )
            FROM public.curriculum_versions cv
            JOIN public.exam_configurations exam ON exam.id = cv.exam_configuration_id
            JOIN public.media medium ON medium.id = cv.medium_id
            JOIN public.subjects subject ON subject.id = cv.subject_id
            WHERE cv.id = curriculum_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.catalogue_scope_fingerprint(snapshot jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT 'sha256:' || encode(sha256(convert_to(
                public.paper_canonical_jsonb(snapshot), 'UTF8'
            )), 'hex')
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.catalogue_scope_labels_approvable(snapshot jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE key text; label text;
        BEGIN
            IF jsonb_typeof(snapshot) IS DISTINCT FROM 'object'
                OR snapshot->>'subject_id' IS NULL
                OR snapshot->>'subject_id' = '00000000-0000-5000-8000-000000000023'
            THEN RETURN false; END IF;
            FOREACH key IN ARRAY ARRAY[
                'exam_configuration_name', 'medium_name', 'subject_name', 'curriculum_title',
                'exam_configuration_code', 'medium_code', 'subject_code', 'curriculum_code'
            ] LOOP
                label := snapshot->>key;
                IF jsonb_typeof(snapshot->key) IS DISTINCT FROM 'string'
                    OR NOT public.catalogue_admission_text_valid(label, 255)
                    OR normalize(label, NFKC) !~ '[[:alnum:]]'
                    OR normalize(label, NFKC) ~*
                        '(^|[^a-z0-9])(e2e[a-z0-9]*|fixtures?[0-9]*|internal|smoke|synthetic)([^a-z0-9]|$)'
                THEN RETURN false; END IF;
                IF key IN (
                    'exam_configuration_name', 'medium_name', 'subject_name', 'curriculum_title'
                ) AND label ~* (
                    '^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                    || '|(sha256:)?[0-9a-f]{64})$'
                ) THEN RETURN false; END IF;
            END LOOP;
            RETURN true;
        END;
        $$
        """
    )
    op.create_table(
        "catalogue_admission_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("scope_fingerprint", sa.String(71), nullable=False),
        sa.Column("scope_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("educational_approval", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=False),
        sa.Column("source_reference", sa.String(1024), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("audit_event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["audit_event_id"], ["admin_audit_events.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "curriculum_version_id", "version", name="uq_catalogue_admission_decision_version"
        ),
        sa.UniqueConstraint(
            "curriculum_version_id", "id", "version", name="uq_catalogue_admission_decision_pointer"
        ),
        sa.UniqueConstraint("audit_event_id", name="uq_catalogue_admission_decision_audit"),
        sa.CheckConstraint("version >= 1", name="ck_catalogue_admission_decision_version"),
        sa.CheckConstraint(
            "state IN ('approved', 'rejected', 'quarantined')",
            name="ck_catalogue_admission_decision_state",
        ),
        sa.CheckConstraint(
            "educational_approval = (state = 'approved')",
            name="ck_catalogue_admission_explicit_approval",
        ),
        sa.CheckConstraint(
            "scope_fingerprint ~ '^sha256:[0-9a-f]{64}$'", name="ck_catalogue_admission_fingerprint"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'object' "
            "AND octet_length(scope_snapshot::text) <= 16384",
            name="ck_catalogue_admission_snapshot",
        ),
        sa.CheckConstraint(
            "public.catalogue_admission_text_valid(reason, 1024)",
            name="ck_catalogue_admission_reason",
        ),
        sa.CheckConstraint(
            "public.catalogue_admission_text_valid(source_reference, 1024)",
            name="ck_catalogue_admission_source_reference",
        ),
        sa.CheckConstraint(
            "public.catalogue_admission_evidence_valid(evidence)",
            name="ck_catalogue_admission_evidence",
        ),
    )
    op.create_table(
        "catalogue_admission_current",
        sa.Column("curriculum_version_id", sa.Uuid(), primary_key=True),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id", "decision_id", "version"],
            [
                "catalogue_admission_decisions.curriculum_version_id",
                "catalogue_admission_decisions.id",
                "catalogue_admission_decisions.version",
            ],
            ondelete="RESTRICT",
            name="fk_catalogue_admission_current_decision",
        ),
        sa.CheckConstraint("version >= 1", name="ck_catalogue_admission_current_version"),
    )
    op.execute(
        """
        CREATE FUNCTION public.catalogue_curriculum_is_admitted(curriculum_id uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.curriculum_versions cv
                JOIN public.exam_configurations exam ON exam.id = cv.exam_configuration_id
                JOIN public.media medium ON medium.id = cv.medium_id
                JOIN public.subjects subject ON subject.id = cv.subject_id
                JOIN public.catalogue_admission_current current
                    ON current.curriculum_version_id = cv.id
                JOIN public.catalogue_admission_decisions decision
                    ON decision.id = current.decision_id
                    AND decision.curriculum_version_id = current.curriculum_version_id
                    AND decision.version = current.version
                WHERE cv.id = curriculum_id
                    AND cv.active AND exam.active AND medium.active AND subject.active
                    AND decision.state = 'approved' AND decision.educational_approval
                    AND decision.scope_snapshot = public.catalogue_scope_snapshot(cv.id)
                    AND decision.scope_fingerprint = public.catalogue_scope_fingerprint(
                        public.catalogue_scope_snapshot(cv.id)
                    )
                    AND public.catalogue_scope_labels_approvable(decision.scope_snapshot)
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_catalogue_admission_decision()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE snapshot jsonb; current_version integer;
        BEGIN
            PERFORM 1 FROM public.curriculum_versions
                WHERE id = NEW.curriculum_version_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'catalogue admission scope does not exist' USING ERRCODE = '23503';
            END IF;
            PERFORM 1 FROM public.curriculum_versions cv
                JOIN public.exam_configurations exam ON exam.id = cv.exam_configuration_id
                JOIN public.media medium ON medium.id = cv.medium_id
                JOIN public.subjects subject ON subject.id = cv.subject_id
                WHERE cv.id = NEW.curriculum_version_id
                FOR SHARE OF cv, exam, medium, subject;
            snapshot := public.catalogue_scope_snapshot(NEW.curriculum_version_id);
            SELECT version INTO current_version FROM public.catalogue_admission_current
                WHERE curriculum_version_id = NEW.curriculum_version_id;
            IF NEW.version IS DISTINCT FROM COALESCE(current_version, 0) + 1 THEN
                RAISE EXCEPTION 'catalogue admission requires contiguous CAS version'
                    USING ERRCODE = '23514';
            END IF;
            IF snapshot IS NULL OR NEW.scope_snapshot IS DISTINCT FROM snapshot
                OR NEW.scope_fingerprint IS DISTINCT FROM
                    public.catalogue_scope_fingerprint(snapshot)
            THEN
                RAISE EXCEPTION 'catalogue admission fingerprint differs from current content'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.state = 'approved' THEN
                IF NOT public.catalogue_scope_labels_approvable(snapshot) THEN
                    RAISE EXCEPTION 'catalogue approval requires readable non-fixture scope labels'
                        USING ERRCODE = '23514';
                END IF;
                PERFORM 1 FROM public.curriculum_versions cv
                    JOIN public.exam_configurations exam ON exam.id = cv.exam_configuration_id
                    JOIN public.media medium ON medium.id = cv.medium_id
                    JOIN public.subjects subject ON subject.id = cv.subject_id
                    WHERE cv.id = NEW.curriculum_version_id
                        AND cv.active AND exam.active AND medium.active AND subject.active;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'catalogue approval requires an active scope chain'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.admin_audit_events audit
                WHERE audit.id = NEW.audit_event_id AND audit.actor_id = NEW.actor_id
                    AND audit.action = 'catalogue_admission.' || NEW.state
                    AND audit.resource_type = 'curriculum_admission'
                    AND audit.resource_id = NEW.curriculum_version_id
                    AND audit.created_at = transaction_timestamp()
                    AND audit.payload = jsonb_build_object(
                        'decision_id', NEW.id,
                        'previous_version', COALESCE(current_version, 0),
                        'version', NEW.version,
                        'state', NEW.state,
                        'scope_fingerprint', NEW.scope_fingerprint,
                        'scope_snapshot', NEW.scope_snapshot,
                        'educational_approval', NEW.educational_approval,
                        'reason', NEW.reason,
                        'source_reference', NEW.source_reference,
                        'evidence', NEW.evidence
                    )
            ) THEN
                RAISE EXCEPTION 'catalogue admission requires matching explicit review audit'
                    USING ERRCODE = '23514';
            END IF;
            NEW.decided_at := transaction_timestamp();
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_catalogue_admission_current()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE previous_version integer;
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') OR pg_trigger_depth() <> 2 THEN
                RAISE EXCEPTION 'catalogue current state can only advance through audited decisions'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                previous_version := OLD.version;
                IF NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id THEN
                    RAISE EXCEPTION 'catalogue current scope cannot change' USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT version INTO previous_version FROM public.catalogue_admission_current
                    WHERE curriculum_version_id = NEW.curriculum_version_id;
            END IF;
            IF NEW.version IS DISTINCT FROM COALESCE(previous_version, 0) + 1 THEN
                RAISE EXCEPTION 'catalogue current state must advance exactly one version'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.advance_catalogue_admission_current()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO public.catalogue_admission_current
                (curriculum_version_id, decision_id, version)
                VALUES (NEW.curriculum_version_id, NEW.id, NEW.version)
                ON CONFLICT (curriculum_version_id) DO UPDATE
                    SET decision_id = EXCLUDED.decision_id, version = EXCLUDED.version;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.reject_catalogue_admission_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'catalogue admission decisions are append-only' USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER catalogue_admission_decision_insert
        BEFORE INSERT ON catalogue_admission_decisions
        FOR EACH ROW EXECUTE FUNCTION public.enforce_catalogue_admission_decision()
        """
    )
    op.execute(
        """
        CREATE TRIGGER catalogue_admission_decision_advance
        AFTER INSERT ON catalogue_admission_decisions
        FOR EACH ROW EXECUTE FUNCTION public.advance_catalogue_admission_current()
        """
    )
    op.execute(
        """
        CREATE TRIGGER catalogue_admission_decisions_immutable
        BEFORE UPDATE OR DELETE ON catalogue_admission_decisions
        FOR EACH ROW EXECUTE FUNCTION public.reject_catalogue_admission_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER catalogue_admission_decisions_no_truncate
        BEFORE TRUNCATE ON catalogue_admission_decisions
        FOR EACH STATEMENT EXECUTE FUNCTION public.reject_catalogue_admission_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER catalogue_admission_current_guard
        BEFORE INSERT OR UPDATE OR DELETE ON catalogue_admission_current
        FOR EACH ROW EXECUTE FUNCTION public.guard_catalogue_admission_current()
        """
    )
    op.execute(
        """
        CREATE TRIGGER catalogue_admission_current_no_truncate
        BEFORE TRUNCATE ON catalogue_admission_current
        FOR EACH STATEMENT EXECUTE FUNCTION public.reject_catalogue_admission_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.catalogue_admission_decisions) THEN
                RAISE EXCEPTION 'cannot discard catalogue admission history during downgrade';
            END IF;
        END $$
        """
    )
    op.execute("DROP FUNCTION public.catalogue_curriculum_is_admitted(uuid)")
    op.drop_table("catalogue_admission_current")
    op.drop_table("catalogue_admission_decisions")
    op.execute("DROP FUNCTION public.reject_catalogue_admission_mutation()")
    op.execute("DROP FUNCTION public.advance_catalogue_admission_current()")
    op.execute("DROP FUNCTION public.guard_catalogue_admission_current()")
    op.execute("DROP FUNCTION public.enforce_catalogue_admission_decision()")
    op.execute("DROP FUNCTION public.catalogue_scope_labels_approvable(jsonb)")
    op.execute("DROP FUNCTION public.catalogue_scope_fingerprint(jsonb)")
    op.execute("DROP FUNCTION public.catalogue_scope_snapshot(uuid)")
    op.execute("DROP FUNCTION public.catalogue_admission_evidence_valid(jsonb)")
    op.execute("DROP FUNCTION public.catalogue_admission_text_valid(text, integer)")
