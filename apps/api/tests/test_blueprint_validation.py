from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

import pytest

import exam_guru_api.blueprints.generator as generator_module
from exam_guru_api.blueprints import (
    BlueprintSection,
    BlueprintSlot,
    BlueprintSpecification,
    BlueprintValidationError,
    CurriculumScope,
    DeterministicBlueprintGenerator,
    Difficulty,
    DifficultyAllocation,
    ImpossibleBlueprintError,
    PaperBlueprint,
    PracticePriority,
    QuestionType,
    QuestionTypeAllocation,
    SectionSpecification,
    TaxonomyRequirement,
    TaxonomyTarget,
    UniquenessPolicy,
    Violation,
    generate_blueprint,
)
from tests.test_blueprint_domain import (
    COMPETENCY_A,
    COMPETENCY_B,
    CURRICULUM_VERSION_ID,
    SKILL_A,
    SKILL_B,
    baseline_priority,
    generation_policy,
    make_specification,
    make_uniform_specification,
    target,
)


def complete_forecast() -> PracticePriority:
    return PracticePriority(
        baseline_score=100,
        baseline_version="baseline-v1",
        baseline_evidence_refs=("baseline:evidence",),
        forecast_score=200,
        forecast_version="forecast-v1",
        baseline_backtest_score=500,
        forecast_backtest_score=550,
        minimum_backtest_improvement=10,
        forecast_evidence_refs=("forecast:evidence",),
    )


def section() -> SectionSpecification:
    return SectionSpecification("S", "Section", 2, 1, (2,))


def requirement() -> TaxonomyRequirement:
    return TaxonomyRequirement(
        target=target(COMPETENCY_A, SKILL_A),
        minimum_slots=1,
        maximum_slots=1,
        priority=baseline_priority("validation"),
        retrieval_query_hints=("reviewed concept",),
        generation_instructions=("Generate a bounded question.",),
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CurriculumScope(CURRICULUM_VERSION_ID, 0, "en"),
        lambda: CurriculumScope(CURRICULUM_VERSION_ID, 5, " "),
        lambda: TaxonomyTarget(COMPETENCY_A, sub_skill_id=UUID(int=10)),
        lambda: TaxonomyTarget(
            COMPETENCY_A,
            skill_id=SKILL_A,
            learning_concept_id=UUID(int=11),
        ),
        lambda: replace(baseline_priority("x"), baseline_score=0),
        lambda: replace(baseline_priority("x"), minimum_backtest_improvement=0),
        lambda: replace(
            baseline_priority("x"),
            forecast_evidence_refs=("forecast:without-signal",),
        ),
        lambda: replace(complete_forecast(), forecast_score=0),
        lambda: replace(complete_forecast(), baseline_backtest_score=-1),
        lambda: replace(complete_forecast(), forecast_backtest_score=-1),
        lambda: replace(complete_forecast(), forecast_version=" "),
        lambda: replace(complete_forecast(), forecast_evidence_refs=()),
        lambda: replace(section(), marks=0),
        lambda: replace(section(), question_count=0),
        lambda: replace(section(), allowed_marks_per_slot=()),
        lambda: replace(section(), allowed_marks_per_slot=(0,)),
        lambda: replace(section(), allowed_marks_per_slot=(1, 1)),
        lambda: replace(section(), allowed_question_types=()),
        lambda: replace(
            section(),
            allowed_question_types=(
                QuestionType.MULTIPLE_CHOICE,
                QuestionType.MULTIPLE_CHOICE,
            ),
        ),
        lambda: replace(section(), allowed_difficulties=()),
        lambda: replace(
            section(),
            allowed_difficulties=(Difficulty.EASY, Difficulty.EASY),
        ),
        lambda: replace(
            section(),
            allowed_taxonomy_targets=(requirement().target, requirement().target),
        ),
        lambda: replace(section(), retrieval_query_hints=("same", "same")),
        lambda: QuestionTypeAllocation(QuestionType.MULTIPLE_CHOICE, 0, ("mcq",)),
        lambda: QuestionTypeAllocation(QuestionType.MULTIPLE_CHOICE, 2, ("mcq",), 1),
        lambda: QuestionTypeAllocation(QuestionType.MULTIPLE_CHOICE, 1, ()),
        lambda: DifficultyAllocation(Difficulty.EASY, 0),
        lambda: DifficultyAllocation(Difficulty.EASY, 2, 1),
        lambda: replace(requirement(), minimum_slots=0),
        lambda: replace(requirement(), minimum_slots=2, maximum_slots=1),
        lambda: replace(requirement(), retrieval_query_hints=()),
        lambda: replace(requirement(), generation_instructions=()),
        lambda: replace(requirement(), allowed_section_ids=("S", "S")),
        lambda: UniquenessPolicy(max_similarity_basis_points=-1),
        lambda: UniquenessPolicy(max_similarity_basis_points=10_000),
        lambda: UniquenessPolicy(minimum_distinct_contexts=0),
        lambda: replace(generation_policy(), instructions=()),
        lambda: replace(generation_policy(), answer_requirements=("same", "same")),
    ],
)
def test_invalid_leaf_domain_values_are_rejected(factory: Callable[[], object]) -> None:
    with pytest.raises(BlueprintValidationError):
        factory()


