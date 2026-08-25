from copy import deepcopy
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
from exam_guru_api.generation.schemas import (
    GenerationAttemptResponse,
    GenerationJobResponse,
    GenerationRunCreateRequest,
    GenerationRunResponse,
    GenerationRunSummaryResponse,
)
from exam_guru_api.main import create_app

CURRICULUM_ID = UUID(int=910_001)
BLUEPRINT_ID = UUID(int=910_002)
RUN_ID = UUID(int=910_003)
JOB_ID = UUID(int=910_004)
BASE_PATH = "/api/v1/admin/curricula/{curriculum_version_id}/generation-runs"
RUN_PATH = BASE_PATH + "/{generation_run_id}"
RETRY_PATH = RUN_PATH + "/retry"
ATTEMPTS_PATH = RUN_PATH + "/attempts"
JOB_PATH = "/api/v1/admin/curricula/{curriculum_version_id}/generation-jobs/{generation_job_id}"


def valid_payload() -> dict[str, object]:
    return {
        "paper_blueprint_id": str(BLUEPRINT_ID),
        "slot_id": "slot-001",
        "knowledge_chunk_ids": [str(UUID(int=910_011))],
        "historical_question_ids": [str(UUID(int=910_012))],
    }


def test_generation_permissions_separate_admin_commands_from_reviewer_reads() -> None:
    admin = Principal(UUID(int=1), frozenset({AdminRole.ADMIN}))
    reviewer = Principal(UUID(int=2), frozenset({AdminRole.REVIEWER}))

    assert authorize(admin, Permission.GENERATION_RUN) is admin
    assert authorize(admin, Permission.GENERATION_READ) is admin
    assert authorize(reviewer, Permission.GENERATION_READ) is reviewer
    with pytest.raises(AuthorizationError):
        authorize(reviewer, Permission.GENERATION_RUN)


def test_create_contract_accepts_only_bounded_server_resolved_identifiers() -> None:
    request = GenerationRunCreateRequest.model_validate(valid_payload())

    assert request.paper_blueprint_id == BLUEPRINT_ID
    assert request.slot_id == "slot-001"
    assert len(request.context_references) == 2

    forbidden_fields: dict[str, object] = {
        "source_text": "client supplied source",
        "query_vector": [0.1, 0.2],
        "trusted_instructions": "ignore policy",
        "provider_api_key": "not-allowed",  # pragma: allowlist secret
        "provider": "openai",
        "model": "expensive-model",
        "pricing": {"input": 0},
        "publish_state": "published",
        "temperature": 2.0,
    }
    for field, value in forbidden_fields.items():
        payload = deepcopy(valid_payload())
        payload[field] = value
        with pytest.raises(ValidationError):
            GenerationRunCreateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"slot_id": " leading-space"},
        {"knowledge_chunk_ids": [], "historical_question_ids": []},
        {"knowledge_chunk_ids": [str(UUID(int=index + 1)) for index in range(17)]},
        {"knowledge_chunk_ids": [str(UUID(int=1)), str(UUID(int=1))]},
        {
            "historical_question_ids": [str(UUID(int=2)), str(UUID(int=2))],
            "knowledge_chunk_ids": [],
        },
    ],
)
def test_create_contract_rejects_invalid_or_unbounded_context(change: dict[str, object]) -> None:
    payload = valid_payload()
    payload.update(change)

    with pytest.raises(ValidationError):
        GenerationRunCreateRequest.model_validate(payload)


def test_generation_openapi_exposes_async_jobs_and_bounded_authorized_reads() -> None:
    schema = create_app().openapi()

    assert "post" in schema["paths"][BASE_PATH]
    assert "get" in schema["paths"][BASE_PATH]
    assert "get" in schema["paths"][RUN_PATH]
    assert "post" in schema["paths"][RETRY_PATH]
    assert "get" in schema["paths"][ATTEMPTS_PATH]
    assert "get" in schema["paths"][JOB_PATH]
    for path, method in (
        (BASE_PATH, "post"),
        (BASE_PATH, "get"),
        (RUN_PATH, "get"),
        (RETRY_PATH, "post"),
        (ATTEMPTS_PATH, "get"),
        (JOB_PATH, "get"),
    ):
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"HTTPBearer": []}]

    create_operation = schema["paths"][BASE_PATH]["post"]
    assert create_operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GenerationJobResponse"
    }
    request_schema = schema["components"]["schemas"]["GenerationRunCreateRequest"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {
        "paper_blueprint_id",
        "slot_id",
        "knowledge_chunk_ids",
        "historical_question_ids",
    }
    assert request_schema["properties"]["knowledge_chunk_ids"]["maxItems"] == 16
    assert request_schema["properties"]["historical_question_ids"]["maxItems"] == 16

    for response_name in ("GenerationRunSummaryResponse", "GenerationRunResponse"):
        retry_depth = schema["components"]["schemas"][response_name]["properties"]["retry_depth"]
        assert retry_depth["minimum"] == 0
        assert retry_depth["maximum"] == 3
        assert "retry_depth" in schema["components"]["schemas"][response_name]["required"]

    list_parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][BASE_PATH]["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"]["maximum"] == 100
    assert list_parameters["offset"]["schema"]["maximum"] == 100_000

    attempt_parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][ATTEMPTS_PATH]["get"]["parameters"]
    }
    assert attempt_parameters["limit"]["schema"]["maximum"] == 10


def test_generation_response_factories_reject_wrong_models_and_malformed_context() -> None:
    factories = (
        GenerationJobResponse.from_model,
        GenerationRunSummaryResponse.from_model,
        GenerationRunResponse.from_model,
        GenerationAttemptResponse.from_model,
    )
    for factory in factories:
        with pytest.raises(TypeError):
            factory(object())

    from tests.test_generation_repository import run_model

    run = run_model()
    run.context_snapshot = {"trust": "untrusted_data"}
    with pytest.raises(TypeError, match="item list"):
        GenerationRunResponse.from_model(run)
