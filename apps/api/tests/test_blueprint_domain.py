from collections import Counter
from dataclasses import replace
from uuid import UUID

import pytest

from exam_guru_api.blueprints import (
    ALGORITHM_VERSION,
    SCHEMA_VERSION,
    BlueprintSpecification,
    BlueprintValidationError,
    CurriculumScope,
    Difficulty,
    DifficultyAllocation,
    GenerationPolicy,
    ImpossibleBlueprintError,
    PracticePriority,
    PriorityMode,
    QuestionType,
    QuestionTypeAllocation,
    SectionSpecification,
    TaxonomyRequirement,
    TaxonomyTarget,
    UniquenessPolicy,
    Violation,
    generate_blueprint,
)

CURRICULUM_VERSION_ID = UUID("10000000-0000-0000-0000-000000000001")
COMPETENCY_A = UUID("20000000-0000-0000-0000-000000000001")
COMPETENCY_B = UUID("20000000-0000-0000-0000-000000000002")
COMPETENCY_C = UUID("20000000-0000-0000-0000-000000000003")
SKILL_A = UUID("30000000-0000-0000-0000-000000000001")
SKILL_B = UUID("30000000-0000-0000-0000-000000000002")
SKILL_C = UUID("30000000-0000-0000-0000-000000000003")


def target(competency_id: UUID, skill_id: UUID) -> TaxonomyTarget:
    return TaxonomyTarget(competency_id=competency_id, skill_id=skill_id)


def baseline_priority(label: str, score: int = 100) -> PracticePriority:
    return PracticePriority(
        baseline_score=score,
        baseline_version="syllabus-balanced-v1",
        baseline_evidence_refs=(f"curriculum:{label}",),
    )


def generation_policy() -> GenerationPolicy:
    return GenerationPolicy(
        response_language="si",
        instructions=(
            "Use age-appropriate Grade 5 language.",
            "Use only reviewed curriculum evidence.",
        ),
        answer_requirements=(
            "Provide an unambiguous answer.",
            "Provide marking guidance matching the exact marks.",
        ),
        retrieval_query_hints=("Grade 5 scholarship curriculum",),
        uniqueness=UniquenessPolicy(
            forbid_duplicate_stems=True,
            forbid_verbatim_sources=True,
            max_similarity_basis_points=8500,
            minimum_distinct_contexts=1,
        ),
    )


def make_specification() -> BlueprintSpecification:
    taxonomy_a = target(COMPETENCY_A, SKILL_A)
    taxonomy_b = target(COMPETENCY_B, SKILL_B)
    taxonomy_c = target(COMPETENCY_C, SKILL_C)
    return BlueprintSpecification(
        config_version="grade5-scholarship-blueprint-v1",
        paper_code="G5-PRACTICE-01",
        title="Grade 5 Scholarship Practice Paper",
        total_marks=20,
        curriculum_scope=CurriculumScope(
            curriculum_version_id=CURRICULUM_VERSION_ID,
            grade=5,
            medium="si",
        ),
        sections=(
            SectionSpecification(
                section_id="A",
                title="Selection",
                marks=12,
                question_count=4,
                allowed_marks_per_slot=(2, 4),
                retrieval_query_hints=("selection section",),
            ),
            SectionSpecification(
                section_id="B",
                title="Constructed response",
                marks=8,
                question_count=2,
                allowed_marks_per_slot=(4,),
                retrieval_query_hints=("constructed response section",),
            ),
        ),
        question_type_allocations=(
            QuestionTypeAllocation(
                question_type=QuestionType.MULTIPLE_CHOICE,
                exact_slots=3,
                exact_marks=8,
                archetypes=("single_best_answer", "classification"),
            ),
            QuestionTypeAllocation(
                question_type=QuestionType.SHORT_ANSWER,
                exact_slots=2,
                exact_marks=8,
                archetypes=("direct_response",),
            ),
            QuestionTypeAllocation(
                question_type=QuestionType.STRUCTURED,
                exact_slots=1,
                exact_marks=4,
                archetypes=("multi_step_reasoning",),
            ),
        ),
        difficulty_allocations=(
            DifficultyAllocation(
                difficulty=Difficulty.EASY,
                exact_slots=2,
                exact_marks=4,
            ),
            DifficultyAllocation(
                difficulty=Difficulty.MEDIUM,
                exact_slots=3,
                exact_marks=12,
            ),
            DifficultyAllocation(
                difficulty=Difficulty.HARD,
                exact_slots=1,
                exact_marks=4,
            ),
        ),
        taxonomy_requirements=(
            TaxonomyRequirement(
                target=taxonomy_a,
                minimum_slots=1,
                maximum_slots=3,
                priority=PracticePriority(
                    baseline_score=100,
                    baseline_version="syllabus-balanced-v1",
                    baseline_evidence_refs=("curriculum:competency-a",),
                    forecast_score=300,
                    forecast_version="forecast-v7",
                    baseline_backtest_score=600,
                    forecast_backtest_score=650,
                    minimum_backtest_improvement=25,
                    forecast_evidence_refs=("backtest:forecast-v7:a",),
                ),
                retrieval_query_hints=("competency A reviewed concepts",),
                generation_instructions=("Vary the context used for competency A.",),
            ),
            TaxonomyRequirement(
                target=taxonomy_b,
                minimum_slots=1,
                maximum_slots=3,
                priority=PracticePriority(
                    baseline_score=100,
                    baseline_version="syllabus-balanced-v1",
                    baseline_evidence_refs=("curriculum:competency-b",),
                    forecast_score=900,
                    forecast_version="forecast-v7",
                    baseline_backtest_score=600,
                    forecast_backtest_score=610,
                    minimum_backtest_improvement=25,
                    forecast_evidence_refs=("backtest:forecast-v7:b",),
                ),
                retrieval_query_hints=("competency B reviewed concepts",),
                generation_instructions=("Avoid repeating competency B stems.",),
            ),
            TaxonomyRequirement(
                target=taxonomy_c,
                minimum_slots=1,
                maximum_slots=3,
                priority=baseline_priority("competency-c"),
                retrieval_query_hints=("competency C reviewed concepts",),
                generation_instructions=("Use a familiar Grade 5 context.",),
            ),
        ),
        generation_policy=generation_policy(),
    )


