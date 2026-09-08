import base64
import hashlib
import json
import math
import re
import unicodedata
import zlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, cast

import pymupdf

ALGORITHM_VERSION = "source-math-layout-v1"
MAX_ANCHORS = 1024
MAX_CELLS = 512
MAX_IMAGES = 16
MAX_SEGMENTS = 1024
MAX_NATIVE_CHARACTERS = 100_000
MAX_TEXT_CHARACTERS = 100_000
MAX_WORDS = 8192
MAX_TOKEN_CHARACTERS = 64
MAX_METADATA_BYTES = 1_048_576
MAX_RASTER_EDGE = 768
MAX_RASTER_PIXELS = 4_000_000
MATH_EVIDENCE_COMPRESSION_THRESHOLD = 8 * 1024
MAX_ENCODED_MATH_REFERENCE_BYTES = 24 * 1024
MAX_ENCODED_MATH_WORD_BYTES = 16 * 1024
MAX_COMPRESSED_MATH_EVIDENCE_BYTES = 18 * 1024
MAX_MATH_EVIDENCE_DEPTH = 64
MAX_MATH_EVIDENCE_NODES = 100_000
_MATH_EVIDENCE_ENCODING = "zlib-json-base64-v1"
_TOLERANCE = 1.5
_TOKEN = re.compile(
    r"\d+(?:[.,]\d+)*|[\u00d7\u00f7=+\u2212*/<>\u2264\u2265\u2260()[\]{}^%]"
    r"|(?<![A-Za-z])[xX-](?![A-Za-z])"
)
_LEGACY = re.compile(r"^(?:fm|dl|thibus|niesin|kaputa|amalee|bamini|kalaham|nietml)", re.IGNORECASE)
_STANDARD = re.compile(
    r"^(?:arial|calibri|helvetica|times|courier|cambria|segoe|liberation|dejavu|noto|"
    r"charissil|droid|verdana|tahoma|georgia)",
    re.IGNORECASE,
)
Box = tuple[float, float, float, float]
Segment = tuple[bool, float, float, float]
Record = dict[str, Any]
MathEvidence = dict[str, Any] | list[Any]


