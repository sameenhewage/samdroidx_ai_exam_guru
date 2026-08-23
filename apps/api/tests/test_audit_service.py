import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.audit import list_admin_audit_events
from exam_guru_api.auth.audit_service import AdminAuditService
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel


class ScalarRows:
    def __init__(self, rows: list[AdminAuditEventModel]) -> None:
        self._rows = rows

    def all(self) -> list[AdminAuditEventModel]:
        return self._rows


class StubSession:
    def __init__(self, rows: list[AdminAuditEventModel]) -> None:
        self.rows = rows

    async def scalars(self, _query: object) -> ScalarRows:
        return ScalarRows(self.rows)


def audit_event() -> AdminAuditEventModel:
    return AdminAuditEventModel(
        id=UUID(int=1),
        actor_id=UUID(int=2),
        action="taxonomy.node.created",
        resource_type="taxonomy_node",
        resource_id=UUID(int=3),
        payload={"code": "C1"},
        created_at=datetime.now(UTC),
    )


def test_audit_service_filters_and_route_serializes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = audit_event()
    session = cast(AsyncSession, StubSession([event]))
    service = AdminAuditService(session)

    assert asyncio.run(service.list_events(limit=10, resource_type="taxonomy_node")) == [event]

    async def list_events(
        _service: AdminAuditService,
        *,
        limit: int,
        resource_type: str | None = None,
    ) -> list[AdminAuditEventModel]:
        assert limit == 10
        assert resource_type == "taxonomy_node"
        return [event]

    monkeypatch.setattr(AdminAuditService, "list_events", list_events)
    principal = Principal(subject_id=UUID(int=2), roles=frozenset({AdminRole.REVIEWER}))
    responses = asyncio.run(
        list_admin_audit_events(
            principal,
            session,
            limit=10,
            resource_type="taxonomy_node",
        )
    )

    assert responses[0].action == "taxonomy.node.created"
