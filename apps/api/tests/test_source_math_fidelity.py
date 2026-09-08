import base64
import hashlib
import json
import zlib
from copy import deepcopy
from typing import Any, cast

import pymupdf
import pytest

from exam_guru_api.documents import source_math_fidelity as fidelity
from exam_guru_api.documents.fidelity_service import assess_candidate


def _reference(page: pymupdf.Page) -> dict[str, Any]:
    return fidelity.extract_math_layout(page)


def _assess(
    reference: dict[str, object],
    text: str,
    words: list[tuple[str, tuple[float, float, float, float]]] | None = None,
) -> dict[str, Any]:
    return fidelity.assess_math_fidelity(reference, text, words)


def _words(page: pymupdf.Page) -> list[tuple[str, tuple[float, float, float, float]]]:
    return [(word[4], tuple(word[:4])) for word in page.get_text("words", sort=True)]


def _grid(page: pymupdf.Page, rows: int = 3, columns: int = 3) -> None:
    for column in range(columns + 1):
        x = 40 + column * 35
        page.draw_line((x, 100), (x, 100 + rows * 25))
    for row in range(rows + 1):
        y = 100 + row * 25
        page.draw_line((40, y), (40 + columns * 35, y))


def _cell_text(page: pymupdf.Page, row: int, column: int, text: str) -> None:
    page.insert_text((47 + column * 35, 116 + row * 25), text, fontsize=10)


def _raster(page: pymupdf.Page, *, grid: bool) -> None:
    with pymupdf.open() as source:
        image = source.new_page(width=200, height=200)
        if grid:
            _grid(image)
            _cell_text(image, 0, 0, "2")
            _cell_text(image, 1, 1, "7")
        else:
            image.draw_circle((100, 100), 35, color=(0, 0, 0))
        pixels = image.get_pixmap(matrix=pymupdf.Matrix(1, 1))
        page.insert_image(pymupdf.Rect(30, 200, 230, 400), pixmap=pixels)


@pytest.mark.parametrize("tampered", [False, True])
def test_candidate_gate_decodes_and_checks_lossless_word_evidence(tampered: bool) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text(
            (30, 30),
            "\n".join(
                f"Synthetic abc123de page 1, line {index}: 2 + 2 = 4." for index in range(1, 41)
            ),
            fontsize=10,
        )
        reference = fidelity.encode_math_evidence(_reference(page))
        words = fidelity.encode_math_evidence([[text, list(box)] for text, box in _words(page)])
        raw = page.get_text("text", sort=True)
    assert isinstance(words, dict)
    if tampered:
        words = {**words, "sha256": "0" * 64}
    assessment = assess_candidate(
        raw,
        "native",
        {
            "source_languages": ["en"],
            "maths_reference": reference,
            "maths_words": words,
        },
    )
    assert assessment.can_confirm is (not tampered)
    if tampered:
        assert "invalid_math_word_evidence" in assessment.risk_codes


def test_candidate_gate_rechecks_numbers_instead_of_trusting_a_cached_math_pass() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "3 X 4 = 12")
        reference = _reference(page)
    result = assess_candidate(
        "ගණිතය 3 X 4 = 13",
        "human",
        {
            "languages": ["si"],
            "maths_reference": reference,
            "maths_fidelity": {"can_confirm": True, "risk_codes": []},
        },
    )
    assert not result.can_confirm
    assert "maths_fidelity_unconfirmed" in result.risk_codes


def test_corrected_human_math_is_reassessed_without_reusing_parent_word_positions() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "3 X 4 = 12")
        reference = _reference(page)
    result = assess_candidate(
        "ගණිතය 3 X 4 = 12",
        "human",
        {
            "languages": ["si"],
            "maths_reference": reference,
            "maths_words": [["13", [0, 0, 1, 1]]],
            "maths_fidelity": {"can_confirm": False, "risk_codes": ["math_tokens_changed"]},
        },
    )
    assert result.can_confirm
    assert "math_tokens_changed" not in result.risk_codes


def test_preserves_literal_x_and_native_anchor_geometry_without_source_mutation() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "3 X 4 =")
        before = document.tobytes(no_new_id=True)
        reference = _reference(page)
        assert [anchor["text"] for anchor in reference["anchors"]] == ["3", "X", "4", "="]
        assert all(len(anchor["bbox"]) == 4 for anchor in reference["anchors"])
        assert all(anchor["source"] == "native_nonlegacy" for anchor in reference["anchors"])
        assert document.tobytes(no_new_id=True) == before
        result = _assess(reference, "3 X 4 =")
        assert result["can_confirm"] is True
        assert result["lost"] == []
        assert result["changed"] == []
        changed = _assess(reference, "3 \u00d7 4 =")
        assert changed["can_confirm"] is False
        assert changed["changed"]
        assert "math_tokens_changed" in changed["risk_codes"]
        assert json.loads(json.dumps(reference)) == reference


def test_unicode_operators_and_numbers_are_not_compatibility_normalized() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_htmlbox((30, 30, 500, 100), "12 \u00d7 3 ÷ 2 = 18 + 4 \u2212 1")
        reference = _reference(page)
        assert [anchor["text"] for anchor in reference["anchors"]] == [
            "12",
            "\u00d7",
            "3",
            "÷",
            "2",
            "=",
            "18",
            "+",
            "4",
            "\u2212",
            "1",
        ]
        assert _assess(reference, "12 \u00d7 3 ÷ 2 = 18 + 4 \u2212 1")["can_confirm"] is True
        for altered in ("12 x 3 ÷ 2 = 18 + 4 \u2212 1", "12 \u00d7 3 ÷ 2 = 18 + 4 - 1"):
            assert _assess(reference, altered)["can_confirm"] is False


