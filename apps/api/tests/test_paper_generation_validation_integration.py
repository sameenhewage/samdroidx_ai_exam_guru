import json
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

import exam_guru_api.papers.adapters as paper_adapters
from exam_guru_api.blueprints import QuestionType
from exam_guru_api.generation import (
    GeneratedQuestion,
    GenerationIdentity,
    GenerationResult,
    MarkingCriterion,
    MarkingScheme,
    QuestionAnswer,
)
from exam_guru_api.papers import (
    CandidateState,
    GenerationValidationAdapterError,
    GenerationValidationMismatch,
    GenerationValidationMismatchError,
    QuestionCandidate,
    SourceProvenance,
    ValidationNotPassedError,
    adapt_generation_validation,
    build_generation_validation_input,
)
from exam_guru_api.validation import (
    BlueprintRequirements,
    FindingStatus,
    GenerationAdapterError,
    TrustedSubjectScope,
    ValidationInput,
    ValidationReport,
    adapt_generation_result,
    generation_result_fingerprint,
    validate_question,
)
from tests.test_generation_domain import accounting, mcq, request

VALIDATION_RUN_ID = UUID("00000000-0000-0000-0000-000000009901")


def persisted_finding_ids(report: ValidationReport) -> tuple[UUID, ...]:
    return tuple(UUID(int=9_910 + index) for index, _finding in enumerate(report.findings))


def adapt_persisted(
    result: GenerationResult,
    report: ValidationReport,
) -> QuestionCandidate:
    return adapt_generation_validation(
        result,
        report,
        validation_run_id=VALIDATION_RUN_ID,
        finding_ids=persisted_finding_ids(report),
    )


def generation_result(
    *,
    slot_id: str | None = None,
    identity: GenerationIdentity | None = None,
    question: GeneratedQuestion | None = None,
) -> GenerationResult:
    generation_request = request()
    constraints = replace(
        generation_request.blueprint_slot.generation_constraints,
        response_language="en-LK",
    )
    slot = replace(
        generation_request.blueprint_slot,
        slot_id=slot_id or generation_request.blueprint_slot.slot_id,
        generation_constraints=constraints,
    )
    generation_request = replace(
        generation_request,
        identity=identity or generation_request.identity,
        blueprint_slot=slot,
    )
    return GenerationResult(
        request=generation_request,
        question=question or mcq(),
        accounting=accounting(),
    )


def validation_requirements(result: GenerationResult) -> BlueprintRequirements:
    slot = result.request.blueprint_slot
    return BlueprintRequirements(
        slot_id=slot.slot_id,
        schema_version=result.request.versions.schema_version,
        question_type=slot.question_type.value,
        marks=slot.marks,
        language=slot.generation_constraints.response_language,
        minimum_age=9,
        maximum_age=11,
    )


def validation_input(result: GenerationResult) -> ValidationInput:
    scope = result.request.blueprint_slot.generation_constraints.curriculum_scope
    return adapt_generation_result(
        result,
        requirements=validation_requirements(result),
        trusted_scope=TrustedSubjectScope(
            grade=scope.grade,
            medium=scope.medium,
            subject_id=scope.subject_id,
            subject_code="MATHEMATICS",
            curriculum_version_id=scope.curriculum_version_id,
            unit_ids=scope.unit_ids,
            lesson_ids=scope.lesson_ids,
        ),
    )


def passing_report(result: GenerationResult) -> ValidationReport:
    report = validate_question(validation_input(result))
    assert not report.blocked
    return report


