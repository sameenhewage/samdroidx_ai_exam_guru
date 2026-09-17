import json
from typing import Any

import pytest

from exam_guru_api.documents.source_reading_qwen import (
    QwenSourceReadConfig,
    QwenSourceReadError,
    QwenSourceReadProvider,
)
from tests.test_source_consensus import region_input


def config() -> QwenSourceReadConfig:
    return QwenSourceReadConfig(model_digest="a" * 64)


def runtime_response(url: str) -> dict[str, object] | None:
    if url.endswith("/api/version"):
        return {"version": config().api_version}
    if url.endswith("/api/tags"):
        return {"models": [{"name": config().model, "digest": config().model_digest}]}
    return None


def response(content: str = "මව්බස") -> dict[str, Any]:
    return {
        "model": "qwen3-vl:8b",
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": json.dumps({"exact_text": content, "uncertain": False}),
            "thinking": "private diagnostic, not source text",
        },
        "prompt_eval_count": 20,
        "eval_count": 10,
        "total_duration": 1000000,
    }


def test_qwen_reads_only_source_pixels_locally_with_its_supported_thinking_parser() -> None:
    source = region_input()
    sent: list[dict[str, Any]] = []

    def transport(url: str, payload: dict[str, object] | None, timeout: float) -> dict[str, object]:
        metadata = runtime_response(url)
        if metadata is not None:
            assert payload is None
            return metadata
        assert payload is not None
        assert url == "http://127.0.0.1:11434/api/chat"
        assert 0 < timeout <= 180
        sent.append(payload)
        return response()

    result = QwenSourceReadProvider(config(), transport=transport).read(source)
    assert result.reader.reader == "qwen"
    assert result.reader.provider == "ollama"
    assert result.content.exact_text == "මව්බස"
    assert result.cost_microusd == 0
    assert result.input_fingerprint == source.fingerprint
    payload = sent[0]
    assert payload["model"] == "qwen3-vl:8b"
    assert payload["think"] is True
    assert payload["stream"] is False
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["temperature"] == 0.3
    assert set(payload["format"]["properties"]) == {"exact_text", "uncertain"}
    assert payload["messages"][1]["images"]
    assert "openai" not in json.dumps(payload).lower()
    assert "මව්බස" not in json.dumps(payload, ensure_ascii=False)
    assert "education" not in payload["format"]["properties"]


@pytest.mark.parametrize(
    "failure", ["thinking_only", "truncated", "wrong_model", "malformed", "missing_usage"]
)
def test_invalid_or_thinking_only_qwen_output_is_not_transcription(failure: str) -> None:
    value = response()
    if failure == "thinking_only":
        value["message"]["thinking"] = value["message"]["content"]
        value["message"]["content"] = ""
    elif failure == "truncated":
        value["done_reason"] = "length"
    elif failure == "wrong_model":
        value["model"] = "unapproved-model"
    elif failure == "malformed":
        value["message"]["content"] = '{"topic":"not source"}'
    else:
        value.pop("eval_count")
    with pytest.raises(QwenSourceReadError, match="qwen_invalid_response"):
        QwenSourceReadProvider(
            config(), transport=lambda url, *_: runtime_response(url) or value
        ).read(region_input())


def test_reasoning_that_exhausts_the_output_budget_is_an_explicit_missing_reading() -> None:
    """Measured 2026-09-17: qwen3-vl:8b loops on Sinhala it cannot decode and returns
    empty content with done_reason=length. Raising num_predict only scales the loop, so
    this must surface as an explicit exhausted-budget failure, never as fallback text."""
    value = response()
    value["done_reason"] = "length"
    value["message"]["content"] = ""
    value["message"]["thinking"] = "private reasoning that never reached a final answer"
    value["eval_count"] = 4096

    with pytest.raises(QwenSourceReadError, match="qwen_output_budget_exhausted") as caught:
        QwenSourceReadProvider(
            config(), transport=lambda url, *_: runtime_response(url) or value
        ).read(region_input())
    assert "private reasoning" not in str(caught.value)


@pytest.mark.parametrize("mismatch", ["version", "missing_model", "digest", "malformed"])
def test_runtime_identity_is_checked_before_sending_source_pixels(mismatch: str) -> None:
    dispatched: list[str] = []

    def transport(url: str, payload: dict[str, object] | None, timeout: float) -> dict[str, object]:
        if url.endswith("/api/version"):
            return {"version": "changed" if mismatch == "version" else config().api_version}
        if url.endswith("/api/tags"):
            if mismatch == "malformed":
                return {"models": "invalid"}
            return {
                "models": []
                if mismatch == "missing_model"
                else [{"name": config().model, "digest": "b" * 64}]
            }
        dispatched.append(url)
        return response()

    with pytest.raises(QwenSourceReadError, match=r"qwen_(runtime|model)"):
        QwenSourceReadProvider(config(), transport=transport).read(region_input())
    assert dispatched == []


def test_thinking_runtime_defaults_leave_room_for_a_final_source_answer() -> None:
    value = config()
    assert value.context_tokens == 8192
    assert value.output_tokens == 4096
    assert value.temperature == 0.3


def test_qwen_timeout_is_explicit_and_has_no_hidden_retries() -> None:
    calls = 0

    def transport(url: str, *_: object) -> dict[str, object]:
        nonlocal calls
        metadata = runtime_response(url)
        if metadata is not None:
            return metadata
        calls += 1
        raise TimeoutError("private diagnostic")

    with pytest.raises(QwenSourceReadError, match="timeout"):
        QwenSourceReadProvider(config(), transport=transport).read(region_input())
    assert calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.2:11434",
        "http://127.0.0.1:11434/redirect",
        "http://user:password@127.0.0.1:11434",
    ],
)
def test_source_images_cannot_be_redirected_to_a_nonlocal_qwen_endpoint(url: str) -> None:
    with pytest.raises(ValueError, match="local endpoint"):
        QwenSourceReadConfig(base_url=url, model_digest="a" * 64)


def test_local_transport_itself_rejects_external_endpoints_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from exam_guru_api.documents.source_reading_qwen import _local_json

    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("unexpected network connection")

    monkeypatch.setattr(socket, "create_connection", blocked)
    with pytest.raises(QwenSourceReadError, match="local_endpoint"):
        _local_json("https://example.com/api/chat", {}, 1)
