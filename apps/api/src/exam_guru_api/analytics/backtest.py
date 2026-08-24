"""Rolling held-out evaluation for deterministic practice-priority methods."""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from uuid import UUID

from exam_guru_api.analytics.domain import (
    HistoricalQuestionObservation,
    PracticeRecommendation,
    SourceVersion,
    SyllabusSkill,
    observation_fingerprint,
    source_versions_for,
    validate_analytics_inputs,
    validate_observations,
)
from exam_guru_api.analytics.forecast import (
    PracticePriorityConfig,
    PracticePriorityMethod,
    PracticePriorityRun,
    build_syllabus_balanced_baseline,
    calculate_practice_priorities,
)

ROLLING_BACKTEST_VERSION = "rolling-heldout-backtest-v1"
BACKTEST_LIMITATIONS = (
    "Backtest scores measure historical held-out distribution alignment; they do not "
    "establish future exam certainty.",
    "Results are limited to the supplied provenance-backed years, classifications, and "
    "syllabus scope.",
)


class BacktestViolation(StrEnum):
    INVALID_CONFIG = "invalid_config"
    INSUFFICIENT_YEARS = "insufficient_years"
    LEAKAGE_DETECTED = "leakage_detected"


class BacktestContractError(ValueError):
    def __init__(self, violation: BacktestViolation, detail: str = "") -> None:
        self.violation = violation
        self.detail = detail
        message = violation.value if not detail else f"{violation.value}: {detail}"
        super().__init__(message)


class BacktestLeakageError(BacktestContractError):
    def __init__(self, heldout_year: int) -> None:
        self.heldout_year = heldout_year
        super().__init__(
            BacktestViolation.LEAKAGE_DETECTED,
            f"training evidence must be strictly earlier than held-out year {heldout_year}",
        )


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    minimum_training_years: int = 2
    top_k_skills: int = 3
    meaningful_improvement: Fraction = Fraction(1, 100)

    def __post_init__(self) -> None:
        invalid = (
            not isinstance(self.minimum_training_years, int)
            or isinstance(self.minimum_training_years, bool)
            or self.minimum_training_years < 1
            or not isinstance(self.top_k_skills, int)
            or isinstance(self.top_k_skills, bool)
            or self.top_k_skills < 1
            or not isinstance(self.meaningful_improvement, Fraction)
            or not Fraction() < self.meaningful_improvement <= 1
        )
        if invalid:
            raise BacktestContractError(
                BacktestViolation.INVALID_CONFIG,
                "positive training years/top-k and a threshold in (0, 1] are required",
            )


@dataclass(frozen=True, slots=True)
class LeakageAudit:
    training_cutoff_exclusive: int
    latest_training_year: int
    training_observation_ids: tuple[UUID, ...]
    heldout_observation_ids: tuple[UUID, ...]
    overlapping_observation_ids: tuple[UUID, ...]

    @property
    def passed(self) -> bool:
        actual_overlap = tuple(
            sorted(
                set(self.training_observation_ids).intersection(self.heldout_observation_ids),
                key=lambda item: item.int,
            )
        )
        return (
            self.latest_training_year < self.training_cutoff_exclusive
            and not actual_overlap
            and self.overlapping_observation_ids == actual_overlap
        )

    def assert_passed(self) -> None:
        if not self.passed:
            raise BacktestLeakageError(self.training_cutoff_exclusive)


@dataclass(frozen=True, slots=True)
class RollingWindow:
    training_years: tuple[int, ...]
    heldout_year: int
    training_observations: tuple[HistoricalQuestionObservation, ...]
    heldout_observations: tuple[HistoricalQuestionObservation, ...]
    leakage_audit: LeakageAudit


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    competency_distribution_error: Fraction
    skill_distribution_error: Fraction
    competency_distribution_accuracy: Fraction
    skill_distribution_accuracy: Fraction
    top_k_skill_hit_rate: Fraction
    composite_score: Fraction


@dataclass(frozen=True, slots=True)
class BacktestWindowResult:
    training_years: tuple[int, ...]
    heldout_year: int
    leakage_audit: LeakageAudit
    training_input_fingerprint: str
    heldout_input_fingerprint: str
    heldout_sources: tuple[SourceVersion, ...]
    method_run: PracticePriorityRun
    baseline_run: PracticePriorityRun
    method_metrics: BacktestMetrics
    baseline_metrics: BacktestMetrics
    baseline_delta: Fraction


