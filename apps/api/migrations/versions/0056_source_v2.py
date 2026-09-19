"""Source V2 pages, reader candidates, machine candidates, review, verified content.

Forward-only. Nothing existing is altered, no legacy row is promoted to
verified, and no historical migration is touched (decision D8).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0056_source_v2"
down_revision = "0055_source_consensus"
branch_labels = None
depends_on = None

REGION_TYPES = "('text','heading','figure','table','decorative','unknown')"
REGION_STATES = "('unverified','verified','excluded')"
REVIEW_ACTIONS = "('confirm','correct','exclude','reopen')"


def upgrade() -> None:
    op.create_table(
        "source_v2_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("dpi", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("detector_version", sa.String(length=128), nullable=False),
        sa.Column("layout", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", "page_number", name="uq_source_v2_page"),
        sa.CheckConstraint("page_number >= 1", name="ck_source_v2_page_number"),
        sa.CheckConstraint("char_length(image_sha256) = 64", name="ck_source_v2_page_sha"),
    )

    op.create_table(
        "source_v2_reader_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "page_id",
            sa.Uuid(),
            sa.ForeignKey("source_v2_pages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("reader", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure", sa.String(length=400)),
        sa.Column("seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("signals", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("page_id", "region_id", "reader", name="uq_source_v2_reader_region"),
    )
    op.create_index("ix_source_v2_reader_page", "source_v2_reader_candidates", ["page_id"])

    op.create_table(
        "source_v2_machine_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "page_id",
            sa.Uuid(),
            sa.ForeignKey("source_v2_pages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("region_type", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey("source_v2_machine_candidates.id", ondelete="RESTRICT"),
        ),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("abstained", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("chosen_reader", sa.String(length=64)),
        sa.Column("reason", sa.String(length=400), nullable=False),
        sa.Column("critical_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("agreement_ratio", sa.Float(), nullable=False, server_default="1"),
        sa.Column("disagreement", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="unverified"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "page_id", "region_id", "revision", name="uq_source_v2_machine_revision"
        ),
        sa.UniqueConstraint("id", "page_id", "region_id", name="uq_source_v2_machine_identity"),
        sa.CheckConstraint("revision >= 1", name="ck_source_v2_machine_revision"),
        sa.CheckConstraint(
            f"region_type IN {REGION_TYPES}", name="ck_source_v2_machine_region_type"
        ),
        sa.CheckConstraint(f"state IN {REGION_STATES}", name="ck_source_v2_machine_state"),
        sa.CheckConstraint(
            "NOT abstained OR state <> 'verified'", name="ck_source_v2_no_verified_abstention"
        ),
        sa.CheckConstraint(
            "origin IN ('machine','human-correction')", name="ck_source_v2_machine_origin"
        ),
    )
    # Exactly one live reading per region, enforced by the database rather than
    # by every call site remembering to clear the previous one.
    op.create_index(
        "uq_source_v2_machine_current",
        "source_v2_machine_candidates",
        ["page_id", "region_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "source_v2_review_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("compared_with_image_sha256", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=2000)),
        sa.Column("corrected_text", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "page_id", "region_id"],
            [
                "source_v2_machine_candidates.id",
                "source_v2_machine_candidates.page_id",
                "source_v2_machine_candidates.region_id",
            ],
            ondelete="RESTRICT",
            name="fk_source_v2_event_candidate",
        ),
        sa.CheckConstraint(f"action IN {REVIEW_ACTIONS}", name="ck_source_v2_event_action"),
        sa.CheckConstraint(
            "char_length(compared_with_image_sha256) = 64", name="ck_source_v2_event_sha"
        ),
        sa.CheckConstraint(
            "action <> 'exclude' OR (note IS NOT NULL AND btrim(note) <> '')",
            name="ck_source_v2_exclude_needs_reason",
        ),
    )
    op.create_index(
        "ix_source_v2_event_page", "source_v2_review_events", ["page_id", "region_id"]
    )

    op.create_table(
        "source_v2_verified_regions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_nfc", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("page_id", "region_id", name="uq_source_v2_verified_region"),
        sa.ForeignKeyConstraint(
            ["candidate_id", "page_id", "region_id"],
            [
                "source_v2_machine_candidates.id",
                "source_v2_machine_candidates.page_id",
                "source_v2_machine_candidates.region_id",
            ],
            ondelete="RESTRICT",
            name="fk_source_v2_verified_candidate",
        ),
        sa.CheckConstraint("char_length(image_sha256) = 64", name="ck_source_v2_verified_sha"),
        sa.CheckConstraint("btrim(text) <> ''", name="ck_source_v2_verified_not_empty"),
    )

    # Review history is evidence. It may be written once and never rewritten.
    op.execute("""
        CREATE FUNCTION public.source_v2_events_are_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'source_v2_review_events is append-only';
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER source_v2_events_append_only
        BEFORE UPDATE OR DELETE ON public.source_v2_review_events
        FOR EACH ROW EXECUTE FUNCTION public.source_v2_events_are_append_only();
    """)

    # Verified content must cite the page image it was compared against, and a
    # confirmation must exist for exactly that candidate revision.
    op.execute("""
        CREATE FUNCTION public.source_v2_verified_requires_confirmation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE page_sha text;
        BEGIN
            SELECT image_sha256 INTO page_sha FROM public.source_v2_pages WHERE id = NEW.page_id;
            IF page_sha IS NULL OR page_sha <> NEW.image_sha256 THEN
                RAISE EXCEPTION 'verified content must cite the current rendered page';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.source_v2_review_events e
                WHERE e.page_id = NEW.page_id
                  AND e.region_id = NEW.region_id
                  AND e.candidate_id = NEW.candidate_id
                  AND e.candidate_revision = NEW.candidate_revision
                  AND e.action = 'confirm'
            ) THEN
                RAISE EXCEPTION 'verified content needs a confirm event for that revision';
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER source_v2_verified_requires_confirmation_trigger
        BEFORE INSERT OR UPDATE ON public.source_v2_verified_regions
        FOR EACH ROW EXECUTE FUNCTION public.source_v2_verified_requires_confirmation();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM public.source_v2_verified_regions)
                OR EXISTS(SELECT 1 FROM public.source_v2_review_events)
            THEN RAISE EXCEPTION 'cannot discard retained Source V2 verification evidence'; END IF;
        END; $$;
    """)
    op.execute(
        "DROP TRIGGER source_v2_verified_requires_confirmation_trigger "
        "ON public.source_v2_verified_regions"
    )
    op.execute("DROP FUNCTION public.source_v2_verified_requires_confirmation()")
    op.execute("DROP TRIGGER source_v2_events_append_only ON public.source_v2_review_events")
    op.execute("DROP FUNCTION public.source_v2_events_are_append_only()")
    op.drop_table("source_v2_verified_regions")
    op.drop_index("ix_source_v2_event_page", table_name="source_v2_review_events")
    op.drop_table("source_v2_review_events")
    op.drop_index("uq_source_v2_machine_current", table_name="source_v2_machine_candidates")
    op.drop_table("source_v2_machine_candidates")
    op.drop_index("ix_source_v2_reader_page", table_name="source_v2_reader_candidates")
    op.drop_table("source_v2_reader_candidates")
    op.drop_table("source_v2_pages")