def test_blueprint_is_deterministic_versioned_and_exact() -> None:
    specification = make_specification()

    first = generate_blueprint(specification, seed=2025)
    repeated = generate_blueprint(specification, seed=2025)
    another_seed = generate_blueprint(specification, seed=2026)
    another_config = generate_blueprint(
        replace(specification, config_version="grade5-scholarship-blueprint-v2"),
        seed=2025,
    )

    assert first == repeated
    assert first.version.schema_version == SCHEMA_VERSION
    assert first.version.algorithm_version == ALGORITHM_VERSION
    assert first.version.config_version == specification.config_version
    assert len(first.version.input_fingerprint) == 64
    assert first.version.blueprint_id != another_seed.version.blueprint_id
    assert first.version.blueprint_id != another_config.version.blueprint_id

    assert sum(slot.marks for slot in first.slots) == specification.total_marks
    section_marks = Counter[str]()
    section_slots = Counter[str]()
    for slot in first.slots:
        section_marks[slot.section_id] += slot.marks
        section_slots[slot.section_id] += 1
    assert section_marks == {"A": 12, "B": 8}
    assert section_slots == {"A": 4, "B": 2}

    question_type_slots = Counter(slot.question_type for slot in first.slots)
    question_type_marks = Counter[QuestionType]()
    difficulty_slots = Counter(slot.difficulty for slot in first.slots)
    difficulty_marks = Counter[Difficulty]()
    for slot in first.slots:
        question_type_marks[slot.question_type] += slot.marks
        difficulty_marks[slot.difficulty] += slot.marks
    assert question_type_slots == {
        QuestionType.MULTIPLE_CHOICE: 3,
        QuestionType.SHORT_ANSWER: 2,
        QuestionType.STRUCTURED: 1,
    }
    assert question_type_marks == {
        QuestionType.MULTIPLE_CHOICE: 8,
        QuestionType.SHORT_ANSWER: 8,
        QuestionType.STRUCTURED: 4,
    }
    assert difficulty_slots == {
        Difficulty.EASY: 2,
        Difficulty.MEDIUM: 3,
        Difficulty.HARD: 1,
    }
    assert difficulty_marks == {
        Difficulty.EASY: 4,
        Difficulty.MEDIUM: 12,
        Difficulty.HARD: 4,
    }


