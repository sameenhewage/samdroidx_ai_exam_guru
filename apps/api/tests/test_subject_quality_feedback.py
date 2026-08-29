import json
from copy import deepcopy
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.subject_quality.domain import (
    CorrectionReasonCode,
    EvalComparisonOutcome,
    ReviewReasonCode,
    canonical_fingerprint,
    compare_eval_report,
    compose_review_reason,
    evaluate_snapshot,
    validation_input_from_eval_snapshot,
)
from exam_guru_api.subject_quality.schemas import (
    SubjectQualityEvalApprovalRequest,
    SubjectQualityEvalRunRequest,
    SubjectQualityPromotionRequest,
)
from exam_guru_api.teacher_papers.schemas import (
    ReviewQuestionApproveRequest,
    ReviewQuestionEditRequest,
    ReviewQuestionRegenerateRequest,
    ReviewQuestionRejectRequest,
)
from exam_guru_api.validation.domain import (
    FindingEvidence,
    FindingStatus,
    ValidationFinding,
    ValidationReport,
)
from exam_guru_api.validation.pipeline import ValidationPipeline, build_default_pipeline

CURRICULUM_ID = UUID("00000000-0000-0000-0000-000000002601")
SUBJECT_ID = UUID("00000000-0000-0000-0000-000000002602")
UNIT_ID = UUID("00000000-0000-0000-0000-000000002603")
LESSON_ID = UUID("00000000-0000-0000-0000-000000002604")


def replay_snapshot() -> dict[str, object]:
    return {
        "schema_version": "subject-quality-eval-input.v1",
        "candidate_id": "a" * 64,
        "candidate": {
            "schema_version": "question.v1",
            "question_type": "multiple_choice",
            "stem": "What is 2 + 3?",
            "options": [
                {"option_id": "A", "text": "4"},
                {"option_id": "B", "text": "5"},
            ],
            "answer": {
                "correct_option_id": "B",
                "accepted_responses": [],
                "explanation": "Two plus three is five.",
            },
            "marking": {
                "total_marks": 1,
                "criteria": [{"criterion_id": "m1", "description": "Selects 5.", "marks": 1}],
            },
            "context_references": ["knowledge_chunk:fixture"],
            "generation_metadata": {
                "generation_id": "00000000-0000-0000-0000-000000002610",
                "attempt_id": "00000000-0000-0000-0000-000000002611",
                "attempt_number": 1,
                "retry_of_attempt_id": None,
                "blueprint_version": "fixture-blueprint-v1",
                "blueprint_schema_version": "paper-blueprint.v1",
                "blueprint_algorithm_version": "fixture-algorithm-v1",
                "blueprint_config_version": "fixture-config-v1",
                "blueprint_input_fingerprint": "sha256:" + "b" * 64,
                "prompt_id": "fixture-prompt",
                "prompt_version": "fixture-prompt-v1",
                "provider": "deterministic-fixture",
                "provider_version": "fixture-provider-v1",
                "model": "fixture-model",
                "model_version": "fixture-model-v1",
                "retrieval_version": "fixture-retrieval-v1",
                "schema_version": "question.v1",
                "disposition": "requires_validation",
            },
        },
        "blueprint": {
            "slot_id": "slot-1",
            "schema_version": "question.v1",
            "question_type": "multiple_choice",
            "marks": 1,
            "language": "en",
            "minimum_age": 11,
            "maximum_age": 13,
            "minimum_options": 2,
            "maximum_options": 4,
        },
        "subject_scope": {
            "trust": "server_owned",
            "grade": 7,
            "medium": "en",
            "subject_id": str(SUBJECT_ID),
            "subject_code": "MATHEMATICS",
            "curriculum_version_id": str(CURRICULUM_ID),
            "unit_ids": [str(UNIT_ID)],
            "lesson_ids": [str(LESSON_ID)],
        },
        "generated_scope": {
            "grade": 7,
            "medium": "en",
            "subject_id": str(SUBJECT_ID),
            "curriculum_version_id": str(CURRICULUM_ID),
            "unit_ids": [str(UNIT_ID)],
            "lesson_ids": [str(LESSON_ID)],
        },
        "context_scope_bindings": [
            {
                "context_id": "knowledge_chunk:fixture",
                "curriculum_version_id": str(CURRICULUM_ID),
                "subject_id": str(SUBJECT_ID),
                "unit_id": str(UNIT_ID),
                "lesson_id": str(LESSON_ID),
                "snapshot_unit_id": str(UNIT_ID),
                "snapshot_lesson_id": str(LESSON_ID),
            }
        ],
        "grounding_sources": [
            {
                "context_id": "knowledge_chunk:fixture",
                "text": "Reviewed lesson evidence: two plus three equals five.",
                "source_document_id": "00000000-0000-0000-0000-000000002620",
                "source_version": "1",
                "page_number": 4,
                "chunk_id": "00000000-0000-0000-0000-000000002621",
                "trust": "untrusted_data",
            }
        ],
        "duplicate_references": [],
    }


