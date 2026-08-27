import ast
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID

import pytest

import exam_guru_api.validation.maths as maths_validation
from exam_guru_api.validation import (
    ContextScopeBinding,
    FindingEvidence,
    FindingStatus,
    GeneratedSubjectScope,
    GroundedSemanticVerifier,
    SemanticEvidenceReference,
    SemanticVerificationRequest,
    SemanticVerificationResult,
    SemanticVerificationStatus,
    SemanticVerifierAccounting,
    SubjectFindingCode,
    SubjectValidationContext,
    SubjectValidationRouter,
    TrustedSubjectScope,
    TrustedSubjectScopeValidator,
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
    ValidationReport,
    build_default_pipeline,
)
from tests.test_validation_fixtures import valid_candidate, validation_input

MATHS_SUBJECT_ID = UUID("10000000-0000-4000-8000-000000000001")
SCIENCE_SUBJECT_ID = UUID("10000000-0000-4000-8000-000000000002")
CURRICULUM_ID = UUID("20000000-0000-4000-8000-000000000001")
UNIT_ID = UUID("30000000-0000-4000-8000-000000000001")
LESSON_ID = UUID("40000000-0000-4000-8000-000000000001")
OTHER_LESSON_ID = UUID("40000000-0000-4000-8000-000000000002")


def trusted_scope(
    *,
    subject_id: UUID = MATHS_SUBJECT_ID,
    subject_code: str = "MATHEMATICS",
    unit_ids: tuple[UUID, ...] = (),
    lesson_ids: tuple[UUID, ...] = (),
) -> TrustedSubjectScope:
    return TrustedSubjectScope(
        grade=5,
        medium="en",
        subject_id=subject_id,
        subject_code=subject_code,
        curriculum_version_id=CURRICULUM_ID,
        unit_ids=unit_ids,
        lesson_ids=lesson_ids,
    )


def generated_scope(scope: TrustedSubjectScope) -> GeneratedSubjectScope:
    return GeneratedSubjectScope(
        grade=scope.grade,
        medium=scope.medium,
        subject_id=scope.subject_id,
        curriculum_version_id=scope.curriculum_version_id,
        unit_ids=scope.unit_ids,
        lesson_ids=scope.lesson_ids,
    )


def candidate(
    *,
    stem: str,
    options: tuple[tuple[str, str], ...],
    correct_option_id: str,
    explanation: str = "Compute the expression independently.",
    marking_description: str = "Awards the marks for the correct answer.",
) -> dict[str, object]:
    value = valid_candidate()
    value["stem"] = stem
    value["options"] = [{"option_id": option_id, "text": text} for option_id, text in options]
    value["answer"] = {
        "correct_option_id": correct_option_id,
        "accepted_responses": [],
        "explanation": explanation,
    }
    value["marking"] = {
        "total_marks": 2,
        "criteria": [
            {
                "criterion_id": "correct-answer",
                "description": marking_description,
                "marks": 2,
            }
        ],
    }
    return value


def subject_input(
    value: dict[str, object],
    *,
    scope: TrustedSubjectScope | None = None,
    observed: GeneratedSubjectScope | None = None,
    context_scope_bindings: tuple[ContextScopeBinding, ...] = (),
) -> ValidationInput:
    active_scope = trusted_scope() if scope is None else scope
    return replace(
        validation_input(candidate=value),
        trusted_scope=active_scope,
        generated_scope=generated_scope(active_scope) if observed is None else observed,
        context_scope_bindings=context_scope_bindings,
    )


def findings_by_code(report: ValidationReport) -> dict[str, ValidationFinding]:
    return {str(finding.code): finding for finding in report.findings}


@pytest.mark.parametrize(
    ("stem", "options", "answer"),
    [
        ("What is 27 + 15?", (("A", "41"), ("B", "42"), ("C", "43")), "B"),
        ("What is 1/2 + 1/4?", (("A", "3/4"), ("B", "1/4"), ("C", "1")), "A"),
        ("What is 25% of 80?", (("A", "5"), ("B", "20"), ("C", "40")), "B"),
    ],
)
def test_maths_recomputes_supported_exact_arithmetic_fraction_and_percentage(
    stem: str,
    options: tuple[tuple[str, str], ...],
    answer: str,
) -> None:
    report = build_default_pipeline().validate(
        subject_input(candidate(stem=stem, options=options, correct_option_id=answer))
    )
    by_code = findings_by_code(report)

    assert report.overall_status is FindingStatus.PASS
    assert by_code[SubjectFindingCode.MATH_ANSWER_MISMATCH].status is FindingStatus.PASS
    assert by_code[SubjectFindingCode.MATH_MULTIPLE_CORRECT_OPTIONS].status is FindingStatus.PASS
    assert by_code[SubjectFindingCode.MARKING_ANSWER_INCONSISTENT].status is FindingStatus.PASS


