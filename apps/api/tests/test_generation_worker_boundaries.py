import asyncio
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings
from exam_guru_api.generation.domain import GenerationRequest, GenerationResult
from exam_guru_api.generation.models import GenerationRunModel
from exam_guru_api.generation.ports import ProviderError, ProviderFailureCode
from exam_guru_api.generation.repository import GenerationClaimRecord
from exam_guru_api.generation.run_service import (
    GenerationRegisteredConfigMismatchError,
    GenerationWorkerService,
    _CompletedAttempt,
    _generation_request,
    _persisted_context,
    _RecordingProvider,
    _RunResultCache,
    _slot_snapshot,
)
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    RegisteredGenerationConfig,
    create_generation_runtime,
)
from exam_guru_api.generation.service import (
    GenerationBudgetUsage,
    GenerationOrchestrationError,
    GenerationRetryExhaustedError,
)
from tests.test_generation_provider import accounting, question, request
from tests.test_generation_repository import job_model, run_model
from tests.test_generation_run_service import (
    FakeGenerationRepository,
    build_service,
    create,
)

JOB_ID = UUID(int=970_001)
RUN_ID = UUID(int=970_002)


class CrashingProvider:
    def generate(self, generation_request: GenerationRequest) -> object:
        del generation_request
        raise RuntimeError("raw provider exception with secret")


def test_worker_result_cache_preserves_the_first_stored_winner() -> None:
    generation_request = request()
    result = GenerationResult(generation_request, question(), accounting())
    cache = _RunResultCache()

    assert cache.get(generation_request) is None
    assert cache.put_if_absent(generation_request, result) is result
    assert cache.put_if_absent(generation_request, result) is result
    assert cache.get(generation_request) is result


def test_slot_snapshot_checks_later_slots_before_reporting_not_found() -> None:
    expected = {"slot_id": "wanted", "marks": 2}

    assert (
        _slot_snapshot(
            {"slots": [{"slot_id": "first", "marks": 1}, expected]},
            "wanted",
        )
        == expected
    )


def test_recording_provider_normalizes_unexpected_provider_exception() -> None:
    recorder = _RecordingProvider(CrashingProvider())  # type: ignore[arg-type]

    with pytest.raises(ProviderError) as raised:
        recorder.generate(request())

    assert raised.value.code is ProviderFailureCode.UNAVAILABLE
    assert len(recorder.completed) == 1
    completed = recorder.completed[0]
    assert completed.failure_code == "provider_unavailable"
    assert completed.accounting is None
    assert completed.candidate is None
    assert "secret" not in str(raised.value)


class ClaimSession:
    def __init__(self, values: tuple[object | None, ...]) -> None:
        self.values = list(values)
        self.rollbacks = 0
        self.added: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.values.pop(0)

    async def rollback(self) -> None:
        self.rollbacks += 1

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None


def test_worker_claim_rolls_back_when_job_does_not_match_claimed_run() -> None:
    async def exercise() -> None:
        session = ClaimSession((run_model(), None))
        worker = GenerationWorkerService(
            cast(AsyncSession, session),
            create_generation_runtime(Settings(environment="test")),
            sleep=lambda _: None,
        )

        assert await worker._claim(JOB_ID, RUN_ID) is None
        assert session.rollbacks == 1

    asyncio.run(exercise())


def test_worker_rejects_unregistered_configuration_and_rolls_back_lost_lease() -> None:
    async def exercise() -> None:
        runtime = create_generation_runtime(Settings(environment="test"))
        changed = run_model()
        worker = GenerationWorkerService(
            cast(AsyncSession, ClaimSession(())),
            runtime,
            sleep=lambda _: None,
        )
        with pytest.raises(
            GenerationRegisteredConfigMismatchError,
            match="no longer registered",
        ):
            worker._matching_config(changed)

        class InactiveRepository:
            async def lock_active_completion(
                self,
                run_id: UUID,
                job_id: UUID,
            ) -> None:
                del run_id, job_id

        session = ClaimSession(())
        worker = GenerationWorkerService(
            cast(AsyncSession, session),
            runtime,
            sleep=lambda _: None,
        )
        worker._repository = cast(object, InactiveRepository())  # type: ignore[assignment]
        assert (
            await worker._complete(
                changed,
                JOB_ID,
                (),
                result=None,
                failure_code="generation_internal_error",
            )
            is False
        )
        assert session.rollbacks == 1

    asyncio.run(exercise())


