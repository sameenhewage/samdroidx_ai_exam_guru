import json
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BeforeValidator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.understanding import _PrivateUnderstandingRoute
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.knowledge.unit_review import (
    KnowledgeReviewRequest,
    KnowledgeUnitReview,
    KnowledgeUnitReviewError,
    KnowledgeUnitReviewService,
    KnowledgeUnitWorkspace,
)


class _PrivateKnowledgeUnitRoute(_PrivateUnderstandingRoute):
    invalid_request_code = "invalid_knowledge_unit_request"


router = APIRouter(
    route_class=_PrivateKnowledgeUnitRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_WRITE))]


def _review_body(value: object) -> KnowledgeReviewRequest:
    if isinstance(value, KnowledgeReviewRequest):
        return KnowledgeReviewRequest.model_validate(value)
    return KnowledgeReviewRequest.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


ReviewBody = Annotated[KnowledgeReviewRequest, BeforeValidator(_review_body)]


async def _execute[Result](
    session: AsyncSession, operation: Callable[[], Awaitable[Result]]
) -> Result:
    try:
        return await operation()
    except KnowledgeUnitReviewError as error:
        await session.rollback()
        code = str(error)
        status = 404 if code.endswith("_not_found") else 503 if code.startswith("stored_") else 409
        raise HTTPException(status, detail={"code": code}) from None
    except AuthorizationError:
        await session.rollback()
        raise HTTPException(403, detail={"code": "permission_denied"}) from None
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, detail={"code": "knowledge_unit_review_conflict"}) from None
    except ValueError:
        await session.rollback()
        raise HTTPException(422, detail={"code": "invalid_knowledge_unit_request"}) from None


@router.get(
    "/{curriculum_version_id}/knowledge/units/{unit_id}",
    operation_id="get_knowledge_unit_workspace",
    response_model=KnowledgeUnitWorkspace,
)
async def get_knowledge_unit_workspace(
    curriculum_version_id: UUID, unit_id: UUID, principal: ReadPrincipal, session: DatabaseSession
) -> KnowledgeUnitWorkspace:
    return await _execute(
        session,
        lambda: KnowledgeUnitReviewService(session).get_workspace(
            principal=principal, unit_id=unit_id, curriculum_version_id=curriculum_version_id
        ),
    )


@router.post(
    "/{curriculum_version_id}/knowledge/units/{unit_id}/reviews",
    operation_id="review_knowledge_unit",
    response_model=KnowledgeUnitReview,
)
async def review_knowledge_unit(
    curriculum_version_id: UUID,
    unit_id: UUID,
    body: ReviewBody,
    principal: WritePrincipal,
    session: DatabaseSession,
) -> KnowledgeUnitReview:
    return await _execute(
        session,
        lambda: KnowledgeUnitReviewService(session).review(
            principal=principal,
            unit_id=unit_id,
            request=body,
            curriculum_version_id=curriculum_version_id,
        ),
    )