def test_maths_rejects_wrong_numeric_answer_despite_plausible_explanation() -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 27 + 15?",
                options=(("A", "41"), ("B", "42"), ("C", "43")),
                correct_option_id="A",
                explanation="Add the ones, regroup one ten, and then add the tens.",
            )
        )
    )

    finding = findings_by_code(report)[SubjectFindingCode.MATH_ANSWER_MISMATCH]
    assert report.overall_status is FindingStatus.FAIL
    assert finding.status is FindingStatus.FAIL
    assert "41" not in finding.message


def test_maths_accepts_one_equivalent_answer_representation() -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 1/4 + 1/4?",
                options=(("A", "0.5"), ("B", "3/4"), ("C", "1")),
                correct_option_id="A",
            )
        )
    )

    assert report.overall_status is FindingStatus.PASS
    assert (
        findings_by_code(report)[SubjectFindingCode.MATH_ANSWER_MISMATCH].status
        is FindingStatus.PASS
    )


def test_maths_rejects_two_correct_equivalent_options_and_duplicate_equivalents() -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 1/4 + 1/4?",
                options=(("A", "1/2"), ("B", "0.5"), ("C", "3/4")),
                correct_option_id="A",
            )
        )
    )
    by_code = findings_by_code(report)

    assert report.overall_status is FindingStatus.FAIL
    assert by_code[SubjectFindingCode.MATH_MULTIPLE_CORRECT_OPTIONS].status is FindingStatus.FAIL
    assert (
        by_code[SubjectFindingCode.MATH_DUPLICATE_EQUIVALENT_OPTIONS].status is FindingStatus.FAIL
    )


def test_maths_rejects_duplicate_equivalent_distractors() -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 1 + 1?",
                options=(("A", "2"), ("B", "3"), ("C", "6/2")),
                correct_option_id="A",
            )
        )
    )

    assert report.overall_status is FindingStatus.FAIL
    assert (
        findings_by_code(report)[SubjectFindingCode.MATH_DUPLICATE_EQUIVALENT_OPTIONS].status
        is FindingStatus.FAIL
    )


def test_maths_rejects_answer_and_marking_inconsistency() -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 27 + 15?",
                options=(("A", "41"), ("B", "42"), ("C", "43")),
                correct_option_id="B",
                marking_description="Award two marks when the answer is 41.",
            )
        )
    )

    assert report.overall_status is FindingStatus.FAIL
    assert (
        findings_by_code(report)[SubjectFindingCode.MARKING_ANSWER_INCONSISTENT].status
        is FindingStatus.FAIL
    )


@pytest.mark.parametrize(
    ("stem", "expected_code"),
    [
        ("What is 1 m + 50 cm?", SubjectFindingCode.MATH_UNIT_MISMATCH),
        (
            "A pattern starts with one triangle. What comes next?",
            SubjectFindingCode.MATH_UNSUPPORTED_EXPRESSION,
        ),
    ],
)
def test_maths_units_and_unsupported_or_underspecified_expressions_warn(
    stem: str,
    expected_code: SubjectFindingCode,
) -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem=stem,
                options=(("A", "1"), ("B", "2"), ("C", "3")),
                correct_option_id="A",
            )
        )
    )

    assert report.overall_status is FindingStatus.WARN
    assert findings_by_code(report)[expected_code].status is FindingStatus.WARN


@pytest.mark.parametrize(
    "expression",
    [
        "-" * 200 + "1",
        "9**9**9**9**9**9**9**9",
        "(" * 100 + "1" + ")" * 100,
        "1 << 999999999999999999999999",
        "__import__('os').system('id')",
    ],
)
def test_maths_pathological_ast_input_is_bounded_and_never_executed(expression: str) -> None:
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem=f"What is {expression}?",
                options=(("A", "1"), ("B", "2"), ("C", "3")),
                correct_option_id="A",
            )
        )
    )

    assert report.overall_status is FindingStatus.WARN
    assert (
        findings_by_code(report)[SubjectFindingCode.MATH_UNSUPPORTED_EXPRESSION].status
        is FindingStatus.WARN
    )


