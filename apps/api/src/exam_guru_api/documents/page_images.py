import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import resource
import stat
import struct
import sys
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from typing import Annotated, BinaryIO, Literal, Protocol, Self, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import anyio
import pymupdf
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.core.config import Settings, StorageBackend
from exam_guru_api.documents.fidelity_models import PageReviewStateModel, PageTextCandidateModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.tesseract_ocr import RenderedPageImage, open_pdf_file
from exam_guru_api.infrastructure.object_storage import (
    InvalidObjectKeyError,
    ObjectStorageOperationError,
    validate_source_object_key,
)
from exam_guru_api.infrastructure.private_artifacts import (
    ARTIFACT_CHUNK_BYTES,
    PrivateArtifactError,
    PrivateUploadArtifacts,
)

STREAM_CHUNK_BYTES = 1024 * 1024
MAX_IMAGE_METADATA_BYTES = 64 * 1024
MAX_SOURCE_PAGE_NUMBER = 2_147_483_646
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 16_000
_RENDER_SLOTS = BoundedSemaphore(2)
_IMAGE_SLOTS = BoundedSemaphore(4)
_SOURCE_SLOTS = BoundedSemaphore(4)
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_RANGE = re.compile(r"bytes=([0-9]{0,19})-([0-9]{0,19})")
_IMAGE_NAMESPACE = "fidelity-page-images"
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK


class PageImageError(RuntimeError):
    def __init__(self, code: str, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class SourceImageStorage(Protocol):
    def open_source(self, key: str) -> AbstractContextManager[BinaryIO]: ...


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


@dataclass(frozen=True, slots=True)
class PageImageLimits:
    dpi: int = 144
    max_pixels: int = MAX_IMAGE_PIXELS
    max_dimension: int = MAX_IMAGE_DIMENSION
    max_png_bytes: int = MAX_PNG_BYTES
    timeout_seconds: float = 20.0
    max_memory_bytes: int = 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not _integer(self.dpi, 72, 600)
            or not _integer(self.max_pixels, 1, MAX_IMAGE_PIXELS)
            or not _integer(self.max_dimension, 1, MAX_IMAGE_DIMENSION)
            or not _integer(self.max_png_bytes, 1, MAX_PNG_BYTES)
            or not _integer(self.max_memory_bytes, 128 * 1024 * 1024, 2 * 1024**3)
            or isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 60
        ):
            raise ValueError("invalid page image resource limits")


@dataclass(frozen=True, slots=True)
class SourceImageIdentity:
    document_id: UUID
    checksum_sha256: str
    object_key: str
    size_bytes: int
    page_count: int | None
    filename: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.document_id, UUID)
            or not isinstance(self.checksum_sha256, str)
            or _CHECKSUM.fullmatch(self.checksum_sha256) is None
            or not isinstance(self.object_key, str)
            or self.object_key != f"sources/{self.checksum_sha256[:2]}/{self.checksum_sha256}.pdf"
            or not _integer(self.size_bytes, 5, 2**63 - 1)
            or (
                self.page_count is not None
                and not _integer(self.page_count, 1, MAX_SOURCE_PAGE_NUMBER)
            )
            or not isinstance(self.filename, str)
        ):
            raise PageImageError("source_original_unavailable")
        try:
            validate_source_object_key(self.object_key)
        except InvalidObjectKeyError:
            raise PageImageError("source_original_unavailable") from None

    def check_page(self, page_number: int) -> None:
        if not _integer(page_number, 1, MAX_SOURCE_PAGE_NUMBER) or (
            self.page_count is not None and page_number > self.page_count
        ):
            raise PageImageError("source_page_not_found", 404)


def image_artifact_id(checksum: str) -> UUID:
    if not isinstance(checksum, str) or _CHECKSUM.fullmatch(checksum) is None:
        raise ValueError("invalid image checksum")
    return uuid5(NAMESPACE_URL, f"exam-guru:{_IMAGE_NAMESPACE}:{checksum}")


