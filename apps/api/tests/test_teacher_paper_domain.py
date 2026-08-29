from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.blueprints.domain import Difficulty, TaxonomyTarget
from exam_guru_api.blueprints.generator import generate_blueprint
from exam_guru_api.retrieval.domain import RetrievalScope, RetrievalScopeSet, TaxonomyScope
from exam_guru_api.teacher_papers.domain import (
    PaperDifficulty,
    PaperScopeError,
    PaperSettings,
    ProgrammePaperPart,
    ResolvedCurriculum,
    ResolvedLesson,
    ResolvedPaperScope,
    ResolvedProgrammeMapping,
    ResolvedProgrammeSelection,
    ResolvedTaxonomyTarget,
    ScholarshipPaperMode,
    ScholarshipProgrammePolicy,
    TeacherScopeKind,
    TeacherScopeSelection,
    _difficulty_counts,
    _resolved_target,
    _selected_taxonomy_targets,
    assign_blueprint_lessons,
    build_blueprint_specification,
    resolve_scholarship_programme,
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


def settings(question_count: int = 4) -> PaperSettings:
    return PaperSettings(
        paper_name="Grade 7 Mathematics practice",
        mcq_count=question_count,
        written_count=0,
        structured_count=0,
        duration_minutes=45,
        difficulty=PaperDifficulty.BALANCED,
    )


def programme_mapping(
    identity: int,
    part: ScholarshipPaperMode,
    anchor: ResolvedTaxonomyTarget,
) -> ResolvedProgrammeMapping:
    return ResolvedProgrammeMapping(
        scope_id=UUID(int=26_000 + identity),
        part=part,
        ordinal=identity,
        anchor_lesson_id=next(
            item.id for item in curriculum().lessons if anchor in item.taxonomy_targets
        ),
        anchor_target=anchor.domain,
        retrieval_scope=RetrievalScope(
            grade=5,
            exam_id=UUID(int=27_000 + identity),
            medium_id=MEDIUM_ID,
            subject_id=UUID(int=28_000 + identity),
            curriculum_version_id=UUID(int=29_000 + identity),
            taxonomy=TaxonomyScope(competency_id=anchor.competency_id),
        ),
    )


def programme_scope(
    mode: ScholarshipPaperMode,
    mappings: tuple[ResolvedProgrammeMapping, ...],
) -> ResolvedPaperScope:
    selected = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )
    return replace(
        selected,
        kind=TeacherScopeKind.PROGRAMME,
        summary=f"Scholarship {mode.value}",
        programme=ResolvedProgrammeSelection(
            policy_id=UUID(int=26_500),
            policy_code="G5-SCHOLARSHIP",
            policy_version="2026.v1",
            content_hash="sha256:" + "e" * 64,
            mode=mode,
            paper_i_profile_version="ability.v1",
            paper_ii_profile_version="curriculum-coverage.v1",
            paper_i_weight=1,
            paper_ii_weight=1,
            mappings=mappings,
        ),
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


def test_selected_lesson_translation_keeps_only_exact_server_owned_lessons() -> None:
    resolved = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(
            kind=TeacherScopeKind.SELECTED_LESSONS,
            lesson_numbers=(1, 3),
        ),
    )

    assert [item.number for item in resolved.lessons] == [1, 3]
    assert resolved.lesson_ids == (LESSON_IDS[0], LESSON_IDS[2])
    assert resolved.unit_ids == (UNIT_ONE_ID, UNIT_TWO_ID)
    assert resolved.summary == "Lessons 1 and 3"


@pytest.mark.parametrize(
    ("lesson_numbers", "summary"),
    [((1,), "Lessons 1"), ((1, 2, 3), "Lessons 1, 2, and 3")],
)
def test_selected_lesson_summary_is_readable(
    lesson_numbers: tuple[int, ...],
    summary: str,
) -> None:
    resolved = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(
            kind=TeacherScopeKind.SELECTED_LESSONS,
            lesson_numbers=lesson_numbers,
        ),
    )

    assert resolved.summary == summary


