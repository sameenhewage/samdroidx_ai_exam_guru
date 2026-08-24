from fractions import Fraction
from uuid import UUID

import pytest

from exam_guru_api.analytics.domain import (
    AnalyticsContractError,
    AnalyticsViolation,
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    QuestionType,
)
from exam_guru_api.analytics.statistics import calculate_historical_statistics

CURRICULUM_ID = UUID(int=100)
COMPETENCY_A = UUID(int=201)
COMPETENCY_B = UUID(int=202)
SKILL_A = UUID(int=301)
SKILL_B = UUID(int=302)
SKILL_C = UUID(int=303)


def observation(
    identifier: int,
    *,
    year: int,
    competency_id: UUID,
    skill_id: UUID,
    question_type: QuestionType,
    difficulty: Difficulty,
    marks: int,
    source_version: str = "paper-v1",
) -> HistoricalQuestionObservation:
    return HistoricalQuestionObservation(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        year=year,
        paper_code=f"P{year}",
        question_number=str(identifier),
        competency_id=competency_id,
        skill_id=skill_id,
        question_type=question_type,
        difficulty=difficulty,
        marks=marks,
        provenance=ObservationProvenance(
            source_document_id=UUID(int=1_000 + year),
            source_version=source_version,
            page_number=identifier,
            source_block_id=UUID(int=10_000 + identifier),
        ),
    )


def history() -> tuple[HistoricalQuestionObservation, ...]:
    return (
        observation(
            1,
            year=2020,
            competency_id=COMPETENCY_A,
            skill_id=SKILL_A,
            question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=Difficulty.EASY,
            marks=1,
        ),
        observation(
            2,
            year=2020,
            competency_id=COMPETENCY_A,
            skill_id=SKILL_B,
            question_type=QuestionType.SHORT_ANSWER,
            difficulty=Difficulty.MEDIUM,
            marks=3,
        ),
        observation(
            3,
            year=2021,
            competency_id=COMPETENCY_B,
            skill_id=SKILL_C,
            question_type=QuestionType.MULTIPLE_CHOICE,
            difficulty=Difficulty.HARD,
            marks=2,
        ),
    )


def test_statistics_reproduce_all_required_distributions_and_source_evidence() -> None:
    result = calculate_historical_statistics(history())

    assert result.curriculum_version_id == CURRICULUM_ID
    assert result.algorithm_version == "historical-distributions-v1"
    assert result.years == (2020, 2021)
    assert result.observation_count == 3
    assert result.total_marks == 6
    assert result.input_observation_ids == (UUID(int=1), UUID(int=2), UUID(int=3))
    assert result.input_fingerprint.startswith("sha256:")
    assert len(result.input_fingerprint) == len("sha256:") + 64

    competency_a = result.competency_distribution[0]
    assert competency_a.key == COMPETENCY_A
    assert competency_a.question_count == 2
    assert competency_a.total_marks == 4
    assert competency_a.question_share == Fraction(2, 3)
    assert competency_a.marks_share == Fraction(2, 3)

    assert tuple(bucket.key for bucket in result.skill_distribution) == (
        SKILL_A,
        SKILL_B,
        SKILL_C,
    )
    assert tuple(bucket.key for bucket in result.question_type_distribution) == (
        QuestionType.MULTIPLE_CHOICE,
        QuestionType.SHORT_ANSWER,
    )
    assert tuple(bucket.key for bucket in result.difficulty_distribution) == (
        Difficulty.EASY,
        Difficulty.HARD,
        Difficulty.MEDIUM,
    )
    assert tuple(bucket.key for bucket in result.marks_distribution) == (1, 2, 3)
    assert tuple(
        (source.source_document_id, source.source_version) for source in result.sources
    ) == (
        (UUID(int=3_020), "paper-v1"),
        (UUID(int=3_021), "paper-v1"),
    )


def test_statistics_are_exact_and_invariant_to_input_order() -> None:
    observations = history()

    forward = calculate_historical_statistics(observations)
    reverse = calculate_historical_statistics(reversed(observations))

    assert forward == reverse
    for distribution in (
        forward.competency_distribution,
        forward.skill_distribution,
        forward.question_type_distribution,
        forward.difficulty_distribution,
        forward.marks_distribution,
    ):
        assert sum((bucket.question_share for bucket in distribution), Fraction()) == 1
        assert sum((bucket.marks_share for bucket in distribution), Fraction()) == 1


def test_statistics_fingerprint_changes_when_provenance_or_evidence_changes() -> None:
    original = list(history())
    changed = list(history())
    changed[0] = observation(
        1,
        year=2020,
        competency_id=COMPETENCY_A,
        skill_id=SKILL_A,
        question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty=Difficulty.EASY,
        marks=1,
        source_version="paper-v2",
    )

    assert (
        calculate_historical_statistics(original).input_fingerprint
        != calculate_historical_statistics(changed).input_fingerprint
    )


def test_statistics_reject_empty_or_mixed_curriculum_evidence() -> None:
    with pytest.raises(AnalyticsContractError) as raised:
        calculate_historical_statistics(())
    assert raised.value.violation is AnalyticsViolation.NO_OBSERVATIONS

    mixed = list(history())
    first = mixed[0]
    mixed[0] = HistoricalQuestionObservation(
        id=first.id,
        curriculum_version_id=UUID(int=999),
        year=first.year,
        paper_code=first.paper_code,
        question_number=first.question_number,
        competency_id=first.competency_id,
        skill_id=first.skill_id,
        question_type=first.question_type,
        difficulty=first.difficulty,
        marks=first.marks,
        provenance=first.provenance,
    )

    with pytest.raises(AnalyticsContractError) as raised:
        calculate_historical_statistics(mixed)
    assert raised.value.violation is AnalyticsViolation.MIXED_CURRICULUM
