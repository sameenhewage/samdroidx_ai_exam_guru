"""Deterministic PDF fixtures shared by page-image and PDF-render suites."""

from pathlib import Path
from typing import cast

import pymupdf


def pdf_bytes(page_count: int = 1, *, encrypted: bool = False) -> bytes:
    document = pymupdf.open()
    for page_index in range(page_count):
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 30), f"synthetic page {page_index + 1}")
    options: dict[str, object] = {"garbage": 4, "deflate": True}
    if encrypted:
        options.update(
            encryption=cast(int, pymupdf.PDF_ENCRYPT_AES_256),  # type: ignore[attr-defined]
            owner_pw="owner-fixture",
            user_pw="user-fixture",
        )
    data = cast(bytes, document.tobytes(**options))
    document.close()
    return data


def source_pdf(path: Path, *, pages: int = 1, font_name: str | None = None) -> Path:
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=180, height=180)
            page.insert_text(
                (15, 30), f"Read the question and choose the answer {index + 1}", fontsize=5
            )
        if font_name is not None:
            for font in document[0].get_fonts():
                document.xref_set_key(font[0], "BaseFont", f"/{font_name}")
        document.save(path)
    return path