@pytest.mark.parametrize(
    "lesson_numbers",
    [(), (0,), (1, 1), (1, 5), (2, 1), tuple(range(1, 102))],
)
def test_selected_lesson_translation_rejects_empty_duplicate_or_unknown_lessons(
    lesson_numbers: tuple[int, ...],
) -> None:
    with pytest.raises(PaperScopeError) as captured:
        translate_teacher_scope(
            curriculum(),
            TeacherScopeSelection(
                kind=TeacherScopeKind.SELECTED_LESSONS,
                lesson_numbers=lesson_numbers,
            ),
        )

    assert captured.value.code == "paper_generation_selected_lessons_invalid"


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
        paper_name="Grade 7 Mathematics practice",
        mcq_count=12,
        written_count=0,
        structured_count=0,
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


def test_scholarship_programme_resolution_preserves_paper_profiles_and_policy_scopes() -> None:
    def source_scope(grade: int, identity: int) -> RetrievalScope:
        return RetrievalScope(
            grade=grade,
            exam_id=UUID(int=30_000 + identity),
            medium_id=MEDIUM_ID,
            subject_id=UUID(int=31_000 + identity),
            curriculum_version_id=UUID(int=32_000 + identity),
            unit_ids=(UUID(int=33_000 + identity),),
            lesson_ids=(UUID(int=34_000 + identity),),
            taxonomy=TaxonomyScope(competency_id=UUID(int=35_000 + identity)),
        )

    paper_i = ProgrammePaperPart(
        mode=ScholarshipPaperMode.PAPER_I,
        profile_version="grade5-scholarship-ability.v1",
        retrieval_scopes=RetrievalScopeSet(
            policy_version="grade5-scholarship-paper-i-sources.v1",
            scopes=(source_scope(5, 1),),
        ),
    )
    paper_ii = ProgrammePaperPart(
        mode=ScholarshipPaperMode.PAPER_II,
        profile_version="grade5-scholarship-coverage.v1",
        retrieval_scopes=RetrievalScopeSet(
            policy_version="grade5-scholarship-paper-ii-sources.v1",
            scopes=(source_scope(3, 2), source_scope(4, 3), source_scope(5, 4)),
        ),
    )
    policy = ScholarshipProgrammePolicy(
        code="G5-SCHOLARSHIP",
        version="2026.v1",
        grade=5,
        medium_id=MEDIUM_ID,
        paper_i=paper_i,
        paper_ii=paper_ii,
    )

    resolved_i = resolve_scholarship_programme(policy, ScholarshipPaperMode.PAPER_I)
    resolved_ii = resolve_scholarship_programme(policy, ScholarshipPaperMode.PAPER_II)
    resolved_full = resolve_scholarship_programme(policy, ScholarshipPaperMode.FULL)

    assert resolved_i.parts == (paper_i,)
    assert resolved_ii.parts == (paper_ii,)
    assert resolved_full.parts == (paper_i, paper_ii)
    assert resolved_full.policy_version == "2026.v1"
    assert {scope.grade for scope in resolved_ii.parts[0].retrieval_scopes.scopes} == {3, 4, 5}

    invalid_parts: tuple[Callable[[], ProgrammePaperPart], ...] = (
        lambda: replace(paper_i, mode=ScholarshipPaperMode.FULL),
        lambda: replace(paper_i, profile_version=cast(str, 123)),
        lambda: replace(paper_i, profile_version=""),
        lambda: replace(paper_i, profile_version=" padded "),
        lambda: replace(paper_i, profile_version="x" * 129),
        lambda: replace(
            paper_i,
            retrieval_scopes=cast(RetrievalScopeSet, "not-scope-set"),
        ),
    )
    for build_part in invalid_parts:
        with pytest.raises(ValueError, match=r"part|profile|retrieval"):
            build_part()

    invalid_policies: tuple[Callable[[], ScholarshipProgrammePolicy], ...] = (
        lambda: replace(policy, code=cast(str, 123)),
        lambda: replace(policy, code=""),
        lambda: replace(policy, code=" padded "),
        lambda: replace(policy, version="x" * 129),
        lambda: replace(policy, grade=4),
        lambda: replace(policy, grade=True),
        lambda: replace(policy, medium_id=cast(UUID, "medium")),
        lambda: replace(policy, paper_i=cast(ProgrammePaperPart, "paper-i")),
        lambda: replace(policy, paper_i=paper_ii),
        lambda: replace(policy, paper_ii=paper_i),
        lambda: replace(policy, medium_id=UUID(int=99_999)),
    )
    for build_policy in invalid_policies:
        with pytest.raises(ValueError, match=r"must|paper|programme"):
            build_policy()

    with pytest.raises(ValueError, match="policy"):
        resolve_scholarship_programme(
            cast(ScholarshipProgrammePolicy, "policy"),
            ScholarshipPaperMode.PAPER_I,
        )
    with pytest.raises(ValueError, match="mode"):
        resolve_scholarship_programme(policy, cast(ScholarshipPaperMode, "mode"))