@dataclass(frozen=True, slots=True)
class BacktestAggregate:
    window_count: int
    mean_method_score: Fraction
    mean_baseline_score: Fraction
    baseline_delta: Fraction
    method_score_variance: Fraction
    baseline_score_variance: Fraction


@dataclass(frozen=True, slots=True)
class PracticeRecommendationDecision:
    mode: PracticeRecommendation
    selected_method: PracticePriorityMethod
    observed_baseline_delta: Fraction
    meaningful_improvement: Fraction
    language: str


@dataclass(frozen=True, slots=True)
class RollingBacktestResult:
    backtest_version: str
    config_fingerprint: str
    input_fingerprint: str
    sources: tuple[SourceVersion, ...]
    limitations: tuple[str, ...]
    windows: tuple[BacktestWindowResult, ...]
    aggregate: BacktestAggregate
    recommendation: PracticeRecommendationDecision
    recommended_run: PracticePriorityRun


def build_rolling_windows(
    observations: Iterable[HistoricalQuestionObservation],
    *,
    minimum_training_years: int = 2,
) -> tuple[RollingWindow, ...]:
    """Create expanding windows whose training evidence is strictly pre-holdout."""

    if (
        not isinstance(minimum_training_years, int)
        or isinstance(minimum_training_years, bool)
        or minimum_training_years < 1
    ):
        raise BacktestContractError(
            BacktestViolation.INVALID_CONFIG,
            "minimum_training_years must be positive",
        )
    canonical = validate_observations(observations)
    years = tuple(sorted({item.year for item in canonical}))
    if len(years) <= minimum_training_years:
        raise BacktestContractError(
            BacktestViolation.INSUFFICIENT_YEARS,
            "a backtest needs the minimum training years plus a held-out year",
        )

    windows: list[RollingWindow] = []
    for heldout_index in range(minimum_training_years, len(years)):
        heldout_year = years[heldout_index]
        training_years = years[:heldout_index]
        training = tuple(item for item in canonical if item.year < heldout_year)
        heldout = tuple(item for item in canonical if item.year == heldout_year)
        training_ids = tuple(item.id for item in training)
        heldout_ids = tuple(item.id for item in heldout)
        overlap = tuple(
            sorted(
                set(training_ids).intersection(heldout_ids),
                key=lambda item: item.int,
            )
        )
        audit = LeakageAudit(
            training_cutoff_exclusive=heldout_year,
            latest_training_year=max(item.year for item in training),
            training_observation_ids=training_ids,
            heldout_observation_ids=heldout_ids,
            overlapping_observation_ids=overlap,
        )
        audit.assert_passed()
        windows.append(
            RollingWindow(
                training_years=training_years,
                heldout_year=heldout_year,
                training_observations=training,
                heldout_observations=heldout,
                leakage_audit=audit,
            )
        )
    return tuple(windows)


