import hashlib
from pathlib import Path
from typing import cast

import pymupdf
import pytest

from exam_guru_api.documents.ocr import OCRRequest
from exam_guru_api.documents.tesseract_ocr import (
    TesseractCliOCRAdapter,
    TesseractOCRConfig,
    TesseractUnavailableError,
)


def synthetic_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=150)
    page.insert_text((30, 60), "Synthetic OCR integration fixture")
    data = cast(bytes, document.tobytes(garbage=4, deflate=True))
    document.close()
    return data


@pytest.mark.integration
def test_real_tesseract_cli_smoke_when_sinhala_traineddata_is_available(
    tmp_path: Path,
) -> None:
    """Exercise mechanics only; this synthetic smoke test makes no Sinhala quality claim."""

    adapter = TesseractCliOCRAdapter(
        config=TesseractOCRConfig(
            max_source_bytes=1_000_000,
            max_pages=1,
            batch_size=1,
            dpi=144,
            timeout_seconds=15,
            max_pixels_per_page=2_000_000,
        )
    )
    try:
        probe = adapter.probe(temporary_directory=tmp_path)
    except TesseractUnavailableError as error:
        pytest.skip(f"optional Tesseract/sin integration unavailable: {error}")

    data = synthetic_pdf()
    result = adapter.extract(
        OCRRequest(
            source_document_id="synthetic-tesseract-smoke",
            source_checksum_sha256=hashlib.sha256(data).hexdigest(),
            content=data,
            media_type="application/pdf",
        ),
        temporary_directory=tmp_path,
    )

    assert probe.engine_version == result.engine_version
    assert {"sin", "eng"}.issubset(probe.available_languages)
    assert result.engine == "tesseract-cli"
    assert result.config["language"] == "sin+eng"
    assert tuple(page.page_number for page in result.pages) == (1,)
    assert list(tmp_path.iterdir()) == []
