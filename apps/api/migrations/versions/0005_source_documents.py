from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_source_documents"
down_revision: str | None = "0004_taxonomy_review_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    document_type = sa.Enum(
        "syllabus",
        "teacher_guide",
        "past_paper",
        "marking_scheme",
        "evaluation_report",
        "other_approved",
        name="source_document_type",
        native_enum=False,
        create_constraint=True,
        length=32,
    )
    extraction_status = sa.Enum(
        "uploaded",
        "extraction_pending",
        "extracted",
        "in_review",
        "trusted",
        "failed",
        name="extraction_status",
        native_enum=False,
        create_constraint=True,
        length=32,
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("document_type", document_type, nullable=False),
        sa.Column(
            "extraction_status",
            extraction_status,
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("paper_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("size_bytes > 0", name="ck_source_document_positive_size"),
        sa.CheckConstraint(
            "year IS NULL OR year BETWEEN 1900 AND 2100",
            name="ck_source_document_year",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_source_documents_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("checksum_sha256", name="uq_source_documents_checksum"),
        sa.UniqueConstraint("object_key", name="uq_source_documents_object_key"),
    )
    op.create_index(
        "ix_source_documents_status",
        "source_documents",
        ["extraction_status"],
    )
    op.create_index(
        "ix_source_documents_curriculum",
        "source_documents",
        ["curriculum_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_documents_curriculum", table_name="source_documents")
    op.drop_index("ix_source_documents_status", table_name="source_documents")
    op.drop_table("source_documents")
