import hashlib
from typing import cast

import cv2
import numpy as np
import pymupdf
import pytest

from exam_guru_api.documents.source_geometry import detect_source_geometry


def grid_image(*, dpi: int = 300) -> bytes:
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=180)
        for x in (20, 60, 100, 140, 180):
            page.draw_line((x, 30), (x, 120), color=(0, 0, 0), width=0.6)
        for y in (30, 60, 90, 120):
            page.draw_line((20, y), (180, y), color=(0, 0, 0), width=0.6)
        page.insert_text((70, 51), "54", fontsize=12)
        page.insert_text((21, 160), "Keep this visible footer.", fontsize=10)
        return cast(bytes, page.get_pixmap(dpi=dpi, alpha=False).tobytes("png"))


@pytest.mark.parametrize("dpi", [300, 400, 600])
def test_geometry_derives_cells_from_source_pixels_without_a_model(dpi: int) -> None:
    image = grid_image(dpi=dpi)
    result = detect_source_geometry(image, dpi=dpi)
    assert result.image_sha256 == hashlib.sha256(image).hexdigest()
    assert len(result.tables) == 1
    table = result.tables[0]
    assert (table.rows, table.columns) == (3, 4)
    assert len(table.cells) == 12
    by_position = {(cell.row, cell.column): cell for cell in table.cells}
    assert not by_position[1, 2].blank
    assert by_position[1, 2].ink_pixels > 0
    assert all(cell.blank for cell in table.cells if (cell.row, cell.column) != (1, 2))
    assert by_position[1, 2].bounds.left < by_position[1, 3].bounds.left
    assert by_position[1, 2].bounds.top < by_position[2, 2].bounds.top
    assert result.regions
    assert any(region.bounds.top > table.bounds.bottom for region in result.regions)


def test_grid_geometry_never_uses_numeric_patterns_to_fill_blank_cells() -> None:
    result = detect_source_geometry(grid_image(), dpi=300)
    assert sum(cell.blank for cell in result.tables[0].cells) == 11
    assert not hasattr(result.tables[0].cells[0], "calculated_value")


def test_geometry_checks_png_identity_and_size_before_decoding() -> None:
    with pytest.raises((ValueError, RuntimeError)):
        detect_source_geometry(b"not a PNG", dpi=300)


def test_text_only_page_does_not_acquire_an_invented_table() -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=180)
        page.insert_text((20, 40), "First printed line.", fontsize=12)
        page.insert_text((20, 60), "Second printed line.", fontsize=12)
        image = page.get_pixmap(dpi=300, alpha=False).tobytes("png")
    result = detect_source_geometry(image, dpi=300)
    assert not result.tables
    assert result.regions
    assert len(result.regions) <= 2


def test_split_raster_stroke_edges_are_one_grid_boundary_not_extra_cells() -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=180)
        for offset in (-0.35, 0.35):
            for x in (20, 60, 100, 140, 180):
                page.draw_line((x + offset, 30), (x + offset, 120), color=(0, 0, 0), width=0.2)
            for y in (30, 60, 90, 120):
                page.draw_line((20, y + offset), (180, y + offset), color=(0, 0, 0), width=0.2)
        page.insert_text((70, 51), "54", fontsize=12)
        image = page.get_pixmap(dpi=400, alpha=False).tobytes("png")
    result = detect_source_geometry(image, dpi=400)
    assert len(result.tables) == 1
    assert (result.tables[0].rows, result.tables[0].columns) == (3, 4)
    assert sum(cell.blank for cell in result.tables[0].cells) == 11


def test_an_enclosing_page_frame_does_not_hide_separate_source_text_regions() -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=180)
        page.draw_rect(pymupdf.Rect(1, 1, 239, 179), color=(0, 0, 0), width=0.4)
        page.insert_text((20, 45), "Separate title", fontsize=18)
        page.insert_text((20, 125), "Separate footer", fontsize=10)
        image = page.get_pixmap(dpi=300, alpha=False).tobytes("png")
    result = detect_source_geometry(image, dpi=300)
    assert len(result.regions) >= 2
    assert all(
        (r.bounds.right - r.bounds.left) * (r.bounds.bottom - r.bounds.top) < 0.75
        for r in result.regions
    )


def test_tiny_grid_intersection_artifacts_do_not_fill_blank_cells() -> None:
    image = cv2.imdecode(np.frombuffer(grid_image(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    image[129:131, 87:89] = 0
    encoded, data = cv2.imencode(".png", image)
    assert encoded
    table = detect_source_geometry(data.tobytes(), dpi=300).tables[0]
    assert {(cell.row, cell.column) for cell in table.cells if not cell.blank} == {(1, 2)}


def test_thick_grid_strokes_do_not_fill_blanks_or_erase_a_small_detached_source_mark() -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=180)
        for x in (20, 60, 100, 140, 180):
            page.draw_line((x, 30), (x, 120), color=(0, 0, 0), width=2.6)
        for y in (30, 60, 90, 120):
            page.draw_line((20, y), (180, y), color=(0, 0, 0), width=2.6)
        page.insert_text((70, 51), "54", fontsize=12)
        page.draw_circle((61.8, 75), 0.3, color=(0, 0, 0), fill=(0, 0, 0), width=0.1)
        image = page.get_pixmap(dpi=300, alpha=False).tobytes("png")
    table = detect_source_geometry(image, dpi=300).tables[0]
    occupied = {(cell.row, cell.column) for cell in table.cells if not cell.blank}
    assert occupied == {(1, 2), (2, 2)}
