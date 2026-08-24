from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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

MAX_SPECIFICATION_SNAPSHOT_BYTES = 524_288
MAX_BLUEPRINT_SNAPSHOT_BYTES = 2_097_152
MAX_TAXONOMY_SNAPSHOT_BYTES = 524_288
MAX_BLUEPRINT_SLOTS = 200
MAX_TAXONOMY_SNAPSHOT_NODES = 800
MAX_BLUEPRINT_TOTAL_MARKS = 100_000
_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


class PaperBlueprintModel(Base):
    __tablename__ = "paper_blueprints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analytics_run_id", "curriculum_version_id"],
            ["analytics_runs.id", "analytics_runs.curriculum_version_id"],
            name="fk_paper_blueprints_analytics_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("blueprint_id", name="uq_paper_blueprints_blueprint_id"),
        UniqueConstraint(
            "input_fingerprint",
            name="uq_paper_blueprints_input_fingerprint",
        ),
        CheckConstraint(
            "blueprint_id ~ '^bp_[0-9a-f]{24}$'",
            name="ck_paper_blueprints_blueprint_id",
        ),
        *(
            CheckConstraint(
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
            CheckConstraint(
                f"{column_name} = btrim({column_name}) AND length({column_name}) > 0",
                name=f"ck_paper_blueprints_{column_name}",
            )
            for column_name in ("schema_version", "algorithm_version", "config_version")
        ),
        CheckConstraint(
            f"total_marks BETWEEN 1 AND {MAX_BLUEPRINT_TOTAL_MARKS}",
            name="ck_paper_blueprints_total_marks",
        ),
        CheckConstraint(
            f"slot_count BETWEEN 1 AND {MAX_BLUEPRINT_SLOTS}",
            name="ck_paper_blueprints_slot_count",
        ),
        CheckConstraint(
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
        CheckConstraint(
            f"pg_column_size(specification) <= {MAX_SPECIFICATION_SNAPSHOT_BYTES}",
            name="ck_paper_blueprints_specification_size",
        ),
        CheckConstraint(
            "specification->'total_marks' = to_jsonb(total_marks) AND "
            "specification->'config_version' = to_jsonb(config_version) AND "
            "specification->'curriculum_scope'->'curriculum_version_id' = "
            "to_jsonb(curriculum_version_id::text)",
            name="ck_paper_blueprints_specification_metadata",
        ),
        CheckConstraint(
            "jsonb_typeof(blueprint) = 'object' AND "
            "blueprint ?& ARRAY['version', 'paper_code', 'title', 'seed', 'total_marks', "
            "'curriculum_scope', 'sections', 'question_type_allocations', "
            "'difficulty_allocations', 'taxonomy_requirements', 'slots'] AND "
            "jsonb_typeof(blueprint->'version') = 'object' AND "
            "jsonb_typeof(blueprint->'slots') = 'array' AND "
            "jsonb_array_length(blueprint->'slots') = slot_count",
            name="ck_paper_blueprints_blueprint_shape",
        ),
        CheckConstraint(
            f"pg_column_size(blueprint) <= {MAX_BLUEPRINT_SNAPSHOT_BYTES}",
            name="ck_paper_blueprints_blueprint_size",
        ),
        CheckConstraint(
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
        CheckConstraint(
            f"pg_column_size(taxonomy_snapshot) <= {MAX_TAXONOMY_SNAPSHOT_BYTES} AND "
            "paper_blueprint_taxonomy_snapshot_valid("
            "taxonomy_snapshot, curriculum_version_id)",
            name="ck_paper_blueprints_taxonomy_snapshot",
        ),
        Index(
            "ix_paper_blueprints_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
        Index("ix_paper_blueprints_analytics_run", "analytics_run_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "curriculum_versions.id",
            name="fk_paper_blueprints_curriculum_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    analytics_run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    blueprint_id: Mapped[str] = mapped_column(String(27), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_marks: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    specification_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    result_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    specification: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    blueprint: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    taxonomy_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
