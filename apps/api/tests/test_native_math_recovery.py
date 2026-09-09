import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pymupdf
import pytest

from exam_guru_api.documents import native_math_recovery as recovery
from exam_guru_api.documents import source_math_fidelity as fidelity


def _words(page: pymupdf.Page) -> list[tuple[str, tuple[float, float, float, float]]]:
    return [(word[4], tuple(word[:4])) for word in page.get_text("words", sort=True)]


def _page(document: pymupdf.Document, equation: str = "3 X 4 = 13") -> pymupdf.Page:
    page = document.new_page()
    page.insert_text((50, 40), "Before private prose", fontname="cour")
    page.insert_text((50, 90), equation)
    page.insert_text((50, 150), "After private prose", fontname="cour")
    return page


def _ocr(page: pymupdf.Page, equation: str = "3 x 4 = 12") -> tuple[str, list[Any]]:
    words = _words(page)
    replacement = equation.split()
    selected = [index for index, (_, box) in enumerate(words) if 70 < box[1] < 100]
    assert len(selected) == len(replacement)
    for index, text in zip(selected, replacement, strict=True):
        words[index] = (text, words[index][1])
    return "Before private prose\r\n  " + equation + "  \r\nAfter private prose\n", words


def test_recovers_exact_source_not_correct_answer_and_preserves_ocr_and_pdf() -> None:
    with pymupdf.open() as document:
        page = _page(document)
        text, words = _ocr(page)
        original_words = deepcopy(words)
        original_pdf = document.tobytes(no_new_id=True)
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assert result.raw_text == text.replace("3 x 4 = 12", "3 X 4 = 13")
        assert result.words == tuple(_words(page))
        assert words == original_words
        assert document.tobytes(no_new_id=True) == original_pdf
        assert recovery.recover_native_equations(page, result.raw_text, list(result.words)) is None
        assert "private prose" not in repr(result)
        assert "private prose" not in json.dumps(result.provenance)
        assert result.provenance["algorithm_version"] == "native-math-recovery-v1"
        assert result.provenance["original_ocr_sha256"] == hashlib.sha256(text.encode()).hexdigest()
        edits = result.provenance["edits"]
        assert isinstance(edits, list)
        assert len(edits) == 1
        edit = edits[0]
        start = text.index("3 x")
        assert edit["old_offsets"] == [start, start + len("3 x 4 = 12")]
        assert edit["source_line"] == 2
        assert edit["source_fonts"] == ["Helvetica"]
        assert edit["source_text_sha256"] == hashlib.sha256(b"3 X 4 = 13").hexdigest()
        assert edit["old_text_sha256"] == hashlib.sha256(b"3 x 4 = 12").hexdigest()
        with pytest.raises(FrozenInstanceError):
            cast(Any, result).raw_text = "changed"


@pytest.mark.parametrize("equation", ["3 X 4 =", "3 x 4 = 13", "3 \u00d7 4 = 13", "1.5 + 2 = 7"])
def test_preserves_literal_operators_and_unsolved_or_wrong_equations(equation: str) -> None:
    with pymupdf.open() as document:
        page = _page(document, equation)
        source_words = _words(page)
        words = [("?" if 70 < box[1] < 100 else token, box) for token, box in source_words]
        text = (
            "Before private prose\n"
            + " ".join(token for token, box in words if 70 < box[1] < 100)
            + "\nAfter private prose"
        )
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assert result.raw_text == "Before private prose\n" + equation + "\nAfter private prose"


