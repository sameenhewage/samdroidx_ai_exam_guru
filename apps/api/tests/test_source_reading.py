import hashlib
import json
from typing import Any

import pymupdf
import pytest
from pydantic import ValidationError

from exam_guru_api.documents.source_reading import (
    SourceLayout,
    SourceReadCandidate,
    SourceRegionReading,
    assemble_source_reading,
    crop_source_image,
)
from exam_guru_api.documents.understanding_contracts import RegionBounds
from tests.test_document_understanding_contracts import counting_candidate


def source_payload() -> dict[str, Any]:
    previous = counting_candidate()
    return {
        "schema_version": "source-read-candidate.v1",
        "observation": previous["observation"],
        "uncertainties": previous["uncertainties"],
    }


@pytest.mark.parametrize("field", ["education", "topic", "skill", "learning_objective", "answer"])
def test_source_reading_contract_rejects_educational_interpretation(field: str) -> None:
    value = source_payload()
    value[field] = "This must not be part of source reading"
    with pytest.raises(ValidationError):
        SourceReadCandidate.model_validate_json(json.dumps(value))


def test_source_candidate_keeps_exact_unicode_and_literal_arithmetic_without_trust() -> None:
    value = source_payload()
    value["observation"]["regions"][0]["exact_text"] = "මව්බස\n1 \u00d7 8 = 9\ninfo@nie.lk"
    result = SourceReadCandidate.model_validate_json(json.dumps(value))
    assert result.observation.regions[0].exact_text == "මව්බස\n1 \u00d7 8 = 9\ninfo@nie.lk"
    assert "education" not in result.model_dump()
    assert not hasattr(result, "verified")
    assert result.as_legacy_envelope().education.claims == ()


def layout() -> SourceLayout:
    return SourceLayout.model_validate_json(
        json.dumps(
            {
                "schema_version": "source-layout.v1",
                "language": "si",
                "regions": [
                    {
                        "key": "second",
                        "kind": "table",
                        "reading_order": 1,
                        "parent_key": None,
                        "bounds": {"left": 0.1, "top": 0.5, "right": 0.9, "bottom": 0.9},
                    },
                    {
                        "key": "first",
                        "kind": "heading",
                        "reading_order": 0,
                        "parent_key": None,
                        "bounds": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.3},
                    },
                ],
                "relationships": [],
            }
        )
    )


def region_reading(text: str = "") -> SourceRegionReading:
    return SourceRegionReading.model_validate_json(
        json.dumps(
            {
                "exact_text": text,
                "equations": [],
                "table": None,
                "visual_facts": [],
                "uncertainties": [],
            }
        )
    )


def test_reconstruction_pins_reading_order_geometry_and_blank_cell_coordinates() -> None:
    table = region_reading().model_dump(mode="json")
    table["table"] = {
        "rows": [
            [{"state": "visible", "exact_text": "\u00d7"}, {"state": "visible", "exact_text": "9"}],
            [{"state": "visible", "exact_text": "6"}, {"state": "blank", "exact_text": ""}],
        ]
    }
    result = assemble_source_reading(
        layout(),
        {
            "second": SourceRegionReading.model_validate_json(json.dumps(table)),
            "first": region_reading("ගුණ කිරීම"),
        },
    )
    assert [region.key for region in result.observation.regions] == ["first", "second"]
    observed = result.observation.regions[1]
    assert observed.bounds == layout().regions[0].bounds
    assert observed.table is not None
    assert (observed.table.rows, observed.table.columns) == (2, 2)
    cell = observed.table.cells[3]
    assert (cell.row, cell.column, cell.state, cell.exact_text) == (1, 1, "blank", "")
    assert "54" not in result.model_dump_json()


@pytest.mark.parametrize("missing", [True, False])
def test_reconstruction_rejects_missing_or_unrequested_regions(missing: bool) -> None:
    readings = {"first": region_reading("මව්බස"), "second": region_reading()}
    if missing:
        readings.pop("second")
    else:
        readings["invented"] = region_reading("new source")
    with pytest.raises(ValueError, match="region"):
        assemble_source_reading(layout(), readings)


def test_compact_matrix_rejects_missing_cells_and_inferred_blank_answers() -> None:
    value = region_reading().model_dump(mode="json")
    value["table"] = {
        "rows": [
            [{"state": "visible", "exact_text": "9"}],
            [],
        ]
    }
    with pytest.raises(ValidationError):
        SourceRegionReading.model_validate_json(json.dumps(value))
    value["table"] = {"rows": [[{"state": "blank", "exact_text": "54"}]]}
    with pytest.raises(ValidationError):
        SourceRegionReading.model_validate_json(json.dumps(value))


def test_crop_uses_original_pixels_and_records_exact_parent_and_crop_identity() -> None:
    original = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 200), False)
    original.clear_with(255)
    original.set_pixel(20, 40, (0, 0, 0))
    image = original.tobytes("png")
    result = crop_source_image(image, RegionBounds(left=0.1, top=0.1, right=0.6, bottom=0.6))
    assert result.parent_sha256 == hashlib.sha256(image).hexdigest()
    assert result.sha256 == hashlib.sha256(result.png).hexdigest()
    assert (result.left, result.top, result.width, result.height) == (10, 20, 50, 100)
    assert pymupdf.Pixmap(result.png).pixel(10, 20) == (0, 0, 0)
    assert result.width < original.width
    assert result.height < original.height


def test_whole_page_pass_retains_the_exact_original_png_and_dpi() -> None:
    original = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 200), False)
    original.clear_with(255)
    original.set_dpi(300, 300)
    image = original.tobytes("png")
    result = crop_source_image(image, RegionBounds(left=0.0, top=0.0, right=1.0, bottom=1.0))
    assert result.png == image
    assert result.sha256 == result.parent_sha256


def test_uncertainties_remain_unique_and_complete_across_regions() -> None:
    first = region_reading("මව්බස").model_dump(mode="json")
    first["uncertainties"] = [
        {"field": f"glyph_{index}", "reason": "Compare the original", "alternatives": []}
        for index in range(4)
    ]
    second = region_reading().model_dump(mode="json")
    second["uncertainties"] = [
        {"field": "cell", "reason": "Compare the original", "alternatives": []}
    ]
    result = assemble_source_reading(
        layout(),
        {
            "first": SourceRegionReading.model_validate_json(json.dumps(first)),
            "second": SourceRegionReading.model_validate_json(json.dumps(second)),
        },
    )
    assert len(result.uncertainties) == 5
    assert len({uncertain.key for uncertain in result.uncertainties}) == 5
