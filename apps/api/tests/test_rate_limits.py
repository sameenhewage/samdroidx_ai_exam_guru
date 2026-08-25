import asyncio
from typing import cast
from uuid import UUID

import pytest
from redis.asyncio import Redis

from exam_guru_api.auth.rate_limits import (
    NoOpRateLimiter,
    RateLimitDecision,
    RateLimiterUnavailableError,
    RateLimitScope,
    UnavailableRateLimiter,
    ValkeyFixedWindowRateLimiter,
    create_rate_limiter,
)
from exam_guru_api.core.config import Settings

ACTOR_ID = UUID("11111111-1111-1111-1111-111111111111")


def scope_limits(limit: int = 3) -> dict[RateLimitScope, int]:
    return dict.fromkeys(RateLimitScope, limit)


class RecordingValkey:
    def __init__(self, result: object = (1, 30_000), *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, int, str, int, int]] = []

    async def eval(
        self,
        script: str,
        key_count: int,
        key: str,
        limit: int,
        window_ms: int,
    ) -> object:
        self.calls.append((script, key_count, key, limit, window_ms))
        if self.error is not None:
            raise self.error
        return self.result


class ResourcesWithValkey:
    def __init__(self, valkey: RecordingValkey) -> None:
        self.valkey = cast(Redis, valkey)


@pytest.mark.parametrize(
    ("result", "expected_allowed", "expected_retry_after"),
    [
        ((1, 30_000), True, 0),
        ((0, 1), False, 1),
        ((0, 1_001), False, 2),
        ((0, 30_000), False, 30),
    ],
)
def test_fixed_window_decision_is_sanitized_and_retry_after_rounds_up(
    result: object,
    expected_allowed: bool,
    expected_retry_after: int,
) -> None:
    valkey = RecordingValkey(result)
    limiter = ValkeyFixedWindowRateLimiter(
        cast(Redis, valkey),
        window_seconds=30,
        limits=scope_limits(),
    )

    decision = asyncio.run(limiter.consume(ACTOR_ID, RateLimitScope.SOURCE_UPLOAD))

    assert decision == RateLimitDecision(
        allowed=expected_allowed,
        retry_after_seconds=expected_retry_after,
    )
    script, key_count, key, limit, window_ms = valkey.calls[0]
    assert "INCR" in script
    assert "PEXPIRE" in script
    assert key_count == 1
    assert limit == 3
    assert window_ms == 30_000
    assert key.startswith("exam-guru:rate-limit:v1:")
    assert len(key) <= 96
    assert str(ACTOR_ID) not in key
    assert ACTOR_ID.hex not in key
    assert RateLimitScope.SOURCE_UPLOAD.value not in key


@pytest.mark.parametrize(
    "result",
    [
        None,
        (),
        (1,),
        (1, 1, 1),
        (True, 1_000),
        (2, 1_000),
        (1, 0),
        (1, 30_001),
    ],
)
def test_malformed_valkey_results_fail_closed_without_leaking_values(result: object) -> None:
    limiter = ValkeyFixedWindowRateLimiter(
        cast(Redis, RecordingValkey(result)),
        window_seconds=30,
        limits=scope_limits(),
    )

    with pytest.raises(RateLimiterUnavailableError) as raised:
        asyncio.run(limiter.consume(ACTOR_ID, RateLimitScope.VALIDATION_RUN))

    assert str(raised.value) == "rate limiter unavailable"
    assert repr(result) not in str(raised.value)


def test_valkey_exceptions_are_wrapped_with_a_fixed_sanitized_error() -> None:
    sensitive = "redis://user:" + "private-password" + "@cache/internal-key"
    limiter = ValkeyFixedWindowRateLimiter(
        cast(Redis, RecordingValkey(error=RuntimeError(sensitive))),
        window_seconds=30,
        limits=scope_limits(),
    )

    with pytest.raises(RateLimiterUnavailableError) as raised:
        asyncio.run(limiter.consume(ACTOR_ID, RateLimitScope.EMBEDDING_JOB_CREATE))

    assert str(raised.value) == "rate limiter unavailable"
    assert sensitive not in str(raised.value)
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_noop_and_unavailable_limiters_are_deterministic_test_injections() -> None:
    allowed = asyncio.run(
        NoOpRateLimiter().consume(ACTOR_ID, RateLimitScope.GENERATION_CREATE_RETRY)
    )
    assert allowed == RateLimitDecision(allowed=True, retry_after_seconds=0)

    with pytest.raises(RateLimiterUnavailableError):
        asyncio.run(
            UnavailableRateLimiter().consume(
                ACTOR_ID,
                RateLimitScope.PAPER_PUBLISH_ARCHIVE,
            )
        )


def test_factory_uses_existing_resource_valkey_and_exact_per_scope_settings() -> None:
    valkey = RecordingValkey((0, 60_000))
    settings = Settings(
        environment="test",
        rate_limit_window_seconds=60,
        rate_limit_source_upload=11,
        rate_limit_extraction_trigger=12,
        rate_limit_embedding_job_create=13,
        rate_limit_generation_create_retry=14,
        rate_limit_validation_run=15,
        rate_limit_paper_publish_archive=16,
    )
    limiter = create_rate_limiter(settings, ResourcesWithValkey(valkey))

    async def consume_all_scopes() -> None:
        for scope in RateLimitScope:
            await limiter.consume(ACTOR_ID, scope)

    asyncio.run(consume_all_scopes())

    assert [call[3] for call in valkey.calls] == [11, 12, 13, 14, 15, 16]
    assert all(call[4] == 60_000 for call in valkey.calls)


def test_factory_is_noop_only_when_explicitly_disabled_and_fails_closed_without_valkey() -> None:
    disabled = create_rate_limiter(
        Settings(environment="test", rate_limits_enabled=False),
        object(),
    )
    missing_valkey = create_rate_limiter(Settings(environment="test"), object())

    assert isinstance(disabled, NoOpRateLimiter)
    assert isinstance(missing_valkey, UnavailableRateLimiter)


def test_all_public_scope_values_are_fixed_safe_allowlisted_tokens() -> None:
    assert tuple(scope.value for scope in RateLimitScope) == (
        "source_upload",
        "extraction_trigger",
        "embedding_job_create",
        "generation_create_retry",
        "validation_run",
        "paper_publish_archive",
    )
    assert all(value.isascii() and value.replace("_", "").isalnum() for value in RateLimitScope)
