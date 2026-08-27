from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID

from exam_guru_api.validation.domain import (
    BlueprintRequirements,
    ContextScopeBinding,
    DuplicateReference,
    FindingStatus,
    GeneratedSubjectScope,
    GroundingSource,
    TrustedSubjectScope,
    ValidationContractError,
    ValidationInput,
    ValidationReport,
)
from exam_guru_api.validation.pipeline import ValidationPipeline

FEEDBACK_SCHEMA_VERSION = "subject-quality-feedback.v1"
EVAL_INPUT_SCHEMA_VERSION = "subject-quality-eval-input.v1"
EVAL_EXPORT_SCHEMA_VERSION: Literal["subject-quality-eval-export.v1"] = (
    "subject-quality-eval-export.v1"
)
EVAL_RUNNER_VERSION: Literal["subject-quality-eval-runner.v1"] = "subject-quality-eval-runner.v1"
MAX_REVIEW_NOTE_CHARACTERS = 768
MAX_EXPECTED_FINDING_CODES = 32


class FeedbackAction(StrEnum):
    EDIT = "edit"
    REJECT = "reject"
    REGENERATE = "regenerate"
    APPROVE = "approve"


class CorrectionReasonCode(StrEnum):
    ANSWER_INCORRECT = "answer_incorrect"
    AMBIGUOUS_WORDING = "ambiguous_wording"
    OUTSIDE_SCOPE = "outside_scope"
    SOURCE_NOT_SUPPORTED = "source_not_supported"
    MARKING_INCONSISTENT = "marking_inconsistent"
    LANGUAGE_QUALITY = "language_quality"
    DISTRACTOR_QUALITY = "distractor_quality"
    DUPLICATE_CONTENT = "duplicate_content"
    UNSAFE_CONTENT = "unsafe_content"
    OTHER_QUALITY_ISSUE = "other_quality_issue"


class ReviewReasonCode(StrEnum):
    ANSWER_INCORRECT = "answer_incorrect"
    AMBIGUOUS_WORDING = "ambiguous_wording"
    OUTSIDE_SCOPE = "outside_scope"
    SOURCE_NOT_SUPPORTED = "source_not_supported"
    MARKING_INCONSISTENT = "marking_inconsistent"
    LANGUAGE_QUALITY = "language_quality"
    DISTRACTOR_QUALITY = "distractor_quality"
    DUPLICATE_CONTENT = "duplicate_content"
    UNSAFE_CONTENT = "unsafe_content"
    OTHER_QUALITY_ISSUE = "other_quality_issue"
    CONFIRMED_QUALITY = "confirmed_quality"


REVIEW_REASON_LABELS: Mapping[ReviewReasonCode, str] = {
    ReviewReasonCode.ANSWER_INCORRECT: "Answer is incorrect",
    ReviewReasonCode.AMBIGUOUS_WORDING: "Wording is unclear or ambiguous",
    ReviewReasonCode.OUTSIDE_SCOPE: "Question is outside the selected curriculum scope",
    ReviewReasonCode.SOURCE_NOT_SUPPORTED: "Reviewed sources do not support the content",
    ReviewReasonCode.MARKING_INCONSISTENT: "Answer and marking guidance are inconsistent",
    ReviewReasonCode.LANGUAGE_QUALITY: "Language is not suitable for the learners",
    ReviewReasonCode.DISTRACTOR_QUALITY: "Answer choices are not suitable",
    ReviewReasonCode.DUPLICATE_CONTENT: "Question is too similar to existing content",
    ReviewReasonCode.UNSAFE_CONTENT: "Content is unsafe or inappropriate",
    ReviewReasonCode.OTHER_QUALITY_ISSUE: "Other educational quality issue",
    ReviewReasonCode.CONFIRMED_QUALITY: "Reviewer confirmed the content quality",
}


class DefectCategory(StrEnum):
    NO_DEFECT = "no_defect"
    ANSWER_CORRECTNESS = "answer_correctness"
    MULTIPLE_CORRECT_ANSWERS = "multiple_correct_answers"
    MARKING_CONSISTENCY = "marking_consistency"
    SCOPE_ALIGNMENT = "scope_alignment"
    SOURCE_GROUNDING = "source_grounding"
    LANGUAGE_CLARITY = "language_clarity"
    DISTRACTOR_QUALITY = "distractor_quality"
    DUPLICATE_CONTENT = "duplicate_content"
    SECURITY_RESIDUE = "security_residue"
    OTHER = "other"


