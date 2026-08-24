from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012_paper_blueprints"
down_revision: str | None = "0011_analytics_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"
_MAX_SPECIFICATION_BYTES = 524_288
_MAX_BLUEPRINT_BYTES = 2_097_152
_MAX_TAXONOMY_BYTES = 524_288
_MAX_SLOTS = 200
_MAX_TOTAL_MARKS = 100_000


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_analytics_runs_id_curriculum",
        "analytics_runs",
        ["id", "curriculum_version_id"],
    )
    op.execute(
        """
        CREATE FUNCTION paper_blueprint_taxonomy_snapshot_valid(
            candidate jsonb,
            expected_curriculum_id uuid
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            item_count integer;
            distinct_id_count integer;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) NOT BETWEEN 1 AND 800
            THEN
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY[
                        'id', 'curriculum_version_id', 'parent_id', 'level', 'code', 'title',
                        'active', 'review_state', 'reviewed_at', 'reviewed_by'
                    ]
                    OR (SELECT count(*) FROM jsonb_object_keys(item)) <> 10
                    OR jsonb_typeof(item->'id') <> 'string'
                    OR jsonb_typeof(item->'curriculum_version_id') <> 'string'
                    OR jsonb_typeof(item->'level') <> 'string'
                    OR jsonb_typeof(item->'code') <> 'string'
                    OR jsonb_typeof(item->'title') <> 'string'
                    OR jsonb_typeof(item->'active') <> 'boolean'
                    OR jsonb_typeof(item->'review_state') <> 'string'
                    OR jsonb_typeof(item->'reviewed_at') <> 'string'
                    OR jsonb_typeof(item->'reviewed_by') <> 'string'
                    OR (jsonb_typeof(item->'parent_id') NOT IN ('null', 'string'))
                    OR item->>'curriculum_version_id' <> expected_curriculum_id::text
                    OR item->>'level' NOT IN (
                        'competency', 'skill', 'sub_skill', 'learning_concept'
                    )
                    OR item->'active' <> 'true'::jsonb
                    OR item->>'review_state' <> 'reviewed'
                    OR item->>'id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                        '[0-9a-f]{4}-[0-9a-f]{12}$'
                    OR item->>'reviewed_by' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                        '[0-9a-f]{4}-[0-9a-f]{12}$'
                    OR item->>'code' <> btrim(item->>'code')
                    OR char_length(item->>'code') NOT BETWEEN 1 AND 64
                    OR item->>'title' <> btrim(item->>'title')
                    OR char_length(item->>'title') NOT BETWEEN 1 AND 255
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;

            SELECT count(*), count(DISTINCT value->>'id')
            INTO item_count, distinct_id_count
            FROM jsonb_array_elements(candidate);
            RETURN item_count = distinct_id_count;
        END;
        $$
        """
    )
    op.create_table(
        "paper_blueprints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("analytics_run_id", sa.Uuid(), nullable=True),
        sa.Column("blueprint_id", sa.String(27), nullable=False),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("algorithm_version", sa.String(128), nullable=False),
        sa.Column("config_version", sa.String(128), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("total_marks", sa.Integer(), nullable=False),
        sa.Column("slot_count", sa.Integer(), nullable=False),
        sa.Column("specification_fingerprint", sa.String(71), nullable=False),
        sa.Column("input_fingerprint", sa.String(71), nullable=False),
        sa.Column("result_fingerprint", sa.String(71), nullable=False),
        sa.Column("specification", JSONB(), nullable=False),
        sa.Column("blueprint", JSONB(), nullable=False),
        sa.Column("taxonomy_snapshot", JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_paper_blueprints_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analytics_run_id", "curriculum_version_id"],
            ["analytics_runs.id", "analytics_runs.curriculum_version_id"],
            name="fk_paper_blueprints_analytics_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "blueprint_id",
            name="uq_paper_blueprints_blueprint_id",
        ),
        sa.UniqueConstraint(
            "input_fingerprint",
            name="uq_paper_blueprints_input_fingerprint",
        ),
        sa.CheckConstraint(
            "blueprint_id ~ '^bp_[0-9a-f]{24}$'",
            name="ck_paper_blueprints_blueprint_id",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_paper_blueprints_{column_name}",
            )
            for column_name in (
                "specification_fingerprint",
                "input_fingerprint",
                "result_fingerprint",
            )
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} = btrim({column_name}) AND length({column_name}) > 0",
                name=f"ck_paper_blueprints_{column_name}",
            )
            for column_name in (
                "schema_version",
                "algorithm_version",
                "config_version",
            )
        ),
        sa.CheckConstraint(
            f"total_marks BETWEEN 1 AND {_MAX_TOTAL_MARKS}",
            name="ck_paper_blueprints_total_marks",
        ),
        sa.CheckConstraint(
            f"slot_count BETWEEN 1 AND {_MAX_SLOTS}",
            name="ck_paper_blueprints_slot_count",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(specification) = 'object' AND "
            "specification ?& ARRAY['config_version', 'paper_code', 'title', 'total_marks', "
            "'curriculum_scope', 'sections', 'question_type_allocations', "
            "'difficulty_allocations', 'taxonomy_requirements', 'generation_policy'] AND "
            "jsonb_typeof(specification->'curriculum_scope') = 'object' AND "
            "jsonb_typeof(specification->'sections') = 'array' AND "
            "jsonb_array_length(specification->'sections') BETWEEN 1 AND 20 AND "
            "jsonb_typeof(specification->'taxonomy_requirements') = 'array' AND "
            "jsonb_array_length(specification->'taxonomy_requirements') BETWEEN 1 AND 200",
            name="ck_paper_blueprints_specification_shape",
        ),
        sa.CheckConstraint(
            f"pg_column_size(specification) <= {_MAX_SPECIFICATION_BYTES}",
            name="ck_paper_blueprints_specification_size",
        ),
        sa.CheckConstraint(
            "specification->'total_marks' = to_jsonb(total_marks) AND "
            "specification->'config_version' = to_jsonb(config_version) AND "
            "specification->'curriculum_scope'->'curriculum_version_id' = "
            "to_jsonb(curriculum_version_id::text)",
            name="ck_paper_blueprints_specification_metadata",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(blueprint) = 'object' AND "
            "blueprint ?& ARRAY['version', 'paper_code', 'title', 'seed', 'total_marks', "
            "'curriculum_scope', 'sections', 'question_type_allocations', "
            "'difficulty_allocations', 'taxonomy_requirements', 'slots'] AND "
            "jsonb_typeof(blueprint->'version') = 'object' AND "
            "jsonb_typeof(blueprint->'slots') = 'array' AND "
            "jsonb_array_length(blueprint->'slots') = slot_count",
            name="ck_paper_blueprints_blueprint_shape",
        ),
        sa.CheckConstraint(
            f"pg_column_size(blueprint) <= {_MAX_BLUEPRINT_BYTES}",
            name="ck_paper_blueprints_blueprint_size",
        ),
        sa.CheckConstraint(
            "blueprint->'seed' = to_jsonb(seed) AND "
            "blueprint->'total_marks' = to_jsonb(total_marks) AND "
            "blueprint->'curriculum_scope'->'curriculum_version_id' = "
            "to_jsonb(curriculum_version_id::text) AND "
            "blueprint->'version'->'blueprint_id' = to_jsonb(blueprint_id) AND "
            "blueprint->'version'->'schema_version' = to_jsonb(schema_version) AND "
            "blueprint->'version'->'algorithm_version' = to_jsonb(algorithm_version) AND "
            "blueprint->'version'->'config_version' = to_jsonb(config_version)",
            name="ck_paper_blueprints_blueprint_metadata",
        ),
        sa.CheckConstraint(
            f"pg_column_size(taxonomy_snapshot) <= {_MAX_TAXONOMY_BYTES} AND "
            "paper_blueprint_taxonomy_snapshot_valid("
            "taxonomy_snapshot, curriculum_version_id)",
            name="ck_paper_blueprints_taxonomy_snapshot",
        ),
    )
    op.create_index(
        "ix_paper_blueprints_curriculum_created",
        "paper_blueprints",
        ["curriculum_version_id", "created_at", "id"],
    )
    op.create_index(
        "ix_paper_blueprints_analytics_run",
        "paper_blueprints",
        ["analytics_run_id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_paper_blueprint_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'paper blueprints are immutable and append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_paper_blueprint_mutation_trigger
        BEFORE UPDATE OR DELETE ON paper_blueprints
        FOR EACH ROW EXECUTE FUNCTION reject_paper_blueprint_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reject_paper_blueprint_mutation_trigger ON paper_blueprints")
    op.execute("DROP FUNCTION reject_paper_blueprint_mutation()")
    op.drop_index("ix_paper_blueprints_analytics_run", table_name="paper_blueprints")
    op.drop_index("ix_paper_blueprints_curriculum_created", table_name="paper_blueprints")
    op.drop_table("paper_blueprints")
    op.execute("DROP FUNCTION paper_blueprint_taxonomy_snapshot_valid(jsonb, uuid)")
    op.drop_constraint(
        "uq_analytics_runs_id_curriculum",
        "analytics_runs",
        type_="unique",
    )
