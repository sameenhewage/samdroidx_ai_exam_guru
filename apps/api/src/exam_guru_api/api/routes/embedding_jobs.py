from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import (
    get_database_session,
    get_embedding_dispatcher,
    get_embedding_provider_registry,
    get_settings,
)
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embedding_job_repository import EmbeddingJobNotFoundError
from exam_guru_api.knowledge.embedding_job_schemas import (
    EmbeddingJobCreateRequest,
    EmbeddingJobResponse,
)
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingCurriculumNotFoundError,
    EmbeddingIdempotencyConflictError,
    EmbeddingJobReadService,
    EmbeddingJobService,
    EmbeddingQueueUnavailableError,
    EmbeddingSourceNotFoundError,
    EmbeddingSourceNotReviewedError,
)
from exam_guru_api.knowledge.embedding_jobs import EmbeddingDispatcher
from exam_guru_api.knowledge.models import EmbeddingJobStatus
from exam_guru_api.knowledge.repository import (
    EmbeddingSourceConflictError,
    EmbeddingSpaceConflictError,
    KnowledgeRecordNotFoundError,
)
from exam_guru_api.knowledge.service import EmbeddingRequiresReviewedRecordError
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
    create_active_embedding_config,
)

router = APIRouter()
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_WRITE))]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_READ))]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ProviderRegistry = Annotated[
    EmbeddingProviderRegistry,
    Depends(get_embedding_provider_registry),
]
JobDispatcher = Annotated[EmbeddingDispatcher, Depends(get_embedding_dispatcher)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Embedding job, curriculum, or selected source not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Embedding idempotency, source identity, or persistence conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Selected source is not reviewed",
        "model": ApiErrorResponse,
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": "Embedding configuration, provider, or queue unavailable",
        "model": ApiErrorResponse,
    },
}


@router.post(
    "/{curriculum_version_id}/embedding-jobs",
    operation_id="create_embedding_job",
    response_model=EmbeddingJobResponse,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue reviewed curriculum records for server-owned embedding",
)
async def create_embedding_job(
    curriculum_version_id: UUID,
    request: EmbeddingJobCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: WritePrincipal,
    session: DatabaseSession,
    providers: ProviderRegistry,
    dispatcher: JobDispatcher,
    settings: ApplicationSettings,
) -> EmbeddingJobResponse:
    result = await _execute_embedding_operation(
        session,
        lambda: EmbeddingJobService(
            session,
            providers,
            dispatcher,
            create_active_embedding_config(settings),
        ).create(
            curriculum_version_id,
            historical_question_ids=request.historical_question_ids,
            knowledge_chunk_ids=request.knowledge_chunk_ids,
            idempotency_key=idempotency_key,
            actor_id=principal.subject_id,
        ),
    )
    return EmbeddingJobResponse.from_model(result.job, deduplicated=result.deduplicated)


@router.get(
    "/{curriculum_version_id}/embedding-jobs",
    operation_id="list_embedding_jobs",
    response_model=list[EmbeddingJobResponse],
    responses=_ERROR_RESPONSES,
    summary="List curriculum embedding jobs",
)
async def list_embedding_jobs(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    job_status: Annotated[EmbeddingJobStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[EmbeddingJobResponse]:
    del principal
    records = await _execute_embedding_operation(
        session,
        lambda: EmbeddingJobReadService(session).list(
            curriculum_version_id,
            status=job_status,
            limit=limit,
            offset=offset,
        ),
    )
    return [EmbeddingJobResponse.from_model(record) for record in records]


@router.get(
    "/{curriculum_version_id}/embedding-jobs/{embedding_job_id}",
    operation_id="get_embedding_job",
    response_model=EmbeddingJobResponse,
    responses=_ERROR_RESPONSES,
    summary="Get a durable curriculum embedding job",
)
async def get_embedding_job(
    curriculum_version_id: UUID,
    embedding_job_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> EmbeddingJobResponse:
    del principal
    record = await _execute_embedding_operation(
        session,
        lambda: EmbeddingJobReadService(session).get(
            curriculum_version_id,
            embedding_job_id,
        ),
    )
    return EmbeddingJobResponse.from_model(record)


async def _execute_embedding_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "embedding_persistence_conflict"},
        ) from error
    except EmbeddingJobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "embedding_job_not_found"},
        ) from error
    except EmbeddingCurriculumNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "embedding_curriculum_not_found"},
        ) from error
    except (EmbeddingSourceNotFoundError, KnowledgeRecordNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "embedding_source_not_found"},
        ) from error
    except (EmbeddingSourceNotReviewedError, EmbeddingRequiresReviewedRecordError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "embedding_source_not_reviewed"},
        ) from error
    except EmbeddingIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "embedding_idempotency_conflict"},
        ) from error
    except EmbeddingSourceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "embedding_source_conflict"},
        ) from error
    except EmbeddingSpaceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "embedding_config_conflict"},
        ) from error
    except EmbeddingQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_queue_unavailable"},
        ) from error
    except ActiveEmbeddingConfigUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_config_unavailable"},
        ) from error
    except EmbeddingProviderUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_provider_unavailable"},
        ) from error