class EvalCaseState(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class EvalComparisonOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - eval outcome, not a credential
    REGRESSION = "regression"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EvalComparison:
    expected_status: FindingStatus
    expected_finding_codes: tuple[str, ...]
    actual_status: FindingStatus
    actual_finding_codes: tuple[str, ...]
    outcome: EvalComparisonOutcome
    passed: bool
    fingerprint: str


@dataclass(frozen=True, slots=True)
class EvaluatedSnapshot:
    report: ValidationReport
    comparison: EvalComparison
    pipeline_version: str
    pipeline_fingerprint: str
    validator_versions: tuple[dict[str, str], ...]


def canonical_fingerprint(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def compose_review_reason(
    reason_code: ReviewReasonCode | CorrectionReasonCode,
    note: str | None,
) -> str:
    if not isinstance(reason_code, ReviewReasonCode | CorrectionReasonCode):
        raise ValueError("reason_code must be a supported review reason")
    if note is not None and (
        not isinstance(note, str)
        or not note
        or note != note.strip()
        or len(note) > MAX_REVIEW_NOTE_CHARACTERS
    ):
        raise ValueError("review note must be trimmed bounded text")
    label = REVIEW_REASON_LABELS[ReviewReasonCode(reason_code.value)]
    return label if note is None else f"{label} — {note}"


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be strings")
    return cast(Mapping[str, object], value)


def _sequence(value: object, field_name: str, *, maximum: int) -> Sequence[object]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) > maximum
    ):
        raise ValueError(f"{field_name} must be a bounded array")
    return cast(Sequence[object], value)


def _text(value: object, field_name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} must be bounded trimmed text")
    return value