def test_paper_helper_reuses_the_canonical_validation_subject_without_losing_content() -> None:
    result = generation_result()

    canonical_input = validation_input(result)
    paper_input = build_generation_validation_input(result)

    assert paper_input.candidate == canonical_input.candidate
    assert paper_input.blueprint == canonical_input.blueprint
    assert paper_input.grounding_sources == canonical_input.grounding_sources
    assert paper_input.candidate_fingerprint == canonical_input.candidate_fingerprint
    assert paper_adapters.generation_result_fingerprint is generation_result_fingerprint
    assert paper_input.candidate_id == generation_result_fingerprint(result)
    assert paper_input.blueprint.slot_id == result.request.blueprint_slot.slot_id
    assert paper_input.blueprint.question_type == result.question.question_type.value
    assert paper_input.blueprint.marks == result.question.marking.total_marks
    assert paper_input.blueprint.language == "en-LK"
    assert paper_input.candidate["stem"] == result.question.stem
    assert paper_input.candidate["options"] == (
        {"option_id": "A", "text": "32"},
        {"option_id": "B", "text": "42"},
        {"option_id": "C", "text": "52"},
    )
    assert paper_input.candidate["context_references"] == ("context-01",)
    assert paper_input.grounding_sources[0].context_id == "context-01"
    assert paper_input.grounding_sources[0].text == result.request.context.items[0].text
    assert paper_input.grounding_sources[0].source_document_id == (
        result.request.context.items[0].provenance.source_document_id
    )
    assert paper_input.grounding_sources[0].source_version == (
        result.request.context.items[0].provenance.source_version
    )
    assert paper_input.grounding_sources[0].page_number == (
        result.request.context.items[0].provenance.page_number
    )
    assert paper_input.grounding_sources[0].chunk_id == (
        result.request.context.items[0].provenance.chunk_id
    )


def test_paper_adapter_uses_the_selected_grade_age_bounds() -> None:
    result = generation_result()
    constraints = result.request.blueprint_slot.generation_constraints
    grade_seven_scope = replace(constraints.curriculum_scope, grade=7)
    grade_seven_slot = replace(
        result.request.blueprint_slot,
        generation_constraints=replace(constraints, curriculum_scope=grade_seven_scope),
    )
    grade_seven_result = replace(
        result,
        request=replace(result.request, blueprint_slot=grade_seven_slot),
    )

    validation_input = build_generation_validation_input(grade_seven_result)

    assert (validation_input.blueprint.minimum_age, validation_input.blueprint.maximum_age) == (
        11,
        13,
    )


def test_passing_generation_and_report_become_only_a_validated_paper_candidate() -> None:
    result = generation_result()
    report = passing_report(result)

    candidate = adapt_persisted(result, report)

    assert candidate.candidate_id == result.request.identity.generation_id
    assert candidate.state is CandidateState.VALIDATED
    assert candidate.version == 2
    assert candidate.review_history == ()
    assert candidate.decision is None
    assert candidate.lineage.generation_id == result.request.identity.generation_id
    assert candidate.lineage.generation_attempt_id == result.request.identity.attempt_id
    assert candidate.lineage.blueprint_id == result.request.blueprint_version.blueprint_id
    assert candidate.lineage.blueprint_version == result.request.versions.blueprint_version
    assert candidate.lineage.blueprint_slot_id == result.request.blueprint_slot.slot_id
    assert candidate.lineage.prompt_version == result.request.versions.prompt_version
    assert candidate.lineage.provider == result.request.versions.provider
    assert candidate.lineage.model_version == result.request.versions.model_version
    assert candidate.lineage.retrieval_version == result.request.versions.retrieval_version
    assert candidate.lineage.schema_version == result.request.versions.schema_version
    assert candidate.lineage.provenance == (
        SourceProvenance(
            source_document_id="curriculum-grade-5-maths",
            source_version="reviewed-v3",
            page_number=7,
            chunk_id="chunk-context-01",
        ),
    )
    assert candidate.content.question_type == result.question.question_type.value
    assert candidate.content.stem == result.question.stem
    assert tuple((option.option_id, option.text) for option in candidate.content.options) == (
        ("A", "32"),
        ("B", "42"),
        ("C", "52"),
    )
    assert candidate.content.answer == "B"
    assert candidate.content.explanation == result.question.answer.explanation
    assert candidate.content.marks == 2
    assert candidate.content.marking_guide == ("Selects the correct sum.",)
    assert candidate.content.marking_point_marks == (2,)

    assert candidate.validation is not None
    assert candidate.validation.validation_run_id == VALIDATION_RUN_ID
    assert candidate.validation.validator_version == (
        f"{report.pipeline_version}/{report.report_schema_version}"
    )
    assert candidate.validation.finding_refs == tuple(
        str(finding_id) for finding_id in persisted_finding_ids(report)
    )
    assert candidate.validation.passed is True
    assert candidate.validation.validated_revision == 1
    assert not hasattr(paper_adapters, "approve_candidate")
    assert not hasattr(paper_adapters, "assemble_paper_draft")
    assert not hasattr(paper_adapters, "publish")


