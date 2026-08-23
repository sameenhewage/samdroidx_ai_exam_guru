from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_taxonomy_review_lifecycle"
down_revision: str | None = "0003_admin_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "taxonomy_nodes",
        sa.Column(
            "review_state",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
        ),
    )
    op.create_check_constraint(
        "ck_taxonomy_node_review_state",
        "taxonomy_nodes",
        "review_state IN ('draft', 'reviewed', 'deprecated')",
    )
    op.create_check_constraint(
        "ck_taxonomy_node_review_state_active",
        "taxonomy_nodes",
        "(review_state = 'deprecated' AND NOT active) OR "
        "(review_state IN ('draft', 'reviewed') AND active)",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_taxonomy_node_hierarchy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_level text;
            parent_active boolean;
            parent_review_state text;
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.level <> OLD.level
                OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
            ) THEN
                RAISE EXCEPTION 'taxonomy identity and hierarchy are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'UPDATE' AND OLD.review_state = 'reviewed' AND (
                NEW.code <> OLD.code OR NEW.title <> OLD.title
            ) THEN
                RAISE EXCEPTION 'reviewed taxonomy nodes are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'UPDATE' AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state IN (
                    'draft', 'reviewed', 'deprecated'
                ))
                OR (OLD.review_state = 'reviewed' AND NEW.review_state IN (
                    'reviewed', 'deprecated'
                ))
                OR (OLD.review_state = 'deprecated' AND NEW.review_state = 'deprecated')
            ) THEN
                RAISE EXCEPTION 'invalid taxonomy review transition'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.parent_id IS NOT NULL THEN
                SELECT level, active, review_state
                INTO parent_level, parent_active, parent_review_state
                FROM taxonomy_nodes
                WHERE id = NEW.parent_id
                  AND curriculum_version_id = NEW.curriculum_version_id;

                IF FOUND AND NOT (
                    (NEW.level = 'skill' AND parent_level = 'competency')
                    OR (NEW.level = 'sub_skill' AND parent_level = 'skill')
                    OR (NEW.level = 'learning_concept' AND parent_level = 'sub_skill')
                ) THEN
                    RAISE EXCEPTION 'taxonomy node has an invalid parent level'
                        USING ERRCODE = '23514';
                END IF;

                IF FOUND AND NEW.active AND NOT parent_active THEN
                    RAISE EXCEPTION 'active taxonomy node cannot have an inactive parent'
                        USING ERRCODE = '23514';
                END IF;

                IF FOUND
                    AND NEW.review_state = 'reviewed'
                    AND parent_review_state <> 'reviewed'
                THEN
                    RAISE EXCEPTION 'reviewed taxonomy node requires a reviewed parent'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            IF NOT NEW.active AND EXISTS (
                SELECT 1
                FROM taxonomy_nodes
                WHERE parent_id = NEW.id
                  AND curriculum_version_id = NEW.curriculum_version_id
                  AND active
            ) THEN
                RAISE EXCEPTION 'taxonomy parent with active children cannot be deactivated'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_taxonomy_node_hierarchy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_level text;
            parent_active boolean;
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.level <> OLD.level
                OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
            ) THEN
                RAISE EXCEPTION 'taxonomy identity and hierarchy are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.parent_id IS NOT NULL THEN
                SELECT level, active
                INTO parent_level, parent_active
                FROM taxonomy_nodes
                WHERE id = NEW.parent_id
                  AND curriculum_version_id = NEW.curriculum_version_id;

                IF FOUND AND NOT (
                    (NEW.level = 'skill' AND parent_level = 'competency')
                    OR (NEW.level = 'sub_skill' AND parent_level = 'skill')
                    OR (NEW.level = 'learning_concept' AND parent_level = 'sub_skill')
                ) THEN
                    RAISE EXCEPTION 'taxonomy node has an invalid parent level'
                        USING ERRCODE = '23514';
                END IF;

                IF FOUND AND NEW.active AND NOT parent_active THEN
                    RAISE EXCEPTION 'active taxonomy node cannot have an inactive parent'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            IF NOT NEW.active AND EXISTS (
                SELECT 1
                FROM taxonomy_nodes
                WHERE parent_id = NEW.id
                  AND curriculum_version_id = NEW.curriculum_version_id
                  AND active
            ) THEN
                RAISE EXCEPTION 'taxonomy parent with active children cannot be deactivated'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.drop_constraint(
        "ck_taxonomy_node_review_state_active",
        "taxonomy_nodes",
        type_="check",
    )
    op.drop_constraint(
        "ck_taxonomy_node_review_state",
        "taxonomy_nodes",
        type_="check",
    )
    op.drop_column("taxonomy_nodes", "review_state")
