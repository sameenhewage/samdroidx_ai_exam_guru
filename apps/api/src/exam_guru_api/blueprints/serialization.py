import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, cast
from uuid import UUID

from .domain import (
    BlueprintSection,
    BlueprintSlot,
    BlueprintSpecification,
    BlueprintValidationError,
    BlueprintVersion,
    CurriculumScope,
    Difficulty,
    DifficultyAllocation,
    GenerationPolicy,
    PaperBlueprint,
    PracticePriority,
    PriorityMode,
    QuestionType,
    QuestionTypeAllocation,
    SectionSpecification,
    SlotEvidence,
    SlotGenerationConstraints,
    SlotRationale,
    TaxonomyRequirement,
    TaxonomyTarget,
    UniquenessPolicy,
)

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class BlueprintSnapshotError(ValueError):
    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid blueprint snapshot at {path}: {detail}")


def _canonicalize(value: object) -> JsonValue:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        enum_value = value.value
        if not isinstance(enum_value, (str, int)) or isinstance(enum_value, bool):
            raise TypeError(f"unsupported enum value type: {type(enum_value).__name__}")
        return enum_value
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("blueprint snapshot object keys must be strings")
            result[key] = _canonicalize(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        dataclass_value = cast(Any, value)
        return {
            field.name: _canonicalize(getattr(dataclass_value, field.name))
            for field in fields(dataclass_value)
        }
    raise TypeError(f"cannot canonicalize blueprint value of type {type(value).__name__}")


def canonical_snapshot_bytes(value: object) -> bytes:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def fingerprint_snapshot(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_snapshot_bytes(value)).hexdigest()}"


def serialize_specification(specification: BlueprintSpecification) -> JsonObject:
    return cast(JsonObject, _canonicalize(specification))


def serialize_blueprint(blueprint: PaperBlueprint) -> JsonObject:
    return cast(JsonObject, _canonicalize(blueprint))


def deserialize_specification(snapshot: Mapping[str, object]) -> BlueprintSpecification:
    try:
        root = _object(snapshot, "specification", _SPECIFICATION_KEYS)
        return BlueprintSpecification(
            config_version=_string(root["config_version"], "specification.config_version"),
            paper_code=_string(root["paper_code"], "specification.paper_code"),
            title=_string(root["title"], "specification.title"),
            total_marks=_integer(root["total_marks"], "specification.total_marks"),
            curriculum_scope=_curriculum_scope(
                root["curriculum_scope"], "specification.curriculum_scope"
            ),
            sections=tuple(
                _section_specification(item, f"specification.sections[{index}]")
                for index, item in enumerate(_array(root["sections"], "specification.sections"))
            ),
            question_type_allocations=tuple(
                _question_type_allocation(
                    item,
                    f"specification.question_type_allocations[{index}]",
                )
                for index, item in enumerate(
                    _array(
                        root["question_type_allocations"],
                        "specification.question_type_allocations",
                    )
                )
            ),
            difficulty_allocations=tuple(
                _difficulty_allocation(
                    item,
                    f"specification.difficulty_allocations[{index}]",
                )
                for index, item in enumerate(
                    _array(
                        root["difficulty_allocations"],
                        "specification.difficulty_allocations",
                    )
                )
            ),
            taxonomy_requirements=tuple(
                _taxonomy_requirement(
                    item,
                    f"specification.taxonomy_requirements[{index}]",
                )
                for index, item in enumerate(
                    _array(
                        root["taxonomy_requirements"],
                        "specification.taxonomy_requirements",
                    )
                )
            ),
            generation_policy=_generation_policy(
                root["generation_policy"], "specification.generation_policy"
            ),
        )
    except BlueprintSnapshotError:
        raise
    except (BlueprintValidationError, TypeError, ValueError) as error:
        raise BlueprintSnapshotError("specification", str(error)) from error


