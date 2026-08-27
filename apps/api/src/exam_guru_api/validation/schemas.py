from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidationRunCreateRequest(_StrictModel):
    """The generation identity is the only client-selected validation input."""

    generation_run_id: UUID


class ValidationRunSummaryResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    generation_run_id: UUID
    generation_attempt_id: UUID
    pipeline_version: str
    pipeline_fingerprint: str
    generation_result_fingerprint: str
    input_fingerprint: str
    candidate_fingerprint: str
    report_fingerprint: str
    overall_status: Literal["pass", "warn", "fail"]
    finding_count: int
    validator_count: int
    grounding_source_count: int
    duplicate_reference_count: int
    created_by: UUID
    created_at: datetime
    deduplicated: bool = False

    @classmethod
    def from_model(cls, value: object, *, deduplicated: bool = False) -> Self:
        from .models import ValidationRunModel

        if not isinstance(value, ValidationRunModel):
            raise TypeError("value must be ValidationRunModel")
        return cls(
            id=value.id,
            curriculum_version_id=value.curriculum_version_id,
            generation_run_id=value.generation_run_id,
            generation_attempt_id=value.generation_attempt_id,
            pipeline_version=value.pipeline_version,
            pipeline_fingerprint=value.pipeline_fingerprint,
            generation_result_fingerprint=value.generation_result_fingerprint,
            input_fingerprint=value.input_fingerprint,
            candidate_fingerprint=value.candidate_fingerprint,
            report_fingerprint=value.report_fingerprint,
            overall_status=cast(Literal["pass", "warn", "fail"], value.overall_status),
            finding_count=value.finding_count,
            validator_count=value.validator_count,
            grounding_source_count=value.grounding_source_count,
            duplicate_reference_count=value.duplicate_reference_count,
            created_by=value.created_by,
            created_at=value.created_at,
            deduplicated=deduplicated,
        )


class ValidationRunResponse(ValidationRunSummaryResponse):
    input_schema_version: str
    report_schema_version: str
    input_snapshot: dict[str, object]
    validator_lineage: list[dict[str, str]]
    limitations: list[str]

    @classmethod
    def from_model(cls, value: object, *, deduplicated: bool = False) -> Self:
        from .models import ValidationRunModel

        if not isinstance(value, ValidationRunModel):
            raise TypeError("value must be ValidationRunModel")
        summary = ValidationRunSummaryResponse.from_model(value, deduplicated=deduplicated)
        return cls(
            **summary.model_dump(),
            input_schema_version=value.input_schema_version,
            report_schema_version=value.report_schema_version,
            input_snapshot=value.input_snapshot,
            validator_lineage=value.validator_lineage,
            limitations=value.limitations,
        )


class SemanticEvidenceReferenceResponse(_FrozenStrictModel):
    context_id: str
    source_document_id: str
    page_number: int


class SemanticClaimEvidenceResponse(_FrozenStrictModel):
    claim_id: str
    claim_type: Literal["answer", "explanation", "marking"]
    location: str
    status: Literal["supported", "contradicted", "insufficient_evidence", "unavailable"]
    summary: str
    evidence_refs: list[SemanticEvidenceReferenceResponse]


class SemanticVerifierLineageResponse(_FrozenStrictModel):
    verifier_id: str
    verifier_version: str
    prompt_version: str
    provider: str
    provider_version: str
    model: str
    model_version: str
    pricing_version: str


class SemanticVerifierAccountingResponse(_FrozenStrictModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_microusd: int
    latency_ms: int


class SemanticVerificationDetailsResponse(_FrozenStrictModel):
    schema_version: Literal["semantic-verification.v1"]
    decomposition_version: Literal["deterministic-factual-claims.v1"]
    call_attempted: bool
    failure_code: str | None
    status: Literal["supported", "contradicted", "insufficient_evidence", "unavailable"]
    summary: str
    claims: list[SemanticClaimEvidenceResponse]
    lineage: SemanticVerifierLineageResponse | None
    accounting: SemanticVerifierAccountingResponse | None


class ValidationFindingEvidenceResponse(_FrozenStrictModel):
    location: str
    expected: str
    observed: str
    details: dict[str, object] | None = None


class ValidationFindingResponse(_FrozenStrictModel):
    id: UUID
    validation_run_id: UUID
    ordinal: int
    validator_id: str
    validator_version: str
    code: str
    status: Literal["pass", "warn", "fail"]
    message: str
    evidence: list[ValidationFindingEvidenceResponse]
    semantic_verification: SemanticVerificationDetailsResponse | None = None
    evidence_count: int
    created_at: datetime

    @classmethod
    def from_model(cls, value: object) -> Self:
        from .models import ValidationFindingModel

        if not isinstance(value, ValidationFindingModel):
            raise TypeError("value must be ValidationFindingModel")
        semantic_details = [
            cast(Mapping[str, object], item["details"])
            for item in value.evidence
            if item.get("location") == "$.semantic_verification"
            and isinstance(item.get("details"), Mapping)
        ]
        if len(semantic_details) > 1:
            raise ValueError("finding contains duplicate semantic verification details")
        semantic_verification = (
            SemanticVerificationDetailsResponse.model_validate(semantic_details[0])
            if semantic_details
            else None
        )
        return cls(
            id=value.id,
            validation_run_id=value.validation_run_id,
            ordinal=value.ordinal,
            validator_id=value.validator_id,
            validator_version=value.validator_version,
            code=value.code,
            status=cast(Literal["pass", "warn", "fail"], value.status),
            message=value.message,
            evidence=[
                ValidationFindingEvidenceResponse.model_validate(item) for item in value.evidence
            ],
            semantic_verification=semantic_verification,
            evidence_count=value.evidence_count,
            created_at=value.created_at,
        )
