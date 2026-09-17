from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.documents.understanding_models import _Created
from exam_guru_api.infrastructure.database import Base


class SourceWitnessEventModel(_Created, Base):
    __tablename__ = "source_witness_events"
    __table_args__ = (
        UniqueConstraint("job_id", "pass_number", "event", name="uq_source_witness_event"),
        CheckConstraint("pass_number>=0 AND pass_number<512", name="ck_source_witness_pass"),
        CheckConstraint("reader IN ('qwen','openai','layout')", name="ck_source_witness_reader"),
        CheckConstraint(
            "event IN ('requested','provider_completed','parsed','failed','cache_hit')",
            name="ck_source_witness_event",
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=2097152 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_source_witness_payload",
        ),
        Index("ix_source_witness_input", "reader", "input_fingerprint", "event"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    lease_token: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    pass_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reader: Mapped[str] = mapped_column(String(16), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class MachineSourceCandidateModel(_Created, Base):
    __tablename__ = "source_machine_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_source_machine_job"),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304 "
            "AND fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_source_machine_payload",
        ),
    )
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_understanding_candidates.id", ondelete="RESTRICT"), primary_key=True
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
