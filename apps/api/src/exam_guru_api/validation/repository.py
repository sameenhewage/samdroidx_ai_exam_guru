from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.models import HistoricalQuestionModel

from .models import (
    MAX_VALIDATION_DUPLICATE_REFERENCES,
    ValidationFindingModel,
    ValidationRunModel,
)


@dataclass(frozen=True, slots=True)
class ValidationGenerationRecord:
    run: GenerationRunModel
    attempt: GenerationAttemptModel | None


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
                select(GenerationRunModel, GenerationAttemptModel)
                .outerjoin(
                    GenerationAttemptModel,
                    GenerationAttemptModel.id == GenerationRunModel.result_attempt_id,
                )
                .where(
                    GenerationRunModel.id == generation_run_id,
                    GenerationRunModel.curriculum_version_id == curriculum_version_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise ValidationGenerationNotFoundError(generation_run_id)
        return ValidationGenerationRecord(run=row[0], attempt=row[1])

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
