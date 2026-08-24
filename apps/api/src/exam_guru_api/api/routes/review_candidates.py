from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.papers.domain import CandidateInvariantError, CandidateState
from exam_guru_api.papers.repository import (
    CandidatePersistenceIntegrityError,
    ReviewCandidateNotFoundError,
    ReviewCurriculumNotFoundError,
    ReviewValidationRunNotFoundError,
)
from exam_guru_api.papers.review_service import (
    ReviewCandidateIdempotencyConflictError,
    ReviewCandidateService,
    ReviewCandidateStateConflictError,
    ReviewCandidateVersionConflictError,
    ReviewUpstreamIntegrityError,
    ReviewValidationNotPassedError,
)
from exam_guru_api.papers.schemas import (
    CandidateStateValue,
    ReviewCandidateApproveRequest,
    ReviewCandidateCreateRequest,
    ReviewCandidateEditRequest,
    ReviewCandidateRejectRequest,
    ReviewCandidateResponse,
    ReviewCandidateStartRequest,
    ReviewCandidateSummaryResponse,
)

router = APIRouter()
ReviewPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.CONTENT_REVIEW)),
]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
SlotFilter = Annotated[str | None, Query(min_length=1, max_length=128)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Review candidate resource not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Review state, version, upstream, or persistence conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Invalid bounded review command or question content",
        "model": ApiErrorResponse,
    },
}


@router.post(
    "/{curriculum_version_id}/review-candidates",
    operation_id="create_review_candidate",
    response_model=ReviewCandidateResponse,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Create a validated review candidate from persisted validation evidence",
)
async def create_review_candidate(
    curriculum_version_id: UUID,
    request: ReviewCandidateCreateRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> ReviewCandidateResponse:
    result = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).create(
            curriculum_version_id,
            validation_run_id=request.validation_run_id,
            principal=principal,
        ),
    )
    return ReviewCandidateResponse.from_record(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/review-candidates",
    operation_id="list_review_candidates",
    response_model=list[ReviewCandidateSummaryResponse],
    responses=_ERROR_RESPONSES,
    summary="List bounded curriculum-scoped review candidates",
)
async def list_review_candidates(
    curriculum_version_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    state: CandidateStateValue | None = None,
    paper_blueprint_id: UUID | None = None,
    blueprint_slot_id: SlotFilter = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[ReviewCandidateSummaryResponse]:
    records = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).list(
            curriculum_version_id,
            principal=principal,
            state=CandidateState(state) if state is not None else None,
            paper_blueprint_id=paper_blueprint_id,
            blueprint_slot_id=blueprint_slot_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [ReviewCandidateSummaryResponse.from_record(record) for record in records]


@router.get(
    "/{curriculum_version_id}/review-candidates/{candidate_id}",
    operation_id="get_review_candidate",
    response_model=ReviewCandidateResponse,
    responses=_ERROR_RESPONSES,
    summary="Get a review candidate with revisions, events, lineage, and validation evidence",
)
async def get_review_candidate(
    curriculum_version_id: UUID,
    candidate_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> ReviewCandidateResponse:
    record = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).get(
            curriculum_version_id,
            candidate_id,
            principal=principal,
        ),
    )
    return ReviewCandidateResponse.from_record(record)


@router.post(
    "/{curriculum_version_id}/review-candidates/{candidate_id}/start-review",
    operation_id="start_review_candidate",
    response_model=ReviewCandidateResponse,
    responses=_ERROR_RESPONSES,
    summary="Start human review with optimistic concurrency",
)
async def start_review_candidate(
    curriculum_version_id: UUID,
    candidate_id: UUID,
    request: ReviewCandidateStartRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> ReviewCandidateResponse:
    record = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).start_review(
            curriculum_version_id,
            candidate_id,
            expected_version=request.expected_version,
            principal=principal,
        ),
    )
    return ReviewCandidateResponse.from_record(record)


@router.patch(
    "/{curriculum_version_id}/review-candidates/{candidate_id}",
    operation_id="edit_review_candidate",
    response_model=ReviewCandidateResponse,
    responses=_ERROR_RESPONSES,
    summary="Append a bounded human content revision",
)
async def edit_review_candidate(
    curriculum_version_id: UUID,
    candidate_id: UUID,
    request: ReviewCandidateEditRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> ReviewCandidateResponse:
    record = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).edit(
            curriculum_version_id,
            candidate_id,
            content=request.content.to_domain(),
            reason=request.reason,
            expected_version=request.expected_version,
            principal=principal,
        ),
    )
    return ReviewCandidateResponse.from_record(record)


@router.post(
    "/{curriculum_version_id}/review-candidates/{candidate_id}/approve",
    operation_id="approve_review_candidate",
    response_model=ReviewCandidateResponse,
    responses=_ERROR_RESPONSES,
    summary="Approve an in-review candidate with optimistic concurrency",
)
async def approve_review_candidate(
    curriculum_version_id: UUID,
    candidate_id: UUID,
    request: ReviewCandidateApproveRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> ReviewCandidateResponse:
    record = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).approve(
            curriculum_version_id,
            candidate_id,
            expected_version=request.expected_version,
            note=request.note,
            principal=principal,
        ),
    )
    return ReviewCandidateResponse.from_record(record)


@router.post(
    "/{curriculum_version_id}/review-candidates/{candidate_id}/reject",
    operation_id="reject_review_candidate",
    response_model=ReviewCandidateResponse,
    responses=_ERROR_RESPONSES,
    summary="Reject an in-review candidate with an explicit reason",
)
async def reject_review_candidate(
    curriculum_version_id: UUID,
    candidate_id: UUID,
    request: ReviewCandidateRejectRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> ReviewCandidateResponse:
    record = await _execute_review_operation(
        session,
        lambda: ReviewCandidateService(session).reject(
            curriculum_version_id,
            candidate_id,
            expected_version=request.expected_version,
            reason=request.reason,
            principal=principal,
        ),
    )
    return ReviewCandidateResponse.from_record(record)


async def _execute_review_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_persistence_conflict"},
        ) from error
    except ReviewValidationRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "review_validation_run_not_found"},
        ) from error
    except ReviewCandidateNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "review_candidate_not_found"},
        ) from error
    except ReviewCurriculumNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "review_curriculum_not_found"},
        ) from error
    except ReviewValidationNotPassedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_validation_not_passed"},
        ) from error
    except (ReviewUpstreamIntegrityError, CandidatePersistenceIntegrityError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_upstream_integrity_invalid"},
        ) from error
    except ReviewCandidateIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_candidate_idempotency_conflict"},
        ) from error
    except ReviewCandidateVersionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_candidate_version_conflict"},
        ) from error
    except ReviewCandidateStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "review_candidate_state_conflict"},
        ) from error
    except CandidateInvariantError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "review_candidate_content_invalid"},
        ) from error
