import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.api.routes.knowledge_preparation as routes
import exam_guru_api.knowledge.preparation_summary as summary
from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Permission, Principal
from exam_guru_api.documents.service import SourceDocumentNotFoundError

ADMIN = Principal(UUID(int=87011), frozenset({AdminRole.ADMIN}))
DOCUMENT_ID = UUID(int=87012)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AuthorizationError(ADMIN.subject_id, Permission.KNOWLEDGE_READ), 403, "permission_denied"),
        (
            SourceDocumentNotFoundError("private document identity"),
            404,
            "source_document_not_found",
        ),
        (
            SQLAlchemyError("private database details"),
            503,
            "material_knowledge_preparation_unavailable",
        ),
        (ValueError("private invalid record"), 503, "material_knowledge_preparation_unavailable"),
    ],
)
def test_summary_maps_only_safe_errors_without_returning_private_evidence(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    code: str,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(routes, "get_material_knowledge_preparation", AsyncMock(side_effect=error))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes.material_knowledge_preparation(DOCUMENT_ID, ADMIN, session))
    assert caught.value.status_code == status
    assert cast(object, caught.value.detail) == {"code": code}
    assert session.rollback.await_count == int(status == 503)
    session.commit.assert_not_awaited()


def test_summary_checks_both_read_permissions_before_accessing_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[Permission] = []

    def authorize(principal: Principal, permission: Permission) -> Principal:
        checked.append(permission)
        if permission is Permission.KNOWLEDGE_READ:
            raise AuthorizationError(principal.subject_id, permission)
        return principal

    monkeypatch.setattr(summary, "authorize", authorize)
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(AuthorizationError):
        asyncio.run(
            summary.get_material_knowledge_preparation(
                session, principal=ADMIN, document_id=DOCUMENT_ID
            )
        )
    assert checked == [Permission.SOURCE_READ, Permission.KNOWLEDGE_READ]
    assert not session.mock_calls


@pytest.mark.parametrize("missing", [False, True])
def test_summary_is_a_single_read_and_never_infers_request_from_prepared_rows(
    missing: bool,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.no_autoflush = nullcontext()
    row = (
        None
        if missing
        else {
            "active_for_ai": True,
            "requested": False,
            "source_ready": True,
            "scope_ready": True,
            "verified_pages": 1,
            "prepared_pages": 1,
            "unit_count": 2,
            "projection_count": 2,
            "failed_pages": 0,
        }
    )
    session.execute.return_value = SimpleNamespace(
        mappings=lambda: SimpleNamespace(one_or_none=lambda: row)
    )
    if missing:
        with pytest.raises(SourceDocumentNotFoundError):
            asyncio.run(
                summary.get_material_knowledge_preparation(
                    session, principal=ADMIN, document_id=DOCUMENT_ID
                )
            )
    else:
        response = asyncio.run(
            summary.get_material_knowledge_preparation(
                session, principal=ADMIN, document_id=DOCUMENT_ID
            )
        )
        assert response.status == "not_requested"
        assert response.requested is False
        assert response.pending_pages == 0
        assert response.prepared_pages == 1
    session.execute.assert_awaited_once()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
