"""Reading a stored `reason` string as the diagnostics it actually contains.

The reason strings here are copied from the real Machine Candidates for
Grade 5 Sinhala pages 156 and 186. Splitting them is what lets the review card
put provenance and validator noise behind a collapsed disclosure instead of
above the picture a teacher came to look at.
"""

from __future__ import annotations

from exam_guru_api.source_v2.evidence import diagnostics

PRIMARY = "primary reading by the executing agent from the canonical crop"
# p186-r003, verbatim.
FLAGGED = f"{PRIMARY}; 2 deterministic finding(s); primary flagged ['spacing-doubt', 'non-text']"
# p186-r002, verbatim, including the semicolon inside the prose.
NO_TEXT = (
    "primary reading found no text: Line-art figure only (a foam block, a ring magnet, "
    "a paper butterfly, a pin, two sticks and a length of thread). Inspected the whole "
    "crop in quadrants at 5x; no printed label, caption, digit or letter appears anywhere "
    "inside the region, so the printed text is genuinely empty."
)


def test_the_provenance_clause_is_never_a_finding() -> None:
    """It is true of every region, so it says nothing about this one."""

    assert diagnostics(PRIMARY).findings == ()
    assert diagnostics(PRIMARY).uncertainty == ()


def test_deterministic_findings_and_declared_uncertainty_are_separated() -> None:
    split = diagnostics(FLAGGED)
    assert split.findings == ("2 deterministic finding(s)",)
    assert split.uncertainty == ("spacing-doubt", "non-text")


def test_a_repeated_flag_is_reported_once_in_order() -> None:
    split = diagnostics(f"{PRIMARY}; primary flagged ['spacing-doubt', 'spacing-doubt']")
    assert split.uncertainty == ("spacing-doubt",)


def test_an_abstention_becomes_the_no_text_code_and_keeps_its_prose() -> None:
    """The prose contains its own semicolon; splitting on it would truncate."""

    split = diagnostics(NO_TEXT)
    assert split.uncertainty == ("no-text",)
    assert len(split.findings) == 1
    assert split.findings[0].startswith("Line-art figure only")
    assert split.findings[0].endswith("genuinely empty.")


def test_the_full_reason_is_always_carried_unchanged() -> None:
    """Nothing is hidden by a parser that guessed wrong."""

    assert diagnostics(NO_TEXT).reason == NO_TEXT
    assert diagnostics(FLAGGED).reason == FLAGGED


def test_a_single_clause_reason_is_not_repeated_as_its_own_finding() -> None:
    assert diagnostics("human correction; awaiting confirmation").findings == (
        "human correction",
        "awaiting confirmation",
    )
    assert diagnostics("layout-doubt").findings == ()


def test_an_empty_reason_produces_nothing() -> None:
    assert diagnostics("") == diagnostics("   ")
    assert diagnostics("").findings == ()
    assert diagnostics("").uncertainty == ()
    assert diagnostics("").reason == ""
