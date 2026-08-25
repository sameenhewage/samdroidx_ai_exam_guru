from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from exam_guru_api.auth.rate_limits import RateLimitScope


class ApiErrorDetail(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str


class ApiErrorResponse(BaseModel):
    detail: ApiErrorDetail | list[dict[str, JsonValue]]


class RateLimitExceededDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["rate_limit_exceeded"]
    scope: RateLimitScope


class RateLimitExceededResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: RateLimitExceededDetail


class RateLimiterUnavailableDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["rate_limiter_unavailable"]


class RateLimiterUnavailableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: RateLimiterUnavailableDetail


RATE_LIMIT_EXCEEDED_OPENAPI_RESPONSE: dict[str, Any] = {
    "description": (
        "Authenticated principal cost limit exceeded. Idempotent duplicate attempts consume "
        "one unit. Retry-After is an integer number of seconds."
    ),
    "model": RateLimitExceededResponse,
    "headers": {
        "Retry-After": {
            "description": "Integer seconds until this principal/scope window resets",
            "schema": {"type": "integer", "minimum": 1},
        }
    },
}
RATE_LIMITER_UNAVAILABLE_OPENAPI_RESPONSE: dict[str, Any] = {
    "description": "Authenticated cost limiter unavailable; the costly operation fails closed",
    "model": RateLimiterUnavailableResponse,
}