Checksum = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class PageImageArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    namespace: Literal["fidelity-page-images"] = "fidelity-page-images"
    id: str = Field(strict=True, min_length=36, max_length=36)
    size_bytes: int = Field(strict=True, ge=1, le=MAX_PNG_BYTES)
    sha256: Checksum
    chunk_bytes: Literal[4194304] = 4194304
    chunk_sha256: tuple[Checksum, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (
            self.id != str(image_artifact_id(self.sha256))
            or len(self.chunk_sha256)
            != (self.size_bytes + ARTIFACT_CHUNK_BYTES - 1) // ARTIFACT_CHUNK_BYTES
        ):
            raise ValueError("invalid page image artifact identity")
        return self


class SourceCandidateImageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(strict=True, min_length=36, max_length=36)
    source_checksum_sha256: Checksum
    source_object_key: str = Field(strict=True, max_length=255)
    source_size_bytes: int = Field(strict=True, ge=5, le=2**63 - 1)
    page_number: int = Field(strict=True, ge=1, le=MAX_SOURCE_PAGE_NUMBER)
    sha256: Checksum
    width: int = Field(strict=True, ge=1, le=MAX_IMAGE_DIMENSION)
    height: int = Field(strict=True, ge=1, le=MAX_IMAGE_DIMENSION)
    dpi: int = Field(strict=True, ge=72, le=600)
    content_type: Literal["image/png"]
    rasterizer: Literal["pymupdf"]
    rasterizer_version: str = Field(
        strict=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._+~-]+$"
    )
    artifact: PageImageArtifactReference

    @model_validator(mode="before")
    @classmethod
    def bound_metadata(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise ValueError("page image metadata must be an object")
        try:
            encoded = json.dumps(value, allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError):
            raise ValueError("invalid page image metadata") from None
        if len(encoded) > MAX_IMAGE_METADATA_BYTES:
            raise ValueError("page image metadata exceeds its bound")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if (
            str(UUID(self.document_id)) != self.document_id
            or self.source_object_key
            != f"sources/{self.source_checksum_sha256[:2]}/{self.source_checksum_sha256}.pdf"
            or self.width * self.height > MAX_IMAGE_PIXELS
            or self.sha256 != self.artifact.sha256
        ):
            raise ValueError("page image metadata identity mismatch")
        return self


@contextmanager
def _slot(
    semaphore: BoundedSemaphore, code: str, *, wait_seconds: float | None = None
) -> Iterator[None]:
    acquired = (
        semaphore.acquire(blocking=False)
        if wait_seconds is None
        else semaphore.acquire(timeout=wait_seconds)
    )
    if not acquired:
        raise PageImageError(code)
    try:
        yield
    finally:
        semaphore.release()


def _read_bounded(stream: BinaryIO, size: int) -> bytes:
    if not _integer(size, 1, MAX_PNG_BYTES):
        raise PageImageError("source_page_image_invalid")
    pieces: list[bytes] = []
    remaining = size
    while remaining:
        amount = min(STREAM_CHUNK_BYTES, remaining)
        piece = stream.read(amount)
        if not isinstance(piece, bytes) or not 0 < len(piece) <= amount:
            raise PageImageError("source_page_image_invalid")
        pieces.append(piece)
        remaining -= len(piece)
    if stream.read(1):
        raise PageImageError("source_page_image_invalid")
    return b"".join(pieces)


def _stat_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _png_dimensions(data: bytes, limits: PageImageLimits) -> tuple[int, int]:
    if len(data) > limits.max_png_bytes or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PageImageError("source_page_image_invalid")
    position = 8
    dimensions: tuple[int, int] | None = None
    expected = 0
    stride = 0
    expanded = 0
    decoder = zlib.decompressobj()
    seen_idat = False
    ended_idat = False
    try:
        while position < len(data):
            if position + 12 > len(data):
                raise ValueError
            length = struct.unpack_from(">I", data, position)[0]
            end = position + 12 + length
            if end > len(data):
                raise ValueError
            kind = data[position + 4 : position + 8]
            payload = data[position + 8 : end - 4]
            checksum = struct.unpack_from(">I", data, end - 4)[0]
            if zlib.crc32(payload, zlib.crc32(kind)) != checksum:
                raise ValueError
            if dimensions is None:
                if kind != b"IHDR" or length != 13:
                    raise ValueError
                width, height, depth, color, compression, filtering, interlace = struct.unpack(
                    ">IIBBBBB", payload
                )
                if (
                    not 0 < width <= limits.max_dimension
                    or not 0 < height <= limits.max_dimension
                    or width * height > limits.max_pixels
                    or (depth, color, compression, filtering, interlace) != (8, 2, 0, 0, 0)
                ):
                    raise ValueError
                dimensions = width, height
                stride = width * 3 + 1
                expected = height * stride
            elif kind == b"IHDR":
                raise ValueError
            elif kind == b"IDAT":
                if ended_idat:
                    raise ValueError
                seen_idat = True
                for offset in range(0, len(payload), STREAM_CHUNK_BYTES):
                    compressed = payload[offset : offset + STREAM_CHUNK_BYTES]
                    while compressed:
                        decoded = decoder.decompress(
                            compressed, min(STREAM_CHUNK_BYTES, expected - expanded + 1)
                        )
                        if any(
                            decoded[index] > 4
                            for index in range(-expanded % stride, len(decoded), stride)
                        ):
                            raise ValueError
                        expanded += len(decoded)
                        if expanded > expected or decoder.unused_data:
                            raise ValueError
                        compressed = decoder.unconsumed_tail
            elif kind == b"IEND":
                if (
                    length
                    or end != len(data)
                    or not seen_idat
                    or not decoder.eof
                    or expanded != expected
                ):
                    raise ValueError
                return dimensions
            elif not kind[0] & 32:
                raise ValueError
            if seen_idat and kind != b"IDAT":
                ended_idat = True
            position = end
    except (ValueError, zlib.error, struct.error, IndexError):
        raise PageImageError("source_page_image_invalid") from None
    raise PageImageError("source_page_image_invalid")


def read_rendered_image(
    image: RenderedPageImage, *, limits: PageImageLimits | None = None
) -> bytes:
    selected = limits or PageImageLimits()
    try:
        descriptor = os.open(image.path, _READ_FLAGS)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or not 0 < before.st_size <= selected.max_png_bytes
            ):
                raise PageImageError("source_page_image_invalid")
            data = _read_bounded(stream, before.st_size)
            if _stat_identity(before) != _stat_identity(os.fstat(stream.fileno())):
                raise PageImageError("source_page_image_invalid")
        if _png_dimensions(data, selected) != (
            image.width,
            image.height,
        ) or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), image.sha256):
            raise PageImageError("source_page_image_invalid")
        return data
    except (OSError, TypeError, ValueError):
        raise PageImageError("source_page_image_invalid") from None


