from collections.abc import Awaitable, Callable
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.documents.fidelity_models import SourceReadJobModel
from exam_guru_api.documents.fidelity_queries import (
    confirm_source_page,
    create_source_benchmark,
    edit_source_page,
    exclude_source_page,
    get_review_workspace,
    get_source_benchmark,
    get_source_page_candidate,
    list_source_benchmarks,
)
from exam_guru_api.documents.fidelity_schemas import (
    PageConfirmRequest,
    PageEditRequest,
    PageExcludeRequest,
    PageRereadRequest,
    PageReviewMutationResponse,
    PageReviewView,
    PageReviewWorkspaceResponse,
    SourceBenchmarkCreateRequest,
    SourceBenchmarkResponse,
    SourceReadJobResponse,
)
from exam_guru_api.documents.fidelity_service import (
    FidelitySourceNotFoundError,
    PageFidelityConflictError,
    PageVerificationBlockedError,
)
from exam_guru_api.documents.page_reading_jobs import (
    SourceReadDispatcher,
    dispatch_source_read,
    queue_source_read,
)

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


def _private_response(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


router = APIRouter(
    dependencies=[Depends(_private_response)],
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 422)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
TrustPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_TRUST))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))]
ReviewPrincipal = Annotated[Principal, Depends(require_permission(Permission.CONTENT_REVIEW))]
PageNumber = Annotated[int, Path(ge=1)]
TriggerPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.EXTRACTION_TRIGGER, RateLimitScope.EXTRACTION_TRIGGER)),
]


def get_source_read_dispatcher(request: Request) -> SourceReadDispatcher:
    dispatcher = getattr(request.app.state, "source_read_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(
            503, detail={"code": "source_reading_unavailable"}, headers=_PRIVATE_HEADERS
        )
    return cast(SourceReadDispatcher, dispatcher)


ReadDispatcher = Annotated[SourceReadDispatcher, Depends(get_source_read_dispatcher)]


@router.post(
    "/materials/{document_id}/pages/{page_number}/reread",
    operation_id="reread_source_page",
    response_model=SourceReadJobResponse,
    status_code=202,
)
async def reread_page(
    document_id: UUID,
    page_number: PageNumber,
    request: PageRereadRequest,
    principal: TriggerPrincipal,
    session: DatabaseSession,
    dispatcher: ReadDispatcher,
) -> SourceReadJobResponse:
    job = await _run(
        session,
        lambda: queue_source_read(
            session,
            document_id,
            page_number=page_number,
            expected_page_version=request.expected_version,
            actor_id=principal.subject_id,
        ),
    )
    await dispatch_source_read(job.id, dispatcher)
    return SourceReadJobResponse.model_validate(job)


@router.post(
    "/source-documents/{document_id}/read",
    operation_id="read_source_document",
    response_model=SourceReadJobResponse,
    status_code=202,
)
async def read_document(
    document_id: UUID,
    principal: TriggerPrincipal,
    session: DatabaseSession,
    dispatcher: ReadDispatcher,
) -> SourceReadJobResponse:
    job = await _run(
        session,
        lambda: queue_source_read(
            session,
            document_id,
            actor_id=principal.subject_id,
        ),
    )
    await dispatch_source_read(job.id, dispatcher)
    return SourceReadJobResponse.model_validate(job)


