from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import (
    get_database_session,
    get_object_storage,
    get_settings,
)
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import SourceDocumentType, UploadValidationError
from exam_guru_api.documents.schemas import SourceDocumentResponse
from exam_guru_api.documents.service import (
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

router = APIRouter()


@router.post(
    "/source-documents",
    operation_id="upload_source_document",
    response_model=SourceDocumentResponse,
    responses={
        status.HTTP_200_OK: {"description": "Existing checksum returned idempotently"},
        status.HTTP_201_CREATED: {"description": "Source document created"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Upload validation failed"},
    },
    status_code=status.HTTP_201_CREATED,
)
async def upload_source_document(
    response: Response,
    file: Annotated[UploadFile, File()],
    document_type: Annotated[SourceDocumentType, Form()],
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.TAXONOMY_WRITE)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
    curriculum_version_id: Annotated[UUID | None, Form()] = None,
    year: Annotated[int | None, Form(ge=1900, le=2100)] = None,
    paper_code: Annotated[str | None, Form(max_length=64)] = None,
) -> SourceDocumentResponse:
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    try:
        result = await SourceDocumentService(
            session,
            object_storage,
            max_upload_bytes=settings.max_upload_bytes,
        ).upload_pdf(
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
            document_type=document_type,
            actor_id=principal.subject_id,
            curriculum_version_id=curriculum_version_id,
            year=year,
            paper_code=paper_code,
        )
    except UploadValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.violation.value},
        ) from error
    except SourceCurriculumNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "curriculum_version_not_found"},
        ) from error
    except SourceCurriculumInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "curriculum_version_inactive"},
        ) from error

    response.status_code = status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED
    return SourceDocumentResponse.model_validate(result.document).model_copy(
        update={"deduplicated": result.deduplicated}
    )
