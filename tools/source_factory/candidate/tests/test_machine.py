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
from tools.source_factory.candidate.machine import (  # noqa: E402
    PRIMARY_READER,
    PrimaryReading,
    Witness,
    build,
)


def candidate(
    witnesses: list[Witness],
    region_type: str = "text",
    primary_text: str | None = None,
    uncertainty: tuple[dict, ...] = (),
):
    """The agent read the page first; the witnesses corroborate or contradict.

    Where a test does not care what the agent saw, it reads the same thing as
    the highest-ranked witness, so the test is about witness handling rather
    than about the primary reading.
    """

    if primary_text is None:
        usable = [w for w in witnesses if w.text.strip() and not w.failed]
        best = min(usable, key=lambda w: (w.rank, w.reader), default=None)
        primary_text = best.text if best else ""
    return build(
        primary=PrimaryReading(
            region_id="p156-r002",
            region_type=region_type,
            text=primary_text,
            uncertainty=uncertainty,
        ),
        witnesses=witnesses,
    )


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


def test_the_candidate_text_is_always_the_primary_reading() -> None:
    """D14. Local OCR proposes nothing; it corroborates or contradicts."""

    seen = "පළමු කියවීම"
    result = candidate(
        [
            Witness(reader="a", text="දෙවන කියවීම", rank=0),
            Witness(reader="b", text="තෙවන කියවීම", rank=1),
        ],
        primary_text=seen,
    )
    assert result.text == seen, "readings must never be blended or replaced"
    assert result.chosen_reader == PRIMARY_READER


def test_agreeing_witnesses_cannot_outvote_the_primary_reading() -> None:
    result = candidate(
        [
            Witness(reader="ocr-1", text="wrong", rank=0),
            Witness(reader="ocr-2", text="wrong", rank=1),
        ],
        primary_text="right",
    )
    assert result.text == "right"
    assert result.critical_conflict, "a flat contradiction has to reach a human"


def test_the_highest_ranked_witness_still_cannot_take_over() -> None:
    result = candidate(
        [Witness(reader="measured-best", text="ocr text", rank=0)],
        primary_text="what the page says",
    )
    assert result.text == "what the page says"
    assert result.chosen_reader == PRIMARY_READER


def test_degenerate_reading_is_rejected_as_evidence_not_used() -> None:
    result = candidate(
        [
            Witness(reader="looper", text="x" * 400, rank=0, repetition=0.99),
            Witness(reader="sound", text="real reading", rank=1),
        ],
        primary_text="real reading",
    )
    assert result.text == "real reading"
    assert "looper" in result.rejected
    assert "sound" not in result.rejected


def test_the_primary_reading_survives_every_witness_being_rejected() -> None:
    result = candidate(
        [
            Witness(reader="looper", text="y" * 400, rank=0, structural_repetition=0.95),
            Witness(reader="broken", text="", rank=1, failed=True),
        ],
        primary_text="what the page says",
    )
    assert not result.abstained, "the agent read the page; that reading still stands"
    assert result.text == "what the page says"
    assert result.chosen_reader == PRIMARY_READER
    assert set(result.rejected) == {"looper", "broken"}
    assert "uncorroborated" in result.reason


def test_a_blank_primary_reading_abstains_with_its_recorded_reason() -> None:
    result = candidate(
        [],
        region_type="figure",
        primary_text="",
        uncertainty=({"kind": "non-text", "detail": "line drawing, no printed text"},),
    )
    assert result.abstained
    assert result.text == ""
    assert "line drawing" in result.reason


def test_primary_uncertainty_forces_review() -> None:
    result = candidate(
        [Witness(reader="a", text="a reading", rank=0)],
        primary_text="a reading",
        uncertainty=({"kind": "illegible", "detail": "one glyph is smudged"},),
    )
    assert result.critical_conflict, "the agent said it could not read part of this"


def test_single_witness_is_marked_uncorroborated() -> None:
    result = candidate(
        [
            Witness(reader="only", text="a reading", rank=0),
            Witness(reader="dead", text="", rank=1, failed=True),
        ]
    )
    assert result.chosen_reader == PRIMARY_READER
    assert "no second witness" in result.reason
    assert "dead" in result.rejected


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
