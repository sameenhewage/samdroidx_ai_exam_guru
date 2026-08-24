"""Deterministic, provider-free validators for generated question candidates.

These checks intentionally stop at machine-verifiable structure, declared references,
bounded indicators, prohibited phrase residue, exact canonical matching, and bounded
lexical overlap. They do not infer factual support, solve arbitrary questions, or claim
semantic quality or semantic paraphrase detection.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from exam_guru_api.validation.domain import (
    MAX_DUPLICATE_TEXT_CHARACTERS,
    FindingCode,
    FindingEvidence,
    FindingStatus,
    GroundingSource,
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
    canonical_text_sha256,
    canonicalize_text,
)

_MAX_SCHEMA_OPTIONS = 16
_MAX_SCHEMA_CRITERIA = 64
_MAX_SCHEMA_REFERENCES = 128
_MAX_SCHEMA_TEXT = 16_000
_MAX_AUTHORED_SCAN_CHARACTERS = 64_000
_MAX_EVIDENCE_ISSUES = 64


def _require_input(validation_input: object) -> ValidationInput:
    if not isinstance(validation_input, ValidationInput):
        raise ValidationContractError("validator input must be ValidationInput")
    return validation_input


def _is_non_blank_text(value: object, *, maximum: int = _MAX_SCHEMA_TEXT) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _is_integer(value: object, *, minimum: int = 1, maximum: int = 100) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _describe(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"text(length={len(value)}, sha256={digest})"
    if isinstance(value, tuple):
        return f"array(length={len(value)})"
    if isinstance(value, Mapping):
        keys = tuple(sorted(str(key) for key in value))
        digest = hashlib.sha256("\0".join(keys).encode("utf-8")).hexdigest()
        return f"object(key_count={len(keys)}, keys_sha256={digest})"
    return f"{type(value).__name__}({value!r})"[:160]


def _summarize_identifiers(
    values: list[str],
    *,
    count_label: str,
    identifiers_label: str,
) -> str:
    canonical_values = sorted(set(values))
    digest = hashlib.sha256("\0".join(canonical_values).encode("utf-8")).hexdigest()
    prefix = f"{count_label}={len(canonical_values)}; {identifiers_label}="
    suffix = f"; ids_sha256={digest}"
    available = 700 - len(prefix) - len(suffix)
    displayed: list[str] = []
    used = 0
    for value in canonical_values:
        additional = len(value) + (1 if displayed else 0)
        if used + additional > available:
            break
        displayed.append(value)
        used += additional
    omitted = ",..." if len(displayed) < len(canonical_values) else ""
    return f"{prefix}{','.join(displayed)}{omitted}{suffix}"


def _evidence(location: str, expected: str, observed: str) -> FindingEvidence:
    return FindingEvidence(location=location, expected=expected, observed=observed)


def _finding(
    *,
    validator_id: str,
    validator_version: str,
    code: FindingCode,
    status: FindingStatus,
    message: str,
    evidence: tuple[FindingEvidence, ...],
) -> ValidationFinding:
    return ValidationFinding(
        validator_id=validator_id,
        validator_version=validator_version,
        code=code,
        status=status,
        message=message,
        evidence=evidence,
    )


def _bounded_issues(issues: list[FindingEvidence]) -> tuple[FindingEvidence, ...]:
    if len(issues) <= _MAX_EVIDENCE_ISSUES:
        return tuple(issues)
    retained = issues[: _MAX_EVIDENCE_ISSUES - 1]
    retained.append(
        _evidence(
            "$",
            f"at most {_MAX_EVIDENCE_ISSUES - 1} detailed evidence entries",
            f"{len(issues)} issues found; additional issues omitted deterministically",
        )
    )
    return tuple(retained)


class SchemaCompletenessValidator:
    """Check the raw candidate's complete v1 JSON shape without parsing it eagerly."""

    validator_id: ClassVar[str] = "schema-completeness"
    validator_version: ClassVar[str] = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        candidate = request.candidate
        issues: list[FindingEvidence] = []

        required_fields = (
            "schema_version",
            "question_type",
            "stem",
            "options",
            "answer",
            "marking",
            "context_references",
        )
        issues.extend(
            _evidence(f"$.{field_name}", "required field present", "missing")
            for field_name in required_fields
            if field_name not in candidate
        )

        schema_version = candidate.get("schema_version")
        if schema_version != request.blueprint.schema_version:
            issues.append(
                _evidence(
                    "$.schema_version",
                    f"exact schema version {request.blueprint.schema_version!r}",
                    _describe(schema_version),
                )
            )
        if not _is_non_blank_text(candidate.get("question_type"), maximum=64):
            issues.append(
                _evidence(
                    "$.question_type",
                    "bounded non-blank question type",
                    _describe(candidate.get("question_type")),
                )
            )
        if not _is_non_blank_text(candidate.get("stem")):
            issues.append(
                _evidence(
                    "$.stem",
                    "bounded non-blank question text",
                    _describe(candidate.get("stem")),
                )
            )

        options = candidate.get("options")
        if not isinstance(options, tuple):
            issues.append(_evidence("$.options", "JSON array", _describe(options)))
        else:
            if len(options) > _MAX_SCHEMA_OPTIONS:
                issues.append(
                    _evidence(
                        "$.options",
                        f"at most {_MAX_SCHEMA_OPTIONS} options",
                        f"array(length={len(options)})",
                    )
                )
            for index, option in enumerate(options[:_MAX_SCHEMA_OPTIONS]):
                if not isinstance(option, Mapping):
                    issues.append(
                        _evidence(
                            f"$.options[{index}]",
                            "option object",
                            _describe(option),
                        )
                    )
                    continue
                for key in ("option_id", "text"):
                    value = option.get(key)
                    if not _is_non_blank_text(value, maximum=2_000):
                        issues.append(
                            _evidence(
                                f"$.options[{index}].{key}",
                                "bounded non-blank text",
                                _describe(value),
                            )
                        )

        answer = candidate.get("answer")
        if not isinstance(answer, Mapping):
            issues.append(_evidence("$.answer", "answer object", _describe(answer)))
        else:
            explanation = answer.get("explanation")
            if not _is_non_blank_text(explanation):
                issues.append(
                    _evidence(
                        "$.answer.explanation",
                        "bounded non-blank explanation",
                        _describe(explanation),
                    )
                )
            correct_option = answer.get("correct_option_id")
            accepted = answer.get("accepted_responses")
            has_option_answer = _is_non_blank_text(correct_option, maximum=128)
            has_constructed_answer = isinstance(accepted, tuple) and bool(accepted)
            if not has_option_answer and not has_constructed_answer:
                issues.append(
                    _evidence(
                        "$.answer",
                        "a declared option answer or non-empty accepted responses",
                        "no usable answer mode",
                    )
                )
            if accepted is not None and not isinstance(accepted, tuple):
                issues.append(
                    _evidence(
                        "$.answer.accepted_responses",
                        "JSON array when supplied",
                        _describe(accepted),
                    )
                )
            elif isinstance(accepted, tuple):
                if len(accepted) > _MAX_SCHEMA_CRITERIA:
                    issues.append(
                        _evidence(
                            "$.answer.accepted_responses",
                            f"at most {_MAX_SCHEMA_CRITERIA} accepted responses",
                            f"array(length={len(accepted)})",
                        )
                    )
                for index, response in enumerate(accepted[:_MAX_SCHEMA_CRITERIA]):
                    if not _is_non_blank_text(response, maximum=2_000):
                        issues.append(
                            _evidence(
                                f"$.answer.accepted_responses[{index}]",
                                "bounded non-blank response",
                                _describe(response),
                            )
                        )

        marking = candidate.get("marking")
        if not isinstance(marking, Mapping):
            issues.append(_evidence("$.marking", "marking object", _describe(marking)))
        else:
            total_marks = marking.get("total_marks")
            if not _is_integer(total_marks):
                issues.append(
                    _evidence(
                        "$.marking.total_marks",
                        "integer between 1 and 100",
                        _describe(total_marks),
                    )
                )
            criteria = marking.get("criteria")
            if not isinstance(criteria, tuple) or not criteria:
                issues.append(
                    _evidence(
                        "$.marking.criteria",
                        "non-empty JSON array",
                        _describe(criteria),
                    )
                )
            else:
                if len(criteria) > _MAX_SCHEMA_CRITERIA:
                    issues.append(
                        _evidence(
                            "$.marking.criteria",
                            f"at most {_MAX_SCHEMA_CRITERIA} criteria",
                            f"array(length={len(criteria)})",
                        )
                    )
                for index, criterion in enumerate(criteria[:_MAX_SCHEMA_CRITERIA]):
                    if not isinstance(criterion, Mapping):
                        issues.append(
                            _evidence(
                                f"$.marking.criteria[{index}]",
                                "criterion object",
                                _describe(criterion),
                            )
                        )
                        continue
                    for key in ("criterion_id", "description"):
                        value = criterion.get(key)
                        if not _is_non_blank_text(value):
                            issues.append(
                                _evidence(
                                    f"$.marking.criteria[{index}].{key}",
                                    "bounded non-blank text",
                                    _describe(value),
                                )
                            )
                    marks = criterion.get("marks")
                    if not _is_integer(marks):
                        issues.append(
                            _evidence(
                                f"$.marking.criteria[{index}].marks",
                                "integer between 1 and 100",
                                _describe(marks),
                            )
                        )

        references = candidate.get("context_references")
        if not isinstance(references, tuple):
            issues.append(_evidence("$.context_references", "JSON array", _describe(references)))
        else:
            if len(references) > _MAX_SCHEMA_REFERENCES:
                issues.append(
                    _evidence(
                        "$.context_references",
                        f"at most {_MAX_SCHEMA_REFERENCES} references",
                        f"array(length={len(references)})",
                    )
                )
            for index, reference in enumerate(references[:_MAX_SCHEMA_REFERENCES]):
                if not _is_non_blank_text(reference, maximum=128):
                    issues.append(
                        _evidence(
                            f"$.context_references[{index}]",
                            "bounded non-blank context identifier",
                            _describe(reference),
                        )
                    )

        if issues:
            finding = _finding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code=FindingCode.SCHEMA_COMPLETENESS,
                status=FindingStatus.FAIL,
                message="The structured candidate is incomplete or malformed.",
                evidence=_bounded_issues(issues),
            )
        else:
            finding = _finding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code=FindingCode.SCHEMA_COMPLETENESS,
                status=FindingStatus.PASS,
                message="All required v1 structured fields are present and bounded.",
                evidence=(
                    _evidence(
                        "$",
                        "complete bounded structured candidate",
                        f"candidate_sha256={request.candidate_fingerprint}",
                    ),
                ),
            )
        return (finding,)