def _evidence_json(value: object) -> bytes:
    nodes = 0
    ceiling = min(MAX_METADATA_BYTES, 1_048_576)

    def validate(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_MATH_EVIDENCE_NODES or depth > MAX_MATH_EVIDENCE_DEPTH:
            raise ValueError("invalid math evidence")
        if type(item) is dict:
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("invalid math evidence")
                validate(key, depth + 1)
                validate(child, depth + 1)
        elif type(item) is list:
            for child in item:
                validate(child, depth + 1)
        elif type(item) is str:
            if len(item) > ceiling:
                raise ValueError("invalid math evidence")
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("invalid math evidence")
        elif type(item) not in (int, bool, type(None)):
            raise ValueError("invalid math evidence")

    try:
        if type(value) not in (dict, list):
            raise ValueError("invalid math evidence")
        validate(value, 0)
        encoded = bytearray()
        for chunk in json.JSONEncoder(ensure_ascii=True, allow_nan=False).iterencode(value):
            if len(encoded) + len(chunk) > ceiling:
                raise ValueError("invalid math evidence")
            encoded.extend(chunk.encode("ascii"))
        return bytes(encoded)
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ValueError("invalid math evidence") from None


def _unique_evidence_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid math evidence")
        value[key] = item
    return value


def encode_math_evidence(value: MathEvidence) -> MathEvidence:
    raw = _evidence_json(value)
    if isinstance(value, dict) and "_math_evidence" in value:
        raise ValueError("invalid math evidence")
    if len(raw) <= MATH_EVIDENCE_COMPRESSION_THRESHOLD:
        return value
    compressed = zlib.compress(raw, level=9)
    if len(compressed) > MAX_COMPRESSED_MATH_EVIDENCE_BYTES:
        raise ValueError("invalid math evidence")
    kind = "dict" if isinstance(value, dict) else "list"
    result: dict[str, Any] = {
        "_math_evidence": _MATH_EVIDENCE_ENCODING,
        "kind": kind,
        "uncompressed_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data": base64.b64encode(compressed).decode("ascii"),
    }
    limit = MAX_ENCODED_MATH_REFERENCE_BYTES if kind == "dict" else MAX_ENCODED_MATH_WORD_BYTES
    if len(_evidence_json(result)) > limit:
        raise ValueError("invalid math evidence")
    return result


def decode_math_evidence(value: object) -> MathEvidence:
    try:
        if not isinstance(value, dict) or "_math_evidence" not in value:
            _evidence_json(value)
            return cast(MathEvidence, value)
        if len(value) != 5 or set(value) != {
            "_math_evidence",
            "kind",
            "uncompressed_bytes",
            "sha256",
            "data",
        }:
            raise ValueError("invalid math evidence")
        kind = value["kind"]
        size = value["uncompressed_bytes"]
        digest = value["sha256"]
        data = value["data"]
        if (
            value["_math_evidence"] != _MATH_EVIDENCE_ENCODING
            or kind not in ("dict", "list")
            or type(size) is not int
            or not 2 <= size <= min(MAX_METADATA_BYTES, 1_048_576)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(data, str)
            or len(data) > 4 * ((MAX_COMPRESSED_MATH_EVIDENCE_BYTES + 2) // 3)
        ):
            raise ValueError("invalid math evidence")
        limit = MAX_ENCODED_MATH_REFERENCE_BYTES if kind == "dict" else MAX_ENCODED_MATH_WORD_BYTES
        if len(_evidence_json(value)) > limit:
            raise ValueError("invalid math evidence")
        compressed = base64.b64decode(data.encode("ascii"), validate=True)
        if len(compressed) > MAX_COMPRESSED_MATH_EVIDENCE_BYTES:
            raise ValueError("invalid math evidence")
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, size + 1)
        if (
            len(raw) != size
            or not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ValueError("invalid math evidence")
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_evidence_object)
        if type(decoded) is not (dict if kind == "dict" else list):
            raise ValueError("invalid math evidence")
        if isinstance(decoded, dict) and "_math_evidence" in decoded:
            raise ValueError("invalid math evidence")
        _evidence_json(decoded)
        return cast(MathEvidence, decoded)
    except (TypeError, ValueError, RecursionError, OverflowError, zlib.error):
        raise ValueError("invalid math evidence") from None


@dataclass(frozen=True)
class _Glyph:
    text: str
    bbox: Box
    font: str
    trusted: bool
    line: int


def _box(value: Any) -> Box:
    if not isinstance(value, (list, tuple, pymupdf.Rect)) or len(value) != 4:
        raise ValueError("invalid rectangle")
    values = tuple(float(value[index]) for index in range(4))
    if not all(math.isfinite(item) for item in values):
        raise ValueError("nonfinite rectangle")
    if values[2] <= values[0] or values[3] <= values[1]:
        raise ValueError("empty rectangle")
    return cast(Box, values)


def _union(boxes: list[Box]) -> Box:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _overlap(left: Box, right: Box) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _inside(inner: Box, outer: Box) -> bool:
    return _overlap(inner, outer) >= 0.9 * (inner[2] - inner[0]) * (inner[3] - inner[1])


def _matches(text: str) -> list[re.Match[str]]:
    matches = list(_TOKEN.finditer(text))
    if any(match.group()[0].isdigit() or match.group() not in "xX-()[]{}" for match in matches):
        return matches
    return matches if text.strip() in {"x", "X"} else []


def _tokens(text: str) -> list[str]:
    return [match.group() for match in _matches(text)]


def _unsupported_symbols(text: str) -> bool:
    return any(
        (unicodedata.category(char) in {"Sm", "No", "Co"} or char == "\ufffd")
        and not _TOKEN.fullmatch(char)
        for char in text
    )


def _native(page: pymupdf.Page, risks: set[str]) -> tuple[list[Record], list[_Glyph], int]:
    anchors: list[Record] = []
    glyphs: list[_Glyph] = []
    excluded = 0
    line_number = 0
    raw = page.get_text(
        "rawdict", sort=True, flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    )
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            line_number += 1
            line_glyphs: list[_Glyph] = []
            for span in line["spans"]:
                font = str(span["font"])
                base_font = font.split("+")[-1]
                legacy = bool(_LEGACY.match(base_font))
                trusted = bool(_STANDARD.match(base_font)) and not legacy and len(font) <= 128
                excluded += int(legacy)
                if len(glyphs) + len(line_glyphs) + len(span["chars"]) > MAX_NATIVE_CHARACTERS:
                    risks.add("native_character_limit")
                    return anchors, glyphs, excluded
                text = "".join(char["c"] for char in span["chars"])
                if not trusted and not legacy and _matches(text):
                    risks.add("untrusted_native_math_font")
                for char in span["chars"]:
                    if len(char["c"]) != 1:
                        risks.add("unsupported_native_character")
                        return anchors, glyphs, excluded
                    try:
                        rectangle = _box(char["bbox"])
                    except (TypeError, ValueError):
                        risks.add("invalid_native_geometry")
                        return anchors, glyphs, excluded
                    line_glyphs.append(_Glyph(char["c"], rectangle, font, trusted, line_number))
            glyphs.extend(line_glyphs)
            trusted_text = "".join(glyph.text if glyph.trusted else " " for glyph in line_glyphs)
            if _unsupported_symbols(trusted_text):
                risks.add("unsupported_native_math_symbol")
            native_words = iter(
                (
                    word.start(),
                    word.end(),
                    _union([glyph.bbox for glyph in line_glyphs[word.start() : word.end()]]),
                )
                for word in re.finditer(r"\S+", trusted_text)
            )
            native_word = next(native_words, None)
            for match in _matches(trusted_text):
                while native_word is not None and match.start() >= native_word[1]:
                    native_word = next(native_words, None)
                if len(anchors) >= MAX_ANCHORS:
                    risks.add("native_anchor_limit")
                    return anchors, glyphs, excluded
                if len(match.group()) > MAX_TOKEN_CHARACTERS:
                    risks.add("native_token_limit")
                    continue
                selected = line_glyphs[match.start() : match.end()]
                anchor_box = _union([glyph.bbox for glyph in selected])
                anchors.append(
                    {
                        "id": len(anchors),
                        "text": match.group(),
                        "kind": "number" if match.group()[0].isdigit() else "operator",
                        "bbox": list(anchor_box),
                        "word_bbox": list(
                            native_word[2] if native_word is not None else anchor_box
                        ),
                        "fonts": sorted({glyph.font for glyph in selected}),
                        "line": line_number,
                        "source": "native_nonlegacy",
                    }
                )
                direction = line.get("dir", (1.0, 0.0))
                if abs(direction[0] - 1) > 0.01 or abs(direction[1]) > 0.01:
                    risks.add("unsupported_native_math_layout")
    if anchors and any(
        not glyph.trusted
        and not glyph.text.isspace()
        and not _LEGACY.match(glyph.font.split("+")[-1])
        for glyph in glyphs
    ):
        risks.add("untrusted_native_math_font")
    return anchors, glyphs, excluded


def _line_segment(start: Any, end: Any) -> Segment | None:
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        raise ValueError("invalid vector coordinates")
    if abs(y1 - y0) <= 0.5 and abs(x1 - x0) >= 5:
        return True, (y0 + y1) / 2, min(x0, x1), max(x0, x1)
    if abs(x1 - x0) <= 0.5 and abs(y1 - y0) >= 5:
        return False, (x0 + x1) / 2, min(y0, y1), max(y0, y1)
    return None


def _vector_segments(page: pymupdf.Page, risks: set[str]) -> tuple[list[Segment], list[Box]]:
    segments: list[Segment] = []
    unsupported: list[Box] = []
    items_seen = 0
    for path in page.get_drawings():
        non_axis = False
        for item in path["items"]:
            items_seen += 1
            if items_seen > MAX_SEGMENTS or len(segments) > MAX_SEGMENTS - 4:
                risks.add("vector_segment_limit")
                return segments, unsupported
            if item[0] == "l":
                segment = _line_segment(item[1], item[2])
                if segment is not None:
                    segments.append(segment)
                else:
                    non_axis = True
            elif item[0] == "re":
                rectangle = item[1]
                if path["type"] == "f":
                    if rectangle.width <= 2:
                        segments.append(
                            (False, (rectangle.x0 + rectangle.x1) / 2, rectangle.y0, rectangle.y1)
                        )
                    elif rectangle.height <= 2:
                        segments.append(
                            (True, (rectangle.y0 + rectangle.y1) / 2, rectangle.x0, rectangle.x1)
                        )
                else:
                    segments.extend(
                        [
                            (True, rectangle.y0, rectangle.x0, rectangle.x1),
                            (True, rectangle.y1, rectangle.x0, rectangle.x1),
                            (False, rectangle.x0, rectangle.y0, rectangle.y1),
                            (False, rectangle.x1, rectangle.y0, rectangle.y1),
                        ]
                    )
            else:
                non_axis = True
        if non_axis:
            if len(unsupported) >= 32:
                risks.add("unsupported_vector_limit")
            else:
                try:
                    unsupported.append(_box(path["rect"]))
                except ValueError:
                    risks.add("invalid_vector_geometry")
    return segments, unsupported


def _merged(segments: list[Segment]) -> list[Segment]:
    merged: list[Segment] = []
    for horizontal in (True, False):
        groups: list[list[Segment]] = []
        for segment in sorted(item for item in segments if item[0] == horizontal):
            if groups and abs(segment[1] - groups[-1][0][1]) <= _TOLERANCE:
                groups[-1].append(segment)
            else:
                groups.append([segment])
        for group in groups:
            coordinate = sum(item[1] for item in group) / len(group)
            intervals = sorted((item[2], item[3]) for item in group)
            start, end = intervals[0]
            for next_start, next_end in intervals[1:]:
                if next_start <= end + _TOLERANCE:
                    end = max(end, next_end)
                else:
                    merged.append((horizontal, coordinate, start, end))
                    start, end = next_start, next_end
            merged.append((horizontal, coordinate, start, end))
    return merged


def _grid_shapes(segments: list[Segment]) -> tuple[list[Record], bool]:
    lines = _merged(segments)
    neighbours: list[set[int]] = [set() for _ in lines]
    for index, left in enumerate(lines):
        for other in range(index + 1, len(lines)):
            right = lines[other]
            if left[0] == right[0]:
                continue
            horizontal, vertical = (left, right) if left[0] else (right, left)
            if (
                horizontal[2] - _TOLERANCE <= vertical[1] <= horizontal[3] + _TOLERANCE
                and vertical[2] - _TOLERANCE <= horizontal[1] <= vertical[3] + _TOLERANCE
            ):
                neighbours[index].add(other)
                neighbours[other].add(index)
    seen: set[int] = set()
    shapes: list[Record] = []
    unsupported = False
    for root in range(len(lines)):
        if root in seen:
            continue
        pending = [root]
        component: list[Segment] = []
        seen.add(root)
        while pending:
            current = pending.pop()
            component.append(lines[current])
            for other in neighbours[current] - seen:
                seen.add(other)
                pending.append(other)
        xs = sorted({line[1] for line in component if not line[0]})
        ys = sorted({line[1] for line in component if line[0]})
        if len(xs) < 2 or len(ys) < 2 or max(len(xs), len(ys)) < 3:
            continue
        complete = all(
            any(
                item[0] == horizontal
                and abs(item[1] - coordinate) <= _TOLERANCE
                and item[2] <= low + _TOLERANCE
                and item[3] >= high - _TOLERANCE
                for item in component
            )
            for horizontal, coordinates, low, high in (
                (True, ys, xs[0], xs[-1]),
                (False, xs, ys[0], ys[-1]),
            )
            for coordinate in coordinates
        )
        if not complete:
            unsupported = True
            continue
        shapes.append({"xs": xs, "ys": ys, "bbox": [xs[0], ys[0], xs[-1], ys[-1]]})
    return sorted(shapes, key=lambda shape: (shape["ys"][0], shape["xs"][0])), unsupported


def _raster_grids(pixmap: pymupdf.Pixmap) -> list[Record]:
    width, height = pixmap.width, pixmap.height
    pixels = pixmap.samples.translate(bytes(1 if value < 175 else 0 for value in range(256)))
    horizontal_run = re.compile(b"\x01{%d,}" % max(12, width // 12))
    vertical_run = re.compile(b"\x01{%d,}" % max(12, height // 12))
    segments: list[Segment] = []
    for y in range(height):
        row = pixels[y * pixmap.stride : y * pixmap.stride + width]
        segments.extend(
            (True, float(y), float(match.start()), float(match.end() - 1))
            for match in horizontal_run.finditer(row)
        )
        if len(segments) > MAX_SEGMENTS:
            raise ValueError("raster line budget")
    for x in range(width):
        column = pixels[x : height * pixmap.stride : pixmap.stride]
        segments.extend(
            (False, float(x), float(match.start()), float(match.end() - 1))
            for match in vertical_run.finditer(column)
        )
        if len(segments) > MAX_SEGMENTS:
            raise ValueError("raster line budget")
    shapes, _ = _grid_shapes(segments)
    return [shape for shape in shapes if len(shape["xs"]) >= 3 and len(shape["ys"]) >= 3]


def _images(page: pymupdf.Page, risks: set[str]) -> list[Record]:
    images: list[Record] = []
    pixels_used = 0
    for image in page.get_image_info():
        if len(images) >= MAX_IMAGES:
            risks.add("raster_image_limit")
            break
        rectangle = _box(image["bbox"])
        record: Record = {
            "bbox": list(rectangle),
            "width": int(image["width"]),
            "height": int(image["height"]),
            "grid_detected": False,
            "content_verified": False,
            "analysis_complete": False,
            "grids": [],
        }
        images.append(record)
        clip = pymupdf.Rect(rectangle) & page.rect
        if clip.is_empty or not math.isfinite(clip.width + clip.height):
            risks.add("invalid_raster_geometry")
            continue
        scale = min(2.0, MAX_RASTER_EDGE / max(clip.width, clip.height))
        budget = math.ceil(clip.width * scale + 2) * math.ceil(clip.height * scale + 2)
        if pixels_used + budget > MAX_RASTER_PIXELS:
            risks.add("raster_pixel_limit")
            continue
        pixels_used += budget
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale),
            clip=clip,
            colorspace=pymupdf.csGRAY,
            alpha=False,
            annots=False,
        )
        try:
            grids = _raster_grids(pixmap)
        except ValueError:
            risks.add("raster_analysis_limit")
            continue
        record["analysis_complete"] = True
        record["grid_detected"] = bool(grids)
        for grid in grids:
            left, top, right, bottom = grid["bbox"]
            record["grids"].append(
                {
                    "rows": len(grid["ys"]) - 1,
                    "columns": len(grid["xs"]) - 1,
                    "bbox": [
                        (left + pixmap.x) / scale,
                        (top + pixmap.y) / scale,
                        (right + pixmap.x) / scale,
                        (bottom + pixmap.y) / scale,
                    ],
                    "source": "raster_axis_lines",
                    "symbols_verified": False,
                }
            )
        if grids:
            risks.add("raster_grid_unverified")
    return images


def _tables(
    shapes: list[Record],
    glyphs: list[_Glyph],
    anchors: list[Record],
    images: list[Record],
    unsupported: list[Box],
    risks: set[str],
) -> list[Record]:
    tables: list[Record] = []
    cells_used = 0
    for shape in shapes:
        xs, ys = shape["xs"], shape["ys"]
        rows, columns = len(ys) - 1, len(xs) - 1
        if cells_used + rows * columns > MAX_CELLS:
            risks.add("table_cell_limit")
            continue
        cells_used += rows * columns
        cells: list[Record] = []
        for row in range(rows):
            for column in range(columns):
                box = (xs[column], ys[row], xs[column + 1], ys[row + 1])
                selected = [
                    glyph
                    for glyph in glyphs
                    if not glyph.text.isspace() and _overlap(glyph.bbox, box) > 0
                ]
                certain = all(glyph.trusted and _inside(glyph.bbox, box) for glyph in selected)
                certain = certain and not any(
                    _overlap(tuple(image["bbox"]), box) > 0 for image in images
                )
                certain = certain and not any(_overlap(region, box) > 0 for region in unsupported)
                text = "".join(glyph.text for glyph in selected)
                if len(text) > MAX_TOKEN_CHARACTERS:
                    risks.add("table_cell_text_limit")
                    certain = False
                    text = ""
                ids = [anchor["id"] for anchor in anchors if _inside(tuple(anchor["bbox"]), box)]
                cells.append(
                    {
                        "row": row,
                        "column": column,
                        "bbox": list(box),
                        "anchor_ids": ids,
                        "text": text if certain else None,
                        "empty": not bool(selected) if certain else None,
                        "content_status": (
                            ("native" if selected else "empty") if certain else "unverified"
                        ),
                    }
                )
                if not certain:
                    risks.add("table_cell_content_unverified")
        tables.append(
            {
                "id": len(tables),
                "source": "vector_axis_lines",
                "bbox": shape["bbox"],
                "rows": rows,
                "columns": columns,
                "cells": cells,
            }
        )
    return tables


def extract_math_layout(page: pymupdf.Page) -> dict[str, object]:
    risks: set[str] = set()
    anchors: list[Record] = []
    glyphs: list[_Glyph] = []
    images: list[Record] = []
    tables: list[Record] = []
    excluded = 0
    unsupported: list[Box] = []
    try:
        anchors, glyphs, excluded = _native(page, risks)
    except Exception:
        risks.add("native_math_evidence_unavailable")
    try:
        segments, unsupported = _vector_segments(page, risks)
        shapes, broken = _grid_shapes(segments)
        if broken:
            risks.add("unsupported_vector_grid")
    except Exception:
        shapes = []
        risks.add("vector_layout_evidence_unavailable")
    try:
        images = _images(page, risks)
    except Exception:
        risks.add("raster_layout_evidence_unavailable")
    try:
        tables = _tables(shapes, glyphs, anchors, images, unsupported, risks)
    except Exception:
        risks.add("table_layout_evidence_unavailable")
    if any(
        _overlap(tuple(anchor["bbox"]), region) > 0 for anchor in anchors for region in unsupported
    ):
        risks.add("unsupported_vector_math_layout")
    if page.rotation and (anchors or images or tables):
        risks.add("unsupported_page_rotation")
    result: Record = {
        "algorithm_version": ALGORITHM_VERSION,
        "coordinate_space": "unrotated_pdf_points",
        "page_number": page.number + 1,
        "page_bbox": list(page.rect),
        "anchors": anchors,
        "tables": tables,
        "images": images,
        "excluded_legacy_spans": excluded,
        "native_character_count": len(glyphs),
        "unsupported_layouts": [
            {"kind": "non_axis_vector", "bbox": list(box)} for box in unsupported
        ],
        "complete": not risks,
        "risk_codes": sorted(risks),
    }
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_METADATA_BYTES:
        result.update(anchors=[], tables=[], images=[], unsupported_layouts=[], complete=False)
        result["risk_codes"] = sorted(risks | {"math_metadata_limit"})
    return result


def _valid_reference(reference: Record) -> bool:
    try:
        if (
            not isinstance(reference, dict)
            or reference.get("algorithm_version") != ALGORITHM_VERSION
            or reference.get("coordinate_space") != "unrotated_pdf_points"
            or not isinstance(reference.get("complete"), bool)
            or not isinstance(reference.get("risk_codes"), list)
            or any(not isinstance(code, str) for code in reference["risk_codes"])
            or not isinstance(reference.get("native_character_count"), int)
            or not 0 <= reference["native_character_count"] <= MAX_NATIVE_CHARACTERS
        ):
            return False
        _box(reference["page_bbox"])
        for key, maximum in (
            ("anchors", MAX_ANCHORS),
            ("tables", MAX_CELLS),
            ("images", MAX_IMAGES),
        ):
            if not isinstance(reference.get(key), list) or len(reference[key]) > maximum:
                return False
        for index, anchor in enumerate(reference["anchors"]):
            box = _box(anchor["bbox"])
            if "word_bbox" in anchor and not _inside(box, _box(anchor["word_bbox"])):
                return False
            if (
                anchor["id"] != index
                or not isinstance(anchor["line"], int)
                or anchor["source"] != "native_nonlegacy"
                or not isinstance(anchor["text"], str)
                or not 0 < len(anchor["text"]) <= MAX_TOKEN_CHARACTERS
                or not _TOKEN.fullmatch(anchor["text"])
            ):
                return False
        cells_used = 0
        for index, table in enumerate(reference["tables"]):
            _box(table["bbox"])
            if (
                table["id"] != index
                or table["source"] != "vector_axis_lines"
                or not isinstance(table["rows"], int)
                or table["rows"] < 1
                or not isinstance(table["columns"], int)
                or table["columns"] < 1
                or not isinstance(table["cells"], list)
                or len(table["cells"]) != table["rows"] * table["columns"]
            ):
                return False
            cells_used += len(table["cells"])
            if cells_used > MAX_CELLS:
                return False
            for cell_index, cell in enumerate(table["cells"]):
                box = _box(cell["bbox"])
                if (
                    (cell["row"], cell["column"]) != divmod(cell_index, table["columns"])
                    or not _inside(box, tuple(table["bbox"]))
                    or cell["content_status"] not in {"native", "empty", "unverified"}
                    or not isinstance(cell["anchor_ids"], list)
                    or (
                        cell["text"] is not None
                        and (
                            not isinstance(cell["text"], str)
                            or len(cell["text"]) > MAX_TOKEN_CHARACTERS
                        )
                    )
                    or (cell["empty"] is not None and not isinstance(cell["empty"], bool))
                ):
                    return False
                for anchor_id in cell["anchor_ids"]:
                    if not isinstance(anchor_id, int) or not 0 <= anchor_id < len(
                        reference["anchors"]
                    ):
                        return False
        for image in reference["images"]:
            _box(image["bbox"])
            if (
                not isinstance(image["grid_detected"], bool)
                or not isinstance(image["analysis_complete"], bool)
                or image["content_verified"] is not False
                or not isinstance(image["grids"], list)
            ):
                return False
        return len(json.dumps(reference, allow_nan=False).encode()) <= MAX_METADATA_BYTES
    except (KeyError, TypeError, ValueError, RecursionError):
        return False


def _word_evidence(
    words: list[tuple[str, Box]],
    page_bbox: Box,
    risks: set[str],
) -> list[tuple[str, Box]]:
    if len(words) > MAX_WORDS:
        risks.add("ocr_word_limit")
        return []
    checked: list[tuple[str, Box]] = []
    characters = 0
    for word in words:
        try:
            text, rectangle = word
            box = _box(rectangle)
            if not isinstance(text, str) or not text.strip() or not _inside(box, page_bbox):
                raise ValueError("invalid OCR word")
            characters += len(text)
            if characters > MAX_TEXT_CHARACTERS:
                risks.add("ocr_word_text_limit")
                return []
            checked.append((text, box))
        except (TypeError, ValueError):
            risks.add("invalid_word_boxes")
            return []
    return checked


def _compare_tables(
    tables: list[Record], words: list[tuple[str, Box]], risks: set[str]
) -> list[Record]:
    comparisons: list[Record] = []
    for table in tables:
        assigned: list[list[str]] = [[] for _ in table["cells"]]
        order: list[int] = []
        for text, box in words:
            if not _overlap(box, tuple(table["bbox"])):
                continue
            candidates = [
                index
                for index, cell in enumerate(table["cells"])
                if _inside(box, tuple(cell["bbox"]))
            ]
            if len(candidates) != 1:
                risks.add("ambiguous_table_word_box")
                continue
            assigned[candidates[0]].append(text)
            order.append(candidates[0])
        if order != sorted(order):
            risks.add("table_reading_order_changed")
        cells: list[Record] = []
        for index, cell in enumerate(table["cells"]):
            actual = "".join("".join(assigned[index]).split())
            expected = cell["text"]
            preserved = expected is not None and actual == "".join(expected.split())
            cells.append(
                {
                    "row": cell["row"],
                    "column": cell["column"],
                    "bbox": cell["bbox"],
                    "expected": expected,
                    "actual": actual,
                    "empty": cell["empty"],
                    "preserved": preserved,
                }
            )
            if not preserved:
                risks.add("table_cell_mismatch")
        comparisons.append(
            {"id": table["id"], "rows": table["rows"], "columns": table["columns"], "cells": cells}
        )
    return comparisons


def _compare_anchor_positions(
    anchors: list[Record],
    words: list[tuple[str, Box]],
    table_ids: set[int],
    risks: set[str],
) -> None:
    if not anchors:
        return
    located = [
        (match.group(), index)
        for index, (text, _) in enumerate(words)
        for match in _TOKEN.finditer(text)
    ]
    if [token for token, _ in located] != [anchor["text"] for anchor in anchors]:
        risks.add("math_anchor_position_mismatch")
        return
    groups: dict[int, list[Record]] = {}
    for anchor, (_, word_index) in zip(anchors, located, strict=True):
        if anchor["id"] not in table_ids:
            groups.setdefault(word_index, []).append(anchor)
    for word_index, selected in groups.items():
        word_box = words[word_index][1]
        for anchor in selected:
            box = _box(anchor["bbox"])
            height = box[3] - box[1]
            tolerance = max(3.0, height / 4)
            expanded = (
                word_box[0] - tolerance,
                word_box[1] - tolerance,
                word_box[2] + tolerance,
                word_box[3] + tolerance,
            )
            if (
                not _inside(box, expanded)
                or word_box[3] - word_box[1] > 1.8 * height
                or abs((box[1] + box[3] - word_box[1] - word_box[3]) / 2) > 0.45 * height
            ):
                risks.add("math_anchor_position_mismatch")
        if all("word_bbox" in anchor for anchor in selected):
            box = _union([_box(anchor["word_bbox"]) for anchor in selected])
            expanded = (box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4)
            if not _inside(word_box, expanded):
                risks.add("math_anchor_position_mismatch")


def assess_math_fidelity(
    reference: dict[str, object],
    text: str,
    words: list[tuple[str, tuple[float, float, float, float]]] | None = None,
) -> dict[str, object]:
    result: Record = {
        "algorithm_version": ALGORITHM_VERSION,
        "can_confirm": False,
        "risk_codes": [],
        "preserved": [],
        "lost": [],
        "changed": [],
        "added": [],
        "tables": [],
        "scope": "math_and_layout_only",
    }
    try:
        decoded = decode_math_evidence(reference)
    except ValueError:
        result["risk_codes"] = ["invalid_math_reference"]
        return result
    if not isinstance(decoded, dict) or not _valid_reference(decoded):
        result["risk_codes"] = ["invalid_math_reference"]
        return result
    ref = decoded
    risks = set(ref["risk_codes"])
    if not ref["complete"]:
        risks.add("incomplete_math_reference")
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARACTERS:
        result["risk_codes"] = sorted(risks | {"candidate_math_text_limit"})
        return result
    actual = _tokens(text)
    if _unsupported_symbols(text):
        risks.add("unsupported_candidate_math_symbol")
    if len(actual) > MAX_ANCHORS or any(len(token) > MAX_TOKEN_CHARACTERS for token in actual):
        result["risk_codes"] = sorted(risks | {"candidate_math_token_limit"})
        return result
    expected = [anchor["text"] for anchor in ref["anchors"]]
    for operation, left, right, start, end in SequenceMatcher(
        a=expected, b=actual, autojunk=False
    ).get_opcodes():
        if operation == "equal":
            result["preserved"].extend(ref["anchors"][left:right])
        elif operation == "delete":
            result["lost"].extend(ref["anchors"][left:right])
            risks.add("math_tokens_lost")
        elif operation == "replace":
            result["changed"].append(
                {"expected": ref["anchors"][left:right], "actual": actual[start:end]}
            )
            risks.add("math_tokens_changed")
        else:
            result["added"].extend(actual[start:end])
            risks.add("math_tokens_added")
    if ref["images"] and (expected or actual or ref["tables"]):
        risks.add("raster_math_content_unverified")
    if ref["images"] and not ref["native_character_count"]:
        risks.add("raster_content_unverified")
    if ref["tables"] and words is None:
        risks.add("table_word_boxes_required")
    if words is not None:
        checked = _word_evidence(words, _box(ref["page_bbox"]), risks)
        if _tokens(" ".join(word[0] for word in checked)) != actual:
            risks.add("word_text_mismatch")
        result["tables"] = _compare_tables(ref["tables"], checked, risks)
        table_ids = {
            anchor_id
            for table in ref["tables"]
            for cell in table["cells"]
            for anchor_id in cell["anchor_ids"]
        }
        _compare_anchor_positions(ref["anchors"], checked, table_ids, risks)
    elif not ref["tables"]:
        lines: dict[int, list[str]] = {}
        for anchor in ref["anchors"]:
            lines.setdefault(anchor["line"], []).append(anchor["text"])
        if len(lines) > 1:
            observed_lines = [tokens for line in text.splitlines() if (tokens := _tokens(line))]
            if observed_lines != list(lines.values()):
                risks.add("math_line_order_unverified")
    result["risk_codes"] = sorted(risks)
    result["can_confirm"] = not risks
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_METADATA_BYTES:
        result.update(preserved=[], lost=[], changed=[], added=[], tables=[], can_confirm=False)
        result["risk_codes"] = sorted(risks | {"math_assessment_metadata_limit"})
    return result
