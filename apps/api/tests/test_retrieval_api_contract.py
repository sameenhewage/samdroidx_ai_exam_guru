from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

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
            "curriculum_version_id": str(UUID(int=3)),
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


def test_retrieval_request_scope_config_and_cost_bounds_are_explicit() -> None:
    schema = create_app().openapi()["components"]["schemas"]
    scope = schema["RetrievalScopeRequest"]
    taxonomy = schema["RetrievalTaxonomyScopeRequest"]
    embedding = schema["EmbeddingConfigRequest"]
    limits = schema["RetrievalExploreLimitsRequest"]

    assert scope["additionalProperties"] is False
    assert scope["properties"]["grade"]["const"] == 5
    assert set(scope["required"]) == {
        "grade",
        "exam_id",
        "medium_id",
        "curriculum_version_id",
        "taxonomy",
    }
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

    wrong_grade = _valid_request()
    cast(dict[str, object], wrong_grade["scope"])["grade"] = 6
    with pytest.raises(ValidationError, match="Input should be 5"):
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
    assert domain.curriculum_version_id == UUID(int=3)
    assert domain.taxonomy.competency_id == UUID(int=4)