class PageImageArtifacts:
    def __init__(self, *, root: str | os.PathLike[str]) -> None:
        self._artifacts = PrivateUploadArtifacts(root=root)

    def observer(
        self, source: SourceImageIdentity
    ) -> Callable[[RenderedPageImage], dict[str, object]]:
        return lambda image: self.persist(image, source=source)

    def persist(
        self, image: RenderedPageImage, *, source: SourceImageIdentity
    ) -> dict[str, object]:
        source.check_page(image.page_number)
        with _slot(_IMAGE_SLOTS, "source_page_image_busy"):
            data = read_rendered_image(image)
            identifier = image_artifact_id(image.sha256)
            hashes = tuple(
                hashlib.sha256(data[offset : offset + ARTIFACT_CHUNK_BYTES]).hexdigest()
                for offset in range(0, len(data), ARTIFACT_CHUNK_BYTES)
            )
            reference = PageImageArtifactReference(
                id=str(identifier), size_bytes=len(data), sha256=image.sha256, chunk_sha256=hashes
            )
            metadata = SourceCandidateImageMetadata.model_validate(
                {
                    **image.metadata(),
                    "document_id": str(source.document_id),
                    "source_checksum_sha256": source.checksum_sha256,
                    "source_object_key": source.object_key,
                    "source_size_bytes": source.size_bytes,
                    "artifact": reference.model_dump(mode="json"),
                }
            )
            try:
                for index, checksum in enumerate(hashes):
                    offset = index * ARTIFACT_CHUNK_BYTES
                    self._artifacts.put_chunk(
                        identifier,
                        offset,
                        data[offset : offset + ARTIFACT_CHUNK_BYTES],
                        checksum_sha256=checksum,
                    )
            except (PrivateArtifactError, OSError):
                raise PageImageError("source_page_image_artifact_unavailable") from None
            return cast(dict[str, object], metadata.model_dump(mode="json"))

    def read(
        self, metadata: dict[str, object], *, source: SourceImageIdentity, page_number: int
    ) -> bytes:
        try:
            parsed = SourceCandidateImageMetadata.model_validate(metadata)
        except ValidationError:
            raise PageImageError("source_page_image_metadata_invalid") from None
        if (
            parsed.document_id != str(source.document_id)
            or parsed.source_checksum_sha256 != source.checksum_sha256
            or parsed.source_object_key != source.object_key
            or parsed.source_size_bytes != source.size_bytes
            or parsed.page_number != page_number
        ):
            raise PageImageError("source_page_image_metadata_invalid")
        source.check_page(page_number)
        reference = parsed.artifact
        with _slot(_IMAGE_SLOTS, "source_page_image_busy"):
            try:
                with self._artifacts.open_upload(
                    UUID(reference.id), expected_size=reference.size_bytes
                ) as stream:
                    data = _read_bounded(stream, reference.size_bytes)
                if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), reference.sha256):
                    raise PrivateArtifactError("image_checksum_mismatch")
                for index, checksum in enumerate(reference.chunk_sha256):
                    offset = index * ARTIFACT_CHUNK_BYTES
                    if not hmac.compare_digest(
                        hashlib.sha256(data[offset : offset + ARTIFACT_CHUNK_BYTES]).hexdigest(),
                        checksum,
                    ):
                        raise PrivateArtifactError("image_chunk_mismatch")
                if _png_dimensions(data, PageImageLimits()) != (parsed.width, parsed.height):
                    raise PrivateArtifactError("image_dimensions_mismatch")
            except (PrivateArtifactError, PageImageError, OSError):
                raise PageImageError("source_page_image_artifact_unavailable") from None
            return data


