"""Measurement for reader benchmarks.

Two kinds of measurement, kept apart on purpose:

* **Referenced** — CER, insertions, deletions, substitutions and exact match
  against a human-confirmed reference. Only reported where such a reference
  exists.
* **Unreferenced** — script-profile mismatch, repetition collapse, density
  implausibility. These catch the failure that actually destroyed V1: a model
  that confidently emits fluent text the page does not contain. They are
  *signals*, never a substitute for a reference.

Nothing here normalises source text. NFC is a separate view and NFKC is never
used (it rewrites the source).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SINHALA = re.compile(r"[\u0D80-\u0DFF]")
TAMIL = re.compile(r"[\u0B80-\u0BFF]")
LATIN = re.compile(r"[A-Za-z]")
DIGITS = re.compile(r"[0-9\u0DE6-\u0DEF\u0BE6-\u0BEF]")
URL = re.compile(r"(?:https?://|www\.)[^\s\u0D80-\u0DFF]+", re.IGNORECASE)
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
MULTIPLY = re.compile(r"[x\u00d7X]")


def nfc(text: str) -> str:
    """The separate NFC view. The raw text is never replaced by it."""

    return unicodedata.normalize("NFC", text)


def edit_counts(reference: str, hypothesis: str) -> tuple[int, int, int]:
    """(substitutions, deletions, insertions) by Levenshtein backtrace."""

    a, b = reference, hypothesis
    rows, cols = len(a) + 1, len(b) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            if a[i - 1] == b[j - 1]:
                cost[i][j] = cost[i - 1][j - 1]
            else:
                cost[i][j] = 1 + min(cost[i - 1][j - 1], cost[i - 1][j], cost[i][j - 1])
    substitutions = deletions = insertions = 0
    i, j = len(a), len(b)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and cost[i][j] == cost[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + 1:
            substitutions += 1
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return substitutions, deletions, insertions


def character_error_rate(reference: str, hypothesis: str) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    substitutions, deletions, insertions = edit_counts(reference, hypothesis)
    return (substitutions + deletions + insertions) / len(reference)


# Named blocks are not enough: a reader can emit *any* script. Everything
# outside the expected set has to be counted, which is how Myanmar glyphs
# appearing on a Sinhala heading were missed the first time.
SCRIPT_BLOCKS: tuple[tuple[str, int, int], ...] = (
    ("latin", 0x0041, 0x024F),
    ("greek", 0x0370, 0x03FF),
    ("cyrillic", 0x0400, 0x04FF),
    ("hebrew", 0x0590, 0x05FF),
    ("arabic", 0x0600, 0x06FF),
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("oriya", 0x0B00, 0x0B7F),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("sinhala", 0x0D80, 0x0DFF),
    ("thai", 0x0E00, 0x0E7F),
    ("lao", 0x0E80, 0x0EFF),
    ("tibetan", 0x0F00, 0x0FFF),
    ("myanmar", 0x1000, 0x109F),
    ("georgian", 0x10A0, 0x10FF),
    ("khmer", 0x1780, 0x17FF),
    ("cjk", 0x4E00, 0x9FFF),
    ("hiragana", 0x3040, 0x309F),
    ("katakana", 0x30A0, 0x30FF),
    ("hangul", 0xAC00, 0xD7AF),
)


def _script_of(character: str) -> str | None:
    point = ord(character)
    for name, low, high in SCRIPT_BLOCKS:
        if low <= point <= high:
            return name
    return None


def script_profile(text: str) -> dict[str, int]:
    """Letters per script actually present, plus digits. Nothing is assumed."""

    profile: dict[str, int] = {}
    for character in text:
        name = _script_of(character)
        if name is not None:
            profile[name] = profile.get(name, 0) + 1
    digits = len(DIGITS.findall(text))
    if digits:
        profile["digits"] = digits
    return profile


def foreign_script_ratio(text: str, expected: str) -> float:
    """Share of letters written in a script the page is not printed in.

    Latin is tolerated inside a Sinhala page because real teacher guides carry
    URLs, units and loan words. Every other script is foreign, including ones
    nobody thought to list: the check is "not expected", not "in a bad list".
    """

    profile = {name: count for name, count in script_profile(text).items() if name != "digits"}
    letters = sum(profile.values())
    if letters == 0:
        return 0.0
    allowed = {"sinhala": {"sinhala", "latin"}, "tamil": {"tamil", "latin"}}.get(
        expected, {"sinhala", "tamil", "latin"}
    )
    foreign = sum(count for name, count in profile.items() if name not in allowed)
    return foreign / letters


def expected_script_ratio(text: str, expected: str) -> float:
    """Share of letters actually written in the page's own script.

    `foreign_script_ratio` tolerates Latin inside a Sinhala page, because real
    teacher guides carry URLs and units. That tolerance hides the opposite
    failure: a reading of a Sinhala region that is *entirely* English, which is
    fluent, contains no foreign script by that definition, and is invented.
    """

    profile = {name: count for name, count in script_profile(text).items() if name != "digits"}
    letters = sum(profile.values())
    if letters == 0:
        return 0.0
    return profile.get(expected, 0) / letters


def repetition_ratio(text: str, window: int = 12) -> float:
    """Share of the output taken up by an immediately repeated block.

    A decoder that collapses into a loop is the single most common way a small
    VLM invents page content.
    """

    stripped = "".join(text.split())
    if len(stripped) < window * 2:
        return 0.0
    repeated = 0
    index = 0
    while index + window * 2 <= len(stripped):
        if stripped[index : index + window] == stripped[index + window : index + window * 2]:
            repeated += window
            index += window
        else:
            index += 1
    return repeated / len(stripped)


def _shape(line: str) -> str:
    """Collapse a line to its typographic shape: letters -> a, digits -> 9."""

    return "".join(
        "9" if character.isdigit() else "a" if character.isalpha() else character
        for character in line.strip()
    )


def structural_repetition(text: str) -> float:
    """Share of lines that repeat an earlier line's *shape*.

    Literal repetition is easy to spot. The dangerous case is a decoder that
    enumerates a template — `$\\mathbb{I}$`, `$\\mathbb{V}$`, `$\\mathbb{W}$` —
    which is fluent, never repeats a character run, and is entirely invented.
    """

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return 0.0
    seen: dict[str, int] = {}
    for line in lines:
        shape = _shape(line)
        if len(shape) < 3:
            continue
        seen[shape] = seen.get(shape, 0) + 1
    repeated = sum(count for count in seen.values() if count > 1)
    return repeated / len(lines)


def is_degenerate(text: str) -> tuple[bool, str | None]:
    """Has the decoder stopped reading the page and started generating?"""

    if repetition_ratio(text) >= 0.5:
        return True, "decoder repeated the same character run"
    if structural_repetition(text) >= 0.6:
        return True, "decoder enumerated a repeated line template"
    return False, None


def density(text: str, pixels: int, dpi: float) -> float:
    """Characters per square inch of crop. Implausible density means invention."""

    square_inches = pixels / (dpi * dpi) if dpi else 0.0
    return len(text.strip()) / square_inches if square_inches > 0 else 0.0


def critical_tokens(text: str) -> dict[str, list[str]]:
    """The tokens where a wrong character changes meaning, not just spelling."""

    return {
        "urls": URL.findall(text),
        "emails": EMAIL.findall(text),
        "digit_runs": re.findall(r"[0-9\u0DE6-\u0DEF\u0BE6-\u0BEF]+", text),
        "multiply_signs": MULTIPLY.findall(text),
    }


@dataclass
class CropMeasurement:
    """One reader's result on one crop."""

    crop_id: str
    reader: str
    region_type: str
    chars: int
    seconds: float
    abstained: bool
    failure: str | None
    foreign_script: float
    expected_script: float
    repetition: float
    structural_repetition: float
    density: float
    scripts: dict[str, int]
    criticals: dict[str, list[str]]
    cer: float | None = None
    substitutions: int | None = None
    deletions: int | None = None
    insertions: int | None = None
    exact: bool | None = None
    critical_exact: bool | None = None
    peak_vram_bytes: int | None = None

    def to_json(self) -> dict:
        payload = {
            "crop_id": self.crop_id,
            "reader": self.reader,
            "region_type": self.region_type,
            "chars": self.chars,
            "seconds": round(self.seconds, 3),
            "abstained": self.abstained,
            "foreign_script": round(self.foreign_script, 4),
            "expected_script": round(self.expected_script, 4),
            "repetition": round(self.repetition, 4),
            "structural_repetition": round(self.structural_repetition, 4),
            "density_per_sq_in": round(self.density, 1),
            "scripts": self.scripts,
            "criticals": self.criticals,
        }
        if self.failure:
            payload["failure"] = self.failure
        if self.cer is not None:
            payload |= {
                "cer": round(self.cer, 4),
                "substitutions": self.substitutions,
                "deletions": self.deletions,
                "insertions": self.insertions,
                "exact": self.exact,
                "critical_exact": self.critical_exact,
            }
        if self.peak_vram_bytes is not None:
            payload["peak_vram_bytes"] = self.peak_vram_bytes
        return payload


