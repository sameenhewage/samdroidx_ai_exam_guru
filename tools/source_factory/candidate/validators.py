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


def validate_primary(text: str, *, region_type: str, language: str = "sinhala") -> list[Finding]:
    """Everything deterministically suspicious about one primary reading."""

    findings: list[Finding] = []
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


def audit_warnings(
    primary_text: str, readings: dict[str, str], *, rejected: dict[str, str]
) -> list[Finding]:
    """What the audit-only local readers noticed, phrased as warnings.

    Their text is never a candidate. The only thing they contribute is
    suspicion, and suspicion is enough to make a human look.
    """

    findings = [
        Finding("reader-rejected", f"{reader}: {reason}") for reader, reason in rejected.items()
    ]
    for reader, text in readings.items():
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
                    "reader-under-generated",
                    f"{reader} produced only {len(text)} characters against the primary "
                    f"reading's {len(primary_text)}",
                )
            )
    return findings
