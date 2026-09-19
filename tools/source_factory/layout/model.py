"""Layout region contract for Source V2 Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SCHEMA_VERSION = "1.0.0"

RegionType = Literal["text", "heading", "figure", "table", "decorative", "unknown"]

REGION_TYPES: tuple[RegionType, ...] = (
    "text",
    "heading",
    "figure",
    "table",
    "decorative",
    "unknown",
)


@dataclass(frozen=True)
class Box:
    """Pixel bounding box in rendered-page coordinates, x1/y1 exclusive."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def cx_int(self) -> int:
        return (self.x0 + self.x1) // 2

    def cy_int(self) -> int:
        return (self.y0 + self.y1) // 2

    def union(self, other: Box) -> Box:
        return Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersects(self, other: Box) -> bool:
        return not (
            self.x1 <= other.x0
            or other.x1 <= self.x0
            or self.y1 <= other.y0
            or other.y1 <= self.y0
        )

    def contains(self, other: Box, tolerance: int = 0) -> bool:
        return (
            self.x0 - tolerance <= other.x0
            and self.y0 - tolerance <= other.y0
            and self.x1 + tolerance >= other.x1
            and self.y1 + tolerance >= other.y1
        )

    def pad(self, amount: int, bounds: Box | None = None) -> Box:
        padded = Box(self.x0 - amount, self.y0 - amount, self.x1 + amount, self.y1 + amount)
        if bounds is None:
            return padded
        return Box(
            max(padded.x0, bounds.x0),
            max(padded.y0, bounds.y0),
            min(padded.x1, bounds.x1),
            min(padded.y1, bounds.y1),
        )

    def as_list(self) -> list[int]:
        return [self.x0, self.y0, self.x1, self.y1]

    @staticmethod
    def hull(boxes: list[Box]) -> Box:
        if not boxes:
            raise ValueError("cannot take the hull of zero boxes")
        first = boxes[0]
        for box in boxes[1:]:
            first = first.union(box)
        return first


def horizontal_overlap(left: Box, right: Box) -> int:
    return max(0, min(left.x1, right.x1) - max(left.x0, right.x0))


def vertical_overlap(top: Box, bottom: Box) -> int:
    return max(0, min(top.y1, bottom.y1) - max(top.y0, bottom.y0))


@dataclass
class Region:
    """One candidate layout region on a page."""

    id: str
    type: RegionType
    bbox: Box
    reading_order: int = -1
    parent: str | None = None
    column: int | None = None
    column_count: int | None = None
    band: int | None = None
    line_count: int = 0
    evidence: dict[str, float | int | str | bool] = field(default_factory=dict)

    def to_json(self) -> dict:
        payload: dict = {
            "id": self.id,
            "type": self.type,
            "bbox": self.bbox.as_list(),
            "reading_order": self.reading_order,
            "parent": self.parent,
            "column": self.column,
            "column_count": self.column_count if self.column is not None else None,
            "band": self.band,
            "line_count": self.line_count,
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        return payload


@dataclass
class PageLayout:
    """Layout segmentation result for one rendered page."""

    document_id: str
    page_number: int
    width: int
    height: int
    dpi: float
    image_sha256: str
    detector_version: str
    regions: list[Region] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "document_id": self.document_id,
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "image_sha256": self.image_sha256,
            "detector_version": self.detector_version,
            "regions": [region.to_json() for region in self.regions],
            "diagnostics": self.diagnostics,
        }
