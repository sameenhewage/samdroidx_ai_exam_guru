from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_source_metadata_candidates"
down_revision: str | None = "0040_source_fidelity_rules_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_metadata_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scope_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("material_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", "version", name="uq_source_metadata_candidate_version"),
        sa.CheckConstraint(
            "version > 0 AND scope_version >= 0", name="ck_metadata_candidate_version"
        ),
        sa.CheckConstraint(
            "source_checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_metadata_candidate_hash"
        ),
        sa.CheckConstraint(
            "source_intake_metadata_is_bounded(payload)", name="ck_metadata_candidate_payload"
        ),
        sa.CheckConstraint(
            "material_type IN ('syllabus','teacher_guide','past_paper','marking_scheme',"
            "'evaluation_report','other_approved')",
            name="ck_metadata_candidate_type",
        ),
        sa.CheckConstraint(
            "reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 512 "
            "AND reason !~ '[[:cntrl:]]'",
            name="ck_metadata_candidate_reason",
        ),
    )
    op.execute("""
        CREATE FUNCTION validate_source_metadata_candidate() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE; previous integer;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR UPDATE;
            IF NOT FOUND OR source.checksum_sha256 <> NEW.source_checksum_sha256
                OR source.metadata_scope_version <> NEW.scope_version
                OR source.curriculum_version_id IS NOT NULL
                OR NOT source.metadata_review_required OR source.quarantined_for_teacher_use
                OR source.extraction_status = 'trusted'
                OR EXISTS (SELECT 1 FROM public.knowledge_chunks WHERE source_document_id=source.id)
                OR EXISTS (SELECT 1 FROM public.historical_questions
                    WHERE source_document_id=source.id)
            THEN
                RAISE EXCEPTION 'metadata candidate requires an unassigned untrusted source'
                    USING ERRCODE='23514';
            END IF;
            SELECT coalesce(max(version),0) INTO previous
                FROM public.source_metadata_candidates WHERE document_id=NEW.document_id;
            IF NEW.version <> previous+1 THEN
                RAISE EXCEPTION 'metadata candidate version must increment by one'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_source_metadata_candidate_trigger
            BEFORE INSERT ON public.source_metadata_candidates
            FOR EACH ROW EXECUTE FUNCTION validate_source_metadata_candidate();
    """)
    op.execute("""
        CREATE TRIGGER immutable_source_metadata_candidate
            BEFORE UPDATE OR DELETE ON public.source_metadata_candidates
            FOR EACH ROW EXECUTE FUNCTION reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE TRIGGER immutable_source_metadata_candidate_truncate
            BEFORE TRUNCATE ON public.source_metadata_candidates
            FOR EACH STATEMENT EXECUTE FUNCTION reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE FUNCTION validate_source_metadata_candidate_audit()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM public.admin_audit_events e
                WHERE e.resource_type='source_document' AND e.resource_id=NEW.document_id
                    AND e.actor_id=NEW.created_by
                    AND e.action='source_document.metadata_candidate_corrected'
                    AND e.payload->>'candidate_id'=NEW.id::text
                    AND e.payload->>'source_checksum_sha256'=NEW.source_checksum_sha256
                    AND e.payload->'version'=to_jsonb(NEW.version)
                    AND e.payload->'scope_version'=to_jsonb(NEW.scope_version)
                    AND e.payload->'metadata'=NEW.payload
                    AND e.payload->>'material_type'=NEW.material_type
                    AND e.payload->>'reason'=NEW.reason
            ) THEN
                RAISE EXCEPTION 'metadata candidate requires matching audit' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER source_metadata_candidate_audit
            AFTER INSERT ON public.source_metadata_candidates
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION validate_source_metadata_candidate_audit();
    """)

    op.execute("""
        CREATE FUNCTION validate_metadata_candidate_confirmation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE candidate public.source_metadata_candidates%ROWTYPE;
        BEGIN
            IF OLD.metadata_review_required AND NOT NEW.metadata_review_required THEN
                SELECT * INTO candidate FROM public.source_metadata_candidates
                    WHERE document_id=OLD.id AND scope_version=OLD.metadata_scope_version
                        AND source_checksum_sha256=OLD.checksum_sha256
                    ORDER BY version DESC LIMIT 1;
                IF FOUND AND (
                    NEW.year IS DISTINCT FROM (candidate.payload->>'year')::integer
                    OR NEW.document_type IS DISTINCT FROM candidate.material_type
                    OR NOT EXISTS (
                        SELECT 1 FROM public.admin_audit_events e
                        WHERE e.resource_type='source_document' AND e.resource_id=NEW.id
                            AND e.actor_id=NEW.updated_by
                            AND e.action='source_document.intake_metadata_confirmed'
                            AND e.payload->>'confirmed_metadata_candidate_id'=candidate.id::text
                            AND e.payload->'confirmed_metadata'=candidate.payload
                            AND e.payload->'version'=to_jsonb(NEW.metadata_scope_version)
                    )
                ) THEN
                    RAISE EXCEPTION 'metadata confirmation requires the current candidate'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NULL;
        END; $$;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER source_metadata_candidate_confirmation
            AFTER UPDATE ON public.source_documents DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION validate_metadata_candidate_confirmation();
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE public.source_metadata_candidates IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.source_metadata_candidates) THEN
                RAISE EXCEPTION 'cannot discard source metadata candidate evidence'
                    USING ERRCODE='23514';
            END IF;
        END; $$;
    """)
    op.execute("DROP TRIGGER source_metadata_candidate_confirmation ON source_documents")
    op.execute("DROP FUNCTION validate_metadata_candidate_confirmation()")
    op.drop_table("source_metadata_candidates")
    op.execute("DROP FUNCTION validate_source_metadata_candidate_audit()")
    op.execute("DROP FUNCTION validate_source_metadata_candidate()")
