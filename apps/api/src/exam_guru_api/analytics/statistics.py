"""Exact, order-invariant historical question distributions."""

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from uuid import UUID

from exam_guru_api.analytics.domain import (
    AnalyticsContractError,
    AnalyticsViolation,
    Difficulty,
    HistoricalQuestionObservation,
    QuestionType,
    SourceVersion,
    observation_fingerprint,
    source_versions_for,
    validate_observations,
)

HISTORICAL_STATISTICS_VERSION = "historical-distributions-v1"


@dataclass(frozen=True, slots=True)
class DistributionBucket[BucketKey: Hashable]:
    key: BucketKey
    question_count: int
    total_marks: int
    question_share: Fraction
    marks_share: Fraction


@dataclass(frozen=True, slots=True)
class HistoricalStatistics:
    curriculum_version_id: UUID
    algorithm_version: str
    years: tuple[int, ...]
    observation_count: int
    total_marks: int
    competency_distribution: tuple[DistributionBucket[UUID], ...]
    skill_distribution: tuple[DistributionBucket[UUID], ...]
    question_type_distribution: tuple[DistributionBucket[QuestionType], ...]
    difficulty_distribution: tuple[DistributionBucket[Difficulty], ...]
    marks_distribution: tuple[DistributionBucket[int], ...]
    input_observation_ids: tuple[UUID, ...]
    sources: tuple[SourceVersion, ...]
    input_fingerprint: str


def calculate_historical_statistics(
    observations: Iterable[HistoricalQuestionObservation],
) -> HistoricalStatistics:
    """Calculate exact distributions from one provenance-backed curriculum history."""

    canonical = validate_observations(observations)
    if not canonical:
        raise AnalyticsContractError(
            AnalyticsViolation.NO_OBSERVATIONS,
            "historical statistics require at least one observation",
        )

    observation_count = len(canonical)
    total_marks = sum(item.marks for item in canonical)
    return HistoricalStatistics(
        curriculum_version_id=canonical[0].curriculum_version_id,
        algorithm_version=HISTORICAL_STATISTICS_VERSION,
        years=tuple(sorted({item.year for item in canonical})),
        observation_count=observation_count,
        total_marks=total_marks,
        competency_distribution=_build_distribution(
            canonical,
            key_for=lambda item: item.competency_id,
            sort_key=lambda key: (key.int,),
            observation_count=observation_count,
            total_marks=total_marks,
        ),
        skill_distribution=_build_distribution(
            canonical,
            key_for=lambda item: item.skill_id,
            sort_key=lambda key: (key.int,),
            observation_count=observation_count,
            total_marks=total_marks,
        ),
        question_type_distribution=_build_distribution(
            canonical,
            key_for=lambda item: item.question_type,
            sort_key=lambda key: tuple(key.value.encode("ascii")),
            observation_count=observation_count,
            total_marks=total_marks,
        ),
        difficulty_distribution=_build_distribution(
            canonical,
            key_for=lambda item: item.difficulty,
            sort_key=lambda key: tuple(key.value.encode("ascii")),
            observation_count=observation_count,
            total_marks=total_marks,
        ),
        marks_distribution=_build_distribution(
            canonical,
            key_for=lambda item: item.marks,
            sort_key=lambda key: (key,),
            observation_count=observation_count,
            total_marks=total_marks,
        ),
        input_observation_ids=tuple(item.id for item in canonical),
        sources=source_versions_for(canonical),
        input_fingerprint=observation_fingerprint(canonical),
    )


def _build_distribution[BucketKey: Hashable](
    observations: tuple[HistoricalQuestionObservation, ...],
    *,
    key_for: Callable[[HistoricalQuestionObservation], BucketKey],
    sort_key: Callable[[BucketKey], tuple[int, ...]],
    observation_count: int,
    total_marks: int,
) -> tuple[DistributionBucket[BucketKey], ...]:
    counts: dict[BucketKey, int] = {}
    marks: dict[BucketKey, int] = {}
    for observation in observations:
        key = key_for(observation)
        counts[key] = counts.get(key, 0) + 1
        marks[key] = marks.get(key, 0) + observation.marks

    return tuple(
        DistributionBucket(
            key=key,
            question_count=counts[key],
            total_marks=marks[key],
            question_share=Fraction(counts[key], observation_count),
            marks_share=Fraction(marks[key], total_marks),
        )
        for key in sorted(counts, key=sort_key)
    )
