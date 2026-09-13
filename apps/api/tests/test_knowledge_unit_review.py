import asyncio
import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes import knowledge_units as routes
from exam_guru_api.auth.domain import AuthorizationError, Permission
from exam_guru_api.knowledge.unit_review import (
    KnowledgeReviewRequest,
    KnowledgeUnitReview,
    KnowledgeUnitReviewError,
    _snapshot,
)
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel


def request() -> dict[str, object]:
    return {
        "state": "reviewed",
        "confirmed_mapping": True,
        "competency_id": str(UUID(int=99501)),
        "reason": "Explicit educational mapping review",
        "expected_version": 0,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed_mapping": False},
        {"confirmed_mapping": 1},
        {"competency_id": None},
        {"state": "rejected"},
        {"state": "rejected", "confirmed_mapping": False},
        {"skill_id": str(UUID(int=99502)), "competency_id": None},
        {"sub_skill_id": str(UUID(int=99503))},
        {"learning_concept_id": str(UUID(int=99504))},
        {"lesson_id": str(UUID(int=99505))},
        {"expected_version": True},
        {"reason": "Unsafe\nreason"},
        {"reason": " "},
    ],
)
def test_mapping_review_requires_explicit_clean_consistent_choices(
    change: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        KnowledgeReviewRequest.model_validate_json(json.dumps({**request(), **change}))


@pytest.mark.parametrize("field", ["reason", "version", "unit_id"])
def test_review_snapshot_is_immutable_and_payload_changes_do_not_reuse_its_fingerprint(
    field: str,
) -> None:
    command = KnowledgeReviewRequest.model_validate_json(json.dumps(request()))
    assert command.reason == "Explicit educational mapping review"
    value = KnowledgeUnitReview(
        id=UUID(int=99510),
        unit_id=UUID(int=99511),
        unit_fingerprint="a" * 64,
        curriculum_version_id=UUID(int=99512),
        version=1,
        actor_id=UUID(int=99513),
        **command.model_dump(exclude={"expected_version"}),
    )
    with pytest.raises(ValidationError, match="frozen"):
        setattr(value, field, "Changed")
    assert value.fingerprint != value.model_copy(update={"version": 2}).fingerprint
    row = KnowledgeUnitReviewModel.from_domain(value, audit_event_id=UUID(int=99514))
    assert _snapshot(row) == value
    row.unit_fingerprint = "b" * 64
    with pytest.raises(KnowledgeUnitReviewError, match="stored_knowledge_review"):
        _snapshot(row)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (KnowledgeUnitReviewError("knowledge_unit_not_found"), 404),
        (KnowledgeUnitReviewError("knowledge_review_version_conflict"), 409),
        (KnowledgeUnitReviewError("stored_knowledge_review_invalid"), 503),
        (AuthorizationError(UUID(int=99520), Permission.KNOWLEDGE_WRITE), 403),
        (IntegrityError("synthetic statement", {}, RuntimeError("synthetic conflict")), 409),
        (ValueError("private malformed source"), 422),
    ],
)
def test_unit_review_api_error_boundary_is_private_and_rolls_back(
    error: Exception, status: int
) -> None:
    session = AsyncMock(spec=AsyncSession)

    async def operation() -> object:
        raise error

    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes._execute(session, operation))
    assert caught.value.status_code == status
    assert "private malformed source" not in str(caught.value.detail)
    session.rollback.assert_awaited_once()


def test_review_transport_preserves_first_party_strict_values() -> None:
    value = KnowledgeReviewRequest.model_validate_json(json.dumps(request()))
    assert routes._review_body(value) == value
    assert routes._review_body(value.model_dump(mode="json")) == value


def test_rejection_has_no_implicit_approved_classification() -> None:
    value = KnowledgeReviewRequest(
        expected_version=1, state="rejected", confirmed_mapping=False, reason="Withdraw approval"
    )
    assert value.competency_id is None
    assert value.curriculum_unit_id is None
