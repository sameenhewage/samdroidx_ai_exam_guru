from copy import deepcopy
from typing import Any, cast
from uuid import UUID

import pytest

import exam_guru_api.papers.serialization as serialization
from exam_guru_api.papers import (
    PaperWorkflowService,
    PublishedPaperSnapshot,
    PublishPaperCommand,
)
from exam_guru_api.papers.serialization import (
    MAX_PUBLISHED_SNAPSHOT_BYTES,
    PaperSnapshotIntegrityError,
    canonical_publication_bytes,
    reconstruct_published_snapshot,
    serialize_published_snapshot,
)
from tests.test_paper_domain import assembled_draft
from tests.test_paper_service import admin


def published_snapshot() -> PublishedPaperSnapshot:
    draft = assembled_draft()
    return PaperWorkflowService().publish(
        admin(),
        PublishPaperCommand(draft=draft, expected_version=draft.version),
    )


def test_published_snapshot_strict_round_trip_covers_complete_review_lineage() -> None:
    publication = published_snapshot()

    payload = cast(dict[str, Any], serialize_published_snapshot(publication))
    restored = reconstruct_published_snapshot(
        payload,
        content_hash=publication.content_hash,
        published_by=publication.published_by,
        previous_version=publication.previous_version,
        supersedes_content_hash=publication.supersedes_content_hash,
    )

    assert restored == publication
    assert restored.content_hash == publication.content_hash
    assert len(canonical_publication_bytes(payload)) <= MAX_PUBLISHED_SNAPSHOT_BYTES
    question = payload["questions"][0]
    assert question["revisions"]
    assert question["review_history"]
    assert question["decision"]["state"] == "approved"
    assert question["validation"]["passed"] is True
    assert question["lineage"]["provenance"]


def test_published_snapshot_hash_detects_content_and_review_lineage_tampering() -> None:
    publication = published_snapshot()
    payload = cast(dict[str, Any], serialize_published_snapshot(publication))

    tampered_values = []
    tampered_content = deepcopy(payload)
    tampered_content["questions"][0]["content"]["stem"] = "Tampered student content"
    tampered_values.append(tampered_content)
    tampered_reviewer = deepcopy(payload)
    tampered_reviewer["questions"][0]["decision"]["reviewer_id"] = str(UUID(int=999_991))
    tampered_values.append(tampered_reviewer)
    tampered_validation = deepcopy(payload)
    tampered_validation["questions"][0]["validation"]["passed"] = False
    tampered_values.append(tampered_validation)

    for tampered in tampered_values:
        with pytest.raises(PaperSnapshotIntegrityError):
            reconstruct_published_snapshot(
                tampered,
                content_hash=publication.content_hash,
                published_by=publication.published_by,
                previous_version=publication.previous_version,
                supersedes_content_hash=publication.supersedes_content_hash,
            )


def test_published_snapshot_reconstruction_fails_closed_on_shape_and_type_changes() -> None:
    publication = published_snapshot()
    payload = cast(dict[str, Any], serialize_published_snapshot(publication))
    malformed_values = []
    extra_root = deepcopy(payload)
    extra_root["client_state"] = "published"
    malformed_values.append(extra_root)
    missing_question_lineage = deepcopy(payload)
    del missing_question_lineage["questions"][0]["lineage"]
    malformed_values.append(missing_question_lineage)
    boolean_version = deepcopy(payload)
    boolean_version["paper_version"] = True
    malformed_values.append(boolean_version)
    duplicate_slot = deepcopy(payload)
    duplicate_slot["questions"][1]["slot_id"] = duplicate_slot["questions"][0]["slot_id"]
    malformed_values.append(duplicate_slot)

    for malformed in malformed_values:
        with pytest.raises(PaperSnapshotIntegrityError):
            reconstruct_published_snapshot(
                malformed,
                content_hash=serialization.publication_content_hash(malformed),
                published_by=publication.published_by,
                previous_version=publication.previous_version,
                supersedes_content_hash=publication.supersedes_content_hash,
            )


