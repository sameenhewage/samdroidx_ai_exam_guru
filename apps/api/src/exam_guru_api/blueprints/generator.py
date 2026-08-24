import hashlib
import json
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from fractions import Fraction
from functools import cache
from typing import Any, Never, cast
from uuid import UUID

from .domain import (
    BlueprintSection,
    BlueprintSlot,
    BlueprintSpecification,
    BlueprintValidationError,
    BlueprintVersion,
    ImpossibleBlueprintError,
    PaperBlueprint,
    PracticePriority,
    PriorityMode,
    QuestionType,
    SectionSpecification,
    SlotEvidence,
    SlotGenerationConstraints,
    SlotRationale,
    TaxonomyRequirement,
    Violation,
)

SCHEMA_VERSION = "1"
ALGORITHM_VERSION = "deterministic-paper-blueprint-v1"

_JsonScalar = str | int | bool | None
_JsonValue = _JsonScalar | list["_JsonValue"] | dict[str, "_JsonValue"]


@dataclass(frozen=True, slots=True)
class _SlotDraft:
    index: int
    section: SectionSpecification
    section_ordinal: int
    marks: int


def _canonicalize(value: object) -> _JsonValue:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return cast(str, value.value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        dataclass_value = cast(Any, value)
        return {
            field.name: _canonicalize(getattr(dataclass_value, field.name))
            for field in fields(dataclass_value)
        }
    raise TypeError(f"cannot canonicalize blueprint value of type {type(value).__name__}")


def _specification_fingerprint(specification: BlueprintSpecification) -> str:
    canonical = json.dumps(
        _canonicalize(specification),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _stable_rank(seed: int, *parts: object) -> int:
    payload = "\x1f".join((ALGORITHM_VERSION, str(seed), *(str(part) for part in parts)))
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")


def _impossible(violation: Violation, constraint: str, message: str) -> Never:
    raise ImpossibleBlueprintError(violation, constraint, message)


def _validate_aggregate_constraints(specification: BlueprintSpecification) -> int:
    section_mark_total = sum(section.marks for section in specification.sections)
    if section_mark_total != specification.total_marks:
        _impossible(
            Violation.TOTAL_MARKS_MISMATCH,
            "specification.total_marks",
            f"section marks total {section_mark_total}, expected {specification.total_marks}",
        )

    slot_count = sum(section.question_count for section in specification.sections)
    question_type_slots = sum(
        allocation.exact_slots for allocation in specification.question_type_allocations
    )
    if question_type_slots != slot_count:
        _impossible(
            Violation.QUESTION_TYPE_SLOT_MISMATCH,
            "specification.question_type_allocations",
            f"question-type slots total {question_type_slots}, expected {slot_count}",
        )
    difficulty_slots = sum(
        allocation.exact_slots for allocation in specification.difficulty_allocations
    )
    if difficulty_slots != slot_count:
        _impossible(
            Violation.DIFFICULTY_SLOT_MISMATCH,
            "specification.difficulty_allocations",
            f"difficulty slots total {difficulty_slots}, expected {slot_count}",
        )

    minimum_taxonomy_slots = sum(
        requirement.minimum_slots for requirement in specification.taxonomy_requirements
    )
    maximum_taxonomy_slots = sum(
        requirement.maximum_slots if requirement.maximum_slots is not None else slot_count
        for requirement in specification.taxonomy_requirements
    )
    if minimum_taxonomy_slots > slot_count or maximum_taxonomy_slots < slot_count:
        _impossible(
            Violation.TAXONOMY_COVERAGE_IMPOSSIBLE,
            "specification.taxonomy_requirements",
            "taxonomy minimum/maximum slot bounds cannot cover "
            f"{slot_count} slots (minimum={minimum_taxonomy_slots}, "
            f"maximum={maximum_taxonomy_slots})",
        )
    return slot_count


def _compose_section_marks(section: SectionSpecification, seed: int) -> tuple[int, ...]:
    allowed_marks = tuple(
        sorted(
            section.allowed_marks_per_slot,
            key=lambda mark: _stable_rank(
                seed,
                "section-marks",
                section.section_id,
                mark,
            ),
        )
    )
    minimum_mark = min(allowed_marks)
    maximum_mark = max(allowed_marks)

    @cache
    def solve(position: int, remaining_marks: int) -> tuple[int, ...] | None:
        slots_left = section.question_count - position
        if slots_left == 0:
            return () if remaining_marks == 0 else None
        if not minimum_mark * slots_left <= remaining_marks <= maximum_mark * slots_left:
            return None

        ordered_marks = sorted(
            allowed_marks,
            key=lambda mark: _stable_rank(
                seed,
                "section-marks",
                section.section_id,
                position,
                mark,
            ),
        )
        for mark in ordered_marks:
            tail = solve(position + 1, remaining_marks - mark)
            if tail is not None:
                return (mark, *tail)
        return None

    result = solve(0, section.marks)
    if result is None:
        _impossible(
            Violation.SECTION_MARKS_IMPOSSIBLE,
            f"section.{section.section_id}.marks",
            f"cannot create {section.question_count} slots totalling {section.marks} marks "
            f"from allowed values {sorted(section.allowed_marks_per_slot)}",
        )
    return result


def _build_slot_drafts(
    specification: BlueprintSpecification,
    seed: int,
) -> tuple[_SlotDraft, ...]:
    drafts: list[_SlotDraft] = []
    for section in specification.sections:
        marks = _compose_section_marks(section, seed)
        for section_ordinal, slot_marks in enumerate(marks, start=1):
            drafts.append(
                _SlotDraft(
                    index=len(drafts),
                    section=section,
                    section_ordinal=section_ordinal,
                    marks=slot_marks,
                )
            )
    return tuple(drafts)


def _assign_exact_categories[Category: Hashable](
    *,
    slots: tuple[_SlotDraft, ...],
    categories: tuple[Category, ...],
    exact_counts: Mapping[Category, int],
    exact_marks: Mapping[Category, int | None],
    is_allowed: Callable[[_SlotDraft, Category], bool],
    seed: int,
    rank_label: str,
    violation: Violation,
) -> tuple[Category, ...]:
    ordered_slot_indexes = tuple(
        sorted(
            range(len(slots)),
            key=lambda slot_index: (
                sum(is_allowed(slots[slot_index], category) for category in categories),
                -slots[slot_index].marks,
                _stable_rank(seed, rank_label, "slot", slot_index),
            ),
        )
    )
    initial_counts = tuple(exact_counts[category] for category in categories)
    initial_marks = tuple(
        -1 if exact_marks[category] is None else cast(int, exact_marks[category])
        for category in categories
    )

    def state_is_feasible(
        position: int,
        remaining_counts: tuple[int, ...],
        remaining_marks: tuple[int, ...],
    ) -> bool:
        remaining_slot_indexes = ordered_slot_indexes[position:]
        for category_index, category in enumerate(categories):
            required_count = remaining_counts[category_index]
            eligible_marks = sorted(
                slots[slot_index].marks
                for slot_index in remaining_slot_indexes
                if is_allowed(slots[slot_index], category)
            )
            if len(eligible_marks) < required_count:
                return False
            required_marks = remaining_marks[category_index]
            if (
                required_marks >= 0
                and required_count > 0
                and not (
                    sum(eligible_marks[:required_count])
                    <= required_marks
                    <= sum(eligible_marks[-required_count:])
                )
            ):
                return False
        return True

    @cache
    def solve(
        position: int,
        remaining_counts: tuple[int, ...],
        remaining_marks: tuple[int, ...],
    ) -> tuple[int, ...] | None:
        if not state_is_feasible(position, remaining_counts, remaining_marks):
            return None
        if position == len(ordered_slot_indexes):
            return ()

        slot_index = ordered_slot_indexes[position]
        slot = slots[slot_index]
        candidate_indexes = [
            category_index
            for category_index, category in enumerate(categories)
            if remaining_counts[category_index] > 0
            and is_allowed(slot, category)
            and (
                remaining_marks[category_index] < 0 or remaining_marks[category_index] >= slot.marks
            )
        ]
        candidate_indexes.sort(
            key=lambda category_index: (
                sum(
                    is_allowed(slots[index], categories[category_index])
                    for index in ordered_slot_indexes[position:]
                )
                - remaining_counts[category_index],
                _stable_rank(
                    seed,
                    rank_label,
                    "category",
                    str(categories[category_index]),
                    position,
                ),
            )
        )
        for category_index in candidate_indexes:
            updated_counts = list(remaining_counts)
            updated_counts[category_index] -= 1
            updated_marks = list(remaining_marks)
            if updated_marks[category_index] >= 0:
                updated_marks[category_index] -= slot.marks
            tail = solve(position + 1, tuple(updated_counts), tuple(updated_marks))
            if tail is not None:
                return (category_index, *tail)
        return None

    result = solve(0, initial_counts, initial_marks)
    if result is None:
        _impossible(
            violation,
            f"specification.{rank_label}_allocations",
            f"no assignment satisfies exact {rank_label} slots, marks, and section rules",
        )

    assignment_indexes = [0] * len(slots)
    for ordered_position, category_index in enumerate(result):
        assignment_indexes[ordered_slot_indexes[ordered_position]] = category_index
    return tuple(categories[index] for index in assignment_indexes)


def _taxonomy_allowed(slot: _SlotDraft, requirement: TaxonomyRequirement) -> bool:
    section = slot.section
    section_allows = (
        not section.allowed_taxonomy_targets
        or requirement.target in section.allowed_taxonomy_targets
    )
    requirement_allows = (
        not requirement.allowed_section_ids or section.section_id in requirement.allowed_section_ids
    )
    return section_allows and requirement_allows


def _assign_taxonomy_requirements(
    slots: tuple[_SlotDraft, ...],
    requirements: tuple[TaxonomyRequirement, ...],
    seed: int,
) -> tuple[TaxonomyRequirement, ...]:
    ordered_slot_indexes = tuple(
        sorted(
            range(len(slots)),
            key=lambda slot_index: (
                sum(
                    _taxonomy_allowed(slots[slot_index], requirement)
                    for requirement in requirements
                ),
                _stable_rank(seed, "taxonomy", "slot", slot_index),
            ),
        )
    )
    minimums = tuple(requirement.minimum_slots for requirement in requirements)
    maximums = tuple(
        requirement.maximum_slots if requirement.maximum_slots is not None else len(slots)
        for requirement in requirements
    )

    def state_is_feasible(position: int, assigned_counts: tuple[int, ...]) -> bool:
        remaining_slot_indexes = ordered_slot_indexes[position:]
        required_remaining = 0
        available_capacity = 0
        for requirement_index, requirement in enumerate(requirements):
            assigned = assigned_counts[requirement_index]
            needed = max(0, minimums[requirement_index] - assigned)
            eligible_count = sum(
                _taxonomy_allowed(slots[slot_index], requirement)
                for slot_index in remaining_slot_indexes
            )
            if eligible_count < needed:
                return False
            required_remaining += needed
            available_capacity += min(
                maximums[requirement_index] - assigned,
                eligible_count,
            )
        if required_remaining > len(remaining_slot_indexes):
            return False
        if available_capacity < len(remaining_slot_indexes):
            return False
        return all(
            any(_taxonomy_allowed(slots[slot_index], requirement) for requirement in requirements)
            for slot_index in remaining_slot_indexes
        )

    @cache
    def solve(
        position: int,
        assigned_counts: tuple[int, ...],
    ) -> tuple[int, ...] | None:
        if not state_is_feasible(position, assigned_counts):
            return None
        if position == len(ordered_slot_indexes):
            return ()

        slot_index = ordered_slot_indexes[position]
        slot = slots[slot_index]
        candidate_indexes = [
            index
            for index, requirement in enumerate(requirements)
            if assigned_counts[index] < maximums[index] and _taxonomy_allowed(slot, requirement)
        ]
        candidate_indexes.sort(
            key=lambda index: (
                -Fraction(
                    requirements[index].priority.effective_score,
                    assigned_counts[index] + 1,
                ),
                _stable_rank(
                    seed,
                    "taxonomy",
                    requirements[index].target.key,
                    position,
                ),
            )
        )
        for requirement_index in candidate_indexes:
            updated_counts = list(assigned_counts)
            updated_counts[requirement_index] += 1
            tail = solve(position + 1, tuple(updated_counts))
            if tail is not None:
                return (requirement_index, *tail)
        return None

    result = solve(0, (0,) * len(requirements))
    if result is None:
        _impossible(
            Violation.TAXONOMY_COVERAGE_IMPOSSIBLE,
            "specification.taxonomy_requirements",
            "no assignment satisfies taxonomy coverage bounds and section scope rules",
        )

    assignment_indexes = [0] * len(slots)
    for ordered_position, requirement_index in enumerate(result):
        assignment_indexes[ordered_slot_indexes[ordered_position]] = requirement_index
    return tuple(requirements[index] for index in assignment_indexes)


def _merge_unique_text(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            if value not in seen:
                seen.add(value)
                merged.append(value)
    return tuple(merged)


def _priority_summary(priority: PracticePriority) -> str:
    if priority.mode is PriorityMode.FORECAST:
        baseline_metric = cast(int, priority.baseline_backtest_score)
        forecast_metric = cast(int, priority.forecast_backtest_score)
        return (
            "Backtested practice priority used because its improvement "
            f"({forecast_metric - baseline_metric}) met the configured minimum "
            f"({priority.minimum_backtest_improvement}); hard blueprint rules remain authoritative."
        )
    if priority.mode is PriorityMode.BASELINE_FALLBACK:
        baseline_metric = cast(int, priority.baseline_backtest_score)
        forecast_metric = cast(int, priority.forecast_backtest_score)
        return (
            "Syllabus-balanced baseline practice priority retained because the forecast "
            f"improvement ({forecast_metric - baseline_metric}) was below the configured minimum "
            f"({priority.minimum_backtest_improvement})."
        )
    return "Syllabus-balanced baseline practice priority used; no backtested forecast was supplied."


def _select_archetype(
    allocation_archetypes: tuple[str, ...],
    slot: _SlotDraft,
    question_type: QuestionType,
    seed: int,
) -> str:
    index = _stable_rank(
        seed,
        "archetype",
        slot.section.section_id,
        slot.section_ordinal,
        question_type.value,
    ) % len(allocation_archetypes)
    return allocation_archetypes[index]


def generate_blueprint(
    specification: BlueprintSpecification,
    *,
    seed: int = 0,
) -> PaperBlueprint:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise BlueprintValidationError(
            Violation.INVALID_VALUE,
            "seed",
            "must be an integer",
        )
    _validate_aggregate_constraints(specification)
    drafts = _build_slot_drafts(specification, seed)

    question_types = tuple(
        allocation.question_type for allocation in specification.question_type_allocations
    )
    question_type_assignments = _assign_exact_categories(
        slots=drafts,
        categories=question_types,
        exact_counts={
            allocation.question_type: allocation.exact_slots
            for allocation in specification.question_type_allocations
        },
        exact_marks={
            allocation.question_type: allocation.exact_marks
            for allocation in specification.question_type_allocations
        },
        is_allowed=lambda slot, question_type: question_type in slot.section.allowed_question_types,
        seed=seed,
        rank_label="question_type",
        violation=Violation.QUESTION_TYPE_ALLOCATION_IMPOSSIBLE,
    )

    difficulties = tuple(
        allocation.difficulty for allocation in specification.difficulty_allocations
    )
    difficulty_assignments = _assign_exact_categories(
        slots=drafts,
        categories=difficulties,
        exact_counts={
            allocation.difficulty: allocation.exact_slots
            for allocation in specification.difficulty_allocations
        },
        exact_marks={
            allocation.difficulty: allocation.exact_marks
            for allocation in specification.difficulty_allocations
        },
        is_allowed=lambda slot, difficulty: difficulty in slot.section.allowed_difficulties,
        seed=seed,
        rank_label="difficulty",
        violation=Violation.DIFFICULTY_ALLOCATION_IMPOSSIBLE,
    )
    taxonomy_assignments = _assign_taxonomy_requirements(
        drafts,
        specification.taxonomy_requirements,
        seed,
    )

    input_fingerprint = _specification_fingerprint(specification)
    generation_digest = hashlib.sha256(
        f"{ALGORITHM_VERSION}\x1f{input_fingerprint}\x1f{seed}".encode()
    ).hexdigest()
    blueprint_id = f"bp_{generation_digest[:24]}"
    version = BlueprintVersion(
        blueprint_id=blueprint_id,
        schema_version=SCHEMA_VERSION,
        algorithm_version=ALGORITHM_VERSION,
        config_version=specification.config_version,
        input_fingerprint=input_fingerprint,
    )
    question_type_allocations = {
        allocation.question_type: allocation
        for allocation in specification.question_type_allocations
    }

    slots: list[BlueprintSlot] = []
    for draft, question_type, difficulty, taxonomy_requirement in zip(
        drafts,
        question_type_assignments,
        difficulty_assignments,
        taxonomy_assignments,
        strict=True,
    ):
        allocation = question_type_allocations[question_type]
        archetype = _select_archetype(allocation.archetypes, draft, question_type, seed)
        priority = taxonomy_requirement.priority
        constraints = SlotGenerationConstraints(
            curriculum_scope=specification.curriculum_scope,
            taxonomy_target=taxonomy_requirement.target,
            required_question_type=question_type,
            required_archetype=archetype,
            required_difficulty=difficulty,
            exact_marks=draft.marks,
            response_language=specification.generation_policy.response_language,
            instructions=_merge_unique_text(
                specification.generation_policy.instructions,
                taxonomy_requirement.generation_instructions,
            ),
            answer_requirements=specification.generation_policy.answer_requirements,
            retrieval_query_hints=_merge_unique_text(
                specification.generation_policy.retrieval_query_hints,
                draft.section.retrieval_query_hints,
                taxonomy_requirement.retrieval_query_hints,
            ),
            uniqueness=specification.generation_policy.uniqueness,
            diversity_key=(
                f"{specification.paper_code}:{draft.section.section_id}:"
                f"{taxonomy_requirement.target.key}:{question_type.value}:{difficulty.value}"
            ),
        )
        rationale = SlotRationale(
            priority_mode=priority.mode,
            effective_priority_score=priority.effective_score,
            summary=_priority_summary(priority),
        )
        evidence = SlotEvidence(
            config_version=specification.config_version,
            baseline_version=priority.baseline_version,
            baseline_score=priority.baseline_score,
            evidence_refs=_merge_unique_text(priority.evidence_refs),
            forecast_version=priority.forecast_version,
            forecast_score=priority.forecast_score,
            baseline_backtest_score=priority.baseline_backtest_score,
            forecast_backtest_score=priority.forecast_backtest_score,
            minimum_backtest_improvement=priority.minimum_backtest_improvement,
        )
        slots.append(
            BlueprintSlot(
                slot_id=(f"{blueprint_id}:{draft.section.section_id}:{draft.section_ordinal:03d}"),
                ordinal=draft.index + 1,
                paper_code=specification.paper_code,
                section_id=draft.section.section_id,
                section_title=draft.section.title,
                section_ordinal=draft.section_ordinal,
                taxonomy_target=taxonomy_requirement.target,
                question_type=question_type,
                archetype=archetype,
                difficulty=difficulty,
                marks=draft.marks,
                generation_constraints=constraints,
                rationale=rationale,
                evidence=evidence,
            )
        )

    return PaperBlueprint(
        version=version,
        paper_code=specification.paper_code,
        title=specification.title,
        seed=seed,
        total_marks=specification.total_marks,
        curriculum_scope=specification.curriculum_scope,
        sections=tuple(
            BlueprintSection(
                section_id=section.section_id,
                title=section.title,
                marks=section.marks,
                slot_count=section.question_count,
            )
            for section in specification.sections
        ),
        question_type_allocations=specification.question_type_allocations,
        difficulty_allocations=specification.difficulty_allocations,
        taxonomy_requirements=specification.taxonomy_requirements,
        slots=tuple(slots),
    )


class DeterministicBlueprintGenerator:
    algorithm_version = ALGORITHM_VERSION

    def generate(
        self,
        specification: BlueprintSpecification,
        *,
        seed: int = 0,
    ) -> PaperBlueprint:
        return generate_blueprint(specification, seed=seed)
