from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.main import create_app
from exam_guru_api.retrieval.schemas import (
    RetrievalExploreLimitsRequest,
    RetrievalExploreRequest,
    RetrievalScopeRequest,
    RetrievalTaxonomyScopeRequest,
)

EXPLORE_PATH = "/api/v1/admin/retrieval/explore"


def _valid_request() -> dict[str, object]:
    return {
        "query": "square perimeter",
        "scope": {
            "grade": 5,
            "exam_id": str(UUID(int=1)),
            "medium_id": str(UUID(int=2)),
            "subject_id": str(UUID(int=5)),
            "curriculum_version_id": str(UUID(int=3)),
            "unit_ids": [str(UUID(int=6))],
            "lesson_ids": [str(UUID(int=7))],
            "taxonomy": {"competency_id": str(UUID(int=4))},
        },
        "embedding_config": {
            "provider": "deterministic",
            "model": "grade5-fixture",
            "dimension": 3,
            "version": "v1",
            "config_fingerprint": "grade5-fixture-v1-d3",
        },
        "limits": {
            "candidate_limit": 20,
            "top_k": 5,
            "max_context_items": 3,
            "max_context_characters": 1_000,
            "max_context_item_characters": 500,
        },
    }


def test_retrieval_explorer_openapi_is_authorized_typed_and_vector_free() -> None:
    app = create_app()
    schema = app.openapi()

    operation = schema["paths"][EXPLORE_PATH]["post"]
    assert operation["operationId"] == "explore_retrieval"
    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RetrievalExploreResponse"
    }
    assert {"404", "422", "503"} <= set(operation["responses"])
    for status_code in ("404", "422", "503"):
        assert operation["responses"][status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiErrorResponse"
        }

    request_schema = schema["components"]["schemas"]["RetrievalExploreRequest"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {"query", "scope", "embedding_config", "limits"}
    assert set(request_schema["properties"]) == {
        "query",
        "scope",
        "embedding_config",
        "limits",
    }

    serialized_operation = str(operation).casefold()
    assert "query_vector" not in serialized_operation
    assert "raw_vector" not in serialized_operation
    assert "embedding_values" not in serialized_operation


def test_api_error_contract_types_codes_extras_and_validation_details() -> None:
    coded = ApiErrorResponse.model_validate(
        {"detail": {"code": "analytics_record_limit_exceeded", "maximum": 5_000}}
    )
    validation = ApiErrorResponse.model_validate(
        {"detail": [{"loc": ["body", "query"], "msg": "required", "type": "missing"}]}
    )

    assert not isinstance(coded.detail, list)
    assert coded.detail.code == "analytics_record_limit_exceeded"
    assert coded.detail.model_extra == {"maximum": 5_000}
    assert isinstance(validation.detail, list)
    assert validation.detail[0]["type"] == "missing"


def test_retrieval_request_scope_config_and_cost_bounds_are_explicit() -> None:
    schema = create_app().openapi()["components"]["schemas"]
    scope = schema["RetrievalScopeRequest"]
    taxonomy = schema["RetrievalTaxonomyScopeRequest"]
    embedding = schema["EmbeddingConfigRequest"]
    limits = schema["RetrievalExploreLimitsRequest"]

    assert scope["additionalProperties"] is False
    assert scope["properties"]["grade"]["minimum"] == 1
    assert scope["properties"]["grade"]["maximum"] == 13
    assert set(scope["required"]) == {
        "grade",
        "exam_id",
        "medium_id",
        "curriculum_version_id",
        "taxonomy",
    }
    assert {"subject_id", "unit_ids", "lesson_ids"} <= set(scope["properties"])
    assert scope["properties"]["unit_ids"]["default"] == []
    assert scope["properties"]["lesson_ids"]["default"] == []
    assert taxonomy["additionalProperties"] is False
    assert "competency_id" in taxonomy["required"]
    assert embedding["additionalProperties"] is False
    assert set(embedding["required"]) == {
        "provider",
        "model",
        "dimension",
        "version",
        "config_fingerprint",
    }
    assert limits["additionalProperties"] is False
    assert set(limits["required"]) == {
        "candidate_limit",
        "top_k",
        "max_context_items",
        "max_context_characters",
        "max_context_item_characters",
    }
    assert limits["properties"]["candidate_limit"]["maximum"] == 100
    assert limits["properties"]["top_k"]["maximum"] == 100
    assert limits["properties"]["max_context_items"]["maximum"] == 100
    assert limits["properties"]["max_context_characters"]["maximum"] == 100_000
    assert limits["properties"]["max_context_item_characters"]["maximum"] == 20_000


def test_retrieval_request_rejects_extra_fields_vectors_and_invalid_hierarchy() -> None:
    payload = _valid_request()
    payload["query_vector"] = [1.0, 0.0, 0.0]
    with pytest.raises(ValidationError, match="query_vector"):
        RetrievalExploreRequest.model_validate(payload)

    for invalid_grade in (0, 14):
        wrong_grade = _valid_request()
        cast(dict[str, object], wrong_grade["scope"])["grade"] = invalid_grade
        with pytest.raises(
            ValidationError,
            match=r"greater than or equal to 1|less than or equal to 13",
        ):
            RetrievalExploreRequest.model_validate(wrong_grade)

    with pytest.raises(ValidationError, match="sub_skill_id requires skill_id"):
        RetrievalTaxonomyScopeRequest(
            competency_id=UUID(int=1),
            sub_skill_id=UUID(int=2),
        )
    with pytest.raises(ValidationError, match="learning_concept_id requires sub_skill_id"):
        RetrievalTaxonomyScopeRequest(
            competency_id=UUID(int=1),
            skill_id=UUID(int=2),
            learning_concept_id=UUID(int=3),
        )
    base_scope = cast(dict[str, object], _valid_request()["scope"])
    for field_name in ("unit_ids", "lesson_ids"):
        duplicate_scope = {**base_scope, field_name: [str(UUID(int=6)), str(UUID(int=6))]}
        with pytest.raises(ValidationError, match=f"{field_name} must be unique"):
            RetrievalScopeRequest.model_validate(duplicate_scope)
    lesson_without_unit = {**base_scope, "unit_ids": []}
    with pytest.raises(ValidationError, match="lesson_ids require unit_ids"):
        RetrievalScopeRequest.model_validate(lesson_without_unit)


def test_retrieval_limits_reject_amplification_relationships() -> None:
    with pytest.raises(ValidationError, match="top_k cannot exceed candidate_limit"):
        RetrievalExploreLimitsRequest(
            candidate_limit=2,
            top_k=3,
            max_context_items=2,
            max_context_characters=100,
            max_context_item_characters=50,
        )
    with pytest.raises(ValidationError, match="max_context_items cannot exceed top_k"):
        RetrievalExploreLimitsRequest(
            candidate_limit=3,
            top_k=2,
            max_context_items=3,
            max_context_characters=100,
            max_context_item_characters=50,
        )
    with pytest.raises(
        ValidationError,
        match="max_context_item_characters cannot exceed max_context_characters",
    ):
        RetrievalExploreLimitsRequest(
            candidate_limit=3,
            top_k=3,
            max_context_items=3,
            max_context_characters=49,
            max_context_item_characters=50,
        )


def test_scope_schema_builds_exact_domain_scope() -> None:
    request = RetrievalScopeRequest.model_validate(
        cast(dict[str, object], _valid_request()["scope"])
    )

    domain = request.to_domain()

    assert domain.grade == 5
    assert domain.exam_id == UUID(int=1)
    assert domain.medium_id == UUID(int=2)
    assert domain.subject_id == UUID(int=5)
    assert domain.curriculum_version_id == UUID(int=3)
    assert domain.unit_ids == (UUID(int=6),)
    assert domain.lesson_ids == (UUID(int=7),)
    assert domain.taxonomy.competency_id == UUID(int=4)