def observe_page_image(
    image: RenderedPageImage,
    observer: Callable[[RenderedPageImage], dict[str, object] | None] | None,
) -> tuple[dict[str, object], str | None]:
    metadata = image.metadata()
    try:
        observed = observer(image) if observer is not None else None
        if observed is not None:
            parsed = SourceCandidateImageMetadata.model_validate(observed)
            if any(observed.get(key) != value for key, value in metadata.items()):
                raise ValueError("render observer changed image identity")
            metadata = cast(dict[str, object], parsed.model_dump(mode="json"))
    except Exception:
        metadata["failure_code"] = "page_image_artifact_unavailable"
        return metadata, "page_image_artifact_unavailable"
    return metadata, None


def block_provenance(
    raw_text: str,
    blocks: Sequence[tuple[str, tuple[float, float, float, float] | None]],
    *,
    separator: str,
    coordinate_space: str,
    max_bytes: int = 16 * 1024,
) -> dict[str, object]:
    joined = separator.join(text for text, _bbox in blocks)
    aligned = joined == raw_text
    items: list[dict[str, object]] = []
    result: dict[str, object] = {
        "view": "blocks",
        "coordinate_space": coordinate_space,
        "offset_space": "raw_text_codepoints",
        "offset_status": "exact" if aligned else "unknown",
        "view_sha256": hashlib.sha256(joined.encode("utf-8", errors="surrogatepass")).hexdigest(),
        "total_count": len(blocks),
        "truncated": False,
        "items": items,
    }
    offset = 0
    for order, (text, bbox) in enumerate(blocks):
        rectangle = (
            list(bbox) if bbox is not None and all(math.isfinite(value) for value in bbox) else None
        )
        items.append(
            {
                "reading_order": order,
                "bbox": rectangle,
                "sha256": hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest(),
                "character_count": len(text),
                "character_start": offset if aligned else None,
                "character_end": offset + len(text) if aligned else None,
                "offset_status": "exact" if aligned else "unknown",
            }
        )
        if order >= 255 or len(json.dumps(result).encode()) > max_bytes:
            items.pop()
            result["truncated"] = True
            break
        offset += len(text) + len(separator)
    return result