def run_rolling_backtest(
    observations: Iterable[HistoricalQuestionObservation],
    syllabus: Iterable[SyllabusSkill],
    *,
    config: BacktestConfig | None = None,
    priority_config: PracticePriorityConfig | None = None,
) -> RollingBacktestResult:
    """Evaluate every eligible historical year and select the safer practice method."""

    active_config = config or BacktestConfig()
    active_priority_config = priority_config or PracticePriorityConfig()
    canonical_observations, canonical_syllabus = validate_analytics_inputs(observations, syllabus)
    windows = build_rolling_windows(
        canonical_observations,
        minimum_training_years=active_config.minimum_training_years,
    )

    window_results: list[BacktestWindowResult] = []
    for window in windows:
        method_run = calculate_practice_priorities(
            window.training_observations,
            canonical_syllabus,
            target_year=window.heldout_year,
            config=active_priority_config,
        )
        baseline_run = build_syllabus_balanced_baseline(
            canonical_syllabus,
            target_year=window.heldout_year,
        )
        method_metrics = _evaluate_run(
            method_run,
            window.heldout_observations,
            top_k_skills=active_config.top_k_skills,
        )
        baseline_metrics = _evaluate_run(
            baseline_run,
            window.heldout_observations,
            top_k_skills=active_config.top_k_skills,
        )
        window_results.append(
            BacktestWindowResult(
                training_years=window.training_years,
                heldout_year=window.heldout_year,
                leakage_audit=window.leakage_audit,
                training_input_fingerprint=observation_fingerprint(window.training_observations),
                heldout_input_fingerprint=observation_fingerprint(window.heldout_observations),
                heldout_sources=source_versions_for(window.heldout_observations),
                method_run=method_run,
                baseline_run=baseline_run,
                method_metrics=method_metrics,
                baseline_metrics=baseline_metrics,
                baseline_delta=(method_metrics.composite_score - baseline_metrics.composite_score),
            )
        )

    results = tuple(window_results)
    method_scores = tuple(item.method_metrics.composite_score for item in results)
    baseline_scores = tuple(item.baseline_metrics.composite_score for item in results)
    mean_method = _mean(method_scores)
    mean_baseline = _mean(baseline_scores)
    aggregate = BacktestAggregate(
        window_count=len(results),
        mean_method_score=mean_method,
        mean_baseline_score=mean_baseline,
        baseline_delta=mean_method - mean_baseline,
        method_score_variance=_variance(method_scores, mean_method),
        baseline_score_variance=_variance(baseline_scores, mean_baseline),
    )
    recommendation = select_practice_recommendation(
        aggregate.baseline_delta,
        active_config.meaningful_improvement,
    )
    recommendation_year = max(item.year for item in canonical_observations) + 1
    if recommendation.selected_method is PracticePriorityMethod.HISTORICAL_EVIDENCE:
        recommended_run = calculate_practice_priorities(
            canonical_observations,
            canonical_syllabus,
            target_year=recommendation_year,
            config=active_priority_config,
        )
    else:
        recommended_run = build_syllabus_balanced_baseline(
            canonical_syllabus,
            target_year=recommendation_year,
        )

    config_fingerprint = _backtest_config_fingerprint(
        active_config,
        active_priority_config,
        canonical_syllabus,
    )
    return RollingBacktestResult(
        backtest_version=ROLLING_BACKTEST_VERSION,
        config_fingerprint=config_fingerprint,
        input_fingerprint=observation_fingerprint(canonical_observations),
        sources=source_versions_for(canonical_observations),
        limitations=BACKTEST_LIMITATIONS,
        windows=results,
        aggregate=aggregate,
        recommendation=recommendation,
        recommended_run=recommended_run,
    )


def select_practice_recommendation(
    baseline_delta: Fraction,
    meaningful_improvement: Fraction,
) -> PracticeRecommendationDecision:
    if (
        not isinstance(baseline_delta, Fraction)
        or not -1 <= baseline_delta <= 1
        or not isinstance(meaningful_improvement, Fraction)
        or not Fraction() < meaningful_improvement <= 1
    ):
        raise BacktestContractError(
            BacktestViolation.INVALID_CONFIG,
            "recommendation requires an exact delta in [-1, 1] and a threshold in (0, 1]",
        )
    if baseline_delta >= meaningful_improvement:
        return PracticeRecommendationDecision(
            mode=PracticeRecommendation.EVIDENCE_BACKED_PRACTICE,
            selected_method=PracticePriorityMethod.HISTORICAL_EVIDENCE,
            observed_baseline_delta=baseline_delta,
            meaningful_improvement=meaningful_improvement,
            language=(
                "Use evidence-backed practice priorities: rolling held-out evaluation showed "
                "meaningful improvement over syllabus-balanced practice. This guides practice "
                "allocation only and makes no claim about future exam content."
            ),
        )
    return PracticeRecommendationDecision(
        mode=PracticeRecommendation.SYLLABUS_BALANCED_PRACTICE,
        selected_method=PracticePriorityMethod.SYLLABUS_BALANCED,
        observed_baseline_delta=baseline_delta,
        meaningful_improvement=meaningful_improvement,
        language=(
            "Use syllabus-balanced practice because the historical method did not show meaningful "
            "improvement over the baseline across rolling held-out years. This is practice "
            "guidance and makes no claim about future exam content."
        ),
    )


