from collections.abc import Callable, Coroutine
from contextlib import ExitStack
from typing import Annotated, Any
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from exam_guru_api.api.dependencies import get_database_session, get_object_storage, get_settings
from exam_guru_api.api.routes.documents import _content_disposition
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.page_images import (
    MAX_SOURCE_PAGE_NUMBER,
    PageImageError,
    SourceImageIdentity,
    SourceImageStorage,
    create_page_image_artifacts,
    get_material_page_image,
    iter_original,
    load_material_source,
    open_verified_original,
    parse_single_range,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; sandbox",
}


def _http_error(error: PageImageError, *, size: int | None = None) -> HTTPException:
    headers = dict(_PRIVATE_HEADERS)
    if error.status_code == 416 and size is not None:
        headers["Content-Range"] = f"bytes */{size}"
    return HTTPException(error.status_code, detail={"code": error.code}, headers=headers)


class _PrivateRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except StarletteHTTPException as error:
                raise HTTPException(
                    error.status_code,
                    detail=error.detail,
                    headers={**(error.headers or {}), **_PRIVATE_HEADERS},
                ) from None
            except RequestValidationError:
                raise HTTPException(
                    422, detail={"code": "invalid_source_image_request"}, headers=_PRIVATE_HEADERS
                ) from None

        return handler


router = APIRouter(
    route_class=_PrivateRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 416, 422, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]
PageNumber = Annotated[int, Path(ge=1, le=MAX_SOURCE_PAGE_NUMBER)]


class OriginalPDFResponse(Response):
    def __init__(
        self,
        source: SourceImageIdentity,
        storage: SourceImageStorage,
        *,
        head: bool,
        range_header: str | None,
    ) -> None:
        super().__init__(media_type="application/pdf")
        self._source = source
        self._storage = storage
        self._head = head
        self._range_header = range_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        stack = ExitStack()
        try:
            try:
                with anyio.CancelScope(shield=True):
                    stream = await anyio.to_thread.run_sync(
                        lambda: stack.enter_context(
                            open_verified_original(self._storage, self._source)
                        )
                    )
                selected_range = parse_single_range(
                    None if self._head else self._range_header, self._source.size_bytes
                )
                start, end = selected_range or (0, self._source.size_bytes - 1)
                chunks = (
                    None
                    if self._head
                    else await anyio.to_thread.run_sync(
                        lambda: iter_original(stream, start=start, length=end - start + 1)
                    )
                )
            except PageImageError as error:
                raise _http_error(error, size=self._source.size_bytes) from None
            headers = {
                **_PRIVATE_HEADERS,
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
                "Content-Disposition": _content_disposition(self._source.filename),
            }
            if selected_range is not None:
                headers["Content-Range"] = f"bytes {start}-{end}/{self._source.size_bytes}"
            response: Response
            if chunks is None:
                response = Response(media_type="application/pdf", headers=headers)
            else:
                response = StreamingResponse(
                    chunks,
                    status_code=206 if selected_range is not None else 200,
                    media_type="application/pdf",
                    headers=headers,
                )
            await response(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(stack.close)


@router.get(
    "/materials/{document_id}/pages/{page_number}/image",
    operation_id="get_material_page_image",
    response_class=Response,
    responses={
        200: {
            "description": "Verified page image or bounded render of the immutable original",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
@router.head("/materials/{document_id}/pages/{page_number}/image", include_in_schema=False)
async def material_page_image(
    document_id: UUID,
    page_number: PageNumber,
    request: Request,
    principal: ReadPrincipal,
    session: DatabaseSession,
    object_storage: Storage,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    try:
        content = await get_material_page_image(
            session,
            document_id,
            page_number,
            principal=principal,
            storage=object_storage,
            artifacts=create_page_image_artifacts(settings),
        )
    except PageImageError as error:
        raise _http_error(error) from None
    return Response(
        content=b"" if request.method == "HEAD" else content.data,
        media_type="image/png",
        headers={
            **_PRIVATE_HEADERS,
            "Content-Length": str(len(content.data)),
            "X-Source-Image-Origin": content.origin,
        },
    )


@router.get(
    "/materials/{document_id}/original",
    operation_id="get_material_original",
    response_class=Response,
    responses={
        200: {
            "description": "Verified immutable original PDF, streamed in bounded chunks",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        206: {
            "description": "One validated byte range of the verified original",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
@router.head("/materials/{document_id}/original", include_in_schema=False)
async def material_original(
    document_id: UUID,
    request: Request,
    principal: ReadPrincipal,
    session: DatabaseSession,
    object_storage: Storage,
) -> Response:
    try:
        source = await load_material_source(session, document_id, principal=principal)
    except PageImageError as error:
        raise _http_error(error) from None
    ranges = request.headers.getlist("range")
    range_header = ",".join(ranges) if ranges and "if-range" not in request.headers else None
    return OriginalPDFResponse(
        source, object_storage, head=request.method == "HEAD", range_header=range_header
    )
