from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_analytics_runs"
down_revision: str | None = "0010_historical_answer_encoding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "analytics_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("run_fingerprint", sa.String(71), nullable=False),
        sa.Column("config_fingerprint", sa.String(71), nullable=False),
        sa.Column("input_fingerprint", sa.String(71), nullable=False),
        sa.Column("source_fingerprint", sa.String(71), nullable=False),
        sa.Column("result_fingerprint", sa.String(71), nullable=False),
        sa.Column("statistics_algorithm_version", sa.String(128), nullable=False),
        sa.Column("practice_priority_algorithm_version", sa.String(128), nullable=False),
        sa.Column("baseline_algorithm_version", sa.String(128), nullable=False),
        sa.Column("backtest_algorithm_version", sa.String(128), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("input_snapshot", JSONB(), nullable=False),
        sa.Column("source_versions", JSONB(), nullable=False),
        sa.Column("data_quality", JSONB(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("compute_duration_ms", sa.Integer(), nullable=False),
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
            name="fk_analytics_runs_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "run_fingerprint",
            name="uq_analytics_runs_run_fingerprint",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_analytics_runs_{column_name}",
            )
            for column_name in (
                "run_fingerprint",
                "config_fingerprint",
                "input_fingerprint",
                "source_fingerprint",
                "result_fingerprint",
            )
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} = btrim({column_name}) AND length({column_name}) > 0",
                name=f"ck_analytics_runs_{column_name}",
            )
            for column_name in (
                "statistics_algorithm_version",
                "practice_priority_algorithm_version",
                "baseline_algorithm_version",
                "backtest_algorithm_version",
            )
        ),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_analytics_runs_config_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_analytics_runs_input_snapshot_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_versions) = 'array'",
            name="ck_analytics_runs_source_versions_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(data_quality) = 'object'",
            name="ck_analytics_runs_data_quality_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result) = 'object'",
            name="ck_analytics_runs_result_object",
        ),
        sa.CheckConstraint(
            "compute_duration_ms >= 0",
            name="ck_analytics_runs_compute_duration_ms",
        ),
    )
    op.create_index(
        "ix_analytics_runs_curriculum_created",
        "analytics_runs",
        ["curriculum_version_id", "created_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_analytics_run_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'analytics runs are immutable and append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_analytics_run_mutation_trigger
        BEFORE UPDATE OR DELETE ON analytics_runs
        FOR EACH ROW EXECUTE FUNCTION reject_analytics_run_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reject_analytics_run_mutation_trigger ON analytics_runs")
    op.execute("DROP FUNCTION reject_analytics_run_mutation()")
    op.drop_index("ix_analytics_runs_curriculum_created", table_name="analytics_runs")
    op.drop_table("analytics_runs")