def test_trusted_scope_rejects_subject_spoof_and_selected_lesson_leak() -> None:
    scope = trusted_scope(unit_ids=(UNIT_ID,), lesson_ids=(LESSON_ID,))
    spoofed = replace(generated_scope(scope), subject_id=SCIENCE_SUBJECT_ID)
    binding = ContextScopeBinding(
        context_id="context-01",
        curriculum_version_id=CURRICULUM_ID,
        subject_id=MATHS_SUBJECT_ID,
        unit_id=UNIT_ID,
        lesson_id=OTHER_LESSON_ID,
        snapshot_unit_id=UNIT_ID,
        snapshot_lesson_id=OTHER_LESSON_ID,
    )
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 2 + 2?",
                options=(("A", "3"), ("B", "4"), ("C", "5")),
                correct_option_id="B",
            ),
            scope=scope,
            observed=spoofed,
            context_scope_bindings=(binding,),
        )
    )
    by_code = findings_by_code(report)

    assert report.overall_status is FindingStatus.FAIL
    assert by_code[SubjectFindingCode.SCOPE_SUBJECT_MISMATCH].status is FindingStatus.FAIL
    assert by_code[SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_LESSON].status is FindingStatus.FAIL


def test_unknown_subject_routes_to_explicit_warning_not_universal_pass() -> None:
    scope = trusted_scope(subject_id=SCIENCE_SUBJECT_ID, subject_code="UNREGISTERED_SUBJECT")
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="Which answer is supported?",
                options=(("A", "One"), ("B", "Two"), ("C", "Three")),
                correct_option_id="A",
            ),
            scope=scope,
        )
    )

    assert report.overall_status is FindingStatus.WARN
    finding = findings_by_code(report)[SubjectFindingCode.SUBJECT_UNREGISTERED]
    assert finding.status is FindingStatus.WARN
    assert finding.validator_id == "subject-validation-router"


class FakeSemanticVerifier(GroundedSemanticVerifier):
    verifier_id = "deterministic-semantic-fake"
    verifier_version = "1.0.0"
    prompt_version = "subject-factual-test.v1"
    provider = "deterministic"
    provider_version = "1.0.0"
    model = "fixture-semantic-model"
    model_version = "fixture-semantic-model-v1"
    pricing_version = "zero-cost-v1"

    def __init__(self, status: SemanticVerificationStatus) -> None:
        self.status = status
        self.requests: list[SemanticVerificationRequest] = []

    def verify(self, request: SemanticVerificationRequest) -> SemanticVerificationResult:
        self.requests.append(request)
        return SemanticVerificationResult(
            status=self.status,
            summary=f"fixture-{self.status.value}",
            evidence_refs=(
                SemanticEvidenceReference(
                    context_id="context-01",
                    source_document_id="curriculum-grade-5-maths",
                    page_number=7,
                ),
            ),
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            prompt_version=self.prompt_version,
            provider=self.provider,
            provider_version=self.provider_version,
            model=self.model,
            model_version=self.model_version,
            pricing_version=self.pricing_version,
            accounting=SemanticVerifierAccounting(10, 5, 15, 0, 1),
        )


def factual_input() -> ValidationInput:
    scope = trusted_scope(subject_id=SCIENCE_SUBJECT_ID, subject_code="SCIENCE")
    return subject_input(
        candidate(
            stem="Which answer is supported by the reviewed lesson?",
            options=(("A", "Water freezes at 0 C."), ("B", "Water freezes at 50 C.")),
            correct_option_id="A",
        ),
        scope=scope,
    )


