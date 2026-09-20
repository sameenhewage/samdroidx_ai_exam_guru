"""Source V2 persistence.

Shapes chosen so the database — not application care — enforces the rules:

* one current machine candidate per region, by partial unique index;
* a verified row can only point at a candidate that exists for that region and
  revision, by composite foreign key;
* review events are append-only, by trigger;
* verified content records the rendered image it was compared against, so a
  re-render invalidates trust instead of silently inheriting it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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

from exam_guru_api.infrastructure.database import Base

REGION_TYPES = ("text", "heading", "figure", "table", "decorative", "unknown")
REGION_STATES = ("unverified", "verified", "excluded")
REVIEW_ACTIONS = ("confirm", "correct", "exclude", "reopen")


class SourceV2Page(Base):
    """One rendered page, identified by the exact image the agent read."""

    __tablename__ = "source_v2_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_source_v2_page"),
        CheckConstraint("page_number >= 1", name="ck_source_v2_page_number"),
        CheckConstraint("char_length(image_sha256) = 64", name="ck_source_v2_page_sha"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    dpi: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(128), nullable=False)
    layout: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceV2MachineCandidate(Base):
    """The proposed reading for one region at one revision.

    One canonical crop, one reading by the executing agent, one candidate.
    There are no competing readers, so nothing here records a chosen reader,
    an agreement ratio or a conflict between readings.

    A correction appends a new revision whose parent is the reading it
    replaced; nothing is ever overwritten.
    """

    __tablename__ = "source_v2_machine_candidates"
    __table_args__ = (
        UniqueConstraint("page_id", "region_id", "revision", name="uq_source_v2_machine_revision"),
        UniqueConstraint("id", "page_id", "region_id", name="uq_source_v2_machine_identity"),
        Index(
            "uq_source_v2_machine_current",
            "page_id",
            "region_id",
            unique=True,
            postgresql_where="is_current",
        ),
        CheckConstraint("revision >= 1", name="ck_source_v2_machine_revision"),
        CheckConstraint(f"region_type IN {REGION_TYPES}", name="ck_source_v2_machine_region_type"),
        CheckConstraint(f"state IN {REGION_STATES}", name="ck_source_v2_machine_state"),
        CheckConstraint(
            # D18: abstaining on *text* is the correct reading of a figure, so a
            # visual-only region may still be verified as a visual.
            "NOT abstained OR state <> 'verified' OR source_kind = 'visual_only'",
            name="ck_source_v2_no_verified_abstention",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    page_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("source_v2_pages.id", ondelete="RESTRICT"), nullable=False
    )
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region_type: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("source_v2_machine_candidates.id", ondelete="RESTRICT")
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)  # machine | human-correction
    text: Mapped[str] = mapped_column(Text, nullable=False)
    abstained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(400), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceV2ReviewEvent(Base):
    """Append-only record of a human decision."""

    __tablename__ = "source_v2_review_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "page_id", "region_id"],
            [
                "source_v2_machine_candidates.id",
                "source_v2_machine_candidates.page_id",
                "source_v2_machine_candidates.region_id",
            ],
            ondelete="RESTRICT",
            name="fk_source_v2_event_candidate",
        ),
        CheckConstraint(f"action IN {REVIEW_ACTIONS}", name="ck_source_v2_event_action"),
        CheckConstraint(
            "char_length(compared_with_image_sha256) = 64", name="ck_source_v2_event_sha"
        ),
        CheckConstraint(
            "action <> 'exclude' OR (note IS NOT NULL AND btrim(note) <> '')",
            name="ck_source_v2_exclude_needs_reason",
        ),
        Index("ix_source_v2_event_page", "page_id", "region_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    page_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    compared_with_image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(2000))
    corrected_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceV2VerifiedRegion(Base):
    """Verified Source Content for one region. Immutable."""

    __tablename__ = "source_v2_verified_regions"
    __table_args__ = (
        UniqueConstraint("page_id", "region_id", name="uq_source_v2_verified_region"),
        ForeignKeyConstraint(
            ["candidate_id", "page_id", "region_id"],
            [
                "source_v2_machine_candidates.id",
                "source_v2_machine_candidates.page_id",
                "source_v2_machine_candidates.region_id",
            ],
            ondelete="RESTRICT",
            name="fk_source_v2_verified_candidate",
        ),
        CheckConstraint("char_length(image_sha256) = 64", name="ck_source_v2_verified_sha"),
        CheckConstraint("btrim(text) <> ''", name="ck_source_v2_verified_not_empty"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    page_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    region_id: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_nfc: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
