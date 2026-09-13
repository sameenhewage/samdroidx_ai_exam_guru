from __future__ import annotations

from typing import TYPE_CHECKING, Self
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base
from exam_guru_api.knowledge.unit_models import _Created

if TYPE_CHECKING:
    from exam_guru_api.knowledge.unit_review import KnowledgeUnitReview


class KnowledgeUnitReviewModel(_Created, Base):
    __tablename__ = "knowledge_unit_reviews"
    __table_args__ = (
        UniqueConstraint("unit_id", "version", name="uq_knowledge_unit_review_version"),
        ForeignKeyConstraint(
            ["unit_id", "unit_fingerprint"],
            ["knowledge_units.id", "knowledge_units.fingerprint"],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_review_unit",
        ),
        ForeignKeyConstraint(
            ["curriculum_unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_review_curriculum_unit",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "curriculum_unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_review_lesson",
        ),
        *(
            ForeignKeyConstraint(
                [column, "curriculum_version_id"],
                ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
                ondelete="RESTRICT",
                name="fk_knowledge_unit_review_" + column,
            )
            for column in ("competency_id", "skill_id", "sub_skill_id", "learning_concept_id")
        ),
        CheckConstraint(
            "version BETWEEN 1 AND 2147483646", name="ck_knowledge_unit_review_version"
        ),
        CheckConstraint(
            "(state='reviewed' AND confirmed_mapping AND competency_id IS NOT NULL) OR "
            "(state='rejected' AND NOT confirmed_mapping AND competency_id IS NULL "
            "AND skill_id IS NULL AND sub_skill_id IS NULL AND learning_concept_id IS NULL "
            "AND curriculum_unit_id IS NULL AND lesson_id IS NULL)",
            name="ck_knowledge_unit_review_decision",
        ),
        CheckConstraint(
            "(lesson_id IS NULL OR curriculum_unit_id IS NOT NULL) "
            "AND (skill_id IS NULL OR competency_id IS NOT NULL) "
            "AND (sub_skill_id IS NULL OR skill_id IS NOT NULL) "
            "AND (learning_concept_id IS NULL OR sub_skill_id IS NOT NULL)",
            name="ck_knowledge_unit_review_path",
        ),
        CheckConstraint(
            "public.knowledge_review_reason_valid(reason)", name="ck_knowledge_unit_review_reason"
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536",
            name="ck_knowledge_unit_review_payload",
        ),
        CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_knowledge_unit_review_hash",
        ),
        Index(
            "ix_knowledge_unit_review_scope", "curriculum_version_id", "competency_id", "skill_id"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    unit_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed_mapping: Mapped[bool] = mapped_column(Boolean, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_unit_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lesson_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    competency_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sub_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    learning_concept_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    @classmethod
    def from_domain(cls, review: KnowledgeUnitReview, *, audit_event_id: UUID) -> Self:
        return cls(
            id=review.id,
            unit_id=review.unit_id,
            unit_fingerprint=review.unit_fingerprint,
            version=review.version,
            state=review.state,
            confirmed_mapping=review.confirmed_mapping,
            curriculum_version_id=review.curriculum_version_id,
            curriculum_unit_id=review.curriculum_unit_id,
            lesson_id=review.lesson_id,
            competency_id=review.competency_id,
            skill_id=review.skill_id,
            sub_skill_id=review.sub_skill_id,
            learning_concept_id=review.learning_concept_id,
            reason=review.reason,
            payload=review.model_dump(mode="json"),
            fingerprint=review.fingerprint,
            created_by=review.actor_id,
            audit_event_id=audit_event_id,
        )
