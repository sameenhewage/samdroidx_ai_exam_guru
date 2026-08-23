from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.curriculum.domain import (
    TaxonomyNode,
    TaxonomyValidationError,
    TaxonomyViolation,
)
from exam_guru_api.curriculum.schemas import (
    TaxonomyNodeCreate,
    TaxonomyNodeResponse,
    TaxonomyNodeUpdate,
)
from exam_guru_api.curriculum.service import (
    CurriculumVersionInactiveError,
    CurriculumVersionNotFoundError,
    TaxonomyNodeNotFoundError,
    TaxonomyService,
)

router = APIRouter()


@router.get(
    "/{curriculum_version_id}/taxonomy/nodes",
    operation_id="list_taxonomy_nodes",
    response_model=list[TaxonomyNodeResponse],
    summary="List curriculum taxonomy nodes",
)
async def list_taxonomy_nodes(
    curriculum_version_id: UUID,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.TAXONOMY_READ)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[TaxonomyNodeResponse]:
    del principal
    try:
        nodes = await TaxonomyService(session).list_nodes(curriculum_version_id)
    except CurriculumVersionNotFoundError as error:
        raise _curriculum_not_found_exception() from error
    return [TaxonomyNodeResponse.from_domain(node) for node in nodes]


@router.post(
    "/{curriculum_version_id}/taxonomy/nodes",
    operation_id="create_taxonomy_node",
    response_model=TaxonomyNodeResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Curriculum version not found"},
        status.HTTP_409_CONFLICT: {"description": "Taxonomy conflict"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Invalid taxonomy hierarchy"},
    },
    status_code=status.HTTP_201_CREATED,
    summary="Create a curriculum taxonomy node",
)
async def create_taxonomy_node(
    curriculum_version_id: UUID,
    request: TaxonomyNodeCreate,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.TAXONOMY_WRITE)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TaxonomyNodeResponse:
    try:
        node = TaxonomyNode(
            id=uuid4(),
            curriculum_version_id=curriculum_version_id,
            parent_id=request.parent_id,
            level=request.level,
            code=request.code,
            title=request.title,
            active=request.active,
        )
        created = await TaxonomyService(session).add_node(node, actor_id=principal.subject_id)
    except CurriculumVersionNotFoundError as error:
        raise _curriculum_not_found_exception() from error
    except TaxonomyValidationError as error:
        response_status = (
            status.HTTP_409_CONFLICT
            if error.violation
            in {TaxonomyViolation.DUPLICATE_ID, TaxonomyViolation.DUPLICATE_SIBLING_CODE}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=response_status,
            detail={"code": error.violation.value, "node_id": str(error.node_id)},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "taxonomy_conflict"},
        ) from error
    return TaxonomyNodeResponse.from_domain(created)


@router.patch(
    "/{curriculum_version_id}/taxonomy/nodes/{node_id}",
    operation_id="update_taxonomy_node",
    response_model=TaxonomyNodeResponse,
)
async def update_taxonomy_node_route(
    curriculum_version_id: UUID,
    node_id: UUID,
    request: TaxonomyNodeUpdate,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.TAXONOMY_WRITE)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TaxonomyNodeResponse:
    updated = await _execute_taxonomy_write(
        session,
        lambda: TaxonomyService(session).update_node(
            curriculum_version_id,
            node_id,
            code=request.code,
            title=request.title,
            actor_id=principal.subject_id,
        ),
    )
    return TaxonomyNodeResponse.from_domain(updated)


@router.post(
    "/{curriculum_version_id}/taxonomy/nodes/{node_id}/review",
    operation_id="review_taxonomy_node",
    response_model=TaxonomyNodeResponse,
)
async def review_taxonomy_node_route(
    curriculum_version_id: UUID,
    node_id: UUID,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.CONTENT_REVIEW)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TaxonomyNodeResponse:
    reviewed = await _execute_taxonomy_write(
        session,
        lambda: TaxonomyService(session).review_node(
            curriculum_version_id,
            node_id,
            actor_id=principal.subject_id,
        ),
    )
    return TaxonomyNodeResponse.from_domain(reviewed)


@router.post(
    "/{curriculum_version_id}/taxonomy/nodes/{node_id}/deactivate",
    operation_id="deactivate_taxonomy_node",
    response_model=TaxonomyNodeResponse,
)
async def deactivate_taxonomy_node_route(
    curriculum_version_id: UUID,
    node_id: UUID,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.TAXONOMY_WRITE)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> TaxonomyNodeResponse:
    deprecated = await _execute_taxonomy_write(
        session,
        lambda: TaxonomyService(session).deactivate_node(
            curriculum_version_id,
            node_id,
            actor_id=principal.subject_id,
        ),
    )
    return TaxonomyNodeResponse.from_domain(deprecated)


async def _execute_taxonomy_write(
    session: AsyncSession,
    operation: Callable[[], Awaitable[TaxonomyNode]],
) -> TaxonomyNode:
    try:
        return await operation()
    except CurriculumVersionNotFoundError as error:
        raise _curriculum_not_found_exception() from error
    except CurriculumVersionInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "curriculum_version_inactive"},
        ) from error
    except TaxonomyNodeNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "taxonomy_node_not_found"},
        ) from error
    except TaxonomyValidationError as error:
        response_status = (
            status.HTTP_409_CONFLICT
            if error.violation
            in {
                TaxonomyViolation.DUPLICATE_ID,
                TaxonomyViolation.DUPLICATE_SIBLING_CODE,
                TaxonomyViolation.INVALID_REVIEW_TRANSITION,
                TaxonomyViolation.REVIEWED_NODE_IMMUTABLE,
                TaxonomyViolation.REVIEWED_PARENT_REQUIRED,
                TaxonomyViolation.INACTIVE_PARENT,
            }
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=response_status,
            detail={"code": error.violation.value, "node_id": str(error.node_id)},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "taxonomy_conflict"},
        ) from error


def _curriculum_not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "curriculum_version_not_found"},
    )
