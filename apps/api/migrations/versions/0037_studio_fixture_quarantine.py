from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_studio_fixture_quarantine"
down_revision: str | None = "0036_resumable_source_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_guard(*, include_quarantine: bool) -> None:
    quarantine_change = (
        "OR NEW.quarantined_for_teacher_use IS DISTINCT FROM OLD.quarantined_for_teacher_use"
        if include_quarantine
        else ""
    )
    initial_quarantine = "OR NEW.quarantined_for_teacher_use" if include_quarantine else ""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_source_document_use_and_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE protected_change boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.metadata_scope_version <> 0 {initial_quarantine} THEN
                    RAISE EXCEPTION 'source document metadata must start at zero without quarantine'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            protected_change :=
                NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.unit_id IS DISTINCT FROM OLD.unit_id
                OR NEW.lesson_id IS DISTINCT FROM OLD.lesson_id
                OR NEW.active_for_ai IS DISTINCT FROM OLD.active_for_ai
                OR NEW.removal_reason IS DISTINCT FROM OLD.removal_reason
                OR NEW.removed_by IS DISTINCT FROM OLD.removed_by
                OR NEW.removed_at IS DISTINCT FROM OLD.removed_at
                OR NEW.metadata_review_required IS DISTINCT FROM OLD.metadata_review_required
                {quarantine_change};
            IF protected_change
                AND NEW.metadata_scope_version <> OLD.metadata_scope_version + 1
            THEN
                RAISE EXCEPTION 'source document metadata scope version must increment by one'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT protected_change
                AND NEW.metadata_scope_version IS DISTINCT FROM OLD.metadata_scope_version
            THEN
                RAISE EXCEPTION 'source document metadata scope version cannot change alone'
                    USING ERRCODE = '23514';
            END IF;
            IF (
                NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.unit_id IS DISTINCT FROM OLD.unit_id
                OR NEW.lesson_id IS DISTINCT FROM OLD.lesson_id
            ) AND (
                OLD.extraction_status = 'trusted'
                OR EXISTS (SELECT 1 FROM historical_questions WHERE source_document_id = OLD.id)
                OR EXISTS (SELECT 1 FROM knowledge_chunks WHERE source_document_id = OLD.id)
            ) THEN
                RAISE EXCEPTION 'trusted or imported source scope is immutable; remove it from use'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """.replace("{initial_quarantine}", initial_quarantine).replace(
            "{quarantine_change}", quarantine_change
        )
    )


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column(
            "quarantined_for_teacher_use", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_check_constraint(
        "ck_source_documents_quarantine_inactive",
        "source_documents",
        "NOT quarantined_for_teacher_use OR NOT active_for_ai",
    )
    _scope_guard(include_quarantine=True)
    op.execute(
        """
        CREATE FUNCTION public.source_fixture_review_text_valid(value text, maximum integer)
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT value IS NOT NULL AND char_length(value) BETWEEN 1 AND maximum
                AND value = btrim(value)
                AND value !~ '(^[[:space:]])|([[:space:]]$)|[[:cntrl:]]'
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.source_fixture_quarantine_review_valid(
            review jsonb, source_id uuid, checksum text,
            from_quarantine boolean, from_active boolean, to_quarantine boolean,
            previous_version integer, next_version integer
        ) RETURNS boolean LANGUAGE plpgsql STABLE AS $$
        DECLARE evidence jsonb; observation jsonb;
        BEGIN
            IF jsonb_typeof(review) IS DISTINCT FROM 'object'
                OR review - ARRAY['confirmation', 'reason', 'provenance_evidence',
                    'previous_version', 'version', 'from', 'to'] IS DISTINCT FROM '{}'::jsonb
                OR review->'confirmation' IS DISTINCT FROM to_jsonb(CASE WHEN to_quarantine
                    THEN 'quarantine_exact_source_fixture' ELSE 'restore_exact_source_fixture' END)
                OR jsonb_typeof(review->'reason') IS DISTINCT FROM 'string'
                OR NOT public.source_fixture_review_text_valid(review->>'reason', 512)
                OR review->'previous_version' IS DISTINCT FROM to_jsonb(previous_version)
                OR review->'version' IS DISTINCT FROM to_jsonb(next_version)
                OR review->'from' IS DISTINCT FROM jsonb_build_object(
                    'quarantined_for_teacher_use', from_quarantine, 'active_for_ai', from_active)
                OR review->'to' IS DISTINCT FROM jsonb_build_object(
                    'quarantined_for_teacher_use', to_quarantine, 'active_for_ai', false)
            THEN RETURN false; END IF;
            evidence := review->'provenance_evidence';
            IF jsonb_typeof(evidence) IS DISTINCT FROM 'object'
                OR evidence - ARRAY['source_document_id', 'checksum_sha256',
                    'upload_audit_event_id', 'fixture_reference', 'observed_evidence']
                    IS DISTINCT FROM '{}'::jsonb
                OR evidence->'source_document_id' IS DISTINCT FROM to_jsonb(source_id::text)
                OR evidence->'checksum_sha256' IS DISTINCT FROM to_jsonb(checksum)
                OR checksum !~ '^[0-9a-f]{64}$' OR octet_length(checksum) <> 64
                OR jsonb_typeof(evidence->'fixture_reference') IS DISTINCT FROM 'string'
                OR NOT public.source_fixture_review_text_valid(evidence->>'fixture_reference', 1024)
                OR jsonb_typeof(evidence->'observed_evidence') IS DISTINCT FROM 'array'
            THEN RETURN false; END IF;
            IF jsonb_array_length(evidence->'observed_evidence') NOT BETWEEN 1 AND 16
            THEN RETURN false; END IF;
            FOR observation IN
                SELECT * FROM jsonb_array_elements(evidence->'observed_evidence')
            LOOP
                IF jsonb_typeof(observation) IS DISTINCT FROM 'string'
                    OR NOT public.source_fixture_review_text_valid(observation #>> '{}', 1024)
                THEN RETURN false; END IF;
            END LOOP;
            RETURN EXISTS (
                SELECT 1 FROM public.admin_audit_events upload
                WHERE evidence->'upload_audit_event_id' = to_jsonb(upload.id::text)
                    AND upload.resource_type = 'source_document' AND upload.resource_id = source_id
                    AND upload.action = 'source_document.uploaded'
                    AND upload.payload->'checksum_sha256' = to_jsonb(checksum)
            );
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_source_fixture_quarantine()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mutable_columns text[] := ARRAY[
            'quarantined_for_teacher_use', 'active_for_ai', 'removal_reason', 'removed_by',
            'removed_at', 'metadata_scope_version', 'updated_by', 'updated_at'
        ];
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.quarantined_for_teacher_use OR EXISTS (
                    SELECT 1 FROM public.admin_audit_events
                    WHERE resource_type = 'source_document' AND resource_id = OLD.id
                        AND action IN ('source_document.fixture_quarantined',
                            'source_document.fixture_restored')
                ) THEN
                    RAISE EXCEPTION 'source fixture quarantine history is immutable'
                        USING ERRCODE = '23514';
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.quarantined_for_teacher_use IS NOT DISTINCT FROM OLD.quarantined_for_teacher_use
            THEN RETURN NEW; END IF;
            IF NEW.active_for_ai
                OR to_jsonb(NEW) - mutable_columns IS DISTINCT FROM to_jsonb(OLD) - mutable_columns
            THEN
                RAISE EXCEPTION 'fixture quarantine cannot reactivate, trust or rewrite a source'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT OLD.active_for_ai AND (
                NEW.removal_reason IS DISTINCT FROM OLD.removal_reason
                OR NEW.removed_by IS DISTINCT FROM OLD.removed_by
                OR NEW.removed_at IS DISTINCT FROM OLD.removed_at
            ) THEN
                RAISE EXCEPTION 'fixture quarantine must preserve prior removal history'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.active_for_ai AND (
                NEW.removed_by IS DISTINCT FROM NEW.updated_by
                OR NOT public.source_fixture_review_text_valid(NEW.removal_reason, 512)
            ) THEN
                RAISE EXCEPTION 'fixture quarantine requires an explicit removal review'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_source_fixture_quarantine_trigger
        BEFORE UPDATE OR DELETE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION public.enforce_source_fixture_quarantine()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.enforce_source_fixture_quarantine_audit()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE review_count integer; matching_count integer;
        BEGIN
            IF NEW.quarantined_for_teacher_use IS NOT DISTINCT FROM OLD.quarantined_for_teacher_use
            THEN RETURN NULL; END IF;
            SELECT count(*), count(*) FILTER (WHERE
                review.actor_id = NEW.updated_by
                AND review.action = CASE WHEN NEW.quarantined_for_teacher_use
                    THEN 'source_document.fixture_quarantined'
                    ELSE 'source_document.fixture_restored' END
                AND public.source_fixture_quarantine_review_valid(
                    review.payload, NEW.id, NEW.checksum_sha256,
                    OLD.quarantined_for_teacher_use, OLD.active_for_ai,
                    NEW.quarantined_for_teacher_use, OLD.metadata_scope_version,
                    NEW.metadata_scope_version)
                AND (NOT OLD.active_for_ai OR review.payload->>'reason' = NEW.removal_reason)
            ) INTO review_count, matching_count
            FROM public.admin_audit_events review
            WHERE review.resource_type = 'source_document' AND review.resource_id = NEW.id
                AND review.action IN (
                    'source_document.fixture_quarantined', 'source_document.fixture_restored')
                AND review.payload->'version' = to_jsonb(NEW.metadata_scope_version);
            IF review_count <> 1 OR matching_count <> 1 THEN
                RAISE EXCEPTION 'fixture quarantine requires exactly one matching evidence audit'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER enforce_source_fixture_quarantine_audit_trigger
        AFTER UPDATE ON source_documents DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.enforce_source_fixture_quarantine_audit()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM source_documents WHERE quarantined_for_teacher_use)
                OR EXISTS (SELECT 1 FROM admin_audit_events
                    WHERE resource_type = 'source_document'
                        AND action IN ('source_document.fixture_quarantined',
                            'source_document.fixture_restored'))
            THEN
                RAISE EXCEPTION 'cannot discard source fixture quarantine history'
                    USING ERRCODE = '23514';
            END IF;
        END $$
        """
    )
    op.execute("DROP TRIGGER enforce_source_fixture_quarantine_audit_trigger ON source_documents")
    op.execute("DROP FUNCTION public.enforce_source_fixture_quarantine_audit()")
    op.execute("DROP TRIGGER enforce_source_fixture_quarantine_trigger ON source_documents")
    op.execute("DROP FUNCTION public.enforce_source_fixture_quarantine()")
    op.execute(
        "DROP FUNCTION public.source_fixture_quarantine_review_valid("
        "jsonb, uuid, text, boolean, boolean, boolean, integer, integer)"
    )
    op.execute("DROP FUNCTION public.source_fixture_review_text_valid(text, integer)")
    _scope_guard(include_quarantine=False)
    op.drop_constraint("ck_source_documents_quarantine_inactive", "source_documents")
    op.drop_column("source_documents", "quarantined_for_teacher_use")
