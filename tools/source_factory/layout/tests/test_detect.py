# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "jsonschema==4.25.1",
#   "numpy==2.2.6",
#   "opencv-python-headless==4.12.0.88",
#   "pytest==8.4.2",
# ]
# ///
"""Mechanics tests for the Source V2 layout detector.

Synthetic pages prove the structural rules only. Real layout quality is proved
by the annotated previews of the fixed benchmark pages, not by these tests; the
final test here runs the real corpus when it is present on the local disk and
skips otherwise, because rendered source pages are never committed.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.source_factory.layout import benchmark as benchmark_set  # noqa: E402
from tools.source_factory.layout.contract import check_page  # noqa: E402
from tools.source_factory.layout.detect import detect_layout  # noqa: E402

WIDTH, HEIGHT = 2480, 3508
MARGIN = 300
LINE_HEIGHT = 34
LEADING = 62
GLYPH = 22
GAP = 10


def blank_page() -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)


def draw_line(page: np.ndarray, x: int, y: int, width: int, height: int = LINE_HEIGHT) -> None:
    """Paint one printed line as a run of glyph-sized marks."""

    cursor = x
    while cursor + GLYPH <= x + width:
        cv2.rectangle(page, (cursor, y), (cursor + GLYPH, y + height), (20, 20, 20), -1)
        cursor += GLYPH + GAP


def draw_column(page: np.ndarray, x: int, y: int, width: int, lines: int) -> None:
    for index in range(lines):
        draw_line(page, x, y + index * LEADING, width)


def segment(page: np.ndarray, number: int = 1):
    return detect_layout(
        page,
        document_id="synthetic",
        page_number=number,
        dpi=300.0,
        image_sha256=hashlib.sha256(page.tobytes()).hexdigest(),
    )


def column_counts(layout) -> set[int]:
    return {region.column_count for region in layout.regions if region.column_count}


def test_single_column_page_stays_single_column() -> None:
    page = blank_page()
    draw_column(page, MARGIN, 400, WIDTH - 2 * MARGIN, 20)
    layout = segment(page)
    assert column_counts(layout) == set()
    assert all(region.type == "text" for region in layout.regions)


def test_two_column_page_splits_into_two_columns() -> None:
    page = blank_page()
    draw_column(page, MARGIN, 400, 780, 20)
    draw_column(page, MARGIN + 940, 400, 780, 20)
    layout = segment(page)
    assert column_counts(layout) == {2}
    columns = {region.column for region in layout.regions if region.column is not None}
    assert columns == {0, 1}


def test_full_width_bar_does_not_break_columns() -> None:
    page = blank_page()
    cv2.rectangle(page, (MARGIN, 240), (WIDTH - MARGIN, 350), (60, 150, 60), -1)
    draw_column(page, MARGIN, 500, 780, 18)
    draw_column(page, MARGIN + 940, 500, 780, 18)
    cv2.rectangle(page, (MARGIN, 3200), (WIDTH - MARGIN, 3300), (60, 150, 60), -1)
    layout = segment(page)
    assert column_counts(layout) == {2}
    assert [region.type for region in layout.regions].count("decorative") == 2


def test_figure_crossing_the_gutter_keeps_the_columns() -> None:
    page = blank_page()
    draw_column(page, MARGIN, 400, 780, 10)
    draw_column(page, MARGIN + 940, 400, 780, 10)
    cv2.rectangle(page, (700, 1200), (1800, 1900), (90, 90, 200), -1)
    draw_column(page, MARGIN, 2100, 780, 10)
    draw_column(page, MARGIN + 940, 2100, 780, 10)
    layout = segment(page)
    assert column_counts(layout) == {2}
    assert any(region.type == "figure" for region in layout.regions)


def test_reading_order_runs_down_each_column_in_turn() -> None:
    page = blank_page()
    draw_column(page, MARGIN, 400, 780, 8)
    draw_column(page, MARGIN, 1000, 780, 8)
    draw_column(page, MARGIN + 940, 400, 780, 8)
    draw_column(page, MARGIN + 940, 1000, 780, 8)
    layout = segment(page)
    ordered = sorted(layout.regions, key=lambda region: region.reading_order)
    columns = [region.column for region in ordered if region.column is not None]
    assert columns == sorted(columns), "a column must be finished before the next one starts"


def test_regions_carry_the_phase_one_contract() -> None:
    page = blank_page()
    draw_column(page, MARGIN, 400, 780, 12)
    draw_column(page, MARGIN + 940, 400, 780, 12)
    payload = segment(page).to_json()
    assert payload["detector_version"]
    check_page(payload)
    for index, region in enumerate(payload["regions"]):
        assert region["reading_order"] == index
        x0, y0, x1, y1 = region["bbox"]
        assert x0 < x1 and y0 < y1


# --- real corpus ---------------------------------------------------------------

EXPECTED_COLUMNS = {4: 2, 152: 2, 156: 2, 157: 2, 163: 2, 171: 2, 186: 2, 197: 2}
EXPECTED_TYPES = {
    157: {"figure", "decorative", "text"},
    163: {"decorative", "heading", "text"},
    171: {"table", "decorative", "text"},
    186: {"figure", "decorative", "text"},
    197: {"figure", "decorative", "text"},
}


def _document():
    from tools.source_factory.layout.corpus import load_document

    try:
        return load_document(None)
    except SystemExit:
        return None


@pytest.mark.parametrize("page_number", [page for page, _ in benchmark_set.PAGES])
def test_benchmark_page_segments_as_reviewed(page_number: int) -> None:
    document = _document()
    if document is None or page_number not in document.pages:
        pytest.skip("rendered benchmark corpus is not present on this machine")
    page = document.page(page_number)
    image = cv2.imdecode(np.fromfile(str(page.path), dtype=np.uint8), cv2.IMREAD_COLOR)
    layout = detect_layout(
        image,
        document_id=document.document_id,
        page_number=page_number,
        dpi=document.dpi,
        image_sha256=page.sha256,
    )
    check_page(layout.to_json())
    counts = column_counts(layout)
    assert counts == {EXPECTED_COLUMNS[page_number]}, f"page {page_number} columns {counts}"
    present = {region.type for region in layout.regions}
    for required in EXPECTED_TYPES.get(page_number, set()):
        assert required in present, f"page {page_number} lost its {required} regions"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