def deserialize_blueprint(snapshot: Mapping[str, object]) -> PaperBlueprint:
    try:
        root = _object(snapshot, "blueprint", _BLUEPRINT_KEYS)
        return PaperBlueprint(
            version=_blueprint_version(root["version"], "blueprint.version"),
            paper_code=_string(root["paper_code"], "blueprint.paper_code"),
            title=_string(root["title"], "blueprint.title"),
            seed=_integer(root["seed"], "blueprint.seed"),
            total_marks=_integer(root["total_marks"], "blueprint.total_marks"),
            curriculum_scope=_curriculum_scope(
                root["curriculum_scope"], "blueprint.curriculum_scope"
            ),
            sections=tuple(
                _blueprint_section(item, f"blueprint.sections[{index}]")
                for index, item in enumerate(_array(root["sections"], "blueprint.sections"))
            ),
            question_type_allocations=tuple(
                _question_type_allocation(
                    item,
                    f"blueprint.question_type_allocations[{index}]",
                )
                for index, item in enumerate(
                    _array(
                        root["question_type_allocations"],
                        "blueprint.question_type_allocations",
                    )
                )
            ),
            difficulty_allocations=tuple(
                _difficulty_allocation(
                    item,
                    f"blueprint.difficulty_allocations[{index}]",
                )
                for index, item in enumerate(
                    _array(
                        root["difficulty_allocations"],
                        "blueprint.difficulty_allocations",
                    )
                )
            ),
            taxonomy_requirements=tuple(
                _taxonomy_requirement(item, f"blueprint.taxonomy_requirements[{index}]")
                for index, item in enumerate(
                    _array(
                        root["taxonomy_requirements"],
                        "blueprint.taxonomy_requirements",
                    )
                )
            ),
            slots=tuple(
                _blueprint_slot(item, f"blueprint.slots[{index}]")
                for index, item in enumerate(_array(root["slots"], "blueprint.slots"))
            ),
        )
    except BlueprintSnapshotError:
        raise
    except (BlueprintValidationError, TypeError, ValueError) as error:
        raise BlueprintSnapshotError("blueprint", str(error)) from error


_SPECIFICATION_KEYS = frozenset(
    {
        "config_version",
        "paper_code",
        "title",
        "total_marks",
        "curriculum_scope",
        "sections",
        "question_type_allocations",
        "difficulty_allocations",
        "taxonomy_requirements",
        "generation_policy",
    }
)
_BLUEPRINT_KEYS = frozenset(
    {
        "version",
        "paper_code",
        "title",
        "seed",
        "total_marks",
        "curriculum_scope",
        "sections",
        "question_type_allocations",
        "difficulty_allocations",
        "taxonomy_requirements",
        "slots",
    }
)
_TARGET_KEYS = frozenset({"competency_id", "skill_id", "sub_skill_id", "learning_concept_id"})
_PRIORITY_KEYS = frozenset(
    {
        "baseline_score",
        "baseline_version",
        "baseline_evidence_refs",
        "forecast_score",
        "forecast_version",
        "baseline_backtest_score",
        "forecast_backtest_score",
        "minimum_backtest_improvement",
        "forecast_evidence_refs",
    }
)


