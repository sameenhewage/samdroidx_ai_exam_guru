from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from exam_guru_api.main import create_app
from exam_guru_api.papers.publication_models import PaperArchiveEventModel
from exam_guru_api.papers.publication_repository import (
    PaperSummary,
    PublicationVersionSummary,
    StoredPaperArchive,
    StoredPaperDraft,
)
from exam_guru_api.papers.publication_schemas import (
    PaperAggregateResponse,
    PaperArchiveRequest,
    PaperArchiveResponse,
    PaperDraftCreateRequest,
    PaperDraftVersionResponse,
    PaperPublishRequest,
    PaperRevisionCreateRequest,
    PaperSummaryResponse,
    PublishedPaperSnapshotResponse,
    PublishedPaperVersionResponse,
    PublishedPaperVersionSummaryResponse,
)
from exam_guru_api.papers.serialization import serialize_published_snapshot
from tests.test_paper_publication_repository import (
    NOW,
    draft_models,
    paper_model,
    persisted_draft,
    publication_record,
)
from tests.test_paper_publication_serialization import published_snapshot
from tests.test_review_candidate_repository import ACTOR_ID

CURRICULUM_ID = UUID(int=996_001)
PAPER_BLUEPRINT_ID = UUID(int=996_002)
CANDIDATE_A = UUID(int=996_003)
CANDIDATE_B = UUID(int=996_004)
BASE = "/api/v1/admin/curricula/{curriculum_version_id}"
CREATE_PATH = BASE + "/paper-drafts"
PAPERS_PATH = BASE + "/papers"
PAPER_PATH = PAPERS_PATH + "/{paper_id}"
DRAFTS_PATH = PAPER_PATH + "/draft-versions"
DRAFT_PATH = DRAFTS_PATH + "/{version}"
REVISE_PATH = PAPER_PATH + "/revisions"
PUBLISH_PATH = PAPER_PATH + "/publish"
PUBLICATIONS_PATH = PAPER_PATH + "/publication-versions"
PUBLICATION_PATH = PUBLICATIONS_PATH + "/{version}"
ARCHIVE_PATH = PAPER_PATH + "/archive"


def create_payload() -> dict[str, object]:
    return {
        "paper_blueprint_id": str(PAPER_BLUEPRINT_ID),
        "title": "Grade 5 Scholarship Practice Paper",
        "candidate_ids": [str(CANDIDATE_B), str(CANDIDATE_A)],
    }


