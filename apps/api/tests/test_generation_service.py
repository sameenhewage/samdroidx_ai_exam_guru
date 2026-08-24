from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import Literal, cast
from uuid import UUID

import pytest

import exam_guru_api.generation as generation_contract
from exam_guru_api.generation.domain import (
    CandidateDisposition,
    GeneratedQuestion,
    GenerationAccounting,
    GenerationIdentity,
    GenerationRequest,
    GenerationResult,
)
from exam_guru_api.generation.ports import (
    GenerationProvider,
    ProviderError,
    ProviderFailure,
    ProviderFailureCode,
)
from exam_guru_api.generation.service import (
    MAX_CUMULATIVE_COST_MICROUSD,
    MAX_CUMULATIVE_INPUT_TOKENS,
    MAX_CUMULATIVE_OUTPUT_TOKENS,
    MAX_RETRY_BACKOFF_MS,
    CanonicalGenerationRequestFactory,
    GenerationBudgetDimension,
    GenerationBudgetExceededError,
    GenerationOrchestrationContractError,
    GenerationResultCache,
    GenerationRetry,
    GenerationRetryExhaustedError,
    GenerationService,
    GenerationServiceConfig,
    RetryScheduler,
)
from tests.test_generation_provider import question, request


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    code: ProviderFailureCode
    accounting: GenerationAccounting | None = None
    retry_after_ms: int | None = None
    identity_mode: Literal["current", "missing", "mismatched"] = "current"


class AccountedProviderTestError(ProviderError):
    def __init__(
        self,
        code: ProviderFailureCode,
        *,
        identity: GenerationIdentity,
        accounting: GenerationAccounting,
        retry_after_ms: int | None = None,
    ) -> None:
        self.accounting = accounting
        super().__init__(code, identity=identity, retry_after_ms=retry_after_ms)


class ScriptedProvider:
    def __init__(
        self,
        outcomes: tuple[FailureOutcome | GenerationAccounting, ...],
        *,
        generated_question: GeneratedQuestion | None = None,
    ) -> None:
        self._outcomes: Iterator[FailureOutcome | GenerationAccounting] = iter(outcomes)
        self._question = generated_question or question()
        self.requests: list[GenerationRequest] = []
        self.failures: list[ProviderError] = []

    def generate(self, generation_request: GenerationRequest) -> GenerationResult:
        self.requests.append(generation_request)
        outcome = next(self._outcomes)
        if isinstance(outcome, GenerationAccounting):
            return GenerationResult(
                request=generation_request,
                question=self._question,
                accounting=outcome,
            )

        identity = self._failure_identity(generation_request, outcome.identity_mode)
        if outcome.accounting is None:
            failure = ProviderFailure(
                outcome.code,
                identity=identity,
                retry_after_ms=outcome.retry_after_ms,
            )
        else:
            if identity is None:
                raise AssertionError("accounted test failures require an identity")
            failure = AccountedProviderTestError(
                outcome.code,
                identity=identity,
                accounting=outcome.accounting,
                retry_after_ms=outcome.retry_after_ms,
            )
        self.failures.append(failure)
        raise failure

    @staticmethod
    def _failure_identity(
        generation_request: GenerationRequest,
        mode: Literal["current", "missing", "mismatched"],
    ) -> GenerationIdentity | None:
        if mode == "missing":
            return None
        if mode == "mismatched":
            return replace(generation_request.identity, generation_id=UUID(int=8_888))
        return generation_request.identity


class ReturningProvider:
    def __init__(self, result_factory: Callable[[GenerationRequest], object]) -> None:
        self._result_factory = result_factory
        self.requests: list[GenerationRequest] = []

    def generate(self, generation_request: GenerationRequest) -> GenerationResult:
        self.requests.append(generation_request)
        return cast(GenerationResult, self._result_factory(generation_request))


class MemoryResultCache:
    def __init__(self) -> None:
        self.entries: dict[GenerationRequest, GenerationResult] = {}
        self.get_requests: list[GenerationRequest] = []
        self.put_requests: list[tuple[GenerationRequest, GenerationResult]] = []
        self.winner: GenerationResult | None = None

    def get(self, canonical_request: GenerationRequest) -> GenerationResult | None:
        self.get_requests.append(canonical_request)
        return self.entries.get(canonical_request)

    def put_if_absent(
        self,
        canonical_request: GenerationRequest,
        result: GenerationResult,
    ) -> GenerationResult:
        self.put_requests.append((canonical_request, result))
        if self.winner is not None:
            return self.winner
        return self.entries.setdefault(canonical_request, result)


