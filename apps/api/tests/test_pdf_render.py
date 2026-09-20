import hashlib
import io
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO, cast

import pymupdf
import pytest

from exam_guru_api.documents.pdf_render import (
    PdfSourceError,
    PdfSourceViolation,
    RenderedPageImage,
    open_pdf_file,
)
from tests.pdf_fixtures import pdf_bytes, source_pdf


def test_rendered_page_image_metadata_is_the_deterministic_rasterizer_identity(
    tmp_path: Path,
) -> None:
    image = RenderedPageImage(7, tmp_path / "page.png", "a" * 64, 32, 24, 144)
    assert image.metadata() == {
        "page_number": 7,
        "sha256": "a" * 64,
        "width": 32,
        "height": 24,
        "dpi": 144,
        "content_type": "image/png",
        "rasterizer": "pymupdf",
        "rasterizer_version": pymupdf.VersionBind,
    }


def test_file_backed_pdf_rejects_memory_stream_and_never_calls_unbounded_read() -> None:
    class NoRead(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("source stream must not be copied into RAM")

    with pytest.raises(PdfSourceError), open_pdf_file(NoRead(b"%PDF-fixture")):
        pytest.fail("memory-only input must fail closed")


def test_file_backed_pdf_does_not_trust_source_name(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")

    class DescriptorOnly:
        name = "https://invalid.example/private-source.pdf"

        def __init__(self, source: BinaryIO) -> None:
            self.source = source

        def fileno(self) -> int:
            return self.source.fileno()

        def read(self, size: int = -1) -> bytes:
            raise AssertionError("must open the local descriptor")

    with path.open("rb") as source, open_pdf_file(cast(BinaryIO, DescriptorOnly(source))) as pdf:
        assert pdf.page_count == 1


def test_checksum_is_streamed_without_moving_the_caller_cursor(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf", pages=3)
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    with path.open("rb") as source:
        source.seek(17)
        with open_pdf_file(source, source_checksum_sha256=checksum) as document:
            assert document.page_count == 3
        assert source.tell() == 17
        with (
            pytest.raises(PdfSourceError) as error,
            open_pdf_file(source, source_checksum_sha256="0" * 64),
        ):
            pytest.fail("mismatched checksums must fail closed")
        assert error.value.violation is PdfSourceViolation.CHECKSUM_MISMATCH
        assert not source.closed


def test_nonregular_source_descriptor_is_rejected_and_duplicate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    duplicate = os.dup
    captured: list[int] = []

    def tracking_dup(fd: int) -> int:
        owned = duplicate(fd)
        captured.append(owned)
        return owned

    monkeypatch.setattr(os, "dup", tracking_dup)
    try:
        with os.fdopen(read_fd, "rb") as source:
            with pytest.raises(PdfSourceError) as error, open_pdf_file(source):
                pytest.fail("a pipe must never reach the PDF parser")
            assert error.value.violation is PdfSourceViolation.FILE_DESCRIPTOR_REQUIRED
            assert not source.closed
            assert len(captured) == 1
            with pytest.raises(OSError, match="Bad file descriptor"):
                os.fstat(captured[0])
    finally:
        os.close(write_fd)


def test_pdf_signature_rejection_does_not_close_callers_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"PRIVATE not a PDF")
    with path.open("rb") as source:
        with pytest.raises(PdfSourceError) as error, open_pdf_file(source):
            pytest.fail("invalid PDF signature must fail")
        assert error.value.violation is PdfSourceViolation.INVALID_PDF_SIGNATURE
        assert "PRIVATE" not in str(error.value)
        assert not source.closed


@pytest.mark.parametrize("change", ["truncated", "metadata"])
def test_source_changed_during_checksum_verification_is_rejected_without_moving_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original_pread = os.pread
    reads: list[tuple[int, int]] = []

    def changing_read(fd: int, size: int, offset: int) -> bytes:
        reads.append((size, offset))
        data = original_pread(fd, size, offset)
        if len(reads) == 1 and change == "truncated":
            with path.open("r+b") as writer:
                writer.truncate(0)
        elif len(reads) == 2 and change == "metadata":
            details = path.stat()
            os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns + 1))
        return data

    monkeypatch.setattr(os, "pread", changing_read)
    with path.open("rb") as source:
        source.seek(17)
        with (
            pytest.raises(PdfSourceError) as error,
            open_pdf_file(source, source_checksum_sha256=digest),
        ):
            pytest.fail("a source changed during verification is not immutable evidence")
        assert error.value.violation is PdfSourceViolation.SOURCE_CHANGED
        assert source.tell() == 17
        assert not source.closed
    assert all(size <= 1024 * 1024 for size, _offset in reads)


def test_encrypted_pdf_is_rejected_and_parser_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(pdf_bytes(encrypted=True))
    opened: list[pymupdf.Document] = []
    original = cast(Callable[..., pymupdf.Document], pymupdf.open)

    def tracked_open(*args: Any, **kwargs: Any) -> pymupdf.Document:
        document = original(*args, **kwargs)
        opened.append(document)
        return document

    monkeypatch.setattr(pymupdf, "open", tracked_open)
    with path.open("rb") as source:
        with pytest.raises(PdfSourceError) as error, open_pdf_file(source):
            pytest.fail("encrypted originals require an explicit unsupported response")
        assert error.value.violation is PdfSourceViolation.ENCRYPTED_PDF
        assert not source.closed
    assert opened
    assert all(document.is_closed for document in opened)


def test_empty_parser_document_is_rejected_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    empty = pymupdf.open()
    monkeypatch.setattr(pymupdf, "open", lambda **_kwargs: empty)
    with (
        path.open("rb") as source,
        pytest.raises(PdfSourceError) as error,
        open_pdf_file(source),
    ):
        pytest.fail("a parser result with zero pages cannot become a source reading")
    assert error.value.violation is PdfSourceViolation.MALFORMED_PDF
    assert empty.is_closed


def test_malformed_pdf_body_is_rejected_after_a_valid_signature(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\nPRIVATE truncated body")
    with path.open("rb") as source:
        with pytest.raises(PdfSourceError) as error, open_pdf_file(source):
            pytest.fail("a malformed body cannot become a readable document")
        assert error.value.violation is PdfSourceViolation.MALFORMED_PDF
        assert "PRIVATE" not in str(error.value)


def test_document_and_duplicate_descriptor_are_always_released(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    duplicate = os.dup
    captured: list[int] = []
    documents: list[pymupdf.Document] = []

    def tracking_dup(fd: int) -> int:
        owned = duplicate(fd)
        captured.append(owned)
        return owned

    def read_and_fail(source: BinaryIO) -> None:
        with open_pdf_file(source) as document:
            documents.append(document)
            assert document.page_count == 1
            raise RuntimeError("fixture")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "dup", tracking_dup)
        with path.open("rb") as source:
            with pytest.raises(RuntimeError, match="fixture"):
                read_and_fail(source)
            assert documents[0].is_closed
            assert not source.closed
    assert len(captured) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(captured[0])