class FixedValidator:
    validator_id = "fixed-eval-validator"
    validator_version = "1.0.0"

    def __init__(self, status: FindingStatus, code: str) -> None:
        self.status = status
        self.code = code

    def validate(self, _validation_input: object) -> tuple[ValidationFinding, ...]:
        return (
            ValidationFinding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code=self.code,
                status=self.status,
                message="Deterministic replay fixture.",
                evidence=(
                    FindingEvidence(
                        location="$.candidate",
                        expected="fixture expected",
                        observed="fixture observed",
                    ),
                ),
            ),
        )


def report(code: str, status: FindingStatus) -> ValidationReport:
    return ValidationReport(
        candidate_id="a" * 64,
        pipeline_version="fixture-pipeline.v1",
        findings=(
            ValidationFinding(
                validator_id="fixture-validator",
                validator_version="1.0.0",
                code=code,
                status=status,
                message="Deterministic fixture finding.",
                evidence=(
                    FindingEvidence(
                        location="$.candidate",
                        expected="fixture expected",
                        observed="fixture observed",
                    ),
                ),
            ),
        ),
    )


def edit_payload() -> dict[str, object]:
    return {
        "expected_version": 3,
        "reason_code": "ambiguous_wording",
        "note": "The stem has two plausible readings.",
        "content": {
            "question_type": "multiple_choice",
            "stem": "Which answer is correct?",
            "options": [
                {"option_id": "A", "text": "One"},
                {"option_id": "B", "text": "Two"},
            ],
            "answer": "B",
            "explanation": "Two is intended.",
            "marks": 1,
            "marking_guide": ["Selects B."],
            "marking_point_marks": [1],
        },
    }


def test_teacher_actions_require_bounded_structured_reason_codes_and_optional_notes() -> None:
    edit = ReviewQuestionEditRequest.model_validate(edit_payload())
    assert edit.reason_code is CorrectionReasonCode.AMBIGUOUS_WORDING
    assert edit.note == "The stem has two plausible readings."

    reject = ReviewQuestionRejectRequest.model_validate(
        {"expected_version": 4, "reason_code": "outside_scope", "note": None}
    )
    regenerate = ReviewQuestionRegenerateRequest.model_validate(
        {
            "expected_version": 5,
            "reason_code": "answer_incorrect",
            "note": "Recalculate the answer.",
        }
    )
    assert reject.reason_code is CorrectionReasonCode.OUTSIDE_SCOPE
    assert regenerate.reason_code is CorrectionReasonCode.ANSWER_INCORRECT
    assert (
        ReviewQuestionApproveRequest(
            expected_version=4,
            marking_confirmed=True,
            note="Meaningful confirmation.",
        ).note
        == "Meaningful confirmation."
    )
    with pytest.raises(ValidationError):
        ReviewQuestionApproveRequest(expected_version=4, marking_confirmed=True, note=" padded ")

    for payload in (
        {**edit_payload(), "reason_code": "free_form_not_allowed"},
        {**edit_payload(), "reason_code": "confirmed_quality"},
        {key: value for key, value in edit_payload().items() if key != "reason_code"},
        {**edit_payload(), "note": "x" * 769},
        {**edit_payload(), "note": " surrounding whitespace "},
    ):
        with pytest.raises(ValidationError):
            ReviewQuestionEditRequest.model_validate(payload)
    for contract in (ReviewQuestionRejectRequest, ReviewQuestionRegenerateRequest):
        with pytest.raises(ValidationError):
            contract.model_validate(
                {
                    "expected_version": 4,
                    "reason_code": "confirmed_quality",
                    "note": None,
                }
            )


