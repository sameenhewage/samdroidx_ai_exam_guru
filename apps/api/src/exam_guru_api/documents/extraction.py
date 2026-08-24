from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pymupdf

from exam_guru_api.documents.domain import ExtractionStatus

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
    bbox: tuple[float, float, float, float]
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_number: int
    text: str
    blocks: tuple[ExtractedBlock, ...]
    largest_image_coverage: float = 0.0


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


class PyMuPdfExtractor:
    def __init__(self, *, max_pages: int) -> None:
        self._max_pages = max_pages

    def extract(self, data: bytes) -> NativeExtractionResult:
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
                self._extract_page(document[index], index + 1)
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
        return NativeExtractionResult(
            engine="pymupdf",
            engine_version=pymupdf.VersionBind,
            pages=pages,
            page_count=len(pages),
            character_count=character_count,
            native_text_page_ratio=native_text_page_ratio,
            needs_ocr=(
                character_count == 0 or native_text_page_ratio < 0.8 or image_dominant_pages > 0
            ),
            image_dominant_page_ratio=image_dominant_page_ratio,
        )

    @staticmethod
    def _extract_page(page: Any, page_number: int) -> ExtractedPage:
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
                )
            )
        return ExtractedPage(
            page_number=page_number,
            text="\n".join(block.text for block in blocks),
            blocks=tuple(blocks),
            largest_image_coverage=PyMuPdfExtractor._largest_image_coverage(page),
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
