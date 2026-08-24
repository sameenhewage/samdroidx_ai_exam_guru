from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base

_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


class AnalyticsRunModel(Base):
    __tablename__ = "analytics_runs"
    __table_args__ = (
        UniqueConstraint(
            "run_fingerprint",
            name="uq_analytics_runs_run_fingerprint",
        ),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_analytics_runs_id_curriculum",
        ),
        *(
            CheckConstraint(
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
            CheckConstraint(
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
        CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_analytics_runs_config_object",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_analytics_runs_input_snapshot_object",
        ),
        CheckConstraint(
            "jsonb_typeof(source_versions) = 'array'",
            name="ck_analytics_runs_source_versions_array",
        ),
        CheckConstraint(
            "jsonb_typeof(data_quality) = 'object'",
            name="ck_analytics_runs_data_quality_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result) = 'object'",
            name="ck_analytics_runs_result_object",
        ),
        CheckConstraint(
            "compute_duration_ms >= 0",
            name="ck_analytics_runs_compute_duration_ms",
        ),
        Index(
            "ix_analytics_runs_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "curriculum_versions.id",
            name="fk_analytics_runs_curriculum_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    run_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    result_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    statistics_algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    practice_priority_algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    backtest_algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_versions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    data_quality: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    compute_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
