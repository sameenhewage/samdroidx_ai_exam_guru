from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.curriculum.configuration_schemas import (
    CurriculumVersionCreate,
    CurriculumVersionResponse,
    CurriculumVersionUpdate,
    ExamConfigurationCreate,
    ExamConfigurationResponse,
    ExamConfigurationUpdate,
    MediumCreate,
    MediumResponse,
    MediumUpdate,
)
from exam_guru_api.curriculum.configuration_service import (
    ConfigurationInactiveError,
    ConfigurationInUseError,
    ConfigurationNotFoundError,
    ConfigurationService,
)

router = APIRouter()


def read_principal() -> object:
    return Depends(require_permission(Permission.TAXONOMY_READ))


def write_principal() -> object:
    return Depends(require_permission(Permission.TAXONOMY_WRITE))


@router.get(
    "/exam-configurations",
    operation_id="list_exam_configurations",
    response_model=list[ExamConfigurationResponse],
)
async def list_exam_configurations(
    principal: Annotated[Principal, read_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[ExamConfigurationResponse]:
    del principal
    return [
        ExamConfigurationResponse.model_validate(model)
        for model in await ConfigurationService(session).list_exams()
    ]


@router.post(
    "/exam-configurations",
    operation_id="create_exam_configuration",
    response_model=ExamConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_exam_configuration(
    request: ExamConfigurationCreate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExamConfigurationResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).create_exam(request, actor_id=principal.subject_id),
    )
    return ExamConfigurationResponse.model_validate(model)


@router.patch(
    "/exam-configurations/{resource_id}",
    operation_id="update_exam_configuration",
    response_model=ExamConfigurationResponse,
)
async def update_exam_configuration(
    resource_id: UUID,
    request: ExamConfigurationUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExamConfigurationResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).update_exam(
            resource_id, request.name, actor_id=principal.subject_id
        ),
    )
    return ExamConfigurationResponse.model_validate(model)


@router.post(
    "/exam-configurations/{resource_id}/deactivate",
    operation_id="deactivate_exam_configuration",
    response_model=ExamConfigurationResponse,
)
async def deactivate_exam_configuration(
    resource_id: UUID,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> ExamConfigurationResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).deactivate_exam(
            resource_id, actor_id=principal.subject_id
        ),
    )
    return ExamConfigurationResponse.model_validate(model)


@router.get("/media", operation_id="list_media", response_model=list[MediumResponse])
async def list_media(
    principal: Annotated[Principal, read_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[MediumResponse]:
    del principal
    return [
        MediumResponse.model_validate(model)
        for model in await ConfigurationService(session).list_media()
    ]


@router.post(
    "/media",
    operation_id="create_medium",
    response_model=MediumResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_medium(
    request: MediumCreate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MediumResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).create_medium(request, actor_id=principal.subject_id),
    )
    return MediumResponse.model_validate(model)


@router.patch("/media/{resource_id}", operation_id="update_medium", response_model=MediumResponse)
async def update_medium(
    resource_id: UUID,
    request: MediumUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MediumResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).update_medium(
            resource_id, request.name, actor_id=principal.subject_id
        ),
    )
    return MediumResponse.model_validate(model)


@router.post(
    "/media/{resource_id}/deactivate",
    operation_id="deactivate_medium",
    response_model=MediumResponse,
)
async def deactivate_medium(
    resource_id: UUID,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> MediumResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).deactivate_medium(
            resource_id, actor_id=principal.subject_id
        ),
    )
    return MediumResponse.model_validate(model)


@router.get(
    "/curriculum-versions",
    operation_id="list_curriculum_versions",
    response_model=list[CurriculumVersionResponse],
)
async def list_curriculum_versions(
    principal: Annotated[Principal, read_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[CurriculumVersionResponse]:
    del principal
    return [
        CurriculumVersionResponse.model_validate(model)
        for model in await ConfigurationService(session).list_curricula()
    ]


@router.post(
    "/curriculum-versions",
    operation_id="create_curriculum_version",
    response_model=CurriculumVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_curriculum_version(
    request: CurriculumVersionCreate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumVersionResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).create_curriculum(
            request, actor_id=principal.subject_id
        ),
    )
    return CurriculumVersionResponse.model_validate(model)


@router.patch(
    "/curriculum-versions/{resource_id}",
    operation_id="update_curriculum_version",
    response_model=CurriculumVersionResponse,
)
async def update_curriculum_version(
    resource_id: UUID,
    request: CurriculumVersionUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumVersionResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).update_curriculum(
            resource_id, request.title, actor_id=principal.subject_id
        ),
    )
    return CurriculumVersionResponse.model_validate(model)


@router.post(
    "/curriculum-versions/{resource_id}/deactivate",
    operation_id="deactivate_curriculum_version",
    response_model=CurriculumVersionResponse,
)
async def deactivate_curriculum_version(
    resource_id: UUID,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumVersionResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).deactivate_curriculum(
            resource_id, actor_id=principal.subject_id
        ),
    )
    return CurriculumVersionResponse.model_validate(model)


async def _write[ModelT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[ModelT]],
) -> ModelT:
    try:
        return await operation()
    except ConfigurationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "configuration_not_found", "resource_type": error.resource_type},
        ) from error
    except ConfigurationInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "configuration_inactive", "resource_type": error.resource_type},
        ) from error
    except ConfigurationInUseError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "configuration_in_use", "resource_type": error.resource_type},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "configuration_conflict"}
        ) from error