class PoisonedCache(MemoryResultCache):
    def __init__(self, cached: object) -> None:
        super().__init__()
        self._cached = cached

    def get(self, canonical_request: GenerationRequest) -> GenerationResult | None:
        self.get_requests.append(canonical_request)
        return cast(GenerationResult | None, self._cached)


class RecordingRetryScheduler:
    def __init__(self) -> None:
        self.retries: list[GenerationRetry] = []

    def schedule(self, retry: GenerationRetry) -> None:
        self.retries.append(retry)


class RecordingRequestFactory:
    def __init__(self, canonical_request: object) -> None:
        self._canonical_request = canonical_request
        self.calls = 0

    def __call__(self) -> GenerationRequest:
        self.calls += 1
        return cast(GenerationRequest, self._canonical_request)


class AttemptIdSequence:
    def __init__(self, *identifiers: object) -> None:
        self._identifiers = iter(identifiers or (UUID(int=901), UUID(int=902)))

    def __call__(self) -> UUID:
        return cast(UUID, next(self._identifiers))


def attempt_accounting(
    input_tokens: int,
    output_tokens: int,
    cost_microusd: int,
) -> GenerationAccounting:
    return GenerationAccounting(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_microusd=cost_microusd,
        latency_ms=5,
    )


def service_config(**changes: int) -> GenerationServiceConfig:
    values = {
        "max_attempts": 3,
        "max_total_input_tokens": 10_000,
        "max_total_output_tokens": 2_000,
        "max_total_cost_microusd": 100_000,
        "initial_backoff_ms": 25,
        "max_backoff_ms": 1_000,
    }
    values.update(changes)
    return GenerationServiceConfig(**values)


def build_service(
    provider: GenerationProvider,
    *,
    canonical_request: object | None = None,
    cache: GenerationResultCache | None = None,
    scheduler: RetryScheduler | None = None,
    config: GenerationServiceConfig | None = None,
    attempt_ids: Callable[[], UUID] | None = None,
) -> tuple[
    GenerationService,
    RecordingRequestFactory,
    GenerationResultCache,
    RetryScheduler,
]:
    factory = RecordingRequestFactory(canonical_request or request())
    active_cache = cache or MemoryResultCache()
    active_scheduler = scheduler or RecordingRetryScheduler()
    return (
        GenerationService(
            request_factory=factory,
            provider=provider,
            result_cache=active_cache,
            retry_scheduler=active_scheduler,
            config=config or service_config(),
            attempt_id_factory=attempt_ids or AttemptIdSequence(),
        ),
        factory,
        active_cache,
        active_scheduler,
    )


def route(generation_request: GenerationRequest) -> tuple[str, str, str, str]:
    versions = generation_request.versions
    return (
        versions.provider,
        versions.provider_version,
        versions.model,
        versions.model_version,
    )


def test_success_is_validation_required_and_same_canonical_request_is_cached() -> None:
    provider = ScriptedProvider((attempt_accounting(120, 30, 400),))
    cache = MemoryResultCache()
    scheduler = RecordingRetryScheduler()
    service, factory, _, _ = build_service(provider, cache=cache, scheduler=scheduler)

    first = service.generate()
    second = service.generate()

    assert second is first
    assert first.disposition is CandidateDisposition.REQUIRES_VALIDATION
    assert provider.requests == [first.request]
    assert factory.calls == 2
    assert cache.get_requests == [first.request, first.request]
    assert cache.put_requests == [(first.request, first)]
    assert scheduler.retries == []


def test_only_exact_same_canonical_request_hits_the_result_cache() -> None:
    cache = MemoryResultCache()
    first_provider = ScriptedProvider((attempt_accounting(100, 20, 100),))
    first_service, _, _, _ = build_service(first_provider, cache=cache)
    first = first_service.generate()
    changed_request = request(text="A changed reviewed and grounded context.")
    second_provider = ScriptedProvider((attempt_accounting(110, 21, 101),))
    second_service, _, _, _ = build_service(
        second_provider,
        canonical_request=changed_request,
        cache=cache,
    )

    second = second_service.generate()

    assert first.request.identity.idempotency_key == second.request.identity.idempotency_key
    assert first.request.context != second.request.context
    assert len(cache.entries) == 2
    assert second_provider.requests == [changed_request]


