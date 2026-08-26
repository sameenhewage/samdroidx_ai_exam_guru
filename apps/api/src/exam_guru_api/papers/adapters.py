"""Strict generation/validation boundary for paper question candidates.

The adapter binds a validation report to one exact ``GenerationResult`` before
using the paper domain's generated-to-validated transition.  It deliberately
has no review, approval, assembly, persistence, or publication capability.
"""

import json
from enum import StrEnum
from uuid import UUID

from exam_guru_api.generation.domain import GenerationResult
from exam_guru_api.papers.domain import (
    CandidateInvariantError,
    GenerationLineage,
    QuestionCandidate,
    QuestionContent,
    QuestionOption,
    SourceProvenance,
    ValidationEvidence,
    create_generated_candidate,
    record_candidate_validation,
)
from exam_guru_api.validation.domain import (
    BlueprintRequirements,
    DuplicateReference,
    FindingCode,
    FindingStatus,
    ValidationInput,
    ValidationReport,
    grade_age_bounds,
)
from exam_guru_api.validation.generation_adapter import adapt_generation_result
from exam_guru_api.validation.generation_adapter import (
    generation_result_fingerprint as _generation_result_fingerprint,
)

generation_result_fingerprint = _generation_result_fingerprint


class GenerationValidationMismatch(StrEnum):
    """Stable reasons why generation and validation evidence cannot be joined."""

    GENERATION_FINGERPRINT = "generation_fingerprint"
    CANDIDATE_FINGERPRINT = "candidate_fingerprint"
    REPORT_FINGERPRINT = "report_fingerprint"


class GenerationValidationAdapterError(CandidateInvariantError):
    """Raised when an untyped value crosses the generation/validation adapter."""


class GenerationValidationMismatchError(GenerationValidationAdapterError):
    """Raised when otherwise typed evidence belongs to another immutable subject."""

    def __init__(
        self,
        mismatch: GenerationValidationMismatch,
        *,
        expected: str,
        actual: str,
    ) -> None:
        self.mismatch = mismatch
        self.expected = expected
        self.actual = actual
        super().__init__(f"{mismatch.value} mismatch: expected {expected!r}, found {actual!r}")


def _require_generation_result(value: object) -> GenerationResult:
    if not isinstance(value, GenerationResult):
        raise GenerationValidationAdapterError("generation_result must be GenerationResult")
    return value


def _require_validation_report(value: object) -> ValidationReport:
    if not isinstance(value, ValidationReport):
        raise GenerationValidationAdapterError("validation_report must be ValidationReport")
    return value


def build_generation_validation_input(
    generation_result: GenerationResult,
    *,
    duplicate_references: tuple[DuplicateReference, ...] = (),
) -> ValidationInput:
    """Build the canonical validator input for one generated blueprint slot."""

    result = _require_generation_result(generation_result)
    slot = result.request.blueprint_slot
    minimum_age, maximum_age = grade_age_bounds(slot.generation_constraints.curriculum_scope.grade)
    return adapt_generation_result(
        result,
        requirements=BlueprintRequirements(
            slot_id=slot.slot_id,
            schema_version=result.request.versions.schema_version,
            question_type=slot.question_type.value,
            marks=slot.marks,
            language=slot.generation_constraints.response_language,
            minimum_age=minimum_age,
            maximum_age=maximum_age,
        ),
        duplicate_references=duplicate_references,
    )


def _assert_report_integrity(report: ValidationReport) -> None:
    canonical_report = ValidationReport(
        candidate_id=report.candidate_id,
        pipeline_version=report.pipeline_version,
        findings=report.findings,
    )
    if canonical_report != report:
        raise GenerationValidationMismatchError(
            GenerationValidationMismatch.REPORT_FINGERPRINT,
            expected=canonical_report.report_fingerprint,
            actual=report.report_fingerprint,
        )