def test_scholarship_blueprint_deduplicates_anchors_and_splits_question_types() -> None:
    first_target = target(1)
    second_target = target(2)
    selected = programme_scope(
        ScholarshipPaperMode.FULL,
        (
            programme_mapping(1, ScholarshipPaperMode.PAPER_I, first_target),
            programme_mapping(2, ScholarshipPaperMode.PAPER_II, first_target),
            programme_mapping(3, ScholarshipPaperMode.PAPER_II, second_target),
        ),
    )
    mixed_settings = PaperSettings(
        paper_name="Full Grade 5 Scholarship practice",
        mcq_count=1,
        written_count=1,
        structured_count=0,
        duration_minutes=45,
        difficulty=PaperDifficulty.BALANCED,
    )

    specification = build_blueprint_specification(
        curriculum(),
        selected,
        mixed_settings,
        paper_reference="EGP-2500-0005",
        request_fingerprint="sha256:" + "f" * 64,
    )

    assert [requirement.target for requirement in specification.taxonomy_requirements] == [
        first_target.domain,
        second_target.domain,
    ]
    assert [(section.section_id, section.question_count) for section in specification.sections] == [
        ("paper_i-multiple_choice", 1),
        ("paper_ii-short_answer", 1),
    ]
    assert "Scholarship mode is full." in specification.generation_policy.instructions


def test_scholarship_blueprint_supports_single_part_and_exhausted_anchor_mappings() -> None:
    first_target = target(1)
    second_target = target(2)
    single_part = programme_scope(
        ScholarshipPaperMode.PAPER_I,
        (programme_mapping(1, ScholarshipPaperMode.PAPER_I, first_target),),
    )

    paper_i_specification = build_blueprint_specification(
        curriculum(),
        single_part,
        settings(1),
        paper_reference="EGP-2500-0006",
        request_fingerprint="sha256:" + "1" * 64,
    )

    assert [section.section_id for section in paper_i_specification.sections] == [
        "paper_i-multiple_choice"
    ]
    assert paper_i_specification.sections[0].retrieval_query_hints == (
        single_part.summary,
        "ability.v1",
    )

    full_scope = programme_scope(
        ScholarshipPaperMode.FULL,
        (
            programme_mapping(1, ScholarshipPaperMode.PAPER_I, first_target),
            programme_mapping(2, ScholarshipPaperMode.PAPER_II, first_target),
            programme_mapping(3, ScholarshipPaperMode.PAPER_II, second_target),
        ),
    )
    exhausted_specification = build_blueprint_specification(
        curriculum(),
        full_scope,
        settings(3),
        paper_reference="EGP-2500-0007",
        request_fingerprint="sha256:" + "2" * 64,
    )

    assert len(exhausted_specification.taxonomy_requirements) == 2
    assert exhausted_specification.taxonomy_requirements[0].maximum_slots == 2
    assert [section.question_count for section in exhausted_specification.sections] == [1, 2]


