import asyncio
from collections.abc import Iterable
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode, TaxonomyReviewState
from exam_guru_api.curriculum.models import CurriculumVersionModel, TaxonomyNodeModel
from exam_guru_api.curriculum.repository import SqlAlchemyTaxonomyRepository
from exam_guru_api.curriculum.service import (
    CurriculumVersionInactiveError,
    CurriculumVersionNotFoundError,
    TaxonomyNodeNotFoundError,
    TaxonomyService,
)


class StubSession:
    def __init__(
        self,
        curriculum_exists: bool,
        *,
        curriculum_active: bool = True,
        node_model: TaxonomyNodeModel | None = None,
    ) -> None:
        self.curriculum_exists = curriculum_exists
        self.curriculum_active = curriculum_active
        self.node_model = node_model
        self.added: list[object] = []
        self.committed = False

    async def get(self, model: object, _identifier: UUID, **_kwargs: object) -> object | None:
        if model is CurriculumVersionModel:
            return (
                SimpleNamespace(active=self.curriculum_active) if self.curriculum_exists else None
            )
        return self.node_model

    def add(self, model: object) -> None:
        self.added.append(model)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _model: object) -> None:
        return None


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


def test_taxonomy_service_update_review_and_deactivate_lifecycle() -> None:
    curriculum_version_id = UUID(int=30)
    actor_id = UUID(int=31)
    draft = TaxonomyNode(
        id=UUID(int=32),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Draft",
    )
    model = TaxonomyNodeModel.from_domain(draft, actor_id)
    session = StubSession(True, node_model=model)
    repository = StubRepository((draft,))
    service = TaxonomyService(cast(AsyncSession, session))
    service._repository = cast(SqlAlchemyTaxonomyRepository, repository)

    updated = asyncio.run(
        service.update_node(
            curriculum_version_id,
            draft.id,
            code="C2",
            title="Updated",
            actor_id=actor_id,
        )
    )
    reviewed = asyncio.run(service.review_node(curriculum_version_id, draft.id, actor_id=actor_id))
    reviewed_again = asyncio.run(
        service.review_node(curriculum_version_id, draft.id, actor_id=actor_id)
    )
    deprecated = asyncio.run(
        service.deactivate_node(curriculum_version_id, draft.id, actor_id=actor_id)
    )

    assert updated.code == "C2"
    assert reviewed.review_state is TaxonomyReviewState.REVIEWED
    assert reviewed_again.review_state is TaxonomyReviewState.REVIEWED
    assert deprecated.review_state is TaxonomyReviewState.DEPRECATED
    actions = [event.action for event in session.added if isinstance(event, AdminAuditEventModel)]
    assert actions == [
        "taxonomy.node.updated",
        "taxonomy.node.reviewed",
        "taxonomy.node.deactivated",
    ]


def test_taxonomy_service_rejects_inactive_curriculum_and_missing_node() -> None:
    curriculum_version_id = UUID(int=40)
    node = TaxonomyNode(
        id=UUID(int=41),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Draft",
    )
    inactive_service = TaxonomyService(
        cast(AsyncSession, StubSession(True, curriculum_active=False))
    )

    with pytest.raises(CurriculumVersionInactiveError):
        asyncio.run(inactive_service.add_node(node, actor_id=UUID(int=42)))

    missing_node_service = TaxonomyService(cast(AsyncSession, StubSession(True)))
    with pytest.raises(TaxonomyNodeNotFoundError):
        asyncio.run(
            missing_node_service.update_node(
                curriculum_version_id,
                node.id,
                code="C2",
                title="Missing",
                actor_id=UUID(int=42),
            )
        )

    repository = StubRepository(())
    inactive_service._repository = cast(SqlAlchemyTaxonomyRepository, repository)
    assert asyncio.run(inactive_service.list_nodes(curriculum_version_id)) == ()
