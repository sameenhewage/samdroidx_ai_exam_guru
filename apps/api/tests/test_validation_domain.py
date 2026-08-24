from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from exam_guru_api.validation.domain import (
    REPORT_LIMITATIONS,
    BlueprintRequirements,
    DuplicateReference,
    FindingCode,
    FindingEvidence,
    FindingStatus,
    GroundingSource,
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
    ValidationReport,
    canonical_text_sha256,
)
from tests.test_validation_fixtures import blueprint, source, valid_candidate, validation_input


def evidence(
    location: str = "$.stem",
    expected: str = "non-blank text",
    observed: str = "present",
) -> FindingEvidence:
    return FindingEvidence(location=location, expected=expected, observed=observed)


def finding(
    *,
    validator_id: str = "fixture-validator",
    code: str = "fixture.check",
    status: FindingStatus = FindingStatus.PASS,
    finding_evidence: tuple[FindingEvidence, ...] | None = None,
) -> ValidationFinding:
    return ValidationFinding(
        validator_id=validator_id,
        validator_version="1.0.0",
        code=code,
        status=status,
        message="A deterministic fixture finding.",
        evidence=(evidence(),) if finding_evidence is None else finding_evidence,
    )


def test_findings_are_versioned_coded_and_canonicalize_evidence() -> None:
    second = evidence("$.z", "safe", "safe")
    first = evidence("$.a", "present", "missing")

    result = ValidationFinding(
        validator_id="schema-completeness",
        validator_version="1.0.0",
        code=FindingCode.SCHEMA_COMPLETENESS,
        status=FindingStatus.FAIL,
        message="Required fields are incomplete.",
        evidence=(second, first),
    )

    assert result.code == "schema.completeness"
    assert result.status is FindingStatus.FAIL
    assert result.evidence == (first, second)
    assert result.validator_version == "1.0.0"


def test_every_finding_requires_evidence_including_failures() -> None:
    with pytest.raises(ValidationContractError, match="evidence"):
        finding(status=FindingStatus.FAIL, finding_evidence=())


@pytest.mark.parametrize(
    "build",
    [
        lambda: FindingEvidence(" ", "expected", "observed"),
        lambda: FindingEvidence("$.x", " ", "observed"),
        lambda: FindingEvidence("$.x", "expected", " "),
        lambda: replace(finding(), validator_id="bad id"),
        lambda: replace(finding(), validator_version=" "),
        lambda: replace(finding(), code="UPPER CASE"),
        lambda: replace(finding(), status=cast(FindingStatus, "fail")),
        lambda: replace(finding(), message=" "),
        lambda: replace(finding(), evidence=cast(tuple[FindingEvidence, ...], [evidence()])),
        lambda: replace(finding(), evidence=(cast(FindingEvidence, "evidence"),)),
    ],
)
def test_finding_contract_rejects_malformed_audit_data(build: Callable[[], object]) -> None:
    with pytest.raises(ValidationContractError):
        build()


def test_validation_input_takes_an_immutable_canonical_snapshot() -> None:
    candidate = valid_candidate()
    options = cast(list[dict[str, object]], candidate["options"])
    request = validation_input(candidate=candidate)
    fingerprint = request.candidate_fingerprint

    candidate["stem"] = "Changed after validation input creation"
    options[0]["text"] = "changed"

    assert request.candidate["stem"] == "What is 27 + 15?"
    assert cast(tuple[object, ...], request.candidate["options"])[0] != options[0]
    assert request.candidate_fingerprint == fingerprint
    with pytest.raises(TypeError):
        request.candidate["stem"] = "mutation"  # type: ignore[index]


def test_validation_input_canonicalizes_external_evidence_order() -> None:
    first_source = source("context-a", chunk_id="chunk-a")
    second_source = source("context-b", chunk_id="chunk-b")
    first_duplicate = DuplicateReference(question_id="question-a", text="First question")
    second_duplicate = DuplicateReference(question_id="question-b", text="Second question")

    first = ValidationInput(
        candidate_id="candidate-01",
        candidate=valid_candidate(),
        blueprint=blueprint(),
        grounding_sources=(second_source, first_source),
        duplicate_references=(second_duplicate, first_duplicate),
    )
    second = replace(
        first,
        grounding_sources=(first_source, second_source),
        duplicate_references=(first_duplicate, second_duplicate),
    )

    assert first.grounding_sources == second.grounding_sources
    assert first.duplicate_references == second.duplicate_references
    assert first.input_fingerprint == second.input_fingerprint