@pytest.mark.parametrize(
    ("violation", "mutate"),
    [
        (Violation.INVALID_VALUE, lambda spec: replace(spec, total_marks=0)),
        (Violation.INVALID_VALUE, lambda spec: replace(spec, sections=())),
        (
            Violation.INVALID_VALUE,
            lambda spec: replace(spec, question_type_allocations=()),
        ),
        (
            Violation.INVALID_VALUE,
            lambda spec: replace(spec, difficulty_allocations=()),
        ),
        (
            Violation.INVALID_VALUE,
            lambda spec: replace(spec, taxonomy_requirements=()),
        ),
        (
            Violation.DUPLICATE_SECTION_ID,
            lambda spec: replace(
                spec,
                sections=(
                    spec.sections[0],
                    replace(spec.sections[1], section_id=spec.sections[0].section_id),
                ),
            ),
        ),
        (
            Violation.DUPLICATE_ALLOCATION,
            lambda spec: replace(
                spec,
                question_type_allocations=(
                    spec.question_type_allocations[0],
                    replace(
                        spec.question_type_allocations[1],
                        question_type=spec.question_type_allocations[0].question_type,
                    ),
                    spec.question_type_allocations[2],
                ),
            ),
        ),
        (
            Violation.DUPLICATE_ALLOCATION,
            lambda spec: replace(
                spec,
                difficulty_allocations=(
                    spec.difficulty_allocations[0],
                    replace(
                        spec.difficulty_allocations[1],
                        difficulty=spec.difficulty_allocations[0].difficulty,
                    ),
                    spec.difficulty_allocations[2],
                ),
            ),
        ),
        (
            Violation.DUPLICATE_TAXONOMY_TARGET,
            lambda spec: replace(
                spec,
                taxonomy_requirements=(
                    spec.taxonomy_requirements[0],
                    replace(
                        spec.taxonomy_requirements[1],
                        target=spec.taxonomy_requirements[0].target,
                    ),
                    spec.taxonomy_requirements[2],
                ),
            ),
        ),
        (
            Violation.UNKNOWN_SECTION,
            lambda spec: replace(
                spec,
                taxonomy_requirements=(
                    replace(spec.taxonomy_requirements[0], allowed_section_ids=("missing",)),
                    *spec.taxonomy_requirements[1:],
                ),
            ),
        ),
        (
            Violation.UNKNOWN_TAXONOMY_TARGET,
            lambda spec: replace(
                spec,
                sections=(
                    replace(
                        spec.sections[0],
                        allowed_taxonomy_targets=(TaxonomyTarget(UUID(int=999)),),
                    ),
                    spec.sections[1],
                ),
            ),
        ),
    ],
)
def test_invalid_specification_shapes_are_rejected(
    violation: Violation,
    mutate: Callable[[object], object],
) -> None:
    with pytest.raises(BlueprintValidationError) as raised:
        mutate(make_specification())
    assert raised.value.violation is violation


