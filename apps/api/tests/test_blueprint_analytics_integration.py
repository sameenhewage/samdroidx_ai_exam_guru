from dataclasses import replace
from fractions import Fraction
from uuid import UUID

import pytest

from exam_guru_api.analytics import (
    BacktestConfig,
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    PracticePriorityMethod,
    PracticeRecommendation,
    QuestionType,
    RollingBacktestResult,
    SyllabusSkill,
    build_syllabus_balanced_baseline,
    run_rolling_backtest,
)
from exam_guru_api.blueprints import (
    BlueprintValidationError,
    PriorityMode,
    TaxonomyTarget,
    Violation,
    adapt_rolling_backtest_priorities,
)

CURRICULUM_ID = UUID(int=100)
COMPETENCY_ID = UUID(int=200)
SKILL_A = UUID(int=301)
SKILL_B = UUID(int=302)


def syllabus() -> tuple[SyllabusSkill, ...]:
    return (
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_A, "Numbers"),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_B, "Operations"),
    )


def syllabus_targets() -> tuple[TaxonomyTarget, ...]:
    return (
        TaxonomyTarget(competency_id=COMPETENCY_ID, skill_id=SKILL_A),
        TaxonomyTarget(competency_id=COMPETENCY_ID, skill_id=SKILL_B),
    )


def observation(
    identifier: int,
    *,
    year: int,
    skill_id: UUID,
    marks: int,
) -> HistoricalQuestionObservation:
    return HistoricalQuestionObservation(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        year=year,
        paper_code=f"P{year}",
        question_number=str(identifier),
        competency_id=COMPETENCY_ID,
        skill_id=skill_id,
        question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty=Difficulty.MEDIUM,
        marks=marks,
        provenance=ObservationProvenance(
            source_document_id=UUID(int=1_000 + year),
            source_version=f"reviewed-{year}-v1",
            page_number=identifier,
        ),
    )


def repeated_distribution(
    a_marks: int,
    b_marks: int,
) -> tuple[HistoricalQuestionObservation, ...]:
    records: list[HistoricalQuestionObservation] = []
    for offset, year in enumerate(range(2018, 2022)):
        records.extend(
            (
                observation(10 + offset * 2, year=year, skill_id=SKILL_A, marks=a_marks),
                observation(11 + offset * 2, year=year, skill_id=SKILL_B, marks=b_marks),
            )
        )
    return tuple(records)


def meaningful_result() -> RollingBacktestResult:
    return run_rolling_backtest(
        repeated_distribution(9, 1),
        syllabus(),
        config=BacktestConfig(
            minimum_training_years=2,
            top_k_skills=1,
            meaningful_improvement=Fraction(1, 100),
        ),
    )


def test_adapter_preserves_taxonomy_evidence_versions_and_exact_baseline_delta() -> None:
    result = meaningful_result()
    targets = syllabus_targets()

    adapted = adapt_rolling_backtest_priorities(result, reversed(targets))

    assert tuple(adapted) == targets
    priority_a = adapted[targets[0]]
    priority_b = adapted[targets[1]]
    baseline_run = result.windows[-1].baseline_run
    forecast_run = result.recommended_run

    assert priority_a.mode is PriorityMode.FORECAST
    assert priority_a.baseline_version == baseline_run.algorithm_version
    assert priority_a.forecast_version == forecast_run.algorithm_version
    assert priority_a.baseline_score == 150
    assert priority_a.forecast_score == 174
    assert priority_b.baseline_score == 150
    assert priority_b.forecast_score == 126

    assert priority_a.baseline_backtest_score == 260
    assert priority_a.forecast_backtest_score == 268
    assert priority_a.minimum_backtest_improvement == 3
    assert priority_a.forecast_backtest_score - priority_a.baseline_backtest_score == 8
    assert Fraction(8, 300) == result.aggregate.baseline_delta

    baseline_evidence = "\n".join(priority_a.baseline_evidence_refs)
    forecast_evidence = "\n".join(priority_a.forecast_evidence_refs)
    assert baseline_run.config_fingerprint in baseline_evidence
    assert baseline_run.run_fingerprint in baseline_evidence
    assert forecast_run.config_fingerprint in forecast_evidence
    assert forecast_run.run_fingerprint in forecast_evidence
    assert result.backtest_version in forecast_evidence
    assert result.config_fingerprint in forecast_evidence
    assert result.input_fingerprint in forecast_evidence
    assert "2/75" in forecast_evidence
    for source in result.sources:
        assert str(source.source_document_id) in forecast_evidence
        assert source.source_version in forecast_evidence