def test_full_scholarship_blueprint_rejects_a_single_question() -> None:
    selected = programme_scope(
        ScholarshipPaperMode.FULL,
        (
            programme_mapping(1, ScholarshipPaperMode.PAPER_I, target(1)),
            programme_mapping(2, ScholarshipPaperMode.PAPER_II, target(2)),
        ),
    )

    with pytest.raises(PaperScopeError) as captured:
        build_blueprint_specification(
            curriculum(),
            selected,
            settings(1),
            paper_reference="EGP-2500-0008",
            request_fingerprint="sha256:" + "3" * 64,
        )

    assert captured.value.code == "paper_generation_programme_question_count_invalid"


def test_scholarship_blueprint_fails_closed_if_section_mappings_disappear() -> None:
    mapping = programme_mapping(1, ScholarshipPaperMode.PAPER_I, target(1))

    class DisappearingMappings:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self) -> Iterator[ResolvedProgrammeMapping]:
            self.iterations += 1
            if self.iterations <= 3:
                yield mapping

    selected = programme_scope(ScholarshipPaperMode.PAPER_I, (mapping,))
    assert selected.programme is not None
    selected = replace(
        selected,
        programme=replace(
            selected.programme,
            mappings=cast(tuple[ResolvedProgrammeMapping, ...], DisappearingMappings()),
        ),
    )

    with pytest.raises(PaperScopeError) as captured:
        build_blueprint_specification(
            curriculum(),
            selected,
            settings(1),
            paper_reference="EGP-2500-0009",
            request_fingerprint="sha256:" + "4" * 64,
        )

    assert captured.value.code == "paper_generation_programme_question_count_invalid"


def test_teacher_question_counts_build_a_mixed_blueprint_without_teacher_entered_marks() -> None:
    selected = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )
    settings = PaperSettings(
        paper_name="Grade 5 mixed practice",
        mcq_count=2,
        written_count=1,
        structured_count=1,
        duration_minutes=50,
        difficulty=PaperDifficulty.BALANCED,
        teacher_instruction="Keep every question concise.",
    )

    specification = build_blueprint_specification(
        curriculum(),
        selected,
        settings,
        paper_reference="EGP-2500-0004",
        request_fingerprint="sha256:" + "d" * 64,
    )
    blueprint = generate_blueprint(specification, seed=23)

    assert blueprint.title == "Grade 5 mixed practice"
    assert len(blueprint.slots) == 4
    assert Counter(slot.question_type.value for slot in blueprint.slots) == {
        "multiple_choice": 2,
        "short_answer": 1,
        "structured": 1,
    }
    assert specification.config_version == "teacher-paper-settings.v2"
    assert "Keep every question concise." in specification.generation_policy.instructions


def test_slot_assignment_never_falls_back_to_a_lesson_without_exact_taxonomy() -> None:
    selected = translate_teacher_scope(
        curriculum(),
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )
    selected_settings = settings()
    specification = build_blueprint_specification(
        curriculum(),
        selected,
        selected_settings,
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
    builders: tuple[Callable[[], PaperSettings], ...] = (
        lambda: replace(settings(), paper_name=""),
        lambda: replace(settings(), mcq_count=-1),
        lambda: replace(settings(), mcq_count=True),
        lambda: replace(settings(), mcq_count=0, written_count=0, structured_count=0),
        lambda: replace(settings(), duration_minutes=0),
        lambda: replace(settings(), duration_minutes=True),
        lambda: replace(settings(), difficulty=cast(PaperDifficulty, "balanced")),
        lambda: replace(settings(), teacher_instruction=" padded "),
    )
    for build in builders:
        with pytest.raises(ValueError, match="must be"):
            build()

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
            settings(1),
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
