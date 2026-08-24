from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import pytest

from exam_guru_api.validation import (
    DEFAULT_PIPELINE_VERSION,
    FindingEvidence,
    FindingStatus,
    LexicalSimilarityIndicatorValidator,
    QuestionValidator,
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
    ValidationPipeline,
    build_default_pipeline,
    validate_question,
)
from tests.test_validation_fixtures import validation_input


@dataclass(frozen=True, slots=True)
class FixtureValidator:
    validator_id: str
    validator_version: str
    code: str
    status: FindingStatus = FindingStatus.PASS

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        return (
            ValidationFinding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code=self.code,
                status=self.status,
                message="A composable fixture validator finding.",
                evidence=(
                    FindingEvidence(
                        location="candidate_id",
                        expected="the snapshotted candidate identifier",
                        observed=validation_input.candidate_id,
                    ),
                ),
            ),
        )


def run_validator(validator: QuestionValidator, request: ValidationInput) -> object:
    return validator.validate(request)


def test_default_pipeline_is_composable_versioned_and_fully_deterministic() -> None:
    request = validation_input()
    pipeline = build_default_pipeline()

    first = pipeline.validate(request)
    second = validate_question(request)

    assert first == second
    assert first.pipeline_version == DEFAULT_PIPELINE_VERSION
    assert first.pipeline_version == "deterministic-question-validation.v2"
    assert first.overall_status is FindingStatus.PASS
    assert first.passed is True
    assert first.blocked is False
    assert len(first.findings) == 13
    assert len({(finding.validator_id, finding.code) for finding in first.findings}) == 13
    assert any(
        validator.validator_id == LexicalSimilarityIndicatorValidator.validator_id
        for validator in pipeline.validators
    )
    assert all(finding.evidence for finding in first.findings)
    assert all(finding.validator_version for finding in first.findings)


def test_pipeline_canonicalizes_validator_and_finding_order() -> None:
    alpha = FixtureValidator("alpha-validator", "1.0.0", "custom.alpha")
    zeta = FixtureValidator("zeta-validator", "2.0.0", "custom.zeta", FindingStatus.WARN)
    request = validation_input()

    first = ValidationPipeline(
        validators=(zeta, alpha),
        version="fixture-pipeline.v1",
    ).validate(request)
    second = ValidationPipeline(
        validators=(alpha, zeta),
        version="fixture-pipeline.v1",
    ).validate(request)

    assert first == second
    assert [finding.code for finding in first.findings] == ["custom.alpha", "custom.zeta"]
    assert first.overall_status is FindingStatus.WARN


def test_protocol_remains_a_small_first_party_synchronous_boundary() -> None:
    validator = FixtureValidator("fixture-validator", "1.0.0", "custom.fixture")

    findings = run_validator(validator, validation_input())

    assert len(cast(tuple[ValidationFinding, ...], findings)) == 1
    assert not hasattr(validator, "generate")
    assert not hasattr(validator, "persist")


@dataclass(frozen=True, slots=True)
class EmptyValidator:
    validator_id: str = "empty-validator"
    validator_version: str = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        del validation_input
        return ()


@dataclass(frozen=True, slots=True)
class ForeignFindingValidator:
    validator_id: str = "declared-validator"
    validator_version: str = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        return FixtureValidator(
            "different-validator",
            "1.0.0",
            "custom.foreign",
        ).validate(validation_input)


@pytest.mark.parametrize(
    "build",
    [
        lambda: ValidationPipeline(validators=(), version="pipeline.v1"),
        lambda: ValidationPipeline(
            validators=cast(
                tuple[QuestionValidator, ...], [FixtureValidator("one", "1.0.0", "custom.one")]
            ),
            version="pipeline.v1",
        ),
        lambda: ValidationPipeline(
            validators=(
                FixtureValidator("same", "1.0.0", "custom.one"),
                FixtureValidator("same", "2.0.0", "custom.two"),
            ),
            version="pipeline.v1",
        ),
        lambda: ValidationPipeline(
            validators=(cast(QuestionValidator, object()),),
            version="pipeline.v1",
        ),
        lambda: ValidationPipeline(
            validators=(FixtureValidator("one", "1.0.0", "custom.one"),),
            version=" ",
        ),
    ],
)
def test_pipeline_contract_rejects_invalid_composition(build: Callable[[], object]) -> None:
    with pytest.raises(ValidationContractError):
        build()


def test_pipeline_rejects_empty_or_foreign_validator_output() -> None:
    request = validation_input()

    with pytest.raises(ValidationContractError, match="at least one finding"):
        ValidationPipeline((EmptyValidator(),), "pipeline.v1").validate(request)

    with pytest.raises(ValidationContractError, match="identity"):
        ValidationPipeline((ForeignFindingValidator(),), "pipeline.v1").validate(request)


def test_pipeline_requires_a_validation_input() -> None:
    with pytest.raises(ValidationContractError, match="ValidationInput"):
        build_default_pipeline().validate(cast(ValidationInput, "candidate"))
