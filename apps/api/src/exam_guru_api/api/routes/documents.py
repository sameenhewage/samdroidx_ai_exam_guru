import re
import unicodedata
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import (
    get_database_session,
    get_extraction_dispatcher,
    get_object_storage,
    get_settings,
)
from exam_guru_api.api.schemas import (
    RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
    ApiErrorResponse,
)
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.documents.domain import SourceDocumentType, UploadValidationError
from exam_guru_api.documents.extraction import (
    InvalidExtractionTransitionError,
    PyMuPdfExtractor,
)
from exam_guru_api.documents.extraction_service import (
    ConcurrentReviewVersionError,
    DocumentExtractionService,
    ExtractedBlockNotFoundError,
    ExtractionDocumentNotFoundError,
    ReviewNotActiveError,
    SourcePageNotFoundError,
)
from exam_guru_api.documents.jobs import ExtractionDispatcher
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import (
    ExtractedBlockResponse,
    ExtractionJobResponse,
    MaterialGradeSummaryResponse,
    MaterialListItemResponse,
    MaterialRemoveRequest,
    MaterialRestoreRequest,
    MaterialScopeCorrectionRequest,
    ReviewedTextUpdate,
    SourceDocumentResponse,
    SourcePageResponse,
)
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    InvalidMaterialRemovalReasonError,
    MaterialScopeImmutableError,
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentContentUnavailableError,
    SourceDocumentNotFoundError,
    SourceDocumentService,
    SourceLearningScopeInactiveError,
    SourceLearningScopeMismatchError,
    SourceLearningScopeNotFoundError,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

router = APIRouter()
ExtractionTriggerPrincipal = Annotated[
    Principal,
    Depends(
        require_rate_limit(
            Permission.EXTRACTION_TRIGGER,
            RateLimitScope.EXTRACTION_TRIGGER,
        )
    ),
]
SourceUploadPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.SOURCE_WRITE, RateLimitScope.SOURCE_UPLOAD)),
]
_MAX_CONTENT_FILENAME_CHARACTERS = 180
_MAX_ENCODED_CONTENT_FILENAME_CHARACTERS = 700
_UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._ -]+")


def _content_disposition(filename: str) -> str:
    normalized = unicodedata.normalize("NFC", filename)[:_MAX_CONTENT_FILENAME_CHARACTERS]
    cleaned = "".join(
        character if character.isprintable() and character not in {"/", "\\", '"', "'"} else "_"
        for character in normalized
    ).strip(" .")
    unicode_stem = cleaned[:-4] if cleaned.casefold().endswith(".pdf") else cleaned
    bounded_stem = ""
    for character in unicode_stem:
        candidate = f"{bounded_stem}{character}.pdf"
        if len(quote(candidate, safe="!#$&+-.^_`|~")) > _MAX_ENCODED_CONTENT_FILENAME_CHARACTERS:
            break
        bounded_stem += character
    bounded_stem = bounded_stem.strip(" .")
    safe_unicode = f"{bounded_stem or 'source'}.pdf"
    ascii_stem = unicodedata.normalize("NFKD", bounded_stem).encode("ascii", "ignore").decode()
    ascii_stem = _UNSAFE_FILENAME_CHARACTERS.sub("_", ascii_stem).strip(" .")
    ascii_value = f"{ascii_stem or 'source'}.pdf"
    encoded = quote(safe_unicode, safe="!#$&+-.^_`|~")
    return f"inline; filename=\"{ascii_value}\"; filename*=UTF-8''{encoded}"