class BlueprintComplianceValidator:
    """Enforce deterministic slot type, marks, option, and answer-mode rules."""

    validator_id: ClassVar[str] = "blueprint-compliance"
    validator_version: ClassVar[str] = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        candidate = request.candidate
        blueprint = request.blueprint

        actual_type = candidate.get("question_type")
        type_status = (
            FindingStatus.PASS if actual_type == blueprint.question_type else FindingStatus.FAIL
        )
        type_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.BLUEPRINT_QUESTION_TYPE,
            status=type_status,
            message=(
                "Question type matches the blueprint slot."
                if type_status is FindingStatus.PASS
                else "Question type does not match the blueprint slot."
            ),
            evidence=(
                _evidence(
                    "$.question_type",
                    blueprint.question_type,
                    _describe(actual_type),
                ),
            ),
        )

        marks_issues: list[FindingEvidence] = []
        marking = candidate.get("marking")
        total_marks: object = None
        criteria: object = None
        if isinstance(marking, Mapping):
            total_marks = marking.get("total_marks")
            criteria = marking.get("criteria")
        if total_marks != blueprint.marks or isinstance(total_marks, bool):
            marks_issues.append(
                _evidence(
                    "$.marking.total_marks",
                    f"exactly {blueprint.marks}",
                    _describe(total_marks),
                )
            )
        if not isinstance(criteria, tuple) or not criteria:
            marks_issues.append(
                _evidence(
                    "$.marking.criteria",
                    f"criteria whose marks sum to {blueprint.marks}",
                    _describe(criteria),
                )
            )
        else:
            criterion_marks = tuple(
                item.get("marks") if isinstance(item, Mapping) else None for item in criteria
            )
            valid_criterion_marks = tuple(
                value
                for value in criterion_marks
                if isinstance(value, int) and not isinstance(value, bool)
            )
            if len(valid_criterion_marks) != len(criterion_marks) or not all(
                _is_integer(value) for value in valid_criterion_marks
            ):
                marks_issues.append(
                    _evidence(
                        "$.marking.criteria[*].marks",
                        "positive integer marks",
                        _describe(criterion_marks),
                    )
                )
            else:
                criterion_mark_total = sum(valid_criterion_marks)
                if criterion_mark_total != blueprint.marks:
                    marks_issues.append(
                        _evidence(
                            "$.marking.criteria[*].marks",
                            f"sum exactly {blueprint.marks}",
                            f"sum={criterion_mark_total}",
                        )
                    )
        marks_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.BLUEPRINT_MARKS,
            status=FindingStatus.FAIL if marks_issues else FindingStatus.PASS,
            message=(
                "Declared marks and marking criteria match the blueprint slot."
                if not marks_issues
                else "Declared marks or marking criteria do not match the blueprint slot."
            ),
            evidence=(
                _bounded_issues(marks_issues)
                if marks_issues
                else (
                    _evidence(
                        "$.marking",
                        f"total and criteria sum of {blueprint.marks}",
                        f"total={total_marks}; criteria_sum={blueprint.marks}",
                    ),
                )
            ),
        )

        option_issues: list[FindingEvidence] = []
        options = candidate.get("options")
        option_count = len(options) if isinstance(options, tuple) else 0
        option_ids: list[str] = []
        option_texts: list[str] = []
        if not isinstance(options, tuple):
            option_issues.append(_evidence("$.options", "JSON array", _describe(options)))
        else:
            if blueprint.question_type == "multiple_choice":
                if not blueprint.minimum_options <= len(options) <= blueprint.maximum_options:
                    option_issues.append(
                        _evidence(
                            "$.options",
                            (
                                f"between {blueprint.minimum_options} and "
                                f"{blueprint.maximum_options} options"
                            ),
                            f"count={len(options)}",
                        )
                    )
            elif options:
                option_issues.append(
                    _evidence(
                        "$.options",
                        "no options for a constructed response",
                        f"count={len(options)}",
                    )
                )
            for index, option in enumerate(options[:_MAX_SCHEMA_OPTIONS]):
                if not isinstance(option, Mapping):
                    option_issues.append(
                        _evidence(f"$.options[{index}]", "option object", _describe(option))
                    )
                    continue
                option_id = option.get("option_id")
                text = option.get("text")
                if _is_non_blank_text(option_id, maximum=128):
                    option_ids.append(str(option_id))
                else:
                    option_issues.append(
                        _evidence(
                            f"$.options[{index}].option_id",
                            "bounded non-blank unique identifier",
                            _describe(option_id),
                        )
                    )
                if _is_non_blank_text(text, maximum=2_000):
                    option_texts.append(canonicalize_text(str(text)))
                else:
                    option_issues.append(
                        _evidence(
                            f"$.options[{index}].text",
                            "bounded non-blank unique text",
                            _describe(text),
                        )
                    )
            duplicate_ids = sorted(
                value for value, count in Counter(option_ids).items() if count > 1
            )
            duplicate_texts = sum(
                count - 1 for count in Counter(option_texts).values() if count > 1
            )
            if duplicate_ids:
                option_issues.append(
                    _evidence(
                        "$.options[*].option_id",
                        "unique option identifiers",
                        _summarize_identifiers(
                            duplicate_ids,
                            count_label="duplicate_count",
                            identifiers_label="option_ids",
                        ),
                    )
                )
            if duplicate_texts:
                option_issues.append(
                    _evidence(
                        "$.options[*].text",
                        "unique canonical option text",
                        f"duplicate_count={duplicate_texts}",
                    )
                )
        options_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.BLUEPRINT_OPTIONS,
            status=FindingStatus.FAIL if option_issues else FindingStatus.PASS,
            message=(
                "Option count, identity, and text follow the deterministic type rules."
                if not option_issues
                else "Options violate deterministic count, identity, or type rules."
            ),
            evidence=(
                _bounded_issues(option_issues)
                if option_issues
                else (
                    _evidence(
                        "$.options",
                        "option rules for the blueprint question type",
                        f"question_type={blueprint.question_type}; count={option_count}",
                    ),
                )
            ),
        )

        answer_issues: list[FindingEvidence] = []
        answer = candidate.get("answer")
        if not isinstance(answer, Mapping):
            answer_issues.append(_evidence("$.answer", "answer object", _describe(answer)))
        elif blueprint.question_type == "multiple_choice":
            correct_option = answer.get("correct_option_id")
            accepted = answer.get("accepted_responses", ())
            if not _is_non_blank_text(correct_option, maximum=128):
                answer_issues.append(
                    _evidence(
                        "$.answer.correct_option_id",
                        "exactly one non-blank option identifier",
                        _describe(correct_option),
                    )
                )
            elif str(correct_option) not in option_ids:
                answer_issues.append(
                    _evidence(
                        "$.answer.correct_option_id",
                        "identifier of exactly one supplied option",
                        _describe(correct_option),
                    )
                )
            if isinstance(accepted, tuple) and accepted:
                answer_issues.append(
                    _evidence(
                        "$.answer.accepted_responses",
                        "empty for multiple-choice mode",
                        f"count={len(accepted)}",
                    )
                )
            elif not isinstance(accepted, tuple):
                answer_issues.append(
                    _evidence(
                        "$.answer.accepted_responses",
                        "JSON array when supplied",
                        _describe(accepted),
                    )
                )
            if "correct_option_ids" in answer:
                answer_issues.append(
                    _evidence(
                        "$.answer.correct_option_ids",
                        "absent; the singular answer field is authoritative",
                        _describe(answer.get("correct_option_ids")),
                    )
                )
            if isinstance(options, tuple) and any(
                isinstance(option, Mapping) and "is_correct" in option for option in options
            ):
                answer_issues.append(
                    _evidence(
                        "$.options[*].is_correct",
                        "absent; the singular answer field is authoritative",
                        "secondary correctness flags present",
                    )
                )
        else:
            correct_option = answer.get("correct_option_id")
            accepted = answer.get("accepted_responses")
            if correct_option is not None:
                answer_issues.append(
                    _evidence(
                        "$.answer.correct_option_id",
                        "null or absent for constructed response",
                        _describe(correct_option),
                    )
                )
            if (
                not isinstance(accepted, tuple)
                or not accepted
                or any(not _is_non_blank_text(item, maximum=2_000) for item in accepted)
            ):
                answer_issues.append(
                    _evidence(
                        "$.answer.accepted_responses",
                        "non-empty bounded response array",
                        _describe(accepted),
                    )
                )
            elif len({canonicalize_text(str(item)) for item in accepted}) != len(accepted):
                answer_issues.append(
                    _evidence(
                        "$.answer.accepted_responses",
                        "unique canonical responses",
                        f"count={len(accepted)} with duplicates",
                    )
                )
            if "correct_option_ids" in answer:
                answer_issues.append(
                    _evidence(
                        "$.answer.correct_option_ids",
                        "absent for constructed response",
                        _describe(answer.get("correct_option_ids")),
                    )
                )
        answer_mode_observed = "single supplied option identifier"
        if blueprint.question_type != "multiple_choice":
            accepted_responses = (
                answer.get("accepted_responses", ()) if isinstance(answer, Mapping) else ()
            )
            accepted_response_count = (
                len(accepted_responses) if isinstance(accepted_responses, tuple) else 0
            )
            answer_mode_observed = f"accepted_response_count={accepted_response_count}"
        answer_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.BLUEPRINT_EXACTLY_ONE_ANSWER,
            status=FindingStatus.FAIL if answer_issues else FindingStatus.PASS,
            message=(
                "The candidate uses exactly one answer mode required by its type."
                if not answer_issues
                else "The candidate has a missing, conflicting, or invalid answer mode."
            ),
            evidence=(
                _bounded_issues(answer_issues)
                if answer_issues
                else (
                    _evidence(
                        "$.answer",
                        "one type-appropriate answer mode",
                        answer_mode_observed,
                    ),
                )
            ),
        )
        return (type_finding, marks_finding, options_finding, answer_finding)


