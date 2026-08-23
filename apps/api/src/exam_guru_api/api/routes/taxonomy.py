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
from exam_guru_api.curriculum.schemas import TaxonomyNodeCreate, TaxonomyNodeResponse
from exam_guru_api.curriculum.service import (
    CurriculumVersionNotFoundError,
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


def _curriculum_not_found_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "curriculum_version_not_found"},
    )
