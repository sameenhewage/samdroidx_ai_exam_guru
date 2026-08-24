import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.analytics.models import AnalyticsRunModel
from exam_guru_api.analytics.repository import (
    AnalyticsRunFingerprintConflictError,
    AnalyticsRunNotFoundError,
    AnalyticsRunWrite,
    SqlAlchemyAnalyticsRepository,
)

FINGERPRINT = "sha256:" + "a" * 64


class ScriptedSession:
    def __init__(self, *responses: AnalyticsRunModel | None) -> None:
        self.responses = list(responses)

    async def scalar(self, statement: object) -> AnalyticsRunModel | None:
        del statement
        return self.responses.pop(0)


class ScalarRows:
    def __init__(self, rows: tuple[AnalyticsRunModel, ...]) -> None:
        self.rows = rows

    def __iter__(self) -> Iterator[AnalyticsRunModel]:
        return iter(self.rows)


class ListSession:
    def __init__(self, rows: tuple[AnalyticsRunModel, ...]) -> None:
        self.rows = rows

    async def scalars(self, statement: object) -> ScalarRows:
        del statement
        return ScalarRows(self.rows)


def run_write() -> AnalyticsRunWrite:
    return AnalyticsRunWrite(
        id=UUID(int=1),
        curriculum_version_id=UUID(int=2),
        run_fingerprint=FINGERPRINT,
        config_fingerprint=FINGERPRINT,
        input_fingerprint=FINGERPRINT,
        source_fingerprint=FINGERPRINT,
        result_fingerprint=FINGERPRINT,
        statistics_algorithm_version="statistics-v1",
        practice_priority_algorithm_version="priority-v1",
        baseline_algorithm_version="baseline-v1",
        backtest_algorithm_version="backtest-v1",
        config={"minimum_training_years": 2},
        input_snapshot={"observation_ids": []},
        source_versions=[],
        data_quality={"included_count": 0},
        result={"backtest": {}},
        compute_duration_ms=1,
        created_by=UUID(int=3),
    )


def run_model(run: AnalyticsRunWrite | None = None) -> AnalyticsRunModel:
    value = run or run_write()
    return AnalyticsRunModel(
        id=value.id,
        curriculum_version_id=value.curriculum_version_id,
        run_fingerprint=value.run_fingerprint,
        config_fingerprint=value.config_fingerprint,
        input_fingerprint=value.input_fingerprint,
        source_fingerprint=value.source_fingerprint,
        result_fingerprint=value.result_fingerprint,
        statistics_algorithm_version=value.statistics_algorithm_version,
        practice_priority_algorithm_version=value.practice_priority_algorithm_version,
        baseline_algorithm_version=value.baseline_algorithm_version,
        backtest_algorithm_version=value.backtest_algorithm_version,
        config=value.config,
        input_snapshot=value.input_snapshot,
        source_versions=value.source_versions,
        data_quality=value.data_quality,
        result=value.result,
        compute_duration_ms=value.compute_duration_ms,
        created_by=value.created_by,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_repository_returns_created_and_idempotent_runs_and_lists_records() -> None:
    async def exercise() -> None:
        write = run_write()
        model = run_model(write)
        created_repository = SqlAlchemyAnalyticsRepository(
            cast(AsyncSession, ScriptedSession(model))
        )
        duplicate_repository = SqlAlchemyAnalyticsRepository(
            cast(AsyncSession, ScriptedSession(None, model))
        )
        list_repository = SqlAlchemyAnalyticsRepository(cast(AsyncSession, ListSession((model,))))
        get_repository = SqlAlchemyAnalyticsRepository(cast(AsyncSession, ScriptedSession(model)))

        created = await created_repository.store_run(write)
        duplicate = await duplicate_repository.store_run(write)
        listed = await list_repository.list_runs(write.curriculum_version_id, limit=10, offset=0)
        fetched = await get_repository.get_run(write.curriculum_version_id, write.id)

        assert created.created is True
        assert duplicate.created is False
        assert created.record == duplicate.record == listed[0] == fetched

    asyncio.run(exercise())


def test_repository_reports_missing_run_and_fingerprint_conflicts_stably() -> None:
    async def exercise() -> None:
        write = run_write()
        missing_repository = SqlAlchemyAnalyticsRepository(
            cast(AsyncSession, ScriptedSession(None))
        )
        with pytest.raises(AnalyticsRunNotFoundError) as missing:
            await missing_repository.get_run(write.curriculum_version_id, write.id)
        assert missing.value.run_id == write.id

        absent_conflict_repository = SqlAlchemyAnalyticsRepository(
            cast(AsyncSession, ScriptedSession(None, None))
        )
        with pytest.raises(AnalyticsRunFingerprintConflictError) as absent_conflict:
            await absent_conflict_repository.store_run(write)
        assert absent_conflict.value.run_fingerprint == FINGERPRINT

        conflicting_model = run_model()
        conflicting_model.result_fingerprint = "sha256:" + "b" * 64
        changed_conflict_repository = SqlAlchemyAnalyticsRepository(
            cast(AsyncSession, ScriptedSession(None, conflicting_model))
        )
        with pytest.raises(AnalyticsRunFingerprintConflictError):
            await changed_conflict_repository.store_run(write)

    asyncio.run(exercise())