def test_version_slot_and_output_leaf_guards_are_enforced() -> None:
    blueprint = generate_blueprint(make_specification(), seed=8)
    slot = next(item for item in blueprint.slots if item.evidence.forecast_score is not None)

    invalid_factories: tuple[Callable[[], object], ...] = (
        lambda: replace(blueprint.version, input_fingerprint="short"),
        lambda: replace(blueprint.version, input_fingerprint="g" * 64),
        lambda: replace(slot.rationale, effective_priority_score=0),
        lambda: replace(slot.evidence, baseline_score=0),
        lambda: replace(slot.evidence, forecast_score=None),
        lambda: replace(slot.evidence, minimum_backtest_improvement=0),
        lambda: replace(slot.generation_constraints, exact_marks=0),
        lambda: replace(slot, ordinal=0),
        lambda: replace(slot, section_ordinal=0),
        lambda: replace(slot, marks=0),
        lambda: replace(
            slot,
            generation_constraints=replace(
                slot.generation_constraints,
                required_archetype="different",
            ),
        ),
        lambda: BlueprintSection("S", "Section", 0, 1),
        lambda: BlueprintSection("S", "Section", 1, 0),
    )

    for factory in invalid_factories:
        with pytest.raises(BlueprintValidationError):
            factory()


def replace_first_slot(
    blueprint: PaperBlueprint,
    **changes: object,
) -> tuple[BlueprintSlot, ...]:
    return (
        replace(blueprint.slots[0], **changes),  # type: ignore[arg-type]
        *blueprint.slots[1:],
    )


def with_changed_scope(blueprint: PaperBlueprint) -> PaperBlueprint:
    changed_scope = replace(blueprint.curriculum_scope, medium="ta")
    changed_constraints = replace(
        blueprint.slots[0].generation_constraints,
        curriculum_scope=changed_scope,
    )
    changed_slot = replace(blueprint.slots[0], generation_constraints=changed_constraints)
    return replace(blueprint, slots=(changed_slot, *blueprint.slots[1:]))


def with_taxonomy_bound_mismatch(blueprint: PaperBlueprint) -> PaperBlueprint:
    counts = Counter(slot.taxonomy_target for slot in blueprint.slots)
    first = blueprint.taxonomy_requirements[0]
    required = counts[first.target] + 1
    changed = replace(first, minimum_slots=required, maximum_slots=required)
    return replace(
        blueprint,
        taxonomy_requirements=(changed, *blueprint.taxonomy_requirements[1:]),
    )


@pytest.mark.parametrize(
    ("violation", "mutate"),
    [
        (Violation.INVALID_VALUE, lambda bp: replace(bp, seed=True)),
        (Violation.INVALID_VALUE, lambda bp: replace(bp, seed="bad")),
        (Violation.INVALID_VALUE, lambda bp: replace(bp, total_marks=0)),
        (Violation.INVALID_VALUE, lambda bp: replace(bp, sections=())),
        (Violation.INVALID_VALUE, lambda bp: replace(bp, slots=())),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                slots=replace_first_slot(bp, ordinal=2),
            ),
        ),
        (
            Violation.DUPLICATE_SECTION_ID,
            lambda bp: replace(
                bp,
                sections=(
                    bp.sections[0],
                    replace(bp.sections[1], section_id=bp.sections[0].section_id),
                ),
            ),
        ),
        (
            Violation.TOTAL_MARKS_MISMATCH,
            lambda bp: replace(
                bp,
                sections=(
                    replace(bp.sections[0], marks=bp.sections[0].marks + 1),
                    *bp.sections[1:],
                ),
            ),
        ),
        (
            Violation.UNKNOWN_SECTION,
            lambda bp: replace(
                bp,
                slots=replace_first_slot(bp, section_id="missing", section_title="Missing"),
            ),
        ),
        (
            Violation.SLOT_METADATA_MISMATCH,
            lambda bp: replace(
                bp,
                slots=replace_first_slot(bp, paper_code="OTHER"),
            ),
        ),
        (Violation.SLOT_METADATA_MISMATCH, with_changed_scope),
        (
            Violation.SLOT_METADATA_MISMATCH,
            lambda bp: replace(
                bp,
                slots=replace_first_slot(
                    bp,
                    evidence=replace(bp.slots[0].evidence, config_version="other-v1"),
                ),
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                sections=(
                    replace(bp.sections[0], slot_count=bp.sections[0].slot_count + 1),
                    *bp.sections[1:],
                ),
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                question_type_allocations=bp.question_type_allocations[1:],
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                difficulty_allocations=bp.difficulty_allocations[1:],
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                question_type_allocations=(
                    replace(bp.question_type_allocations[0], exact_slots=2),
                    replace(bp.question_type_allocations[1], exact_slots=3),
                    bp.question_type_allocations[2],
                ),
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                question_type_allocations=(
                    replace(
                        bp.question_type_allocations[0],
                        exact_marks=bp.question_type_allocations[0].exact_marks + 1,
                    ),
                    *bp.question_type_allocations[1:],
                ),
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                difficulty_allocations=(
                    replace(bp.difficulty_allocations[0], exact_slots=3),
                    replace(bp.difficulty_allocations[1], exact_slots=2),
                    bp.difficulty_allocations[2],
                ),
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(
                bp,
                difficulty_allocations=(
                    replace(
                        bp.difficulty_allocations[0],
                        exact_marks=bp.difficulty_allocations[0].exact_marks + 1,
                    ),
                    *bp.difficulty_allocations[1:],
                ),
            ),
        ),
        (
            Violation.SLOT_ALLOCATION_MISMATCH,
            lambda bp: replace(bp, taxonomy_requirements=bp.taxonomy_requirements[1:]),
        ),
        (Violation.SLOT_ALLOCATION_MISMATCH, with_taxonomy_bound_mismatch),
    ],
)
def test_materialized_blueprint_rejects_broken_invariants(
    violation: Violation,
    mutate: Callable[[PaperBlueprint], object],
) -> None:
    blueprint = generate_blueprint(make_specification(), seed=12)
    with pytest.raises(BlueprintValidationError) as raised:
        mutate(blueprint)
    assert raised.value.violation is violation


