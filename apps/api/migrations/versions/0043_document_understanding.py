from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_document_understanding"
down_revision: str | None = "0042_evaluation_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "source_understanding_runs",
    "source_understanding_candidates",
    "source_understanding_regions",
    "source_understanding_reports",
    "source_understanding_decisions",
    "trusted_page_knowledge",
    "source_understanding_pages",
)
_CANDIDATE_SNAPSHOT = """jsonb_build_object(
    'id',id::text,'run_id',run_id::text,'revision',revision,'method',method,
    'source',jsonb_build_object('document_id',document_id::text,'source_sha256',source_sha256,
        'page_number',page_number,'image_sha256',image_sha256),
    'content',jsonb_build_object('schema_version','page-understanding.v1',
        'observation',observation,'education',educational_understanding,'uncertainties',uncertainties))"""


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column(
            "audit_event_id",
            sa.Uuid(),
            sa.ForeignKey(
                "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
            ),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.source_understanding_fingerprint(value jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT encode(sha256(convert_to(public.paper_canonical_jsonb(value),'UTF8')),'hex');
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.source_understanding_keys_match(
            selected jsonb, known jsonb, complete boolean
        )
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT CASE WHEN jsonb_typeof(selected)='array' AND jsonb_typeof(known)='array' THEN
                NOT EXISTS (SELECT 1 FROM jsonb_array_elements(selected) item
                    WHERE jsonb_typeof(item)<>'string' OR NOT known ? (item#>>'{}'))
                AND jsonb_array_length(selected)=(SELECT count(DISTINCT item)
                    FROM jsonb_array_elements(selected) item)
                AND (NOT complete OR jsonb_array_length(selected)=jsonb_array_length(known))
            ELSE false END;
        $$;
    """)
    op.create_table(
        "source_understanding_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("image_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("provider_profile", postgresql.JSONB(), nullable=True),
        sa.Column("budget", postgresql.JSONB(), nullable=True),
        sa.Column("accounting", postgresql.JSONB(), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint("created_by", "request_id", name="uq_understanding_run_request"),
        sa.UniqueConstraint(
            "id",
            "document_id",
            "page_number",
            "source_sha256",
            "image_sha256",
            name="uq_understanding_run_source",
        ),
        sa.CheckConstraint("page_number > 0", name="ck_understanding_run_page"),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND image_sha256 ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_understanding_run_hashes",
        ),
        sa.CheckConstraint(
            "method IN ('native','ocr','visual_ai','human')", name="ck_understanding_run_method"
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded','failed','unknown') "
            "AND ((outcome='succeeded' AND failure_code IS NULL) "
            "OR (outcome<>'succeeded' AND failure_code IS NOT NULL))",
            name="ck_understanding_run_outcome",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(image_metadata)='object' AND octet_length(image_metadata::text)<=65536",
            name="ck_understanding_run_image",
        ),
        sa.CheckConstraint(
            "provider_profile IS NULL OR (jsonb_typeof(provider_profile)='object' "
            "AND octet_length(provider_profile::text)<=16384)",
            name="ck_understanding_run_profile",
        ),
        sa.CheckConstraint(
            "budget IS NULL OR (jsonb_typeof(budget)='object' "
            "AND octet_length(budget::text)<=4096)",
            name="ck_understanding_run_budget",
        ),
        sa.CheckConstraint(
            "accounting IS NULL OR (jsonb_typeof(accounting)='object' "
            "AND octet_length(accounting::text)<=4096)",
            name="ck_understanding_run_accounting",
        ),
        sa.CheckConstraint(
            "method<>'visual_ai' OR (provider_profile IS NOT NULL AND budget IS NOT NULL "
            "AND (outcome<>'succeeded' OR accounting IS NOT NULL))",
            name="ck_understanding_run_visual_lineage",
        ),
    )
    op.create_table(
        "source_understanding_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("observation", postgresql.JSONB(), nullable=False),
        sa.Column("educational_understanding", postgresql.JSONB(), nullable=False),
        sa.Column("uncertainties", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("run_id", name="uq_understanding_candidate_run"),
        sa.UniqueConstraint(
            "document_id", "page_number", "revision", name="uq_understanding_candidate_revision"
        ),
        sa.UniqueConstraint(
            "id", "document_id", "page_number", name="uq_understanding_candidate_source"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "document_id", "page_number", "source_sha256", "image_sha256"],
            [
                "source_understanding_runs.id",
                "source_understanding_runs.document_id",
                "source_understanding_runs.page_number",
                "source_understanding_runs.source_sha256",
                "source_understanding_runs.image_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_candidate_run",
        ),
        sa.CheckConstraint(
            "page_number > 0 AND revision > 0", name="ck_understanding_candidate_revision"
        ),
        sa.CheckConstraint(
            "method IN ('native','ocr','visual_ai','human')",
            name="ck_understanding_candidate_method",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(observation)='object' AND octet_length(observation::text)<=2097152",
            name="ck_understanding_candidate_observation",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(educational_understanding)='object' "
            "AND octet_length(educational_understanding::text)<=2097152",
            name="ck_understanding_candidate_education",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(uncertainties)='array' AND jsonb_array_length(uncertainties)<=128 "
            "AND octet_length(uncertainties::text)<=1048576",
            name="ck_understanding_candidate_uncertainties",
        ),
        sa.CheckConstraint(
            f"fingerprint=public.source_understanding_fingerprint({_CANDIDATE_SNAPSHOT})",
            name="ck_understanding_candidate_fingerprint",
        ),
    )
    op.create_table(
        "source_understanding_regions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("region_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("parent_key", sa.String(64), nullable=True),
        sa.UniqueConstraint("candidate_id", "region_key", name="uq_understanding_region_key"),
        sa.UniqueConstraint(
            "id",
            "candidate_id",
            "document_id",
            "page_number",
            name="uq_understanding_region_source",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_region_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "parent_key"],
            [
                "source_understanding_regions.candidate_id",
                "source_understanding_regions.region_key",
            ],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            name="fk_understanding_region_parent",
        ),
        sa.CheckConstraint(
            "region_key ~ '^[a-z][a-z0-9_-]{0,63}$' AND reading_order >= 0",
            name="ck_understanding_region_key",
        ),
    )
    op.create_table(
        "source_understanding_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "candidate_id", "fingerprint", name="uq_understanding_report_fingerprint"
        ),
        sa.UniqueConstraint(
            "id",
            "candidate_id",
            "document_id",
            "page_number",
            name="uq_understanding_report_source",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_report_candidate",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304",
            name="ck_understanding_report_payload",
        ),
        sa.CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_understanding_report_hash",
        ),
    )
    op.create_table(
        "source_understanding_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "id",
            "candidate_id",
            "document_id",
            "page_number",
            name="uq_understanding_decision_source",
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
            name="fk_understanding_decision_report",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536",
            name="ck_understanding_decision_payload",
        ),
        sa.CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_understanding_decision_hash",
        ),
    )
    op.create_table(
        "trusted_page_knowledge",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "document_id", "page_number", "revision", name="uq_trusted_page_revision"
        ),
        sa.UniqueConstraint(
            "id", "candidate_id", "document_id", "page_number", name="uq_trusted_page_source"
        ),
        sa.ForeignKeyConstraint(
            ["id", "candidate_id", "document_id", "page_number"],
            [
                "source_understanding_decisions.id",
                "source_understanding_decisions.candidate_id",
                "source_understanding_decisions.document_id",
                "source_understanding_decisions.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_trusted_page_decision",
        ),
        sa.CheckConstraint("revision > 0 AND page_number > 0", name="ck_trusted_page_revision"),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304",
            name="ck_trusted_page_payload",
        ),
        sa.CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_trusted_page_hash",
        ),
    )
    op.create_table(
        "source_understanding_pages",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trusted_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(32), nullable=False, server_default="unprocessed"),
        sa.Column("current_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("current_report_id", sa.Uuid(), nullable=True),
        sa.Column("current_trusted_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey(
                "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
            ),
            nullable=True,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["current_candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_page_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["current_report_id", "current_candidate_id", "document_id", "page_number"],
            [
                "source_understanding_reports.id",
                "source_understanding_reports.candidate_id",
                "source_understanding_reports.document_id",
                "source_understanding_reports.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_page_report",
        ),
        sa.ForeignKeyConstraint(
            ["current_trusted_id", "current_candidate_id", "document_id", "page_number"],
            [
                "trusted_page_knowledge.id",
                "trusted_page_knowledge.candidate_id",
                "trusted_page_knowledge.document_id",
                "trusted_page_knowledge.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_page_trusted",
        ),
        sa.CheckConstraint(
            "page_number > 0 AND version >= 0 "
            "AND candidate_revision >= 0 AND trusted_revision >= 0",
            name="ck_understanding_page_versions",
        ),
        sa.CheckConstraint(
            "state IN ('unprocessed','observed','processing','needs_human_review',"
            "'needs_reprocessing','corrected','verified','excluded')",
            name="ck_understanding_page_state",
        ),
        sa.CheckConstraint(
            "(version=0 AND state='unprocessed' AND current_candidate_id IS NULL "
            "AND current_report_id IS NULL AND current_trusted_id IS NULL AND event_id IS NULL) "
            "OR (version>0 AND event_id IS NOT NULL)",
            name="ck_understanding_page_event",
        ),
        sa.CheckConstraint(
            "(state='verified')=(current_trusted_id IS NOT NULL)",
            name="ck_understanding_page_trust",
        ),
    )
    op.create_index(
        "ix_understanding_page_state",
        "source_understanding_pages",
        ["document_id", "state", "page_number"],
    )
    _create_guards()


def _create_guards() -> None:
    op.execute("""
        CREATE FUNCTION public.validate_understanding_run() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE; image jsonb;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            image := NEW.image_metadata;
            IF NOT FOUND OR NOT source.active_for_ai
                OR source.quarantined_for_teacher_use
                OR source.checksum_sha256<>NEW.source_sha256
                OR source.original_page_count IS NULL
                OR NEW.page_number>source.original_page_count
                OR image->>'document_id' IS DISTINCT FROM NEW.document_id::text
                OR image->>'source_checksum_sha256' IS DISTINCT FROM NEW.source_sha256
                OR image->>'source_object_key' IS DISTINCT FROM source.object_key
                OR image->'source_size_bytes' IS DISTINCT FROM to_jsonb(source.size_bytes)
                OR image->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR image->>'sha256' IS DISTINCT FROM NEW.image_sha256
                OR image->>'content_type' IS DISTINCT FROM 'image/png'
                OR image->'artifact'->>'sha256' IS DISTINCT FROM NEW.image_sha256
                OR image->'artifact'->>'namespace' IS DISTINCT FROM 'fidelity-page-images'
            THEN
                RAISE EXCEPTION 'understanding run must bind an active immutable original image'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_run_trigger
            BEFORE INSERT ON public.source_understanding_runs
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_run();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_candidate() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE run public.source_understanding_runs%ROWTYPE;
            page public.source_understanding_pages%ROWTYPE;
        BEGIN
            SELECT * INTO run
                FROM public.source_understanding_runs WHERE id=NEW.run_id;
            IF NOT FOUND
                OR run.method<>NEW.method
                OR run.outcome<>'succeeded'
                OR run.created_by<>NEW.created_by THEN
                RAISE EXCEPTION 'candidate requires its succeeded source run'
                    USING ERRCODE='23514';
            END IF;
            SELECT * INTO page
                FROM public.source_understanding_pages
                WHERE document_id=NEW.document_id AND page_number=NEW.page_number FOR UPDATE;
            IF NOT FOUND
                OR NEW.revision<>page.candidate_revision+1 THEN
                RAISE EXCEPTION 'candidate revision must extend the locked page'
                    USING ERRCODE='23514';
            END IF;
            IF NOT (NEW.observation ?& ARRAY['language','regions','relationships'])
                OR NEW.observation-ARRAY['language','regions','relationships']<>'{}'::jsonb
                OR jsonb_typeof(NEW.observation->'regions') IS DISTINCT FROM 'array'
                OR jsonb_array_length(NEW.observation->'regions') NOT BETWEEN 1 AND 128
                OR jsonb_typeof(NEW.observation->'relationships') IS DISTINCT FROM 'array'
                OR jsonb_typeof(NEW.educational_understanding->'claims') IS DISTINCT FROM 'array'
                OR NEW.educational_understanding-ARRAY['claims']<>'{}'::jsonb
            THEN
                RAISE EXCEPTION 'candidate observation and understanding must remain structured'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_candidate_trigger
            BEFORE INSERT ON public.source_understanding_candidates
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_candidate();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_region() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE region jsonb;
        BEGIN
            SELECT item INTO region
                FROM public.source_understanding_candidates c,
                jsonb_array_elements(c.observation->'regions') item
                WHERE c.id=NEW.candidate_id AND item->>'key'=NEW.region_key;
            IF NOT FOUND OR region->>'kind' IS DISTINCT FROM NEW.kind
                OR region->'reading_order' IS DISTINCT FROM to_jsonb(NEW.reading_order)
                OR region->>'parent_key' IS DISTINCT FROM NEW.parent_key THEN
                RAISE EXCEPTION 'region identity must match its immutable observation'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_region_trigger
            BEFORE INSERT ON public.source_understanding_regions
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_region();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_report() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE candidate public.source_understanding_candidates%ROWTYPE;
        BEGIN
            SELECT * INTO candidate
                FROM public.source_understanding_candidates WHERE id=NEW.candidate_id;
            IF NOT FOUND
                OR NEW.payload->>'candidate_id' IS DISTINCT FROM candidate.id::text
                OR NEW.payload->>'candidate_fingerprint' IS DISTINCT FROM candidate.fingerprint
                OR NEW.payload->>'policy_version'
                    IS DISTINCT FROM 'page-understanding-verification.v1'
                OR jsonb_typeof(NEW.payload->'source_checker_version') IS DISTINCT FROM 'string'
                OR jsonb_typeof(NEW.payload->'findings') IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'verification report must bind its exact candidate and policy'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_report_trigger
            BEFORE INSERT ON public.source_understanding_reports
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_report();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_decision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE candidate public.source_understanding_candidates%ROWTYPE;
            report public.source_understanding_reports%ROWTYPE;
        BEGIN
            SELECT * INTO candidate
                FROM public.source_understanding_candidates WHERE id=NEW.candidate_id;
            SELECT * INTO report
                FROM public.source_understanding_reports WHERE id=NEW.report_id;
            IF NOT FOUND
                OR NEW.payload->>'id' IS DISTINCT FROM NEW.id::text
                OR NEW.payload->>'actor_id' IS DISTINCT FROM NEW.created_by::text
                OR NEW.payload->>'candidate_id' IS DISTINCT FROM NEW.candidate_id::text
                OR NEW.payload->>'candidate_fingerprint' IS DISTINCT FROM candidate.fingerprint
                OR NEW.payload->>'report_fingerprint' IS DISTINCT FROM report.fingerprint
                OR NEW.payload->>'policy_version' IS DISTINCT FROM report.payload->>'policy_version'
                OR NEW.payload->>'source_checker_version'
                    IS DISTINCT FROM report.payload->>'source_checker_version'
                OR NEW.payload->'compared_with_original' IS DISTINCT FROM 'true'::jsonb
                OR NEW.payload->'source' IS DISTINCT FROM jsonb_build_object(
                    'document_id',candidate.document_id::text,
                    'source_sha256',candidate.source_sha256,
                    'page_number',candidate.page_number,'image_sha256',candidate.image_sha256)
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(report.payload->'findings') item
                    WHERE item->>'severity'='block')
            THEN
                RAISE EXCEPTION
                'source trust requires an explicit current source comparison and nonblocking report'
                    USING ERRCODE='23514';
            END IF;
            IF NOT public.source_understanding_keys_match(
                    NEW.payload->'reviewed_region_keys',
                    (SELECT coalesce(jsonb_agg(item->'key'),'[]'::jsonb)
                        FROM jsonb_array_elements(candidate.observation->'regions') item),true)
                OR NOT public.source_understanding_keys_match(
                    NEW.payload->'accepted_claim_keys',
                    (SELECT coalesce(jsonb_agg(item->'key'),'[]'::jsonb)
                        FROM jsonb_array_elements(
                            candidate.educational_understanding->'claims') item),false)
                OR NOT public.source_understanding_keys_match(
                    NEW.payload->'resolved_uncertainty_keys',
                    (SELECT coalesce(jsonb_agg(item->'key'),'[]'::jsonb)
                        FROM jsonb_array_elements(candidate.uncertainties) item),true)
                OR jsonb_typeof(NEW.payload->'reason') IS DISTINCT FROM 'string'
                OR char_length(NEW.payload->>'reason') NOT BETWEEN 1 AND 2000
                OR NEW.payload->>'reason'<>btrim(NEW.payload->>'reason')
                OR NEW.payload->>'reason' ~ '^[[:space:]]*$|[[:cntrl:]]'
            THEN
                RAISE EXCEPTION 'verification requires exact review attestations and a clean reason'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_decision_trigger
            BEFORE INSERT ON public.source_understanding_decisions
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_decision();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_trusted_page_knowledge() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE decision public.source_understanding_decisions%ROWTYPE;
            candidate public.source_understanding_candidates%ROWTYPE;
            page public.source_understanding_pages%ROWTYPE; accepted jsonb;
        BEGIN
            SELECT * INTO decision
                FROM public.source_understanding_decisions WHERE id=NEW.id;
            SELECT * INTO candidate
                FROM public.source_understanding_candidates WHERE id=NEW.candidate_id;
            SELECT * INTO page
                FROM public.source_understanding_pages
                WHERE document_id=NEW.document_id AND page_number=NEW.page_number FOR UPDATE;
            IF NOT FOUND OR page.current_candidate_id IS DISTINCT FROM NEW.candidate_id
                OR page.current_report_id IS DISTINCT FROM decision.report_id
                OR NEW.revision<>page.trusted_revision+1
                OR NEW.created_by<>decision.created_by
                OR NEW.payload->>'id' IS DISTINCT FROM NEW.id::text
                OR NEW.payload->'revision' IS DISTINCT FROM to_jsonb(NEW.revision)
                OR NEW.payload->'source' IS DISTINCT FROM decision.payload->'source'
                OR NEW.payload->'decision' IS DISTINCT FROM decision.payload
                OR NEW.payload->'observation' IS DISTINCT FROM candidate.observation
                OR NEW.payload->'resolved_uncertainties' IS DISTINCT FROM candidate.uncertainties
            THEN
                RAISE EXCEPTION
                    'trusted knowledge requires a current immutable verification snapshot'
                    USING ERRCODE='23514';
            END IF;
            SELECT jsonb_build_object('claims',
                coalesce(jsonb_agg(item ORDER BY ordinal),'[]'::jsonb)) INTO accepted
                FROM jsonb_array_elements(candidate.educational_understanding->'claims')
                    WITH ORDINALITY AS claim(item,ordinal)
                WHERE decision.payload->'accepted_claim_keys' ? (item->>'key');
            IF NEW.payload->'education' IS DISTINCT FROM accepted
                OR decision.payload->>'verified_content_fingerprint' IS DISTINCT
                FROM public.source_understanding_fingerprint(
                    jsonb_build_object('schema_version','page-understanding.v1',
                        'observation',candidate.observation,'education',accepted,
                        'uncertainties',candidate.uncertainties)) THEN
                RAISE EXCEPTION
                    'trusted educational meaning must match the explicitly accepted claims'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_trusted_page_knowledge_trigger
            BEFORE INSERT ON public.trusted_page_knowledge
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_trusted_page_knowledge();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_page() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE;
            candidate public.source_understanding_candidates%ROWTYPE;
            trusted public.trusted_page_knowledge%ROWTYPE; audit public.admin_audit_events%ROWTYPE;
        BEGIN
            SELECT * INTO source FROM public.source_documents WHERE id=NEW.document_id FOR SHARE;
            IF NOT FOUND OR NOT source.active_for_ai
                OR source.quarantined_for_teacher_use
                OR source.original_page_count IS NULL
                OR NEW.page_number>source.original_page_count THEN
                RAISE EXCEPTION 'understanding page requires an active original page'
                    USING ERRCODE='23514';
            END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.version<>0
                    OR NEW.candidate_revision<>0
                    OR NEW.trusted_revision<>0 THEN
                    RAISE EXCEPTION 'understanding page must start unverified'
                        USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.document_id<>OLD.document_id
                OR NEW.page_number<>OLD.page_number
                OR NEW.version<>OLD.version+1 THEN
                RAISE EXCEPTION 'understanding page updates require the next version'
                    USING ERRCODE='23514';
            END IF;
            SELECT * INTO candidate
                FROM public.source_understanding_candidates WHERE id=NEW.current_candidate_id;
            IF NOT FOUND
                OR candidate.document_id<>NEW.document_id
                OR candidate.page_number<>NEW.page_number
                OR candidate.source_sha256<>source.checksum_sha256
                OR candidate.revision<>NEW.candidate_revision
                OR NEW.current_report_id IS NULL THEN
                RAISE EXCEPTION 'understanding page must bind its current candidate and report'
                    USING ERRCODE='23514';
            END IF;
            IF NEW.current_candidate_id IS DISTINCT FROM OLD.current_candidate_id THEN
                IF NEW.candidate_revision<>OLD.candidate_revision+1
                    OR NEW.trusted_revision<>OLD.trusted_revision
                    OR NEW.state NOT IN ('needs_human_review','needs_reprocessing','corrected')
                    OR NEW.current_trusted_id IS NOT NULL THEN
                    RAISE EXCEPTION 'new observations must invalidate active trusted knowledge'
                        USING ERRCODE='23514';
                END IF;
            ELSE
                IF NEW.candidate_revision<>OLD.candidate_revision OR NEW.state<>'verified' THEN
                    RAISE EXCEPTION
                        'unchanged candidate transition requires explicit verification'
                        USING ERRCODE='23514';
                END IF;
                SELECT * INTO trusted FROM public.trusted_page_knowledge
                    WHERE id=NEW.current_trusted_id;
                IF NOT FOUND OR trusted.revision<>NEW.trusted_revision
                    OR NEW.trusted_revision<>OLD.trusted_revision+1 THEN
                    RAISE EXCEPTION 'verified page must advance trusted revision'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.event_id;
            IF NOT FOUND
                OR audit.actor_id<>NEW.updated_by
                OR audit.resource_type<>'page_understanding'
                OR audit.resource_id<>NEW.document_id
                OR audit.payload->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR audit.payload->'version' IS DISTINCT FROM to_jsonb(NEW.version)
                OR audit.payload->>'candidate_id' IS DISTINCT FROM NEW.current_candidate_id::text
                OR audit.payload->>'report_id' IS DISTINCT FROM NEW.current_report_id::text
                OR audit.payload->>'trusted_knowledge_id'
                    IS DISTINCT FROM NEW.current_trusted_id::text THEN
                RAISE EXCEPTION 'page understanding state requires matching audit evidence'
                    USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_understanding_page_trigger
            BEFORE INSERT OR UPDATE ON public.source_understanding_pages
            FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_page();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE audit public.admin_audit_events%ROWTYPE; identity_field text;
        BEGIN
            identity_field := CASE TG_TABLE_NAME
                WHEN 'source_understanding_runs' THEN 'run_id'
                WHEN 'source_understanding_candidates' THEN 'candidate_id'
                WHEN 'source_understanding_reports' THEN 'report_id'
                ELSE 'trusted_knowledge_id' END;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF NOT FOUND
                OR audit.actor_id<>NEW.created_by
                OR audit.resource_type<>'page_understanding'
                OR audit.resource_id<>NEW.document_id
                OR audit.payload->'page_number' IS DISTINCT FROM to_jsonb(NEW.page_number)
                OR audit.payload->>identity_field IS DISTINCT FROM NEW.id::text
                OR audit.action<>(CASE WHEN TG_TABLE_NAME
                    IN ('source_understanding_decisions','trusted_page_knowledge')
                    THEN 'page_understanding.verified' ELSE 'page_understanding.observed' END) THEN
                RAISE EXCEPTION 'understanding evidence requires matching append-only audit'
                    USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.validate_understanding_regions_complete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (SELECT count(*)
                FROM public.source_understanding_regions WHERE candidate_id=NEW.id)
                <>jsonb_array_length(NEW.observation->'regions') THEN
                RAISE EXCEPTION 'all observed regions require immutable identities'
                    USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END; $$;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER understanding_regions_complete
            AFTER INSERT ON public.source_understanding_candidates
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.validate_understanding_regions_complete();
    """)
    op.execute("""
        CREATE FUNCTION public.trusted_page_knowledge_is_current(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS(SELECT 1 FROM public.trusted_page_knowledge k
                JOIN public.source_understanding_pages p
                    ON p.document_id=k.document_id AND p.page_number=k.page_number
                    AND p.current_trusted_id=k.id AND p.current_candidate_id=k.candidate_id
                    AND p.state='verified'
                JOIN public.source_documents d ON d.id=k.document_id
                WHERE k.id=identifier AND d.active_for_ai AND NOT d.quarantined_for_teacher_use
                    AND d.checksum_sha256=k.payload->'source'->>'source_sha256'
                    AND k.payload->'decision'->>'policy_version'
                        ='page-understanding-verification.v1'
                    AND k.payload->'decision'->>'source_checker_version'
                        ='source-fidelity-v2/rules-2/ucd-15.0.0');
        $$;
    """)
    for table in _TABLES[:-1]:
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_{table}_truncate BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )
        if table != "source_understanding_regions":
            op.execute(
                f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON public.{table} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION public.validate_understanding_audit()"
            )
    op.execute(
        "CREATE TRIGGER immutable_understanding_page_delete "
        "BEFORE DELETE ON public.source_understanding_pages "
        "FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
    )
    op.execute(
        "CREATE TRIGGER immutable_understanding_page_truncate "
        "BEFORE TRUNCATE ON public.source_understanding_pages "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE " + ",".join(_TABLES) + " IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1
                FROM public.source_understanding_runs)
                OR EXISTS(SELECT 1
                FROM public.source_understanding_candidates)
                OR EXISTS(SELECT 1
                FROM public.source_understanding_regions)
                OR EXISTS(SELECT 1
                FROM public.source_understanding_reports)
                OR EXISTS(SELECT 1
                FROM public.source_understanding_decisions)
                OR EXISTS(SELECT 1 FROM public.trusted_page_knowledge)
                OR EXISTS(SELECT 1
                FROM public.source_understanding_pages)
            THEN
                RAISE EXCEPTION 'cannot discard document understanding evidence'
                    USING ERRCODE='23514';
            END IF;
        END; $$;
    """)
    op.execute("DROP FUNCTION public.trusted_page_knowledge_is_current(uuid)")
    for table in reversed(_TABLES):
        op.drop_table(table)
    for name in (
        "validate_understanding_run",
        "validate_understanding_candidate",
        "validate_understanding_region",
        "validate_understanding_report",
        "validate_understanding_decision",
        "validate_trusted_page_knowledge",
        "validate_understanding_page",
        "validate_understanding_audit",
        "validate_understanding_regions_complete",
    ):
        op.execute(f"DROP FUNCTION public.{name}()")
    op.execute("DROP FUNCTION public.source_understanding_keys_match(jsonb,jsonb,boolean)")
    op.execute("DROP FUNCTION public.source_understanding_fingerprint(jsonb)")
