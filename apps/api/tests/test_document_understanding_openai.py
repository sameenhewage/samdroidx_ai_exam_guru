import base64
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from openai import OpenAI
from pydantic import SecretStr, ValidationError

import exam_guru_api.documents.understanding_openai as adapter
from exam_guru_api.documents.understanding_contracts import MAX_UNDERSTANDING_BYTES
from exam_guru_api.documents.understanding_openai import (
    OpenAIUnderstandingConfig,
    OpenAIUnderstandingProvider,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderError,
    UnderstandingProviderResult,
    UnderstandingRequest,
)
from exam_guru_api.generation.ports import ProviderFailureCode
from tests.test_document_understanding_contracts import counting_candidate
from tests.test_document_understanding_provider import request as fixture_request


def request() -> UnderstandingRequest:
    fixture = fixture_request()
    profile = fixture.profile.model_copy(
        update={
            "provider": "openai",
            "provider_version": "3.1.0",
            "model": "fixture-vision",
            "model_version": "fixture-vision-2026-08-01",
        }
    )
    return UnderstandingRequest.model_validate(fixture.model_copy(update={"profile": profile}))


def configuration(value: UnderstandingRequest) -> OpenAIUnderstandingConfig:
    return OpenAIUnderstandingConfig(
        api_key=SecretStr("unit-test-placeholder-not-a-credential"),
        profile=value.profile,
        image_input_verified=True,
        structured_output_verified=True,
    )


def completion(value: UnderstandingRequest) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 0,
        "model": value.profile.model_version,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(counting_candidate()),
                    "refusal": None,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    }


def clock() -> Callable[[], int]:
    ticks = iter((1_000_000_000, 1_100_000_000))
    return lambda: next(ticks)


def run_response(
    value: UnderstandingRequest, body: dict[str, Any], clock_ns: Callable[[], int] | None = None
) -> UnderstandingProviderResult:
    with OpenAI(
        api_key=configuration(value).api_key.get_secret_value(),
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=body))
        ),
    ) as client:
        return OpenAIUnderstandingProvider(
            configuration(value), client=client, clock_ns=clock_ns or clock()
        ).understand(value)


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_clock_failures_are_sanitized_without_inventing_latency(after_dispatch: bool) -> None:
    value = request()
    calls = 0

    def broken_clock() -> int:
        nonlocal calls
        calls += 1
        if after_dispatch and calls == 1:
            return 1_000_000_000
        raise RuntimeError("private-clock-detail")

    with pytest.raises(UnderstandingProviderError) as caught:
        run_response(value, completion(value), broken_clock)
    assert "private-clock-detail" not in str(caught.value)
    assert caught.value.accounting is None


@pytest.mark.parametrize("case", ["missing_usage", "zero_usage", "bad_total", "wrong_model"])
def test_incomplete_or_inconsistent_accounting_never_looks_successful(case: str) -> None:
    value = request()
    body = completion(value)
    if case == "missing_usage":
        body.pop("usage")
    elif case == "zero_usage":
        body["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    elif case == "bad_total":
        body["usage"]["total_tokens"] = 1
    else:
        body["model"] = "unapproved-model"
    with pytest.raises(UnderstandingProviderError) as caught:
        run_response(value, body)
    assert caught.value.code is ProviderFailureCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    "case",
    [
        "empty_choices",
        "many_choices",
        "refusal",
        "tool_call",
        "length",
        "missing_content",
        "invalid_json",
        "oversized",
    ],
)
def test_invalid_visual_responses_retain_accounted_usage_without_trust(case: str) -> None:
    value = request()
    body = completion(value)
    if case == "empty_choices":
        body["choices"] = []
    elif case == "many_choices":
        body["choices"] *= 2
    elif case == "refusal":
        body["choices"][0]["message"]["refusal"] = "Not available"
    elif case == "tool_call":
        body["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "forbidden",
                "type": "function",
                "function": {"name": "approve_source", "arguments": "{}"},
            }
        ]
    elif case == "length":
        body["choices"][0]["finish_reason"] = "length"
    elif case == "missing_content":
        body["choices"][0]["message"]["content"] = None
    elif case == "invalid_json":
        body["choices"][0]["message"]["content"] = "not JSON"
    else:
        body["choices"][0]["message"]["content"] = "x" * (MAX_UNDERSTANDING_BYTES + 1)
    with pytest.raises(UnderstandingProviderError) as caught:
        run_response(value, body)
    assert caught.value.code is ProviderFailureCode.INVALID_RESPONSE
    assert caught.value.accounting is not None
    assert caught.value.accounting.cost_microusd == 500


