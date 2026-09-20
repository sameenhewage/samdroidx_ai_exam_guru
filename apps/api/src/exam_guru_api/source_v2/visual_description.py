"""Descriptions of source visuals. Derived knowledge, never source text.

A `visual_only` region carries meaning that no transcription captures, so a
description is useful — for search, for a teacher scanning a page, later for an
image embedding's caption. But a description is written *about* the source, not
read *off* it, and D18 is explicit that it can never become Verified Source
Content or be written into `exact_text`.

Two rules follow, and both are enforced here rather than trusted:

1. **Language follows the source.** A Sinhala page gets a Sinhala description.
   Describing Sinhala material in English quietly translates the curriculum and
   makes the description useless to the teacher who has to check it.

2. **Only what is visible inside the crop.** The surrounding page usually says
   things the picture does not: `(20 cm x 6 cm)`, a part name, a caption from
   the paragraph above. Copying those into the description asserts that they
   are legible in the image when they are not. That is a provenance failure,
   not a wording preference, because a later reader cannot tell the difference.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

SINHALA = re.compile(r"[\u0D80-\u0DFF]")
TAMIL = re.compile(r"[\u0B80-\u0BFF]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")

#: A measurement stated as fact: `20 cm`, `6cm`, `2 m`, `15 mm`, `20 cm x 6 cm`.
MEASUREMENT = re.compile(r"\d+\s*(?:cm|mm|m|km|kg|g|ml|l|°|inch|in)\b", re.IGNORECASE)
#: A quoted label lifted from somewhere. Curly quotes are listed deliberately:
#: printed Sinhala material uses them, so a description written against a real
#: page will contain them and a straight-quote-only pattern would miss it.
QUOTED_LABEL = re.compile(
    r"[\"\u201c\u201d'\u2018\u2019]([^\"\u201c\u201d'\u2018\u2019]{2,})[\"\u201c\u201d'\u2018\u2019]"
)


class DescriptionRefusedError(Exception):
    """The description asserted something the crop cannot support."""


class Script(StrEnum):
    SINHALA = "sinhala"
    TAMIL = "tamil"
    ENGLISH = "english"


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _script_of(language: str) -> Script:
    normalised = language.strip().lower()
    if normalised.startswith("sinhala") or normalised in {"si", "sin"}:
        return Script.SINHALA
    if normalised.startswith("tamil") or normalised in {"ta", "tam"}:
        return Script.TAMIL
    return Script.ENGLISH


def _has_script(text: str, script: Script) -> bool:
    if script is Script.SINHALA:
        return bool(SINHALA.search(text))
    if script is Script.TAMIL:
        return bool(TAMIL.search(text))
    return bool(LATIN_WORD.search(text))


def validate(
    description: str,
    *,
    language: str,
    visible_text: str = "",
) -> list[Finding]:
    """Check a visual description against its source and its crop.

    `visible_text` is the text actually transcribed from *this* crop — the
    figure's own labels and caption. Anything the description asserts that is
    not in there and not in the description's own judgement words is being
    imported from elsewhere on the page.
    """

    findings: list[Finding] = []
    body = unicodedata.normalize("NFC", description).strip()
    if not body:
        return [Finding("empty-description", "a visual description must say something")]

    script = _script_of(language)
    if not _has_script(body, script):
        findings.append(
            Finding(
                "wrong-language",
                f"the source is {language} but the description contains no {script} script; "
                "describe the visual in the language of the material",
            )
        )

    inside = unicodedata.normalize("NFC", visible_text)

    # A measurement is a claim about the picture. It is only honest if the
    # picture prints it.
    findings.extend(
        Finding(
            "unverifiable-measurement",
            f"{match.group(0)!r} is stated as if measured from the image, but it is "
            "not printed inside this crop; a dimension from the surrounding page is "
            "not something the picture shows",
        )
        for match in MEASUREMENT.finditer(body)
        if match.group(0) not in inside
    )

    # A quoted label claims "this text appears in the image".
    for match in QUOTED_LABEL.finditer(body):
        label = match.group(1).strip()
        if label and label not in inside:
            findings.append(
                Finding(
                    "unverifiable-label",
                    f"the description quotes {label!r} as a label, but that text is not "
                    "printed inside this crop",
                )
            )

    return findings


def assert_describable(*, source_kind: str, verified: bool, has_canonical_visual: bool) -> None:
    """A description may only be written about a real, verified source visual.

    Refusing here keeps the ordering honest: the picture is confirmed as source
    first, and only then described. A description of an unverified region would
    be derived knowledge built on something nobody has checked.
    """

    if source_kind not in {"visual_only", "visual_with_text"}:
        raise DescriptionRefusedError(f"{source_kind} has no source visual to describe")
    if not has_canonical_visual:
        raise DescriptionRefusedError(
            "the canonical crop for this region is missing; there is nothing to describe"
        )
    if not verified:
        raise DescriptionRefusedError(
            "the visual is not verified source content yet; describe it after a human "
            "has confirmed it, not before"
        )