@pytest.mark.parametrize(
    ("semantic_status", "overall_status", "finding_code", "finding_status"),
    [
        (
            SemanticVerificationStatus.SUPPORTED,
            FindingStatus.PASS,
            SubjectFindingCode.FACTUAL_GROUNDED,
            FindingStatus.PASS,
        ),
        (
            SemanticVerificationStatus.CONTRADICTED,
            FindingStatus.FAIL,
            SubjectFindingCode.FACTUAL_SOURCE_CONTRADICTION,
            FindingStatus.FAIL,
        ),
        (
            SemanticVerificationStatus.INSUFFICIENT_EVIDENCE,
            FindingStatus.WARN,
            SubjectFindingCode.FACTUAL_UNSUPPORTED_CLAIM,
            FindingStatus.WARN,
        ),
    ],
)
def test_grounded_semantic_fake_maps_structured_status_and_evidence(
    semantic_status: SemanticVerificationStatus,
    overall_status: FindingStatus,
    finding_code: SubjectFindingCode,
    finding_status: FindingStatus,
) -> None:
    verifier = FakeSemanticVerifier(semantic_status)
    report = build_default_pipeline(semantic_verifier=verifier).validate(factual_input())
    finding = findings_by_code(report)[finding_code]

    assert report.overall_status is overall_status
    assert finding.status is finding_status
    assert verifier.requests[0].subject_code == "SCIENCE"
    assert verifier.requests[0].grounding_sources[0].context_id == "context-01"
    assert any("context-01" in evidence.observed for evidence in finding.evidence)


def test_factual_verification_without_provider_is_warning_not_pass() -> None:
    report = build_default_pipeline().validate(factual_input())

    assert report.overall_status is FindingStatus.WARN
    assert (
        findings_by_code(report)[SubjectFindingCode.FACTUAL_VERIFIER_UNAVAILABLE].status
        is FindingStatus.WARN
    )


def test_router_rejects_ambiguous_or_unbounded_registration() -> None:
    router = build_default_pipeline().subject_router
    assert router is not None
    maths = next(
        validator for validator in router.validators if "MATHEMATICS" in validator.subject_codes
    )

    with pytest.raises(ValidationContractError, match="registered once"):
        SubjectValidationRouter(validators=(maths, maths), fallback_validator=None)
    with pytest.raises(ValidationContractError, match="bounded"):
        SubjectValidationRouter(
            validators=tuple(maths for _ in range(33)),
            fallback_validator=None,
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: TrustedSubjectScope(
            5,
            "en",
            cast(UUID, "bad-subject"),
            "MATHEMATICS",
            CURRICULUM_ID,
        ),
        lambda: TrustedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            "bad subject",
            CURRICULUM_ID,
        ),
        lambda: TrustedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            "MATHEMATICS",
            cast(UUID, "bad-curriculum"),
        ),
        lambda: TrustedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            "MATHEMATICS",
            CURRICULUM_ID,
            cast(tuple[UUID, ...], [UNIT_ID]),
        ),
        lambda: TrustedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            "MATHEMATICS",
            CURRICULUM_ID,
            (UNIT_ID, UNIT_ID),
        ),
        lambda: TrustedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            "MATHEMATICS",
            CURRICULUM_ID,
            (),
            (LESSON_ID,),
        ),
        lambda: GeneratedSubjectScope(
            5,
            "en",
            cast(UUID, "bad-subject"),
            CURRICULUM_ID,
        ),
        lambda: GeneratedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            cast(UUID, "bad-curriculum"),
        ),
        lambda: GeneratedSubjectScope(
            5,
            "en",
            MATHS_SUBJECT_ID,
            CURRICULUM_ID,
            (),
            (LESSON_ID,),
        ),
        lambda: ContextScopeBinding(
            "context-01",
            CURRICULUM_ID,
            MATHS_SUBJECT_ID,
            cast(UUID, "bad-unit"),
            None,
            None,
            None,
        ),
        lambda: ContextScopeBinding(
            "context-01",
            CURRICULUM_ID,
            MATHS_SUBJECT_ID,
            None,
            LESSON_ID,
            None,
            None,
        ),
        lambda: ContextScopeBinding(
            "context-01",
            CURRICULUM_ID,
            MATHS_SUBJECT_ID,
            None,
            None,
            None,
            LESSON_ID,
        ),
    ],
)
def test_subject_scope_contracts_reject_malformed_trusted_values(
    build: Callable[[], object],
) -> None:
    with pytest.raises(ValidationContractError):
        build()


def test_validation_input_rejects_malformed_subject_scope_members() -> None:
    request = validation_input()
    binding = ContextScopeBinding(
        "context-01",
        CURRICULUM_ID,
        MATHS_SUBJECT_ID,
        None,
        None,
        None,
        None,
    )
    invalid_values = (
        {"trusted_scope": cast(TrustedSubjectScope, "bad")},
        {"generated_scope": cast(GeneratedSubjectScope, "bad")},
        {"context_scope_bindings": cast(tuple[ContextScopeBinding, ...], [binding])},
        {"context_scope_bindings": (binding, binding)},
    )
    for values in invalid_values:
        with pytest.raises(ValidationContractError):
            replace(request, **values)


