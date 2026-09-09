import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import groupby, pairwise
from typing import Any, cast

import pymupdf

from exam_guru_api.documents import source_math_fidelity as fidelity

ALGORITHM_VERSION = "native-math-recovery-v1"
MAX_EQUATIONS = 32
MAX_LINE_CHARACTERS = 256
MAX_PROVENANCE_BYTES = 16_384
MAX_VISUAL_ITEMS = 8192
Box = tuple[float, float, float, float]
Word = tuple[str, Box]


@dataclass(frozen=True, slots=True)
class NativeMathRecovery:
    raw_text: str = field(repr=False)
    words: tuple[Word, ...] = field(repr=False)
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Equation:
    text: str
    bbox: Box
    words: tuple[Word, ...]
    line: int
    fonts: tuple[str, ...]
    offsets: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _OCRLine:
    start: int
    end: int
    line_start: int
    first: int
    last: int
    bbox: Box


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    first: int
    last: int
    replacement: str
    equation: _Equation


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":")
    )


def _vertical_overlap(left: Box, right: Box) -> bool:
    return min(left[3], right[3]) > max(left[1], right[1])


def _contained(inner: Box, outer: Box, margin: float = 0) -> bool:
    return (
        inner[0] >= outer[0] - margin
        and inner[1] >= outer[1] - margin
        and inner[2] <= outer[2] + margin
        and inner[3] <= outer[3] + margin
    )


def _mapped_lines(text: str, words: list[Word], page_box: Box) -> list[_OCRLine] | None:
    tokens = list(re.finditer(r"\S+", text))
    if len(tokens) != len(words):
        return None
    lines: list[_OCRLine] = []
    for index, (token, (word, rectangle)) in enumerate(zip(tokens, words, strict=True)):
        box = fidelity._box(rectangle)
        if token.group() != word or not _contained(box, page_box):
            return None
        if not lines or any(char in text[lines[-1].end : token.start()] for char in "\r\n"):
            if lines and box[1] < lines[-1].bbox[3]:
                return None
            line_start = (
                max(text.rfind("\n", 0, token.start()), text.rfind("\r", 0, token.start())) + 1
            )
            lines.append(_OCRLine(token.start(), token.end(), line_start, index, index + 1, box))
        else:
            previous = lines[-1]
            if not _vertical_overlap(previous.bbox, box) or box[0] < previous.bbox[2]:
                return None
            lines[-1] = _OCRLine(
                previous.start,
                token.end(),
                previous.line_start,
                previous.first,
                index + 1,
                fidelity._union([previous.bbox, box]),
            )
    return lines


def _equations(page: pymupdf.Page, reference: dict[str, Any]) -> list[_Equation]:
    risks: set[str] = set()
    anchors, glyphs, _ = fidelity._native(page, risks)
    if risks or anchors != reference["anchors"]:
        return []
    groups = [list(group) for _, group in groupby(glyphs, key=lambda glyph: glyph.line)]
    regions = [fidelity._union([glyph.bbox for glyph in group]) for group in groups]
    blocked = [
        fidelity._box(item["bbox"])
        for kind in ("tables", "images", "unsupported_layouts")
        for item in reference[kind]
    ]
    visuals = page.get_bboxlog()
    traces = page.get_texttrace()
    if len(visuals) > MAX_VISUAL_ITEMS or len(traces) > MAX_VISUAL_ITEMS:
        return []
    blocked.extend(
        fidelity._box(box) for kind, box in visuals if kind not in {"fill-text", "stroke-text"}
    )
    blocked.extend(
        fidelity._box(span["bbox"])
        for span in traces
        if span["type"] not in {0, 1}
        or span["opacity"] != 1
        or (span["colorspace"], tuple(span["color"])) not in {(1, (0.0,)), (3, (0.0, 0.0, 0.0))}
    )
    equations: list[_Equation] = []
    for index, group in enumerate(groups):
        text = "".join(glyph.text for glyph in group)
        selected = [anchor for anchor in anchors if anchor["line"] == group[0].line]
        if (
            len(text) > MAX_LINE_CHARACTERS
            or text.count("=") != 1
            or sum(anchor["kind"] == "number" for anchor in selected) < 2
            or "".join(anchor["text"] for anchor in selected) != "".join(text.split())
            or not all(glyph.trusted for glyph in group)
        ):
            continue
        start, end = len(text) - len(text.lstrip()), len(text.rstrip())
        group = group[start:end]
        text = text[start:end]
        box = fidelity._union([glyph.bbox for glyph in group])
        if (
            not _contained(box, tuple(reference["page_bbox"]))
            or any(fidelity._overlap(box, region) > 0 for region in blocked)
            or any(
                _vertical_overlap(box, other)
                for other_index, other in enumerate(regions)
                if other_index != index
            )
            or any(
                right.bbox[0] < left.bbox[2] - 0.5 or right.bbox[0] - left.bbox[2] > box[3] - box[1]
                for left, right in pairwise(group)
            )
        ):
            continue
        source_words = tuple(
            (
                match.group(),
                fidelity._union([glyph.bbox for glyph in group[match.start() : match.end()]]),
            )
            for match in re.finditer(r"\S+", text)
        )
        equations.append(
            _Equation(
                text,
                box,
                source_words,
                group[0].line,
                tuple(sorted({glyph.font for glyph in group})),
                (start, end),
            )
        )
    return sorted(equations, key=lambda equation: equation.bbox[1])


