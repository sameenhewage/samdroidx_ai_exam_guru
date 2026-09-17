import base64
import json
from typing import Any

import httpx2
import pytest
from openai import OpenAI

from exam_guru_api.documents.source_reading import SourceReadingBudget
from exam_guru_api.documents.source_reading_openai import OpenAISourceReadingProvider
from exam_guru_api.documents.understanding_provider import (
    UnderstandingBudget,
    UnderstandingProviderError,
    UnderstandingProviderProfile,
    UnderstandingRequest,
)
from tests.test_document_understanding_openai import configuration
from tests.test_document_understanding_openai import request as previous_request


def request(*, limit: int = 8) -> UnderstandingRequest:
    old = previous_request()
    profile = UnderstandingProviderProfile.model_validate(
        {
            **old.profile.model_dump(),
            "model": "gpt-5.6-sol",
            "model_version": "gpt-5.6-sol",
            "prompt_version": "visual-source-reading.v3",
            "schema_version": "source-read-candidate.v1",
            "reasoning_effort": "high",
        }
    )
    budget = UnderstandingBudget(pipeline=SourceReadingBudget(max_requests=limit))
    return UnderstandingRequest(
        source=old.source, image_png=old.image_png, profile=profile, budget=budget
    )


def region(text: str, *, uncertain: bool = False) -> dict[str, Any]:
    return {
        "exact_text": text,
        "equations": [],
        "table": None,
        "visual_facts": [],
        "uncertainties": [
            {"field": "exact_text", "reason": "A glyph is unclear", "alternatives": []}
        ]
        if uncertain
        else [],
    }


def layout() -> dict[str, Any]:
    return {
        "schema_version": "source-layout.v1",
        "language": "si",
        "regions": [
            {
                "key": "heading",
                "kind": "heading",
                "reading_order": 0,
                "parent_key": None,
                "bounds": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 0.5},
            },
            {
                "key": "footer",
                "kind": "footer",
                "reading_order": 1,
                "parent_key": None,
                "bounds": {"left": 0.0, "top": 0.5, "right": 1.0, "bottom": 1.0},
            },
        ],
        "relationships": [],
    }


