"""Remove the multi-reader persistence from the active Source V2 schema.

The executing AI agent reading the canonical crop is now the only machine
source reader. `source_v2_reader_candidates` existed to hold competing
readings from local OCR models, and the machine-candidate columns
`chosen_reader`, `critical_conflict`, `agreement_ratio` and `disagreement`
existed only to express which reader won and where the readers differed.
None of those concepts has a meaning any more, so HEAD must not carry them.

Forward-only. Migrations 0001-0059 are untouched and may still mention the
table: history is evidence. This drop is safe because the corroboration table
carries no rows and the four columns carry no signal that is not now
reconstructible from the one reading itself.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060_source_v2_single_reader"
down_revision = "0059_source_v2_visual_abstention"
branch_labels = None
depends_on = None

CANDIDATES = "source_v2_machine_candidates"
READER_COLUMNS = ("chosen_reader", "critical_conflict", "agreement_ratio", "disagreement")


def upgrade() -> None:
    # Reader rows are raw model output, never trust and never referenced by a
    # review event or a verified region. Refuse only if some deployment
    # somehow still holds them, rather than discarding evidence silently.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM public.source_v2_reader_candidates) THEN
                RAISE EXCEPTION
                    'source_v2_reader_candidates still holds rows; export them before '
                    'completing the single-reader cutover';
            END IF;
        END; $$;
    """)
    op.drop_index("ix_source_v2_reader_page", table_name="source_v2_reader_candidates")
    op.drop_table("source_v2_reader_candidates")

    for column in READER_COLUMNS:
        op.drop_column(CANDIDATES, column)


def downgrade() -> None:
    op.add_column(CANDIDATES, sa.Column("chosen_reader", sa.String(length=64)))
    op.add_column(
        CANDIDATES,
        sa.Column("critical_conflict", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        CANDIDATES,
        sa.Column("agreement_ratio", sa.Float(), nullable=False, server_default="1"),
    )
    op.add_column(
        CANDIDATES,
        sa.Column("disagreement", postgresql.JSONB(), nullable=False, server_default="{}"),
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
