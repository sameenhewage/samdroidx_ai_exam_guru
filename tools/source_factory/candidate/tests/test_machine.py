# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest==8.4.2"]
# ///
"""Rules the Machine Candidate must never break."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.source_factory.candidate.alignment import build_disagreement_map  # noqa: E402
from tools.source_factory.candidate.machine import Witness, build  # noqa: E402


def candidate(witnesses: list[Witness], region_type: str = "text"):
    return build(region_id="p156-r002", region_type=region_type, witnesses=witnesses)


def test_one_operator_conflict_pins_that_token_only() -> None:
    result = candidate(
        [
            Witness(reader="a", text="දිග 4 × 6 වේ", rank=0),
            Witness(reader="b", text="දිග 4 x 6 වේ", rank=1),
        ]
    )
    assert not result.abstained
    assert result.critical_conflict
    assert len(result.uncertain_tokens) == 1
    assert result.uncertain_tokens[0]["kind"] == "operator"
    # the rest of the region is still agreed
    assert result.agreement_ratio > 0.7


def test_candidate_text_is_always_a_real_witness_reading() -> None:
    first, second = "පළමු කියවීම", "දෙවන කියවීම"
    result = candidate(
        [Witness(reader="a", text=first, rank=0), Witness(reader="b", text=second, rank=1)]
    )
    assert result.text in {first, second}, "readings must never be blended"
    assert result.chosen_reader == "a"


def test_rank_decides_not_majority() -> None:
    result = candidate(
        [
            Witness(reader="weak-1", text="wrong", rank=5),
            Witness(reader="weak-2", text="wrong", rank=6),
            Witness(reader="measured-best", text="right", rank=0),
        ]
    )
    assert result.text == "right", "two agreeing weak witnesses must not outvote the measured best"


def test_degenerate_reading_is_rejected_not_used() -> None:
    result = candidate(
        [
            Witness(reader="looper", text="x" * 400, rank=0, repetition=0.99),
            Witness(reader="sound", text="real reading", rank=1),
        ]
    )
    assert result.chosen_reader == "sound"
    assert "looper" in result.rejected


def test_abstains_when_no_witness_is_trustworthy() -> None:
    result = candidate(
        [
            Witness(reader="looper", text="y" * 400, rank=0, structural_repetition=0.95),
            Witness(reader="broken", text="", rank=1, failed=True),
        ]
    )
    assert result.abstained
    assert result.text == ""
    assert result.chosen_reader is None
    assert set(result.rejected) == {"looper", "broken"}


def test_single_witness_is_marked_uncorroborated() -> None:
    result = candidate(
        [
            Witness(reader="only", text="a reading", rank=0),
            Witness(reader="dead", text="", rank=1, failed=True),
        ]
    )
    assert result.chosen_reader == "only"
    assert "uncorroborated" in result.reason or "nothing corroborates" in result.reason


def test_a_silent_witness_is_recorded_missing_not_invented() -> None:
    disagreement = build_disagreement_map({"a": "something", "b": "   "})
    assert disagreement.missing_readers == ("b",)
    assert disagreement.cells == ()


def test_digit_conflict_is_critical() -> None:
    disagreement = build_disagreement_map({"a": "වර්ෂය 476", "b": "වර්ෂය 470"})
    assert disagreement.critical_conflict
    kinds = {cell.kind for cell in disagreement.cells}
    assert "number" in kinds
    cell = next(cell for cell in disagreement.cells if cell.kind == "number")
    assert cell.character_positions == (2,), "only the differing digit position is pinned"


def test_url_substitution_is_critical() -> None:
    disagreement = build_disagreement_map(
        {"a": "බලන්න www.nie.lk අඩවිය", "b": "බලන්න www.moe.gov.lk අඩවිය"}
    )
    assert disagreement.critical_conflict
    assert any(cell.kind == "url" for cell in disagreement.cells)


def test_identical_readings_have_no_conflict() -> None:
    disagreement = build_disagreement_map({"a": "එකම පෙළ", "b": "එකම පෙළ"})
    assert disagreement.cells == ()
    assert disagreement.agreement_ratio == 1.0
    assert not disagreement.critical_conflict


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
