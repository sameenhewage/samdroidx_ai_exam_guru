"""Small first-party provider port and normalized provider failures."""

from enum import StrEnum
from typing import Protocol

from exam_guru_api.generation.domain import (
    GenerationIdentity,
    GenerationRequest,
    GenerationResult,
)

_MAX_RETRY_AFTER_MS = 3_600_000


class ProviderFailureCode(StrEnum):
    AUTHENTICATION = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "provider_unavailable"
    CONTEXT_LIMIT_EXCEEDED = "context_limit_exceeded"
    CONTENT_FILTERED = "content_filtered"
    INVALID_RESPONSE = "invalid_response"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


_RETRYABLE_FAILURES = frozenset(
    {
        ProviderFailureCode.RATE_LIMITED,
        ProviderFailureCode.TIMEOUT,
        ProviderFailureCode.UNAVAILABLE,
    }
)


class ProviderError(RuntimeError):
    """A stable, SDK-independent failure safe for orchestration decisions.

    Raw provider payloads and exception strings intentionally do not cross this
    boundary.  Retryability is derived from the normalized code rather than
    being selected by an adapter.
    """

    def __init__(
        self,
        code: ProviderFailureCode,
        *,
        identity: GenerationIdentity | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        if not isinstance(code, ProviderFailureCode):
            raise ValueError("code must be ProviderFailureCode")
        if identity is not None and not isinstance(identity, GenerationIdentity):
            raise ValueError("identity must be GenerationIdentity")
        if retry_after_ms is not None:
            if (
                not isinstance(retry_after_ms, int)
                or isinstance(retry_after_ms, bool)
                or not 0 <= retry_after_ms <= _MAX_RETRY_AFTER_MS
            ):
                raise ValueError("retry_after_ms must be a bounded non-negative integer")
            if code not in _RETRYABLE_FAILURES:
                raise ValueError("retry_after_ms is valid only for retryable failures")
        self.code = code
        self.identity = identity
        self.retry_after_ms = retry_after_ms
        super().__init__(code.value)

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_FAILURES


ProviderFailure = ProviderError


class GenerationProvider(Protocol):
    """Provider-neutral structured question generation boundary."""

    def generate(self, request: GenerationRequest) -> GenerationResult: ...
