from copy import deepcopy
from dataclasses import replace
from enum import Enum
from typing import Any, cast

import pytest

from exam_guru_api.blueprints import generate_blueprint
from exam_guru_api.blueprints.serialization import (
    BlueprintSnapshotError,
    deserialize_blueprint,
    deserialize_specification,
    fingerprint_snapshot,
    serialize_blueprint,
    serialize_specification,
)
from tests.test_blueprint_domain import make_specification


def test_canonical_specification_and_blueprint_snapshots_round_trip_without_loss() -> None:
    specification = make_specification()
    blueprint = generate_blueprint(specification, seed=2025)

    specification_snapshot = serialize_specification(specification)
    blueprint_snapshot = serialize_blueprint(blueprint)

    assert deserialize_specification(specification_snapshot) == specification
    assert deserialize_blueprint(blueprint_snapshot) == blueprint
    assert specification_snapshot["curriculum_scope"] == {
        "curriculum_version_id": str(specification.curriculum_scope.curriculum_version_id),
        "grade": 5,
        "medium": "si",
        "subject_id": str(specification.curriculum_scope.subject_id),
        "unit_ids": [],
        "lesson_ids": [],
    }
    serialized_slots = cast(list[dict[str, Any]], blueprint_snapshot["slots"])
    assert len(serialized_slots) == len(blueprint.slots)
    assert fingerprint_snapshot(specification_snapshot) == (
        f"sha256:{blueprint.version.input_fingerprint}"
    )
    assert fingerprint_snapshot(blueprint_snapshot).startswith("sha256:")
    assert fingerprint_snapshot(blueprint_snapshot) == fingerprint_snapshot(
        serialize_blueprint(generate_blueprint(specification, seed=2025))
    )


def test_curriculum_scope_deserialization_accepts_legacy_and_rejects_bad_shapes() -> None:
    current = serialize_specification(make_specification())
    legacy = deepcopy(current)
    legacy_scope = cast(dict[str, object], legacy["curriculum_scope"])
    for field_name in ("subject_id", "unit_ids", "lesson_ids"):
        del legacy_scope[field_name]
    assert (
        deserialize_specification(legacy).curriculum_scope == make_specification().curriculum_scope
    )

    non_object = deepcopy(current)
    non_object["curriculum_scope"] = []
    with pytest.raises(BlueprintSnapshotError, match="curriculum_scope"):
        deserialize_specification(non_object)
    unexpected = deepcopy(current)
    cast(dict[str, object], unexpected["curriculum_scope"])["unexpected"] = True
    with pytest.raises(BlueprintSnapshotError, match="curriculum_scope"):
        deserialize_specification(unexpected)


def test_snapshot_deserialization_revalidates_nested_domain_invariants() -> None:
    specification = serialize_specification(make_specification())
    blueprint = serialize_blueprint(generate_blueprint(make_specification(), seed=7))

    invalid_specification = {**specification, "unexpected": True}
    slots = cast(list[dict[str, Any]], blueprint["slots"])
    invalid_blueprint = {
        **blueprint,
        "slots": [
            {**slots[0], "marks": slots[0]["marks"] + 1},
            *slots[1:],
        ],
    }

    with pytest.raises(BlueprintSnapshotError):
        deserialize_specification(invalid_specification)
    with pytest.raises(BlueprintSnapshotError):
        deserialize_blueprint(invalid_blueprint)


def test_snapshot_fingerprint_is_order_independent_but_type_strict() -> None:
    first = {"b": [2, 1], "a": {"value": "x"}}
    second = {"a": {"value": "x"}, "b": [2, 1]}

    assert fingerprint_snapshot(first) == fingerprint_snapshot(second)
    assert fingerprint_snapshot({"value": True}) != fingerprint_snapshot({"value": 1})

    with pytest.raises(TypeError):
        fingerprint_snapshot({"invalid": replace})


class UnsupportedEnum(Enum):
    VALUE = 1.5


def test_snapshot_canonicalization_rejects_unsupported_enums_and_non_string_keys() -> None:
    with pytest.raises(TypeError, match="unsupported enum"):
        fingerprint_snapshot(UnsupportedEnum.VALUE)
    with pytest.raises(TypeError, match="keys must be strings"):
        fingerprint_snapshot(cast(dict[str, object], {1: "invalid"}))


def test_snapshot_deserializers_report_every_primitive_shape_boundary() -> None:
    specification = cast(dict[str, Any], serialize_specification(make_specification()))
    blueprint = cast(
        dict[str, Any],
        serialize_blueprint(generate_blueprint(make_specification(), seed=7)),
    )

    domain_invalid = deepcopy(specification)
    domain_invalid["total_marks"] = 0
    with pytest.raises(BlueprintSnapshotError, match="specification"):
        deserialize_specification(domain_invalid)

    malformed_specifications = []
    not_an_object = cast(dict[str, object], [])
    malformed_specifications.append(not_an_object)
    invalid_array = deepcopy(specification)
    invalid_array["sections"] = "not-an-array"
    malformed_specifications.append(invalid_array)
    invalid_string = deepcopy(specification)
    invalid_string["config_version"] = 1
    malformed_specifications.append(invalid_string)
    invalid_integer = deepcopy(specification)
    invalid_integer["total_marks"] = True
    malformed_specifications.append(invalid_integer)
    invalid_boolean = deepcopy(specification)
    invalid_boolean["generation_policy"]["uniqueness"]["forbid_duplicate_stems"] = "yes"
    malformed_specifications.append(invalid_boolean)
    invalid_uuid = deepcopy(specification)
    invalid_uuid["curriculum_scope"]["curriculum_version_id"] = "not-a-uuid"
    malformed_specifications.append(invalid_uuid)
    invalid_enum = deepcopy(specification)
    invalid_enum["question_type_allocations"][0]["question_type"] = "essay"
    malformed_specifications.append(invalid_enum)

    for malformed in malformed_specifications:
        with pytest.raises(BlueprintSnapshotError):
            deserialize_specification(malformed)

    with pytest.raises(BlueprintSnapshotError):
        deserialize_blueprint({"unexpected": blueprint})