@dataclass
class ReaderSummary:
    reader: str
    crops: int = 0
    ran: int = 0
    abstained: int = 0
    failed: int = 0
    degenerate: int = 0
    referenced: int = 0
    exact: int = 0
    critical_exact: int = 0
    cer_values: list[float] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)
    foreign: list[float] = field(default_factory=list)
    repetition: list[float] = field(default_factory=list)
    peak_vram_bytes: int = 0

    def add(self, measurement: CropMeasurement) -> None:
        self.crops += 1
        if measurement.failure:
            self.failed += 1
            return
        if measurement.abstained:
            self.abstained += 1
            return
        self.ran += 1
        if measurement.repetition >= 0.5 or measurement.structural_repetition >= 0.6:
            self.degenerate += 1
        self.seconds.append(measurement.seconds)
        self.foreign.append(measurement.foreign_script)
        self.repetition.append(measurement.repetition)
        if measurement.peak_vram_bytes:
            self.peak_vram_bytes = max(self.peak_vram_bytes, measurement.peak_vram_bytes)
        if measurement.cer is not None:
            self.referenced += 1
            self.cer_values.append(measurement.cer)
            self.exact += 1 if measurement.exact else 0
            self.critical_exact += 1 if measurement.critical_exact else 0

    @staticmethod
    def _mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    def to_json(self) -> dict:
        return {
            "reader": self.reader,
            "crops": self.crops,
            "ran": self.ran,
            "abstained": self.abstained,
            "failed": self.failed,
            "degenerate": self.degenerate,
            "referenced_crops": self.referenced,
            "mean_cer": self._mean(self.cer_values),
            "exact_matches": self.exact,
            "critical_token_exact": self.critical_exact,
            "mean_seconds": self._mean(self.seconds),
            "total_seconds": round(sum(self.seconds), 1) if self.seconds else 0.0,
            "mean_foreign_script": self._mean(self.foreign),
            "max_repetition": round(max(self.repetition), 4) if self.repetition else None,
            "peak_vram_mib": round(self.peak_vram_bytes / 1048576) if self.peak_vram_bytes else None,
        }