def test_composed_event_reason_is_readable_bounded_and_does_not_infer_prompt_changes() -> None:
    assert compose_review_reason(ReviewReasonCode.ANSWER_INCORRECT, None) == "Answer is incorrect"
    assert (
        compose_review_reason(
            ReviewReasonCode.AMBIGUOUS_WORDING,
            "Two readings are possible.",
        )
        == "Wording is unclear or ambiguous — Two readings are possible."
    )
    assert len(compose_review_reason(ReviewReasonCode.OTHER_QUALITY_ISSUE, "x" * 768)) <= 1_024
    with pytest.raises(ValueError, match="supported review reason"):
        compose_review_reason(cast(ReviewReasonCode, "not-a-code"), None)
    for invalid_note in ("", " padded ", "x" * 769):
        with pytest.raises(ValueError, match="trimmed bounded text"):
            compose_review_reason(ReviewReasonCode.OTHER_QUALITY_ISSUE, invalid_note)


def test_promotion_approval_and_run_contracts_are_strict_and_bounded() -> None:
    promotion = SubjectQualityPromotionRequest.model_validate(
        {
            "expected_status": "fail",
            "expected_finding_codes": ["subject.math.answer_mismatch"],
            "defect_category": "answer_correctness",
        }
    )
    assert promotion.expected_finding_codes == ("subject.math.answer_mismatch",)
    assert SubjectQualityEvalApprovalRequest(expected_version=1).expected_version == 1
    assert SubjectQualityEvalRunRequest(case_ids=(UUID(int=1),)).case_ids == (UUID(int=1),)

    invalid_promotions = (
        {
            "expected_status": "fail",
            "expected_finding_codes": ["BAD CODE"],
            "defect_category": "answer_correctness",
        },
        {
            "expected_status": "pass",
            "expected_finding_codes": ["subject.math.answer_mismatch"],
            "defect_category": "no_defect",
        },
        {
            "expected_status": "fail",
            "expected_finding_codes": [],
            "defect_category": "answer_correctness",
        },
        {
            "expected_status": "warn",
            "expected_finding_codes": ["subject.scope.issue", "subject.scope.issue"],
            "defect_category": "scope_alignment",
        },
        {
            "expected_status": "pass",
            "expected_finding_codes": [],
            "defect_category": "answer_correctness",
        },
    )
    for payload in invalid_promotions:
        with pytest.raises(ValidationError):
            SubjectQualityPromotionRequest.model_validate(payload)
    with pytest.raises(ValidationError):
        SubjectQualityEvalRunRequest(case_ids=tuple(UUID(int=index) for index in range(1, 102)))
    with pytest.raises(ValidationError):
        SubjectQualityEvalRunRequest(case_ids=(UUID(int=1), UUID(int=1)))


def test_eval_snapshot_reconstructs_exact_trusted_scope_and_rejects_scope_spoofing() -> None:
    snapshot = replay_snapshot()
    validation_input = validation_input_from_eval_snapshot(
        snapshot,
        expected_curriculum_version_id=CURRICULUM_ID,
    )
    assert json.loads(validation_input._candidate_json) == snapshot["candidate"]
    assert validation_input.trusted_scope.grade == 7
    assert validation_input.trusted_scope.subject_code == "MATHEMATICS"
    assert validation_input.trusted_scope.unit_ids == (UNIT_ID,)
    assert validation_input.trusted_scope.lesson_ids == (LESSON_ID,)
    assert validation_input.grounding_sources[0].text.startswith("Reviewed lesson evidence")

    spoofed = deepcopy(snapshot)
    subject_scope = spoofed["subject_scope"]
    assert isinstance(subject_scope, dict)
    subject_scope["curriculum_version_id"] = str(UUID(int=999))
    with pytest.raises(ValueError, match="curriculum"):
        validation_input_from_eval_snapshot(
            spoofed,
            expected_curriculum_version_id=CURRICULUM_ID,
        )


