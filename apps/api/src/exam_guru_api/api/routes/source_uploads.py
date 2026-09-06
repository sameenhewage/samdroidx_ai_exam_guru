from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from exam_guru_api.api.dependencies import get_database_session, get_object_storage
from exam_guru_api.api.schemas import (
    RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    ApiErrorResponse,
)
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.core.config import Settings, StorageBackend
from exam_guru_api.documents.resumable_uploads import (
    ResumableUploadError,
    ResumableUploadService,
    UploadLimits,
)
from exam_guru_api.documents.upload_jobs import SourceUploadDispatcher, dispatch_source_upload
from exam_guru_api.documents.upload_schemas import (
    MAX_UPLOAD_INTEGER,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_RECEIPT_PAGE_SIZE,
    SourceUploadChunkPageResponse,
    SourceUploadCompleteRequest,
    SourceUploadCreateRequest,
    SourceUploadResponse,
    UploadStatus,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage, S3ObjectStorage
from exam_guru_api.infrastructure.private_artifacts import PrivateUploadArtifacts

router = APIRouter()
UploadPrincipal = Annotated[
    Principal, Depends(require_rate_limit(Permission.SOURCE_WRITE, RateLimitScope.SOURCE_UPLOAD))
]
UploadReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))]
_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ApiErrorResponse, "description": "Upload not found for this owner"},
    409: {
        "model": ApiErrorResponse,
        "description": "Upload request identity, offset, version, state, or quota conflict",
    },
    413: {
        "model": ApiErrorResponse,
        "description": "Configured size or bounded chunk limit exceeded",
    },
    422: {"model": ApiErrorResponse, "description": "Upload validation failed"},
    429: RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE,
    503: {
        "model": ApiErrorResponse,
        "description": "Resumable storage, dispatch, or authenticated cost limiter unavailable",
    },
}


def get_resumable_upload_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> ResumableUploadService:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(storage, S3ObjectStorage) or (
        isinstance(settings, Settings) and settings.storage_backend is not StorageBackend.LOCAL
    ):
        raise HTTPException(status_code=503, detail={"code": "source_upload_storage_unsupported"})
    limits = getattr(request.app.state, "source_upload_limits", None)
    artifacts = getattr(request.app.state, "source_upload_artifacts", None)
    if not isinstance(limits, UploadLimits) or not isinstance(artifacts, PrivateUploadArtifacts):
        raise HTTPException(status_code=503, detail={"code": "source_upload_unavailable"})
    return ResumableUploadService(session, storage, artifacts, limits=limits)


UploadService = Annotated[ResumableUploadService, Depends(get_resumable_upload_service)]


def get_source_upload_dispatcher(request: Request) -> SourceUploadDispatcher:
    dispatcher = getattr(request.app.state, "source_upload_dispatcher", None)
    if not callable(getattr(dispatcher, "dispatch", None)):
        raise HTTPException(status_code=503, detail={"code": "source_upload_unavailable"})
    return cast(SourceUploadDispatcher, dispatcher)


UploadDispatcher = Annotated[SourceUploadDispatcher, Depends(get_source_upload_dispatcher)]


def _failure(error: ResumableUploadError) -> HTTPException:
    detail: dict[str, object] = {"code": error.code}
    if error.next_offset is not None:
        detail["next_offset"] = error.next_offset
    return HTTPException(status_code=error.status_code, detail=detail)


def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/source-uploads",
    operation_id="create_source_upload",
    response_model=SourceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
async def create_source_upload(
    body: SourceUploadCreateRequest,
    request: Request,
    response: Response,
    principal: UploadPrincipal,
    service: UploadService,
) -> SourceUploadResponse:
    try:
        result = await service.create(body, principal=principal)
    except ResumableUploadError as error:
        raise _failure(error) from None
    response.headers["Location"] = f"{request.url.path.rstrip('/')}/{result.id}"
    _private_response(response)
    return result


@router.get(
    "/source-uploads/by-request/{request_id}",
    operation_id="get_source_upload_by_request",
    response_model=SourceUploadResponse,
    responses=_ERRORS,
)
async def get_source_upload_by_request(
    request_id: UUID,
    response: Response,
    principal: UploadReadPrincipal,
    service: UploadService,
) -> SourceUploadResponse:
    try:
        result = await service.get_by_request(request_id, principal=principal)
    except ResumableUploadError as error:
        raise _failure(error) from None
    _private_response(response)
    return result


