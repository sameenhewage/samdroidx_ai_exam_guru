import json
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BeforeValidator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import (
    get_database_session,
    get_embedding_provider_registry,
    get_settings,
)
from exam_guru_api.api.routes.knowledge_units import ReviewBody
from exam_guru_api.api.routes.private_route import PrivateStudioRoute
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.material_indexing import (
    MaterialKnowledgeError,
    MaterialKnowledgeIndexRetryRequest,
    MaterialKnowledgeNotFoundError,
)
from exam_guru_api.knowledge.material_review import (
    MaterialKnowledgeReviewService,
    MaterialKnowledgeUnitsResponse,
    MaterialKnowledgeUnitWorkspace,
)
from exam_guru_api.knowledge.unit_review import KnowledgeUnitReviewError
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry


class _PrivateMaterialKnowledgeRoute(PrivateStudioRoute):
    invalid_request_code = "invalid_material_knowledge_request"


router = APIRouter(
    route_class=_PrivateMaterialKnowledgeRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_WRITE))]
Configuration = Annotated[Settings, Depends(get_settings)]
Providers = Annotated[EmbeddingProviderRegistry, Depends(get_embedding_provider_registry)]


def _retry_body(value: object) -> MaterialKnowledgeIndexRetryRequest:
    if isinstance(value, MaterialKnowledgeIndexRetryRequest):
        return MaterialKnowledgeIndexRetryRequest.model_validate(value)
    return MaterialKnowledgeIndexRetryRequest.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


RetryBody = Annotated[MaterialKnowledgeIndexRetryRequest, BeforeValidator(_retry_body)]


async def _execute[Result](
    session: AsyncSession, operation: Callable[[], Awaitable[Result]]
) -> Result:
    try:
        return await operation()
    except AuthorizationError:
        await session.rollback()
        raise HTTPException(403, detail={"code": "permission_denied"}) from None
    except MaterialKnowledgeNotFoundError:
        await session.rollback()
        raise HTTPException(404, detail={"code": "material_knowledge_not_found"}) from None
    except (MaterialKnowledgeError, KnowledgeUnitReviewError) as error:
        await session.rollback()
        code = str(error)
        allowed = {
            "knowledge_review_version_conflict",
            "knowledge_review_source_not_current",
            "knowledge_review_taxonomy_invalid",
            "material_indexing_version_conflict",
            "material_indexing_source_not_current",
            "material_indexing_retry_not_allowed",
            "material_indexing_retry_requires_confirmation",
        }
        if code in allowed:
            raise HTTPException(409, detail={"code": code}) from None
        raise HTTPException(503, detail={"code": "material_knowledge_unavailable"}) from None
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, detail={"code": "material_knowledge_conflict"}) from None
    except Exception:
        await session.rollback()
        raise HTTPException(503, detail={"code": "material_knowledge_unavailable"}) from None


@router.get(
    "/materials/{document_id}/knowledge-units",
    operation_id="list_material_knowledge_units",
    response_model=MaterialKnowledgeUnitsResponse,
)
async def list_material_knowledge_units(
    document_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    settings: Configuration,
    providers: Providers,
    limit: Annotated[int, Query(ge=1, le=25)] = 20,
    offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
) -> MaterialKnowledgeUnitsResponse:
    return await _execute(
        session,
        lambda: MaterialKnowledgeReviewService(session).list_units(
            principal=principal,
            document_id=document_id,
            settings=settings,
            providers=providers,
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/materials/{document_id}/knowledge-units/{unit_id}",
    operation_id="get_material_knowledge_unit_workspace",
    response_model=MaterialKnowledgeUnitWorkspace,
)
async def get_material_knowledge_unit_workspace(
    document_id: UUID,
    unit_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    settings: Configuration,
    providers: Providers,
) -> MaterialKnowledgeUnitWorkspace:
    return await _execute(
        session,
        lambda: MaterialKnowledgeReviewService(session).get_workspace(
            principal=principal,
            document_id=document_id,
            unit_id=unit_id,
            settings=settings,
            providers=providers,
        ),
    )


@router.post(
    "/materials/{document_id}/knowledge-units/{unit_id}/curriculum-review",
    operation_id="review_material_knowledge_unit",
    response_model=MaterialKnowledgeUnitWorkspace,
)
async def review_material_knowledge_unit(
    document_id: UUID,
    unit_id: UUID,
    body: ReviewBody,
    principal: WritePrincipal,
    session: DatabaseSession,
    settings: Configuration,
    providers: Providers,
) -> MaterialKnowledgeUnitWorkspace:
    async def command() -> MaterialKnowledgeUnitWorkspace:
        service = MaterialKnowledgeReviewService(session)
        await service.review(
            principal=principal, document_id=document_id, unit_id=unit_id, request=body
        )
        return await service.get_workspace(
            principal=principal,
            document_id=document_id,
            unit_id=unit_id,
            settings=settings,
            providers=providers,
        )

    return await _execute(session, command)


@router.post(
    "/materials/{document_id}/knowledge-units/{unit_id}/indexing-retry",
    operation_id="retry_material_knowledge_indexing",
    response_model=MaterialKnowledgeUnitWorkspace,
)
async def retry_material_knowledge_indexing(
    document_id: UUID,
    unit_id: UUID,
    body: RetryBody,
    principal: WritePrincipal,
    session: DatabaseSession,
    settings: Configuration,
    providers: Providers,
) -> MaterialKnowledgeUnitWorkspace:
    async def command() -> MaterialKnowledgeUnitWorkspace:
        service = MaterialKnowledgeReviewService(session)
        await service.retry(
            principal=principal,
            document_id=document_id,
            unit_id=unit_id,
            request=body,
            settings=settings,
            providers=providers,
        )
        return await service.get_workspace(
            principal=principal,
            document_id=document_id,
            unit_id=unit_id,
            settings=settings,
            providers=providers,
        )

    return await _execute(session, command)