def test_eval_snapshot_boundary_rejects_malformed_untrusted_and_cross_scope_values() -> None:
    with pytest.raises(ValueError, match="expected curriculum"):
        validation_input_from_eval_snapshot(
            replay_snapshot(),
            expected_curriculum_version_id=cast(UUID, "not-a-uuid"),
        )
    for malformed in (None, {1: "not-a-string-key"}):
        with pytest.raises(ValueError, match="eval snapshot"):
            validation_input_from_eval_snapshot(
                malformed,
                expected_curriculum_version_id=CURRICULUM_ID,
            )

    bad_schema = replay_snapshot()
    bad_schema["schema_version"] = "unknown"
    with pytest.raises(ValueError, match="schema"):
        validation_input_from_eval_snapshot(
            bad_schema,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    invalid_uuid = replay_snapshot()
    cast(dict[str, object], invalid_uuid["subject_scope"])["curriculum_version_id"] = "bad"
    with pytest.raises(ValueError, match="UUID"):
        validation_input_from_eval_snapshot(
            invalid_uuid,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    oversized_sequence = replay_snapshot()
    oversized_sequence["context_scope_bindings"] = [{}] * 129
    with pytest.raises(ValueError, match="bounded array"):
        validation_input_from_eval_snapshot(
            oversized_sequence,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    cross_binding = replay_snapshot()
    binding = cast(list[dict[str, object]], cross_binding["context_scope_bindings"])[0]
    binding["curriculum_version_id"] = str(UUID(int=999))
    with pytest.raises(ValueError, match="crosses curriculum"):
        validation_input_from_eval_snapshot(
            cross_binding,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    untrusted = replay_snapshot()
    source = cast(list[dict[str, object]], untrusted["grounding_sources"])[0]
    source["trust"] = "trusted_instruction"
    with pytest.raises(ValueError, match="untrusted"):
        validation_input_from_eval_snapshot(
            untrusted,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    invalid_text = replay_snapshot()
    invalid_text["candidate_id"] = " padded "
    with pytest.raises(ValueError, match="trimmed text"):
        validation_input_from_eval_snapshot(
            invalid_text,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    invalid_integer = replay_snapshot()
    cast(dict[str, object], invalid_integer["blueprint"])["marks"] = True
    with pytest.raises(ValueError, match="bounded integer"):
        validation_input_from_eval_snapshot(
            invalid_integer,
            expected_curriculum_version_id=CURRICULUM_ID,
        )

    invalid_contract = replay_snapshot()
    cast(dict[str, object], invalid_contract["subject_scope"])["subject_code"] = "lowercase"
    with pytest.raises(ValueError, match="validation input contract"):
        validation_input_from_eval_snapshot(
            invalid_contract,
            expected_curriculum_version_id=CURRICULUM_ID,
        )


def test_eval_snapshot_supports_absent_optional_provenance_and_bounded_duplicates() -> None:
    snapshot = replay_snapshot()
    source = cast(list[dict[str, object]], snapshot["grounding_sources"])[0]
    source.update(
        {
            "source_document_id": None,
            "source_version": None,
            "page_number": None,
            "chunk_id": None,
        }
    )
    binding = cast(list[dict[str, object]], snapshot["context_scope_bindings"])[0]
    binding.update(
        {
            "unit_id": None,
            "lesson_id": None,
            "snapshot_unit_id": None,
            "snapshot_lesson_id": None,
        }
    )
    snapshot["duplicate_references"] = [
        {"question_id": "historic-text", "text": "A prior question.", "content_sha256": None},
        {"question_id": "historic-hash", "text": None, "content_sha256": "0" * 64},
    ]
    result = validation_input_from_eval_snapshot(
        snapshot,
        expected_curriculum_version_id=CURRICULUM_ID,
    )
    assert result.grounding_sources[0].page_number is None
    assert len(result.duplicate_references) == 2


def test_eval_comparison_rejects_malformed_expectations_and_reports() -> None:
    passing = report("schema.completeness", FindingStatus.PASS)
    with pytest.raises(ValueError, match="expected status"):
        compare_eval_report(
            expected_status=cast(FindingStatus, "pass"),
            expected_finding_codes=(),
            report=passing,
        )
    for invalid_codes in (cast(tuple[str, ...], ["code"]), ("",), ("same", "same")):
        with pytest.raises(ValueError, match="expected finding codes"):
            compare_eval_report(
                expected_status=FindingStatus.PASS,
                expected_finding_codes=invalid_codes,
                report=passing,
            )
    with pytest.raises(ValueError, match="report"):
        compare_eval_report(
            expected_status=FindingStatus.PASS,
            expected_finding_codes=(),
            report=cast(ValidationReport, object()),
        )
    with pytest.raises(ValueError, match="pipeline"):
        evaluate_snapshot(
            snapshot=replay_snapshot(),
            expected_curriculum_version_id=CURRICULUM_ID,
            expected_status=FindingStatus.PASS,
            expected_finding_codes=(),
            pipeline=cast(ValidationPipeline, object()),
        )


def test_eval_comparison_is_deterministic_for_good_regression_and_unavailable_cases() -> None:
    good = compare_eval_report(
        expected_status=FindingStatus.PASS,
        expected_finding_codes=(),
        report=report("schema.completeness", FindingStatus.PASS),
    )
    duplicate_good = compare_eval_report(
        expected_status=FindingStatus.PASS,
        expected_finding_codes=(),
        report=report("schema.completeness", FindingStatus.PASS),
    )
    assert good.outcome is EvalComparisonOutcome.PASS
    assert good.passed is True
    assert duplicate_good.fingerprint == good.fingerprint

    regression = compare_eval_report(
        expected_status=FindingStatus.PASS,
        expected_finding_codes=(),
        report=report("subject.math.answer_mismatch", FindingStatus.FAIL),
    )
    assert regression.outcome is EvalComparisonOutcome.REGRESSION
    assert regression.passed is False
    assert regression.actual_status is FindingStatus.FAIL
    assert regression.actual_finding_codes == ("subject.math.answer_mismatch",)

    unavailable = compare_eval_report(
        expected_status=FindingStatus.WARN,
        expected_finding_codes=("subject.factual.verifier_unavailable",),
        report=report("subject.factual.verifier_unavailable", FindingStatus.WARN),
    )
    assert unavailable.outcome is EvalComparisonOutcome.UNAVAILABLE
    assert unavailable.passed is False

    deterministic_failure = report("subject.math.answer_mismatch", FindingStatus.FAIL)
    verifier_unavailable = report("subject.factual.verifier_unavailable", FindingStatus.WARN)
    mixed = ValidationReport(
        candidate_id="a" * 64,
        pipeline_version="fixture-pipeline.v1",
        findings=deterministic_failure.findings + verifier_unavailable.findings,
    )
    mixed_regression = compare_eval_report(
        expected_status=FindingStatus.PASS,
        expected_finding_codes=(),
        report=mixed,
    )
    assert mixed_regression.outcome is EvalComparisonOutcome.REGRESSION
    assert mixed_regression.passed is False


def test_runner_replays_good_and_regression_cases_through_the_pipeline() -> None:
    good = evaluate_snapshot(
        snapshot=replay_snapshot(),
        expected_curriculum_version_id=CURRICULUM_ID,
        expected_status=FindingStatus.PASS,
        expected_finding_codes=(),
        pipeline=ValidationPipeline(
            validators=(FixedValidator(FindingStatus.PASS, "schema.completeness"),),
            version="fixed-good-pipeline.v1",
        ),
    )
    assert good.comparison.outcome is EvalComparisonOutcome.PASS
    assert good.comparison.passed is True

    regression = evaluate_snapshot(
        snapshot=replay_snapshot(),
        expected_curriculum_version_id=CURRICULUM_ID,
        expected_status=FindingStatus.PASS,
        expected_finding_codes=(),
        pipeline=ValidationPipeline(
            validators=(FixedValidator(FindingStatus.FAIL, "subject.math.answer_mismatch"),),
            version="fixed-regression-pipeline.v1",
        ),
    )
    assert regression.comparison.outcome is EvalComparisonOutcome.REGRESSION
    assert regression.comparison.passed is False


def test_runner_replays_current_pipeline_without_provider_and_marks_semantic_case_unavailable() -> (
    None
):
    snapshot = replay_snapshot()
    subject_scope = snapshot["subject_scope"]
    assert isinstance(subject_scope, dict)
    subject_scope["subject_code"] = "ENVIRONMENT"

    result = evaluate_snapshot(
        snapshot=snapshot,
        expected_curriculum_version_id=CURRICULUM_ID,
        expected_status=FindingStatus.WARN,
        expected_finding_codes=("subject.factual.verifier_unavailable",),
        pipeline=build_default_pipeline(),
    )

    assert result.comparison.outcome is EvalComparisonOutcome.UNAVAILABLE
    assert result.comparison.passed is False
    assert "subject.factual.verifier_unavailable" in result.comparison.actual_finding_codes
    assert result.validator_versions
    assert result.pipeline_version == "deterministic-question-validation.v5"


def test_canonical_fingerprint_is_order_stable_and_sensitive_to_snapshot_content() -> None:
    left = {"scope": {"grade": 7, "subject": "MATHEMATICS"}, "codes": ["a", "b"]}
    right = {"codes": ["a", "b"], "scope": {"subject": "MATHEMATICS", "grade": 7}}
    assert canonical_fingerprint(left) == canonical_fingerprint(right)
    assert canonical_fingerprint(left) != canonical_fingerprint({**right, "codes": ["b", "a"]})