@pytest.mark.parametrize(
    "font",
    ["FMBindumathi", "ABCDEF+FMAbhaya", "DL-Manel", "Thibus29", "Bamini", "NIEtml", "Kalaham"],
)
def test_legacy_equals_and_digits_are_excluded_not_math_anchors(font: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "legacy = 123 X")
        xref = page.get_fonts()[0][0]
        document.xref_set_key(xref, "BaseFont", "/" + font)
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        reference = _reference(document[0])
        assert reference["anchors"] == []
        assert reference["excluded_legacy_spans"] > 0
        assert _assess(reference, "සිංහල පාඩම")["can_confirm"] is True


def test_mixed_legacy_and_standard_native_spans_only_keep_standard_math() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "legacy = 123")
        xref = page.get_fonts()[0][0]
        document.xref_set_key(xref, "BaseFont", "/FMBindumathi")
        page.insert_text((50, 100), "3 X 4 =", fontname="cour")
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        reference = _reference(document[0])
        assert [anchor["text"] for anchor in reference["anchors"]] == ["3", "X", "4", "="]


def test_unknown_font_numeric_encoding_cannot_become_a_trusted_anchor() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3 X 4 =")
        document.xref_set_key(page.get_fonts()[0][0], "BaseFont", "/UnidentifiedGlyphFont")
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        reference = _reference(document[0])
        assert reference["anchors"] == []
        assert _assess(reference, "3 X 4 =")["can_confirm"] is False
        assert "untrusted_native_math_font" in reference["risk_codes"]


def test_reports_lost_tokens_added_answers_and_order_changes() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "3 X 4 =")
        reference = _reference(page)
        lost = _assess(reference, "3 4 =")
        assert [item["text"] for item in cast(list[dict[str, Any]], lost["lost"])] == ["X"]
        added = _assess(reference, "3 X 4 = 12")
        assert added["can_confirm"] is False
        assert added["added"] == ["12"]
        assert "math_tokens_added" in added["risk_codes"]
        reordered = _assess(reference, "4 X 3 =")
        assert reordered["can_confirm"] is False


def test_vector_table_keeps_all_forty_cells_and_fifteen_values_in_row_major_order() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page, rows=10, columns=4)
        occupied = {(index // 4, index % 4): str(index + 1) for index in range(15)}
        for (row, column), text in occupied.items():
            _cell_text(page, row, column, text)
        reference = _reference(page)
        assert len(reference["tables"]) == 1
        table = reference["tables"][0]
        assert (table["rows"], table["columns"]) == (10, 4)
        assert len(table["cells"]) == 40
        assert [(cell["row"], cell["column"]) for cell in table["cells"]] == [
            (row, column) for row in range(10) for column in range(4)
        ]
        assert sum(cell["empty"] is True for cell in table["cells"]) == 25
        assert [cell["text"] for cell in table["cells"] if not cell["empty"]] == list(
            occupied.values()
        )
        assert _assess(reference, page.get_text())["can_confirm"] is False
        assert "table_word_boxes_required" in _assess(reference, page.get_text())["risk_codes"]
        checked = _assess(reference, page.get_text(), _words(page))
        assert checked["can_confirm"] is True
        assert len(cast(list[dict[str, Any]], checked["tables"])[0]["cells"]) == 40


def test_same_global_tokens_in_wrong_cells_do_not_pass() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        _cell_text(page, 0, 1, "4")
        reference = _reference(page)
        words = _words(page)
        wrong = [(words[0][0], words[1][1]), (words[1][0], words[0][1])]
        result = _assess(reference, "3 4", wrong)
        assert result["can_confirm"] is False
        assert "table_cell_mismatch" in result["risk_codes"]


def test_missing_table_values_and_invented_empty_cell_answer_fail() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        _cell_text(page, 0, 1, "4")
        reference = _reference(page)
        words = _words(page)
        missing = _assess(reference, "3", words[:1])
        assert missing["can_confirm"] is False
        invented = _assess(reference, "3 4 12", [*words, ("12", (116, 103, 130, 118))])
        assert invented["can_confirm"] is False
        assert "table_cell_mismatch" in invented["risk_codes"]


def test_table_word_crossing_cell_boundary_cannot_be_assigned_by_center_alone() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        reference = _reference(page)
        result = _assess(reference, "3", [("3", (60, 104, 91, 118))])
        assert result["can_confirm"] is False
        assert "ambiguous_table_word_box" in result["risk_codes"]


def test_word_boxes_do_not_override_changed_or_reordered_candidate_text() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        _cell_text(page, 0, 1, "4")
        reference = _reference(page)
        result = _assess(reference, "4 3", _words(page))
        assert result["can_confirm"] is False
        assert "word_text_mismatch" in result["risk_codes"]


def test_detects_two_raster_grids_without_claiming_ocr_verified_them() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _raster(page, grid=True)
        with pymupdf.open() as image_document:
            image_page = image_document.new_page(width=200, height=200)
            _grid(image_page)
            page.insert_image((280, 200, 480, 400), pixmap=image_page.get_pixmap())
        reference = _reference(page)
        assert len(reference["images"]) == 2
        assert all(image["grid_detected"] for image in reference["images"])
        assert reference["tables"] == []
        result = _assess(reference, "2 7", [("2", (77, 306, 83, 316))])
        assert result["can_confirm"] is False
        assert "raster_grid_unverified" in result["risk_codes"]


def test_raster_exercise_panel_does_not_disappear_behind_good_vector_table() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        _raster(page, grid=False)
        reference = _reference(page)
        result = _assess(reference, "3", _words(page))
        assert result["can_confirm"] is False
        assert "raster_math_content_unverified" in result["risk_codes"]


def test_decorative_image_is_not_claimed_to_be_a_grid_and_plain_prose_is_not_blocked() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "Read the passage about trees.")
        _raster(page, grid=False)
        reference = _reference(page)
        assert reference["images"][0]["grid_detected"] is False
        assert reference["images"][0]["content_verified"] is False
        assert _assess(reference, "Read the passage about trees.")["can_confirm"] is True


def test_broken_or_merged_vector_grid_is_explicitly_unsupported() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for y in (100, 125, 150):
            page.draw_line((40, y), (110, y))
        for x in (40, 110):
            page.draw_line((x, 100), (x, 150))
        page.draw_line((75, 125), (75, 150))
        reference = _reference(page)
        assert "unsupported_vector_grid" in reference["risk_codes"]
        assert _assess(reference, "")["can_confirm"] is False


def test_native_math_and_metadata_limits_are_explicit_not_silent_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fidelity, "MAX_ANCHORS", 2)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3 X 4 =")
        reference = _reference(page)
        assert len(reference["anchors"]) <= 2
        assert "native_anchor_limit" in reference["risk_codes"]
        assert reference["complete"] is False
        assert _assess(reference, "3 X 4 =")["can_confirm"] is False