def test_retry_lineage_route_backoff_and_failed_usage_are_strictly_cumulative() -> None:
    provider = ScriptedProvider(
        (
            FailureOutcome(
                ProviderFailureCode.RATE_LIMITED,
                attempt_accounting(100, 10, 200),
                retry_after_ms=90,
            ),
            FailureOutcome(
                ProviderFailureCode.UNAVAILABLE,
                attempt_accounting(120, 12, 250),
            ),
            attempt_accounting(140, 14, 300),
        )
    )
    scheduler = RecordingRetryScheduler()
    service, factory, _, _ = build_service(
        provider,
        scheduler=scheduler,
        config=service_config(max_backoff_ms=60),
        attempt_ids=AttemptIdSequence(UUID(int=911), UUID(int=912)),
    )

    result = service.generate()

    first, second, third = provider.requests
    assert factory.calls == 1
    assert result.request is third
    assert result.disposition is CandidateDisposition.REQUIRES_VALIDATION
    assert [item.identity.attempt_number for item in provider.requests] == [1, 2, 3]
    assert second.identity.retry_of_attempt_id == first.identity.attempt_id
    assert third.identity.retry_of_attempt_id == second.identity.attempt_id
    assert len({item.identity.attempt_id for item in provider.requests}) == 3
    assert all(
        item.identity.generation_id == first.identity.generation_id for item in provider.requests
    )
    assert all(
        item.identity.idempotency_key == first.identity.idempotency_key
        for item in provider.requests
    )
    assert all(replace(item, identity=first.identity) == first for item in provider.requests)
    assert [route(item) for item in provider.requests] == [route(first)] * 3
    assert [event.delay_ms for event in scheduler.retries] == [60, 50]
    assert [event.failure_code for event in scheduler.retries] == [
        ProviderFailureCode.RATE_LIMITED,
        ProviderFailureCode.UNAVAILABLE,
    ]
    assert scheduler.retries[0].failed_identity == first.identity
    assert scheduler.retries[0].next_identity == second.identity
    assert scheduler.retries[1].failed_identity == second.identity
    assert scheduler.retries[1].next_identity == third.identity
    assert scheduler.retries[0].usage.input_tokens == 100
    assert scheduler.retries[1].usage.input_tokens == 220
    assert scheduler.retries[1].usage.output_tokens == 22
    assert scheduler.retries[1].usage.cost_microusd == 450


@pytest.mark.parametrize(
    "code",
    [
        ProviderFailureCode.AUTHENTICATION,
        ProviderFailureCode.PERMISSION_DENIED,
        ProviderFailureCode.INVALID_REQUEST,
        ProviderFailureCode.CONTEXT_LIMIT_EXCEEDED,
        ProviderFailureCode.CONTENT_FILTERED,
        ProviderFailureCode.INVALID_RESPONSE,
        ProviderFailureCode.IDEMPOTENCY_CONFLICT,
    ],
)
def test_non_retryable_provider_codes_are_never_retried(code: ProviderFailureCode) -> None:
    provider = ScriptedProvider((FailureOutcome(code),))
    scheduler = RecordingRetryScheduler()
    service, _, _, _ = build_service(provider, scheduler=scheduler)

    with pytest.raises(ProviderError) as raised:
        service.generate()

    assert raised.value.code is code
    assert provider.requests == [request()]
    assert scheduler.retries == []


@pytest.mark.parametrize("identity_mode", ["missing", "mismatched"])
def test_retryable_failure_requires_the_current_attempt_identity(
    identity_mode: Literal["missing", "mismatched"],
) -> None:
    provider = ScriptedProvider(
        (FailureOutcome(ProviderFailureCode.TIMEOUT, identity_mode=identity_mode),)
    )
    scheduler = RecordingRetryScheduler()
    service, _, _, _ = build_service(provider, scheduler=scheduler)

    with pytest.raises(GenerationOrchestrationContractError, match="failure identity"):
        service.generate()

    assert len(provider.requests) == 1
    assert scheduler.retries == []


