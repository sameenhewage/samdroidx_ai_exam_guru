from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.curriculum.models import AuditColumns
from exam_guru_api.infrastructure.database import Base
from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    Provenance,
    QuestionType,
    ReviewState,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig

_REVIEW_STATES_SQL = ", ".join(f"'{state.value}'" for state in ReviewState)
_QUESTION_TYPES_SQL = ", ".join(f"'{question_type.value}'" for question_type in QuestionType)
_CHUNK_TYPES_SQL = ", ".join(f"'{chunk_type.value}'" for chunk_type in ChunkType)


def _enum(enum_type: type[Any], *, name: str, length: int) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        values_callable=lambda values: [value.value for value in values],
        length=length,
    )


class HistoricalQuestionModel(AuditColumns, Base):
    __tablename__ = "historical_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_document_id", "page_number"],
            ["source_pages.source_document_id", "source_pages.page_number"],
            name="fk_historical_questions_source_page",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["competency_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_competency_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_skill_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sub_skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_sub_skill_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["learning_concept_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_learning_concept_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_document_id",
            "question_number",
            name="uq_historical_questions_source_question",
        ),
        CheckConstraint(
            f"question_type IN ({_QUESTION_TYPES_SQL})",
            name="ck_historical_questions_question_type",
        ),
        CheckConstraint(
            f"review_state IN ({_REVIEW_STATES_SQL})",
            name="ck_historical_questions_review_state",
        ),
        CheckConstraint(
            "year BETWEEN 1900 AND 2100",
            name="ck_historical_questions_year",
        ),
        CheckConstraint(
            "paper_code = btrim(paper_code) AND length(paper_code) > 0",
            name="ck_historical_questions_paper_code",
        ),
        CheckConstraint(
            "question_number = btrim(question_number) AND length(question_number) > 0",
            name="ck_historical_questions_question_number",
        ),
        CheckConstraint(
            "length(btrim(text)) > 0",
            name="ck_historical_questions_text",
        ),
        CheckConstraint("marks > 0", name="ck_historical_questions_marks"),
        CheckConstraint("page_number > 0", name="ck_historical_questions_page_number"),
        CheckConstraint(
            "review_state <> 'reviewed' OR "
            "(source_block_id IS NOT NULL AND competency_id IS NOT NULL)",
            name="ck_historical_questions_reviewed_references",
        ),
        Index(
            "ix_historical_questions_curriculum_review",
            "curriculum_version_id",
            "review_state",
        ),
        Index("ix_historical_questions_competency", "competency_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_code: Mapped[str] = mapped_column(String(64), nullable=False)
    question_number: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[QuestionType] = mapped_column(
        _enum(QuestionType, name="knowledge_question_type", length=32),
        nullable=False,
    )
    marks: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_block_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("extracted_blocks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    review_state: Mapped[ReviewState] = mapped_column(
        _enum(ReviewState, name="knowledge_review_state", length=32),
        nullable=False,
        default=ReviewState.DRAFT,
        server_default=ReviewState.DRAFT.value,
    )
    competency_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sub_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    learning_concept_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    @classmethod
    def from_domain(cls, question: HistoricalQuestion, actor_id: UUID) -> Self:
        return cls(
            id=question.id,
            curriculum_version_id=question.curriculum_version_id,
            year=question.year,
            paper_code=question.paper_code,
            question_number=question.question_number,
            text=question.text,
            question_type=question.question_type,
            marks=question.marks,
            source_document_id=question.provenance.source_document_id,
            page_number=question.provenance.page_number,
            source_block_id=question.provenance.source_block_id,
            review_state=question.review_state,
            competency_id=question.competency_id,
            skill_id=question.skill_id,
            sub_skill_id=question.sub_skill_id,
            learning_concept_id=question.learning_concept_id,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def to_domain(self) -> HistoricalQuestion:
        return HistoricalQuestion(
            id=self.id,
            curriculum_version_id=self.curriculum_version_id,
            year=self.year,
            paper_code=self.paper_code,
            question_number=self.question_number,
            text=self.text,
            question_type=self.question_type,
            marks=self.marks,
            provenance=Provenance(
                source_document_id=self.source_document_id,
                page_number=self.page_number,
                source_block_id=self.source_block_id,
            ),
            review_state=self.review_state,
            competency_id=self.competency_id,
            skill_id=self.skill_id,
            sub_skill_id=self.sub_skill_id,
            learning_concept_id=self.learning_concept_id,
        )


class KnowledgeChunkModel(AuditColumns, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_document_id", "page_number"],
            ["source_pages.source_document_id", "source_pages.page_number"],
            name="fk_knowledge_chunks_source_page",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["competency_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_competency_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_skill_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sub_skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_sub_skill_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["learning_concept_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_learning_concept_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_document_id",
            "sequence",
            name="uq_knowledge_chunks_source_sequence",
        ),
        CheckConstraint(
            f"chunk_type IN ({_CHUNK_TYPES_SQL})",
            name="ck_knowledge_chunks_chunk_type",
        ),
        CheckConstraint(
            f"review_state IN ({_REVIEW_STATES_SQL})",
            name="ck_knowledge_chunks_review_state",
        ),
        CheckConstraint("length(btrim(text)) > 0", name="ck_knowledge_chunks_text"),
        CheckConstraint(
            "educational_boundary = btrim(educational_boundary) "
            "AND length(educational_boundary) > 0",
            name="ck_knowledge_chunks_educational_boundary",
        ),
        CheckConstraint("sequence >= 0", name="ck_knowledge_chunks_sequence"),
        CheckConstraint("page_number > 0", name="ck_knowledge_chunks_page_number"),
        CheckConstraint(
            "review_state <> 'reviewed' OR "
            "(source_block_id IS NOT NULL AND competency_id IS NOT NULL)",
            name="ck_knowledge_chunks_reviewed_references",
        ),
        Index(
            "ix_knowledge_chunks_curriculum_review",
            "curriculum_version_id",
            "review_state",
        ),
        Index("ix_knowledge_chunks_competency", "competency_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chunk_type: Mapped[ChunkType] = mapped_column(
        _enum(ChunkType, name="knowledge_chunk_type", length=32),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    educational_boundary: Mapped[str] = mapped_column(String(512), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_block_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("extracted_blocks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    review_state: Mapped[ReviewState] = mapped_column(
        _enum(ReviewState, name="knowledge_review_state", length=32),
        nullable=False,
        default=ReviewState.DRAFT,
        server_default=ReviewState.DRAFT.value,
    )
    competency_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sub_skill_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    learning_concept_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    @classmethod
    def from_domain(cls, chunk: KnowledgeChunk, actor_id: UUID) -> Self:
        return cls(
            id=chunk.id,
            curriculum_version_id=chunk.curriculum_version_id,
            chunk_type=chunk.chunk_type,
            text=chunk.text,
            educational_boundary=chunk.educational_boundary,
            sequence=chunk.sequence,
            source_document_id=chunk.provenance.source_document_id,
            page_number=chunk.provenance.page_number,
            source_block_id=chunk.provenance.source_block_id,
            review_state=chunk.review_state,
            competency_id=chunk.competency_id,
            skill_id=chunk.skill_id,
            sub_skill_id=chunk.sub_skill_id,
            learning_concept_id=chunk.learning_concept_id,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def to_domain(self) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=self.id,
            curriculum_version_id=self.curriculum_version_id,
            chunk_type=self.chunk_type,
            text=self.text,
            educational_boundary=self.educational_boundary,
            sequence=self.sequence,
            provenance=Provenance(
                source_document_id=self.source_document_id,
                page_number=self.page_number,
                source_block_id=self.source_block_id,
            ),
            review_state=self.review_state,
            competency_id=self.competency_id,
            skill_id=self.skill_id,
            sub_skill_id=self.sub_skill_id,
            learning_concept_id=self.learning_concept_id,
        )


class EmbeddingConfigurationModel(AuditColumns, Base):
    __tablename__ = "embedding_configurations"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "version",
            "config_fingerprint",
            name="uq_embedding_configurations_space",
        ),
        UniqueConstraint(
            "id",
            "dimension",
            name="uq_embedding_configurations_id_dimension",
        ),
        CheckConstraint(
            "provider = btrim(provider) AND length(provider) > 0",
            name="ck_embedding_configurations_provider",
        ),
        CheckConstraint(
            "model = btrim(model) AND length(model) > 0",
            name="ck_embedding_configurations_model",
        ),
        CheckConstraint(
            "version = btrim(version) AND length(version) > 0",
            name="ck_embedding_configurations_version",
        ),
        CheckConstraint(
            "config_fingerprint = btrim(config_fingerprint) AND length(config_fingerprint) > 0",
            name="ck_embedding_configurations_fingerprint",
        ),
        CheckConstraint(
            "dimension BETWEEN 1 AND 4096",
            name="ck_embedding_configurations_dimension",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)

    @classmethod
    def from_domain(
        cls,
        configuration_id: UUID,
        config: EmbeddingConfig,
        actor_id: UUID,
    ) -> Self:
        return cls(
            id=configuration_id,
            provider=config.provider,
            model=config.model,
            dimension=config.dimension,
            version=config.version,
            config_fingerprint=config.config_fingerprint,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def to_domain(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            provider=self.provider,
            model=self.model,
            dimension=self.dimension,
            version=self.version,
            config_fingerprint=self.config_fingerprint,
        )


class KnowledgeEmbeddingModel(Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["embedding_configuration_id", "embedding_dimension"],
            ["embedding_configurations.id", "embedding_configurations.dimension"],
            name="fk_knowledge_embeddings_configuration_dimension",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(historical_question_id IS NOT NULL AND knowledge_chunk_id IS NULL) OR "
            "(historical_question_id IS NULL AND knowledge_chunk_id IS NOT NULL)",
            name="ck_knowledge_embeddings_single_target",
        ),
        CheckConstraint(
            "embedding_dimension BETWEEN 1 AND 4096",
            name="ck_knowledge_embeddings_dimension",
        ),
        CheckConstraint(
            "vector_dims(embedding) = embedding_dimension",
            name="ck_knowledge_embeddings_vector_dimension",
        ),
        CheckConstraint(
            "source_text_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_embeddings_source_text_sha256",
        ),
        Index(
            "uq_knowledge_embeddings_question_configuration",
            "historical_question_id",
            "embedding_configuration_id",
            unique=True,
            postgresql_where=text("historical_question_id IS NOT NULL"),
        ),
        Index(
            "uq_knowledge_embeddings_chunk_configuration",
            "knowledge_chunk_id",
            "embedding_configuration_id",
            unique=True,
            postgresql_where=text("knowledge_chunk_id IS NOT NULL"),
        ),
        Index("ix_knowledge_embeddings_configuration", "embedding_configuration_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    historical_question_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("historical_questions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    knowledge_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    embedding_configuration_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    @property
    def vector(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.embedding)