def test_cell_limit_reports_unsupported_layout_without_partial_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fidelity, "MAX_CELLS", 4)
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        reference = _reference(page)
        assert "table_cell_limit" in reference["risk_codes"]
        assert reference["tables"] == []
        assert _assess(reference, "")["can_confirm"] is False


@pytest.mark.parametrize("box", [(float("nan"), 0, 10, 10), (20, 0, 10, 10), (0, 0, 0, 0)])
def test_invalid_word_geometry_fails_closed(box: tuple[float, float, float, float]) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        result = _assess(_reference(page), "3", [("3", box)])
        assert result["can_confirm"] is False
        assert "invalid_word_boxes" in result["risk_codes"]


def test_missing_reference_is_not_evidence_that_math_is_preserved() -> None:
    result = _assess({}, "3 X 4 =")
    assert result["can_confirm"] is False
    assert "invalid_math_reference" in result["risk_codes"]


@pytest.mark.parametrize("symbol", ["/", "*", "\u2260", "\u2264"])
def test_other_math_symbols_cannot_be_silently_omitted(symbol: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_htmlbox((30, 30, 500, 100), f"3 {symbol} 4")
        reference = _reference(page)
        assert _assess(reference, "3 4")["can_confirm"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("risk_codes", [[]]),
        ("anchors", [{"text": "3", "bbox": [1, 1, 2, 2]}]),
        ("tables", [{"cells": [{}]}]),
        ("images", [{}]),
        ("native_character_count", None),
    ],
)
def test_malformed_reference_fields_fail_closed(key: str, value: object) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        reference = _reference(page)
        reference[key] = value
        result = _assess(reference, "3", [])
        assert result["can_confirm"] is False
        assert "invalid_math_reference" in result["risk_codes"]


def test_native_rectangle_cell_grid_and_reversed_word_order_with_duplicate_values() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for row in range(2):
            for column in range(2):
                page.draw_rect((40 + column * 35, 100 + row * 25, 75 + column * 35, 125 + row * 25))
        _cell_text(page, 0, 0, "3")
        _cell_text(page, 0, 1, "3")
        reference = _reference(page)
        assert len(reference["tables"][0]["cells"]) == 4
        assert _assess(reference, "3 3", _words(page))["can_confirm"] is True
        result = _assess(reference, "3 3", list(reversed(_words(page))))
        assert result["can_confirm"] is False
        assert "table_reading_order_changed" in result["risk_codes"]


def test_three_equations_require_line_order_or_matching_position_evidence() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for y, equation in ((70, "3 X 4 ="), (110, "4 X 5 ="), (150, "5 X 6 =")):
            page.insert_text((50, y), equation)
        reference = _reference(page)
        text = "3 X 4 =\n4 X 5 =\n5 X 6 ="
        assert _assess(reference, text)["can_confirm"] is True
        flat = text.replace("\n", " ")
        assert _assess(reference, flat)["can_confirm"] is False
        assert _assess(reference, flat, _words(page))["can_confirm"] is True
        displaced = [
            (word, (box[0] + 100, box[1], box[2] + 100, box[3])) for word, box in _words(page)
        ]
        assert _assess(reference, flat, displaced)["can_confirm"] is False


@pytest.mark.parametrize(
    ("limit", "risk"),
    [
        ("MAX_NATIVE_CHARACTERS", "native_character_limit"),
        ("MAX_TOKEN_CHARACTERS", "native_token_limit"),
        ("MAX_METADATA_BYTES", "math_metadata_limit"),
    ],
)
def test_other_native_evidence_limits_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
    risk: str,
) -> None:
    monkeypatch.setattr(fidelity, limit, 1)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "123")
        reference = _reference(page)
        assert risk in reference["risk_codes"]
        assert reference["complete"] is False
        assert _assess(reference, "123")["can_confirm"] is False


@pytest.mark.parametrize(
    ("limit", "risk"),
    [
        ("MAX_IMAGES", "raster_image_limit"),
        ("MAX_RASTER_PIXELS", "raster_pixel_limit"),
        ("MAX_SEGMENTS", "raster_analysis_limit"),
    ],
)
def test_raster_evidence_limits_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
    risk: str,
) -> None:
    monkeypatch.setattr(fidelity, limit, 0)
    with pymupdf.open() as document:
        page = document.new_page()
        _raster(page, grid=True)
        reference = _reference(page)
        assert risk in reference["risk_codes"]
        assert _assess(reference, "2 7")["can_confirm"] is False