def test_visual_adapter_uses_rendered_image_strict_schema_and_candidate_only_accounting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    value = request()
    sent: list[dict[str, Any]] = []

    def respond(incoming: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(incoming.content))
        assert incoming.headers["Idempotency-Key"]
        logging.getLogger("openai._base_client").debug("private-provider-payload-canary")
        return httpx2.Response(200, json=completion(value))

    with (
        OpenAI(
            api_key=configuration(value).api_key.get_secret_value(),
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        ) as client,
        caplog.at_level(logging.DEBUG),
    ):
        result = OpenAIUnderstandingProvider(
            configuration(value), client=client, clock_ns=clock()
        ).understand(value)
    assert len(sent) == 1
    assert sent[0]["model"] == value.profile.model_version
    assert sent[0]["store"] is False
    assert sent[0]["n"] == 1
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    image = sent[0]["messages"][1]["content"][1]["image_url"]["url"]
    assert base64.b64decode(image.split(",", 1)[1]) == value.image_png
    assert result.disposition == "requires_verification"
    assert result.source == value.source
    assert result.content.observation.regions[3].table is not None
    assert result.content.observation.regions[3].table.cells[1].state == "blank"
    assert result.accounting.input_tokens == 100
    assert result.accounting.output_tokens == 200
    assert result.accounting.cost_microusd == 500
    assert result.accounting.latency_ms == 100
    assert "private-provider-payload-canary" not in caplog.text


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ProviderFailureCode.AUTHENTICATION),
        (403, ProviderFailureCode.PERMISSION_DENIED),
        (429, ProviderFailureCode.RATE_LIMITED),
        (408, ProviderFailureCode.TIMEOUT),
        (500, ProviderFailureCode.UNAVAILABLE),
        (400, ProviderFailureCode.INVALID_REQUEST),
    ],
)
def test_sdk_failures_are_sanitized_and_never_hidden_by_automatic_retries(
    status: int, code: ProviderFailureCode
) -> None:
    value = request()
    calls = 0

    def respond(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(
            status,
            json={"error": {"message": "private-error-canary", "type": "fixture"}},
            headers={"Retry-After": "2"},
        )

    with (
        OpenAI(
            api_key=configuration(value).api_key.get_secret_value(),
            max_retries=2,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        ) as client,
        pytest.raises(UnderstandingProviderError) as caught,
    ):
        OpenAIUnderstandingProvider(
            configuration(value), client=client, clock_ns=clock()
        ).understand(value)
    assert calls == 1
    assert caught.value.code is code
    assert "private-error-canary" not in str(caught.value)
    assert caught.value.accounting is None
    assert caught.value.retry_after_ms == (
        2000
        if code
        in {
            ProviderFailureCode.RATE_LIMITED,
            ProviderFailureCode.TIMEOUT,
            ProviderFailureCode.UNAVAILABLE,
        }
        else None
    )


@pytest.mark.parametrize("case", ["output", "cost", "latency", "zero_output"])
def test_response_budget_failures_preserve_known_accounting(case: str) -> None:
    value = request()
    body = completion(value)
    timer = clock()
    if case == "output":
        value = value.model_copy(
            update={"budget": value.budget.model_copy(update={"max_output_tokens": 100})}
        )
    elif case == "cost":
        value = value.model_copy(
            update={"budget": value.budget.model_copy(update={"max_cost_microusd": 100})}
        )
    elif case == "latency":
        ticks = iter((0, 31_000_000_000))
        timer = ticks.__next__
    else:
        body["usage"] = {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100}
    with pytest.raises(UnderstandingProviderError) as caught:
        run_response(value, body, timer)
    assert caught.value.accounting is not None
    assert caught.value.accounting.cost_microusd == (100 if case == "zero_output" else 500)
    assert caught.value.code is (
        ProviderFailureCode.TIMEOUT if case == "latency" else ProviderFailureCode.INVALID_RESPONSE
    )


def test_adapter_creates_and_closes_its_own_bounded_client(monkeypatch: pytest.MonkeyPatch) -> None:
    value = request()
    observed: dict[str, Any] = {}
    client = OpenAI(
        api_key=configuration(value).api_key.get_secret_value(),
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=completion(value)))
        ),
    )

    def create(**kwargs: Any) -> OpenAI:
        observed.update(kwargs)
        return client

    monkeypatch.setattr(adapter, "OpenAI", create)
    result = OpenAIUnderstandingProvider(configuration(value), clock_ns=clock()).understand(value)
    assert result.accounting.total_tokens == 300
    assert observed["max_retries"] == 0
    assert observed["timeout"] == value.budget.timeout_ms / 1000
    assert client.is_closed()


def test_changed_profiles_and_sdk_versions_fail_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = request()
    provider = OpenAIUnderstandingProvider(configuration(value), client=object(), clock_ns=clock())
    changed = value.model_copy(
        update={"profile": value.profile.model_copy(update={"temperature": 1.0})}
    )
    with pytest.raises(UnderstandingProviderError) as caught:
        provider.understand(changed)
    assert caught.value.code is ProviderFailureCode.INVALID_REQUEST
    monkeypatch.setattr(adapter, "openai_sdk_version", "unapproved")
    with pytest.raises(UnderstandingProviderError) as caught:
        provider.understand(value)
    assert caught.value.code is ProviderFailureCode.UNAVAILABLE


