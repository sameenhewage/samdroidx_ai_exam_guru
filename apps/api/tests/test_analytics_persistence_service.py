import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from fractions import Fraction
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.analytics.domain import SyllabusSkill
from exam_guru_api.analytics.repository import (
    AnalyticsQuestionCandidate,
    AnalyticsRunRecord,
    AnalyticsRunWrite,
    RepositoryAnalyticsRunResult,
    SqlAlchemyAnalyticsRepository,
)
from exam_guru_api.analytics.service import (
    AnalyticsCurriculumNotFoundError,
    AnalyticsInsufficientHistoryError,
    AnalyticsRecordLimitError,
    AnalyticsRunConfig,
    AnalyticsRunService,
    AnalyticsSyllabusEmptyError,
    AnalyticsYearLimitError,
    fingerprint_payload,
)
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.knowledge.domain import DifficultyLabel, QuestionType, ReviewState

CURRICULUM_ID = UUID(int=30_001)
COMPETENCY_ID = UUID(int=30_002)
SKILL_ID = UUID(int=30_003)
ACTOR_ID = UUID(int=30_004)


def syllabus() -> tuple[SyllabusSkill, ...]:
    return (SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_ID, "Numbers"),)


def candidate(identifier: int, year: int) -> AnalyticsQuestionCandidate:
    return AnalyticsQuestionCandidate(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        year=year,
        paper_code=f"P{year}",
        question_number=str(identifier),
        competency_id=COMPETENCY_ID,
        skill_id=SKILL_ID,
        question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty_label=DifficultyLabel.MEDIUM,
        difficulty_confidence=0.9,
        difficulty_source="reviewer_confirmed",
        marks=2,
        source_document_id=UUID(int=40_000 + identifier),
        page_number=1,
        source_block_id=UUID(int=50_000 + identifier),
        review_state=ReviewState.REVIEWED,
        source_status=ExtractionStatus.TRUSTED,
        source_checksum_sha256=f"{identifier:064x}",
    )


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1


