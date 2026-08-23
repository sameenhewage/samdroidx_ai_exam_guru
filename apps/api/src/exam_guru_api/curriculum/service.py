from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import (
    TaxonomyNode,
    TaxonomyReviewState,
    transition_review_state,
    update_taxonomy_node,
    validate_taxonomy,
)
from exam_guru_api.curriculum.models import CurriculumVersionModel, TaxonomyNodeModel
from exam_guru_api.curriculum.repository import SqlAlchemyTaxonomyRepository


class CurriculumVersionNotFoundError(LookupError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(str(curriculum_version_id))


class CurriculumVersionInactiveError(RuntimeError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(str(curriculum_version_id))


class TaxonomyNodeNotFoundError(LookupError):
    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        super().__init__(str(node_id))


class TaxonomyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SqlAlchemyTaxonomyRepository(session)

    async def add_node(self, node: TaxonomyNode, *, actor_id: UUID) -> TaxonomyNode:
        await self._require_curriculum_version(node.curriculum_version_id)
        await self._repository.add_nodes((node,), actor_id=actor_id)
        self._audit(
            action="taxonomy.node.created",
            actor_id=actor_id,
            node=node,
            payload={
                "active": node.active,
                "code": node.code,
                "curriculum_version_id": str(node.curriculum_version_id),
                "level": node.level.value,
                "parent_id": str(node.parent_id) if node.parent_id is not None else None,
                "review_state": node.review_state.value,
                "title": node.title,
            },
        )
        await self._session.commit()
        return node

    async def list_nodes(self, curriculum_version_id: UUID) -> tuple[TaxonomyNode, ...]:
        await self._require_curriculum_version(curriculum_version_id, require_active=False)
        return await self._repository.list_nodes(curriculum_version_id)

    async def update_node(
        self,
        curriculum_version_id: UUID,
        node_id: UUID,
        *,
        code: str,
        title: str,
        actor_id: UUID,
    ) -> TaxonomyNode:
        model = await self._get_node(curriculum_version_id, node_id)
        current = model.to_domain()
        candidate = update_taxonomy_node(current, code=code, title=title)
        await self._validate_candidate(curriculum_version_id, candidate)
        model.code = candidate.code
        model.title = candidate.title
        model.updated_by = actor_id
        self._audit(
            action="taxonomy.node.updated",
            actor_id=actor_id,
            node=candidate,
            payload={
                "changes": {
                    "code": {"from": current.code, "to": candidate.code},
                    "title": {"from": current.title, "to": candidate.title},
                }
            },
        )
        return await self._commit_model(model)

    async def review_node(
        self,
        curriculum_version_id: UUID,
        node_id: UUID,
        *,
        actor_id: UUID,
    ) -> TaxonomyNode:
        return await self._transition_node(
            curriculum_version_id,
            node_id,
            TaxonomyReviewState.REVIEWED,
            actor_id=actor_id,
            action="taxonomy.node.reviewed",
        )

    async def deactivate_node(
        self,
        curriculum_version_id: UUID,
        node_id: UUID,
        *,
        actor_id: UUID,
    ) -> TaxonomyNode:
        return await self._transition_node(
            curriculum_version_id,
            node_id,
            TaxonomyReviewState.DEPRECATED,
            actor_id=actor_id,
            action="taxonomy.node.deactivated",
        )

    async def _transition_node(
        self,
        curriculum_version_id: UUID,
        node_id: UUID,
        target: TaxonomyReviewState,
        *,
        actor_id: UUID,
        action: str,
    ) -> TaxonomyNode:
        model = await self._get_node(curriculum_version_id, node_id)
        current = model.to_domain()
        candidate = transition_review_state(current, target)
        if candidate is current:
            return current
        await self._validate_candidate(curriculum_version_id, candidate)
        model.active = candidate.active
        model.review_state = candidate.review_state
        model.updated_by = actor_id
        self._audit(
            action=action,
            actor_id=actor_id,
            node=candidate,
            payload={
                "review_state": {
                    "from": current.review_state.value,
                    "to": candidate.review_state.value,
                }
            },
        )
        return await self._commit_model(model)

    async def _validate_candidate(
        self,
        curriculum_version_id: UUID,
        candidate: TaxonomyNode,
    ) -> None:
        nodes = await self._repository.list_nodes(curriculum_version_id)
        validate_taxonomy(candidate if node.id == candidate.id else node for node in nodes)

    async def _get_node(
        self,
        curriculum_version_id: UUID,
        node_id: UUID,
    ) -> TaxonomyNodeModel:
        await self._require_curriculum_version(curriculum_version_id)
        model = await self._session.get(TaxonomyNodeModel, node_id, with_for_update=True)
        if model is None or model.curriculum_version_id != curriculum_version_id:
            raise TaxonomyNodeNotFoundError(node_id)
        return model

    async def _commit_model(self, model: TaxonomyNodeModel) -> TaxonomyNode:
        await self._session.commit()
        await self._session.refresh(model)
        return model.to_domain()

    async def _require_curriculum_version(
        self,
        curriculum_version_id: UUID,
        *,
        require_active: bool = True,
    ) -> None:
        curriculum_version = await self._session.get(
            CurriculumVersionModel,
            curriculum_version_id,
        )
        if curriculum_version is None:
            raise CurriculumVersionNotFoundError(curriculum_version_id)
        if require_active and not curriculum_version.active:
            raise CurriculumVersionInactiveError(curriculum_version_id)

    def _audit(
        self,
        *,
        action: str,
        actor_id: UUID,
        node: TaxonomyNode,
        payload: dict[str, Any],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="taxonomy_node",
                resource_id=node.id,
                payload=payload,
            )
        )
