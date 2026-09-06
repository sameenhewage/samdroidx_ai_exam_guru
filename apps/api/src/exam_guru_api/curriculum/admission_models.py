from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base


class CatalogueAdmissionDecisionModel(Base):
    __tablename__ = "catalogue_admission_decisions"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_version_id", "version", name="uq_catalogue_admission_decision_version"
        ),
        UniqueConstraint(
            "curriculum_version_id", "id", "version", name="uq_catalogue_admission_decision_pointer"
        ),
        UniqueConstraint("audit_event_id", name="uq_catalogue_admission_decision_audit"),
        CheckConstraint("version >= 1", name="ck_catalogue_admission_decision_version"),
        CheckConstraint(
            "state IN ('approved', 'rejected', 'quarantined')",
            name="ck_catalogue_admission_decision_state",
        ),
        CheckConstraint(
            "educational_approval = (state = 'approved')",
            name="ck_catalogue_admission_explicit_approval",
        ),
        CheckConstraint(
            "scope_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_catalogue_admission_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(scope_snapshot) = 'object' "
            "AND octet_length(scope_snapshot::text) <= 16384",
            name="ck_catalogue_admission_snapshot",
        ),
        CheckConstraint(
            "public.catalogue_admission_text_valid(reason, 1024)",
            name="ck_catalogue_admission_reason",
        ),
        CheckConstraint(
            "public.catalogue_admission_text_valid(source_reference, 1024)",
            name="ck_catalogue_admission_source_reference",
        ),
        CheckConstraint(
            "public.catalogue_admission_evidence_valid(evidence)",
            name="ck_catalogue_admission_evidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    scope_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    educational_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_audit_events.id", ondelete="RESTRICT"), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CatalogueAdmissionCurrentModel(Base):
    __tablename__ = "catalogue_admission_current"
    __table_args__ = (
        ForeignKeyConstraint(
            ["curriculum_version_id", "decision_id", "version"],
            [
                "catalogue_admission_decisions.curriculum_version_id",
                "catalogue_admission_decisions.id",
                "catalogue_admission_decisions.version",
            ],
            ondelete="RESTRICT",
            name="fk_catalogue_admission_current_decision",
        ),
        CheckConstraint("version >= 1", name="ck_catalogue_admission_current_version"),
    )

    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
