import hashlib
from typing import Any, Literal

import pytest

from exam_guru_api.documents.source_consensus import (
    IndependentReading,
    ReaderIdentity,
    SourceRegionInput,
    align_source_tokens,
    reconcile_region,
)
from exam_guru_api.documents.source_reading import SourceLayoutRegion, SourceRegionReading
from exam_guru_api.documents.understanding_contracts import RegionBounds
from tests.test_document_understanding_provider import request as fixture_request


def region_input(*, purpose: str = "text", language: str = "si") -> SourceRegionInput:
    source = fixture_request()
    return SourceRegionInput.model_validate(
        {
            "source": source.source,
            "region": SourceLayoutRegion(
                key="text",
                kind="paragraph",
                reading_order=0,
                parent_key=None,
                bounds=RegionBounds(left=0.0, top=0.0, right=1.0, bottom=1.0),
            ),
            "image_png": source.image_png,
            "image_sha256": hashlib.sha256(source.image_png).hexdigest(),
            "render_dpi": 300,
            "purpose": purpose,
            "language_hint": language,
        }
    )


def reading(
    source: SourceRegionInput, reader: Literal["qwen", "openai"], text: str
) -> IndependentReading:
    return IndependentReading.model_validate(
        {
            "reader": ReaderIdentity(
                reader=reader,
                provider="ollama" if reader == "qwen" else "openai",
                model="qwen3-vl:8b" if reader == "qwen" else "test-vision",
                model_version="fixture.v1",
                prompt_version="source-witness.v1",
                configuration_fingerprint="a" * 64,
            ),
            "input_fingerprint": source.fingerprint,
            "content": SourceRegionReading(
                exact_text=text, equations=(), table=None, visual_facts=(), uncertainties=()
            ),
            "input_tokens": 10,
            "output_tokens": 10,
            "latency_ms": 10,
            "cost_microusd": 0 if reader == "qwen" else 10,
        }
    )


def test_independent_agreement_is_validated_machine_evidence_not_human_trust() -> None:
    source = region_input()
    local, remote = reading(source, "qwen", "මව්බස"), reading(source, "openai", "මව්බස")
    result = reconcile_region(source, local, remote)
    assert result.state == "validated"
    assert result.selected is not None
    assert result.selected.exact_text == "මව්බස"
    assert result.witness_fingerprints == (local.fingerprint, remote.fingerprint)
    assert not hasattr(result, "verified_source")
    assert result.human_verified is False


@pytest.mark.parametrize("count", [1, 3])
def test_consensus_requires_exactly_two_witness_fingerprints(count: int) -> None:
    source = region_input()
    result = reconcile_region(
        source, reading(source, "qwen", "මව්බස"), reading(source, "openai", "මව්බස")
    )
    with pytest.raises(ValueError, match="witness_fingerprints"):
        type(result).model_validate(
            result.model_copy(update={"witness_fingerprints": ("a" * 64,) * count})
        )


def test_disagreement_never_selects_a_reader_or_synthesizes_characters() -> None:
    source = region_input()
    result = reconcile_region(
        source, reading(source, "qwen", "මව්බස"), reading(source, "openai", "මව්බිම")
    )
    assert result.state == "disagreed"
    assert result.selected is None
    assert result.differences
    assert result.region_key == "text"
    assert result.requires_reread is True


def test_alignment_preserves_critical_glyph_differences_and_line_positions() -> None:
    result = align_source_tokens("1 \u00d7 8 = 8\ninfo@nie.lk", "1 X 8 = 8\ninfo@moe.lk")
    assert any(d.kind == "operator" and d.left == "\u00d7" and d.right == "X" for d in result)
    assert any(d.kind == "email" and d.line == 1 for d in result)
    assert align_source_tokens("a\u0301", "á") == ()