class GroundingValidator:
    """Check context references and provenance identities without judging semantics."""

    validator_id: ClassVar[str] = "context-grounding"
    validator_version: ClassVar[str] = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        raw_references = request.candidate.get("context_references")
        reference_issues: list[FindingEvidence] = []
        references: tuple[str, ...] = ()
        if not isinstance(raw_references, tuple) or not raw_references:
            reference_issues.append(
                _evidence(
                    "$.context_references",
                    "non-empty JSON array of context identifiers",
                    _describe(raw_references),
                )
            )
        elif any(not _is_non_blank_text(item, maximum=128) for item in raw_references):
            for index, item in enumerate(raw_references):
                if not _is_non_blank_text(item, maximum=128):
                    reference_issues.append(
                        _evidence(
                            f"$.context_references[{index}]",
                            "bounded non-blank context identifier",
                            _describe(item),
                        )
                    )
        else:
            references = tuple(str(item) for item in raw_references)
            reference_counts = Counter(references)
            duplicate_references = sorted(
                item for item, count in reference_counts.items() if count > 1
            )
            if duplicate_references:
                reference_issues.append(
                    _evidence(
                        "$.context_references",
                        "unique context identifiers",
                        _summarize_identifiers(
                            duplicate_references,
                            count_label="duplicate_count",
                            identifiers_label="context_ids",
                        ),
                    )
                )
                for duplicate in duplicate_references:
                    first_index = references.index(duplicate)
                    reference_issues.append(
                        _evidence(
                            f"$.context_references[{first_index}]",
                            "identifier appears exactly once",
                            f"{duplicate!r} appears {reference_counts[duplicate]} times",
                        )
                    )

        source_counts = Counter(item.context_id for item in request.grounding_sources)
        duplicate_sources = sorted(item for item, count in source_counts.items() if count > 1)
        if duplicate_sources:
            reference_issues.append(
                _evidence(
                    "grounding_sources",
                    "unique context identities",
                    _summarize_identifiers(
                        duplicate_sources,
                        count_label="duplicate_count",
                        identifiers_label="context_ids",
                    ),
                )
            )
        known_ids = set(source_counts)
        for index, reference in enumerate(references):
            if reference not in known_ids:
                reference_issues.append(
                    _evidence(
                        f"$.context_references[{index}]",
                        "identifier present in supplied grounding sources",
                        f"unknown={reference!r}",
                    )
                )

        references_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.GROUNDING_REFERENCES,
            status=FindingStatus.FAIL if reference_issues else FindingStatus.PASS,
            message=(
                "Context references are unique and resolve by identifier."
                if not reference_issues
                else "Context references are missing, duplicated, or unresolved."
            ),
            evidence=(
                _bounded_issues(reference_issues)
                if reference_issues
                else (
                    _evidence(
                        "$.context_references",
                        "unique identifiers resolving to supplied contexts",
                        f"resolved_count={len(references)}",
                    ),
                )
            ),
        )

        provenance_issues: list[FindingEvidence] = []
        if not references:
            provenance_issues.append(
                _evidence(
                    "$.context_references",
                    "usable references before provenance can be verified",
                    "no usable references",
                )
            )
        source_by_id: dict[str, list[GroundingSource]] = {}
        for grounding_source in request.grounding_sources:
            source_by_id.setdefault(grounding_source.context_id, []).append(grounding_source)
        for reference in sorted(set(references)):
            matches = source_by_id.get(reference, [])
            if len(matches) != 1:
                provenance_issues.append(
                    _evidence(
                        f"grounding_sources[{reference}]",
                        "exactly one source carrying complete provenance",
                        f"match_count={len(matches)}",
                    )
                )
                continue
            grounding_source = matches[0]
            provenance_fields = (
                ("source_document_id", grounding_source.source_document_id),
                ("source_version", grounding_source.source_version),
                ("chunk_id", grounding_source.chunk_id),
            )
            for field_name, value in provenance_fields:
                if not _is_non_blank_text(value, maximum=256):
                    provenance_issues.append(
                        _evidence(
                            f"grounding_sources[{reference}].{field_name}",
                            "bounded non-blank immutable source identity",
                            _describe(value),
                        )
                    )
            page_number = grounding_source.page_number
            if not _is_integer(page_number, minimum=1, maximum=1_000_000):
                provenance_issues.append(
                    _evidence(
                        f"grounding_sources[{reference}].page_number",
                        "positive integer page number",
                        _describe(page_number),
                    )
                )

        provenance_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.GROUNDING_PROVENANCE,
            status=FindingStatus.FAIL if provenance_issues else FindingStatus.PASS,
            message=(
                "Referenced contexts carry complete identifier-level provenance; this does not "
                "establish semantic or factual support."
                if not provenance_issues
                else "One or more referenced contexts lack complete identifier-level provenance."
            ),
            evidence=(
                _bounded_issues(provenance_issues)
                if provenance_issues
                else (
                    _evidence(
                        "grounding_sources",
                        "document, version, page, and chunk identity per reference",
                        f"verified_reference_count={len(set(references))}",
                    ),
                )
            ),
        )
        return references_finding, provenance_finding


