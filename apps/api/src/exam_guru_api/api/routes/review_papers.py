from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import (
    get_database_session,
    get_generation_dispatcher,
    get_generation_runtime_registry,
    get_paper_generation_dispatcher,
)
from exam_guru_api.api.schemas import (
    RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
    ApiErrorResponse,
)
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry
from exam_guru_api.papers.publication_service import (
    PaperCandidateSelectionError,
    PaperIdempotencyConflictError,
    PaperIntegrityError,
)
from exam_guru_api.papers.repository import ReviewCandidateNotFoundError
from exam_guru_api.papers.schemas import (
    ReviewCandidateApproveRequest,
    ReviewCandidateRejectRequest,
    ReviewCandidateStartRequest,
)
from exam_guru_api.teacher_papers.jobs import PaperGenerationDispatcher
from exam_guru_api.teacher_papers.repository import (
    TeacherPaperJobNotFoundError,
    TeacherPaperPersistenceConflictError,
    TeacherPaperQuestionNotFoundError,
)
from exam_guru_api.teacher_papers.schemas import (
    ReviewPaperCreateDraftRequest,
    ReviewPaperDetailResponse,
    ReviewPaperDraftCreatedResponse,
    ReviewPaperListResponse,
    ReviewQuestionEditRequest,
    ReviewQuestionRegenerateRequest,
    ReviewQuestionRegenerationResponse,
    ReviewQuestionResponse,
)
from exam_guru_api.teacher_papers.service import (
    TeacherPaperCostLimitError,
    TeacherPaperQueueUnavailableError,
    TeacherPaperRetryLimitError,
    TeacherPaperRevalidationRequiredError,
    TeacherPaperReviewService,
    TeacherPaperStateConflictError,
    TeacherPaperVersionConflictError,
)

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
PaperDispatcher = Annotated[
    PaperGenerationDispatcher,
    Depends(get_paper_generation_dispatcher),
]
GenerationJobDispatcher = Annotated[
    GenerationDispatcher,
    Depends(get_generation_dispatcher),
]
Runtime = Annotated[
    GenerationRuntimeRegistry,
    Depends(get_generation_runtime_registry),
]
ReviewPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.CONTENT_REVIEW)),
]
RegeneratePrincipal = Annotated[
    Principal,
    Depends(
        require_rate_limit(
            Permission.CONTENT_REVIEW,
            RateLimitScope.GENERATION_CREATE_RETRY,
        )
    ),
]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "The review paper or question was not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Review version, state, revalidation, cost, or lineage conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Invalid bounded review content or command",
        "model": ApiErrorResponse,
    },
}
_REGENERATE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_ERROR_RESPONSES,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    status.HTTP_503_SERVICE_UNAVAILABLE: RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
}


def _service(
    session: AsyncSession,
    paper_dispatcher: PaperGenerationDispatcher,
    generation_dispatcher: GenerationDispatcher,
    runtime: GenerationRuntimeRegistry,
) -> TeacherPaperReviewService:
    return TeacherPaperReviewService(
        session,
        paper_dispatcher,
        generation_dispatcher,
        runtime,
    )


@router.get(
    "",
    operation_id="list_teacher_review_papers",
    response_model=ReviewPaperListResponse,
    responses=_ERROR_RESPONSES,
    summary="List teacher-readable generated paper aggregates",
)
async def list_teacher_review_papers(
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ReviewPaperListResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).list(principal=principal, limit=limit, offset=offset),
    )


@router.get(
    "/{paper_job_id}",
    operation_id="get_teacher_review_paper",
    response_model=ReviewPaperDetailResponse,
    responses=_ERROR_RESPONSES,
    summary="Get questions, answers, marking, readable sources, and validation together",
)
async def get_teacher_review_paper(
    paper_job_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewPaperDetailResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).get(paper_job_id, principal=principal),
    )


@router.patch(
    "/{paper_job_id}/questions/{question_id}",
    operation_id="edit_teacher_review_question",
    response_model=ReviewQuestionResponse,
    responses=_ERROR_RESPONSES,
    summary="Append an edit and require fresh generation validation before approval",
)
async def edit_teacher_review_question(
    paper_job_id: UUID,
    question_id: UUID,
    request: ReviewQuestionEditRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewQuestionResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).edit(paper_job_id, question_id, request, principal=principal),
    )


