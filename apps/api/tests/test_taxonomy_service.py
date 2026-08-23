import asyncio
from collections.abc import Iterable
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode
from exam_guru_api.curriculum.repository import SqlAlchemyTaxonomyRepository
from exam_guru_api.curriculum.service import CurriculumVersionNotFoundError, TaxonomyService


class StubSession:
    def __init__(self, curriculum_exists: bool) -> None:
        self.curriculum_exists = curriculum_exists
        self.added: list[object] = []
        self.committed = False

    async def get(self, _model: object, _identifier: UUID) -> object | None:
        return object() if self.curriculum_exists else None

    def add(self, model: object) -> None:
        self.added.append(model)

    async def commit(self) -> None:
        self.committed = True


class StubRepository:
    def __init__(self, listed: tuple[TaxonomyNode, ...]) -> None:
        self.listed = listed
        self.added: tuple[TaxonomyNode, ...] = ()
        self.actor_id: UUID | None = None

    async def add_nodes(self, nodes: Iterable[TaxonomyNode], *, actor_id: UUID) -> None:
        self.added = tuple(nodes)
        self.actor_id = actor_id

    async def list_nodes(self, _curriculum_version_id: UUID) -> tuple[TaxonomyNode, ...]:
        return self.listed


def test_taxonomy_service_writes_audit_event_and_lists_nodes() -> None:
    curriculum_version_id = UUID(int=10)
    actor_id = UUID(int=11)
    node = TaxonomyNode(
        id=UUID(int=12),
        curriculum_version_id=curriculum_version_id,
        parent_id=UUID(int=13),
        level=TaxonomyLevel.SKILL,
        code="S1",
        title="Skill 1",
    )
    session = StubSession(curriculum_exists=True)
    repository = StubRepository((node,))
    service = TaxonomyService(cast(AsyncSession, session))
    service._repository = cast(SqlAlchemyTaxonomyRepository, repository)

    created = asyncio.run(service.add_node(node, actor_id=actor_id))
    listed = asyncio.run(service.list_nodes(curriculum_version_id))

    assert created is node
    assert listed == (node,)
    assert repository.added == (node,)
    assert repository.actor_id == actor_id
    assert session.committed
    assert len(session.added) == 1
    audit_event = cast(AdminAuditEventModel, session.added[0])
    assert audit_event.actor_id == actor_id
    assert audit_event.resource_id == node.id
    assert audit_event.payload["parent_id"] == str(node.parent_id)


def test_taxonomy_service_rejects_unknown_curriculum() -> None:
    service = TaxonomyService(cast(AsyncSession, StubSession(curriculum_exists=False)))
    node = TaxonomyNode(
        id=UUID(int=20),
        curriculum_version_id=UUID(int=21),
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Competency 1",
    )

    with pytest.raises(CurriculumVersionNotFoundError):
        asyncio.run(service.add_node(node, actor_id=UUID(int=22)))
