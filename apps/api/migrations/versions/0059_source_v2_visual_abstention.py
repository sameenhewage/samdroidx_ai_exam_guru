"""D18: an abstained figure may still be verified as a visual.

`ck_source_v2_no_verified_abstention` encodes a pre-D18 assumption: abstaining
means the region cannot be trusted, so it can never be verified. That is right
for text and wrong for a drawing. The primary reading of `p186-r002` abstains
because the figure carries no printed text - which is the correct reading, not
a failure - and the region is still real educational source.

The rule is narrowed rather than dropped: an abstained candidate may reach
`verified` only when it is `visual_only`, where having no text is the point.
"""

from alembic import op

revision = "0059_source_v2_visual_abstention"
down_revision = "0058_source_v2_candidate_crop"
branch_labels = None
depends_on = None

TABLE = "source_v2_machine_candidates"
NAME = "ck_source_v2_no_verified_abstention"


def upgrade() -> None:
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(
        NAME,
        TABLE,
        "NOT abstained OR state <> 'verified' OR source_kind = 'visual_only'",
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(
                SELECT 1 FROM public.source_v2_machine_candidates
                WHERE abstained AND state = 'verified' AND source_kind = 'visual_only'
            ) THEN RAISE EXCEPTION
                'cannot restore the old rule while verified visual-only figures exist';
            END IF;
        END; $$;
    """)
    op.drop_constraint(NAME, TABLE, type_="check")
    op.create_check_constraint(NAME, TABLE, "NOT abstained OR state <> 'verified'")