def test_retry_exhaustion_is_typed_and_preserves_accounted_failure_usage() -> None:
    first_usage = attempt_accounting(80, 8, 100)
    second_usage = attempt_accounting(90, 9, 110)
    provider = ScriptedProvider(
        (
            FailureOutcome(ProviderFailureCode.TIMEOUT, first_usage),
            FailureOutcome(ProviderFailureCode.TIMEOUT, second_usage),
        )
    )
    scheduler = RecordingRetryScheduler()
    service, _, cache, _ = build_service(
        provider,
        scheduler=scheduler,
        config=service_config(max_attempts=2),
    )

    with pytest.raises(GenerationRetryExhaustedError) as raised:
        service.generate()

    error = raised.value
    assert error.attempts == 2
    assert error.last_failure is provider.failures[-1]
    assert error.last_failure.code is ProviderFailureCode.TIMEOUT
    assert error.usage.attempt_count == 2
    assert error.usage.input_tokens == 170
    assert error.usage.output_tokens == 17
    assert error.usage.total_tokens == 187
    assert error.usage.cost_microusd == 210
    assert error.__cause__ is provider.failures[-1]
    assert len(scheduler.retries) == 1
    assert cast(MemoryResultCache, cache).put_requests == []


def test_failed_attempt_without_accounting_is_still_counted_but_adds_no_usage() -> None:
    provider = ScriptedProvider(
        (
            FailureOutcome(ProviderFailureCode.TIMEOUT),
            FailureOutcome(ProviderFailureCode.UNAVAILABLE),
        )
    )
    service, _, _, _ = build_service(provider, config=service_config(max_attempts=2))

    with pytest.raises(GenerationRetryExhaustedError) as raised:
        service.generate()

    assert raised.value.usage.attempt_count == 2
    assert raised.value.usage.input_tokens == 0
    assert raised.value.usage.output_tokens == 0
    assert raised.value.usage.total_tokens == 0
    assert raised.value.usage.cost_microusd == 0


@pytest.mark.parametrize(
    ("config_change", "dimension", "consumed", "limit"),
    [
        ({"max_total_input_tokens": 109}, GenerationBudgetDimension.INPUT_TOKENS, 110, 109),
        ({"max_total_output_tokens": 49}, GenerationBudgetDimension.OUTPUT_TOKENS, 50, 49),
        ({"max_total_cost_microusd": 249}, GenerationBudgetDimension.COST_MICROUSD, 250, 249),
    ],
)
def test_budget_counts_failed_and_successful_attempt_usage_before_returning(
    config_change: dict[str, int],
    dimension: GenerationBudgetDimension,
    consumed: int,
    limit: int,
) -> None:
    provider = ScriptedProvider(
        (
            FailureOutcome(
                ProviderFailureCode.TIMEOUT,
                attempt_accounting(40, 20, 100),
            ),
            attempt_accounting(70, 30, 150),
        )
    )
    service, _, cache, _ = build_service(
        provider,
        config=service_config(**config_change),
    )

    with pytest.raises(GenerationBudgetExceededError) as raised:
        service.generate()

    error = raised.value
    assert error.dimension is dimension
    assert error.consumed == consumed
    assert error.limit == limit
    assert error.usage.attempt_count == 2
    assert len(provider.requests) == 2
    assert cast(MemoryResultCache, cache).put_requests == []


def test_failed_attempt_budget_breach_stops_before_scheduling_another_call() -> None:
    provider = ScriptedProvider(
        (
            FailureOutcome(
                ProviderFailureCode.TIMEOUT,
                attempt_accounting(40, 20, 100),
            ),
        )
    )
    scheduler = RecordingRetryScheduler()
    service, _, _, _ = build_service(
        provider,
        scheduler=scheduler,
        config=service_config(max_total_input_tokens=39),
    )

    with pytest.raises(GenerationBudgetExceededError) as raised:
        service.generate()

    assert raised.value.identity == provider.requests[0].identity
    assert raised.value.usage.attempt_count == 1
    assert len(provider.requests) == 1
    assert scheduler.retries == []


def test_malformed_optional_failure_accounting_is_rejected_without_retry() -> None:
    class MalformedAccountingError(ProviderError):
        accounting = "not-accounting"

    failure = MalformedAccountingError(ProviderFailureCode.TIMEOUT, identity=request().identity)

    def fail(_: GenerationRequest) -> object:
        raise failure

    provider = ReturningProvider(fail)
    service, _, _, _ = build_service(provider)

    with pytest.raises(GenerationOrchestrationContractError, match="failure accounting"):
        service.generate()

    assert len(provider.requests) == 1


