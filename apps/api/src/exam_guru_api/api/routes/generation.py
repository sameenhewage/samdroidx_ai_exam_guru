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
)
from exam_guru_api.api.schemas import RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE, ApiErrorResponse
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.blueprints.serialization import BlueprintSnapshotError
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.repository import (
    GenerationJobNotFoundError,
    GenerationPersistenceConflictError,
    GenerationRunNotFoundError,
)
from exam_guru_api.generation.run_service import (
    GenerationBlueprintNotFoundError,
    GenerationBlueprintScopeMismatchError,
    GenerationContextCrossCurriculumError,
    GenerationContextLimitError,
    GenerationContextNotFoundError,
    GenerationContextNotReviewedError,
    GenerationContextScopeInactiveError,
    GenerationContextSourceUntrustedError,
    GenerationContextTaxonomyMismatchError,
    GenerationCurriculumInactiveError,
    GenerationCurriculumNotFoundError,
    GenerationIdempotencyConflictError,
    GenerationQueueUnavailableError,
    GenerationRetryLimitExceededError,
    GenerationRetryStateError,
    GenerationRunService,
    GenerationSlotNotFoundError,
)
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    GenerationRuntimeUnavailableError,
)
from exam_guru_api.generation.schemas import (
    GenerationAttemptResponse,
    GenerationJobResponse,
    GenerationRunCreateRequest,
    GenerationRunResponse,
    GenerationRunSummaryResponse,
)

router = APIRouter()
RunPrincipal = Annotated[
    Principal,
    Depends(
        require_rate_limit(
            Permission.GENERATION_RUN,
            RateLimitScope.GENERATION_CREATE_RETRY,
        )
    ),
]
ReadPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.GENERATION_READ)),
]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
RuntimeRegistry = Annotated[
    GenerationRuntimeRegistry,
    Depends(get_generation_runtime_registry),
]
JobDispatcher = Annotated[GenerationDispatcher, Depends(get_generation_dispatcher)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]
AttemptLimit = Annotated[int, Query(ge=1, le=10)]
Offset = Annotated[int, Query(ge=0, le=100_000)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Generation resource not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Generation state conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Invalid generation selection",
        "model": ApiErrorResponse,
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "Generation configuration or queue unavailable",
        "model": ApiErrorResponse,
    },
}
_COSTLY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_ERROR_RESPONSES,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "Generation configuration, queue, or cost limiter unavailable",
        "model": ApiErrorResponse,
    },
}


