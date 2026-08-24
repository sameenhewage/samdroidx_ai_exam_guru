from fractions import Fraction
from uuid import UUID

import pytest

from exam_guru_api.analytics.backtest import (
    BacktestConfig,
    BacktestContractError,
    BacktestLeakageError,
    BacktestViolation,
    LeakageAudit,
    build_rolling_windows,
    run_rolling_backtest,
    select_practice_recommendation,
)
from exam_guru_api.analytics.domain import (
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    PracticeRecommendation,
    QuestionType,
    SyllabusSkill,
)
from exam_guru_api.analytics.forecast import PracticePriorityMethod

CURRICULUM_ID = UUID(int=100)
COMPETENCY_ID = UUID(int=200)
SKILL_A = UUID(int=301)
SKILL_B = UUID(int=302)


def syllabus() -> tuple[SyllabusSkill, ...]:
    return (
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_A, "Numbers"),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_B, "Operations"),
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


def repeated_distribution(a_marks: int, b_marks: int) -> tuple[HistoricalQuestionObservation, ...]:
    records: list[HistoricalQuestionObservation] = []
    for offset, year in enumerate(range(2018, 2022)):
        records.extend(
            (
                observation(10 + offset * 2, year=year, skill_id=SKILL_A, marks=a_marks),
                observation(11 + offset * 2, year=year, skill_id=SKILL_B, marks=b_marks),
            )
        )
    return tuple(records)


def test_rolling_windows_train_only_on_strictly_earlier_years() -> None:
    observations = repeated_distribution(9, 1)

    windows = build_rolling_windows(observations, minimum_training_years=2)

    assert tuple(window.heldout_year for window in windows) == (2020, 2021)
    assert windows[0].training_years == (2018, 2019)
    assert windows[1].training_years == (2018, 2019, 2020)
    assert {item.year for item in windows[0].training_observations} == {2018, 2019}
    assert {item.year for item in windows[0].heldout_observations} == {2020}
    assert windows[0].leakage_audit.training_cutoff_exclusive == 2020
    assert windows[0].leakage_audit.passed
    assert windows[0].leakage_audit.overlapping_observation_ids == ()
    assert set(windows[0].leakage_audit.training_observation_ids).isdisjoint(
        windows[0].leakage_audit.heldout_observation_ids
    )


def test_backtest_records_deterministic_metrics_baseline_delta_and_all_windows() -> None:
    result = run_rolling_backtest(
        repeated_distribution(9, 1),
        syllabus(),
        config=BacktestConfig(
            minimum_training_years=2,
            top_k_skills=1,
            meaningful_improvement=Fraction(1, 100),
        ),
    )

    assert len(result.windows) == 2
    assert result.aggregate.window_count == 2
    assert result.aggregate.mean_method_score == Fraction(67, 75)
    assert result.aggregate.mean_baseline_score == Fraction(13, 15)
    assert result.aggregate.baseline_delta == Fraction(2, 75)
    assert result.aggregate.method_score_variance == 0
    assert result.aggregate.baseline_score_variance == 0
    assert all(window.baseline_delta == Fraction(2, 75) for window in result.windows)
    assert all(window.leakage_audit.passed for window in result.windows)
    assert result.recommendation.mode is PracticeRecommendation.EVIDENCE_BACKED_PRACTICE
    assert result.recommendation.selected_method is PracticePriorityMethod.HISTORICAL_EVIDENCE
    assert result.recommended_run.method is PracticePriorityMethod.HISTORICAL_EVIDENCE
    assert result.recommended_run.target_year == 2022
    assert result.limitations == (
        "Backtest scores measure historical held-out distribution alignment; they do not "
        "establish future exam certainty.",
        "Results are limited to the supplied provenance-backed years, classifications, and "
        "syllabus scope.",
    )
    assert "practice" in result.recommendation.language.casefold()
    assert "future exam question will" not in result.recommendation.language.casefold()


def test_no_meaningful_improvement_falls_back_to_syllabus_balanced_practice() -> None:
    result = run_rolling_backtest(
        repeated_distribution(1, 1),
        reversed(syllabus()),
        config=BacktestConfig(meaningful_improvement=Fraction(1, 100)),
    )

    assert result.aggregate.baseline_delta == 0
    assert result.recommendation.mode is PracticeRecommendation.SYLLABUS_BALANCED_PRACTICE
    assert result.recommendation.selected_method is PracticePriorityMethod.SYLLABUS_BALANCED
    assert result.recommended_run.method is PracticePriorityMethod.SYLLABUS_BALANCED
    assert tuple(priority.practice_share for priority in result.recommended_run.priorities) == (
        Fraction(1, 2),
        Fraction(1, 2),
    )
    assert "did not show meaningful improvement" in result.recommendation.language


def test_meaningful_improvement_threshold_is_an_explicit_inclusive_boundary() -> None:
    threshold = Fraction(1, 20)

    meaningful = select_practice_recommendation(threshold, threshold)
    fallback = select_practice_recommendation(threshold - Fraction(1, 10_000), threshold)

    assert meaningful.mode is PracticeRecommendation.EVIDENCE_BACKED_PRACTICE
    assert fallback.mode is PracticeRecommendation.SYLLABUS_BALANCED_PRACTICE


