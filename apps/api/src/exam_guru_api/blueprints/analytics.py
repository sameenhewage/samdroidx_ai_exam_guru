"""Adapter from analytics backtests to canonical blueprint practice priorities."""

from collections.abc import Iterable
from fractions import Fraction
from math import lcm
from typing import cast
from uuid import UUID

from exam_guru_api.analytics.backtest import RollingBacktestResult
from exam_guru_api.analytics.domain import PracticeRecommendation, SourceVersion
from exam_guru_api.analytics.forecast import (
    PracticePriority as AnalyticsPracticePriority,
)
from exam_guru_api.analytics.forecast import PracticePriorityMethod, PracticePriorityRun

from .domain import BlueprintValidationError, PracticePriority, TaxonomyTarget, Violation


def adapt_rolling_backtest_priorities(
    result: RollingBacktestResult,
    syllabus_targets: Iterable[TaxonomyTarget],
) -> dict[TaxonomyTarget, PracticePriority]:
    """Adapt exact analytics outputs while keeping the rolling recommendation authoritative.

    All fractional values share their least common denominator. The resulting integer scores
    retain exact ratios and the exact aggregate baseline delta without converting through float.
    """

    latest_window = result.windows[-1]
    recommended_is_forecast = (
        result.recommended_run.method is PracticePriorityMethod.HISTORICAL_EVIDENCE
    )
    baseline_run = latest_window.baseline_run if recommended_is_forecast else result.recommended_run
    forecast_run = result.recommended_run if recommended_is_forecast else latest_window.method_run

    targets = tuple(sorted(syllabus_targets, key=lambda target: target.key))
    baseline_by_skill = _by_skill(baseline_run)
    forecast_by_skill = _by_skill(forecast_run)
    _validate_targets(targets, baseline_by_skill, forecast_by_skill)

    fractions = (
        *(priority.practice_share for priority in baseline_by_skill.values()),
        *(priority.practice_share for priority in forecast_by_skill.values()),
        result.aggregate.mean_baseline_score,
        result.aggregate.mean_method_score,
        result.aggregate.baseline_delta,
        result.recommendation.meaningful_improvement,
    )
    scale = _least_common_scale(fractions)
    baseline_metric = _scale(result.aggregate.mean_baseline_score, scale)
    forecast_metric = _scale(result.aggregate.mean_method_score, scale)
    scaled_delta = _scale(result.aggregate.baseline_delta, scale)
    minimum_improvement = _scale(result.recommendation.meaningful_improvement, scale)

    recommendation_chose_forecast = (
        result.recommendation.selected_method,
        result.recommendation.mode,
        result.recommended_run.method,
    ) == (
        PracticePriorityMethod.HISTORICAL_EVIDENCE,
        PracticeRecommendation.EVIDENCE_BACKED_PRACTICE,
        PracticePriorityMethod.HISTORICAL_EVIDENCE,
    )
    meaningful_improvement = (
        result.aggregate.baseline_delta >= result.recommendation.meaningful_improvement
    )
    if (recommendation_chose_forecast, meaningful_improvement) != (True, True):
        # Preserve the delta, but ensure PracticePriority resolves to baseline fallback.
        minimum_improvement = max(minimum_improvement, scaled_delta + 1)

    baseline_evidence = (
        f"analytics:baseline:config:{baseline_run.config_fingerprint}",
        f"analytics:baseline:run:{baseline_run.run_fingerprint}",
    )
    forecast_evidence = (
        f"analytics:forecast:config:{forecast_run.config_fingerprint}",
        f"analytics:forecast:run:{forecast_run.run_fingerprint}",
        f"analytics:backtest:version:{result.backtest_version}",
        f"analytics:backtest:config:{result.config_fingerprint}",
        f"analytics:backtest:input:{result.input_fingerprint}",
        f"analytics:backtest:baseline-delta:{_fraction_text(result.aggregate.baseline_delta)}",
        f"analytics:backtest:meaningful-improvement:"
        f"{_fraction_text(result.recommendation.meaningful_improvement)}",
        f"analytics:score-scale:{scale}",
        *_source_refs(result.sources),
    )

    return {
        target: PracticePriority(
            baseline_score=_scale(
                baseline_by_skill[cast(UUID, target.skill_id)].practice_share,
                scale,
            ),
            baseline_version=baseline_run.algorithm_version,
            baseline_evidence_refs=baseline_evidence,
            forecast_score=_scale(
                forecast_by_skill[cast(UUID, target.skill_id)].practice_share,
                scale,
            ),
            forecast_version=forecast_run.algorithm_version,
            baseline_backtest_score=baseline_metric,
            forecast_backtest_score=forecast_metric,
            minimum_backtest_improvement=minimum_improvement,
            forecast_evidence_refs=forecast_evidence,
        )
        for target in targets
    }


def _by_skill(run: PracticePriorityRun) -> dict[UUID, AnalyticsPracticePriority]:
    return {priority.skill_id: priority for priority in run.priorities}


def _validate_targets(
    targets: tuple[TaxonomyTarget, ...],
    baseline_by_skill: dict[UUID, AnalyticsPracticePriority],
    forecast_by_skill: dict[UUID, AnalyticsPracticePriority],
) -> None:
    for target in targets:
        if target.key.count("/") != 1:
            _raise_target_error(
                f"syllabus target mismatch: {target.key} is not an analytics skill target"
            )

    target_skills = {cast(UUID, target.skill_id) for target in targets}
    identity_sets_match = (
        len(
            {
                frozenset(target_skills),
                frozenset(baseline_by_skill),
                frozenset(forecast_by_skill),
            }
        )
        == 1
    )
    if not identity_sets_match:
        differing = target_skills.symmetric_difference(
            set(baseline_by_skill).union(forecast_by_skill)
        )
        _raise_target_error(
            "missing or unexpected syllabus target skill(s): "
            + ", ".join(str(skill_id) for skill_id in sorted(differing, key=lambda item: item.int))
        )

    for target in targets:
        skill_id = cast(UUID, target.skill_id)
        if (
            baseline_by_skill[skill_id].competency_id,
            forecast_by_skill[skill_id].competency_id,
        ) != (target.competency_id, target.competency_id):
            _raise_target_error(
                f"syllabus target competency mismatch for analytics skill {skill_id}"
            )


def _least_common_scale(values: tuple[Fraction, ...]) -> int:
    scale = 1
    for value in values:
        scale = lcm(scale, value.denominator)
    return scale


def _scale(value: Fraction, scale: int) -> int:
    return value.numerator * (scale // value.denominator)


def _source_refs(sources: tuple[SourceVersion, ...]) -> tuple[str, ...]:
    return tuple(
        f"analytics:source:{source.source_document_id}:{source.source_version}"
        for source in sorted(
            sources,
            key=lambda item: (item.source_document_id.int, item.source_version),
        )
    )


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _raise_target_error(detail: str) -> None:
    raise BlueprintValidationError(
        Violation.UNKNOWN_TAXONOMY_TARGET,
        "syllabus_targets",
        detail,
    )
