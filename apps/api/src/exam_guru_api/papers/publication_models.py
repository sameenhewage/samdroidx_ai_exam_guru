from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base
from exam_guru_api.papers.domain import (
    MAX_CANDIDATE_REVISIONS,
    MAX_CANDIDATE_VERSION,
    MAX_PAPER_VERSIONS,
)

MAX_PAPER_TITLE_CHARACTERS = 512
MAX_PAPER_ARCHIVE_REASON_CHARACTERS = 1_024
MAX_PUBLISHED_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_PAPER_SLOTS = 200


class PracticePaperModel(Base):
    __tablename__ = "practice_papers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_practice_papers_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_practice_papers_id_curriculum",
        ),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_practice_papers_idempotency_key_hash",
        ),
        CheckConstraint(
            f"state IN ('draft', 'published', 'archived') AND current_version BETWEEN 1 AND "
            f"{MAX_PAPER_VERSIONS}",
            name="ck_practice_papers_state_version",
        ),
        CheckConstraint(
            "blueprint_id = btrim(blueprint_id) AND length(blueprint_id) BETWEEN 1 AND 128 "
            "AND blueprint_version = btrim(blueprint_version) "
            "AND length(blueprint_version) BETWEEN 1 AND 128",
            name="ck_practice_papers_blueprint_identity",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^sha256:[0-9a-f]{64}$' "
            "AND create_request_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_practice_papers_fingerprints",
        ),
        Index(
            "ix_practice_papers_curriculum_state_updated",
            "curriculum_version_id",
            "state",
            "updated_at",
            "id",
        ),
        Index(
            "ix_practice_papers_curriculum_blueprint_updated",
            "curriculum_version_id",
            "paper_blueprint_id",
            "updated_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    paper_blueprint_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    blueprint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    blueprint_version: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    create_request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperDraftVersionModel(Base):
    __tablename__ = "paper_draft_versions"
    __table_args__ = (
        PrimaryKeyConstraint("paper_id", "version", name="pk_paper_draft_versions"),
        UniqueConstraint(
            "paper_id",
            "curriculum_version_id",
            "version",
            name="uq_paper_draft_versions_scope",
        ),
        ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id"],
            ["practice_papers.id", "practice_papers.curriculum_version_id"],
            name="fk_paper_draft_versions_paper_curriculum",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"version BETWEEN 1 AND {MAX_PAPER_VERSIONS}",
            name="ck_paper_draft_versions_version",
        ),
        CheckConstraint(
            f"title = btrim(title) AND char_length(title) BETWEEN 1 AND "
            f"{MAX_PAPER_TITLE_CHARACTERS}",
            name="ck_paper_draft_versions_title",
        ),
        CheckConstraint(
            "supersedes_content_hash IS NULL OR supersedes_content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_paper_draft_versions_supersedes_hash",
        ),
        Index("ix_paper_draft_versions_paper_version", "paper_id", "version"),
    )

    paper_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(MAX_PAPER_TITLE_CHARACTERS), nullable=False)
    supersedes_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperDraftCandidateModel(Base):
    __tablename__ = "paper_draft_candidates"
    __table_args__ = (
        PrimaryKeyConstraint(
            "paper_id", "paper_version", "ordinal", name="pk_paper_draft_candidates"
        ),
        UniqueConstraint(
            "paper_id",
            "paper_version",
            "blueprint_slot_id",
            name="uq_paper_draft_candidates_slot",
        ),
        UniqueConstraint(
            "paper_id",
            "paper_version",
            "candidate_id",
            name="uq_paper_draft_candidates_candidate",
        ),
        ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id", "paper_version"],
            [
                "paper_draft_versions.paper_id",
                "paper_draft_versions.curriculum_version_id",
                "paper_draft_versions.version",
            ],
            name="fk_paper_draft_candidates_draft_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_paper_draft_candidates_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"paper_version BETWEEN 1 AND {MAX_PAPER_VERSIONS} "
            f"AND ordinal BETWEEN 1 AND {MAX_PAPER_SLOTS}",
            name="ck_paper_draft_candidates_paper_bounds",
        ),
        CheckConstraint(
            f"candidate_version BETWEEN 1 AND {MAX_CANDIDATE_VERSION} "
            f"AND candidate_revision BETWEEN 1 AND {MAX_CANDIDATE_REVISIONS}",
            name="ck_paper_draft_candidates_candidate_bounds",
        ),
        CheckConstraint(
            "blueprint_slot_id = btrim(blueprint_slot_id) "
            "AND length(blueprint_slot_id) BETWEEN 1 AND 128",
            name="ck_paper_draft_candidates_slot_id",
        ),
        Index(
            "ix_paper_draft_candidates_candidate",
            "candidate_id",
            "paper_id",
            "paper_version",
        ),
    )

    paper_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    paper_version: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    blueprint_slot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)


