from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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

MAX_VALIDATION_FINDINGS = 256
MAX_VALIDATION_VALIDATORS = 32
MAX_VALIDATION_EVIDENCE_PER_FINDING = 64
MAX_VALIDATION_GROUNDING_SOURCES = 16
MAX_VALIDATION_DUPLICATE_REFERENCES = 256
MAX_VALIDATION_INPUT_SNAPSHOT_BYTES = 8_388_608
MAX_VALIDATION_EVIDENCE_BYTES = 196_608
_FINGERPRINT_SQL = "^[0-9a-f]{64}$"


class ValidationRunModel(Base):
    __tablename__ = "validation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_validation_runs_generation_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["generation_attempt_id", "generation_run_id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_validation_runs_generation_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "generation_run_id",
            "pipeline_version",
            name="uq_validation_runs_generation_pipeline",
        ),
        UniqueConstraint(
            "input_fingerprint",
            "pipeline_version",
            name="uq_validation_runs_input_pipeline",
        ),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_validation_runs_id_curriculum",
        ),
        UniqueConstraint(
            "id",
            "generation_run_id",
            "generation_attempt_id",
            "curriculum_version_id",
            name="uq_validation_runs_candidate_lineage",
        ),
        *(
            CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_validation_runs_{column_name}",
            )
            for column_name in (
                "pipeline_fingerprint",
                "generation_result_fingerprint",
                "input_fingerprint",
                "candidate_fingerprint",
                "report_fingerprint",
            )
        ),
        *(
            CheckConstraint(
                f"{column_name} = btrim({column_name}) AND length({column_name}) BETWEEN 1 AND 128",
                name=f"ck_validation_runs_{column_name}",
            )
            for column_name in (
                "pipeline_version",
                "input_schema_version",
                "report_schema_version",
            )
        ),
        CheckConstraint(
            "overall_status IN ('pass', 'warn', 'fail')",
            name="ck_validation_runs_overall_status",
        ),
        CheckConstraint(
            f"finding_count BETWEEN 1 AND {MAX_VALIDATION_FINDINGS} AND "
            f"validator_count BETWEEN 1 AND {MAX_VALIDATION_VALIDATORS} AND "
            "validator_count <= finding_count AND "
            f"grounding_source_count BETWEEN 1 AND {MAX_VALIDATION_GROUNDING_SOURCES} AND "
            f"duplicate_reference_count BETWEEN 0 AND {MAX_VALIDATION_DUPLICATE_REFERENCES}",
            name="ck_validation_runs_counts",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND "
            "input_snapshot ?& ARRAY['schema_version', 'trust', 'generation', 'candidate', "
            "'candidate_fingerprint', 'input_fingerprint', 'blueprint', 'grounding_sources', "
            "'duplicate_references'] AND "
            "input_snapshot->>'trust' = 'server_reconstructed' AND "
            "input_snapshot->>'schema_version' = input_schema_version AND "
            "input_snapshot->>'candidate_fingerprint' = candidate_fingerprint AND "
            "input_snapshot->>'input_fingerprint' = input_fingerprint AND "
            "jsonb_typeof(input_snapshot->'generation') = 'object' AND "
            "input_snapshot->'generation' ?& ARRAY['generation_run_id', "
            "'generation_attempt_id', 'generation_result_fingerprint'] AND "
            "input_snapshot->'generation'->>'generation_run_id' = generation_run_id::text AND "
            "input_snapshot->'generation'->>'generation_attempt_id' = "
            "generation_attempt_id::text AND "
            "input_snapshot->'generation'->>'generation_result_fingerprint' = "
            "generation_result_fingerprint AND "
            "(input_schema_version <> 'question-validation-input.v3' OR ("
            "input_snapshot ?& ARRAY['subject_scope', 'generated_scope', "
            "'context_scope_bindings'] AND "
            "jsonb_typeof(input_snapshot->'subject_scope') = 'object' AND "
            "input_snapshot->'subject_scope' ?& ARRAY['trust', 'grade', 'medium', 'subject_id', "
            "'subject_code', 'curriculum_version_id', 'unit_ids', 'lesson_ids'] AND "
            "input_snapshot->'subject_scope'->>'trust' = 'server_owned' AND "
            "input_snapshot->'subject_scope'->>'curriculum_version_id' = "
            "curriculum_version_id::text AND "
            "jsonb_typeof(input_snapshot->'generated_scope') = 'object' AND "
            "jsonb_typeof(input_snapshot->'context_scope_bindings') = 'array')) AND "
            "jsonb_typeof(input_snapshot->'candidate') = 'object' AND "
            "jsonb_typeof(input_snapshot->'blueprint') = 'object' AND "
            "jsonb_typeof(input_snapshot->'grounding_sources') = 'array' AND "
            "jsonb_array_length(input_snapshot->'grounding_sources') = grounding_source_count AND "
            "jsonb_typeof(input_snapshot->'duplicate_references') = 'array' AND "
            "jsonb_array_length(input_snapshot->'duplicate_references') = "
            "duplicate_reference_count AND "
            f"pg_column_size(input_snapshot) <= {MAX_VALIDATION_INPUT_SNAPSHOT_BYTES}",
            name="ck_validation_runs_input_snapshot",
        ),
        CheckConstraint(
            "validation_lineage_valid(validator_lineage, validator_count)",
            name="ck_validation_runs_validator_lineage",
        ),
        CheckConstraint(
            "validation_text_array_valid(limitations, 1, 16, 2048)",
            name="ck_validation_runs_limitations",
        ),
        Index(
            "ix_validation_runs_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_validation_runs_generation_created",
            "generation_run_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_validation_runs_status_created",
            "overall_status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    generation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    generation_attempt_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    report_schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    generation_result_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    report_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    overall_status: Mapped[str] = mapped_column(String(8), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    validator_lineage: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    validator_count: Mapped[int] = mapped_column(Integer, nullable=False)
    grounding_source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_reference_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ValidationFindingModel(Base):
    __tablename__ = "validation_findings"
    __table_args__ = (
        UniqueConstraint(
            "validation_run_id",
            "ordinal",
            name="uq_validation_findings_run_ordinal",
        ),
        UniqueConstraint(
            "validation_run_id",
            "validator_id",
            "code",
            name="uq_validation_findings_run_validator_code",
        ),
        CheckConstraint(
            f"ordinal BETWEEN 0 AND {MAX_VALIDATION_FINDINGS - 1}",
            name="ck_validation_findings_ordinal",
        ),
        CheckConstraint(
            "validator_id = btrim(validator_id) AND length(validator_id) BETWEEN 1 AND 128 AND "
            "validator_id ~ '^[a-z0-9][a-z0-9-]*$'",
            name="ck_validation_findings_validator_id",
        ),
        CheckConstraint(
            "validator_version = btrim(validator_version) AND "
            "length(validator_version) BETWEEN 1 AND 128",
            name="ck_validation_findings_validator_version",
        ),
        CheckConstraint(
            "code = btrim(code) AND length(code) BETWEEN 1 AND 128 AND "
            "code ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'",
            name="ck_validation_findings_code",
        ),
        CheckConstraint(
            "status IN ('pass', 'warn', 'fail')",
            name="ck_validation_findings_status",
        ),
        CheckConstraint(
            "message = btrim(message) AND char_length(message) BETWEEN 1 AND 1024",
            name="ck_validation_findings_message",
        ),
        CheckConstraint(
            "validation_evidence_valid(evidence, evidence_count) AND "
            f"pg_column_size(evidence) <= {MAX_VALIDATION_EVIDENCE_BYTES}",
            name="ck_validation_findings_evidence",
        ),
        Index(
            "ix_validation_findings_run_ordinal",
            "validation_run_id",
            "ordinal",
        ),
        Index(
            "ix_validation_findings_code_status",
            "code",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    validation_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("validation_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    validator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
