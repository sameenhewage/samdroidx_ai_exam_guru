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


class MaterialKnowledgeRequestModel(Base):
    __tablename__ = "material_knowledge_requests"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_material_knowledge_request_document"),
        UniqueConstraint(
            "id",
            "document_id",
            "source_sha256",
            "requested_by",
            name="uq_material_knowledge_request_source",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_material_knowledge_request_hash"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_audit_events.id", ondelete="RESTRICT"), nullable=False
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


class KnowledgePreparationJobModel(Base):
    __tablename__ = "knowledge_preparation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "trusted_page_id",
            "scope_fingerprint",
            "derivation_version",
            "transformation_version",
            name="uq_knowledge_preparation_input",
        ),
        ForeignKeyConstraint(
            ["request_id", "document_id", "source_sha256", "requested_by"],
            [
                "material_knowledge_requests.id",
                "material_knowledge_requests.document_id",
                "material_knowledge_requests.source_sha256",
                "material_knowledge_requests.requested_by",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_preparation_request",
        ),
        ForeignKeyConstraint(
            ["trusted_page_id", "candidate_id", "document_id", "page_number"],
            [
                "trusted_page_knowledge.id",
                "trusted_page_knowledge.candidate_id",
                "trusted_page_knowledge.document_id",
                "trusted_page_knowledge.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_preparation_trusted",
        ),
        CheckConstraint(
            "page_number>0 AND version>=0 AND attempts BETWEEN 0 AND 3",
            name="ck_knowledge_preparation_versions",
        ),
        CheckConstraint(
            "status IN ('queued','running','deferred','succeeded','failed','superseded')",
            name="ck_knowledge_preparation_status",
        ),
        CheckConstraint(
            "derivation_version='page-region-components.v1' "
            "AND transformation_version='source-observation-meaning.v1'",
            name="ck_knowledge_preparation_transforms",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND scope_fingerprint ~ '^[0-9a-f]{64}$' "
            "AND input_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_preparation_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot)='object' AND octet_length(input_snapshot::text)<=16384",
            name="ck_knowledge_preparation_input",
        ),
        CheckConstraint(
            "input_fingerprint=public.source_understanding_fingerprint(input_snapshot) "
            "AND scope_fingerprint="
            "public.source_understanding_fingerprint(input_snapshot->'scope')",
            name="ck_knowledge_preparation_input_hash",
        ),
        CheckConstraint(
            "(status='running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at>updated_at "
            "AND lease_expires_at<=updated_at+interval '3600 seconds') "
            "OR (status<>'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_knowledge_preparation_lease",
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','superseded'))=(completed_at IS NOT NULL)",
            name="ck_knowledge_preparation_completed",
        ),
        CheckConstraint(
            "((status='succeeded' AND unit_count BETWEEN 1 AND 128 "
            "AND projection_count BETWEEN 0 AND unit_count) "
            "OR (status<>'succeeded' AND unit_count IS NULL AND projection_count IS NULL)) IS TRUE",
            name="ck_knowledge_preparation_result",
        ),
        CheckConstraint(
            "((status IN ('running','succeeded') AND failure_code IS NULL) "
            "OR (status='queued' AND (failure_code IS NULL OR failure_code IN "
            "('knowledge_preparation_failed','knowledge_preparation_invalid',"
            "'knowledge_preparation_lease_expired'))) "
            "OR (status='deferred' AND failure_code='knowledge_preparation_unavailable') "
            "OR (status='superseded' AND failure_code='knowledge_preparation_superseded') "
            "OR (status='failed' AND failure_code IN "
            "('knowledge_preparation_failed','knowledge_preparation_invalid',"
            "'knowledge_preparation_lease_expired'))) IS TRUE",
            name="ck_knowledge_preparation_failure",
        ),
        Index("ix_knowledge_preparation_recovery", "status", "updated_at", "lease_expires_at"),
        Index("ix_knowledge_preparation_document", "document_id", "page_number"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    trusted_page_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_audit_events.id", ondelete="RESTRICT"), nullable=False
    )
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    derivation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    transformation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    projection_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
        ),
        nullable=False,
    )