def test_worker_rolls_back_when_terminal_completion_cas_is_lost() -> None:
    async def exercise() -> None:
        run = run_model()
        job = job_model()

        class ActiveRepository:
            async def lock_active_completion(
                self,
                run_id: UUID,
                job_id: UUID,
            ) -> GenerationClaimRecord:
                assert (run_id, job_id) == (run.id, job.id)
                return GenerationClaimRecord(run=run, job=job)

        session = ClaimSession((None, job))
        worker = GenerationWorkerService(
            cast(AsyncSession, session),
            create_generation_runtime(Settings(environment="test")),
            sleep=lambda _: None,
        )
        worker._repository = cast(object, ActiveRepository())  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="completion lost"):
            await worker._complete(
                run,
                job.id,
                (),
                result=None,
                failure_code="generation_internal_error",
            )
        assert session.rollbacks == 1

    asyncio.run(exercise())


def test_worker_maps_retry_exhaustion_orchestration_and_internal_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import exam_guru_api.generation.run_service as module

    generation_request = request()
    failures: tuple[Exception, ...] = (
        GenerationRetryExhaustedError(
            attempts=3,
            last_failure=ProviderError(
                ProviderFailureCode.TIMEOUT,
                identity=generation_request.identity,
            ),
            usage=GenerationBudgetUsage(attempt_count=3),
        ),
        GenerationOrchestrationError("contract"),
        GenerationRegisteredConfigMismatchError("config drift"),
        RuntimeError("raw internal secret"),
    )
    expected = (
        "provider_retries_exhausted",
        "generation_orchestration_failed",
        "generation_config_unavailable",
        "generation_internal_error",
    )
    outcomes = iter(failures)

    class RaisingGenerationService:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def generate(self) -> object:
            raise next(outcomes)

    class BoundaryWorker(GenerationWorkerService):
        def __init__(
            self,
            runtime: GenerationRuntimeRegistry,
            config: RegisteredGenerationConfig,
        ) -> None:
            super().__init__(cast(AsyncSession, object()), runtime, sleep=lambda _: None)
            self._config = config
            self.failure_codes: list[str | None] = []

        async def _claim(self, job_id: UUID, run_id: UUID) -> GenerationRunModel | None:
            del job_id, run_id
            return run_model()

        def _matching_config(self, run: GenerationRunModel) -> RegisteredGenerationConfig:
            del run
            return self._config

        async def _complete(
            self,
            run: GenerationRunModel,
            job_id: UUID,
            completed: tuple[_CompletedAttempt, ...],
            *,
            result: GenerationResult | None,
            failure_code: str | None,
        ) -> bool:
            del run, job_id, completed, result
            self.failure_codes.append(failure_code)
            return True

    runtime = create_generation_runtime(Settings(environment="test"))
    config = runtime.active_config
    monkeypatch.setattr(module, "GenerationService", RaisingGenerationService)
    monkeypatch.setattr(module, "_generation_request", lambda run, active: generation_request)

    async def exercise() -> None:
        for failure_code in expected:
            worker = BoundaryWorker(runtime, config)
            assert await worker.process(JOB_ID, RUN_ID) is True
            assert worker.failure_codes == [failure_code]

    asyncio.run(exercise())


def test_persisted_request_and_context_snapshot_corruption_is_rejected() -> None:
    async def valid_run() -> GenerationRunModel:
        repository = FakeGenerationRepository()
        service, _, _ = build_service(repository)
        return (await create(service, key="worker-snapshot-boundaries")).run

    run = asyncio.run(valid_run())
    config = create_generation_runtime(Settings(environment="test")).active_config
    original_version = run.blueprint_version
    run.blueprint_version = "bp_" + "0" * 24
    with pytest.raises(ValueError, match="blueprint version"):
        _generation_request(run, config)
    run.blueprint_version = original_version
    run.slot_id = "missing-slot"
    with pytest.raises(ValueError, match="generation slot"):
        _generation_request(run, config)

    malformed_contexts = (
        {"trust": "trusted", "items": []},
        {"trust": "untrusted_data", "items": [None]},
        {"trust": "untrusted_data", "items": [{"context_id": "item"}]},
    )
    for snapshot in malformed_contexts:
        with pytest.raises(ValueError, match="persisted context"):
            _persisted_context(cast(dict[str, object], snapshot))
