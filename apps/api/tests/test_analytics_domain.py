from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest

from exam_guru_api.analytics.domain import (
    AnalyticsContractError,
    AnalyticsViolation,
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    QuestionType,
    SyllabusSkill,
    validate_analytics_inputs,
    validate_observations,
    validate_syllabus,
)

CURRICULUM_ID = UUID(int=100)
COMPETENCY_ID = UUID(int=200)
SKILL_ID = UUID(int=300)


def make_observation(
    identifier: int = 1,
    *,
    curriculum_version_id: UUID = CURRICULUM_ID,
    year: int = 2020,
    paper_code: str = "P1",
    question_number: str = "1",
    competency_id: UUID = COMPETENCY_ID,
    skill_id: UUID = SKILL_ID,
    question_type: QuestionType = QuestionType.MULTIPLE_CHOICE,
    difficulty: Difficulty = Difficulty.EASY,
    marks: int = 2,
    source_version: str = "sha256:paper-v1",
    page_number: int = 3,
) -> HistoricalQuestionObservation:
    return HistoricalQuestionObservation(
        id=UUID(int=identifier),
        curriculum_version_id=curriculum_version_id,
        year=year,
        paper_code=paper_code,
        question_number=question_number,
        competency_id=competency_id,
        skill_id=skill_id,
        question_type=question_type,
        difficulty=difficulty,
        marks=marks,
        provenance=ObservationProvenance(
            source_document_id=UUID(int=1_000 + identifier),
            source_version=source_version,
            page_number=page_number,
            source_block_id=UUID(int=2_000 + identifier),
        ),
    )


def make_syllabus_skill(
    *,
    curriculum_version_id: UUID = CURRICULUM_ID,
    competency_id: UUID = COMPETENCY_ID,
    skill_id: UUID = SKILL_ID,
    title: str = "Number relationships",
    balance_weight: int = 1,
) -> SyllabusSkill:
    return SyllabusSkill(
        curriculum_version_id=curriculum_version_id,
        competency_id=competency_id,
        skill_id=skill_id,
        title=title,
        balance_weight=balance_weight,
    )


def test_observation_preserves_immutable_question_identity_and_provenance() -> None:
    observation = make_observation()

    assert observation.year == 2020
    assert observation.provenance.source_version == "sha256:paper-v1"
    assert observation.provenance.page_number == 3

    with pytest.raises(FrozenInstanceError):
        observation.marks = 4  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "violation"),
    [
        (
            lambda: ObservationProvenance(
                source_document_id=UUID(int=1), source_version=" ", page_number=1
            ),
            AnalyticsViolation.BLANK_VALUE,
        ),
        (
            lambda: ObservationProvenance(
                source_document_id=UUID(int=1), source_version="version", page_number=0
            ),
            AnalyticsViolation.INVALID_PAGE,
        ),
        (
            lambda: ObservationProvenance(
                source_document_id=UUID(int=1),
                source_version="version",
                page_number=1.5,  # type: ignore[arg-type]
            ),
            AnalyticsViolation.INVALID_PAGE,
        ),
        (lambda: make_observation(year=1899), AnalyticsViolation.INVALID_YEAR),
        (lambda: make_observation(year=2101), AnalyticsViolation.INVALID_YEAR),
        (lambda: make_observation(year=True), AnalyticsViolation.INVALID_YEAR),
        (lambda: make_observation(year=2020.5), AnalyticsViolation.INVALID_YEAR),  # type: ignore[arg-type]
        (lambda: make_observation(paper_code=" P1"), AnalyticsViolation.BLANK_VALUE),
        (lambda: make_observation(question_number=""), AnalyticsViolation.BLANK_VALUE),
        (lambda: make_observation(marks=0), AnalyticsViolation.INVALID_MARKS),
        (lambda: make_observation(marks=-1), AnalyticsViolation.INVALID_MARKS),
        (lambda: make_observation(marks=True), AnalyticsViolation.INVALID_MARKS),
        (lambda: make_observation(marks=1.5), AnalyticsViolation.INVALID_MARKS),  # type: ignore[arg-type]
        (lambda: make_syllabus_skill(title=" "), AnalyticsViolation.BLANK_VALUE),
        (lambda: make_syllabus_skill(balance_weight=0), AnalyticsViolation.INVALID_WEIGHT),
        (
            lambda: make_syllabus_skill(balance_weight=1.5),  # type: ignore[arg-type]
            AnalyticsViolation.INVALID_WEIGHT,
        ),
    ],
)
def test_domain_boundaries_reject_invalid_values(
    factory: object,
    violation: AnalyticsViolation,
) -> None:
    with pytest.raises(AnalyticsContractError) as raised:
        factory()  # type: ignore[operator]

    assert raised.value.violation is violation


