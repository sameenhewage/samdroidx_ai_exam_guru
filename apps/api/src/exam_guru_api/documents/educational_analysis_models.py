from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.documents.understanding_models import _Created
from exam_guru_api.infrastructure.database import Base


class EducationalAnalysisModel(_Created, Base):
    __tablename__ = "source_educational_analyses"
    __table_args__ = (
        UniqueConstraint("created_by", "request_id", name="uq_source_education_request"),
        CheckConstraint("version>=0", name="ck_source_education_version"),
        CheckConstraint(
            "status IN ('queued','running','provider_completed','failed','unknown')",
            name="ck_source_education_status",
        ),
        CheckConstraint(
            "(status='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_source_education_lease",
        ),
        CheckConstraint(
            "(status='provider_completed')=(content IS NOT NULL) "
            "AND (status NOT IN ('failed','unknown'))=(failure_code IS NULL)",
            name="ck_source_education_result",
        ),
        CheckConstraint(
            "jsonb_typeof(profile)='object' AND octet_length(profile::text)<=16384 "
            "AND jsonb_typeof(budget)='object' AND octet_length(budget::text)<=4096",
            name="ck_source_education_configuration",
        ),
        CheckConstraint(
            "content IS NULL OR (jsonb_typeof(content)='object' "
            "AND octet_length(content::text)<=1048576)",
            name="ck_source_education_content",
        ),
        CheckConstraint(
            "accounting IS NULL OR (jsonb_typeof(accounting)='object' "
            "AND octet_length(accounting::text)<=4096)",
            name="ck_source_education_accounting",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    verified_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("verified_source_contents.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    profile: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    budget: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="queued")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    accounting: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
