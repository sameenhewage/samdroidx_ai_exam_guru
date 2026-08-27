from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.auth.domain import (
    AdminRole,
    AuthorizationError,
    Permission,
    Principal,
    authorize,
)
from exam_guru_api.main import create_app
from exam_guru_api.validation import build_default_pipeline
from exam_guru_api.validation.schemas import (
    ValidationFindingResponse,
    ValidationRunCreateRequest,
    ValidationRunResponse,
    ValidationRunSummaryResponse,
)

CURRICULUM_ID = UUID(int=930_001)
GENERATION_RUN_ID = UUID(int=930_002)
VALIDATION_RUN_ID = UUID(int=930_003)
BASE_PATH = "/api/v1/admin/curricula/{curriculum_version_id}/validation-runs"
RUN_PATH = BASE_PATH + "/{validation_run_id}"
FINDINGS_PATH = RUN_PATH + "/findings"


def test_validation_permissions_separate_admin_commands_from_reviewer_reads() -> None:
    admin = Principal(UUID(int=1), frozenset({AdminRole.ADMIN}))
    reviewer = Principal(UUID(int=2), frozenset({AdminRole.REVIEWER}))

    assert authorize(admin, Permission.VALIDATION_RUN) is admin
    assert authorize(admin, Permission.VALIDATION_READ) is admin
    assert authorize(reviewer, Permission.VALIDATION_READ) is reviewer
    with pytest.raises(AuthorizationError):
        authorize(reviewer, Permission.VALIDATION_RUN)


def test_create_contract_accepts_only_a_generation_run_identifier() -> None:
    payload: dict[str, object] = {"generation_run_id": str(GENERATION_RUN_ID)}

    request = ValidationRunCreateRequest.model_validate(payload)

    assert request.generation_run_id == GENERATION_RUN_ID
    forbidden_fields: dict[str, object] = {
        "candidate": {"stem": "client supplied"},
        "context": [{"text": "client supplied"}],
        "findings": [],
        "overall_status": "pass",
        "passed": True,
        "pipeline_version": "client-pipeline",
        "validators": ["skip-grounding"],
        "validator_id": "client-selected-validator",
        "subject_id": str(UUID(int=999)),
        "subject_code": "MATHEMATICS",
        "grade": 5,
        "medium": "en",
        "unit_ids": [],
        "lesson_ids": [],
        "duplicate_references": [],
        "minimum_age": 1,
    }
    for field_name, value in forbidden_fields.items():
        invalid = deepcopy(payload)
        invalid[field_name] = value
        with pytest.raises(ValidationError):
            ValidationRunCreateRequest.model_validate(invalid)


def test_validation_pipeline_fingerprint_binds_versioned_validator_composition() -> None:
    pipeline = build_default_pipeline()
    same = build_default_pipeline()
    changed = replace(pipeline, version="deterministic-question-validation.v6")

    assert pipeline.pipeline_fingerprint == same.pipeline_fingerprint
    assert len(pipeline.pipeline_fingerprint) == 64
    assert changed.pipeline_fingerprint != pipeline.pipeline_fingerprint


def test_validation_openapi_exposes_bounded_authorized_run_and_finding_reads() -> None:
    schema = create_app().openapi()

    assert "post" in schema["paths"][BASE_PATH]
    assert "get" in schema["paths"][BASE_PATH]
    assert "get" in schema["paths"][RUN_PATH]
    assert "get" in schema["paths"][FINDINGS_PATH]
    for path, method in (
        (BASE_PATH, "post"),
        (BASE_PATH, "get"),
        (RUN_PATH, "get"),
        (FINDINGS_PATH, "get"),
    ):
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"HTTPBearer": []}]

    create_operation = schema["paths"][BASE_PATH]["post"]
    assert create_operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ValidationRunResponse"
    }
    request_schema = schema["components"]["schemas"]["ValidationRunCreateRequest"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {"generation_run_id"}

    list_parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][BASE_PATH]["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"]["maximum"] == 100
    assert list_parameters["offset"]["schema"]["maximum"] == 100_000
    finding_parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][FINDINGS_PATH]["get"]["parameters"]
    }
    assert finding_parameters["limit"]["schema"]["maximum"] == 100
    assert finding_parameters["offset"]["schema"]["maximum"] == 10_000


def test_validation_response_factories_reject_wrong_model_types() -> None:
    for factory in (
        ValidationRunSummaryResponse.from_model,
        ValidationRunResponse.from_model,
        ValidationFindingResponse.from_model,
    ):
        with pytest.raises(TypeError):
            factory(object())