def test_table_legacy_content_cannot_be_declared_empty() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "=")
        document.xref_set_key(page.get_fonts()[0][0], "BaseFont", "/FMBindumathi")
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        reference = _reference(document[0])
        cell = reference["tables"][0]["cells"][0]
        assert cell["empty"] is None
        assert cell["text"] is None
        assert _assess(reference, "", [])["can_confirm"] is False


def test_nonmath_prose_punctuation_does_not_create_math_anchors() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "- Read (not calculate) the non-math text.")
        reference = _reference(page)
        assert reference["anchors"] == []
        assert _assess(reference, "Read the text.")["can_confirm"] is True


def test_unknown_font_glyph_between_numbers_is_not_assumed_to_be_nonmath() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        page.insert_text((65, 50), "q", fontname="cour")
        page.insert_text((80, 50), "4")
        document.xref_set_key(page.get_fonts()[1][0], "BaseFont", "/UnidentifiedGlyphFont")
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        reference = _reference(document[0])
        assert _assess(reference, "3 4")["can_confirm"] is False
        assert "untrusted_native_math_font" in reference["risk_codes"]


def test_assessment_details_are_bounded_with_explicit_limit_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        reference = _reference(page)
        monkeypatch.setattr(fidelity, "MAX_METADATA_BYTES", 6000)
        text = "a" * 5000
        result = _assess(reference, text, [(text, (47, 103, 58, 117))])
        assert result["can_confirm"] is False
        assert "math_assessment_metadata_limit" in result["risk_codes"]
        assert len(json.dumps(result).encode()) <= 6000


def _dense_page(page: pymupdf.Page) -> None:
    for index in range(40):
        page.insert_text(
            (40, 45 + index * 18),
            f"Synthetic abc123de page 1, line {index}: 2 + 2 = 4.",
            fontsize=11,
        )


def _encoded_payload(raw: bytes, kind: str = "dict") -> dict[str, Any]:
    return {
        "_math_evidence": "zlib-json-base64-v1",
        "kind": kind,
        "uncompressed_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data": base64.b64encode(zlib.compress(raw, level=9)).decode("ascii"),
    }


def test_dense_native_numeric_words_preserve_original_math_and_positions() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _dense_page(page)
        reference = _reference(page)
        text = page.get_text("text", sort=True)
        words = _words(page)
        assert len(reference["anchors"]) == 320
        assert _assess(reference, text)["can_confirm"] is True
        result = _assess(reference, text, words)
        assert result["can_confirm"] is True
        assert len(result["preserved"]) == 320
        assert result["risk_codes"] == []


def test_dense_evidence_round_trips_losslessly_within_reader_field_budgets() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _dense_page(page)
        reference = _reference(page)
        words = _words(page)
        word_evidence = [[text, list(box)] for text, box in words]
        assert len(json.dumps(reference).encode()) + len(json.dumps(word_evidence).encode()) > 65536
        packed_reference = fidelity.encode_math_evidence(reference)
        packed_words = fidelity.encode_math_evidence(word_evidence)
        assert isinstance(packed_reference, dict)
        assert isinstance(packed_words, dict)
        assert packed_reference["_math_evidence"] == "zlib-json-base64-v1"
        assert len(json.dumps(packed_reference).encode()) <= 24 * 1024
        assert len(json.dumps(packed_words).encode()) <= 16 * 1024
        assert fidelity.decode_math_evidence(packed_reference) == reference
        assert fidelity.decode_math_evidence(packed_words) == word_evidence
        assert (
            _assess(packed_reference, page.get_text("text", sort=True), words)["can_confirm"]
            is True
        )
        assert fidelity.encode_math_evidence(reference) == packed_reference


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"literal": "සිංහල தமிழ் \u00d7 X \u2212", "empty": [None, "", False]},
        [["abc123de", [1.125, 2.25, 3.5, 4.75]]],
    ],
)
def test_small_math_evidence_stays_plain_without_mutation(
    value: dict[str, Any] | list[Any],
) -> None:
    before = json.dumps(value)
    assert fidelity.encode_math_evidence(value) is value
    assert fidelity.decode_math_evidence(value) is value
    assert json.dumps(value) == before


def test_codec_compresses_at_eight_kib_without_rewriting_unicode_or_float_data() -> None:
    value = {
        "text": "සිංහල தமிழ் \u00d7 X \u2212" * 1000,
        "box": [0.125, 1.0, -0.0, 0.30000000000000004],
    }
    encoded = fidelity.encode_math_evidence(value)
    assert isinstance(encoded, dict)
    assert encoded["_math_evidence"] == "zlib-json-base64-v1"
    decoded = fidelity.decode_math_evidence(encoded)
    assert decoded == value
    assert json.dumps(decoded) == json.dumps(value)
    assert isinstance(fidelity.encode_math_evidence({"text": "a" * 8192}), dict)
    assert "_math_evidence" in fidelity.encode_math_evidence({"text": "a" * 8192})


