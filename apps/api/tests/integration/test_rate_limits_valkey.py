import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from redis.asyncio import Redis
from testcontainers.community.redis import RedisContainer

from exam_guru_api.auth.rate_limits import (
    RateLimiterUnavailableError,
    RateLimitScope,
    ValkeyFixedWindowRateLimiter,
)

VALKEY_IMAGE = "valkey/valkey:9.1.1-alpine3.24"
ACTOR_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ACTOR_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def scope_limits(limit: int) -> dict[RateLimitScope, int]:
    return dict.fromkeys(RateLimitScope, limit)


@pytest.fixture(scope="module")
def rate_limit_valkey_url() -> Iterator[str]:
    with RedisContainer(image=VALKEY_IMAGE) as valkey:
        host = valkey.get_container_host_ip()
        port = valkey.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.mark.integration
def test_real_valkey_enforces_exact_limit_expiry_and_actor_scope_isolation(
    rate_limit_valkey_url: str,
) -> None:
    async def scenario() -> None:
        valkey = Redis.from_url(rate_limit_valkey_url)
        try:
            await valkey.flushdb()
            limiter = ValkeyFixedWindowRateLimiter(
                valkey,
                window_seconds=1,
                limits=scope_limits(2),
            )

            first = await limiter.consume(ACTOR_A, RateLimitScope.SOURCE_UPLOAD)
            second = await limiter.consume(ACTOR_A, RateLimitScope.SOURCE_UPLOAD)
            rejected = await limiter.consume(ACTOR_A, RateLimitScope.SOURCE_UPLOAD)
            other_actor = await limiter.consume(ACTOR_B, RateLimitScope.SOURCE_UPLOAD)
            other_scope = await limiter.consume(ACTOR_A, RateLimitScope.EXTRACTION_TRIGGER)

            assert [first.allowed, second.allowed, rejected.allowed] == [True, True, False]
            assert rejected.retry_after_seconds == 1
            assert other_actor.allowed is True
            assert other_scope.allowed is True

            keys = await valkey.keys("exam-guru:rate-limit:v1:*")
            assert len(keys) == 3
            for key in keys:
                rendered = key.decode("ascii") if isinstance(key, bytes) else key
                assert len(rendered) <= 96
                assert str(ACTOR_A) not in rendered
                assert ACTOR_A.hex not in rendered
                assert all(scope.value not in rendered for scope in RateLimitScope)
                count = int(await valkey.get(key) or b"0")
                ttl_ms = await valkey.pttl(key)
                assert 1 <= count <= 2
                assert 1 <= ttl_ms <= 1_000

            await asyncio.sleep(1.05)
            reset = await limiter.consume(ACTOR_A, RateLimitScope.SOURCE_UPLOAD)
            assert reset.allowed is True
            reset_keys = await valkey.keys("exam-guru:rate-limit:v1:*")
            reset_counts = [int(await valkey.get(key) or b"0") for key in reset_keys]
            assert reset_counts == [1]
        finally:
            await valkey.aclose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_valkey_atomic_concurrency_never_exceeds_the_configured_limit(
    rate_limit_valkey_url: str,
) -> None:
    async def scenario() -> None:
        valkey = Redis.from_url(rate_limit_valkey_url)
        try:
            await valkey.flushdb()
            limiter = ValkeyFixedWindowRateLimiter(
                valkey,
                window_seconds=30,
                limits=scope_limits(7),
            )
            decisions = await asyncio.gather(
                *(
                    limiter.consume(ACTOR_A, RateLimitScope.GENERATION_CREATE_RETRY)
                    for _ in range(64)
                )
            )

            assert sum(decision.allowed for decision in decisions) == 7
            retry_after_values = {
                decision.retry_after_seconds for decision in decisions if not decision.allowed
            }
            assert all(1 <= value <= 30 for value in retry_after_values)
            keys = await valkey.keys("exam-guru:rate-limit:v1:*")
            assert len(keys) == 1
            assert await valkey.get(keys[0]) == b"7"
            assert 1 <= await valkey.pttl(keys[0]) <= 30_000
        finally:
            await valkey.aclose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_valkey_repairs_counter_and_ttl_to_configured_bounds(
    rate_limit_valkey_url: str,
) -> None:
    async def scenario() -> None:
        valkey = Redis.from_url(rate_limit_valkey_url)
        try:
            await valkey.flushdb()
            limiter = ValkeyFixedWindowRateLimiter(
                valkey,
                window_seconds=1,
                limits=scope_limits(2),
            )
            assert (await limiter.consume(ACTOR_A, RateLimitScope.SOURCE_UPLOAD)).allowed
            keys = await valkey.keys("exam-guru:rate-limit:v1:*")
            assert len(keys) == 1
            await valkey.set(keys[0], 999_999, px=60_000)

            rejected = await limiter.consume(ACTOR_A, RateLimitScope.SOURCE_UPLOAD)

            assert rejected.allowed is False
            assert rejected.retry_after_seconds == 1
            assert await valkey.get(keys[0]) == b"2"
            assert 1 <= await valkey.pttl(keys[0]) <= 1_000
        finally:
            await valkey.aclose()

    asyncio.run(scenario())


@pytest.mark.integration
def test_real_valkey_script_failure_is_sanitized_and_fails_closed(
    rate_limit_valkey_url: str,
) -> None:
    async def scenario() -> None:
        valkey = Redis.from_url(rate_limit_valkey_url)
        try:
            await valkey.flushdb()
            limiter = ValkeyFixedWindowRateLimiter(
                valkey,
                window_seconds=30,
                limits=scope_limits(2),
            )
            assert (await limiter.consume(ACTOR_A, RateLimitScope.VALIDATION_RUN)).allowed
            keys = await valkey.keys("exam-guru:rate-limit:v1:*")
            assert len(keys) == 1
            sensitive_value = "private-token count=999999"
            await valkey.set(keys[0], sensitive_value, px=30_000)

            with pytest.raises(RateLimiterUnavailableError) as raised:
                await limiter.consume(ACTOR_A, RateLimitScope.VALIDATION_RUN)

            assert str(raised.value) == "rate limiter unavailable"
            assert sensitive_value not in str(raised.value)
        finally:
            await valkey.aclose()

    asyncio.run(scenario())
