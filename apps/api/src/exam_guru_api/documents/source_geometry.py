import hashlib
from itertools import pairwise
from typing import Self, cast

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import Field, model_validator

from exam_guru_api.documents.page_images import PageImageLimits, _png_dimensions
from exam_guru_api.documents.source_reading import SourceLayoutRegion
from exam_guru_api.documents.understanding_contracts import (
    Key,
    RegionBounds,
    UnderstandingModel,
    _canonical_bytes,
)
from exam_guru_api.documents.understanding_verification import Checksum

_GEOMETRY_VERSION = "source-raster-geometry.v2"
Pixels = NDArray[np.uint8]


class SourceCellGeometry(UnderstandingModel):
    row: int = Field(ge=1, le=64)
    column: int = Field(ge=1, le=64)
    bounds: RegionBounds
    blank: bool
    ink_pixels: int = Field(ge=0)


class SourceTableGeometry(UnderstandingModel):
    key: Key
    bounds: RegionBounds
    rows: int = Field(ge=2, le=64)
    columns: int = Field(ge=2, le=64)
    cells: tuple[SourceCellGeometry, ...] = Field(max_length=4096)
    geometry_valid: bool

    @model_validator(mode="after")
    def complete_cells(self) -> Self:
        if len(self.cells) != self.rows * self.columns or {
            (cell.row, cell.column) for cell in self.cells
        } != {
            (row, column)
            for row in range(1, self.rows + 1)
            for column in range(1, self.columns + 1)
        }:
            raise ValueError("geometry must represent every cell exactly once")
        return self


class SourcePageGeometry(UnderstandingModel):
    schema_version: str = _GEOMETRY_VERSION
    image_sha256: Checksum
    dpi: int = Field(ge=72, le=600)
    width: int = Field(ge=1, le=16000)
    height: int = Field(ge=1, le=16000)
    tables: tuple[SourceTableGeometry, ...] = Field(max_length=16)
    regions: tuple[SourceLayoutRegion, ...] = Field(max_length=128)
    unassigned_ink_pixels: int = Field(ge=0)
    findings: tuple[str, ...] = Field(max_length=32)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


def _bounds(x: int, y: int, right: int, bottom: int, width: int, height: int) -> RegionBounds:
    return RegionBounds(
        left=max(0, x) / width,
        top=max(0, y) / height,
        right=min(width, right) / width,
        bottom=min(height, bottom) / height,
    )


def _centres(values: NDArray[np.int64], threshold: float, gap: int) -> list[int]:
    indices = np.flatnonzero(values >= threshold)
    if not len(indices):
        return []
    groups = np.split(indices, np.flatnonzero(np.diff(indices) > gap) + 1)
    return [round(float(np.mean(group))) for group in groups]


