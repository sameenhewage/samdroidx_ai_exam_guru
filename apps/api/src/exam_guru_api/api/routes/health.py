import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from exam_guru_api.api.dependencies import get_resources, get_settings
from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.resources import ApplicationResources
from exam_guru_api.schemas.health import (
    DependencyChecks,
    DependencyStatus,
    HealthResponse,
    ReadinessResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _dependency_status(
    check: Callable[[], Awaitable[None]],
    timeout_seconds: float,
) -> DependencyStatus:
    try:
        await asyncio.wait_for(check(), timeout=timeout_seconds)
    except Exception:
        logger.warning("Readiness dependency check failed", exc_info=True)
        return "unavailable"
    return "ok"


@router.get(
    "/live",
    operation_id="get_liveness",
    response_model=HealthResponse,
    summary="Check API liveness",
)
def get_liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    operation_id="get_readiness",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
    summary="Check API dependencies",
)
async def get_readiness(
    response: Response,
    resources: Annotated[ApplicationResources, Depends(get_resources)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    database_status, valkey_status = await asyncio.gather(
        _dependency_status(resources.check_database, settings.readiness_timeout_seconds),
        _dependency_status(resources.check_valkey, settings.readiness_timeout_seconds),
    )
    checks = DependencyChecks(database=database_status, valkey=valkey_status)
    readiness_status: DependencyStatus = (
        "ok" if database_status == valkey_status == "ok" else "unavailable"
    )
    if readiness_status == "unavailable":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status=readiness_status, checks=checks)
