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
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    GenerationRuntimeUnavailableError,
)
from exam_guru_api.teacher_papers.domain import PaperScopeError
from exam_guru_api.teacher_papers.jobs import PaperGenerationDispatcher
from exam_guru_api.teacher_papers.repository import (
    TeacherPaperJobNotFoundError,
    TeacherPaperPersistenceConflictError,
    TeacherPaperRepository,
)
from exam_guru_api.teacher_papers.schemas import (
    CurriculumLabelsResponse,
    LessonLabelsResponse,
    TeacherPaperAdvanceRequest,
    TeacherPaperJobCreateRequest,
    TeacherPaperJobResponse,
    TeacherPaperOptionsResponse,
    TeacherPaperRetryRequest,
)
from exam_guru_api.teacher_papers.service import (
    TeacherPaperContextUnavailableError,
    TeacherPaperCostLimitError,
    TeacherPaperCurriculumAmbiguousError,
    TeacherPaperCurriculumNotFoundError,
    TeacherPaperIdempotencyConflictError,
    TeacherPaperJobService,
    TeacherPaperQueryService,
    TeacherPaperQueueUnavailableError,
    TeacherPaperRetryLimitError,
    TeacherPaperStateConflictError,
    TeacherPaperVersionConflictError,
    teacher_paper_job_response,
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
ReadPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.GENERATION_READ)),
]
CreatePrincipal = Annotated[
    Principal,
    Depends(
        require_rate_limit(
            Permission.GENERATION_RUN,
            RateLimitScope.GENERATION_CREATE_RETRY,
        )
    ),
]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$"),
]
GradeQuery = Annotated[int, Query(ge=1, le=13)]
CodeQuery = Annotated[str, Query(min_length=1, max_length=64)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "The requested active curriculum or paper job was not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Curriculum ambiguity, idempotency, version, state, or lineage conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "The teacher scope or bounded command is invalid",
        "model": ApiErrorResponse,
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "The configured generation runtime or durable queue is unavailable",
        "model": ApiErrorResponse,
    },
}
_COSTLY_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_ERROR_RESPONSES,
    status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    status.HTTP_503_SERVICE_UNAVAILABLE: RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
}


