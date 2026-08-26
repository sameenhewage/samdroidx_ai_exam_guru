"""Composable deterministic orchestration for first-party question validators."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

from exam_guru_api.validation.domain import (
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
    ValidationReport,
)
from exam_guru_api.validation.validators import (
    AgeLanguageHeuristicsValidator,
    BlueprintComplianceValidator,
    ExactHashDuplicateValidator,
    GroundingValidator,
    HeuristicPolicy,
    LexicalSimilarityIndicatorValidator,
    PromptInjectionResidueValidator,
    SchemaCompletenessValidator,
)

DEFAULT_PIPELINE_VERSION = "deterministic-question-validation.v3"


class QuestionValidator(Protocol):
    """Small provider-independent synchronous validation boundary."""

    @property
    def validator_id(self) -> str: ...

    @property
    def validator_version(self) -> str: ...

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]: ...


def _valid_component_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 128
        and all(character.isprintable() and not character.isspace() for character in value)
    )


@dataclass(frozen=True, slots=True)
class ValidationPipeline:
    """Run a canonical validator set and reject malformed component output."""

    validators: tuple[QuestionValidator, ...]
    version: str = DEFAULT_PIPELINE_VERSION
    pipeline_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not _valid_component_identity(self.version):
            raise ValidationContractError("pipeline version must be a bounded machine value")
        if not isinstance(self.validators, tuple) or not self.validators:
            raise ValidationContractError("pipeline validators must be a non-empty tuple")

        validated: list[QuestionValidator] = []
        for validator in self.validators:
            validator_id = getattr(validator, "validator_id", None)
            validator_version = getattr(validator, "validator_version", None)
            validate = getattr(validator, "validate", None)
            if (
                not _valid_component_identity(validator_id)
                or not _valid_component_identity(validator_version)
                or not callable(validate)
            ):
                raise ValidationContractError(
                    "each pipeline component must expose validator identity, version, and validate"
                )
            validated.append(validator)

        canonical = tuple(
            sorted(
                validated,
                key=lambda item: (item.validator_id, item.validator_version),
            )
        )
        validator_ids = tuple(item.validator_id for item in canonical)
        if len(set(validator_ids)) != len(validator_ids):
            raise ValidationContractError("pipeline validator identifiers must be unique")
        object.__setattr__(self, "validators", canonical)
        fingerprint_payload = json.dumps(
            {
                "pipeline_version": self.version,
                "validators": [
                    {
                        "validator_id": validator.validator_id,
                        "validator_version": validator.validator_version,
                    }
                    for validator in canonical
                ],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(
            self,
            "pipeline_fingerprint",
            hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest(),
        )

    def validate(self, validation_input: ValidationInput) -> ValidationReport:
        if not isinstance(validation_input, ValidationInput):
            raise ValidationContractError("pipeline input must be ValidationInput")

        findings: list[ValidationFinding] = []
        for validator in self.validators:
            component_findings = validator.validate(validation_input)
            if not isinstance(component_findings, tuple) or not component_findings:
                raise ValidationContractError(
                    f"validator {validator.validator_id!r} must return at least one finding"
                )
            if any(not isinstance(item, ValidationFinding) for item in component_findings):
                raise ValidationContractError(
                    f"validator {validator.validator_id!r} returned a malformed finding"
                )
            if any(
                item.validator_id != validator.validator_id
                or item.validator_version != validator.validator_version
                for item in component_findings
            ):
                raise ValidationContractError(
                    f"validator {validator.validator_id!r} returned a foreign finding identity"
                )
            findings.extend(component_findings)

        return ValidationReport(
            candidate_id=validation_input.candidate_id,
            pipeline_version=self.version,
            findings=tuple(findings),
        )


def build_default_pipeline(
    *,
    heuristic_policy: HeuristicPolicy | None = None,
) -> ValidationPipeline:
    """Build the canonical versioned ruleset; no model, persistence, or network is involved."""

    return ValidationPipeline(
        validators=(
            SchemaCompletenessValidator(),
            BlueprintComplianceValidator(),
            GroundingValidator(),
            PromptInjectionResidueValidator(),
            AgeLanguageHeuristicsValidator(policy=heuristic_policy),
            ExactHashDuplicateValidator(),
            LexicalSimilarityIndicatorValidator(),
        )
    )


def validate_question(
    validation_input: ValidationInput,
    *,
    pipeline: ValidationPipeline | None = None,
) -> ValidationReport:
    active_pipeline = build_default_pipeline() if pipeline is None else pipeline
    if not isinstance(active_pipeline, ValidationPipeline):
        raise ValidationContractError("pipeline must be ValidationPipeline")
    return active_pipeline.validate(validation_input)
