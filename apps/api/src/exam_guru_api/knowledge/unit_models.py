import hashlib
from datetime import datetime
from typing import Self
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.documents.understanding_contracts import _canonical_bytes
from exam_guru_api.infrastructure.database import Base
from exam_guru_api.knowledge.units import KnowledgeProjection, KnowledgeUnit


class _Created:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
        ),
        nullable=False,
    )


class KnowledgeUnitModel(_Created, Base):
    __tablename__ = "knowledge_units"
    __table_args__ = (
        UniqueConstraint(
            "id", "candidate_id", "document_id", "page_number", name="uq_knowledge_unit_source"
        ),
        UniqueConstraint("id", "fingerprint", name="uq_knowledge_unit_fingerprint"),
        UniqueConstraint(
            "trusted_page_id",
            "scope_fingerprint",
            "derivation_version",
            "sequence",
            name="uq_knowledge_unit_derivation",
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
            name="fk_knowledge_unit_trusted_page",
        ),
        ForeignKeyConstraint(
            ["curriculum_version_id", "catalogue_decision_id", "catalogue_version"],
            [
                "catalogue_admission_decisions.curriculum_version_id",
                "catalogue_admission_decisions.id",
                "catalogue_admission_decisions.version",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_admission",
        ),
        ForeignKeyConstraint(
            ["curriculum_unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_curriculum_unit",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "curriculum_unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_lesson",
        ),
        CheckConstraint(
            "page_number>0 AND sequence BETWEEN 0 AND 127 AND metadata_scope_version>=0",
            name="ck_knowledge_unit_versions",
        ),
        CheckConstraint(
            "lesson_id IS NULL OR curriculum_unit_id IS NOT NULL", name="ck_knowledge_unit_lesson"
        ),
        CheckConstraint(
            "derivation_version='page-region-components.v1'", name="ck_knowledge_unit_derivation"
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304",
            name="ck_knowledge_unit_payload",
        ),
        CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_knowledge_unit_payload_hash",
        ),
        CheckConstraint(
            "scope_fingerprint=public.source_understanding_fingerprint(payload->'scope')",
            name="ck_knowledge_unit_scope_hash",
        ),
        Index(
            "ix_knowledge_unit_scope", "curriculum_version_id", "curriculum_unit_id", "lesson_id"
        ),
        Index("ix_knowledge_unit_document", "document_id", "page_number"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    trusted_page_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_unit_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lesson_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    catalogue_decision_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    catalogue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_scope_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    derivation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    @classmethod
    def from_domain(cls, unit: KnowledgeUnit, *, actor_id: UUID, audit_event_id: UUID) -> Self:
        scope = unit.scope
        return cls(
            id=unit.id,
            trusted_page_id=unit.trusted_page_id,
            candidate_id=unit.candidate_id,
            document_id=unit.source.document_id,
            page_number=unit.source.page_number,
            curriculum_version_id=scope.curriculum_version_id,
            curriculum_unit_id=scope.curriculum_unit_id,
            lesson_id=scope.lesson_id,
            catalogue_decision_id=scope.catalogue_decision_id,
            catalogue_version=scope.catalogue_version,
            metadata_scope_version=scope.metadata_scope_version,
            scope_fingerprint=hashlib.sha256(_canonical_bytes(scope)).hexdigest(),
            derivation_version=unit.derivation_version,
            sequence=unit.sequence,
            payload=unit.model_dump(mode="json"),
            fingerprint=unit.fingerprint,
            created_by=actor_id,
            audit_event_id=audit_event_id,
        )


class KnowledgeUnitRegionModel(Base):
    __tablename__ = "knowledge_unit_regions"
    __table_args__ = (
        UniqueConstraint("unit_id", "ordinal", name="uq_knowledge_unit_region_order"),
        ForeignKeyConstraint(
            ["unit_id", "candidate_id", "document_id", "page_number"],
            [
                "knowledge_units.id",
                "knowledge_units.candidate_id",
                "knowledge_units.document_id",
                "knowledge_units.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_region_unit",
        ),
        ForeignKeyConstraint(
            ["region_id", "candidate_id", "document_id", "page_number"],
            [
                "source_understanding_regions.id",
                "source_understanding_regions.candidate_id",
                "source_understanding_regions.document_id",
                "source_understanding_regions.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_region_source",
        ),
        CheckConstraint(
            "ordinal BETWEEN 0 AND 127 AND region_key ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="ck_knowledge_unit_region_order",
        ),
    )
    unit_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    region_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    region_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class KnowledgeProjectionModel(_Created, Base):
    __tablename__ = "knowledge_projections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["unit_id", "unit_fingerprint"],
            ["knowledge_units.id", "knowledge_units.fingerprint"],
            ondelete="RESTRICT",
            name="fk_knowledge_projection_unit",
        ),
        UniqueConstraint(
            "unit_id", "transformation_version", name="uq_knowledge_projection_transform"
        ),
        CheckConstraint(
            "transformation_version='source-observation-meaning.v1'",
            name="ck_knowledge_projection_transform",
        ),
        CheckConstraint(
            "char_length(text) BETWEEN 1 AND 32768 AND octet_length(text)<=65536 "
            "AND text=normalize(text,NFC)",
            name="ck_knowledge_projection_text",
        ),
        CheckConstraint(
            "text_sha256=encode(sha256(convert_to(text,'UTF8')),'hex')",
            name="ck_knowledge_projection_text_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=131072",
            name="ck_knowledge_projection_payload",
        ),
        CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_knowledge_projection_payload_hash",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    unit_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    transformation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    @classmethod
    def from_domain(
        cls, projection: KnowledgeProjection, *, actor_id: UUID, audit_event_id: UUID
    ) -> Self:
        return cls(
            id=projection.id,
            unit_id=projection.unit_id,
            unit_fingerprint=projection.unit_fingerprint,
            transformation_version=projection.transformation_version,
            text=projection.text,
            text_sha256=projection.text_sha256,
            payload=projection.model_dump(mode="json"),
            fingerprint=projection.fingerprint,
            created_by=actor_id,
            audit_event_id=audit_event_id,
        )
