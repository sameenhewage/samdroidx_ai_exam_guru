"""Immutable contracts for deterministic historical exam analytics."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class AnalyticsViolation(StrEnum):
    BLANK_VALUE = "blank_value"
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_PAGE = "invalid_page"
    INVALID_YEAR = "invalid_year"
    INVALID_MARKS = "invalid_marks"
    INVALID_WEIGHT = "invalid_weight"
    INVALID_CATEGORY = "invalid_category"
    DUPLICATE_OBSERVATION_ID = "duplicate_observation_id"
    DUPLICATE_QUESTION = "duplicate_question"
    DUPLICATE_SKILL = "duplicate_skill"
    MIXED_CURRICULUM = "mixed_curriculum"
    UNKNOWN_SKILL = "unknown_skill"
    COMPETENCY_MISMATCH = "competency_mismatch"
    EMPTY_SYLLABUS = "empty_syllabus"
    NO_OBSERVATIONS = "no_observations"
    HELDOUT_LEAKAGE = "heldout_leakage"


class AnalyticsContractError(ValueError):
    def __init__(self, violation: AnalyticsViolation, detail: str = "") -> None:
        self.violation = violation
        self.detail = detail
        message = violation.value if not detail else f"{violation.value}: {detail}"
        super().__init__(message)


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    STRUCTURED = "structured"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class PracticeRecommendation(StrEnum):
    EVIDENCE_BACKED_PRACTICE = "evidence_backed_practice"
    SYLLABUS_BALANCED_PRACTICE = "syllabus_balanced_practice"


@dataclass(frozen=True, slots=True)
class ObservationProvenance:
    """Immutable source location and version for one historical observation."""

    source_document_id: UUID
    source_version: str
    page_number: int
    source_block_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.source_document_id, "source_document_id")
        _require_clean_text(self.source_version, "source_version")
        if (
            not isinstance(self.page_number, int)
            or isinstance(self.page_number, bool)
            or self.page_number < 1
        ):
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_PAGE,
                "page_number must be a positive integer",
            )
        if self.source_block_id is not None:
            _require_uuid(self.source_block_id, "source_block_id")


@dataclass(frozen=True, slots=True)
class SourceVersion:
    source_document_id: UUID
    source_version: str


@dataclass(frozen=True, slots=True)
class HistoricalQuestionObservation:
    """Reviewed question attributes needed for analytics, without question content."""

    id: UUID
    curriculum_version_id: UUID
    year: int
    paper_code: str
    question_number: str
    competency_id: UUID
    skill_id: UUID
    question_type: QuestionType
    difficulty: Difficulty
    marks: int
    provenance: ObservationProvenance

    def __post_init__(self) -> None:
        for value, name in (
            (self.id, "id"),
            (self.curriculum_version_id, "curriculum_version_id"),
            (self.competency_id, "competency_id"),
            (self.skill_id, "skill_id"),
        ):
            _require_uuid(value, name)
        if (
            not isinstance(self.year, int)
            or isinstance(self.year, bool)
            or not 1900 <= self.year <= 2100
        ):
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_YEAR,
                "year must be between 1900 and 2100",
            )
        _require_clean_text(self.paper_code, "paper_code")
        _require_clean_text(self.question_number, "question_number")
        if not isinstance(self.question_type, QuestionType) or not isinstance(
            self.difficulty, Difficulty
        ):
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_CATEGORY,
                "question_type and difficulty must use analytics enums",
            )
        if not isinstance(self.marks, int) or isinstance(self.marks, bool) or self.marks < 1:
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_MARKS,
                "marks must be a positive integer",
            )
        if not isinstance(self.provenance, ObservationProvenance):
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_CATEGORY,
                "provenance must be an ObservationProvenance",
            )


@dataclass(frozen=True, slots=True)
class SyllabusSkill:
    """One in-scope syllabus skill and its declared balancing weight."""

    curriculum_version_id: UUID
    competency_id: UUID
    skill_id: UUID
    title: str
    balance_weight: int = 1

    def __post_init__(self) -> None:
        for value, name in (
            (self.curriculum_version_id, "curriculum_version_id"),
            (self.competency_id, "competency_id"),
            (self.skill_id, "skill_id"),
        ):
            _require_uuid(value, name)
        _require_clean_text(self.title, "title")
        if (
            not isinstance(self.balance_weight, int)
            or isinstance(self.balance_weight, bool)
            or self.balance_weight < 1
        ):
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_WEIGHT,
                "balance_weight must be a positive integer",
            )


def validate_observations(
    observations: Iterable[HistoricalQuestionObservation],
) -> tuple[HistoricalQuestionObservation, ...]:
    """Validate identity/curriculum invariants and return canonical ID order."""

    received = tuple(observations)
    if any(not isinstance(item, HistoricalQuestionObservation) for item in received):
        raise AnalyticsContractError(
            AnalyticsViolation.INVALID_CATEGORY,
            "all inputs must be HistoricalQuestionObservation values",
        )
    canonical = tuple(sorted(received, key=lambda item: item.id.int))
    seen_ids: set[UUID] = set()
    seen_questions: set[tuple[UUID, int, str, str]] = set()
    curriculum_ids: set[UUID] = set()

    for observation in canonical:
        if observation.id in seen_ids:
            raise AnalyticsContractError(
                AnalyticsViolation.DUPLICATE_OBSERVATION_ID,
                str(observation.id),
            )
        seen_ids.add(observation.id)
        question_key = (
            observation.curriculum_version_id,
            observation.year,
            observation.paper_code.casefold(),
            observation.question_number.casefold(),
        )
        if question_key in seen_questions:
            raise AnalyticsContractError(
                AnalyticsViolation.DUPLICATE_QUESTION,
                f"{observation.year}/{observation.paper_code}/{observation.question_number}",
            )
        seen_questions.add(question_key)
        curriculum_ids.add(observation.curriculum_version_id)

    if len(curriculum_ids) > 1:
        raise AnalyticsContractError(
            AnalyticsViolation.MIXED_CURRICULUM,
            "one analytics input cannot combine curriculum versions",
        )
    return canonical


def validate_syllabus(syllabus: Iterable[SyllabusSkill]) -> tuple[SyllabusSkill, ...]:
    received = tuple(syllabus)
    if any(not isinstance(item, SyllabusSkill) for item in received):
        raise AnalyticsContractError(
            AnalyticsViolation.INVALID_CATEGORY,
            "all syllabus inputs must be SyllabusSkill values",
        )
    canonical = tuple(
        sorted(received, key=lambda item: (item.competency_id.int, item.skill_id.int))
    )
    if not canonical:
        raise AnalyticsContractError(
            AnalyticsViolation.EMPTY_SYLLABUS,
            "at least one syllabus skill is required",
        )

    seen_skills: set[UUID] = set()
    curriculum_ids: set[UUID] = set()
    for skill in canonical:
        if skill.skill_id in seen_skills:
            raise AnalyticsContractError(
                AnalyticsViolation.DUPLICATE_SKILL,
                str(skill.skill_id),
            )
        seen_skills.add(skill.skill_id)
        curriculum_ids.add(skill.curriculum_version_id)

    if len(curriculum_ids) > 1:
        raise AnalyticsContractError(
            AnalyticsViolation.MIXED_CURRICULUM,
            "one syllabus cannot combine curriculum versions",
        )
    return canonical


def validate_analytics_inputs(
    observations: Iterable[HistoricalQuestionObservation],
    syllabus: Iterable[SyllabusSkill],
) -> tuple[tuple[HistoricalQuestionObservation, ...], tuple[SyllabusSkill, ...]]:
    canonical_observations = validate_observations(observations)
    canonical_syllabus = validate_syllabus(syllabus)
    curriculum_id = canonical_syllabus[0].curriculum_version_id
    skills_by_id = {item.skill_id: item for item in canonical_syllabus}

    for observation in canonical_observations:
        if observation.curriculum_version_id != curriculum_id:
            raise AnalyticsContractError(
                AnalyticsViolation.MIXED_CURRICULUM,
                "observation and syllabus curriculum versions differ",
            )
        syllabus_skill = skills_by_id.get(observation.skill_id)
        if syllabus_skill is None:
            raise AnalyticsContractError(
                AnalyticsViolation.UNKNOWN_SKILL,
                str(observation.skill_id),
            )
        if syllabus_skill.competency_id != observation.competency_id:
            raise AnalyticsContractError(
                AnalyticsViolation.COMPETENCY_MISMATCH,
                str(observation.id),
            )
    return canonical_observations, canonical_syllabus


def observation_fingerprint(observations: Iterable[HistoricalQuestionObservation]) -> str:
    canonical = validate_observations(observations)
    payload = [
        {
            "competency_id": str(item.competency_id),
            "curriculum_version_id": str(item.curriculum_version_id),
            "difficulty": item.difficulty.value,
            "id": str(item.id),
            "marks": item.marks,
            "paper_code": item.paper_code,
            "provenance": {
                "page_number": item.provenance.page_number,
                "source_block_id": (
                    str(item.provenance.source_block_id)
                    if item.provenance.source_block_id is not None
                    else None
                ),
                "source_document_id": str(item.provenance.source_document_id),
                "source_version": item.provenance.source_version,
            },
            "question_number": item.question_number,
            "question_type": item.question_type.value,
            "skill_id": str(item.skill_id),
            "year": item.year,
        }
        for item in canonical
    ]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def source_versions_for(
    observations: Iterable[HistoricalQuestionObservation],
) -> tuple[SourceVersion, ...]:
    canonical = validate_observations(observations)
    source_pairs = {
        (item.provenance.source_document_id, item.provenance.source_version) for item in canonical
    }
    return tuple(
        SourceVersion(source_document_id=document_id, source_version=version)
        for document_id, version in sorted(source_pairs, key=lambda pair: (pair[0].int, pair[1]))
    )


def _require_clean_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AnalyticsContractError(
            AnalyticsViolation.BLANK_VALUE,
            f"{field_name} must be non-blank and trimmed",
        )


def _require_uuid(value: UUID, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise AnalyticsContractError(
            AnalyticsViolation.INVALID_IDENTIFIER,
            f"{field_name} must be a UUID",
        )
