import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.analytics.backtest import BacktestContractError, BacktestViolation
from exam_guru_api.analytics.domain import AnalyticsContractError, AnalyticsViolation
from exam_guru_api.analytics.repository import (
    AnalyticsRunFingerprintConflictError,
    AnalyticsRunNotFoundError,
    AnalyticsRunRecord,
)
from exam_guru_api.analytics.schemas import (
    AnalyticsRunRequest,
    AnalyticsRunResponse,
    AnalyticsRunSummaryResponse,
)
from exam_guru_api.analytics.service import (
    AnalyticsCurriculumNotFoundError,
    AnalyticsDataQuality,
    AnalyticsInsufficientHistoryError,
    AnalyticsRecordLimitError,
    AnalyticsRunCreationResult,
    AnalyticsSyllabusEmptyError,
    AnalyticsYearLimitError,
)
from exam_guru_api.api.routes.analytics import (
    _execute_analytics_operation,
    create_analytics_run,
    get_analytics_run,
    list_analytics_runs,
)
from exam_guru_api.auth.domain import AdminRole, Principal

CURRICULUM_ID = UUID(int=70_001)
RUN_ID = UUID(int=70_002)
ACTOR_ID = UUID(int=70_003)


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (AnalyticsCurriculumNotFoundError(CURRICULUM_ID), 404, "curriculum_version_not_found"),
        (AnalyticsRunNotFoundError(RUN_ID), 404, "analytics_run_not_found"),
        (AnalyticsSyllabusEmptyError(CURRICULUM_ID), 422, "analytics_syllabus_empty"),
        (AnalyticsRecordLimitError(5_000), 422, "analytics_record_limit_exceeded"),
        (AnalyticsYearLimitError(50, 51), 422, "analytics_year_limit_exceeded"),
        (
            AnalyticsInsufficientHistoryError(
                minimum_training_years=2,
                available_years=(2020,),
                data_quality=AnalyticsDataQuality(1, 0, 1, ()),
            ),
            422,
            "analytics_insufficient_history",
        ),
        (
            AnalyticsRunFingerprintConflictError("sha256:" + "a" * 64),
            409,
            "analytics_run_fingerprint_conflict",
        ),
        (
            AnalyticsContractError(AnalyticsViolation.NO_OBSERVATIONS),
            422,
            "analytics_input_invalid",
        ),
        (
            BacktestContractError(BacktestViolation.INVALID_CONFIG),
            422,
            "analytics_input_invalid",
        ),
    ],
)
def test_analytics_errors_have_stable_http_contracts(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def exercise() -> None:
        async def fail() -> None:
            raise error

        with pytest.raises(HTTPException) as raised:
            await _execute_analytics_operation(cast(AsyncSession, object()), fail)

        assert raised.value.status_code == status_code
        detail = cast(dict[str, object], raised.value.detail)
        assert detail["code"] == code
        if isinstance(error, AnalyticsRecordLimitError):
            assert detail["maximum"] == 5_000
        if isinstance(error, AnalyticsYearLimitError):
            assert detail == {
                "code": code,
                "maximum": 50,
                "actual": 51,
            }
        if isinstance(error, AnalyticsInsufficientHistoryError):
            assert detail["required_year_count"] == 3
            quality = cast(dict[str, object], detail["data_quality"])
            assert quality["excluded_count"] == 1

    asyncio.run(exercise())


def test_integrity_errors_roll_back_and_success_values_pass_through() -> None:
    async def exercise() -> None:
        session = RollbackSession()

        async def fail() -> None:
            raise IntegrityError("statement", {}, RuntimeError("conflict"))

        with pytest.raises(HTTPException) as raised:
            await _execute_analytics_operation(cast(AsyncSession, session), fail)
        assert raised.value.status_code == 409
        assert cast(dict[str, object], raised.value.detail) == {
            "code": "analytics_persistence_conflict"
        }
        assert session.rolled_back

        async def succeed() -> str:
            return "ok"

        assert await _execute_analytics_operation(cast(AsyncSession, session), succeed) == "ok"

    asyncio.run(exercise())


def test_route_functions_serialize_create_list_and_get_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        record = cast(AnalyticsRunRecord, object())
        principal = Principal(ACTOR_ID, frozenset({AdminRole.ADMIN}))
        full_response = cast(AnalyticsRunResponse, object())
        summary_response = cast(AnalyticsRunSummaryResponse, object())

        deduplicated_results = iter((False, True))

        class FakeService:
            async def create_run(
                self, *args: object, **kwargs: object
            ) -> AnalyticsRunCreationResult:
                return AnalyticsRunCreationResult(
                    record,
                    deduplicated=next(deduplicated_results),
                )

            async def list_runs(
                self, *args: object, **kwargs: object
            ) -> tuple[AnalyticsRunRecord, ...]:
                return (record,)

            async def get_run(self, *args: object, **kwargs: object) -> AnalyticsRunRecord:
                return record

        monkeypatch.setattr(
            "exam_guru_api.api.routes.analytics.AnalyticsRunService",
            lambda session: FakeService(),
        )
        monkeypatch.setattr(
            AnalyticsRunResponse,
            "from_record",
            classmethod(lambda cls, value, **kwargs: full_response),
        )
        monkeypatch.setattr(
            AnalyticsRunSummaryResponse,
            "from_record",
            classmethod(lambda cls, value: summary_response),
        )
        created_response = Response()
        duplicate_response = Response()

        created = await create_analytics_run(
            CURRICULUM_ID,
            AnalyticsRunRequest(),
            created_response,
            principal,
            cast(AsyncSession, object()),
        )
        duplicate = await create_analytics_run(
            CURRICULUM_ID,
            AnalyticsRunRequest(),
            duplicate_response,
            principal,
            cast(AsyncSession, object()),
        )
        listed = await list_analytics_runs(
            CURRICULUM_ID,
            principal,
            cast(AsyncSession, object()),
            limit=10,
            offset=0,
        )
        fetched = await get_analytics_run(
            CURRICULUM_ID,
            RUN_ID,
            principal,
            cast(AsyncSession, object()),
        )

        assert created_response.status_code == 201
        assert duplicate_response.status_code == 200
        assert created is full_response
        assert duplicate is full_response
        assert listed == [summary_response]
        assert fetched is full_response

    asyncio.run(exercise())
