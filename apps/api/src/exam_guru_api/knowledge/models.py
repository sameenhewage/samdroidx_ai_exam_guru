from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH
from exam_guru_api.curriculum.models import AuditColumns
from exam_guru_api.infrastructure.database import Base
from exam_guru_api.knowledge.domain import (
    MAX_ANSWER_CHARACTERS,
    MAX_DIFFICULTY_SOURCE_CHARACTERS,
    MAX_MARKING_DATA_BYTES,
    MAX_MARKING_GUIDANCE_CHARACTERS,
    MAX_MEDIA_REFERENCE_CHARACTERS,
    MAX_MEDIA_REFERENCES,
    MAX_QUESTION_ARCHETYPE_CHARACTERS,
    MAX_QUESTION_OPTION_CHARACTERS,
    MAX_QUESTION_OPTIONS,
    MIN_QUESTION_OPTIONS,
    ChunkType,
    DifficultyLabel,
    HistoricalQuestion,
    KnowledgeChunk,
    Provenance,
    QuestionType,
    ReviewState,
    marking_data_to_dict,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig

_REVIEW_STATES_SQL = ", ".join(f"'{state.value}'" for state in ReviewState)
_QUESTION_TYPES_SQL = ", ".join(f"'{question_type.value}'" for question_type in QuestionType)
_DIFFICULTY_LABELS_SQL = ", ".join(f"'{label.value}'" for label in DifficultyLabel)
_CHUNK_TYPES_SQL = ", ".join(f"'{chunk_type.value}'" for chunk_type in ChunkType)
_FINGERPRINT_SQL = "^[s][h][a]256:[0-9a-f]{64}$"


class EmbeddingJobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_EMBEDDING_JOB_STATES_SQL = ", ".join(f"'{state.value}'" for state in EmbeddingJobStatus)


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
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_historical_questions_unit_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_historical_questions_lesson_scope",
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
        CheckConstraint(
            "media_references IS NULL OR historical_question_text_array_valid("
            f"media_references, 1, {MAX_MEDIA_REFERENCES}, "
            f"{MAX_MEDIA_REFERENCE_CHARACTERS})",
            name="ck_historical_questions_metadata_media_references",
        ),
        CheckConstraint(
            "options IS NULL OR historical_question_text_array_valid("
            f"options, {MIN_QUESTION_OPTIONS}, {MAX_QUESTION_OPTIONS}, "
            f"{MAX_QUESTION_OPTION_CHARACTERS})",
            name="ck_historical_questions_metadata_options",
        ),
        CheckConstraint(
            "answer IS NULL OR (answer = btrim(answer) AND "
            f"char_length(answer) BETWEEN 1 AND {MAX_ANSWER_CHARACTERS})",
            name="ck_historical_questions_metadata_answer",
        ),
        CheckConstraint(
            "marking_guidance IS NULL OR (marking_guidance = btrim(marking_guidance) AND "
            "char_length(marking_guidance) BETWEEN 1 AND "
            f"{MAX_MARKING_GUIDANCE_CHARACTERS})",
            name="ck_historical_questions_metadata_marking_guidance",
        ),
        CheckConstraint(
            "marking_data IS NULL OR (jsonb_typeof(marking_data) = 'object' AND "
            "marking_data <> '{}'::jsonb AND "
            f"pg_column_size(marking_data) <= {MAX_MARKING_DATA_BYTES})",
            name="ck_historical_questions_metadata_marking_data",
        ),
        CheckConstraint(
            "question_archetype IS NULL OR (question_archetype = btrim(question_archetype) AND "
            f"char_length(question_archetype) BETWEEN 1 AND {MAX_QUESTION_ARCHETYPE_CHARACTERS})",
            name="ck_historical_questions_metadata_question_archetype",
        ),
        CheckConstraint(
            "(difficulty_label IS NULL AND difficulty_confidence IS NULL AND "
            "difficulty_source IS NULL) OR (difficulty_label IS NOT NULL AND "
            "difficulty_confidence IS NOT NULL AND difficulty_source IS NOT NULL AND "
            f"difficulty_label IN ({_DIFFICULTY_LABELS_SQL}) AND "
            "difficulty_source = btrim(difficulty_source) AND "
            f"char_length(difficulty_source) BETWEEN 1 AND {MAX_DIFFICULTY_SOURCE_CHARACTERS})",
            name="ck_historical_questions_metadata_difficulty_evidence",
        ),
        CheckConstraint(
            "difficulty_confidence IS NULL OR (difficulty_confidence BETWEEN 0.0 AND 1.0 AND "
            "difficulty_confidence NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision))",
            name="ck_historical_questions_metadata_difficulty_confidence",
        ),
        CheckConstraint("page_number > 0", name="ck_historical_questions_page_number"),
        CheckConstraint(
            "lesson_id IS NULL OR unit_id IS NOT NULL",
            name="ck_historical_questions_lesson_requires_unit",
        ),
        CheckConstraint("version >= 0", name="ck_historical_questions_version"),
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
        Index(
            "ix_historical_questions_learning_scope",
            "curriculum_version_id",
            "unit_id",
            "lesson_id",
        ),
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
    media_references: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    options: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    marking_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    marking_data: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    question_archetype: Mapped[str | None] = mapped_column(String(128), nullable=True)
    difficulty_label: Mapped[DifficultyLabel | None] = mapped_column(
        _enum(
            DifficultyLabel,
            name="historical_question_difficulty_label",
            length=16,
        ),
        nullable=True,
    )
    difficulty_confidence: Mapped[float | None] = mapped_column(Double, nullable=True)
    difficulty_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    unit_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lesson_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
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
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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
            media_references=(
                list(question.media_references) if question.media_references is not None else None
            ),
            options=list(question.options) if question.options is not None else None,
            answer=question.answer,
            marking_guidance=question.marking_guidance,
            marking_data=marking_data_to_dict(question.marking_data),
            question_archetype=question.question_archetype,
            difficulty_label=question.difficulty_label,
            difficulty_confidence=question.difficulty_confidence,
            difficulty_source=question.difficulty_source,
            source_document_id=question.provenance.source_document_id,
            unit_id=question.unit_id,
            lesson_id=question.lesson_id,
            page_number=question.provenance.page_number,
            source_block_id=question.provenance.source_block_id,
            review_state=question.review_state,
            competency_id=question.competency_id,
            skill_id=question.skill_id,
            sub_skill_id=question.sub_skill_id,
            learning_concept_id=question.learning_concept_id,
            version=question.version,
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
            media_references=(
                tuple(self.media_references) if self.media_references is not None else None
            ),
            options=tuple(self.options) if self.options is not None else None,
            answer=self.answer,
            marking_guidance=self.marking_guidance,
            marking_data=self.marking_data,
            question_archetype=self.question_archetype,
            difficulty_label=self.difficulty_label,
            difficulty_confidence=self.difficulty_confidence,
            difficulty_source=self.difficulty_source,
            provenance=Provenance(
                source_document_id=self.source_document_id,
                page_number=self.page_number,
                source_block_id=self.source_block_id,
            ),
            unit_id=self.unit_id,
            lesson_id=self.lesson_id,
            review_state=self.review_state,
            competency_id=self.competency_id,
            skill_id=self.skill_id,
            sub_skill_id=self.sub_skill_id,
            learning_concept_id=self.learning_concept_id,
            version=self.version,
            created_at=getattr(self, "created_at", None),
            updated_at=getattr(self, "updated_at", None),
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
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_knowledge_chunks_unit_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_knowledge_chunks_lesson_scope",
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
            "lesson_id IS NULL OR unit_id IS NOT NULL",
            name="ck_knowledge_chunks_lesson_requires_unit",
        ),
        CheckConstraint("version >= 0", name="ck_knowledge_chunks_version"),
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
        Index(
            "ix_knowledge_chunks_learning_scope",
            "curriculum_version_id",
            "unit_id",
            "lesson_id",
        ),
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
    unit_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lesson_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
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
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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
            unit_id=chunk.unit_id,
            lesson_id=chunk.lesson_id,
            page_number=chunk.provenance.page_number,
            source_block_id=chunk.provenance.source_block_id,
            review_state=chunk.review_state,
            competency_id=chunk.competency_id,
            skill_id=chunk.skill_id,
            sub_skill_id=chunk.sub_skill_id,
            learning_concept_id=chunk.learning_concept_id,
            version=chunk.version,
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
            unit_id=self.unit_id,
            lesson_id=self.lesson_id,
            review_state=self.review_state,
            competency_id=self.competency_id,
            skill_id=self.skill_id,
            sub_skill_id=self.sub_skill_id,
            learning_concept_id=self.learning_concept_id,
            version=self.version,
            created_at=getattr(self, "created_at", None),
            updated_at=getattr(self, "updated_at", None),
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