def test_list_and_dict_encoded_limits_are_separate_and_never_truncate() -> None:
    text = "".join(hashlib.sha256(str(index).encode()).hexdigest() for index in range(350))
    encoded = fidelity.encode_math_evidence({"text": text})
    assert 16 * 1024 < len(json.dumps(encoded).encode()) <= 24 * 1024
    assert fidelity.decode_math_evidence(encoded) == {"text": text}
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.encode_math_evidence([text])
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.encode_math_evidence({"text": text * 3 + text[::-1]})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("_math_evidence", "zlib-json-base64-v99"),
        ("kind", "scalar"),
        ("uncompressed_bytes", -1),
        ("uncompressed_bytes", True),
        ("uncompressed_bytes", 1024 * 1024 + 1),
        ("uncompressed_bytes", 3),
        ("sha256", "0" * 64),
        ("sha256", "not-a-hash"),
        ("data", "@@@"),
        ("data", "සිංහල"),
        pytest.param("data", "a" * (24 * 1024), id="oversized-base64"),
        ("extra", "unknown"),
    ],
)
def test_codec_rejects_invalid_envelope_fields_with_bounded_errors(key: str, value: object) -> None:
    envelope = _encoded_payload(b"{}")
    envelope[key] = value
    with pytest.raises(ValueError, match="math evidence") as caught:
        fidelity.decode_math_evidence(envelope)
    assert len(str(caught.value)) <= 80


@pytest.mark.parametrize("variant", ["truncated", "trailing", "second_stream", "compressed_limit"])
def test_codec_requires_a_single_complete_bounded_zlib_stream(variant: str) -> None:
    envelope = _encoded_payload(b'{"value":42}')
    compressed = base64.b64decode(envelope["data"])
    if variant == "truncated":
        compressed = compressed[:-1]
    elif variant == "trailing":
        compressed += b"trailing"
    elif variant == "second_stream":
        compressed += zlib.compress(b"{}")
    else:
        compressed += b"0" * (18 * 1024)
    envelope["data"] = base64.b64encode(compressed).decode("ascii")
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(envelope)


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"42",
        b'"string"',
        b"{broken}",
        b'{"value":NaN}',
        b'{"value":1e9999}',
        b'{"same":1,"same":2}',
        b"\xff",
        b'{"nested":' + b"[" * 100 + b"]" * 100 + b"}",
    ],
)
def test_codec_rejects_invalid_or_ambiguous_json_shapes(raw: bytes) -> None:
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(_encoded_payload(raw))


def test_codec_checks_declared_json_kind_and_accepts_exact_byte_ceiling() -> None:
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(_encoded_payload(b"[]"))
    ceiling = 1024 * 1024
    raw = b'["' + b"a" * (ceiling - 4) + b'"]'
    assert len(raw) == ceiling
    assert fidelity.decode_math_evidence(_encoded_payload(raw, "list")) == ["a" * (ceiling - 4)]


def test_codec_rejects_zip_bomb_even_when_declared_size_is_tiny() -> None:
    envelope = _encoded_payload(b'["' + b"a" * (2 * 1024 * 1024) + b'"]', "list")
    envelope["uncompressed_bytes"] = 2
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(envelope)


@pytest.mark.parametrize(
    "value",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {1: "not-a-string-key"},
        {"tuple": (1, 2)},
        {"_math_evidence": "reserved"},
        {"huge": "a" * (1024 * 1024 + 1)},
    ],
)
def test_codec_refuses_non_json_or_unbounded_plain_evidence(value: Any) -> None:
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.encode_math_evidence(value)
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(value)


def test_codec_rejects_cycles_without_recursion_or_payload_disclosure() -> None:
    value: list[Any] = []
    value.append(value)
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.encode_math_evidence(value)
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(value)


def test_invalid_encoded_reference_fails_closed_in_math_assessment() -> None:
    envelope = _encoded_payload(b"{}")
    envelope["sha256"] = "0" * 64
    result = _assess(envelope, "3 X 4 =")
    assert result["can_confirm"] is False
    assert "invalid_math_reference" in result["risk_codes"]


@pytest.mark.parametrize("movement", ["row", "column", "duplicate", "oversized"])
def test_numeric_word_matching_cannot_reassign_identical_values_to_other_positions(
    movement: str,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "abc123de")
        page.insert_text((300, 70), "abc123de")
        page.insert_text((50, 120), "abc123de")
        reference = _reference(page)
        words = _words(page)
        text = page.get_text("text", sort=True)
        assert _assess(reference, text, words)["can_confirm"] is True
        changed = list(words)
        if movement == "row":
            changed[0], changed[2] = changed[2], changed[0]
        elif movement == "column":
            changed[0], changed[1] = changed[1], changed[0]
        elif movement == "duplicate":
            changed[1] = changed[0]
        else:
            changed[0] = (changed[0][0], (40, 50, 500, 140))
        result = _assess(reference, text, changed)
        assert result["can_confirm"] is False
        assert "math_anchor_position_mismatch" in result["risk_codes"]


def test_older_native_reference_without_word_context_still_matches_numeric_words() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _dense_page(page)
        reference = _reference(page)
        for anchor in reference["anchors"]:
            anchor.pop("word_bbox", None)
        assert (
            _assess(reference, page.get_text("text", sort=True), _words(page))["can_confirm"]
            is True
        )


def test_compressed_reference_preserves_empty_table_cells_and_rejects_wrong_positions() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        _cell_text(page, 0, 1, "4")
        reference = _reference(page)
        envelope = _encoded_payload(json.dumps(reference).encode())
        assert fidelity.decode_math_evidence(envelope) == reference
        words = _words(page)
        result = _assess(envelope, "3 4", words)
        assert result["can_confirm"] is True
        assert sum(cell["empty"] is True for cell in result["tables"][0]["cells"]) == 7
        misplaced = [(words[0][0], words[1][1]), (words[1][0], words[0][1])]
        result = _assess(envelope, "3 4", misplaced)
        assert result["can_confirm"] is False
        assert "table_cell_mismatch" in result["risk_codes"]


def test_compressed_json_node_budget_is_enforced_independently_of_byte_size() -> None:
    raw = b"[" + b"0," * 100_000 + b"0]"
    with pytest.raises(ValueError, match="math evidence"):
        fidelity.decode_math_evidence(_encoded_payload(raw, "list"))


