from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.auth.domain import AdminRole, Permission, Principal, authorize
from exam_guru_api.main import create_app
from exam_guru_api.papers.schemas import (
    ReviewCandidateApproveRequest,
    ReviewCandidateCreateRequest,
    ReviewCandidateEditRequest,
    ReviewCandidateRejectRequest,
    ReviewCandidateResponse,
    ReviewCandidateStartRequest,
    ReviewCandidateSummaryResponse,
)

CURRICULUM_ID = UUID(int=990_001)
VALIDATION_RUN_ID = UUID(int=990_002)
CANDIDATE_ID = UUID(int=990_003)
BASE_PATH = "/api/v1/admin/curricula/{curriculum_version_id}/review-candidates"
CANDIDATE_PATH = BASE_PATH + "/{candidate_id}"
START_PATH = CANDIDATE_PATH + "/start-review"
APPROVE_PATH = CANDIDATE_PATH + "/approve"
REJECT_PATH = CANDIDATE_PATH + "/reject"


def content_payload() -> dict[str, object]:
    return {
        "question_type": "multiple_choice",
        "stem": "Which answer is supported by the reviewed source?",
        "options": [
            {"option_id": "A", "text": "First answer"},
            {"option_id": "B", "text": "Second answer"},
        ],
        "answer": "B",
        "explanation": "The reviewed source supports the second answer.",
        "marks": 2,
        "marking_guide": ["Award two marks for selecting B."],
    }


def test_reviewer_and_admin_share_the_content_review_boundary() -> None:
    reviewer = Principal(UUID(int=1), frozenset({AdminRole.REVIEWER}))
    admin = Principal(UUID(int=2), frozenset({AdminRole.ADMIN}))

    assert authorize(reviewer, Permission.CONTENT_REVIEW) is reviewer
    assert authorize(admin, Permission.CONTENT_REVIEW) is admin


def test_create_contract_accepts_only_a_validation_run_identifier() -> None:
    payload: dict[str, object] = {"validation_run_id": str(VALIDATION_RUN_ID)}

    request = ReviewCandidateCreateRequest.model_validate(payload)

    assert request.validation_run_id == VALIDATION_RUN_ID
    forbidden_fields: dict[str, object] = {
        "candidate_id": str(CANDIDATE_ID),
        "generation_run_id": str(CANDIDATE_ID),
        "generation_attempt_id": str(UUID(int=4)),
        "content": content_payload(),
        "lineage": {"client": "forged"},
        "validation": {"overall_status": "pass"},
        "state": "approved",
        "version": 99,
        "created_by": str(UUID(int=5)),
    }
    for field_name, value in forbidden_fields.items():
        invalid = deepcopy(payload)
        invalid[field_name] = value
        with pytest.raises(ValidationError):
            ReviewCandidateCreateRequest.model_validate(invalid)


def test_transition_contracts_are_strict_and_typed() -> None:
    assert ReviewCandidateStartRequest.model_validate({"expected_version": 2}).expected_version == 2
    assert (
        ReviewCandidateApproveRequest.model_validate(
            {"expected_version": 4, "note": "Source and answer reviewed."}
        ).note
        == "Source and answer reviewed."
    )
    assert (
        ReviewCandidateRejectRequest.model_validate(
            {"expected_version": 4, "reason": "Answer is not uniquely supported."}
        ).reason
        == "Answer is not uniquely supported."
    )
    edited = ReviewCandidateEditRequest.model_validate(
        {
            "content": content_payload(),
            "reason": "Clarify the stem and answer options.",
            "expected_version": 3,
        }
    )
    assert edited.content.answer == "B"
    assert edited.expected_version == 3

    invalid_payloads = (
        (ReviewCandidateStartRequest, {"expected_version": True}),
        (ReviewCandidateStartRequest, {"expected_version": 2, "state": "approved"}),
        (ReviewCandidateApproveRequest, {"expected_version": 3, "note": " "}),
        (ReviewCandidateApproveRequest, {"expected_version": 3, "note": " note "}),
        (ReviewCandidateRejectRequest, {"expected_version": 3, "reason": " "}),
        (ReviewCandidateRejectRequest, {"expected_version": 3, "reason": " reason "}),
        (
            ReviewCandidateEditRequest,
            {
                "content": {**content_payload(), "answer": "missing"},
                "reason": "Invalid answer reference.",
                "expected_version": 3,
            },
        ),
        (
            ReviewCandidateEditRequest,
            {
                "content": {**content_payload(), "untrusted": "extra"},
                "reason": "Extra content field.",
                "expected_version": 3,
            },
        ),
    )
    for schema, payload in invalid_payloads:
        with pytest.raises(ValidationError):
            schema.model_validate(payload)

    invalid_content_values: list[dict[str, object]] = []
    surrounding_stem = content_payload()
    surrounding_stem["stem"] = " surrounded "
    invalid_content_values.append(surrounding_stem)
    surrounding_option = content_payload()
    cast_options = surrounding_option["options"]
    assert isinstance(cast_options, list)
    cast_options[0] = {"option_id": " A", "text": "First answer"}
    invalid_content_values.append(surrounding_option)
    surrounding_guide = content_payload()
    surrounding_guide["marking_guide"] = [" surrounded "]
    invalid_content_values.append(surrounding_guide)
    duplicate_options = content_payload()
    duplicate_options["options"] = [
        {"option_id": "A", "text": "First"},
        {"option_id": "A", "text": "Second"},
    ]
    invalid_content_values.append(duplicate_options)
    oversized_content = content_payload()
    oversized_content["marking_guide"] = ["x" * 8_192 for _index in range(20)]
    invalid_content_values.append(oversized_content)
    for invalid_content in invalid_content_values:
        with pytest.raises(ValidationError):
            ReviewCandidateEditRequest.model_validate(
                {
                    "content": invalid_content,
                    "reason": "Bounded reason.",
                    "expected_version": 3,
                }
            )
    with pytest.raises(ValidationError):
        ReviewCandidateEditRequest.model_validate(
            {
                "content": content_payload(),
                "reason": " surrounded ",
                "expected_version": 3,
            }
        )
    with pytest.raises(TypeError, match="StoredQuestionCandidate"):
        ReviewCandidateResponse.from_record(object())
    with pytest.raises(TypeError, match="ReviewCandidateSummary"):
        ReviewCandidateSummaryResponse.from_record(object())