@pytest.mark.parametrize("position", ["middle", "first", "last"])
def test_inserts_missing_equation_at_unambiguous_line_boundary(position: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        lines = ["Before", "3 X 4 = 13", "After"]
        if position == "first":
            lines = lines[1:]
        elif position == "last":
            lines = lines[:-1]
        for index, line in enumerate(lines):
            page.insert_text((50, 40 + index * 50), line)
        words = [(token, box) for token, box in _words(page) if token in {"Before", "After"}]
        text = "\r\n".join(token for token, _ in words)
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        separator = "\r\n" if position == "middle" else "\n"
        assert result.raw_text == separator.join(lines)
        assert [token for token, _ in result.words] == [
            token for line in lines for token in line.split()
        ]
        assert len(cast(list[Any], result.provenance["edits"])) == 1


@pytest.mark.parametrize(
    "equation",
    ["123", "3 + 4", "Answer 3 X 4 = 13", "3 X 4 = 13 apples", "3 =", "abc = 123"],
)
def test_refuses_numbers_non_equations_and_prose(equation: str) -> None:
    with pymupdf.open() as document:
        page = _page(document, equation)
        words = [(token, box) for token, box in _words(page) if not 70 < box[1] < 100]
        assert (
            recovery.recover_native_equations(
                page, "Before private prose\nAfter private prose", words
            )
            is None
        )


@pytest.mark.parametrize("font", ["FMBindumathi", "ABCDEF+FMAbhaya", "UnknownNative"])
def test_refuses_legacy_and_untrusted_font_equations(font: str) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        xref = next(item[0] for item in page.get_fonts() if item[3] == "Helvetica")
        document.xref_set_key(xref, "BaseFont", "/" + font)
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page = document[0]
        text, words = _ocr(page)
        assert recovery.recover_native_equations(page, text, words) is None


def test_keeps_legacy_prose_untouched_while_recovering_trusted_equation() -> None:
    with pymupdf.open() as document:
        page = _page(document)
        xref = next(item[0] for item in page.get_fonts() if item[3] == "Courier")
        document.xref_set_key(xref, "BaseFont", "/FMBindumathi")
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page = document[0]
        text, words = _ocr(page)
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assert result.raw_text == text.replace("3 x 4 = 12", "3 X 4 = 13")


@pytest.mark.parametrize(
    "problem",
    [
        "missing_word",
        "duplicate_text",
        "word_mismatch",
        "reordered",
        "nonfinite",
        "outside",
        "overlap",
    ],
)
def test_refuses_ambiguous_ocr_mapping_and_geometry(problem: str) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        text, words = _ocr(page)
        if problem == "missing_word":
            words.pop()
        elif problem == "duplicate_text":
            text += "After private prose"
        elif problem == "word_mismatch":
            words[0] = ("Unknown", words[0][1])
        elif problem == "reordered":
            words[0], words[-1] = words[-1], words[0]
            text = "\n".join(token for token, _ in words)
        elif problem == "nonfinite":
            words[0] = (words[0][0], (0, 0, float("nan"), 10))
        elif problem == "outside":
            words[0] = (words[0][0], (-10, -10, 10, 10))
        else:
            words[1] = (words[1][0], words[0][1])
        assert recovery.recover_native_equations(page, text, words) is None


@pytest.mark.parametrize("obstruction", ["table", "prose", "image", "rotation"])
def test_refuses_table_overlapping_prose_image_or_rotated_equation(obstruction: str) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        if obstruction == "table":
            for y in (65, 105, 125):
                page.draw_line((40, y), (250, y))
            for x in (40, 150, 250):
                page.draw_line((x, 65), (x, 125))
        elif obstruction == "prose":
            page.insert_text((230, 90), "same row prose", fontname="cour")
        elif obstruction == "image":
            pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 20, 20), False)
            pixmap.clear_with(255)
            page.insert_image((45, 65, 200, 105), pixmap=pixmap)
        else:
            page.set_rotation(90)
        words = [(token, box) for token, box in _words(page) if not 70 < box[1] < 100]
        assert (
            recovery.recover_native_equations(
                page, "Before private prose\nAfter private prose", words
            )
            is None
        )


def test_recovery_does_not_claim_raster_table_or_other_number_fidelity() -> None:
    with pymupdf.open() as document:
        page = _page(document)
        page.insert_text((50, 220), "Other 19", fontname="cour")
        text, words = _ocr(page)
        text += "Other 18\n"
        words[-1] = ("18", words[-1][1])
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assert result.raw_text.endswith("Other 18\n")
        assessment = fidelity.assess_math_fidelity(
            fidelity.extract_math_layout(page), result.raw_text, list(result.words)
        )
        assert assessment["can_confirm"] is False


def test_empty_or_unchanged_ocr_returns_none() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        assert recovery.recover_native_equations(page, "", []) is None
        page.insert_text((50, 90), "3 X 4 = 13")
        assert recovery.recover_native_equations(page, "3 X 4 = 13\n", _words(page)) is None


@pytest.mark.parametrize("obstruction", ["hidden", "transparent", "covered"])
def test_hidden_or_visually_obscured_native_text_is_not_recovery_evidence(obstruction: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 40), "Before")
        page.insert_text(
            (50, 90),
            "3 X 4 = 13",
            render_mode=3 if obstruction == "hidden" else 0,
            fill_opacity=0 if obstruction == "transparent" else 1,
        )
        page.insert_text((50, 150), "After")
        if obstruction == "covered":
            page.draw_rect((45, 70, 200, 110), color=None, fill=(1, 1, 1))
        words = [(token, box) for token, box in _words(page) if token in {"Before", "After"}]
        assert recovery.recover_native_equations(page, "Before\nAfter", words) is None