def _integer(value: object, field_name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be a bounded integer")
    return value


def _uuid(value: object, field_name: str) -> UUID:
    try:
        return UUID(_text(value, field_name, maximum=36))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a UUID") from error


def _optional_uuid(value: object, field_name: str) -> UUID | None:
    return None if value is None else _uuid(value, field_name)


def _uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    return tuple(
        _uuid(item, f"{field_name} item") for item in _sequence(value, field_name, maximum=256)
    )


def validation_input_from_eval_snapshot(
    snapshot: object,
    *,
    expected_curriculum_version_id: UUID,
) -> ValidationInput:
    if not isinstance(expected_curriculum_version_id, UUID):
        raise ValueError("expected curriculum identity must be a UUID")
    root = _mapping(snapshot, "eval snapshot")
    if root.get("schema_version") != EVAL_INPUT_SCHEMA_VERSION:
        raise ValueError("eval snapshot schema version is unsupported")

    trusted = _mapping(root.get("subject_scope"), "subject_scope")
    generated = _mapping(root.get("generated_scope"), "generated_scope")
    trusted_curriculum = _uuid(
        trusted.get("curriculum_version_id"), "subject_scope curriculum_version_id"
    )
    generated_curriculum = _uuid(
        generated.get("curriculum_version_id"), "generated_scope curriculum_version_id"
    )
    if (
        trusted.get("trust") != "server_owned"
        or trusted_curriculum != expected_curriculum_version_id
        or generated_curriculum != expected_curriculum_version_id
    ):
        raise ValueError("eval snapshot curriculum scope conflicts with its trusted source")

    bindings = tuple(
        ContextScopeBinding(
            context_id=_text(item.get("context_id"), "binding context_id"),
            curriculum_version_id=_uuid(
                item.get("curriculum_version_id"), "binding curriculum_version_id"
            ),
            subject_id=_uuid(item.get("subject_id"), "binding subject_id"),
            unit_id=_optional_uuid(item.get("unit_id"), "binding unit_id"),
            lesson_id=_optional_uuid(item.get("lesson_id"), "binding lesson_id"),
            snapshot_unit_id=_optional_uuid(
                item.get("snapshot_unit_id"), "binding snapshot_unit_id"
            ),
            snapshot_lesson_id=_optional_uuid(
                item.get("snapshot_lesson_id"), "binding snapshot_lesson_id"
            ),
        )
        for item in (
            _mapping(value, "context scope binding")
            for value in _sequence(
                root.get("context_scope_bindings"),
                "context_scope_bindings",
                maximum=128,
            )
        )
    )
    if any(item.curriculum_version_id != expected_curriculum_version_id for item in bindings):
        raise ValueError("eval context binding crosses curriculum scope")

    sources: list[GroundingSource] = []
    for value in _sequence(root.get("grounding_sources"), "grounding_sources", maximum=128):
        item = _mapping(value, "grounding source")
        if item.get("trust") != "untrusted_data":
            raise ValueError("grounding source must remain untrusted data")
        page_number = item.get("page_number")
        sources.append(
            GroundingSource(
                context_id=_text(item.get("context_id"), "source context_id"),
                text=_text(item.get("text"), "source text", maximum=32_000),
                source_document_id=(
                    None
                    if item.get("source_document_id") is None
                    else _text(item.get("source_document_id"), "source document id")
                ),
                source_version=(
                    None
                    if item.get("source_version") is None
                    else _text(item.get("source_version"), "source version")
                ),
                page_number=(
                    None
                    if page_number is None
                    else _integer(page_number, "source page number", minimum=1, maximum=1_000_000)
                ),
                chunk_id=(
                    None
                    if item.get("chunk_id") is None
                    else _text(item.get("chunk_id"), "source chunk id")
                ),
            )
        )

    duplicates: list[DuplicateReference] = []
    for value in _sequence(
        root.get("duplicate_references"), "duplicate_references", maximum=10_000
    ):
        item = _mapping(value, "duplicate reference")
        text_value = item.get("text")
        digest = item.get("content_sha256")
        duplicates.append(
            DuplicateReference(
                question_id=_text(item.get("question_id"), "duplicate question_id"),
                text=(
                    None
                    if text_value is None
                    else _text(text_value, "duplicate text", maximum=16_000)
                ),
                content_sha256=(
                    None
                    if digest is None
                    else _text(digest, "duplicate content_sha256", maximum=64)
                ),
            )
        )

    blueprint = _mapping(root.get("blueprint"), "blueprint")
    candidate = _mapping(root.get("candidate"), "candidate")
    try:
        return ValidationInput(
            candidate_id=_text(root.get("candidate_id"), "candidate_id", maximum=128),
            candidate=candidate,
            blueprint=BlueprintRequirements(
                slot_id=_text(blueprint.get("slot_id"), "blueprint slot_id"),
                schema_version=_text(blueprint.get("schema_version"), "blueprint schema_version"),
                question_type=_text(blueprint.get("question_type"), "blueprint question_type"),
                marks=_integer(blueprint.get("marks"), "blueprint marks", minimum=1, maximum=100),
                language=_text(blueprint.get("language"), "blueprint language", maximum=32),
                minimum_age=_integer(
                    blueprint.get("minimum_age"), "blueprint minimum_age", minimum=1, maximum=18
                ),
                maximum_age=_integer(
                    blueprint.get("maximum_age"), "blueprint maximum_age", minimum=1, maximum=19
                ),
                minimum_options=_integer(
                    blueprint.get("minimum_options"),
                    "blueprint minimum_options",
                    minimum=1,
                    maximum=16,
                ),
                maximum_options=_integer(
                    blueprint.get("maximum_options"),
                    "blueprint maximum_options",
                    minimum=1,
                    maximum=16,
                ),
            ),
            grounding_sources=tuple(sources),
            duplicate_references=tuple(duplicates),
            trusted_scope=TrustedSubjectScope(
                grade=_integer(trusted.get("grade"), "subject grade", minimum=1, maximum=13),
                medium=_text(trusted.get("medium"), "subject medium", maximum=32),
                subject_id=_uuid(trusted.get("subject_id"), "subject id"),
                subject_code=_text(trusted.get("subject_code"), "subject code", maximum=64),
                curriculum_version_id=trusted_curriculum,
                unit_ids=_uuid_tuple(trusted.get("unit_ids"), "subject unit_ids"),
                lesson_ids=_uuid_tuple(trusted.get("lesson_ids"), "subject lesson_ids"),
            ),
            generated_scope=GeneratedSubjectScope(
                grade=_integer(generated.get("grade"), "generated grade", minimum=1, maximum=13),
                medium=_text(generated.get("medium"), "generated medium", maximum=32),
                subject_id=_uuid(generated.get("subject_id"), "generated subject id"),
                curriculum_version_id=generated_curriculum,
                unit_ids=_uuid_tuple(generated.get("unit_ids"), "generated unit_ids"),
                lesson_ids=_uuid_tuple(generated.get("lesson_ids"), "generated lesson_ids"),
            ),
            context_scope_bindings=bindings,
        )
    except ValidationContractError as error:
        raise ValueError("eval snapshot violates the validation input contract") from error


def compare_eval_report(
    *,
    expected_status: FindingStatus,
    expected_finding_codes: tuple[str, ...],
    report: ValidationReport,
) -> EvalComparison:
    if not isinstance(expected_status, FindingStatus):
        raise ValueError("expected status must be FindingStatus")
    if (
        not isinstance(expected_finding_codes, tuple)
        or len(expected_finding_codes) > MAX_EXPECTED_FINDING_CODES
        or any(not isinstance(code, str) or not code for code in expected_finding_codes)
    ):
        raise ValueError("expected finding codes must be a bounded tuple")
    if not isinstance(report, ValidationReport):
        raise ValueError("report must be ValidationReport")
    canonical_expected = tuple(sorted(set(expected_finding_codes)))
    if len(canonical_expected) != len(expected_finding_codes):
        raise ValueError("expected finding codes must be unique")
    actual_codes = tuple(
        sorted(
            finding.code for finding in report.findings if finding.status is not FindingStatus.PASS
        )
    )

    def unavailable_code(code: str) -> bool:
        return code.endswith((".verifier_unavailable", ".provider_unavailable"))

    unavailable = any(unavailable_code(code) for code in actual_codes)
    matched = report.overall_status is expected_status and actual_codes == canonical_expected
    differing_codes = set(actual_codes).symmetric_difference(canonical_expected)
    substantive_mismatch = any(not unavailable_code(code) for code in differing_codes)
    outcome = (
        EvalComparisonOutcome.PASS
        if matched and not unavailable
        else EvalComparisonOutcome.UNAVAILABLE
        if unavailable and not substantive_mismatch
        else EvalComparisonOutcome.REGRESSION
    )
    material = {
        "runner_version": EVAL_RUNNER_VERSION,
        "expected": {
            "status": expected_status.value,
            "finding_codes": list(canonical_expected),
        },
        "actual": {
            "status": report.overall_status.value,
            "finding_codes": list(actual_codes),
            "report_fingerprint": report.report_fingerprint,
        },
        "outcome": outcome.value,
    }
    return EvalComparison(
        expected_status=expected_status,
        expected_finding_codes=canonical_expected,
        actual_status=report.overall_status,
        actual_finding_codes=actual_codes,
        outcome=outcome,
        passed=outcome is EvalComparisonOutcome.PASS,
        fingerprint=canonical_fingerprint(material),
    )


def evaluate_snapshot(
    *,
    snapshot: object,
    expected_curriculum_version_id: UUID,
    expected_status: FindingStatus,
    expected_finding_codes: tuple[str, ...],
    pipeline: ValidationPipeline,
) -> EvaluatedSnapshot:
    if not isinstance(pipeline, ValidationPipeline):
        raise ValueError("pipeline must be ValidationPipeline")
    validation_input = validation_input_from_eval_snapshot(
        snapshot,
        expected_curriculum_version_id=expected_curriculum_version_id,
    )
    report = pipeline.validate(validation_input)
    comparison = compare_eval_report(
        expected_status=expected_status,
        expected_finding_codes=expected_finding_codes,
        report=report,
    )
    validator_versions = tuple(
        {"validator_id": validator_id, "validator_version": validator_version}
        for validator_id, validator_version in sorted(
            {(finding.validator_id, finding.validator_version) for finding in report.findings}
        )
    )
    return EvaluatedSnapshot(
        report=report,
        comparison=comparison,
        pipeline_version=pipeline.version,
        pipeline_fingerprint=pipeline.pipeline_fingerprint,
        validator_versions=validator_versions,
    )
