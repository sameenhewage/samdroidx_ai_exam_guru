from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.analytics.backtest import BacktestContractError
from exam_guru_api.analytics.domain import AnalyticsContractError
from exam_guru_api.analytics.repository import (
    AnalyticsRunFingerprintConflictError,
    AnalyticsRunNotFoundError,
)
from exam_guru_api.analytics.schemas import (
    AnalyticsRunRequest,
    AnalyticsRunResponse,
    AnalyticsRunSummaryResponse,
    data_quality_error_payload,
)
from exam_guru_api.analytics.service import (
    AnalyticsCurriculumNotFoundError,
    AnalyticsInsufficientHistoryError,
    AnalyticsRecordLimitError,
    AnalyticsRunService,
    AnalyticsSyllabusEmptyError,
    AnalyticsYearLimitError,
)
from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal

router = APIRouter()
RunPrincipal = Annotated[Principal, Depends(require_permission(Permission.ANALYTICS_RUN))]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.ANALYTICS_READ))]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post(
    "/{curriculum_version_id}/analytics/runs",
    operation_id="create_analytics_run",
    response_model=AnalyticsRunResponse,
    responses={
        status.HTTP_200_OK: {"description": "Existing identical analytics run returned"},
        status.HTTP_201_CREATED: {"description": "Deterministic analytics run persisted"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Curriculum version not found",
            "model": ApiErrorResponse,
        },
        status.HTTP_409_CONFLICT: {
            "description": "Analytics fingerprint conflict",
            "model": ApiErrorResponse,
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Insufficient or out-of-bounds reviewed evidence",
            "model": ApiErrorResponse,
        },
    },
    status_code=status.HTTP_201_CREATED,
    summary="Run bounded historical analysis and held-out practice evaluation",
)
async def create_analytics_run(
    curriculum_version_id: UUID,
    request: AnalyticsRunRequest,
    response: Response,
    principal: RunPrincipal,
    session: DatabaseSession,
) -> AnalyticsRunResponse:
    result = await _execute_analytics_operation(
        session,
        lambda: AnalyticsRunService(session).create_run(
            curriculum_version_id,
            request.to_domain(),
            actor_id=principal.subject_id,
        ),
    )
    response.status_code = status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED
    return AnalyticsRunResponse.from_record(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/analytics/runs",
    operation_id="list_analytics_runs",
    response_model=list[AnalyticsRunSummaryResponse],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Curriculum version not found",
            "model": ApiErrorResponse,
        }
    },
    summary="List persisted historical analysis runs",
)
async def list_analytics_runs(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[AnalyticsRunSummaryResponse]:
    del principal
    records = await _execute_analytics_operation(
        session,
        lambda: AnalyticsRunService(session).list_runs(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [AnalyticsRunSummaryResponse.from_record(record) for record in records]


@router.get(
    "/{curriculum_version_id}/analytics/runs/{run_id}",
    operation_id="get_analytics_run",
    response_model=AnalyticsRunResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Curriculum version or analytics run not found",
            "model": ApiErrorResponse,
        }
    },
    summary="Get one persisted historical analysis run",
)
async def get_analytics_run(
    curriculum_version_id: UUID,
    run_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> AnalyticsRunResponse:
    del principal
    record = await _execute_analytics_operation(
        session,
        lambda: AnalyticsRunService(session).get_run(curriculum_version_id, run_id),
    )
    return AnalyticsRunResponse.from_record(record)


async def _execute_analytics_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "analytics_persistence_conflict"},
        ) from error
    except AnalyticsCurriculumNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "curriculum_version_not_found"},
        ) from error
    except AnalyticsRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "analytics_run_not_found"},
        ) from error
    except AnalyticsSyllabusEmptyError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "analytics_syllabus_empty"},
        ) from error
    except AnalyticsRecordLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "analytics_record_limit_exceeded",
                "maximum": error.maximum,
            },
        ) from error
    except AnalyticsYearLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "analytics_year_limit_exceeded",
                "maximum": error.maximum,
                "actual": error.actual,
            },
        ) from error
    except AnalyticsInsufficientHistoryError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "analytics_insufficient_history",
                "required_year_count": error.required_year_count,
                "available_years": list(error.available_years),
                "data_quality": data_quality_error_payload(error.data_quality),
            },
        ) from error
    except AnalyticsRunFingerprintConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "analytics_run_fingerprint_conflict"},
        ) from error
    except (AnalyticsContractError, BacktestContractError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "analytics_input_invalid"},
        ) from error