@pytest.mark.parametrize("suffix", ["", "\n", "\r\n"])
def test_multiple_missing_equations_preserve_order_and_unaffected_whitespace(suffix: str) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 40), "Before")
        for index in range(3):
            page.insert_text((50, 90 + index * 40), f"{index + 1} X 4 = 13")
        words = _words(page)[:1]
        text = "  Before" + suffix
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        separator = "\r\n" if suffix == "\r\n" else "\n"
        assert result.raw_text == "  Before" + separator + separator.join(
            f"{index + 1} X 4 = 13" for index in range(3)
        )
        assert result.words == tuple(_words(page))
        assert len(cast(list[Any], result.provenance["edits"])) == 3


@pytest.mark.parametrize(
    "which",
    ["provenance", "equations", "line", "input_text", "input_words", "output_text", "output_words"],
)
def test_bounded_recovery_refuses_budget_overruns(
    which: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        words = [(token, box) for token, box in _words(page) if not 70 < box[1] < 100]
        text = "Before private prose\nAfter private prose"
        module, limit, maximum = {
            "provenance": (recovery, "MAX_PROVENANCE_BYTES", 1),
            "equations": (recovery, "MAX_EQUATIONS", 0),
            "line": (recovery, "MAX_LINE_CHARACTERS", 1),
            "input_text": (fidelity, "MAX_TEXT_CHARACTERS", len(text) - 1),
            "input_words": (fidelity, "MAX_WORDS", len(words) - 1),
            "output_text": (fidelity, "MAX_TEXT_CHARACTERS", len(text)),
            "output_words": (fidelity, "MAX_WORDS", len(words)),
        }[which]
        monkeypatch.setattr(module, limit, maximum)
        assert recovery.recover_native_equations(page, text, words) is None


@pytest.mark.parametrize("problem", ["bad_reference", "changed_native", "native_risk", "exception"])
def test_fail_closed_on_unavailable_or_inconsistent_source_evidence(
    problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        text, words = _ocr(page)
        reference = fidelity.extract_math_layout(page)
        native = fidelity._native

        def extract(_: pymupdf.Page) -> dict[str, object]:
            if problem == "exception":
                raise RuntimeError("synthetic unavailable evidence")
            return {} if problem == "bad_reference" else reference

        def changed(source: pymupdf.Page, risks: set[str]) -> Any:
            result = native(source, risks)
            if problem == "native_risk":
                risks.add("synthetic_limit")
            elif problem == "changed_native":
                result[0][0]["text"] = "9"
            return result

        monkeypatch.setattr(fidelity, "extract_math_layout", extract)
        monkeypatch.setattr(fidelity, "_native", changed)
        assert recovery.recover_native_equations(page, text, words) is None


@pytest.mark.parametrize("problem", ["outside_region", "two_rows", "not_one_ocr_line"])
def test_refuses_ocr_regions_that_do_not_uniquely_match_equation(problem: str) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        if problem == "outside_region":
            words = [("unknown", (40.0, 80.0, 400.0, 90.0))]
            text = "unknown"
        elif problem == "two_rows":
            words = [("one", (50.0, 80.0, 60.0, 84.0)), ("two", (50.0, 86.0, 60.0, 91.0))]
            text = "one\ntwo"
        else:
            text, words = _ocr(page)
            text = text.replace("\r\n", " ")
        assert recovery.recover_native_equations(page, text, words) is None


def test_conflicting_edits_refuse_entire_derivation() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 90), "3 X 4 = 13", fontsize=1)
        page.insert_text((50, 92), "5 X 6 = 13", fontsize=1)
        assert (
            recovery.recover_native_equations(page, "?", [("?", (50.0, 89.5, 53.0, 91.5))]) is None
        )


def test_visual_evidence_budget_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    with pymupdf.open() as document:
        page = _page(document)
        text, words = _ocr(page)
        monkeypatch.setattr(recovery, "MAX_VISUAL_ITEMS", 0)
        assert recovery.recover_native_equations(page, text, words) is None