def test_scope_validator_reports_every_generation_and_context_scope_mismatch() -> None:
    scope = trusted_scope(unit_ids=(UNIT_ID,), lesson_ids=(LESSON_ID,))
    other_curriculum = UUID(int=202)
    other_unit = UUID(int=303)
    generated = GeneratedSubjectScope(
        grade=6,
        medium="si",
        subject_id=SCIENCE_SUBJECT_ID,
        curriculum_version_id=other_curriculum,
        unit_ids=(other_unit,),
        lesson_ids=(OTHER_LESSON_ID,),
    )
    binding = ContextScopeBinding(
        context_id="context-01",
        curriculum_version_id=other_curriculum,
        subject_id=SCIENCE_SUBJECT_ID,
        unit_id=other_unit,
        lesson_id=OTHER_LESSON_ID,
        snapshot_unit_id=UNIT_ID,
        snapshot_lesson_id=LESSON_ID,
    )
    report = build_default_pipeline().validate(
        subject_input(
            candidate(
                stem="What is 2 + 2?",
                options=(("A", "3"), ("B", "4"), ("C", "5")),
                correct_option_id="B",
            ),
            scope=scope,
            observed=generated,
            context_scope_bindings=(binding,),
        )
    )
    codes = {
        finding.code
        for finding in report.failures
        if finding.validator_id == "trusted-subject-scope"
    }

    assert codes == {
        SubjectFindingCode.SCOPE_GRADE_MISMATCH,
        SubjectFindingCode.SCOPE_MEDIUM_MISMATCH,
        SubjectFindingCode.SCOPE_SUBJECT_MISMATCH,
        SubjectFindingCode.SCOPE_CURRICULUM_MISMATCH,
        SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_UNIT,
        SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_LESSON,
    }


def test_scope_validator_defensive_boundaries_fail_closed() -> None:
    request = subject_input(
        candidate(
            stem="What is 2 + 2?",
            options=(("A", "3"), ("B", "4"), ("C", "5")),
            correct_option_id="B",
        )
    )
    object.__setattr__(request, "generated_scope", None)
    with pytest.raises(ValidationContractError, match="generated scope"):
        TrustedSubjectScopeValidator().validate(request)

    binding = ContextScopeBinding(
        "context-01",
        CURRICULUM_ID,
        MATHS_SUBJECT_ID,
        None,
        None,
        None,
        None,
    )
    with pytest.raises(ValidationContractError, match="collector"):
        TrustedSubjectScopeValidator._check_context_binding(
            trusted_scope(),
            binding,
            object(),
        )
    with pytest.raises(ValidationContractError, match="ValidationInput"):
        SubjectValidationContext.from_input(cast(ValidationInput, object()))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1e3",
        "1000000000001",
        "1 / 0",
        "1 -",
        "1 // 1",
        "1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 + 11 + 12 + 13 + 14 + 15 + 16 + 17 + 18",
    ],
)
def test_safe_math_parser_rejects_unsupported_boundaries(value: str) -> None:
    with pytest.raises(ValueError, match=r"expression|numeric|operator|division"):
        maths_validation.parse_exact_expression(value)


def test_safe_math_parser_covers_exact_unary_and_binary_operations() -> None:
    assert maths_validation.parse_exact_expression("-2 + +5 * 2 - 3 / 1") == 5
    assert maths_validation.parse_exact_expression("50%") == 1 / 2
    assert maths_validation.parse_exact_expression("6 \N{DIVISION SIGN} 2") == 3


@pytest.mark.parametrize(
    "operation",
    [
        lambda: maths_validation._decimal_fraction("not-a-number"),
        lambda: maths_validation._normalise_expression(cast(str, None), question=False),
        lambda: maths_validation._normalise_expression("1 m + 2 cm", question=True),
        lambda: maths_validation._normalise_expression("1%" * 100, question=False),
        lambda: maths_validation._validate_tokens("("),
        lambda: maths_validation._evaluate_node(
            ast.Constant(value="text"),
            "'text'",
            depth=0,
            budget=maths_validation._ParseBudget(),
        ),
        lambda: maths_validation._evaluate_node(
            ast.Constant(value=1),
            "1",
            depth=13,
            budget=maths_validation._ParseBudget(),
        ),
        lambda: maths_validation._evaluate_node(
            ast.Constant(value=1),
            "1",
            depth=0,
            budget=maths_validation._ParseBudget(),
        ),
        lambda: maths_validation._evaluate_node(
            ast.UnaryOp(op=ast.USub(), operand=ast.Constant(1)),
            "-1",
            depth=0,
            budget=maths_validation._ParseBudget(operators=16),
        ),
        lambda: maths_validation._evaluate_node(
            ast.BinOp(left=ast.Constant(1), op=ast.Add(), right=ast.Constant(1)),
            "1 + 1",
            depth=0,
            budget=maths_validation._ParseBudget(operators=16),
        ),
        lambda: maths_validation._evaluate_node(
            ast.Name(id="x"),
            "x",
            depth=0,
            budget=maths_validation._ParseBudget(),
        ),
    ],
)
def test_safe_math_parser_internal_guards_reject_without_execution(
    operation: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match=r"expression|numeric|literal|operator"):
        operation()


