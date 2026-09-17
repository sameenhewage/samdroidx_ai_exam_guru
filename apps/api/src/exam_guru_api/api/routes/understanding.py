import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from exam_guru_api.api.dependencies import get_database_session, get_object_storage, get_settings
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission, require_rate_limit
from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal
from exam_guru_api.auth.rate_limits import RateLimitScope
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.fidelity_schemas import ExplicitConfirmation
from exam_guru_api.documents.page_images import (
    MAX_SOURCE_PAGE_NUMBER,
    PageImageError,
    create_page_image_artifacts,
)
from exam_guru_api.documents.source_consensus import IndependentReading
from exam_guru_api.documents.source_machine import MachineSourceCandidate
from exam_guru_api.documents.source_machine_service import (
    list_source_witness_events,
    load_machine_source,
    source_witness_image,
)
from exam_guru_api.documents.source_reading import SourceReadCandidate
from exam_guru_api.documents.source_verification import (
    VerifiedSourceContent,
    require_readable_source,
)
from exam_guru_api.documents.source_verification_service import SourceVerificationService
from exam_guru_api.documents.understanding_contracts import Key
from exam_guru_api.documents.understanding_jobs import (
    UnderstandingJobDispatcher,
    UnderstandingJobNotFoundError,
    UnderstandingJobService,
    dispatch_understanding_job,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingBudget,
    UnderstandingProviderProfile,
)
from exam_guru_api.documents.understanding_runtime import UnderstandingRuntime
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
    UnderstandingPageExclusion,
    UnderstandingPageSnapshot,
    UnderstandingSourceError,
)
from exam_guru_api.documents.understanding_verification import (
    ObservationCandidate,
    PageVerificationReport,
    TrustedPageKnowledge,
)
from exam_guru_api.generation.domain import GenerationAccounting
from exam_guru_api.infrastructure.object_storage import ObjectStorage

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


class _PrivateUnderstandingRoute(APIRoute):
    invalid_request_code = "invalid_source_understanding_request"

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                result = await original(request)
                result.headers.update(_PRIVATE_HEADERS)
                return result
            except StarletteHTTPException as error:
                raise HTTPException(
                    error.status_code,
                    detail=error.detail,
                    headers={**(error.headers or {}), **_PRIVATE_HEADERS},
                ) from None
            except RequestValidationError:
                raise HTTPException(
                    422,
                    detail={"code": self.invalid_request_code},
                    headers=_PRIVATE_HEADERS,
                ) from None

        return handler


class UnderstandingJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request_id: UUID
    expected_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    reason: str = Field(min_length=1, max_length=2000)


class UnderstandingVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    candidate_id: UUID
    expected_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    compared_with_original: ExplicitConfirmation
    reviewed_region_keys: tuple[Key, ...] = Field(min_length=1, max_length=128)
    resolved_uncertainty_keys: tuple[Key, ...] = Field(max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


def _page_content(value: object) -> SourceReadCandidate:
    if isinstance(value, SourceReadCandidate):
        return SourceReadCandidate.model_validate(value)
    return SourceReadCandidate.model_validate_json(
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    )


class UnderstandingCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    parent_candidate_id: UUID
    request_id: UUID
    expected_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    content: Annotated[SourceReadCandidate, BeforeValidator(_page_content)]
    reason: str = Field(min_length=1, max_length=2000)


class UnderstandingExclusionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    expected_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    confirm_exclusion: ExplicitConfirmation
    reason: str = Field(min_length=1, max_length=2000)


class UnderstandingReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    expected_version: int = Field(strict=True, ge=0, le=2_147_483_646)
    confirm_reopen: ExplicitConfirmation
    reason: str = Field(min_length=1, max_length=2000)


class UnderstandingMutationResponse(BaseModel):
    document_id: UUID
    page_number: int
    version: int
    state: str
    candidate_id: UUID | None


class UnderstandingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    page_number: int
    status: Literal["queued", "running", "succeeded", "failed", "unknown"]
    version: int
    expected_page_version: int
    attempts: int
    retry_depth: int
    candidate_id: UUID | None
    run_id: UUID | None
    failure_code: str | None
    accounting: GenerationAccounting | None
    profile: UnderstandingProviderProfile
    budget: UnderstandingBudget


class UnderstandingPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    document_id: UUID
    page_number: int
    version: int
    state: str
    candidate: ObservationCandidate | None
    report: PageVerificationReport | None
    trusted: TrustedPageKnowledge | None
    active_job_id: UUID | None
    parent_candidate_id: UUID | None = None
    exclusion: UnderstandingPageExclusion | None = None
    latest_job: UnderstandingJobResponse | None = None
    provider_available: bool = False
    provider_completed: bool = False
    verified_source: VerifiedSourceContent | None = None
    machine: MachineSourceCandidate | None = None
    source_status: Literal[
        "not_read",
        "reading",
        "source_fidelity_needs_review",
        "source_fidelity_failed",
        "source_verified",
        "excluded",
    ] = "not_read"


class SourceWitnessEventResponse(BaseModel):
    id: UUID
    reader: str
    pass_number: int
    event: str
    input_fingerprint: str
    source_input: dict[str, object]
    reading: IndependentReading | None
    failure_code: str | None


class SourceWitnessPageResponse(BaseModel):
    items: list[SourceWitnessEventResponse]
    next_offset: int | None


def _source_status(page: UnderstandingPageSnapshot, verified: VerifiedSourceContent | None) -> str:
    if page.state == "excluded":
        return "excluded"
    if page.active_job_id is not None:
        return "reading"
    if verified is not None:
        return "source_verified"
    if page.candidate is None:
        return "not_read"
    if (
        page.report is None
        or page.report.state == "needs_reprocessing"
        or page.candidate.content.education.claims
    ):
        return "source_fidelity_failed"
    try:
        require_readable_source(
            SourceReadCandidate(
                schema_version="source-read-candidate.v1",
                observation=page.candidate.content.observation,
                uncertainties=page.candidate.content.uncertainties,
            )
        )
    except ValueError:
        return "source_fidelity_failed"
    return "source_fidelity_needs_review"


router = APIRouter(
    route_class=_PrivateUnderstandingRoute,
    responses={code: {"model": ApiErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_WRITE))]
TrustPrincipal = Annotated[Principal, Depends(require_permission(Permission.SOURCE_TRUST))]
Storage = Annotated[ObjectStorage, Depends(get_object_storage)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]
TriggerPrincipal = Annotated[
    Principal,
    Depends(require_rate_limit(Permission.SOURCE_WRITE, RateLimitScope.DOCUMENT_UNDERSTANDING)),
]
PageNumber = Annotated[int, Path(ge=1, le=MAX_SOURCE_PAGE_NUMBER)]


def get_understanding_runtime(request: Request) -> UnderstandingRuntime:
    runtime = getattr(request.app.state, "understanding_runtime", None)
    if runtime is None:
        raise HTTPException(503, detail={"code": "source_understanding_provider_unconfigured"})
    return cast(UnderstandingRuntime, runtime)


def get_understanding_dispatcher(request: Request) -> UnderstandingJobDispatcher:
    dispatcher = getattr(request.app.state, "understanding_dispatcher", None)
    if dispatcher is None:
        raise HTTPException(503, detail={"code": "source_understanding_queue_unavailable"})
    return cast(UnderstandingJobDispatcher, dispatcher)


Runtime = Annotated[UnderstandingRuntime, Depends(get_understanding_runtime)]
Dispatcher = Annotated[UnderstandingJobDispatcher, Depends(get_understanding_dispatcher)]


async def _run[Result](session: AsyncSession, operation: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await operation()
    except AuthorizationError:
        await session.rollback()
        raise HTTPException(403, detail={"code": "permission_denied"}) from None
    except (UnderstandingSourceError, UnderstandingJobNotFoundError):
        await session.rollback()
        raise HTTPException(404, detail={"code": "source_understanding_not_found"}) from None
    except (UnderstandingConflictError, IntegrityError):
        await session.rollback()
        raise HTTPException(409, detail={"code": "source_understanding_conflict"}) from None
    except PageImageError as error:
        await session.rollback()
        raise HTTPException(error.status_code, detail={"code": error.code}) from None
    except ValueError:
        await session.rollback()
        raise HTTPException(422, detail={"code": "invalid_source_understanding_request"}) from None


@router.get(
    "/materials/{document_id}/pages/{page_number}/understanding",
    operation_id="get_source_page_understanding",
    response_model=UnderstandingPageResponse,
)
async def get_page_understanding(
    document_id: UUID,
    page_number: PageNumber,
    request: Request,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> UnderstandingPageResponse:
    page = await _run(
        session,
        lambda: PageUnderstandingService(session).get_page(
            principal=principal, document_id=document_id, page_number=page_number
        ),
    )
    latest = await _run(
        session,
        lambda: UnderstandingJobService(session).latest_for_page(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            page_version=page.version,
        ),
    )
    verified = await _run(
        session,
        lambda: SourceVerificationService(session).current(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
        ),
    )
    if verified is not None and (
        page.candidate is None or verified.candidate_id != page.candidate.id
    ):
        verified = None
    machine = None
    candidate = page.candidate
    if candidate is not None and page.active_job_id is None:
        machine = await _run(session, lambda: load_machine_source(session, candidate))
    status = (
        "source_fidelity_failed"
        if page.state == "needs_reprocessing"
        else _source_status(page, verified)
    )
    if (
        status == "source_fidelity_needs_review"
        and machine is not None
        and machine.state == "needs_attention"
    ):
        status = "source_fidelity_failed"
    response = UnderstandingPageResponse.model_validate(page)
    return response.model_copy(
        update={
            "verified_source": verified,
            "machine": machine,
            "source_status": status,
            "provider_completed": latest is not None and latest.status == "succeeded",
            "provider_available": getattr(request.app.state, "understanding_runtime", None)
            is not None,
            "latest_job": None
            if latest is None
            else UnderstandingJobResponse.model_validate(latest),
        }
    )


@router.get(
    "/materials/{document_id}/pages/{page_number}/understanding/candidates/{candidate_id}/image",
    operation_id="get_understanding_candidate_image",
    response_class=Response,
    responses={
        200: {
            "description": "Exact immutable image used by the observation candidate",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def get_candidate_image(
    document_id: UUID,
    page_number: PageNumber,
    candidate_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: ApplicationSettings,
) -> Response:
    image = await _run(
        session,
        lambda: PageUnderstandingService(session).candidate_image(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            candidate_id=candidate_id,
            storage=storage,
            artifacts=create_page_image_artifacts(settings),
        ),
    )
    return Response(image, media_type="image/png", headers=_PRIVATE_HEADERS)


@router.post(
    "/materials/{document_id}/pages/{page_number}/understanding/verify",
    operation_id="verify_source_understanding",
    response_model=VerifiedSourceContent,
)
async def verify_source_understanding(
    document_id: UUID,
    page_number: PageNumber,
    body: UnderstandingVerifyRequest,
    principal: TrustPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: ApplicationSettings,
) -> VerifiedSourceContent:
    return await _run(
        session,
        lambda: SourceVerificationService(session).verify(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            candidate_id=body.candidate_id,
            expected_version=body.expected_version,
            compared_with_original=body.compared_with_original,
            reviewed_region_keys=body.reviewed_region_keys,
            resolved_uncertainty_keys=body.resolved_uncertainty_keys,
            reason=body.reason,
            storage=storage,
            artifacts=create_page_image_artifacts(settings),
        ),
    )


def _mutation_response(page: UnderstandingPageSnapshot) -> UnderstandingMutationResponse:
    return UnderstandingMutationResponse(
        document_id=page.document_id,
        page_number=page.page_number,
        version=page.version,
        state=page.state,
        candidate_id=None if page.candidate is None else page.candidate.id,
    )


@router.post(
    "/materials/{document_id}/pages/{page_number}/understanding/corrections",
    operation_id="correct_source_understanding",
    response_model=ObservationCandidate,
)
async def correct_source_understanding(
    document_id: UUID,
    page_number: PageNumber,
    body: UnderstandingCorrectionRequest,
    principal: WritePrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: ApplicationSettings,
) -> ObservationCandidate:
    return await _run(
        session,
        lambda: PageUnderstandingService(session).correct(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            parent_candidate_id=body.parent_candidate_id,
            request_id=body.request_id,
            expected_version=body.expected_version,
            content=body.content.as_legacy_envelope(),
            reason=body.reason,
            storage=storage,
            artifacts=create_page_image_artifacts(settings),
        ),
    )


@router.post(
    "/materials/{document_id}/pages/{page_number}/understanding/exclude",
    operation_id="exclude_source_understanding_page",
    response_model=UnderstandingMutationResponse,
)
async def exclude_source_understanding_page(
    document_id: UUID,
    page_number: PageNumber,
    body: UnderstandingExclusionRequest,
    principal: TrustPrincipal,
    session: DatabaseSession,
) -> UnderstandingMutationResponse:
    page = await _run(
        session,
        lambda: PageUnderstandingService(session).exclude(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            expected_version=body.expected_version,
            confirm_exclusion=body.confirm_exclusion,
            reason=body.reason,
        ),
    )
    return _mutation_response(page)


@router.post(
    "/materials/{document_id}/pages/{page_number}/understanding/reopen",
    operation_id="reopen_source_understanding_page",
    response_model=UnderstandingMutationResponse,
)
async def reopen_source_understanding_page(
    document_id: UUID,
    page_number: PageNumber,
    body: UnderstandingReopenRequest,
    principal: TrustPrincipal,
    session: DatabaseSession,
) -> UnderstandingMutationResponse:
    page = await _run(
        session,
        lambda: PageUnderstandingService(session).reopen(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            expected_version=body.expected_version,
            confirm_reopen=body.confirm_reopen,
            reason=body.reason,
        ),
    )
    return _mutation_response(page)


@router.post(
    "/materials/{document_id}/pages/{page_number}/understanding/jobs",
    status_code=202,
    operation_id="create_source_understanding_job",
    response_model=UnderstandingJobResponse,
)
async def create_understanding_job(
    document_id: UUID,
    page_number: PageNumber,
    body: UnderstandingJobCreateRequest,
    principal: TriggerPrincipal,
    session: DatabaseSession,
    runtime: Runtime,
    dispatcher: Dispatcher,
) -> UnderstandingJobResponse:
    job = await _run(
        session,
        lambda: UnderstandingJobService(session).create(
            principal=principal,
            request_id=body.request_id,
            document_id=document_id,
            page_number=page_number,
            expected_version=body.expected_version,
            runtime=runtime,
            reason=body.reason,
        ),
    )
    if job.status == "queued":
        await dispatch_understanding_job(job.id, dispatcher)
    return UnderstandingJobResponse.model_validate(job)


@router.get(
    "/materials/understanding/jobs/{job_id}",
    operation_id="get_source_understanding_job",
    response_model=UnderstandingJobResponse,
)
async def get_understanding_job(
    job_id: UUID, principal: ReadPrincipal, session: DatabaseSession
) -> UnderstandingJobResponse:
    job = await _run(
        session, lambda: UnderstandingJobService(session).get(principal=principal, job_id=job_id)
    )
    return UnderstandingJobResponse.model_validate(job)


@router.get(
    "/materials/understanding/jobs/{job_id}/witnesses",
    operation_id="list_source_witness_events",
    response_model=SourceWitnessPageResponse,
)
async def get_source_witness_events(
    job_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    offset: Annotated[int, Query(ge=0, le=2560)] = 0,
    limit: Annotated[int, Query(ge=1, le=40)] = 20,
) -> SourceWitnessPageResponse:
    rows, next_offset = await _run(
        session,
        lambda: list_source_witness_events(
            session,
            principal=principal,
            job_id=job_id,
            offset=offset,
            limit=limit,
        ),
    )
    items = []
    for row in rows:
        value = row.payload.get("reading")
        reading = (
            None
            if value is None or row.reader == "layout"
            else IndependentReading.model_validate_json(json.dumps(value, ensure_ascii=False))
        )
        items.append(
            SourceWitnessEventResponse(
                id=row.id,
                reader=row.reader,
                pass_number=row.pass_number,
                event=row.event,
                input_fingerprint=row.input_fingerprint,
                source_input=cast(dict[str, object], row.payload["input"]),
                reading=reading,
                failure_code=cast(str | None, row.payload.get("failure_code")),
            )
        )
    return SourceWitnessPageResponse(items=items, next_offset=next_offset)


@router.get(
    "/materials/understanding/jobs/{job_id}/witnesses/{event_id}/image",
    operation_id="get_source_witness_image",
    responses={
        200: {
            "description": "Exact recorded independent-reader image",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def get_source_witness_image(
    job_id: UUID,
    event_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    storage: Storage,
    settings: ApplicationSettings,
) -> Response:
    image = await _run(
        session,
        lambda: source_witness_image(
            session,
            principal=principal,
            job_id=job_id,
            event_id=event_id,
            storage=storage,
            artifacts=create_page_image_artifacts(settings),
        ),
    )
    return Response(image, media_type="image/png", headers=_PRIVATE_HEADERS)