def test_review_candidate_openapi_exposes_only_bounded_authorized_vertical_slice() -> None:
    schema = create_app().openapi()

    assert set(schema["paths"][BASE_PATH]) >= {"post", "get"}
    assert set(schema["paths"][CANDIDATE_PATH]) >= {"get", "patch"}
    assert "post" in schema["paths"][START_PATH]
    assert "post" in schema["paths"][APPROVE_PATH]
    assert "post" in schema["paths"][REJECT_PATH]
    assert not any("paper-draft" in path or "publish" in path for path in schema["paths"])

    operations = (
        (BASE_PATH, "post"),
        (BASE_PATH, "get"),
        (CANDIDATE_PATH, "get"),
        (CANDIDATE_PATH, "patch"),
        (START_PATH, "post"),
        (APPROVE_PATH, "post"),
        (REJECT_PATH, "post"),
    )
    for path, method in operations:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert {"404", "409", "422"} <= set(operation["responses"])

    create_schema = schema["components"]["schemas"]["ReviewCandidateCreateRequest"]
    assert create_schema["additionalProperties"] is False
    assert set(create_schema["properties"]) == {"validation_run_id"}
    list_response_schema = schema["paths"][BASE_PATH]["get"]["responses"]["200"]["content"]
    list_item = list_response_schema["application/json"]["schema"]["items"]
    assert list_item == {"$ref": "#/components/schemas/ReviewCandidateSummaryResponse"}
    summary_schema = schema["components"]["schemas"]["ReviewCandidateSummaryResponse"]
    assert summary_schema["additionalProperties"] is False
    assert set(summary_schema["properties"]) == {
        "id",
        "curriculum_version_id",
        "generation_run_id",
        "generation_attempt_id",
        "validation_run_id",
        "paper_blueprint_id",
        "blueprint_id",
        "blueprint_version",
        "blueprint_slot_id",
        "state",
        "version",
        "current_revision",
        "question_type",
        "stem_preview",
        "marks",
        "created_by",
        "created_at",
        "current_revision_created_at",
    }
    assert not {
        "revisions",
        "events",
        "lineage",
        "validation",
        "current_content",
    } & set(summary_schema["properties"])
    for component_name in (
        "ReviewCandidateStartRequest",
        "ReviewCandidateEditRequest",
        "ReviewCandidateApproveRequest",
        "ReviewCandidateRejectRequest",
        "QuestionContentRequest",
        "QuestionOptionRequest",
    ):
        assert schema["components"]["schemas"][component_name]["additionalProperties"] is False
    for component_name in (
        "ReviewCandidateStartRequest",
        "ReviewCandidateEditRequest",
        "ReviewCandidateApproveRequest",
        "ReviewCandidateRejectRequest",
    ):
        expected_version = schema["components"]["schemas"][component_name]["properties"][
            "expected_version"
        ]
        assert expected_version["maximum"] == 35

    list_parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][BASE_PATH]["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"]["maximum"] == 100
    assert list_parameters["offset"]["schema"]["maximum"] == 100_000
    assert {"state", "paper_blueprint_id", "blueprint_slot_id"} <= set(list_parameters)
    state_schema = list_parameters["state"]["schema"]["anyOf"][0]
    if "$ref" in state_schema:
        state_schema = schema["components"]["schemas"][state_schema["$ref"].rsplit("/", 1)[-1]]
    assert set(state_schema["enum"]) == {"validated", "in_review", "approved", "rejected"}
    assert list_parameters["blueprint_slot_id"]["schema"]["anyOf"][0]["maxLength"] == 128
