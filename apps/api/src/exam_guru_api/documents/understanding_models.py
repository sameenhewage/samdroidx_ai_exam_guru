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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base

_CANDIDATE_SNAPSHOT = """jsonb_build_object(
    'id',id::text,'run_id',run_id::text,'revision',revision,'method',method,
    'source',jsonb_build_object('document_id',document_id::text,'source_sha256',source_sha256,
        'page_number',page_number,'image_sha256',image_sha256),
    'content',jsonb_build_object('schema_version','page-understanding.v1',
        'observation',observation,'education',educational_understanding,'uncertainties',uncertainties))"""


_JOB_REQUEST_SNAPSHOT = """jsonb_build_object(
    'document_id',document_id::text,'page_number',page_number,'source_sha256',source_sha256,
    'expected_version',expected_page_version-1,'profile',profile,'budget',budget,'reason',reason,
    'retry_of_job_id',retry_of_job_id::text)"""


class _Created:
    audit_event_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
        ),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentUnderstandingRunModel(_Created, Base):
    __tablename__ = "source_understanding_runs"
    __table_args__ = (
        UniqueConstraint("created_by", "request_id", name="uq_understanding_run_request"),
        UniqueConstraint(
            "id",
            "document_id",
            "page_number",
            "source_sha256",
            "image_sha256",
            name="uq_understanding_run_source",
        ),
        CheckConstraint("page_number > 0", name="ck_understanding_run_page"),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND image_sha256 ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_understanding_run_hashes",
        ),
        CheckConstraint(
            "method IN ('native','ocr','visual_ai','human')", name="ck_understanding_run_method"
        ),
        CheckConstraint(
            "outcome IN ('succeeded','failed','unknown') "
            "AND ((outcome='succeeded' AND failure_code IS NULL) "
            "OR (outcome<>'succeeded' AND failure_code IS NOT NULL))",
            name="ck_understanding_run_outcome",
        ),
        CheckConstraint(
            "jsonb_typeof(image_metadata)='object' AND octet_length(image_metadata::text)<=65536",
            name="ck_understanding_run_image",
        ),
        CheckConstraint(
            "provider_profile IS NULL OR (jsonb_typeof(provider_profile)='object' "
            "AND octet_length(provider_profile::text)<=16384)",
            name="ck_understanding_run_profile",
        ),
        CheckConstraint(
            "budget IS NULL OR (jsonb_typeof(budget)='object' "
            "AND octet_length(budget::text)<=4096)",
            name="ck_understanding_run_budget",
        ),
        CheckConstraint(
            "accounting IS NULL OR (jsonb_typeof(accounting)='object' "
            "AND octet_length(accounting::text)<=4096)",
            name="ck_understanding_run_accounting",
        ),
        CheckConstraint(
            "method<>'visual_ai' OR (provider_profile IS NOT NULL AND budget IS NOT NULL "
            "AND (outcome<>'succeeded' OR accounting IS NOT NULL))",
            name="ck_understanding_run_visual_lineage",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    provider_profile: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    budget: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    accounting: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )


class ObservationCandidateModel(_Created, Base):
    __tablename__ = "source_understanding_candidates"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_understanding_candidate_run"),
        UniqueConstraint(
            "document_id", "page_number", "revision", name="uq_understanding_candidate_revision"
        ),
        UniqueConstraint(
            "id", "document_id", "page_number", name="uq_understanding_candidate_source"
        ),
        ForeignKeyConstraint(
            ["run_id", "document_id", "page_number", "source_sha256", "image_sha256"],
            [
                "source_understanding_runs.id",
                "source_understanding_runs.document_id",
                "source_understanding_runs.page_number",
                "source_understanding_runs.source_sha256",
                "source_understanding_runs.image_sha256",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_candidate_run",
        ),
        CheckConstraint(
            "page_number > 0 AND revision > 0", name="ck_understanding_candidate_revision"
        ),
        CheckConstraint(
            "method IN ('native','ocr','visual_ai','human')",
            name="ck_understanding_candidate_method",
        ),
        CheckConstraint(
            "jsonb_typeof(observation)='object' AND octet_length(observation::text)<=2097152",
            name="ck_understanding_candidate_observation",
        ),
        CheckConstraint(
            "jsonb_typeof(educational_understanding)='object' "
            "AND octet_length(educational_understanding::text)<=2097152",
            name="ck_understanding_candidate_education",
        ),
        CheckConstraint(
            "jsonb_typeof(uncertainties)='array' AND jsonb_array_length(uncertainties)<=128 "
            "AND octet_length(uncertainties::text)<=1048576",
            name="ck_understanding_candidate_uncertainties",
        ),
        CheckConstraint(
            f"fingerprint=public.source_understanding_fingerprint({_CANDIDATE_SNAPSHOT})",
            name="ck_understanding_candidate_fingerprint",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    observation: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    educational_understanding: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    uncertainties: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class PageRegionModel(Base):
    __tablename__ = "source_understanding_regions"
    __table_args__ = (
        UniqueConstraint("candidate_id", "region_key", name="uq_understanding_region_key"),
        UniqueConstraint(
            "id",
            "candidate_id",
            "document_id",
            "page_number",
            name="uq_understanding_region_source",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_region_candidate",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "parent_key"],
            [
                "source_understanding_regions.candidate_id",
                "source_understanding_regions.region_key",
            ],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            name="fk_understanding_region_parent",
        ),
        CheckConstraint(
            "region_key ~ '^[a-z][a-z0-9_-]{0,63}$' AND reading_order >= 0",
            name="ck_understanding_region_key",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    region_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PageVerificationReportModel(_Created, Base):
    __tablename__ = "source_understanding_reports"
    __table_args__ = (
        UniqueConstraint("candidate_id", "fingerprint", name="uq_understanding_report_fingerprint"),
        UniqueConstraint(
            "id",
            "candidate_id",
            "document_id",
            "page_number",
            name="uq_understanding_report_source",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_report_candidate",
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304",
            name="ck_understanding_report_payload",
        ),
        CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_understanding_report_hash",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class PageVerificationDecisionModel(_Created, Base):
    __tablename__ = "source_understanding_decisions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "candidate_id",
            "document_id",
            "page_number",
            name="uq_understanding_decision_source",
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
            name="fk_understanding_decision_report",
        ),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536",
            name="ck_understanding_decision_payload",
        ),
        CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_understanding_decision_hash",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    report_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class TrustedPageKnowledgeModel(_Created, Base):
    __tablename__ = "trusted_page_knowledge"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", "revision", name="uq_trusted_page_revision"),
        UniqueConstraint(
            "id", "candidate_id", "document_id", "page_number", name="uq_trusted_page_source"
        ),
        ForeignKeyConstraint(
            ["id", "candidate_id", "document_id", "page_number"],
            [
                "source_understanding_decisions.id",
                "source_understanding_decisions.candidate_id",
                "source_understanding_decisions.document_id",
                "source_understanding_decisions.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_trusted_page_decision",
        ),
        CheckConstraint("revision > 0 AND page_number > 0", name="ck_trusted_page_revision"),
        CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304",
            name="ck_trusted_page_payload",
        ),
        CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_trusted_page_hash",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class PageUnderstandingStateModel(Base):
    __tablename__ = "source_understanding_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_page_candidate",
        ),
        ForeignKeyConstraint(
            ["current_report_id", "current_candidate_id", "document_id", "page_number"],
            [
                "source_understanding_reports.id",
                "source_understanding_reports.candidate_id",
                "source_understanding_reports.document_id",
                "source_understanding_reports.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_page_report",
        ),
        ForeignKeyConstraint(
            ["current_trusted_id", "current_candidate_id", "document_id", "page_number"],
            [
                "trusted_page_knowledge.id",
                "trusted_page_knowledge.candidate_id",
                "trusted_page_knowledge.document_id",
                "trusted_page_knowledge.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_page_trusted",
        ),
        CheckConstraint(
            "page_number > 0 AND version >= 0 "
            "AND candidate_revision >= 0 AND trusted_revision >= 0",
            name="ck_understanding_page_versions",
        ),
        CheckConstraint(
            "state IN ('unprocessed','observed','processing','needs_human_review',"
            "'needs_reprocessing','corrected','verified','excluded')",
            name="ck_understanding_page_state",
        ),
        CheckConstraint(
            "(version=0 AND state='unprocessed' AND current_candidate_id IS NULL "
            "AND current_report_id IS NULL AND current_trusted_id IS NULL AND event_id IS NULL) "
            "OR (version>0 AND event_id IS NOT NULL)",
            name="ck_understanding_page_event",
        ),
        CheckConstraint(
            "(state='verified')=(current_trusted_id IS NOT NULL)",
            name="ck_understanding_page_trust",
        ),
        ForeignKeyConstraint(
            ["active_job_id", "document_id", "page_number"],
            [
                "source_understanding_jobs.id",
                "source_understanding_jobs.document_id",
                "source_understanding_jobs.page_number",
            ],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            name="fk_understanding_page_job",
        ),
        CheckConstraint(
            "(state='processing')=(active_job_id IS NOT NULL)", name="ck_understanding_page_job"
        ),
        Index("ix_understanding_page_state", "document_id", "state", "page_number"),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    trusted_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="unprocessed")
    current_candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    current_report_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    current_trusted_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    active_job_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
        ),
        nullable=True,
    )
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentUnderstandingJobModel(_Created, Base):
    __tablename__ = "source_understanding_jobs"
    __table_args__ = (
        UniqueConstraint("created_by", "request_id", name="uq_understanding_job_request"),
        UniqueConstraint("id", "document_id", "page_number", name="uq_understanding_job_source"),
        ForeignKeyConstraint(
            ["candidate_id", "document_id", "page_number"],
            [
                "source_understanding_candidates.id",
                "source_understanding_candidates.document_id",
                "source_understanding_candidates.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_understanding_job_candidate",
        ),
        CheckConstraint(
            "page_number>0 AND expected_page_version>0 AND version>=0",
            name="ck_understanding_job_versions",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 3 AND retry_depth BETWEEN 0 AND 3",
            name="ck_understanding_job_retries",
        ),
        CheckConstraint(
            "(retry_of_job_id IS NULL)=(retry_depth=0)", name="ck_understanding_job_retry_parent"
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_understanding_job_hashes",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','unknown')",
            name="ck_understanding_job_status",
        ),
        CheckConstraint(
            "(status='running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status<>'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_understanding_job_lease",
        ),
        CheckConstraint(
            f"request_fingerprint=public.source_understanding_fingerprint({_JOB_REQUEST_SNAPSHOT})",
            name="ck_understanding_job_request_hash",
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','unknown'))=(completed_at IS NOT NULL)",
            name="ck_understanding_job_completed",
        ),
        CheckConstraint(
            "(status IN ('failed','unknown'))=(failure_code IS NOT NULL)",
            name="ck_understanding_job_failure",
        ),
        CheckConstraint(
            "(status='succeeded')=(candidate_id IS NOT NULL)", name="ck_understanding_job_candidate"
        ),
        CheckConstraint(
            "status<>'succeeded' OR (run_id IS NOT NULL AND accounting IS NOT NULL)",
            name="ck_understanding_job_success",
        ),
        CheckConstraint(
            "(provider_started_at IS NULL AND provider_request_key IS NULL) OR "
            "(provider_started_at IS NOT NULL AND image_metadata IS NOT NULL "
            "AND provider_request_key IS NOT NULL AND provider_request_key ~ '^[0-9a-f]{64}$')",
            name="ck_understanding_job_dispatch",
        ),
        CheckConstraint(
            "accounting IS NULL OR provider_started_at IS NOT NULL",
            name="ck_understanding_job_accounted",
        ),
        CheckConstraint(
            "jsonb_typeof(profile)='object' AND octet_length(profile::text)<=16384",
            name="ck_understanding_job_profile",
        ),
        CheckConstraint(
            "jsonb_typeof(budget)='object' AND octet_length(budget::text)<=4096",
            name="ck_understanding_job_budget",
        ),
        CheckConstraint(
            "image_metadata IS NULL OR (jsonb_typeof(image_metadata)='object' "
            "AND octet_length(image_metadata::text)<=65536)",
            name="ck_understanding_job_image",
        ),
        CheckConstraint(
            "accounting IS NULL OR (jsonb_typeof(accounting)='object' "
            "AND octet_length(accounting::text)<=4096)",
            name="ck_understanding_job_accounting",
        ),
        CheckConstraint(
            "char_length(reason) BETWEEN 1 AND 2000 AND reason=btrim(reason) "
            "AND reason !~ '^[[:space:]]*$|[[:cntrl:]]'",
            name="ck_understanding_job_reason",
        ),
        Index("ix_understanding_job_recovery", "status", "lease_expires_at", "created_at"),
        Index(
            "ix_understanding_job_page_history",
            "document_id",
            "page_number",
            "expected_page_version",
        ),
        Index(
            "uq_understanding_job_active_page",
            "document_id",
            "page_number",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_page_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    budget: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retry_of_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_understanding_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    retry_depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_request_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    accounting: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_understanding_runs.id", ondelete="RESTRICT"), nullable=True
    )
    candidate_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
