import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.generation import _execute_generation_operation
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.blueprints.serialization import BlueprintSnapshotError
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.models import (
    GenerationAttemptModel,
    GenerationJobModel,
    GenerationRunModel,
)
from exam_guru_api.generation.repository import (
    GenerationJobNotFoundError,
    GenerationPersistenceConflictError,
    GenerationRunNotFoundError,
)
from exam_guru_api.generation.run_service import (
    GenerationBlueprintNotFoundError,
    GenerationBlueprintScopeMismatchError,
    GenerationContextCrossCurriculumError,
    GenerationContextLimitError,
    GenerationContextNotFoundError,
    GenerationContextNotReviewedError,
    GenerationContextScopeInactiveError,
    GenerationContextSourceUntrustedError,
    GenerationContextTaxonomyMismatchError,
    GenerationCreationResult,
    GenerationCurriculumInactiveError,
    GenerationCurriculumNotFoundError,
    GenerationIdempotencyConflictError,
    GenerationQueueUnavailableError,
    GenerationRetryLimitExceededError,
    GenerationRetryStateError,
    GenerationSlotNotFoundError,
)
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    GenerationRuntimeUnavailableError,
)
from exam_guru_api.generation.schemas import (
    GenerationAttemptResponse,
    GenerationJobResponse,
    GenerationRunCreateRequest,
    GenerationRunResponse,
    GenerationRunSummaryResponse,
)

