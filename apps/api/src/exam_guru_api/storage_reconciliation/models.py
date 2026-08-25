from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base


class ReconciliationRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class FindingStatus(StrEnum):
    CANDIDATE = "candidate"
    RESOLVED = "resolved"


class TagStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    APPLIED = "applied"
    REMOVED = "removed"
    CAPACITY_CONFLICT = "capacity_conflict"
    FAILED = "failed"


class StorageReconciliationStateModel(Base):
    __tablename__ = "storage_reconciliation_state"
    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1",
            name="ck_storage_reconciliation_state_singleton",
        ),
        CheckConstraint(
            "continuation_cursor IS NULL OR "
            "(char_length(continuation_cursor) BETWEEN 1 AND 2048 "
            "AND continuation_cursor !~ '[[:cntrl:]]')",
            name="ck_storage_reconciliation_state_cursor",
        ),
        CheckConstraint(
            "(lease_token IS NULL AND ((last_started_at IS NULL "
            "AND last_completed_at IS NULL AND continuation_cursor IS NULL) OR "
            "(last_started_at IS NOT NULL AND last_completed_at IS NOT NULL "
            "AND last_completed_at >= last_started_at))) OR "
            "(lease_token IS NOT NULL AND last_started_at IS NOT NULL "
            "AND (last_completed_at IS NULL OR last_completed_at <= last_started_at))",
            name="ck_storage_reconciliation_state_timestamps",
        ),
        CheckConstraint(
            "(lease_token IS NULL AND lease_acquired_at IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_acquired_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND lease_acquired_at = last_started_at "
            "AND lease_expires_at > lease_acquired_at)",
            name="ck_storage_reconciliation_state_lease_shape",
        ),
    )

    singleton_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    continuation_cursor: Mapped[str | None] = mapped_column(String(2_048), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StorageReconciliationRunModel(Base):
    __tablename__ = "storage_reconciliation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_storage_reconciliation_run_status",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="ck_storage_reconciliation_run_timestamps",
        ),
        CheckConstraint(
            "grace_seconds BETWEEN 3600 AND 31536000",
            name="ck_storage_reconciliation_run_grace",
        ),
        CheckConstraint(
            "max_objects BETWEEN 1 AND 10000",
            name="ck_storage_reconciliation_run_max_objects",
        ),
        CheckConstraint(
            "scanned_count >= 0 AND referenced_count >= 0 AND candidate_count >= 0 "
            "AND resolved_count >= 0 AND tagged_count >= 0 AND failure_count >= 0 "
            "AND referenced_count <= scanned_count "
            "AND candidate_count + referenced_count <= scanned_count "
            "AND resolved_count <= referenced_count "
            "AND tagged_count <= candidate_count + referenced_count "
            "AND failure_count <= candidate_count + referenced_count + 1",
            name="ck_storage_reconciliation_run_counts",
        ),
        CheckConstraint(
            "(status = 'completed' AND failure_code IS NULL) OR "
            "(status = 'failed' AND failure_code IS NOT NULL "
            "AND failure_count > 0 AND truncated)",
            name="ck_storage_reconciliation_run_outcome",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) "
            "AND char_length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code ~ '^[a-z][a-z0-9_.-]*$')",
            name="ck_storage_reconciliation_run_failure_code",
        ),
        Index("ix_storage_reconciliation_runs_completed", "completed_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    apply_tags: Mapped[bool] = mapped_column(Boolean, nullable=False)
    grace_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_objects: Mapped[int] = mapped_column(Integer, nullable=False)
    scanned_count: Mapped[int] = mapped_column(Integer, nullable=False)
    referenced_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tagged_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class StorageOrphanFindingModel(Base):
    __tablename__ = "storage_orphan_findings"
    __table_args__ = (
        CheckConstraint(
            "object_key = 'sources/' || left(checksum_sha256, 2) || '/' "
            "|| checksum_sha256 || '.pdf' AND char_length(object_key) = 79",
            name="ck_storage_orphan_finding_object_key",
        ),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_storage_orphan_finding_checksum",
        ),
        CheckConstraint(
            "size_bytes BETWEEN 0 AND 5497558138880",
            name="ck_storage_orphan_finding_size",
        ),
        CheckConstraint(
            "first_seen_at <= candidate_since AND candidate_since <= last_seen_at "
            "AND created_at <= updated_at "
            "AND (resolved_at IS NULL OR resolved_at BETWEEN candidate_since AND last_seen_at) "
            "AND (tag_updated_at IS NULL OR tag_updated_at BETWEEN first_seen_at AND updated_at)",
            name="ck_storage_orphan_finding_timestamps",
        ),
        CheckConstraint(
            "(status = 'candidate' AND resolved_at IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL)",
            name="ck_storage_orphan_finding_status",
        ),
        CheckConstraint(
            "tag_status IN ('not_requested', 'applied', 'removed', 'capacity_conflict', 'failed')",
            name="ck_storage_orphan_finding_tag_status",
        ),
        CheckConstraint(
            "(tag_status = 'not_requested' AND tag_updated_at IS NULL "
            "AND failure_code IS NULL) OR "
            "(tag_status IN ('applied', 'removed') AND tag_updated_at IS NOT NULL "
            "AND failure_code IS NULL) OR "
            "(tag_status IN ('capacity_conflict', 'failed') "
            "AND tag_updated_at IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_storage_orphan_finding_tag_outcome",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (failure_code = btrim(failure_code) "
            "AND char_length(failure_code) BETWEEN 1 AND 128 "
            "AND failure_code ~ '^[a-z][a-z0-9_.-]*$')",
            name="ck_storage_orphan_finding_failure_code",
        ),
        Index("ix_storage_orphan_findings_status", "status", "last_seen_at"),
    )

    object_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candidate_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_last_modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    tag_status: Mapped[str] = mapped_column(String(32), nullable=False)
    tag_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
