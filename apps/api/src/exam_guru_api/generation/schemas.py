from datetime import datetime
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH

MAX_GENERATION_CONTEXT_REFERENCES = 16


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationRunCreateRequest(_StrictModel):
    """Untrusted client selection; all text and generation configuration stay server-owned."""

    paper_blueprint_id: UUID
    slot_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
    knowledge_chunk_ids: Annotated[
        tuple[UUID, ...],
        Field(max_length=MAX_GENERATION_CONTEXT_REFERENCES),
    ] = ()
    historical_question_ids: Annotated[
        tuple[UUID, ...],
        Field(max_length=MAX_GENERATION_CONTEXT_REFERENCES),
    ] = ()

    @model_validator(mode="after")
    def validate_context_references(self) -> Self:
        references = self.context_references
        if not references or len(references) > MAX_GENERATION_CONTEXT_REFERENCES:
            raise ValueError(
                "generation context must contain 1 to "
                f"{MAX_GENERATION_CONTEXT_REFERENCES} references"
            )
        if len(set(self.knowledge_chunk_ids)) != len(self.knowledge_chunk_ids):
            raise ValueError("knowledge chunk identifiers must be unique")
        if len(set(self.historical_question_ids)) != len(self.historical_question_ids):
            raise ValueError("historical question identifiers must be unique")
        return self

    @property
    def context_references(self) -> tuple[tuple[str, UUID], ...]:
        return (
            *(("knowledge_chunk", identifier) for identifier in self.knowledge_chunk_ids),
            *(("historical_question", identifier) for identifier in self.historical_question_ids),
        )


class GenerationJobResponse(_FrozenStrictModel):
    id: UUID
    generation_run_id: UUID
    curriculum_version_id: UUID
    status: Literal["queued", "claimed", "succeeded", "failed"]
    version: int
    queue_message_id: str | None
    failure_code: str | None
    created_by: UUID
    created_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    deduplicated: bool = False

    @classmethod
    def from_model(cls, value: object, *, deduplicated: bool = False) -> Self:
        from .models import GenerationJobModel

        if not isinstance(value, GenerationJobModel):
            raise TypeError("value must be GenerationJobModel")
        return cls(
            id=value.id,
            generation_run_id=value.generation_run_id,
            curriculum_version_id=value.curriculum_version_id,
            status=cast(Literal["queued", "claimed", "succeeded", "failed"], value.status),
            version=value.version,
            queue_message_id=value.queue_message_id,
            failure_code=value.failure_code,
            created_by=value.created_by,
            created_at=value.created_at,
            claimed_at=value.claimed_at,
            completed_at=value.completed_at,
            deduplicated=deduplicated,
        )


class GenerationRunSummaryResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    paper_blueprint_id: UUID
    retry_of_run_id: UUID | None
    retry_depth: Annotated[int, Field(ge=0, le=MAX_PROVIDER_JOB_RETRY_DEPTH)]
    slot_id: str
    request_fingerprint: str
    status: Literal["pending", "running", "succeeded", "failed"]
    version: int
    provider: str
    model: str
    prompt_version: str
    attempt_count: int
    total_tokens: int
    cost_microusd: int
    latency_ms: int
    failure_code: str | None
    disposition: Literal["requires_validation"] | None
    created_by: UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_model(cls, value: object) -> Self:
        from .models import GenerationRunModel

        if not isinstance(value, GenerationRunModel):
            raise TypeError("value must be GenerationRunModel")
        return cls(
            id=value.id,
            curriculum_version_id=value.curriculum_version_id,
            paper_blueprint_id=value.paper_blueprint_id,
            retry_of_run_id=value.retry_of_run_id,
            retry_depth=value.retry_depth,
            slot_id=value.slot_id,
            request_fingerprint=value.request_fingerprint,
            status=cast(Literal["pending", "running", "succeeded", "failed"], value.status),
            version=value.version,
            provider=value.provider,
            model=value.model,
            prompt_version=value.prompt_version,
            attempt_count=value.attempt_count,
            total_tokens=value.total_tokens,
            cost_microusd=value.cost_microusd,
            latency_ms=value.latency_ms,
            failure_code=value.failure_code,
            disposition=cast(Literal["requires_validation"] | None, value.disposition),
            created_by=value.created_by,
            created_at=value.created_at,
            started_at=value.started_at,
            completed_at=value.completed_at,
        )


class GenerationRunResponse(GenerationRunSummaryResponse):
    blueprint_id: str
    blueprint_version: str
    blueprint_slot: dict[str, object]
    context: list[dict[str, object]]
    prompt_id: str
    provider_version: str
    model_version: str
    retrieval_version: str
    schema_version: str
    pricing_version: str
    generation_parameters: dict[str, object]
    budgets: dict[str, int]
    input_tokens: int
    output_tokens: int
    candidate: dict[str, object] | None

    @classmethod
    def from_model(cls, value: object) -> Self:
        from .models import GenerationRunModel

        if not isinstance(value, GenerationRunModel):
            raise TypeError("value must be GenerationRunModel")
        summary = GenerationRunSummaryResponse.from_model(value)
        context_items = value.context_snapshot.get("items")
        if not isinstance(context_items, list):
            raise TypeError("generation context snapshot must contain an item list")
        return cls(
            **summary.model_dump(),
            blueprint_id=value.blueprint_version,
            blueprint_version=value.blueprint_version,
            blueprint_slot=value.blueprint_slot_snapshot,
            context=cast(list[dict[str, object]], context_items),
            prompt_id=value.prompt_id,
            provider_version=value.provider_version,
            model_version=value.model_version,
            retrieval_version=value.retrieval_version,
            schema_version=value.schema_version,
            pricing_version=value.pricing_version,
            generation_parameters=value.generation_parameters,
            budgets={
                "max_attempts": value.max_attempts,
                "max_input_tokens": value.max_input_tokens,
                "max_output_tokens": value.max_output_tokens,
                "max_cost_microusd": value.max_cost_microusd,
            },
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
            candidate=value.candidate,
        )


class GenerationAttemptResponse(_FrozenStrictModel):
    id: UUID
    generation_run_id: UUID
    attempt_number: int
    retry_of_attempt_id: UUID | None
    provider_idempotency_key: str
    status: Literal["succeeded", "failed"]
    failure_code: str | None
    retry_after_ms: int | None
    accounting_known: bool
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_microusd: int | None
    latency_ms: int
    candidate: dict[str, object] | None
    disposition: Literal["requires_validation"] | None
    started_at: datetime
    completed_at: datetime

    @classmethod
    def from_model(cls, value: object) -> Self:
        from .models import GenerationAttemptModel

        if not isinstance(value, GenerationAttemptModel):
            raise TypeError("value must be GenerationAttemptModel")
        return cls(
            id=value.id,
            generation_run_id=value.generation_run_id,
            attempt_number=value.attempt_number,
            retry_of_attempt_id=value.retry_of_attempt_id,
            provider_idempotency_key=value.provider_idempotency_key,
            status=cast(Literal["succeeded", "failed"], value.status),
            failure_code=value.failure_code,
            retry_after_ms=value.retry_after_ms,
            accounting_known=value.accounting_known,
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
            total_tokens=value.total_tokens,
            cost_microusd=value.cost_microusd,
            latency_ms=value.latency_ms,
            candidate=value.candidate,
            disposition=cast(Literal["requires_validation"] | None, value.disposition),
            started_at=value.started_at,
            completed_at=value.completed_at,
        )
