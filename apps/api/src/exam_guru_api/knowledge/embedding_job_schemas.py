from datetime import datetime
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH

MAX_EMBEDDING_JOB_RECORDS = 100


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmbeddingJobCreateRequest(_StrictModel):
    """Record selection only; source text, vectors, and configuration remain server-owned."""

    historical_question_ids: tuple[UUID, ...] = Field(
        default=(),
        max_length=MAX_EMBEDDING_JOB_RECORDS,
    )
    knowledge_chunk_ids: tuple[UUID, ...] = Field(
        default=(),
        max_length=MAX_EMBEDDING_JOB_RECORDS,
    )

    @model_validator(mode="after")
    def validate_record_ids(self) -> Self:
        total = len(self.historical_question_ids) + len(self.knowledge_chunk_ids)
        if not 1 <= total <= MAX_EMBEDDING_JOB_RECORDS:
            raise ValueError("embedding job must contain between 1 and 100 records")
        if len(set(self.historical_question_ids)) != len(self.historical_question_ids):
            raise ValueError("historical question identifiers must be unique")
        if len(set(self.knowledge_chunk_ids)) != len(self.knowledge_chunk_ids):
            raise ValueError("knowledge chunk identifiers must be unique")
        return self


class EmbeddingConfigurationResponse(_FrozenStrictModel):
    provider: str
    model: str
    dimension: int
    version: str
    config_fingerprint: str


class EmbeddingJobCountsResponse(_FrozenStrictModel):
    requested: int
    embedded: int
    deduplicated: int


class EmbeddingJobResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    retry_of_job_id: UUID | None
    retry_depth: Annotated[int, Field(ge=0, le=MAX_PROVIDER_JOB_RETRY_DEPTH)]
    historical_question_ids: tuple[UUID, ...]
    knowledge_chunk_ids: tuple[UUID, ...]
    configuration: EmbeddingConfigurationResponse
    status: Literal["queued", "claimed", "succeeded", "failed"]
    version: int
    queue_message_id: str | None
    counts: EmbeddingJobCountsResponse
    failure_code: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    deduplicated: bool = False

    @classmethod
    def from_model(cls, value: object, *, deduplicated: bool = False) -> Self:
        from exam_guru_api.knowledge.models import EmbeddingJobModel

        if not isinstance(value, EmbeddingJobModel):
            raise TypeError("value must be EmbeddingJobModel")
        return cls(
            id=value.id,
            curriculum_version_id=value.curriculum_version_id,
            retry_of_job_id=value.retry_of_job_id,
            retry_depth=value.retry_depth,
            historical_question_ids=tuple(UUID(item) for item in value.historical_question_ids),
            knowledge_chunk_ids=tuple(UUID(item) for item in value.knowledge_chunk_ids),
            configuration=EmbeddingConfigurationResponse(
                provider=value.provider,
                model=value.model,
                dimension=value.dimension,
                version=value.embedding_version,
                config_fingerprint=value.config_fingerprint,
            ),
            status=cast(
                Literal["queued", "claimed", "succeeded", "failed"],
                value.status,
            ),
            version=value.version,
            queue_message_id=value.queue_message_id,
            counts=EmbeddingJobCountsResponse(
                requested=value.requested_count,
                embedded=value.embedded_count,
                deduplicated=value.deduplicated_count,
            ),
            failure_code=value.failure_code,
            created_by=value.created_by,
            created_at=value.created_at,
            updated_at=value.updated_at,
            claimed_at=value.claimed_at,
            completed_at=value.completed_at,
            deduplicated=deduplicated,
        )
