from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base

_MAX_CONTENT_BYTES = 131_072
_MAX_FINDINGS_BYTES = 524_288
_MAX_SCOPE_BYTES = 32_768
_MAX_PROVENANCE_BYTES = 131_072
_MAX_REPLAY_INPUT_BYTES = 8_388_608
_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


class SubjectQualityFeedbackModel(Base):
    __tablename__ = "subject_quality_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["teacher_paper_job_id", "curriculum_version_id"],
            ["teacher_paper_jobs.id", "teacher_paper_jobs.curriculum_version_id"],
            name="fk_subject_quality_feedback_job_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["teacher_paper_slot_id", "teacher_paper_job_id"],
            ["teacher_paper_slots.id", "teacher_paper_slots.paper_job_id"],
            name="fk_subject_quality_feedback_slot_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_subject_quality_feedback_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "candidate_revision"],
            ["question_candidate_revisions.candidate_id", "question_candidate_revisions.revision"],
            name="fk_subject_quality_feedback_candidate_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "review_event_version"],
            ["candidate_review_events.candidate_id", "candidate_review_events.candidate_version"],
            name="fk_subject_quality_feedback_review_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_subject_quality_feedback_generation_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["generation_attempt_id", "generation_run_id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_subject_quality_feedback_generation_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "validation_run_id",
                "generation_run_id",
                "generation_attempt_id",
                "curriculum_version_id",
            ],
            [
                "validation_runs.id",
                "validation_runs.generation_run_id",
                "validation_runs.generation_attempt_id",
                "validation_runs.curriculum_version_id",
            ],
            name="fk_subject_quality_feedback_validation_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["replacement_generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_subject_quality_feedback_replacement_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_subject_quality_feedback_unit_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_subject_quality_feedback_lesson_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("action_fingerprint", name="uq_subject_quality_feedback_action"),
        UniqueConstraint("feedback_fingerprint", name="uq_subject_quality_feedback_fingerprint"),
        CheckConstraint(
            "schema_version = 'subject-quality-feedback.v1'",
            name="ck_subject_quality_feedback_schema",
        ),
        CheckConstraint(
            "action IN ('edit', 'reject', 'regenerate', 'approve')",
            name="ck_subject_quality_feedback_action",
        ),
        CheckConstraint(
            "reason_code IN ('answer_incorrect', 'ambiguous_wording', 'outside_scope', "
            "'source_not_supported', 'marking_inconsistent', 'language_quality', "
            "'distractor_quality', 'duplicate_content', 'unsafe_content', "
            "'other_quality_issue', 'confirmed_quality')",
            name="ck_subject_quality_feedback_reason_code",
        ),
        CheckConstraint(
            "(action = 'approve' AND reason_code = 'confirmed_quality' AND note IS NOT NULL) OR "
            "(action <> 'approve' AND reason_code <> 'confirmed_quality')",
            name="ck_subject_quality_feedback_approval_meaningful",
        ),
        CheckConstraint(
            "note IS NULL OR (note = btrim(note) AND char_length(note) BETWEEN 1 AND 768)",
            name="ck_subject_quality_feedback_note",
        ),
        CheckConstraint(
            "grade BETWEEN 1 AND 13 AND lesson_number BETWEEN 1 AND 10000 AND "
            "candidate_revision BETWEEN 1 AND 32 AND candidate_version BETWEEN 2 AND 35 "
            "AND slot_version BETWEEN 0 AND 100000",
            name="ck_subject_quality_feedback_bounds",
        ),
        CheckConstraint(
            "medium_code = btrim(medium_code) AND char_length(medium_code) BETWEEN 1 AND 32 AND "
            "subject_code = btrim(subject_code) AND char_length(subject_code) BETWEEN 1 AND 64",
            name="ck_subject_quality_feedback_codes",
        ),
        *(
            CheckConstraint(
                f"{column_name} = btrim({column_name}) AND "
                f"char_length({column_name}) BETWEEN 1 AND 128",
                name=f"ck_subject_quality_feedback_{column_name}",
            )
            for column_name in (
                "prompt_version",
                "provider",
                "provider_version",
                "model",
                "model_version",
                "retrieval_version",
            )
        ),
        *(
            CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_subject_quality_feedback_{column_name}",
            )
            for column_name in (
                "original_content_fingerprint",
                "current_content_fingerprint",
                "findings_fingerprint",
                "scope_fingerprint",
                "provenance_fingerprint",
                "feedback_fingerprint",
                "action_fingerprint",
            )
        ),
        CheckConstraint(
            f"idempotency_key_hash IS NULL OR idempotency_key_hash ~ '{_FINGERPRINT_SQL}'",
            name="ck_subject_quality_feedback_idempotency",
        ),
        CheckConstraint(
            "review_candidate_content_valid(original_content_snapshot) AND "
            f"pg_column_size(original_content_snapshot) <= {_MAX_CONTENT_BYTES} AND "
            "review_candidate_content_valid(current_content_snapshot) AND "
            f"pg_column_size(current_content_snapshot) <= {_MAX_CONTENT_BYTES}",
            name="ck_subject_quality_feedback_content",
        ),
        CheckConstraint(
            "jsonb_typeof(findings_snapshot) = 'object' AND "
            "jsonb_typeof(findings_snapshot->'findings') = 'array' AND "
            f"pg_column_size(findings_snapshot) <= {_MAX_FINDINGS_BYTES}",
            name="ck_subject_quality_feedback_findings",
        ),
        CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'object' AND "
            f"pg_column_size(scope_snapshot) <= {_MAX_SCOPE_BYTES}",
            name="ck_subject_quality_feedback_scope",
        ),
        CheckConstraint(
            "jsonb_typeof(provenance_snapshot) = 'array' AND "
            f"pg_column_size(provenance_snapshot) <= {_MAX_PROVENANCE_BYTES}",
            name="ck_subject_quality_feedback_provenance",
        ),
        CheckConstraint(
            "jsonb_typeof(replay_input_snapshot) = 'object' AND "
            "replay_input_snapshot->>'schema_version' = 'subject-quality-eval-input.v1' AND "
            f"pg_column_size(replay_input_snapshot) <= {_MAX_REPLAY_INPUT_BYTES}",
            name="ck_subject_quality_feedback_replay_input",
        ),
        CheckConstraint(
            "jsonb_typeof(validator_versions) = 'array' AND "
            "jsonb_array_length(validator_versions) BETWEEN 1 AND 32",
            name="ck_subject_quality_feedback_validator_versions",
        ),
        Index("ix_subject_quality_feedback_created", "created_at", "id"),
        Index("ix_subject_quality_feedback_candidate_created", "candidate_id", "created_at", "id"),
        Index(
            "ix_subject_quality_feedback_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_paper_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    teacher_paper_slot_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    slot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    medium_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", name="fk_subject_quality_feedback_medium", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", name="fk_subject_quality_feedback_subject", ondelete="RESTRICT"),
        nullable=False,
    )
    unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    lesson_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    grade: Mapped[int] = mapped_column(Integer, nullable=False)
    medium_code: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False)
    lesson_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    review_event_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    generation_attempt_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    validation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    replacement_generation_run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(768), nullable=True)
    original_content_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    current_content_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    findings_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    scope_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    provenance_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    replay_input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(128), nullable=False)
    validator_versions: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    original_content_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    current_content_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    findings_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    provenance_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    feedback_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubjectQualityEvalCaseVersionModel(Base):
    __tablename__ = "subject_quality_eval_case_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["eval_case_id", "previous_version"],
            [
                "subject_quality_eval_case_versions.eval_case_id",
                "subject_quality_eval_case_versions.version",
            ],
            name="fk_subject_quality_eval_case_previous",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "eval_case_id",
            "version",
            "source_feedback_id",
            name="uq_subject_quality_eval_case_source_version",
        ),
        CheckConstraint("version IN (1, 2)", name="ck_subject_quality_eval_case_version"),
        CheckConstraint(
            "state IN ('draft', 'approved') AND expected_status IN ('pass', 'warn', 'fail')",
            name="ck_subject_quality_eval_case_states",
        ),
        CheckConstraint(
            "subject_quality_code_array_valid(expected_finding_codes) AND "
            "((expected_status = 'pass' AND jsonb_array_length(expected_finding_codes) = 0) OR "
            "(expected_status <> 'pass' AND jsonb_array_length(expected_finding_codes) >= 1))",
            name="ck_subject_quality_eval_case_expected_codes",
        ),
        CheckConstraint(
            "defect_category IN ('no_defect', 'answer_correctness', "
            "'multiple_correct_answers', 'marking_consistency', 'scope_alignment', "
            "'source_grounding', 'language_clarity', 'distractor_quality', "
            "'duplicate_content', 'security_residue', 'other')",
            name="ck_subject_quality_eval_case_defect_category",
        ),
        CheckConstraint(
            "jsonb_typeof(replay_input_snapshot) = 'object' AND "
            "replay_input_snapshot->>'schema_version' = 'subject-quality-eval-input.v1' AND "
            f"pg_column_size(replay_input_snapshot) <= {_MAX_REPLAY_INPUT_BYTES}",
            name="ck_subject_quality_eval_case_replay_input",
        ),
        CheckConstraint(
            "jsonb_typeof(subject_scope_snapshot) = 'object' AND "
            f"pg_column_size(subject_scope_snapshot) <= {_MAX_SCOPE_BYTES}",
            name="ck_subject_quality_eval_case_scope",
        ),
        *(
            CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_subject_quality_eval_case_{column_name}",
            )
            for column_name in (
                "case_fingerprint",
                "idempotency_key_hash",
                "promotion_request_fingerprint",
            )
        ),
        CheckConstraint(
            "(version = 1 AND previous_version IS NULL AND state = 'draft' AND "
            "approved_by IS NULL AND approved_at IS NULL) OR "
            "(version = 2 AND previous_version = 1 AND state = 'approved' AND "
            "approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by <> promoted_by)",
            name="ck_subject_quality_eval_case_lifecycle",
        ),
        Index(
            "uq_subject_quality_eval_case_feedback_promotion",
            "source_feedback_id",
            unique=True,
            postgresql_where=text("version = 1"),
        ),
        Index(
            "uq_subject_quality_eval_case_actor_idempotency",
            "promoted_by",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("version = 1"),
        ),
        Index(
            "ix_subject_quality_eval_case_state_created",
            "state",
            "created_at",
            "eval_case_id",
        ),
    )

    eval_case_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_feedback_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "subject_quality_feedback.id",
            name="fk_subject_quality_eval_case_feedback",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_status: Mapped[str] = mapped_column(String(8), nullable=False)
    expected_finding_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    defect_category: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    subject_scope_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    case_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    promotion_request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    promoted_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubjectQualityEvalRunModel(Base):
    __tablename__ = "subject_quality_eval_runs"
    __table_args__ = (
        UniqueConstraint("request_fingerprint", name="uq_subject_quality_eval_runs_request"),
        CheckConstraint(
            "runner_version = 'subject-quality-eval-runner.v1'",
            name="ck_subject_quality_eval_runs_runner",
        ),
        CheckConstraint(
            f"pipeline_fingerprint ~ '{_FINGERPRINT_SQL}' AND "
            f"request_fingerprint ~ '{_FINGERPRINT_SQL}'",
            name="ck_subject_quality_eval_runs_fingerprints",
        ),
        CheckConstraint(
            "pipeline_version = btrim(pipeline_version) AND "
            "char_length(pipeline_version) BETWEEN 1 AND 128",
            name="ck_subject_quality_eval_runs_pipeline",
        ),
        CheckConstraint(
            "case_count BETWEEN 1 AND 100 AND passed_count BETWEEN 0 AND case_count AND "
            "regression_count BETWEEN 0 AND case_count AND "
            "unavailable_count BETWEEN 0 AND case_count AND "
            "passed_count + regression_count + unavailable_count = case_count",
            name="ck_subject_quality_eval_runs_counts",
        ),
        Index("ix_subject_quality_eval_runs_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    runner_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    regression_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unavailable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubjectQualityEvalResultModel(Base):
    __tablename__ = "subject_quality_eval_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["eval_case_id", "eval_case_version"],
            [
                "subject_quality_eval_case_versions.eval_case_id",
                "subject_quality_eval_case_versions.version",
            ],
            name="fk_subject_quality_eval_results_case",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "eval_run_id", "eval_case_id", name="uq_subject_quality_eval_results_run_case"
        ),
        UniqueConstraint("result_fingerprint", name="uq_subject_quality_eval_results_fingerprint"),
        CheckConstraint(
            "eval_case_version = 2 AND expected_status IN ('pass', 'warn', 'fail') AND "
            "actual_status IN ('pass', 'warn', 'fail') AND "
            "outcome IN ('pass', 'regression', 'unavailable')",
            name="ck_subject_quality_eval_results_states",
        ),
        CheckConstraint(
            "passed = (outcome = 'pass')",
            name="ck_subject_quality_eval_results_passed",
        ),
        CheckConstraint(
            "subject_quality_code_array_valid(expected_finding_codes) AND "
            "subject_quality_code_array_valid(actual_finding_codes)",
            name="ck_subject_quality_eval_results_codes",
        ),
        *(
            CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_subject_quality_eval_results_{column_name}",
            )
            for column_name in (
                "pipeline_fingerprint",
                "report_fingerprint",
                "result_fingerprint",
            )
        ),
        CheckConstraint(
            "pipeline_version = btrim(pipeline_version) AND "
            "char_length(pipeline_version) BETWEEN 1 AND 128 AND "
            "jsonb_typeof(validator_versions) = 'array' AND "
            "jsonb_array_length(validator_versions) BETWEEN 1 AND 32",
            name="ck_subject_quality_eval_results_lineage",
        ),
        CheckConstraint(
            "jsonb_typeof(report_snapshot) = 'object' AND "
            "pg_column_size(report_snapshot) <= 524288",
            name="ck_subject_quality_eval_results_report",
        ),
        Index("ix_subject_quality_eval_results_run_case", "eval_run_id", "eval_case_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    eval_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "subject_quality_eval_runs.id",
            name="fk_subject_quality_eval_results_run",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    eval_case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    eval_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_status: Mapped[str] = mapped_column(String(8), nullable=False)
    expected_finding_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    actual_status: Mapped[str] = mapped_column(String(8), nullable=False)
    actual_finding_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    validator_versions: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    report_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    result_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    report_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
