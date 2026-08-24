from fractions import Fraction
from uuid import UUID

import pytest

from exam_guru_api.analytics.domain import (
    AnalyticsContractError,
    AnalyticsViolation,
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    PracticeRecommendation,
    QuestionType,
    SyllabusSkill,
)
from exam_guru_api.analytics.forecast import (
    ForecastLeakageError,
    PracticePriorityConfig,
    PracticePriorityMethod,
    build_syllabus_balanced_baseline,
    calculate_practice_priorities,
)

CURRICULUM_ID = UUID(int=100)
COMPETENCY_A = UUID(int=201)
COMPETENCY_B = UUID(int=202)
SKILL_A = UUID(int=301)
SKILL_B = UUID(int=302)
SKILL_C = UUID(int=303)


def syllabus() -> tuple[SyllabusSkill, ...]:
    return (
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_A, SKILL_A, "Numbers"),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_A, SKILL_B, "Operations"),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_B, SKILL_C, "Measurement"),
    )


def observation(
    identifier: int,
    *,
    year: int,
    skill_id: UUID,
    competency_id: UUID = COMPETENCY_A,
    marks: int = 1,
) -> HistoricalQuestionObservation:
    return HistoricalQuestionObservation(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        year=year,
        paper_code=f"P{year}",
        question_number=str(identifier),
        competency_id=competency_id,
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


def test_syllabus_balanced_baseline_is_equal_explainable_and_deterministic() -> None:
    first = build_syllabus_balanced_baseline(reversed(syllabus()), target_year=2025)
    second = build_syllabus_balanced_baseline(syllabus(), target_year=2025)

    assert first == second
    assert first.method is PracticePriorityMethod.SYLLABUS_BALANCED
    assert first.evidence_through_year is None
    assert first.input_observation_ids == ()
    assert first.random_seed is None
    assert tuple(priority.practice_share for priority in first.priorities) == (
        Fraction(1, 3),
        Fraction(1, 3),
        Fraction(1, 3),
    )
    assert all("practice" in priority.evidence_language.casefold() for priority in first.priorities)
    assert all(
        "will appear" not in priority.evidence_language.casefold() for priority in first.priorities
    )


def test_practice_priorities_use_only_prior_provenance_backed_evidence() -> None:
    observations = (
        observation(1, year=2020, skill_id=SKILL_A, marks=4),
        observation(2, year=2021, skill_id=SKILL_A, marks=4),
        observation(3, year=2021, skill_id=SKILL_B, marks=1),
    )

    result = calculate_practice_priorities(observations, syllabus(), target_year=2023)

    assert result.method is PracticePriorityMethod.HISTORICAL_EVIDENCE
    assert result.evidence_through_year == 2021
    assert result.input_observation_ids == (UUID(int=1), UUID(int=2), UUID(int=3))
    assert result.feature_definitions == (
        "syllabus_balance_share",
        "historical_question_frequency_share",
        "historical_marks_share",
        "recency_gap_share",
    )
    assert sum((priority.practice_share for priority in result.priorities), Fraction()) == 1
    assert tuple(priority.rank for priority in result.priorities) == (1, 2, 3)
    assert result.priorities[0].skill_id == SKILL_A
    assert result.priorities[0].features.evidence_question_count == 2
    assert result.priorities[0].features.evidence_marks == 8
    assert result.priorities[0].features.last_observed_year == 2021
    assert "available before 2023" in result.priorities[0].evidence_language
    assert "practice priority" in result.priorities[0].evidence_language.casefold()
    assert "will appear" not in result.priorities[0].evidence_language.casefold()


def test_forecast_boundary_rejects_heldout_or_future_observations_as_leakage() -> None:
    safe = observation(1, year=2021, skill_id=SKILL_A)
    heldout = observation(2, year=2022, skill_id=SKILL_B)
    future = observation(3, year=2023, skill_id=SKILL_C, competency_id=COMPETENCY_B)

    with pytest.raises(ForecastLeakageError) as raised:
        calculate_practice_priorities((safe, heldout, future), syllabus(), target_year=2022)

    assert raised.value.target_year == 2022
    assert raised.value.leaked_observation_ids == (UUID(int=2), UUID(int=3))
    assert raised.value.violation is AnalyticsViolation.HELDOUT_LEAKAGE


def test_no_history_safely_reduces_to_the_syllabus_baseline() -> None:
    forecast = calculate_practice_priorities((), syllabus(), target_year=2025)
    baseline = build_syllabus_balanced_baseline(syllabus(), target_year=2025)

    assert forecast.method is PracticePriorityMethod.SYLLABUS_BALANCED
    assert forecast.priorities == baseline.priorities
    assert forecast.run_fingerprint == baseline.run_fingerprint


def test_priority_scoring_is_order_invariant_exact_and_bounded_for_many_inputs() -> None:
    observations = tuple(
        observation(
            identifier=index,
            year=2015 + (index % 8),
            skill_id=(SKILL_A, SKILL_B, SKILL_C)[index % 3],
            competency_id=COMPETENCY_B if index % 3 == 2 else COMPETENCY_A,
            marks=(index % 5) + 1,
        )
        for index in range(1, 25)
    )

    forward = calculate_practice_priorities(observations, syllabus(), target_year=2025)
    reverse = calculate_practice_priorities(
        reversed(observations),
        reversed(syllabus()),
        target_year=2025,
    )

    assert forward == reverse
    assert sum((priority.practice_share for priority in forward.priorities), Fraction()) == 1
    for priority in forward.priorities:
        assert Fraction() <= priority.practice_share <= 1
        assert Fraction() <= priority.features.syllabus_share <= 1
        assert Fraction() <= priority.features.question_frequency_share <= 1
        assert Fraction() <= priority.features.marks_share <= 1
        assert Fraction() <= priority.features.recency_gap_share <= 1


def test_practice_priority_algorithm_version_must_be_nonblank_and_trimmed() -> None:
    with pytest.raises(AnalyticsContractError) as raised:
        PracticePriorityConfig(algorithm_version=" ")

    assert raised.value.violation is AnalyticsViolation.BLANK_VALUE


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PracticePriorityConfig(syllabus_weight=Fraction(-1, 5)),
        lambda: PracticePriorityConfig(
            syllabus_weight=Fraction(1, 4),
            frequency_weight=Fraction(1, 4),
            marks_weight=Fraction(1, 4),
            recency_weight=Fraction(1, 8),
        ),
    ],
)
def test_scoring_weights_must_be_nonnegative_and_sum_to_one(factory: object) -> None:
    with pytest.raises(AnalyticsContractError) as raised:
        factory()  # type: ignore[operator]

    assert raised.value.violation is AnalyticsViolation.INVALID_WEIGHT


def test_weighted_syllabus_baseline_honours_declared_balance_without_prediction_claims() -> None:
    weighted = (
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_A, SKILL_A, "Numbers", balance_weight=2),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_A, SKILL_B, "Operations", balance_weight=1),
    )

    result = build_syllabus_balanced_baseline(weighted, target_year=2025)

    assert tuple(priority.practice_share for priority in result.priorities) == (
        Fraction(2, 3),
        Fraction(1, 3),
    )
    assert result.recommendation is PracticeRecommendation.SYLLABUS_BALANCED_PRACTICE


@pytest.mark.parametrize("target_year", [1900, 2102, True, 2025.5])
def test_target_year_must_be_an_integer_inside_the_supported_boundary(
    target_year: object,
) -> None:
    with pytest.raises(AnalyticsContractError) as raised:
        build_syllabus_balanced_baseline(
            syllabus(),
            target_year=target_year,  # type: ignore[arg-type]
        )

    assert raised.value.violation is AnalyticsViolation.INVALID_YEAR