def _evaluate_run(
    run: PracticePriorityRun,
    heldout: tuple[HistoricalQuestionObservation, ...],
    *,
    top_k_skills: int,
) -> BacktestMetrics:
    total_marks = sum(item.marks for item in heldout)
    actual_skill_marks: dict[UUID, int] = {}
    actual_competency_marks: dict[UUID, int] = {}
    for item in heldout:
        actual_skill_marks[item.skill_id] = actual_skill_marks.get(item.skill_id, 0) + item.marks
        actual_competency_marks[item.competency_id] = (
            actual_competency_marks.get(item.competency_id, 0) + item.marks
        )

    predicted_skill = {item.skill_id: item.practice_share for item in run.priorities}
    actual_skill = {
        skill_id: Fraction(actual_skill_marks.get(skill_id, 0), total_marks)
        for skill_id in predicted_skill
    }
    predicted_competency: dict[UUID, Fraction] = {}
    for priority in run.priorities:
        predicted_competency[priority.competency_id] = (
            predicted_competency.get(priority.competency_id, Fraction()) + priority.practice_share
        )
    actual_competency = {
        competency_id: Fraction(actual_competency_marks.get(competency_id, 0), total_marks)
        for competency_id in predicted_competency
    }

    skill_error = _total_variation_distance(predicted_skill, actual_skill)
    competency_error = _total_variation_distance(
        predicted_competency,
        actual_competency,
    )
    relevant_skills = set(actual_skill_marks)
    selected_skills = {item.skill_id for item in run.priorities[:top_k_skills]}
    hit_denominator = min(top_k_skills, len(relevant_skills))
    hit_rate = Fraction(len(selected_skills.intersection(relevant_skills)), hit_denominator)
    skill_accuracy = 1 - skill_error
    competency_accuracy = 1 - competency_error
    composite = (skill_accuracy + competency_accuracy + hit_rate) / 3
    return BacktestMetrics(
        competency_distribution_error=competency_error,
        skill_distribution_error=skill_error,
        competency_distribution_accuracy=competency_accuracy,
        skill_distribution_accuracy=skill_accuracy,
        top_k_skill_hit_rate=hit_rate,
        composite_score=composite,
    )


def _total_variation_distance(
    predicted: Mapping[UUID, Fraction],
    actual: Mapping[UUID, Fraction],
) -> Fraction:
    keys = set(predicted).union(actual)
    return (
        sum(
            (abs(predicted.get(key, Fraction()) - actual.get(key, Fraction())) for key in keys),
            Fraction(),
        )
        / 2
    )


def _mean(values: tuple[Fraction, ...]) -> Fraction:
    return sum(values, Fraction()) / len(values)


def _variance(values: tuple[Fraction, ...], mean: Fraction) -> Fraction:
    return sum(((value - mean) ** 2 for value in values), Fraction()) / len(values)


def _backtest_config_fingerprint(
    config: BacktestConfig,
    priority_config: PracticePriorityConfig,
    syllabus: tuple[SyllabusSkill, ...],
) -> str:
    payload = {
        "backtest_version": ROLLING_BACKTEST_VERSION,
        "meaningful_improvement": _fraction_payload(config.meaningful_improvement),
        "minimum_training_years": config.minimum_training_years,
        "priority_algorithm_version": priority_config.algorithm_version,
        "priority_weights": {
            "frequency": _fraction_payload(priority_config.frequency_weight),
            "marks": _fraction_payload(priority_config.marks_weight),
            "recency": _fraction_payload(priority_config.recency_weight),
            "syllabus": _fraction_payload(priority_config.syllabus_weight),
        },
        "syllabus": [
            {
                "balance_weight": item.balance_weight,
                "competency_id": str(item.competency_id),
                "curriculum_version_id": str(item.curriculum_version_id),
                "skill_id": str(item.skill_id),
                "title": item.title,
            }
            for item in syllabus
        ],
        "top_k_skills": config.top_k_skills,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _fraction_payload(value: Fraction) -> dict[str, int]:
    return {"denominator": value.denominator, "numerator": value.numerator}
