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
    CurriculumLessonCreate,
    CurriculumLessonResponse,
    CurriculumLessonTaxonomyUpdate,
    CurriculumLessonUpdate,
    CurriculumUnitCreate,
    CurriculumUnitResponse,
    CurriculumUnitUpdate,
    CurriculumVersionCreate,
    CurriculumVersionResponse,
    CurriculumVersionUpdate,
    ExamConfigurationCreate,
    ExamConfigurationResponse,
    ExamConfigurationUpdate,
    MediumCreate,
    MediumResponse,
    MediumUpdate,
    SubjectCreate,
    SubjectResponse,
    SubjectUpdate,
)
from exam_guru_api.curriculum.configuration_service import (
    ConfigurationInactiveError,
    ConfigurationInUseError,
    ConfigurationNotFoundError,
    ConfigurationScopeMismatchError,
    ConfigurationService,
)
from exam_guru_api.curriculum.domain import LEGACY_UNCLASSIFIED_SUBJECT_ID
from exam_guru_api.curriculum.models import CurriculumVersionModel

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


@router.get("/subjects", operation_id="list_subjects", response_model=list[SubjectResponse])
async def list_subjects(
    principal: Annotated[Principal, read_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[SubjectResponse]:
    del principal
    return [
        SubjectResponse.model_validate(model)
        for model in await ConfigurationService(session).list_subjects()
    ]


@router.post(
    "/subjects",
    operation_id="create_subject",
    response_model=SubjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_subject(
    request: SubjectCreate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SubjectResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).create_subject(
            request,
            actor_id=principal.subject_id,
        ),
    )
    return SubjectResponse.model_validate(model)


@router.patch(
    "/subjects/{resource_id}",
    operation_id="update_subject",
    response_model=SubjectResponse,
)
async def update_subject(
    resource_id: UUID,
    request: SubjectUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SubjectResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).update_subject(
            resource_id, request.name, actor_id=principal.subject_id
        ),
    )
    return SubjectResponse.model_validate(model)


@router.post(
    "/subjects/{resource_id}/deactivate",
    operation_id="deactivate_subject",
    response_model=SubjectResponse,
)
async def deactivate_subject(
    resource_id: UUID,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> SubjectResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).deactivate_subject(
            resource_id, actor_id=principal.subject_id
        ),
    )
    return SubjectResponse.model_validate(model)


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
        _curriculum_response(model)
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
    return _curriculum_response(model)


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
    return _curriculum_response(model)


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
    return _curriculum_response(model)


@router.get(
    "/curriculum-versions/{curriculum_version_id}/units",
    operation_id="list_curriculum_units",
    response_model=list[CurriculumUnitResponse],
)
async def list_curriculum_units(
    curriculum_version_id: UUID,
    principal: Annotated[Principal, read_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[CurriculumUnitResponse]:
    del principal
    models = await _write(
        session,
        lambda: ConfigurationService(session).list_units(curriculum_version_id),
    )
    return [CurriculumUnitResponse.model_validate(model) for model in models]


@router.post(
    "/curriculum-versions/{curriculum_version_id}/units",
    operation_id="create_curriculum_unit",
    response_model=CurriculumUnitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_curriculum_unit(
    curriculum_version_id: UUID,
    request: CurriculumUnitCreate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumUnitResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).create_unit(
            curriculum_version_id,
            request,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumUnitResponse.model_validate(model)


@router.patch(
    "/curriculum-versions/{curriculum_version_id}/units/{unit_id}",
    operation_id="update_curriculum_unit",
    response_model=CurriculumUnitResponse,
)
async def update_curriculum_unit(
    curriculum_version_id: UUID,
    unit_id: UUID,
    request: CurriculumUnitUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumUnitResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).update_unit(
            curriculum_version_id,
            unit_id,
            request.title,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumUnitResponse.model_validate(model)


@router.post(
    "/curriculum-versions/{curriculum_version_id}/units/{unit_id}/deactivate",
    operation_id="deactivate_curriculum_unit",
    response_model=CurriculumUnitResponse,
)
async def deactivate_curriculum_unit(
    curriculum_version_id: UUID,
    unit_id: UUID,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumUnitResponse:
    model = await _write(
        session,
        lambda: ConfigurationService(session).deactivate_unit(
            curriculum_version_id,
            unit_id,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumUnitResponse.model_validate(model)


@router.get(
    "/curriculum-versions/{curriculum_version_id}/lessons",
    operation_id="list_curriculum_lessons",
    response_model=list[CurriculumLessonResponse],
)
async def list_curriculum_lessons(
    curriculum_version_id: UUID,
    principal: Annotated[Principal, read_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> list[CurriculumLessonResponse]:
    del principal
    records = await _write(
        session,
        lambda: ConfigurationService(session).list_lessons(curriculum_version_id),
    )
    return [CurriculumLessonResponse.model_validate(record) for record in records]


@router.post(
    "/curriculum-versions/{curriculum_version_id}/lessons",
    operation_id="create_curriculum_lesson",
    response_model=CurriculumLessonResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_curriculum_lesson(
    curriculum_version_id: UUID,
    request: CurriculumLessonCreate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumLessonResponse:
    record = await _write(
        session,
        lambda: ConfigurationService(session).create_lesson(
            curriculum_version_id,
            request,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumLessonResponse.model_validate(record)


@router.patch(
    "/curriculum-versions/{curriculum_version_id}/lessons/{lesson_id}",
    operation_id="update_curriculum_lesson",
    response_model=CurriculumLessonResponse,
)
async def update_curriculum_lesson(
    curriculum_version_id: UUID,
    lesson_id: UUID,
    request: CurriculumLessonUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumLessonResponse:
    record = await _write(
        session,
        lambda: ConfigurationService(session).update_lesson(
            curriculum_version_id,
            lesson_id,
            request.title,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumLessonResponse.model_validate(record)


@router.put(
    "/curriculum-versions/{curriculum_version_id}/lessons/{lesson_id}/taxonomy",
    operation_id="replace_curriculum_lesson_taxonomy",
    response_model=CurriculumLessonResponse,
)
async def replace_curriculum_lesson_taxonomy(
    curriculum_version_id: UUID,
    lesson_id: UUID,
    request: CurriculumLessonTaxonomyUpdate,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumLessonResponse:
    record = await _write(
        session,
        lambda: ConfigurationService(session).replace_lesson_taxonomy(
            curriculum_version_id,
            lesson_id,
            request.taxonomy_node_ids,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumLessonResponse.model_validate(record)


@router.post(
    "/curriculum-versions/{curriculum_version_id}/lessons/{lesson_id}/deactivate",
    operation_id="deactivate_curriculum_lesson",
    response_model=CurriculumLessonResponse,
)
async def deactivate_curriculum_lesson(
    curriculum_version_id: UUID,
    lesson_id: UUID,
    principal: Annotated[Principal, write_principal()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CurriculumLessonResponse:
    record = await _write(
        session,
        lambda: ConfigurationService(session).deactivate_lesson(
            curriculum_version_id,
            lesson_id,
            actor_id=principal.subject_id,
        ),
    )
    return CurriculumLessonResponse.model_validate(record)


def _curriculum_response(model: CurriculumVersionModel) -> CurriculumVersionResponse:
    if model.subject_id is None:
        model.subject_id = LEGACY_UNCLASSIFIED_SUBJECT_ID
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
    except ConfigurationScopeMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "configuration_scope_mismatch", "resource_type": error.resource_type},
        ) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "configuration_conflict"}
        ) from error
