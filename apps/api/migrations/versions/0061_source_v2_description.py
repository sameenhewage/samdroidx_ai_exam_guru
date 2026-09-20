"""A visual region carries three separate things, so store them separately.

Before this, one `text` column had to hold everything a reviewer was shown for
a figure: the words printed inside the crop, a description of what the picture
shows, and whatever the deterministic checks noticed. Page 186 `p186-r002`
made the cost obvious — an English sentence, ``Line-art figure only (a foam
block, a ring magnet...)``, sitting in the field that means "the exact text
printed on this Sinhala page".

D18 already says a description of a visual is **Derived Knowledge, never
Verified Source Content**. A shared column cannot express that: whatever is in
`text` is source by definition. So the three concepts get three columns:

* ``text`` — unchanged. The exact text printed inside the crop. Source.
* ``visual_description`` — what the picture shows, in the language of the
  material. Derived knowledge. Never source, never embedded as source text.
* ``detected_labels`` — the labels legible inside the crop, as a JSON array,
  so a label is addressable instead of being buried in prose.

Both tables get them. The candidate holds the working value; the verified row
keeps its own copy so a verified visual's description travels with the
verification rather than being re-derived from a candidate that may since have
moved to a new revision.

Forward-only (D8). Nothing here promotes a row to verified, reinterprets an
exclusion, or rewrites any existing text.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0061_source_v2_description"
down_revision = "0060_source_v2_single_reader"
branch_labels = None
depends_on = None

TABLES = ("source_v2_machine_candidates", "source_v2_verified_regions")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("visual_description", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "detected_labels",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
        # A blank description is not a description. Storing one would let the
        # review screen show an empty "machine-generated" section that looks
        # answered when nobody wrote anything.
        op.create_check_constraint(
            f"ck_{table}_description_not_blank",
            table,
            "visual_description IS NULL OR btrim(visual_description) <> ''",
        )
        # The labels are a list of labels. A bare string or object here would
        # be read as one label per character by anything iterating it.
        op.create_check_constraint(
            f"ck_{table}_detected_labels_array",
            table,
            "jsonb_typeof(detected_labels) = 'array'",
        )


def downgrade() -> None:
    # A description attached to verified source is derived knowledge a human
    # accepted. Dropping the column would delete it with no trace, which is
    # the one thing D8 forbids, so refuse rather than discard.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(
                SELECT 1 FROM public.source_v2_verified_regions
                WHERE visual_description IS NOT NULL
            ) THEN RAISE EXCEPTION
                'cannot drop visual_description while verified visuals carry one; '
                'export the descriptions before reversing this migration';
            END IF;
        END; $$;
    """)
    for table in TABLES:
        op.drop_constraint(f"ck_{table}_detected_labels_array", table, type_="check")
        op.drop_constraint(f"ck_{table}_description_not_blank", table, type_="check")
        op.drop_column(table, "detected_labels")
        op.drop_column(table, "visual_description")
