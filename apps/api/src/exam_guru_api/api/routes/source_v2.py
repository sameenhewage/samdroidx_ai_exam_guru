"""Source V2 review API: original page beside the Machine Candidate, then decide.

Source text is produced once, offline, by the executing agent looking at the
canonical crop. Nothing in here reads a page. These endpoints serve what the
offline pipeline already produced and record the human decision on top of it.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.source_v2 import repository
from exam_guru_api.source_v2.domain import NotVerifiedError, SourceV2Error, StaleReviewError
from exam_guru_api.source_v2.gate import DownstreamPurpose, assert_document_usable
from exam_guru_api.source_v2.repository import PageNotFoundError
from exam_guru_api.source_v2.schemas import (
    ConfirmRequest,
    ConfirmVisualRequest,
    CorrectRequest,
    DocumentGateView,
    ExcludeRequest,
    ImportPageRequest,
    ImportPageResponse,
    PageProgress,
    PageView,
    ReclassifyRequest,
    RegionMutationResponse,
    RegionView,
)

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}

router = APIRouter(
    responses={
        401: {"model": ApiErrorResponse},
        403: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
    }
)


def _private(response: Response) -> None:
    response.headers.update(_PRIVATE_HEADERS)


def _fail(error: Exception) -> HTTPException:
    if isinstance(error, PageNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, StaleReviewError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, NotVerifiedError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


async def _page_view(session: AsyncSession, page_id: UUID) -> PageView:
    page = await repository.get_page(session, page_id)
    regions = await repository.list_regions(session, page_id)
    counts = await repository.progress(session, page_id)
    return PageView(
        page_id=page.page_id,
        document_id=page.document_id,
        page_number=page.page_number,
        image_sha256=page.image_sha256,
        width=page.width,
        height=page.height,
        dpi=page.dpi,
        language=page.language,
        detector_version=page.detector_version,
        progress=PageProgress(**counts),
        regions=[
            RegionView(
                region_id=row.region_id,
                region_type=row.region_type,
                candidate_id=row.candidate_id,
                revision=row.revision,
                origin=row.origin,
                text=row.text,
                abstained=row.abstained,
                reason=row.reason,
                state=row.state,
                bbox=row.bbox,
                verified_text=row.verified_text,
                source_kind=row.source_kind,
                proposed_source_kind=row.proposed_source_kind,
                crop_sha256=row.crop_sha256,
            )
            for row in regions
        ],
    )


async def _region_response(
    session: AsyncSession, page_id: UUID, region_id: str
) -> RegionMutationResponse:
    view = await _page_view(session, page_id)
    region = next((item for item in view.regions if item.region_id == region_id), None)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown region {region_id}")
    return RegionMutationResponse(region=region, progress=view.progress)


@router.post(
    "/source-v2/pages",
    response_model=ImportPageResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ApiErrorResponse}},
)
async def import_page(
    response: Response,
    payload: ImportPageRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))],
    refresh: bool = False,
) -> ImportPageResponse:
    """Hand one page of Source Factory output to the Studio.

    Geometry and proposed readings only. Nothing ingested here is verified;
    a reviewer still has to decide every region against the original page.
    """

    _private(response)
    _ = principal
    try:
        result = await repository.import_page(
            session,
            document_id=payload.document_id,
            page_number=payload.page_number,
            language=payload.language,
            image_sha256=payload.image_sha256,
            width=payload.width,
            height=payload.height,
            dpi=payload.dpi,
            detector_version=payload.detector_version,
            layout=payload.layout,
            candidates=[item.model_dump() for item in payload.candidates],
            refresh=refresh,
        )
        await session.commit()
    except SourceV2Error as error:
        await session.rollback()
        raise _fail(error) from error
    return ImportPageResponse(**result)


@router.get("/source-v2/pages/{page_id}", response_model=PageView)
async def read_page(
    response: Response,
    page_id: Annotated[UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
) -> PageView:
    """Everything a reviewer needs for one page, in one call."""

    _private(response)
    _ = principal
    try:
        return await _page_view(session, page_id)
    except SourceV2Error as error:
        raise _fail(error) from error


@router.get(
    "/source-v2/documents/{document_id}/pages/{page_number}",
    response_model=PageView,
)
async def read_page_by_number(
    response: Response,
    document_id: Annotated[UUID, Path()],
    page_number: Annotated[int, Path(ge=1)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
) -> PageView:
    _private(response)
    _ = principal
    page = await repository.find_page(session, document_id, page_number)
    if page is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"document {document_id} has no Source V2 page {page_number}",
        )
    return await _page_view(session, page.page_id)


@router.get(
    "/source-v2/pages/{page_id}/render",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def read_page_render(
    page_id: Annotated[UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
) -> Response:
    """The original page, checksum-verified against what the agent read."""

    _ = principal
    try:
        page = await repository.get_page(session, page_id)
        payload = repository.rendered_page_bytes(page)
    except SourceV2Error as error:
        raise _fail(error) from error
    return Response(
        content=payload,
        media_type="image/png",
        headers={**_PRIVATE_HEADERS, "X-Source-Image-Sha256": page.image_sha256},
    )


@router.post(
    "/source-v2/pages/{page_id}/regions/{region_id}/confirm",
    response_model=RegionMutationResponse,
    responses={409: {"model": ApiErrorResponse}},
)
async def confirm_region(
    response: Response,
    page_id: Annotated[UUID, Path()],
    region_id: Annotated[str, Path(max_length=64)],
    payload: ConfirmRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_TRUST))],
) -> RegionMutationResponse:
    """Accept the machine's reading after comparing it with the original page."""

    _private(response)
    try:
        page = await repository.get_page(session, page_id)
        await repository.confirm(
            session,
            page=page,
            region_id=region_id,
            candidate_id=payload.candidate_id,
            revision=payload.revision,
            reviewer_id=principal.subject_id,
            compared_with_image_sha256=payload.compared_with_image_sha256,
            note=payload.note,
        )
        await session.commit()
    except SourceV2Error as error:
        await session.rollback()
        raise _fail(error) from error
    return await _region_response(session, page_id, region_id)