CURRICULUM_ID = UUID(int=940_001)
RESOURCE_ID = UUID(int=940_002)


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (GenerationCurriculumNotFoundError(CURRICULUM_ID), 404, "generation_curriculum_not_found"),
        (GenerationBlueprintNotFoundError(RESOURCE_ID), 404, "generation_blueprint_not_found"),
        (GenerationRunNotFoundError(RESOURCE_ID), 404, "generation_run_not_found"),
        (GenerationJobNotFoundError(RESOURCE_ID), 404, "generation_job_not_found"),
        (GenerationCurriculumInactiveError(CURRICULUM_ID), 409, "generation_curriculum_inactive"),
        (GenerationIdempotencyConflictError("collision"), 409, "generation_idempotency_conflict"),
        (GenerationRetryStateError(RESOURCE_ID), 409, "generation_retry_state_invalid"),
        (
            GenerationRetryLimitExceededError(RESOURCE_ID),
            409,
            "generation_retry_limit_exceeded",
        ),
        (GenerationSlotNotFoundError("slot"), 422, "generation_slot_not_found"),
        (GenerationContextNotFoundError(RESOURCE_ID), 422, "generation_context_not_found"),
        (
            GenerationContextCrossCurriculumError(RESOURCE_ID),
            422,
            "generation_context_cross_curriculum",
        ),
        (GenerationContextNotReviewedError(RESOURCE_ID), 422, "generation_context_not_reviewed"),
        (
            GenerationContextSourceUntrustedError(RESOURCE_ID),
            422,
            "generation_context_source_untrusted",
        ),
        (
            GenerationContextScopeInactiveError(RESOURCE_ID),
            422,
            "generation_context_scope_inactive",
        ),
        (
            GenerationContextTaxonomyMismatchError(RESOURCE_ID),
            422,
            "generation_context_taxonomy_mismatch",
        ),
        (GenerationContextLimitError("oversized"), 422, "generation_context_limit_exceeded"),
        (
            GenerationBlueprintScopeMismatchError(RESOURCE_ID),
            409,
            "generation_blueprint_snapshot_invalid",
        ),
        (
            BlueprintSnapshotError("blueprint", "corrupt"),
            409,
            "generation_blueprint_snapshot_invalid",
        ),
        (GenerationRuntimeUnavailableError("missing"), 503, "generation_runtime_unavailable"),
        (GenerationQueueUnavailableError(), 503, "generation_queue_unavailable"),
    ],
)
def test_generation_errors_have_stable_http_contracts(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def exercise() -> None:
        async def fail() -> None:
            raise error

        with pytest.raises(HTTPException) as raised:
            await _execute_generation_operation(cast(AsyncSession, object()), fail)
        assert raised.value.status_code == status_code
        assert cast(dict[str, object], raised.value.detail) == {"code": code}

    asyncio.run(exercise())


def test_generation_integrity_rolls_back_and_success_passes_through() -> None:
    async def exercise() -> None:
        session = RollbackSession()

        for error in (
            IntegrityError("INSERT", {}, RuntimeError("constraint")),
            GenerationPersistenceConflictError("fork"),
        ):
            session.rolled_back = False

            async def fail(active_error: Exception = error) -> None:
                raise active_error

            with pytest.raises(HTTPException) as raised:
                await _execute_generation_operation(cast(AsyncSession, session), fail)
            assert raised.value.status_code == 409
            assert cast(dict[str, object], raised.value.detail) == {
                "code": "generation_persistence_conflict"
            }
            assert session.rolled_back

        async def succeed() -> str:
            return "ok"

        assert await _execute_generation_operation(cast(AsyncSession, session), succeed) == "ok"

    asyncio.run(exercise())


def test_generation_route_functions_serialize_commands_and_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.api.routes.generation import (
        create_generation_run,
        get_generation_job,
        get_generation_run,
        list_generation_attempts,
        list_generation_runs,
        retry_generation_run,
    )

    async def exercise() -> None:
        run = cast(GenerationRunModel, object())
        job = cast(GenerationJobModel, object())
        attempt = cast(GenerationAttemptModel, object())
        job_response = cast(GenerationJobResponse, object())
        run_response = cast(GenerationRunResponse, object())
        summary_response = cast(GenerationRunSummaryResponse, object())
        attempt_response = cast(GenerationAttemptResponse, object())
        principal = Principal(RESOURCE_ID, frozenset({AdminRole.ADMIN}))

        class FakeService:
            async def create(self, *args: object, **kwargs: object) -> GenerationCreationResult:
                return GenerationCreationResult(run, job, deduplicated=False)

            async def retry(self, *args: object, **kwargs: object) -> GenerationCreationResult:
                return GenerationCreationResult(run, job, deduplicated=True)

            async def list_runs(
                self,
                *args: object,
                **kwargs: object,
            ) -> tuple[GenerationRunModel, ...]:
                return (run,)

            async def get_run(self, *args: object, **kwargs: object) -> GenerationRunModel:
                return run

            async def list_attempts(
                self,
                *args: object,
                **kwargs: object,
            ) -> tuple[GenerationAttemptModel, ...]:
                return (attempt,)

            async def get_job(self, *args: object, **kwargs: object) -> GenerationJobModel:
                return job

        monkeypatch.setattr(
            "exam_guru_api.api.routes.generation.GenerationRunService",
            lambda *args: FakeService(),
        )
        monkeypatch.setattr(
            GenerationJobResponse,
            "from_model",
            classmethod(lambda cls, value, **kwargs: job_response),
        )
        monkeypatch.setattr(
            GenerationRunResponse,
            "from_model",
            classmethod(lambda cls, value: run_response),
        )
        monkeypatch.setattr(
            GenerationRunSummaryResponse,
            "from_model",
            classmethod(lambda cls, value: summary_response),
        )
        monkeypatch.setattr(
            GenerationAttemptResponse,
            "from_model",
            classmethod(lambda cls, value: attempt_response),
        )
        request = GenerationRunCreateRequest(
            paper_blueprint_id=RESOURCE_ID,
            slot_id="slot-1",
            knowledge_chunk_ids=(UUID(int=940_003),),
        )
        session = cast(AsyncSession, object())
        runtime = cast(GenerationRuntimeRegistry, object())
        dispatcher = cast(GenerationDispatcher, object())

        created = await create_generation_run(
            CURRICULUM_ID,
            request,
            "create-key",
            principal,
            session,
            runtime,
            dispatcher,
        )
        retried = await retry_generation_run(
            CURRICULUM_ID,
            RESOURCE_ID,
            "retry-key",
            principal,
            session,
            runtime,
            dispatcher,
        )
        listed = await list_generation_runs(
            CURRICULUM_ID,
            principal,
            session,
            runtime,
            dispatcher,
            limit=10,
            offset=0,
        )
        fetched = await get_generation_run(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            session,
            runtime,
            dispatcher,
        )
        attempts = await list_generation_attempts(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            session,
            runtime,
            dispatcher,
            limit=3,
            offset=0,
        )
        fetched_job = await get_generation_job(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            session,
            runtime,
            dispatcher,
        )

        assert created is retried is fetched_job is job_response
        assert listed == [summary_response]
        assert fetched is run_response
        assert attempts == [attempt_response]

    asyncio.run(exercise())
