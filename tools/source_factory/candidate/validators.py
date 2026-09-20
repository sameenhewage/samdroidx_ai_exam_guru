"""Deterministic checks on the one primary reading.

These run on the text itself and on the geometry it came from. There is no
second reader to consult: they do not need one, they cannot hallucinate, and
they catch the mistakes a careful reader still makes — a bracket left open, a
digit glued to a letter, a mixed-script word, a region transcribed part-way.

A validator never edits the text. It reports, and a report is enough to make
the region require human attention.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Scripts that legitimately appear inside a Sinhala teacher guide.
SINHALA = re.compile(r"[\u0D80-\u0DFF]")
LATIN = re.compile(r"[A-Za-z]")
DIGITS = re.compile(r"[0-9\u0DE6-\u0DEF]")

PAIRS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = {value: key for key, value in PAIRS.items()}

# A word with Sinhala and Latin letters welded together is almost always a
# transcription slip rather than something the page prints.
MIXED_WORD = re.compile(r"\S*[\u0D80-\u0DFF]\S*[A-Za-z]\S*|\S*[A-Za-z]\S*[\u0D80-\u0DFF]\S*")
CONTROL = re.compile(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]")
# Placeholder text a transcriber might leave behind by accident.
PLACEHOLDER = re.compile(r"(?i)\b(TODO|TBD|FIXME|XXX|\?\?\?|<[a-z ]+>)\b")


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def validate_primary(
    text: str,
    *,
    region_type: str,
    language: str = "sinhala",
    layout_lines: int | None = None,
) -> list[Finding]:
    """Everything deterministically suspicious about one primary reading."""

    findings: list[Finding] = []
    findings += _coverage(text, layout_lines=layout_lines, region_type=region_type)
    if not text.strip():
        return findings

    if CONTROL.search(text):
        findings.append(
            Finding("control-character", "text contains a control character")
        )
    if "\ufffd" in text:
        findings.append(
            Finding("replacement-character", "text contains U+FFFD; mark uncertain instead")
        )
    if text != unicodedata.normalize("NFC", text):
        findings.append(
            Finding("not-nfc", "text is not in NFC; stored text must match what was printed")
        )

    placeholder = PLACEHOLDER.search(text)
    if placeholder:
        findings.append(
            Finding("placeholder", f"looks like leftover placeholder text: {placeholder.group()!r}")
        )

    stack: list[str] = []
    for character in text:
        if character in PAIRS:
            stack.append(character)
        elif character in CLOSERS:
            if not stack or stack[-1] != CLOSERS[character]:
                findings.append(
                    Finding("unbalanced-bracket", f"unmatched {character!r}")
                )
                break
            stack.pop()
    else:
        if stack:
            findings.append(
                Finding("unbalanced-bracket", f"unclosed {''.join(stack)!r}")
            )

    if language == "sinhala":
        for match in MIXED_WORD.finditer(text):
            word = match.group()
            # A unit like "20cm" or a bulleted Latin gloss is fine; a Sinhala
            # word with Latin letters inside it is not.
            if SINHALA.search(word) and LATIN.search(word) and not DIGITS.search(word):
                findings.append(
                    Finding("mixed-script-word", f"Sinhala and Latin welded together: {word!r}")
                )
                break

    if region_type in {"text", "heading"} and not SINHALA.search(text):
        if LATIN.search(text):
            findings.append(
                Finding(
                    "no-expected-script",
                    "a Sinhala-page text region with no Sinhala at all; confirm it really is "
                    "printed in Latin",
                )
            )

    return findings


DIAGRAM_TYPES = frozenset({"figure", "table", "decorative"})


def _coverage(text: str, *, layout_lines: int | None, region_type: str) -> list[Finding]:
    """Did the reading actually cover the region, or stop part-way?

    The layout counts text lines geometrically, from ink, with no reading
    involved. Comparing that to the number of transcribed lines catches the
    one failure a careful reader still makes and cannot see: transcribing the
    top of a long region and stopping.

    Seen for real: page 186 region r001 has 26 printed lines and was first
    transcribed with 13.
    """

    if layout_lines is None or layout_lines <= 0:
        return []
    # A diagram's labels are scattered around the drawing, so the geometric
    # line count says nothing about how many label lines there should be.
    # Treating that as a transcription failure would cry wolf on every figure.
    if region_type in DIAGRAM_TYPES:
        return []
    written = len([line for line in text.split("\n") if line.strip()])
    if written == 0:
        return [
            Finding(
                "region-not-transcribed",
                f"the layout found {layout_lines} lines of text here but nothing was written",
            )
        ]
    # Wrapped lines and joined lines both shift the count a little; a third
    # of the region missing is not a rounding difference.
    if written < layout_lines * 0.67:
        return [
            Finding(
                "under-transcribed",
                f"{written} lines written against {layout_lines} printed lines; "
                "the reading may have stopped part-way through the region",
            )
        ]
    if written > layout_lines * 1.5 + 2:
        return [
            Finding(
                "over-transcribed",
                f"{written} lines written against {layout_lines} printed lines",
            )
        ]
    return []
