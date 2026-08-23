from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.curriculum.models import AuditColumns
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.infrastructure.database import Base

_DOCUMENT_TYPES_SQL = ", ".join(f"'{value.value}'" for value in SourceDocumentType)
_EXTRACTION_STATUSES_SQL = ", ".join(f"'{value.value}'" for value in ExtractionStatus)


class SourceDocumentModel(AuditColumns, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint(
            f"document_type IN ({_DOCUMENT_TYPES_SQL})",
            name="source_document_type",
        ),
        CheckConstraint(
            f"extraction_status IN ({_EXTRACTION_STATUSES_SQL})",
            name="extraction_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_source_document_positive_size"),
        CheckConstraint(
            "year IS NULL OR year BETWEEN 1900 AND 2100",
            name="ck_source_document_year",
        ),
        Index("ix_source_documents_status", "extraction_status"),
        Index("ix_source_documents_curriculum", "curriculum_version_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[SourceDocumentType] = mapped_column(
        Enum(
            SourceDocumentType,
            name="source_document_type",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=32,
        ),
        nullable=False,
    )
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        Enum(
            ExtractionStatus,
            name="extraction_status",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda values: [value.value for value in values],
            length=32,
        ),
        nullable=False,
        default=ExtractionStatus.UPLOADED,
        server_default=ExtractionStatus.UPLOADED.value,
    )
    curriculum_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