def _authored_texts(candidate: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    texts: list[tuple[str, str]] = []
    stem = candidate.get("stem")
    if isinstance(stem, str):
        texts.append(("$.stem", stem))
    options = candidate.get("options")
    if isinstance(options, tuple):
        for index, option in enumerate(options[:_MAX_SCHEMA_OPTIONS]):
            if isinstance(option, Mapping) and isinstance(option.get("text"), str):
                texts.append((f"$.options[{index}].text", str(option["text"])))
    answer = candidate.get("answer")
    if isinstance(answer, Mapping):
        explanation = answer.get("explanation")
        if isinstance(explanation, str):
            texts.append(("$.answer.explanation", explanation))
        responses = answer.get("accepted_responses")
        if isinstance(responses, tuple):
            for index, response in enumerate(responses[:_MAX_SCHEMA_CRITERIA]):
                if isinstance(response, str):
                    texts.append((f"$.answer.accepted_responses[{index}]", response))
    marking = candidate.get("marking")
    if isinstance(marking, Mapping):
        criteria = marking.get("criteria")
        if isinstance(criteria, tuple):
            for index, criterion in enumerate(criteria[:_MAX_SCHEMA_CRITERIA]):
                if isinstance(criterion, Mapping) and isinstance(criterion.get("description"), str):
                    texts.append(
                        (f"$.marking.criteria[{index}].description", str(criterion["description"]))
                    )
    return tuple(texts)


def _student_facing_texts(candidate: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(
        item
        for item in _authored_texts(candidate)
        if item[0] == "$.stem" or item[0].startswith("$.options[")
    )


def _normalized_security_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_format_controls = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    words_only = "".join(
        character if character.isalnum() else " " for character in without_format_controls
    )
    return " ".join(words_only.split())


_PROMPT_PATTERNS = (
    (
        "role-tag",
        re.compile(r"<\s*/?\s*(?:system|developer|assistant)\b", re.IGNORECASE),
        False,
    ),
    (
        "instruction-override",
        re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b"),
        True,
    ),
    ("role-override", re.compile(r"\byou\s+are\s+now\b"), True),
    (
        "prompt-disclosure",
        re.compile(
            r"\b(?:reveal|show|print|repeat)\s+(?:the\s+)?(?:system|developer)\s+"
            r"(?:prompt|message|instructions?)\b"
        ),
        True,
    ),
    (
        "workflow-override",
        re.compile(r"\bapprove\s+and\s+publish\s+(?:this|the)\s+(?:question|content)\b"),
        True,
    ),
    ("instruction-delimiter", re.compile(r"(?:\[inst\]|<<\s*sys\s*>>)", re.IGNORECASE), False),
)


class PromptInjectionResidueValidator:
    """Reject fixed prompt-injection residue in authored output, never source data."""

    validator_id: ClassVar[str] = "prompt-injection-residue"
    validator_version: ClassVar[str] = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        authored_texts = _authored_texts(request.candidate)
        total_characters = sum(len(text) for _, text in authored_texts)
        issues: list[FindingEvidence] = []
        if total_characters > _MAX_AUTHORED_SCAN_CHARACTERS:
            issues.append(
                _evidence(
                    "$",
                    f"at most {_MAX_AUTHORED_SCAN_CHARACTERS} authored characters to scan",
                    f"authored_character_count={total_characters}",
                )
            )
        else:
            for location, text in authored_texts:
                normalized = _normalized_security_text(text)
                normalized_original = unicodedata.normalize("NFKC", text).casefold()
                text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                for pattern_id, pattern, use_words_only in _PROMPT_PATTERNS:
                    haystack = normalized if use_words_only else normalized_original
                    if pattern.search(haystack):
                        issues.append(
                            _evidence(
                                location,
                                f"no prohibited residue pattern {pattern_id!r}",
                                f"pattern={pattern_id}; sha256={text_digest}",
                            )
                        )
        finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.PROMPT_INJECTION_RESIDUE,
            status=FindingStatus.FAIL if issues else FindingStatus.PASS,
            message=(
                "Generated authored fields contain prohibited prompt-injection residue."
                if issues
                else "No fixed prohibited residue pattern was found in bounded authored fields; "
                "retrieved source text remained untrusted data and was not interpreted."
            ),
            evidence=(
                _bounded_issues(issues)
                if issues
                else (
                    _evidence(
                        "$",
                        "bounded authored fields contain no fixed prohibited pattern",
                        (
                            f"scanned_fields={len(authored_texts)}; "
                            f"scanned_characters={total_characters}"
                        ),
                    ),
                )
            ),
        )
        return (finding,)


@dataclass(frozen=True, slots=True)
class HeuristicPolicy:
    """Explicitly versioned, bounded indicators for deterministic review triage."""

    policy_version: str = "grade5-bounded-indicators.v1"
    maximum_student_characters: int = 16_000
    maximum_words: int = 2_000
    primary_max_age: int = 12
    maximum_sentence_words: int = 30
    maximum_word_characters: int = 24
    minimum_expected_script_basis_points: int = 7_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version
            or self.policy_version != self.policy_version.strip()
            or any(character.isspace() for character in self.policy_version)
            or len(self.policy_version) > 128
        ):
            raise ValidationContractError("policy_version must be a bounded machine value")
        for field_name, value, maximum in (
            ("maximum_student_characters", self.maximum_student_characters, 100_000),
            ("maximum_words", self.maximum_words, 20_000),
            ("primary_max_age", self.primary_max_age, 18),
            ("maximum_sentence_words", self.maximum_sentence_words, 1_000),
            ("maximum_word_characters", self.maximum_word_characters, 1_000),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValidationContractError(f"{field_name} must be between 1 and {maximum}")
        if (
            not isinstance(self.minimum_expected_script_basis_points, int)
            or isinstance(self.minimum_expected_script_basis_points, bool)
            or not 0 <= self.minimum_expected_script_basis_points <= 10_000
        ):
            raise ValidationContractError(
                "minimum_expected_script_basis_points must be between 0 and 10000"
            )


_WORD_PATTERN = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*", re.UNICODE)
_SENTENCE_BOUNDARY = re.compile(r"[.!?…।]+")


def _script_for(character: str) -> str:
    codepoint = ord(character)
    if 0x0D80 <= codepoint <= 0x0DFF:
        return "sinhala"
    if 0x0B80 <= codepoint <= 0x0BFF:
        return "tamil"
    if unicodedata.name(character, "").startswith("LATIN "):
        return "latin"
    return "other"


class AgeLanguageHeuristicsValidator:
    """Run transparent proxies that can warn, but cannot certify educational quality."""

    validator_id: ClassVar[str] = "age-language-heuristics"
    base_validator_version: ClassVar[str] = "1.0.0"

    def __init__(self, *, policy: HeuristicPolicy | None = None) -> None:
        self.policy = policy or HeuristicPolicy()
        if not isinstance(self.policy, HeuristicPolicy):
            raise ValidationContractError("policy must be HeuristicPolicy")

    @property
    def validator_version(self) -> str:
        return f"{self.base_validator_version}+{self.policy.policy_version}"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        student_texts = _student_facing_texts(request.candidate)
        combined = " ".join(text for _, text in student_texts)
        character_count = sum(len(text) for _, text in student_texts)
        words = _WORD_PATTERN.findall(combined)

        hard_bound_issues: list[FindingEvidence] = []
        if character_count > self.policy.maximum_student_characters:
            hard_bound_issues.append(
                _evidence(
                    "$.stem,$.options[*].text",
                    f"at most {self.policy.maximum_student_characters} student-facing characters",
                    f"character_count={character_count}",
                )
            )
        if len(words) > self.policy.maximum_words:
            hard_bound_issues.append(
                _evidence(
                    "$.stem,$.options[*].text",
                    f"at most {self.policy.maximum_words} words",
                    f"word_count={len(words)}",
                )
            )

        if hard_bound_issues:
            age_status = FindingStatus.FAIL
            age_message = "Student-facing text exceeds deterministic heuristic scan bounds."
            age_evidence = tuple(hard_bound_issues)
        else:
            sentence_word_counts = tuple(
                len(_WORD_PATTERN.findall(sentence))
                for sentence in _SENTENCE_BOUNDARY.split(combined)
                if sentence.strip()
            )
            maximum_sentence_words = max(sentence_word_counts, default=0)
            maximum_word_characters = max((len(word) for word in words), default=0)
            indicators: list[FindingEvidence] = []
            if request.blueprint.maximum_age <= self.policy.primary_max_age:
                if maximum_sentence_words > self.policy.maximum_sentence_words:
                    indicators.append(
                        _evidence(
                            "$.stem,$.options[*].text",
                            (f"sentence proxy at most {self.policy.maximum_sentence_words} words"),
                            f"maximum_sentence_words={maximum_sentence_words}",
                        )
                    )
                if maximum_word_characters > self.policy.maximum_word_characters:
                    indicators.append(
                        _evidence(
                            "$.stem,$.options[*].text",
                            (
                                f"word-length proxy at most "
                                f"{self.policy.maximum_word_characters} characters"
                            ),
                            f"maximum_word_characters={maximum_word_characters}",
                        )
                    )
            age_status = FindingStatus.WARN if indicators else FindingStatus.PASS
            age_message = (
                "One or more bounded readability indicators need human review; this does not "
                "establish age appropriateness."
                if indicators
                else "Bounded readability indicators are within configured thresholds; this "
                "does not establish age appropriateness."
            )
            age_evidence = (
                tuple(indicators)
                if indicators
                else (
                    _evidence(
                        "$.stem,$.options[*].text",
                        "bounded text and configured readability indicators",
                        (
                            f"characters={character_count}; words={len(words)}; "
                            f"maximum_sentence_words={maximum_sentence_words}; "
                            f"maximum_word_characters={maximum_word_characters}"
                        ),
                    ),
                )
            )
        age_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.AGE_HEURISTIC,
            status=age_status,
            message=age_message,
            evidence=age_evidence,
        )

        if hard_bound_issues:
            language_status = FindingStatus.FAIL
            language_message = "Student-facing text exceeds deterministic language scan bounds."
            language_evidence = tuple(hard_bound_issues)
        else:
            language_prefix = request.blueprint.language.casefold().replace("_", "-").split("-")[0]
            expected_script = {"en": "latin", "si": "sinhala", "ta": "tamil"}.get(language_prefix)
            letters = tuple(character for character in combined if character.isalpha())
            if expected_script is None:
                language_status = FindingStatus.WARN
                language_message = (
                    "No script heuristic is configured for the declared language; language "
                    "quality and fluency require human review."
                )
                language_evidence = (
                    _evidence(
                        "blueprint.language",
                        "one of the configured en, si, or ta language prefixes",
                        request.blueprint.language,
                    ),
                )
            elif not letters:
                language_status = FindingStatus.WARN
                language_message = (
                    "No alphabetic script evidence is available; language quality and fluency "
                    "require human review."
                )
                language_evidence = (
                    _evidence(
                        "$.stem,$.options[*].text",
                        f"alphabetic evidence in the expected {expected_script} script",
                        "letter_count=0",
                    ),
                )
            else:
                expected_count = sum(
                    _script_for(character) == expected_script for character in letters
                )
                basis_points = (expected_count * 10_000) // len(letters)
                language_status = (
                    FindingStatus.PASS
                    if basis_points >= self.policy.minimum_expected_script_basis_points
                    else FindingStatus.WARN
                )
                language_message = (
                    "Expected-script proportion meets the configured indicator; this does not "
                    "establish language quality or fluency."
                    if language_status is FindingStatus.PASS
                    else "Expected-script proportion is below the configured indicator; script "
                    "alone cannot establish language quality or fluency."
                )
                language_evidence = (
                    _evidence(
                        "$.stem,$.options[*].text",
                        (
                            f"at least {self.policy.minimum_expected_script_basis_points} basis "
                            f"points in {expected_script} script"
                        ),
                        (
                            f"expected_script_basis_points={basis_points}; "
                            f"letter_count={len(letters)}"
                        ),
                    ),
                )
        language_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.LANGUAGE_HEURISTIC,
            status=language_status,
            message=language_message,
            evidence=language_evidence,
        )
        return age_finding, language_finding


