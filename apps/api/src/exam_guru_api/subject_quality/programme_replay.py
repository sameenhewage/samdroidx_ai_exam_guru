from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import cast
from uuid import UUID

from exam_guru_api.generation.domain import ProgrammeContextBinding
from exam_guru_api.retrieval.domain import (
    RetrievalScope,
    RetrievalScopeSet,
    deserialize_retrieval_filters,
    deserialize_retrieval_scope,
)


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("programme replay requires structured evidence")
    return cast(Mapping[str, object], value)


def _identity(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("programme replay identity must be canonical UUID text")
    try:
        identifier = UUID(value)
    except ValueError as error:
        raise ValueError("programme replay identity is invalid") from error
    if str(identifier) != value:
        raise ValueError("programme replay identity must be canonical UUID text")
    return identifier


def _rows(value: object, *, maximum: int) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError("programme replay evidence exceeds its bounds")
    return tuple(_object(row) for row in value)


def _source_scope(value: object) -> RetrievalScope:
    source = _object(value)
    return deserialize_retrieval_scope(
        {
            "grade": source.get("grade"),
            "exam_id": source.get("exam_configuration_id"),
            "medium_id": source.get("medium_id"),
            "subject_id": source.get("subject_id"),
            "curriculum_version_id": source.get("curriculum_version_id"),
            "unit_ids": [] if source.get("unit_id") is None else [source["unit_id"]],
            "lesson_ids": [] if source.get("lesson_id") is None else [source["lesson_id"]],
            "taxonomy": {
                key: source.get(key)
                for key in ("competency_id", "skill_id", "sub_skill_id", "learning_concept_id")
            },
        }
    )


def programme_replay_scopes(
    evidence: object,
    *,
    expected_curriculum_version_id: UUID,
    subject_scope: Mapping[str, object],
    blueprint: Mapping[str, object],
    generation: Mapping[str, object],
) -> dict[str, RetrievalScope]:
    root = _object(evidence)
    if (
        set(root)
        != {
            "schema_version",
            "generation_run_id",
            "medium",
            "binding",
            "policy_snapshot",
            "slot",
            "filters",
            "sources",
        }
        or root["schema_version"] != "programme-replay-evidence.v1"
    ):
        raise ValueError("programme replay evidence has an invalid version or shape")
    if str(_identity(root["generation_run_id"])) != generation.get("generation_run_id"):
        raise ValueError("programme replay evidence belongs to another generation")
    binding = ProgrammeContextBinding.from_snapshot(root["binding"])
    policy = _object(root["policy_snapshot"])
    encoded = json.dumps(
        policy, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if (
        len(encoded) > 1_048_576
        or hashlib.sha256(encoded).hexdigest() != binding.policy_content_hash
    ):
        raise ValueError("programme replay policy fingerprint is invalid")
    curriculum_id = str(expected_curriculum_version_id)
    medium = _object(root["medium"])
    medium_id = _identity(medium.get("id"))
    if (
        policy.get("schema") != "assessment-programme-policy.v1"
        or policy.get("id") != str(binding.policy_id)
        or policy.get("anchor_curriculum_version_id") != curriculum_id
        or policy.get("medium_id") != medium.get("id")
        or medium.get("code") != subject_scope.get("medium")
    ):
        raise ValueError("programme replay policy conflicts with the trusted target")
    slot = _object(root["slot"])
    constraints = _object(slot.get("generation_constraints"))
    target_scope = _object(constraints.get("curriculum_scope"))
    if (
        target_scope.get("curriculum_version_id") != curriculum_id
        or any(
            target_scope.get(key) != subject_scope.get(key)
            for key in ("grade", "medium", "subject_id", "unit_ids", "lesson_ids")
        )
        or type(target_scope.get("grade")) is not int
        or type(slot.get("marks")) is not int
        or any(slot.get(key) != blueprint.get(key) for key in ("slot_id", "question_type", "marks"))
    ):
        raise ValueError("programme replay slot conflicts with the trusted target")
    mappings = _rows(policy.get("scopes"), maximum=128)
    for row in mappings:
        _identity(row.get("id"))
        ordinal, part = row.get("ordinal"), row.get("part")
        if (
            type(ordinal) is not int
            or not 1 <= ordinal <= 64
            or not isinstance(part, str)
            or part not in {"paper_i", "paper_ii"}
        ):
            raise ValueError("programme replay mapping metadata is invalid")
    if len({(row["part"], row["ordinal"]) for row in mappings}) != len(mappings):
        raise ValueError("programme replay mapping ordinals are duplicated")
    by_id = {cast(str, row["id"]): row for row in mappings}
    if len(by_id) != len(mappings):
        raise ValueError("programme replay policy scope identities are duplicated")
    selected = tuple(by_id.get(str(identifier)) for identifier in binding.scope_ids)
    if any(row is None for row in selected):
        raise ValueError("programme replay mapping is not part of its policy")
    first = cast(Mapping[str, object], selected[0])
    anchor = _object(first.get("anchor"))
    part = first.get("part")
    taxonomy = _object(slot.get("taxonomy_target"))
    if set(taxonomy) != {"competency_id", "skill_id", "sub_skill_id", "learning_concept_id"}:
        raise ValueError("programme replay slot taxonomy is malformed")
    if (
        part not in {"paper_i", "paper_ii"}
        or slot.get("section_id") != f"{part}-{slot.get('question_type')}"
    ):
        raise ValueError("programme replay part differs from its slot")
    if (
        anchor.get("curriculum_version_id") != curriculum_id
        or not isinstance(target_scope.get("unit_ids"), list)
        or anchor.get("unit_id") not in cast(list[object], target_scope["unit_ids"])
        or not isinstance(target_scope.get("lesson_ids"), list)
        or anchor.get("lesson_id") not in cast(list[object], target_scope["lesson_ids"])
        or {key: anchor.get(key) for key in taxonomy} != dict(taxonomy)
    ):
        raise ValueError("programme replay anchor differs from its slot")
    matching = tuple(
        row for row in mappings if row.get("part") == part and row.get("anchor") == anchor
    )
    if {row.get("id") for row in matching} != {str(identifier) for identifier in binding.scope_ids}:
        raise ValueError("programme replay mapping membership is incomplete")
    expected_scopes = tuple(
        dict.fromkeys(
            _source_scope(row.get("source"))
            for row in sorted(matching, key=lambda row: cast(int, row["ordinal"]))
        )
    )
    filters = deserialize_retrieval_filters(root["filters"])
    if (
        not isinstance(filters, RetrievalScopeSet)
        or filters.scopes != expected_scopes
        or any(scope.medium_id != medium_id for scope in expected_scopes)
    ):
        raise ValueError("programme replay filters differ from the reviewed policy")
    sources = _object(root["sources"])
    if not 1 <= len(sources) <= 16:
        raise ValueError("programme replay source set exceeds its bounds")
    result: dict[str, RetrievalScope] = {}
    for context_id, value in sources.items():
        if not isinstance(context_id, str) or not context_id or len(context_id) > 256:
            raise ValueError("programme replay context identity is invalid")
        scope = deserialize_retrieval_scope(value)
        if not filters.allows(scope):
            raise ValueError("programme replay source crosses its reviewed policy")
        result[context_id] = scope
    return result
