from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.analytics.models import AnalyticsRunModel
from exam_guru_api.analytics.repository import AnalyticsRunRecord
from exam_guru_api.curriculum.domain import (
    LEGACY_UNCLASSIFIED_SUBJECT_ID,
    TaxonomyLevel,
    TaxonomyReviewState,
)
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)

from .models import PaperBlueprintModel


class PaperBlueprintNotFoundError(LookupError):
    def __init__(self, paper_blueprint_id: UUID) -> None:
        self.paper_blueprint_id = paper_blueprint_id
        super().__init__(f"paper blueprint not found: {paper_blueprint_id}")


class BlueprintFingerprintConflictError(RuntimeError):
    def __init__(self, input_fingerprint: str) -> None:
        self.input_fingerprint = input_fingerprint
        super().__init__(f"paper blueprint fingerprint conflict: {input_fingerprint}")


@dataclass(frozen=True, slots=True)
class CurriculumScopeRecord:
    curriculum_version_id: UUID
    grade: int
    medium: str
    curriculum_active: bool
    exam_active: bool
    medium_active: bool
    subject_id: UUID = LEGACY_UNCLASSIFIED_SUBJECT_ID
    subject_active: bool = True


@dataclass(frozen=True, slots=True)
class ReviewedTaxonomyNodeRecord:
    id: UUID
    curriculum_version_id: UUID
    parent_id: UUID | None
    level: TaxonomyLevel
    code: str
    title: str
    active: bool
    review_state: TaxonomyReviewState
    reviewed_at: datetime
    reviewed_by: UUID

    def to_snapshot(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "curriculum_version_id": str(self.curriculum_version_id),
            "parent_id": str(self.parent_id) if self.parent_id is not None else None,
            "level": self.level.value,
            "code": self.code,
            "title": self.title,
            "active": self.active,
            "review_state": self.review_state.value,
            "reviewed_at": self.reviewed_at.isoformat(),
            "reviewed_by": str(self.reviewed_by),
        }


@dataclass(frozen=True, slots=True)
class PaperBlueprintWrite:
    id: UUID
    curriculum_version_id: UUID
    analytics_run_id: UUID | None
    blueprint_id: str
    schema_version: str
    algorithm_version: str
    config_version: str
    seed: int
    total_marks: int
    slot_count: int
    specification_fingerprint: str
    input_fingerprint: str
    result_fingerprint: str
    specification: dict[str, object]
    blueprint: dict[str, object]
    taxonomy_snapshot: list[dict[str, object]]
    created_by: UUID


@dataclass(frozen=True, slots=True)
class PaperBlueprintRecord:
    id: UUID
    curriculum_version_id: UUID
    analytics_run_id: UUID | None
    blueprint_id: str
    schema_version: str
    algorithm_version: str
    config_version: str
    seed: int
    total_marks: int
    slot_count: int
    specification_fingerprint: str
    input_fingerprint: str
    result_fingerprint: str
    specification: dict[str, object]
    blueprint: dict[str, object]
    taxonomy_snapshot: list[dict[str, object]]
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RepositoryPaperBlueprintResult:
    record: PaperBlueprintRecord
    created: bool


class SqlAlchemyBlueprintRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_curriculum_scope(
        self,
        curriculum_version_id: UUID,
    ) -> CurriculumScopeRecord | None:
        row = (
            await self._session.execute(
                select(
                    CurriculumVersionModel.id,
                    ExamConfigurationModel.grade,
                    MediumModel.code,
                    SubjectModel.id,
                    CurriculumVersionModel.active,
                    ExamConfigurationModel.active,
                    MediumModel.active,
                    SubjectModel.active,
                )
                .join(
                    ExamConfigurationModel,
                    ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
                )
                .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
                .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
                .where(CurriculumVersionModel.id == curriculum_version_id)
            )
        ).one_or_none()
        if row is None:
            return None
        if len(row) == 6:  # Compatibility for legacy repository test adapters.
            return CurriculumScopeRecord(
                curriculum_version_id=row[0],
                grade=row[1],
                medium=row[2],
                curriculum_active=row[3],
                exam_active=row[4],
                medium_active=row[5],
            )
        return CurriculumScopeRecord(
            curriculum_version_id=row[0],
            grade=row[1],
            medium=row[2],
            subject_id=row[3],
            curriculum_active=row[4],
            exam_active=row[5],
            medium_active=row[6],
            subject_active=row[7],
        )

    async def learning_scope_exists(
        self,
        curriculum_version_id: UUID,
        unit_ids: tuple[UUID, ...],
        lesson_ids: tuple[UUID, ...],
    ) -> bool:
        if unit_ids:
            unit_count = await self._session.scalar(
                select(func.count(CurriculumUnitModel.id)).where(
                    CurriculumUnitModel.id.in_(unit_ids),
                    CurriculumUnitModel.curriculum_version_id == curriculum_version_id,
                    CurriculumUnitModel.active.is_(True),
                )
            )
            if unit_count != len(unit_ids):
                return False
        if lesson_ids:
            lesson_count = await self._session.scalar(
                select(func.count(CurriculumLessonModel.id)).where(
                    CurriculumLessonModel.id.in_(lesson_ids),
                    CurriculumLessonModel.curriculum_version_id == curriculum_version_id,
                    CurriculumLessonModel.unit_id.in_(unit_ids),
                    CurriculumLessonModel.active.is_(True),
                )
            )
            if lesson_count != len(lesson_ids):
                return False
        return True

    async def list_taxonomy_nodes(
        self,
        curriculum_version_id: UUID,
        node_ids: frozenset[UUID],
    ) -> tuple[ReviewedTaxonomyNodeRecord, ...]:
        if not node_ids:
            return ()
        models = tuple(
            await self._session.scalars(
                select(TaxonomyNodeModel)
                .where(
                    TaxonomyNodeModel.curriculum_version_id == curriculum_version_id,
                    TaxonomyNodeModel.id.in_(node_ids),
                )
                .order_by(TaxonomyNodeModel.id)
            )
        )
        return tuple(
            ReviewedTaxonomyNodeRecord(
                id=model.id,
                curriculum_version_id=model.curriculum_version_id,
                parent_id=model.parent_id,
                level=model.level,
                code=model.code,
                title=model.title,
                active=model.active,
                review_state=model.review_state,
                reviewed_at=model.updated_at,
                reviewed_by=model.updated_by,
            )
            for model in models
        )

    async def get_analytics_run(self, run_id: UUID) -> AnalyticsRunRecord | None:
        model = await self._session.get(AnalyticsRunModel, run_id)
        if model is None:
            return None
        return AnalyticsRunRecord(
            id=model.id,
            curriculum_version_id=model.curriculum_version_id,
            run_fingerprint=model.run_fingerprint,
            config_fingerprint=model.config_fingerprint,
            input_fingerprint=model.input_fingerprint,
            source_fingerprint=model.source_fingerprint,
            result_fingerprint=model.result_fingerprint,
            statistics_algorithm_version=model.statistics_algorithm_version,
            practice_priority_algorithm_version=model.practice_priority_algorithm_version,
            baseline_algorithm_version=model.baseline_algorithm_version,
            backtest_algorithm_version=model.backtest_algorithm_version,
            config=model.config,
            input_snapshot=model.input_snapshot,
            source_versions=model.source_versions,
            data_quality=model.data_quality,
            result=model.result,
            compute_duration_ms=model.compute_duration_ms,
            created_by=model.created_by,
            created_at=model.created_at,
        )

    async def store_blueprint(
        self,
        write: PaperBlueprintWrite,
    ) -> RepositoryPaperBlueprintResult:
        model = await self._session.scalar(
            insert(PaperBlueprintModel)
            .values(
                id=write.id,
                curriculum_version_id=write.curriculum_version_id,
                analytics_run_id=write.analytics_run_id,
                blueprint_id=write.blueprint_id,
                schema_version=write.schema_version,
                algorithm_version=write.algorithm_version,
                config_version=write.config_version,
                seed=write.seed,
                total_marks=write.total_marks,
                slot_count=write.slot_count,
                specification_fingerprint=write.specification_fingerprint,
                input_fingerprint=write.input_fingerprint,
                result_fingerprint=write.result_fingerprint,
                specification=write.specification,
                blueprint=write.blueprint,
                taxonomy_snapshot=write.taxonomy_snapshot,
                created_by=write.created_by,
            )
            .on_conflict_do_nothing()
            .returning(PaperBlueprintModel)
        )
        if model is not None:
            return RepositoryPaperBlueprintResult(self._to_record(model), created=True)

        model = await self._session.scalar(
            select(PaperBlueprintModel).where(
                or_(
                    PaperBlueprintModel.input_fingerprint == write.input_fingerprint,
                    PaperBlueprintModel.blueprint_id == write.blueprint_id,
                )
            )
        )
        if model is None or not self._same_blueprint(model, write):
            raise BlueprintFingerprintConflictError(write.input_fingerprint)
        return RepositoryPaperBlueprintResult(self._to_record(model), created=False)

    async def get_blueprint(
        self,
        curriculum_version_id: UUID,
        paper_blueprint_id: UUID,
    ) -> PaperBlueprintRecord:
        model = await self._session.scalar(
            select(PaperBlueprintModel).where(
                PaperBlueprintModel.id == paper_blueprint_id,
                PaperBlueprintModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if model is None:
            raise PaperBlueprintNotFoundError(paper_blueprint_id)
        return self._to_record(model)

    async def list_blueprints(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[PaperBlueprintRecord, ...]:
        models = tuple(
            await self._session.scalars(
                select(PaperBlueprintModel)
                .where(PaperBlueprintModel.curriculum_version_id == curriculum_version_id)
                .order_by(PaperBlueprintModel.created_at.desc(), PaperBlueprintModel.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return tuple(self._to_record(model) for model in models)

    @staticmethod
    def _same_blueprint(model: PaperBlueprintModel, write: PaperBlueprintWrite) -> bool:
        return (
            model.id == write.id
            and model.curriculum_version_id == write.curriculum_version_id
            and model.analytics_run_id == write.analytics_run_id
            and model.blueprint_id == write.blueprint_id
            and model.schema_version == write.schema_version
            and model.algorithm_version == write.algorithm_version
            and model.config_version == write.config_version
            and model.seed == write.seed
            and model.total_marks == write.total_marks
            and model.slot_count == write.slot_count
            and model.specification_fingerprint == write.specification_fingerprint
            and model.input_fingerprint == write.input_fingerprint
            and model.result_fingerprint == write.result_fingerprint
            and model.specification == write.specification
            and model.blueprint == write.blueprint
            and model.taxonomy_snapshot == write.taxonomy_snapshot
        )

    @staticmethod
    def _to_record(model: PaperBlueprintModel) -> PaperBlueprintRecord:
        return PaperBlueprintRecord(
            id=model.id,
            curriculum_version_id=model.curriculum_version_id,
            analytics_run_id=model.analytics_run_id,
            blueprint_id=model.blueprint_id,
            schema_version=model.schema_version,
            algorithm_version=model.algorithm_version,
            config_version=model.config_version,
            seed=model.seed,
            total_marks=model.total_marks,
            slot_count=model.slot_count,
            specification_fingerprint=model.specification_fingerprint,
            input_fingerprint=model.input_fingerprint,
            result_fingerprint=model.result_fingerprint,
            specification=model.specification,
            blueprint=model.blueprint,
            taxonomy_snapshot=model.taxonomy_snapshot,
            created_by=model.created_by,
            created_at=model.created_at,
        )