@pytest.mark.parametrize("identity_mode", ["missing", "mismatched"])
def test_accounted_non_retryable_failure_must_also_identify_its_attempt(
    identity_mode: Literal["missing", "mismatched"],
) -> None:
    class ForeignAccountedProviderTestError(ProviderError):
        accounting = attempt_accounting(1, 1, 1)

    canonical = request()
    identity = (
        None
        if identity_mode == "missing"
        else replace(canonical.identity, generation_id=UUID(int=7_777))
    )
    failure = ForeignAccountedProviderTestError(
        ProviderFailureCode.INVALID_RESPONSE,
        identity=identity,
    )

    def fail(_: GenerationRequest) -> object:
        raise failure

    service, _, _, _ = build_service(ReturningProvider(fail), canonical_request=canonical)

    with pytest.raises(GenerationOrchestrationContractError, match="failure identity"):
        service.generate()


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda _current: "not-a-generation-result",
        lambda current: GenerationResult(
            request=replace(
                current,
                identity=replace(current.identity, attempt_id=UUID(int=6_001)),
            ),
            question=question(),
            accounting=attempt_accounting(10, 2, 3),
        ),
    ],
)
def test_provider_must_return_a_result_for_the_exact_current_attempt(
    result_factory: Callable[[GenerationRequest], object],
) -> None:
    provider = ReturningProvider(result_factory)
    service, _, cache, _ = build_service(provider)

    with pytest.raises(GenerationOrchestrationContractError, match="provider result"):
        service.generate()

    assert cast(MemoryResultCache, cache).put_requests == []


def test_provider_result_cannot_escape_the_requires_validation_disposition() -> None:
    def untrusted_result(current: GenerationRequest) -> GenerationResult:
        result = GenerationResult(
            request=current,
            question=question(),
            accounting=attempt_accounting(10, 2, 3),
        )
        object.__setattr__(result, "disposition", "published")
        return result

    provider = ReturningProvider(untrusted_result)
    service, _, cache, _ = build_service(provider)

    with pytest.raises(GenerationOrchestrationContractError, match="validation"):
        service.generate()

    assert cast(MemoryResultCache, cache).put_requests == []


def test_poisoned_cache_is_rejected_before_any_provider_call() -> None:
    canonical = request()
    foreign_result = GenerationResult(
        request=replace(
            canonical,
            identity=GenerationIdentity(
                generation_id=UUID(int=5_001),
                attempt_id=UUID(int=5_002),
                idempotency_key="foreign-cache-entry",
                attempt_number=1,
            ),
        ),
        question=question(),
        accounting=attempt_accounting(10, 2, 3),
    )
    provider = ScriptedProvider((attempt_accounting(20, 4, 6),))
    service, _, _, _ = build_service(
        provider,
        canonical_request=canonical,
        cache=PoisonedCache(foreign_result),
    )

    with pytest.raises(GenerationOrchestrationContractError, match="cached result"):
        service.generate()

    assert provider.requests == []


def test_malformed_cache_value_is_rejected_before_any_provider_call() -> None:
    provider = ScriptedProvider((attempt_accounting(20, 4, 6),))
    service, _, _, _ = build_service(provider, cache=PoisonedCache("poison"))

    with pytest.raises(GenerationOrchestrationContractError, match="cached result"):
        service.generate()

    assert provider.requests == []


def test_cached_result_cannot_escape_the_requires_validation_disposition() -> None:
    canonical = request()
    poisoned = GenerationResult(
        request=canonical,
        question=question(),
        accounting=attempt_accounting(20, 4, 6),
    )
    object.__setattr__(poisoned, "disposition", "published")
    provider = ScriptedProvider((attempt_accounting(20, 4, 6),))
    service, _, _, _ = build_service(
        provider,
        canonical_request=canonical,
        cache=PoisonedCache(poisoned),
    )

    with pytest.raises(GenerationOrchestrationContractError, match="validation"):
        service.generate()

    assert provider.requests == []


def test_atomic_cache_winner_is_returned_and_revalidated() -> None:
    canonical = request()
    winner = GenerationResult(
        request=canonical,
        question=question(),
        accounting=attempt_accounting(30, 5, 7),
    )
    cache = MemoryResultCache()
    cache.winner = winner
    provider = ScriptedProvider((attempt_accounting(40, 6, 8),))
    service, _, _, _ = build_service(
        provider,
        canonical_request=canonical,
        cache=cache,
    )

    result = service.generate()

    assert result is winner
    assert len(provider.requests) == 1
    assert cache.put_requests[0][1].accounting != winner.accounting