@pytest.mark.parametrize(
    ("factory", "violation"),
    [
        (lambda: BacktestConfig(minimum_training_years=0), BacktestViolation.INVALID_CONFIG),
        (
            lambda: BacktestConfig(minimum_training_years=1.5),  # type: ignore[arg-type]
            BacktestViolation.INVALID_CONFIG,
        ),
        (
            lambda: build_rolling_windows(repeated_distribution(1, 1), minimum_training_years=0),
            BacktestViolation.INVALID_CONFIG,
        ),
        (
            lambda: build_rolling_windows(
                repeated_distribution(1, 1),
                minimum_training_years=1.5,  # type: ignore[arg-type]
            ),
            BacktestViolation.INVALID_CONFIG,
        ),
        (lambda: BacktestConfig(top_k_skills=0), BacktestViolation.INVALID_CONFIG),
        (
            lambda: BacktestConfig(top_k_skills=1.5),  # type: ignore[arg-type]
            BacktestViolation.INVALID_CONFIG,
        ),
        (
            lambda: BacktestConfig(meaningful_improvement=Fraction()),
            BacktestViolation.INVALID_CONFIG,
        ),
        (
            lambda: build_rolling_windows(
                repeated_distribution(1, 1)[:4], minimum_training_years=2
            ),
            BacktestViolation.INSUFFICIENT_YEARS,
        ),
    ],
)
def test_backtest_boundaries_reject_invalid_or_insufficient_windows(
    factory: object, violation: BacktestViolation
) -> None:
    with pytest.raises(BacktestContractError) as raised:
        factory()  # type: ignore[operator]

    assert raised.value.violation is violation


def test_backtest_fingerprint_includes_the_syllabus_baseline_configuration() -> None:
    weighted_syllabus = (
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_A, "Numbers", balance_weight=2),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_B, "Operations"),
    )
    observations = repeated_distribution(1, 1)

    balanced = run_rolling_backtest(observations, syllabus())
    weighted = run_rolling_backtest(observations, weighted_syllabus)

    assert balanced.input_fingerprint == weighted.input_fingerprint
    assert balanced.config_fingerprint != weighted.config_fingerprint


def test_backtest_is_input_order_invariant_and_all_metrics_are_bounded() -> None:
    for a_marks in range(1, 10):
        observations = repeated_distribution(a_marks, 10 - a_marks)
        config = BacktestConfig(top_k_skills=1)

        forward = run_rolling_backtest(observations, syllabus(), config=config)
        reverse = run_rolling_backtest(reversed(observations), reversed(syllabus()), config=config)

        assert forward == reverse
        for window in forward.windows:
            for metrics in (window.method_metrics, window.baseline_metrics):
                assert Fraction() <= metrics.competency_distribution_accuracy <= 1
                assert Fraction() <= metrics.skill_distribution_accuracy <= 1
                assert Fraction() <= metrics.top_k_skill_hit_rate <= 1
                assert Fraction() <= metrics.composite_score <= 1
            assert -1 <= window.baseline_delta <= 1
        assert -1 <= forward.aggregate.baseline_delta <= 1


@pytest.mark.parametrize(
    "audit",
    [
        LeakageAudit(
            training_cutoff_exclusive=2020,
            latest_training_year=2020,
            training_observation_ids=(UUID(int=1),),
            heldout_observation_ids=(UUID(int=2),),
            overlapping_observation_ids=(),
        ),
        LeakageAudit(
            training_cutoff_exclusive=2020,
            latest_training_year=2019,
            training_observation_ids=(UUID(int=1),),
            heldout_observation_ids=(UUID(int=1),),
            overlapping_observation_ids=(UUID(int=1),),
        ),
        LeakageAudit(
            training_cutoff_exclusive=2020,
            latest_training_year=2019,
            training_observation_ids=(UUID(int=1),),
            heldout_observation_ids=(UUID(int=1),),
            overlapping_observation_ids=(),
        ),
    ],
)
def test_failed_leakage_audit_is_rejected_before_evaluation(audit: LeakageAudit) -> None:
    with pytest.raises(BacktestLeakageError) as raised:
        audit.assert_passed()

    assert raised.value.heldout_year == 2020
    assert raised.value.violation is BacktestViolation.LEAKAGE_DETECTED


def test_invalid_recommendation_metrics_and_plain_errors_are_rejected_stably() -> None:
    with pytest.raises(BacktestContractError) as raised:
        select_practice_recommendation(Fraction(), Fraction())
    assert raised.value.violation is BacktestViolation.INVALID_CONFIG

    with pytest.raises(BacktestContractError) as raised:
        select_practice_recommendation(Fraction(2), Fraction(1, 100))
    assert raised.value.violation is BacktestViolation.INVALID_CONFIG

    error = BacktestContractError(BacktestViolation.INVALID_CONFIG)
    assert str(error) == BacktestViolation.INVALID_CONFIG.value