def _alter_generated_content(result: GenerationResult) -> GenerationResult:
    return replace(
        result,
        question=replace(result.question, stem="Which addition expression totals forty-two?"),
    )


def _alter_generation_context(result: GenerationResult) -> GenerationResult:
    context_item = result.request.context.items[0]
    changed_item = replace(
        context_item,
        provenance=replace(context_item.provenance, source_version="reviewed-v4"),
    )
    return replace(
        result,
        request=replace(
            result.request,
            context=replace(result.request.context, items=(changed_item,)),
        ),
    )


def _alter_generation_version_route(result: GenerationResult) -> GenerationResult:
    return replace(
        result,
        request=replace(
            result.request,
            versions=replace(result.request.versions, prompt_version="prompt-v99"),
        ),
    )


def _alter_blueprint_slot(result: GenerationResult) -> GenerationResult:
    return replace(
        result,
        request=replace(
            result.request,
            blueprint_slot=replace(result.request.blueprint_slot, slot_id="paper-slot-b"),
        ),
    )


@pytest.mark.parametrize(
    "alter",
    [
        _alter_generated_content,
        _alter_generation_context,
        _alter_generation_version_route,
        _alter_blueprint_slot,
    ],
    ids=("content", "context", "version-route", "blueprint-slot"),
)
def test_canonical_validation_report_cannot_be_replayed_against_an_altered_result(
    alter: Callable[[GenerationResult], GenerationResult],
) -> None:
    result = generation_result(slot_id="paper-slot-a")
    report = validate_question(validation_input(result))
    altered_result = alter(result)

    assert report.passed
    assert report.candidate_id == generation_result_fingerprint(result)
    assert generation_result_fingerprint(altered_result) != report.candidate_id
    with pytest.raises(GenerationValidationMismatchError) as raised:
        adapt_persisted(altered_result, report)

    assert raised.value.mismatch is GenerationValidationMismatch.GENERATION_FINGERPRINT


def test_warning_requires_human_review_but_failure_cannot_promote_generated_candidate() -> None:
    result = generation_result()
    report = passing_report(result)
    warning_report = ValidationReport(
        candidate_id=report.candidate_id,
        pipeline_version=report.pipeline_version,
        findings=(replace(report.findings[0], status=FindingStatus.WARN), *report.findings[1:]),
    )
    warned_candidate = adapt_persisted(result, warning_report)

    assert warned_candidate.state is CandidateState.VALIDATED
    assert warned_candidate.review_history == ()
    assert warned_candidate.decision is None

    blocked_report = ValidationReport(
        candidate_id=report.candidate_id,
        pipeline_version=report.pipeline_version,
        findings=(replace(report.findings[0], status=FindingStatus.FAIL), *report.findings[1:]),
    )
    with pytest.raises(ValidationNotPassedError) as raised:
        adapt_persisted(result, blocked_report)
    assert raised.value.candidate_id == result.request.identity.generation_id


def test_adapter_rejects_a_report_bound_to_a_different_blueprint_slot() -> None:
    result = generation_result(slot_id="paper-slot-a")
    report_for_other_slot = passing_report(generation_result(slot_id="paper-slot-b"))

    with pytest.raises(GenerationValidationMismatchError) as raised:
        adapt_persisted(result, report_for_other_slot)

    assert raised.value.mismatch is GenerationValidationMismatch.GENERATION_FINGERPRINT


def test_adapter_rejects_a_report_bound_to_a_different_generation_fingerprint() -> None:
    result = generation_result()
    other_identity = replace(
        result.request.identity,
        generation_id=UUID(int=901),
        attempt_id=UUID(int=902),
    )
    report_for_other_generation = passing_report(generation_result(identity=other_identity))

    with pytest.raises(GenerationValidationMismatchError) as raised:
        adapt_persisted(result, report_for_other_generation)

    assert raised.value.mismatch is GenerationValidationMismatch.GENERATION_FINGERPRINT


