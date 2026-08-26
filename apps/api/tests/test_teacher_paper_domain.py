from dataclasses import replace
from uuid import UUID

import pytest

from exam_guru_api.blueprints.domain import Difficulty, TaxonomyTarget
from exam_guru_api.blueprints.generator import generate_blueprint
from exam_guru_api.teacher_papers.domain import (
    PaperDifficulty,
    PaperScopeError,
    PaperSettings,
    ResolvedCurriculum,
    ResolvedLesson,
    ResolvedTaxonomyTarget,
    TeacherScopeKind,
    TeacherScopeSelection,
    _difficulty_counts,
    _resolved_target,
    _selected_taxonomy_targets,
    assign_blueprint_lessons,
    build_blueprint_specification,
    translate_teacher_scope,
)

ACTOR_ID = UUID(int=25_001)
CURRICULUM_ID = UUID(int=25_002)
EXAM_ID = UUID(int=25_003)
MEDIUM_ID = UUID(int=25_004)
SUBJECT_ID = UUID(int=25_005)
UNIT_ONE_ID = UUID(int=25_006)
UNIT_TWO_ID = UUID(int=25_007)
COMPETENCY_ID = UUID(int=25_008)
SKILL_IDS = tuple(UUID(int=25_100 + index) for index in range(1, 5))
LESSON_IDS = tuple(UUID(int=25_200 + index) for index in range(1, 5))


def target(index: int) -> ResolvedTaxonomyTarget:
    return ResolvedTaxonomyTarget(
        competency_id=COMPETENCY_ID,
        skill_id=SKILL_IDS[index - 1],
        sub_skill_id=None,
        learning_concept_id=None,
        label=f"Numbers / Skill {index}",
    )


def lesson(index: int, *, mapped: bool = True) -> ResolvedLesson:
    unit_id = UNIT_ONE_ID if index <= 2 else UNIT_TWO_ID
    return ResolvedLesson(
        id=LESSON_IDS[index - 1],
        unit_id=unit_id,
        unit_code="NUMBERS" if index <= 2 else "FRACTIONS",
        unit_title="Numbers" if index <= 2 else "Fractions",
        unit_ordinal=1 if index <= 2 else 2,
        code=f"LESSON-{index}",
        title=("Whole numbers", "Factors", "Fractions", "Decimals")[index - 1],
        ordinal=1 if index in {1, 3} else 2,
        taxonomy_targets=(target(index),) if mapped else (),
    )


def curriculum(*, lessons: tuple[ResolvedLesson, ...] | None = None) -> ResolvedCurriculum:
    return ResolvedCurriculum(
        curriculum_version_id=CURRICULUM_ID,
        exam_configuration_id=EXAM_ID,
        assessment_code="SCHOOL-G7",
        assessment_label="School Grade 7",
        grade=7,
        medium_id=MEDIUM_ID,
        medium_code="en",
        medium_label="English",
        subject_id=SUBJECT_ID,
        subject_code="MATHEMATICS",
        subject_label="Mathematics",
        curriculum_code="G7-MATH-V1",
        curriculum_title="Grade 7 Mathematics",
        lessons=(tuple(lesson(index) for index in range(1, 5)) if lessons is None else lessons),
    )


def test_lesson_range_translation_is_inclusive_normalized_and_server_owned() -> None:
    resolved = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(
            kind=TeacherScopeKind.LESSON_RANGE,
            start_lesson=1,
            end_lesson=3,
        ),
    )

    assert [item.number for item in resolved.lessons] == [1, 2, 3]
    assert [item.lesson.id for item in resolved.lessons] == list(LESSON_IDS[:3])
    assert resolved.unit_ids == (UNIT_ONE_ID, UNIT_TWO_ID)
    assert resolved.lesson_ids == LESSON_IDS[:3]
    assert resolved.summary == "Lessons 1\u20133"
    assert all(item.lesson.taxonomy_targets for item in resolved.lessons)