def test_taxonomy_coverage_is_forecast_informed_but_baseline_safe() -> None:
    specification = make_specification()
    blueprint = generate_blueprint(specification, seed=19)
    taxonomy_counts = Counter(slot.taxonomy_target for slot in blueprint.slots)

    for requirement in specification.taxonomy_requirements:
        assert taxonomy_counts[requirement.target] >= requirement.minimum_slots
        assert requirement.maximum_slots is not None
        assert taxonomy_counts[requirement.target] <= requirement.maximum_slots

    target_a, target_b, target_c = (
        requirement.target for requirement in specification.taxonomy_requirements
    )
    assert taxonomy_counts[target_a] >= taxonomy_counts[target_b]
    assert taxonomy_counts[target_a] >= taxonomy_counts[target_c]

    rationale_modes = {
        slot.taxonomy_target: slot.rationale.priority_mode for slot in blueprint.slots
    }
    assert rationale_modes[target_a] is PriorityMode.FORECAST
    assert rationale_modes[target_b] is PriorityMode.BASELINE_FALLBACK
    assert rationale_modes[target_c] is PriorityMode.BASELINE_ONLY

    fallback_slots = [slot for slot in blueprint.slots if slot.taxonomy_target == target_b]
    assert fallback_slots
    assert all(slot.rationale.effective_priority_score == 100 for slot in fallback_slots)
    assert all(slot.evidence.forecast_score == 900 for slot in fallback_slots)
    assert all(slot.evidence.forecast_backtest_score == 610 for slot in fallback_slots)

    changed_unproven_forecast = replace(
        specification,
        taxonomy_requirements=(
            specification.taxonomy_requirements[0],
            replace(
                specification.taxonomy_requirements[1],
                priority=replace(
                    specification.taxonomy_requirements[1].priority,
                    forecast_score=50_000,
                ),
            ),
            specification.taxonomy_requirements[2],
        ),
    )
    changed_counts = Counter(
        slot.taxonomy_target
        for slot in generate_blueprint(changed_unproven_forecast, seed=19).slots
    )
    assert changed_counts == taxonomy_counts


def test_every_slot_is_unique_and_self_contained_for_generation() -> None:
    specification = make_specification()
    blueprint = generate_blueprint(specification, seed=7)

    assert len({slot.slot_id for slot in blueprint.slots}) == len(blueprint.slots)
    assert [slot.ordinal for slot in blueprint.slots] == list(range(1, len(blueprint.slots) + 1))

    for slot in blueprint.slots:
        constraints = slot.generation_constraints
        assert constraints.curriculum_scope == specification.curriculum_scope
        assert constraints.taxonomy_target == slot.taxonomy_target
        assert constraints.required_question_type is slot.question_type
        assert constraints.required_archetype == slot.archetype
        assert constraints.required_difficulty is slot.difficulty
        assert constraints.exact_marks == slot.marks
        assert constraints.instructions
        assert constraints.answer_requirements
        assert constraints.retrieval_query_hints
        assert constraints.uniqueness.forbid_duplicate_stems
        assert constraints.diversity_key
        assert slot.rationale.summary
        assert "prediction" not in slot.rationale.summary.casefold()
        assert slot.evidence.config_version == specification.config_version
        assert slot.evidence.baseline_version
        assert slot.evidence.evidence_refs

    duplicate = replace(blueprint.slots[1], slot_id=blueprint.slots[0].slot_id)
    with pytest.raises(BlueprintValidationError) as raised:
        replace(blueprint, slots=(blueprint.slots[0], duplicate, *blueprint.slots[2:]))
    assert raised.value.violation is Violation.DUPLICATE_SLOT_ID


@pytest.mark.parametrize(
    ("violation", "mutate"),
    [
        (
            Violation.TOTAL_MARKS_MISMATCH,
            lambda specification: replace(specification, total_marks=21),
        ),
        (
            Violation.SECTION_MARKS_IMPOSSIBLE,
            lambda specification: replace(
                specification,
                sections=(
                    replace(
                        specification.sections[0],
                        allowed_marks_per_slot=(4,),
                    ),
                    specification.sections[1],
                ),
            ),
        ),
        (
            Violation.QUESTION_TYPE_SLOT_MISMATCH,
            lambda specification: replace(
                specification,
                question_type_allocations=(
                    replace(specification.question_type_allocations[0], exact_slots=4),
                    *specification.question_type_allocations[1:],
                ),
            ),
        ),
        (
            Violation.DIFFICULTY_SLOT_MISMATCH,
            lambda specification: replace(
                specification,
                difficulty_allocations=(
                    replace(specification.difficulty_allocations[0], exact_slots=3),
                    *specification.difficulty_allocations[1:],
                ),
            ),
        ),
        (
            Violation.TAXONOMY_COVERAGE_IMPOSSIBLE,
            lambda specification: replace(
                specification,
                taxonomy_requirements=(
                    replace(
                        specification.taxonomy_requirements[0],
                        minimum_slots=5,
                        maximum_slots=5,
                    ),
                    *specification.taxonomy_requirements[1:],
                ),
            ),
        ),
        (
            Violation.QUESTION_TYPE_ALLOCATION_IMPOSSIBLE,
            lambda specification: replace(
                specification,
                sections=(
                    replace(
                        specification.sections[0],
                        allowed_question_types=(QuestionType.MULTIPLE_CHOICE,),
                    ),
                    specification.sections[1],
                ),
            ),
        ),
        (
            Violation.DIFFICULTY_ALLOCATION_IMPOSSIBLE,
            lambda specification: replace(
                specification,
                sections=(
                    replace(
                        specification.sections[0],
                        allowed_difficulties=(Difficulty.HARD,),
                    ),
                    specification.sections[1],
                ),
            ),
        ),
        (
            Violation.TAXONOMY_COVERAGE_IMPOSSIBLE,
            lambda specification: replace(
                specification,
                sections=(
                    replace(
                        specification.sections[0],
                        allowed_taxonomy_targets=(
                            specification.taxonomy_requirements[1].target,
                            specification.taxonomy_requirements[2].target,
                        ),
                    ),
                    replace(
                        specification.sections[1],
                        allowed_taxonomy_targets=(specification.taxonomy_requirements[0].target,),
                    ),
                ),
                taxonomy_requirements=(
                    replace(specification.taxonomy_requirements[0], maximum_slots=1),
                    *specification.taxonomy_requirements[1:],
                ),
            ),
        ),
    ],
)
def test_impossible_constraints_fail_clearly(
    violation: Violation,
    mutate: object,
) -> None:
    specification = make_specification()
    impossible = mutate(specification)  # type: ignore[operator]

    with pytest.raises(ImpossibleBlueprintError) as raised:
        generate_blueprint(impossible, seed=3)

    assert raised.value.violation is violation
    assert raised.value.constraint
    assert violation.value in str(raised.value)