@router.get(
    "/options",
    operation_id="get_teacher_paper_generation_options",
    response_model=TeacherPaperOptionsResponse,
    responses=_ERROR_RESPONSES,
    summary="List teacher-readable paper generation options",
)
async def get_teacher_paper_generation_options(
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> TeacherPaperOptionsResponse:
    del principal
    return await _execute(session, lambda: TeacherPaperQueryService(session).options())


@router.get(
    "/curricula",
    operation_id="list_teacher_paper_curricula",
    response_model=CurriculumLabelsResponse,
    responses=_ERROR_RESPONSES,
    summary="List matching active curriculum labels without exposing internal IDs",
)
async def list_teacher_paper_curricula(
    principal: ReadPrincipal,
    session: DatabaseSession,
    grade: GradeQuery,
    medium: CodeQuery,
    subject: CodeQuery,
    assessment_programme: CodeQuery | None = None,
) -> CurriculumLabelsResponse:
    del principal
    return await _execute(
        session,
        lambda: TeacherPaperQueryService(session).curricula(
            grade=grade,
            medium=medium,
            subject=subject,
            assessment_programme=assessment_programme,
        ),
    )


@router.get(
    "/lessons",
    operation_id="list_teacher_paper_lessons",
    response_model=LessonLabelsResponse,
    responses=_ERROR_RESPONSES,
    summary="List normalized active lesson labels for one exact curriculum",
)
async def list_teacher_paper_lessons(
    principal: ReadPrincipal,
    session: DatabaseSession,
    grade: GradeQuery,
    medium: CodeQuery,
    subject: CodeQuery,
    assessment_programme: CodeQuery | None = None,
) -> LessonLabelsResponse:
    del principal
    return await _execute(
        session,
        lambda: TeacherPaperQueryService(session).lessons(
            grade=grade,
            medium=medium,
            subject=subject,
            assessment_programme=assessment_programme,
        ),
    )


@router.post(
    "/jobs",
    operation_id="create_teacher_paper_job",
    response_model=TeacherPaperJobResponse,
    responses=_COSTLY_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create and dispatch a durable teacher paper job",
)
async def create_teacher_paper_job(
    request: TeacherPaperJobCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: CreatePrincipal,
    session: DatabaseSession,
    dispatcher: PaperDispatcher,
    runtime: Runtime,
) -> TeacherPaperJobResponse:
    result = await _execute(
        session,
        lambda: TeacherPaperJobService(session, dispatcher, runtime).create(
            request,
            idempotency_key=idempotency_key,
            principal=principal,
        ),
    )
    return await teacher_paper_job_response(
        TeacherPaperRepository(session),
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/jobs/{paper_job_id}",
    operation_id="get_teacher_paper_job",
    response_model=TeacherPaperJobResponse,
    responses=_ERROR_RESPONSES,
    summary="Get durable paper progress, partial failures, and current slot lineage",
)
async def get_teacher_paper_job(
    paper_job_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    dispatcher: PaperDispatcher,
    runtime: Runtime,
) -> TeacherPaperJobResponse:
    record = await _execute(
        session,
        lambda: TeacherPaperJobService(session, dispatcher, runtime).get(
            paper_job_id,
            principal=principal,
        ),
    )
    return await teacher_paper_job_response(TeacherPaperRepository(session), record)


@router.post(
    "/jobs/{paper_job_id}/advance",
    operation_id="advance_teacher_paper_job",
    response_model=TeacherPaperJobResponse,
    responses=_COSTLY_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request one idempotent bounded aggregate advance",
)
async def advance_teacher_paper_job(
    paper_job_id: UUID,
    request: TeacherPaperAdvanceRequest,
    principal: CreatePrincipal,
    session: DatabaseSession,
    dispatcher: PaperDispatcher,
    runtime: Runtime,
) -> TeacherPaperJobResponse:
    record = await _execute(
        session,
        lambda: TeacherPaperJobService(session, dispatcher, runtime).advance(
            paper_job_id,
            expected_version=request.expected_version,
            principal=principal,
        ),
    )
    return await teacher_paper_job_response(TeacherPaperRepository(session), record)


@router.post(
    "/jobs/{paper_job_id}/retry",
    operation_id="retry_teacher_paper_job",
    response_model=TeacherPaperJobResponse,
    responses=_COSTLY_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create bounded auditable generation lineage for failed slots",
)
async def retry_teacher_paper_job(
    paper_job_id: UUID,
    request: TeacherPaperRetryRequest,
    idempotency_key: IdempotencyKey,
    principal: CreatePrincipal,
    session: DatabaseSession,
    dispatcher: PaperDispatcher,
    generation_dispatcher: GenerationJobDispatcher,
    runtime: Runtime,
) -> TeacherPaperJobResponse:
    record = await _execute(
        session,
        lambda: TeacherPaperJobService(session, dispatcher, runtime).retry(
            paper_job_id,
            expected_version=request.expected_version,
            idempotency_key=idempotency_key,
            principal=principal,
            generation_dispatcher=generation_dispatcher,
        ),
    )
    return await teacher_paper_job_response(TeacherPaperRepository(session), record)


async def _execute[ResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_persistence_conflict"},
        ) from error
    except (TeacherPaperCurriculumNotFoundError, TeacherPaperJobNotFoundError) as error:
        code = (
            "paper_generation_curriculum_not_found"
            if isinstance(error, TeacherPaperCurriculumNotFoundError)
            else "paper_generation_job_not_found"
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": code}) from error
    except TeacherPaperCurriculumAmbiguousError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_curriculum_ambiguous"},
        ) from error
    except TeacherPaperIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_idempotency_conflict"},
        ) from error
    except TeacherPaperVersionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_version_conflict"},
        ) from error
    except TeacherPaperStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_state_conflict"},
        ) from error
    except TeacherPaperRetryLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_retry_limit_exceeded"},
        ) from error
    except TeacherPaperCostLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_cost_limit_exceeded"},
        ) from error
    except PaperScopeError as error:
        detail: dict[str, object] = {"code": error.code}
        if error.lesson_number is not None:
            detail["lesson_number"] = error.lesson_number
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail,
        ) from error
    except TeacherPaperContextUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "paper_generation_context_unavailable"},
        ) from error
    except (GenerationRuntimeUnavailableError, TeacherPaperQueueUnavailableError) as error:
        code = (
            "paper_generation_runtime_unavailable"
            if isinstance(error, GenerationRuntimeUnavailableError)
            else "paper_generation_queue_unavailable"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": code},
        ) from error
    except TeacherPaperPersistenceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_generation_lineage_conflict"},
        ) from error
