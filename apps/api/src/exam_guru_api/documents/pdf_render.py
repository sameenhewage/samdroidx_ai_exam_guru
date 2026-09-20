"""Deterministic PDF rendering primitives.

These helpers are provider-independent and contain no text-recognition logic.
They open an already-verified immutable source PDF from a file descriptor,
reject descriptors that are not regular files, reject anything without a PDF
signature, optionally re-verify the recorded SHA-256, refuse a source that was
mutated while it was open, reject encrypted or malformed documents, and always
close the document and the duplicated descriptor deterministically.
"""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

import pymupdf

_HASH_CHUNK_BYTES = 1024 * 1024


class PdfSourceViolation(StrEnum):
    FILE_DESCRIPTOR_REQUIRED = "file_descriptor_required"
    INVALID_PDF_SIGNATURE = "invalid_pdf_signature"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    MALFORMED_PDF = "malformed_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    SOURCE_CHANGED = "source_changed"


class PdfSourceError(RuntimeError):
    """Typed rejection of an unsafe or out-of-policy source PDF."""

    def __init__(self, violation: PdfSourceViolation) -> None:
        self.violation = violation
        super().__init__(violation.value)


@dataclass(frozen=True, slots=True)
class RenderedPageImage:
    page_number: int
    path: Path
    sha256: str
    width: int
    height: int
    dpi: int

    def metadata(self) -> dict[str, object]:
        return {
            "page_number": self.page_number,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "content_type": "image/png",
            "rasterizer": "pymupdf",
            "rasterizer_version": pymupdf.VersionBind,
        }


def _validate_descriptor(descriptor: int, source_checksum_sha256: str | None) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise PdfSourceError(PdfSourceViolation.FILE_DESCRIPTOR_REQUIRED)
    if os.pread(descriptor, 5, 0) != b"%PDF-":
        raise PdfSourceError(PdfSourceViolation.INVALID_PDF_SIGNATURE)
    if source_checksum_sha256 is not None:
        digest = hashlib.sha256()
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(descriptor, min(_HASH_CHUNK_BYTES, metadata.st_size - offset), offset)
            if not chunk:
                raise PdfSourceError(PdfSourceViolation.SOURCE_CHANGED)
            digest.update(chunk)
            offset += len(chunk)
        if digest.hexdigest() != source_checksum_sha256:
            raise PdfSourceError(PdfSourceViolation.CHECKSUM_MISMATCH)
    current = os.fstat(descriptor)
    if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
    ):
        raise PdfSourceError(PdfSourceViolation.SOURCE_CHANGED)


@contextmanager
def open_pdf_file(
    source: BinaryIO, *, source_checksum_sha256: str | None = None
) -> Iterator[pymupdf.Document]:
    descriptor: int | None = None
    document: pymupdf.Document | None = None
    try:
        try:
            descriptor = os.dup(source.fileno())
            _validate_descriptor(descriptor, source_checksum_sha256)
        except (AttributeError, OSError, ValueError):
            raise PdfSourceError(PdfSourceViolation.FILE_DESCRIPTOR_REQUIRED) from None
        try:
            document = pymupdf.open(filename=f"/proc/self/fd/{descriptor}", filetype="pdf")
            if document.needs_pass:
                raise PdfSourceError(PdfSourceViolation.ENCRYPTED_PDF)
            if document.page_count < 1:
                raise PdfSourceError(PdfSourceViolation.MALFORMED_PDF)
        except PdfSourceError:
            raise
        except Exception:
            raise PdfSourceError(PdfSourceViolation.MALFORMED_PDF) from None
        yield document
    finally:
        if document is not None:
            document.close()
        if descriptor is not None:
            os.close(descriptor)