def test_full_subject_translation_uses_every_active_mapped_lesson() -> None:
    resolved = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )

    assert resolved.lesson_ids == LESSON_IDS
    assert resolved.summary == "Full syllabus"
    assert [item.label for item in resolved.lessons] == [
        "Lesson 1 — Whole numbers",
        "Lesson 2 — Factors",
        "Lesson 3 — Fractions",
        "Lesson 4 — Decimals",
    ]


@pytest.mark.parametrize(
    ("selection", "code"),
    [
        (
            TeacherScopeSelection(
                kind=TeacherScopeKind.LESSON_RANGE,
                start_lesson=3,
                end_lesson=1,
            ),
            "paper_generation_lesson_range_invalid",
        ),
        (
            TeacherScopeSelection(
                kind=TeacherScopeKind.LESSON_RANGE,
                start_lesson=0,
                end_lesson=1,
            ),
            "paper_generation_lesson_range_invalid",
        ),
        (
            TeacherScopeSelection(
                kind=TeacherScopeKind.LESSON_RANGE,
                start_lesson=1,
                end_lesson=5,
            ),
            "paper_generation_lesson_range_not_found",
        ),
    ],
)
def test_scope_translation_rejects_malformed_or_missing_ranges(
    selection: TeacherScopeSelection,
    code: str,
) -> None:
    with pytest.raises(PaperScopeError) as captured:
        translate_teacher_scope(curriculum(), selection)

    assert captured.value.code == code


def test_scope_translation_fails_closed_when_any_selected_lesson_is_unmapped() -> None:
    lessons = (lesson(1), lesson(2, mapped=False), lesson(3), lesson(4))

    with pytest.raises(PaperScopeError) as captured:
        translate_teacher_scope(
            curriculum(lessons=lessons),
            TeacherScopeSelection(
                kind=TeacherScopeKind.LESSON_RANGE,
                start_lesson=1,
                end_lesson=3,
            ),
        )

    assert captured.value.code == "paper_generation_lesson_unmapped"
    assert captured.value.lesson_number == 2


def test_teacher_settings_build_a_deterministic_bounded_blueprint_and_slot_lesson_plan() -> None:
    selected = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(
            kind=TeacherScopeKind.LESSON_RANGE,
            start_lesson=1,
            end_lesson=3,
        ),
    )
    settings = PaperSettings(
        question_count=12,
        duration_minutes=50,
        difficulty=PaperDifficulty.BALANCED,
    )
    specification = build_blueprint_specification(
        curriculum(),
        selected,
        settings,
        paper_reference="EGP-2500-0001",
        request_fingerprint="sha256:" + "a" * 64,
    )

    first = generate_blueprint(specification, seed=17)
    second = generate_blueprint(specification, seed=17)
    assignments = assign_blueprint_lessons(first, selected)

    assert first == second
    assert len(first.slots) == 12
    assert first.total_marks == 12
    assert first.curriculum_scope.grade == 7
    assert first.curriculum_scope.medium == "en"
    assert first.curriculum_scope.subject_id == SUBJECT_ID
    assert first.curriculum_scope.lesson_ids == LESSON_IDS[:3]
    assert {slot.difficulty.value for slot in first.slots} == {"easy", "medium", "hard"}
    assert len(assignments) == 12
    assert {assignment.lesson.id for assignment in assignments} == set(LESSON_IDS[:3])
    assert all(
        assignment.taxonomy_target in assignment.lesson.taxonomy_targets
        for assignment in assignments
    )


def test_slot_assignment_never_falls_back_to_a_lesson_without_exact_taxonomy() -> None:
    selected = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )
    settings = PaperSettings(4, 45, PaperDifficulty.BALANCED)
    specification = build_blueprint_specification(
        curriculum(),
        selected,
        settings,
        paper_reference="EGP-2500-0002",
        request_fingerprint="sha256:" + "b" * 64,
    )
    blueprint = generate_blueprint(specification, seed=19)
    impossible_lessons = tuple(
        replace(item, lesson=replace(item.lesson, taxonomy_targets=(target(4),)))
        for item in selected.lessons
    )

    with pytest.raises(PaperScopeError) as captured:
        assign_blueprint_lessons(blueprint, replace(selected, lessons=impossible_lessons))

    assert captured.value.code == "paper_generation_slot_lesson_mapping_missing"


