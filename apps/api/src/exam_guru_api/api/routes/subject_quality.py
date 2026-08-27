from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session, get_validation_pipeline
from exam_guru_api.api.schemas import (
    RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
    ApiErrorResponse,
)
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.subject_quality.domain import EvalCaseState
from exam_guru_api.subject_quality.repository import (
    SubjectQualityEvalCaseNotFoundError,
    SubjectQualityEvalRunNotFoundError,
    SubjectQualityFeedbackNotFoundError,
)
from exam_guru_api.subject_quality.schemas import (
    SubjectQualityEvalApprovalRequest,
    SubjectQualityEvalCaseListResponse,
    SubjectQualityEvalCaseResponse,
    SubjectQualityEvalExportResponse,
    SubjectQualityEvalRunRequest,
    SubjectQualityEvalRunResponse,
    SubjectQualityFeedbackListResponse,
    SubjectQualityPromotionRequest,
)
from exam_guru_api.subject_quality.service import (
    SubjectQualityEvalIntegrityError,
    SubjectQualityEvalService,
    SubjectQualityEvalVersionConflictError,
    SubjectQualityFeedbackPersistenceError,
    SubjectQualityFeedbackService,
    SubjectQualityPromotionConflictError,
    SubjectQualitySecondReviewerRequiredError,
)
from exam_guru_api.validation.pipeline import ValidationPipeline

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ActivePipeline = Annotated[ValidationPipeline, Depends(get_validation_pipeline)]
ReviewPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.CONTENT_REVIEW)),
]
EvalRunPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.CONTENT_REVIEW, RateLimitScope.VALIDATION_RUN)),
]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Private Studio quality evidence was not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Immutable lineage, idempotency, version, or second-review conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "The bounded quality-evidence request is invalid",
        "model": ApiErrorResponse,
    },
}
_COSTLY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_ERROR_RESPONSES,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    status.HTTP_503_SERVICE_UNAVAILABLE: RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
}


@router.get(
    "/feedback",
    operation_id="list_subject_quality_feedback",
    response_model=SubjectQualityFeedbackListResponse,
    responses=_ERROR_RESPONSES,
    summary="List append-only reviewer evidence inside the private Studio",
)
async def list_subject_quality_feedback(
    principal: ReviewPrincipal,
    session: DatabaseSession,
    candidate_id: UUID | None = None,
    curriculum_version_id: UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> SubjectQualityFeedbackListResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityFeedbackService(session).list(
            principal=principal,
            candidate_id=candidate_id,
            curriculum_version_id=curriculum_version_id,
            limit=limit,
            offset=offset,
        ),
    )


@router.post(
    "/feedback/{feedback_id}/promote",
    operation_id="promote_subject_quality_feedback",
    response_model=SubjectQualityEvalCaseResponse,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Explicitly promote one feedback item to an immutable draft golden eval case",
)
async def promote_subject_quality_feedback(
    feedback_id: UUID,
    request: SubjectQualityPromotionRequest,
    idempotency_key: IdempotencyKey,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
) -> SubjectQualityEvalCaseResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityEvalService(session, pipeline).promote(
            feedback_id,
            request,
            idempotency_key=idempotency_key,
            principal=principal,
        ),
    )


@router.get(
    "/eval-cases",
    operation_id="list_subject_quality_eval_cases",
    response_model=SubjectQualityEvalCaseListResponse,
    responses=_ERROR_RESPONSES,
    summary="List latest immutable versions of private Studio golden eval cases",
)
async def list_subject_quality_eval_cases(
    principal: ReviewPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
    state: EvalCaseState | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> SubjectQualityEvalCaseListResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityEvalService(session, pipeline).list_cases(
            principal=principal,
            state=state,
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/eval-cases/export",
    operation_id="export_subject_quality_eval_cases",
    response_model=SubjectQualityEvalExportResponse,
    responses=_ERROR_RESPONSES,
    summary="Export a bounded stable JSON contract for the offline eval runner",
)
async def export_subject_quality_eval_cases(
    principal: ReviewPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
    limit: Limit = 50,
    offset: Offset = 0,
) -> SubjectQualityEvalExportResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityEvalService(session, pipeline).export(
            principal=principal,
            limit=limit,
            offset=offset,
        ),
    )


@router.post(
    "/eval-cases/{eval_case_id}/approve",
    operation_id="approve_subject_quality_eval_case",
    response_model=SubjectQualityEvalCaseResponse,
    responses=_ERROR_RESPONSES,
    summary="Append second-reviewer approval with compare-and-swap",
)
async def approve_subject_quality_eval_case(
    eval_case_id: UUID,
    request: SubjectQualityEvalApprovalRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
) -> SubjectQualityEvalCaseResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityEvalService(session, pipeline).approve(
            eval_case_id,
            expected_version=request.expected_version,
            principal=principal,
        ),
    )


@router.post(
    "/eval-runs",
    operation_id="run_subject_quality_evals",
    response_model=SubjectQualityEvalRunResponse,
    responses=_COSTLY_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Replay approved cases through the current configured validation pipeline",
)
async def run_subject_quality_evals(
    request: SubjectQualityEvalRunRequest,
    principal: EvalRunPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
) -> SubjectQualityEvalRunResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityEvalService(session, pipeline).run(request, principal=principal),
    )


@router.get(
    "/eval-runs/{run_id}",
    operation_id="get_subject_quality_eval_run",
    response_model=SubjectQualityEvalRunResponse,
    responses=_ERROR_RESPONSES,
    summary="Get one immutable deterministic subject-quality eval run",
)
async def get_subject_quality_eval_run(
    run_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
) -> SubjectQualityEvalRunResponse:
    return await _execute_quality_operation(
        session,
        lambda: SubjectQualityEvalService(session, pipeline).get_run(run_id, principal=principal),
    )


async def _execute_quality_operation[ResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    try:
        return await operation()
    except SubjectQualityFeedbackNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "subject_quality_feedback_not_found"},
        ) from error
    except SubjectQualityEvalCaseNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "subject_quality_eval_case_not_found"},
        ) from error
    except SubjectQualityEvalRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "subject_quality_eval_run_not_found"},
        ) from error
    except SubjectQualitySecondReviewerRequiredError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "eval_case_second_reviewer_required"},
        ) from error
    except SubjectQualityEvalVersionConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "subject_quality_eval_version_conflict"},
        ) from error
    except SubjectQualityPromotionConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "subject_quality_promotion_conflict"},
        ) from error
    except (SubjectQualityFeedbackPersistenceError, SubjectQualityEvalIntegrityError) as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "subject_quality_persistence_conflict"},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "subject_quality_persistence_conflict"},
        ) from error
