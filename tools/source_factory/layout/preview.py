"""Annotated layout previews and contact sheets.

Previews are visual proof for Phase 1. They are written under the gitignored
`.exam-guru-data` area because they contain rendered source material.
"""

from __future__ import annotations

import cv2
import numpy as np

from tools.source_factory.layout.model import PageLayout

COLOURS: dict[str, tuple[int, int, int]] = {  # BGR
    "text": (40, 160, 40),
    "heading": (0, 140, 255),
    "figure": (200, 60, 60),
    "table": (180, 0, 180),
    "decorative": (0, 200, 220),
    "unknown": (120, 120, 120),
}


def annotate(image_bgr: np.ndarray, layout: PageLayout, max_side: int = 1400) -> np.ndarray:
    canvas = image_bgr.copy()
    if canvas.ndim == 2:
        canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    scale = min(1.0, max_side / max(canvas.shape[0], canvas.shape[1]))
    thickness = max(2, int(3 / max(scale, 0.05)))
    font_scale = max(0.9, 1.6 / max(scale, 0.05) * 0.55)

    for region in layout.regions:
        colour = COLOURS.get(region.type, COLOURS["unknown"])
        box = region.bbox
        cv2.rectangle(canvas, (box.x0, box.y0), (box.x1, box.y1), colour, thickness)
        label = f"{region.reading_order}:{region.type[:4]}"
        if region.column is not None:
            label += f" c{region.column + 1}/{region.column_count}"
        (text_w, text_h), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        origin_y = max(box.y0, text_h + 6)
        cv2.rectangle(
            canvas,
            (box.x0, origin_y - text_h - 6),
            (box.x0 + text_w + 8, origin_y + 4),
            colour,
            -1,
        )
        cv2.putText(
            canvas,
            label,
            (box.x0 + 4, origin_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )

    _draw_reading_order(canvas, layout, thickness)
    preview = cv2.resize(
        canvas,
        (int(canvas.shape[1] * scale), int(canvas.shape[0] * scale)),
        interpolation=cv2.INTER_AREA,
    )
    return _with_legend(preview, layout)


def _draw_reading_order(canvas: np.ndarray, layout: PageLayout, thickness: int) -> None:
    ordered = sorted(layout.regions, key=lambda region: region.reading_order)
    for first, second in zip(ordered, ordered[1:], strict=False):
        start = (int(first.bbox.cx), int(first.bbox.cy))
        end = (int(second.bbox.cx), int(second.bbox.cy))
        cv2.arrowedLine(canvas, start, end, (255, 0, 255), max(1, thickness // 2), tipLength=0.02)


def _with_legend(preview: np.ndarray, layout: PageLayout) -> np.ndarray:
    bar_height = 58
    legend = np.full((bar_height, preview.shape[1], 3), 255, dtype=np.uint8)
    counts: dict[str, int] = {}
    for region in layout.regions:
        counts[region.type] = counts.get(region.type, 0) + 1
    cursor = 10
    header = f"p{layout.page_number}  regions={len(layout.regions)}"
    cv2.putText(legend, header, (cursor, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    cursor = 10
    for name, colour in COLOURS.items():
        if name not in counts:
            continue
        cv2.rectangle(legend, (cursor, 34), (cursor + 16, 48), colour, -1)
        text = f"{name} {counts[name]}"
        cv2.putText(
            legend, text, (cursor + 22, 47), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 0, 0), 1, cv2.LINE_AA
        )
        (width, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        cursor += 34 + width
    return np.vstack([legend, preview])


ELEMENT_COLOURS: dict[str, tuple[int, int, int]] = {  # BGR
    "text": (40, 160, 40),
    "artwork": (200, 60, 60),
    "container": (0, 165, 255),
    "rule_h": (180, 0, 180),
    "rule_v": (180, 80, 0),
    "frame": (0, 0, 0),
    "label": (0, 220, 255),
}


def annotate_elements(image_bgr: np.ndarray, elements, max_side: int = 1400) -> np.ndarray:
    """Debug view of the raw structural elements before any grouping."""

    canvas = image_bgr.copy()
    scale = min(1.0, max_side / max(canvas.shape[0], canvas.shape[1]))
    thickness = max(2, int(2 / max(scale, 0.05)))
    for element in elements:
        if element.kind == "graphic":
            key = "container" if element.container else "artwork"
        elif element.is_text:
            key = "label" if element.figure_label else "text"
        else:
            key = element.kind
        box = element.box
        cv2.rectangle(
            canvas, (box.x0, box.y0), (box.x1, box.y1), ELEMENT_COLOURS[key], thickness
        )
    return cv2.resize(
        canvas,
        (int(canvas.shape[1] * scale), int(canvas.shape[0] * scale)),
        interpolation=cv2.INTER_AREA,
    )


def contact_sheet(
    images: list[tuple[str, np.ndarray]], columns: int = 4, cell_width: int = 460
) -> np.ndarray:
    if not images:
        raise ValueError("no images for the contact sheet")
    cells: list[np.ndarray] = []
    for label, image in images:
        scale = cell_width / image.shape[1]
        cell = cv2.resize(
            image, (cell_width, int(image.shape[0] * scale)), interpolation=cv2.INTER_AREA
        )
        if cell.ndim == 2:
            cell = cv2.cvtColor(cell, cv2.COLOR_GRAY2BGR)
        banner = np.full((26, cell_width, 3), 245, dtype=np.uint8)
        cv2.putText(
            banner, label, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA
        )
        cells.append(np.vstack([banner, cell]))

    height = max(cell.shape[0] for cell in cells)
    padded = []
    for cell in cells:
        pad = np.full((height - cell.shape[0], cell_width, 3), 255, dtype=np.uint8)
        padded.append(np.vstack([cell, pad]) if pad.size else cell)
    rows = []
    for start in range(0, len(padded), columns):
        chunk = padded[start : start + columns]
        while len(chunk) < columns:
            chunk.append(np.full((height, cell_width, 3), 255, dtype=np.uint8))
        rows.append(np.hstack(chunk))
    return np.vstack(rows)
