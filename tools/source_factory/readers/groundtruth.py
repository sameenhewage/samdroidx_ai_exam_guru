"""References used to score readers.

A reference here is **not** Verified Source Content and never becomes it. It is
an independent scoring reference held beside the pipeline, exactly so that a
reader cannot be graded against its own output.

Each entry records how it was established:

* `human` — a person read the crop against the original page. Only this level
  may be quoted as a human-confirmed CER.
* `certain-glyph` — machine-established but restricted to content whose exact
  form is unambiguous at 300 dpi: digits, Latin words, URLs, punctuation. These
  are the critical tokens where V1 actually broke, and they are checkable by
  anyone who opens the crop.

A Sinhala prose crop with no reference is still useful: it is scored on the
unreferenced signals only, and its CER is reported as unavailable rather than
guessed.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.source_factory.layout.corpus import Document

LEVELS = ("human", "certain-glyph")


def path(document: Document) -> Path:
    return document.folder / "readers" / "groundtruth.json"


def load(document: Document) -> dict[str, dict]:
    target = path(document)
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    entries = payload.get("references", {})
    for crop_id, entry in entries.items():
        if entry.get("level") not in LEVELS:
            raise SystemExit(f"reference {crop_id} has an unknown level {entry.get('level')!r}")
        if "text" not in entry and not entry.get("must_contain"):
            raise SystemExit(f"reference {crop_id} asserts nothing")
    return entries


def token_checks(entry: dict) -> tuple[list[str], list[str]]:
    """Exact substrings the reading must and must not contain.

    This is how a crop of Sinhala prose can still be scored on the parts that
    are unambiguous — a date, a measurement, a URL — without pretending to a
    full human transcription of the prose around them.
    """

    return list(entry.get("must_contain", [])), list(entry.get("must_not_contain", []))


def save(document: Document, references: dict[str, dict]) -> Path:
    target = path(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "note": (
                    "Scoring references only. Never Verified Source Content. "
                    "'human' means a person compared the crop with the original page."
                ),
                "references": references,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def human_only(references: dict[str, dict]) -> dict[str, dict]:
    return {key: value for key, value in references.items() if value.get("level") == "human"}
