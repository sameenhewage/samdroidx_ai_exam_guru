from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.domain import TaxonomyNode, validate_taxonomy
from exam_guru_api.curriculum.models import TaxonomyNodeModel


class SqlAlchemyTaxonomyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_nodes(self, nodes: Iterable[TaxonomyNode], *, actor_id: UUID) -> None:
        new_nodes = tuple(nodes)
        curriculum_version_ids = {node.curriculum_version_id for node in new_nodes}
        existing_models = await self._session.scalars(
            select(TaxonomyNodeModel).where(
                TaxonomyNodeModel.curriculum_version_id.in_(curriculum_version_ids)
            )
        )
        existing_nodes = tuple(model.to_domain() for model in existing_models)
        validate_taxonomy((*existing_nodes, *new_nodes))
        self._session.add_all(TaxonomyNodeModel.from_domain(node, actor_id) for node in new_nodes)
        await self._session.flush()

    async def list_nodes(self, curriculum_version_id: UUID) -> tuple[TaxonomyNode, ...]:
        result = await self._session.scalars(
            select(TaxonomyNodeModel)
            .where(TaxonomyNodeModel.curriculum_version_id == curriculum_version_id)
            .order_by(TaxonomyNodeModel.id)
        )
        return tuple(model.to_domain() for model in result)