def test_duplicate_reference_hashes_only_canonical_text_without_semantic_claims() -> None:
    digest = canonical_text_sha256("  WHAT is 27 + 15?\n")
    reference = DuplicateReference(
        question_id="historical-01",
        text="What is 27 + 15?",
        content_sha256=digest,
    )

    assert digest == canonical_text_sha256("what is 27 + 15?")
    assert reference.effective_sha256 == digest
    assert digest != canonical_text_sha256("Find the sum of twenty-seven and fifteen.")


@pytest.mark.parametrize(
    "build",
    [
        lambda: BlueprintRequirements(" ", "question.v1", "multiple_choice", 2, "en-LK", 9, 11),
        lambda: blueprint(marks=cast(int, True)),
        lambda: blueprint(marks=0),
        lambda: blueprint(minimum_age=12, maximum_age=11),
        lambda: blueprint(minimum_options=5, maximum_options=4),
        lambda: GroundingSource("context", " ", "source", "v1", 1, "chunk"),
        lambda: DuplicateReference(question_id="duplicate"),
        lambda: DuplicateReference(question_id="duplicate", content_sha256="A" * 64),
        lambda: DuplicateReference(
            question_id="duplicate",
            text="content",
            content_sha256="0" * 64,
        ),
        lambda: ValidationInput(
            candidate_id="candidate",
            candidate=cast(dict[str, object], {1: "not-a-string-key"}),
            blueprint=blueprint(),
            grounding_sources=(source(),),
        ),
        lambda: ValidationInput(
            candidate_id="candidate",
            candidate={"not_json": object()},
            blueprint=blueprint(),
            grounding_sources=(source(),),
        ),
        lambda: ValidationInput(
            candidate_id="candidate",
            candidate=valid_candidate(),
            blueprint=cast(BlueprintRequirements, "blueprint"),
            grounding_sources=(source(),),
        ),
        lambda: ValidationInput(
            candidate_id="candidate",
            candidate=valid_candidate(),
            blueprint=blueprint(),
            grounding_sources=cast(tuple[GroundingSource, ...], [source()]),
        ),
    ],
)
def test_validation_domain_rejects_invalid_trusted_contracts(build: Callable[[], object]) -> None:
    with pytest.raises(ValidationContractError):
        build()


def test_report_status_fingerprint_and_limitations_are_deterministic() -> None:
    warning = finding(
        validator_id="age-language",
        code="heuristic.age_indicators",
        status=FindingStatus.WARN,
    )
    passing = finding()

    first = ValidationReport(
        candidate_id="candidate-01",
        pipeline_version="deterministic-validation.v1",
        findings=(passing, warning),
    )
    second = ValidationReport(
        candidate_id="candidate-01",
        pipeline_version="deterministic-validation.v1",
        findings=(warning, passing),
    )

    assert first == second
    assert first.overall_status is FindingStatus.WARN
    assert first.failures == ()
    assert first.warnings == (warning,)
    assert first.passed is False
    assert first.report_fingerprint == second.report_fingerprint
    assert first.limitations == REPORT_LIMITATIONS
    assert any("semantic" in limitation for limitation in first.limitations)
    assert any(
        "lexical" in limitation and "false positives" in limitation
        for limitation in first.limitations
    )
    assert any("false negatives" in limitation for limitation in first.limitations)
    assert not hasattr(first, "publish")


def test_report_failure_is_blocking_and_duplicate_finding_codes_are_rejected() -> None:
    failure = finding(status=FindingStatus.FAIL)
    report = ValidationReport(
        candidate_id="candidate-01",
        pipeline_version="deterministic-validation.v1",
        findings=(failure,),
    )

    assert report.overall_status is FindingStatus.FAIL
    assert report.failures == (failure,)
    assert report.blocked is True

    with pytest.raises(ValidationContractError, match="duplicate"):
        ValidationReport(
            candidate_id="candidate-01",
            pipeline_version="deterministic-validation.v1",
            findings=(failure, failure),
        )
