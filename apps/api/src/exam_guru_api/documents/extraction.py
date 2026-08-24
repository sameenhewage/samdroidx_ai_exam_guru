import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pymupdf

from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.ocr import (
    MAX_OCR_ENGINE_CHARACTERS,
    MAX_OCR_ENGINE_VERSION_CHARACTERS,
    OCRConfigValue,
    snapshot_ocr_config,
)

_IMAGE_DOMINANT_COVERAGE = 0.8
_SPARSE_OVERLAY_CHARACTER_LIMIT = 200
_SAFE_TEXT_CONTROLS = frozenset("\t\n\r")
_BIDI_CONTROL_CODEPOINTS = frozenset(
    {0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}
)

_ALLOWED_EXTRACTION_TRANSITIONS = frozenset(
    {
        (ExtractionStatus.UPLOADED, ExtractionStatus.EXTRACTION_PENDING),
        (ExtractionStatus.EXTRACTION_PENDING, ExtractionStatus.EXTRACTED),
        (ExtractionStatus.EXTRACTION_PENDING, ExtractionStatus.FAILED),
        (ExtractionStatus.FAILED, ExtractionStatus.EXTRACTION_PENDING),
        (ExtractionStatus.EXTRACTED, ExtractionStatus.IN_REVIEW),
        (ExtractionStatus.IN_REVIEW, ExtractionStatus.TRUSTED),
    }
)


