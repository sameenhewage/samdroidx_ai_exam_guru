from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.papers.domain import (
    MAX_PAPER_VERSIONS,
    CandidateInvariantError,
    PaperAssemblyError,
    PaperState,
)
from exam_guru_api.papers.publication_repository import (
    PaperArchiveNotFoundError,
    PaperBlueprintNotFoundError,
    PaperCandidateSelectionSourceLimitError,
    PaperDraftNotFoundError,
    PaperNotFoundError,
    PaperPersistenceIntegrityError,
    PaperPublicationNotFoundError,
)
from exam_guru_api.papers.publication_schemas import (
    PaperAggregateResponse,
    PaperArchiveRequest,
    PaperArchiveResponse,
    PaperDraftCreateRequest,
    PaperDraftVersionResponse,
    PaperPublishRequest,
    PaperRevisionCreateRequest,
    PaperStateValue,
    PaperSummaryResponse,
    PublishedPaperVersionResponse,
    PublishedPaperVersionSummaryResponse,
)
from exam_guru_api.papers.publication_service import (
    PaperCandidateSelectionError,
    PaperCandidateSelectionResourceLimitError,
    PaperCommandInvalidError,
    PaperIdempotencyConflictError,
    PaperIntegrityError,
    PaperPublicationService,
    PaperStateConflictError,
    PaperVersionConflictError,
)

router = APIRouter()
ReviewPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.CONTENT_REVIEW)),
]
PublishPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.PAPER_PUBLISH)),
]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^\S+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
PaperVersion = Annotated[int, Path(ge=1, le=MAX_PAPER_VERSIONS)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Paper, blueprint, draft, publication, or archive not found",
        "model": ApiErrorResponse,
    },
    status.HTTP_409_CONFLICT: {
        "description": "Paper state, version, idempotency, or integrity conflict",
        "model": ApiErrorResponse,
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Invalid bounded paper command or candidate selection",
        "model": ApiErrorResponse,
    },
}


@router.post(
    "/{curriculum_version_id}/paper-drafts",
    operation_id="create_paper_draft",
    response_model=PaperDraftVersionResponse,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Assemble an approved candidate set into an immutable paper draft",
)
async def create_paper_draft(
    curriculum_version_id: UUID,
    request: PaperDraftCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PaperDraftVersionResponse:
    result = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).create_draft(
            curriculum_version_id,
            paper_blueprint_id=request.paper_blueprint_id,
            title=request.title,
            candidate_ids=request.candidate_ids,
            idempotency_key=idempotency_key,
            principal=principal,
        ),
    )
    return PaperDraftVersionResponse.from_record(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/papers",
    operation_id="list_practice_papers",
    response_model=list[PaperSummaryResponse],
    responses=_ERROR_RESPONSES,
    summary="List bounded curriculum-scoped paper aggregates",
)
async def list_practice_papers(
    curriculum_version_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    state: PaperStateValue | None = None,
    paper_blueprint_id: UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[PaperSummaryResponse]:
    records = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).list_papers(
            curriculum_version_id,
            principal=principal,
            state=PaperState(state) if state is not None else None,
            paper_blueprint_id=paper_blueprint_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [PaperSummaryResponse.from_record(record) for record in records]


@router.get(
    "/{curriculum_version_id}/papers/{paper_id}",
    operation_id="get_practice_paper",
    response_model=PaperAggregateResponse,
    responses=_ERROR_RESPONSES,
    summary="Get a curriculum-scoped paper aggregate",
)
async def get_practice_paper(
    curriculum_version_id: UUID,
    paper_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PaperAggregateResponse:
    model = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).get_paper(
            curriculum_version_id,
            paper_id,
            principal=principal,
        ),
    )
    return PaperAggregateResponse.from_model(model)


@router.get(
    "/{curriculum_version_id}/papers/{paper_id}/draft-versions",
    operation_id="list_paper_draft_versions",
    response_model=list[PaperDraftVersionResponse],
    responses=_ERROR_RESPONSES,
    summary="List immutable paper draft versions",
)
async def list_paper_draft_versions(
    curriculum_version_id: UUID,
    paper_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[PaperDraftVersionResponse]:
    records = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).list_drafts(
            curriculum_version_id,
            paper_id,
            principal=principal,
            limit=limit,
            offset=offset,
        ),
    )
    return [PaperDraftVersionResponse.from_record(record) for record in records]


@router.get(
    "/{curriculum_version_id}/papers/{paper_id}/draft-versions/{version}",
    operation_id="get_paper_draft_version",
    response_model=PaperDraftVersionResponse,
    responses=_ERROR_RESPONSES,
    summary="Get an immutable paper draft version",
)
async def get_paper_draft_version(
    curriculum_version_id: UUID,
    paper_id: UUID,
    version: PaperVersion,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PaperDraftVersionResponse:
    record = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).get_draft(
            curriculum_version_id,
            paper_id,
            version,
            principal=principal,
        ),
    )
    return PaperDraftVersionResponse.from_record(record)