def measure(
    *,
    crop_id: str,
    reader: str,
    region_type: str,
    text: str,
    seconds: float,
    abstained: bool,
    failure: str | None,
    pixels: int,
    dpi: float,
    language: str,
    reference: str | None,
    peak_vram_bytes: int | None,
) -> CropMeasurement:
    measurement = CropMeasurement(
        crop_id=crop_id,
        reader=reader,
        region_type=region_type,
        chars=len(text),
        seconds=seconds,
        abstained=abstained,
        failure=failure,
        foreign_script=foreign_script_ratio(text, language),
        expected_script=expected_script_ratio(text, language),
        repetition=repetition_ratio(text),
        structural_repetition=structural_repetition(text),
        density=density(text, pixels, dpi),
        scripts=script_profile(text),
        criticals=critical_tokens(text),
        peak_vram_bytes=peak_vram_bytes,
    )
    if reference is not None:
        # NFC on both sides so a canonical-composition difference is not scored
        # as a reading error. The stored source text stays untouched.
        expected, produced = nfc(reference), nfc(text)
        measurement.cer = character_error_rate(expected, produced)
        subs, dels, ins = edit_counts(expected, produced)
        measurement.substitutions, measurement.deletions, measurement.insertions = subs, dels, ins
        measurement.exact = expected == produced
        measurement.critical_exact = critical_tokens(expected) == critical_tokens(produced)
    return measurement