def test_teacher_domain_rejects_malformed_settings_labels_and_empty_curriculum() -> None:
    for values in (
        (0, 45, PaperDifficulty.BALANCED),
        (True, 45, PaperDifficulty.BALANCED),
        (1, 0, PaperDifficulty.BALANCED),
        (1, True, PaperDifficulty.BALANCED),
        (1, 45, "balanced"),
    ):
        with pytest.raises(ValueError, match="must be"):
            PaperSettings(*values)  # type: ignore[arg-type]

    for label in ("", " padded ", "x" * 1_025):
        with pytest.raises(ValueError, match="taxonomy label"):
            replace(target(1), label=label)

    with pytest.raises(PaperScopeError) as empty:
        translate_teacher_scope(
            curriculum(lessons=()),
            TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
        )
    assert empty.value.code == "paper_generation_curriculum_content_missing"


def test_teacher_scope_rejects_full_subject_range_and_unknown_kind() -> None:
    with pytest.raises(PaperScopeError) as ranged_full:
        translate_teacher_scope(
            curriculum(),
            TeacherScopeSelection(
                kind=TeacherScopeKind.FULL_SUBJECT,
                start_lesson=1,
            ),
        )
    assert ranged_full.value.code == "paper_generation_lesson_range_invalid"

    with pytest.raises(PaperScopeError) as unknown:
        translate_teacher_scope(
            curriculum(),
            TeacherScopeSelection(kind="unknown"),  # type: ignore[arg-type]
        )
    assert unknown.value.code == "paper_generation_scope_invalid"


def test_difficulty_presets_and_secondary_taxonomy_selection_are_deterministic() -> None:
    assert _difficulty_counts(1, PaperDifficulty.EASIER) == {Difficulty.EASY: 1}
    assert _difficulty_counts(1, PaperDifficulty.CHALLENGING) == {Difficulty.HARD: 1}
    for preset in PaperDifficulty:
        counts = _difficulty_counts(7, preset)
        assert sum(counts.values()) == 7
        assert all(value > 0 for value in counts.values())

    first = lesson(1)
    extra_target = target(2)
    multi = replace(first, taxonomy_targets=(first.taxonomy_targets[0], extra_target))
    scope = translate_teacher_scope(
        curriculum(lessons=(multi,)),
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )
    assert _selected_taxonomy_targets(scope, 1) == (first.taxonomy_targets[0],)
    assert _selected_taxonomy_targets(scope, 2) == (
        first.taxonomy_targets[0],
        extra_target,
    )

    second_lesson = replace(
        lesson(2),
        taxonomy_targets=(first.taxonomy_targets[0], extra_target),
    )
    repeated_scope = replace(
        scope,
        lessons=(scope.lessons[0], replace(scope.lessons[0], lesson=second_lesson)),
    )
    assert _selected_taxonomy_targets(repeated_scope, 3) == (
        first.taxonomy_targets[0],
        extra_target,
    )
    empty_mapping_scope = replace(
        scope,
        lessons=(replace(scope.lessons[0], lesson=replace(first, taxonomy_targets=())),),
    )
    assert _selected_taxonomy_targets(empty_mapping_scope, 1) == ()


def test_empty_blueprint_taxonomy_and_missing_resolved_target_fail_closed() -> None:
    empty_scope = replace(
        translate_teacher_scope(
            curriculum(),
            TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
        ),
        lessons=(),
    )
    with pytest.raises(PaperScopeError) as empty:
        build_blueprint_specification(
            curriculum(),
            empty_scope,
            PaperSettings(1, 45, PaperDifficulty.BALANCED),
            paper_reference="EGP-2500-0003",
            request_fingerprint="sha256:" + "c" * 64,
        )
    assert empty.value.code == "paper_generation_lesson_unmapped"

    with pytest.raises(PaperScopeError) as unresolved:
        _resolved_target(
            TaxonomyTarget(competency_id=UUID(int=999_999)),
            translate_teacher_scope(
                curriculum(),
                TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
            ),
        )
    assert unresolved.value.code == "paper_generation_slot_lesson_mapping_missing"
