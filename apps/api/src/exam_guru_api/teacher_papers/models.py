from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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
from exam_guru_api.papers.domain import MAX_CANDIDATE_REVISIONS, MAX_CANDIDATE_VERSION
from exam_guru_api.teacher_papers.domain import MAX_SLOT_REGENERATIONS

MAX_TEACHER_INTENT_BYTES = 32_768
MAX_TEACHER_SETTINGS_BYTES = 8_192
MAX_RESOLUTION_SNAPSHOT_BYTES = 262_144
MAX_TEACHER_PAPER_COST_MICROUSD = 1_000_000_000_000
MAX_TEACHER_PAPER_TOKENS = 100_000_000
_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"
MAX_PROGRAMME_POLICY_SNAPSHOT_BYTES = 1_048_576


class AssessmentProgrammePolicyVersionModel(Base):
    __tablename__ = "assessment_programme_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "code",
            "version",
            "medium_id",
            name="uq_assessment_programme_policy_identity",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_assessment_programme_policy_fingerprint",
        ),
        CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_assessment_programme_policy_code",
        ),
        CheckConstraint(
            "version = btrim(version) AND char_length(version) BETWEEN 1 AND 128",
            name="ck_assessment_programme_policy_version",
        ),
        CheckConstraint(
            "title = btrim(title) AND char_length(title) BETWEEN 1 AND 255",
            name="ck_assessment_programme_policy_title",
        ),
        CheckConstraint(
            "paper_i_profile_version = btrim(paper_i_profile_version) "
            "AND char_length(paper_i_profile_version) BETWEEN 1 AND 128 "
            "AND paper_ii_profile_version = btrim(paper_ii_profile_version) "
            "AND char_length(paper_ii_profile_version) BETWEEN 1 AND 128",
            name="ck_assessment_programme_policy_profiles",
        ),
        CheckConstraint(
            "paper_i_weight BETWEEN 1 AND 100 AND paper_ii_weight BETWEEN 1 AND 100",
            name="ck_assessment_programme_policy_weights",
        ),
        CheckConstraint(
            "(state = 'draft' AND lock_version = 0 AND review_snapshot IS NULL "
            "AND content_hash IS NULL AND reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(state = 'reviewed' AND lock_version = 1 "
            "AND jsonb_typeof(review_snapshot) = 'object' "
            f"AND pg_column_size(review_snapshot) <= {MAX_PROGRAMME_POLICY_SNAPSHOT_BYTES} "
            "AND content_hash ~ '^[0-9a-f]{64}$' AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL) OR "
            "(state = 'retired' AND lock_version = 2 "
            "AND jsonb_typeof(review_snapshot) = 'object' "
            f"AND pg_column_size(review_snapshot) <= {MAX_PROGRAMME_POLICY_SNAPSHOT_BYTES} "
            "AND content_hash ~ '^[0-9a-f]{64}$' AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL)",
            name="ck_assessment_programme_policy_state",
        ),
        Index(
            "uq_assessment_programme_policy_active",
            "code",
            "medium_id",
            unique=True,
            postgresql_where=text("state = 'reviewed'"),
        ),
        Index(
            "ix_assessment_programme_policy_lookup",
            "programme_exam_configuration_id",
            "medium_id",
            "state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    programme_exam_configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("exam_configurations.id", ondelete="RESTRICT"), nullable=False
    )
    medium_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", ondelete="RESTRICT"), nullable=False
    )
    anchor_curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    paper_i_profile_version: Mapped[str] = mapped_column(String(128), nullable=False)
    paper_ii_profile_version: Mapped[str] = mapped_column(String(128), nullable=False)
    paper_i_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_ii_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    review_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssessmentProgrammePolicyScopeModel(Base):
    __tablename__ = "assessment_programme_policy_scopes"
    __table_args__ = (
        UniqueConstraint(
            "policy_version_id",
            "part",
            "ordinal",
            name="uq_assessment_programme_policy_scope_ordinal",
        ),
        ForeignKeyConstraint(
            ["anchor_unit_id", "anchor_curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_programme_scope_anchor_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["anchor_lesson_id", "anchor_unit_id", "anchor_curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_programme_scope_anchor_lesson",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["anchor_competency_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_competency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["anchor_skill_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_skill",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["anchor_sub_skill_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_sub_skill",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["anchor_learning_concept_id", "anchor_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_anchor_learning_concept",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_unit_id", "source_curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_programme_scope_source_unit",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_lesson_id", "source_unit_id", "source_curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_programme_scope_source_lesson",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_competency_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_competency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_skill_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_skill",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_sub_skill_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_sub_skill",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_learning_concept_id", "source_curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_programme_scope_source_learning_concept",
            ondelete="RESTRICT",
        ),
        CheckConstraint("part IN ('paper_i', 'paper_ii')", name="ck_programme_scope_part"),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 64",
            name="ck_programme_scope_ordinal",
        ),
        CheckConstraint(
            "source_grade BETWEEN 1 AND 13",
            name="ck_programme_scope_grade",
        ),
        CheckConstraint(
            "(anchor_skill_id IS NULL AND anchor_sub_skill_id IS NULL "
            "AND anchor_learning_concept_id IS NULL) OR "
            "(anchor_skill_id IS NOT NULL AND anchor_sub_skill_id IS NULL "
            "AND anchor_learning_concept_id IS NULL) OR "
            "(anchor_skill_id IS NOT NULL AND anchor_sub_skill_id IS NOT NULL) "
            "AND (anchor_learning_concept_id IS NULL OR anchor_sub_skill_id IS NOT NULL)",
            name="ck_programme_scope_anchor_taxonomy_shape",
        ),
        CheckConstraint(
            "(source_skill_id IS NULL AND source_sub_skill_id IS NULL "
            "AND source_learning_concept_id IS NULL) OR "
            "(source_skill_id IS NOT NULL AND source_sub_skill_id IS NULL "
            "AND source_learning_concept_id IS NULL) OR "
            "(source_skill_id IS NOT NULL AND source_sub_skill_id IS NOT NULL) "
            "AND (source_learning_concept_id IS NULL OR source_sub_skill_id IS NOT NULL)",
            name="ck_programme_scope_source_taxonomy_shape",
        ),
        CheckConstraint(
            "(source_unit_id IS NULL AND source_lesson_id IS NULL) OR source_unit_id IS NOT NULL",
            name="ck_programme_scope_source_learning_shape",
        ),
        Index(
            "ix_assessment_programme_policy_scope_source",
            "policy_version_id",
            "part",
            "source_curriculum_version_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    policy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_programme_policy_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    part: Mapped[str] = mapped_column(String(16), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    anchor_unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    anchor_lesson_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    anchor_competency_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    anchor_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    anchor_sub_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    anchor_learning_concept_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_grade: Mapped[int] = mapped_column(Integer, nullable=False)
    source_exam_configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("exam_configurations.id", ondelete="RESTRICT"), nullable=False
    )
    source_medium_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", ondelete="RESTRICT"), nullable=False
    )
    source_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False
    )
    source_curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    source_unit_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_lesson_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_competency_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_sub_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_learning_concept_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeacherPaperJobModel(Base):
    __tablename__ = "teacher_paper_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_teacher_paper_jobs_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["practice_paper_id", "curriculum_version_id"],
            ["practice_papers.id", "practice_papers.curriculum_version_id"],
            name="fk_teacher_paper_jobs_practice_paper_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("paper_reference", name="uq_teacher_paper_jobs_reference"),
        UniqueConstraint(
            "created_by",
            "idempotency_key_hash",
            name="uq_teacher_paper_jobs_actor_idempotency",
        ),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_teacher_paper_jobs_id_curriculum",
        ),
        CheckConstraint(
            f"request_fingerprint ~ '{_FINGERPRINT_SQL}' AND "
            f"idempotency_key_hash ~ '{_FINGERPRINT_SQL}'",
            name="ck_teacher_paper_jobs_fingerprints",
        ),
        CheckConstraint(
            "paper_reference ~ '^EGP-[0-9A-F]{4}-[0-9A-F]{8}$'",
            name="ck_teacher_paper_jobs_reference",
        ),
        CheckConstraint(
            "title = btrim(title) AND char_length(title) BETWEEN 1 AND 512",
            name="ck_teacher_paper_jobs_title",
        ),
        CheckConstraint(
            "status IN ('preparing', 'generating', 'checking_answers', "
            "'ready_for_review', 'failed') AND version >= 0",
            name="ck_teacher_paper_jobs_status_version",
        ),
        CheckConstraint(
            "slot_count BETWEEN 1 AND 50 AND generated_count BETWEEN 0 AND slot_count AND "
            "validated_count BETWEEN 0 AND slot_count AND candidate_count BETWEEN 0 AND slot_count "
            "AND approved_count BETWEEN 0 AND slot_count AND failed_count BETWEEN 0 AND slot_count",
            name="ck_teacher_paper_jobs_counts",
        ),
        CheckConstraint(
            f"total_tokens BETWEEN 0 AND {MAX_TEACHER_PAPER_TOKENS} AND "
            f"cost_microusd BETWEEN 0 AND {MAX_TEACHER_PAPER_COST_MICROUSD} AND "
            f"max_cost_microusd BETWEEN 1 AND {MAX_TEACHER_PAPER_COST_MICROUSD} AND "
            "cost_microusd <= max_cost_microusd",
            name="ck_teacher_paper_jobs_cost",
        ),
        CheckConstraint(
            "jsonb_typeof(teacher_intent) = 'object' AND "
            f"pg_column_size(teacher_intent) <= {MAX_TEACHER_INTENT_BYTES}",
            name="ck_teacher_paper_jobs_intent",
        ),
        CheckConstraint(
            "jsonb_typeof(paper_settings) = 'object' AND "
            f"pg_column_size(paper_settings) <= {MAX_TEACHER_SETTINGS_BYTES}",
            name="ck_teacher_paper_jobs_settings",
        ),
        CheckConstraint(
            "jsonb_typeof(resolution_snapshot) = 'object' AND "
            f"pg_column_size(resolution_snapshot) <= {MAX_RESOLUTION_SNAPSHOT_BYTES}",
            name="ck_teacher_paper_jobs_resolution",
        ),
        CheckConstraint(
            "(actor_token IS NULL AND actor_lease_expires_at IS NULL) OR "
            "(actor_token IS NOT NULL AND actor_lease_expires_at IS NOT NULL)",
            name="ck_teacher_paper_jobs_actor_lease",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "char_length(failure_code) BETWEEN 1 AND 128)",
            name="ck_teacher_paper_jobs_failure_code",
        ),
        CheckConstraint(
            "failure_detail IS NULL OR (failure_detail = btrim(failure_detail) AND "
            "char_length(failure_detail) BETWEEN 1 AND 1024)",
            name="ck_teacher_paper_jobs_failure_detail",
        ),
        CheckConstraint(
            "dispatch_message_id IS NULL OR (dispatch_message_id = btrim(dispatch_message_id) "
            "AND char_length(dispatch_message_id) BETWEEN 1 AND 128)",
            name="ck_teacher_paper_jobs_dispatch_message",
        ),
        Index("ix_teacher_paper_jobs_status_updated", "status", "updated_at", "id"),
        Index("ix_teacher_paper_jobs_created_by_created", "created_by", "created_at", "id"),
        Index(
            "ix_teacher_paper_jobs_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    paper_reference: Mapped[str] = mapped_column(String(17), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    exam_configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("exam_configurations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    medium_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    teacher_intent: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    paper_settings: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    resolution_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    paper_blueprint_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    practice_paper_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    slot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    validated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    approved_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cost_microusd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    max_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    actor_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    actor_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatch_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeacherPaperSlotModel(Base):
    __tablename__ = "teacher_paper_slots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["paper_job_id", "curriculum_version_id"],
            ["teacher_paper_jobs.id", "teacher_paper_jobs.curriculum_version_id"],
            name="fk_teacher_paper_slots_job_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_teacher_paper_slots_unit_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_teacher_paper_slots_lesson_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["competency_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_teacher_paper_slots_competency_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_teacher_paper_slots_skill_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sub_skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_teacher_paper_slots_sub_skill_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["learning_concept_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_teacher_paper_slots_concept_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_teacher_paper_slots_generation_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_validation_run_id", "curriculum_version_id"],
            ["validation_runs.id", "validation_runs.curriculum_version_id"],
            name="fk_teacher_paper_slots_validation_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_teacher_paper_slots_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "paper_job_id", name="uq_teacher_paper_slots_id_job"),
        UniqueConstraint("paper_job_id", "ordinal", name="uq_teacher_paper_slots_job_ordinal"),
        UniqueConstraint(
            "paper_job_id",
            "blueprint_slot_id",
            name="uq_teacher_paper_slots_job_blueprint_slot",
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 50", name="ck_teacher_paper_slots_ordinal"),
        CheckConstraint(
            "lesson_number BETWEEN 1 AND 10000",
            name="ck_teacher_paper_slots_lesson_number",
        ),
        CheckConstraint(
            "blueprint_slot_id = btrim(blueprint_slot_id) AND "
            "char_length(blueprint_slot_id) BETWEEN 1 AND 128",
            name="ck_teacher_paper_slots_blueprint_slot",
        ),
        CheckConstraint(
            "status IN ('generating', 'checking_answers', 'awaiting_review', 'in_review', "
            "'approved', 'rejected', 'revalidation_required', 'failed') AND version >= 0",
            name="ck_teacher_paper_slots_status_version",
        ),
        CheckConstraint(
            f"regeneration_count BETWEEN 0 AND {MAX_SLOT_REGENERATIONS}",
            name="ck_teacher_paper_slots_regeneration_count",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) AND "
            "char_length(failure_code) BETWEEN 1 AND 128)",
            name="ck_teacher_paper_slots_failure_code",
        ),
        Index("ix_teacher_paper_slots_job_ordinal", "paper_job_id", "ordinal"),
        Index("ix_teacher_paper_slots_current_generation", "current_generation_run_id"),
        Index("ix_teacher_paper_slots_current_candidate", "current_candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    paper_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    blueprint_slot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    lesson_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    lesson_number: Mapped[int] = mapped_column(Integer, nullable=False)
    competency_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sub_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    learning_concept_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    current_generation_run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    current_validation_run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    current_candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    regeneration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    requires_revalidation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeacherPaperMarkingConfirmationModel(Base):
    __tablename__ = "teacher_paper_marking_confirmations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id", "paper_job_id"],
            ["teacher_paper_slots.id", "teacher_paper_slots.paper_job_id"],
            name="fk_teacher_paper_marking_confirmations_slot_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_teacher_paper_marking_confirmations_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "slot_id",
            "candidate_id",
            "candidate_revision",
            "marking_fingerprint",
            name="uq_teacher_paper_marking_confirmation_content",
        ),
        CheckConstraint(
            f"candidate_revision BETWEEN 1 AND {MAX_CANDIDATE_REVISIONS} AND "
            f"review_candidate_version BETWEEN 3 AND {MAX_CANDIDATE_VERSION}",
            name="ck_teacher_paper_marking_confirmation_revision",
        ),
        CheckConstraint(
            "marking_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_teacher_paper_marking_confirmation_fingerprint",
        ),
        CheckConstraint(
            "total_marks BETWEEN 1 AND 100 AND criteria_count BETWEEN 1 AND 64",
            name="ck_teacher_paper_marking_confirmation_summary",
        ),
        Index(
            "ix_teacher_paper_marking_confirmations_slot",
            "slot_id",
            "candidate_id",
            "candidate_revision",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    paper_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    slot_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    review_candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    marking_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    total_marks: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TeacherPaperSlotRunModel(Base):
    __tablename__ = "teacher_paper_slot_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["slot_id", "paper_job_id"],
            ["teacher_paper_slots.id", "teacher_paper_slots.paper_job_id"],
            name="fk_teacher_paper_slot_runs_slot_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_teacher_paper_slot_runs_generation_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("generation_run_id", name="uq_teacher_paper_slot_runs_generation"),
        UniqueConstraint(
            "slot_id",
            "sequence",
            name="uq_teacher_paper_slot_runs_slot_sequence",
        ),
        CheckConstraint("slot_ordinal BETWEEN 1 AND 50", name="ck_teacher_paper_slot_runs_ordinal"),
        CheckConstraint("sequence BETWEEN 1 AND 3", name="ck_teacher_paper_slot_runs_sequence"),
        CheckConstraint(
            "reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND 1024",
            name="ck_teacher_paper_slot_runs_reason",
        ),
        Index("ix_teacher_paper_slot_runs_job_slot", "paper_job_id", "slot_ordinal", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    paper_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("teacher_paper_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    slot_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    slot_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    generation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