def test_generator_class_invalid_seed_and_private_canonical_boundary() -> None:
    specification = make_uniform_specification((1,), 1)
    generated = DeterministicBlueprintGenerator().generate(specification, seed=4)
    assert generated == generate_blueprint(specification, seed=4)

    with pytest.raises(BlueprintValidationError):
        generate_blueprint(specification, seed=True)
    with pytest.raises(TypeError):
        generator_module._canonicalize(object())


def test_allocations_without_mark_targets_and_duplicate_hints_are_supported() -> None:
    specification = make_specification()
    shared_hint = specification.generation_policy.retrieval_query_hints[0]
    changed = replace(
        specification,
        question_type_allocations=tuple(
            replace(allocation, exact_marks=None)
            for allocation in specification.question_type_allocations
        ),
        difficulty_allocations=tuple(
            replace(allocation, exact_marks=None)
            for allocation in specification.difficulty_allocations
        ),
        taxonomy_requirements=(
            replace(specification.taxonomy_requirements[0], retrieval_query_hints=(shared_hint,)),
            *specification.taxonomy_requirements[1:],
        ),
    )

    blueprint = generate_blueprint(changed, seed=2)

    assert blueprint.slots
    for slot in blueprint.slots:
        assert len(slot.generation_constraints.retrieval_query_hints) == len(
            set(slot.generation_constraints.retrieval_query_hints)
        )


def test_section_mark_composition_detects_non_representable_interior_total() -> None:
    specification = make_uniform_specification((2,), 2)
    impossible = replace(
        specification,
        total_marks=5,
        sections=(replace(specification.sections[0], marks=5, allowed_marks_per_slot=(2, 4)),),
        question_type_allocations=(
            replace(specification.question_type_allocations[0], exact_marks=5),
        ),
        difficulty_allocations=(replace(specification.difficulty_allocations[0], exact_marks=5),),
    )

    with pytest.raises(ImpossibleBlueprintError) as raised:
        generate_blueprint(impossible)
    assert raised.value.violation is Violation.SECTION_MARKS_IMPOSSIBLE


