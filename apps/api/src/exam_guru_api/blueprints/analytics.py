"""Adapter from analytics backtests to canonical blueprint practice priorities."""

from collections.abc import Iterable
from dataclasses import replace
from fractions import Fraction
from math import lcm
from typing import cast
from uuid import UUID

from pydantic import ValidationError

from exam_guru_api.analytics.backtest import (
    BacktestAggregate,
    BacktestMetrics,
    BacktestWindowResult,
    LeakageAudit,
    PracticeRecommendationDecision,
    RollingBacktestResult,
)
from exam_guru_api.analytics.domain import PracticeRecommendation, SourceVersion
from exam_guru_api.analytics.forecast import (
    PracticePriority as AnalyticsPracticePriority,
)
from exam_guru_api.analytics.forecast import (
    PracticePriorityFeatures,
    PracticePriorityMethod,
    PracticePriorityRun,
)
from exam_guru_api.analytics.repository import AnalyticsRunRecord
from exam_guru_api.analytics.schemas import (
    AnalyticsResultsResponse,
    BacktestMetricsResponse,
    BacktestWindowResponse,
    PracticePriorityRunResponse,
    SourceVersionResponse,
)
from exam_guru_api.analytics.service import fingerprint_payload

from .domain import BlueprintValidationError, PracticePriority, TaxonomyTarget, Violation


class PersistedAnalyticsEvidenceError(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"invalid persisted analytics evidence: {detail}")


def adapt_persisted_analytics_priorities(
    record: AnalyticsRunRecord,
    syllabus_targets: Iterable[TaxonomyTarget],
) -> dict[TaxonomyTarget, PracticePriority]:
    if fingerprint_payload(record.result) != record.result_fingerprint:
        raise PersistedAnalyticsEvidenceError("result fingerprint mismatch")
    try:
        response = AnalyticsResultsResponse.model_validate(record.result)
    except ValidationError as error:
        raise PersistedAnalyticsEvidenceError("result snapshot shape is invalid") from error
    _validate_persisted_versions_and_scope(record, response)
    result = _rolling_backtest_from_response(response)
    adapted = adapt_rolling_backtest_priorities(result, syllabus_targets)
    persisted_refs = (
        f"analytics:persisted-run:{record.id}",
        f"analytics:persisted-result:{record.result_fingerprint}",
    )
    return {
        target: replace(
            priority,
            forecast_evidence_refs=(*priority.forecast_evidence_refs, *persisted_refs),
        )
        for target, priority in adapted.items()
    }


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


def _validate_persisted_versions_and_scope(
    record: AnalyticsRunRecord,
    response: AnalyticsResultsResponse,
) -> None:
    backtest = response.backtest
    runs = (
        *(window.method_run for window in backtest.windows),
        *(window.baseline_run for window in backtest.windows),
        backtest.recommended_run,
    )
    invalid = (
        response.statistics.curriculum_version_id != record.curriculum_version_id
        or response.statistics.algorithm_version != record.statistics_algorithm_version
        or backtest.backtest_version != record.backtest_algorithm_version
        or backtest.config_fingerprint != record.config_fingerprint
        or not backtest.windows
        or any(run.curriculum_version_id != record.curriculum_version_id for run in runs)
        or any(
            window.method_run.algorithm_version != record.practice_priority_algorithm_version
            for window in backtest.windows
        )
        or any(
            window.baseline_run.algorithm_version != record.baseline_algorithm_version
            for window in backtest.windows
        )
    )
    expected_recommended_version = (
        record.practice_priority_algorithm_version
        if backtest.recommended_run.method is PracticePriorityMethod.HISTORICAL_EVIDENCE
        else record.baseline_algorithm_version
    )
    if invalid or backtest.recommended_run.algorithm_version != expected_recommended_version:
        raise PersistedAnalyticsEvidenceError("curriculum scope or algorithm version mismatch")


