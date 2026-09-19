"""Primary reading records and their fidelity rules.

Nothing here interprets the page. The only job is to hold exactly what was
printed, plus an honest account of what could not be read.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = "2.0.0"
PROVENANCE = "primary-agent-reading"
READER_VERSION = "primary-agent-reading.v2"

REGION_ID = re.compile(r"^p[0-9]{3}-r[0-9]{3}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")

REGION_TYPES = frozenset(
    {"text", "heading", "figure", "table", "decorative", "unknown"}
)
UNCERTAINTY_KINDS = frozenset(
    {
        "illegible",
        "ambiguous-glyph",
        "cut-off",
        "non-text",
        "layout-doubt",
        "spacing-doubt",
        "table-structure",
    }
)


class PrimaryReadingError(Exception):
    """A primary reading broke a fidelity rule."""


@dataclass(frozen=True)
class Uncertainty:
    kind: str
    detail: str
    excerpt: str | None = None

    def to_json(self) -> dict:
        payload = {"kind": self.kind, "detail": self.detail}
        if self.excerpt is not None:
            payload["excerpt"] = self.excerpt
        return payload


@dataclass(frozen=True)
class Cell:
    """One printed table cell. A blank cell stays blank."""

    row: int
    column: int
    bbox: tuple[int, int, int, int]
    exact_text: str
    uncertainty: tuple[Uncertainty, ...] = ()

    def to_json(self) -> dict:
        payload = {
            "row": self.row,
            "column": self.column,
            "bbox": list(self.bbox),
            "exact_text": self.exact_text,
            "blank": not self.exact_text.strip(),
            "uncertain": bool(self.uncertainty),
        }
        if self.uncertainty:
            payload["uncertainty_reason"] = [item.to_json() for item in self.uncertainty]
        return payload


@dataclass(frozen=True)
class Table:
    bbox: tuple[int, int, int, int]
    rows: int
    columns: int
    cells: tuple[Cell, ...]

    def to_json(self) -> dict:
        return {
            "bbox": list(self.bbox),
            "rows": self.rows,
            "columns": self.columns,
            "cells": [cell.to_json() for cell in self.cells],
        }


@dataclass(frozen=True)
class PrimaryRegion:
    region_id: str
    region_type: str
    bbox: tuple[int, int, int, int]
    reading_order: int
    exact_text: str
    source_image_sha256: str
    language: str = "sinhala"
    uncertainty: tuple[Uncertainty, ...] = ()
    crop_sha256: str | None = None
    table: Table | None = None

    @property
    def blank(self) -> bool:
        return not self.exact_text.strip()

    def to_json(self) -> dict:
        payload = {
            "region_id": self.region_id,
            "region_type": self.region_type,
            "bbox": list(self.bbox),
            "reading_order": self.reading_order,
            "language": self.language,
            "crop_sha256": self.crop_sha256,
            "source_image_sha256": self.source_image_sha256,
            "provenance": PROVENANCE,
            "exact_text": self.exact_text,
            "uncertain": bool(self.uncertainty),
            "status": "unverified",
        }
        if self.uncertainty:
            payload["uncertainty_reason"] = [item.to_json() for item in self.uncertainty]
        if self.table is not None:
            payload["table"] = self.table.to_json()
        return payload


@dataclass
class PrimaryPage:
    document_id: str
    page_number: int
    source_sha256: str
    image_sha256: str
    render_dpi: float
    language: str = "sinhala"
    notes: str = ""
    read_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    regions: list[PrimaryRegion] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "document_id": self.document_id,
            "page_number": self.page_number,
            "source_sha256": self.source_sha256,
            "image_sha256": self.image_sha256,
            "render_dpi": self.render_dpi,
            "language": self.language,
            "provenance": PROVENANCE,
            "read_at": self.read_at,
            "reader_version": READER_VERSION,
            "notes": self.notes,
            "regions": [region.to_json() for region in self.regions],
        }


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(page: dict) -> None:
    """Structural and fidelity checks, run on every write.

    A contract only checked in tests drifts, and a primary reading is the one
    artefact in the pipeline that nothing downstream can re-derive.
    """

    if page.get("schema_version") != SCHEMA_VERSION:
        raise PrimaryReadingError(f"unexpected schema_version {page.get('schema_version')!r}")
    if page.get("provenance") != PROVENANCE:
        raise PrimaryReadingError(
            f"provenance must be {PROVENANCE!r}; a primary reading is not OCR output"
        )
    for key in ("source_sha256", "image_sha256"):
        if not SHA256.fullmatch(page.get(key, "")):
            raise PrimaryReadingError(f"{key} must be a sha256 hex digest")
    if not isinstance(page.get("page_number"), int) or page["page_number"] < 1:
        raise PrimaryReadingError("page_number must be a positive integer")
    if not 72 <= float(page.get("render_dpi", 0)) <= 1200:
        raise PrimaryReadingError("render_dpi must be between 72 and 1200")

    regions = page.get("regions")
    if not isinstance(regions, list) or not regions:
        raise PrimaryReadingError("a primary reading must contain at least one region")

    seen_ids: set[str] = set()
    orders: list[int] = []
    for region in regions:
        region_id = region.get("region_id", "")
        if not REGION_ID.fullmatch(region_id):
            raise PrimaryReadingError(f"bad region_id {region_id!r}")
        if region_id in seen_ids:
            raise PrimaryReadingError(f"duplicate region_id {region_id}")
        seen_ids.add(region_id)
        if region.get("region_type") not in REGION_TYPES:
            raise PrimaryReadingError(
                f"{region_id}: unknown region_type {region.get('region_type')!r}"
            )
        if region.get("provenance") != PROVENANCE:
            raise PrimaryReadingError(f"{region_id}: provenance must be {PROVENANCE!r}")
        if not SHA256.fullmatch(region.get("source_image_sha256", "")):
            raise PrimaryReadingError(f"{region_id}: source_image_sha256 must be a digest")
        bbox = region.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            raise PrimaryReadingError(f"{region_id}: bbox must be [x0, y0, x1, y1]")
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise PrimaryReadingError(f"{region_id}: bbox is empty or inverted")
        if region.get("status") != "unverified":
            raise PrimaryReadingError(
                f"{region_id}: a primary reading is never self-verifying"
            )
        text = region.get("exact_text")
        if not isinstance(text, str):
            raise PrimaryReadingError(f"{region_id}: exact_text must be a string")
        _check_fidelity(region_id, region, text)
        if region.get("table") is not None:
            _check_table(region_id, region["table"])
        orders.append(region.get("reading_order", -1))

    if sorted(orders) != list(range(len(orders))):
        raise PrimaryReadingError(
            f"reading_order must be a dense 0..{len(orders) - 1} sequence, got {sorted(orders)}"
        )


def _check_uncertainty(region_id: str, reasons: list) -> None:
    for item in reasons:
        if item.get("kind") not in UNCERTAINTY_KINDS:
            raise PrimaryReadingError(f"{region_id}: unknown uncertainty kind {item.get('kind')!r}")
        if not str(item.get("detail", "")).strip():
            raise PrimaryReadingError(f"{region_id}: an uncertainty must say what was unclear")


def _check_fidelity(region_id: str, region: dict, text: str) -> None:
    uncertain = region.get("uncertain")
    reasons = region.get("uncertainty_reason", [])
    if not isinstance(uncertain, bool):
        raise PrimaryReadingError(f"{region_id}: uncertain must be a boolean")
    if uncertain and not reasons:
        raise PrimaryReadingError(
            f"{region_id}: uncertain is true but no reason was recorded; "
            "say what could not be read rather than guessing"
        )
    if reasons and not uncertain:
        raise PrimaryReadingError(f"{region_id}: uncertainty recorded but uncertain is false")
    _check_uncertainty(region_id, reasons)

    # A region with nothing written down has to say why. Silence is the one
    # thing that could pass for a reading without being one.
    if not text.strip() and not uncertain:
        raise PrimaryReadingError(
            f"{region_id}: empty exact_text needs an uncertainty entry saying why "
            "(a figure carries no text; say so rather than leaving it blank)"
        )

    if text != unicodedata.normalize("NFC", unicodedata.normalize("NFC", text)):
        raise PrimaryReadingError(f"{region_id}: exact_text is not stable under NFC")
    if "\ufffd" in text:
        raise PrimaryReadingError(
            f"{region_id}: exact_text contains U+FFFD; record an illegible uncertainty instead"
        )


def _check_table(region_id: str, table: dict) -> None:
    rows, columns = table.get("rows"), table.get("columns")
    if not isinstance(rows, int) or rows < 1 or not isinstance(columns, int) or columns < 1:
        raise PrimaryReadingError(f"{region_id}: a table needs at least one row and column")
    seen: set[tuple[int, int]] = set()
    for cell in table.get("cells", []):
        position = (cell.get("row"), cell.get("column"))
        if not (0 <= position[0] < rows and 0 <= position[1] < columns):
            raise PrimaryReadingError(f"{region_id}: cell {position} is outside the grid")
        if position in seen:
            raise PrimaryReadingError(f"{region_id}: duplicate cell {position}")
        seen.add(position)
        blank = cell.get("blank")
        if blank is not (not str(cell.get("exact_text", "")).strip()):
            raise PrimaryReadingError(
                f"{region_id}: cell {position} marks blank={blank} but its text disagrees; "
                "a visually blank cell is never filled in"
            )
        _check_uncertainty(region_id, cell.get("uncertainty_reason", []))


def write(path: Path, page: PrimaryPage) -> dict:
    payload = page.to_json()
    validate(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate(payload)
    return payload