def native_block_provenance(
    document: pymupdf.Document, page_number: int, raw_text: str, *, max_bytes: int = 16 * 1024
) -> dict[str, object]:
    try:
        blocks = document[page_number - 1].get_text("blocks", sort=True)
        selected = [
            (
                str(block[4]),
                cast(tuple[float, float, float, float], tuple(float(value) for value in block[:4])),
            )
            for block in blocks
            if len(block) >= 7 and block[6] == 0
        ]
        return block_provenance(
            raw_text, selected, separator="", coordinate_space="pdf_points", max_bytes=max_bytes
        )
    except Exception:
        return {
            "view": "blocks",
            "offset_status": "unknown",
            "failure_code": "native_layout_unavailable",
            "items": [],
        }


def create_page_image_artifacts(settings: Settings) -> PageImageArtifacts | None:
    if settings.storage_backend is not StorageBackend.LOCAL:
        return None
    return PageImageArtifacts(root=f"{settings.storage_root}/{_IMAGE_NAMESPACE}")


@contextmanager
def open_verified_original(
    storage: SourceImageStorage, source: SourceImageIdentity
) -> Iterator[BinaryIO]:
    with _slot(_SOURCE_SLOTS, "source_streaming_busy"):
        try:
            with storage.open_source(source.object_key) as stream:
                details = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(details.st_mode)
                    or details.st_size != source.size_bytes
                    or not stream.seekable()
                    or not stream.readable()
                    or os.pread(stream.fileno(), 5, 0) != b"%PDF-"
                ):
                    raise PageImageError("source_original_unavailable")
                yield stream
        except ObjectStorageOperationError as error:
            if error.failure_code == "object_storage_streaming_unsupported":
                raise PageImageError("source_streaming_unsupported") from None
            raise PageImageError("source_original_unavailable") from None
        except (AttributeError, OSError, ValueError, InvalidObjectKeyError):
            raise PageImageError("source_original_unavailable") from None


def iter_original(stream: BinaryIO, *, start: int, length: int) -> Iterator[bytes]:
    try:
        before = os.fstat(stream.fileno())
        if not _integer(start, 0, before.st_size - 1) or not _integer(
            length, 1, before.st_size - start
        ):
            raise PageImageError("source_original_unavailable")
        stream.seek(start)
    except (OSError, ValueError):
        raise PageImageError("source_original_unavailable") from None
    return _original_chunks(stream, before, length)


def _original_chunks(stream: BinaryIO, before: os.stat_result, remaining: int) -> Iterator[bytes]:
    try:
        while remaining:
            amount = min(STREAM_CHUNK_BYTES, remaining)
            piece = stream.read(amount)
            if (
                not isinstance(piece, bytes)
                or not 0 < len(piece) <= amount
                or _stat_identity(before) != _stat_identity(os.fstat(stream.fileno()))
            ):
                raise PageImageError("source_original_unavailable")
            remaining -= len(piece)
            yield piece
    except (OSError, ValueError):
        raise PageImageError("source_original_unavailable") from None


def parse_single_range(header: str | None, size: int) -> tuple[int, int] | None:
    if header is None:
        return None
    matched = _RANGE.fullmatch(header) if len(header) <= 128 else None
    if matched is None or not any(matched.groups()):
        raise PageImageError("source_range_not_satisfiable", 416)
    first, last = matched.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise PageImageError("source_range_not_satisfiable", 416)
        return max(0, size - suffix), size - 1
    start, end = int(first), int(last) if last else size - 1
    if not 0 <= start <= end or start >= size:
        raise PageImageError("source_range_not_satisfiable", 416)
    return start, min(end, size - 1)


