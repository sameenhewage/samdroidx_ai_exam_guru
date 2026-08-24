from datetime import datetime
from typing import Annotated, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from exam_guru_api.knowledge.domain import (
    ChunkType,
    EmbeddingConfigurationMetadata,
    EmbeddingStatus,
    HistoricalQuestion,
    KnowledgeChunk,
    QuestionType,
    ReviewState,
)

TrimmedCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
TrimmedBoundary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class HistoricalQuestionImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=1900, le=2100)
    paper_code: TrimmedCode
    question_number: TrimmedCode
    text: str = Field(min_length=1, max_length=1_000_000)
    question_type: QuestionType
    marks: int = Field(ge=1, le=1_000)
    source_document_id: UUID
    page_number: int = Field(ge=1, le=1_000_000)
    source_block_id: UUID | None = None


class KnowledgeChunkImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_type: ChunkType
    text: str = Field(min_length=1, max_length=1_000_000)
    educational_boundary: TrimmedBoundary
    sequence: int = Field(ge=0, le=2_147_483_647)
    source_document_id: UUID
    page_number: int = Field(ge=1, le=1_000_000)
    source_block_id: UUID | None = None


class KnowledgeClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competency_id: UUID | None = None
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None
    expected_version: int = Field(ge=0, le=2_147_483_647)


class KnowledgeReviewTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: ReviewState
    expected_version: int = Field(ge=0, le=2_147_483_647)


class KnowledgeProvenanceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_document_id: UUID
    page_number: int
    source_block_id: UUID | None


class KnowledgeClassificationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    competency_id: UUID | None
    skill_id: UUID | None
    sub_skill_id: UUID | None
    learning_concept_id: UUID | None


class EmbeddingConfigurationMetadataResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    provider: str
    model: str
    dimension: int
    version: str
    config_fingerprint: str

    @classmethod
    def from_domain(
        cls,
        metadata: EmbeddingConfigurationMetadata,
    ) -> Self:
        return cls(
            id=metadata.id,
            provider=metadata.provider,
            model=metadata.model,
            dimension=metadata.dimension,
            version=metadata.version,
            config_fingerprint=metadata.config_fingerprint,
        )


class HistoricalQuestionResponse(BaseModel):
    id: UUID
    curriculum_version_id: UUID
    year: int
    paper_code: str
    question_number: str
    text: str
    question_type: QuestionType
    marks: int
    provenance: KnowledgeProvenanceResponse
    classification: KnowledgeClassificationResponse
    review_state: ReviewState
    version: int
    created_at: datetime
    updated_at: datetime
    embedding_status: EmbeddingStatus
    embedding_configurations: list[EmbeddingConfigurationMetadataResponse]
    deduplicated: bool = False

    @classmethod
    def from_domain(
        cls,
        question: HistoricalQuestion,
        *,
        deduplicated: bool = False,
    ) -> Self:
        return cls(
            id=question.id,
            curriculum_version_id=question.curriculum_version_id,
            year=question.year,
            paper_code=question.paper_code,
            question_number=question.question_number,
            text=question.text,
            question_type=question.question_type,
            marks=question.marks,
            provenance=KnowledgeProvenanceResponse(
                source_document_id=question.provenance.source_document_id,
                page_number=question.provenance.page_number,
                source_block_id=question.provenance.source_block_id,
            ),
            classification=KnowledgeClassificationResponse(
                competency_id=question.competency_id,
                skill_id=question.skill_id,
                sub_skill_id=question.sub_skill_id,
                learning_concept_id=question.learning_concept_id,
            ),
            review_state=question.review_state,
            version=question.version,
            created_at=cast(datetime, question.created_at),
            updated_at=cast(datetime, question.updated_at),
            embedding_status=question.embedding_status,
            embedding_configurations=[
                EmbeddingConfigurationMetadataResponse.from_domain(configuration)
                for configuration in question.embedding_configurations
            ],
            deduplicated=deduplicated,
        )


class KnowledgeChunkResponse(BaseModel):
    id: UUID
    curriculum_version_id: UUID
    chunk_type: ChunkType
    text: str
    educational_boundary: str
    sequence: int
    provenance: KnowledgeProvenanceResponse
    classification: KnowledgeClassificationResponse
    review_state: ReviewState
    version: int
    created_at: datetime
    updated_at: datetime
    embedding_status: EmbeddingStatus
    embedding_configurations: list[EmbeddingConfigurationMetadataResponse]
    deduplicated: bool = False

    @classmethod
    def from_domain(
        cls,
        chunk: KnowledgeChunk,
        *,
        deduplicated: bool = False,
    ) -> Self:
        return cls(
            id=chunk.id,
            curriculum_version_id=chunk.curriculum_version_id,
            chunk_type=chunk.chunk_type,
            text=chunk.text,
            educational_boundary=chunk.educational_boundary,
            sequence=chunk.sequence,
            provenance=KnowledgeProvenanceResponse(
                source_document_id=chunk.provenance.source_document_id,
                page_number=chunk.provenance.page_number,
                source_block_id=chunk.provenance.source_block_id,
            ),
            classification=KnowledgeClassificationResponse(
                competency_id=chunk.competency_id,
                skill_id=chunk.skill_id,
                sub_skill_id=chunk.sub_skill_id,
                learning_concept_id=chunk.learning_concept_id,
            ),
            review_state=chunk.review_state,
            version=chunk.version,
            created_at=cast(datetime, chunk.created_at),
            updated_at=cast(datetime, chunk.updated_at),
            embedding_status=chunk.embedding_status,
            embedding_configurations=[
                EmbeddingConfigurationMetadataResponse.from_domain(configuration)
                for configuration in chunk.embedding_configurations
            ],
            deduplicated=deduplicated,
        )
