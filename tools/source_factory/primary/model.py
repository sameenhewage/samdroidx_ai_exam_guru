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

SCHEMA_VERSION = "1.0.0"
PROVENANCE = "primary-agent-visual"
READER_VERSION = "primary-agent-visual.v1"

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
class PrimaryRegion:
    region_id: str
    region_type: str
    bbox: tuple[int, int, int, int]
    reading_order: int
    text: str
    uncertainty: tuple[Uncertainty, ...] = ()
    crop_sha256: str | None = None

    @property
    def blank(self) -> bool:
        return not self.text.strip()

    def to_json(self) -> dict:
        return {
            "region_id": self.region_id,
            "region_type": self.region_type,
            "bbox": list(self.bbox),
            "reading_order": self.reading_order,
            "crop_sha256": self.crop_sha256,
            "text": self.text,
            "uncertainty": [item.to_json() for item in self.uncertainty],
            "status": "unverified",
        }


@dataclass
class PrimaryPage:
    document_id: str
    page_number: int
    image_sha256: str
    render_sha256: str
    language: str = "sinhala"
    notes: str = ""
    read_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    regions: list[PrimaryRegion] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "document_id": self.document_id,
            "page_number": self.page_number,
            "image_sha256": self.image_sha256,
            "render_sha256": self.render_sha256,
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
            "provenance must be 'primary-agent-visual'; a primary reading is not OCR output"
        )
    for key in ("image_sha256", "render_sha256"):
        if not SHA256.fullmatch(page.get(key, "")):
            raise PrimaryReadingError(f"{key} must be a sha256 hex digest")
    if not isinstance(page.get("page_number"), int) or page["page_number"] < 1:
        raise PrimaryReadingError("page_number must be a positive integer")

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
        bbox = region.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            raise PrimaryReadingError(f"{region_id}: bbox must be [x0, y0, x1, y1]")
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise PrimaryReadingError(f"{region_id}: bbox is empty or inverted")
        if region.get("status") != "unverified":
            raise PrimaryReadingError(
                f"{region_id}: a primary reading is never self-verifying"
            )
        text = region.get("text")
        if not isinstance(text, str):
            raise PrimaryReadingError(f"{region_id}: text must be a string")
        _check_fidelity(region_id, region, text)
        orders.append(region.get("reading_order", -1))

    if sorted(orders) != list(range(len(orders))):
        raise PrimaryReadingError(
            f"reading_order must be a dense 0..{len(orders) - 1} sequence, got {sorted(orders)}"
        )


def _check_fidelity(region_id: str, region: dict, text: str) -> None:
    uncertainty = region.get("uncertainty")
    if not isinstance(uncertainty, list):
        raise PrimaryReadingError(f"{region_id}: uncertainty must be a list")
    for item in uncertainty:
        if item.get("kind") not in UNCERTAINTY_KINDS:
            raise PrimaryReadingError(f"{region_id}: unknown uncertainty kind {item.get('kind')!r}")
        if not str(item.get("detail", "")).strip():
            raise PrimaryReadingError(f"{region_id}: an uncertainty must say what was unclear")

    # A region with nothing written down has to say why. Silence is the one
    # thing that could pass for a reading without being one.
    if not text.strip() and not uncertainty:
        raise PrimaryReadingError(
            f"{region_id}: empty text needs an uncertainty entry saying why "
            "(a figure carries no text; say so rather than leaving it blank)"
        )

    # NFC is the comparison view, never the stored one. If the stored text is
    # already NFC-normalised away from what was printed we cannot tell, but we
    # can at least refuse the compatibility normalisation that destroys
    # Sinhala conjuncts and turns ﬁ into fi.
    if text != unicodedata.normalize("NFC", unicodedata.normalize("NFC", text)):
        raise PrimaryReadingError(f"{region_id}: text is not stable under NFC")
    if "\ufffd" in text:
        raise PrimaryReadingError(
            f"{region_id}: text contains U+FFFD; record an illegible uncertainty instead"
        )


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