def _render_worker(
    source_fd: int,
    output_fd: int,
    page_number: int,
    limits: PageImageLimits,
    expected_count: int | None,
) -> dict[str, object]:
    resource.setrlimit(resource.RLIMIT_AS, (limits.max_memory_bytes, limits.max_memory_bytes))
    cpu_seconds = max(1, math.ceil(limits.timeout_seconds))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_png_bytes, limits.max_png_bytes))
    with (
        os.fdopen(os.dup(source_fd), "rb", buffering=0) as source,
        open_pdf_file(source) as document,
    ):
        if expected_count is not None and document.page_count != expected_count:
            raise PageImageError("source_page_count_mismatch")
        if not _integer(page_number, 1, document.page_count):
            raise PageImageError("source_page_not_found", 404)
        page = document[page_number - 1]
        scale = limits.dpi / 72.0
        width, height = float(page.rect.width) * scale, float(page.rect.height) * scale
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or not 0 < width <= limits.max_dimension
            or not 0 < height <= limits.max_dimension
            or math.ceil(width) * math.ceil(height) > limits.max_pixels
        ):
            raise PageImageError("source_page_image_raster_limit")
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False
        )
        if (
            not 0 < pixmap.width <= limits.max_dimension
            or not 0 < pixmap.height <= limits.max_dimension
            or pixmap.width * pixmap.height > limits.max_pixels
        ):
            raise PageImageError("source_page_image_raster_limit")
        png = pixmap.tobytes("png")
        if not 0 < len(png) <= limits.max_png_bytes:
            raise PageImageError("source_page_image_png_limit")
        remaining = memoryview(png)
        while remaining:
            written = os.write(output_fd, remaining[:STREAM_CHUNK_BYTES])
            if written <= 0:
                raise PageImageError("source_page_image_unavailable")
            remaining = remaining[written:]
        return {
            "width": pixmap.width,
            "height": pixmap.height,
            "rasterizer_version": pymupdf.VersionBind,
        }


async def _run_renderer(
    source_fd: int,
    output_fd: int,
    page_number: int,
    limits: PageImageLimits,
    expected_count: int | None,
) -> dict[str, object]:
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                "exam_guru_api.documents.page_images",
                str(source_fd),
                str(output_fd),
                str(page_number),
                json.dumps(asdict(limits)),
                json.dumps(expected_count),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                pass_fds=(source_fd, output_fd),
                limit=4096,
            )
            if process.stdout is None:
                raise PageImageError("source_page_image_unavailable")
            output = bytearray()
            while True:
                chunk = await process.stdout.read(4097 - len(output))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 4096:
                    raise PageImageError("source_page_image_unavailable")
            if await process.wait() != 0:
                raise PageImageError("source_page_image_unavailable")
            metadata = json.loads(output)
            if not isinstance(metadata, dict):
                raise PageImageError("source_page_image_unavailable")
            code = metadata.get("code")
            if code is not None:
                if not isinstance(code, str) or code not in {
                    "source_page_not_found",
                    "source_page_count_mismatch",
                    "source_page_image_raster_limit",
                }:
                    code = "source_page_image_unavailable"
                raise PageImageError(code, 404 if code == "source_page_not_found" else 503)
            return metadata
    except TimeoutError:
        raise PageImageError("source_page_image_timeout") from None
    except (OSError, ValueError):
        raise PageImageError("source_page_image_unavailable") from None
    finally:
        if process is not None:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()


@contextmanager
def render_page_image(
    source: BinaryIO,
    page_number: int,
    *,
    limits: PageImageLimits | None = None,
    expected_page_count: int | None = None,
    wait_for_slot: bool = False,
) -> Iterator[RenderedPageImage]:
    selected = limits or PageImageLimits()
    if not _integer(page_number, 1, MAX_SOURCE_PAGE_NUMBER):
        raise PageImageError("source_page_not_found", 404)
    deadline = time.monotonic() + selected.timeout_seconds
    with (
        _slot(
            _RENDER_SLOTS,
            "source_page_image_timeout" if wait_for_slot else "source_page_image_busy",
            wait_seconds=selected.timeout_seconds if wait_for_slot else None,
        ),
        TemporaryDirectory(prefix="exam-guru-page-image-") as directory,
    ):
        if wait_for_slot:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PageImageError("source_page_image_timeout")
            selected = replace(selected, timeout_seconds=remaining)
        path = Path(directory) / "page.png"
        with path.open("w+b") as output:
            metadata = asyncio.run(
                _run_renderer(
                    source.fileno(), output.fileno(), page_number, selected, expected_page_count
                )
            )
            size = os.fstat(output.fileno()).st_size
            if not 0 < size <= selected.max_png_bytes:
                raise PageImageError("source_page_image_invalid")
            output.seek(0)
            data = _read_bounded(output, size)
            width, height = _png_dimensions(data, selected)
            if (
                metadata.get("width") != width
                or metadata.get("height") != height
                or metadata.get("rasterizer_version") != pymupdf.VersionBind
            ):
                raise PageImageError("source_page_image_invalid")
            image = RenderedPageImage(
                page_number, path, hashlib.sha256(data).hexdigest(), width, height, selected.dpi
            )
        yield image


