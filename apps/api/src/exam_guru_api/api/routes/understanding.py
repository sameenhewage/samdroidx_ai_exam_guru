from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.documents.page_images import MAX_SOURCE_PAGE_NUMBER
from exam_guru_api.documents.understanding_jobs import (
    UnderstandingJobDispatcher,
    UnderstandingJobNotFoundError,
    UnderstandingJobService,
    dispatch_understanding_job,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingBudget,
    UnderstandingProviderProfile,
)
from exam_guru_api.documents.understanding_runtime import UnderstandingRuntime
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
    UnderstandingSourceError,
)
from exam_guru_api.documents.understanding_verification import (
    ObservationCandidate,
    PageVerificationReport,
    TrustedPageKnowledge,
)
from exam_guru_api.generation.domain import GenerationAccounting

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


class _PrivateUnderstandingRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                result = await original(request)
                result.headers.update(_PRIVATE_HEADERS)
                return result
            except StarletteHTTPException as error:
                raise HTTPException(
                    error.status_code,
                    detail=error.detail,
                    headers={**(error.headers or {}), **_PRIVATE_HEADERS},
                ) from None
            except RequestValidationError:
                raise HTTPException(
                    422,
                    detail={"code": "invalid_source_understanding_request"},
                    headers=_PRIVATE_HEADERS,
                ) from None

        return handler


class UnderstandingJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request_id: UUID
    expected_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    reason: str = Field(min_length=1, max_length=2000)


class UnderstandingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    page_number: int
    status: Literal["queued", "running", "succeeded", "failed", "unknown"]
    version: int
    expected_page_version: int
    attempts: int
    retry_depth: int
    candidate_id: UUID | None
    run_id: UUID | None
    failure_code: str | None
    accounting: GenerationAccounting | None
    profile: UnderstandingProviderProfile
    budget: UnderstandingBudget


class UnderstandingPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    document_id: UUID
    page_number: int
    version: int
    state: str
    candidate: ObservationCandidate | None
    report: PageVerificationReport | None
    trusted: TrustedPageKnowledge | None
    active_job_id: UUID | None
    latest_job: UnderstandingJobResponse | None = None
    provider_available: bool = False


router = APIRouter(
    route_class=_PrivateUnderstandingRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
TriggerPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.SOURCE_WRITE, RateLimitScope.DOCUMENT_UNDERSTANDING)),
]
PageNumber = Annotated[int, Path(ge=1, le=MAX_SOURCE_PAGE_NUMBER)]


def get_understanding_runtime(request: Request) -> UnderstandingRuntime:
    runtime = getattr(request.app.state, "understanding_runtime", None)
    if runtime is None:
        raise HTTPException(503, detail={"code": "source_understanding_provider_unconfigured"})
    return cast(UnderstandingRuntime, runtime)


def get_understanding_dispatcher(request: Request) -> UnderstandingJobDispatcher:
    dispatcher = getattr(request.app.state, "understanding_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(503, detail={"code": "source_understanding_queue_unavailable"})
    return cast(UnderstandingJobDispatcher, dispatcher)


Runtime = Annotated[UnderstandingRuntime, Depends(get_understanding_runtime)]
Dispatcher = Annotated[UnderstandingJobDispatcher, Depends(get_understanding_dispatcher)]


async def _run[Result](session: AsyncSession, operation: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await operation()
    except AuthorizationError:
        await session.rollback()
        raise HTTPException(403, detail={"code": "permission_denied"}) from None
    except (UnderstandingSourceError, UnderstandingJobNotFoundError):
        await session.rollback()
        raise HTTPException(404, detail={"code": "source_understanding_not_found"}) from None
    except (UnderstandingConflictError, IntegrityError):
        await session.rollback()
        raise HTTPException(409, detail={"code": "source_understanding_conflict"}) from None
    except ValueError:
        await session.rollback()
        raise HTTPException(422, detail={"code": "invalid_source_understanding_request"}) from None


@router.get(
    "/materials/{document_id}/pages/{page_number}/understanding",
    operation_id="get_source_page_understanding",
    response_model=UnderstandingPageResponse,
)
async def get_page_understanding(
    document_id: UUID,
    page_number: PageNumber,
    request: Request,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> UnderstandingPageResponse:
    page = await _run(
        session,
        lambda: PageUnderstandingService(session).get_page(
            principal=principal, document_id=document_id, page_number=page_number
        ),
    )
    latest = await _run(
        session,
        lambda: UnderstandingJobService(session).latest_for_page(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            page_version=page.version,
        ),
    )
    response = UnderstandingPageResponse.model_validate(page)
    return response.model_copy(
        update={
            "provider_available": getattr(request.app.state, "understanding_runtime", None)
            is not None,
            "latest_job": None
            if latest is None
            else UnderstandingJobResponse.model_validate(latest),
        }
    )


@router.post(
    "/materials/{document_id}/pages/{page_number}/understanding/jobs",
    status_code=202,
    operation_id="create_source_understanding_job",
    response_model=UnderstandingJobResponse,
)
async def create_understanding_job(
    document_id: UUID,
    page_number: PageNumber,
    body: UnderstandingJobCreateRequest,
    principal: TriggerPrincipal,
    session: DatabaseSession,
    runtime: Runtime,
    dispatcher: Dispatcher,
) -> UnderstandingJobResponse:
    job = await _run(
        session,
        lambda: UnderstandingJobService(session).create(
            principal=principal,
            request_id=body.request_id,
            document_id=document_id,
            page_number=page_number,
            expected_version=body.expected_version,
            runtime=runtime,
            reason=body.reason,
        ),
    )
    if job.status == "queued":
        await dispatch_understanding_job(job.id, dispatcher)
    return UnderstandingJobResponse.model_validate(job)


@router.get(
    "/materials/understanding/jobs/{job_id}",
    operation_id="get_source_understanding_job",
    response_model=UnderstandingJobResponse,
)
async def get_understanding_job(
    job_id: UUID, principal: ReadPrincipal, session: DatabaseSession
) -> UnderstandingJobResponse:
    job = await _run(
        session, lambda: UnderstandingJobService(session).get(principal=principal, job_id=job_id)
    )
    return UnderstandingJobResponse.model_validate(job)
