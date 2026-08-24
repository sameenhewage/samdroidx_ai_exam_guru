from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    STRUCTURED = "structured"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class PriorityMode(StrEnum):
    BASELINE_ONLY = "baseline_only"
    BASELINE_FALLBACK = "baseline_fallback"
    FORECAST = "forecast"


class Violation(StrEnum):
    INVALID_VALUE = "invalid_value"
    INVALID_PRIORITY_EVIDENCE = "invalid_priority_evidence"
    DUPLICATE_SECTION_ID = "duplicate_section_id"
    DUPLICATE_ALLOCATION = "duplicate_allocation"
    DUPLICATE_TAXONOMY_TARGET = "duplicate_taxonomy_target"
    UNKNOWN_SECTION = "unknown_section"
    UNKNOWN_TAXONOMY_TARGET = "unknown_taxonomy_target"
    TOTAL_MARKS_MISMATCH = "total_marks_mismatch"
    SECTION_MARKS_IMPOSSIBLE = "section_marks_impossible"
    QUESTION_TYPE_SLOT_MISMATCH = "question_type_slot_mismatch"
    QUESTION_TYPE_ALLOCATION_IMPOSSIBLE = "question_type_allocation_impossible"
    DIFFICULTY_SLOT_MISMATCH = "difficulty_slot_mismatch"
    DIFFICULTY_ALLOCATION_IMPOSSIBLE = "difficulty_allocation_impossible"
    TAXONOMY_COVERAGE_IMPOSSIBLE = "taxonomy_coverage_impossible"
    DUPLICATE_SLOT_ID = "duplicate_slot_id"
    SLOT_ALLOCATION_MISMATCH = "slot_allocation_mismatch"
    SLOT_METADATA_MISMATCH = "slot_metadata_mismatch"


class BlueprintValidationError(ValueError):
    def __init__(self, violation: Violation, constraint: str, message: str) -> None:
        self.violation = violation
        self.constraint = constraint
        self.detail = message
        super().__init__(f"{violation.value} [{constraint}]: {message}")


class ImpossibleBlueprintError(BlueprintValidationError):
    """Raised when individually valid rules cannot form a complete paper."""


def _invalid(constraint: str, message: str) -> BlueprintValidationError:
    return BlueprintValidationError(Violation.INVALID_VALUE, constraint, message)


def _validate_text(value: str, constraint: str) -> None:
    if not value or value != value.strip():
        raise _invalid(constraint, "must be non-blank and have no surrounding whitespace")


