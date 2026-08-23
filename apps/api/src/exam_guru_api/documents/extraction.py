from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pymupdf


class ExtractionViolation(StrEnum):
    MALFORMED_PDF = "malformed_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"


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


@dataclass(frozen=True, slots=True)
class NativeExtractionResult:
    engine: str
    engine_version: str
    pages: tuple[ExtractedPage, ...]
    page_count: int
    character_count: int
    native_text_page_ratio: float
    needs_ocr: bool


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
        return NativeExtractionResult(
            engine="pymupdf",
            engine_version=pymupdf.VersionBind,
            pages=pages,
            page_count=len(pages),
            character_count=character_count,
            native_text_page_ratio=native_text_page_ratio,
            needs_ocr=character_count == 0 or native_text_page_ratio < 0.8,
        )

    @staticmethod
    def _extract_page(page: Any, page_number: int) -> ExtractedPage:
        blocks: list[ExtractedBlock] = []
        for raw_block in page.get_text("blocks", sort=True):
            text = str(raw_block[4]).replace("\r\n", "\n").replace("\r", "\n").strip()
            block_type = int(raw_block[6])
            if block_type != 0 or not text:
                continue
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
        )