def _grids(ink: Pixels, *, dpi: int) -> list[SourceTableGeometry]:
    height, width = ink.shape
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(16, width // 40)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(16, height // 40), 1), np.uint8))
    mask = cv2.bitwise_or(horizontal, vertical)
    expanded = cv2.dilate(mask, np.ones((11, 11), np.uint8))
    count, labels, components, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    retained = np.ones(count, dtype=np.bool_)
    retained[0] = False
    retained[np.unique(labels[mask != 0])] = False
    near_line = np.bincount(labels[(expanded != 0) & (ink != 0)], minlength=count)
    intersections = cv2.bitwise_and(
        cv2.dilate(horizontal, np.ones((11, 11), np.uint8)),
        cv2.dilate(vertical, np.ones((11, 11), np.uint8)),
    )
    near_corner = np.bincount(labels[(intersections != 0) & (ink != 0)], minlength=count)
    for index, component in enumerate(components[1:], 1):
        _, _, component_width, component_height, area = (int(value) for value in component)
        if (
            max(component_width, component_height) >= max(24, dpi // 10)
            and min(component_width, component_height) <= 3
            and near_line[index] >= area * 0.9
        ) or (component_width <= 2 and component_height <= 2 and near_corner[index] == area):
            retained[index] = False
    detached = np.where(retained[labels], ink, 0).astype(np.uint8)
    body = cv2.bitwise_or(cv2.bitwise_and(ink, cv2.bitwise_not(expanded)), detached)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rectangles = sorted(
        (cv2.boundingRect(contour) for contour in contours), key=lambda r: (r[1], r[0])
    )
    tables: list[SourceTableGeometry] = []
    for x, y, w, h in rectangles:
        if w < max(50, dpi // 3) or h < max(40, dpi // 4):
            continue
        gap = max(2, round(dpi / 30))
        xs = _centres(np.count_nonzero(vertical[y : y + h, x : x + w], axis=0), h * 0.65, gap)
        ys = _centres(np.count_nonzero(horizontal[y : y + h, x : x + w], axis=1), w * 0.65, gap)
        if not 3 <= len(xs) <= 65 or not 3 <= len(ys) <= 65:
            continue
        if min(np.diff(xs)) < 6 or min(np.diff(ys)) < 6:
            continue
        if len(tables) == 16:
            raise ValueError("source table geometry exceeds its bound")
        padding = max(3, round(dpi / 100))
        cells: list[SourceCellGeometry] = []
        valid = True
        for row, (top, bottom) in enumerate(pairwise(ys), 1):
            for column, (left, right) in enumerate(pairwise(xs), 1):
                a, b, c, d = x + left, y + top, x + right, y + bottom
                interior = body[b + padding : d - padding, a + padding : c - padding]
                if interior.size == 0:
                    valid = False
                count = int(np.count_nonzero(interior))
                for px, py in ((a, b), (a, d), (c, b), (c, d)):
                    window = mask[max(0, py - 2) : py + 3, max(0, px - 2) : px + 3]
                    if not np.any(window):
                        valid = False
                cells.append(
                    SourceCellGeometry(
                        row=row,
                        column=column,
                        bounds=_bounds(a, b, c, d, width, height),
                        blank=interior.size > 0 and count == 0,
                        ink_pixels=count,
                    )
                )
        tables.append(
            SourceTableGeometry(
                key=f"table_{len(tables)}",
                bounds=_bounds(x + xs[0], y + ys[0], x + xs[-1], y + ys[-1], width, height),
                rows=len(ys) - 1,
                columns=len(xs) - 1,
                cells=tuple(cells),
                geometry_valid=valid,
            )
        )
    return tables


def _text_regions(ink: Pixels, colour: Pixels, *, dpi: int) -> list[SourceLayoutRegion]:
    height, width = ink.shape
    joined = cv2.morphologyEx(
        ink,
        cv2.MORPH_CLOSE,
        np.ones((max(2, round(dpi / 120)), max(8, round(dpi * 0.055))), np.uint8),
    )
    _, _, components, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    boxes = sorted(
        (tuple(int(value) for value in component[:4]) for component in components[1:]),
        key=lambda r: (r[1], r[0]),
    )
    padding = max(4, round(dpi / 36))
    merged: list[list[int]] = []
    for x, y, w, h in boxes:
        if w < 3 or h < 3 or cv2.countNonZero(ink[y : y + h, x : x + w]) < 4:
            continue
        current = [
            max(0, x - padding),
            max(0, y - padding),
            min(width, x + w + padding),
            min(height, y + h + padding),
        ]
        match = None
        for index in range(len(merged) - 1, -1, -1):
            a, b, c, d = merged[index]
            overlap = min(c, current[2]) - max(a, current[0])
            close_line = current[1] <= d + max(padding, round(dpi * 0.035))
            bounded_height = max(d, current[3]) - b <= dpi * 0.9
            vertical_overlap = min(d, current[3]) - max(b, current[1])
            same_line = (
                vertical_overlap >= min(d - b, current[3] - current[1]) * 0.6
                and overlap >= -dpi * 0.12
            )
            next_line = (
                overlap >= min(c - a, current[2] - current[0]) * 0.65
                and close_line
                and bounded_height
            )
            if same_line or next_line:
                match = index
                break
        if match is None:
            merged.append(current)
        else:
            a, b, c, d = merged[match]
            merged[match] = [
                min(a, current[0]),
                min(b, current[1]),
                max(c, current[2]),
                max(d, current[3]),
            ]
    if len(merged) > 128:
        raise ValueError("source text-region geometry exceeds its bound")
    regions: list[SourceLayoutRegion] = []
    for index, (x, y, right, bottom) in enumerate(sorted(merged, key=lambda r: (r[1], r[0]))):
        pixels = colour[y:bottom, x:right]
        chromatic = np.max(pixels, axis=2).astype(np.int16) - np.min(pixels, axis=2)
        coloured = float(np.count_nonzero(chromatic > 55)) / max(1, chromatic.size)
        visual = coloured > 0.15 and bottom - y > dpi * 0.25
        regions.append(
            SourceLayoutRegion(
                key=f"region_{index}",
                kind="illustration" if visual else "paragraph",
                reading_order=index,
                parent_key=None,
                bounds=_bounds(x, y, right, bottom, width, height),
            )
        )
    return regions


def _remove_page_frame(ink: Pixels, dpi: int) -> Pixels:
    height, width = ink.shape
    _, labels, components, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    margin = max(8, round(dpi / 24))
    for index, component in enumerate(components[1:], 1):
        _, _, w, h, area = (int(value) for value in component)
        if w < width * 0.95 or h < height * 0.95:
            continue
        interior = int(
            np.count_nonzero(labels[margin : height - margin, margin : width - margin] == index)
        )
        if interior <= area * 0.1:
            ink[labels == index] = 0
    return ink


def detect_source_geometry(image_png: bytes, *, dpi: int) -> SourcePageGeometry:
    if type(dpi) is not int or not 72 <= dpi <= 600:
        raise ValueError("invalid source render DPI")
    width, height = _png_dimensions(image_png, PageImageLimits())
    cv2.setNumThreads(1)
    colour = cv2.imdecode(np.frombuffer(image_png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if colour is None or colour.shape[:2] != (height, width):
        raise ValueError("source geometry image dimensions changed")
    colour = cast(Pixels, colour)
    gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
    ink = cast(
        Pixels,
        cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15
        ),
    )
    ink = _remove_page_frame(ink, dpi)
    tables = _grids(ink, dpi=dpi)
    remaining = ink.copy()
    for table in tables:
        box = table.bounds
        padding = max(3, round(dpi / 72))
        x, y = max(0, int(box.left * width) - padding), max(0, int(box.top * height) - padding)
        right, bottom = (
            min(width, int(box.right * width) + padding),
            min(height, int(box.bottom * height) + padding),
        )
        remaining[y:bottom, x:right] = 0
    regions = _text_regions(remaining, colour, dpi=dpi)
    for region in regions:
        box = region.bounds
        remaining[
            int(box.top * height) : round(box.bottom * height),
            int(box.left * width) : round(box.right * width),
        ] = 0
    unassigned = int(np.count_nonzero(remaining))
    findings = []
    if any(not table.geometry_valid for table in tables):
        findings.append("table_geometry_ambiguous")
    if unassigned > max(100, int(np.count_nonzero(ink) * 0.005)):
        findings.append("unassigned_source_marks")
    if not tables and not regions and np.any(ink):
        findings.append("missing_source_regions")
    return SourcePageGeometry(
        image_sha256=hashlib.sha256(image_png).hexdigest(),
        dpi=dpi,
        width=width,
        height=height,
        tables=tuple(tables),
        regions=tuple(regions),
        unassigned_ink_pixels=unassigned,
        findings=tuple(findings),
    )
