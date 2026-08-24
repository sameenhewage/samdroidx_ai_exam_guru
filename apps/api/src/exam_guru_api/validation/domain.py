"""Immutable contracts for deterministic question validation.

The contracts deliberately preserve a raw structured candidate so schema failures can
be reported as findings rather than hidden by an eager parser.  Inputs are snapshotted
into immutable JSON values at the boundary; no validator may mutate provider output or
retrieved source data.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

REPORT_SCHEMA_VERSION = "question-validation-report.v1"
QUESTION_SCHEMA_VERSION = "question.v1"
MAX_CANDIDATE_CHARACTERS = 262_144
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 20_000
MAX_JSON_STRING_CHARACTERS = 131_072
MAX_GROUNDING_SOURCES = 128
MAX_DUPLICATE_REFERENCES = 10_000
MAX_SOURCE_TEXT_CHARACTERS = 32_000
MAX_DUPLICATE_TEXT_CHARACTERS = 16_000

REPORT_LIMITATIONS = (
    "Deterministic checks cover declared structure, encoded blueprint rules, identifier-level "
    "provenance, bounded text indicators, prohibited residue, and exact normalized hashes only.",
    "A passing report does not establish factual or semantic correctness, curriculum alignment, "
    "age appropriateness, language fluency, or paraphrase uniqueness.",
    "Generated content still requires the configured human review gate before any separate "
    "publication workflow may use it.",
)

_MACHINE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FINDING_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_QUESTION_TYPES = frozenset(
    {"multiple_choice", "short_answer", "structured", "structured_response"}
)


class ValidationContractError(ValueError):
    """Raised when the trusted validation boundary itself is malformed."""


class FindingStatus(StrEnum):
    PASS = "pass"  # noqa: S105 - validation outcome, not a credential
    WARN = "warn"
    FAIL = "fail"


class FindingCode(StrEnum):
    """Stable rule identities; status and evidence carry each rule's outcome."""

    SCHEMA_COMPLETENESS = "schema.completeness"
    BLUEPRINT_QUESTION_TYPE = "blueprint.question_type"
    BLUEPRINT_MARKS = "blueprint.marks"
    BLUEPRINT_OPTIONS = "blueprint.options"
    BLUEPRINT_EXACTLY_ONE_ANSWER = "blueprint.exactly_one_answer"
    GROUNDING_REFERENCES = "grounding.references"
    GROUNDING_PROVENANCE = "grounding.provenance"
    PROMPT_INJECTION_RESIDUE = "security.prompt_injection_residue"
    AGE_HEURISTIC = "heuristic.age_indicators"
    LANGUAGE_HEURISTIC = "heuristic.language_script"
    DUPLICATE_EXACT = "duplicate.exact_normalized_text"
    DUPLICATE_SHA256 = "duplicate.canonical_sha256"


def _require_machine_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _MACHINE_ID.fullmatch(value) or len(value) > 128:
        raise ValidationContractError(
            f"{field_name} must be a bounded lowercase machine identifier"
        )
    return value


def _require_machine_value(value: object, field_name: str, *, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(not character.isprintable() or character.isspace() for character in value)
    ):
        raise ValidationContractError(f"{field_name} must be a bounded machine value")
    return value


def _require_text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationContractError(f"{field_name} must be bounded non-blank text")
    return value