@router.get("/source-read-jobs/{job_id}", response_model=SourceReadJobResponse)
async def source_read_job(
    job_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> SourceReadJobResponse:
    del principal
    job = await session.get(SourceReadJobModel, job_id)
    if job is None:
        raise HTTPException(
            404, detail={"code": "source_read_job_not_found"}, headers=_PRIVATE_HEADERS
        )
    return SourceReadJobResponse.model_validate(job)


@router.get(
    "/materials/{document_id}/review-workspace",
    operation_id="get_material_review_workspace",
    response_model=PageReviewWorkspaceResponse,
)
async def review_workspace(
    document_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    page_number: Annotated[int, Query(ge=1)] = 1,
) -> PageReviewWorkspaceResponse:
    return await _run(
        session,
        lambda: get_review_workspace(
            session, document_id, page_number=page_number, principal=principal
        ),
    )


@router.get(
    "/materials/{document_id}/pages/{page_number}/candidates/{candidate_id}",
    operation_id="get_source_page_candidate",
    response_model=PageReviewView,
)
async def source_page_candidate(
    document_id: UUID,
    page_number: PageNumber,
    candidate_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> PageReviewView:
    return await _run(
        session,
        lambda: get_source_page_candidate(
            session, document_id, page_number, candidate_id, principal=principal
        ),
    )


@router.post(
    "/materials/{document_id}/pages/{page_number}/confirm",
    operation_id="confirm_source_page",
    response_model=PageReviewMutationResponse,
)
async def confirm_page(
    document_id: UUID,
    page_number: PageNumber,
    request: PageConfirmRequest,
    principal: TrustPrincipal,
    session: DatabaseSession,
) -> PageReviewMutationResponse:
    return await _run(
        session,
        lambda: confirm_source_page(
            session, document_id, page_number, request, principal=principal
        ),
    )


@router.post(
    "/materials/{document_id}/pages/{page_number}/edit",
    operation_id="edit_source_page",
    response_model=PageReviewMutationResponse,
)
async def edit_page(
    document_id: UUID,
    page_number: PageNumber,
    request: PageEditRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PageReviewMutationResponse:
    return await _run(
        session,
        lambda: edit_source_page(session, document_id, page_number, request, principal=principal),
    )


@router.post(
    "/materials/{document_id}/pages/{page_number}/exclude",
    operation_id="exclude_source_page",
    response_model=PageReviewMutationResponse,
)
async def exclude_page(
    document_id: UUID,
    page_number: PageNumber,
    request: PageExcludeRequest,
    principal: WritePrincipal,
    session: DatabaseSession,
) -> PageReviewMutationResponse:
    return await _run(
        session,
        lambda: exclude_source_page(
            session, document_id, page_number, request, principal=principal
        ),
    )


@router.get(
    "/source-benchmarks",
    operation_id="list_source_benchmarks",
    response_model=list[SourceBenchmarkResponse],
)
async def source_benchmarks(
    principal: ReadPrincipal,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[SourceBenchmarkResponse]:
    return await _run(
        session,
        lambda: list_source_benchmarks(session, principal=principal, limit=limit, offset=offset),
    )


@router.get(
    "/source-benchmarks/{benchmark_id}",
    operation_id="get_source_benchmark",
    response_model=SourceBenchmarkResponse,
)
async def source_benchmark(
    benchmark_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> SourceBenchmarkResponse:
    return await _run(
        session, lambda: get_source_benchmark(session, benchmark_id, principal=principal)
    )


@router.post(
    "/source-benchmarks",
    operation_id="create_source_benchmark",
    response_model=SourceBenchmarkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def new_source_benchmark(
    request: SourceBenchmarkCreateRequest,
    principal: WritePrincipal,
    session: DatabaseSession,
) -> SourceBenchmarkResponse:
    return await _run(
        session, lambda: create_source_benchmark(session, request, principal=principal)
    )


async def _run[ResultT](
    session: AsyncSession, operation: Callable[[], Awaitable[ResultT]]
) -> ResultT:
    try:
        return await operation()
    except FidelitySourceNotFoundError as error:
        await session.rollback()
        code = str(error)
        if code not in {
            "source_document_not_found",
            "source_page_not_found",
            "source_candidate_not_found",
            "source_benchmark_not_found",
        }:
            code = "source_fidelity_not_found"
        raise HTTPException(404, detail={"code": code}, headers=_PRIVATE_HEADERS) from error
    except PageFidelityConflictError as error:
        await session.rollback()
        code = str(error)
        if code not in {
            "source_page_version_conflict",
            "source_candidate_changed",
            "explicit_page_revision_required",
            "page_reading_in_progress",
        }:
            code = "source_fidelity_conflict"
        raise HTTPException(409, detail={"code": code}, headers=_PRIVATE_HEADERS) from error
    except PageVerificationBlockedError as error:
        await session.rollback()
        reason = str(error)
        if reason not in {"source_candidate_not_confirmable", "source_page_not_awaiting_review"}:
            reason = "source_verification_requirement_not_met"
        raise HTTPException(
            409,
            detail={"code": "source_page_verification_blocked", "reason_code": reason},
            headers=_PRIVATE_HEADERS,
        ) from error
    except AuthorizationError as error:
        await session.rollback()
        raise HTTPException(
            403, detail={"code": "permission_denied"}, headers=_PRIVATE_HEADERS
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            409, detail={"code": "source_fidelity_conflict"}, headers=_PRIVATE_HEADERS
        ) from error
    except ValueError as error:
        await session.rollback()
        raise HTTPException(
            422, detail={"code": "invalid_source_fidelity_request"}, headers=_PRIVATE_HEADERS
        ) from error
