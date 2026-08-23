from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel


class AdminAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_events(
        self,
        *,
        limit: int,
        resource_type: str | None = None,
    ) -> Sequence[AdminAuditEventModel]:
        query = select(AdminAuditEventModel)
        if resource_type is not None:
            query = query.where(AdminAuditEventModel.resource_type == resource_type)
        query = query.order_by(AdminAuditEventModel.created_at.desc()).limit(limit)
        return (await self._session.scalars(query)).all()
