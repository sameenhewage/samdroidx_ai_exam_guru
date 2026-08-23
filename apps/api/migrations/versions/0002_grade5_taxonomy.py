from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_grade5_taxonomy"
down_revision: str | None = "0001_enable_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "exam_configurations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *audit_columns(),
        sa.CheckConstraint("grade = 5", name="ck_exam_configurations_grade_five"),
        sa.CheckConstraint("code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'", name="ck_exam_code"),
        sa.CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_exam_name"),
        sa.UniqueConstraint("code", name="uq_exam_configurations_code"),
    )
    op.create_table(
        "media",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *audit_columns(),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9-]{1,15}$'", name="ck_medium_code"),
        sa.CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_medium_name"),
        sa.UniqueConstraint("code", name="uq_media_code"),
    )
    op.create_table(
        "curriculum_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("exam_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("medium_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *audit_columns(),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_curriculum_code",
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_curriculum_title",
        ),
        sa.ForeignKeyConstraint(
            ["exam_configuration_id"],
            ["exam_configurations.id"],
            name="fk_curriculum_versions_exam_configuration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medium_id"],
            ["media.id"],
            name="fk_curriculum_versions_medium",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "exam_configuration_id",
            "medium_id",
            "code",
            name="uq_curriculum_version_scope_code",
        ),
    )
    op.create_table(
        "taxonomy_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *audit_columns(),
        sa.CheckConstraint(
            "level IN ('competency', 'skill', 'sub_skill', 'learning_concept')",
            name="ck_taxonomy_node_level",
        ),
        sa.CheckConstraint(
            "(level = 'competency' AND parent_id IS NULL) OR "
            "(level <> 'competency' AND parent_id IS NOT NULL)",
            name="ck_taxonomy_node_parent_shape",
        ),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_taxonomy_node_code",
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_taxonomy_node_title",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_taxonomy_nodes_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_taxonomy_node_id_curriculum",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_taxonomy_nodes_parent_curriculum",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_taxonomy_node_sibling_code",
        "taxonomy_nodes",
        ["curriculum_version_id", "parent_id", "level", "code"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.execute(
        """
        CREATE FUNCTION enforce_taxonomy_node_hierarchy()
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
    op.execute(
        """
        CREATE TRIGGER trg_enforce_taxonomy_node_hierarchy
        BEFORE INSERT OR UPDATE ON taxonomy_nodes
        FOR EACH ROW
        EXECUTE FUNCTION enforce_taxonomy_node_hierarchy()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_enforce_taxonomy_node_hierarchy ON taxonomy_nodes")
    op.execute("DROP FUNCTION enforce_taxonomy_node_hierarchy()")
    op.drop_index("uq_taxonomy_node_sibling_code", table_name="taxonomy_nodes")
    op.drop_table("taxonomy_nodes")
    op.drop_table("curriculum_versions")
    op.drop_table("media")
    op.drop_table("exam_configurations")
