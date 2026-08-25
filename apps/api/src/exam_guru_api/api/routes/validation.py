from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import (
    get_database_session,
    get_operational_telemetry,
    get_validation_pipeline,
)
from exam_guru_api.api.schemas import (
    RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
    ApiErrorResponse,
)
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.observability import OperationalTelemetry
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.repository import (
    ValidationGenerationNotFoundError,
    ValidationRunNotFoundError,
)
from exam_guru_api.validation.schemas import (
    ValidationFindingResponse,
    ValidationRunCreateRequest,
    ValidationRunResponse,
    ValidationRunSummaryResponse,
)
from exam_guru_api.validation.service import (
    ValidationCurriculumNotFoundError,
    ValidationGenerationIntegrityError,
    ValidationGenerationNotSucceededError,
    ValidationIdempotencyConflictError,
    ValidationPipelineVersionConflictError,
    ValidationResourceLimitError,
    ValidationRunService,
)

router = APIRouter()
RunPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.VALIDATION_RUN, RateLimitScope.VALIDATION_RUN)),
]
ReadPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.VALIDATION_READ)),
]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ActivePipeline = Annotated[ValidationPipeline, Depends(get_validation_pipeline)]
Telemetry = Annotated[OperationalTelemetry | None, Depends(get_operational_telemetry)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
FindingOffset = Annotated[int, Query(ge=0, le=10_000)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Validation resource not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Generation, pipeline, or persistence integrity conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Validation input exceeds a deterministic resource bound",
        "model": ApiErrorResponse,
    },
}
_COSTLY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_ERROR_RESPONSES,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    status.HTTP_503_SERVICE_UNAVAILABLE: RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
}


@router.post(
    "/{curriculum_version_id}/validation-runs",
    operation_id="create_validation_run",
    response_model=ValidationRunResponse,
    responses=_COSTLY_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Run and persist canonical validation for a succeeded generation",
)
async def create_validation_run(
    curriculum_version_id: UUID,
    request: ValidationRunCreateRequest,
    principal: RunPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
    telemetry: Telemetry = None,
) -> ValidationRunResponse:
    service = (
        ValidationRunService(session, pipeline)
        if telemetry is None
        else ValidationRunService(session, pipeline, telemetry=telemetry)
    )
    result = await _execute_validation_operation(
        session,
        lambda: service.create(
            curriculum_version_id,
            generation_run_id=request.generation_run_id,
            actor_id=principal.subject_id,
        ),
    )
    return ValidationRunResponse.from_model(result.run, deduplicated=result.deduplicated)


@router.get(
    "/{curriculum_version_id}/validation-runs",
    operation_id="list_validation_runs",
    response_model=list[ValidationRunSummaryResponse],
    responses=_ERROR_RESPONSES,
    summary="List immutable validation runs",
)
async def list_validation_runs(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[ValidationRunSummaryResponse]:
    del principal
    records = await _execute_validation_operation(
        session,
        lambda: ValidationRunService(session, pipeline).list_runs(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [ValidationRunSummaryResponse.from_model(record) for record in records]


@router.get(
    "/{curriculum_version_id}/validation-runs/{validation_run_id}",
    operation_id="get_validation_run",
    response_model=ValidationRunResponse,
    responses=_ERROR_RESPONSES,
    summary="Get an immutable validation input and report",
)
async def get_validation_run(
    curriculum_version_id: UUID,
    validation_run_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
) -> ValidationRunResponse:
    del principal
    record = await _execute_validation_operation(
        session,
        lambda: ValidationRunService(session, pipeline).get_run(
            curriculum_version_id,
            validation_run_id,
        ),
    )
    return ValidationRunResponse.from_model(record)


@router.get(
    "/{curriculum_version_id}/validation-runs/{validation_run_id}/findings",
    operation_id="list_validation_findings",
    response_model=list[ValidationFindingResponse],
    responses=_ERROR_RESPONSES,
    summary="List bounded append-only findings for a validation run",
)
async def list_validation_findings(
    curriculum_version_id: UUID,
    validation_run_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    pipeline: ActivePipeline,
    limit: Limit = 100,
    offset: FindingOffset = 0,
) -> list[ValidationFindingResponse]:
    del principal
    records = await _execute_validation_operation(
        session,
        lambda: ValidationRunService(session, pipeline).list_findings(
            curriculum_version_id,
            validation_run_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [ValidationFindingResponse.from_model(record) for record in records]


async def _execute_validation_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "validation_persistence_conflict"},
        ) from error
    except ValidationGenerationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "validation_generation_run_not_found"},
        ) from error
    except ValidationRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "validation_run_not_found"},
        ) from error
    except ValidationCurriculumNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "validation_curriculum_not_found"},
        ) from error
    except ValidationGenerationNotSucceededError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "validation_generation_not_succeeded"},
        ) from error
    except ValidationGenerationIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "validation_generation_integrity_invalid"},
        ) from error
    except ValidationPipelineVersionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "validation_pipeline_version_conflict"},
        ) from error
    except ValidationIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "validation_idempotency_conflict"},
        ) from error
    except ValidationResourceLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "validation_resource_limit_exceeded"},
        ) from error