class PublishedPaperVersionModel(Base):
    __tablename__ = "published_paper_versions"
    __table_args__ = (
        PrimaryKeyConstraint("paper_id", "version", name="pk_published_paper_versions"),
        UniqueConstraint(
            "paper_id",
            "curriculum_version_id",
            "version",
            name="uq_published_paper_versions_scope",
        ),
        ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id", "version"],
            [
                "paper_draft_versions.paper_id",
                "paper_draft_versions.curriculum_version_id",
                "paper_draft_versions.version",
            ],
            name="fk_published_paper_versions_draft_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"version BETWEEN 1 AND {MAX_PAPER_VERSIONS}",
            name="ck_published_paper_versions_version",
        ),
        CheckConstraint(
            "(version = 1 AND previous_version IS NULL AND supersedes_content_hash IS NULL) "
            "OR (version > 1 AND previous_version = version - 1 "
            "AND supersedes_content_hash IS NOT NULL)",
            name="ck_published_paper_versions_chain",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' AND (supersedes_content_hash IS NULL "
            "OR supersedes_content_hash ~ '^[0-9a-f]{64}$')",
            name="ck_published_paper_versions_hashes",
        ),
        CheckConstraint(
            f"jsonb_typeof(snapshot) = 'object' AND pg_column_size(snapshot) <= "
            f"{MAX_PUBLISHED_SNAPSHOT_BYTES} AND "
            "octet_length(paper_canonical_jsonb(snapshot)) <= "
            f"{MAX_PUBLISHED_SNAPSHOT_BYTES}",
            name="ck_published_paper_versions_snapshot_bound",
        ),
        Index("ix_published_paper_versions_paper_version", "paper_id", "version"),
        Index(
            "ix_published_paper_versions_curriculum_published",
            "curriculum_version_id",
            "published_at",
            "paper_id",
            "version",
        ),
    )

    paper_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supersedes_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperArchiveEventModel(Base):
    __tablename__ = "paper_archive_events"
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "curriculum_version_id",
            "version",
            name="uq_paper_archive_events_scope",
        ),
        ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id", "version"],
            [
                "published_paper_versions.paper_id",
                "published_paper_versions.curriculum_version_id",
                "published_paper_versions.version",
            ],
            name="fk_paper_archive_events_publication_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"version BETWEEN 1 AND {MAX_PAPER_VERSIONS}",
            name="ck_paper_archive_events_version",
        ),
        CheckConstraint(
            f"reason = btrim(reason) AND char_length(reason) BETWEEN 1 AND "
            f"{MAX_PAPER_ARCHIVE_REASON_CHARACTERS}",
            name="ck_paper_archive_events_reason",
        ),
        Index(
            "ix_paper_archive_events_curriculum_archived",
            "curriculum_version_id",
            "archived_at",
            "paper_id",
        ),
    )

    paper_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(MAX_PAPER_ARCHIVE_REASON_CHARACTERS), nullable=False)
    archived_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