@router.post(
    "/{paper_job_id}/questions/{question_id}/start",
    operation_id="start_teacher_review_question",
    response_model=ReviewQuestionResponse,
    responses=_ERROR_RESPONSES,
    summary="Start review with an expected candidate version",
)
async def start_teacher_review_question(
    paper_job_id: UUID,
    question_id: UUID,
    request: ReviewCandidateStartRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewQuestionResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).start(
            paper_job_id,
            question_id,
            expected_version=request.expected_version,
            principal=principal,
        ),
    )


@router.post(
    "/{paper_job_id}/questions/{question_id}/approve",
    operation_id="approve_teacher_review_question",
    response_model=ReviewQuestionResponse,
    responses=_ERROR_RESPONSES,
    summary="Approve only a currently validated unedited question",
)
async def approve_teacher_review_question(
    paper_job_id: UUID,
    question_id: UUID,
    request: ReviewCandidateApproveRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewQuestionResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).approve(
            paper_job_id,
            question_id,
            expected_version=request.expected_version,
            note=request.note,
            principal=principal,
        ),
    )


@router.post(
    "/{paper_job_id}/questions/{question_id}/reject",
    operation_id="reject_teacher_review_question",
    response_model=ReviewQuestionResponse,
    responses=_ERROR_RESPONSES,
    summary="Reject a question with an expected version and reason",
)
async def reject_teacher_review_question(
    paper_job_id: UUID,
    question_id: UUID,
    request: ReviewCandidateRejectRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewQuestionResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).reject(
            paper_job_id,
            question_id,
            expected_version=request.expected_version,
            reason=request.reason,
            principal=principal,
        ),
    )


@router.post(
    "/{paper_job_id}/questions/{question_id}/regenerate",
    operation_id="regenerate_teacher_review_question",
    response_model=ReviewQuestionRegenerationResponse,
    responses=_REGENERATE_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue bounded replacement generation and full canonical revalidation",
)
async def regenerate_teacher_review_question(
    paper_job_id: UUID,
    question_id: UUID,
    request: ReviewQuestionRegenerateRequest,
    idempotency_key: IdempotencyKey,
    principal: RegeneratePrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewQuestionRegenerationResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).regenerate(
            paper_job_id,
            question_id,
            request,
            idempotency_key=idempotency_key,
            principal=principal,
        ),
    )


@router.post(
    "/{paper_job_id}/create-draft",
    operation_id="create_teacher_review_paper_draft",
    response_model=ReviewPaperDraftCreatedResponse,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Create an immutable draft only after every question is approved",
)
async def create_teacher_review_paper_draft(
    paper_job_id: UUID,
    request: ReviewPaperCreateDraftRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    paper_dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> ReviewPaperDraftCreatedResponse:
    return await _execute_review(
        session,
        lambda: _service(
            session,
            paper_dispatcher,
            generation_dispatcher,
            runtime,
        ).create_draft(paper_job_id, request, principal=principal),
    )


async def _execute_review[ResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_paper_persistence_conflict"},
        ) from error
    except TeacherPaperJobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "review_paper_not_found"},
        ) from error
    except (TeacherPaperQuestionNotFoundError, ReviewCandidateNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "review_question_not_found"},
        ) from error
    except TeacherPaperVersionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_question_version_conflict"},
        ) from error
    except TeacherPaperRevalidationRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_question_revalidation_required"},
        ) from error
    except TeacherPaperStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_paper_state_conflict"},
        ) from error
    except TeacherPaperRetryLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_question_regeneration_limit_exceeded"},
        ) from error
    except TeacherPaperCostLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_question_cost_limit_exceeded"},
        ) from error
    except TeacherPaperQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "review_question_queue_unavailable"},
        ) from error
    except (
        TeacherPaperPersistenceConflictError,
        PaperCandidateSelectionError,
        PaperIdempotencyConflictError,
        PaperIntegrityError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_paper_lineage_conflict"},
        ) from error
