from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.domain import (
    TaxonomyLevel,
    TaxonomyReviewState,
)
from exam_guru_api.curriculum.models import CurriculumVersionModel, TaxonomyNodeModel
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge.domain import DifficultyLabel, QuestionType, ReviewState
from exam_guru_api.knowledge.models import HistoricalQuestionModel

from .domain import SyllabusSkill
from .models import AnalyticsRunModel


class AnalyticsRunNotFoundError(LookupError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"analytics run not found: {run_id}")


class AnalyticsRunFingerprintConflictError(RuntimeError):
    def __init__(self, run_fingerprint: str) -> None:
        self.run_fingerprint = run_fingerprint
        super().__init__(f"analytics run fingerprint conflict: {run_fingerprint}")


@dataclass(frozen=True, slots=True)
class AnalyticsQuestionCandidate:
    id: UUID
    curriculum_version_id: UUID
    year: int
    paper_code: str
    question_number: str
    competency_id: UUID | None
    skill_id: UUID | None
    question_type: QuestionType
    difficulty_label: DifficultyLabel | None
    difficulty_confidence: float | None
    difficulty_source: str | None
    marks: int
    source_document_id: UUID
    page_number: int
    source_block_id: UUID | None
    review_state: ReviewState
    source_status: ExtractionStatus | None
    source_checksum_sha256: str | None


@dataclass(frozen=True, slots=True)
class AnalyticsRunWrite:
    id: UUID
    curriculum_version_id: UUID
    run_fingerprint: str
    config_fingerprint: str
    input_fingerprint: str
    source_fingerprint: str
    result_fingerprint: str
    statistics_algorithm_version: str
    practice_priority_algorithm_version: str
    baseline_algorithm_version: str
    backtest_algorithm_version: str
    config: dict[str, object]
    input_snapshot: dict[str, object]
    source_versions: list[dict[str, object]]
    data_quality: dict[str, object]
    result: dict[str, object]
    compute_duration_ms: int
    created_by: UUID


@dataclass(frozen=True, slots=True)
class AnalyticsRunRecord:
    id: UUID
    curriculum_version_id: UUID
    run_fingerprint: str
    config_fingerprint: str
    input_fingerprint: str
    source_fingerprint: str
    result_fingerprint: str
    statistics_algorithm_version: str
    practice_priority_algorithm_version: str
    baseline_algorithm_version: str
    backtest_algorithm_version: str
    config: dict[str, object]
    input_snapshot: dict[str, object]
    source_versions: list[dict[str, object]]
    data_quality: dict[str, object]
    result: dict[str, object]
    compute_duration_ms: int
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RepositoryAnalyticsRunResult:
    record: AnalyticsRunRecord
    created: bool


class SqlAlchemyAnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def curriculum_exists(self, curriculum_version_id: UUID) -> bool:
        return await self._session.get(CurriculumVersionModel, curriculum_version_id) is not None

    async def list_syllabus(self, curriculum_version_id: UUID) -> tuple[SyllabusSkill, ...]:
        nodes = tuple(
            await self._session.scalars(
                select(TaxonomyNodeModel)
                .where(
                    TaxonomyNodeModel.curriculum_version_id == curriculum_version_id,
                    TaxonomyNodeModel.level == TaxonomyLevel.SKILL,
                    TaxonomyNodeModel.active.is_(True),
                    TaxonomyNodeModel.review_state == TaxonomyReviewState.REVIEWED,
                )
                .order_by(
                    TaxonomyNodeModel.parent_id,
                    TaxonomyNodeModel.id,
                )
            )
        )
        return tuple(
            SyllabusSkill(
                curriculum_version_id=node.curriculum_version_id,
                competency_id=cast(UUID, node.parent_id),
                skill_id=node.id,
                title=node.title,
                balance_weight=1,
            )
            for node in nodes
        )

    async def list_question_candidates(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
    ) -> tuple[AnalyticsQuestionCandidate, ...]:
        rows = (
            await self._session.execute(
                select(
                    HistoricalQuestionModel.id,
                    HistoricalQuestionModel.curriculum_version_id,
                    HistoricalQuestionModel.year,
                    HistoricalQuestionModel.paper_code,
                    HistoricalQuestionModel.question_number,
                    HistoricalQuestionModel.competency_id,
                    HistoricalQuestionModel.skill_id,
                    HistoricalQuestionModel.question_type,
                    HistoricalQuestionModel.difficulty_label,
                    HistoricalQuestionModel.difficulty_confidence,
                    HistoricalQuestionModel.difficulty_source,
                    HistoricalQuestionModel.marks,
                    HistoricalQuestionModel.source_document_id,
                    HistoricalQuestionModel.page_number,
                    HistoricalQuestionModel.source_block_id,
                    HistoricalQuestionModel.review_state,
                    SourceDocumentModel.extraction_status,
                    SourceDocumentModel.checksum_sha256,
                )
                .join(
                    SourceDocumentModel,
                    SourceDocumentModel.id == HistoricalQuestionModel.source_document_id,
                )
                .where(
                    HistoricalQuestionModel.curriculum_version_id == curriculum_version_id,
                )
                .order_by(HistoricalQuestionModel.id)
                .limit(limit)
            )
        ).all()
        return tuple(AnalyticsQuestionCandidate(*row._tuple()) for row in rows)

    async def store_run(self, run: AnalyticsRunWrite) -> RepositoryAnalyticsRunResult:
        model = await self._session.scalar(
            insert(AnalyticsRunModel)
            .values(
                id=run.id,
                curriculum_version_id=run.curriculum_version_id,
                run_fingerprint=run.run_fingerprint,
                config_fingerprint=run.config_fingerprint,
                input_fingerprint=run.input_fingerprint,
                source_fingerprint=run.source_fingerprint,
                result_fingerprint=run.result_fingerprint,
                statistics_algorithm_version=run.statistics_algorithm_version,
                practice_priority_algorithm_version=run.practice_priority_algorithm_version,
                baseline_algorithm_version=run.baseline_algorithm_version,
                backtest_algorithm_version=run.backtest_algorithm_version,
                config=run.config,
                input_snapshot=run.input_snapshot,
                source_versions=run.source_versions,
                data_quality=run.data_quality,
                result=run.result,
                compute_duration_ms=run.compute_duration_ms,
                created_by=run.created_by,
            )
            .on_conflict_do_nothing(constraint="uq_analytics_runs_run_fingerprint")
            .returning(AnalyticsRunModel)
        )
        if model is not None:
            return RepositoryAnalyticsRunResult(self._to_record(model), created=True)

        model = await self._session.scalar(
            select(AnalyticsRunModel).where(
                AnalyticsRunModel.run_fingerprint == run.run_fingerprint
            )
        )
        if model is None or not self._same_run(model, run):
            raise AnalyticsRunFingerprintConflictError(run.run_fingerprint)
        return RepositoryAnalyticsRunResult(self._to_record(model), created=False)

    async def get_run(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
    ) -> AnalyticsRunRecord:
        model = await self._session.scalar(
            select(AnalyticsRunModel).where(
                AnalyticsRunModel.id == run_id,
                AnalyticsRunModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if model is None:
            raise AnalyticsRunNotFoundError(run_id)
        return self._to_record(model)

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[AnalyticsRunRecord, ...]:
        models = tuple(
            await self._session.scalars(
                select(AnalyticsRunModel)
                .where(AnalyticsRunModel.curriculum_version_id == curriculum_version_id)
                .order_by(AnalyticsRunModel.created_at.desc(), AnalyticsRunModel.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return tuple(self._to_record(model) for model in models)

    @staticmethod
    def _same_run(model: AnalyticsRunModel, run: AnalyticsRunWrite) -> bool:
        return (
            model.id == run.id
            and model.curriculum_version_id == run.curriculum_version_id
            and model.config_fingerprint == run.config_fingerprint
            and model.input_fingerprint == run.input_fingerprint
            and model.source_fingerprint == run.source_fingerprint
            and model.result_fingerprint == run.result_fingerprint
            and model.statistics_algorithm_version == run.statistics_algorithm_version
            and model.practice_priority_algorithm_version == run.practice_priority_algorithm_version
            and model.baseline_algorithm_version == run.baseline_algorithm_version
            and model.backtest_algorithm_version == run.backtest_algorithm_version
            and model.config == run.config
            and model.input_snapshot == run.input_snapshot
            and model.source_versions == run.source_versions
            and model.data_quality == run.data_quality
            and model.result == run.result
        )

    @staticmethod
    def _to_record(model: AnalyticsRunModel) -> AnalyticsRunRecord:
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