@router.post(
    "/{curriculum_version_id}/generation-runs",
    operation_id="create_generation_run",
    response_model=GenerationJobResponse,
    responses=_COSTLY_ERROR_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create and queue a grounded generation run",
)
async def create_generation_run(
    curriculum_version_id: UUID,
    request: GenerationRunCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: RunPrincipal,
    session: DatabaseSession,
    runtime: RuntimeRegistry,
    dispatcher: JobDispatcher,
) -> GenerationJobResponse:
    result = await _execute_generation_operation(
        session,
        lambda: GenerationRunService(session, runtime, dispatcher).create(
            curriculum_version_id,
            paper_blueprint_id=request.paper_blueprint_id,
            slot_id=request.slot_id,
            knowledge_chunk_ids=request.knowledge_chunk_ids,
            historical_question_ids=request.historical_question_ids,
            idempotency_key=idempotency_key,
            actor_id=principal.subject_id,
        ),
    )
    return GenerationJobResponse.from_model(
        result.job,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/generation-runs",
    operation_id="list_generation_runs",
    response_model=list[GenerationRunSummaryResponse],
    responses=_ERROR_RESPONSES,
    summary="List generation runs",
)
async def list_generation_runs(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    runtime: RuntimeRegistry,
    dispatcher: JobDispatcher,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[GenerationRunSummaryResponse]:
    del principal
    records = await _execute_generation_operation(
        session,
        lambda: GenerationRunService(session, runtime, dispatcher).list_runs(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [GenerationRunSummaryResponse.from_model(record) for record in records]


@router.get(
    "/{curriculum_version_id}/generation-runs/{generation_run_id}",
    operation_id="get_generation_run",
    response_model=GenerationRunResponse,
    responses=_ERROR_RESPONSES,
    summary="Get a durable generation run",
)
async def get_generation_run(
    curriculum_version_id: UUID,
    generation_run_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    runtime: RuntimeRegistry,
    dispatcher: JobDispatcher,
) -> GenerationRunResponse:
    del principal
    record = await _execute_generation_operation(
        session,
        lambda: GenerationRunService(session, runtime, dispatcher).get_run(
            curriculum_version_id,
            generation_run_id,
        ),
    )
    return GenerationRunResponse.from_model(record)


@router.post(
    "/{curriculum_version_id}/generation-runs/{generation_run_id}/retry",
    operation_id="retry_generation_run",
    response_model=GenerationJobResponse,
    responses=_COSTLY_ERROR_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a new run retry from a failed generation run",
)
async def retry_generation_run(
    curriculum_version_id: UUID,
    generation_run_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: RunPrincipal,
    session: DatabaseSession,
    runtime: RuntimeRegistry,
    dispatcher: JobDispatcher,
) -> GenerationJobResponse:
    result = await _execute_generation_operation(
        session,
        lambda: GenerationRunService(session, runtime, dispatcher).retry(
            curriculum_version_id,
            generation_run_id,
            idempotency_key=idempotency_key,
            actor_id=principal.subject_id,
        ),
    )
    return GenerationJobResponse.from_model(
        result.job,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/generation-runs/{generation_run_id}/attempts",
    operation_id="list_generation_attempts",
    response_model=list[GenerationAttemptResponse],
    responses=_ERROR_RESPONSES,
    summary="List append-only provider attempts for a generation run",
)
async def list_generation_attempts(
    curriculum_version_id: UUID,
    generation_run_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    runtime: RuntimeRegistry,
    dispatcher: JobDispatcher,
    limit: AttemptLimit = 10,
    offset: Offset = 0,
) -> list[GenerationAttemptResponse]:
    del principal
    records = await _execute_generation_operation(
        session,
        lambda: GenerationRunService(session, runtime, dispatcher).list_attempts(
            curriculum_version_id,
            generation_run_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [GenerationAttemptResponse.from_model(record) for record in records]


@router.get(
    "/{curriculum_version_id}/generation-jobs/{generation_job_id}",
    operation_id="get_generation_job",
    response_model=GenerationJobResponse,
    responses=_ERROR_RESPONSES,
    summary="Get a durable generation queue job",
)
async def get_generation_job(
    curriculum_version_id: UUID,
    generation_job_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    runtime: RuntimeRegistry,
    dispatcher: JobDispatcher,
) -> GenerationJobResponse:
    del principal
    record = await _execute_generation_operation(
        session,
        lambda: GenerationRunService(session, runtime, dispatcher).get_job(
            curriculum_version_id,
            generation_job_id,
        ),
    )
    return GenerationJobResponse.from_model(record)


async def _execute_generation_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except (IntegrityError, GenerationPersistenceConflictError) as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "generation_persistence_conflict"},
        ) from error
    except (GenerationCurriculumNotFoundError, GenerationBlueprintNotFoundError) as error:
        code = (
            "generation_curriculum_not_found"
            if isinstance(error, GenerationCurriculumNotFoundError)
            else "generation_blueprint_not_found"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": code},
        ) from error
    except GenerationRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "generation_run_not_found"},
        ) from error
    except GenerationJobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "generation_job_not_found"},
        ) from error
    except GenerationCurriculumInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "generation_curriculum_inactive"},
        ) from error
    except GenerationIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "generation_idempotency_conflict"},
        ) from error
    except GenerationRetryStateError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "generation_retry_state_invalid"},
        ) from error
    except GenerationRetryLimitExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "generation_retry_limit_exceeded"},
        ) from error
    except GenerationSlotNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_slot_not_found"},
        ) from error
    except GenerationContextNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_not_found"},
        ) from error
    except GenerationContextCrossCurriculumError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_cross_curriculum"},
        ) from error
    except GenerationContextNotReviewedError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_not_reviewed"},
        ) from error
    except GenerationContextSourceUntrustedError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_source_untrusted"},
        ) from error
    except GenerationContextScopeInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_scope_inactive"},
        ) from error
    except GenerationContextTaxonomyMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_taxonomy_mismatch"},
        ) from error
    except GenerationContextLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "generation_context_limit_exceeded"},
        ) from error
    except (GenerationBlueprintScopeMismatchError, BlueprintSnapshotError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "generation_blueprint_snapshot_invalid"},
        ) from error
    except GenerationRuntimeUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "generation_runtime_unavailable"},
        ) from error
    except GenerationQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "generation_queue_unavailable"},
        ) from error
