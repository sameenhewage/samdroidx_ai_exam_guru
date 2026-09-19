"""Deterministic checks on the primary reading, before any OCR is consulted.

These run on the text itself. They do not need a second opinion, they cannot
hallucinate, and they catch the mistakes a careful reader still makes: a
bracket left open, a digit glued to a letter, a mixed-script word.

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

    This is the deterministic replacement for what cross-reader disagreement
    used to catch. Seen for real: page 186 region r001 has 26 printed lines
    and was first transcribed with 13.
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

BULLET = re.compile(r"(?m)^\s*(?:[\u2022\u25cf\u25cb\u00b7*\-\u2013]|\(?\d{1,2}[.)])\s")


def _blocks(text: str) -> int:
    """Paragraph-ish blocks: runs of lines separated by a blank line."""

    blocks = [part for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
    return len(blocks)


def audit_warnings(
    primary_text: str, readings: dict[str, str], *, rejected: dict[str, str]
) -> list[Finding]:
    """What the audit-only local readers noticed, phrased as warnings.

    Their text is never a candidate and is frequently garbage. The only thing
    they contribute is *suspicion*, and suspicion is enough to make a human
    look again. Page 186 is the reason this exists: three regions of the
    primary reading were seriously wrong and already human-confirmed, and
    broad disagreement from these readers is what surfaced it (D16).

    Bad OCR text stays bad evidence. Strong disagreement is still a signal.
    """

    findings = [
        Finding("reader-rejected", f"{reader}: {reason}") for reader, reason in rejected.items()
    ]
    primary_lines = len([line for line in primary_text.split("\n") if line.strip()])
    primary_blocks = _blocks(primary_text)
    primary_bullets = len(BULLET.findall(primary_text))

    for reader, text in sorted(readings.items()):
        if not text.strip():
            continue

        ratio = len(text) / max(len(primary_text), 1)
        if ratio >= 3:
            findings.append(
                Finding(
                    "reader-over-generated",
                    f"{reader} produced {len(text)} characters against the primary "
                    f"reading's {len(primary_text)} ({ratio:.1f}x)",
                )
            )
        elif ratio <= 0.34:
            findings.append(
                Finding(
                    "primary-may-be-longer-than-page",
                    f"{reader} produced only {len(text)} characters against the primary "
                    f"reading's {len(primary_text)}; check the primary reading did not "
                    "run past the region",
                )
            )

        # Truncation cuts lines off the end, so the readers see more lines than
        # were written down. This is what caught page 186 r001.
        reader_lines = len([line for line in text.split("\n") if line.strip()])
        if primary_lines and reader_lines >= primary_lines * 1.5 + 2:
            findings.append(
                Finding(
                    "primary-may-be-truncated",
                    f"{reader} read {reader_lines} lines where the primary reading has "
                    f"{primary_lines}; the primary reading may have stopped part-way",
                )
            )

        reader_blocks = _blocks(text)
        if abs(reader_blocks - primary_blocks) >= 3:
            findings.append(
                Finding(
                    "paragraph-count-disagreement",
                    f"{reader} sees {reader_blocks} paragraph blocks against the primary "
                    f"reading's {primary_blocks}",
                )
            )

        reader_bullets = len(BULLET.findall(text))
        if abs(reader_bullets - primary_bullets) >= 2:
            findings.append(
                Finding(
                    "list-coverage-disagreement",
                    f"{reader} sees {reader_bullets} list items against the primary "
                    f"reading's {primary_bullets}",
                )
            )

    return findings
