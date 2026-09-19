"""Provider-neutral witness alignment.

Ported from `apps/api/src/exam_guru_api/documents/source_consensus.py`
(decision D10: ported, not imported, so the old module can be deleted whole).
Dependency-free on purpose — it must run in every reader environment.

The rule this encodes: one witness disagreeing about one operator pins *that
operator*. It must never make the whole region uncertain, and the readings are
never averaged or blended.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Mapping

TOKEN = re.compile(r"\S+")
OPERATORS = frozenset({"+", "-", "*", "/", "=", "<", ">", "×", "÷", "±", "x", "X"})
MAX_TEXT = 100_000
MAX_TOKENS = 20_000

Kind = Literal["text", "number", "operator", "url", "email", "identifier", "structure"]
CRITICAL_KINDS = frozenset({"number", "operator", "url", "email", "identifier"})
# An all-caps Latin run is an acronym, a code or a proper name - OBIHIRO, JICA,
# NIE. A substitution there is silent, plausible and damaging, so it is treated
# as critical rather than as ordinary prose. Seen for real: DeepSeek read the
# printed OBIHIRO as ORHRO.
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9./-]{2,}$")


def tokens(value: str) -> list[tuple[str, int]]:
    """(token, line index) over the NFC view. The stored text stays untouched."""

    if len(value) > MAX_TEXT:
        raise ValueError("source alignment text exceeds its bound")
    return [
        (token, line)
        for line, text in enumerate(unicodedata.normalize("NFC", value).splitlines())
        for token in TOKEN.findall(text)
    ]


def kind_of(left: str, right: str) -> Kind:
    values = (left, right)
    if any("@" in value for value in values):
        return "email"
    if any(value.startswith(("www.", "http://", "https://")) for value in values):
        return "url"
    if any(value in OPERATORS for value in values if value):
        return "operator"
    if any(value and value[0].isdigit() for value in values):
        return "number"
    if any(IDENTIFIER.fullmatch(value) for value in values if value):
        return "identifier"
    return "text"


@dataclass(frozen=True)
class Variant:
    value: str
    readers: tuple[str, ...]


@dataclass(frozen=True)
class DisagreementCell:
    kind: Kind
    line: int
    token_index: int
    variants: tuple[Variant, ...]
    critical: bool
    character_positions: tuple[int, ...]

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "line": self.line,
            "token_index": self.token_index,
            "critical": self.critical,
            "character_positions": list(self.character_positions),
            "variants": [
                {"value": variant.value, "readers": list(variant.readers)}
                for variant in self.variants
            ],
        }


@dataclass
class DisagreementMap:
    readers: tuple[str, ...]
    missing_readers: tuple[str, ...]
    cells: tuple[DisagreementCell, ...] = ()
    critical_conflict: bool = False
    agreement_ratio: float = 1.0

    def to_json(self) -> dict:
        return {
            "readers": list(self.readers),
            "missing_readers": list(self.missing_readers),
            "critical_conflict": self.critical_conflict,
            "agreement_ratio": round(self.agreement_ratio, 4),
            "cells": [cell.to_json() for cell in self.cells],
        }


def _columns(
    anchor: list[tuple[str, int]], other: list[tuple[str, int]]
) -> list[tuple[int | None, int | None]]:
    """Aligned columns that keep a substitution paired.

    A replaced token must line up with the token it replaced, otherwise `476`
    against `470` looks like a whole missing token instead of one wrong digit.
    """

    left = [token for token, _ in anchor]
    right = [token for token, _ in other]
    pairs: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs.extend((i, j) for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
            continue
        if tag == "replace":
            shared = min(i2 - i1, j2 - j1)
            pairs.extend((i1 + k, j1 + k) for k in range(shared))
            pairs.extend((i, None) for i in range(i1 + shared, i2))
            pairs.extend((None, j) for j in range(j1 + shared, j2))
            continue
        pairs.extend((i, None) for i in range(i1, i2))
        pairs.extend((None, j) for j in range(j1, j2))
    return pairs


def build_disagreement_map(readings: Mapping[str, str]) -> DisagreementMap:
    """Align every witness at token level, then at character level in a conflict.

    A witness that produced nothing is recorded as missing, never invented.
    """

    if not readings:
        raise ValueError("a disagreement map needs at least one witness")
    readers = tuple(sorted(readings))
    missing = tuple(name for name in readers if not readings[name].strip())
    present = [name for name in readers if name not in missing]
    table = {name: tokens(readings[name]) for name in present}
    if len(present) < 2:
        return DisagreementMap(readers=readers, missing_readers=missing)

    # Anchor on the longest reading so one runaway witness cannot redefine the
    # region's shape for everybody else.
    anchor_name = max(present, key=lambda name: (len(table[name]), name))
    anchor = table[anchor_name]
    slots: list[dict[str, str]] = [{} for _ in range(len(anchor))]
    trailing: dict[int, dict[str, str]] = {}
    for name in present:
        if name == anchor_name:
            for index, (token, _) in enumerate(anchor):
                slots[index][name] = token
            continue
        for left, right in _columns(anchor, table[name]):
            if left is not None:
                slots[left][name] = table[name][right][0] if right is not None else ""
            elif right is not None:
                trailing.setdefault(len(slots), {})[name] = table[name][right][0]

    cells: list[DisagreementCell] = []
    for index, slot in enumerate([*slots, trailing.get(len(slots), {})]):
        if not slot:
            continue
        filled = {name: slot.get(name, "") for name in present}
        if len(set(filled.values())) < 2:
            continue
        grouped: dict[str, list[str]] = {}
        for name, value in sorted(filled.items()):
            grouped.setdefault(value, []).append(name)
        competing = [value for value in grouped if value]
        kind = kind_of(competing[0], competing[-1] if len(competing) > 1 else competing[0])
        longest = max(len(value) for value in grouped)
        positions = tuple(
            position
            for position in range(longest)
            if len({value[position : position + 1] for value in grouped}) > 1
        )
        cells.append(
            DisagreementCell(
                kind=kind,
                line=anchor[index][1] if index < len(anchor) else anchor[-1][1],
                token_index=index,
                variants=tuple(
                    Variant(value=value, readers=tuple(names))
                    for value, names in sorted(grouped.items())
                ),
                critical=kind in CRITICAL_KINDS,
                character_positions=positions,
            )
        )
    compared = max(len(slots), 1)
    return DisagreementMap(
        readers=readers,
        missing_readers=missing,
        cells=tuple(cells),
        critical_conflict=any(cell.critical for cell in cells),
        agreement_ratio=round(max(0.0, compared - len(cells)) / compared, 4),
    )


@dataclass(frozen=True)
class TokenDifference:
    kind: Kind
    line: int
    left: str
    right: str


def align_source_tokens(left: str, right: str) -> tuple[TokenDifference, ...]:
    """Pairwise differences between two readings, kept at token granularity."""

    a, b = tokens(left), tokens(right)
    if len(a) + len(b) > MAX_TOKENS:
        raise ValueError("source alignment token budget exceeded")
    differences: list[TokenDifference] = []
    alignment = SequenceMatcher(None, [t[0] for t in a], [t[0] for t in b], autojunk=False)
    for operation, first, end, second, stop in alignment.get_opcodes():
        if operation == "equal":
            if [item[1] for item in a[first:end]] != [item[1] for item in b[second:stop]]:
                differences.append(
                    TokenDifference(
                        kind="structure",
                        line=a[first][1],
                        left="source_line_boundaries",
                        right="different_line_boundaries",
                    )
                )
            continue
        left_text = " ".join(item[0] for item in a[first:end])
        right_text = " ".join(item[0] for item in b[second:stop])
        differences.append(
            TokenDifference(
                kind=kind_of(left_text, right_text),
                line=a[first][1] if first < len(a) else b[second][1] if second < len(b) else 0,
                left=left_text,
                right=right_text,
            )
        )
    return tuple(differences)