def test_maths_malformed_candidate_and_marking_shapes_warn_without_crashing() -> None:
    cases = []
    non_text_stem = valid_candidate()
    non_text_stem["stem"] = 42
    cases.append(non_text_stem)
    bad_options = valid_candidate()
    bad_options["options"] = "not-options"
    cases.append(bad_options)
    non_mapping_option = valid_candidate()
    non_mapping_option["options"] = ["bad"]
    cases.append(non_mapping_option)
    non_text_option = valid_candidate()
    non_text_option["options"] = [{"option_id": "A", "text": 1}]
    cases.append(non_text_option)
    unsupported_option = valid_candidate()
    cast(list[dict[str, object]], unsupported_option["options"])[0]["text"] = "not numeric"
    cases.append(unsupported_option)

    for value in cases:
        report = build_default_pipeline().validate(subject_input(value))
        assert SubjectFindingCode.MATH_UNSUPPORTED_EXPRESSION in findings_by_code(report)

    malformed_marking = candidate(
        stem="What is 27 + 15?",
        options=(("A", "41"), ("B", "42"), ("C", "43")),
        correct_option_id="B",
    )
    malformed_marking["marking"] = {
        "total_marks": 2,
        "criteria": [
            "bad",
            {"description": 1},
            {"description": "The answer is 999999999999999999999999999999."},
        ],
    }
    report = build_default_pipeline().validate(subject_input(malformed_marking))
    assert SubjectFindingCode.MARKING_ANSWER_INCONSISTENT in findings_by_code(report)

    non_list_criteria = candidate(
        stem="What is 27 + 15?",
        options=(("A", "41"), ("B", "42"), ("C", "43")),
        correct_option_id="B",
    )
    non_list_criteria["marking"] = {"total_marks": 2, "criteria": "not-a-list"}
    report = build_default_pipeline().validate(subject_input(non_list_criteria))
    assert SubjectFindingCode.MARKING_ANSWER_INCONSISTENT in findings_by_code(report)


def semantic_result(**changes: object) -> SemanticVerificationResult:
    values: dict[str, object] = {
        "status": SemanticVerificationStatus.SUPPORTED,
        "summary": "supported fixture",
        "evidence_refs": (
            SemanticEvidenceReference(
                "context-01",
                "curriculum-grade-5-maths",
                7,
            ),
        ),
        "verifier_id": "deterministic-semantic-fake",
        "verifier_version": "1.0.0",
        "prompt_version": "subject-factual-test.v1",
        "provider": "deterministic",
        "provider_version": "1.0.0",
        "model": "fixture-semantic-model",
        "model_version": "fixture-semantic-model-v1",
        "pricing_version": "zero-cost-v1",
        "accounting": SemanticVerifierAccounting(10, 5, 15, 0, 1),
    }
    values.update(changes)
    return SemanticVerificationResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "build",
    [
        lambda: SemanticEvidenceReference("", "source", 1),
        lambda: SemanticEvidenceReference("context", "source", 0),
        lambda: semantic_result(status=cast(SemanticVerificationStatus, "supported")),
        lambda: semantic_result(summary=" "),
        lambda: semantic_result(evidence_refs=cast(tuple[SemanticEvidenceReference, ...], [])),
        lambda: semantic_result(
            evidence_refs=(
                SemanticEvidenceReference("context-01", "source", 1),
                SemanticEvidenceReference("context-01", "source", 1),
            )
        ),
        lambda: semantic_result(verifier_id="bad verifier"),
        lambda: semantic_result(accounting=cast(SemanticVerifierAccounting, object())),
    ],
)
def test_semantic_contracts_reject_malformed_status_lineage_and_evidence(
    build: Callable[[], object],
) -> None:
    with pytest.raises(ValidationContractError):
        build()


