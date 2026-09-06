from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    CatalogueAdmissionConflictError,
    CatalogueAdmissionDecision,
    CatalogueAdmissionReview,
    CatalogueApprovalError,
    CatalogueScopeNotFoundError,
    MaterialCatalogueEntry,
    get_catalogue_admission,
    list_catalogue_admission_history,
    list_material_catalogue,
    record_catalogue_admission,
)


class CatalogueAdmissionErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str


class CatalogueAdmissionErrorResponse(BaseModel):
    detail: CatalogueAdmissionErrorDetail


router = APIRouter()
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.TAXONOMY_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.TAXONOMY_WRITE))]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
_ERRORS: dict[int | str, dict[str, object]] = {
    403: {"model": CatalogueAdmissionErrorResponse},
    404: {"model": CatalogueAdmissionErrorResponse},
    409: {"model": CatalogueAdmissionErrorResponse},
}


@router.get(
    "/material-catalogue",
    operation_id="list_material_catalogue",
    response_model=list[MaterialCatalogueEntry],
)
async def material_catalogue(
    principal: ReadPrincipal,
    session: DatabaseSession,
    grade: Annotated[int | None, Query(ge=1, le=13)] = None,
    medium_id: UUID | None = None,
    subject_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[MaterialCatalogueEntry]:
    del principal
    return await list_material_catalogue(
        session, grade=grade, medium_id=medium_id, subject_id=subject_id, limit=limit, offset=offset
    )


@router.get(
    "/curriculum-versions/{curriculum_version_id}/admission",
    operation_id="get_catalogue_admission",
    response_model=CatalogueAdmissionReview,
    responses=_ERRORS,
)
async def catalogue_admission_review(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> CatalogueAdmissionReview:
    del principal
    return await _run(session, lambda: get_catalogue_admission(session, curriculum_version_id))


@router.get(
    "/curriculum-versions/{curriculum_version_id}/admission/history",
    operation_id="list_catalogue_admission_history",
    response_model=list[CatalogueAdmissionDecision],
    responses=_ERRORS,
)
async def catalogue_admission_history(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_version: Annotated[int | None, Query(ge=1)] = None,
) -> list[CatalogueAdmissionDecision]:
    del principal
    return await _run(
        session,
        lambda: list_catalogue_admission_history(
            session, curriculum_version_id, limit=limit, before_version=before_version
        ),
    )


@router.post(
    "/curriculum-versions/{curriculum_version_id}/admission",
    operation_id="create_catalogue_admission_decision",
    response_model=CatalogueAdmissionReview,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
@router.patch(
    "/curriculum-versions/{curriculum_version_id}/admission",
    operation_id="review_catalogue_admission",
    response_model=CatalogueAdmissionReview,
    responses=_ERRORS,
)
async def catalogue_admission_decision(
    curriculum_version_id: UUID,
    request: AdmissionDecisionRequest,
    principal: WritePrincipal,
    session: DatabaseSession,
) -> CatalogueAdmissionReview:
    return await _run(
        session,
        lambda: record_catalogue_admission(
            session, curriculum_version_id, request, principal=principal
        ),
        commit=True,
    )


async def _run[ResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[ResultT]],
    *,
    commit: bool = False,
) -> ResultT:
    try:
        result = await operation()
        if commit:
            await session.commit()
        return result
    except CatalogueScopeNotFoundError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "catalogue_scope_not_found"}
        ) from error
    except CatalogueAdmissionConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": error.code}
        ) from error
    except CatalogueApprovalError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT
            if error.code == "catalogue_scope_inactive"
            else status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code},
        ) from error
    except AuthorizationError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"code": "permission_denied"}
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "catalogue_admission_conflict"}
        ) from error
