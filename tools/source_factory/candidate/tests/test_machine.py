# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest==8.4.2"]
# ///
"""Rules the Machine Candidate must never break.

One canonical crop, one primary reading by the executing agent, one candidate.
There is no second reader to corroborate, outvote or contradict it, so every
rule here is about carrying that single reading through untouched and making
the right things reach a human.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.source_factory.candidate.machine import (  # noqa: E402
    MachineCandidate,
    PrimaryReading,
    build,
)


def candidate(
    text: str,
    region_type: str = "text",
    uncertainty: tuple[dict, ...] = (),
    layout_lines: int | None = None,
) -> MachineCandidate:
    return build(
        primary=PrimaryReading(
            region_id="p156-r002",
            region_type=region_type,
            text=text,
            uncertainty=uncertainty,
            layout_lines=layout_lines,
        )
    )


def test_the_candidate_text_is_the_primary_reading_byte_for_byte() -> None:
    seen = "පළමු කියවීම\n\n  දෙවන පේළිය  "
    assert candidate(seen).text == seen, "the sealed reading must never be rewritten"


def test_a_clean_reading_needs_no_human_attention() -> None:
    result = candidate("පාසල් වත්තේ හෝ ආසන්න පරිසරයේ හෝ ශාකවල විවිධත්වය සොයා බලන්න.")
    assert not result.abstained
    assert result.validation_findings == []
    assert not result.requires_human_attention


def test_the_reason_names_the_executing_agent_and_never_a_model() -> None:
    reason = candidate("කියවූ පෙළ").reason
    assert "executing agent" in reason
    assert "canonical crop" in reason


def test_a_blank_primary_reading_abstains_with_its_recorded_reason() -> None:
    result = candidate(
        "",
        region_type="figure",
        uncertainty=({"kind": "non-text", "detail": "line drawing, no printed text"},),
    )
    assert result.abstained
    assert result.text == ""
    assert "line drawing" in result.reason
    assert result.requires_human_attention


def test_a_blank_reading_with_no_recorded_reason_still_abstains() -> None:
    result = candidate("   ", region_type="decorative")
    assert result.abstained
    assert "no text" in result.reason


def test_primary_uncertainty_forces_review() -> None:
    result = candidate(
        "a reading",
        uncertainty=({"kind": "illegible", "detail": "one glyph is smudged"},),
    )
    assert result.uncertain, "the agent said it could not read part of this"
    assert result.requires_human_attention
    assert any("smudged" in note for note in result.validation_findings)


def test_a_deterministic_finding_forces_review_without_changing_the_text() -> None:
    result = candidate("දණ්ඩ චුමිබක (Bar magnet")
    assert result.text == "දණ්ඩ චුමිබක (Bar magnet"
    assert result.requires_human_attention
    assert any("unbalanced-bracket" in note for note in result.validation_findings)


def test_a_reading_that_stopped_part_way_is_caught_by_geometry() -> None:
    """The deterministic replacement for what cross-reader noise used to catch."""

    result = candidate("එක් පේළියක්\nදෙවන පේළිය", layout_lines=26)
    assert any("under-transcribed" in note for note in result.validation_findings)
    assert result.requires_human_attention


def test_the_serialised_candidate_carries_no_reader_ensemble_fields() -> None:
    payload = candidate("කියවූ පෙළ").to_json()
    assert set(payload) == {
        "region_id",
        "region_type",
        "text",
        "abstained",
        "reason",
        "validation_findings",
        "uncertain",
        "requires_human_attention",
    }


def test_the_builder_accepts_nothing_but_the_primary_reading() -> None:
    """A second reader cannot be smuggled back in through the call signature."""

    with pytest.raises(TypeError):
        build(  # type: ignore[call-arg]
            primary=PrimaryReading(region_id="p156-r002", region_type="text", text="x"),
            witnesses=[{"reader": "sinhala-deepseek", "text": "y"}],
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
