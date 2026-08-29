"""Trusted subject routing, curriculum-scope checks, and grounded verifier contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
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
MAX_SEMANTIC_ACCOUNTING_TOKENS = 10_000_000
MAX_SEMANTIC_COST_MICROUSD = 100_000_000_000
MAX_SEMANTIC_LATENCY_MS = 120_000
MAX_FACTUAL_CLAIMS = 32
MAX_FACTUAL_CLAIM_TEXT_CHARACTERS = 16_000
MAX_SEMANTIC_CLAIM_SUMMARY_CHARACTERS = 512
FACTUAL_CLAIM_DECOMPOSITION_VERSION = "deterministic-factual-claims.v1"
_CLAIM_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CLAIM_SENTENCE_BOUNDARY = re.compile(r"(?:[\r\n]+|(?<=[.!?\u3002\uff01\uff1f])\s+)")


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


class FactualClaimType(StrEnum):
    ANSWER = "answer"
    EXPLANATION = "explanation"
    MARKING = "marking"


@dataclass(frozen=True, slots=True)
class FactualClaim:
    claim_id: str
    claim_type: FactualClaimType
    location: str
    text: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claim_id, str)
            or len(self.claim_id) > 128
            or not _CLAIM_ID.fullmatch(self.claim_id)
        ):
            raise ValidationContractError("factual claim id must be a bounded machine identifier")
        if not isinstance(self.claim_type, FactualClaimType):
            raise ValidationContractError("factual claim type is invalid")
        if (
            not isinstance(self.location, str)
            or not self.location.startswith("$.candidate.")
            or len(self.location) > 512
            or not self.location.isprintable()
        ):
            raise ValidationContractError("factual claim location must be a bounded candidate path")
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or self.text != self.text.strip()
            or len(self.text) > MAX_FACTUAL_CLAIM_TEXT_CHARACTERS
        ):
            raise ValidationContractError("factual claim text must be bounded trimmed text")


def _claim_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationContractError(f"claim decomposition {label} must be an object")
    return cast(Mapping[str, object], value)


def _claim_sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValidationContractError(f"claim decomposition {label} must be an array")
    return cast(Sequence[object], value)


def _claim_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationContractError(f"claim decomposition {label} must be text")
    return value.strip()


def _claim_sentences(value: str) -> tuple[str, ...]:
    return tuple(
        sentence.strip() for sentence in _CLAIM_SENTENCE_BOUNDARY.split(value) if sentence.strip()
    )


def _marking_claim_id(value: str, ordinal: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    suffix = normalized[:96] or f"criterion-{ordinal + 1}"
    return f"marking-{suffix}"


def decompose_factual_claims(candidate: Mapping[str, object]) -> tuple[FactualClaim, ...]:
    root = _claim_mapping(candidate, "candidate")
    stem = _claim_text(root.get("stem"), "stem")
    question_type = _claim_text(root.get("question_type"), "question_type")
    answer = _claim_mapping(root.get("answer"), "answer")
    explanation = _claim_text(answer.get("explanation"), "answer explanation")
    answer_values: tuple[str, ...]
    if question_type == "multiple_choice":
        correct_option_id = _claim_text(answer.get("correct_option_id"), "correct option")
        option_texts = {
            _claim_text(option.get("option_id"), "option id"): _claim_text(
                option.get("text"), "option text"
            )
            for option in (
                _claim_mapping(item, "option")
                for item in _claim_sequence(root.get("options"), "options")
            )
        }
        if correct_option_id not in option_texts:
            raise ValidationContractError("claim decomposition correct option is missing")
        answer_values = (option_texts[correct_option_id],)
    else:
        answer_values = tuple(
            _claim_text(item, "accepted response")
            for item in _claim_sequence(answer.get("accepted_responses"), "accepted responses")
        )
        if not answer_values:
            raise ValidationContractError("claim decomposition requires an accepted response")
    claims: list[FactualClaim] = [
        FactualClaim(
            claim_id="answer" if question_type == "multiple_choice" else f"answer-{ordinal + 1}",
            claim_type=FactualClaimType.ANSWER,
            location=(
                "$.candidate.answer"
                if question_type == "multiple_choice"
                else f"$.candidate.answer.accepted_responses[{ordinal}]"
            ),
            text=f"Question: {stem}\nProposed answer: {answer_value}",
        )
        for ordinal, answer_value in enumerate(answer_values)
    ]
    claims.extend(
        FactualClaim(
            claim_id=f"explanation-{ordinal + 1}",
            claim_type=FactualClaimType.EXPLANATION,
            location=f"$.candidate.answer.explanation#{ordinal + 1}",
            text=sentence,
        )
        for ordinal, sentence in enumerate(_claim_sentences(explanation))
    )
    marking = _claim_mapping(root.get("marking"), "marking")
    criteria = _claim_sequence(marking.get("criteria"), "marking criteria")
    for ordinal, raw_criterion in enumerate(criteria):
        criterion = _claim_mapping(raw_criterion, "marking criterion")
        criterion_id = _claim_text(criterion.get("criterion_id"), "criterion id")
        claims.append(
            FactualClaim(
                claim_id=_marking_claim_id(criterion_id, ordinal),
                claim_type=FactualClaimType.MARKING,
                location=f"$.candidate.marking.criteria[{ordinal}]",
                text=_claim_text(criterion.get("description"), "criterion description"),
            )
        )
    if not 2 <= len(claims) <= MAX_FACTUAL_CLAIMS:
        raise ValidationContractError("claim decomposition count is outside its bound")
    claim_ids = tuple(claim.claim_id for claim in claims)
    if len(set(claim_ids)) != len(claim_ids):
        raise ValidationContractError("claim decomposition identifiers must be unique")
    return tuple(claims)


def _bounded_semantic_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be an integer between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class SemanticVerifierBudget:
    max_grounding_sources: int = 8
    max_source_bytes: int = 8_192
    max_total_source_bytes: int = 32_768
    max_candidate_bytes: int = 32_768
    max_request_bytes: int = 65_536
    max_output_tokens: int = 512
    max_cost_microusd: int = 1_000_000

    def __post_init__(self) -> None:
        _bounded_semantic_integer(
            self.max_grounding_sources,
            "max_grounding_sources",
            minimum=1,
            maximum=32,
        )
        _bounded_semantic_integer(
            self.max_source_bytes,
            "max_source_bytes",
            minimum=1,
            maximum=32_768,
        )
        _bounded_semantic_integer(
            self.max_total_source_bytes,
            "max_total_source_bytes",
            minimum=1,
            maximum=262_144,
        )
        _bounded_semantic_integer(
            self.max_candidate_bytes,
            "max_candidate_bytes",
            minimum=1,
            maximum=262_144,
        )
        _bounded_semantic_integer(
            self.max_request_bytes,
            "max_request_bytes",
            minimum=1,
            maximum=524_288,
        )
        _bounded_semantic_integer(
            self.max_output_tokens,
            "max_output_tokens",
            minimum=1,
            maximum=4_096,
        )
        _bounded_semantic_integer(
            self.max_cost_microusd,
            "max_cost_microusd",
            minimum=1,
            maximum=MAX_SEMANTIC_COST_MICROUSD,
        )
        if self.max_source_bytes > self.max_total_source_bytes:
            raise ValueError("per-source bytes cannot exceed total source bytes")
        if self.max_candidate_bytes > self.max_request_bytes:
            raise ValueError("candidate bytes cannot exceed request bytes")


@dataclass(frozen=True, slots=True)
class SemanticVerifierAccounting:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_microusd: int
    latency_ms: int

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "total_tokens"):
            _bounded_semantic_integer(
                getattr(self, field_name),
                field_name,
                minimum=0,
                maximum=MAX_SEMANTIC_ACCOUNTING_TOKENS,
            )
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("semantic total_tokens must equal input_tokens plus output_tokens")
        _bounded_semantic_integer(
            self.cost_microusd,
            "cost_microusd",
            minimum=0,
            maximum=MAX_SEMANTIC_COST_MICROUSD,
        )
        _bounded_semantic_integer(
            self.latency_ms,
            "latency_ms",
            minimum=0,
            maximum=MAX_SEMANTIC_LATENCY_MS,
        )


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
class SemanticClaimVerification:
    claim_id: str
    status: SemanticVerificationStatus
    summary: str
    evidence_refs: tuple[SemanticEvidenceReference, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claim_id, str)
            or len(self.claim_id) > 128
            or not _CLAIM_ID.fullmatch(self.claim_id)
        ):
            raise ValidationContractError("semantic claim id must be a bounded machine identifier")
        if not isinstance(self.status, SemanticVerificationStatus):
            raise ValidationContractError("semantic claim status is invalid")
        if (
            not isinstance(self.summary, str)
            or not self.summary.strip()
            or self.summary != self.summary.strip()
            or len(self.summary) > MAX_SEMANTIC_CLAIM_SUMMARY_CHARACTERS
        ):
            raise ValidationContractError("semantic claim summary must be bounded trimmed text")
        if (
            not isinstance(self.evidence_refs, tuple)
            or len(self.evidence_refs) > MAX_SEMANTIC_EVIDENCE_REFS
            or any(not isinstance(item, SemanticEvidenceReference) for item in self.evidence_refs)
        ):
            raise ValidationContractError("semantic claim evidence must be a bounded tuple")
        canonical = tuple(sorted(self.evidence_refs))
        if len(set(canonical)) != len(canonical):
            raise ValidationContractError("semantic claim evidence cannot contain duplicates")
        if (
            self.status
            in {
                SemanticVerificationStatus.SUPPORTED,
                SemanticVerificationStatus.CONTRADICTED,
            }
            and not canonical
        ):
            raise ValidationContractError("conclusive claim verification requires evidence")
        object.__setattr__(self, "evidence_refs", canonical)


@dataclass(frozen=True, slots=True)
class SemanticVerificationRequest:
    grade: int
    medium: str
    subject_id: UUID
    subject_code: str
    curriculum_version_id: UUID
    selected_scope: CurriculumSelection
    candidate: Mapping[str, object]
    claims: tuple[FactualClaim, ...]
    grounding_sources: tuple[GroundingSource, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.claims, tuple)
            or not 1 <= len(self.claims) <= MAX_FACTUAL_CLAIMS
            or any(not isinstance(claim, FactualClaim) for claim in self.claims)
        ):
            raise ValidationContractError("semantic verification claims must be a bounded tuple")
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValidationContractError("semantic verification claim identifiers must be unique")


@dataclass(frozen=True, slots=True)
class SemanticVerificationResult:
    status: SemanticVerificationStatus
    summary: str
    evidence_refs: tuple[SemanticEvidenceReference, ...]
    claims: tuple[SemanticClaimVerification, ...]
    verifier_id: str
    verifier_version: str
    prompt_version: str
    provider: str
    provider_version: str
    model: str
    model_version: str
    pricing_version: str
    accounting: SemanticVerifierAccounting

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
        if (
            not isinstance(self.claims, tuple)
            or not 1 <= len(self.claims) <= MAX_FACTUAL_CLAIMS
            or any(not isinstance(claim, SemanticClaimVerification) for claim in self.claims)
        ):
            raise ValidationContractError("semantic claim results must be a bounded tuple")
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValidationContractError("semantic claim result identifiers must be unique")
        aggregate_status = (
            SemanticVerificationStatus.CONTRADICTED
            if any(claim.status is SemanticVerificationStatus.CONTRADICTED for claim in self.claims)
            else SemanticVerificationStatus.INSUFFICIENT_EVIDENCE
            if any(
                claim.status is SemanticVerificationStatus.INSUFFICIENT_EVIDENCE
                for claim in self.claims
            )
            else SemanticVerificationStatus.SUPPORTED
        )
        if self.status is not aggregate_status:
            raise ValidationContractError("semantic overall status must match its claim results")
        claim_evidence = tuple(
            sorted({reference for claim in self.claims for reference in claim.evidence_refs})
        )
        if canonical != claim_evidence:
            raise ValidationContractError("semantic overall evidence must match its claim results")
        for field_name in (
            "verifier_id",
            "verifier_version",
            "prompt_version",
            "provider",
            "provider_version",
            "model",
            "model_version",
            "pricing_version",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > 128
                or any(character.isspace() or not character.isprintable() for character in value)
            ):
                raise ValidationContractError(f"semantic {field_name} must be a machine value")
        if not isinstance(self.accounting, SemanticVerifierAccounting):
            raise ValidationContractError("semantic accounting is malformed")


class GroundedSemanticVerifier(Protocol):
    @property
    def verifier_id(self) -> str: ...

    @property
    def verifier_version(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def provider(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def pricing_version(self) -> str: ...

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
        if (
            not binding.programme_authorized
            and binding.curriculum_version_id != trusted.curriculum_version_id
        ):
            add(
                SubjectFindingCode.SCOPE_CURRICULUM_MISMATCH,
                f"{location}.curriculum_version_id",
                trusted.curriculum_version_id,
                binding.curriculum_version_id,
            )
        if not binding.programme_authorized and binding.subject_id != trusted.subject_id:
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
        if (
            not binding.programme_authorized
            and trusted.unit_ids
            and binding.unit_id not in trusted.unit_ids
        ):
            add(
                SubjectFindingCode.SCOPE_OUTSIDE_SELECTED_UNIT,
                f"{location}.unit_id",
                trusted.unit_ids,
                binding.unit_id,
            )
        if (
            not binding.programme_authorized
            and trusted.lesson_ids
            and binding.lesson_id not in trusted.lesson_ids
        ):
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
    base_validator_version: str = "2.0.0"

    @property
    def validator_version(self) -> str:
        if self.verifier is None:
            return f"{self.base_validator_version}+unconfigured"
        material = json.dumps(
            {
                "verifier_id": self.verifier.verifier_id,
                "verifier_version": self.verifier.verifier_version,
                "prompt_version": self.verifier.prompt_version,
                "provider": self.verifier.provider,
                "provider_version": self.verifier.provider_version,
                "model": self.verifier.model,
                "model_version": self.verifier.model_version,
                "pricing_version": self.verifier.pricing_version,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = hashlib.sha256(material).hexdigest()[:16]
        return f"{self.base_validator_version}+configured-{fingerprint}"

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
                    details=self._unconfigured_details(context),
                ),
            )
        request: SemanticVerificationRequest | None = None
        try:
            request = SemanticVerificationRequest(
                grade=context.grade,
                medium=context.medium,
                subject_id=context.subject_id,
                subject_code=context.subject_code,
                curriculum_version_id=context.curriculum_version_id,
                selected_scope=context.selected_scope,
                candidate=context.candidate,
                claims=decompose_factual_claims(context.candidate),
                grounding_sources=context.grounding_sources,
            )
            result = self.verifier.verify(request)
            self._validate_result(context, result)
        except Exception as error:
            return (
                self._finding(
                    code=SubjectFindingCode.FACTUAL_VERIFIER_UNAVAILABLE,
                    status=FindingStatus.WARN,
                    message=(
                        "Grounded semantic verification was unavailable; human review is required."
                    ),
                    observed=self._failure_observed(error),
                    details=self._failure_details(request, error),
                ),
            )

        details = self._success_details(request, result)
        evidence_refs = ",".join(
            f"{item.context_id}@{item.source_document_id}:p{item.page_number}"
            for item in result.evidence_refs
        )
        observed = (
            f"status={result.status.value};evidence={evidence_refs or 'none'};"
            f"verifier={result.verifier_id}/{result.verifier_version};"
            f"prompt={result.prompt_version};provider={result.provider}/{result.provider_version};"
            f"model={result.model}/{result.model_version};pricing={result.pricing_version};"
            f"tokens={result.accounting.input_tokens}+{result.accounting.output_tokens};"
            f"cost_microusd={result.accounting.cost_microusd};"
            f"latency_ms={result.accounting.latency_ms}"
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
                    details=details,
                ),
            )
        if result.status is SemanticVerificationStatus.CONTRADICTED:
            return (
                self._finding(
                    code=SubjectFindingCode.FACTUAL_SOURCE_CONTRADICTION,
                    status=FindingStatus.FAIL,
                    message="Reviewed evidence contradicts a material answer claim.",
                    observed=observed,
                    details=details,
                ),
            )
        return (
            self._finding(
                code=SubjectFindingCode.FACTUAL_UNSUPPORTED_CLAIM,
                status=FindingStatus.WARN,
                message="Reviewed evidence is insufficient to verify the material answer claims.",
                observed=observed,
                details=details,
            ),
        )

    @staticmethod
    def _accounting_details(accounting: SemanticVerifierAccounting) -> dict[str, int]:
        return {
            "input_tokens": accounting.input_tokens,
            "output_tokens": accounting.output_tokens,
            "total_tokens": accounting.total_tokens,
            "cost_microusd": accounting.cost_microusd,
            "latency_ms": accounting.latency_ms,
        }

    @staticmethod
    def _reference_details(reference: SemanticEvidenceReference) -> dict[str, object]:
        return {
            "context_id": reference.context_id,
            "source_document_id": reference.source_document_id,
            "page_number": reference.page_number,
        }

    def _lineage_details(self) -> dict[str, str] | None:
        verifier = self.verifier
        if verifier is None:
            return None
        return {
            "verifier_id": verifier.verifier_id,
            "verifier_version": verifier.verifier_version,
            "prompt_version": verifier.prompt_version,
            "provider": verifier.provider,
            "provider_version": verifier.provider_version,
            "model": verifier.model,
            "model_version": verifier.model_version,
            "pricing_version": verifier.pricing_version,
        }

    def _success_details(
        self,
        request: SemanticVerificationRequest,
        result: SemanticVerificationResult,
    ) -> dict[str, object]:
        inputs = {claim.claim_id: claim for claim in request.claims}
        return {
            "schema_version": "semantic-verification.v1",
            "decomposition_version": FACTUAL_CLAIM_DECOMPOSITION_VERSION,
            "call_attempted": True,
            "failure_code": None,
            "status": result.status.value,
            "summary": result.summary,
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "claim_type": inputs[claim.claim_id].claim_type.value,
                    "location": inputs[claim.claim_id].location,
                    "status": claim.status.value,
                    "summary": claim.summary,
                    "evidence_refs": [
                        self._reference_details(reference) for reference in claim.evidence_refs
                    ],
                }
                for claim in result.claims
            ],
            "lineage": self._lineage_details(),
            "accounting": self._accounting_details(result.accounting),
        }

    def _failure_details(
        self,
        request: SemanticVerificationRequest | None,
        error: Exception,
    ) -> dict[str, object]:
        accounting = getattr(error, "accounting", None)
        return {
            "schema_version": "semantic-verification.v1",
            "decomposition_version": FACTUAL_CLAIM_DECOMPOSITION_VERSION,
            "call_attempted": request is not None,
            "failure_code": self._failure_code(error),
            "status": "unavailable",
            "summary": "Grounded verification was unavailable; human review is required.",
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "claim_type": claim.claim_type.value,
                    "location": claim.location,
                    "status": "unavailable",
                    "summary": "This claim requires human review.",
                    "evidence_refs": [],
                }
                for claim in request.claims
            ]
            if request is not None
            else [],
            "lineage": self._lineage_details(),
            "accounting": self._accounting_details(accounting)
            if isinstance(accounting, SemanticVerifierAccounting)
            else None,
        }

    def _unconfigured_details(self, context: SubjectValidationContext) -> dict[str, object]:
        try:
            claims = decompose_factual_claims(context.candidate)
        except ValidationContractError:
            claims = ()
        return {
            "schema_version": "semantic-verification.v1",
            "decomposition_version": FACTUAL_CLAIM_DECOMPOSITION_VERSION,
            "call_attempted": False,
            "failure_code": "not_configured",
            "status": "unavailable",
            "summary": "No grounded verifier is configured; human review is required.",
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "claim_type": claim.claim_type.value,
                    "location": claim.location,
                    "status": "unavailable",
                    "summary": "This claim requires human review.",
                    "evidence_refs": [],
                }
                for claim in claims
            ],
            "lineage": None,
            "accounting": None,
        }

    @staticmethod
    def _failure_code(error: Exception) -> str:
        allowed_codes = {
            "authentication",
            "permission_denied",
            "rate_limited",
            "timeout",
            "content_filtered",
            "invalid_request",
            "invalid_response",
            "resource_limit",
            "cost_limit",
            "provider_unavailable",
        }
        raw_code = getattr(error, "code", None)
        code = raw_code.value if isinstance(raw_code, StrEnum) else None
        return code if code in allowed_codes else "unavailable-or-invalid-result"

    def _failure_observed(self, error: Exception) -> str:
        failure_code = self._failure_code(error)
        verifier = cast(GroundedSemanticVerifier, self.verifier)
        parts = [
            f"failure={failure_code}",
            f"verifier={verifier.verifier_id}/{verifier.verifier_version}",
            f"prompt={verifier.prompt_version}",
            f"provider={verifier.provider}/{verifier.provider_version}",
            f"model={verifier.model}/{verifier.model_version}",
            f"pricing={verifier.pricing_version}",
        ]
        accounting = getattr(error, "accounting", None)
        if isinstance(accounting, SemanticVerifierAccounting):
            parts.extend(
                (
                    f"tokens={accounting.input_tokens}+{accounting.output_tokens}",
                    f"cost_microusd={accounting.cost_microusd}",
                    f"latency_ms={accounting.latency_ms}",
                )
            )
        return ";".join(parts)

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
        expected_claim_ids = tuple(
            claim.claim_id for claim in decompose_factual_claims(context.candidate)
        )
        if tuple(claim.claim_id for claim in result.claims) != expected_claim_ids:
            raise ValidationContractError("semantic verifier claim set is inconsistent")
        if (
            result.verifier_id != verifier.verifier_id
            or result.verifier_version != verifier.verifier_version
            or result.prompt_version != verifier.prompt_version
            or result.provider != verifier.provider
            or result.provider_version != verifier.provider_version
            or result.model != verifier.model
            or result.model_version != verifier.model_version
            or result.pricing_version != verifier.pricing_version
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
        details: Mapping[str, object],
    ) -> ValidationFinding:
        return ValidationFinding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=code,
            status=status,
            message=message,
            evidence=(
                FindingEvidence(
                    location="$.semantic_verification",
                    expected="bounded reviewed evidence with structured verification status",
                    observed=observed[:1_024],
                    details=details,
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
