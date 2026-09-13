from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.understanding import _PrivateUnderstandingRoute
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.documents.service import SourceDocumentNotFoundError
from exam_guru_api.knowledge.preparation_summary import (
    MaterialKnowledgePreparationResponse,
    get_material_knowledge_preparation,
)


class _PrivatePreparationRoute(_PrivateUnderstandingRoute):
    invalid_request_code = "invalid_material_knowledge_preparation_request"


router = APIRouter(
    route_class=_PrivatePreparationRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 422, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]


@router.get(
    "/materials/{document_id}/knowledge-preparation",
    operation_id="get_material_knowledge_preparation",
    response_model=MaterialKnowledgePreparationResponse,
)
async def material_knowledge_preparation(
    document_id: UUID, principal: ReadPrincipal, session: DatabaseSession
) -> MaterialKnowledgePreparationResponse:
    try:
        return await get_material_knowledge_preparation(
            session, principal=principal, document_id=document_id
        )
    except AuthorizationError:
        raise HTTPException(403, detail={"code": "permission_denied"}) from None
    except SourceDocumentNotFoundError:
        raise HTTPException(404, detail={"code": "source_document_not_found"}) from None
    except (SQLAlchemyError, ValueError):
        await session.rollback()
        raise HTTPException(
            503, detail={"code": "material_knowledge_preparation_unavailable"}
        ) from None