def _edit(equation: _Equation, lines: list[_OCRLine], text: str, word_count: int) -> _Edit | None:
    overlaps = [line for line in lines if _vertical_overlap(equation.bbox, line.bbox)]
    if overlaps:
        if len(overlaps) != 1 or not _contained(overlaps[0].bbox, equation.bbox, 3):
            return None
        line = overlaps[0]
        if text[line.start : line.end] == equation.text:
            return None
        return _Edit(line.start, line.end, line.first, line.last, equation.text, equation)
    newline = "\r\n" if "\r\n" in text else "\n"
    for line in lines:
        if line.bbox[1] >= equation.bbox[3]:
            return _Edit(
                line.line_start,
                line.line_start,
                line.first,
                line.first,
                equation.text + newline,
                equation,
            )
    prefix = "" if text.endswith(("\r", "\n")) else newline
    return _Edit(len(text), len(text), word_count, word_count, prefix + equation.text, equation)


def _derive(
    page: pymupdf.Page, text: str, words: list[Word], reference: dict[str, Any]
) -> NativeMathRecovery | None:
    lines = _mapped_lines(text, words, fidelity._box(reference["page_bbox"]))
    if not lines:
        return None
    edits = [
        edit
        for equation in _equations(page, reference)
        if (edit := _edit(equation, lines, text, len(words))) is not None
    ]
    if not edits or len(edits) > MAX_EQUATIONS:
        return None
    output: list[str] = []
    output_words: list[Word] = []
    evidence: list[dict[str, object]] = []
    cursor = word_cursor = output_length = 0
    for index, edit in enumerate(edits):
        if edit.start < cursor or edit.first < word_cursor:
            return None
        replacement = edit.replacement
        if index and edit.start == edit.end == cursor == len(text):
            replacement = ("\r\n" if "\r\n" in text else "\n") + edit.equation.text
        prefix = text[cursor : edit.start]
        output.extend((prefix, replacement))
        output_words.extend(words[word_cursor : edit.first])
        output_words.extend(edit.equation.words)
        output_length += len(prefix)
        evidence.append(
            {
                "kind": "insert" if edit.start == edit.end else "replace",
                "old_offsets": [edit.start, edit.end],
                "new_offsets": [output_length, output_length + len(replacement)],
                "old_word_offsets": [edit.first, edit.last],
                "region": list(edit.equation.bbox),
                "source_line": edit.equation.line,
                "source_line_offsets": list(edit.equation.offsets),
                "source_fonts": list(edit.equation.fonts),
                "source_text_sha256": _sha(edit.equation.text),
                "old_text_sha256": _sha(text[edit.start : edit.end]),
            }
        )
        output_length += len(replacement)
        cursor, word_cursor = edit.end, edit.last
    output.append(text[cursor:])
    output_words.extend(words[word_cursor:])
    raw_text = "".join(output)
    if len(raw_text) > fidelity.MAX_TEXT_CHARACTERS or len(output_words) > fidelity.MAX_WORDS:
        return None
    provenance: dict[str, object] = {
        "algorithm_version": ALGORITHM_VERSION,
        "source": "original_pdf_native_nonlegacy",
        "coordinate_space": "unrotated_pdf_points",
        "offset_space": "unicode_codepoints",
        "page_number": reference["page_number"],
        "source_reference_sha256": _sha(_json(reference)),
        "original_ocr_sha256": _sha(text),
        "original_words_sha256": _sha(_json(words)),
        "recovered_text_sha256": _sha(raw_text),
        "evidence_hash_serialization": {
            "version": "python-json-sorted-compact-ascii-v1",
            "input": "decoded_math_evidence",
            "fields": ["source_reference_sha256", "original_words_sha256"],
            "hash_algorithm": "sha256",
            "encoding": "utf-8",
            "ensure_ascii": True,
            "sort_keys": True,
            "allow_nan": False,
            "separators": [",", ":"],
        },
        "edits": evidence,
    }
    if len(_json(provenance)) > MAX_PROVENANCE_BYTES:
        return None
    return NativeMathRecovery(raw_text, tuple(output_words), provenance)


def recover_native_equations(
    page: pymupdf.Page, text: str, words: list[Word]
) -> NativeMathRecovery | None:
    if len(text) > fidelity.MAX_TEXT_CHARACTERS or len(words) > fidelity.MAX_WORDS:
        return None
    try:
        reference = cast(dict[str, Any], fidelity.extract_math_layout(page))
        if not fidelity._valid_reference(reference) or set(reference["risk_codes"]) - {
            "raster_grid_unverified"
        }:
            return None
        return _derive(page, text, words, reference)
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        return None
