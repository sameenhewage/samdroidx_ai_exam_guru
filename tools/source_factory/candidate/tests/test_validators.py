# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest==8.4.2"]
# ///
"""Deterministic checks on the primary reading, and OCR reduced to warnings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.source_factory.candidate import selection  # noqa: E402
from tools.source_factory.candidate.machine import PRIMARY_READER, PrimaryReading, Witness, build  # noqa: E402
from tools.source_factory.candidate.validators import (  # noqa: E402
    audit_warnings,
    validate_primary,
)


def codes(text: str, region_type: str = "text") -> set[str]:
    return {item.code for item in validate_primary(text, region_type=region_type)}


def test_clean_sinhala_prose_raises_nothing() -> None:
    assert codes("පාසල් වත්තේ හෝ ආසන්න පරිසරයේ හෝ ශාකවල විවිධත්වය සොයා බලන්න.") == set()


def test_an_unclosed_bracket_is_reported() -> None:
    assert "unbalanced-bracket" in codes("දණ්ඩ චුමිබක (Bar magnet")


def test_a_balanced_bracket_is_not_reported() -> None:
    assert "unbalanced-bracket" not in codes("දණ්ඩ චුමිබක (Bar magnet )")


def test_sinhala_and_latin_welded_into_one_word_is_reported() -> None:
    assert "mixed-script-word" in codes("පාසල්school වත්තේ")


def test_a_unit_like_20cm_is_not_a_mixed_script_error() -> None:
    assert "mixed-script-word" not in codes("රෙජිෆෝමි කැල්ලක් (20 cm x 6 cm)")


def test_leftover_placeholder_text_is_reported() -> None:
    assert "placeholder" in codes("පළමු සංඛ්‍යාව TODO ලියන්න.")


def test_a_replacement_character_is_reported() -> None:
    assert "replacement-character" in codes("පාසල් \ufffd වත්තේ")


def test_an_all_latin_text_region_on_a_sinhala_page_is_questioned() -> None:
    assert "no-expected-script" in codes("Resource :JICA OBIHIRO Presentation Manual - 2007")


def test_a_reader_that_over_generates_raises_a_warning() -> None:
    warnings = audit_warnings("141", {"sinhala-lightonocr": "x" * 1200}, rejected={})
    assert any(item.code == "reader-over-generated" for item in warnings)


def test_a_rejected_reader_is_carried_through_as_a_warning() -> None:
    warnings = audit_warnings("text", {}, rejected={"sinhala-deepseek": "foreign script"})
    assert [item.code for item in warnings] == ["reader-rejected"]


# --- the policy itself --------------------------------------------------------


def test_both_local_readers_are_audit_only() -> None:
    """D15. Neither may be promoted back to a source of text."""

    assert selection.ACTIVE.audit_only("sinhala-deepseek")
    assert selection.ACTIVE.audit_only("sinhala-lightonocr")
    assert selection.ACTIVE.tier(PRIMARY_READER) is selection.Tier.PRIMARY


@pytest.mark.parametrize("reader", ["sinhala-deepseek", "sinhala-lightonocr"])
def test_an_audit_reader_cannot_supply_the_candidate_text(reader: str) -> None:
    result = build(
        primary=PrimaryReading(
            region_id="p156-r002",
            region_type="text",
            text="what the page says",
        ),
        witnesses=[Witness(reader=reader, text="what the model says", rank=1)],
    )
    assert result.text == "what the page says"
    assert result.selected_source == PRIMARY_READER


def test_validator_findings_force_human_attention() -> None:
    result = build(
        primary=PrimaryReading(
            region_id="p156-r002",
            region_type="text",
            text="දණ්ඩ චුමිබක (Bar magnet",
        ),
        witnesses=[],
    )
    assert result.requires_human_attention
    assert any("unbalanced-bracket" in note for note in result.validation_findings)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