@pytest.mark.parametrize(
    "failure", [TimeoutError("private-timeout"), RuntimeError("private-failure")]
)
def test_unexpected_transport_failures_remain_typed_without_raw_details(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    value = request()
    provider = OpenAIUnderstandingProvider(configuration(value), client=object(), clock_ns=clock())

    def dispatch(_: UnderstandingRequest) -> object:
        raise failure

    monkeypatch.setattr(provider, "_dispatch", dispatch)
    with pytest.raises(UnderstandingProviderError) as caught:
        provider.understand(value)
    assert "private" not in str(caught.value)
    assert caught.value.accounting is None
    assert caught.value.code is (
        ProviderFailureCode.TIMEOUT
        if isinstance(failure, TimeoutError)
        else ProviderFailureCode.UNAVAILABLE
    )


@pytest.mark.parametrize(
    "case",
    [
        "plain_key",
        "empty_key",
        "oversized_key",
        "space_key",
        "control_key",
        "provider",
        "sdk",
        "prompt",
        "input_price",
        "output_price",
        "structured_capability",
    ],
)
def test_adapter_configuration_is_explicit_bounded_and_secret_safe(case: str) -> None:
    value = request()
    payload = configuration(value).model_dump()
    if case == "plain_key":
        payload["api_key"] = value.profile.model
    elif case == "empty_key":
        payload["api_key"] = SecretStr("")
    elif case == "oversized_key":
        payload["api_key"] = SecretStr("x" * 4097)
    elif case == "space_key":
        payload["api_key"] = SecretStr("not valid")
    elif case == "control_key":
        payload["api_key"] = SecretStr("invalid\x01value")
    elif case == "structured_capability":
        payload["structured_output_verified"] = False
    else:
        field, replacement = {
            "provider": ("provider", "unapproved"),
            "sdk": ("provider_version", "unapproved"),
            "prompt": ("prompt_version", "unapproved"),
            "input_price": ("input_microusd_per_million_tokens", 0),
            "output_price": ("output_microusd_per_million_tokens", 0),
        }[case]
        payload["profile"] = value.profile.model_copy(update={field: replacement})
    with pytest.raises(ValidationError):
        OpenAIUnderstandingConfig.model_validate(payload)


def test_retry_after_milliseconds_is_bounded_without_an_sdk_retry() -> None:
    value = request()
    with (
        OpenAI(
            api_key=configuration(value).api_key.get_secret_value(),
            http_client=httpx2.Client(
                transport=httpx2.MockTransport(
                    lambda _: httpx2.Response(
                        429,
                        json={"error": {"message": "fixture rate limit"}},
                        headers={"retry-after-ms": "1250", "Retry-After": "2"},
                    )
                )
            ),
        ) as client,
        pytest.raises(UnderstandingProviderError) as caught,
    ):
        OpenAIUnderstandingProvider(
            configuration(value), client=client, clock_ns=clock()
        ).understand(value)
    assert caught.value.retry_after_ms == 1250


def test_connection_failure_without_response_headers_has_unknown_accounting() -> None:
    value = request()

    def disconnected(incoming: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("private-connection-detail", request=incoming)

    with (
        OpenAI(
            api_key=configuration(value).api_key.get_secret_value(),
            http_client=httpx2.Client(transport=httpx2.MockTransport(disconnected)),
        ) as client,
        pytest.raises(UnderstandingProviderError) as caught,
    ):
        OpenAIUnderstandingProvider(
            configuration(value), client=client, clock_ns=clock()
        ).understand(value)
    assert caught.value.code is ProviderFailureCode.UNAVAILABLE
    assert caught.value.retry_after_ms is None
    assert caught.value.accounting is None
    assert "private-connection-detail" not in str(caught.value)


def test_unverified_model_capabilities_cannot_start_a_provider() -> None:
    value = request()
    with pytest.raises(ValidationError, match="capabilit"):
        OpenAIUnderstandingConfig.model_validate(
            configuration(value).model_copy(update={"image_input_verified": False})
        )


def test_provider_output_cannot_grant_trust_and_invalid_output_keeps_usage() -> None:
    value = request()
    body = completion(value)
    payload = counting_candidate()
    payload["verified"] = True
    body["choices"][0]["message"]["content"] = json.dumps(payload)
    with (
        OpenAI(
            api_key=configuration(value).api_key.get_secret_value(),
            http_client=httpx2.Client(
                transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=body))
            ),
        ) as client,
        pytest.raises(UnderstandingProviderError) as caught,
    ):
        OpenAIUnderstandingProvider(
            configuration(value), client=client, clock_ns=clock()
        ).understand(value)
    assert caught.value.code is ProviderFailureCode.INVALID_RESPONSE
    assert caught.value.accounting is not None
    assert caught.value.accounting.cost_microusd == 500