def test_adapter_keeps_forecast_auditable_but_uses_baseline_without_improvement() -> None:
    result = run_rolling_backtest(
        repeated_distribution(1, 1),
        syllabus(),
        config=BacktestConfig(meaningful_improvement=Fraction(1, 100)),
    )

    adapted = adapt_rolling_backtest_priorities(result, syllabus_targets())

    assert result.recommendation.selected_method is PracticePriorityMethod.SYLLABUS_BALANCED
    assert all(priority.mode is PriorityMode.BASELINE_FALLBACK for priority in adapted.values())
    assert all(priority.effective_score == priority.baseline_score for priority in adapted.values())
    assert all(priority.forecast_version is not None for priority in adapted.values())
    assert all(priority.forecast_evidence_refs for priority in adapted.values())
    assert all(priority.baseline_backtest_score == 100 for priority in adapted.values())
    assert all(priority.forecast_backtest_score == 100 for priority in adapted.values())
    assert all(priority.minimum_backtest_improvement == 1 for priority in adapted.values())


def test_adapter_requires_both_the_recommendation_and_meaningful_delta_for_forecast() -> None:
    result = meaningful_result()
    baseline_recommendation = replace(
        result.recommendation,
        mode=PracticeRecommendation.SYLLABUS_BALANCED_PRACTICE,
        selected_method=PracticePriorityMethod.SYLLABUS_BALANCED,
    )
    baseline_run = build_syllabus_balanced_baseline(syllabus(), target_year=2022)
    result = replace(
        result,
        recommendation=baseline_recommendation,
        recommended_run=baseline_run,
    )

    adapted = adapt_rolling_backtest_priorities(result, syllabus_targets())

    assert all(priority.mode is PriorityMode.BASELINE_FALLBACK for priority in adapted.values())
    assert all(priority.effective_score == priority.baseline_score for priority in adapted.values())
    for priority in adapted.values():
        assert priority.baseline_backtest_score is not None
        assert priority.forecast_backtest_score is not None
        assert priority.forecast_backtest_score - priority.baseline_backtest_score == 8
        assert priority.minimum_backtest_improvement == 9


def test_adapter_rejects_a_missing_syllabus_target() -> None:
    with pytest.raises(BlueprintValidationError) as raised:
        adapt_rolling_backtest_priorities(meaningful_result(), syllabus_targets()[:1])

    assert raised.value.violation is Violation.UNKNOWN_TAXONOMY_TARGET
    assert "missing" in raised.value.detail
    assert str(SKILL_B) in raised.value.detail


def test_adapter_rejects_a_target_below_the_analytics_skill_level() -> None:
    mismatched = (
        TaxonomyTarget(
            competency_id=COMPETENCY_ID,
            skill_id=SKILL_A,
            sub_skill_id=UUID(int=401),
        ),
        syllabus_targets()[1],
    )

    with pytest.raises(BlueprintValidationError) as raised:
        adapt_rolling_backtest_priorities(meaningful_result(), mismatched)

    assert raised.value.violation is Violation.UNKNOWN_TAXONOMY_TARGET
    assert "mismatch" in raised.value.detail
    assert str(SKILL_A) in raised.value.detail


def test_adapter_rejects_a_competency_mismatch_for_the_same_syllabus_skill() -> None:
    mismatched = (
        TaxonomyTarget(competency_id=UUID(int=999), skill_id=SKILL_A),
        syllabus_targets()[1],
    )

    with pytest.raises(BlueprintValidationError) as raised:
        adapt_rolling_backtest_priorities(meaningful_result(), mismatched)

    assert raised.value.violation is Violation.UNKNOWN_TAXONOMY_TARGET
    assert "mismatch" in raised.value.detail
    assert str(SKILL_A) in raised.value.detail