def test_adapter_rejects_a_report_for_different_candidate_content_even_if_id_is_reused() -> None:
    result = generation_result()
    expected_input = build_generation_validation_input(result)
    changed_candidate = dict(expected_input.candidate)
    changed_candidate["stem"] = "Which addition expression has the value forty-two?"
    mismatched_input = ValidationInput(
        candidate_id=expected_input.candidate_id,
        candidate=changed_candidate,
        blueprint=expected_input.blueprint,
        grounding_sources=expected_input.grounding_sources,
    )
    mismatched_report = validate_question(mismatched_input)
    assert not mismatched_report.blocked

    with pytest.raises(GenerationValidationMismatchError) as raised:
        adapt_persisted(result, mismatched_report)

    assert raised.value.mismatch is GenerationValidationMismatch.CANDIDATE_FINGERPRINT


def test_adapter_recomputes_and_rejects_a_tampered_report_fingerprint() -> None:
    result = generation_result()
    report = passing_report(result)
    object.__setattr__(report, "report_fingerprint", "0" * 64)

    with pytest.raises(GenerationValidationMismatchError) as raised:
        adapt_persisted(result, report)

    assert raised.value.mismatch is GenerationValidationMismatch.REPORT_FINGERPRINT


def test_constructed_response_and_each_marking_criterion_remain_lossless() -> None:
    base = generation_result()
    constraints = replace(
        base.request.blueprint_slot.generation_constraints,
        required_question_type=QuestionType.SHORT_ANSWER,
    )
    slot = replace(
        base.request.blueprint_slot,
        question_type=QuestionType.SHORT_ANSWER,
        generation_constraints=constraints,
    )
    question = GeneratedQuestion(
        question_type=QuestionType.SHORT_ANSWER,
        stem="Write two equivalent ways to show forty-two.",
        options=(),
        answer=QuestionAnswer(
            explanation="Both accepted forms represent forty-two.",
            accepted_responses=("42", "forty-two | 42\nunits"),
        ),
        marking=MarkingScheme(
            total_marks=2,
            criteria=(
                MarkingCriterion("numeric", "Writes the numeral.", 1),
                MarkingCriterion("equivalent", "Writes an equivalent form.", 1),
            ),
        ),
    )
    result = GenerationResult(
        request=replace(base.request, blueprint_slot=slot),
        question=question,
        accounting=base.accounting,
    )

    candidate = adapt_persisted(result, passing_report(result))

    assert json.loads(candidate.content.answer) == ["42", "forty-two | 42\nunits"]
    assert candidate.content.marking_guide == (
        "Writes the numeral.",
        "Writes an equivalent form.",
    )
    assert candidate.content.marking_point_marks == (1, 1)


def test_adapter_rejects_untyped_boundary_values() -> None:
    result = generation_result()
    report = passing_report(result)

    with pytest.raises(GenerationValidationAdapterError, match="GenerationResult"):
        build_generation_validation_input(cast(GenerationResult, object()))
    with pytest.raises(GenerationAdapterError, match="GenerationResult"):
        generation_result_fingerprint(cast(GenerationResult, object()))
    with pytest.raises(GenerationValidationAdapterError, match="GenerationResult"):
        adapt_generation_validation(
            cast(GenerationResult, object()),
            report,
            validation_run_id=VALIDATION_RUN_ID,
            finding_ids=persisted_finding_ids(report),
        )
    with pytest.raises(GenerationValidationAdapterError, match="ValidationReport"):
        adapt_generation_validation(
            result,
            cast(ValidationReport, object()),
            validation_run_id=VALIDATION_RUN_ID,
            finding_ids=persisted_finding_ids(report),
        )


def test_adapter_requires_actual_persisted_validation_and_finding_identities() -> None:
    result = generation_result()
    report = passing_report(result)

    with pytest.raises(GenerationValidationAdapterError, match="validation_run_id"):
        adapt_generation_validation(
            result,
            report,
            validation_run_id=cast(UUID, "derived-or-client-value"),
            finding_ids=persisted_finding_ids(report),
        )
    with pytest.raises(GenerationValidationAdapterError, match="finding_ids"):
        adapt_generation_validation(
            result,
            report,
            validation_run_id=VALIDATION_RUN_ID,
            finding_ids=persisted_finding_ids(report)[:-1],
        )