def test_cache_winner_must_belong_to_the_same_canonical_request() -> None:
    canonical = request()
    foreign_request = request(text="Different retrieval payload.")
    cache = MemoryResultCache()
    cache.winner = GenerationResult(
        request=foreign_request,
        question=question(),
        accounting=attempt_accounting(30, 5, 7),
    )
    provider = ScriptedProvider((attempt_accounting(40, 6, 8),))
    service, _, _, _ = build_service(provider, canonical_request=canonical, cache=cache)

    with pytest.raises(GenerationOrchestrationContractError, match="cached result"):
        service.generate()


@pytest.mark.parametrize(
    "canonical_request",
    [
        "not-a-request",
        replace(
            request(),
            identity=GenerationIdentity(
                generation_id=UUID(int=201),
                attempt_id=UUID(int=4_002),
                idempotency_key="provider-contract-idempotency",
                attempt_number=2,
                retry_of_attempt_id=UUID(int=4_001),
            ),
        ),
    ],
)
def test_factory_must_return_a_canonical_first_attempt(canonical_request: object) -> None:
    provider = ScriptedProvider((attempt_accounting(20, 4, 6),))
    cache = MemoryResultCache()
    service, factory, _, _ = build_service(
        provider,
        canonical_request=canonical_request,
        cache=cache,
    )

    with pytest.raises(GenerationOrchestrationContractError, match="canonical request"):
        service.generate()

    assert factory.calls == 1
    assert cache.get_requests == []
    assert provider.requests == []


@pytest.mark.parametrize("attempt_id", ["not-a-uuid", UUID(int=202)])
def test_retry_attempt_id_factory_must_return_a_new_uuid(attempt_id: object) -> None:
    provider = ScriptedProvider((FailureOutcome(ProviderFailureCode.TIMEOUT),))
    scheduler = RecordingRetryScheduler()
    service, _, _, _ = build_service(
        provider,
        scheduler=scheduler,
        attempt_ids=AttemptIdSequence(attempt_id),
    )

    with pytest.raises(GenerationOrchestrationContractError, match="attempt id"):
        service.generate()

    assert len(provider.requests) == 1
    assert scheduler.retries == []


@pytest.mark.parametrize(
    "build",
    [
        lambda: service_config(max_attempts=0),
        lambda: service_config(max_attempts=4),
        lambda: service_config(max_attempts=cast(int, True)),
        lambda: service_config(max_total_input_tokens=0),
        lambda: service_config(max_total_input_tokens=MAX_CUMULATIVE_INPUT_TOKENS + 1),
        lambda: service_config(max_total_input_tokens=cast(int, True)),
        lambda: service_config(max_total_output_tokens=0),
        lambda: service_config(max_total_output_tokens=MAX_CUMULATIVE_OUTPUT_TOKENS + 1),
        lambda: service_config(max_total_cost_microusd=0),
        lambda: service_config(max_total_cost_microusd=MAX_CUMULATIVE_COST_MICROUSD + 1),
        lambda: service_config(initial_backoff_ms=-1),
        lambda: service_config(initial_backoff_ms=MAX_RETRY_BACKOFF_MS + 1),
        lambda: service_config(initial_backoff_ms=cast(int, True)),
        lambda: service_config(max_backoff_ms=-1),
        lambda: service_config(max_backoff_ms=MAX_RETRY_BACKOFF_MS + 1),
        lambda: service_config(initial_backoff_ms=20, max_backoff_ms=19),
    ],
)
def test_service_configuration_is_strictly_bounded(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match=r"must|cannot"):
        build()


def test_service_rejects_an_untyped_configuration() -> None:
    with pytest.raises(TypeError, match="config"):
        GenerationService(
            request_factory=RecordingRequestFactory(request()),
            provider=ScriptedProvider((attempt_accounting(1, 1, 1),)),
            result_cache=MemoryResultCache(),
            retry_scheduler=RecordingRetryScheduler(),
            config=cast(GenerationServiceConfig, "config"),
        )


def test_generation_package_exports_the_orchestration_contract() -> None:
    assert generation_contract.GenerationService is GenerationService
    assert generation_contract.GenerationServiceConfig is GenerationServiceConfig
    assert generation_contract.GenerationBudgetExceededError is GenerationBudgetExceededError
    assert generation_contract.GenerationRetryExhaustedError is GenerationRetryExhaustedError
    assert (
        generation_contract.CanonicalGenerationRequestFactory is CanonicalGenerationRequestFactory
    )
