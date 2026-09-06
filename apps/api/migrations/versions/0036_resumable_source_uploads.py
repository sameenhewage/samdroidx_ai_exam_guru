from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_resumable_source_uploads"
down_revision: str | None = "0035_verified_knowledge_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "source_documents",
        "size_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.create_table(
        "source_upload_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("intake_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("expected_checksum_sha256", sa.String(64), nullable=True),
        sa.Column(
            "curriculum_version_id",
            sa.Uuid(),
            sa.ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("lesson_id", sa.Uuid(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("paper_code", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploading"),
        sa.Column("next_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("verified_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("deduplicated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "likely_metadata_duplicate_of_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("size_bytes >= 5", name="ck_source_upload_size"),
        sa.CheckConstraint(
            "next_offset BETWEEN 0 AND size_bytes AND "
            "(next_offset = size_bytes OR next_offset % 4194304 = 0)",
            name="ck_source_upload_offset",
        ),
        sa.CheckConstraint(
            "verified_bytes BETWEEN 0 AND next_offset AND version >= 0",
            name="ck_source_upload_progress",
        ),
        sa.CheckConstraint(
            "status IN ('uploading', 'pending', 'finalizing', 'completed', 'failed')",
            name="ck_source_upload_status",
        ),
        sa.CheckConstraint(
            "document_type IN ('syllabus', 'teacher_guide', 'past_paper', "
            "'marking_scheme', 'evaluation_report', 'other_approved')",
            name="ck_source_upload_type",
        ),
        sa.CheckConstraint(
            "char_length(filename) BETWEEN 5 AND 255 AND right(lower(filename), 4) = '.pdf' "
            "AND filename !~ '[[:cntrl:]/]' AND position(chr(92) in filename) = 0",
            name="ck_source_upload_filename",
        ),
        sa.CheckConstraint(
            "expected_checksum_sha256 IS NULL OR expected_checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_source_upload_expected_checksum",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_source_upload_checksum",
        ),
        sa.CheckConstraint(
            "public.source_intake_metadata_is_bounded(intake_metadata)",
            name="ck_source_upload_intake",
        ),
        sa.CheckConstraint(
            "(unit_id IS NULL OR curriculum_version_id IS NOT NULL) AND "
            "(lesson_id IS NULL OR unit_id IS NOT NULL)",
            name="ck_source_upload_scope_shape",
        ),
        sa.CheckConstraint(
            "year IS NULL OR year BETWEEN 1900 AND 2100", name="ck_source_upload_year"
        ),
        sa.CheckConstraint(
            "paper_code IS NULL OR (char_length(paper_code) BETWEEN 1 AND 64 AND "
            "paper_code = btrim(paper_code) AND paper_code !~ '[[:cntrl:]]')",
            name="ck_source_upload_paper_code",
        ),
        sa.CheckConstraint(
            "(status = 'finalizing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'finalizing' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_source_upload_lease",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND document_id IS NOT NULL AND checksum_sha256 IS NOT NULL "
            "AND verified_bytes = size_bytes AND failure_code IS NULL) OR "
            "(status <> 'completed' AND document_id IS NULL AND NOT deduplicated "
            "AND likely_metadata_duplicate_of_id IS NULL)",
            name="ck_source_upload_result",
        ),
        sa.CheckConstraint(
            "(status = 'uploading' AND verified_bytes = 0 AND checksum_sha256 IS NULL "
            "AND failure_code IS NULL) OR (status <> 'uploading' AND next_offset = size_bytes)",
            name="ck_source_upload_ready",
        ),
        sa.CheckConstraint(
            "(status <> 'failed' OR failure_code IS NOT NULL) AND "
            "(failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$')",
            name="ck_source_upload_failure",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_source_upload_unit_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_source_upload_lesson_scope",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_source_upload_owner", "source_upload_sessions", ["owner_id", "status"])
    op.create_index(
        "ix_source_upload_pending",
        "source_upload_sessions",
        ["status", "lease_expires_at", "created_at", "id"],
        postgresql_where=sa.text("status IN ('pending', 'finalizing')"),
    )
    op.create_table(
        "source_upload_chunks",
        sa.Column(
            "upload_id",
            sa.Uuid(),
            sa.ForeignKey("source_upload_sessions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("offset", sa.BigInteger(), primary_key=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            '"offset" >= 0 AND "offset" % 4194304 = 0', name="ck_source_upload_chunk_offset"
        ),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 4194304", name="ck_source_upload_chunk_size"),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_source_upload_chunk_checksum"
        ),
    )
    op.execute(
        """
        CREATE FUNCTION guard_source_upload_session() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'source upload evidence is retained' USING ERRCODE = '23514';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'uploading' OR NEW.next_offset <> 0 OR NEW.version <> 0
                    OR NEW.verified_bytes <> 0 THEN
                    RAISE EXCEPTION 'source upload must start empty' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.id, NEW.owner_id, NEW.filename, NEW.size_bytes, NEW.document_type,
                NEW.intake_metadata, NEW.expected_checksum_sha256, NEW.curriculum_version_id,
                NEW.unit_id, NEW.lesson_id, NEW.year, NEW.paper_code, NEW.created_at)
                IS DISTINCT FROM ROW(OLD.id, OLD.owner_id, OLD.filename, OLD.size_bytes,
                OLD.document_type, OLD.intake_metadata, OLD.expected_checksum_sha256,
                OLD.curriculum_version_id, OLD.unit_id, OLD.lesson_id, OLD.year,
                OLD.paper_code, OLD.created_at) THEN
                RAISE EXCEPTION 'source upload identity is immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.status IN ('completed', 'failed') OR NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'source upload requires a live version transition'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT ((OLD.status = 'uploading' AND NEW.status IN ('uploading', 'pending'))
                OR (OLD.status = 'pending' AND NEW.status = 'finalizing')
                OR (OLD.status = 'finalizing' AND NEW.status IN
                    ('finalizing', 'pending', 'completed', 'failed'))) THEN
                RAISE EXCEPTION 'invalid source upload transition' USING ERRCODE = '23514';
            END IF;
            IF NEW.next_offset IS DISTINCT FROM OLD.next_offset THEN
                IF OLD.status <> 'uploading' OR NEW.status <> 'uploading' OR NOT EXISTS (
                    SELECT 1 FROM public.source_upload_chunks c WHERE c.upload_id = OLD.id
                        AND c.offset = OLD.next_offset
                        AND NEW.next_offset = OLD.next_offset + c.size_bytes
                ) THEN
                    RAISE EXCEPTION 'source upload progress requires a matching chunk receipt'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            IF OLD.status = 'finalizing' AND NEW.status = 'finalizing'
                AND NEW.lease_token IS DISTINCT FROM OLD.lease_token
                AND OLD.lease_expires_at > clock_timestamp() THEN
                RAISE EXCEPTION 'source upload finalization lease is still active'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.verified_bytes < OLD.verified_bytes AND NEW.status <> 'pending'
                AND NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token THEN
                RAISE EXCEPTION 'source upload verification progress cannot regress'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'completed' AND (
                (NEW.expected_checksum_sha256 IS NOT NULL
                    AND NEW.expected_checksum_sha256 <> NEW.checksum_sha256)
                OR NOT EXISTS (
                    SELECT 1 FROM public.source_documents d WHERE d.id = NEW.document_id
                        AND d.size_bytes = NEW.size_bytes
                        AND d.checksum_sha256 = NEW.checksum_sha256
                )) THEN
                RAISE EXCEPTION 'completed upload must reference its immutable source'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_source_upload_chunk() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE upload public.source_upload_sessions%ROWTYPE;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'source upload chunk receipts are immutable'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO upload FROM public.source_upload_sessions WHERE id = NEW.upload_id
                FOR UPDATE;
            IF NOT FOUND OR upload.status <> 'uploading' OR NEW.offset <> upload.next_offset
                OR NEW.size_bytes <> LEAST(4194304::bigint, upload.size_bytes - upload.next_offset)
            THEN
                RAISE EXCEPTION 'chunk must match the current upload offset and remaining size'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION check_source_upload_chunk_progress() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.source_upload_sessions
                WHERE id = NEW.upload_id AND next_offset >= NEW.offset + NEW.size_bytes) THEN
                RAISE EXCEPTION 'chunk receipt requires committed upload progress'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION check_source_upload_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events
                    WHERE resource_type = 'source_upload' AND resource_id = NEW.id
                        AND actor_id = NEW.owner_id AND action = 'source_upload.created'
                        AND payload->'size_bytes' = to_jsonb(NEW.size_bytes)
                        AND payload->'intake_metadata' = NEW.intake_metadata) THEN
                    RAISE EXCEPTION 'source upload requires creation audit' USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.status = 'completed' AND OLD.status <> 'completed' THEN
                IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events
                    WHERE resource_type = 'source_upload' AND resource_id = NEW.id
                        AND actor_id = NEW.owner_id AND action = 'source_upload.completed'
                        AND payload->>'document_id' = NEW.document_id::text
                        AND payload->>'checksum_sha256' = NEW.checksum_sha256
                        AND payload->'deduplicated' = to_jsonb(NEW.deduplicated)) THEN
                    RAISE EXCEPTION 'source upload completion requires audit'
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
        CREATE FUNCTION deny_source_upload_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'source upload history cannot be truncated' USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_source_upload_session_trigger BEFORE INSERT OR UPDATE OR DELETE "
        "ON source_upload_sessions FOR EACH ROW EXECUTE FUNCTION guard_source_upload_session()"
    )
    op.execute(
        "CREATE TRIGGER guard_source_upload_chunk_trigger BEFORE INSERT OR UPDATE OR DELETE "
        "ON source_upload_chunks FOR EACH ROW EXECUTE FUNCTION guard_source_upload_chunk()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_source_upload_chunk_progress_trigger AFTER INSERT "
        "ON source_upload_chunks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_source_upload_chunk_progress()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_source_upload_audit_trigger AFTER INSERT OR UPDATE "
        "ON source_upload_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_source_upload_audit()"
    )
    op.execute(
        "CREATE TRIGGER deny_source_upload_session_truncate BEFORE TRUNCATE "
        "ON source_upload_sessions FOR EACH STATEMENT "
        "EXECUTE FUNCTION deny_source_upload_truncate()"
    )
    op.execute(
        "CREATE TRIGGER deny_source_upload_chunk_truncate BEFORE TRUNCATE "
        "ON source_upload_chunks FOR EACH STATEMENT EXECUTE FUNCTION deny_source_upload_truncate()"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.source_upload_sessions)
                OR EXISTS (SELECT 1 FROM public.source_upload_chunks) THEN
                RAISE EXCEPTION 'cannot discard resumable source upload evidence';
            END IF;
            IF EXISTS (SELECT 1 FROM public.source_documents WHERE size_bytes > 2147483647) THEN
                RAISE EXCEPTION 'cannot narrow large source document sizes';
            END IF;
        END $$
        """
    )
    op.drop_table("source_upload_chunks")
    op.drop_table("source_upload_sessions")
    op.execute("DROP FUNCTION guard_source_upload_session()")
    op.execute("DROP FUNCTION guard_source_upload_chunk()")
    op.execute("DROP FUNCTION check_source_upload_chunk_progress()")
    op.execute("DROP FUNCTION check_source_upload_audit()")
    op.execute("DROP FUNCTION deny_source_upload_truncate()")
    op.alter_column(
        "source_documents",
        "size_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