def _raw_native(text: str, font: str = "Helvetica") -> dict[str, Any]:
    return {
        "blocks": [
            {
                "lines": [
                    {
                        "dir": (1, 0),
                        "spans": [
                            {
                                "font": font,
                                "chars": [
                                    {"c": char, "bbox": [50 + index * 8, 50, 56 + index * 8, 62]}
                                    for index, char in enumerate(text)
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def _assert_reference_blocked(reference: dict[str, Any], code: str) -> None:
    assert code in reference["risk_codes"]
    assert reference["complete"] is False
    assert _assess(reference, "", [])["can_confirm"] is False


@pytest.mark.parametrize("value", [None, 3, "not-an-object", (1, 2)])
def test_codec_rejects_scalar_or_tuple_roots(value: Any) -> None:
    for operation in (fidelity.encode_math_evidence, fidelity.decode_math_evidence):
        with pytest.raises(ValueError, match=r"^invalid math evidence$"):
            operation(value)


def test_codec_checks_serialized_bytes_not_only_character_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fidelity, "MAX_METADATA_BYTES", 64)
    value = {"letters": "සිංහල" * 3}
    assert len(value["letters"]) < 64
    assert len(json.dumps(value).encode()) > 64
    with pytest.raises(ValueError, match=r"^invalid math evidence$"):
        fidelity.encode_math_evidence(value)
    with pytest.raises(ValueError, match=r"^invalid math evidence$"):
        fidelity.decode_math_evidence(value)


def test_decoder_checks_decoded_compressed_size_at_base64_padding_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = _encoded_payload(b'{"x":1}')
    compressed_size = len(base64.b64decode(envelope["data"]))
    assert compressed_size % 3 == 0
    monkeypatch.setattr(fidelity, "MAX_COMPRESSED_MATH_EVIDENCE_BYTES", compressed_size - 1)
    with pytest.raises(ValueError, match=r"^invalid math evidence$"):
        fidelity.decode_math_evidence(envelope)


def test_decoder_rejects_nested_encoded_evidence_instead_of_recursively_expanding() -> None:
    inner = _encoded_payload(b"{}")
    outer = _encoded_payload(json.dumps(inner).encode())
    with pytest.raises(ValueError, match=r"^invalid math evidence$"):
        fidelity.decode_math_evidence(outer)


@pytest.mark.parametrize(
    ("character", "box", "code"),
    [
        ("", [58, 50, 64, 62], "unsupported_native_character"),
        ("12", [58, 50, 64, 62], "unsupported_native_character"),
        ("9", [], "invalid_native_geometry"),
        (" ", [0, 0, 0, 0], "invalid_native_geometry"),
    ],
)
def test_invalid_native_characters_cannot_merge_neighbouring_numbers(
    monkeypatch: pytest.MonkeyPatch,
    character: str,
    box: list[int],
    code: str,
) -> None:
    raw = _raw_native("3 4")
    raw["blocks"][0]["lines"][0]["spans"][0]["chars"][1] = {"c": character, "bbox": box}
    before = deepcopy(raw)
    with pymupdf.open() as document:
        page = document.new_page()
        monkeypatch.setattr(page, "get_text", lambda *args, **kwargs: raw)
        reference = _reference(page)
        _assert_reference_blocked(reference, code)
        assert reference["anchors"] == []
        assert _assess(reference, "34")["can_confirm"] is False
        assert raw == before


def test_native_parse_failure_retains_only_completed_prior_line_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_native("3 4")
    damaged = raw["blocks"][0]["lines"][0]
    damaged["spans"][0]["chars"][1]["bbox"] = []
    completed = _raw_native("7")["blocks"][0]["lines"][0]
    raw["blocks"][0]["lines"].insert(0, completed)
    with pymupdf.open() as document:
        page = document.new_page()
        monkeypatch.setattr(page, "get_text", lambda *args, **kwargs: raw)
        reference = _reference(page)
        _assert_reference_blocked(reference, "invalid_native_geometry")
        assert [anchor["text"] for anchor in reference["anchors"]] == ["7"]
        assert _assess(reference, "7")["can_confirm"] is False


@pytest.mark.parametrize("symbol", ["\u221a", "\ufffd", "\ue000", "\u00bd"])
def test_unsupported_native_symbols_cannot_vanish_from_matching_numeric_text(
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        raw = _raw_native("3 " + symbol)
        monkeypatch.setattr(page, "get_text", lambda *args, **kwargs: raw)
        reference = _reference(page)
        _assert_reference_blocked(reference, "unsupported_native_math_symbol")
        assert _assess(reference, "3")["can_confirm"] is False


def test_oversized_standard_looking_font_name_is_not_a_trusted_math_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        raw = _raw_native("3", "Helvetica" + "A" * 128)
        monkeypatch.setattr(page, "get_text", lambda *args, **kwargs: raw)
        reference = _reference(page)
        _assert_reference_blocked(reference, "untrusted_native_math_font")
        assert reference["anchors"] == []


@pytest.mark.parametrize("rotation", ["glyphs", "page"])
def test_rotated_math_requires_review_instead_of_unrotated_position_confirmation(
    rotation: str,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((100, 200), "3 + 4", rotate=90 if rotation == "glyphs" else 0)
        if rotation == "page":
            page.set_rotation(90)
        reference = _reference(page)
        code = (
            "unsupported_native_math_layout"
            if rotation == "glyphs"
            else "unsupported_page_rotation"
        )
        _assert_reference_blocked(reference, code)
        assert _assess(reference, "3 + 4")["can_confirm"] is False


@pytest.mark.parametrize(
    ("method", "code"),
    [
        ("get_text", "native_math_evidence_unavailable"),
        ("get_drawings", "vector_layout_evidence_unavailable"),
        ("get_image_info", "raster_layout_evidence_unavailable"),
        ("get_pixmap", "raster_layout_evidence_unavailable"),
    ],
)
def test_external_parser_failures_are_bounded_review_evidence(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    code: str,
) -> None:
    def broken_parser(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic-parser-detail")

    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        if method == "get_pixmap":
            _raster(page, grid=False)
        monkeypatch.setattr(page, method, broken_parser)
        reference = _reference(page)
        _assert_reference_blocked(reference, code)
        assert "synthetic-parser-detail" not in json.dumps(reference)


def test_table_analysis_failure_does_not_reuse_a_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_tables(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic-table-stage-failure")

    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        monkeypatch.setattr(fidelity, "_tables", broken_tables)
        reference = _reference(page)
        _assert_reference_blocked(reference, "table_layout_evidence_unavailable")
        assert reference["tables"] == []
        assert [anchor["text"] for anchor in reference["anchors"]] == ["3"]


@pytest.mark.parametrize("bad_geometry", ["nonfinite_line", "invalid_path_box"])
def test_malformed_vector_geometry_is_never_treated_as_a_successful_empty_layout(
    monkeypatch: pytest.MonkeyPatch,
    bad_geometry: str,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        if bad_geometry == "nonfinite_line":
            items = [("l", (float("nan"), 40), (60, 40))]
            code = "vector_layout_evidence_unavailable"
        else:
            items = [("l", (40, 40), (60, 60))]
            code = "invalid_vector_geometry"
        monkeypatch.setattr(
            page,
            "get_drawings",
            lambda: [
                {
                    "type": "s",
                    "items": items,
                    "rect": pymupdf.Rect(0, 0, 0, 0),
                }
            ],
        )
        _assert_reference_blocked(_reference(page), code)


@pytest.mark.parametrize("drawing", ["diagonal", "curve"])
def test_non_axis_vectors_overlapping_math_remain_unsupported(drawing: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "3")
        if drawing == "diagonal":
            page.draw_line((45, 55), (70, 80))
        else:
            page.draw_bezier((45, 55), (60, 100), (75, 20), (90, 80))
        reference = _reference(page)
        _assert_reference_blocked(reference, "unsupported_vector_math_layout")
        assert reference["unsupported_layouts"]


def test_unsupported_vector_and_segment_limits_are_explicit_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for index in range(33):
            page.draw_line((40, 40 + index * 4), (60, 42 + index * 4))
        reference = _reference(page)
        _assert_reference_blocked(reference, "unsupported_vector_limit")
        assert len(reference["unsupported_layouts"]) == 32
        monkeypatch.setattr(fidelity, "MAX_SEGMENTS", 4)
        _assert_reference_blocked(_reference(page), "vector_segment_limit")


def test_thin_filled_rectangles_form_an_empty_grid_without_inventing_values() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for x in (40, 75, 110):
            page.draw_rect((x, 100, x + 1, 151), color=None, fill=(0, 0, 0))
        for y in (100, 125, 150):
            page.draw_rect((40, y, 111, y + 1), color=None, fill=(0, 0, 0))
        page.draw_rect((200, 200, 240, 240), color=None, fill=(0, 0, 0))
        reference = _reference(page)
        assert len(reference["tables"]) == 1
        assert (reference["tables"][0]["rows"], reference["tables"][0]["columns"]) == (2, 2)
        assert all(cell["empty"] is True for cell in reference["tables"][0]["cells"])
        assert _assess(reference, "", [])["can_confirm"] is True


def test_disjoint_collinear_grid_edges_stay_separate_tables() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for offset in (0, 200):
            for x in (40, 75, 110):
                page.draw_line((x + offset, 100), (x + offset, 150))
            for y in (100, 125, 150):
                page.draw_line((40 + offset, y), (110 + offset, y))
            page.insert_text((47 + offset, 116), "3", fontsize=10)
        reference = _reference(page)
        assert len(reference["tables"]) == 2
        assert [len(table["cells"]) for table in reference["tables"]] == [4, 4]
        result = _assess(reference, "3 3", _words(page))
        assert result["can_confirm"] is True
        assert len(result["tables"]) == 2


def test_vertical_raster_line_budget_does_not_claim_a_grid_or_verified_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pymupdf.open() as image_document, pymupdf.open() as document:
        source = image_document.new_page(width=80, height=80)
        for x in (10, 30, 50):
            source.draw_line((x, 5), (x, 75), width=1)
        page = document.new_page()
        page.insert_image((40, 40, 120, 120), pixmap=source.get_pixmap())
        monkeypatch.setattr(fidelity, "MAX_SEGMENTS", 2)
        reference = _reference(page)
        _assert_reference_blocked(reference, "raster_analysis_limit")
        assert reference["images"][0]["analysis_complete"] is False
        assert reference["images"][0]["grid_detected"] is False


def test_off_page_raster_geometry_is_not_rendered_or_silently_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_render(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid raster geometry must not be rendered")

    with pymupdf.open() as document:
        page = document.new_page()
        monkeypatch.setattr(
            page,
            "get_image_info",
            lambda: [
                {
                    "bbox": [-100, -100, -50, -50],
                    "width": 50,
                    "height": 50,
                }
            ],
        )
        monkeypatch.setattr(page, "get_pixmap", unexpected_render)
        reference = _reference(page)
        _assert_reference_blocked(reference, "invalid_raster_geometry")
        assert reference["images"][0]["analysis_complete"] is False


def test_table_cell_text_limit_preserves_unknown_instead_of_a_truncated_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "abc")
        monkeypatch.setattr(fidelity, "MAX_TOKEN_CHARACTERS", 2)
        reference = _reference(page)
        _assert_reference_blocked(reference, "table_cell_text_limit")
        cell = reference["tables"][0]["cells"][0]
        assert cell["text"] is None
        assert cell["empty"] is None
        assert cell["content_status"] == "unverified"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (None, "candidate_math_text_limit"),
        pytest.param("3" * (100_000 + 1), "candidate_math_text_limit", id="overlong-candidate"),
        ("3 \u221a", "unsupported_candidate_math_symbol"),
    ],
)
def test_invalid_or_unknown_candidate_text_never_passes_matching_numbers(
    text: Any, code: str
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        result = _assess(_reference(page), text)
        assert result["can_confirm"] is False
        assert code in result["risk_codes"]


@pytest.mark.parametrize(
    "word",
    [
        ("", (50, 40, 60, 55)),
        (None, (50, 40, 60, 55)),
        ("3", (-50, -50, -40, -40)),
        ("3", []),
        ("3", None),
        ("3", (1, 2, 3)),
    ],
)
def test_invalid_word_shapes_and_outside_page_boxes_fail_closed(word: Any) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        result = _assess(_reference(page), "3", [word])
        assert result["can_confirm"] is False
        assert "invalid_word_boxes" in result["risk_codes"]


@pytest.mark.parametrize(
    ("limit", "words", "code"),
    [
        ("MAX_WORDS", [("3", (50, 40, 60, 55)), ("4", (70, 40, 80, 55))], "ocr_word_limit"),
        ("MAX_TEXT_CHARACTERS", [("p3q", (50, 40, 60, 55))], "ocr_word_text_limit"),
    ],
)
def test_word_budgets_reject_incomplete_position_evidence(
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
    words: Any,
    code: str,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        reference = _reference(page)
        monkeypatch.setattr(fidelity, limit, 1)
        result = _assess(reference, "3", words)
        assert result["can_confirm"] is False
        assert code in result["risk_codes"]


@pytest.mark.parametrize("key", ["anchors", "tables", "images"])
@pytest.mark.parametrize("oversized", [False, True])
def test_reference_collection_bounds_are_revalidated_not_assumed(key: str, oversized: bool) -> None:
    with pymupdf.open() as document:
        reference = _reference(document.new_page())
    limits = {
        "anchors": fidelity.MAX_ANCHORS,
        "tables": fidelity.MAX_CELLS,
        "images": fidelity.MAX_IMAGES,
    }
    reference[key] = [{}] * (limits[key] + 1) if oversized else {}
    assert not fidelity._valid_reference(reference)
    assert _assess(reference, "")["can_confirm"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("word_bbox", [0, 0, 1, 1]),
        ("id", 100),
        ("line", "row"),
        ("source", "legacy_font"),
        ("text", ""),
        ("text", 3),
        ("text", "not-math"),
        ("text", "3" * 65),
    ],
)
def test_malformed_native_anchor_reference_cannot_confirm(key: str, value: object) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "3")
        reference = _reference(page)
    reference["anchors"][0][key] = value
    assert not fidelity._valid_reference(reference)
    result = _assess(reference, "3")
    assert result["can_confirm"] is False
    assert "invalid_math_reference" in result["risk_codes"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("id", 1),
        ("source", "raster_guess"),
        ("rows", 0),
        ("rows", "3"),
        ("columns", 0),
        ("columns", "3"),
        ("cells", {}),
        ("cells", []),
    ],
)
def test_malformed_table_structure_cannot_confirm_matching_values(key: str, value: object) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        reference = _reference(page)
    reference["tables"][0][key] = value
    assert not fidelity._valid_reference(reference)
    assert _assess(reference, "", [])["can_confirm"] is False


def test_reference_cell_budget_counts_all_tables_together(monkeypatch: pytest.MonkeyPatch) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        reference = _reference(page)
    additional = deepcopy(reference["tables"][0])
    additional["id"] = 1
    reference["tables"].append(additional)
    monkeypatch.setattr(fidelity, "MAX_CELLS", 10)
    assert not fidelity._valid_reference(reference)
    assert _assess(reference, "", [])["can_confirm"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("row", 2),
        ("column", 2),
        ("bbox", [300, 300, 310, 310]),
        ("content_status", "verified-by-guess"),
        ("anchor_ids", {}),
        ("anchor_ids", ["0"]),
        ("anchor_ids", [-1]),
        ("anchor_ids", [100]),
        ("text", 3),
        ("text", "a" * 65),
        ("empty", "false"),
    ],
)
def test_malformed_cell_content_and_anchor_links_never_confirm(key: str, value: object) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        _grid(page)
        _cell_text(page, 0, 0, "3")
        reference = _reference(page)
    reference["tables"][0]["cells"][0][key] = value
    assert not fidelity._valid_reference(reference)
    assert _assess(reference, "3", [])["can_confirm"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("grid_detected", "yes"),
        ("analysis_complete", 1),
        ("content_verified", True),
        ("grids", {}),
    ],
)
def test_malformed_raster_indicators_do_not_create_verification(key: str, value: object) -> None:
    with pymupdf.open() as document:
        reference = _reference(document.new_page())
    image: dict[str, Any] = {
        "bbox": [40, 40, 80, 80],
        "grid_detected": False,
        "analysis_complete": True,
        "content_verified": False,
        "grids": [],
    }
    image[key] = value
    reference["images"] = [image]
    assert not fidelity._valid_reference(reference)
    assert _assess(reference, "")["can_confirm"] is False
