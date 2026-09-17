import json
from pathlib import Path
from typing import Any, cast

import pytest

from exam_guru_api.documents.page_images import PageImageArtifacts
from exam_guru_api.documents.source_consensus import SourceExecutionBlockedError
from exam_guru_api.documents.source_consensus_provider import ConsensusSourceReadingProvider
from exam_guru_api.documents.source_reading_qwen import QwenSourceReadConfig
from exam_guru_api.documents.source_renders import SourcePageRenderer
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderProfile,
    UnderstandingRequest,
)
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity
from tests.test_document_understanding_openai import configuration
from tests.test_page_images import FileSourceStore, image_source
from tests.test_source_reading_openai import client_for, layout, region, request
from tests.test_source_reading_qwen import runtime_response
from tests.test_tesseract_file_input import source_pdf


def prepared_request(tmp_path: Path) -> tuple[SourcePageRenderer, UnderstandingRequest]:
    original = source_pdf(tmp_path / "source.pdf")
    identity = image_source(original)
    renderer = SourcePageRenderer(
        identity, 1, FileSourceStore(original), PageImageArtifacts(root=tmp_path / "images")
    )
    base = renderer.render(300)
    template = request(limit=8)
    profile = UnderstandingProviderProfile.model_validate(
        {
            **template.profile.model_dump(),
            "prompt_version": "qwen-openai-source-consensus.v1",
            "qwen": QwenSourceReadConfig(model_digest="a" * 64),
        }
    )
    value = UnderstandingRequest(
        source=PageArtifactIdentity(
            document_id=identity.document_id,
            source_sha256=identity.checksum_sha256,
            page_number=1,
            image_sha256=base.metadata.sha256,
        ),
        image_png=base.png,
        profile=profile,
        budget=template.budget,
    )
    return renderer, value


def test_live_pipeline_contract_retains_independent_readers_and_true_render_identity(
    tmp_path: Path,
) -> None:
    renderer, value = prepared_request(tmp_path)
    messages: list[dict[str, Any]] = []
    local_messages: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    texts = iter(("මව්බස", "info@nie.lk"))

    def local(url: str, payload: dict[str, object] | None, timeout: float) -> dict[str, object]:
        metadata = runtime_response(url)
        if metadata is not None:
            assert payload is None
            return metadata
        assert payload is not None
        local_messages.append(payload)
        return {
            "model": "qwen3-vl:8b",
            "done": True,
            "done_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps({"exact_text": next(texts), "uncertain": False}),
            },
            "prompt_eval_count": 20,
            "eval_count": 10,
            "total_duration": 1000,
        }

    with client_for([layout(), region("මව්බස"), region("info@nie.lk")], messages, value) as client:
        provider = ConsensusSourceReadingProvider(
            configuration(value), client=client, qwen_transport=local
        )
        result = provider.with_recorder(events.append).with_renderer(renderer).understand(value)
    assert result.machine is not None
    assert result.machine.state == "machine_ready"
    assert result.machine.human_verified is False
    assert result.content.education.claims == ()
    assert len(local_messages) == 2
    parsed = [event for event in events if event["event"] == "parsed"]
    assert len(parsed) == 4
    assert {event["reader"] for event in parsed} == {"qwen", "openai"}
    for event in parsed:
        supplied = cast(dict[str, Any], event["input"])
        assert supplied["render_dpi"] == 400
        assert supplied["render_metadata"]["dpi"] == 400
    assert len({event["input_fingerprint"] for event in parsed}) == 2
    assert result.accounting.input_tokens == 300
    assert result.accounting.output_tokens == 150


def test_missing_local_reader_stops_before_any_paid_layout_or_source_call(tmp_path: Path) -> None:
    renderer, value = prepared_request(tmp_path)
    messages: list[dict[str, Any]] = []
    local_calls = 0

    def unavailable(*args: object) -> dict[str, object]:
        nonlocal local_calls
        local_calls += 1
        raise ConnectionRefusedError("local runtime unavailable")

    with client_for([], messages, value) as client:
        provider = ConsensusSourceReadingProvider(
            configuration(value), client=client, qwen_transport=unavailable, renderer=renderer
        )
        with pytest.raises(SourceExecutionBlockedError, match="qwen_runtime_unavailable"):
            provider.understand(value)
    assert messages == []
    assert local_calls == 1


def test_runtime_change_mid_page_stops_new_calls_and_retains_known_accounting(
    tmp_path: Path,
) -> None:
    from tests.test_source_reading_qwen import response

    renderer, value = prepared_request(tmp_path)
    messages: list[dict[str, Any]] = []
    events: list[dict[str, object]] = []
    checks = 0
    image_calls = 0

    def local(url: str, payload: dict[str, object] | None, timeout: float) -> dict[str, object]:
        nonlocal checks, image_calls
        if url.endswith("/api/tags"):
            checks += 1
            if checks == 3:
                return {"models": [{"name": "qwen3-vl:8b", "digest": "b" * 64}]}
        metadata = runtime_response(url)
        if metadata is not None:
            return metadata
        image_calls += 1
        return response()

    with client_for([layout(), region("මව්බස")], messages, value) as client:
        provider = ConsensusSourceReadingProvider(
            configuration(value),
            client=client,
            qwen_transport=local,
            renderer=renderer,
            recorder=events.append,
        )
        with pytest.raises(
            SourceExecutionBlockedError, match="qwen_model_digest_mismatch"
        ) as failed:
            provider.understand(value)
    assert len(messages) == 2
    assert image_calls == 1
    assert failed.value.accounting is not None
    assert failed.value.accounting.input_tokens == 200
    assert failed.value.accounting.output_tokens == 100
    assert events[-1]["event"] == "failed"
    assert events[-1]["failure_code"] == "qwen_model_digest_mismatch"