def _assert_report_binding(
    result: GenerationResult,
    report: ValidationReport,
    expected_input: ValidationInput,
) -> None:
    expected_fingerprint = generation_result_fingerprint(result)
    if report.candidate_id != expected_fingerprint:
        raise GenerationValidationMismatchError(
            GenerationValidationMismatch.GENERATION_FINGERPRINT,
            expected=expected_fingerprint,
            actual=report.candidate_id,
        )

    if not report.blocked:
        expected_marker = f"candidate_sha256={expected_input.candidate_fingerprint}"
        observed_markers = tuple(
            evidence.observed
            for finding in report.findings
            if finding.validator_id == "schema-completeness"
            and str(finding.code) == FindingCode.SCHEMA_COMPLETENESS.value
            and finding.status is FindingStatus.PASS
            for evidence in finding.evidence
            if evidence.location == "$"
        )
        if observed_markers != (expected_marker,):
            raise GenerationValidationMismatchError(
                GenerationValidationMismatch.CANDIDATE_FINGERPRINT,
                expected=expected_marker,
                actual=", ".join(observed_markers) or "missing",
            )


def _generation_lineage(result: GenerationResult) -> GenerationLineage:
    request = result.request
    return GenerationLineage(
        generation_id=request.identity.generation_id,
        generation_attempt_id=request.identity.attempt_id,
        blueprint_id=request.blueprint_version.blueprint_id,
        blueprint_version=request.versions.blueprint_version,
        blueprint_slot_id=request.blueprint_slot.slot_id,
        prompt_version=request.versions.prompt_version,
        provider=request.versions.provider,
        model_version=request.versions.model_version,
        retrieval_version=request.versions.retrieval_version,
        schema_version=request.versions.schema_version,
        provenance=tuple(
            SourceProvenance(
                source_document_id=item.provenance.source_document_id,
                source_version=item.provenance.source_version,
                page_number=item.provenance.page_number,
                chunk_id=item.provenance.chunk_id,
            )
            for item in request.context.items
        ),
    )


def _paper_content(result: GenerationResult) -> QuestionContent:
    question = result.question
    answer = question.answer.correct_option_id
    if answer is None:
        answer = json.dumps(
            question.answer.accepted_responses,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return QuestionContent(
        question_type=question.question_type.value,
        stem=question.stem,
        options=tuple(
            QuestionOption(option_id=option.option_id, text=option.text)
            for option in question.options
        ),
        answer=answer,
        explanation=question.answer.explanation,
        marks=question.marking.total_marks,
        marking_guide=tuple(
            json.dumps(
                {
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                    "marks": criterion.marks,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for criterion in question.marking.criteria
        ),
    )


def _validation_evidence(
    report: ValidationReport,
    *,
    validation_run_id: UUID,
    finding_ids: tuple[UUID, ...],
) -> ValidationEvidence:
    if not isinstance(validation_run_id, UUID):
        raise GenerationValidationAdapterError("validation_run_id must be a persisted UUID")
    if (
        not isinstance(finding_ids, tuple)
        or len(finding_ids) != len(report.findings)
        or any(not isinstance(finding_id, UUID) for finding_id in finding_ids)
        or len(set(finding_ids)) != len(finding_ids)
    ):
        raise GenerationValidationAdapterError(
            "finding_ids must be unique persisted UUIDs matching every report finding"
        )
    return ValidationEvidence(
        validation_run_id=validation_run_id,
        validator_version=f"{report.pipeline_version}/{report.report_schema_version}",
        finding_refs=tuple(str(finding_id) for finding_id in finding_ids),
        passed=not report.blocked,
        validated_revision=1,
    )


def adapt_generation_validation(
    generation_result: GenerationResult,
    validation_report: ValidationReport,
    *,
    validation_run_id: UUID,
    finding_ids: tuple[UUID, ...],
) -> QuestionCandidate:
    """Create a generated candidate and apply only the domain validation transition."""

    result = _require_generation_result(generation_result)
    report = _require_validation_report(validation_report)
    _assert_report_integrity(report)
    expected_input = build_generation_validation_input(result)
    _assert_report_binding(result, report, expected_input)

    generated = create_generated_candidate(
        candidate_id=result.request.identity.generation_id,
        lineage=_generation_lineage(result),
        content=_paper_content(result),
    )
    return record_candidate_validation(
        generated,
        _validation_evidence(
            report,
            validation_run_id=validation_run_id,
            finding_ids=finding_ids,
        ),
        expected_version=generated.version,
    )
