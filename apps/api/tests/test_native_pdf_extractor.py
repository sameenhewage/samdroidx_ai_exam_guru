from typing import cast

import pymupdf
import pytest

from exam_guru_api.documents.extraction import (
    ExtractionError,
    ExtractionViolation,
    PyMuPdfExtractor,
)


def pdf_bytes(*page_texts: str) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = cast(bytes, document.tobytes(garbage=4, deflate=True))
    document.close()
    return data


def test_native_pdf_extraction_preserves_page_and_block_provenance() -> None:
    result = PyMuPdfExtractor(max_pages=10).extract(
        pdf_bytes("Grade 5 competency one", "Second page marking guidance")
    )

    assert result.engine == "pymupdf"
    assert result.engine_version
    assert result.page_count == 2
    assert result.character_count > 20
    assert result.needs_ocr is False
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "Grade 5 competency one" in result.pages[0].text
    assert result.pages[0].blocks[0].page_number == 1
    assert result.pages[0].blocks[0].reading_order == 0
    assert result.pages[0].blocks[0].bbox is not None
    assert len(result.pages[0].blocks[0].bbox) == 4


def test_textless_pdf_is_explicitly_routed_to_ocr() -> None:
    result = PyMuPdfExtractor(max_pages=10).extract(pdf_bytes(""))

    assert result.page_count == 1
    assert result.character_count == 0
    assert result.native_text_page_ratio == 0
    assert result.needs_ocr is True


def test_image_dominant_sparse_overlay_is_routed_to_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRect:
        def __init__(self, area: float) -> None:
            self._area = area

        def get_area(self) -> float:
            return self._area

    class FakePage:
        rect = FakeRect(100.0)

        def get_text(self, _mode: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort
            return [(0, 0, 10, 10, "Repeated sparse overlay text", 0, 0)]

        def get_images(self, *, full: bool) -> list[tuple[int]]:
            assert full
            return [(1,)]

        def get_image_rects(self, _xref: int) -> list[FakeRect]:
            return [FakeRect(99.0)]

    class FakeDocument:
        needs_pass = False
        page_count = 1

        def __getitem__(self, _index: int) -> FakePage:
            return FakePage()

        def close(self) -> None:
            return None

    monkeypatch.setattr(pymupdf, "open", lambda **_kwargs: FakeDocument())

    result = PyMuPdfExtractor(max_pages=10).extract(b"fixture")

    assert result.image_dominant_page_ratio == 1.0
    assert result.pages[0].largest_image_coverage == 0.99
    assert result.needs_ocr is True


def test_native_pdf_extraction_rejects_encrypted_and_empty_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDocument:
        def __init__(self, *, needs_pass: bool, page_count: int) -> None:
            self.needs_pass = needs_pass
            self.page_count = page_count

        def close(self) -> None:
            return None

    for document, violation in (
        (FakeDocument(needs_pass=True, page_count=1), ExtractionViolation.ENCRYPTED_PDF),
        (FakeDocument(needs_pass=False, page_count=0), ExtractionViolation.MALFORMED_PDF),
    ):
        monkeypatch.setattr(pymupdf, "open", lambda document=document, **_kwargs: document)
        with pytest.raises(ExtractionError) as raised:
            PyMuPdfExtractor(max_pages=10).extract(b"fixture")
        assert raised.value.violation is violation


def test_zero_area_page_cannot_report_image_coverage() -> None:
    class ZeroRect:
        @staticmethod
        def get_area() -> float:
            return 0.0

    class FakePage:
        rect = ZeroRect()

        @staticmethod
        def get_text(_mode: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort
            return []

        @staticmethod
        def get_images(*, full: bool) -> list[tuple[int]]:
            assert full
            return [(1,)]

        @staticmethod
        def get_image_rects(_xref: int) -> list[ZeroRect]:
            return [ZeroRect()]

    page = PyMuPdfExtractor._extract_page(FakePage(), 1)

    assert page.largest_image_coverage == 0.0


@pytest.mark.parametrize("unsafe_text", ["text\x00value", "text\x1b[31mvalue", "text\u202evalue"])
def test_page_extraction_rejects_unsafe_control_text(unsafe_text: str) -> None:
    class FakePage:
        def get_text(self, _mode: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort
            return [(0, 0, 10, 10, unsafe_text, 0, 0)]

    with pytest.raises(ExtractionError) as raised:
        PyMuPdfExtractor._extract_page(FakePage(), 1)

    assert raised.value.violation is ExtractionViolation.UNSAFE_TEXT


def test_page_extraction_preserves_sinhala_joiners_and_safe_whitespace() -> None:
    class FakePage:
        @staticmethod
        def get_text(_mode: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort
            return [(0, 0, 10, 10, "සිංහල\u200d පෙළ\tඅගය", 0, 0)]

    page = PyMuPdfExtractor._extract_page(FakePage(), 1)

    assert page.text == "සිංහල\u200d පෙළ\tඅගය"


def test_page_extraction_ignores_non_text_blocks() -> None:
    class FakePage:
        def get_text(self, _mode: str, *, sort: bool) -> list[tuple[object, ...]]:
            assert sort
            return [
                (0, 0, 10, 10, "image", 0, 1),
                (1, 2, 3, 4, "kept text", 0, 0),
            ]

    page = PyMuPdfExtractor._extract_page(FakePage(), 1)

    assert page.text == "kept text"
    assert len(page.blocks) == 1


@pytest.mark.parametrize(
    ("data", "max_pages", "violation"),
    [
        (b"not a pdf", 10, ExtractionViolation.MALFORMED_PDF),
        (pdf_bytes("one", "two"), 1, ExtractionViolation.PAGE_LIMIT_EXCEEDED),
    ],
)
def test_native_pdf_extraction_rejects_unsafe_input(
    data: bytes,
    max_pages: int,
    violation: ExtractionViolation,
) -> None:
    with pytest.raises(ExtractionError) as raised:
        PyMuPdfExtractor(max_pages=max_pages).extract(data)

    assert raised.value.violation is violation
