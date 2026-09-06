from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_upload_request_identity"
down_revision: str | None = "0037_studio_fixture_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_upload_sessions", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_source_upload_owner_request", "source_upload_sessions", ["owner_id", "request_id"]
    )
    op.execute(
        """
        CREATE FUNCTION public.guard_source_upload_request_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.request_id IS DISTINCT FROM OLD.request_id THEN
                RAISE EXCEPTION 'source upload request identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_source_upload_request_identity_trigger "
        "BEFORE UPDATE ON source_upload_sessions FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_source_upload_request_identity()"
    )
    op.execute(
        """
        CREATE FUNCTION public.check_source_upload_request_audit()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.request_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.admin_audit_events
                WHERE resource_type = 'source_upload' AND resource_id = NEW.id
                    AND actor_id = NEW.owner_id AND action = 'source_upload.created'
                    AND payload->>'request_id' = NEW.request_id::text
            ) THEN
                RAISE EXCEPTION 'source upload request identity requires creation audit'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER check_source_upload_request_audit_trigger "
        "AFTER INSERT ON source_upload_sessions DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION public.check_source_upload_request_audit()"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.source_upload_sessions WHERE request_id IS NOT NULL)
            THEN
                RAISE EXCEPTION 'cannot discard source upload request identities'
                    USING ERRCODE = '23514';
            END IF;
        END $$
        """
    )
    op.execute("DROP TRIGGER check_source_upload_request_audit_trigger ON source_upload_sessions")
    op.execute("DROP FUNCTION public.check_source_upload_request_audit()")
    op.execute(
        "DROP TRIGGER guard_source_upload_request_identity_trigger ON source_upload_sessions"
    )
    op.execute("DROP FUNCTION public.guard_source_upload_request_identity()")
    op.drop_constraint("uq_source_upload_owner_request", "source_upload_sessions", type_="unique")
    op.drop_column("source_upload_sessions", "request_id")
