from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.operations.schemas import OperationsSummaryResponse
from exam_guru_api.operations.service import (
    OperationsSummaryService,
    OperationsWindow,
    OperationsWindowError,
)

router = APIRouter()
OperationsPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.OPERATIONS_READ)),
]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]


@router.get(
    "/operations/summary",
    operation_id="get_operations_summary",
    response_model=OperationsSummaryResponse,
    responses={
        status.HTTP_403_FORBIDDEN: {
            "description": "The authenticated principal cannot read operational aggregates",
            "model": ApiErrorResponse,
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The UTC operations window is invalid or unsupported",
            "model": ApiErrorResponse,
        },
    },
    summary="Read a fixed operational and AI-cost summary",
    description=(
        "Aggregates persisted operational state over a half-open UTC window. The default window "
        "is 24 hours and the maximum is 31 days. Dimensions are fixed server-side; content, "
        "vectors, prompts, secrets, and high-cardinality resource identifiers are never returned."
    ),
)
async def get_operations_summary(
    request: Request,
    principal: OperationsPrincipal,
    session: DatabaseSession,
    start: Annotated[datetime | None, Query(description="Inclusive UTC window start")] = None,
    end: Annotated[datetime | None, Query(description="Exclusive UTC window end")] = None,
) -> OperationsSummaryResponse:
    del principal
    unsupported = set(request.query_params) - {"start", "end"}
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "unsupported_operations_query_parameter"},
        )
    if any(len(request.query_params.getlist(name)) > 1 for name in ("start", "end")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "operations_window_ambiguous"},
        )
    try:
        window = OperationsWindow.resolve(
            start=start,
            end=end,
            now=datetime.now(UTC),
        )
    except OperationsWindowError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code},
        ) from error
    return await OperationsSummaryService(session).summarize(window)