def _validate_text_tuple(
    values: tuple[str, ...],
    constraint: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not values and not allow_empty:
        raise _invalid(constraint, "must contain at least one value")
    if len(set(values)) != len(values):
        raise _invalid(constraint, "must not contain duplicate values")
    for value in values:
        _validate_text(value, constraint)


@dataclass(frozen=True, slots=True)
class CurriculumScope:
    curriculum_version_id: UUID
    grade: int
    medium: str

    def __post_init__(self) -> None:
        if not 1 <= self.grade <= 13:
            raise _invalid("curriculum_scope.grade", "must be between 1 and 13")
        _validate_text(self.medium, "curriculum_scope.medium")


@dataclass(frozen=True, slots=True)
class TaxonomyTarget:
    competency_id: UUID
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.sub_skill_id is not None and self.skill_id is None:
            raise _invalid("taxonomy_target.sub_skill_id", "requires a skill_id")
        if self.learning_concept_id is not None and self.sub_skill_id is None:
            raise _invalid(
                "taxonomy_target.learning_concept_id",
                "requires a sub_skill_id",
            )

    @property
    def key(self) -> str:
        identifiers = (
            self.competency_id,
            self.skill_id,
            self.sub_skill_id,
            self.learning_concept_id,
        )
        return "/".join(str(identifier) for identifier in identifiers if identifier is not None)


@dataclass(frozen=True, slots=True)
class PracticePriority:
    baseline_score: int
    baseline_version: str
    baseline_evidence_refs: tuple[str, ...]
    forecast_score: int | None = None
    forecast_version: str | None = None
    baseline_backtest_score: int | None = None
    forecast_backtest_score: int | None = None
    minimum_backtest_improvement: int = 1
    forecast_evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.baseline_score < 1:
            raise _invalid("priority.baseline_score", "must be positive")
        _validate_text(self.baseline_version, "priority.baseline_version")
        _validate_text_tuple(
            self.baseline_evidence_refs,
            "priority.baseline_evidence_refs",
        )
        if self.minimum_backtest_improvement < 1:
            raise _invalid(
                "priority.minimum_backtest_improvement",
                "must be positive so an equal forecast cannot displace the baseline",
            )

        forecast_fields = (
            self.forecast_score,
            self.forecast_version,
            self.baseline_backtest_score,
            self.forecast_backtest_score,
        )
        has_forecast = any(value is not None for value in forecast_fields)
        complete_forecast = all(value is not None for value in forecast_fields)
        if has_forecast != complete_forecast:
            raise BlueprintValidationError(
                Violation.INVALID_PRIORITY_EVIDENCE,
                "priority.forecast",
                "forecast score, version, baseline metric, and forecast metric are all required",
            )
        if not has_forecast:
            if self.forecast_evidence_refs:
                raise BlueprintValidationError(
                    Violation.INVALID_PRIORITY_EVIDENCE,
                    "priority.forecast_evidence_refs",
                    "forecast evidence requires a complete forecast signal",
                )
            return

        forecast_score = cast(int, self.forecast_score)
        baseline_backtest_score = cast(int, self.baseline_backtest_score)
        forecast_backtest_score = cast(int, self.forecast_backtest_score)
        forecast_version = cast(str, self.forecast_version)
        if forecast_score < 1:
            raise _invalid("priority.forecast_score", "must be positive")
        if baseline_backtest_score < 0:
            raise _invalid("priority.baseline_backtest_score", "must be non-negative")
        if forecast_backtest_score < 0:
            raise _invalid("priority.forecast_backtest_score", "must be non-negative")
        _validate_text(forecast_version, "priority.forecast_version")
        _validate_text_tuple(
            self.forecast_evidence_refs,
            "priority.forecast_evidence_refs",
        )

    @property
    def mode(self) -> PriorityMode:
        if self.forecast_score is None:
            return PriorityMode.BASELINE_ONLY
        baseline_metric = cast(int, self.baseline_backtest_score)
        forecast_metric = cast(int, self.forecast_backtest_score)
        improvement = forecast_metric - baseline_metric
        if improvement >= self.minimum_backtest_improvement:
            return PriorityMode.FORECAST
        return PriorityMode.BASELINE_FALLBACK

    @property
    def effective_score(self) -> int:
        if self.mode is PriorityMode.FORECAST:
            return cast(int, self.forecast_score)
        return self.baseline_score

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return self.baseline_evidence_refs + self.forecast_evidence_refs


_ALL_QUESTION_TYPES = tuple(QuestionType)
_ALL_DIFFICULTIES = tuple(Difficulty)


@dataclass(frozen=True, slots=True)
class SectionSpecification:
    section_id: str
    title: str
    marks: int
    question_count: int
    allowed_marks_per_slot: tuple[int, ...]
    allowed_question_types: tuple[QuestionType, ...] = _ALL_QUESTION_TYPES
    allowed_difficulties: tuple[Difficulty, ...] = _ALL_DIFFICULTIES
    allowed_taxonomy_targets: tuple[TaxonomyTarget, ...] = ()
    retrieval_query_hints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_text(self.section_id, "section.section_id")
        _validate_text(self.title, "section.title")
        if self.marks < 1:
            raise _invalid("section.marks", "must be positive")
        if self.question_count < 1:
            raise _invalid("section.question_count", "must be positive")
        if not self.allowed_marks_per_slot:
            raise _invalid("section.allowed_marks_per_slot", "must not be empty")
        if any(mark < 1 for mark in self.allowed_marks_per_slot):
            raise _invalid("section.allowed_marks_per_slot", "marks must be positive")
        if len(set(self.allowed_marks_per_slot)) != len(self.allowed_marks_per_slot):
            raise _invalid("section.allowed_marks_per_slot", "must not contain duplicates")
        if not self.allowed_question_types:
            raise _invalid("section.allowed_question_types", "must not be empty")
        if len(set(self.allowed_question_types)) != len(self.allowed_question_types):
            raise _invalid("section.allowed_question_types", "must not contain duplicates")
        if not self.allowed_difficulties:
            raise _invalid("section.allowed_difficulties", "must not be empty")
        if len(set(self.allowed_difficulties)) != len(self.allowed_difficulties):
            raise _invalid("section.allowed_difficulties", "must not contain duplicates")
        if len(set(self.allowed_taxonomy_targets)) != len(self.allowed_taxonomy_targets):
            raise _invalid("section.allowed_taxonomy_targets", "must not contain duplicates")
        _validate_text_tuple(
            self.retrieval_query_hints,
            "section.retrieval_query_hints",
            allow_empty=True,
        )


@dataclass(frozen=True, slots=True)
class QuestionTypeAllocation:
    question_type: QuestionType
    exact_slots: int
    archetypes: tuple[str, ...]
    exact_marks: int | None = None

    def __post_init__(self) -> None:
        if self.exact_slots < 1:
            raise _invalid("question_type_allocation.exact_slots", "must be positive")
        if self.exact_marks is not None and self.exact_marks < self.exact_slots:
            raise _invalid(
                "question_type_allocation.exact_marks",
                "must allow at least one mark per slot",
            )
        _validate_text_tuple(self.archetypes, "question_type_allocation.archetypes")


@dataclass(frozen=True, slots=True)
class DifficultyAllocation:
    difficulty: Difficulty
    exact_slots: int
    exact_marks: int | None = None

    def __post_init__(self) -> None:
        if self.exact_slots < 1:
            raise _invalid("difficulty_allocation.exact_slots", "must be positive")
        if self.exact_marks is not None and self.exact_marks < self.exact_slots:
            raise _invalid(
                "difficulty_allocation.exact_marks",
                "must allow at least one mark per slot",
            )


@dataclass(frozen=True, slots=True)
class TaxonomyRequirement:
    target: TaxonomyTarget
    minimum_slots: int
    priority: PracticePriority
    retrieval_query_hints: tuple[str, ...]
    generation_instructions: tuple[str, ...]
    maximum_slots: int | None = None
    allowed_section_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.minimum_slots < 1:
            raise _invalid("taxonomy_requirement.minimum_slots", "must be positive")
        if self.maximum_slots is not None and self.maximum_slots < self.minimum_slots:
            raise _invalid(
                "taxonomy_requirement.maximum_slots",
                "must be greater than or equal to minimum_slots",
            )
        _validate_text_tuple(
            self.retrieval_query_hints,
            "taxonomy_requirement.retrieval_query_hints",
        )
        _validate_text_tuple(
            self.generation_instructions,
            "taxonomy_requirement.generation_instructions",
        )
        _validate_text_tuple(
            self.allowed_section_ids,
            "taxonomy_requirement.allowed_section_ids",
            allow_empty=True,
        )


@dataclass(frozen=True, slots=True)
class UniquenessPolicy:
    forbid_duplicate_stems: bool = True
    forbid_verbatim_sources: bool = True
    max_similarity_basis_points: int = 8500
    minimum_distinct_contexts: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.max_similarity_basis_points < 10_000:
            raise _invalid(
                "uniqueness.max_similarity_basis_points",
                "must be between 0 and 9999",
            )
        if self.minimum_distinct_contexts < 1:
            raise _invalid("uniqueness.minimum_distinct_contexts", "must be positive")


@dataclass(frozen=True, slots=True)
class GenerationPolicy:
    response_language: str
    instructions: tuple[str, ...]
    answer_requirements: tuple[str, ...]
    retrieval_query_hints: tuple[str, ...]
    uniqueness: UniquenessPolicy

    def __post_init__(self) -> None:
        _validate_text(self.response_language, "generation_policy.response_language")
        _validate_text_tuple(self.instructions, "generation_policy.instructions")
        _validate_text_tuple(
            self.answer_requirements,
            "generation_policy.answer_requirements",
        )
        _validate_text_tuple(
            self.retrieval_query_hints,
            "generation_policy.retrieval_query_hints",
        )


@dataclass(frozen=True, slots=True)
class BlueprintSpecification:
    config_version: str
    paper_code: str
    title: str
    total_marks: int
    curriculum_scope: CurriculumScope
    sections: tuple[SectionSpecification, ...]
    question_type_allocations: tuple[QuestionTypeAllocation, ...]
    difficulty_allocations: tuple[DifficultyAllocation, ...]
    taxonomy_requirements: tuple[TaxonomyRequirement, ...]
    generation_policy: GenerationPolicy

    def __post_init__(self) -> None:
        _validate_text(self.config_version, "specification.config_version")
        _validate_text(self.paper_code, "specification.paper_code")
        _validate_text(self.title, "specification.title")
        if self.total_marks < 1:
            raise _invalid("specification.total_marks", "must be positive")
        if not self.sections:
            raise _invalid("specification.sections", "must not be empty")
        if not self.question_type_allocations:
            raise _invalid("specification.question_type_allocations", "must not be empty")
        if not self.difficulty_allocations:
            raise _invalid("specification.difficulty_allocations", "must not be empty")
        if not self.taxonomy_requirements:
            raise _invalid("specification.taxonomy_requirements", "must not be empty")

        section_ids = tuple(section.section_id for section in self.sections)
        if len(set(section_ids)) != len(section_ids):
            raise BlueprintValidationError(
                Violation.DUPLICATE_SECTION_ID,
                "specification.sections",
                "section_id values must be unique",
            )
        question_types = tuple(
            allocation.question_type for allocation in self.question_type_allocations
        )
        if len(set(question_types)) != len(question_types):
            raise BlueprintValidationError(
                Violation.DUPLICATE_ALLOCATION,
                "specification.question_type_allocations",
                "question types must be unique",
            )
        difficulties = tuple(allocation.difficulty for allocation in self.difficulty_allocations)
        if len(set(difficulties)) != len(difficulties):
            raise BlueprintValidationError(
                Violation.DUPLICATE_ALLOCATION,
                "specification.difficulty_allocations",
                "difficulties must be unique",
            )
        taxonomy_targets = tuple(requirement.target for requirement in self.taxonomy_requirements)
        if len(set(taxonomy_targets)) != len(taxonomy_targets):
            raise BlueprintValidationError(
                Violation.DUPLICATE_TAXONOMY_TARGET,
                "specification.taxonomy_requirements",
                "taxonomy targets must be unique",
            )

        known_sections = set(section_ids)
        known_targets = set(taxonomy_targets)
        for requirement in self.taxonomy_requirements:
            unknown_sections = set(requirement.allowed_section_ids) - known_sections
            if unknown_sections:
                raise BlueprintValidationError(
                    Violation.UNKNOWN_SECTION,
                    "taxonomy_requirement.allowed_section_ids",
                    f"unknown sections: {sorted(unknown_sections)}",
                )
        for section in self.sections:
            unknown_targets = set(section.allowed_taxonomy_targets) - known_targets
            if unknown_targets:
                raise BlueprintValidationError(
                    Violation.UNKNOWN_TAXONOMY_TARGET,
                    "section.allowed_taxonomy_targets",
                    f"section {section.section_id} references an unknown taxonomy target",
                )


@dataclass(frozen=True, slots=True)
class BlueprintVersion:
    blueprint_id: str
    schema_version: str
    algorithm_version: str
    config_version: str
    input_fingerprint: str

    def __post_init__(self) -> None:
        _validate_text(self.blueprint_id, "blueprint_version.blueprint_id")
        _validate_text(self.schema_version, "blueprint_version.schema_version")
        _validate_text(self.algorithm_version, "blueprint_version.algorithm_version")
        _validate_text(self.config_version, "blueprint_version.config_version")
        if len(self.input_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in self.input_fingerprint
        ):
            raise _invalid(
                "blueprint_version.input_fingerprint",
                "must be a lowercase SHA-256 digest",
            )


@dataclass(frozen=True, slots=True)
class SlotRationale:
    priority_mode: PriorityMode
    effective_priority_score: int
    summary: str

    def __post_init__(self) -> None:
        if self.effective_priority_score < 1:
            raise _invalid("slot_rationale.effective_priority_score", "must be positive")
        _validate_text(self.summary, "slot_rationale.summary")


@dataclass(frozen=True, slots=True)
class SlotEvidence:
    config_version: str
    baseline_version: str
    baseline_score: int
    evidence_refs: tuple[str, ...]
    forecast_version: str | None = None
    forecast_score: int | None = None
    baseline_backtest_score: int | None = None
    forecast_backtest_score: int | None = None
    minimum_backtest_improvement: int = 1

    def __post_init__(self) -> None:
        _validate_text(self.config_version, "slot_evidence.config_version")
        _validate_text(self.baseline_version, "slot_evidence.baseline_version")
        if self.baseline_score < 1:
            raise _invalid("slot_evidence.baseline_score", "must be positive")
        _validate_text_tuple(self.evidence_refs, "slot_evidence.evidence_refs")
        forecast_fields = (
            self.forecast_version,
            self.forecast_score,
            self.baseline_backtest_score,
            self.forecast_backtest_score,
        )
        if any(value is not None for value in forecast_fields) != all(
            value is not None for value in forecast_fields
        ):
            raise BlueprintValidationError(
                Violation.INVALID_PRIORITY_EVIDENCE,
                "slot_evidence.forecast",
                "forecast metadata must be complete",
            )
        if self.minimum_backtest_improvement < 1:
            raise _invalid(
                "slot_evidence.minimum_backtest_improvement",
                "must be positive",
            )


@dataclass(frozen=True, slots=True)
class SlotGenerationConstraints:
    curriculum_scope: CurriculumScope
    taxonomy_target: TaxonomyTarget
    required_question_type: QuestionType
    required_archetype: str
    required_difficulty: Difficulty
    exact_marks: int
    response_language: str
    instructions: tuple[str, ...]
    answer_requirements: tuple[str, ...]
    retrieval_query_hints: tuple[str, ...]
    uniqueness: UniquenessPolicy
    diversity_key: str

    def __post_init__(self) -> None:
        _validate_text(self.required_archetype, "slot_constraints.required_archetype")
        if self.exact_marks < 1:
            raise _invalid("slot_constraints.exact_marks", "must be positive")
        _validate_text(self.response_language, "slot_constraints.response_language")
        _validate_text_tuple(self.instructions, "slot_constraints.instructions")
        _validate_text_tuple(
            self.answer_requirements,
            "slot_constraints.answer_requirements",
        )
        _validate_text_tuple(
            self.retrieval_query_hints,
            "slot_constraints.retrieval_query_hints",
        )
        _validate_text(self.diversity_key, "slot_constraints.diversity_key")


@dataclass(frozen=True, slots=True)
class BlueprintSlot:
    slot_id: str
    ordinal: int
    paper_code: str
    section_id: str
    section_title: str
    section_ordinal: int
    taxonomy_target: TaxonomyTarget
    question_type: QuestionType
    archetype: str
    difficulty: Difficulty
    marks: int
    generation_constraints: SlotGenerationConstraints
    rationale: SlotRationale
    evidence: SlotEvidence

    def __post_init__(self) -> None:
        _validate_text(self.slot_id, "slot.slot_id")
        _validate_text(self.paper_code, "slot.paper_code")
        _validate_text(self.section_id, "slot.section_id")
        _validate_text(self.section_title, "slot.section_title")
        _validate_text(self.archetype, "slot.archetype")
        if self.ordinal < 1 or self.section_ordinal < 1:
            raise _invalid("slot.ordinal", "ordinals must be positive")
        if self.marks < 1:
            raise _invalid("slot.marks", "must be positive")
        constraints = self.generation_constraints
        if (
            constraints.taxonomy_target != self.taxonomy_target
            or constraints.required_question_type is not self.question_type
            or constraints.required_archetype != self.archetype
            or constraints.required_difficulty is not self.difficulty
            or constraints.exact_marks != self.marks
        ):
            raise BlueprintValidationError(
                Violation.SLOT_METADATA_MISMATCH,
                "slot.generation_constraints",
                "generation requirements must exactly match the allocated slot",
            )


@dataclass(frozen=True, slots=True)
class BlueprintSection:
    section_id: str
    title: str
    marks: int
    slot_count: int

    def __post_init__(self) -> None:
        _validate_text(self.section_id, "blueprint_section.section_id")
        _validate_text(self.title, "blueprint_section.title")
        if self.marks < 1 or self.slot_count < 1:
            raise _invalid("blueprint_section", "marks and slot_count must be positive")


@dataclass(frozen=True, slots=True)
class PaperBlueprint:
    version: BlueprintVersion
    paper_code: str
    title: str
    seed: int
    total_marks: int
    curriculum_scope: CurriculumScope
    sections: tuple[BlueprintSection, ...]
    question_type_allocations: tuple[QuestionTypeAllocation, ...]
    difficulty_allocations: tuple[DifficultyAllocation, ...]
    taxonomy_requirements: tuple[TaxonomyRequirement, ...]
    slots: tuple[BlueprintSlot, ...]

    def __post_init__(self) -> None:
        _validate_text(self.paper_code, "blueprint.paper_code")
        _validate_text(self.title, "blueprint.title")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise _invalid("blueprint.seed", "must be an integer")
        if self.total_marks < 1:
            raise _invalid("blueprint.total_marks", "must be positive")
        if not self.sections or not self.slots:
            raise _invalid("blueprint", "sections and slots must not be empty")

        slot_ids = tuple(slot.slot_id for slot in self.slots)
        if len(set(slot_ids)) != len(slot_ids):
            raise BlueprintValidationError(
                Violation.DUPLICATE_SLOT_ID,
                "blueprint.slots",
                "slot_id values must be globally unique",
            )
        expected_ordinals = tuple(range(1, len(self.slots) + 1))
        if tuple(slot.ordinal for slot in self.slots) != expected_ordinals:
            raise BlueprintValidationError(
                Violation.SLOT_ALLOCATION_MISMATCH,
                "blueprint.slots.ordinal",
                "global slot ordinals must be contiguous and ordered",
            )

        section_by_id = {section.section_id: section for section in self.sections}
        if len(section_by_id) != len(self.sections):
            raise BlueprintValidationError(
                Violation.DUPLICATE_SECTION_ID,
                "blueprint.sections",
                "section_id values must be unique",
            )
        if sum(section.marks for section in self.sections) != self.total_marks:
            raise BlueprintValidationError(
                Violation.TOTAL_MARKS_MISMATCH,
                "blueprint.sections",
                "section marks must sum to total_marks",
            )

        section_marks: Counter[str] = Counter()
        section_counts: Counter[str] = Counter()
        section_ordinals: dict[str, list[int]] = {section_id: [] for section_id in section_by_id}
        for slot in self.slots:
            section = section_by_id.get(slot.section_id)
            if section is None:
                raise BlueprintValidationError(
                    Violation.UNKNOWN_SECTION,
                    "blueprint.slots.section_id",
                    f"slot references unknown section {slot.section_id}",
                )
            if slot.paper_code != self.paper_code or slot.section_title != section.title:
                raise BlueprintValidationError(
                    Violation.SLOT_METADATA_MISMATCH,
                    "blueprint.slots",
                    "paper and section metadata must match the blueprint",
                )
            if slot.generation_constraints.curriculum_scope != self.curriculum_scope:
                raise BlueprintValidationError(
                    Violation.SLOT_METADATA_MISMATCH,
                    "blueprint.slots.curriculum_scope",
                    "every slot must retain the blueprint curriculum scope",
                )
            if slot.evidence.config_version != self.version.config_version:
                raise BlueprintValidationError(
                    Violation.SLOT_METADATA_MISMATCH,
                    "blueprint.slots.evidence.config_version",
                    "every slot must retain the blueprint config version",
                )
            section_marks[slot.section_id] += slot.marks
            section_counts[slot.section_id] += 1
            section_ordinals[slot.section_id].append(slot.section_ordinal)

        for section_id, section in section_by_id.items():
            if (
                section_marks[section_id] != section.marks
                or section_counts[section_id] != section.slot_count
                or section_ordinals[section_id] != list(range(1, section.slot_count + 1))
            ):
                raise BlueprintValidationError(
                    Violation.SLOT_ALLOCATION_MISMATCH,
                    f"blueprint.sections.{section_id}",
                    "slot count, marks, and section ordinals must match the section",
                )
        self._validate_exact_allocations()
        self._validate_taxonomy_coverage()

    def _validate_exact_allocations(self) -> None:
        question_type_counts = Counter(slot.question_type for slot in self.slots)
        question_type_marks: Counter[QuestionType] = Counter()
        difficulty_counts = Counter(slot.difficulty for slot in self.slots)
        difficulty_marks: Counter[Difficulty] = Counter()
        for slot in self.slots:
            question_type_marks[slot.question_type] += slot.marks
            difficulty_marks[slot.difficulty] += slot.marks

        expected_question_types = {
            allocation.question_type for allocation in self.question_type_allocations
        }
        expected_difficulties = {
            allocation.difficulty for allocation in self.difficulty_allocations
        }
        if set(question_type_counts) != expected_question_types:
            raise BlueprintValidationError(
                Violation.SLOT_ALLOCATION_MISMATCH,
                "blueprint.question_type_allocations",
                "slots contain an unexpected or missing question type",
            )
        if set(difficulty_counts) != expected_difficulties:
            raise BlueprintValidationError(
                Violation.SLOT_ALLOCATION_MISMATCH,
                "blueprint.difficulty_allocations",
                "slots contain an unexpected or missing difficulty",
            )

        for question_allocation in self.question_type_allocations:
            if (
                question_type_counts[question_allocation.question_type]
                != question_allocation.exact_slots
            ):
                raise BlueprintValidationError(
                    Violation.SLOT_ALLOCATION_MISMATCH,
                    f"question_type.{question_allocation.question_type.value}.slots",
                    "exact question-type slot allocation was not preserved",
                )
            if (
                question_allocation.exact_marks is not None
                and question_type_marks[question_allocation.question_type]
                != question_allocation.exact_marks
            ):
                raise BlueprintValidationError(
                    Violation.SLOT_ALLOCATION_MISMATCH,
                    f"question_type.{question_allocation.question_type.value}.marks",
                    "exact question-type mark allocation was not preserved",
                )
        for difficulty_allocation in self.difficulty_allocations:
            if (
                difficulty_counts[difficulty_allocation.difficulty]
                != difficulty_allocation.exact_slots
            ):
                raise BlueprintValidationError(
                    Violation.SLOT_ALLOCATION_MISMATCH,
                    f"difficulty.{difficulty_allocation.difficulty.value}.slots",
                    "exact difficulty slot allocation was not preserved",
                )
            if (
                difficulty_allocation.exact_marks is not None
                and difficulty_marks[difficulty_allocation.difficulty]
                != difficulty_allocation.exact_marks
            ):
                raise BlueprintValidationError(
                    Violation.SLOT_ALLOCATION_MISMATCH,
                    f"difficulty.{difficulty_allocation.difficulty.value}.marks",
                    "exact difficulty mark allocation was not preserved",
                )

    def _validate_taxonomy_coverage(self) -> None:
        taxonomy_counts = Counter(slot.taxonomy_target for slot in self.slots)
        requirements_by_target = {
            requirement.target: requirement for requirement in self.taxonomy_requirements
        }
        if set(taxonomy_counts) != set(requirements_by_target):
            raise BlueprintValidationError(
                Violation.SLOT_ALLOCATION_MISMATCH,
                "blueprint.taxonomy_requirements",
                "every slot must use a configured taxonomy target and every target needs coverage",
            )
        for target, requirement in requirements_by_target.items():
            count = taxonomy_counts[target]
            if count < requirement.minimum_slots or (
                requirement.maximum_slots is not None and count > requirement.maximum_slots
            ):
                raise BlueprintValidationError(
                    Violation.SLOT_ALLOCATION_MISMATCH,
                    f"taxonomy.{target.key}",
                    "taxonomy slot coverage is outside its configured bounds",
                )
