import base64
from typing import Any, cast

from exam_guru_api.documents.source_reading_openai import (
    OpenAISourceWitnessProvider,
    SourceReadingSession,
)
from exam_guru_api.documents.understanding_openai import OpenAIUnderstandingProvider, _Client
from tests.test_document_understanding_openai import configuration
from tests.test_source_consensus import region_input
from tests.test_source_reading_openai import client_for, region, request


def test_openai_witness_receives_original_pixels_without_the_qwen_reading() -> None:
    value = request()
    source = region_input()
    sent: list[dict[str, Any]] = []
    events: list[dict[str, object]] = []
    with client_for([region("මව්බස")], sent, value) as client:
        session = SourceReadingSession(
            value,
            OpenAIUnderstandingProvider(configuration(value)),
            cast(_Client, client),
            events.append,
            lambda: 1,
        )
        result = OpenAISourceWitnessProvider(session).read(source)
    assert result.content.exact_text == "මව්බස"
    assert result.input_fingerprint == source.fingerprint
    assert result.reader.reader == "openai"
    assert result.reader.model == value.profile.model
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost_microusd > 0
    assert len(sent) == 1
    payload = sent[0]
    parts = payload["messages"][1]["content"]
    assert len(parts) == 2
    assert base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1]) == source.image_png
    assert "qwen" not in parts[0]["text"].lower()
    assert "මව්බස" not in parts[0]["text"]
    assert payload["response_format"]["json_schema"]["name"] == "SourceTextReading"
    assert payload["store"] is False
    assert events[-1]["event"] == "provider_completed"


def test_openai_witness_preserves_visual_source_schema_without_educational_claims() -> None:
    value = request()
    source = region_input(purpose="visual", language="und")
    sent: list[dict[str, Any]] = []
    with client_for([region("8")], sent, value) as client:
        session = SourceReadingSession(
            value,
            OpenAIUnderstandingProvider(configuration(value)),
            cast(_Client, client),
            None,
            lambda: 1,
        )
        result = OpenAISourceWitnessProvider(session).read(source)
    assert result.content.exact_text == "8"
    schema = sent[0]["response_format"]["json_schema"]["schema"]
    assert "education" not in schema["properties"]
    assert "visual_facts" in schema["properties"]
