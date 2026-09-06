from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session, get_object_storage, get_settings
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.core.config import Environment, Settings
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    FixtureProvenanceEvidence,
    FixtureProvenanceMismatchError,
    FixtureQuarantineConflictError,
    FixtureReviewReason,
    InvalidFixtureQuarantineReviewError,
    SourceDocumentNotFoundError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage

router = APIRouter(prefix="/studio-safety")
SourceWritePrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
SourceStorage = Annotated[ObjectStorage, Depends(get_object_storage)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class RuntimeIdentityResponse(BaseModel):
    application_env: Environment
    test_runtime_id: str | None = None


class FixtureReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: FixtureReviewReason
    expected_version: Annotated[int, Field(strict=True, ge=0, le=2_147_483_646)]
    provenance_evidence: FixtureProvenanceEvidence


class QuarantineSourceFixtureRequest(FixtureReviewRequest):
    confirmation: Literal["quarantine_exact_source_fixture"]


class RestoreSourceFixtureRequest(FixtureReviewRequest):
    confirmation: Literal["restore_exact_source_fixture"]


class FixtureQuarantineResponse(BaseModel):
    id: UUID
    quarantined_for_teacher_use: bool
    active_for_ai: bool
    metadata_scope_version: int
    audit_event_id: UUID


@router.get(
    "/runtime-identity",
    operation_id="get_studio_runtime_identity",
    response_model=RuntimeIdentityResponse,
    response_model_exclude_none=True,
)
async def get_runtime_identity(
    principal: SourceWritePrincipal,
    settings: ApplicationSettings,
    response: Response,
) -> RuntimeIdentityResponse:
    del principal
    response.headers.update(_PRIVATE_HEADERS)
    return RuntimeIdentityResponse(
        application_env=settings.environment, test_runtime_id=settings.test_runtime_id
    )


@router.post(
    "/source-documents/{document_id}/quarantine",
    operation_id="quarantine_exact_source_fixture",
    response_model=FixtureQuarantineResponse,
)
async def quarantine_source_fixture(
    document_id: UUID,
    request: QuarantineSourceFixtureRequest,
    principal: SourceWritePrincipal,
    session: DatabaseSession,
    storage: SourceStorage,
    settings: ApplicationSettings,
    response: Response,
) -> FixtureQuarantineResponse:
    response.headers.update(_PRIVATE_HEADERS)
    return await _change_quarantine(
        document_id, request, principal, session, storage, settings, quarantined=True
    )


@router.post(
    "/source-documents/{document_id}/restore",
    operation_id="restore_exact_source_fixture",
    response_model=FixtureQuarantineResponse,
)
async def restore_source_fixture(
    document_id: UUID,
    request: RestoreSourceFixtureRequest,
    principal: SourceWritePrincipal,
    session: DatabaseSession,
    storage: SourceStorage,
    settings: ApplicationSettings,
    response: Response,
) -> FixtureQuarantineResponse:
    response.headers.update(_PRIVATE_HEADERS)
    return await _change_quarantine(
        document_id, request, principal, session, storage, settings, quarantined=False
    )


async def _change_quarantine(
    document_id: UUID,
    request: QuarantineSourceFixtureRequest | RestoreSourceFixtureRequest,
    principal: Principal,
    session: AsyncSession,
    storage: ObjectStorage,
    settings: Settings,
    *,
    quarantined: bool,
) -> FixtureQuarantineResponse:
    try:
        result = await SourceDocumentService(
            session, storage, max_upload_bytes=settings.max_upload_bytes
        ).change_fixture_quarantine(
            document_id,
            quarantined=quarantined,
            principal=principal,
            expected_version=request.expected_version,
            reason=request.reason,
            confirmation=request.confirmation,
            provenance_evidence=request.provenance_evidence,
        )
    except SourceDocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "source_document_not_found"},
            headers=_PRIVATE_HEADERS,
        ) from error
    except InvalidFixtureQuarantineReviewError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_fixture_quarantine_review"},
            headers=_PRIVATE_HEADERS,
        ) from error
    except (
        ConcurrentMaterialScopeVersionError,
        FixtureQuarantineConflictError,
        FixtureProvenanceMismatchError,
    ) as error:
        code = (
            "material_version_conflict"
            if isinstance(error, ConcurrentMaterialScopeVersionError)
            else "fixture_provenance_mismatch"
            if isinstance(error, FixtureProvenanceMismatchError)
            else "fixture_quarantine_state_conflict"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": code}, headers=_PRIVATE_HEADERS
        ) from error
    return FixtureQuarantineResponse(
        id=result.document.id,
        quarantined_for_teacher_use=result.document.quarantined_for_teacher_use,
        active_for_ai=result.document.active_for_ai,
        metadata_scope_version=result.document.metadata_scope_version,
        audit_event_id=result.audit_event_id,
    )