@router.get(
    "/materials/grade-summary",
    operation_id="list_material_grade_summary",
    response_model=list[MaterialGradeSummaryResponse],
)
async def list_material_grade_summary(
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[MaterialGradeSummaryResponse]:
    del principal
    service = SourceDocumentService(
        session,
        object_storage,
        max_upload_bytes=settings.max_upload_bytes,
    )
    return list(await service.grade_summary())


@router.get(
    "/materials",
    operation_id="list_materials",
    response_model=list[MaterialListItemResponse],
)
async def list_materials(
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
    grade: Annotated[int | None, Query(ge=1, le=13)] = None,
    subject_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[MaterialListItemResponse]:
    del principal
    service = SourceDocumentService(
        session,
        object_storage,
        max_upload_bytes=settings.max_upload_bytes,
    )
    return list(
        await service.list_materials(
            grade=grade,
            subject_id=subject_id,
            limit=limit,
            offset=offset,
        )
    )


@router.post(
    "/materials/{document_id}/remove-from-use",
    operation_id="remove_material_from_use",
    response_model=SourceDocumentResponse,
)
async def remove_material_from_use(
    document_id: UUID,
    request: MaterialRemoveRequest,
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SourceDocumentResponse:
    try:
        document = await SourceDocumentService(
            session,
            object_storage,
            max_upload_bytes=settings.max_upload_bytes,
        ).remove_from_ai_use(
            document_id,
            reason=request.reason,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        )
    except (
        ConcurrentMaterialScopeVersionError,
        InvalidMaterialRemovalReasonError,
        SourceDocumentNotFoundError,
    ) as error:
        raise _material_http_exception(error) from error
    return await _source_document_response(session, document)


@router.post(
    "/materials/{document_id}/restore",
    operation_id="restore_material_to_use",
    response_model=SourceDocumentResponse,
)
async def restore_material_to_use(
    document_id: UUID,
    request: MaterialRestoreRequest,
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SourceDocumentResponse:
    try:
        document = await SourceDocumentService(
            session,
            object_storage,
            max_upload_bytes=settings.max_upload_bytes,
        ).restore_to_ai_use(
            document_id,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        )
    except (
        ConcurrentMaterialScopeVersionError,
        SourceCurriculumInactiveError,
        SourceCurriculumNotFoundError,
        SourceDocumentNotFoundError,
        SourceLearningScopeInactiveError,
        SourceLearningScopeMismatchError,
        SourceLearningScopeNotFoundError,
    ) as error:
        raise _material_http_exception(error) from error
    return await _source_document_response(session, document)


@router.patch(
    "/materials/{document_id}/scope",
    operation_id="correct_material_scope",
    response_model=SourceDocumentResponse,
)
async def correct_material_scope(
    document_id: UUID,
    request: MaterialScopeCorrectionRequest,
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SourceDocumentResponse:
    try:
        document = await SourceDocumentService(
            session,
            object_storage,
            max_upload_bytes=settings.max_upload_bytes,
        ).correct_scope(
            document_id,
            curriculum_version_id=request.curriculum_version_id,
            unit_id=request.unit_id,
            lesson_id=request.lesson_id,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        )
    except (
        ConcurrentMaterialScopeVersionError,
        MaterialScopeImmutableError,
        SourceCurriculumInactiveError,
        SourceCurriculumNotFoundError,
        SourceDocumentNotFoundError,
        SourceLearningScopeInactiveError,
        SourceLearningScopeMismatchError,
        SourceLearningScopeNotFoundError,
    ) as error:
        raise _material_http_exception(error) from error
    return await _source_document_response(session, document)


@router.get(
    "/source-documents",
    operation_id="list_source_documents",
    response_model=list[SourceDocumentResponse],
)
async def list_source_documents(
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.SOURCE_READ)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[SourceDocumentResponse]:
    del principal
    documents = await SourceDocumentService(
        session,
        object_storage,
        max_upload_bytes=settings.max_upload_bytes,
    ).list_documents()
    return [await _source_document_response(session, document) for document in documents]


@router.get(
    "/source-documents/{document_id}/content",
    operation_id="get_source_document_content",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "description": "Verified immutable original PDF",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Authentication required",
            "model": ApiErrorResponse,
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "Source read permission required",
            "model": ApiErrorResponse,
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Source document not found",
            "model": ApiErrorResponse,
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Source document content unavailable or corrupt",
            "model": ApiErrorResponse,
        },
    },
)
async def get_source_document_content(
    document_id: UUID,
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    del principal
    try:
        content = await SourceDocumentService(
            session,
            object_storage,
            max_upload_bytes=settings.max_upload_bytes,
        ).read_original(document_id)
    except SourceDocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "source_document_not_found"},
        ) from None
    except SourceDocumentContentUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "source_document_content_unavailable"},
        ) from None
    return Response(
        content=content.data,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(content.filename),
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; sandbox",
            "Cross-Origin-Resource-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.post(
    "/source-documents/{document_id}/extract",
    operation_id="trigger_source_document_extraction",
    response_model=ExtractionJobResponse,
    responses={
        status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Extraction queue or authenticated cost limiter unavailable",
            "model": ApiErrorResponse,
        },
    },
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_source_document_extraction(
    document_id: UUID,
    principal: ExtractionTriggerPrincipal,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    dispatcher: Annotated[ExtractionDispatcher, Depends(get_extraction_dispatcher)],
) -> ExtractionJobResponse:
    service = _extraction_service(session, object_storage)
    try:
        result = await service.queue_extraction(document_id, actor_id=principal.subject_id)
    except (ExtractionDocumentNotFoundError, InvalidExtractionTransitionError) as error:
        raise _extraction_http_exception(error) from error
    if result.queue_message_id is not None:
        return ExtractionJobResponse(
            document_id=document_id,
            message_id=result.queue_message_id,
            status=result.status,
        )
    try:
        dispatched_message_id = dispatcher.dispatch(
            document_id,
            actor_id=principal.subject_id,
        )
        attached = await service.attach_queue_message(
            document_id,
            dispatched_message_id,
            actor_id=principal.subject_id,
        )
    except Exception as error:
        await service.record_queue_dispatch_failure(
            document_id,
            actor_id=principal.subject_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "extraction_queue_unavailable"},
        ) from error
    return ExtractionJobResponse(
        document_id=document_id,
        message_id=attached.queue_message_id or dispatched_message_id,
        status=attached.status,
    )


