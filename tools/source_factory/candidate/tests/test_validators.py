# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest==8.4.2"]
# ///
"""Deterministic checks on the one primary reading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.source_factory.candidate.machine import PrimaryReading, build  # noqa: E402
from tools.source_factory.candidate.validators import validate_primary  # noqa: E402


def codes(text: str, region_type: str = "text", layout_lines: int | None = None) -> set[str]:
    return {
        item.code
        for item in validate_primary(
            text, region_type=region_type, layout_lines=layout_lines
        )
    }


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


# --- coverage against the deterministic layout -------------------------------


def test_a_region_transcribed_part_way_is_reported() -> None:
    assert "under-transcribed" in codes("one line\nsecond line", layout_lines=26)


def test_a_region_the_layout_found_ink_in_but_nobody_wrote_is_reported() -> None:
    assert "region-not-transcribed" in codes("", layout_lines=8)


def test_a_figures_scattered_labels_are_not_a_coverage_failure() -> None:
    """A drawing's labels are not lines of prose; counting them cries wolf."""

    assert codes("ලේබලය", region_type="figure", layout_lines=12) == set()


# --- the contract ------------------------------------------------------------


def test_validator_findings_force_human_attention() -> None:
    result = build(
        primary=PrimaryReading(
            region_id="p156-r002",
            region_type="text",
            text="දණ්ඩ චුමිබක (Bar magnet",
        )
    )
    assert result.requires_human_attention
    assert any("unbalanced-bracket" in note for note in result.validation_findings)


def test_a_validator_reports_and_never_rewrites() -> None:
    printed = "පාසල් \ufffd වත්තේ"
    result = build(
        primary=PrimaryReading(region_id="p156-r002", region_type="text", text=printed)
    )
    assert result.text == printed, "a finding is a warning, never a correction"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
