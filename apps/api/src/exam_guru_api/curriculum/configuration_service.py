from collections.abc import Sequence
from typing import Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.configuration_schemas import (
    CurriculumVersionCreate,
    ExamConfigurationCreate,
    MediumCreate,
)
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    TaxonomyNodeModel,
)

ConfigurationModel = ExamConfigurationModel | MediumModel | CurriculumVersionModel
ModelT = TypeVar("ModelT", ExamConfigurationModel, MediumModel, CurriculumVersionModel)


class ConfigurationNotFoundError(LookupError):
    def __init__(self, resource_type: str, resource_id: UUID) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type}:{resource_id}")


class ConfigurationInactiveError(RuntimeError):
    def __init__(self, resource_type: str, resource_id: UUID) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type}:{resource_id}")


class ConfigurationInUseError(RuntimeError):
    def __init__(self, resource_type: str, resource_id: UUID) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type}:{resource_id}")


class ConfigurationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_exam(
        self,
        request: ExamConfigurationCreate,
        *,
        actor_id: UUID,
    ) -> ExamConfigurationModel:
        model = ExamConfigurationModel(
            id=uuid4(),
            code=request.code,
            name=request.name,
            grade=request.grade,
            active=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(model)
        self._audit(
            "exam_configuration.created",
            "exam_configuration",
            model.id,
            actor_id,
            {"code": model.code, "grade": model.grade, "name": model.name},
        )
        return await self._commit_and_refresh(model)

    async def create_medium(self, request: MediumCreate, *, actor_id: UUID) -> MediumModel:
        model = MediumModel(
            id=uuid4(),
            code=request.code,
            name=request.name,
            active=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(model)
        self._audit(
            "medium.created", "medium", model.id, actor_id, {"code": model.code, "name": model.name}
        )
        return await self._commit_and_refresh(model)

    async def create_curriculum(
        self,
        request: CurriculumVersionCreate,
        *,
        actor_id: UUID,
    ) -> CurriculumVersionModel:
        exam = await self._get_active(
            ExamConfigurationModel, "exam_configuration", request.exam_configuration_id
        )
        medium = await self._get_active(MediumModel, "medium", request.medium_id)
        model = CurriculumVersionModel(
            id=uuid4(),
            exam_configuration_id=exam.id,
            medium_id=medium.id,
            code=request.code,
            title=request.title,
            active=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(model)
        self._audit(
            "curriculum_version.created",
            "curriculum_version",
            model.id,
            actor_id,
            {
                "code": model.code,
                "exam_configuration_id": str(model.exam_configuration_id),
                "medium_id": str(model.medium_id),
                "title": model.title,
            },
        )
        return await self._commit_and_refresh(model)

    async def list_exams(self) -> Sequence[ExamConfigurationModel]:
        return (
            await self._session.scalars(
                select(ExamConfigurationModel).order_by(ExamConfigurationModel.code)
            )
        ).all()

    async def list_media(self) -> Sequence[MediumModel]:
        return (await self._session.scalars(select(MediumModel).order_by(MediumModel.code))).all()

    async def list_curricula(self) -> Sequence[CurriculumVersionModel]:
        return (
            await self._session.scalars(
                select(CurriculumVersionModel).order_by(CurriculumVersionModel.code)
            )
        ).all()

    async def update_exam(
        self, resource_id: UUID, name: str, *, actor_id: UUID
    ) -> ExamConfigurationModel:
        model = await self._get_active(
            ExamConfigurationModel, "exam_configuration", resource_id, lock=True
        )
        self._update_text(
            model, "name", name, actor_id, "exam_configuration.updated", "exam_configuration"
        )
        return await self._commit_and_refresh(model)

    async def update_medium(self, resource_id: UUID, name: str, *, actor_id: UUID) -> MediumModel:
        model = await self._get_active(MediumModel, "medium", resource_id, lock=True)
        self._update_text(model, "name", name, actor_id, "medium.updated", "medium")
        return await self._commit_and_refresh(model)

    async def update_curriculum(
        self, resource_id: UUID, title: str, *, actor_id: UUID
    ) -> CurriculumVersionModel:
        model = await self._get_active(
            CurriculumVersionModel, "curriculum_version", resource_id, lock=True
        )
        self._update_text(
            model, "title", title, actor_id, "curriculum_version.updated", "curriculum_version"
        )
        return await self._commit_and_refresh(model)

    async def deactivate_exam(self, resource_id: UUID, *, actor_id: UUID) -> ExamConfigurationModel:
        model = await self._get_for_deactivation(
            ExamConfigurationModel, "exam_configuration", resource_id
        )
        if model.active and await self._session.scalar(
            select(
                exists().where(
                    CurriculumVersionModel.exam_configuration_id == resource_id,
                    CurriculumVersionModel.active.is_(True),
                )
            )
        ):
            raise ConfigurationInUseError("exam_configuration", resource_id)
        return await self._deactivate(model, "exam_configuration", actor_id)

    async def deactivate_medium(self, resource_id: UUID, *, actor_id: UUID) -> MediumModel:
        model = await self._get_for_deactivation(MediumModel, "medium", resource_id)
        if model.active and await self._session.scalar(
            select(
                exists().where(
                    CurriculumVersionModel.medium_id == resource_id,
                    CurriculumVersionModel.active.is_(True),
                )
            )
        ):
            raise ConfigurationInUseError("medium", resource_id)
        return await self._deactivate(model, "medium", actor_id)

    async def deactivate_curriculum(
        self, resource_id: UUID, *, actor_id: UUID
    ) -> CurriculumVersionModel:
        model = await self._get_for_deactivation(
            CurriculumVersionModel, "curriculum_version", resource_id
        )
        if model.active and await self._session.scalar(
            select(
                exists().where(
                    TaxonomyNodeModel.curriculum_version_id == resource_id,
                    TaxonomyNodeModel.active.is_(True),
                )
            )
        ):
            raise ConfigurationInUseError("curriculum_version", resource_id)
        return await self._deactivate(model, "curriculum_version", actor_id)

    async def _get_active(
        self, model_type: type[ModelT], resource_type: str, resource_id: UUID, *, lock: bool = False
    ) -> ModelT:
        model = await self._session.get(model_type, resource_id, with_for_update=lock)
        if model is None:
            raise ConfigurationNotFoundError(resource_type, resource_id)
        if not model.active:
            raise ConfigurationInactiveError(resource_type, resource_id)
        return model

    async def _get_for_deactivation(
        self, model_type: type[ModelT], resource_type: str, resource_id: UUID
    ) -> ModelT:
        model = await self._session.get(model_type, resource_id, with_for_update=True)
        if model is None:
            raise ConfigurationNotFoundError(resource_type, resource_id)
        return model

    async def _deactivate(self, model: ModelT, resource_type: str, actor_id: UUID) -> ModelT:
        if not model.active:
            return model
        model.active = False
        model.updated_by = actor_id
        self._audit(
            f"{resource_type}.deactivated", resource_type, model.id, actor_id, {"active": False}
        )
        return await self._commit_and_refresh(model)

    async def _commit_and_refresh(self, model: ModelT) -> ModelT:
        await self._session.commit()
        await self._session.refresh(model)
        return model

    def _update_text(
        self,
        model: ConfigurationModel,
        field: str,
        value: str,
        actor_id: UUID,
        action: str,
        resource_type: str,
    ) -> None:
        previous = getattr(model, field)
        setattr(model, field, value)
        model.updated_by = actor_id
        self._audit(
            action,
            resource_type,
            model.id,
            actor_id,
            {"changes": {field: {"from": previous, "to": value}}},
        )

    def _audit(
        self,
        action: str,
        resource_type: str,
        resource_id: UUID,
        actor_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=payload,
            )
        )
