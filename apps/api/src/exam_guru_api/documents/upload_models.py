from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.upload_schemas import UploadStatus
from exam_guru_api.infrastructure.database import Base

_DOCUMENT_TYPES = ", ".join(f"'{value.value}'" for value in SourceDocumentType)


class SourceUploadSessionModel(Base):
    __tablename__ = "source_upload_sessions"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_source_upload_owner_request"),
        CheckConstraint("size_bytes >= 5", name="ck_source_upload_size"),
        CheckConstraint(
            "next_offset BETWEEN 0 AND size_bytes AND "
            "(next_offset = size_bytes OR next_offset % 4194304 = 0)",
            name="ck_source_upload_offset",
        ),
        CheckConstraint(
            "verified_bytes BETWEEN 0 AND next_offset AND version >= 0",
            name="ck_source_upload_progress",
        ),
        CheckConstraint(
            "status IN ('uploading', 'pending', 'finalizing', 'completed', 'failed')",
            name="ck_source_upload_status",
        ),
        CheckConstraint(f"document_type IN ({_DOCUMENT_TYPES})", name="ck_source_upload_type"),
        CheckConstraint(
            "char_length(filename) BETWEEN 5 AND 255 AND right(lower(filename), 4) = '.pdf' "
            "AND filename !~ '[[:cntrl:]/]' AND position(chr(92) in filename) = 0",
            name="ck_source_upload_filename",
        ),
        CheckConstraint(
            "expected_checksum_sha256 IS NULL OR expected_checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_source_upload_expected_checksum",
        ),
        CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_source_upload_checksum",
        ),
        CheckConstraint(
            "public.source_intake_metadata_is_bounded(intake_metadata)",
            name="ck_source_upload_intake",
        ),
        CheckConstraint(
            "(unit_id IS NULL OR curriculum_version_id IS NOT NULL) AND "
            "(lesson_id IS NULL OR unit_id IS NOT NULL)",
            name="ck_source_upload_scope_shape",
        ),
        CheckConstraint("year IS NULL OR year BETWEEN 1900 AND 2100", name="ck_source_upload_year"),
        CheckConstraint(
            "paper_code IS NULL OR (char_length(paper_code) BETWEEN 1 AND 64 AND "
            "paper_code = btrim(paper_code) AND paper_code !~ '[[:cntrl:]]')",
            name="ck_source_upload_paper_code",
        ),
        CheckConstraint(
            "(status = 'finalizing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'finalizing' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_source_upload_lease",
        ),
        CheckConstraint(
            "(status = 'completed' AND document_id IS NOT NULL AND checksum_sha256 IS NOT NULL "
            "AND verified_bytes = size_bytes AND failure_code IS NULL) OR "
            "(status <> 'completed' AND document_id IS NULL AND NOT deduplicated "
            "AND likely_metadata_duplicate_of_id IS NULL)",
            name="ck_source_upload_result",
        ),
        CheckConstraint(
            "(status = 'uploading' AND verified_bytes = 0 AND checksum_sha256 IS NULL "
            "AND failure_code IS NULL) OR (status <> 'uploading' AND next_offset = size_bytes)",
            name="ck_source_upload_ready",
        ),
        CheckConstraint(
            "(status <> 'failed' OR failure_code IS NOT NULL) AND "
            "(failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$')",
            name="ck_source_upload_failure",
        ),
        ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_source_upload_unit_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_source_upload_lesson_scope",
            ondelete="RESTRICT",
        ),
        Index("ix_source_upload_owner", "owner_id", "status"),
        Index(
            "ix_source_upload_pending",
            "status",
            "lease_expires_at",
            "created_at",
            "id",
            postgresql_where=text("status IN ('pending', 'finalizing')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_type: Mapped[SourceDocumentType] = mapped_column(
        Enum(
            SourceDocumentType,
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=32,
        ),
        nullable=False,
    )
    intake_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expected_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    curriculum_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"), nullable=True
    )
    unit_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lesson_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[UploadStatus] = mapped_column(
        Enum(
            UploadStatus,
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=16,
        ),
        nullable=False,
        default=UploadStatus.UPLOADING,
        server_default="uploading",
    )
    next_offset: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    verified_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True
    )
    deduplicated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    likely_metadata_duplicate_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceUploadChunkModel(Base):
    __tablename__ = "source_upload_chunks"
    __table_args__ = (
        CheckConstraint(
            '"offset" >= 0 AND "offset" % 4194304 = 0', name="ck_source_upload_chunk_offset"
        ),
        CheckConstraint("size_bytes BETWEEN 1 AND 4194304", name="ck_source_upload_chunk_size"),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_source_upload_chunk_checksum"
        ),
    )

    upload_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_upload_sessions.id", ondelete="RESTRICT"), primary_key=True
    )
    offset: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