class InvalidSemanticVerifier(FakeSemanticVerifier):
    def __init__(self, result: object) -> None:
        super().__init__(SemanticVerificationStatus.SUPPORTED)
        self.result = result

    def verify(self, request: SemanticVerificationRequest) -> SemanticVerificationResult:
        self.requests.append(request)
        return cast(SemanticVerificationResult, self.result)


@pytest.mark.parametrize(
    "result",
    [
        object(),
        semantic_result(verifier_version="forged"),
        semantic_result(evidence_refs=(SemanticEvidenceReference("other-context", "source", 1),)),
        semantic_result(evidence_refs=()),
    ],
)
def test_invalid_semantic_provider_results_degrade_to_warning(result: object) -> None:
    verifier = InvalidSemanticVerifier(result)
    report = build_default_pipeline(semantic_verifier=verifier).validate(factual_input())

    assert report.overall_status is FindingStatus.WARN
    assert (
        findings_by_code(report)[SubjectFindingCode.FACTUAL_VERIFIER_UNAVAILABLE].status
        is FindingStatus.WARN
    )


def test_grounded_validator_defensively_rejects_missing_configured_verifier() -> None:
    pipeline = build_default_pipeline()
    assert pipeline.subject_router is not None
    fallback = pipeline.subject_router.fallback_validator
    assert fallback is not None
    with pytest.raises(ValidationContractError, match="not configured"):
        fallback._validate_result(
            SubjectValidationContext.from_input(factual_input()),
            semantic_result(),
        )


@dataclass(frozen=True, slots=True)
class RouterFixtureValidator:
    subject_codes: frozenset[str]
    validator_id: str
    validator_version: str = "1.0.0"
    findings: object = ()

    def validate(self, context: SubjectValidationContext) -> tuple[ValidationFinding, ...]:
        del context
        return cast(tuple[ValidationFinding, ...], self.findings)


def fixture_finding(
    *,
    validator_id: str = "fixture-subject",
    validator_version: str = "1.0.0",
) -> ValidationFinding:
    return ValidationFinding(
        validator_id=validator_id,
        validator_version=validator_version,
        code="subject.fixture",
        status=FindingStatus.PASS,
        message="Fixture subject finding.",
        evidence=(FindingEvidence("$", "fixture", "fixture"),),
    )


def test_router_registration_and_output_guards_are_bounded_and_versioned() -> None:
    malformed = RouterFixtureValidator(frozenset(), "malformed")
    with pytest.raises(ValidationContractError, match="malformed"):
        SubjectValidationRouter((malformed,), None)

    first = RouterFixtureValidator(frozenset({"SCIENCE"}), "first")
    second = RouterFixtureValidator(frozenset({"SCIENCE"}), "second")
    with pytest.raises(ValidationContractError, match="subject code"):
        SubjectValidationRouter((first, second), None)

    many_codes = RouterFixtureValidator(
        frozenset(f"SUBJECT_{index}" for index in range(129)),
        "many-codes",
    )
    with pytest.raises(ValidationContractError, match="bounded"):
        SubjectValidationRouter((many_codes,), None)

    valid = RouterFixtureValidator(
        frozenset({"SCIENCE"}),
        "fixture-subject",
        findings=(fixture_finding(),),
    )
    router = SubjectValidationRouter((valid,), None)
    assert router.registration_lineage == (
        ("fixture-subject", "1.0.0"),
        ("subject-validation-router", "1.0.0"),
    )
    with pytest.raises(ValidationContractError, match="context"):
        router.validate(cast(SubjectValidationContext, object()))

    with pytest.raises(ValidationContractError, match="malformed findings"):
        SubjectValidationRouter(
            (replace(valid, findings=()),),
            None,
        ).validate(SubjectValidationContext.from_input(factual_input()))
    with pytest.raises(ValidationContractError, match="foreign lineage"):
        SubjectValidationRouter(
            (
                replace(
                    valid,
                    findings=(fixture_finding(validator_id="foreign"),),
                ),
            ),
            None,
        ).validate(SubjectValidationContext.from_input(factual_input()))
    with pytest.raises(ValidationContractError, match="fallback"):
        SubjectValidationRouter((valid,), cast(Any, object()))
