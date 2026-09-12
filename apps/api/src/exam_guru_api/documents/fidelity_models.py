from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base


class PageTextCandidateModel(Base):
    __tablename__ = "source_page_text_candidates"
    __table_args__ = (
        UniqueConstraint("id", "document_id", "page_number", name="uq_page_candidate_identity"),
        ForeignKeyConstraint(
            ["parent_candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_page_candidate_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint("page_number > 0", name="ck_page_candidate_page"),
        CheckConstraint(
            "method IN ('native','legacy','ocr','human')", name="ck_page_candidate_method"
        ),
        CheckConstraint(
            "octet_length(raw_text_utf8) <= 4194304", name="ck_page_candidate_raw_size"
        ),
        CheckConstraint(
            "normalized_text IS NULL OR (octet_length(normalized_text) <= 4194304 "
            "AND normalized_text = normalize(normalized_text, NFC))",
            name="ck_page_candidate_normalized_text",
        ),
        CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_page_candidate_hash"),
        CheckConstraint(
            "jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 65536 "
            "AND jsonb_typeof(diagnostics) = 'object' AND octet_length(diagnostics::text) <= 65536",
            name="ck_page_candidate_metadata",
        ),
        Index("ix_page_candidates_history", "document_id", "page_number", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT")
    )
    page_number: Mapped[int] = mapped_column(Integer)
    parent_candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    method: Mapped[str] = mapped_column(String(16))
    raw_text_utf8: Mapped[bytes] = mapped_column(LargeBinary)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_sha256: Mapped[str] = mapped_column(String(64))
    can_confirm: Mapped[bool] = mapped_column(Boolean)
    provenance: Mapped[dict[str, object]] = mapped_column(JSONB)
    diagnostics: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PageReviewEventModel(Base):
    __tablename__ = "source_page_review_events"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "page_number", "version", name="uq_page_review_event_version"
        ),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_page_review_event_candidate",
            ondelete="RESTRICT",
        ),
        CheckConstraint("page_number > 0 AND version > 0", name="ck_page_review_event_version"),
        CheckConstraint(
            "action IN ('candidate_recorded','confirmed','edited','excluded','reread_requested',"
            "'reread_failed','reference_verified')",
            name="ck_page_review_event_action",
        ),
        CheckConstraint(
            "state IN ('needs_review','verified','excluded','processing','failed')",
            name="ck_page_review_event_state",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 2000 AND octet_length(payload::text) <= 65536 "
            "AND jsonb_typeof(payload) = 'object'",
            name="ck_page_review_event_payload",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT")
    )
    page_number: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PageReviewStateModel(Base):
    __tablename__ = "source_page_review_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_page_review_current_candidate",
            ondelete="RESTRICT",
        ),
        CheckConstraint("page_number > 0 AND version >= 0", name="ck_page_review_state_version"),
        CheckConstraint(
            "state IN ('pending','needs_review','verified','excluded','processing','failed')",
            name="ck_page_review_state",
        ),
        CheckConstraint(
            "(version = 0 AND state = 'pending' AND event_id IS NULL "
            "AND current_candidate_id IS NULL) "
            "OR (version > 0 AND event_id IS NOT NULL)",
            name="ck_page_review_state_event",
        ),
        CheckConstraint(
            "state <> 'verified' OR current_candidate_id IS NOT NULL",
            name="ck_page_review_verified_candidate",
        ),
        Index("ix_page_review_state", "document_id", "state", "page_number"),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, server_default="0")
    state: Mapped[str] = mapped_column(String(16), server_default="pending")
    current_candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "source_page_review_events.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceBenchmarkModel(Base):
    __tablename__ = "source_fidelity_benchmarks"
    __table_args__ = (
        UniqueConstraint("name", name="uq_source_fidelity_benchmark_name"),
        CheckConstraint("length(name) BETWEEN 1 AND 160", name="ck_source_benchmark_name"),
        CheckConstraint(
            "jsonb_typeof(selection) = 'object' AND octet_length(selection::text) <= 65536",
            name="ck_source_benchmark_selection",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    selection: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceBenchmarkPageModel(Base):
    __tablename__ = "source_fidelity_benchmark_pages"
    __table_args__ = (
        CheckConstraint("page_number > 0", name="ck_source_benchmark_page"),
        CheckConstraint(
            "jsonb_typeof(categories) = 'array' "
            "AND jsonb_array_length(categories) BETWEEN 1 AND 32",
            name="ck_source_benchmark_categories",
        ),
    )
    benchmark_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_fidelity_benchmarks.id", ondelete="RESTRICT"), primary_key=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    categories: Mapped[list[str]] = mapped_column(JSONB)


class PageGroundTruthModel(Base):
    __tablename__ = "source_page_ground_truth"
    __table_args__ = (
        UniqueConstraint("benchmark_id", "review_event_id", name="uq_page_ground_truth_event"),
        ForeignKeyConstraint(
            ["benchmark_id", "document_id", "page_number"],
            [
                "source_fidelity_benchmark_pages.benchmark_id",
                "source_fidelity_benchmark_pages.document_id",
                "source_fidelity_benchmark_pages.page_number",
            ],
            name="fk_ground_truth_benchmark_page",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            name="fk_ground_truth_candidate",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_ground_truth_source_hash"
        ),
        CheckConstraint("text_sha256 ~ '^[0-9a-f]{64}$'", name="ck_ground_truth_text_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    benchmark_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    page_number: Mapped[int] = mapped_column(Integer)
    candidate_id: Mapped[UUID] = mapped_column(Uuid)
    review_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_page_review_events.id", ondelete="RESTRICT")
    )
    source_checksum_sha256: Mapped[str] = mapped_column(String(64))
    text_sha256: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[UUID] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceEvaluationPreviewModel(Base):
    __tablename__ = "source_evaluation_previews"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "benchmark_id",
            "document_id",
            "page_number",
            name="uq_evaluation_preview_identity",
        ),
        ForeignKeyConstraint(
            ["benchmark_id", "document_id", "page_number"],
            [
                "source_fidelity_benchmark_pages.benchmark_id",
                "source_fidelity_benchmark_pages.document_id",
                "source_fidelity_benchmark_pages.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_preview_membership",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_page_text_candidates.id",
                "source_page_text_candidates.document_id",
                "source_page_text_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_preview_candidate",
        ),
        CheckConstraint(
            "source_checksum_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_preview_source_hash"
        ),
        CheckConstraint("image_sha256 ~ '^[0-9a-f]{64}$'", name="ck_evaluation_preview_image_hash"),
        CheckConstraint(
            "jsonb_typeof(image_metadata)='object' AND octet_length(image_metadata::text)<=8192",
            name="ck_evaluation_preview_metadata",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    benchmark_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    page_number: Mapped[int] = mapped_column(Integer)
    candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_checksum_sha256: Mapped[str] = mapped_column(String(64))
    image_sha256: Mapped[str] = mapped_column(String(64))
    image_metadata: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceEvaluationReferenceModel(Base):
    __tablename__ = "source_evaluation_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["preview_id", "benchmark_id", "document_id", "page_number"],
            [
                "source_evaluation_previews.id",
                "source_evaluation_previews.benchmark_id",
                "source_evaluation_previews.document_id",
                "source_evaluation_previews.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_evaluation_reference_preview",
        ),
        UniqueConstraint(
            "benchmark_id",
            "document_id",
            "page_number",
            "version",
            name="uq_evaluation_reference_version",
        ),
        CheckConstraint("version > 0", name="ck_evaluation_reference_version"),
        CheckConstraint(
            "source_evaluation_text_is_valid(raw_text_utf8, normalized_text, blank_reference)",
            name="ck_evaluation_reference_text",
        ),
        CheckConstraint(
            "text_sha256=encode(sha256(raw_text_utf8),'hex')",
            name="ck_evaluation_reference_raw_hash",
        ),
        CheckConstraint(
            "normalized_sha256=encode(sha256(convert_to(normalized_text,'UTF8')),'hex')",
            name="ck_evaluation_reference_normalized_hash",
        ),
        CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 2000 AND reason=btrim(reason) "
            "AND reason !~ '[[:cntrl:]]'",
            name="ck_evaluation_reference_reason",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    preview_id: Mapped[UUID] = mapped_column(Uuid)
    benchmark_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    page_number: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    raw_text_utf8: Mapped[bytes] = mapped_column(LargeBinary)
    normalized_text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    normalized_sha256: Mapped[str] = mapped_column(String(64))
    blank_reference: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    reviewer_id: Mapped[UUID] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceReadJobModel(Base):
    __tablename__ = "source_read_jobs"
    __table_args__ = (
        CheckConstraint("page_number IS NULL OR page_number > 0", name="ck_source_read_job_page"),
        CheckConstraint(
            "next_page > 0 AND attempts >= 0 AND version >= 0", name="ck_source_read_job_progress"
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','superseded')",
            name="ck_source_read_job_status",
        ),
        CheckConstraint(
            "jsonb_typeof(configuration) = 'object' AND octet_length(configuration::text) <= 65536",
            name="ck_source_read_job_configuration",
        ),
        Index(
            "uq_active_source_read_job",
            "document_id",
            "page_number",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_source_read_job_recovery", "status", "lease_expires_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT")
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_page: Mapped[int] = mapped_column(Integer, server_default="1")
    expected_page_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    version: Mapped[int] = mapped_column(Integer, server_default="0")
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
