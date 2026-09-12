from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from exam_guru_api.api.dependencies import get_database_session, get_object_storage, get_settings
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.evaluation_references import (
    EvaluationReferenceError,
    EvaluationReferenceService,
)
from exam_guru_api.documents.fidelity_schemas import (
    EvaluationPreviewResponse,
    EvaluationReferenceResponse,
    EvaluationReferenceSaveRequest,
)
from exam_guru_api.documents.page_images import (
    MAX_SOURCE_PAGE_NUMBER,
    PageImageError,
    create_page_image_artifacts,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


class _EvaluationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            try:
                response = await handler(request)
                response.headers.update(_PRIVATE_HEADERS)
                return response
            except StarletteHTTPException as error:
                raise HTTPException(
                    error.status_code,
                    detail=error.detail,
                    headers={**(error.headers or {}), **_PRIVATE_HEADERS},
                ) from None
            except RequestValidationError:
                raise HTTPException(
                    422,
                    detail={"code": "invalid_evaluation_reference_request"},
                    headers=_PRIVATE_HEADERS,
                ) from None

        return private_handler


router = APIRouter(
    route_class=_EvaluationRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]
Configuration = Annotated[Settings, Depends(get_settings)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
ReviewPrincipal = Annotated[Principal, Depends(require_permission(Permission.CONTENT_REVIEW))]
PreviewPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.CONTENT_REVIEW, RateLimitScope.EXTRACTION_TRIGGER)),
]
PageNumber = Annotated[int, Path(ge=1, le=MAX_SOURCE_PAGE_NUMBER)]


async def _run[Result](session: AsyncSession, operation: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await operation()
    except (EvaluationReferenceError, PageImageError) as error:
        await session.rollback()
        raise HTTPException(error.status_code, detail={"code": error.code}) from None
    except AuthorizationError:
        await session.rollback()
        raise HTTPException(403, detail={"code": "permission_denied"}) from None
    except ValueError:
        await session.rollback()
        raise HTTPException(422, detail={"code": "invalid_evaluation_reference_request"}) from None
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, detail={"code": "evaluation_reference_conflict"}) from None


def _service(
    session: AsyncSession, storage: ObjectStorage, settings: Settings
) -> EvaluationReferenceService:
    return EvaluationReferenceService(session, storage, create_page_image_artifacts(settings))


@router.post(
    "/source-benchmarks/{benchmark_id}/pages/{document_id}/{page_number}/evaluation-preview",
    operation_id="prepare_source_evaluation_preview",
    response_model=EvaluationPreviewResponse,
    status_code=201,
)
async def prepare_preview(
    benchmark_id: UUID,
    document_id: UUID,
    page_number: PageNumber,
    principal: PreviewPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: Configuration,
) -> EvaluationPreviewResponse:
    return await _run(
        session,
        lambda: _service(session, storage, settings).prepare(
            benchmark_id,
            document_id,
            page_number,
            principal=principal,
        ),
    )


@router.get(
    "/source-benchmarks/{benchmark_id}/evaluation-previews/{preview_id}/image",
    operation_id="get_source_evaluation_preview_image",
    response_class=Response,
    responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def preview_image(
    benchmark_id: UUID,
    preview_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: Configuration,
) -> Response:
    content = await _run(
        session,
        lambda: _service(session, storage, settings).image(
            benchmark_id,
            preview_id,
            principal=principal,
        ),
    )
    return Response(
        content,
        media_type="image/png",
        headers={
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; sandbox",
        },
    )


@router.post(
    "/source-benchmarks/{benchmark_id}/pages/{document_id}/{page_number}/evaluation-references",
    operation_id="save_source_evaluation_reference",
    response_model=EvaluationReferenceResponse,
    status_code=201,
)
async def save_reference(
    benchmark_id: UUID,
    document_id: UUID,
    page_number: PageNumber,
    request: EvaluationReferenceSaveRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: Configuration,
) -> EvaluationReferenceResponse:
    return await _run(
        session,
        lambda: _service(session, storage, settings).save(
            benchmark_id,
            document_id,
            page_number,
            request,
            principal=principal,
        ),
    )


@router.get(
    "/source-benchmarks/{benchmark_id}/pages/{document_id}/{page_number}/evaluation-references",
    operation_id="list_source_evaluation_references",
    response_model=list[EvaluationReferenceResponse],
)
async def reference_history(
    benchmark_id: UUID,
    document_id: UUID,
    page_number: PageNumber,
    principal: ReadPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: Configuration,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
) -> list[EvaluationReferenceResponse]:
    return await _run(
        session,
        lambda: _service(session, storage, settings).history(
            benchmark_id,
            document_id,
            page_number,
            principal=principal,
            limit=limit,
            offset=offset,
        ),
    )
