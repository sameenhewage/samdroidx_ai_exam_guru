from typing import cast

from fastapi.routing import APIRoute

from exam_guru_api.main import create_app

QUESTION_COLLECTION = "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/questions"
QUESTION_ITEM = QUESTION_COLLECTION + "/{question_id}"
CHUNK_COLLECTION = "/api/v1/admin/curricula/{curriculum_version_id}/knowledge/chunks"
CHUNK_ITEM = CHUNK_COLLECTION + "/{chunk_id}"


def _operation(path: str, method: str) -> dict[str, object]:
    schema = create_app().openapi()
    return schema["paths"][path][method]  # type: ignore[no-any-return]


def _request_properties(path: str, method: str) -> set[str]:
    schema = create_app().openapi()
    operation = schema["paths"][path][method]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    reference = request_schema["$ref"].split("/")[-1]
    return set(schema["components"]["schemas"][reference]["properties"])


def _nonnull_schema(property_schema: dict[str, object]) -> dict[str, object]:
    alternatives = property_schema.get("anyOf")
    if not isinstance(alternatives, list):
        return property_schema
    return next(
        alternative
        for alternative in alternatives
        if isinstance(alternative, dict) and alternative.get("type") != "null"
    )


def test_knowledge_openapi_exposes_curriculum_scoped_authorized_resources() -> None:
    app = create_app()
    schema = app.openapi()

    expected_methods = {
        QUESTION_COLLECTION: {"get", "post"},
        QUESTION_ITEM: {"get"},
        QUESTION_ITEM + "/classification": {"patch"},
        QUESTION_ITEM + "/review": {"post"},
        CHUNK_COLLECTION: {"get", "post"},
        CHUNK_ITEM: {"get"},
        CHUNK_ITEM + "/classification": {"patch"},
        CHUNK_ITEM + "/review": {"post"},
    }
    for path, methods in expected_methods.items():
        assert methods <= set(schema["paths"][path])
        for method in methods:
            assert _operation(path, method)["security"] == [{"HTTPBearer": []}]

    operation_ids = [
        route.operation_id
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/admin")
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_import_and_mutation_contracts_do_not_accept_ids_state_or_vectors() -> None:
    question_properties = _request_properties(QUESTION_COLLECTION, "post")
    chunk_properties = _request_properties(CHUNK_COLLECTION, "post")

    forbidden = {
        "id",
        "curriculum_version_id",
        "review_state",
        "version",
        "embedding",
        "embeddings",
        "vector",
        "raw_vector",
        "embedding_configurations",
    }
    assert not question_properties & forbidden
    assert not chunk_properties & forbidden
    assert {"source_document_id", "page_number", "source_block_id"} <= question_properties
    assert {
        "media_references",
        "options",
        "answer",
        "marking_guidance",
        "marking_data",
        "question_archetype",
        "difficulty_label",
        "difficulty_confidence",
        "difficulty_source",
    } <= question_properties
    assert {"source_document_id", "page_number", "source_block_id"} <= chunk_properties

    schema = create_app().openapi()
    for schema_name in ("HistoricalQuestionImportRequest", "KnowledgeChunkImportRequest"):
        assert "source_block_id" in schema["components"]["schemas"][schema_name]["required"]

    for path in (QUESTION_ITEM, CHUNK_ITEM):
        assert _request_properties(path + "/classification", "patch") == {
            "competency_id",
            "skill_id",
            "sub_skill_id",
            "learning_concept_id",
            "expected_version",
        }
        assert _request_properties(path + "/review", "post") == {
            "target",
            "expected_version",
        }


def test_knowledge_mutation_versions_are_bounded_to_database_integer_range() -> None:
    schema = create_app().openapi()
    for schema_name in ("KnowledgeClassificationRequest", "KnowledgeReviewTransitionRequest"):
        version = schema["components"]["schemas"][schema_name]["properties"]["expected_version"]
        assert version["minimum"] == 0
        assert version["maximum"] == 2_147_483_647


def test_historical_question_metadata_openapi_is_optional_typed_and_bounded() -> None:
    schema = create_app().openapi()
    request = schema["components"]["schemas"]["HistoricalQuestionImportRequest"]
    properties = request["properties"]
    metadata_fields = {
        "media_references",
        "options",
        "answer",
        "marking_guidance",
        "marking_data",
        "question_archetype",
        "difficulty_label",
        "difficulty_confidence",
        "difficulty_source",
    }

    assert not metadata_fields & set(request["required"])
    media_references = _nonnull_schema(properties["media_references"])
    options = _nonnull_schema(properties["options"])
    confidence = _nonnull_schema(properties["difficulty_confidence"])
    marking_data = _nonnull_schema(properties["marking_data"])
    assert media_references["type"] == "array"
    assert media_references["minItems"] == 1
    assert media_references["maxItems"] == 32
    assert cast(dict[str, object], media_references["items"])["maxLength"] == 2_048
    assert options["type"] == "array"
    assert options["minItems"] == 2
    assert options["maxItems"] == 8
    assert cast(dict[str, object], options["items"])["maxLength"] == 2_000
    assert confidence["minimum"] == 0.0
    assert confidence["maximum"] == 1.0
    assert marking_data["type"] == "object"
    assert schema["components"]["schemas"]["DifficultyLabel"]["enum"] == [
        "easy",
        "medium",
        "hard",
    ]


def test_knowledge_list_bounds_and_response_metadata_are_documented() -> None:
    schema = create_app().openapi()
    for path in (QUESTION_COLLECTION, CHUNK_COLLECTION):
        operation = schema["paths"][path]["get"]
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert parameters["limit"]["schema"]["minimum"] == 1
        assert parameters["offset"]["schema"]["maximum"] == 100_000
        assert parameters["offset"]["schema"]["minimum"] == 0

    for schema_name in ("HistoricalQuestionResponse", "KnowledgeChunkResponse"):
        properties = schema["components"]["schemas"][schema_name]["properties"]
        assert {
            "id",
            "curriculum_version_id",
            "provenance",
            "classification",
            "review_state",
            "version",
            "created_at",
            "updated_at",
            "embedding_status",
            "embedding_configurations",
        } <= set(properties)
        assert "embedding" not in properties
        assert "vector" not in properties
        assert "raw_vector" not in properties

    question_properties = schema["components"]["schemas"]["HistoricalQuestionResponse"][
        "properties"
    ]
    assert {
        "media_references",
        "options",
        "answer",
        "marking_guidance",
        "marking_data",
        "question_archetype",
        "difficulty_label",
        "difficulty_confidence",
        "difficulty_source",
    } <= set(question_properties)