@router.get(
    "/source-documents/{document_id}/pages",
    operation_id="list_source_document_pages",
    response_model=list[SourcePageResponse],
)
async def list_source_document_pages(
    document_id: UUID,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.SOURCE_READ)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> list[SourcePageResponse]:
    del principal
    try:
        pages = await _extraction_service(session, object_storage).list_pages(document_id)
    except ExtractionDocumentNotFoundError as error:
        raise _extraction_http_exception(error) from error
    return [SourcePageResponse.model_validate(page) for page in pages]


@router.get(
    "/source-documents/{document_id}/pages/{page_number}/blocks",
    operation_id="list_source_page_blocks",
    response_model=list[ExtractedBlockResponse],
)
async def list_source_page_blocks(
    document_id: UUID,
    page_number: int,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.SOURCE_READ)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> list[ExtractedBlockResponse]:
    del principal
    try:
        blocks = await _extraction_service(session, object_storage).list_blocks(
            document_id,
            page_number=page_number,
        )
    except ExtractionDocumentNotFoundError as error:
        raise _extraction_http_exception(error) from error
    return [ExtractedBlockResponse.model_validate(block) for block in blocks]


@router.post(
    "/source-documents/{document_id}/review",
    operation_id="begin_source_document_review",
    response_model=SourceDocumentResponse,
)
async def begin_source_document_review(
    document_id: UUID,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.CONTENT_REVIEW)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SourceDocumentResponse:
    service = _extraction_service(session, object_storage)
    try:
        result = await service.begin_review(document_id, actor_id=principal.subject_id)
    except (ExtractionDocumentNotFoundError, InvalidExtractionTransitionError) as error:
        raise _extraction_http_exception(error) from error
    document = await session.get(SourceDocumentModel, result.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "source_document_not_found"})
    return await _source_document_response(
        session,
        document,
        deduplicated=result.deduplicated,
    )


@router.patch(
    "/source-documents/{document_id}/pages/{page_number}",
    operation_id="correct_source_document_page",
    response_model=SourcePageResponse,
)
async def correct_source_document_page(
    document_id: UUID,
    page_number: int,
    request: ReviewedTextUpdate,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.CONTENT_REVIEW)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SourcePageResponse:
    try:
        page = await _extraction_service(session, object_storage).correct_page(
            document_id,
            page_number=page_number,
            reviewed_text=request.reviewed_text,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        )
    except (
        ConcurrentReviewVersionError,
        ExtractionDocumentNotFoundError,
        ReviewNotActiveError,
        SourcePageNotFoundError,
    ) as error:
        raise _extraction_http_exception(error) from error
    return SourcePageResponse.model_validate(page)


@router.patch(
    "/source-documents/{document_id}/pages/{page_number}/blocks/{reading_order}",
    operation_id="correct_source_document_block",
    response_model=ExtractedBlockResponse,
)
async def correct_source_document_block(
    document_id: UUID,
    page_number: int,
    reading_order: int,
    request: ReviewedTextUpdate,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.CONTENT_REVIEW)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ExtractedBlockResponse:
    try:
        block = await _extraction_service(session, object_storage).correct_block(
            document_id,
            page_number=page_number,
            reading_order=reading_order,
            reviewed_text=request.reviewed_text,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        )
    except (
        ConcurrentReviewVersionError,
        ExtractedBlockNotFoundError,
        ExtractionDocumentNotFoundError,
        ReviewNotActiveError,
    ) as error:
        raise _extraction_http_exception(error) from error
    return ExtractedBlockResponse.model_validate(block)