def _require_integer(value: object, field_name: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationContractError(
            f"{field_name} must be an integer between {minimum} and {maximum}"
        )
    return value


def canonicalize_text(value: str) -> str:
    """Return the version-one exact-match normalization, not a similarity score."""

    if not isinstance(value, str):
        raise ValidationContractError("canonical text must be a string")
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def canonical_text_sha256(value: str) -> str:
    return hashlib.sha256(canonicalize_text(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class FindingEvidence:
    """Bounded, serializable evidence attached to every finding."""

    location: str
    expected: str
    observed: str

    def __post_init__(self) -> None:
        _require_text(self.location, "evidence location", maximum=512)
        _require_text(self.expected, "evidence expected", maximum=1_024)
        _require_text(self.observed, "evidence observed", maximum=1_024)


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """One versioned deterministic rule result with mandatory evidence."""

    validator_id: str
    validator_version: str
    code: str
    status: FindingStatus
    message: str
    evidence: tuple[FindingEvidence, ...]

    def __post_init__(self) -> None:
        _require_machine_id(self.validator_id, "validator_id")
        _require_machine_value(self.validator_version, "validator_version")
        code = str(self.code)
        if not _FINDING_CODE.fullmatch(code) or len(code) > 128:
            raise ValidationContractError("finding code must be a stable lowercase machine code")
        if not isinstance(self.status, FindingStatus):
            raise ValidationContractError("finding status must be FindingStatus")
        _require_text(self.message, "finding message", maximum=1_024)
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ValidationContractError("every finding requires a non-empty evidence tuple")
        if any(not isinstance(item, FindingEvidence) for item in self.evidence):
            raise ValidationContractError("finding evidence must contain FindingEvidence values")
        canonical_evidence = tuple(sorted(self.evidence))
        if len(set(canonical_evidence)) != len(canonical_evidence):
            raise ValidationContractError("finding evidence cannot contain duplicates")
        object.__setattr__(self, "evidence", canonical_evidence)


@dataclass(frozen=True, slots=True)
class BlueprintRequirements:
    """Trusted deterministic requirements for one generated blueprint slot."""

    slot_id: str
    schema_version: str
    question_type: str
    marks: int
    language: str
    minimum_age: int
    maximum_age: int
    minimum_options: int = 2
    maximum_options: int = 8

    def __post_init__(self) -> None:
        _require_machine_value(self.slot_id, "slot_id", maximum=256)
        _require_machine_value(self.schema_version, "schema_version")
        question_type = _require_machine_value(self.question_type, "question_type")
        if question_type not in _SUPPORTED_QUESTION_TYPES:
            raise ValidationContractError("question_type is not supported by validation v1")
        _require_integer(self.marks, "marks", minimum=1, maximum=100)
        _require_machine_value(self.language, "language", maximum=32)
        _require_integer(self.minimum_age, "minimum_age", minimum=1, maximum=18)
        _require_integer(self.maximum_age, "maximum_age", minimum=1, maximum=18)
        if self.minimum_age > self.maximum_age:
            raise ValidationContractError("minimum_age cannot exceed maximum_age")
        _require_integer(self.minimum_options, "minimum_options", minimum=1, maximum=16)
        _require_integer(self.maximum_options, "maximum_options", minimum=1, maximum=16)
        if self.minimum_options > self.maximum_options:
            raise ValidationContractError("minimum_options cannot exceed maximum_options")


@dataclass(frozen=True, slots=True)
class GroundingSource:
    """Retrieved source data and its expected immutable provenance fields.

    Provenance members may be absent or malformed enough to require a finding.  The
    envelope still enforces type and resource limits before validation work begins.
    Source ``text`` remains untrusted data and is never scanned as generated output.
    """

    context_id: str
    text: str = field(repr=False)
    source_document_id: str | None = None
    source_version: str | None = None
    page_number: int | None = None
    chunk_id: str | None = None

    def __post_init__(self) -> None:
        _require_machine_value(self.context_id, "context_id")
        _require_text(self.text, "grounding source text", maximum=MAX_SOURCE_TEXT_CHARACTERS)
        for field_name in ("source_document_id", "source_version", "chunk_id"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or len(value) > 256):
                raise ValidationContractError(f"{field_name} must be absent or bounded text")
        if self.page_number is not None and not isinstance(self.page_number, int):
            raise ValidationContractError("page_number must be absent or an integer")


@dataclass(frozen=True, slots=True)
class DuplicateReference:
    """A trusted bank reference usable for exact text and/or canonical hash checks."""

    question_id: str
    text: str | None = field(default=None, repr=False)
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_machine_value(self.question_id, "question_id")
        if self.text is None and self.content_sha256 is None:
            raise ValidationContractError("a duplicate reference requires text or SHA-256")
        if self.text is not None:
            _require_text(
                self.text,
                "duplicate reference text",
                maximum=MAX_DUPLICATE_TEXT_CHARACTERS,
            )
        if self.content_sha256 is not None and not _LOWER_SHA256.fullmatch(self.content_sha256):
            raise ValidationContractError("content_sha256 must be a lowercase SHA-256 digest")
        if (
            self.text is not None
            and self.content_sha256 is not None
            and canonical_text_sha256(self.text) != self.content_sha256
        ):
            raise ValidationContractError("duplicate reference text and SHA-256 disagree")

    @property
    def effective_sha256(self) -> str:
        if self.content_sha256 is not None:
            return self.content_sha256
        return canonical_text_sha256(cast(str, self.text))


def _snapshot_json_value(
    value: object,
    *,
    depth: int,
    active_container_ids: set[int],
    node_count: list[int],
) -> tuple[object, object]:
    node_count[0] += 1
    if node_count[0] > MAX_JSON_NODES:
        raise ValidationContractError("candidate JSON exceeds the node limit")
    if depth > MAX_JSON_DEPTH:
        raise ValidationContractError("candidate JSON exceeds the nesting limit")

    if value is None or isinstance(value, bool | int | str):
        if isinstance(value, str) and len(value) > MAX_JSON_STRING_CHARACTERS:
            raise ValidationContractError("candidate JSON contains an oversized string")
        return value, value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationContractError("candidate JSON numbers must be finite")
        return value, value

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValidationContractError("candidate JSON cannot contain cycles")
        active_container_ids.add(container_id)
        try:
            if any(not isinstance(key, str) for key in value):
                raise ValidationContractError("candidate JSON object keys must be strings")
            frozen_values: dict[str, object] = {}
            plain_values: dict[str, object] = {}
            for key in sorted(cast(Sequence[str], tuple(value.keys()))):
                frozen, plain = _snapshot_json_value(
                    value[key],
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    node_count=node_count,
                )
                frozen_values[key] = frozen
                plain_values[key] = plain
            return MappingProxyType(frozen_values), plain_values
        finally:
            active_container_ids.remove(container_id)

    if isinstance(value, list | tuple):
        container_id = id(value)
        if container_id in active_container_ids:
            raise ValidationContractError("candidate JSON cannot contain cycles")
        active_container_ids.add(container_id)
        try:
            snapshots = tuple(
                _snapshot_json_value(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    node_count=node_count,
                )
                for item in value
            )
            return tuple(item[0] for item in snapshots), [item[1] for item in snapshots]
        finally:
            active_container_ids.remove(container_id)

    raise ValidationContractError("candidate must contain only JSON-compatible values")


def _grounding_sort_key(source: GroundingSource) -> tuple[str, str, str, int, str, str]:
    return (
        source.context_id,
        source.source_document_id or "",
        source.source_version or "",
        source.page_number if isinstance(source.page_number, int) else -1,
        source.chunk_id or "",
        hashlib.sha256(source.text.encode("utf-8")).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class ValidationInput:
    """Immutable snapshot consumed by every validator in a pipeline run."""

    candidate_id: str
    candidate: Mapping[str, object] = field(repr=False)
    blueprint: BlueprintRequirements
    grounding_sources: tuple[GroundingSource, ...]
    duplicate_references: tuple[DuplicateReference, ...] = ()
    candidate_fingerprint: str = field(init=False)
    input_fingerprint: str = field(init=False)
    _candidate_json: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_machine_value(self.candidate_id, "candidate_id")
        if not isinstance(self.candidate, Mapping):
            raise ValidationContractError("candidate must be a JSON object mapping")
        frozen, plain = _snapshot_json_value(
            self.candidate,
            depth=0,
            active_container_ids=set(),
            node_count=[0],
        )
        frozen_candidate = cast(Mapping[str, object], frozen)
        plain_candidate = cast(dict[str, object], plain)
        candidate_json = json.dumps(
            plain_candidate,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(candidate_json) > MAX_CANDIDATE_CHARACTERS:
            raise ValidationContractError("candidate JSON exceeds the character limit")
        object.__setattr__(self, "candidate", frozen_candidate)
        object.__setattr__(self, "_candidate_json", candidate_json)
        candidate_fingerprint = hashlib.sha256(candidate_json.encode("utf-8")).hexdigest()
        object.__setattr__(self, "candidate_fingerprint", candidate_fingerprint)

        if not isinstance(self.blueprint, BlueprintRequirements):
            raise ValidationContractError("blueprint must be BlueprintRequirements")
        if (
            not isinstance(self.grounding_sources, tuple)
            or len(self.grounding_sources) > MAX_GROUNDING_SOURCES
            or any(not isinstance(item, GroundingSource) for item in self.grounding_sources)
        ):
            raise ValidationContractError("grounding_sources must be a bounded tuple")
        if (
            not isinstance(self.duplicate_references, tuple)
            or len(self.duplicate_references) > MAX_DUPLICATE_REFERENCES
            or any(not isinstance(item, DuplicateReference) for item in self.duplicate_references)
        ):
            raise ValidationContractError("duplicate_references must be a bounded tuple")

        canonical_sources = tuple(sorted(self.grounding_sources, key=_grounding_sort_key))
        canonical_duplicates = tuple(
            sorted(
                self.duplicate_references,
                key=lambda item: (item.question_id, item.effective_sha256, item.text or ""),
            )
        )
        duplicate_ids = tuple(item.question_id for item in canonical_duplicates)
        if len(set(duplicate_ids)) != len(duplicate_ids):
            raise ValidationContractError("duplicate reference question identifiers must be unique")
        object.__setattr__(self, "grounding_sources", canonical_sources)
        object.__setattr__(self, "duplicate_references", canonical_duplicates)

        fingerprint_material = {
            "blueprint": {
                "language": self.blueprint.language,
                "marks": self.blueprint.marks,
                "maximum_age": self.blueprint.maximum_age,
                "maximum_options": self.blueprint.maximum_options,
                "minimum_age": self.blueprint.minimum_age,
                "minimum_options": self.blueprint.minimum_options,
                "question_type": self.blueprint.question_type,
                "schema_version": self.blueprint.schema_version,
                "slot_id": self.blueprint.slot_id,
            },
            "candidate_fingerprint": candidate_fingerprint,
            "candidate_id": self.candidate_id,
            "duplicate_references": [
                {
                    "content_sha256": item.effective_sha256,
                    "question_id": item.question_id,
                }
                for item in canonical_duplicates
            ],
            "grounding_sources": [
                {
                    "chunk_id": item.chunk_id,
                    "context_id": item.context_id,
                    "page_number": item.page_number,
                    "source_document_id": item.source_document_id,
                    "source_text_sha256": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                    "source_version": item.source_version,
                }
                for item in canonical_sources
            ],
        }
        serialized_material = json.dumps(
            fingerprint_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(
            self,
            "input_fingerprint",
            hashlib.sha256(serialized_material.encode("utf-8")).hexdigest(),
        )


_STATUS_PRECEDENCE = {
    FindingStatus.PASS: 0,
    FindingStatus.WARN: 1,
    FindingStatus.FAIL: 2,
}


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Canonical pipeline output with no timestamps or provider-dependent values."""

    candidate_id: str
    pipeline_version: str
    findings: tuple[ValidationFinding, ...]
    report_schema_version: str = field(default=REPORT_SCHEMA_VERSION, init=False)
    limitations: tuple[str, ...] = field(default=REPORT_LIMITATIONS, init=False)
    overall_status: FindingStatus = field(init=False)
    report_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _require_machine_value(self.candidate_id, "candidate_id")
        _require_machine_value(self.pipeline_version, "pipeline_version")
        if (
            not isinstance(self.findings, tuple)
            or not self.findings
            or any(not isinstance(item, ValidationFinding) for item in self.findings)
        ):
            raise ValidationContractError("a report requires a non-empty finding tuple")
        canonical_findings = tuple(
            sorted(self.findings, key=lambda item: (item.validator_id, item.code))
        )
        identities = tuple((item.validator_id, item.code) for item in canonical_findings)
        if len(set(identities)) != len(identities):
            raise ValidationContractError("report contains duplicate validator finding codes")
        object.__setattr__(self, "findings", canonical_findings)
        overall_status = max(
            (item.status for item in canonical_findings),
            key=_STATUS_PRECEDENCE.__getitem__,
        )
        object.__setattr__(self, "overall_status", overall_status)

        report_material = {
            "candidate_id": self.candidate_id,
            "findings": [
                {
                    "code": item.code,
                    "evidence": [
                        {
                            "expected": evidence.expected,
                            "location": evidence.location,
                            "observed": evidence.observed,
                        }
                        for evidence in item.evidence
                    ],
                    "message": item.message,
                    "status": item.status.value,
                    "validator_id": item.validator_id,
                    "validator_version": item.validator_version,
                }
                for item in canonical_findings
            ],
            "limitations": list(self.limitations),
            "pipeline_version": self.pipeline_version,
            "report_schema_version": self.report_schema_version,
        }
        serialized = json.dumps(
            report_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(
            self,
            "report_fingerprint",
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    @property
    def failures(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.status is FindingStatus.FAIL)

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        return tuple(item for item in self.findings if item.status is FindingStatus.WARN)

    @property
    def passed(self) -> bool:
        return self.overall_status is FindingStatus.PASS

    @property
    def blocked(self) -> bool:
        return self.overall_status is FindingStatus.FAIL


ValidationEvidence = FindingEvidence
ValidationResult = ValidationReport
ValidationStatus = FindingStatus
ValidationSubject = ValidationInput
