from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base
from exam_guru_api.papers.domain import MAX_CANDIDATE_REVISIONS, MAX_CANDIDATE_VERSION

MAX_CANDIDATE_CONTENT_BYTES = 131_072
MAX_CANDIDATE_LINEAGE_BYTES = 131_072
MAX_CANDIDATE_VALIDATION_EVIDENCE_BYTES = 32_768
MAX_REVIEW_REASON_CHARACTERS = 1_024


class QuestionCandidateModel(Base):
    __tablename__ = "question_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_question_candidates_generation_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["generation_attempt_id", "generation_run_id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_question_candidates_generation_attempt",
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
            name="fk_question_candidates_validation_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_question_candidates_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("generation_run_id", name="uq_question_candidates_generation_run"),
        UniqueConstraint("validation_run_id", name="uq_question_candidates_validation_run"),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_question_candidates_id_curriculum",
        ),
        CheckConstraint("id = generation_run_id", name="ck_question_candidates_deterministic_id"),
        CheckConstraint(
            "blueprint_id = btrim(blueprint_id) AND length(blueprint_id) BETWEEN 1 AND 128 AND "
            "blueprint_version = btrim(blueprint_version) AND "
            "length(blueprint_version) BETWEEN 1 AND 128 AND "
            "blueprint_slot_id = btrim(blueprint_slot_id) AND "
            "length(blueprint_slot_id) BETWEEN 1 AND 128",
            name="ck_question_candidates_blueprint_values",
        ),
        CheckConstraint(
            f"current_revision BETWEEN 1 AND {MAX_CANDIDATE_REVISIONS} AND "
            "((state = 'validated' AND version = 2 AND current_revision = 1) OR "
            "(state = 'in_review' AND version = current_revision + 2) OR "
            "(state IN ('approved', 'rejected') AND version = current_revision + 3))",
            name="ck_question_candidates_state_version_revision",
        ),
        CheckConstraint(
            "review_candidate_lineage_valid(generation_lineage, id, generation_attempt_id, "
            "paper_blueprint_id, blueprint_id, blueprint_version, blueprint_slot_id) AND "
            f"pg_column_size(generation_lineage) <= {MAX_CANDIDATE_LINEAGE_BYTES}",
            name="ck_question_candidates_generation_lineage",
        ),
        CheckConstraint(
            "review_candidate_evidence_valid(validation_evidence, validation_run_id) AND "
            f"pg_column_size(validation_evidence) <= "
            f"{MAX_CANDIDATE_VALIDATION_EVIDENCE_BYTES}",
            name="ck_question_candidates_validation_evidence",
        ),
        Index(
            "ix_question_candidates_curriculum_state_created",
            "curriculum_version_id",
            "state",
            "created_at",
            "id",
        ),
        Index(
            "ix_question_candidates_curriculum_blueprint_slot",
            "curriculum_version_id",
            "paper_blueprint_id",
            "blueprint_slot_id",
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
    validation_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    paper_blueprint_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    blueprint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    blueprint_version: Mapped[str] = mapped_column(String(128), nullable=False)
    blueprint_slot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_lineage: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    validation_evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class QuestionCandidateRevisionModel(Base):
    __tablename__ = "question_candidate_revisions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "candidate_id",
            "revision",
            name="pk_question_candidate_revisions",
        ),
        CheckConstraint(
            f"revision BETWEEN 1 AND {MAX_CANDIDATE_REVISIONS} AND "
            "((revision = 1 AND candidate_version = 2 AND reviewer_id IS NULL AND reason IS NULL) "
            "OR (revision >= 2 AND candidate_version = revision + 2 AND "
            "reviewer_id IS NOT NULL AND reason IS NOT NULL))",
            name="ck_question_candidate_revisions_identity",
        ),
        CheckConstraint(
            "reason IS NULL OR (reason = btrim(reason) AND "
            f"char_length(reason) BETWEEN 1 AND {MAX_REVIEW_REASON_CHARACTERS})",
            name="ck_question_candidate_revisions_reason",
        ),
        CheckConstraint(
            "review_candidate_content_valid(content) AND "
            f"pg_column_size(content) <= {MAX_CANDIDATE_CONTENT_BYTES}",
            name="ck_question_candidate_revisions_content",
        ),
        Index(
            "ix_question_candidate_revisions_candidate_revision",
            "candidate_id",
            "revision",
        ),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("question_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    reviewer_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(MAX_REVIEW_REASON_CHARACTERS), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CandidateReviewEventModel(Base):
    __tablename__ = "candidate_review_events"
    __table_args__ = (
        PrimaryKeyConstraint(
            "candidate_id",
            "candidate_version",
            name="pk_candidate_review_events",
        ),
        CheckConstraint(
            f"candidate_version BETWEEN 3 AND {MAX_CANDIDATE_VERSION} AND "
            f"revision BETWEEN 1 AND {MAX_CANDIDATE_REVISIONS}",
            name="ck_candidate_review_events_bounds",
        ),
        CheckConstraint(
            "(action = 'started' AND candidate_version = 3 AND revision = 1 AND reason IS NULL) "
            "OR (action = 'edited' AND revision >= 2 AND "
            "candidate_version = revision + 2 AND reason IS NOT NULL) "
            "OR (action = 'approved' AND candidate_version = revision + 3) "
            "OR (action = 'rejected' AND candidate_version = revision + 3 AND "
            "reason IS NOT NULL)",
            name="ck_candidate_review_events_action",
        ),
        CheckConstraint(
            "reason IS NULL OR (reason = btrim(reason) AND "
            f"char_length(reason) BETWEEN 1 AND {MAX_REVIEW_REASON_CHARACTERS})",
            name="ck_candidate_review_events_reason",
        ),
        Index(
            "ix_candidate_review_events_candidate_version",
            "candidate_id",
            "candidate_version",
        ),
        Index("ix_candidate_review_events_reviewer_created", "reviewer_id", "created_at"),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("question_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(MAX_REVIEW_REASON_CHARACTERS), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