@dataclass(frozen=True, slots=True)
class LexicalSimilarityPolicy:
    """Versioned bounds for a conservative Unicode character n-gram indicator."""

    policy_version: str = "unicode-char-trigram-dice.v1"
    ngram_size: int = 3
    warning_threshold_basis_points: int = 8_000
    maximum_text_characters: int = MAX_DUPLICATE_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version
            or self.policy_version != self.policy_version.strip()
            or any(character.isspace() for character in self.policy_version)
            or len(self.policy_version) > 128
        ):
            raise ValidationContractError("policy_version must be a bounded machine value")
        if (
            not isinstance(self.ngram_size, int)
            or isinstance(self.ngram_size, bool)
            or not 1 <= self.ngram_size <= 8
        ):
            raise ValidationContractError("ngram_size must be between 1 and 8")
        if (
            not isinstance(self.warning_threshold_basis_points, int)
            or isinstance(self.warning_threshold_basis_points, bool)
            or not 1 <= self.warning_threshold_basis_points <= 10_000
        ):
            raise ValidationContractError(
                "warning_threshold_basis_points must be between 1 and 10000"
            )
        if (
            not isinstance(self.maximum_text_characters, int)
            or isinstance(self.maximum_text_characters, bool)
            or not 1 <= self.maximum_text_characters <= MAX_DUPLICATE_TEXT_CHARACTERS
        ):
            raise ValidationContractError(
                f"maximum_text_characters must be between 1 and {MAX_DUPLICATE_TEXT_CHARACTERS}"
            )