def test_paper_write_contracts_accept_only_server_resolved_fields_and_are_bounded() -> None:
    create = PaperDraftCreateRequest.model_validate(create_payload())
    assert create.paper_blueprint_id == PAPER_BLUEPRINT_ID
    assert create.candidate_ids == (CANDIDATE_B, CANDIDATE_A)
    maximum_selection = PaperDraftCreateRequest(
        paper_blueprint_id=PAPER_BLUEPRINT_ID,
        title="Maximum bounded selection",
        candidate_ids=tuple(UUID(int=index + 1) for index in range(200)),
    )
    assert len(maximum_selection.candidate_ids) == 200
    assert PaperPublishRequest(expected_version=1).expected_version == 1
    assert PaperArchiveRequest(expected_version=1, reason="Retired by administrator.").reason
    revision = PaperRevisionCreateRequest(
        expected_version=1,
        title=None,
        candidate_ids=(CANDIDATE_A, CANDIDATE_B),
    )
    assert revision.title is None

    forged_fields: dict[str, object] = {
        "paper_id": str(UUID(int=996_010)),
        "state": "published",
        "version": 99,
        "current_version": 99,
        "snapshot": {"client": "forged"},
        "content_hash": "0" * 64,
        "published_by": str(UUID(int=996_011)),
        "archived_by": str(UUID(int=996_012)),
        "blueprint": {"client": "forged"},
        "candidate_versions": {str(CANDIDATE_A): 99},
    }
    for field_name, value in forged_fields.items():
        payload = deepcopy(create_payload())
        payload[field_name] = value
        with pytest.raises(ValidationError):
            PaperDraftCreateRequest.model_validate(payload)

    invalid_creates = (
        {**create_payload(), "title": " surrounded "},
        {**create_payload(), "title": "x" * 513},
        {**create_payload(), "candidate_ids": []},
        {**create_payload(), "candidate_ids": [str(CANDIDATE_A), str(CANDIDATE_A)]},
        {**create_payload(), "candidate_ids": [str(UUID(int=index + 1)) for index in range(201)]},
    )
    for payload in invalid_creates:
        with pytest.raises(ValidationError):
            PaperDraftCreateRequest.model_validate(payload)

    invalid_commands: tuple[tuple[type[BaseModel], dict[str, object]], ...] = (
        (PaperPublishRequest, {"expected_version": True}),
        (PaperPublishRequest, {"expected_version": 33}),
        (PaperPublishRequest, {"expected_version": 1, "content_hash": "0" * 64}),
        (
            PaperRevisionCreateRequest,
            {"expected_version": 33, "candidate_ids": [str(CANDIDATE_A)]},
        ),
        (PaperArchiveRequest, {"expected_version": 33, "reason": "Bounded."}),
        (
            PaperRevisionCreateRequest,
            {"expected_version": 1, "candidate_ids": [str(CANDIDATE_A)], "state": "draft"},
        ),
        (
            PaperRevisionCreateRequest,
            {
                "expected_version": 1,
                "candidate_ids": [str(CANDIDATE_A)],
                "title": " surrounded ",
            },
        ),
        (
            PaperRevisionCreateRequest,
            {
                "expected_version": 1,
                "candidate_ids": [str(CANDIDATE_A), str(CANDIDATE_A)],
            },
        ),
        (PaperArchiveRequest, {"expected_version": 1, "reason": " surrounded "}),
        (PaperArchiveRequest, {"expected_version": 1, "reason": "x" * 1_025}),
    )
    for schema, payload in invalid_commands:
        with pytest.raises(ValidationError):
            schema.model_validate(payload)


def test_paper_response_factories_and_nested_snapshot_validation_fail_closed() -> None:
    for response_type, factory_name in (
        (PaperAggregateResponse, "from_model"),
        (PaperSummaryResponse, "from_record"),
        (PaperDraftVersionResponse, "from_record"),
        (PublishedPaperVersionSummaryResponse, "from_record"),
        (PublishedPaperVersionResponse, "from_record"),
        (PaperArchiveResponse, "from_record"),
    ):
        factory = getattr(response_type, factory_name)
        with pytest.raises(TypeError):
            factory(object())

    paper = paper_model()
    assert PaperAggregateResponse.from_model(paper).id == paper.id
    summary = PaperSummary(
        id=paper.id,
        curriculum_version_id=paper.curriculum_version_id,
        paper_blueprint_id=paper.paper_blueprint_id,
        blueprint_id=paper.blueprint_id,
        blueprint_version=paper.blueprint_version,
        state=paper.state,
        current_version=paper.current_version,
        created_by=paper.created_by,
        created_at=paper.created_at,
        updated_by=paper.updated_by,
        updated_at=paper.updated_at,
        title="Paper",
        latest_publication_hash=None,
    )
    assert PaperSummaryResponse.from_record(summary).title == "Paper"
    draft = persisted_draft()
    draft_model, selections = draft_models(draft)
    stored_draft = StoredPaperDraft(paper, draft_model, selections, draft)
    assert PaperDraftVersionResponse.from_record(stored_draft).candidates
    publication = publication_record()
    publication_summary = PublicationVersionSummary(
        paper_id=publication.publication.paper_id,
        curriculum_version_id=publication.publication.curriculum_version_id,
        version=publication.publication.version,
        content_hash=publication.publication.content_hash,
        published_by=publication.publication.published_by,
        published_at=publication.publication.published_at,
    )
    assert PublishedPaperVersionSummaryResponse.from_record(publication_summary).version == 1
    assert PublishedPaperVersionResponse.from_record(publication).content_hash
    archive = PaperArchiveEventModel(
        paper_id=paper.id,
        curriculum_version_id=paper.curriculum_version_id,
        version=1,
        reason="Retired.",
        archived_by=ACTOR_ID,
        archived_at=NOW,
    )
    assert (
        PaperArchiveResponse.from_record(StoredPaperArchive(archive, publication)).content_hash
        == publication.publication.content_hash
    )

    payload = serialize_published_snapshot(published_snapshot())
    payload["blueprint"]["paper_blueprint_id"] = str(PAPER_BLUEPRINT_ID)  # type: ignore[index]
    valid = PublishedPaperSnapshotResponse.model_validate(payload)
    assert valid.questions

    no_revisions = deepcopy(payload)
    no_revisions["questions"][0]["revisions"] = []  # type: ignore[index]
    with pytest.raises(ValidationError, match="content_revision"):
        PublishedPaperSnapshotResponse.model_validate(no_revisions)

    mismatched_content = deepcopy(payload)
    mismatched_content["questions"][0]["content"]["stem"] = "Changed"  # type: ignore[index]
    with pytest.raises(ValidationError, match="final revision"):
        PublishedPaperSnapshotResponse.model_validate(mismatched_content)