class InvalidExtractionTransitionError(ValueError):
    def __init__(self, current: ExtractionStatus, target: ExtractionStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot transition extraction from {current.value} to {target.value}")


def transition_extraction_status(
    current: ExtractionStatus,
    target: ExtractionStatus,
) -> ExtractionStatus:
    if current is not target and (current, target) not in _ALLOWED_EXTRACTION_TRANSITIONS:
        raise InvalidExtractionTransitionError(current, target)
    return target


def _contains_unsafe_text_character(text: str) -> bool:
    return any(
        (ord(character) < 32 and character not in _SAFE_TEXT_CONTROLS)
        or 127 <= ord(character) <= 159
        or ord(character) in _BIDI_CONTROL_CODEPOINTS
        for character in text
    )


def _bounded_identity(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and bool(value)
        and len(value) <= maximum
        and not _contains_unsafe_text_character(value)
    )


def _valid_confidence(value: object) -> bool:
    return value is None or (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


class ExtractionMode(StrEnum):
    NATIVE = "native"
    OCR = "ocr"
    HYBRID = "hybrid"


class ExtractionViolation(StrEnum):
    MALFORMED_PDF = "malformed_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    UNSAFE_TEXT = "unsafe_text"


class ExtractionError(ValueError):
    def __init__(self, violation: ExtractionViolation) -> None:
        self.violation = violation
        super().__init__(violation.value)


@dataclass(frozen=True, slots=True)
class ExtractedBlock:
    page_number: int
    reading_order: int
    bbox: tuple[float, float, float, float] | None
    text: str
    extractor: str | None = None
    extractor_version: str | None = None
    extraction_config: Mapping[str, OCRConfigValue] | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.extraction_config is not None:
            object.__setattr__(
                self,
                "extraction_config",
                snapshot_ocr_config(self.extraction_config),
            )
        if not _valid_confidence(self.confidence):
            raise ValueError("block confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_number: int
    text: str
    blocks: tuple[ExtractedBlock, ...]
    largest_image_coverage: float = 0.0
    extractor: str | None = None
    extractor_version: str | None = None
    extraction_config: Mapping[str, OCRConfigValue] | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.extraction_config is not None:
            object.__setattr__(
                self,
                "extraction_config",
                snapshot_ocr_config(self.extraction_config),
            )
        if not _valid_confidence(self.confidence):
            raise ValueError("page confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class NativeExtractionResult:
    engine: str
    engine_version: str
    pages: tuple[ExtractedPage, ...]
    page_count: int
    character_count: int
    native_text_page_ratio: float
    needs_ocr: bool
    image_dominant_page_ratio: float = 0.0
    config: Mapping[str, OCRConfigValue] = field(default_factory=dict)
    mode: ExtractionMode = ExtractionMode.NATIVE
    ocr_page_count: int = 0
    ocr_engine: str | None = None
    ocr_engine_version: str | None = None
    ocr_page_numbers: tuple[int, ...] = ()
    extraction_config: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not _bounded_identity(self.engine, maximum=MAX_OCR_ENGINE_CHARACTERS):
            raise ValueError("extraction engine must be bounded non-blank text")
        if not _bounded_identity(
            self.engine_version,
            maximum=MAX_OCR_ENGINE_VERSION_CHARACTERS,
        ):
            raise ValueError("extraction engine version must be bounded non-blank text")
        object.__setattr__(self, "config", snapshot_ocr_config(self.config))
        if self.ocr_page_count != len(self.ocr_page_numbers):
            raise ValueError("OCR page count must match OCR page numbers")


def page_requires_ocr(page: ExtractedPage) -> bool:
    """Route only empty pages or sparse overlays over image-dominant pages."""

    return not page.text or (
        page.largest_image_coverage >= _IMAGE_DOMINANT_COVERAGE
        and len(page.text) < _SPARSE_OVERLAY_CHARACTER_LIMIT
    )


def ocr_page_numbers(pages: Sequence[ExtractedPage]) -> tuple[int, ...]:
    return tuple(page.page_number for page in pages if page_requires_ocr(page))


class PyMuPdfExtractor:
    def __init__(self, *, max_pages: int) -> None:
        if (
            not isinstance(max_pages, int)
            or isinstance(max_pages, bool)
            or not 1 <= max_pages <= 1_000
        ):
            raise ValueError("max_pages must be between 1 and 1000")
        self._max_pages = max_pages

    def extract(self, data: bytes) -> NativeExtractionResult:
        engine = "pymupdf"
        engine_version = str(pymupdf.VersionBind)
        config: Mapping[str, OCRConfigValue] = snapshot_ocr_config(
            {"max_pages": self._max_pages, "sort_blocks": True}
        )
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as error:
            raise ExtractionError(ExtractionViolation.MALFORMED_PDF) from error

        try:
            if document.needs_pass:
                raise ExtractionError(ExtractionViolation.ENCRYPTED_PDF)
            if document.page_count == 0:
                raise ExtractionError(ExtractionViolation.MALFORMED_PDF)
            if document.page_count > self._max_pages:
                raise ExtractionError(ExtractionViolation.PAGE_LIMIT_EXCEEDED)

            pages = tuple(
                self._extract_page(
                    document[index],
                    index + 1,
                    extractor=engine,
                    extractor_version=engine_version,
                    extraction_config=config,
                )
                for index in range(document.page_count)
            )
        finally:
            document.close()

        character_count = sum(len(page.text) for page in pages)
        text_pages = sum(bool(page.text) for page in pages)
        native_text_page_ratio = text_pages / len(pages)
        image_dominant_pages = sum(
            page.largest_image_coverage >= _IMAGE_DOMINANT_COVERAGE
            and len(page.text) < _SPARSE_OVERLAY_CHARACTER_LIMIT
            for page in pages
        )
        image_dominant_page_ratio = image_dominant_pages / len(pages)
        selected_ocr_pages = ocr_page_numbers(pages)
        native_manifest: dict[str, object] = {
            "mode": ExtractionMode.NATIVE.value,
            "native": {
                "config": dict(config),
                "engine": engine,
                "version": engine_version,
            },
        }
        return NativeExtractionResult(
            engine=engine,
            engine_version=engine_version,
            pages=pages,
            page_count=len(pages),
            character_count=character_count,
            native_text_page_ratio=native_text_page_ratio,
            needs_ocr=bool(selected_ocr_pages),
            image_dominant_page_ratio=image_dominant_page_ratio,
            config=config,
            extraction_config=native_manifest,
        )

    @staticmethod
    def _extract_page(
        page: Any,
        page_number: int,
        *,
        extractor: str | None = None,
        extractor_version: str | None = None,
        extraction_config: Mapping[str, OCRConfigValue] | None = None,
    ) -> ExtractedPage:
        blocks: list[ExtractedBlock] = []
        for raw_block in page.get_text("blocks", sort=True):
            text = str(raw_block[4]).replace("\r\n", "\n").replace("\r", "\n").strip()
            block_type = int(raw_block[6])
            if block_type != 0 or not text:
                continue
            if _contains_unsafe_text_character(text):
                raise ExtractionError(ExtractionViolation.UNSAFE_TEXT)
            blocks.append(
                ExtractedBlock(
                    page_number=page_number,
                    reading_order=len(blocks),
                    bbox=(
                        float(raw_block[0]),
                        float(raw_block[1]),
                        float(raw_block[2]),
                        float(raw_block[3]),
                    ),
                    text=text,
                    extractor=extractor,
                    extractor_version=extractor_version,
                    extraction_config=extraction_config,
                )
            )
        return ExtractedPage(
            page_number=page_number,
            text="\n".join(block.text for block in blocks),
            blocks=tuple(blocks),
            largest_image_coverage=PyMuPdfExtractor._largest_image_coverage(page),
            extractor=extractor,
            extractor_version=extractor_version,
            extraction_config=extraction_config,
        )

    @staticmethod
    def _largest_image_coverage(page: Any) -> float:
        if not all(
            hasattr(page, attribute) for attribute in ("get_images", "get_image_rects", "rect")
        ):
            return 0.0
        page_area = float(page.rect.get_area())
        if page_area <= 0:
            return 0.0
        largest_area = 0.0
        for image in page.get_images(full=True):
            for image_rect in page.get_image_rects(int(image[0])):
                largest_area = max(largest_area, float(image_rect.get_area()))
        return min(1.0, largest_area / page_area)