@pytest.mark.parametrize(
    ("purpose", "text"),
    [
        ("math", "1 X 8 = 8"),
        ("email", "info ante lk"),
        ("url", "www nie lk"),
        ("text", ""),
        ("text", "unreadable"),
        ("text", "wmf.a mdvï"),
    ],
)
def test_agreement_cannot_bypass_deterministic_source_checks(purpose: str, text: str) -> None:
    source = region_input(purpose=purpose)
    result = reconcile_region(
        source, reading(source, "qwen", text), reading(source, "openai", text)
    )
    assert result.state == "unresolved"
    assert result.selected is None
    assert result.findings


@pytest.mark.parametrize("text", ["www.nie.lk", "info@nie.lk"])
def test_literal_contact_details_are_not_sinhala_corruption(text: str) -> None:
    source = region_input()
    result = reconcile_region(
        source, reading(source, "qwen", text), reading(source, "openai", text)
    )
    assert result.state == "validated"


def test_source_math_is_preserved_not_solved_or_corrected() -> None:
    source = region_input(purpose="math", language="und")
    result = reconcile_region(
        source,
        reading(source, "qwen", "6 \u00d7 2 = 8"),
        reading(source, "openai", "6 \u00d7 2 = 8"),
    )
    assert result.state == "validated"
    assert result.selected is not None
    assert result.selected.exact_text == "6 \u00d7 2 = 8"


@pytest.mark.parametrize("change", ["same_reader", "wrong_input"])
def test_consensus_requires_distinct_readers_of_the_exact_same_render(change: str) -> None:
    source = region_input()
    local = reading(source, "qwen", "මව්බස")
    remote = reading(source, "openai", "මව්බස")
    if change == "same_reader":
        remote = local
    else:
        remote = remote.model_copy(update={"input_fingerprint": "b" * 64})
    with pytest.raises(ValueError, match=r"independent Qwen|different page/render"):
        reconcile_region(source, local, remote)


def test_targeted_reread_resolution_keeps_initial_disagreement_provenance() -> None:
    source = region_input()
    initial = reconcile_region(
        source, reading(source, "qwen", "මව්බස"), reading(source, "openai", "මව්බිම")
    )
    result = reconcile_region(
        source, reading(source, "qwen", "මව්බස"), reading(source, "openai", "මව්බස"), previous=initial
    )
    assert result.state == "resolved_by_reread"
    assert result.previous_fingerprint == initial.fingerprint
    assert result.selected is not None
    assert result.human_verified is False


def test_read_request_has_no_channel_for_other_provider_text_or_educational_claims() -> None:
    source = region_input()
    data: dict[str, Any] = {**source.model_dump(), "image_png": source.image_png}
    for name in ("other_reader_text", "openai_reading", "qwen_reading", "education"):
        with pytest.raises(ValueError, match="Extra inputs"):
            SourceRegionInput.model_validate({**data, name: "untrusted proposed reading"})


def test_consensus_retains_raw_unicode_with_a_separate_canonical_comparison() -> None:
    source = region_input(language="en")
    raw = "a\u0301"
    result = reconcile_region(source, reading(source, "qwen", raw), reading(source, "openai", "á"))
    assert result.selected is not None
    assert result.selected.exact_text == raw


def test_source_line_boundaries_cannot_be_discarded_by_token_alignment() -> None:
    source = region_input(purpose="math", language="und")
    result = reconcile_region(
        source, reading(source, "qwen", "28\n\u00d7 8"), reading(source, "openai", "28 \u00d7 8")
    )
    assert result.state == "disagreed"
    assert result.selected is None


def test_reread_cannot_resolve_a_disagreement_from_another_page() -> None:
    source = region_input()
    initial = reconcile_region(
        source, reading(source, "qwen", "මව්බස"), reading(source, "openai", "මව්බිම")
    )
    replacement = source.model_copy(
        update={"source": source.source.model_copy(update={"page_number": 2})}
    )
    with pytest.raises(ValueError, match="another source page or region"):
        reconcile_region(
            replacement,
            reading(replacement, "qwen", "මව්බස"),
            reading(replacement, "openai", "මව්බස"),
            previous=initial,
        )
