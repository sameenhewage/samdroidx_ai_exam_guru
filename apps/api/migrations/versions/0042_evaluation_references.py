from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_evaluation_references"
down_revision: str | None = "0041_source_metadata_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION source_evaluation_text_is_valid(raw bytea, normalized text, blank boolean)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE decoded text;
        BEGIN
            IF raw IS NULL OR normalized IS NULL OR blank IS NULL
                OR octet_length(raw)>400000 THEN RETURN false; END IF;
            decoded := convert_from(raw,'UTF8');
            IF char_length(decoded)>100000 OR normalize(decoded,NFC)<>normalized
                OR translate(decoded,E'\n\r\t','') ~ '[[:cntrl:]]'
            THEN RETURN false; END IF;
            RETURN CASE WHEN blank THEN decoded='' ELSE decoded !~ '^[[:space:]]*$' END;
        EXCEPTION WHEN OTHERS THEN RETURN false;
        END; $$;
    """)
    op.create_table(
        "source_evaluation_previews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("benchmark_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("source_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("image_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "id",
            "benchmark_id",
            "document_id",
            "page_number",
            name="uq_evaluation_preview_identity",
        ),
        sa.ForeignKeyConstraint(
            ["benchmark_id", "document_id", "page_number"],
            [
                "source_fidelity_benchmark_pages.benchmark_id",
                "source_fidelity_benchmark_pages.document_id",
                "source_fidelity_benchmark_pages.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_preview_membership",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_preview_candidate",
        ),
        sa.CheckConstraint(
            "source_checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_preview_source_hash"
        ),
        sa.CheckConstraint(
            "image_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_preview_image_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(image_metadata)='object' AND octet_length(image_metadata::text)<=8192",
            name="ck_evaluation_preview_metadata",
        ),
    )
    op.create_table(
        "source_evaluation_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("benchmark_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("raw_text_utf8", sa.LargeBinary(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("normalized_sha256", sa.String(64), nullable=False),
        sa.Column("blank_reference", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["preview_id", "benchmark_id", "document_id", "page_number"],
            [
                "source_evaluation_previews.id",
                "source_evaluation_previews.benchmark_id",
                "source_evaluation_previews.document_id",
                "source_evaluation_previews.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_reference_preview",
        ),
        sa.UniqueConstraint(
            "benchmark_id",
            "document_id",
            "page_number",
            "version",
            name="uq_evaluation_reference_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_evaluation_reference_version"),
        sa.CheckConstraint(
            "source_evaluation_text_is_valid(raw_text_utf8, normalized_text, blank_reference)",
            name="ck_evaluation_reference_text",
        ),
        sa.CheckConstraint(
            "text_sha256=encode(sha256(raw_text_utf8),'hex')",
            name="ck_evaluation_reference_raw_hash",
        ),
        sa.CheckConstraint(
            "normalized_sha256=encode(sha256(convert_to(normalized_text,'UTF8')),'hex')",
            name="ck_evaluation_reference_normalized_hash",
        ),
        sa.CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 2000 AND reason=btrim(reason) "
            "AND reason !~ '[[:cntrl:]]'",
            name="ck_evaluation_reference_reason",
        ),
    )
    op.execute("""
        CREATE FUNCTION validate_evaluation_preview() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE; image jsonb; artifact jsonb;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            image := NEW.image_metadata; artifact := image->'artifact';
            IF NOT FOUND OR source.quarantined_for_teacher_use
                OR source.checksum_sha256<>NEW.source_checksum_sha256
                OR NEW.page_number<1 OR source.original_page_count IS NULL
                OR NEW.page_number>source.original_page_count
                OR image->>'document_id' IS DISTINCT FROM NEW.document_id::text
                OR image->>'source_checksum_sha256' IS DISTINCT FROM NEW.source_checksum_sha256
                OR image->>'source_object_key' IS DISTINCT FROM source.object_key
                OR image->'source_size_bytes' IS DISTINCT FROM to_jsonb(source.size_bytes)
                OR image->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR image->>'sha256' IS DISTINCT FROM NEW.image_sha256
                OR image->>'content_type' IS DISTINCT FROM 'image/png'
                OR image->>'rasterizer' IS DISTINCT FROM 'pymupdf'
                OR jsonb_typeof(artifact) IS DISTINCT FROM 'object'
                OR artifact->>'sha256' IS DISTINCT FROM NEW.image_sha256
                OR artifact->>'namespace' IS DISTINCT FROM 'fidelity-page-images'
                OR artifact->'schema_version' IS DISTINCT FROM '1'::jsonb
            THEN
                RAISE EXCEPTION 'evaluation preview must bind the original page and image'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_evaluation_preview_trigger
            BEFORE INSERT ON source_evaluation_previews
            FOR EACH ROW EXECUTE FUNCTION validate_evaluation_preview();
    """)
    op.execute("""
        CREATE FUNCTION validate_evaluation_reference() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE preview public.source_evaluation_previews%ROWTYPE; previous integer;
        BEGIN
            SELECT * INTO preview FROM public.source_evaluation_previews WHERE id=NEW.preview_id;
            IF NOT FOUND OR preview.created_by<>NEW.reviewer_id THEN
                RAISE EXCEPTION 'evaluation reference requires its reviewers original preview'
                    USING ERRCODE='23514';
            END IF;
            PERFORM 1 FROM public.source_documents d WHERE d.id=NEW.document_id
                AND NOT d.quarantined_for_teacher_use
                AND d.checksum_sha256=preview.source_checksum_sha256 FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'evaluation reference source identity changed'
                    USING ERRCODE='23514';
            END IF;
            PERFORM 1 FROM public.source_fidelity_benchmark_pages
                WHERE benchmark_id=NEW.benchmark_id AND document_id=NEW.document_id
                    AND page_number=NEW.page_number FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'evaluation reference requires fixed membership'
                    USING ERRCODE='23514';
            END IF;
            SELECT coalesce(max(version),0) INTO previous FROM public.source_evaluation_references
                WHERE benchmark_id=NEW.benchmark_id AND document_id=NEW.document_id
                    AND page_number=NEW.page_number;
            IF NEW.version<>previous+1 THEN
                RAISE EXCEPTION 'evaluation reference version must increment by one'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_evaluation_reference_trigger
            BEFORE INSERT ON source_evaluation_references
            FOR EACH ROW EXECUTE FUNCTION validate_evaluation_reference();
    """)
    op.execute("""
        CREATE FUNCTION validate_evaluation_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME='source_evaluation_previews' THEN
                IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events e
                    WHERE e.resource_type='source_evaluation_preview' AND e.resource_id=NEW.id
                        AND e.actor_id=NEW.created_by
                        AND e.action='source_evaluation.preview_prepared'
                        AND e.payload->>'benchmark_id'=NEW.benchmark_id::text
                        AND e.payload->>'document_id'=NEW.document_id::text
                        AND e.payload->'page_number'=to_jsonb(NEW.page_number)
                        AND e.payload->>'source_checksum_sha256'=NEW.source_checksum_sha256
                        AND e.payload->'image_metadata'=NEW.image_metadata
                ) THEN
                    RAISE EXCEPTION 'evaluation preview requires matching audit'
                        USING ERRCODE='23514';
                END IF;
            ELSE
                IF NOT EXISTS (SELECT 1 FROM public.admin_audit_events e
                    WHERE e.resource_type='source_evaluation_reference' AND e.resource_id=NEW.id
                        AND e.actor_id=NEW.reviewer_id
                        AND e.action='source_evaluation.reference_saved'
                        AND e.payload->>'preview_id'=NEW.preview_id::text
                        AND e.payload->'version'=to_jsonb(NEW.version)
                        AND e.payload->>'text_sha256'=NEW.text_sha256
                        AND e.payload->>'normalized_sha256'=NEW.normalized_sha256
                        AND e.payload->'blank_reference'=to_jsonb(NEW.blank_reference)
                        AND e.payload->>'reason'=NEW.reason
                        AND e.payload->'human_reviewed'='true'::jsonb
                        AND e.payload->'compared_with_original'='true'::jsonb
                        AND e.payload->'evaluation_only'='true'::jsonb
                ) THEN
                    RAISE EXCEPTION 'evaluation reference requires explicit comparison audit'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            RETURN NULL;
        END; $$;
    """)
    for table in ("source_evaluation_previews", "source_evaluation_references"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION validate_evaluation_audit()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_source_fidelity_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_{table}_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_source_fidelity_mutation()"
        )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE source_evaluation_previews, source_evaluation_references "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM source_evaluation_previews)
                OR EXISTS (SELECT 1 FROM source_evaluation_references) THEN
                RAISE EXCEPTION 'cannot discard evaluation reference evidence'
                    USING ERRCODE='23514';
            END IF;
        END; $$;
    """)
    op.drop_table("source_evaluation_references")
    op.drop_table("source_evaluation_previews")
    op.execute("DROP FUNCTION validate_evaluation_audit()")
    op.execute("DROP FUNCTION validate_evaluation_reference()")
    op.execute("DROP FUNCTION validate_evaluation_preview()")
    op.execute("DROP FUNCTION source_evaluation_text_is_valid(bytea,text,boolean)")
