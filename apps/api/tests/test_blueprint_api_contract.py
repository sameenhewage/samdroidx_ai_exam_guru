from copy import deepcopy
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.blueprints.schemas import (
    BlueprintCreateRequest,
    PaperBlueprintResponse,
    PaperBlueprintSummaryResponse,
)
from exam_guru_api.blueprints.serialization import serialize_specification
from exam_guru_api.main import create_app
from tests.test_blueprint_domain import make_uniform_specification

BLUEPRINTS_PATH = "/api/v1/admin/curricula/{curriculum_version_id}/blueprints"
BLUEPRINT_PATH = BLUEPRINTS_PATH + "/{paper_blueprint_id}"


def baseline_request_payload() -> dict[str, Any]:
    specification = cast(
        dict[str, Any],
        deepcopy(serialize_specification(make_uniform_specification((2,), 2))),
    )
    for requirement in specification["taxonomy_requirements"]:
        priority = requirement["priority"]
        requirement["priority"] = {
            "baseline_score": priority["baseline_score"],
            "baseline_version": priority["baseline_version"],
            "baseline_evidence_refs": priority["baseline_evidence_refs"],
        }
    return {"seed": 2025, "analytics_run_id": None, "specification": specification}


def test_blueprint_openapi_exposes_authorized_bounded_resources_and_typed_snapshots() -> None:
    schema = create_app().openapi()

    assert {"get", "post"} <= set(schema["paths"][BLUEPRINTS_PATH])
    assert "get" in schema["paths"][BLUEPRINT_PATH]
    for path, method in (
        (BLUEPRINTS_PATH, "post"),
        (BLUEPRINTS_PATH, "get"),
        (BLUEPRINT_PATH, "get"),
    ):
        assert schema["paths"][path][method]["security"] == [{"HTTPBearer": []}]

    parameters = {
        item["name"]: item for item in schema["paths"][BLUEPRINTS_PATH]["get"]["parameters"]
    }
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["offset"]["schema"]["minimum"] == 0
    assert parameters["offset"]["schema"]["maximum"] == 100_000

    request = schema["components"]["schemas"]["BlueprintCreateRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"analytics_run_id", "seed", "specification"}
    specification = schema["components"]["schemas"]["BlueprintSpecificationRequest"]
    assert specification["additionalProperties"] is False
    assert specification["properties"]["sections"]["maxItems"] == 20
    assert specification["properties"]["taxonomy_requirements"]["maxItems"] == 200
    baseline = schema["components"]["schemas"]["BaselinePracticePriorityRequest"]
    assert set(baseline["properties"]) == {
        "baseline_score",
        "baseline_version",
        "baseline_evidence_refs",
    }
    assert baseline["additionalProperties"] is False

    create_operation = schema["paths"][BLUEPRINTS_PATH]["post"]
    for status_code in ("200", "201"):
        assert create_operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/PaperBlueprintResponse"}
    for status_code in ("404", "409", "422"):
        assert create_operation["responses"][status_code]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ApiErrorResponse"}

    response = schema["components"]["schemas"]["PaperBlueprintResponse"]
    assert response["properties"]["specification"] == {
        "$ref": "#/components/schemas/BlueprintSpecificationResponse"
    }
    assert response["properties"]["blueprint"] == {
        "$ref": "#/components/schemas/PaperBlueprintSnapshotResponse"
    }
    assert response["properties"]["taxonomy_snapshot"]["items"] == {
        "$ref": "#/components/schemas/ReviewedTaxonomyNodeSnapshotResponse"
    }


def test_blueprint_create_contract_accepts_only_explicit_baseline_safe_priorities() -> None:
    payload = baseline_request_payload()

    request = BlueprintCreateRequest.model_validate(payload)

    assert request.seed == 2025
    specification = request.to_domain()
    assert all(
        requirement.priority.forecast_score is None
        for requirement in specification.taxonomy_requirements
    )
    assert all(
        requirement.priority.baseline_evidence_refs
        for requirement in specification.taxonomy_requirements
    )

    spoofed = deepcopy(payload)
    spoofed["specification"]["taxonomy_requirements"][0]["priority"]["forecast_score"] = 999
    spoofed["specification"]["taxonomy_requirements"][0]["priority"]["forecast_version"] = (
        "client-forged"
    )
    with pytest.raises(ValidationError):
        BlueprintCreateRequest.model_validate(spoofed)

    missing_baseline = deepcopy(payload)
    del missing_baseline["specification"]["taxonomy_requirements"][0]["priority"]
    with pytest.raises(ValidationError):
        BlueprintCreateRequest.model_validate(missing_baseline)


def test_grade_seven_math_lessons_one_to_three_and_full_subject_api_scopes() -> None:
    payload = baseline_request_payload()
    scope = payload["specification"]["curriculum_scope"]
    scope.update(
        {
            "grade": 7,
            "medium": "en",
            "subject_id": str(UUID(int=7_001)),
            "unit_ids": [str(UUID(int=7_010))],
            "lesson_ids": [
                str(UUID(int=7_011)),
                str(UUID(int=7_012)),
                str(UUID(int=7_013)),
            ],
        }
    )

    lessons_one_to_three = BlueprintCreateRequest.model_validate(payload).to_domain()
    assert lessons_one_to_three.curriculum_scope.grade == 7
    assert lessons_one_to_three.curriculum_scope.subject_id == UUID(int=7_001)
    assert lessons_one_to_three.curriculum_scope.lesson_ids == (
        UUID(int=7_011),
        UUID(int=7_012),
        UUID(int=7_013),
    )

    full_payload = deepcopy(payload)
    full_scope = full_payload["specification"]["curriculum_scope"]
    full_scope["unit_ids"] = []
    full_scope["lesson_ids"] = []
    full_subject = BlueprintCreateRequest.model_validate(full_payload).to_domain()
    assert full_subject.curriculum_scope.grade == 7
    assert full_subject.curriculum_scope.unit_ids == ()
    assert full_subject.curriculum_scope.lesson_ids == ()


def test_blueprint_request_bounds_seed_nested_collections_and_scope() -> None:
    payload = baseline_request_payload()
    payload["seed"] = 2**63
    with pytest.raises(ValidationError):
        BlueprintCreateRequest.model_validate(payload)

    payload = baseline_request_payload()
    payload["specification"]["curriculum_scope"]["curriculum_version_id"] = str(UUID(int=999))
    request = BlueprintCreateRequest.model_validate(payload)
    assert request.specification.curriculum_scope.curriculum_version_id == UUID(int=999)

    payload = baseline_request_payload()
    payload["specification"]["sections"] *= 21
    with pytest.raises(ValidationError):
        BlueprintCreateRequest.model_validate(payload)


def test_blueprint_response_factories_reject_non_repository_records() -> None:
    with pytest.raises(TypeError, match="PaperBlueprintRecord"):
        PaperBlueprintResponse.from_record(object())
    with pytest.raises(TypeError, match="PaperBlueprintRecord"):
        PaperBlueprintSummaryResponse.from_record(object())
