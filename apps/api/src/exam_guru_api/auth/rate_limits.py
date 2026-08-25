"""Authenticated application cost controls.

These per-principal controls protect allowlisted, costly authenticated operations. They are
not edge or network DDoS protection: production deployments must additionally enforce
unauthenticated and per-IP controls at the ingress/edge.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, cast
from uuid import UUID

from redis.asyncio import Redis

from exam_guru_api.core.config import Settings

_RATE_LIMIT_KEY_PREFIX = "exam-guru:rate-limit:v1:"
_FIXED_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local raw_count = redis.call("GET", key)

if raw_count then
    local count = tonumber(raw_count)
    if not count or count < 0 then
        return redis.error_reply("invalid rate limiter counter")
    end

    local ttl_ms = redis.call("PTTL", key)
    if ttl_ms < 1 or ttl_ms > window_ms then
        redis.call("PEXPIRE", key, window_ms)
        ttl_ms = window_ms
    end

    if count > limit then
        redis.call("SET", key, limit, "PX", ttl_ms)
        count = limit
    end
    if count >= limit then
        return {0, ttl_ms}
    end
end

local count = redis.call("INCR", key)
if count == 1 then
    redis.call("PEXPIRE", key, window_ms)
end
local ttl_ms = redis.call("PTTL", key)
if ttl_ms < 1 or ttl_ms > window_ms then
    redis.call("PEXPIRE", key, window_ms)
    ttl_ms = window_ms
end
return {1, ttl_ms}
"""


class RateLimitScope(StrEnum):
    SOURCE_UPLOAD = "source_upload"
    EXTRACTION_TRIGGER = "extraction_trigger"
    EMBEDDING_JOB_CREATE = "embedding_job_create"
    GENERATION_CREATE_RETRY = "generation_create_retry"
    VALIDATION_RUN = "validation_run"
    PAPER_PUBLISH_ARCHIVE = "paper_publish_archive"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class RateLimiter(Protocol):
    async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision: ...


class RateLimiterUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("rate limiter unavailable")


class NoOpRateLimiter:
    async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision:
        del principal_id, scope
        return RateLimitDecision(allowed=True, retry_after_seconds=0)


class UnavailableRateLimiter:
    async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision:
        del principal_id, scope
        raise RateLimiterUnavailableError


class ValkeyFixedWindowRateLimiter:
    def __init__(
        self,
        valkey: Redis,
        *,
        window_seconds: int,
        limits: Mapping[RateLimitScope, int],
    ) -> None:
        self._valkey = valkey
        self._window_ms = window_seconds * 1_000
        self._limits = dict(limits)

    async def consume(self, principal_id: UUID, scope: RateLimitScope) -> RateLimitDecision:
        try:
            result = await self._valkey.eval(
                _FIXED_WINDOW_SCRIPT,
                1,
                _rate_limit_key(principal_id, scope),
                self._limits[scope],
                self._window_ms,
            )
            allowed, ttl_ms = _parse_script_result(result, self._window_ms)
        except Exception as error:
            raise RateLimiterUnavailableError from error
        retry_after_seconds = 0 if allowed else (ttl_ms + 999) // 1_000
        return RateLimitDecision(
            allowed=allowed,
            retry_after_seconds=retry_after_seconds,
        )


def create_rate_limiter(settings: Settings, resources: object) -> RateLimiter:
    if not settings.rate_limits_enabled:
        return NoOpRateLimiter()
    valkey = getattr(resources, "valkey", None)
    if valkey is None:
        return UnavailableRateLimiter()
    limits = {
        RateLimitScope.SOURCE_UPLOAD: settings.rate_limit_source_upload,
        RateLimitScope.EXTRACTION_TRIGGER: settings.rate_limit_extraction_trigger,
        RateLimitScope.EMBEDDING_JOB_CREATE: settings.rate_limit_embedding_job_create,
        RateLimitScope.GENERATION_CREATE_RETRY: settings.rate_limit_generation_create_retry,
        RateLimitScope.VALIDATION_RUN: settings.rate_limit_validation_run,
        RateLimitScope.PAPER_PUBLISH_ARCHIVE: settings.rate_limit_paper_publish_archive,
    }
    return ValkeyFixedWindowRateLimiter(
        cast(Redis, valkey),
        window_seconds=settings.rate_limit_window_seconds,
        limits=limits,
    )


def _rate_limit_key(principal_id: UUID, scope: RateLimitScope) -> str:
    material = f"exam-guru-rate-limit-v1\0{principal_id.hex}\0{scope.value}".encode()
    return _RATE_LIMIT_KEY_PREFIX + sha256(material).hexdigest()


def _parse_script_result(result: object, window_ms: int) -> tuple[bool, int]:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise ValueError("invalid rate limiter response")
    allowed, ttl_ms = result
    if type(allowed) is not int or type(ttl_ms) is not int:
        raise ValueError("invalid rate limiter response")
    if allowed not in {0, 1} or not 1 <= ttl_ms <= window_ms:
        raise ValueError("invalid rate limiter response")
    return bool(allowed), ttl_ms
