from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NonNegativeInt = Annotated[int, Field(ge=0)]
FailureCode = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$")]


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class OperationsWindowResponse(_FrozenStrictModel):
    start: datetime
    end: datetime
    semantics: Literal["start_inclusive_end_exclusive"] = "start_inclusive_end_exclusive"


class OperationsDataBoundsResponse(_FrozenStrictModel):
    earliest_observed_at: datetime | None
    latest_observed_at: datetime | None


class OperationsUnitsResponse(_FrozenStrictModel):
    counts: Literal["count"] = "count"
    tokens: Literal["token"] = "token"
    cost: Literal["microusd"] = "microusd"
    latency: Literal["millisecond"] = "millisecond"
    timestamps: Literal["UTC"] = "UTC"


class FailureCodeCountResponse(_FrozenStrictModel):
    code: FailureCode
    count: NonNegativeInt


class GenerationStatusCountsResponse(_FrozenStrictModel):
    pending: NonNegativeInt
    running: NonNegativeInt
    succeeded: NonNegativeInt
    failed: NonNegativeInt


class LatencyMillisecondsResponse(_FrozenStrictModel):
    total: NonNegativeInt
    average: NonNegativeInt
    maximum: NonNegativeInt


class GenerationOperationsResponse(_FrozenStrictModel):
    run_count: NonNegativeInt
    status_counts: GenerationStatusCountsResponse
    failure_codes: list[FailureCodeCountResponse]
    attempt_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    cost_microusd: NonNegativeInt
    latency_ms: LatencyMillisecondsResponse


class ValidationStatusCountsResponse(_FrozenStrictModel):
    pass_: NonNegativeInt = Field(alias="pass", serialization_alias="pass")
    warn: NonNegativeInt
    fail: NonNegativeInt


class ValidationOperationsResponse(_FrozenStrictModel):
    run_count: NonNegativeInt
    run_status_counts: ValidationStatusCountsResponse
    finding_count: NonNegativeInt
    finding_status_counts: ValidationStatusCountsResponse


class ExtractionStatusCountsResponse(_FrozenStrictModel):
    uploaded: NonNegativeInt
    extraction_pending: NonNegativeInt
    extracted: NonNegativeInt
    in_review: NonNegativeInt
    trusted: NonNegativeInt
    failed: NonNegativeInt


class ExtractionOperationsResponse(_FrozenStrictModel):
    document_count: NonNegativeInt
    status_counts: ExtractionStatusCountsResponse
    failure_codes: list[FailureCodeCountResponse]
    ocr_page_count: NonNegativeInt


class EmbeddingStatusCountsResponse(_FrozenStrictModel):
    queued: NonNegativeInt
    claimed: NonNegativeInt
    succeeded: NonNegativeInt
    failed: NonNegativeInt


class EmbeddingOperationsResponse(_FrozenStrictModel):
    job_count: NonNegativeInt
    status_counts: EmbeddingStatusCountsResponse
    failure_codes: list[FailureCodeCountResponse]
    requested_count: NonNegativeInt
    embedded_count: NonNegativeInt
    deduplicated_count: NonNegativeInt


class PracticePaperStateCountsResponse(_FrozenStrictModel):
    draft: NonNegativeInt
    published: NonNegativeInt
    archived: NonNegativeInt


class PracticePaperOperationsResponse(_FrozenStrictModel):
    paper_count: NonNegativeInt
    state_counts: PracticePaperStateCountsResponse
    publication_count: NonNegativeInt
    archive_count: NonNegativeInt


class OperationsSummaryResponse(_FrozenStrictModel):
    window: OperationsWindowResponse
    data_bounds: OperationsDataBoundsResponse
    units: OperationsUnitsResponse
    generation: GenerationOperationsResponse
    validation: ValidationOperationsResponse
    extraction: ExtractionOperationsResponse
    embedding: EmbeddingOperationsResponse
    practice_papers: PracticePaperOperationsResponse