@router.get(
    "/source-uploads/{upload_id}",
    operation_id="get_source_upload",
    response_model=SourceUploadResponse,
    responses=_ERRORS,
)
async def get_source_upload(
    upload_id: UUID,
    response: Response,
    principal: UploadReadPrincipal,
    service: UploadService,
) -> SourceUploadResponse:
    try:
        result = await service.get(upload_id, principal=principal)
    except ResumableUploadError as error:
        raise _failure(error) from None
    _private_response(response)
    return result


@router.get(
    "/source-uploads/{upload_id}/chunks",
    operation_id="list_source_upload_chunks",
    response_model=SourceUploadChunkPageResponse,
    responses=_ERRORS,
)
async def list_source_upload_chunks(
    upload_id: UUID,
    response: Response,
    principal: UploadReadPrincipal,
    service: UploadService,
    offset: Annotated[int, Query(ge=0, le=MAX_UPLOAD_INTEGER)] = 0,
    limit: Annotated[int, Query(ge=1, le=UPLOAD_RECEIPT_PAGE_SIZE)] = UPLOAD_RECEIPT_PAGE_SIZE,
) -> SourceUploadChunkPageResponse:
    try:
        result = await service.list_chunks(
            upload_id, principal=principal, offset=offset, limit=limit
        )
    except ResumableUploadError as error:
        raise _failure(error) from None
    _private_response(response)
    return result


async def _read_chunk(request: Request) -> bytes:
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise ResumableUploadError("unsupported_chunk_media_type", 415)
    raw_length = request.headers.get("Content-Length")
    length = None
    if raw_length is not None:
        if not raw_length.isascii() or not raw_length.isdecimal() or len(raw_length) > 20:
            raise ResumableUploadError("invalid_chunk_content_length", 422)
        length = int(raw_length)
        if length > UPLOAD_CHUNK_BYTES:
            raise ResumableUploadError("source_upload_chunk_too_large", 413)
    data = bytearray()
    try:
        async for piece in request.stream():
            if len(piece) > UPLOAD_CHUNK_BYTES - len(data):
                raise ResumableUploadError("source_upload_chunk_too_large", 413)
            if length is not None and len(data) + len(piece) > length:
                raise ResumableUploadError("source_upload_chunk_length_mismatch", 422)
            data.extend(piece)
    except ClientDisconnect:
        raise ResumableUploadError("source_upload_chunk_truncated", 422) from None
    if not data or (length is not None and len(data) != length):
        raise ResumableUploadError("source_upload_chunk_length_mismatch", 422)
    return bytes(data)


@router.put(
    "/source-uploads/{upload_id}/chunks",
    operation_id="append_source_upload_chunk",
    response_model=SourceUploadResponse,
    responses={
        **_ERRORS,
        415: {"model": ApiErrorResponse, "description": "Raw octet stream required"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                        "maxLength": UPLOAD_CHUNK_BYTES,
                    }
                }
            },
        }
    },
)
async def append_source_upload_chunk(
    upload_id: UUID,
    request: Request,
    response: Response,
    principal: UploadPrincipal,
    service: UploadService,
    offset: Annotated[int, Query(ge=0, le=MAX_UPLOAD_INTEGER)],
    checksum_sha256: Annotated[
        str | None, Header(alias="X-Chunk-SHA256", pattern=r"^[0-9a-f]{64}$")
    ] = None,
) -> SourceUploadResponse:
    try:
        await service.get(upload_id, principal=principal)
        data = await _read_chunk(request)
        result = await service.append_chunk(
            upload_id,
            principal=principal,
            offset=offset,
            data=data,
            checksum_sha256=checksum_sha256,
        )
    except ResumableUploadError as error:
        raise _failure(error) from None
    _private_response(response)
    return result


@router.post(
    "/source-uploads/{upload_id}/complete",
    operation_id="complete_source_upload",
    response_model=SourceUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **_ERRORS,
        200: {
            "model": SourceUploadResponse,
            "description": "Existing terminal upload returned idempotently",
        },
    },
)
async def complete_source_upload(
    upload_id: UUID,
    request: Request,
    response: Response,
    principal: UploadPrincipal,
    service: UploadService,
    dispatcher: UploadDispatcher,
    body: SourceUploadCompleteRequest | None = None,
) -> SourceUploadResponse:
    try:
        result = await service.request_completion(
            upload_id,
            principal=principal,
            expected_version=None if body is None else body.expected_version,
        )
    except ResumableUploadError as error:
        raise _failure(error) from None
    if result.status is UploadStatus.PENDING:
        await dispatch_source_upload(result.id, dispatcher)
    if result.status in {UploadStatus.COMPLETED, UploadStatus.FAILED}:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = request.url.path.removesuffix("/complete")
    _private_response(response)
    return result