def test_three_calibri_equations_and_legacy_prose_are_separate_sources() -> None:
    with pymupdf.open() as document:
        page = _page(document)
        page.insert_text((50, 190), "6 X 2 = 19")
        page.insert_text((50, 230), "7 X 3 =")
        for xref, _, _, font, *_ in page.get_fonts():
            document.xref_set_key(
                xref, "BaseFont", "/Calibri" if font == "Helvetica" else "/FMBindumathi"
            )
        data = document.tobytes()
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page = document[0]
        text = "Before private prose\n  garbage  \nAfter private prose\n"
        source_words = _words(page)
        words = [*source_words[:3], ("garbage", (50.0, 80.0, 95.0, 90.0)), *source_words[8:11]]
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assert result.raw_text == (
            "Before private prose\n  3 X 4 = 13  \nAfter private prose\n6 X 2 = 19\n7 X 3 ="
        )
        assert all(
            edit["source_fonts"] == ["Calibri"]
            for edit in cast(list[Any], result.provenance["edits"])
        )
        assert result.words == tuple(source_words)


def test_raster_table_elsewhere_stays_unresolved_after_equation_recovery() -> None:
    with pymupdf.open() as document, pymupdf.open() as raster_document:
        page = _page(document)
        raster = raster_document.new_page(width=150, height=150)
        for position in (10, 50, 90, 130):
            raster.draw_line((position, 10), (position, 130))
            raster.draw_line((10, position), (130, position))
        page.insert_image((40, 250, 190, 400), pixmap=raster.get_pixmap())
        text, words = _ocr(page)
        reference = fidelity.extract_math_layout(page)
        assert reference["risk_codes"] == ["raster_grid_unverified"]
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assessment = fidelity.assess_math_fidelity(reference, result.raw_text, list(result.words))
        assert assessment["can_confirm"] is False


def test_exact_source_spaces_and_unicode_operators_are_not_reconstructed() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        equation = "12 \u00d7 3 ÷ 2 = 18 + 4 \u2212 1"
        page.insert_htmlbox((30, 30, 500, 100), equation)
        source = page.get_text("text").rstrip("\n")
        words = _words(page)
        old_words = [("?", box) for _, box in words]
        result = recovery.recover_native_equations(page, " ".join("?" for _ in words), old_words)
        assert result is not None
        assert result.raw_text == source == equation
        assert result.words == tuple(words)


def test_two_missing_middle_equations_preserve_crlf_blank_lines_and_unicode_prose() -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        for index, line in enumerate(("Before", "3 X 4 =", "4 + 1 = 9", "After")):
            page.insert_text((50, 40 + index * 40), line)
        native_words = _words(page)
        words = [("සිංහල", native_words[0][1]), ("අකුරු", native_words[-1][1])]
        text = "  සිංහල\r\n\r\n\tඅකුරු  \r\n"
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        assert result.raw_text == "  සිංහල\r\n\r\n3 X 4 =\r\n4 + 1 = 9\r\n\tඅකුරු  \r\n"
        assert result.words[0] == words[0]
        assert result.words[-1] == words[-1]
        edits = cast(list[Any], result.provenance["edits"])
        for edit in edits:
            start, end = edit["new_offsets"]
            assert result.raw_text[start:end].endswith("\r\n")


@pytest.mark.parametrize("color", [(1.0,), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0)])
@pytest.mark.parametrize("render_mode", [0, 1])
def test_white_on_white_equation_is_not_visible_recovery_evidence(
    color: tuple[float, ...], render_mode: int
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 40), "Before")
        page.insert_text((50, 90), "3 X 4 = 13", color=color, render_mode=render_mode)
        page.insert_text((50, 150), "After")
        trace = page.get_texttrace()[1]
        assert trace["opacity"] == 1
        assert trace["type"] == render_mode
        assert fidelity.extract_math_layout(page)["anchors"]
        pixels = page.get_pixmap(clip=(40, 65, 200, 110), colorspace=pymupdf.csGRAY, alpha=False)
        assert set(pixels.samples) == {255}
        words = [(token, box) for token, box in _words(page) if token in {"Before", "After"}]
        assert recovery.recover_native_equations(page, "Before\nAfter", words) is None