def _rolling_backtest_from_response(response: AnalyticsResultsResponse) -> RollingBacktestResult:
    backtest = response.backtest
    windows = tuple(_window_from_response(window) for window in backtest.windows)
    return RollingBacktestResult(
        backtest_version=backtest.backtest_version,
        config_fingerprint=backtest.config_fingerprint,
        input_fingerprint=backtest.input_fingerprint,
        sources=tuple(_source_from_response(source) for source in backtest.sources),
        limitations=tuple(backtest.limitations),
        windows=windows,
        aggregate=BacktestAggregate(
            window_count=backtest.aggregate.window_count,
            mean_method_score=backtest.aggregate.mean_method_score.to_fraction(),
            mean_baseline_score=backtest.aggregate.mean_baseline_score.to_fraction(),
            baseline_delta=backtest.aggregate.baseline_delta.to_fraction(),
            method_score_variance=backtest.aggregate.method_score_variance.to_fraction(),
            baseline_score_variance=backtest.aggregate.baseline_score_variance.to_fraction(),
        ),
        recommendation=PracticeRecommendationDecision(
            mode=backtest.recommendation.mode,
            selected_method=backtest.recommendation.selected_method,
            observed_baseline_delta=(backtest.recommendation.observed_baseline_delta.to_fraction()),
            meaningful_improvement=(backtest.recommendation.meaningful_improvement.to_fraction()),
            language=backtest.recommendation.language,
        ),
        recommended_run=_run_from_response(backtest.recommended_run),
    )


def _window_from_response(window: BacktestWindowResponse) -> BacktestWindowResult:
    audit = LeakageAudit(
        training_cutoff_exclusive=window.leakage_audit.training_cutoff_exclusive,
        latest_training_year=window.leakage_audit.latest_training_year,
        training_observation_ids=tuple(window.leakage_audit.training_observation_ids),
        heldout_observation_ids=tuple(window.leakage_audit.heldout_observation_ids),
        overlapping_observation_ids=tuple(window.leakage_audit.overlapping_observation_ids),
    )
    if audit.passed is not window.leakage_audit.passed or not audit.passed:
        raise PersistedAnalyticsEvidenceError("persisted leakage audit failed")
    return BacktestWindowResult(
        training_years=tuple(window.training_years),
        heldout_year=window.heldout_year,
        leakage_audit=audit,
        training_input_fingerprint=window.training_input_fingerprint,
        heldout_input_fingerprint=window.heldout_input_fingerprint,
        heldout_sources=tuple(_source_from_response(source) for source in window.heldout_sources),
        method_run=_run_from_response(window.method_run),
        baseline_run=_run_from_response(window.baseline_run),
        method_metrics=_metrics_from_response(window.method_metrics),
        baseline_metrics=_metrics_from_response(window.baseline_metrics),
        baseline_delta=window.baseline_delta.to_fraction(),
    )


def _metrics_from_response(metrics: BacktestMetricsResponse) -> BacktestMetrics:
    return BacktestMetrics(
        competency_distribution_error=metrics.competency_distribution_error.to_fraction(),
        skill_distribution_error=metrics.skill_distribution_error.to_fraction(),
        competency_distribution_accuracy=(metrics.competency_distribution_accuracy.to_fraction()),
        skill_distribution_accuracy=metrics.skill_distribution_accuracy.to_fraction(),
        top_k_skill_hit_rate=metrics.top_k_skill_hit_rate.to_fraction(),
        composite_score=metrics.composite_score.to_fraction(),
    )


def _run_from_response(run: PracticePriorityRunResponse) -> PracticePriorityRun:
    return PracticePriorityRun(
        curriculum_version_id=run.curriculum_version_id,
        target_year=run.target_year,
        evidence_through_year=run.evidence_through_year,
        method=run.method,
        recommendation=run.recommendation,
        algorithm_version=run.algorithm_version,
        config_fingerprint=run.config_fingerprint,
        run_fingerprint=run.run_fingerprint,
        feature_definitions=tuple(run.feature_definitions),
        random_seed=None,
        input_observation_ids=tuple(run.input_observation_ids),
        sources=tuple(_source_from_response(source) for source in run.sources),
        priorities=tuple(
            AnalyticsPracticePriority(
                rank=priority.rank,
                competency_id=priority.competency_id,
                skill_id=priority.skill_id,
                skill_title=priority.skill_title,
                practice_share=priority.practice_share.to_fraction(),
                features=PracticePriorityFeatures(
                    syllabus_share=priority.features.syllabus_share.to_fraction(),
                    question_frequency_share=(
                        priority.features.question_frequency_share.to_fraction()
                    ),
                    marks_share=priority.features.marks_share.to_fraction(),
                    recency_gap_share=priority.features.recency_gap_share.to_fraction(),
                    evidence_question_count=priority.features.evidence_question_count,
                    evidence_marks=priority.features.evidence_marks,
                    last_observed_year=priority.features.last_observed_year,
                ),
                evidence_language=priority.evidence_language,
            )
            for priority in run.priorities
        ),
    )


def _source_from_response(source: SourceVersionResponse) -> SourceVersion:
    return SourceVersion(
        source_document_id=source.source_document_id,
        source_version=source.source_version,
    )


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
