"""Bounded, provider-independent orchestration for one generation request.

The service owns retry identity lineage, aggregate spend limits, and idempotent
result reuse.  It deliberately does not validate, review, persist, or publish a
candidate: every returned ``GenerationResult`` retains the domain's
``REQUIRES_VALIDATION`` disposition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from exam_guru_api.generation.domain import (
    MAX_GENERATION_ATTEMPTS,
    CandidateDisposition,
    GenerationAccounting,
    GenerationIdentity,
    GenerationRequest,
    GenerationResult,
)
from exam_guru_api.generation.ports import (
    GenerationProvider,
    ProviderError,
    ProviderFailureCode,
)

# GenerationAccounting bounds each individual report.  These orchestration
# bounds cover the largest valid sum across the domain's fixed attempt ceiling.
MAX_CUMULATIVE_INPUT_TOKENS = MAX_GENERATION_ATTEMPTS * 10_000_000
MAX_CUMULATIVE_OUTPUT_TOKENS = MAX_GENERATION_ATTEMPTS * 10_000_000
MAX_CUMULATIVE_COST_MICROUSD = MAX_GENERATION_ATTEMPTS * 1_000_000_000_000
MAX_RETRY_BACKOFF_MS = 3_600_000

_MISSING = object()


class CanonicalGenerationRequestFactory(Protocol):
    """Build the immutable first attempt used as the cache and retry root."""

    def __call__(self) -> GenerationRequest: ...


class GenerationResultCache(Protocol):
    """Atomic idempotency port keyed by the complete canonical request.

    ``put_if_absent`` returns the stored winner.  Returning a winner rather than
    ``None`` keeps concurrent callers convergent without making this application
    service own persistence or locking.
    """

    def get(self, canonical_request: GenerationRequest) -> GenerationResult | None: ...

    def put_if_absent(
        self,
        canonical_request: GenerationRequest,
        result: GenerationResult,
    ) -> GenerationResult: ...


class AccountedProviderFailure(Protocol):
    """Optional structural extension for provider failures that consumed usage."""

    @property
    def accounting(self) -> GenerationAccounting | None: ...


class RetryScheduler(Protocol):
    """Injected retry boundary; unit tests can record rather than sleep."""

    def schedule(self, retry: GenerationRetry) -> None: ...


class GenerationOrchestrationError(RuntimeError):
    """Base class for stable generation orchestration failures."""


class GenerationOrchestrationContractError(GenerationOrchestrationError):
    """A factory, provider, cache, or identity dependency broke its contract."""


class GenerationBudgetDimension(StrEnum):
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST_MICROUSD = "cost_microusd"


@dataclass(frozen=True, slots=True)
class GenerationBudgetUsage:
    """Aggregate usage observed for all successful and failed attempts so far."""

    attempt_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_microusd: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(self, accounting: GenerationAccounting | None) -> GenerationBudgetUsage:
        if accounting is None:
            return replace(self, attempt_count=self.attempt_count + 1)
        return GenerationBudgetUsage(
            attempt_count=self.attempt_count + 1,
            input_tokens=self.input_tokens + accounting.input_tokens,
            output_tokens=self.output_tokens + accounting.output_tokens,
            cost_microusd=self.cost_microusd + accounting.cost_microusd,
        )


@dataclass(frozen=True, slots=True)
class GenerationServiceConfig:
    """Explicit hard limits for one synchronous orchestration run."""

    max_attempts: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_total_cost_microusd: int
    initial_backoff_ms: int = 100
    max_backoff_ms: int = MAX_RETRY_BACKOFF_MS

    def __post_init__(self) -> None:
        _require_bounded_integer(
            self.max_attempts,
            "max_attempts",
            minimum=1,
            maximum=MAX_GENERATION_ATTEMPTS,
        )
        _require_bounded_integer(
            self.max_total_input_tokens,
            "max_total_input_tokens",
            minimum=1,
            maximum=MAX_CUMULATIVE_INPUT_TOKENS,
        )
        _require_bounded_integer(
            self.max_total_output_tokens,
            "max_total_output_tokens",
            minimum=1,
            maximum=MAX_CUMULATIVE_OUTPUT_TOKENS,
        )
        _require_bounded_integer(
            self.max_total_cost_microusd,
            "max_total_cost_microusd",
            minimum=1,
            maximum=MAX_CUMULATIVE_COST_MICROUSD,
        )
        _require_bounded_integer(
            self.initial_backoff_ms,
            "initial_backoff_ms",
            minimum=0,
            maximum=MAX_RETRY_BACKOFF_MS,
        )
        _require_bounded_integer(
            self.max_backoff_ms,
            "max_backoff_ms",
            minimum=0,
            maximum=MAX_RETRY_BACKOFF_MS,
        )
        if self.initial_backoff_ms > self.max_backoff_ms:
            raise ValueError("initial_backoff_ms cannot exceed max_backoff_ms")


@dataclass(frozen=True, slots=True)
class GenerationRetry:
    """Safe, bounded retry metadata supplied to an external scheduler/observer."""

    failed_identity: GenerationIdentity
    next_identity: GenerationIdentity
    failure_code: ProviderFailureCode
    delay_ms: int
    usage: GenerationBudgetUsage


class GenerationBudgetExceededError(GenerationOrchestrationError):
    """Raised before another retry or a generated candidate can be returned."""

    def __init__(
        self,
        *,
        dimension: GenerationBudgetDimension,
        consumed: int,
        limit: int,
        usage: GenerationBudgetUsage,
        identity: GenerationIdentity,
    ) -> None:
        self.dimension = dimension
        self.consumed = consumed
        self.limit = limit
        self.usage = usage
        self.identity = identity
        super().__init__(
            f"generation {dimension.value} budget exceeded: consumed {consumed}, limit {limit}"
        )


class GenerationRetryExhaustedError(GenerationOrchestrationError):
    """Raised when the bounded final attempt ends in a retryable provider failure."""

    def __init__(
        self,
        *,
        attempts: int,
        last_failure: ProviderError,
        usage: GenerationBudgetUsage,
    ) -> None:
        self.attempts = attempts
        self.last_failure = last_failure
        self.usage = usage
        super().__init__(
            f"generation retries exhausted after {attempts} attempts ({last_failure.code.value})"
        )


class GenerationService:
    """Generate one unvalidated candidate under fixed route, retry, and spend rules."""

    def __init__(
        self,
        *,
        request_factory: CanonicalGenerationRequestFactory,
        provider: GenerationProvider,
        result_cache: GenerationResultCache,
        retry_scheduler: RetryScheduler,
        config: GenerationServiceConfig,
        attempt_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not isinstance(config, GenerationServiceConfig):
            raise TypeError("config must be GenerationServiceConfig")
        self._request_factory = request_factory
        self._provider = provider
        self._result_cache = result_cache
        self._retry_scheduler = retry_scheduler
        self._config = config
        self._attempt_id_factory = attempt_id_factory

    def generate(self) -> GenerationResult:
        canonical_request = self._request_factory()
        self._require_canonical_request(canonical_request)

        cached = self._result_cache.get(canonical_request)
        if cached is not None:
            return self._require_cached_result(cached, canonical_request)

        usage = GenerationBudgetUsage()
        current_request = canonical_request
        used_attempt_ids = {canonical_request.identity.attempt_id}

        while True:
            try:
                result = self._provider.generate(current_request)
            except ProviderError as failure:
                accounting = self._failure_accounting(failure)
                if failure.retryable or accounting is not None:
                    self._require_failure_identity(failure, current_request)
                usage = usage.record(accounting)
                self._enforce_budget(usage, current_request.identity)

                if not failure.retryable:
                    raise
                if current_request.identity.attempt_number >= self._config.max_attempts:
                    raise GenerationRetryExhaustedError(
                        attempts=current_request.identity.attempt_number,
                        last_failure=failure,
                        usage=usage,
                    ) from failure

                next_request = self._build_retry_request(
                    canonical_request,
                    current_request,
                    used_attempt_ids,
                )
                used_attempt_ids.add(next_request.identity.attempt_id)
                self._retry_scheduler.schedule(
                    GenerationRetry(
                        failed_identity=current_request.identity,
                        next_identity=next_request.identity,
                        failure_code=failure.code,
                        delay_ms=self._retry_delay_ms(failure, current_request.identity),
                        usage=usage,
                    )
                )
                current_request = next_request
                continue

            validated_result = self._require_provider_result(result, current_request)
            usage = usage.record(validated_result.accounting)
            self._enforce_budget(usage, current_request.identity)
            winner = self._result_cache.put_if_absent(canonical_request, validated_result)
            return self._require_cached_result(winner, canonical_request)

    @staticmethod
    def _require_canonical_request(request: object) -> None:
        if not isinstance(request, GenerationRequest):
            raise GenerationOrchestrationContractError(
                "canonical request factory must return GenerationRequest"
            )
        identity = request.identity
        if identity.attempt_number != 1 or identity.retry_of_attempt_id is not None:
            raise GenerationOrchestrationContractError(
                "canonical request must be an unlinked first attempt"
            )

    @staticmethod
    def _failure_accounting(failure: ProviderError) -> GenerationAccounting | None:
        accounting = getattr(failure, "accounting", _MISSING)
        if accounting is _MISSING or accounting is None:
            return None
        if not isinstance(accounting, GenerationAccounting):
            raise GenerationOrchestrationContractError(
                "optional provider failure accounting must be GenerationAccounting"
            )
        return accounting

    @staticmethod
    def _require_failure_identity(
        failure: ProviderError,
        current_request: GenerationRequest,
    ) -> None:
        if failure.identity != current_request.identity:
            raise GenerationOrchestrationContractError(
                "provider failure identity must match the current generation attempt"
            )

    def _build_retry_request(
        self,
        canonical_request: GenerationRequest,
        current_request: GenerationRequest,
        used_attempt_ids: set[UUID],
    ) -> GenerationRequest:
        attempt_id = self._attempt_id_factory()
        if not isinstance(attempt_id, UUID) or attempt_id in used_attempt_ids:
            raise GenerationOrchestrationContractError(
                "retry attempt id factory must return a new UUID"
            )
        identity = GenerationIdentity(
            generation_id=canonical_request.identity.generation_id,
            attempt_id=attempt_id,
            idempotency_key=canonical_request.identity.idempotency_key,
            attempt_number=current_request.identity.attempt_number + 1,
            retry_of_attempt_id=current_request.identity.attempt_id,
        )
        return replace(canonical_request, identity=identity)

    def _retry_delay_ms(
        self,
        failure: ProviderError,
        failed_identity: GenerationIdentity,
    ) -> int:
        exponential_delay: int = self._config.initial_backoff_ms * (
            2 ** (failed_identity.attempt_number - 1)
        )
        requested_delay = 0 if failure.retry_after_ms is None else int(failure.retry_after_ms)
        delay = max(exponential_delay, requested_delay)
        if delay > self._config.max_backoff_ms:
            return self._config.max_backoff_ms
        return delay

    @staticmethod
    def _require_provider_result(
        result: object,
        current_request: GenerationRequest,
    ) -> GenerationResult:
        if not isinstance(result, GenerationResult) or result.request != current_request:
            raise GenerationOrchestrationContractError(
                "provider result must belong to the exact current generation attempt"
            )
        if result.disposition is not CandidateDisposition.REQUIRES_VALIDATION:
            raise GenerationOrchestrationContractError(
                "provider result must require validation and cannot be published"
            )
        return result

    def _require_cached_result(
        self,
        result: object,
        canonical_request: GenerationRequest,
    ) -> GenerationResult:
        if not isinstance(result, GenerationResult):
            raise GenerationOrchestrationContractError("cached result must be a GenerationResult")
        cached_request = result.request
        if (
            not _same_generation_payload(cached_request, canonical_request)
            or cached_request.identity.generation_id != canonical_request.identity.generation_id
            or cached_request.identity.idempotency_key != canonical_request.identity.idempotency_key
            or cached_request.identity.attempt_number > self._config.max_attempts
            or (
                cached_request.identity.attempt_number == 1
                and cached_request.identity != canonical_request.identity
            )
        ):
            raise GenerationOrchestrationContractError(
                "cached result does not belong to the canonical generation request"
            )
        if result.disposition is not CandidateDisposition.REQUIRES_VALIDATION:
            raise GenerationOrchestrationContractError(
                "cached result must require validation and cannot be published"
            )
        return result

    def _enforce_budget(
        self,
        usage: GenerationBudgetUsage,
        identity: GenerationIdentity,
    ) -> None:
        checks = (
            (
                GenerationBudgetDimension.INPUT_TOKENS,
                usage.input_tokens,
                self._config.max_total_input_tokens,
            ),
            (
                GenerationBudgetDimension.OUTPUT_TOKENS,
                usage.output_tokens,
                self._config.max_total_output_tokens,
            ),
            (
                GenerationBudgetDimension.COST_MICROUSD,
                usage.cost_microusd,
                self._config.max_total_cost_microusd,
            ),
        )
        for dimension, consumed, limit in checks:
            if consumed > limit:
                raise GenerationBudgetExceededError(
                    dimension=dimension,
                    consumed=consumed,
                    limit=limit,
                    usage=usage,
                    identity=identity,
                )


def _same_generation_payload(left: GenerationRequest, right: GenerationRequest) -> bool:
    """Compare every canonical input except the physical retry identity."""

    return (
        left.blueprint_version == right.blueprint_version
        and left.blueprint_slot == right.blueprint_slot
        and left.context == right.context
        and left.versions == right.versions
        and left.parameters == right.parameters
    )


def _require_bounded_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be an integer between {minimum} and {maximum}")
    return value
