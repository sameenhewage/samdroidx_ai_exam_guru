import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.validation import _execute_validation_operation
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.repository import (
    ValidationGenerationNotFoundError,
    ValidationRunNotFoundError,
)
from exam_guru_api.validation.schemas import (
    ValidationFindingResponse,
    ValidationRunCreateRequest,
    ValidationRunResponse,
    ValidationRunSummaryResponse,
)
from exam_guru_api.validation.service import (
    ValidationCreationResult,
    ValidationCurriculumNotFoundError,
    ValidationGenerationIntegrityError,
    ValidationGenerationNotSucceededError,
    ValidationIdempotencyConflictError,
    ValidationPipelineVersionConflictError,
    ValidationResourceLimitError,
)

CURRICULUM_ID = UUID(int=990_001)
RESOURCE_ID = UUID(int=990_002)
GENERATION_ID = UUID(int=990_003)


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            ValidationGenerationNotFoundError(GENERATION_ID),
            404,
            "validation_generation_run_not_found",
        ),
        (ValidationRunNotFoundError(RESOURCE_ID), 404, "validation_run_not_found"),
        (
            ValidationCurriculumNotFoundError(CURRICULUM_ID),
            404,
            "validation_curriculum_not_found",
        ),
        (
            ValidationGenerationNotSucceededError(GENERATION_ID),
            409,
            "validation_generation_not_succeeded",
        ),
        (
            ValidationGenerationIntegrityError(GENERATION_ID),
            409,
            "validation_generation_integrity_invalid",
        ),
        (
            ValidationPipelineVersionConflictError(RESOURCE_ID),
            409,
            "validation_pipeline_version_conflict",
        ),
        (
            ValidationIdempotencyConflictError(RESOURCE_ID),
            409,
            "validation_idempotency_conflict",
        ),
        (
            ValidationResourceLimitError("bounded"),
            422,
            "validation_resource_limit_exceeded",
        ),
    ],
)
def test_validation_errors_have_stable_http_contracts(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def exercise() -> None:
        async def fail() -> None:
            raise error

        with pytest.raises(HTTPException) as raised:
            await _execute_validation_operation(cast(AsyncSession, object()), fail)
        assert raised.value.status_code == status_code
        assert cast(dict[str, object], raised.value.detail) == {"code": code}

    asyncio.run(exercise())


def test_validation_integrity_rolls_back_and_success_passes_through() -> None:
    async def exercise() -> None:
        session = RollbackSession()

        async def fail() -> None:
            raise IntegrityError("INSERT", {}, RuntimeError("constraint"))

        with pytest.raises(HTTPException) as raised:
            await _execute_validation_operation(cast(AsyncSession, session), fail)
        assert raised.value.status_code == 409
        assert cast(dict[str, object], raised.value.detail) == {
            "code": "validation_persistence_conflict"
        }
        assert session.rolled_back

        async def succeed() -> str:
            return "ok"

        assert await _execute_validation_operation(cast(AsyncSession, session), succeed) == "ok"

    asyncio.run(exercise())


def test_validation_route_functions_serialize_commands_and_bounded_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.api.routes.validation import (
        create_validation_run,
        get_validation_run,
        list_validation_findings,
        list_validation_runs,
    )

    async def exercise() -> None:
        run = cast(ValidationRunModel, object())
        finding = cast(ValidationFindingModel, object())
        run_response = cast(ValidationRunResponse, object())
        summary_response = cast(ValidationRunSummaryResponse, object())
        finding_response = cast(ValidationFindingResponse, object())
        principal = Principal(RESOURCE_ID, frozenset({AdminRole.ADMIN}))

        class FakeService:
            async def create(self, *args: object, **kwargs: object) -> ValidationCreationResult:
                return ValidationCreationResult(run, deduplicated=False)

            async def list_runs(
                self,
                *args: object,
                **kwargs: object,
            ) -> tuple[ValidationRunModel, ...]:
                return (run,)

            async def get_run(self, *args: object, **kwargs: object) -> ValidationRunModel:
                return run

            async def list_findings(
                self,
                *args: object,
                **kwargs: object,
            ) -> tuple[ValidationFindingModel, ...]:
                return (finding,)

        monkeypatch.setattr(
            "exam_guru_api.api.routes.validation.ValidationRunService",
            lambda *args: FakeService(),
        )
        monkeypatch.setattr(
            ValidationRunResponse,
            "from_model",
            classmethod(lambda cls, value, **kwargs: run_response),
        )
        monkeypatch.setattr(
            ValidationRunSummaryResponse,
            "from_model",
            classmethod(lambda cls, value: summary_response),
        )
        monkeypatch.setattr(
            ValidationFindingResponse,
            "from_model",
            classmethod(lambda cls, value: finding_response),
        )
        request = ValidationRunCreateRequest(generation_run_id=GENERATION_ID)
        session = cast(AsyncSession, object())
        pipeline = cast(ValidationPipeline, object())

        created = await create_validation_run(
            CURRICULUM_ID,
            request,
            principal,
            session,
            pipeline,
        )
        listed = await list_validation_runs(
            CURRICULUM_ID,
            principal,
            session,
            pipeline,
            limit=10,
            offset=0,
        )
        fetched = await get_validation_run(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            session,
            pipeline,
        )
        findings = await list_validation_findings(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            session,
            pipeline,
            limit=10,
            offset=0,
        )

        assert created is run_response
        assert listed == [summary_response]
        assert fetched is run_response
        assert findings == [finding_response]

    asyncio.run(exercise())