@pytest.mark.parametrize("compressed", [False, True])
def test_canonical_hashes_verify_decoded_persisted_evidence_independent_of_json_key_order(
    compressed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if compressed:
        monkeypatch.setattr(fidelity, "MATH_EVIDENCE_COMPRESSION_THRESHOLD", 1)
    with pymupdf.open() as document:
        page = _page(document, "3 \u00d7 4 = 13")
        text, words = _ocr(page)
        text = text.replace("Before", "සිංහල")
        words[0] = ("සිංහල", words[0][1])
        reference = fidelity.extract_math_layout(page)
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        reordered_reference = json.loads(
            json.dumps(reference, ensure_ascii=False, indent=2),
            object_pairs_hook=lambda pairs: dict(reversed(pairs)),
        )
        assert reordered_reference == reference
        assert list(reordered_reference) != list(reference)
        for field, value in (
            ("source_reference_sha256", reordered_reference),
            ("original_words_sha256", [[token, list(box)] for token, box in words]),
        ):
            encoded = fidelity.encode_math_evidence(value)
            persisted = json.loads(json.dumps(encoded, ensure_ascii=False, sort_keys=True))
            decoded = fidelity.decode_math_evidence(persisted)
            canonical = json.dumps(
                decoded, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            assert hashlib.sha256(canonical).hexdigest() == result.provenance[field]
            assert (
                hashlib.sha256(json.dumps(decoded).encode()).hexdigest() != result.provenance[field]
            )
            if compressed:
                assert isinstance(encoded, dict)
                assert encoded["_math_evidence"] == "zlib-json-base64-v1"
                assert encoded["sha256"] != result.provenance[field]
        assert result.provenance["evidence_hash_serialization"] == {
            "version": "python-json-sorted-compact-ascii-v1",
            "input": "decoded_math_evidence",
            "fields": ["source_reference_sha256", "original_words_sha256"],
            "hash_algorithm": "sha256",
            "encoding": "utf-8",
            "ensure_ascii": True,
            "sort_keys": True,
            "allow_nan": False,
            "separators": [",", ":"],
        }


@pytest.mark.parametrize("missing", [False, True])
def test_native_boundary_whitespace_does_not_expand_equation_region_over_adjacent_image(
    missing: bool,
) -> None:
    with pymupdf.open() as document:
        equation = "6  X 7 = 41"
        padding = " " * 40
        page = _page(document, padding + equation + " " * 8)
        image = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 20, 20), False)
        image.clear_with(0)
        image_box = (55.0, 65.0, 125.0, 110.0)
        page.insert_image(image_box, pixmap=image)
        native_words = _words(page)
        equation_box = fidelity._union([box for _, box in native_words[3:-3]])
        assert fidelity._overlap(equation_box, image_box) == 0
        line_words = [] if missing else [("unreadable", equation_box)]
        words = [*native_words[:3], *line_words, *native_words[-3:]]
        middle = "" if missing else "  unreadable \t\n"
        text = "Before private prose\n" + middle + "After private prose\n"
        result = recovery.recover_native_equations(page, text, words)
        assert result is not None
        expected_middle = equation + "\n" if missing else "  " + equation + " \t\n"
        assert (
            result.raw_text == "Before private prose\n" + expected_middle + "After private prose\n"
        )
        assert result.words == tuple(native_words)
        edit = cast(list[Any], result.provenance["edits"])[0]
        assert edit["source_line_offsets"] == [len(padding), len(padding) + len(equation)]
        assert tuple(edit["region"]) == equation_box
        assert edit["source_text_sha256"] == hashlib.sha256(equation.encode()).hexdigest()


@pytest.mark.parametrize("obstruction", ["internal_image", "same_row_prose", "reordered_ocr"])
def test_native_boundary_whitespace_does_not_relax_equation_safety(obstruction: str) -> None:
    with pymupdf.open() as document:
        equation = "6" + " " * 20 + "X 7 = 41" if obstruction == "internal_image" else "6 X 7 = 41"
        page = _page(document, " " * 40 + equation + " " * 8)
        if obstruction == "internal_image":
            native_words = _words(page)
            left, right = native_words[3][1][2], native_words[4][1][0]
            image = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 20, 20), False)
            image.clear_with(0)
            page.insert_image((left + 2, 70, right - 2, 100), pixmap=image)
        elif obstruction == "same_row_prose":
            page.insert_text((50, 90), "unrelated prose", fontname="cour")
        native_words = _words(page)
        words = [(token, box) for token, box in native_words if not 70 < box[1] < 100]
        text = "Before private prose\nAfter private prose"
        if obstruction == "reordered_ocr":
            words[0], words[1] = words[1], words[0]
            text = "private Before prose\nAfter private prose"
        assert recovery.recover_native_equations(page, text, words) is None
