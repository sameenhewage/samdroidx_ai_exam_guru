from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class MaterialKnowledgeIndexIntentModel(Base):
    __tablename__ = "material_knowledge_index_intents"
    __table_args__ = (
        UniqueConstraint("review_id", name="uq_material_index_review"),
        UniqueConstraint("dispatch_key", name="uq_material_index_dispatch"),
        UniqueConstraint("audit_event_id", name="uq_material_index_audit"),
        CheckConstraint(
            "version BETWEEN 0 AND 2147483646 AND review_version BETWEEN 1 AND 2147483646 "
            "AND attempt_number BETWEEN 0 AND 4",
            name="ck_material_index_versions",
        ),
        CheckConstraint(
            "status IN ('pending','waiting_configuration','dispatching','queued','ready',"
            "'needs_attention','superseded','not_searchable','configuration_changed')",
            name="ck_material_index_status",
        ),
        CheckConstraint(
            "review_fingerprint ~ '^[0-9a-f]{64}$' AND input_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND input_fingerprint=public.source_understanding_fingerprint(input_snapshot) "
            "AND jsonb_typeof(input_snapshot)='object' "
            "AND octet_length(input_snapshot::text)<=16384",
            name="ck_material_index_input",
        ),
        CheckConstraint(
            "((projection_id IS NULL AND status IN ('not_searchable','superseded') "
            "AND attempt_number=0) OR (projection_id IS NOT NULL AND status<>'not_searchable'))",
            name="ck_material_index_projection",
        ),
        CheckConstraint(
            "((attempt_number=0 AND dispatch_key IS NULL AND config_snapshot IS NULL "
            "AND config_snapshot_fingerprint IS NULL AND embedding_job_id IS NULL) OR "
            "(attempt_number>0 AND dispatch_key IS NOT NULL AND config_snapshot IS NOT NULL "
            "AND config_snapshot_fingerprint IS NOT NULL AND projection_id IS NOT NULL "
            "AND dispatch_key='material-knowledge:'||id::text||':attempt:'||attempt_number::text "
            "AND config_snapshot_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND config_snapshot_fingerprint="
            "public.source_understanding_fingerprint(config_snapshot) "
            "AND public.material_index_config_valid(config_snapshot))) IS TRUE",
            name="ck_material_index_binding",
        ),
        CheckConstraint(
            "(status NOT IN ('dispatching','queued','ready','configuration_changed') "
            "OR attempt_number>0) AND (status<>'queued' OR embedding_job_id IS NOT NULL) "
            "AND (status<>'pending' OR attempt_number=0)",
            name="ck_material_index_state_binding",
        ),
        CheckConstraint(
            "((status IN ('pending','dispatching','queued','ready','not_searchable') "
            "AND failure_code IS NULL) OR "
            "(status='waiting_configuration' AND failure_code='configuration_unavailable') OR "
            "(status='configuration_changed' AND failure_code='configuration_changed') OR "
            "(status='superseded' AND failure_code='indexing_source_superseded') OR "
            "(status='needs_attention' AND failure_code IN ('indexing_job_failed',"
            "'indexing_outcome_unknown','indexing_retry_exhausted','indexing_binding_invalid'))) "
            "IS TRUE",
            name="ck_material_index_failure",
        ),
        CheckConstraint(
            "event IN ('requested','not_searchable','dispatch_bound','retry_approved',"
            "'linked','observed','superseded') AND public.knowledge_review_reason_valid(reason) "
            "AND confirmed_retry=(event='retry_approved') AND updated_at>=created_at",
            name="ck_material_index_event",
        ),
        Index("ix_material_index_recovery", "status", "updated_at", "id"),
        Index("ix_material_index_unit", "unit_id", "review_version"),
        Index(
            "uq_material_index_active_unit",
            "unit_id",
            unique=True,
            postgresql_where=text("status<>'superseded'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    unit_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_units.id", ondelete="RESTRICT"), nullable=False
    )
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_unit_reviews.id", ondelete="RESTRICT"), nullable=False
    )
    review_version: Mapped[int] = mapped_column(Integer, nullable=False)
    review_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=False
    )
    projection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_projections.id", ondelete="RESTRICT"), nullable=True
    )
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    dispatch_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    config_snapshot_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("embedding_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    confirmed_retry: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    previous_audit_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_audit_events.id", ondelete="RESTRICT"), nullable=True
    )
    audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
