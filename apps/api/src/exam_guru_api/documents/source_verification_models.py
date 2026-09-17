from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.documents.understanding_models import _Created
from exam_guru_api.infrastructure.database import Base


class VerifiedSourceContentModel(_Created, Base):
    __tablename__ = "verified_source_contents"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "page_number", "revision", name="uq_verified_source_revision"
        ),
        UniqueConstraint(
            "candidate_id", "page_version", name="uq_verified_source_candidate_version"
        ),
        UniqueConstraint("id", "document_id", "page_number", name="uq_verified_source_identity"),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_verified_source_candidate",
        ),
        ForeignKeyConstraint(
            ["report_id", "candidate_id", "document_id", "page_number"],
            [
                "source_understanding_reports.id",
                "source_understanding_reports.candidate_id",
                "source_understanding_reports.document_id",
                "source_understanding_reports.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_verified_source_report",
        ),
        CheckConstraint(
            "page_number>0 AND page_version>0 AND revision>0", name="ck_verified_source_versions"
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=2097152 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_verified_source_payload",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    report_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    page_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceReadingEventModel(_Created, Base):
    __tablename__ = "source_reading_events"
    __table_args__ = (
        UniqueConstraint("job_id", "pass_number", "event", name="uq_source_reading_event"),
        CheckConstraint("pass_number>=0 AND pass_number<64", name="ck_source_reading_pass"),
        CheckConstraint(
            "event IN ('requested','provider_completed','failed')", name="ck_source_reading_event"
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=2097152 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_source_reading_event_payload",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    lease_token: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    pass_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