def test_paper_openapi_exposes_bounded_role_separated_aggregate_and_version_reads() -> None:
    schema = create_app().openapi()

    expected_methods = {
        CREATE_PATH: {"post"},
        PAPERS_PATH: {"get"},
        PAPER_PATH: {"get"},
        DRAFTS_PATH: {"get"},
        DRAFT_PATH: {"get"},
        REVISE_PATH: {"post"},
        PUBLISH_PATH: {"post"},
        PUBLICATIONS_PATH: {"get"},
        PUBLICATION_PATH: {"get"},
        ARCHIVE_PATH: {"post", "get"},
    }
    for path, methods in expected_methods.items():
        assert methods <= set(schema["paths"][path])
        for method in methods:
            operation = schema["paths"][path][method]
            assert operation["security"] == [{"HTTPBearer": []}]
            assert {"404", "409", "422"} <= set(operation["responses"])

    create_operation = schema["paths"][CREATE_PATH]["post"]
    idempotency = next(
        parameter
        for parameter in create_operation["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["required"] is True
    assert idempotency["schema"]["maxLength"] == 128
    create_schema = schema["components"]["schemas"]["PaperDraftCreateRequest"]
    assert create_schema["additionalProperties"] is False
    assert set(create_schema["properties"]) == {
        "paper_blueprint_id",
        "title",
        "candidate_ids",
    }
    assert create_schema["properties"]["candidate_ids"]["maxItems"] == 200
    for name in (
        "PaperDraftCreateRequest",
        "PaperRevisionCreateRequest",
        "PaperPublishRequest",
        "PaperArchiveRequest",
    ):
        assert schema["components"]["schemas"][name]["additionalProperties"] is False

    publish_properties = schema["components"]["schemas"]["PaperPublishRequest"]["properties"]
    assert set(publish_properties) == {"expected_version"}
    for request_name in (
        "PaperRevisionCreateRequest",
        "PaperPublishRequest",
        "PaperArchiveRequest",
    ):
        expected_version = schema["components"]["schemas"][request_name]["properties"][
            "expected_version"
        ]
        assert expected_version["maximum"] == 32
    for versioned_path in (DRAFT_PATH, PUBLICATION_PATH):
        version_parameter = next(
            parameter
            for parameter in schema["paths"][versioned_path]["get"]["parameters"]
            if parameter["name"] == "version"
        )
        assert version_parameter["schema"]["maximum"] == 32
    archive_properties = schema["components"]["schemas"]["PaperArchiveRequest"]["properties"]
    assert set(archive_properties) == {"expected_version", "reason"}
    publication_response = schema["components"]["schemas"]["PublishedPaperVersionResponse"]
    assert "snapshot" in publication_response["properties"]
    snapshot_ref = publication_response["properties"]["snapshot"]["$ref"].rsplit("/", 1)[-1]
    snapshot = schema["components"]["schemas"][snapshot_ref]
    assert {"questions", "blueprint", "paper_id", "paper_version", "title"} <= set(
        snapshot["properties"]
    )

    list_parameters = {
        parameter["name"]: parameter
        for parameter in schema["paths"][PAPERS_PATH]["get"]["parameters"]
    }
    assert list_parameters["limit"]["schema"]["maximum"] == 100
    assert list_parameters["offset"]["schema"]["maximum"] == 100_000
    assert {"state", "paper_blueprint_id"} <= set(list_parameters)
