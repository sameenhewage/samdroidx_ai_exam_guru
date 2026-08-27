from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.generation.repository import (
    GenerationContextRecord,
    SqlAlchemyGenerationRepository,
)
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.models import HistoricalQuestionModel

from .models import (
    MAX_VALIDATION_DUPLICATE_REFERENCES,
    ValidationFindingModel,
    ValidationRunModel,
)


@dataclass(frozen=True, slots=True)
class ValidationContextScopeRecord:
    context: GenerationContextRecord
    subject_id: UUID


@dataclass(frozen=True, slots=True)
class ValidationTrustedScopeRecord:
    blueprint: PaperBlueprintModel
    grade: int
    medium: str
    subject_id: UUID
    subject_code: str
    context_records: tuple[ValidationContextScopeRecord, ...]


@dataclass(frozen=True, slots=True)
class ValidationGenerationRecord:
    run: GenerationRunModel
    attempt: GenerationAttemptModel | None
    trusted_scope: ValidationTrustedScopeRecord | None = None


@dataclass(frozen=True, slots=True)
class DuplicateReferenceRecord:
    reference_kind: str
    record_id: UUID
    text: str
    record_version: str
    source_document_id: UUID | None = None
    source_page_number: int | None = None
    validation_run_id: UUID | None = None
    generation_run_id: UUID | None = None
    report_fingerprint: str | None = None
    pipeline_version: str | None = None


@dataclass(frozen=True, slots=True)
class StoredValidationReport:
    run: ValidationRunModel
    created: bool


class ValidationGenerationNotFoundError(LookupError):
    pass


class ValidationRunNotFoundError(LookupError):
    pass


class SqlAlchemyValidationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def curriculum_exists(self, curriculum_version_id: UUID) -> bool:
        return (
            await self._session.scalar(
                select(CurriculumVersionModel.id).where(
                    CurriculumVersionModel.id == curriculum_version_id
                )
            )
            is not None
        )

    async def get_generation(
        self,
        curriculum_version_id: UUID,
        generation_run_id: UUID,
    ) -> ValidationGenerationRecord:
        row = (
            await self._session.execute(
                select(
                    GenerationRunModel,
                    GenerationAttemptModel,
                    PaperBlueprintModel,
                    ExamConfigurationModel.grade,
                    MediumModel.code,
                    SubjectModel.id,
                    SubjectModel.code,
                )
                .outerjoin(
                    GenerationAttemptModel,
                    GenerationAttemptModel.id == GenerationRunModel.result_attempt_id,
                )
                .join(
                    PaperBlueprintModel,
                    PaperBlueprintModel.id == GenerationRunModel.paper_blueprint_id,
                )
                .join(
                    CurriculumVersionModel,
                    CurriculumVersionModel.id == GenerationRunModel.curriculum_version_id,
                )
                .join(
                    ExamConfigurationModel,
                    ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
                )
                .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
                .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
                .where(
                    GenerationRunModel.id == generation_run_id,
                    GenerationRunModel.curriculum_version_id == curriculum_version_id,
                    PaperBlueprintModel.curriculum_version_id == curriculum_version_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise ValidationGenerationNotFoundError(generation_run_id)
        if len(row) == 2:  # Compatibility for narrow repository test adapters.
            return ValidationGenerationRecord(run=row[0], attempt=row[1])
        run = row[0]
        context_records = await SqlAlchemyGenerationRepository(self._session).list_context_records(
            tuple(UUID(value) for value in run.knowledge_chunk_ids),
            tuple(UUID(value) for value in run.historical_question_ids),
        )
        curriculum_ids = {record.curriculum_version_id for record in context_records}
        subject_by_curriculum = {
            item[0]: item[1]
            for item in (
                await self._session.execute(
                    select(CurriculumVersionModel.id, CurriculumVersionModel.subject_id).where(
                        CurriculumVersionModel.id.in_(curriculum_ids)
                    )
                )
            ).all()
        }
        scoped_context = tuple(
            ValidationContextScopeRecord(
                context=record,
                subject_id=subject_by_curriculum.get(record.curriculum_version_id, UUID(int=0)),
            )
            for record in context_records
        )
        return ValidationGenerationRecord(
            run=run,
            attempt=row[1],
            trusted_scope=ValidationTrustedScopeRecord(
                blueprint=row[2],
                grade=row[3],
                medium=row[4],
                subject_id=row[5],
                subject_code=row[6],
                context_records=scoped_context,
            ),
        )

    async def lock_generation_for_validation(
        self,
        curriculum_version_id: UUID,
        generation_run_id: UUID,
    ) -> None:
        locked_id = await self._session.scalar(
            select(GenerationRunModel.id)
            .where(
                GenerationRunModel.id == generation_run_id,
                GenerationRunModel.curriculum_version_id == curriculum_version_id,
            )
            .with_for_update()
        )
        if locked_id != generation_run_id:
            raise ValidationGenerationNotFoundError(generation_run_id)

    async def selected_scope_is_valid(
        self,
        curriculum_version_id: UUID,
        *,
        unit_ids: tuple[UUID, ...],
        lesson_ids: tuple[UUID, ...],
    ) -> bool:
        units = tuple(
            await self._session.scalars(
                select(CurriculumUnitModel.id).where(
                    CurriculumUnitModel.curriculum_version_id == curriculum_version_id,
                    CurriculumUnitModel.id.in_(unit_ids),
                )
            )
        )
        if set(units) != set(unit_ids):
            return False
        lessons = (
            await self._session.execute(
                select(CurriculumLessonModel.id, CurriculumLessonModel.unit_id).where(
                    CurriculumLessonModel.curriculum_version_id == curriculum_version_id,
                    CurriculumLessonModel.id.in_(lesson_ids),
                )
            )
        ).all()
        return {item[0] for item in lessons} == set(lesson_ids) and all(
            item[1] in unit_ids for item in lessons
        )

    async def get_for_generation_pipeline(
        self,
        generation_run_id: UUID,
        pipeline_version: str,
    ) -> ValidationRunModel | None:
        record = await self._session.scalar(
            select(ValidationRunModel).where(
                ValidationRunModel.generation_run_id == generation_run_id,
                ValidationRunModel.pipeline_version == pipeline_version,
            )
        )
        return record if isinstance(record, ValidationRunModel) else None

    async def list_duplicate_references(
        self,
        curriculum_version_id: UUID,
        *,
        exclude_generation_run_id: UUID,
        limit: int = MAX_VALIDATION_DUPLICATE_REFERENCES,
    ) -> tuple[DuplicateReferenceRecord, ...]:
        historical_rows = (
            await self._session.execute(
                select(
                    HistoricalQuestionModel.id,
                    HistoricalQuestionModel.text,
                    HistoricalQuestionModel.version,
                    HistoricalQuestionModel.source_document_id,
                    HistoricalQuestionModel.page_number,
                )
                .where(
                    HistoricalQuestionModel.curriculum_version_id == curriculum_version_id,
                    HistoricalQuestionModel.review_state == ReviewState.REVIEWED,
                )
                .order_by(HistoricalQuestionModel.id)
                .limit(limit)
            )
        ).all()
        references = [
            DuplicateReferenceRecord(
                reference_kind="historical",
                record_id=row[0],
                text=row[1],
                record_version=f"historical-question.v{row[2]}",
                source_document_id=row[3],
                source_page_number=row[4],
            )
            for row in historical_rows
        ]
        remaining = limit - len(references)
        if remaining <= 0:
            return tuple(references)

        generated_rows = (
            await self._session.execute(
                select(GenerationRunModel, ValidationRunModel)
                .join(
                    ValidationRunModel,
                    ValidationRunModel.generation_run_id == GenerationRunModel.id,
                )
                .where(
                    GenerationRunModel.curriculum_version_id == curriculum_version_id,
                    GenerationRunModel.id != exclude_generation_run_id,
                    ValidationRunModel.overall_status.in_(("pass", "warn")),
                )
                .order_by(
                    GenerationRunModel.id,
                    ValidationRunModel.created_at.desc(),
                    ValidationRunModel.id.desc(),
                )
                .limit(min(MAX_VALIDATION_DUPLICATE_REFERENCES * 4, remaining * 4))
            )
        ).all()
        seen_generation_ids: set[UUID] = set()
        for generation, validation in generated_rows:
            if generation.id in seen_generation_ids or len(references) >= limit:
                continue
            candidate = generation.candidate
            stem = candidate.get("stem") if isinstance(candidate, dict) else None
            if not isinstance(stem, str) or not stem.strip():
                continue
            seen_generation_ids.add(generation.id)
            references.append(
                DuplicateReferenceRecord(
                    reference_kind="generated",
                    record_id=generation.id,
                    text=stem,
                    record_version=f"generation-result:{validation.generation_result_fingerprint}",
                    validation_run_id=validation.id,
                    generation_run_id=generation.id,
                    report_fingerprint=validation.report_fingerprint,
                    pipeline_version=validation.pipeline_version,
                )
            )
        return tuple(references)

    async def store_report(
        self,
        run_values: dict[str, object],
        findings: tuple[ValidationFindingModel, ...],
    ) -> StoredValidationReport:
        run = await self._session.scalar(
            insert(ValidationRunModel)
            .values(**run_values)
            .on_conflict_do_nothing()
            .returning(ValidationRunModel)
        )
        if run is None:
            existing = await self.get_for_generation_pipeline(
                UUID(str(run_values["generation_run_id"])),
                str(run_values["pipeline_version"]),
            )
            if existing is None:
                raise RuntimeError("idempotent validation report winner was not found")
            return StoredValidationReport(run=existing, created=False)

        for finding in findings:
            self._session.add(finding)
            await self._session.flush()
        return StoredValidationReport(run=run, created=True)

    async def get_run(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
    ) -> ValidationRunModel:
        run = await self._session.scalar(
            select(ValidationRunModel).where(
                ValidationRunModel.id == validation_run_id,
                ValidationRunModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if run is None:
            raise ValidationRunNotFoundError(validation_run_id)
        return run

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ValidationRunModel, ...]:
        return tuple(
            await self._session.scalars(
                select(ValidationRunModel)
                .where(ValidationRunModel.curriculum_version_id == curriculum_version_id)
                .order_by(ValidationRunModel.created_at.desc(), ValidationRunModel.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    async def list_findings(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ValidationFindingModel, ...]:
        await self.get_run(curriculum_version_id, validation_run_id)
        return tuple(
            await self._session.scalars(
                select(ValidationFindingModel)
                .where(ValidationFindingModel.validation_run_id == validation_run_id)
                .order_by(ValidationFindingModel.ordinal)
                .offset(offset)
                .limit(limit)
            )
        )