def make_uniform_specification(
    section_question_counts: tuple[int, ...],
    marks_per_slot: int,
) -> BlueprintSpecification:
    total_slots = sum(section_question_counts)
    total_marks = total_slots * marks_per_slot
    taxonomy = target(COMPETENCY_A, SKILL_A)
    return BlueprintSpecification(
        config_version=f"boundary-{section_question_counts}-{marks_per_slot}",
        paper_code="BOUNDARY",
        title="Boundary paper",
        total_marks=total_marks,
        curriculum_scope=CurriculumScope(CURRICULUM_VERSION_ID, 5, "en"),
        sections=tuple(
            SectionSpecification(
                section_id=f"S{index}",
                title=f"Section {index}",
                marks=count * marks_per_slot,
                question_count=count,
                allowed_marks_per_slot=(marks_per_slot,),
            )
            for index, count in enumerate(section_question_counts, start=1)
        ),
        question_type_allocations=(
            QuestionTypeAllocation(
                question_type=QuestionType.MULTIPLE_CHOICE,
                exact_slots=total_slots,
                exact_marks=total_marks,
                archetypes=("single_best_answer",),
            ),
        ),
        difficulty_allocations=(
            DifficultyAllocation(
                difficulty=Difficulty.MEDIUM,
                exact_slots=total_slots,
                exact_marks=total_marks,
            ),
        ),
        taxonomy_requirements=(
            TaxonomyRequirement(
                target=taxonomy,
                minimum_slots=1,
                maximum_slots=total_slots,
                priority=baseline_priority("boundary"),
                retrieval_query_hints=("boundary curriculum concept",),
                generation_instructions=("Generate one bounded item per slot.",),
            ),
        ),
        generation_policy=generation_policy(),
    )


@pytest.mark.parametrize("section_question_counts", [(1,), (1, 1), (2, 3), (4, 1, 2)])
@pytest.mark.parametrize("marks_per_slot", [1, 2, 5])
@pytest.mark.parametrize("seed", [0, -1, 2**63 - 1])
def test_boundary_property_exactness_for_valid_shapes(
    section_question_counts: tuple[int, ...],
    marks_per_slot: int,
    seed: int,
) -> None:
    specification = make_uniform_specification(section_question_counts, marks_per_slot)

    blueprint = generate_blueprint(specification, seed=seed)

    assert len(blueprint.slots) == sum(section_question_counts)
    assert sum(slot.marks for slot in blueprint.slots) == specification.total_marks
    assert all(slot.marks == marks_per_slot for slot in blueprint.slots)
    for section in specification.sections:
        slots = [slot for slot in blueprint.slots if slot.section_id == section.section_id]
        assert len(slots) == section.question_count
        assert sum(slot.marks for slot in slots) == section.marks
    assert generate_blueprint(specification, seed=seed) == blueprint


def test_incomplete_forecast_evidence_is_rejected_at_the_domain_boundary() -> None:
    with pytest.raises(BlueprintValidationError) as raised:
        PracticePriority(
            baseline_score=100,
            baseline_version="baseline-v1",
            baseline_evidence_refs=("curriculum:c1",),
            forecast_score=200,
            forecast_version="forecast-v1",
        )

    assert raised.value.violation is Violation.INVALID_PRIORITY_EVIDENCE