def _lexical_normalize(value: str, *, maximum_characters: int) -> str:
    """Normalize scripts uniformly while retaining letters, marks, and numbers only."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    pending_separator = False
    for character in normalized:
        if unicodedata.category(character)[0] in {"L", "M", "N"}:
            required_characters = 2 if pending_separator and characters else 1
            if len(characters) + required_characters > maximum_characters:
                break
            if pending_separator and characters:
                characters.append(" ")
            characters.append(character)
            pending_separator = False
        elif characters:
            pending_separator = True
        if len(characters) >= maximum_characters:
            break
    return "".join(characters).strip()


def _character_ngram_counts(value: str, *, ngram_size: int) -> Counter[str]:
    if not value:
        return Counter()
    if len(value) < ngram_size:
        return Counter((value,))
    return Counter(
        value[index : index + ngram_size] for index in range(len(value) - ngram_size + 1)
    )


def _dice_basis_points(candidate: Counter[str], reference: Counter[str]) -> int:
    candidate_total = candidate.total()
    reference_total = reference.total()
    denominator = candidate_total + reference_total
    if denominator == 0:
        return 0
    intersection = sum(min(count, candidate.get(ngram, 0)) for ngram, count in reference.items())
    return (2 * intersection * 10_000) // denominator


class LexicalSimilarityIndicatorValidator:
    """Flag high bounded lexical overlap; this is not semantic paraphrase detection."""

    validator_id: ClassVar[str] = "lexical-similarity-indicator"
    base_validator_version: ClassVar[str] = "1.0.0"

    def __init__(self, *, policy: LexicalSimilarityPolicy | None = None) -> None:
        self.policy = policy or LexicalSimilarityPolicy()
        if not isinstance(self.policy, LexicalSimilarityPolicy):
            raise ValidationContractError("policy must be LexicalSimilarityPolicy")

    @property
    def validator_version(self) -> str:
        return f"{self.base_validator_version}+{self.policy.policy_version}"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        stem = request.candidate.get("stem")
        if not isinstance(stem, str) or not stem.strip():
            observed_count = len(stem) if isinstance(stem, str) else 0
            return (
                _finding(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    code=FindingCode.DUPLICATE_LEXICAL_SIMILARITY,
                    status=FindingStatus.FAIL,
                    message="Bounded lexical comparison cannot run without a usable stem.",
                    evidence=(
                        _evidence(
                            "$.stem",
                            f"maximum_text_characters={self.policy.maximum_text_characters}",
                            f"character_count={observed_count}",
                        ),
                    ),
                ),
            )
        if len(stem) > self.policy.maximum_text_characters:
            return (
                _finding(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    code=FindingCode.DUPLICATE_LEXICAL_SIMILARITY,
                    status=FindingStatus.FAIL,
                    message="Candidate stem exceeds the bounded lexical comparison limit.",
                    evidence=(
                        _evidence(
                            "$.stem",
                            f"maximum_text_characters={self.policy.maximum_text_characters}",
                            f"character_count={len(stem)}",
                        ),
                    ),
                ),
            )

        candidate_hash = canonical_text_sha256(stem)
        candidate_counts = _character_ngram_counts(
            _lexical_normalize(stem, maximum_characters=self.policy.maximum_text_characters),
            ngram_size=self.policy.ngram_size,
        )
        compared_count = 0
        best_id = "none"
        best_score = 0
        for reference in request.duplicate_references:
            if reference.text is None or reference.effective_sha256 == candidate_hash:
                continue
            reference_counts = _character_ngram_counts(
                _lexical_normalize(
                    reference.text,
                    maximum_characters=self.policy.maximum_text_characters,
                ),
                ngram_size=self.policy.ngram_size,
            )
            score = _dice_basis_points(candidate_counts, reference_counts)
            compared_count += 1
            if score > best_score or (score == best_score and reference.question_id < best_id):
                best_id = reference.question_id
                best_score = score

        threshold = self.policy.warning_threshold_basis_points
        status = FindingStatus.WARN if best_score >= threshold else FindingStatus.PASS
        return (
            _finding(
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                code=FindingCode.DUPLICATE_LEXICAL_SIMILARITY,
                status=status,
                message=(
                    "High bounded lexical overlap requires conservative human review; this is not "
                    "semantic paraphrase detection and can produce false positives and false "
                    "negatives."
                    if status is FindingStatus.WARN
                    else "No bounded lexical-overlap score crossed the review threshold; this is "
                    "not semantic paraphrase detection and can produce false negatives."
                ),
                evidence=(
                    _evidence(
                        f"duplicate_reference_id={best_id}",
                        f"threshold_basis_points={threshold}",
                        (
                            f"score_basis_points={best_score}; "
                            f"compared_reference_count={compared_count}"
                        ),
                    ),
                ),
            ),
        )


def _matched_ids_summary(question_ids: list[str]) -> str:
    return _summarize_identifiers(
        question_ids,
        count_label="match_count",
        identifiers_label="question_ids",
    )


class ExactHashDuplicateValidator:
    """Detect exact canonical text and SHA-256 equality, never paraphrase similarity."""

    validator_id: ClassVar[str] = "exact-hash-duplicate"
    validator_version: ClassVar[str] = "1.0.0"

    def validate(self, validation_input: ValidationInput) -> tuple[ValidationFinding, ...]:
        request = _require_input(validation_input)
        stem = request.candidate.get("stem")
        if not _is_non_blank_text(stem):
            unavailable = (
                _evidence(
                    "$.stem",
                    "bounded non-blank text for exact duplicate checks",
                    _describe(stem),
                ),
            )
            return (
                _finding(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    code=FindingCode.DUPLICATE_EXACT,
                    status=FindingStatus.FAIL,
                    message="Exact duplicate checking cannot run without a usable stem.",
                    evidence=unavailable,
                ),
                _finding(
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    code=FindingCode.DUPLICATE_SHA256,
                    status=FindingStatus.FAIL,
                    message="Canonical SHA-256 checking cannot run without a usable stem.",
                    evidence=unavailable,
                ),
            )

        candidate_text = canonicalize_text(str(stem))
        candidate_hash = canonical_text_sha256(str(stem))
        exact_ids = [
            item.question_id
            for item in request.duplicate_references
            if item.text is not None and canonicalize_text(item.text) == candidate_text
        ]
        hash_ids = [
            item.question_id
            for item in request.duplicate_references
            if item.effective_sha256 == candidate_hash
        ]

        exact_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.DUPLICATE_EXACT,
            status=FindingStatus.FAIL if exact_ids else FindingStatus.PASS,
            message=(
                "Candidate stem exactly matches canonical normalized bank text."
                if exact_ids
                else "No exact canonical normalized stem match was found; paraphrase similarity "
                "was not assessed."
            ),
            evidence=(
                (
                    _evidence(
                        "$.stem",
                        "no canonical normalized text equality",
                        _matched_ids_summary(exact_ids),
                    ),
                )
                if exact_ids
                else (
                    _evidence(
                        "$.stem",
                        "no canonical normalized text equality",
                        f"compared_reference_count={len(request.duplicate_references)}",
                    ),
                )
            ),
        )
        hash_finding = _finding(
            validator_id=self.validator_id,
            validator_version=self.validator_version,
            code=FindingCode.DUPLICATE_SHA256,
            status=FindingStatus.FAIL if hash_ids else FindingStatus.PASS,
            message=(
                "Candidate canonical SHA-256 matches one or more bank references; paraphrase "
                "similarity was not assessed."
                if hash_ids
                else "No canonical SHA-256 match was found; paraphrase similarity was not assessed."
            ),
            evidence=(
                (
                    _evidence(
                        "$.stem",
                        "canonical SHA-256 absent from the comparison bank",
                        (f"candidate_sha256={candidate_hash}; {_matched_ids_summary(hash_ids)}"),
                    ),
                )
                if hash_ids
                else (
                    _evidence(
                        "$.stem",
                        "canonical SHA-256 absent from the comparison bank",
                        (
                            f"candidate_sha256={candidate_hash}; "
                            f"compared_reference_count={len(request.duplicate_references)}"
                        ),
                    ),
                )
            ),
        )
        return exact_finding, hash_finding


ContextGroundingValidator = GroundingValidator
BlueprintValidator = BlueprintComplianceValidator
ExactDuplicateValidator = ExactHashDuplicateValidator