def test_exact_allocation_pruning_reports_distinct_conflicts() -> None:
    specification = make_specification()
    no_mark_targets = tuple(
        replace(allocation, exact_marks=None)
        for allocation in specification.question_type_allocations
    )
    too_few_eligible = replace(
        specification,
        sections=(
            replace(
                specification.sections[0],
                allowed_question_types=(
                    QuestionType.MULTIPLE_CHOICE,
                    QuestionType.STRUCTURED,
                ),
            ),
            specification.sections[1],
        ),
        question_type_allocations=(
            replace(no_mark_targets[0], exact_slots=2),
            replace(no_mark_targets[1], exact_slots=3),
            no_mark_targets[2],
        ),
    )
    impossible_marks = replace(
        specification,
        question_type_allocations=(
            replace(specification.question_type_allocations[0], exact_marks=20),
            *specification.question_type_allocations[1:],
        ),
    )
    leftover_marks = replace(
        specification,
        question_type_allocations=(
            replace(specification.question_type_allocations[0], exact_marks=9),
            *specification.question_type_allocations[1:],
        ),
    )

    for impossible in (too_few_eligible, impossible_marks, leftover_marks):
        with pytest.raises(ImpossibleBlueprintError) as raised:
            generate_blueprint(impossible, seed=5)
        assert raised.value.violation is Violation.QUESTION_TYPE_ALLOCATION_IMPOSSIBLE


def test_incompatible_exact_mark_targets_are_rejected() -> None:
    specification = make_uniform_specification((3,), 2)
    impossible = replace(
        specification,
        total_marks=10,
        sections=(
            replace(
                specification.sections[0],
                marks=10,
                allowed_marks_per_slot=(2, 4),
            ),
        ),
        question_type_allocations=(
            QuestionTypeAllocation(
                QuestionType.MULTIPLE_CHOICE,
                2,
                ("multiple_choice",),
                7,
            ),
            QuestionTypeAllocation(
                QuestionType.SHORT_ANSWER,
                1,
                ("short_answer",),
                4,
            ),
        ),
        difficulty_allocations=(DifficultyAllocation(Difficulty.MEDIUM, 3, 10),),
    )

    with pytest.raises(ImpossibleBlueprintError) as raised:
        generate_blueprint(impossible, seed=0)
    assert raised.value.violation is Violation.QUESTION_TYPE_ALLOCATION_IMPOSSIBLE


def five_slot_priority_specification() -> BlueprintSpecification:
    specification = make_uniform_specification((5,), 1)
    base_requirement = specification.taxonomy_requirements[0]
    target_a = target(COMPETENCY_A, SKILL_A)
    target_b = target(COMPETENCY_B, SKILL_B)
    target_c = TaxonomyTarget(UUID(int=703), UUID(int=704))
    return replace(
        specification,
        taxonomy_requirements=(
            replace(
                base_requirement,
                target=target_a,
                minimum_slots=1,
                maximum_slots=5,
                priority=baseline_priority("high", 10_000),
            ),
            replace(
                base_requirement,
                target=target_b,
                minimum_slots=2,
                maximum_slots=5,
                priority=baseline_priority("b"),
            ),
            replace(
                base_requirement,
                target=target_c,
                minimum_slots=2,
                maximum_slots=5,
                priority=baseline_priority("c"),
            ),
        ),
    )


def test_taxonomy_solver_backtracks_to_preserve_all_minimums() -> None:
    specification = five_slot_priority_specification()
    blueprint = generate_blueprint(specification, seed=1)
    counts = Counter(slot.taxonomy_target for slot in blueprint.slots)
    assert sorted(counts.values()) == [1, 2, 2]


def test_taxonomy_scope_capacity_and_minimum_conflicts_fail() -> None:
    specification = make_specification()
    minimum_conflict = replace(
        specification,
        taxonomy_requirements=(
            replace(
                specification.taxonomy_requirements[0],
                minimum_slots=3,
                maximum_slots=3,
                allowed_section_ids=("B",),
            ),
            *specification.taxonomy_requirements[1:],
        ),
    )
    target_a, target_b, target_c = (item.target for item in specification.taxonomy_requirements)
    capacity_conflict = replace(
        specification,
        sections=(
            replace(
                specification.sections[0],
                allowed_taxonomy_targets=(target_b, target_c),
            ),
            replace(specification.sections[1], allowed_taxonomy_targets=(target_a,)),
        ),
        taxonomy_requirements=(
            replace(specification.taxonomy_requirements[0], maximum_slots=3),
            replace(specification.taxonomy_requirements[1], maximum_slots=1),
            replace(specification.taxonomy_requirements[2], maximum_slots=2),
        ),
    )

    for impossible in (minimum_conflict, capacity_conflict):
        with pytest.raises(ImpossibleBlueprintError) as raised:
            generate_blueprint(impossible, seed=6)
        assert raised.value.violation is Violation.TAXONOMY_COVERAGE_IMPOSSIBLE
