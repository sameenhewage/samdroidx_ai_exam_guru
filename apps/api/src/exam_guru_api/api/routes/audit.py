from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.audit_service import AdminAuditService
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.auth.schemas import AdminAuditEventResponse

router = APIRouter()


@router.get(
    "/audit-events",
    operation_id="list_admin_audit_events",
    response_model=list[AdminAuditEventResponse],
)
async def list_admin_audit_events(
    principal: Annotated[
        Principal,
        Depends(require_permission(Permission.TAXONOMY_READ)),
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
) -> list[AdminAuditEventResponse]:
    del principal
    events = await AdminAuditService(session).list_events(
        limit=limit,
        resource_type=resource_type,
    )
    return [AdminAuditEventResponse.model_validate(event) for event in events]