class EmbeddingJobModel(Base):
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["retry_of_job_id", "curriculum_version_id"],
            ["embedding_jobs.id", "embedding_jobs.curriculum_version_id"],
            name="fk_embedding_jobs_retry_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_embedding_jobs_id_curriculum",
        ),
        UniqueConstraint(
            "created_by",
            "idempotency_key_hash",
            name="uq_embedding_jobs_actor_idempotency",
        ),
        CheckConstraint(
            "retry_of_job_id IS NULL OR retry_of_job_id <> id",
            name="ck_embedding_jobs_retry_not_self",
        ),
        CheckConstraint(
            f"retry_depth BETWEEN 0 AND {MAX_PROVIDER_JOB_RETRY_DEPTH}",
            name="ck_embedding_jobs_retry_depth",
        ),
        CheckConstraint(
            "embedding_job_uuid_array_valid(historical_question_ids, 100) AND "
            "embedding_job_uuid_array_valid(knowledge_chunk_ids, 100) AND "
            "jsonb_array_length(historical_question_ids) + "
            "jsonb_array_length(knowledge_chunk_ids) BETWEEN 1 AND 100",
            name="ck_embedding_jobs_record_ids",
        ),
        CheckConstraint(
            f"idempotency_key_hash ~ '{_FINGERPRINT_SQL}' AND "
            f"request_fingerprint ~ '{_FINGERPRINT_SQL}' AND "
            f"source_fingerprint ~ '{_FINGERPRINT_SQL}'",
            name="ck_embedding_jobs_fingerprints",
        ),
        *(
            CheckConstraint(
                f"{column_name} = btrim({column_name}) AND "
                f"char_length({column_name}) BETWEEN 1 AND {maximum} AND "
                f"{column_name} !~ '[[:space:][:cntrl:]]'",
                name=f"ck_embedding_jobs_{column_name}",
            )
            for column_name, maximum in (
                ("provider", 64),
                ("model", 128),
                ("embedding_version", 64),
                ("config_fingerprint", 128),
            )
        ),
        CheckConstraint("dimension BETWEEN 1 AND 4096", name="ck_embedding_jobs_dimension"),
        CheckConstraint(
            f"status IN ({_EMBEDDING_JOB_STATES_SQL}) AND version >= 0",
            name="ck_embedding_jobs_status_version",
        ),
        CheckConstraint(
            "queue_message_id IS NULL OR (queue_message_id = btrim(queue_message_id) AND "
            "char_length(queue_message_id) BETWEEN 1 AND 128 AND "
            "queue_message_id !~ '[[:space:][:cntrl:]]')",
            name="ck_embedding_jobs_queue_message_id",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_embedding_jobs_failure_code",
        ),
        CheckConstraint(
            "requested_count = jsonb_array_length(historical_question_ids) + "
            "jsonb_array_length(knowledge_chunk_ids) AND "
            "requested_count BETWEEN 1 AND 100 AND "
            "embedded_count BETWEEN 0 AND requested_count AND "
            "deduplicated_count BETWEEN 0 AND requested_count AND "
            "embedded_count + deduplicated_count <= requested_count",
            name="ck_embedding_jobs_counts",
        ),
        CheckConstraint(
            "updated_at >= created_at AND "
            "(claimed_at IS NULL OR claimed_at >= created_at) AND "
            "(completed_at IS NULL OR (claimed_at IS NOT NULL AND completed_at >= claimed_at))",
            name="ck_embedding_jobs_timestamps",
        ),
        CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND completed_at IS NULL AND "
            "failure_code IS NULL AND embedded_count = 0 AND deduplicated_count = 0) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND completed_at IS NULL AND "
            "failure_code IS NULL) OR "
            "(status = 'succeeded' AND claimed_at IS NOT NULL AND completed_at IS NOT NULL AND "
            "failure_code IS NULL AND embedded_count + deduplicated_count = requested_count) OR "
            "(status = 'failed' AND claimed_at IS NOT NULL AND completed_at IS NOT NULL AND "
            "failure_code IS NOT NULL)",
            name="ck_embedding_jobs_state_data",
        ),
        Index(
            "ix_embedding_jobs_curriculum_created",
            "curriculum_version_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_embedding_jobs_curriculum_status_created",
            "curriculum_version_id",
            "status",
            "created_at",
            "id",
        ),
        Index("ix_embedding_jobs_status_created", "status", "created_at", "id"),
        Index(
            "uq_embedding_jobs_retry_of",
            "retry_of_job_id",
            unique=True,
            postgresql_where=text("retry_of_job_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "curriculum_versions.id",
            name="fk_embedding_jobs_curriculum_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    retry_of_job_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    retry_depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    historical_question_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    knowledge_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    queue_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedded_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    deduplicated_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def embedding_config(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            provider=self.provider,
            model=self.model,
            dimension=self.dimension,
            version=self.embedding_version,
            config_fingerprint=self.config_fingerprint,
        )