def test_observation_validation_is_canonical_and_rejects_duplicate_identity() -> None:
    first = make_observation(1, question_number="1")
    second = make_observation(2, question_number="2")

    assert validate_observations((second, first)) == (first, second)

    with pytest.raises(AnalyticsContractError) as raised:
        validate_observations((first, first))
    assert raised.value.violation is AnalyticsViolation.DUPLICATE_OBSERVATION_ID

    duplicate_question = make_observation(3, question_number="1")
    with pytest.raises(AnalyticsContractError) as raised:
        validate_observations((first, duplicate_question))
    assert raised.value.violation is AnalyticsViolation.DUPLICATE_QUESTION


def test_analytics_inputs_require_one_curriculum_and_exact_syllabus_classification() -> None:
    skill = make_syllabus_skill()
    observation = make_observation()

    assert validate_analytics_inputs((observation,), (skill,)) == ((observation,), (skill,))

    wrong_curriculum = make_observation(2, curriculum_version_id=UUID(int=999))
    with pytest.raises(AnalyticsContractError) as raised:
        validate_analytics_inputs((wrong_curriculum,), (skill,))
    assert raised.value.violation is AnalyticsViolation.MIXED_CURRICULUM

    unknown_skill = make_observation(3, skill_id=UUID(int=999))
    with pytest.raises(AnalyticsContractError) as raised:
        validate_analytics_inputs((unknown_skill,), (skill,))
    assert raised.value.violation is AnalyticsViolation.UNKNOWN_SKILL

    wrong_competency = make_observation(4, competency_id=UUID(int=999))
    with pytest.raises(AnalyticsContractError) as raised:
        validate_analytics_inputs((wrong_competency,), (skill,))
    assert raised.value.violation is AnalyticsViolation.COMPETENCY_MISMATCH


def test_syllabus_must_be_nonempty_and_skill_ids_must_be_unique() -> None:
    observation = make_observation()

    with pytest.raises(AnalyticsContractError) as raised:
        validate_analytics_inputs((observation,), ())
    assert raised.value.violation is AnalyticsViolation.EMPTY_SYLLABUS

    duplicate = make_syllabus_skill(title="Duplicate label")
    with pytest.raises(AnalyticsContractError) as raised:
        validate_analytics_inputs((observation,), (make_syllabus_skill(), duplicate))
    assert raised.value.violation is AnalyticsViolation.DUPLICATE_SKILL

    other_curriculum = make_syllabus_skill(
        curriculum_version_id=UUID(int=999),
        skill_id=UUID(int=998),
    )
    with pytest.raises(AnalyticsContractError) as raised:
        validate_syllabus((make_syllabus_skill(), other_curriculum))
    assert raised.value.violation is AnalyticsViolation.MIXED_CURRICULUM


def test_runtime_contract_rejects_wrong_identifier_category_and_record_types() -> None:
    with pytest.raises(AnalyticsContractError) as raised:
        ObservationProvenance(
            source_document_id="not-a-uuid",  # type: ignore[arg-type]
            source_version="version",
            page_number=1,
        )
    assert raised.value.violation is AnalyticsViolation.INVALID_IDENTIFIER

    with pytest.raises(AnalyticsContractError) as raised:
        make_observation(question_type="multiple_choice")  # type: ignore[arg-type]
    assert raised.value.violation is AnalyticsViolation.INVALID_CATEGORY

    with pytest.raises(AnalyticsContractError) as raised:
        replace(make_observation(), provenance=object())  # type: ignore[arg-type]
    assert raised.value.violation is AnalyticsViolation.INVALID_CATEGORY

    with pytest.raises(AnalyticsContractError) as raised:
        validate_observations((object(),))  # type: ignore[arg-type]
    assert raised.value.violation is AnalyticsViolation.INVALID_CATEGORY

    with pytest.raises(AnalyticsContractError) as raised:
        validate_syllabus((object(),))  # type: ignore[arg-type]
    assert raised.value.violation is AnalyticsViolation.INVALID_CATEGORY


def test_contract_error_without_detail_has_a_stable_machine_readable_message() -> None:
    error = AnalyticsContractError(AnalyticsViolation.NO_OBSERVATIONS)

    assert str(error) == AnalyticsViolation.NO_OBSERVATIONS.value