def client_for(
    replies: list[dict[str, Any]],
    sent: list[dict[str, Any]],
    value: UnderstandingRequest,
) -> OpenAI:
    def respond(incoming: httpx2.Request) -> httpx2.Response:
        payload = json.loads(incoming.content)
        sent.append(payload)
        response = replies[len(sent) - 1]
        if (
            payload["response_format"]["json_schema"]["name"] == "SourceTextReading"
            and "exact_text" in response
        ):
            response = {
                "exact_text": response["exact_text"],
                "uncertainties": response["uncertainties"],
            }
        return httpx2.Response(
            200,
            json={
                "id": f"reading-{len(sent)}",
                "object": "chat.completion",
                "created": 0,
                "model": value.profile.model_version,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(response),
                            "refusal": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    return OpenAI(
        api_key="unit-test-placeholder-not-a-credential",
        http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
    )


def test_layout_then_independent_original_resolution_crops_never_request_education() -> None:
    value = request()
    sent: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    with client_for([layout(), region("මව්බස"), region("info@nie.lk")], sent, value) as client:
        result = OpenAISourceReadingProvider(
            configuration(value), client=client, recorder=events.append
        ).understand(value)
    assert len(sent) == 3
    assert result.content.education.claims == ()
    assert [r.exact_text for r in result.content.observation.regions] == ["මව්බස", "info@nie.lk"]
    assert result.accounting.input_tokens == 300
    assert result.accounting.output_tokens == 150
    assert result.disposition == "requires_verification"
    for payload in sent:
        assert payload["model"] == "gpt-5.6-sol"
        assert payload["reasoning_effort"] == "high"
        assert "temperature" not in payload
        assert payload["store"] is False
        assert payload["response_format"]["json_schema"]["strict"] is True
        schema = json.dumps(payload["response_format"]["json_schema"]["schema"])
        assert '"education"' not in schema
        assert '"learning_objective"' not in schema
        images = [p for p in payload["messages"][1]["content"] if p["type"] == "image_url"]
        assert images
        assert all(p["image_url"]["detail"] == "original" for p in images)
    original = sent[0]["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(original) == value.image_png
    assert [e["event"] for e in events] == ["requested", "provider_completed"] * 3
    assert [e["kind"] for e in events[::2]] == ["layout", "region", "detail"]
    assert events[2]["images"][0]["height"] < value.image_dimensions[1]
    assert len(events[2]["images"]) == 2
    text_schema = sent[1]["response_format"]["json_schema"]["schema"]
    assert set(text_schema["properties"]) == {"exact_text", "uncertainties"}
    assert "flowers" not in sent[1]["messages"][0]["content"]
    assert "petals" not in sent[1]["messages"][0]["content"]
    assert events[2]["images"][1]["transform"] == "grayscale-otsu.v1"
    assert events[2]["images"][1]["parent_sha256"] == value.source.image_sha256


def test_uncertainty_rereads_only_the_affected_crop_and_flags_disagreement() -> None:
    value = request()
    sent: list[dict[str, Any]] = []
    replies = [layout(), region("මව්බස", uncertain=True), region("info@nie.lk"), region("මවුබස")]
    with client_for(replies, sent, value) as client:
        result = OpenAISourceReadingProvider(configuration(value), client=client).understand(value)
    assert len(sent) == 4
    assert sent[1]["messages"][1]["content"][1] == sent[3]["messages"][1]["content"][1]
    assert sent[0]["messages"][1]["content"][1] != sent[3]["messages"][1]["content"][1]
    assert result.content.observation.regions[1].exact_text == "info@nie.lk"
    assert any(u.field == "contradictory_readings" for u in result.content.uncertainties)


def test_request_bound_stops_without_a_hidden_whole_page_retry_and_retains_usage() -> None:
    value = request(limit=2)
    sent: list[dict[str, Any]] = []
    with (
        client_for([layout(), region("මව්බස")], sent, value) as client,
        pytest.raises(UnderstandingProviderError) as caught,
    ):
        OpenAISourceReadingProvider(configuration(value), client=client).understand(value)
    assert len(sent) == 2
    assert caught.value.accounting is not None
    assert caught.value.accounting.input_tokens == 200


def test_invalid_region_structure_retains_response_and_accounting_without_verification() -> None:
    value = request()
    sent: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    with (
        client_for([layout(), {"education": {"claims": []}}], sent, value) as client,
        pytest.raises(UnderstandingProviderError) as caught,
    ):
        OpenAISourceReadingProvider(
            configuration(value), client=client, recorder=events.append
        ).understand(value)
    assert caught.value.accounting is not None
    assert caught.value.accounting.input_tokens == 200
    assert events[-1]["event"] == "provider_completed"
    assert events[-1]["valid_structure"] is False
    assert events[-1]["response_text"] == json.dumps({"education": {"claims": []}})


def test_exhausted_provider_credit_is_a_page_blocker_not_a_region_retry() -> None:
    from exam_guru_api.documents.source_consensus import SourceExecutionBlockedError

    value = request()
    calls = []
    events: list[dict[str, object]] = []

    def response(incoming: httpx2.Request) -> httpx2.Response:
        calls.append(incoming.url.path)
        return httpx2.Response(
            429,
            json={
                "error": {
                    "type": "insufficient_quota",
                    "code": "credit_balance_exhausted",
                    "message": "Credit exhausted",
                }
            },
        )

    with (
        OpenAI(
            api_key="unit-test-placeholder-not-a-credential",
            http_client=httpx2.Client(transport=httpx2.MockTransport(response)),
        ) as client,
        pytest.raises(SourceExecutionBlockedError, match="credit_balance_exhausted"),
    ):
        OpenAISourceReadingProvider(
            configuration(value), client=client, recorder=events.append
        ).understand(value)
    assert len(calls) == 1
    assert events[-1]["provider_error_code"] == "credit_balance_exhausted"
