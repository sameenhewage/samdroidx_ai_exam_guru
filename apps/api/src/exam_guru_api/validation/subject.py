"""Trusted subject routing, curriculum-scope checks, and grounded verifier contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from exam_guru_api.validation.domain import (
    ContextScopeBinding,
    FindingEvidence,
    FindingStatus,
    GeneratedSubjectScope,
    GroundingSource,
    TrustedSubjectScope,
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
)

MAX_SUBJECT_VALIDATORS = 32
MAX_SUBJECT_CODES = 128
MAX_SEMANTIC_EVIDENCE_REFS = 32
MAX_SEMANTIC_SUMMARY_CHARACTERS = 1_024


class SubjectFindingCode(StrEnum):
    SCOPE_CONSISTENCY = "subject.scope.consistency"
    SCOPE_GRADE_MISMATCH = "subject.scope.grade_mismatch"
    SCOPE_MEDIUM_MISMATCH = "subject.scope.medium_mismatch"
    SCOPE_SUBJECT_MISMATCH = "subject.scope.subject_mismatch"
    SCOPE_CURRICULUM_MISMATCH = "subject.scope.curriculum_mismatch"
    SCOPE_OUTSIDE_SELECTED_UNIT = "subject.scope.outside_selected_unit"
    SCOPE_OUTSIDE_SELECTED_LESSON = "subject.scope.outside_selected_lesson"
    SUBJECT_UNREGISTERED = "subject.unregistered"
    MATH_ANSWER_MISMATCH = "subject.math.answer_mismatch"
    MATH_MULTIPLE_CORRECT_OPTIONS = "subject.math.multiple_correct_options"
    MATH_DUPLICATE_EQUIVALENT_OPTIONS = "subject.math.duplicate_equivalent_options"
    MATH_UNIT_MISMATCH = "subject.math.unit_mismatch"
    MATH_UNSUPPORTED_EXPRESSION = "subject.math.unsupported_expression"
    MARKING_ANSWER_INCONSISTENT = "subject.marking.answer_inconsistent"
    FACTUAL_GROUNDED = "subject.factual.grounded"
    FACTUAL_UNSUPPORTED_CLAIM = "subject.factual.unsupported_claim"
    FACTUAL_SOURCE_CONTRADICTION = "subject.factual.source_contradiction"
    FACTUAL_VERIFIER_UNAVAILABLE = "subject.factual.verifier_unavailable"


@dataclass(frozen=True, slots=True)
class CurriculumSelection:
    unit_ids: tuple[UUID, ...]
    lesson_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class SubjectValidationContext:
    grade: int
    medium: str
    subject_id: UUID
    subject_code: str
    curriculum_version_id: UUID
    selected_scope: CurriculumSelection
    candidate: Mapping[str, object]
    grounding_sources: tuple[GroundingSource, ...]

    @classmethod
    def from_input(cls, validation_input: ValidationInput) -> SubjectValidationContext:
        if not isinstance(validation_input, ValidationInput):
            raise ValidationContractError("subject context requires ValidationInput")
        scope = validation_input.trusted_scope
        return cls(
            grade=scope.grade,
            medium=scope.medium,
            subject_id=scope.subject_id,
            subject_code=scope.subject_code,
            curriculum_version_id=scope.curriculum_version_id,
            selected_scope=CurriculumSelection(scope.unit_ids, scope.lesson_ids),
            candidate=validation_input.candidate,
            grounding_sources=validation_input.grounding_sources,
        )


class SubjectValidator(Protocol):
    @property
    def subject_codes(self) -> frozenset[str]: ...

    @property
    def validator_id(self) -> str: ...

    @property
    def validator_version(self) -> str: ...

    def validate(self, context: SubjectValidationContext) -> tuple[ValidationFinding, ...]: ...


class SemanticVerificationStatus(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True, order=True)
class SemanticEvidenceReference:
    context_id: str
    source_document_id: str
    page_number: int

    def __post_init__(self) -> None:
        for field_name in ("context_id", "source_document_id"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 256
            ):
                raise ValidationContractError(
                    f"semantic evidence {field_name} must be bounded non-blank text"
                )
        if (
            not isinstance(self.page_number, int)
            or isinstance(self.page_number, bool)
            or self.page_number < 1
        ):
            raise ValidationContractError("semantic evidence page_number must be positive")


@dataclass(frozen=True, slots=True)
class SemanticVerificationRequest:
    grade: int
    medium: str
    subject_id: UUID
    subject_code: str
    curriculum_version_id: UUID
    selected_scope: CurriculumSelection
    candidate: Mapping[str, object]
    grounding_sources: tuple[GroundingSource, ...]


@dataclass(frozen=True, slots=True)
class SemanticVerificationResult:
    status: SemanticVerificationStatus
    summary: str
    evidence_refs: tuple[SemanticEvidenceReference, ...]
    verifier_id: str
    verifier_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, SemanticVerificationStatus):
            raise ValidationContractError("semantic verification status is invalid")
        if (
            not isinstance(self.summary, str)
            or not self.summary.strip()
            or len(self.summary) > MAX_SEMANTIC_SUMMARY_CHARACTERS
        ):
            raise ValidationContractError("semantic verification summary must be bounded text")
        if (
            not isinstance(self.evidence_refs, tuple)
            or len(self.evidence_refs) > MAX_SEMANTIC_EVIDENCE_REFS
            or any(not isinstance(item, SemanticEvidenceReference) for item in self.evidence_refs)
        ):
            raise ValidationContractError("semantic evidence references must be a bounded tuple")
        canonical = tuple(sorted(self.evidence_refs))
        if len(set(canonical)) != len(canonical):
            raise ValidationContractError("semantic evidence references cannot contain duplicates")
        object.__setattr__(self, "evidence_refs", canonical)
        for field_name in ("verifier_id", "verifier_version", "prompt_version"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 128
                or any(character.isspace() or not character.isprintable() for character in value)
            ):
                raise ValidationContractError(f"semantic {field_name} must be a machine value")


class GroundedSemanticVerifier(Protocol):
    @property
    def verifier_id(self) -> str: ...

    @property
    def verifier_version(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    def verify(self, request: SemanticVerificationRequest) -> SemanticVerificationResult: ...


def _scope_evidence(
    *,
    location: str,
    expected: object,
    observed: object,
) -> FindingEvidence:
    return FindingEvidence(
        location=location,
        expected=str(expected)[:1_024],
        observed=str(observed)[:1_024],
    )


@dataclass(frozen=True, slots=True)
class TrustedSubjectScopeValidator:
    validator_id: str = "trusted-subject-scope"
    validator_version: str = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        trusted = validation_input.trusted_scope
        generated = validation_input.generated_scope
        if not isinstance(generated, GeneratedSubjectScope):
            raise ValidationContractError("validation input generated scope is missing")

        failures: dict[SubjectFindingCode, list[FindingEvidence]] = {}

        def mismatch(
            code: SubjectFindingCode,
            location: str,
            expected: object,
            observed: object,
        ) -> None:
            failures.setdefault(code, []).append(
                _scope_evidence(location=location, expected=expected, observed=observed)
            )

        if generated.grade != trusted.grade:
            mismatch(
                SubjectFindingCode.SCOPE_GRADE_MISMATCH,
                "$.generated_scope.grade",
                trusted.grade,
                generated.grade,
            )
        if generated.medium != trusted.medium:
            mismatch(
                SubjectFindingCode.SCOPE_MEDIUM_MISMATCH,
                "$.generated_scope.medium",
                trusted.medium,
                generated.medium,
            )
        if generated.subject_id != trusted.subject_id:
            mismatch(
                SubjectFindingCode.SCOPE_SUBJECT_MISMATCH,
                "$.generated_scope.subject_id",
                trusted.subject_id,
                generated.subject_id,
            )
        if generated.curriculum_version_id != trusted.curriculum_version_id:
            mismatch(
                SubjectFindingCode.SCOPE_CURRICULUM_MISMATCH,
                "$.generated_scope.curriculum_version_id",
                trusted.curriculum_version_id,
                generated.curriculum_version_id,
            )
        if generated.unit_ids != trusted.unit_ids:
            mismatch(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_UNIT,
                "$.generated_scope.unit_ids",
                trusted.unit_ids,
                generated.unit_ids,
            )
        if generated.lesson_ids != trusted.lesson_ids:
            mismatch(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_LESSON,
                "$.generated_scope.lesson_ids",
                trusted.lesson_ids,
                generated.lesson_ids,
            )

        for binding in validation_input.context_scope_bindings:
            self._check_context_binding(trusted, binding, mismatch)

        if not failures:
            return (
                ValidationFinding(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    code=SubjectFindingCode.SCOPE_CONSISTENCY,
                    status=FindingStatus.PASS,
                    message="Trusted subject and selected curriculum scope are consistent.",
                    evidence=(
                        _scope_evidence(
                            location="$.subject_scope",
                            expected="server-owned scope matches generation and context",
                            observed=(
                                f"grade={trusted.grade};medium={trusted.medium};"
                                f"subject={trusted.subject_code};"
                                f"curriculum={trusted.curriculum_version_id}"
                            ),
                        ),
                    ),
                ),
            )

        messages = {
            SubjectFindingCode.SCOPE_GRADE_MISMATCH: "Generation grade differs from trusted scope.",
            SubjectFindingCode.SCOPE_MEDIUM_MISMATCH: (
                "Generation medium differs from trusted scope."
            ),
            SubjectFindingCode.SCOPE_SUBJECT_MISMATCH: (
                "Generation or context subject differs from trusted scope."
            ),
            SubjectFindingCode.SCOPE_CURRICULUM_MISMATCH: (
                "Generation or context curriculum differs from trusted scope."
            ),
            SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_UNIT: (
                "Generation context falls outside the selected unit scope."
            ),
            SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_LESSON: (
                "Generation context falls outside the selected lesson scope."
            ),
        }
        return tuple(
            ValidationFinding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code=code,
                status=FindingStatus.FAIL,
                message=messages[code],
                evidence=tuple(evidence[:64]),
            )
            for code, evidence in sorted(failures.items(), key=lambda item: item[0].value)
        )

    @staticmethod
    def _check_context_binding(
        trusted: TrustedSubjectScope,
        binding: ContextScopeBinding,
        mismatch: object,
    ) -> None:
        add = mismatch
        if not callable(add):
            raise ValidationContractError("scope mismatch collector must be callable")
        location = f"$.context_scope_bindings[{binding.context_id}]"
        if binding.curriculum_version_id != trusted.curriculum_version_id:
            add(
                SubjectFindingCode.SCOPE_CURRICULUM_MISMATCH,
                f"{location}.curriculum_version_id",
                trusted.curriculum_version_id,
                binding.curriculum_version_id,
            )
        if binding.subject_id != trusted.subject_id:
            add(
                SubjectFindingCode.SCOPE_SUBJECT_MISMATCH,
                f"{location}.subject_id",
                trusted.subject_id,
                binding.subject_id,
            )
        if binding.unit_id != binding.snapshot_unit_id:
            add(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_UNIT,
                f"{location}.snapshot_unit_id",
                binding.unit_id,
                binding.snapshot_unit_id,
            )
        if binding.lesson_id != binding.snapshot_lesson_id:
            add(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_LESSON,
                f"{location}.snapshot_lesson_id",
                binding.lesson_id,
                binding.snapshot_lesson_id,
            )
        if trusted.unit_ids and binding.unit_id not in trusted.unit_ids:
            add(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_UNIT,
                f"{location}.unit_id",
                trusted.unit_ids,
                binding.unit_id,
            )
        if trusted.lesson_ids and binding.lesson_id not in trusted.lesson_ids:
            add(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_LESSON,
                f"{location}.lesson_id",
                trusted.lesson_ids,
                binding.lesson_id,
            )


FACTUAL_SUBJECT_CODES = frozenset(
    {
        "BUDDHISM",
        "ENGLISH",
        "ENVIRONMENT",
        "ENVIRONMENTAL_STUDIES",
        "HISTORY",
        "RELIGION",
        "SCIENCE",
        "SINHALA",
        "TAMIL",
    }
)


@dataclass(frozen=True, slots=True)
class GroundedFactualSubjectValidator:
    verifier: GroundedSemanticVerifier | None = None
    subject_codes: frozenset[str] = FACTUAL_SUBJECT_CODES
    validator_id: str = "grounded-factual-subject"
    validator_version: str = "1.0.0"

    def validate(self, context: SubjectValidationContext) -> tuple[ValidationFinding, ...]:
        if self.verifier is None:
            return (
                self._finding(
                    code=SubjectFindingCode.FACTUAL_VERIFIER_UNAVAILABLE,
                    status=FindingStatus.WARN,
                    message=(
                        "No grounded semantic verifier is configured; human review is required."
                    ),
                    observed="verifier=not-configured",
                ),
            )
        request = SemanticVerificationRequest(
            grade=context.grade,
            medium=context.medium,
            subject_id=context.subject_id,
            subject_code=context.subject_code,
            curriculum_version_id=context.curriculum_version_id,
            selected_scope=context.selected_scope,
            candidate=context.candidate,
            grounding_sources=context.grounding_sources,
        )
        try:
            result = self.verifier.verify(request)
            self._validate_result(context, result)
        except Exception:
            return (
                self._finding(
                    code=SubjectFindingCode.FACTUAL_VERIFIER_UNAVAILABLE,
                    status=FindingStatus.WARN,
                    message=(
                        "Grounded semantic verification was unavailable; human review is required."
                    ),
                    observed="verifier=unavailable-or-invalid-result",
                ),
            )

        evidence_refs = ",".join(
            f"{item.context_id}@{item.source_document_id}:p{item.page_number}"
            for item in result.evidence_refs
        )
        observed = (
            f"status={result.status.value};evidence={evidence_refs or 'none'};"
            f"verifier={result.verifier_id}/{result.verifier_version};"
            f"prompt={result.prompt_version}"
        )
        if result.status is SemanticVerificationStatus.SUPPORTED:
            return (
                self._finding(
                    code=SubjectFindingCode.FACTUAL_GROUNDED,
                    status=FindingStatus.PASS,
                    message=(
                        "The structured verifier found the answer supported by reviewed evidence."
                    ),
                    observed=observed,
                ),
            )
        if result.status is SemanticVerificationStatus.CONTRADICTED:
            return (
                self._finding(
                    code=SubjectFindingCode.FACTUAL_SOURCE_CONTRADICTION,
                    status=FindingStatus.FAIL,
                    message="Reviewed evidence contradicts a material answer claim.",
                    observed=observed,
                ),
            )
        return (
            self._finding(
                code=SubjectFindingCode.FACTUAL_UNSUPPORTED_CLAIM,
                status=FindingStatus.WARN,
                message="Reviewed evidence is insufficient to verify the material answer claims.",
                observed=observed,
            ),
        )

    def _validate_result(
        self,
        context: SubjectValidationContext,
        result: SemanticVerificationResult,
    ) -> None:
        if not isinstance(result, SemanticVerificationResult):
            raise ValidationContractError("semantic verifier returned a malformed result")
        verifier = self.verifier
        if verifier is None:
            raise ValidationContractError("semantic verifier is not configured")
        if (
            result.verifier_id != verifier.verifier_id
            or result.verifier_version != verifier.verifier_version
            or result.prompt_version != verifier.prompt_version
        ):
            raise ValidationContractError("semantic verifier lineage is inconsistent")
        sources = {
            (source.context_id, source.source_document_id, source.page_number)
            for source in context.grounding_sources
        }
        if any(
            (reference.context_id, reference.source_document_id, reference.page_number)
            not in sources
            for reference in result.evidence_refs
        ):
            raise ValidationContractError("semantic evidence reference is outside trusted context")
        if (
            result.status
            in {
                SemanticVerificationStatus.SUPPORTED,
                SemanticVerificationStatus.CONTRADICTED,
            }
            and not result.evidence_refs
        ):
            raise ValidationContractError("conclusive semantic status requires evidence")

    def _finding(
        self,
        *,
        code: SubjectFindingCode,
        status: FindingStatus,
        message: str,
        observed: str,
    ) -> ValidationFinding:
        return ValidationFinding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=code,
            status=status,
            message=message,
            evidence=(
                FindingEvidence(
                    location="$.grounding_sources",
                    expected="bounded reviewed evidence with structured verification status",
                    observed=observed[:1_024],
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class SubjectValidationRouter:
    validators: tuple[SubjectValidator, ...]
    fallback_validator: GroundedFactualSubjectValidator | None
    validator_id: str = "subject-validation-router"
    validator_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.validators, tuple)
            or not self.validators
            or len(self.validators) > MAX_SUBJECT_VALIDATORS
        ):
            raise ValidationContractError(
                "subject validator registration must be non-empty and bounded"
            )
        registrations: dict[str, SubjectValidator] = {}
        identities: set[str] = set()
        for validator in self.validators:
            if (
                not isinstance(validator.subject_codes, frozenset)
                or not validator.subject_codes
                or not validator.validator_id
                or not validator.validator_version
                or not callable(validator.validate)
            ):
                raise ValidationContractError("subject validator registration is malformed")
            if validator.validator_id in identities:
                raise ValidationContractError("each subject validator must be registered once")
            identities.add(validator.validator_id)
            for code in validator.subject_codes:
                if code in registrations:
                    raise ValidationContractError("each subject code must be registered once")
                registrations[code] = validator
        if len(registrations) > MAX_SUBJECT_CODES:
            raise ValidationContractError("subject code registration must be bounded")
        if self.fallback_validator is not None and not isinstance(
            self.fallback_validator, GroundedFactualSubjectValidator
        ):
            raise ValidationContractError("fallback validator registration is malformed")

    @property
    def registration_lineage(self) -> tuple[tuple[str, str], ...]:
        values = [
            (validator.validator_id, validator.validator_version) for validator in self.validators
        ]
        values.append((self.validator_id, self.validator_version))
        return tuple(sorted(values))

    def validate(self, context: SubjectValidationContext) -> tuple[ValidationFinding, ...]:
        if not isinstance(context, SubjectValidationContext):
            raise ValidationContractError("subject router context is malformed")
        matched = next(
            (
                validator
                for validator in self.validators
                if context.subject_code in validator.subject_codes
            ),
            None,
        )
        if matched is None:
            return (
                ValidationFinding(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    code=SubjectFindingCode.SUBJECT_UNREGISTERED,
                    status=FindingStatus.WARN,
                    message="No subject validator is registered for this trusted subject code.",
                    evidence=(
                        FindingEvidence(
                            location="$.subject_scope.subject_code",
                            expected="a registered subject-specific validation route",
                            observed=context.subject_code,
                        ),
                    ),
                ),
            )

        findings = self._validated_findings(matched, matched.validate(context))
        requires_fallback = any(
            str(finding.code)
            in {
                SubjectFindingCode.MATH_UNIT_MISMATCH.value,
                SubjectFindingCode.MATH_UNSUPPORTED_EXPRESSION.value,
            }
            for finding in findings
        )
        if requires_fallback and self.fallback_validator is not None:
            fallback = self.fallback_validator
            findings += self._validated_findings(fallback, fallback.validate(context))
        return findings

    @staticmethod
    def _validated_findings(
        validator: SubjectValidator,
        findings: object,
    ) -> tuple[ValidationFinding, ...]:
        if (
            not isinstance(findings, tuple)
            or not findings
            or any(not isinstance(item, ValidationFinding) for item in findings)
        ):
            raise ValidationContractError(
                f"subject validator {validator.validator_id!r} returned malformed findings"
            )
        if any(
            item.validator_id != validator.validator_id
            or item.validator_version != validator.validator_version
            for item in findings
        ):
            raise ValidationContractError(
                f"subject validator {validator.validator_id!r} returned foreign lineage"
            )
        return findings
