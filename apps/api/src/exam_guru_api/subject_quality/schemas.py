from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exam_guru_api.subject_quality.domain import (
    DefectCategory,
    EvalCaseState,
    EvalComparisonOutcome,
    FeedbackAction,
)
from exam_guru_api.validation.domain import FindingStatus

_FINDING_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubjectQualityPromotionRequest(_StrictModel):
    expected_status: FindingStatus
    expected_finding_codes: tuple[Annotated[str, Field(min_length=1, max_length=128)], ...] = Field(
        max_length=32
    )
    defect_category: DefectCategory

    @field_validator("expected_finding_codes")
    @classmethod
    def validate_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _FINDING_CODE.fullmatch(code) for code in value):
            raise ValueError("expected finding codes must be stable machine codes")
        canonical = tuple(sorted(value))
        if len(set(canonical)) != len(canonical):
            raise ValueError("expected finding codes must be unique")
        return canonical

    @model_validator(mode="after")
    def validate_expectation(self) -> Self:
        if self.expected_status is FindingStatus.PASS and self.expected_finding_codes:
            raise ValueError("a passing expectation cannot contain non-passing finding codes")
        if self.expected_status is not FindingStatus.PASS and not self.expected_finding_codes:
            raise ValueError("a non-passing expectation requires at least one finding code")
        if (
            self.expected_status is FindingStatus.PASS
            and self.defect_category is not DefectCategory.NO_DEFECT
        ):
            raise ValueError("a passing expectation must use the no_defect category")
        return self


class SubjectQualityEvalApprovalRequest(_StrictModel):
    expected_version: Annotated[int, Field(strict=True, ge=1, le=2)]


class SubjectQualityEvalRunRequest(_StrictModel):
    case_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)

    @field_validator("case_ids")
    @classmethod
    def validate_case_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        canonical = tuple(sorted(value, key=lambda item: item.int))
        if len(set(canonical)) != len(canonical):
            raise ValueError("case_ids must be unique")
        return canonical


class SubjectQualityScopeResponse(_FrozenStrictModel):
    grade: int
    medium: str
    subject_code: str
    curriculum_version_id: UUID
    unit_id: UUID
    lesson_id: UUID
    lesson_number: int


class SubjectQualityLineageResponse(_FrozenStrictModel):
    candidate_id: UUID
    candidate_revision: int
    candidate_version: int
    generation_run_id: UUID
    generation_attempt_id: UUID
    validation_run_id: UUID
    replacement_generation_run_id: UUID | None
    prompt_version: str
    provider: str
    provider_version: str
    model: str
    model_version: str
    retrieval_version: str
    validator_versions: tuple[dict[str, str], ...]
    provenance: tuple[dict[str, object], ...]


class SubjectQualityFingerprintResponse(_FrozenStrictModel):
    original_content: str
    current_content: str
    findings: str
    scope: str
    provenance: str
    feedback: str


class SubjectQualityFeedbackResponse(_FrozenStrictModel):
    id: UUID
    schema_version: str
    action: FeedbackAction
    reason_code: str
    note: str | None
    actor_id: UUID
    created_at: datetime
    original_content: dict[str, object]
    current_content: dict[str, object]
    findings_at_action: dict[str, object]
    scope: SubjectQualityScopeResponse
    lineage: SubjectQualityLineageResponse
    fingerprints: SubjectQualityFingerprintResponse
    promoted_eval_case_id: UUID | None = None


class SubjectQualityFeedbackListResponse(_FrozenStrictModel):
    items: tuple[SubjectQualityFeedbackResponse, ...]
    total: int
    limit: int
    offset: int


class SubjectQualityEvalCaseResponse(_FrozenStrictModel):
    eval_case_id: UUID
    version: int
    state: EvalCaseState
    source_feedback_id: UUID
    expected_status: FindingStatus
    expected_finding_codes: tuple[str, ...]
    defect_category: DefectCategory
    case_fingerprint: str
    promoted_by: UUID
    approved_by: UUID | None
    created_at: datetime
    approved_at: datetime | None
    can_approve: bool
    deduplicated: bool = False


class SubjectQualityEvalCaseListResponse(_FrozenStrictModel):
    items: tuple[SubjectQualityEvalCaseResponse, ...]
    total: int
    limit: int
    offset: int


class SubjectQualityEvalExportExpectedResponse(_FrozenStrictModel):
    status: FindingStatus
    finding_codes: tuple[str, ...]
    defect_category: DefectCategory


class SubjectQualityEvalExportCaseResponse(_FrozenStrictModel):
    eval_case_id: UUID
    version: int
    source_feedback_id: UUID
    case_fingerprint: str
    subject_scope: dict[str, object]
    candidate: dict[str, object]
    blueprint: dict[str, object]
    grounding_sources: tuple[dict[str, object], ...]
    duplicate_references: tuple[dict[str, object], ...]
    generation_versions: dict[str, object]
    expected: SubjectQualityEvalExportExpectedResponse


class SubjectQualityEvalExportResponse(_FrozenStrictModel):
    schema_version: Literal["subject-quality-eval-export.v1"]
    runner_version: Literal["subject-quality-eval-runner.v1"]
    cases: tuple[SubjectQualityEvalExportCaseResponse, ...]
    limit: int
    offset: int
    next_offset: int | None


class SubjectQualityEvalResultResponse(_FrozenStrictModel):
    id: UUID
    eval_case_id: UUID
    eval_case_version: int
    expected_status: FindingStatus
    expected_finding_codes: tuple[str, ...]
    actual_status: FindingStatus
    actual_finding_codes: tuple[str, ...]
    outcome: EvalComparisonOutcome
    passed: bool
    pipeline_version: str
    pipeline_fingerprint: str
    validator_versions: tuple[dict[str, str], ...]
    report_fingerprint: str
    fingerprint: str


class SubjectQualityEvalRunResponse(_FrozenStrictModel):
    run_id: UUID
    runner_version: Literal["subject-quality-eval-runner.v1"]
    pipeline_version: str
    pipeline_fingerprint: str
    request_fingerprint: str
    created_by: UUID
    created_at: datetime
    results: tuple[SubjectQualityEvalResultResponse, ...]
    passed_count: int
    regression_count: int
    unavailable_count: int
    deduplicated: bool = False