@router.post(
    "/source-documents/{document_id}/trust",
    operation_id="trust_source_document",
    response_model=SourceDocumentResponse,
)
async def trust_source_document(
    document_id: UUID,
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.SOURCE_TRUST)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> SourceDocumentResponse:
    service = _extraction_service(session, object_storage)
    try:
        result = await service.trust_document(document_id, actor_id=principal.subject_id)
    except (ExtractionDocumentNotFoundError, InvalidExtractionTransitionError) as error:
        raise _extraction_http_exception(error) from error
    document = await session.get(SourceDocumentModel, result.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "source_document_not_found"})
    return await _source_document_response(
        session,
        document,
        deduplicated=result.deduplicated,
    )


def _extraction_service(
    session: AsyncSession,
    object_storage: ObjectStorage,
) -> DocumentExtractionService:
    return DocumentExtractionService(
        session,
        object_storage,
        PyMuPdfExtractor(max_pages=1_000),
    )


def _extraction_http_exception(error: Exception) -> HTTPException:
    if isinstance(
        error,
        (
            ExtractionDocumentNotFoundError,
            SourcePageNotFoundError,
            ExtractedBlockNotFoundError,
        ),
    ):
        return HTTPException(status_code=404, detail={"code": "extraction_resource_not_found"})
    if isinstance(error, ConcurrentReviewVersionError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "concurrent_review_modification",
                "expected_version": error.expected,
                "actual_version": error.actual,
            },
        )
    return HTTPException(status_code=409, detail={"code": "invalid_extraction_transition"})


@router.post(
    "/source-documents",
    operation_id="upload_source_document",
    response_model=SourceDocumentResponse,
    responses={
        status.HTTP_200_OK: {"description": "Existing checksum returned idempotently"},
        status.HTTP_201_CREATED: {"description": "Source document created"},
        status.HTTP_429_TOO_MANY_REQUESTS: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Upload validation failed"},
        status.HTTP_503_SERVICE_UNAVAILABLE: RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE,
    },
    status_code=status.HTTP_201_CREATED,
)
async def upload_source_document(
    response: Response,
    file: Annotated[UploadFile, File()],
    document_type: Annotated[SourceDocumentType, Form()],
    principal: SourceUploadPrincipal,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
    curriculum_version_id: Annotated[UUID | None, Form()] = None,
    unit_id: Annotated[UUID | None, Form()] = None,
    lesson_id: Annotated[UUID | None, Form()] = None,
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
            unit_id=unit_id,
            lesson_id=lesson_id,
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
    except SourceLearningScopeNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "learning_scope_not_found"},
        ) from error
    except SourceLearningScopeInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "learning_scope_inactive"},
        ) from error
    except SourceLearningScopeMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "learning_scope_mismatch"},
        ) from error

    response.status_code = status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED
    return await _source_document_response(
        session,
        result.document,
        deduplicated=result.deduplicated,
        likely_metadata_duplicate_of_id=result.likely_metadata_duplicate_of_id,
    )


async def _source_document_response(
    session: AsyncSession,
    document: SourceDocumentModel,
    *,
    deduplicated: bool = False,
    likely_metadata_duplicate_of_id: UUID | None = None,
) -> SourceDocumentResponse:
    if document.active_for_ai is None:
        document.active_for_ai = True
    if document.metadata_scope_version is None:
        document.metadata_scope_version = 0
    subject_id: UUID | None = None
    if document.curriculum_version_id is not None:
        curriculum = await session.get(CurriculumVersionModel, document.curriculum_version_id)
        if curriculum is not None:
            subject_id = curriculum.subject_id
    return SourceDocumentResponse.model_validate(document).model_copy(
        update={
            "subject_id": subject_id,
            "deduplicated": deduplicated,
            "likely_metadata_duplicate_of_id": likely_metadata_duplicate_of_id,
        }
    )


def _material_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, SourceDocumentNotFoundError):
        return HTTPException(status_code=404, detail={"code": "source_document_not_found"})
    if isinstance(error, SourceCurriculumNotFoundError | SourceLearningScopeNotFoundError):
        return HTTPException(status_code=404, detail={"code": "material_scope_not_found"})
    if isinstance(error, ConcurrentMaterialScopeVersionError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "concurrent_material_scope_modification",
                "expected_version": error.expected,
                "actual_version": error.actual,
            },
        )
    if isinstance(error, MaterialScopeImmutableError):
        return HTTPException(
            status_code=409,
            detail={"code": "trusted_material_scope_immutable_remove_from_use"},
        )
    if isinstance(error, SourceCurriculumInactiveError | SourceLearningScopeInactiveError):
        return HTTPException(status_code=409, detail={"code": "material_scope_inactive"})
    if isinstance(error, SourceLearningScopeMismatchError):
        return HTTPException(status_code=422, detail={"code": "material_scope_mismatch"})
    return HTTPException(status_code=422, detail={"code": "invalid_removal_reason"})