@router.post(
    "/source-v2/pages/{page_id}/regions/{region_id}/confirm-visual",
    response_model=RegionMutationResponse,
    responses={409: {"model": ApiErrorResponse}},
)
async def confirm_region_visual(
    response: Response,
    page_id: Annotated[UUID, Path()],
    region_id: Annotated[str, Path(max_length=64)],
    payload: ConfirmVisualRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_TRUST))],
) -> RegionMutationResponse:
    """Verify an educational figure as a figure, after comparing it with the page.

    D18. A drawing with no printed text is source content; before this the only
    available action was Exclude, which discarded it.
    """

    _private(response)
    try:
        page = await repository.get_page(session, page_id)
        await repository.confirm_visual(
            session,
            page=page,
            region_id=region_id,
            candidate_id=payload.candidate_id,
            revision=payload.revision,
            reviewer_id=principal.subject_id,
            compared_with_image_sha256=payload.compared_with_image_sha256,
            source_kind=payload.source_kind,
            text_value=payload.text,
            note=payload.note,
        )
        await session.commit()
    except SourceV2Error as error:
        await session.rollback()
        raise _fail(error) from error
    return await _region_response(session, page_id, region_id)


@router.post(
    "/source-v2/pages/{page_id}/regions/{region_id}/reclassify",
    response_model=RegionMutationResponse,
    responses={409: {"model": ApiErrorResponse}},
)
async def reclassify_region(
    response: Response,
    page_id: Annotated[UUID, Path()],
    region_id: Annotated[str, Path(max_length=64)],
    payload: ReclassifyRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_TRUST))],
) -> RegionMutationResponse:
    """Record that the reviewer disagrees with the proposed source kind."""

    _private(response)
    try:
        page = await repository.get_page(session, page_id)
        await repository.reclassify(
            session,
            page=page,
            region_id=region_id,
            candidate_id=payload.candidate_id,
            revision=payload.revision,
            reviewer_id=principal.subject_id,
            source_kind=payload.source_kind,
            note=payload.note,
        )
        await session.commit()
    except SourceV2Error as error:
        await session.rollback()
        raise _fail(error) from error
    return await _region_response(session, page_id, region_id)


@router.post(
    "/source-v2/pages/{page_id}/regions/{region_id}/correct",
    response_model=RegionMutationResponse,
    responses={409: {"model": ApiErrorResponse}},
)
async def correct_region(
    response: Response,
    page_id: Annotated[UUID, Path()],
    region_id: Annotated[str, Path(max_length=64)],
    payload: CorrectRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))],
) -> RegionMutationResponse:
    """Replace the reading. The corrected text still has to be confirmed."""

    _private(response)
    try:
        page = await repository.get_page(session, page_id)
        await repository.correct(
            session,
            page=page,
            region_id=region_id,
            candidate_id=payload.candidate_id,
            revision=payload.revision,
            reviewer_id=principal.subject_id,
            corrected_text=payload.corrected_text,
            note=payload.note,
        )
        await session.commit()
    except SourceV2Error as error:
        await session.rollback()
        raise _fail(error) from error
    return await _region_response(session, page_id, region_id)


@router.post(
    "/source-v2/pages/{page_id}/regions/{region_id}/exclude",
    response_model=RegionMutationResponse,
    responses={409: {"model": ApiErrorResponse}},
)
async def exclude_region(
    response: Response,
    page_id: Annotated[UUID, Path()],
    region_id: Annotated[str, Path(max_length=64)],
    payload: ExcludeRequest,
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))],
) -> RegionMutationResponse:
    """Take a region out of use, keeping every trace of why."""

    _private(response)
    try:
        page = await repository.get_page(session, page_id)
        await repository.exclude(
            session,
            page=page,
            region_id=region_id,
            candidate_id=payload.candidate_id,
            revision=payload.revision,
            reviewer_id=principal.subject_id,
            note=payload.note,
        )
        await session.commit()
    except SourceV2Error as error:
        await session.rollback()
        raise _fail(error) from error
    return await _region_response(session, page_id, region_id)


@router.get("/source-v2/documents/{document_id}/gate", response_model=DocumentGateView)
async def read_gate(
    response: Response,
    document_id: Annotated[UUID, Path()],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    principal: Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))],
) -> DocumentGateView:
    """Whether this document may be used downstream, and why not if it may not."""

    _private(response)
    _ = principal
    pages = await repository.document_resolution(session, document_id)
    try:
        await assert_document_usable(session, document_id, purpose=DownstreamPurpose.KNOWLEDGE)
    except NotVerifiedError as error:
        return DocumentGateView(
            document_id=document_id, usable=False, reason=str(error), pages=pages
        )
    return DocumentGateView(document_id=document_id, usable=True, pages=pages)
