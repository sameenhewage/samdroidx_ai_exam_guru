from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import TaxonomyNode
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.curriculum.repository import SqlAlchemyTaxonomyRepository


class CurriculumVersionNotFoundError(LookupError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(str(curriculum_version_id))


class TaxonomyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SqlAlchemyTaxonomyRepository(session)

    async def add_node(self, node: TaxonomyNode, *, actor_id: UUID) -> TaxonomyNode:
        await self._require_curriculum_version(node.curriculum_version_id)
        await self._repository.add_nodes((node,), actor_id=actor_id)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="taxonomy.node.created",
                resource_type="taxonomy_node",
                resource_id=node.id,
                payload={
                    "active": node.active,
                    "code": node.code,
                    "curriculum_version_id": str(node.curriculum_version_id),
                    "level": node.level.value,
                    "parent_id": str(node.parent_id) if node.parent_id is not None else None,
                    "title": node.title,
                },
            )
        )
        await self._session.commit()
        return node

    async def list_nodes(self, curriculum_version_id: UUID) -> tuple[TaxonomyNode, ...]:
        await self._require_curriculum_version(curriculum_version_id)
        return await self._repository.list_nodes(curriculum_version_id)

    async def _require_curriculum_version(self, curriculum_version_id: UUID) -> None:
        curriculum_version = await self._session.get(
            CurriculumVersionModel,
            curriculum_version_id,
        )
        if curriculum_version is None:
            raise CurriculumVersionNotFoundError(curriculum_version_id)
