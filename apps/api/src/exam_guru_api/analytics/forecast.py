"""Deterministic, evidence-backed practice-priority calculation."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from uuid import UUID

from exam_guru_api.analytics.domain import (
    AnalyticsContractError,
    AnalyticsViolation,
    HistoricalQuestionObservation,
    PracticeRecommendation,
    SourceVersion,
    SyllabusSkill,
    observation_fingerprint,
    source_versions_for,
    validate_analytics_inputs,
    validate_observations,
    validate_syllabus,
)

PRACTICE_PRIORITY_ALGORITHM_VERSION = "deterministic-practice-priority-v1"
SYLLABUS_BASELINE_ALGORITHM_VERSION = "syllabus-balanced-baseline-v1"
FEATURE_DEFINITIONS = (
    "syllabus_balance_share",
    "historical_question_frequency_share",
    "historical_marks_share",
    "recency_gap_share",
)


class PracticePriorityMethod(StrEnum):
    HISTORICAL_EVIDENCE = "historical_evidence"
    SYLLABUS_BALANCED = "syllabus_balanced"


class ForecastLeakageError(AnalyticsContractError):
    def __init__(self, target_year: int, leaked_observation_ids: tuple[UUID, ...]) -> None:
        self.target_year = target_year
        self.leaked_observation_ids = leaked_observation_ids
        super().__init__(
            AnalyticsViolation.HELDOUT_LEAKAGE,
            f"target year {target_year} cannot use observations {leaked_observation_ids}",
        )


@dataclass(frozen=True, slots=True)
class PracticePriorityConfig:
    algorithm_version: str = PRACTICE_PRIORITY_ALGORITHM_VERSION
    syllabus_weight: Fraction = Fraction(2, 5)
    frequency_weight: Fraction = Fraction(1, 5)
    marks_weight: Fraction = Fraction(1, 5)
    recency_weight: Fraction = Fraction(1, 5)

    def __post_init__(self) -> None:
        if not self.algorithm_version or self.algorithm_version != self.algorithm_version.strip():
            raise AnalyticsContractError(
                AnalyticsViolation.BLANK_VALUE,
                "algorithm_version must be non-blank and trimmed",
            )
        if (
            any(
                not isinstance(weight, Fraction) or weight < 0
                for weight in (
                    self.syllabus_weight,
                    self.frequency_weight,
                    self.marks_weight,
                    self.recency_weight,
                )
            )
            or sum(
                (
                    self.syllabus_weight,
                    self.frequency_weight,
                    self.marks_weight,
                    self.recency_weight,
                ),
                Fraction(),
            )
            != 1
        ):
            raise AnalyticsContractError(
                AnalyticsViolation.INVALID_WEIGHT,
                "practice-priority weights must be non-negative Fractions summing to one",
            )


@dataclass(frozen=True, slots=True)
class PracticePriorityFeatures:
    syllabus_share: Fraction
    question_frequency_share: Fraction
    marks_share: Fraction
    recency_gap_share: Fraction
    evidence_question_count: int
    evidence_marks: int
    last_observed_year: int | None


@dataclass(frozen=True, slots=True)
class PracticePriority:
    rank: int
    competency_id: UUID
    skill_id: UUID
    skill_title: str
    practice_share: Fraction
    features: PracticePriorityFeatures
    evidence_language: str


@dataclass(frozen=True, slots=True)
class PracticePriorityRun:
    curriculum_version_id: UUID
    target_year: int
    evidence_through_year: int | None
    method: PracticePriorityMethod
    recommendation: PracticeRecommendation
    algorithm_version: str
    config_fingerprint: str
    run_fingerprint: str
    feature_definitions: tuple[str, ...]
    random_seed: None
    input_observation_ids: tuple[UUID, ...]
    sources: tuple[SourceVersion, ...]
    priorities: tuple[PracticePriority, ...]


def build_syllabus_balanced_baseline(
    syllabus: Iterable[SyllabusSkill],
    *,
    target_year: int,
) -> PracticePriorityRun:
    """Build a simple baseline using only declared syllabus balancing weights."""

    _validate_target_year(target_year)
    canonical_syllabus = validate_syllabus(syllabus)
    total_weight = sum(item.balance_weight for item in canonical_syllabus)
    shares = {
        item.skill_id: Fraction(item.balance_weight, total_weight) for item in canonical_syllabus
    }
    ranked = sorted(
        canonical_syllabus,
        key=lambda item: (-shares[item.skill_id], item.competency_id.int, item.skill_id.int),
    )
    priorities = tuple(
        PracticePriority(
            rank=rank,
            competency_id=item.competency_id,
            skill_id=item.skill_id,
            skill_title=item.title,
            practice_share=shares[item.skill_id],
            features=PracticePriorityFeatures(
                syllabus_share=shares[item.skill_id],
                question_frequency_share=Fraction(),
                marks_share=Fraction(),
                recency_gap_share=Fraction(),
                evidence_question_count=0,
                evidence_marks=0,
                last_observed_year=None,
            ),
            evidence_language=(
                "Syllabus-balanced practice allocation based on declared curriculum coverage. "
                "It is practice guidance and makes no claim about future exam content."
            ),
        )
        for rank, item in enumerate(ranked, start=1)
    )
    syllabus_payload = _syllabus_payload(canonical_syllabus)
    config_fingerprint = _hash_payload(
        {
            "algorithm_version": SYLLABUS_BASELINE_ALGORITHM_VERSION,
            "syllabus": syllabus_payload,
        }
    )
    run_fingerprint = _hash_payload(
        {
            "config_fingerprint": config_fingerprint,
            "target_year": target_year,
        }
    )
    return PracticePriorityRun(
        curriculum_version_id=canonical_syllabus[0].curriculum_version_id,
        target_year=target_year,
        evidence_through_year=None,
        method=PracticePriorityMethod.SYLLABUS_BALANCED,
        recommendation=PracticeRecommendation.SYLLABUS_BALANCED_PRACTICE,
        algorithm_version=SYLLABUS_BASELINE_ALGORITHM_VERSION,
        config_fingerprint=config_fingerprint,
        run_fingerprint=run_fingerprint,
        feature_definitions=("syllabus_balance_share",),
        random_seed=None,
        input_observation_ids=(),
        sources=(),
        priorities=priorities,
    )


def calculate_practice_priorities(
    observations: Iterable[HistoricalQuestionObservation],
    syllabus: Iterable[SyllabusSkill],
    *,
    target_year: int,
    config: PracticePriorityConfig | None = None,
) -> PracticePriorityRun:
    """Allocate practice using evidence strictly earlier than ``target_year``."""

    _validate_target_year(target_year)
    active_config = config or PracticePriorityConfig()
    canonical_observations = validate_observations(observations)
    leaked_ids = tuple(item.id for item in canonical_observations if item.year >= target_year)
    if leaked_ids:
        raise ForecastLeakageError(target_year, leaked_ids)

    canonical_observations, canonical_syllabus = validate_analytics_inputs(
        canonical_observations, syllabus
    )
    if not canonical_observations:
        return build_syllabus_balanced_baseline(canonical_syllabus, target_year=target_year)

    syllabus_total = sum(item.balance_weight for item in canonical_syllabus)
    total_questions = len(canonical_observations)
    total_marks = sum(item.marks for item in canonical_observations)
    earliest_year = min(item.year for item in canonical_observations)

    question_counts = {item.skill_id: 0 for item in canonical_syllabus}
    mark_totals = {item.skill_id: 0 for item in canonical_syllabus}
    last_years: dict[UUID, int | None] = {item.skill_id: None for item in canonical_syllabus}
    for observation in canonical_observations:
        question_counts[observation.skill_id] += 1
        mark_totals[observation.skill_id] += observation.marks
        current_last = last_years[observation.skill_id]
        if current_last is None or observation.year > current_last:
            last_years[observation.skill_id] = observation.year

    raw_gaps = {
        item.skill_id: (
            target_year - last_years[item.skill_id]  # type: ignore[operator]
            if last_years[item.skill_id] is not None
            else target_year - earliest_year + 1
        )
        for item in canonical_syllabus
    }
    gap_total = sum(raw_gaps.values())
    features_by_skill: dict[UUID, PracticePriorityFeatures] = {}
    scores: dict[UUID, Fraction] = {}
    for item in canonical_syllabus:
        skill_id = item.skill_id
        features = PracticePriorityFeatures(
            syllabus_share=Fraction(item.balance_weight, syllabus_total),
            question_frequency_share=Fraction(question_counts[skill_id], total_questions),
            marks_share=Fraction(mark_totals[skill_id], total_marks),
            recency_gap_share=Fraction(raw_gaps[skill_id], gap_total),
            evidence_question_count=question_counts[skill_id],
            evidence_marks=mark_totals[skill_id],
            last_observed_year=last_years[skill_id],
        )
        features_by_skill[skill_id] = features
        scores[skill_id] = (
            active_config.syllabus_weight * features.syllabus_share
            + active_config.frequency_weight * features.question_frequency_share
            + active_config.marks_weight * features.marks_share
            + active_config.recency_weight * features.recency_gap_share
        )

    ranked = sorted(
        canonical_syllabus,
        key=lambda item: (-scores[item.skill_id], item.competency_id.int, item.skill_id.int),
    )
    priorities = tuple(
        PracticePriority(
            rank=rank,
            competency_id=item.competency_id,
            skill_id=item.skill_id,
            skill_title=item.title,
            practice_share=scores[item.skill_id],
            features=features_by_skill[item.skill_id],
            evidence_language=_evidence_language(features_by_skill[item.skill_id], target_year),
        )
        for rank, item in enumerate(ranked, start=1)
    )
    config_fingerprint = _config_fingerprint(active_config, canonical_syllabus)
    input_fingerprint = observation_fingerprint(canonical_observations)
    run_fingerprint = _hash_payload(
        {
            "config_fingerprint": config_fingerprint,
            "input_fingerprint": input_fingerprint,
            "target_year": target_year,
        }
    )
    return PracticePriorityRun(
        curriculum_version_id=canonical_syllabus[0].curriculum_version_id,
        target_year=target_year,
        evidence_through_year=max(item.year for item in canonical_observations),
        method=PracticePriorityMethod.HISTORICAL_EVIDENCE,
        recommendation=PracticeRecommendation.EVIDENCE_BACKED_PRACTICE,
        algorithm_version=active_config.algorithm_version,
        config_fingerprint=config_fingerprint,
        run_fingerprint=run_fingerprint,
        feature_definitions=FEATURE_DEFINITIONS,
        random_seed=None,
        input_observation_ids=tuple(item.id for item in canonical_observations),
        sources=source_versions_for(canonical_observations),
        priorities=priorities,
    )


def _validate_target_year(target_year: int) -> None:
    if (
        not isinstance(target_year, int)
        or isinstance(target_year, bool)
        or not 1901 <= target_year <= 2101
    ):
        raise AnalyticsContractError(
            AnalyticsViolation.INVALID_YEAR,
            "target_year must be between 1901 and 2101",
        )


def _evidence_language(features: PracticePriorityFeatures, target_year: int) -> str:
    if features.evidence_question_count:
        return (
            f"Practice priority based on {features.evidence_question_count} provenance-backed "
            f"historical question observation(s) available before {target_year}, combined with "
            "syllabus balance, frequency, marks, and recency evidence. It guides practice "
            "allocation only and makes no claim about future exam content."
        )
    return (
        f"Practice priority reflects syllabus balance and the absence of direct provenance-backed "
        f"observations available before {target_year}, alongside historical coverage evidence. "
        "It is practice guidance and makes no claim about future exam content."
    )


def _config_fingerprint(
    config: PracticePriorityConfig,
    syllabus: tuple[SyllabusSkill, ...],
) -> str:
    return _hash_payload(
        {
            "algorithm_version": config.algorithm_version,
            "syllabus": _syllabus_payload(syllabus),
            "weights": {
                "frequency": _fraction_payload(config.frequency_weight),
                "marks": _fraction_payload(config.marks_weight),
                "recency": _fraction_payload(config.recency_weight),
                "syllabus": _fraction_payload(config.syllabus_weight),
            },
        }
    )


def _syllabus_payload(syllabus: tuple[SyllabusSkill, ...]) -> list[dict[str, object]]:
    return [
        {
            "balance_weight": item.balance_weight,
            "competency_id": str(item.competency_id),
            "curriculum_version_id": str(item.curriculum_version_id),
            "skill_id": str(item.skill_id),
            "title": item.title,
        }
        for item in syllabus
    ]


def _fraction_payload(value: Fraction) -> dict[str, int]:
    return {"denominator": value.denominator, "numerator": value.numerator}


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
