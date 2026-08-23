from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_admin_audit_events"
down_revision: str | None = "0002_grade5_taxonomy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action = btrim(action) AND length(action) > 0",
            name="ck_admin_audit_event_action",
        ),
        sa.CheckConstraint(
            "resource_type = btrim(resource_type) AND length(resource_type) > 0",
            name="ck_admin_audit_event_resource_type",
        ),
    )
    op.create_index(
        "ix_admin_audit_events_actor_created",
        "admin_audit_events",
        ["actor_id", "created_at"],
    )
    op.create_index(
        "ix_admin_audit_events_resource_created",
        "admin_audit_events",
        ["resource_type", "resource_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_admin_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'admin audit events are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_audit_events_append_only
        BEFORE UPDATE OR DELETE ON admin_audit_events
        FOR EACH ROW
        EXECUTE FUNCTION reject_admin_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_admin_audit_events_append_only ON admin_audit_events")
    op.execute("DROP FUNCTION reject_admin_audit_event_mutation()")
    op.drop_index("ix_admin_audit_events_resource_created", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_actor_created", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