class FakeAnalyticsRepository:
    def __init__(
        self,
        *,
        curriculum_found: bool = True,
        syllabus_records: tuple[SyllabusSkill, ...] | None = None,
        candidates: tuple[AnalyticsQuestionCandidate, ...] = (),
        created_sequence: Sequence[bool] = (True,),
    ) -> None:
        self.curriculum_found = curriculum_found
        self.syllabus_records = syllabus() if syllabus_records is None else syllabus_records
        self.candidates = candidates
        self.created_sequence = list(created_sequence)
        self.stored: AnalyticsRunRecord | None = None

    async def curriculum_exists(self, curriculum_version_id: UUID) -> bool:
        assert curriculum_version_id == CURRICULUM_ID
        return self.curriculum_found

    async def list_syllabus(self, curriculum_version_id: UUID) -> tuple[SyllabusSkill, ...]:
        assert curriculum_version_id == CURRICULUM_ID
        return self.syllabus_records

    async def list_question_candidates(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
    ) -> tuple[AnalyticsQuestionCandidate, ...]:
        assert curriculum_version_id == CURRICULUM_ID
        assert limit > 0
        return self.candidates

    async def store_run(self, run: AnalyticsRunWrite) -> RepositoryAnalyticsRunResult:
        created = self.created_sequence.pop(0)
        if self.stored is None:
            self.stored = AnalyticsRunRecord(
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
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
        return RepositoryAnalyticsRunResult(self.stored, created=created)

    async def get_run(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
    ) -> AnalyticsRunRecord:
        assert curriculum_version_id == CURRICULUM_ID
        assert self.stored is not None
        assert run_id == self.stored.id
        return self.stored

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[AnalyticsRunRecord, ...]:
        assert curriculum_version_id == CURRICULUM_ID
        assert (limit, offset) == (10, 0)
        assert self.stored is not None
        return (self.stored,)


def service_with(
    repository: FakeAnalyticsRepository,
) -> tuple[AnalyticsRunService, FakeSession]:
    session = FakeSession()
    service = AnalyticsRunService(cast(AsyncSession, session))
    service._repository = cast(SqlAlchemyAnalyticsRepository, repository)
    return service, session


def test_service_persists_audits_and_reuses_identical_bounded_runs() -> None:
    async def exercise() -> None:
        repository = FakeAnalyticsRepository(
            candidates=tuple(
                candidate(100 + offset, year) for offset, year in enumerate(range(2018, 2022))
            ),
            created_sequence=(True, False),
        )
        service, session = service_with(repository)
        config = AnalyticsRunConfig(
            minimum_training_years=2,
            top_k_skills=1,
            meaningful_improvement=Fraction(1, 100),
        )

        created = await service.create_run(CURRICULUM_ID, config, actor_id=ACTOR_ID)
        duplicate = await service.create_run(CURRICULUM_ID, config, actor_id=ACTOR_ID)
        fetched = await service.get_run(CURRICULUM_ID, created.record.id)
        listed = await service.list_runs(CURRICULUM_ID, limit=10, offset=0)

        assert created.deduplicated is False
        assert duplicate.deduplicated is True
        assert duplicate.record.id == created.record.id
        assert fetched == created.record
        assert listed == (created.record,)
        assert session.commit_count == 1
        assert len(session.added) == 1
        audit = cast(AdminAuditEventModel, session.added[0])
        assert audit.action == "analytics.run.created"
        assert audit.payload["included_count"] == 4
        assert audit.payload["window_count"] == 2
        assert created.record.compute_duration_ms >= 0
        assert created.record.run_fingerprint.startswith("sha256:")
        assert created.record.result_fingerprint == fingerprint_payload(created.record.result)

    asyncio.run(exercise())


def test_service_reports_curriculum_syllabus_record_year_and_history_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        missing_service, _ = service_with(FakeAnalyticsRepository(curriculum_found=False))
        with pytest.raises(AnalyticsCurriculumNotFoundError):
            await missing_service.create_run(
                CURRICULUM_ID,
                AnalyticsRunConfig(),
                actor_id=ACTOR_ID,
            )

        empty_service, _ = service_with(FakeAnalyticsRepository(syllabus_records=()))
        with pytest.raises(AnalyticsSyllabusEmptyError):
            await empty_service.create_run(
                CURRICULUM_ID,
                AnalyticsRunConfig(),
                actor_id=ACTOR_ID,
            )

        with monkeypatch.context() as context:
            context.setattr("exam_guru_api.analytics.service.MAX_SYNC_ANALYTICS_RECORDS", 1)
            record_service, _ = service_with(
                FakeAnalyticsRepository(candidates=(candidate(1, 2020), candidate(2, 2021)))
            )
            with pytest.raises(AnalyticsRecordLimitError) as record_raised:
                await record_service.create_run(
                    CURRICULUM_ID,
                    AnalyticsRunConfig(),
                    actor_id=ACTOR_ID,
                )
            assert record_raised.value.maximum == 1

        with monkeypatch.context() as context:
            context.setattr("exam_guru_api.analytics.service.MAX_SYNC_ANALYTICS_YEARS", 1)
            year_service, _ = service_with(
                FakeAnalyticsRepository(candidates=(candidate(3, 2020), candidate(4, 2021)))
            )
            with pytest.raises(AnalyticsYearLimitError) as year_raised:
                await year_service.create_run(
                    CURRICULUM_ID,
                    AnalyticsRunConfig(),
                    actor_id=ACTOR_ID,
                )
            assert year_raised.value.maximum == 1
            assert year_raised.value.actual == 2

        history_service, _ = service_with(
            FakeAnalyticsRepository(candidates=(candidate(5, 2020), candidate(6, 2021)))
        )
        with pytest.raises(AnalyticsInsufficientHistoryError) as history_raised:
            await history_service.create_run(
                CURRICULUM_ID,
                AnalyticsRunConfig(minimum_training_years=2),
                actor_id=ACTOR_ID,
            )
        assert history_raised.value.required_year_count == 3
        assert history_raised.value.available_years == (2020, 2021)
        assert history_raised.value.data_quality.included_count == 2

    asyncio.run(exercise())