def test_publication_serialization_bounds_types_and_domain_hash_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = published_snapshot()
    payload = cast(dict[str, Any], serialize_published_snapshot(publication))

    assert len(serialization.publication_content_hash(payload)) == 64
    for invalid_json in ({"set": {"not-json"}}, {"number": float("nan")}):
        with pytest.raises(PaperSnapshotIntegrityError, match="canonical JSON"):
            canonical_publication_bytes(invalid_json)
    with pytest.raises(TypeError, match="PublishedPaperSnapshot"):
        serialize_published_snapshot(cast(PublishedPaperSnapshot, object()))

    object.__setattr__(publication, "content_hash", "0" * 64)
    with pytest.raises(PaperSnapshotIntegrityError, match="domain content"):
        serialize_published_snapshot(publication)
    object.__setattr__(
        publication,
        "content_hash",
        publication.recompute_content_hash(),
    )

    monkeypatch.setattr(serialization, "MAX_PUBLISHED_SNAPSHOT_BYTES", 1)
    with pytest.raises(PaperSnapshotIntegrityError, match="cannot exceed"):
        serialize_published_snapshot(publication)
    with pytest.raises(PaperSnapshotIntegrityError, match="cannot exceed"):
        reconstruct_published_snapshot(
            payload,
            content_hash=publication.content_hash,
            published_by=publication.published_by,
            previous_version=publication.previous_version,
            supersedes_content_hash=publication.supersedes_content_hash,
        )


def test_reconstruction_rejects_every_nested_shape_enum_and_binding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = published_snapshot()
    payload = cast(dict[str, Any], serialize_published_snapshot(publication))
    malformed_values: list[dict[str, Any]] = []

    def changed(path: tuple[object, ...], value: object) -> dict[str, Any]:
        altered = deepcopy(payload)
        target: Any = altered
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        return altered

    malformed_values.extend(
        (
            changed(("schema",), "published-paper.v2"),
            changed(("questions",), "not-an-array"),
            changed(("blueprint", "slot_ids"), []),
            changed(("title",), 7),
            changed(("paper_id",), "not-a-uuid"),
            changed(("paper_version",), True),
            changed(("questions", 0, "validation", "passed"), 1),
            changed(("questions", 0, "review_history", 0, "action"), "forged"),
            changed(("questions", 0, "decision", "state"), "forged"),
            changed(("questions", 0, "content_revision"), 99),
            changed(("questions", 0, "revisions"), []),
        )
    )
    with pytest.raises(PaperSnapshotIntegrityError, match="string keys"):
        serialization._object({1: "invalid"}, "snapshot", frozenset())

    for malformed in malformed_values:
        with pytest.raises(PaperSnapshotIntegrityError):
            reconstruct_published_snapshot(
                malformed,
                content_hash=serialization.publication_content_hash(malformed),
                published_by=publication.published_by,
                previous_version=publication.previous_version,
                supersedes_content_hash=publication.supersedes_content_hash,
            )

    with pytest.raises(PaperSnapshotIntegrityError):
        reconstruct_published_snapshot(
            payload,
            content_hash=publication.content_hash,
            published_by=cast(UUID, object()),
            previous_version=publication.previous_version,
            supersedes_content_hash=publication.supersedes_content_hash,
        )

    monkeypatch.setattr(
        serialization,
        "_published_content_payload",
        lambda _snapshot: {**payload, "title": "non-round-tripping"},
    )
    with pytest.raises(PaperSnapshotIntegrityError, match="round-trip"):
        reconstruct_published_snapshot(
            payload,
            content_hash=publication.content_hash,
            published_by=publication.published_by,
            previous_version=publication.previous_version,
            supersedes_content_hash=publication.supersedes_content_hash,
        )


def test_reconstruction_rechecks_the_rebuilt_domain_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    publication = published_snapshot()
    payload = cast(dict[str, Any], serialize_published_snapshot(publication))
    monkeypatch.setattr(
        PublishedPaperSnapshot,
        "recompute_content_hash",
        lambda _snapshot: "0" * 64,
    )

    with pytest.raises(PaperSnapshotIntegrityError, match="reconstructed domain"):
        reconstruct_published_snapshot(
            payload,
            content_hash=publication.content_hash,
            published_by=publication.published_by,
            previous_version=publication.previous_version,
            supersedes_content_hash=publication.supersedes_content_hash,
        )
