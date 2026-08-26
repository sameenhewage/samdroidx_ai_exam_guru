from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.configuration_schemas import (
    CurriculumLessonCreate,
    CurriculumUnitCreate,
    CurriculumVersionCreate,
    ExamConfigurationCreate,
    MediumCreate,
    SubjectCreate,
)
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumLessonTaxonomyMappingModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge.models import HistoricalQuestionModel, KnowledgeChunkModel

ConfigurationModel = (
    ExamConfigurationModel
    | MediumModel
    | SubjectModel
    | CurriculumVersionModel
    | CurriculumUnitModel
    | CurriculumLessonModel
)
ModelT = TypeVar(
    "ModelT",
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    CurriculumVersionModel,
    CurriculumUnitModel,
    CurriculumLessonModel,
)


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


class ConfigurationScopeMismatchError(ValueError):
    def __init__(self, resource_type: str, resource_id: UUID) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(f"{resource_type}:{resource_id}")


@dataclass(frozen=True, slots=True)
class LessonConfigurationRecord:
    id: UUID
    curriculum_version_id: UUID
    unit_id: UUID
    code: str
    title: str
    ordinal: int
    active: bool
    taxonomy_node_ids: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime


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

    async def create_subject(self, request: SubjectCreate, *, actor_id: UUID) -> SubjectModel:
        model = SubjectModel(
            id=uuid4(),
            code=request.code,
            name=request.name,
            active=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(model)
        self._audit(
            "subject.created",
            "subject",
            model.id,
            actor_id,
            {"code": model.code, "name": model.name},
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
        subject = await self._get_active(SubjectModel, "subject", request.subject_id)
        model = CurriculumVersionModel(
            id=uuid4(),
            exam_configuration_id=exam.id,
            medium_id=medium.id,
            subject_id=subject.id,
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
                "subject_id": str(model.subject_id),
                "title": model.title,
            },
        )
        return await self._commit_and_refresh(model)

    async def create_unit(
        self,
        curriculum_version_id: UUID,
        request: CurriculumUnitCreate,
        *,
        actor_id: UUID,
    ) -> CurriculumUnitModel:
        await self._get_active(
            CurriculumVersionModel,
            "curriculum_version",
            curriculum_version_id,
        )
        model = CurriculumUnitModel(
            id=uuid4(),
            curriculum_version_id=curriculum_version_id,
            code=request.code,
            title=request.title,
            ordinal=request.ordinal,
            active=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(model)
        self._audit(
            "curriculum_unit.created",
            "curriculum_unit",
            model.id,
            actor_id,
            {
                "curriculum_version_id": str(curriculum_version_id),
                "code": model.code,
                "ordinal": model.ordinal,
                "title": model.title,
            },
        )
        return await self._commit_and_refresh(model)

    async def create_lesson(
        self,
        curriculum_version_id: UUID,
        request: CurriculumLessonCreate,
        *,
        actor_id: UUID,
    ) -> LessonConfigurationRecord:
        await self._get_active(
            CurriculumVersionModel,
            "curriculum_version",
            curriculum_version_id,
        )
        unit = await self._get_active(CurriculumUnitModel, "curriculum_unit", request.unit_id)
        if unit.curriculum_version_id != curriculum_version_id:
            raise ConfigurationScopeMismatchError("curriculum_unit", unit.id)
        await self._validate_taxonomy_nodes(curriculum_version_id, request.taxonomy_node_ids)
        model = CurriculumLessonModel(
            id=uuid4(),
            curriculum_version_id=curriculum_version_id,
            unit_id=unit.id,
            code=request.code,
            title=request.title,
            ordinal=request.ordinal,
            active=True,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(model)
        await self._session.flush()
        for node_id in request.taxonomy_node_ids:
            self._session.add(
                CurriculumLessonTaxonomyMappingModel(
                    lesson_id=model.id,
                    curriculum_version_id=curriculum_version_id,
                    unit_id=unit.id,
                    taxonomy_node_id=node_id,
                    created_by=actor_id,
                )
            )
        self._audit(
            "curriculum_lesson.created",
            "curriculum_lesson",
            model.id,
            actor_id,
            {
                "curriculum_version_id": str(curriculum_version_id),
                "unit_id": str(unit.id),
                "code": model.code,
                "ordinal": model.ordinal,
                "title": model.title,
                "taxonomy_node_ids": [str(value) for value in request.taxonomy_node_ids],
            },
        )
        await self._session.commit()
        await self._session.refresh(model)
        return self._lesson_record(model, request.taxonomy_node_ids)

    async def list_exams(self) -> Sequence[ExamConfigurationModel]:
        return (
            await self._session.scalars(
                select(ExamConfigurationModel).order_by(ExamConfigurationModel.code)
            )
        ).all()

    async def list_media(self) -> Sequence[MediumModel]:
        return (await self._session.scalars(select(MediumModel).order_by(MediumModel.code))).all()

    async def list_subjects(self) -> Sequence[SubjectModel]:
        return (await self._session.scalars(select(SubjectModel).order_by(SubjectModel.code))).all()

    async def list_curricula(self) -> Sequence[CurriculumVersionModel]:
        return (
            await self._session.scalars(
                select(CurriculumVersionModel).order_by(CurriculumVersionModel.code)
            )
        ).all()

    async def list_units(self, curriculum_version_id: UUID) -> Sequence[CurriculumUnitModel]:
        await self._get_existing(
            CurriculumVersionModel,
            "curriculum_version",
            curriculum_version_id,
        )
        return (
            await self._session.scalars(
                select(CurriculumUnitModel)
                .where(CurriculumUnitModel.curriculum_version_id == curriculum_version_id)
                .order_by(CurriculumUnitModel.ordinal, CurriculumUnitModel.id)
            )
        ).all()

    async def list_lessons(
        self,
        curriculum_version_id: UUID,
    ) -> tuple[LessonConfigurationRecord, ...]:
        await self._get_existing(
            CurriculumVersionModel,
            "curriculum_version",
            curriculum_version_id,
        )
        models = tuple(
            await self._session.scalars(
                select(CurriculumLessonModel)
                .where(CurriculumLessonModel.curriculum_version_id == curriculum_version_id)
                .order_by(
                    CurriculumLessonModel.unit_id,
                    CurriculumLessonModel.ordinal,
                    CurriculumLessonModel.id,
                )
            )
        )
        mappings = await self._taxonomy_ids_for_lessons(tuple(model.id for model in models))
        return tuple(self._lesson_record(model, mappings[model.id]) for model in models)

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

    async def update_subject(self, resource_id: UUID, name: str, *, actor_id: UUID) -> SubjectModel:
        model = await self._get_active(SubjectModel, "subject", resource_id, lock=True)
        self._update_text(model, "name", name, actor_id, "subject.updated", "subject")
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

    async def update_unit(
        self,
        curriculum_version_id: UUID,
        unit_id: UUID,
        title: str,
        *,
        actor_id: UUID,
    ) -> CurriculumUnitModel:
        model = await self._get_active(CurriculumUnitModel, "curriculum_unit", unit_id, lock=True)
        self._require_curriculum_scope(model.curriculum_version_id, curriculum_version_id, model.id)
        self._update_text(
            model, "title", title, actor_id, "curriculum_unit.updated", "curriculum_unit"
        )
        return await self._commit_and_refresh(model)

    async def update_lesson(
        self,
        curriculum_version_id: UUID,
        lesson_id: UUID,
        title: str,
        *,
        actor_id: UUID,
    ) -> LessonConfigurationRecord:
        model = await self._get_active(
            CurriculumLessonModel,
            "curriculum_lesson",
            lesson_id,
            lock=True,
        )
        self._require_curriculum_scope(model.curriculum_version_id, curriculum_version_id, model.id)
        self._update_text(
            model,
            "title",
            title,
            actor_id,
            "curriculum_lesson.updated",
            "curriculum_lesson",
        )
        await self._session.commit()
        await self._session.refresh(model)
        mappings = await self._taxonomy_ids_for_lessons((model.id,))
        return self._lesson_record(model, mappings[model.id])

    async def replace_lesson_taxonomy(
        self,
        curriculum_version_id: UUID,
        lesson_id: UUID,
        taxonomy_node_ids: tuple[UUID, ...],
        *,
        actor_id: UUID,
    ) -> LessonConfigurationRecord:
        model = await self._get_active(
            CurriculumLessonModel,
            "curriculum_lesson",
            lesson_id,
            lock=True,
        )
        self._require_curriculum_scope(model.curriculum_version_id, curriculum_version_id, model.id)
        await self._validate_taxonomy_nodes(curriculum_version_id, taxonomy_node_ids)
        previous = (await self._taxonomy_ids_for_lessons((model.id,)))[model.id]
        if previous == taxonomy_node_ids:
            return self._lesson_record(model, previous)
        await self._session.execute(
            delete(CurriculumLessonTaxonomyMappingModel).where(
                CurriculumLessonTaxonomyMappingModel.lesson_id == lesson_id
            )
        )
        for node_id in taxonomy_node_ids:
            self._session.add(
                CurriculumLessonTaxonomyMappingModel(
                    lesson_id=model.id,
                    curriculum_version_id=curriculum_version_id,
                    unit_id=model.unit_id,
                    taxonomy_node_id=node_id,
                    created_by=actor_id,
                )
            )
        model.updated_by = actor_id
        self._audit(
            "curriculum_lesson.taxonomy_replaced",
            "curriculum_lesson",
            model.id,
            actor_id,
            {
                "from": [str(value) for value in previous],
                "to": [str(value) for value in taxonomy_node_ids],
            },
        )
        await self._session.commit()
        await self._session.refresh(model)
        return self._lesson_record(model, taxonomy_node_ids)

    async def deactivate_exam(self, resource_id: UUID, *, actor_id: UUID) -> ExamConfigurationModel:
        model = await self._get_for_deactivation(
            ExamConfigurationModel, "exam_configuration", resource_id
        )
        if model.active and await self._has_active_curriculum(
            CurriculumVersionModel.exam_configuration_id == resource_id
        ):
            raise ConfigurationInUseError("exam_configuration", resource_id)
        return await self._deactivate(model, "exam_configuration", actor_id)

    async def deactivate_medium(self, resource_id: UUID, *, actor_id: UUID) -> MediumModel:
        model = await self._get_for_deactivation(MediumModel, "medium", resource_id)
        if model.active and await self._has_active_curriculum(
            CurriculumVersionModel.medium_id == resource_id
        ):
            raise ConfigurationInUseError("medium", resource_id)
        return await self._deactivate(model, "medium", actor_id)

    async def deactivate_subject(self, resource_id: UUID, *, actor_id: UUID) -> SubjectModel:
        model = await self._get_for_deactivation(SubjectModel, "subject", resource_id)
        if model.active and await self._has_active_curriculum(
            CurriculumVersionModel.subject_id == resource_id
        ):
            raise ConfigurationInUseError("subject", resource_id)
        return await self._deactivate(model, "subject", actor_id)

    async def deactivate_curriculum(
        self, resource_id: UUID, *, actor_id: UUID
    ) -> CurriculumVersionModel:
        model = await self._get_for_deactivation(
            CurriculumVersionModel, "curriculum_version", resource_id
        )
        if not model.active:
            return model
        active_taxonomy = await self._exists_where(
            TaxonomyNodeModel.curriculum_version_id == resource_id,
            TaxonomyNodeModel.active.is_(True),
        )
        active_units = False
        if not active_taxonomy:
            active_units = await self._exists_where(
                CurriculumUnitModel.curriculum_version_id == resource_id,
                CurriculumUnitModel.active.is_(True),
            )
        if model.active and (active_taxonomy or active_units):
            raise ConfigurationInUseError("curriculum_version", resource_id)
        return await self._deactivate(model, "curriculum_version", actor_id)

    async def deactivate_unit(
        self,
        curriculum_version_id: UUID,
        unit_id: UUID,
        *,
        actor_id: UUID,
    ) -> CurriculumUnitModel:
        model = await self._get_for_deactivation(CurriculumUnitModel, "curriculum_unit", unit_id)
        self._require_curriculum_scope(model.curriculum_version_id, curriculum_version_id, model.id)
        in_use = any(
            (
                await self._exists_where(
                    CurriculumLessonModel.unit_id == unit_id,
                    CurriculumLessonModel.active.is_(True),
                ),
                await self._exists_where(SourceDocumentModel.unit_id == unit_id),
                await self._exists_where(KnowledgeChunkModel.unit_id == unit_id),
                await self._exists_where(HistoricalQuestionModel.unit_id == unit_id),
            )
        )
        if model.active and in_use:
            raise ConfigurationInUseError("curriculum_unit", unit_id)
        return await self._deactivate(model, "curriculum_unit", actor_id)

    async def deactivate_lesson(
        self,
        curriculum_version_id: UUID,
        lesson_id: UUID,
        *,
        actor_id: UUID,
    ) -> LessonConfigurationRecord:
        model = await self._get_for_deactivation(
            CurriculumLessonModel,
            "curriculum_lesson",
            lesson_id,
        )
        self._require_curriculum_scope(model.curriculum_version_id, curriculum_version_id, model.id)
        in_use = any(
            (
                await self._exists_where(SourceDocumentModel.lesson_id == lesson_id),
                await self._exists_where(KnowledgeChunkModel.lesson_id == lesson_id),
                await self._exists_where(HistoricalQuestionModel.lesson_id == lesson_id),
            )
        )
        if model.active and in_use:
            raise ConfigurationInUseError("curriculum_lesson", lesson_id)
        model = await self._deactivate(model, "curriculum_lesson", actor_id)
        mappings = await self._taxonomy_ids_for_lessons((model.id,))
        return self._lesson_record(model, mappings[model.id])

    async def _exists_where(self, *conditions: Any) -> bool:
        return bool(await self._session.scalar(select(exists().where(*conditions))))

    async def _has_active_curriculum(self, condition: Any) -> bool:
        return bool(
            await self._session.scalar(
                select(exists().where(condition, CurriculumVersionModel.active.is_(True)))
            )
        )

    async def _get_active(
        self, model_type: type[ModelT], resource_type: str, resource_id: UUID, *, lock: bool = False
    ) -> ModelT:
        model = await self._session.get(model_type, resource_id, with_for_update=lock)
        if model is None:
            raise ConfigurationNotFoundError(resource_type, resource_id)
        if not model.active:
            raise ConfigurationInactiveError(resource_type, resource_id)
        return model

    async def _get_existing(
        self,
        model_type: type[ModelT],
        resource_type: str,
        resource_id: UUID,
    ) -> ModelT:
        model = await self._session.get(model_type, resource_id)
        if model is None:
            raise ConfigurationNotFoundError(resource_type, resource_id)
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

    async def _validate_taxonomy_nodes(
        self,
        curriculum_version_id: UUID,
        node_ids: tuple[UUID, ...],
    ) -> None:
        if not node_ids:
            return
        found = set(
            await self._session.scalars(
                select(TaxonomyNodeModel.id).where(
                    TaxonomyNodeModel.curriculum_version_id == curriculum_version_id,
                    TaxonomyNodeModel.id.in_(node_ids),
                    TaxonomyNodeModel.active.is_(True),
                )
            )
        )
        missing = set(node_ids) - found
        if missing:
            missing_id = min(missing, key=lambda value: value.int)
            raise ConfigurationScopeMismatchError("taxonomy_node", missing_id)

    async def _taxonomy_ids_for_lessons(
        self,
        lesson_ids: tuple[UUID, ...],
    ) -> defaultdict[UUID, tuple[UUID, ...]]:
        result: defaultdict[UUID, list[UUID]] = defaultdict(list)
        if lesson_ids:
            rows = await self._session.execute(
                select(
                    CurriculumLessonTaxonomyMappingModel.lesson_id,
                    CurriculumLessonTaxonomyMappingModel.taxonomy_node_id,
                )
                .where(CurriculumLessonTaxonomyMappingModel.lesson_id.in_(lesson_ids))
                .order_by(
                    CurriculumLessonTaxonomyMappingModel.lesson_id,
                    CurriculumLessonTaxonomyMappingModel.taxonomy_node_id,
                )
            )
            for lesson_id, taxonomy_node_id in rows:
                result[lesson_id].append(taxonomy_node_id)
        frozen: defaultdict[UUID, tuple[UUID, ...]] = defaultdict(tuple)
        frozen.update({key: tuple(values) for key, values in result.items()})
        return frozen

    @staticmethod
    def _lesson_record(
        model: CurriculumLessonModel,
        taxonomy_node_ids: tuple[UUID, ...],
    ) -> LessonConfigurationRecord:
        return LessonConfigurationRecord(
            id=model.id,
            curriculum_version_id=model.curriculum_version_id,
            unit_id=model.unit_id,
            code=model.code,
            title=model.title,
            ordinal=model.ordinal,
            active=model.active,
            taxonomy_node_ids=taxonomy_node_ids,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _require_curriculum_scope(actual: UUID, expected: UUID, resource_id: UUID) -> None:
        if actual != expected:
            raise ConfigurationScopeMismatchError("curriculum_version", resource_id)

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
