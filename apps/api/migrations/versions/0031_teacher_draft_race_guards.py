from collections.abc import Sequence

from alembic import op

revision: str = "0031_teacher_draft_race_guards"
down_revision: str | None = "0030_teacher_marking_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_paper_draft_job_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.practice_paper_id IS NOT NULL AND NEW.status <> 'ready_for_review' THEN
                RAISE EXCEPTION 'teacher paper drafts require a ready-for-review job'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_draft_job_state_trigger
        BEFORE UPDATE ON teacher_paper_jobs
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_draft_job_state()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_teacher_paper_draft_slot_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM teacher_paper_jobs
                WHERE id = NEW.paper_job_id AND practice_paper_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'teacher paper slots are immutable after draft creation'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_teacher_paper_draft_slot_immutability_trigger
        BEFORE UPDATE ON teacher_paper_slots
        FOR EACH ROW EXECUTE FUNCTION enforce_teacher_paper_draft_slot_immutability()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER enforce_teacher_paper_draft_slot_immutability_trigger ON teacher_paper_slots"
    )
    op.execute("DROP FUNCTION enforce_teacher_paper_draft_slot_immutability()")
    op.execute("DROP TRIGGER enforce_teacher_paper_draft_job_state_trigger ON teacher_paper_jobs")
    op.execute("DROP FUNCTION enforce_teacher_paper_draft_job_state()")