async def load_material_source(
    session: AsyncSession, document_id: UUID, *, principal: Principal
) -> SourceImageIdentity:
    authorize(principal, Permission.SOURCE_READ)
    document = await session.get(SourceDocumentModel, document_id)
    if document is None or document.quarantined_for_teacher_use:
        raise PageImageError("source_document_not_found", 404)
    if document.id != document_id or document.content_type != "application/pdf":
        raise PageImageError("source_original_unavailable")
    return SourceImageIdentity(
        document.id,
        document.checksum_sha256,
        document.object_key,
        document.size_bytes,
        document.original_page_count,
        document.original_filename,
    )


@dataclass(frozen=True, slots=True)
class PageImageContent:
    data: bytes = field(repr=False)
    origin: Literal["persisted", "original-render"]


def _fallback_image(
    storage: SourceImageStorage, source: SourceImageIdentity, page_number: int
) -> bytes:
    with (
        open_verified_original(storage, source) as stream,
        render_page_image(stream, page_number, expected_page_count=source.page_count) as image,
    ):
        return read_rendered_image(image)


async def get_material_page_image(
    session: AsyncSession,
    document_id: UUID,
    page_number: int,
    *,
    principal: Principal,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts | None,
) -> PageImageContent:
    source = await load_material_source(session, document_id, principal=principal)
    source.check_page(page_number)
    provenance = await session.scalar(
        select(PageTextCandidateModel.provenance)
        .join(
            PageReviewStateModel,
            and_(
                PageReviewStateModel.current_candidate_id == PageTextCandidateModel.id,
                PageReviewStateModel.document_id == PageTextCandidateModel.document_id,
                PageReviewStateModel.page_number == PageTextCandidateModel.page_number,
            ),
        )
        .where(
            PageReviewStateModel.document_id == document_id,
            PageReviewStateModel.page_number == page_number,
        )
    )
    metadata = provenance.get("page_image") if provenance is not None else None
    if provenance is not None and "page_image" in provenance:
        if (
            not isinstance(metadata, dict)
            or not metadata
            or metadata.get("failure_code") is not None
            or metadata.get("page_number") != page_number
            or provenance.get("source_checksum_sha256") != source.checksum_sha256
        ):
            raise PageImageError("source_page_image_metadata_invalid")
        if "artifact" in metadata:
            if artifacts is None:
                raise PageImageError("source_page_image_artifact_unavailable")
            data = await anyio.to_thread.run_sync(
                lambda: artifacts.read(metadata, source=source, page_number=page_number)
            )
            return PageImageContent(data, "persisted")
    data = await anyio.to_thread.run_sync(lambda: _fallback_image(storage, source, page_number))
    return PageImageContent(data, "original-render")


def _main() -> None:
    try:
        pymupdf.set_messages(fd=2)
        pymupdf.set_log(fd=2)
        source_fd, output_fd, page_number = (int(argument) for argument in sys.argv[1:4])
        limits = PageImageLimits(**json.loads(sys.argv[4]))
        expected_count = json.loads(sys.argv[5])
        result = _render_worker(source_fd, output_fd, page_number, limits, expected_count)
    except PageImageError as error:
        result = {"code": error.code}
    except Exception:
        result = {"code": "source_page_image_unavailable"}
    os.write(1, json.dumps(result).encode())


if __name__ == "__main__":
    _main()
