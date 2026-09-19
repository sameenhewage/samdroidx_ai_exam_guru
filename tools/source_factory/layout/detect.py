"""Layout region detection for a rendered page image.

Design notes
------------
Naive whole-page vertical projection is deliberately not the primary algorithm.
The page is decomposed structurally instead:

1.  Three ink views are derived. `dark` holds strong ink, so glyph strokes
    survive on tinted panels; `mark` holds anything that is not paper, so solid
    bars, tints and artwork are captured even when they carry white text;
    `edges` is a Canny view used only to recover hairline table rules that are
    invisible inside a single-colour panel.
2.  Long straight runs become horizontal/vertical rules. A page-border frame is
    recognised by its very low fill ratio and demoted to a decorative element
    instead of swallowing the page.
3.  Connected components of `dark` are split into glyph-sized components and
    oversized graphic components. Glyphs are joined by a short horizontal
    run-length smear into *line fragments*; the smear is far narrower than any
    real gutter so a fragment can never bridge two columns.
4.  Columns are found from the coverage profile of line fragments, with figure
    labels, specks and over-wide fragments removed from the profile. Figures,
    rules and decorative bars never take part, so a figure or a full-width bar
    crossing the centre cannot collapse the columns.
5.  Elements that cross a gutter are lifted out as *straddlers*. They cut the
    region into horizontal slabs and are emitted as their own full-width
    regions, which keeps each column's prose reading top-to-bottom instead of
    being interleaved band by band.
6.  Each column is cut into blocks at leading-sized gaps and again wherever a
    row changes between text-dominant and graphic-dominant, so an illustration
    sitting directly under a paragraph is never absorbed into the prose.
7.  A block that is mostly one panel graphic and still holds prose becomes a
    container region whose interior is segmented again and linked by `parent`.
8.  Reading order is the in-order walk of that tree.

No OCR happens here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from tools.source_factory.layout.model import Box, PageLayout, Region, RegionType

DETECTOR_VERSION = "source-v2-layout-0.5.0"

# --- tunables, expressed at 300 dpi and scaled at runtime ---------------------
PAPER_PERCENTILE = 85
INK_BLOCK = 151.0  # adaptive window, wide enough to hold a whole heading glyph
INK_BIAS = 10  # how much darker than its own neighbourhood a stroke must be
MARK_DELTA = 16  # anything this far from paper counts as a mark
MARK_SATURATION = 38

SPECK_PX = 3.0
GLYPH_MAX_H = 130.0
GLYPH_MAX_W = 520.0
RULE_MIN_LEN_FRACTION = 0.10
RULE_MAX_THICKNESS = 14.0
FRAME_AREA_FRACTION = 0.55
FRAME_FILL_RATIO = 0.22

SMEAR_MIN = 14.0
SMEAR_FACTOR = 0.62
WORD_GAP_MIN_FACTOR = 1.2  # in median line heights
WORD_GAP_MAX_FACTOR = 2.5

MIN_GUTTER_FRACTION = 0.032  # of the content width
MAX_GUTTER_FRACTION = 0.280  # of the region width; wider is a layout hole
MIN_COLUMN_FRACTION = 0.150  # of the content width
WIDE_FRAGMENT_FRACTION = 0.68
COLUMN_MIN_FRAGMENTS_TOTAL = 6
COLUMN_MIN_FRAGMENTS_BEST = 4
COLUMN_BLOCKED_ROW_TOLERANCE = 0.15  # share of rows allowed to cross a gutter
COLUMN_MIN_STRADDLING_ROWS = 2
STRADDLE_GUTTER_FRACTION = 0.6

MAJOR_BAND_GAP_FACTOR = 2.2  # whitespace that separates whole layout bands
BLOCK_GAP_FACTOR = 1.05  # block gap inside a column, in median line heights
GRAPHIC_ROW_DOMINANCE = 2.0
MAX_DEPTH = 5

HEADING_HEIGHT_FACTOR = 1.28
HEADING_MAX_LINES = 3
HEADING_MAX_WIDTH_FRACTION = 0.92
BAR_MAX_HEIGHT_FRACTION = 0.060
BAR_MIN_WIDTH_FRACTION = 0.45
BAR_MIN_FILL = 0.35  # gradient bands fade out towards one end
BAR_MIN_ASPECT = 8.0
BAR_GRAPHIC_DOMINANCE = 2.0
ZONE_CLOSE_FACTOR = 2.5  # in median line heights
ZONE_TEXT_DENSITY = 0.12  # above this a zone is prose, not artwork
ZONE_PROSE_DENSITY = 0.30  # the absorbed text's own hull is packed like a paragraph
ZONE_REACH_FACTOR = 1.8  # how far outside the artwork a caption or mark may sit
CONTAINER_MIN_ASPECT = 1.5
CONTAINER_HEIGHT_SPREAD = 0.40
CONTAINER_MIN_DENSITY = 0.12
FIGURE_DOMINANCE = 1.6
PANEL_COVERAGE = 0.62
PANEL_MIN_AREA_FRACTION = 0.02
TABLE_MIN_ROWS = 3
TABLE_MIN_COLUMNS = 2
TABLE_ROW_COVERAGE = 0.55
TABLE_MIN_TEXT_DENSITY = 0.05
REGION_PAD = 4.0


@dataclass
class Element:
    """One atomic piece of page content used for structural cutting."""

    box: Box
    kind: str  # "text" | "graphic" | "rule_h" | "rule_v" | "frame"
    ink: int = 0
    figure_label: bool = False
    container: bool = False  # a graphic that is a background for prose, not artwork
    bar: bool = False  # a solid full-width rule/running head/title band
    locked: bool = False  # a grouped artwork zone; never reinterpreted as a panel

    @property
    def is_text(self) -> bool:
        return self.kind == "text"

    @property
    def is_artwork(self) -> bool:
        return self.kind == "graphic" and not self.container

    @property
    def is_rule(self) -> bool:
        return self.kind in ("rule_h", "rule_v")


@dataclass
class PageContext:
    width: int
    height: int
    scale: float
    content: Box
    median_line_height: float
    speck_area: float

    def px(self, at300: float) -> float:
        return at300 * self.scale


# --- masks --------------------------------------------------------------------


def build_masks(
    image_bgr: np.ndarray, scale: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """`ink` = strokes darker than their own neighbourhood, `mark` = not paper.

    The ink view is adaptive on purpose: a global threshold loses black text
    printed on a dark tinted bar, because the bar itself is already darker than
    the global cut.
    """

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)
    paper = float(np.percentile(smooth, PAPER_PERCENTILE))
    saturation = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)[:, :, 1]
    block = int(INK_BLOCK * scale) | 1
    ink = cv2.adaptiveThreshold(
        smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, INK_BIAS
    )
    mark = (
        (
            (np.abs(smooth.astype(np.int16) - paper) > MARK_DELTA)
            | (saturation > MARK_SATURATION)
        )
        .astype(np.uint8)
        * 255
    )
    edges = cv2.Canny(smooth, 40, 120)
    return ink, mark, edges, smooth


def detect_rules(sources: list[np.ndarray], scale: float) -> list[Element]:
    height, width = sources[0].shape
    min_h = max(24, int(width * RULE_MIN_LEN_FRACTION))
    min_v = max(24, int(height * RULE_MIN_LEN_FRACTION * 0.8))
    max_thickness = max(3, int(RULE_MAX_THICKNESS * scale))
    found: list[Element] = []
    for source in sources:
        for kind, kernel_size, min_length in (
            ("rule_h", (max(4, min_h // 2), 1), min_h),
            ("rule_v", (1, max(4, min_v // 2)), min_v),
        ):
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
            opened = cv2.morphologyEx(source, cv2.MORPH_OPEN, kernel)
            count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
            for index in range(1, count):
                x, y, w, h, area = (int(value) for value in stats[index])
                long_side, short_side = (w, h) if kind == "rule_h" else (h, w)
                if long_side < min_length or short_side > max_thickness:
                    continue
                found.append(Element(Box(x, y, x + w, y + h), kind, area))
    return _dedupe_rules(found)


def _dedupe_rules(rules: list[Element]) -> list[Element]:
    kept: list[Element] = []
    for rule in sorted(rules, key=lambda element: -element.box.area):
        duplicate = False
        for existing in kept:
            if existing.kind != rule.kind:
                continue
            overlap_w = max(0, min(existing.box.x1, rule.box.x1) - max(existing.box.x0, rule.box.x0))
            overlap_h = max(0, min(existing.box.y1, rule.box.y1) - max(existing.box.y0, rule.box.y0))
            if rule.box.area and overlap_w * overlap_h >= rule.box.area * 0.6:
                duplicate = True
                break
        if not duplicate:
            kept.append(rule)
    return kept


def _glyph_like(w: int, h: int, area: int, box: Box, scale: float, page_width: int) -> bool:
    max_h = GLYPH_MAX_H * scale
    max_w = min(GLYPH_MAX_W * scale, page_width * 0.35)
    solid = box.area > 0 and area >= box.area * 0.88
    large = max(w, h) >= 220 * scale
    return h <= max_h and w <= max_w and not (solid and large)


def glyph_elements(ink: np.ndarray, scale: float) -> list[Element]:
    """Glyph-sized components of the adaptive ink view."""

    page_width = ink.shape[1]
    speck = max(2, int(SPECK_PX * scale)) ** 2
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    glyphs: list[Element] = []
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if area < speck:
            continue
        box = Box(x, y, x + w, y + h)
        if _glyph_like(w, h, area, box, scale, page_width):
            glyphs.append(Element(box, "text", area))
    return glyphs


def graphic_elements(mark: np.ndarray, scale: float) -> tuple[list[Element], list[Element]]:
    """Oversized components of the any-mark view: panels, bars, rules and artwork.

    `mark` is used rather than `dark` so a tinted panel, a gradient bar or a
    white-on-colour running head is recovered as one whole object instead of
    only its darkest corner.
    """

    page_height, page_width = mark.shape
    page_area = page_height * page_width
    floor = max(4, int(14 * scale)) ** 2
    count, _, stats, _ = cv2.connectedComponentsWithStats(mark, connectivity=8)
    graphics: list[Element] = []
    frames: list[Element] = []
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        box = Box(x, y, x + w, y + h)
        if box.area < floor:
            continue
        if _glyph_like(w, h, area, box, scale, page_width):
            continue
        if box.area >= page_area * FRAME_AREA_FRACTION and area <= box.area * FRAME_FILL_RATIO:
            frames.append(Element(box, "frame", area))
            continue
        graphics.append(Element(box, "graphic", area))
    return graphics, frames


def mark_bars(graphics: list[Element], context: PageContext) -> None:
    """Flag solid full-width bands: running heads, footers and title bands.

    They are page furniture, never prose containers, and the adaptive ink view
    reports halo strokes around their reverse-out lettering that must not be
    mistaken for body text.
    """

    for graphic in graphics:
        thin = graphic.box.height <= context.content.height * BAR_MAX_HEIGHT_FRACTION
        wide = graphic.box.width >= context.content.width * BAR_MIN_WIDTH_FRACTION
        solid = graphic.box.area > 0 and graphic.ink >= graphic.box.area * BAR_MIN_FILL
        slender = graphic.box.height > 0 and graphic.box.width >= graphic.box.height * BAR_MIN_ASPECT
        graphic.bar = bool(thin and wide and solid and slender)


def drop_bar_interiors(fragments: list[Element], graphics: list[Element]) -> list[Element]:
    bars = [g for g in graphics if g.bar]
    if not bars:
        return fragments
    return [
        fragment
        for fragment in fragments
        if not any(_mostly_inside(fragment.box, bar.box, 0.7) for bar in bars)
    ]


def mark_containers(graphics: list[Element], fragments: list[Element], context: PageContext) -> None:
    """Decide which graphics are prose backgrounds rather than artwork.

    A tint panel, callout box or table body carries a lot of organised text.
    Artwork carries at most a few short labels.
    """

    for graphic in graphics:
        graphic.container = False
        if graphic.bar or graphic.locked:
            continue
        if graphic.box.area <= 0:
            continue
        inside = [f for f in fragments if _mostly_inside(f.box, graphic.box, 0.7)]
        meaningful = _meaningful_text(inside, context)
        if not meaningful or not _looks_like_lines(meaningful, context):
            continue
        text_area = sum(f.box.area for f in meaningful)
        rows = len(_row_clusters(meaningful, context))
        density = text_area / graphic.box.area
        # A prose panel is wider than it is tall, or deep enough to hold a
        # paragraph, and it is packed with type. An outlined diagram quadrant
        # holds the same kind of marks but far too sparsely.
        shaped = graphic.box.width >= graphic.box.height * CONTAINER_MIN_ASPECT or rows >= 4
        graphic.container = shaped and density >= CONTAINER_MIN_DENSITY


def _looks_like_lines(texts: list[Element], context: PageContext) -> bool:
    """Printed lines share a height; halftone speckle inside a photograph does not."""

    heights = np.array([element.box.height for element in texts], dtype=np.float64)
    median = float(np.median(heights))
    if median <= 0:
        return False
    if not 0.55 <= median / context.median_line_height <= 2.6:
        return False
    return float(np.std(heights)) <= median * CONTAINER_HEIGHT_SPREAD


def line_fragments(glyphs: list[Element], shape: tuple[int, int], scale: float) -> list[Element]:
    """Smear glyph components horizontally and re-group them into line fragments."""

    if not glyphs:
        return []
    median_h = float(np.median([element.box.height for element in glyphs]))
    smear = max(int(SMEAR_MIN * scale), int(median_h * SMEAR_FACTOR))
    canvas = np.zeros(shape, dtype=np.uint8)
    for element in glyphs:
        box = element.box
        canvas[box.y0 : box.y1, box.x0 : box.x1] = 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (smear, max(1, int(2 * scale))))
    smeared = cv2.dilate(canvas, kernel)
    _, labels, _, _ = cv2.connectedComponentsWithStats(smeared, connectivity=8)
    buckets: dict[int, list[Element]] = {}
    for element in glyphs:
        box = element.box
        label = int(labels[min(box.cy_int(), shape[0] - 1), min(box.cx_int(), shape[1] - 1)])
        if label == 0:
            label = int(labels[box.y0, box.x0])
        if label == 0:
            continue
        buckets.setdefault(label, []).append(element)
    return [
        Element(Box.hull([member.box for member in members]), "text", sum(m.ink for m in members))
        for members in buckets.values()
        if members
    ]


def _text_rows(fragments: list[Element]) -> list[list[Element]]:
    """Group fragments that share a printed line."""

    rows: list[list[Element]] = []
    spans: list[list[int]] = []
    for fragment in sorted(fragments, key=lambda e: e.box.cy):
        box = fragment.box
        if spans:
            low, high = spans[-1]
            overlap = min(high, box.y1) - max(low, box.y0)
            if overlap > 0.5 * min(high - low, max(1, box.height)):
                rows[-1].append(fragment)
                spans[-1] = [min(low, box.y0), max(high, box.y1)]
                continue
        rows.append([fragment])
        spans.append([box.y0, box.y1])
    return rows


def _otsu_split(values: np.ndarray) -> float:
    """Threshold between the two natural clusters of a 1-D sample."""

    top = float(values.max())
    if values.size < 8 or top <= 0:
        return float("nan")
    scaled = np.clip(values / top * 255.0, 0, 255).astype(np.uint8)
    level, _ = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(level) / 255.0 * top


def word_gap_limit(fragments: list[Element], median_line_height: float) -> float:
    """How far apart two pieces of the same printed line may sit.

    Learned from the page's own gap distribution, then clamped, so justified
    Sinhala word spacing is bridged while a real column gutter never is.
    """

    gaps: list[int] = []
    for row in _text_rows(fragments):
        ordered = sorted(row, key=lambda e: e.box.x0)
        for left, right in zip(ordered, ordered[1:], strict=False):
            gap = right.box.x0 - left.box.x1
            if gap > 0:
                gaps.append(gap)
    low = WORD_GAP_MIN_FACTOR * median_line_height
    high = WORD_GAP_MAX_FACTOR * median_line_height
    if not gaps:
        return low
    split = _otsu_split(np.array(gaps, dtype=np.float64))
    if not np.isfinite(split):
        return low
    return float(min(max(split, low), high))


def merge_line_fragments(
    fragments: list[Element],
    graphics: list[Element],
    rules: list[Element],
    context: PageContext,
) -> list[Element]:
    """Join same-line fragments across word spacing.

    Never across artwork and never across a cell rule, so a ruled table keeps
    one fragment per cell and can still be recognised as a grid.
    """

    limit = word_gap_limit(fragments, context.median_line_height)
    artwork = [g for g in graphics if g.is_artwork] + [r for r in rules if r.kind == "rule_v"]
    merged: list[Element] = []
    for row in _text_rows(fragments):
        ordered = sorted(row, key=lambda e: e.box.x0)
        current = ordered[0]
        for nxt in ordered[1:]:
            gap = nxt.box.x0 - current.box.x1
            blocked = any(
                piece.box.x1 > current.box.x1
                and piece.box.x0 < nxt.box.x0
                and piece.box.y1 > current.box.y0
                and piece.box.y0 < current.box.y1
                for piece in artwork
            )
            if 0 <= gap <= limit and not blocked:
                current = Element(current.box.union(nxt.box), "text", current.ink + nxt.ink)
                continue
            merged.append(current)
            current = nxt
        merged.append(current)
    return merged


def group_figure_zones(
    fragments: list[Element], graphics: list[Element], context: PageContext
) -> tuple[list[Element], list[Element]]:
    """Grow artwork into zones and pull in the marks and labels that belong to it.

    A diagram is rarely one connected component: circles, connectors, rows of
    little pictograms and their letters all arrive separately, and the
    pictograms look exactly like glyphs. Closing the artwork mask recovers the
    drawing as one object, and a zone is only allowed to swallow text while it
    stays far too sparse to be prose.
    """

    artwork = [g for g in graphics if g.is_artwork and not g.bar]
    if not artwork:
        return fragments, graphics

    step = 4
    shape = (context.height // step + 2, context.width // step + 2)
    mask = np.zeros(shape, dtype=np.uint8)
    for graphic in artwork:
        box = graphic.box
        mask[box.y0 // step : box.y1 // step + 1, box.x0 // step : box.x1 // step + 1] = 255
    size = max(3, int(ZONE_CLOSE_FACTOR * context.median_line_height / step))
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    remaining = [g for g in graphics if not (g.is_artwork and not g.bar)]
    absorbed_ids: set[int] = set()
    for index in range(1, count):
        x, y, w, h, _ = (int(value) for value in stats[index])
        zone = Box(x * step, y * step, (x + w) * step, (y + h) * step)
        members = [g for g in artwork if _mostly_inside(g.box, zone, 0.6)]
        if not members:
            continue
        reach = zone.pad(
            int(ZONE_REACH_FACTOR * context.median_line_height),
            Box(0, 0, context.width, context.height),
        )
        inside = [f for f in fragments if _mostly_inside(f.box, reach, 0.6)]
        text_area = sum(f.box.area for f in inside)
        density = text_area / reach.area if reach.area else 1.0
        own_hull = Box.hull([f.box for f in inside]) if inside else None
        own_density = text_area / own_hull.area if own_hull and own_hull.area else 0.0
        if inside and (density >= ZONE_TEXT_DENSITY or own_density >= ZONE_PROSE_DENSITY):
            remaining.extend(members)
            continue
        hull = Box.hull([m.box for m in members] + [f.box for f in inside])
        remaining.append(Element(hull, "graphic", sum(m.ink for m in members), locked=True))
        absorbed_ids.update(id(f) for f in inside)
    kept = [f for f in fragments if id(f) not in absorbed_ids]
    return kept, remaining


def mark_figure_labels(fragments: list[Element], graphics: list[Element]) -> None:
    """Flag text that lives inside artwork so it cannot vote on column structure."""

    for fragment in fragments:
        for graphic in graphics:
            if graphic.container or graphic.box.area < fragment.box.area * 4:
                continue
            overlap_w = max(
                0, min(fragment.box.x1, graphic.box.x1) - max(fragment.box.x0, graphic.box.x0)
            )
            overlap_h = max(
                0, min(fragment.box.y1, graphic.box.y1) - max(fragment.box.y0, graphic.box.y0)
            )
            if fragment.box.area and overlap_w * overlap_h >= fragment.box.area * 0.5:
                fragment.figure_label = True
                break


def reverse_text_heights(gray: np.ndarray, box: Box, scale: float) -> list[int]:
    """Glyph-sized light shapes inside a coloured band, i.e. white-on-colour text."""

    crop = gray[box.y0 : box.y1, box.x0 : box.x1]
    if crop.size == 0:
        return []
    level = float(np.median(crop)) + 45.0
    holes = (crop > level).astype(np.uint8) * 255
    count, _, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=8)
    speck = max(2, int(SPECK_PX * scale)) ** 2
    heights: list[int] = []
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if area < speck * 2 or h > GLYPH_MAX_H * scale or w > GLYPH_MAX_W * scale:
            continue
        if x == 0 or y == 0 or x + w >= crop.shape[1] or y + h >= crop.shape[0]:
            continue
        heights.append(h)
    return heights


# --- geometry helpers ---------------------------------------------------------


def _occupancy(spans: list[tuple[int, int]], lo: int, hi: int) -> np.ndarray:
    occupied = np.zeros(max(0, hi - lo), dtype=bool)
    for start, end in spans:
        a = max(lo, int(start)) - lo
        b = min(hi, int(end)) - lo
        if b > a:
            occupied[a:b] = True
    return occupied


def _filled_runs(occupied: np.ndarray, lo: int) -> list[tuple[int, int]]:
    if occupied.size == 0:
        return []
    padded = np.concatenate(([False], occupied, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [
        (int(start) + lo, int(end) + lo)
        for start, end in zip(edges[0::2], edges[1::2], strict=False)
    ]


def _merge_runs(runs: list[tuple[int, int]], min_gap: int) -> list[list[int]]:
    if not runs:
        return []
    merged: list[list[int]] = [list(runs[0])]
    for start, end in runs[1:]:
        if start - merged[-1][1] < min_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _y_runs(elements: list[Element], bounds: Box, min_gap: int) -> list[Box]:
    if not elements:
        return []
    occupied = _occupancy([(e.box.y0, e.box.y1) for e in elements], bounds.y0, bounds.y1)
    merged = _merge_runs(_filled_runs(occupied, bounds.y0), max(1, min_gap))
    return [Box(bounds.x0, start, bounds.x1, end) for start, end in merged]


def _inside(elements: list[Element], box: Box) -> list[Element]:
    return [e for e in elements if _mostly_inside(e.box, box)]


def _mostly_inside(inner: Box, outer: Box, ratio: float = 0.6) -> bool:
    overlap_w = max(0, min(inner.x1, outer.x1) - max(inner.x0, outer.x0))
    overlap_h = max(0, min(inner.y1, outer.y1) - max(inner.y0, outer.y0))
    if inner.area == 0:
        return overlap_w > 0 and overlap_h > 0
    return overlap_w * overlap_h >= inner.area * ratio


# --- column detection ---------------------------------------------------------


@dataclass
class ColumnSplit:
    columns: list[Box]
    gutters: list[tuple[int, int]]

    @property
    def count(self) -> int:
        return len(self.columns)


def column_profile_fragments(elements: list[Element], box: Box, context: PageContext) -> list[Element]:
    limit = box.width * WIDE_FRAGMENT_FRACTION
    return [
        element
        for element in elements
        if element.is_text
        and not element.figure_label
        and element.box.area >= context.speck_area
        and element.box.width <= limit
    ]


def detect_columns(elements: list[Element], box: Box, context: PageContext) -> ColumnSplit:
    """Columns from a row-tolerant coverage profile of body-text lines.

    The profile counts how many printed *rows* reach each column of pixels
    rather than whether any ink is present, so a single stray line crossing the
    centre — a figure caption, a wide panel line, a diagram label — cannot veto
    a gutter that every other row respects.
    """

    single = ColumnSplit([box], [])
    fragments = column_profile_fragments(elements, box, context)
    if len(fragments) < COLUMN_MIN_FRAGMENTS_TOTAL:
        return single

    rows = _text_rows(fragments)
    counts = np.zeros(max(0, box.width), dtype=np.int32)
    row_masks: list[np.ndarray] = []
    for row in rows:
        mask = _occupancy([(f.box.x0, f.box.x1) for f in row], box.x0, box.x1)
        row_masks.append(mask)
        counts += mask.astype(np.int32)
    if counts.size == 0:
        return single

    content_width = context.content.width
    min_gutter = max(8, int(content_width * MIN_GUTTER_FRACTION))
    min_column = max(8, int(content_width * MIN_COLUMN_FRACTION))
    tolerance = max(1, int(len(rows) * COLUMN_BLOCKED_ROW_TOLERANCE))

    text_span = _filled_runs(counts > 0, box.x0)
    if not text_span:
        return single
    left_edge, right_edge = text_span[0][0], text_span[-1][1]

    max_gutter = max(min_gutter, int(box.width * MAX_GUTTER_FRACTION))
    gutters = [
        (start, end)
        for start, end in _filled_runs(counts <= tolerance, box.x0)
        if min_gutter <= end - start <= max_gutter and start > left_edge and end < right_edge
    ]
    if not gutters:
        return single

    edges = [left_edge] + [(start + end) // 2 for start, end in gutters] + [right_edge]
    columns = [Box(edges[i], box.y0, edges[i + 1], box.y1) for i in range(len(edges) - 1)]
    populations = [
        sum(1 for f in fragments if column.x0 <= f.box.cx <= column.x1) for column in columns
    ]
    if any(column.width < min_column for column in columns):
        return single
    if any(population < 1 for population in populations):
        return single
    if max(populations) < COLUMN_MIN_FRAGMENTS_BEST:
        return single
    if _straddling_rows(row_masks, gutters, box.x0) < COLUMN_MIN_STRADDLING_ROWS:
        return single

    columns[0] = Box(box.x0, box.y0, columns[0].x1, box.y1)
    columns[-1] = Box(columns[-1].x0, box.y0, box.x1, box.y1)
    return ColumnSplit(columns, gutters)


def _straddling_rows(
    row_masks: list[np.ndarray], gutters: list[tuple[int, int]], origin: int
) -> int:
    """Rows with content on both sides of a gutter: evidence of a real column pair."""

    straddling = 0
    for mask in row_masks:
        for start, end in gutters:
            left = mask[: max(0, start - origin)]
            right = mask[min(mask.size, end - origin) :]
            if left.any() and right.any():
                straddling += 1
                break
    return straddling


def find_straddlers(elements: list[Element], split: ColumnSplit) -> list[Element]:
    crossing: list[Element] = []
    for element in elements:
        if element.kind == "frame":
            continue
        for gutter_start, gutter_end in split.gutters:
            width = max(1, gutter_end - gutter_start)
            overlap = min(element.box.x1, gutter_end) - max(element.box.x0, gutter_start)
            if overlap > width * STRADDLE_GUTTER_FRACTION:
                crossing.append(element)
                break
    return crossing


# --- tree ---------------------------------------------------------------------


@dataclass
class Node:
    box: Box
    role: str  # "column" | "block" | "straddler" | "panel"
    elements: list[Element]
    children: list["Node"] = field(default_factory=list)
    column: int | None = None
    column_count: int | None = None
    emit_self: bool = True
    forced_type: RegionType | None = None


def segment_region(box: Box, elements: list[Element], context: PageContext, depth: int) -> list[Node]:
    elements = [e for e in elements if e.kind != "frame"]
    if not elements:
        return []
    if depth >= MAX_DEPTH:
        return segment_column(box, elements, context, depth, column=None, column_count=None)

    split = detect_columns(elements, box, context)
    if split.count < 2:
        return segment_column(box, elements, context, depth, column=None, column_count=None)

    straddlers = find_straddlers(elements, split)
    if straddlers:
        return _segment_around_straddlers(box, elements, straddlers, context, depth)

    nodes: list[Node] = []
    for index, column_box in enumerate(split.columns):
        members = _inside(elements, column_box)
        if not members:
            continue
        nodes.extend(
            segment_column(
                Box.hull([m.box for m in members]),
                members,
                context,
                depth + 1,
                column=index,
                column_count=split.count,
            )
        )
    return nodes


def _segment_around_straddlers(
    box: Box,
    elements: list[Element],
    straddlers: list[Element],
    context: PageContext,
    depth: int,
) -> list[Node]:
    """Full-width elements cut the region into slabs; columns live inside slabs."""

    gap = max(4, int(context.median_line_height * 0.6))
    bars = _y_runs(straddlers, box, gap)
    straddler_ids = {id(element) for element in straddlers}
    rest = [e for e in elements if id(e) not in straddler_ids]

    nodes: list[Node] = []
    cursor = box.y0
    for bar in bars + [Box(box.x0, box.y1, box.x1, box.y1)]:
        slab = Box(box.x0, cursor, box.x1, max(cursor, bar.y0))
        members = _inside(rest, slab)
        if members and slab.height > 0:
            nodes.extend(
                segment_region(Box.hull([m.box for m in members]), members, context, depth + 1)
            )
        if bar.height > 0:
            bar_elements = [e for e in elements if _mostly_inside(e.box, bar)]
            nodes.append(_make_block(bar, bar_elements, context, depth, None, None, "straddler"))
        cursor = max(cursor, bar.y1)
    return nodes


def segment_column(
    box: Box,
    elements: list[Element],
    context: PageContext,
    depth: int,
    column: int | None,
    column_count: int | None,
) -> list[Node]:
    major_gap = max(8, int(context.median_line_height * MAJOR_BAND_GAP_FACTOR))
    block_gap = max(6, int(context.median_line_height * BLOCK_GAP_FACTOR))
    nodes: list[Node] = []
    for band in _y_runs(elements, box, major_gap):
        band_members = _inside(elements, band)
        if not band_members:
            continue
        hull = Box.hull([m.box for m in band_members])
        nested = _nested_columns(hull, band_members, context, depth)
        if nested:
            nodes.extend(nested)
            continue
        for slab in _y_runs(band_members, hull, block_gap):
            members = _inside(band_members, slab)
            if not members:
                continue
            for block_box, block_elements in _split_text_and_graphics(members, slab, context):
                nodes.append(
                    _make_block(
                        block_box, block_elements, context, depth, column, column_count, "block"
                    )
                )
    return nodes


def _nested_columns(
    box: Box, elements: list[Element], context: PageContext, depth: int
) -> list[Node]:
    """A single block can still be laid out in columns, e.g. a signature pair.

    This also recovers the page grid when a large figure stopped the page-level
    profile from seeing it.
    """

    if depth + 1 >= MAX_DEPTH:
        return []
    if detect_columns(elements, box, context).count < 2:
        return []
    return segment_region(box, elements, context, depth + 1)


def _split_text_and_graphics(
    elements: list[Element], slab: Box, context: PageContext
) -> list[tuple[Box, list[Element]]]:
    """Separate graphic-dominant rows from text-dominant rows inside one slab."""

    rows = _y_runs(elements, slab, 1)
    if len(rows) <= 1:
        return [(Box.hull([e.box for e in elements]), elements)]
    groups: list[tuple[str, list[Element]]] = []
    for row in rows:
        members = _inside(elements, row)
        if not members:
            continue
        text_area = sum(e.box.area for e in members if e.is_text)
        graphic_area = sum(e.box.area for e in members if e.is_artwork)
        label = "graphic" if graphic_area > max(text_area, 1) * GRAPHIC_ROW_DOMINANCE else "text"
        if groups and groups[-1][0] == label:
            groups[-1][1].extend(members)
        else:
            groups.append((label, list(members)))
    return [(Box.hull([e.box for e in members]), members) for _, members in groups if members]


def _make_block(
    box: Box,
    elements: list[Element],
    context: PageContext,
    depth: int,
    column: int | None,
    column_count: int | None,
    role: str,
) -> Node:
    node = Node(box, role, elements, column=column, column_count=column_count)
    if depth >= MAX_DEPTH:
        return node
    panel = _dominant_panel(box, elements, context)
    if panel is None or looks_like_table(box, elements, context):
        return node
    inner = [e for e in elements if e is not panel]
    if not _panel_worthy(panel, inner, context):
        return node
    node.children = segment_region(Box.hull([e.box for e in inner]), inner, context, depth + 1)
    if node.children:
        node.role = "panel"
        node.forced_type = "decorative"
    return node


def _dominant_panel(box: Box, elements: list[Element], context: PageContext) -> Element | None:
    best: Element | None = None
    for element in elements:
        if not element.container:
            continue
        if element.box.area < context.content.area * PANEL_MIN_AREA_FRACTION:
            continue
        if element.box.area < box.area * PANEL_COVERAGE:
            continue
        if best is None or element.box.area > best.box.area:
            best = element
    return best


def _panel_worthy(panel: Element, inner: list[Element], context: PageContext) -> bool:
    """Only recurse into a panel when it really holds prose worth separating."""

    text = _meaningful_text(inner, context)
    if not text:
        return False
    hull = Box.hull([element.box for element in text])
    covers = hull.width >= panel.box.width * 0.35 and hull.height >= panel.box.height * 0.2
    return covers and (len(_row_clusters(text, context)) >= 1)


# --- classification -----------------------------------------------------------


def _meaningful_text(elements: list[Element], context: PageContext) -> list[Element]:
    floor = context.median_line_height * 0.4
    return [e for e in elements if e.is_text and e.box.height >= floor]


def _row_clusters(texts: list[Element], context: PageContext) -> list[list[Element]]:
    if not texts:
        return []
    ordered = sorted(texts, key=lambda e: e.box.y0)
    clusters: list[list[Element]] = [[ordered[0]]]
    cursor = ordered[0].box.y1
    for element in ordered[1:]:
        if element.box.y0 > cursor - context.median_line_height * 0.35:
            clusters.append([element])
            cursor = element.box.y1
        else:
            clusters[-1].append(element)
            cursor = max(cursor, element.box.y1)
    return clusters


def looks_like_table(box: Box, elements: list[Element], context: PageContext) -> bool:
    texts = _meaningful_text(elements, context)
    if len(texts) < TABLE_MIN_ROWS * TABLE_MIN_COLUMNS:
        return False
    text_area = sum(e.box.area for e in texts)
    if not box.area or text_area / box.area < TABLE_MIN_TEXT_DENSITY:
        return False

    rows = _row_clusters(texts, context)
    if len(rows) < TABLE_MIN_ROWS:
        return False

    min_gutter = max(8, int(context.content.width * MIN_GUTTER_FRACTION * 0.6))
    occupied = _occupancy([(t.box.x0, t.box.x1) for t in texts], box.x0, box.x1)
    cells = _merge_runs(_filled_runs(occupied, box.x0), min_gutter)
    if len(cells) < TABLE_MIN_COLUMNS:
        return False

    multi = 0
    for row in rows:
        hit = {
            index
            for index, (start, end) in enumerate(cells)
            for member in row
            if member.box.x0 < end and member.box.x1 > start
        }
        if len(hit) >= TABLE_MIN_COLUMNS:
            multi += 1
    if multi < max(TABLE_MIN_ROWS, int(len(rows) * TABLE_ROW_COVERAGE)):
        return False

    interior = _interior_rules(box, elements, context)
    has_panel = any(
        e.kind == "graphic" and e.box.area >= box.area * PANEL_COVERAGE for e in elements
    )
    return has_panel or interior >= 1


def _interior_rules(box: Box, elements: list[Element], context: PageContext) -> int:
    margin = max(int(context.median_line_height * 0.5), 6)
    count = 0
    for element in elements:
        if not element.is_rule:
            continue
        if element.kind == "rule_h":
            if box.y0 + margin < element.box.cy < box.y1 - margin:
                count += 1
        elif box.x0 + margin < element.box.cx < box.x1 - margin:
            count += 1
    return count


def classify(node: Node, context: PageContext, gray: np.ndarray) -> tuple[RegionType, dict]:
    if node.forced_type:
        return node.forced_type, {"forced": True}

    elements = node.elements
    texts = _meaningful_text(elements, context)
    graphics = [e for e in elements if e.kind == "graphic"]
    artwork = [e for e in elements if e.is_artwork]
    h_rules = [e for e in elements if e.kind == "rule_h"]
    v_rules = [e for e in elements if e.kind == "rule_v"]
    box = node.box
    content = context.content
    text_area = sum(e.box.area for e in texts)
    artwork_area = sum(e.box.area for e in artwork)
    evidence: dict = {
        "text_fragments": len(texts),
        "graphics": len(graphics),
        "artwork": len(artwork),
        "h_rules": len(h_rules),
        "v_rules": len(v_rules),
        "text_area": text_area,
        "artwork_area": artwork_area,
    }

    thin = box.height <= content.height * BAR_MAX_HEIGHT_FRACTION
    wide = box.width >= content.width * BAR_MIN_WIDTH_FRACTION

    if looks_like_table(box, elements, context):
        return "table", evidence

    bars = [e for e in graphics if e.bar]
    bar_area = max((e.box.area for e in bars), default=0)
    furniture = bar_area >= box.area * 0.6 and len(texts) <= 2 and text_area <= box.area * 0.12
    if bars and (not texts or furniture):
        panel = max(bars, key=lambda e: e.box.area)
        heights = reverse_text_heights(gray, panel.box, context.scale)
        if len(heights) >= 3:
            median_reverse = float(np.median(heights))
            evidence["reverse_text_height"] = round(median_reverse, 1)
            if median_reverse >= context.median_line_height * HEADING_HEIGHT_FACTOR:
                return "heading", evidence
        return "decorative", evidence

    if not texts:
        if artwork:
            return "figure", evidence
        if graphics or h_rules or v_rules:
            return "decorative", evidence
        return "unknown", evidence

    if artwork and artwork_area > max(text_area * FIGURE_DOMINANCE, context.px(60) ** 2):
        return "figure", evidence

    rows = len(_row_clusters(texts, context))
    median_h = float(np.median([e.box.height for e in texts]))
    evidence["rows"] = rows
    evidence["median_fragment_height"] = round(median_h, 1)
    if (
        rows <= HEADING_MAX_LINES
        and median_h >= context.median_line_height * HEADING_HEIGHT_FACTOR
        and box.width <= content.width * HEADING_MAX_WIDTH_FRACTION
    ):
        return "heading", evidence
    return "text", evidence


# --- entry point --------------------------------------------------------------


@dataclass
class PageAnalysis:
    context: PageContext
    elements: list[Element]
    frames: list[Element]
    gray: np.ndarray
    counts: dict[str, int]


def analyse_page(image_bgr: np.ndarray, dpi: float) -> PageAnalysis:
    """Everything before grouping: masks, rules, glyphs, fragments, graphics."""

    height, width = image_bgr.shape[:2]
    scale = dpi / 300.0
    ink, mark, edges, gray = build_masks(image_bgr, scale)
    rules = detect_rules([mark, ink, edges], scale)
    glyphs = glyph_elements(ink, scale)
    graphics, frames = graphic_elements(mark, scale)
    fragments = line_fragments(glyphs, (height, width), scale)
    graphics = [g for g in graphics if not _matches_any_rule(g, rules)]

    page_box = Box(0, 0, width, height)
    elements = fragments + graphics + rules
    content = Box.hull([e.box for e in elements]) if elements else page_box
    fragment_heights = [f.box.height for f in fragments] or [g.box.height for g in glyphs] or [1]
    context = PageContext(
        width=width,
        height=height,
        scale=scale,
        content=content,
        median_line_height=float(np.median(fragment_heights)),
        speck_area=(max(2.0, 0.3 * float(np.median(fragment_heights)))) ** 2,
    )
    mark_bars(graphics, context)
    fragments = drop_bar_interiors(fragments, graphics)
    mark_containers(graphics, fragments, context)
    fragments = merge_line_fragments(fragments, graphics, rules, context)
    mark_containers(graphics, fragments, context)
    fragments, graphics = group_figure_zones(fragments, graphics, context)
    mark_containers(graphics, fragments, context)
    mark_figure_labels(fragments, graphics)
    elements = fragments + graphics + rules
    counts = {
        "glyph_components": len(glyphs),
        "line_fragments": len(fragments),
        "graphic_components": len(graphics),
        "container_panels": sum(1 for g in graphics if g.container),
        "bars": sum(1 for g in graphics if g.bar),
        "rules": len(rules),
        "frames": len(frames),
    }
    return PageAnalysis(context, elements, frames, gray, counts)


def detect_layout(
    image_bgr: np.ndarray,
    *,
    document_id: str,
    page_number: int,
    dpi: float,
    image_sha256: str,
) -> PageLayout:
    height, width = image_bgr.shape[:2]
    analysis = analyse_page(image_bgr, dpi)
    context = analysis.context
    elements = analysis.elements
    frames = analysis.frames
    gray = analysis.gray
    content = context.content
    page_box = Box(0, 0, width, height)

    nodes = segment_region(content, elements, context, depth=0)
    regions: list[Region] = []
    _emit(nodes, regions, context, gray, page_number, page_box, parent=None)
    for frame in frames:
        regions.append(
            Region(
                id=f"p{page_number:03d}-r{len(regions):03d}",
                type="decorative",
                bbox=frame.box,
                reading_order=len(regions),
                evidence={"page_frame": True},
            )
        )

    return PageLayout(
        document_id=document_id,
        page_number=page_number,
        width=width,
        height=height,
        dpi=dpi,
        image_sha256=image_sha256,
        detector_version=DETECTOR_VERSION,
        regions=regions,
        diagnostics={
            **analysis.counts,
            "median_line_height": round(context.median_line_height, 1),
            "content_bbox": content.as_list(),
            "max_columns": max((r.column_count or 1 for r in regions), default=1),
        },
    )


def _emit(
    nodes: list[Node],
    regions: list[Region],
    context: PageContext,
    gray: np.ndarray,
    page_number: int,
    page_box: Box,
    parent: str | None,
) -> None:
    pad = max(1, int(context.median_line_height * 0.08), int(REGION_PAD * context.scale))
    for node in nodes:
        identifier = f"p{page_number:03d}-r{len(regions):03d}"
        if node.emit_self:
            region_type, evidence = classify(node, context, gray)
            regions.append(
                Region(
                    id=identifier,
                    type=region_type,
                    bbox=node.box.pad(pad, page_box),
                    reading_order=len(regions),
                    parent=parent,
                    column=node.column,
                    column_count=node.column_count,
                    line_count=len(_row_clusters(_meaningful_text(node.elements, context), context)),
                    evidence=evidence,
                )
            )
        if node.children:
            _emit(node.children, regions, context, gray, page_number, page_box, parent=identifier)


def _matches_any_rule(graphic: Element, rules: list[Element]) -> bool:
    for rule in rules:
        overlap_w = max(0, min(graphic.box.x1, rule.box.x1) - max(graphic.box.x0, rule.box.x0))
        overlap_h = max(0, min(graphic.box.y1, rule.box.y1) - max(graphic.box.y0, rule.box.y0))
        if graphic.box.area and overlap_w * overlap_h >= graphic.box.area * 0.8:
            return True
    return False