def _object(value: object, path: str, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise BlueprintSnapshotError(path, "must be an object with string keys")
    actual_keys = frozenset(cast(str, key) for key in value)
    if actual_keys != keys:
        missing = sorted(keys - actual_keys)
        unexpected = sorted(actual_keys - keys)
        raise BlueprintSnapshotError(
            path,
            f"object keys mismatch (missing={missing}, unexpected={unexpected})",
        )
    return cast(Mapping[str, object], value)


def _array(value: object, path: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BlueprintSnapshotError(path, "must be an array")
    return cast(Sequence[object], value)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise BlueprintSnapshotError(path, "must be a string")
    return value


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BlueprintSnapshotError(path, "must be an integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise BlueprintSnapshotError(path, "must be a boolean")
    return value


def _uuid(value: object, path: str) -> UUID:
    try:
        return UUID(_string(value, path))
    except ValueError as error:
        raise BlueprintSnapshotError(path, "must be a UUID") from error


def _optional_uuid(value: object, path: str) -> UUID | None:
    return None if value is None else _uuid(value, path)


def _optional_string(value: object, path: str) -> str | None:
    return None if value is None else _string(value, path)


def _optional_integer(value: object, path: str) -> int | None:
    return None if value is None else _integer(value, path)


def _string_tuple(value: object, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{path}[{index}]") for index, item in enumerate(_array(value, path))
    )


def _enum[EnumT: Enum](enum_type: type[EnumT], value: object, path: str) -> EnumT:
    try:
        return enum_type(_string(value, path))
    except ValueError as error:
        raise BlueprintSnapshotError(path, f"invalid {enum_type.__name__}") from error


def _curriculum_scope(value: object, path: str) -> CurriculumScope:
    root = _object(value, path, frozenset({"curriculum_version_id", "grade", "medium"}))
    return CurriculumScope(
        curriculum_version_id=_uuid(root["curriculum_version_id"], f"{path}.curriculum_version_id"),
        grade=_integer(root["grade"], f"{path}.grade"),
        medium=_string(root["medium"], f"{path}.medium"),
    )


def _taxonomy_target(value: object, path: str) -> TaxonomyTarget:
    root = _object(value, path, _TARGET_KEYS)
    return TaxonomyTarget(
        competency_id=_uuid(root["competency_id"], f"{path}.competency_id"),
        skill_id=_optional_uuid(root["skill_id"], f"{path}.skill_id"),
        sub_skill_id=_optional_uuid(root["sub_skill_id"], f"{path}.sub_skill_id"),
        learning_concept_id=_optional_uuid(
            root["learning_concept_id"], f"{path}.learning_concept_id"
        ),
    )


def _practice_priority(value: object, path: str) -> PracticePriority:
    root = _object(value, path, _PRIORITY_KEYS)
    return PracticePriority(
        baseline_score=_integer(root["baseline_score"], f"{path}.baseline_score"),
        baseline_version=_string(root["baseline_version"], f"{path}.baseline_version"),
        baseline_evidence_refs=_string_tuple(
            root["baseline_evidence_refs"], f"{path}.baseline_evidence_refs"
        ),
        forecast_score=_optional_integer(root["forecast_score"], f"{path}.forecast_score"),
        forecast_version=_optional_string(root["forecast_version"], f"{path}.forecast_version"),
        baseline_backtest_score=_optional_integer(
            root["baseline_backtest_score"], f"{path}.baseline_backtest_score"
        ),
        forecast_backtest_score=_optional_integer(
            root["forecast_backtest_score"], f"{path}.forecast_backtest_score"
        ),
        minimum_backtest_improvement=_integer(
            root["minimum_backtest_improvement"],
            f"{path}.minimum_backtest_improvement",
        ),
        forecast_evidence_refs=_string_tuple(
            root["forecast_evidence_refs"], f"{path}.forecast_evidence_refs"
        ),
    )


def _section_specification(value: object, path: str) -> SectionSpecification:
    root = _object(
        value,
        path,
        frozenset(
            {
                "section_id",
                "title",
                "marks",
                "question_count",
                "allowed_marks_per_slot",
                "allowed_question_types",
                "allowed_difficulties",
                "allowed_taxonomy_targets",
                "retrieval_query_hints",
            }
        ),
    )
    return SectionSpecification(
        section_id=_string(root["section_id"], f"{path}.section_id"),
        title=_string(root["title"], f"{path}.title"),
        marks=_integer(root["marks"], f"{path}.marks"),
        question_count=_integer(root["question_count"], f"{path}.question_count"),
        allowed_marks_per_slot=tuple(
            _integer(item, f"{path}.allowed_marks_per_slot[{index}]")
            for index, item in enumerate(
                _array(root["allowed_marks_per_slot"], f"{path}.allowed_marks_per_slot")
            )
        ),
        allowed_question_types=tuple(
            _enum(QuestionType, item, f"{path}.allowed_question_types[{index}]")
            for index, item in enumerate(
                _array(root["allowed_question_types"], f"{path}.allowed_question_types")
            )
        ),
        allowed_difficulties=tuple(
            _enum(Difficulty, item, f"{path}.allowed_difficulties[{index}]")
            for index, item in enumerate(
                _array(root["allowed_difficulties"], f"{path}.allowed_difficulties")
            )
        ),
        allowed_taxonomy_targets=tuple(
            _taxonomy_target(item, f"{path}.allowed_taxonomy_targets[{index}]")
            for index, item in enumerate(
                _array(
                    root["allowed_taxonomy_targets"],
                    f"{path}.allowed_taxonomy_targets",
                )
            )
        ),
        retrieval_query_hints=_string_tuple(
            root["retrieval_query_hints"], f"{path}.retrieval_query_hints"
        ),
    )


def _question_type_allocation(value: object, path: str) -> QuestionTypeAllocation:
    root = _object(
        value,
        path,
        frozenset({"question_type", "exact_slots", "archetypes", "exact_marks"}),
    )
    return QuestionTypeAllocation(
        question_type=_enum(QuestionType, root["question_type"], f"{path}.question_type"),
        exact_slots=_integer(root["exact_slots"], f"{path}.exact_slots"),
        archetypes=_string_tuple(root["archetypes"], f"{path}.archetypes"),
        exact_marks=_optional_integer(root["exact_marks"], f"{path}.exact_marks"),
    )


def _difficulty_allocation(value: object, path: str) -> DifficultyAllocation:
    root = _object(
        value,
        path,
        frozenset({"difficulty", "exact_slots", "exact_marks"}),
    )
    return DifficultyAllocation(
        difficulty=_enum(Difficulty, root["difficulty"], f"{path}.difficulty"),
        exact_slots=_integer(root["exact_slots"], f"{path}.exact_slots"),
        exact_marks=_optional_integer(root["exact_marks"], f"{path}.exact_marks"),
    )


def _taxonomy_requirement(value: object, path: str) -> TaxonomyRequirement:
    root = _object(
        value,
        path,
        frozenset(
            {
                "target",
                "minimum_slots",
                "priority",
                "retrieval_query_hints",
                "generation_instructions",
                "maximum_slots",
                "allowed_section_ids",
            }
        ),
    )
    return TaxonomyRequirement(
        target=_taxonomy_target(root["target"], f"{path}.target"),
        minimum_slots=_integer(root["minimum_slots"], f"{path}.minimum_slots"),
        priority=_practice_priority(root["priority"], f"{path}.priority"),
        retrieval_query_hints=_string_tuple(
            root["retrieval_query_hints"], f"{path}.retrieval_query_hints"
        ),
        generation_instructions=_string_tuple(
            root["generation_instructions"], f"{path}.generation_instructions"
        ),
        maximum_slots=_optional_integer(root["maximum_slots"], f"{path}.maximum_slots"),
        allowed_section_ids=_string_tuple(
            root["allowed_section_ids"], f"{path}.allowed_section_ids"
        ),
    )


def _uniqueness(value: object, path: str) -> UniquenessPolicy:
    root = _object(
        value,
        path,
        frozenset(
            {
                "forbid_duplicate_stems",
                "forbid_verbatim_sources",
                "max_similarity_basis_points",
                "minimum_distinct_contexts",
            }
        ),
    )
    return UniquenessPolicy(
        forbid_duplicate_stems=_boolean(
            root["forbid_duplicate_stems"], f"{path}.forbid_duplicate_stems"
        ),
        forbid_verbatim_sources=_boolean(
            root["forbid_verbatim_sources"], f"{path}.forbid_verbatim_sources"
        ),
        max_similarity_basis_points=_integer(
            root["max_similarity_basis_points"], f"{path}.max_similarity_basis_points"
        ),
        minimum_distinct_contexts=_integer(
            root["minimum_distinct_contexts"], f"{path}.minimum_distinct_contexts"
        ),
    )


def _generation_policy(value: object, path: str) -> GenerationPolicy:
    root = _object(
        value,
        path,
        frozenset(
            {
                "response_language",
                "instructions",
                "answer_requirements",
                "retrieval_query_hints",
                "uniqueness",
            }
        ),
    )
    return GenerationPolicy(
        response_language=_string(root["response_language"], f"{path}.response_language"),
        instructions=_string_tuple(root["instructions"], f"{path}.instructions"),
        answer_requirements=_string_tuple(
            root["answer_requirements"], f"{path}.answer_requirements"
        ),
        retrieval_query_hints=_string_tuple(
            root["retrieval_query_hints"], f"{path}.retrieval_query_hints"
        ),
        uniqueness=_uniqueness(root["uniqueness"], f"{path}.uniqueness"),
    )


def _blueprint_version(value: object, path: str) -> BlueprintVersion:
    root = _object(
        value,
        path,
        frozenset(
            {
                "blueprint_id",
                "schema_version",
                "algorithm_version",
                "config_version",
                "input_fingerprint",
            }
        ),
    )
    return BlueprintVersion(
        blueprint_id=_string(root["blueprint_id"], f"{path}.blueprint_id"),
        schema_version=_string(root["schema_version"], f"{path}.schema_version"),
        algorithm_version=_string(root["algorithm_version"], f"{path}.algorithm_version"),
        config_version=_string(root["config_version"], f"{path}.config_version"),
        input_fingerprint=_string(root["input_fingerprint"], f"{path}.input_fingerprint"),
    )


def _blueprint_section(value: object, path: str) -> BlueprintSection:
    root = _object(value, path, frozenset({"section_id", "title", "marks", "slot_count"}))
    return BlueprintSection(
        section_id=_string(root["section_id"], f"{path}.section_id"),
        title=_string(root["title"], f"{path}.title"),
        marks=_integer(root["marks"], f"{path}.marks"),
        slot_count=_integer(root["slot_count"], f"{path}.slot_count"),
    )


def _slot_generation_constraints(value: object, path: str) -> SlotGenerationConstraints:
    root = _object(
        value,
        path,
        frozenset(
            {
                "curriculum_scope",
                "taxonomy_target",
                "required_question_type",
                "required_archetype",
                "required_difficulty",
                "exact_marks",
                "response_language",
                "instructions",
                "answer_requirements",
                "retrieval_query_hints",
                "uniqueness",
                "diversity_key",
            }
        ),
    )
    return SlotGenerationConstraints(
        curriculum_scope=_curriculum_scope(root["curriculum_scope"], f"{path}.curriculum_scope"),
        taxonomy_target=_taxonomy_target(root["taxonomy_target"], f"{path}.taxonomy_target"),
        required_question_type=_enum(
            QuestionType,
            root["required_question_type"],
            f"{path}.required_question_type",
        ),
        required_archetype=_string(root["required_archetype"], f"{path}.required_archetype"),
        required_difficulty=_enum(
            Difficulty,
            root["required_difficulty"],
            f"{path}.required_difficulty",
        ),
        exact_marks=_integer(root["exact_marks"], f"{path}.exact_marks"),
        response_language=_string(root["response_language"], f"{path}.response_language"),
        instructions=_string_tuple(root["instructions"], f"{path}.instructions"),
        answer_requirements=_string_tuple(
            root["answer_requirements"], f"{path}.answer_requirements"
        ),
        retrieval_query_hints=_string_tuple(
            root["retrieval_query_hints"], f"{path}.retrieval_query_hints"
        ),
        uniqueness=_uniqueness(root["uniqueness"], f"{path}.uniqueness"),
        diversity_key=_string(root["diversity_key"], f"{path}.diversity_key"),
    )


def _slot_rationale(value: object, path: str) -> SlotRationale:
    root = _object(
        value,
        path,
        frozenset({"priority_mode", "effective_priority_score", "summary"}),
    )
    return SlotRationale(
        priority_mode=_enum(PriorityMode, root["priority_mode"], f"{path}.priority_mode"),
        effective_priority_score=_integer(
            root["effective_priority_score"], f"{path}.effective_priority_score"
        ),
        summary=_string(root["summary"], f"{path}.summary"),
    )


def _slot_evidence(value: object, path: str) -> SlotEvidence:
    root = _object(
        value,
        path,
        frozenset(
            {
                "config_version",
                "baseline_version",
                "baseline_score",
                "evidence_refs",
                "forecast_version",
                "forecast_score",
                "baseline_backtest_score",
                "forecast_backtest_score",
                "minimum_backtest_improvement",
            }
        ),
    )
    return SlotEvidence(
        config_version=_string(root["config_version"], f"{path}.config_version"),
        baseline_version=_string(root["baseline_version"], f"{path}.baseline_version"),
        baseline_score=_integer(root["baseline_score"], f"{path}.baseline_score"),
        evidence_refs=_string_tuple(root["evidence_refs"], f"{path}.evidence_refs"),
        forecast_version=_optional_string(root["forecast_version"], f"{path}.forecast_version"),
        forecast_score=_optional_integer(root["forecast_score"], f"{path}.forecast_score"),
        baseline_backtest_score=_optional_integer(
            root["baseline_backtest_score"], f"{path}.baseline_backtest_score"
        ),
        forecast_backtest_score=_optional_integer(
            root["forecast_backtest_score"], f"{path}.forecast_backtest_score"
        ),
        minimum_backtest_improvement=_integer(
            root["minimum_backtest_improvement"],
            f"{path}.minimum_backtest_improvement",
        ),
    )


def _blueprint_slot(value: object, path: str) -> BlueprintSlot:
    root = _object(
        value,
        path,
        frozenset(
            {
                "slot_id",
                "ordinal",
                "paper_code",
                "section_id",
                "section_title",
                "section_ordinal",
                "taxonomy_target",
                "question_type",
                "archetype",
                "difficulty",
                "marks",
                "generation_constraints",
                "rationale",
                "evidence",
            }
        ),
    )
    return BlueprintSlot(
        slot_id=_string(root["slot_id"], f"{path}.slot_id"),
        ordinal=_integer(root["ordinal"], f"{path}.ordinal"),
        paper_code=_string(root["paper_code"], f"{path}.paper_code"),
        section_id=_string(root["section_id"], f"{path}.section_id"),
        section_title=_string(root["section_title"], f"{path}.section_title"),
        section_ordinal=_integer(root["section_ordinal"], f"{path}.section_ordinal"),
        taxonomy_target=_taxonomy_target(root["taxonomy_target"], f"{path}.taxonomy_target"),
        question_type=_enum(QuestionType, root["question_type"], f"{path}.question_type"),
        archetype=_string(root["archetype"], f"{path}.archetype"),
        difficulty=_enum(Difficulty, root["difficulty"], f"{path}.difficulty"),
        marks=_integer(root["marks"], f"{path}.marks"),
        generation_constraints=_slot_generation_constraints(
            root["generation_constraints"], f"{path}.generation_constraints"
        ),
        rationale=_slot_rationale(root["rationale"], f"{path}.rationale"),
        evidence=_slot_evidence(root["evidence"], f"{path}.evidence"),
    )
