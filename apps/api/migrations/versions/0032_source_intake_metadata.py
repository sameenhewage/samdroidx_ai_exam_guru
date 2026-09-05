from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_source_intake_metadata"
down_revision: str | None = "0031_teacher_draft_race_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_guard(*, include_review: bool) -> None:
    review_change = (
        "OR NEW.metadata_review_required IS DISTINCT FROM OLD.metadata_review_required"
        if include_review
        else ""
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_source_document_use_and_scope()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE protected_change boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.metadata_scope_version <> 0 THEN
                    RAISE EXCEPTION 'source document metadata scope version must start at zero'
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
                {review_change};
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
        """.replace("{review_change}", review_change)
    )


def upgrade() -> None:
    op.add_column(
        "source_documents", sa.Column("intake_metadata", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "source_documents",
        sa.Column(
            "metadata_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION source_intake_metadata_is_bounded(value jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE
            item record;
            element jsonb;
            label text;
            max_length integer;
        BEGIN
            IF jsonb_typeof(value) IS DISTINCT FROM 'object'
                OR octet_length(value::text) > 16384 THEN RETURN false;
            END IF;
            FOR item IN SELECT * FROM jsonb_each(value) LOOP
                IF item.key NOT IN (
                    'candidate_grade', 'subject_label', 'medium_label', 'curriculum_label',
                    'document_type_label', 'year', 'term', 'publisher', 'source_reference',
                    'evidence', 'warnings'
                ) THEN RETURN false;
                END IF;
                IF item.key IN ('evidence', 'warnings') THEN
                    IF jsonb_typeof(item.value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
                    IF jsonb_array_length(item.value) > 32 THEN RETURN false; END IF;
                    FOR element IN SELECT * FROM jsonb_array_elements(item.value) LOOP
                        label := element #>> '{}';
                        IF jsonb_typeof(element) IS DISTINCT FROM 'string'
                            OR char_length(label) NOT BETWEEN 1 AND 1024
                            OR label <> btrim(label) OR label ~ '[[:cntrl:]]'
                        THEN RETURN false;
                        END IF;
                    END LOOP;
                ELSIF item.value = 'null'::jsonb THEN
                    CONTINUE;
                ELSIF item.key IN ('candidate_grade', 'year') THEN
                    IF jsonb_typeof(item.value) IS DISTINCT FROM 'number'
                        OR item.value::text !~ '^[0-9]+$' THEN RETURN false;
                    END IF;
                    IF item.key = 'candidate_grade' AND
                        (item.value::numeric < 1 OR item.value::numeric > 13) THEN RETURN false;
                    END IF;
                    IF item.key = 'year' AND
                        (item.value::numeric < 1900 OR item.value::numeric > 2100)
                    THEN RETURN false;
                    END IF;
                ELSE
                    max_length := CASE item.key
                        WHEN 'term' THEN 64 WHEN 'source_reference' THEN 1024 ELSE 200 END;
                    label := item.value #>> '{}';
                    IF jsonb_typeof(item.value) IS DISTINCT FROM 'string'
                        OR char_length(label) NOT BETWEEN 1 AND max_length
                        OR label <> btrim(label) OR label ~ '[[:cntrl:]]'
                    THEN RETURN false;
                    END IF;
                END IF;
            END LOOP;
            RETURN true;
        END;
        $$
        """
    )
    op.create_check_constraint(
        "ck_source_documents_intake_metadata",
        "source_documents",
        "intake_metadata IS NULL OR source_intake_metadata_is_bounded(intake_metadata)",
    )
    op.create_check_constraint(
        "ck_source_documents_metadata_review_trust",
        "source_documents",
        "NOT metadata_review_required OR extraction_status <> 'trusted'",
    )
    _scope_guard(include_review=True)
    op.execute(
        """
        CREATE FUNCTION enforce_source_intake_metadata()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.intake_metadata IS NOT NULL THEN
                    RAISE EXCEPTION 'source intake evidence is immutable' USING ERRCODE = '23514';
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.metadata_review_required AND NEW.extraction_status = 'trusted' THEN
                RAISE EXCEPTION 'source intake metadata requires confirmation before trust'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.intake_metadata IS NOT NULL AND NOT NEW.metadata_review_required THEN
                    RAISE EXCEPTION 'source intake metadata must start unverified'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.intake_metadata IS DISTINCT FROM OLD.intake_metadata THEN
                RAISE EXCEPTION 'source intake evidence is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_source_intake_metadata_trigger
        BEFORE INSERT OR UPDATE OR DELETE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_source_intake_metadata()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_source_intake_audit()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.intake_metadata IS NULL THEN RETURN NULL; END IF;
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM admin_audit_events
                    WHERE resource_type = 'source_document' AND resource_id = NEW.id
                        AND action = 'source_document.uploaded' AND actor_id = NEW.created_by
                        AND payload->'intake_metadata' = NEW.intake_metadata
                        AND payload->'metadata_review_required' = 'true'::jsonb
                ) THEN
                    RAISE EXCEPTION 'source intake metadata requires upload audit'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END IF;
            IF NOT NEW.metadata_review_required AND (
                OLD.metadata_review_required
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.unit_id IS DISTINCT FROM OLD.unit_id
                OR NEW.lesson_id IS DISTINCT FROM OLD.lesson_id
            ) THEN
                IF NEW.extraction_status IS DISTINCT FROM OLD.extraction_status THEN
                    RAISE EXCEPTION 'metadata confirmation cannot also change extraction trust'
                        USING ERRCODE = '23514';
                END IF;
                PERFORM 1 FROM curriculum_versions cv
                    JOIN exam_configurations exam ON exam.id = cv.exam_configuration_id
                    JOIN media medium ON medium.id = cv.medium_id
                    JOIN subjects subject ON subject.id = cv.subject_id
                    WHERE cv.id = NEW.curriculum_version_id
                        AND cv.active AND exam.active AND medium.active AND subject.active
                    FOR SHARE OF cv, exam, medium, subject;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'metadata confirmation requires an active curriculum scope'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.unit_id IS NOT NULL THEN
                    PERFORM 1 FROM curriculum_units WHERE id = NEW.unit_id AND active FOR SHARE;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'metadata confirmation requires an active unit'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                IF NEW.lesson_id IS NOT NULL THEN
                    PERFORM 1 FROM curriculum_lessons WHERE id = NEW.lesson_id AND active FOR SHARE;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'metadata confirmation requires an active lesson'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM admin_audit_events
                    WHERE resource_type = 'source_document' AND resource_id = NEW.id
                        AND action = 'source_document.intake_metadata_confirmed'
                        AND actor_id = NEW.updated_by
                        AND payload->'intake_metadata' = OLD.intake_metadata
                        AND payload->'metadata_review_required' = 'false'::jsonb
                        AND payload->'previous_version' = to_jsonb(OLD.metadata_scope_version)
                        AND payload->'version' = to_jsonb(NEW.metadata_scope_version)
                        AND payload->'from' = jsonb_build_object(
                            'curriculum_version_id', OLD.curriculum_version_id,
                            'unit_id', OLD.unit_id, 'lesson_id', OLD.lesson_id)
                        AND payload->'to' = jsonb_build_object(
                            'curriculum_version_id', NEW.curriculum_version_id,
                            'unit_id', NEW.unit_id, 'lesson_id', NEW.lesson_id)
                ) THEN
                    RAISE EXCEPTION 'source intake metadata confirmation requires matching audit'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER enforce_source_intake_audit_trigger
        AFTER INSERT OR UPDATE ON source_documents DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_source_intake_audit()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM source_documents
                WHERE intake_metadata IS NOT NULL OR metadata_review_required) THEN
                RAISE EXCEPTION 'cannot discard source intake evidence during downgrade';
            END IF;
        END $$
        """
    )
    op.execute("DROP TRIGGER enforce_source_intake_audit_trigger ON source_documents")
    op.execute("DROP FUNCTION enforce_source_intake_audit()")
    op.execute("DROP TRIGGER enforce_source_intake_metadata_trigger ON source_documents")
    op.execute("DROP FUNCTION enforce_source_intake_metadata()")
    _scope_guard(include_review=False)
    op.drop_constraint("ck_source_documents_metadata_review_trust", "source_documents")
    op.drop_constraint("ck_source_documents_intake_metadata", "source_documents")
    op.execute("DROP FUNCTION source_intake_metadata_is_bounded(jsonb)")
    op.drop_column("source_documents", "metadata_review_required")
    op.drop_column("source_documents", "intake_metadata")
