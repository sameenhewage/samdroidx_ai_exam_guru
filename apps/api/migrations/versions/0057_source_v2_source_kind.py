"""D18: a region's educational kind, so a figure can be verified as a figure.

Forward-only. Historical migrations untouched, no existing row promoted to
verified, no existing exclusion reinterpreted.

The pre-D18 world could only verify text, so `source_v2_verified_regions`
required non-empty text. That is exactly the rule that made an educational
drawing look like an empty mistake. The check is replaced by one that depends
on the region's kind: text-bearing kinds still require text, a visual-only
region may legitimately have none, and a decorative region may never become
verified educational content.
"""

import sqlalchemy as sa
from alembic import op

revision = "0057_source_v2_source_kind"
down_revision = "0056_source_v2"
branch_labels = None
depends_on = None

KINDS = "('text_only','visual_only','visual_with_text','decorative','undecided')"


def upgrade() -> None:
    for table in ("source_v2_machine_candidates", "source_v2_verified_regions"):
        op.add_column(
            table,
            sa.Column(
                "source_kind",
                sa.String(length=24),
                nullable=False,
                # Everything that already exists was produced by a pipeline
                # that only understood text. Calling it 'undecided' preserves
                # its trust level honestly rather than inventing an
                # educational classification nobody made.
                server_default="undecided",
            ),
        )
        op.create_check_constraint(f"ck_{table}_source_kind", table, f"source_kind IN {KINDS}")

    # A candidate records what the machine *proposed*; the verified row records
    # what a human decided. Keeping both makes a silent reclassification visible.
    op.add_column(
        "source_v2_machine_candidates",
        sa.Column("proposed_source_kind", sa.String(length=24), nullable=True),
    )
    op.create_check_constraint(
        "ck_source_v2_machine_proposed_kind",
        "source_v2_machine_candidates",
        f"proposed_source_kind IS NULL OR proposed_source_kind IN {KINDS}",
    )

    # The canonical crop is the visual evidence. A verified visual without it
    # cannot be audited or embedded later.
    op.add_column(
        "source_v2_verified_regions",
        sa.Column("crop_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "source_v2_verified_regions",
        sa.Column("bbox", sa.String(length=64), nullable=True),
    )

    # Replace the blanket non-empty rule with a kind-aware one.
    op.drop_constraint(
        "ck_source_v2_verified_not_empty", "source_v2_verified_regions", type_="check"
    )
    op.create_check_constraint(
        "ck_source_v2_verified_text_matches_kind",
        "source_v2_verified_regions",
        # text-bearing kinds must carry text; a visual-only region must not be
        # given invented text; decorative can never be verified content.
        "(source_kind IN ('text_only','visual_with_text') AND btrim(text) <> '')"
        " OR (source_kind = 'visual_only')"
        " OR (source_kind = 'undecided' AND btrim(text) <> '')",
    )
    op.create_check_constraint(
        "ck_source_v2_verified_not_decorative",
        "source_v2_verified_regions",
        "source_kind <> 'decorative'",
    )
    op.create_check_constraint(
        "ck_source_v2_verified_visual_has_crop",
        "source_v2_verified_regions",
        "source_kind NOT IN ('visual_only','visual_with_text')"
        " OR crop_sha256 IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(
                SELECT 1 FROM public.source_v2_verified_regions
                WHERE source_kind = 'visual_only'
            ) THEN RAISE EXCEPTION
                'cannot drop source_kind while verified visual-only source exists';
            END IF;
        END; $$;
    """)
    for name in (
        "ck_source_v2_verified_visual_has_crop",
        "ck_source_v2_verified_not_decorative",
        "ck_source_v2_verified_text_matches_kind",
    ):
        op.drop_constraint(name, "source_v2_verified_regions", type_="check")
    op.create_check_constraint(
        "ck_source_v2_verified_not_empty", "source_v2_verified_regions", "btrim(text) <> ''"
    )
    op.drop_column("source_v2_verified_regions", "bbox")
    op.drop_column("source_v2_verified_regions", "crop_sha256")
    op.drop_constraint(
        "ck_source_v2_machine_proposed_kind", "source_v2_machine_candidates", type_="check"
    )
    op.drop_column("source_v2_machine_candidates", "proposed_source_kind")
    for table in ("source_v2_verified_regions", "source_v2_machine_candidates"):
        op.drop_constraint(f"ck_{table}_source_kind", table, type_="check")
        op.drop_column(table, "source_kind")
