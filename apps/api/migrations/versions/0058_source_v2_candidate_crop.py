"""D18 runtime: a candidate carries the canonical crop it was read from.

0057 put `crop_sha256` on the verified row, which is where the constraint
needs it. But the verified row copies from the candidate, so the candidate has
to carry it too or a visual can never be confirmed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_source_v2_candidate_crop"
down_revision = "0057_source_v2_source_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_v2_machine_candidates",
        sa.Column("crop_sha256", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_source_v2_machine_crop_sha",
        "source_v2_machine_candidates",
        "crop_sha256 IS NULL OR char_length(crop_sha256) = 64",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_source_v2_machine_crop_sha", "source_v2_machine_candidates", type_="check"
    )
    op.drop_column("source_v2_machine_candidates", "crop_sha256")