@router.post(
    "/{curriculum_version_id}/papers/{paper_id}/revisions",
    operation_id="revise_practice_paper",
    response_model=PaperDraftVersionResponse,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_201_CREATED,
    summary="Create the next draft only from the current publication",
)
async def revise_practice_paper(
    curriculum_version_id: UUID,
    paper_id: UUID,
    request: PaperRevisionCreateRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PaperDraftVersionResponse:
    result = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).revise(
            curriculum_version_id,
            paper_id,
            expected_version=request.expected_version,
            candidate_ids=request.candidate_ids,
            title=request.title,
            principal=principal,
        ),
    )
    return PaperDraftVersionResponse.from_record(result.record)


@router.post(
    "/{curriculum_version_id}/papers/{paper_id}/publish",
    operation_id="publish_practice_paper",
    response_model=PublishedPaperVersionResponse,
    responses=_ERROR_RESPONSES,
    summary="Publish the current authoritative draft with optimistic concurrency",
)
async def publish_practice_paper(
    curriculum_version_id: UUID,
    paper_id: UUID,
    request: PaperPublishRequest,
    principal: PublishPrincipal,
    session: DatabaseSession,
) -> PublishedPaperVersionResponse:
    result = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).publish(
            curriculum_version_id,
            paper_id,
            expected_version=request.expected_version,
            principal=principal,
        ),
    )
    return PublishedPaperVersionResponse.from_record(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/papers/{paper_id}/publication-versions",
    operation_id="list_published_paper_versions",
    response_model=list[PublishedPaperVersionSummaryResponse],
    responses=_ERROR_RESPONSES,
    summary="List immutable publication metadata without materializing snapshots",
)
async def list_published_paper_versions(
    curriculum_version_id: UUID,
    paper_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[PublishedPaperVersionSummaryResponse]:
    records = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).list_publications(
            curriculum_version_id,
            paper_id,
            principal=principal,
            limit=limit,
            offset=offset,
        ),
    )
    return [PublishedPaperVersionSummaryResponse.from_record(record) for record in records]


@router.get(
    "/{curriculum_version_id}/papers/{paper_id}/publication-versions/{version}",
    operation_id="get_published_paper_version",
    response_model=PublishedPaperVersionResponse,
    responses=_ERROR_RESPONSES,
    summary="Get a verified full student-servable publication snapshot",
)
async def get_published_paper_version(
    curriculum_version_id: UUID,
    paper_id: UUID,
    version: PaperVersion,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PublishedPaperVersionResponse:
    record = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).get_publication(
            curriculum_version_id,
            paper_id,
            version,
            principal=principal,
        ),
    )
    return PublishedPaperVersionResponse.from_record(record)


@router.post(
    "/{curriculum_version_id}/papers/{paper_id}/archive",
    operation_id="archive_practice_paper",
    response_model=PaperArchiveResponse,
    responses=_ERROR_RESPONSES,
    summary="Archive the current publication terminally",
)
async def archive_practice_paper(
    curriculum_version_id: UUID,
    paper_id: UUID,
    request: PaperArchiveRequest,
    principal: PublishPrincipal,
    session: DatabaseSession,
) -> PaperArchiveResponse:
    result = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).archive(
            curriculum_version_id,
            paper_id,
            expected_version=request.expected_version,
            reason=request.reason,
            principal=principal,
        ),
    )
    return PaperArchiveResponse.from_record(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/papers/{paper_id}/archive",
    operation_id="get_paper_archive",
    response_model=PaperArchiveResponse,
    responses=_ERROR_RESPONSES,
    summary="Get the append-only terminal archive event",
)
async def get_paper_archive(
    curriculum_version_id: UUID,
    paper_id: UUID,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> PaperArchiveResponse:
    record = await _execute_paper_operation(
        session,
        lambda: PaperPublicationService(session).get_archive(
            curriculum_version_id,
            paper_id,
            principal=principal,
        ),
    )
    return PaperArchiveResponse.from_record(record)


async def _execute_paper_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_persistence_conflict"},
        ) from error
    except PaperBlueprintNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "paper_blueprint_not_found"},
        ) from error
    except PaperNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "paper_not_found"},
        ) from error
    except PaperDraftNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "paper_draft_not_found"},
        ) from error
    except PaperPublicationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "paper_publication_not_found"},
        ) from error
    except PaperArchiveNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "paper_archive_not_found"},
        ) from error
    except PaperIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_idempotency_conflict"},
        ) from error
    except PaperVersionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_version_conflict"},
        ) from error
    except PaperStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_state_conflict"},
        ) from error
    except (PaperIntegrityError, PaperPersistenceIntegrityError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "paper_integrity_invalid"},
        ) from error
    except (
        PaperCandidateSelectionResourceLimitError,
        PaperCandidateSelectionSourceLimitError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "paper_candidate_selection_too_large"},
        ) from error
    except (PaperCandidateSelectionError, PaperAssemblyError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "paper_candidate_selection_invalid"},
        ) from error
    except (PaperCommandInvalidError, CandidateInvariantError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "paper_command_invalid"},
        ) from error
