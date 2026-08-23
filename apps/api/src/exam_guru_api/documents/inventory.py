import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

import pymupdf

_YEAR_PATTERN = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_NATIVE_TEXT_MINIMUM = 20


class PdfTextMode(StrEnum):
    NATIVE = "native"
    SCANNED = "scanned"
    MIXED = "mixed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PdfInventoryRecord:
    relative_path: str
    filename: str
    sha256: str
    size_bytes: int
    page_count: int
    native_text_pages: int
    image_only_pages: int
    pages_with_images: int
    text_mode: PdfTextMode
    language: str
    subject: str
    document_type: str
    year: int | None
    year_evidence: str | None
    extraction_feasibility: str
    representative_native_pages: tuple[int, ...]
    representative_ocr_pages: tuple[int, ...]
    encrypted: bool
    error: str | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["text_mode"] = self.text_mode.value
        return result


@dataclass(frozen=True, slots=True)
class PdfInventory:
    records: tuple[PdfInventoryRecord, ...]

    def to_dict(self) -> dict[str, object]:
        mode_counts = Counter(record.text_mode.value for record in self.records)
        language_counts = Counter(record.language for record in self.records)
        subject_counts = Counter(record.subject for record in self.records)
        return {
            "summary": {
                "total_files": len(self.records),
                "total_pages": sum(record.page_count for record in self.records),
                "malformed_files": mode_counts[PdfTextMode.ERROR.value],
                "text_modes": dict(sorted(mode_counts.items())),
                "languages": dict(sorted(language_counts.items())),
                "subjects": dict(sorted(subject_counts.items())),
            },
            "records": [record.to_dict() for record in self.records],
        }


def detect_language(text: str) -> str:
    sinhala_count = sum("\u0d80" <= character <= "\u0dff" for character in text)
    latin_count = sum(character.isascii() and character.isalpha() for character in text)
    if sinhala_count >= 3 and latin_count >= 3:
        return "mixed"
    if sinhala_count >= 3:
        return "sinhala"
    if latin_count >= 3:
        return "english"
    return "unknown"


def inventory_pdf(path: Path, *, root: Path) -> PdfInventoryRecord:
    checksum = _sha256(path)
    relative_path = path.relative_to(root).as_posix()
    size_bytes = path.stat().st_size
    try:
        document = pymupdf.open(path)
    except Exception as error:
        return _error_record(path, relative_path, checksum, size_bytes, type(error).__name__)

    try:
        if document.needs_pass:
            return _error_record(
                path,
                relative_path,
                checksum,
                size_bytes,
                "encrypted",
                encrypted=True,
            )
        if document.page_count == 0:
            return _error_record(path, relative_path, checksum, size_bytes, "empty_pdf")

        native_pages: list[int] = []
        ocr_pages: list[int] = []
        pages_with_images = 0
        language_parts: list[str] = []
        evidenced_years: set[int] = set()
        for index in range(document.page_count):
            page = document[index]
            text = str(page.get_text("text", sort=True)).strip()
            if len(text) >= _NATIVE_TEXT_MINIMUM:
                native_pages.append(index + 1)
                language_parts.append(text)
            else:
                ocr_pages.append(index + 1)
            if page.get_images(full=True):
                pages_with_images += 1
            if index < 3:
                evidenced_years.update(int(value) for value in _YEAR_PATTERN.findall(text))
    except Exception as error:
        return _error_record(path, relative_path, checksum, size_bytes, type(error).__name__)
    finally:
        document.close()

    text_mode = _text_mode(len(native_pages), len(ocr_pages))
    filename_years = {int(value) for value in _YEAR_PATTERN.findall(path.name)}
    if len(filename_years) == 1:
        year = next(iter(filename_years))
        year_evidence = "filename"
    elif len(evidenced_years) == 1:
        year = next(iter(evidenced_years))
        year_evidence = "document_text"
    else:
        year = None
        year_evidence = None

    return PdfInventoryRecord(
        relative_path=relative_path,
        filename=path.name,
        sha256=checksum,
        size_bytes=size_bytes,
        page_count=len(native_pages) + len(ocr_pages),
        native_text_pages=len(native_pages),
        image_only_pages=len(ocr_pages),
        pages_with_images=pages_with_images,
        text_mode=text_mode,
        language=detect_language("".join(language_parts)),
        subject=_subject(relative_path),
        document_type=_document_type(path.name),
        year=year,
        year_evidence=year_evidence,
        extraction_feasibility=_feasibility(text_mode),
        representative_native_pages=tuple(native_pages[:3]),
        representative_ocr_pages=tuple(ocr_pages[:3]),
        encrypted=False,
        error=None,
    )


def build_inventory(root: Path) -> PdfInventory:
    records = tuple(
        inventory_pdf(path, root=root)
        for path in sorted(root.rglob("*.pdf"), key=lambda item: item.as_posix().casefold())
    )
    return PdfInventory(records=records)


def write_inventory(inventory: PdfInventory, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(inventory.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args(argv)
    write_inventory(build_inventory(arguments.root), arguments.output)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_mode(native_pages: int, ocr_pages: int) -> PdfTextMode:
    if native_pages and ocr_pages:
        return PdfTextMode.MIXED
    if native_pages:
        return PdfTextMode.NATIVE
    return PdfTextMode.SCANNED


def _subject(relative_path: str) -> str:
    folded = relative_path.casefold()
    if "math" in folded or "ගණිත" in relative_path:
        return "maths"
    if "parisaraya" in folded or "පරිසර" in relative_path:
        return "parisaraya"
    if "english" in folded:
        return "english"
    if "sinhala" in folded or "සිංහල" in relative_path:
        return "sinhala"
    return "unknown"


def _document_type(filename: str) -> str:
    folded = filename.casefold()
    if ("teacher" in folded and "guide" in folded) or "ගුරු" in filename:
        return "teacher_guide"
    if "answer" in folded or "marking" in folded or "පිළිතුරු" in filename:
        return "marking_scheme"
    if "test paper" in folded or "past paper" in folded or "විභාග" in filename:
        return "past_paper"
    if "worksheet" in folded or "work sheet" in folded or "වැඩපොත" in filename:
        return "worksheet"
    if "textbook" in folded or "පෙළ පොත" in filename:
        return "textbook"
    if "activity" in folded or "ක්‍රියාකාරක" in filename:
        return "activity"
    if "story" in folded or "song" in folded or "craft" in folded:
        return "supplementary"
    return "other"


def _feasibility(text_mode: PdfTextMode) -> str:
    if text_mode is PdfTextMode.NATIVE:
        return "native_extraction_candidate"
    if text_mode is PdfTextMode.MIXED:
        return "native_plus_ocr_review_required"
    if text_mode is PdfTextMode.SCANNED:
        return "ocr_and_human_adjudication_required"
    return "manual_inspection_required"


def _error_record(
    path: Path,
    relative_path: str,
    checksum: str,
    size_bytes: int,
    error: str,
    *,
    encrypted: bool = False,
) -> PdfInventoryRecord:
    return PdfInventoryRecord(
        relative_path=relative_path,
        filename=path.name,
        sha256=checksum,
        size_bytes=size_bytes,
        page_count=0,
        native_text_pages=0,
        image_only_pages=0,
        pages_with_images=0,
        text_mode=PdfTextMode.ERROR,
        language="unknown",
        subject=_subject(relative_path),
        document_type=_document_type(path.name),
        year=None,
        year_evidence=None,
        extraction_feasibility="manual_inspection_required",
        representative_native_pages=(),
        representative_ocr_pages=(),
        encrypted=encrypted,
        error=error,
    )
