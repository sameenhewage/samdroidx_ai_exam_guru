from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.blueprints.analytics import PersistedAnalyticsEvidenceError
from exam_guru_api.blueprints.domain import BlueprintValidationError, ImpossibleBlueprintError
from exam_guru_api.blueprints.repository import (
    BlueprintFingerprintConflictError,
    PaperBlueprintNotFoundError,
)
from exam_guru_api.blueprints.schemas import (
    BlueprintCreateRequest,
    PaperBlueprintResponse,
    PaperBlueprintSummaryResponse,
)
from exam_guru_api.blueprints.serialization import BlueprintSnapshotError
from exam_guru_api.blueprints.service import (
    BlueprintAnalyticsCurriculumMismatchError,
    BlueprintAnalyticsRunNotFoundError,
    BlueprintCurriculumInactiveError,
    BlueprintCurriculumNotFoundError,
    BlueprintCurriculumScopeMismatchError,
    BlueprintGenerationService,
    BlueprintSnapshotLimitError,
    BlueprintTaxonomyValidationError,
)

router = APIRouter()
GeneratePrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.BLUEPRINT_GENERATE)),
]
ReadPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.BLUEPRINT_READ)),
]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post(
    "/{curriculum_version_id}/blueprints",
    operation_id="create_paper_blueprint",
    response_model=PaperBlueprintResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Existing identical blueprint returned",
            "model": PaperBlueprintResponse,
        },
        status.HTTP_201_CREATED: {"description": "Deterministic blueprint persisted"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Curriculum version not found",
            "model": ApiErrorResponse,
        },
        status.HTTP_409_CONFLICT: {
            "description": "Immutable blueprint identity conflict",
            "model": ApiErrorResponse,
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Invalid or impossible blueprint specification",
            "model": ApiErrorResponse,
        },
    },
    status_code=status.HTTP_201_CREATED,
    summary="Generate and persist a deterministic paper blueprint",
)
async def create_paper_blueprint(
    curriculum_version_id: UUID,
    request: BlueprintCreateRequest,
    response: Response,
    principal: GeneratePrincipal,
    session: DatabaseSession,
) -> PaperBlueprintResponse:
    result = await _execute_blueprint_operation(
        session,
        lambda: BlueprintGenerationService(session).create_blueprint(
            curriculum_version_id,
            request.to_domain(),
            seed=request.seed,
            analytics_run_id=request.analytics_run_id,
            actor_id=principal.subject_id,
        ),
    )
    response.status_code = status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED
    return PaperBlueprintResponse.from_record(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/blueprints",
    operation_id="list_paper_blueprints",
    response_model=list[PaperBlueprintSummaryResponse],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Curriculum version not found",
            "model": ApiErrorResponse,
        }
    },
    summary="List persisted paper blueprints",
)
async def list_paper_blueprints(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[PaperBlueprintSummaryResponse]:
    del principal
    records = await _execute_blueprint_operation(
        session,
        lambda: BlueprintGenerationService(session).list_blueprints(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        ),
    )
    return [PaperBlueprintSummaryResponse.from_record(record) for record in records]


@router.get(
    "/{curriculum_version_id}/blueprints/{paper_blueprint_id}",
    operation_id="get_paper_blueprint",
    response_model=PaperBlueprintResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Curriculum version or paper blueprint not found",
            "model": ApiErrorResponse,
        }
    },
    summary="Get one immutable paper blueprint snapshot",
)
async def get_paper_blueprint(
    curriculum_version_id: UUID,
    paper_blueprint_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> PaperBlueprintResponse:
    del principal
    record = await _execute_blueprint_operation(
        session,
        lambda: BlueprintGenerationService(session).get_blueprint(
            curriculum_version_id,
            paper_blueprint_id,
        ),
    )
    return PaperBlueprintResponse.from_record(record)


async def _execute_blueprint_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "blueprint_persistence_conflict"},
        ) from error
    except BlueprintCurriculumNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "curriculum_version_not_found"},
        ) from error
    except PaperBlueprintNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "paper_blueprint_not_found"},
        ) from error
    except BlueprintCurriculumInactiveError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "blueprint_curriculum_inactive"},
        ) from error
    except BlueprintCurriculumScopeMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "blueprint_curriculum_scope_mismatch",
                "field": error.field,
                "expected": str(error.expected),
                "actual": str(error.actual),
            },
        ) from error
    except BlueprintTaxonomyValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "blueprint_taxonomy_invalid",
                "node_id": str(error.node_id),
                "violation": error.violation.value,
            },
        ) from error
    except BlueprintAnalyticsRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "blueprint_analytics_run_not_found"},
        ) from error
    except BlueprintAnalyticsCurriculumMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "blueprint_analytics_cross_curriculum"},
        ) from error
    except PersistedAnalyticsEvidenceError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "blueprint_analytics_evidence_invalid"},
        ) from error
    except BlueprintSnapshotLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "blueprint_snapshot_limit_exceeded",
                "snapshot": error.snapshot,
                "maximum": error.maximum_bytes,
                "actual": error.actual_bytes,
            },
        ) from error
    except BlueprintSnapshotError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "blueprint_specification_invalid",
                "path": error.path,
                "message": error.detail,
            },
        ) from error
    except BlueprintValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "blueprint_constraint_violation",
                "violation": error.violation.value,
                "constraint": error.constraint,
                "message": error.detail,
                "impossible": isinstance(error, ImpossibleBlueprintError),
            },
        ) from error
    except BlueprintFingerprintConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "blueprint_fingerprint_conflict"},
        ) from error
