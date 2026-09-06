import asyncio
import hashlib
import io
import json
import os
import resource
import runpy
import stat
import struct
import sys
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, Never, cast
from uuid import UUID

import pymupdf
import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import Settings, StorageBackend
from exam_guru_api.documents import page_images, page_reading
from exam_guru_api.documents.page_images import (
    MAX_IMAGE_METADATA_BYTES,
    STREAM_CHUNK_BYTES,
    PageImageArtifacts,
    PageImageError,
    PageImageLimits,
    SourceCandidateImageMetadata,
    SourceImageIdentity,
    create_page_image_artifacts,
    image_artifact_id,
    iter_original,
    open_verified_original,
    parse_single_range,
    render_page_image,
)
from exam_guru_api.documents.page_reading import FilePageReader, PageReadingConfiguration
from exam_guru_api.documents.tesseract_ocr import RenderedPageImage
from exam_guru_api.infrastructure.object_storage import (
    InvalidObjectKeyError,
    LocalFileObjectStorage,
)
from exam_guru_api.infrastructure.private_artifacts import ARTIFACT_CHUNK_BYTES
from tests.test_tesseract_file_input import FileCommandRunner, file_config, source_pdf

DOCUMENT_ID = UUID(int=98101)


def image_source(path: Path, *, pages: int | None = 1) -> SourceImageIdentity:
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return SourceImageIdentity(
        document_id=DOCUMENT_ID,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        size_bytes=path.stat().st_size,
        page_count=pages,
        filename="source.pdf",
    )


def png_bytes(
    *, width: int = 32, height: int = 24, noisy: bool = False, row_filter: int = 0
) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = os.urandom(width * height * 3) if noisy else b"\xff" * width * height * 3
    rows = b"".join(
        bytes([row_filter]) + pixels[index : index + width * 3]
        for index in range(0, len(pixels), width * 3)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def rendered_fixture(path: Path, *, width: int = 32, height: int = 24) -> RenderedPageImage:
    data = png_bytes(width=width, height=height, noisy=width > 1000)
    path.write_bytes(data)
    return RenderedPageImage(1, path, hashlib.sha256(data).hexdigest(), width, height, 144)


class BoundedSource:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.reads: list[int] = []

    def fileno(self) -> int:
        return self.stream.fileno()

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= STREAM_CHUNK_BYTES
        self.reads.append(size)
        return self.stream.read(size)


class FileSourceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.opens = 0
        self.closes = 0
        self.stream: BoundedSource | None = None

    @contextmanager
    def open_source(self, key: str) -> Iterator[BinaryIO]:
        assert key.startswith("sources/")
        self.opens += 1
        try:
            with self.path.open("rb") as stream:
                self.stream = BoundedSource(stream)
                yield cast(BinaryIO, self.stream)
        finally:
            self.closes += 1

    def get_bytes(self, key: str) -> bytes:
        raise AssertionError("whole-source reads are forbidden")


def test_page_image_artifact_is_immutable_private_chunked_and_bound_to_exact_source(
    tmp_path: Path,
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png", width=1400, height=1100)
    artifacts = PageImageArtifacts(root=tmp_path / "fidelity-page-images")
    metadata = artifacts.persist(image, source=source)
    again = artifacts.persist(image, source=source)
    parsed = SourceCandidateImageMetadata.model_validate(metadata)
    assert metadata == again
    assert parsed.artifact.id == str(image_artifact_id(image.sha256))
    assert parsed.artifact.size_bytes > ARTIFACT_CHUNK_BYTES
    assert len(parsed.artifact.chunk_sha256) == 2
    assert metadata["source_checksum_sha256"] == source.checksum_sha256
    assert metadata["source_size_bytes"] == source.size_bytes
    assert metadata["document_id"] == str(DOCUMENT_ID)
    assert metadata["page_number"] == 1
    assert "path" not in json.dumps(metadata)
    directory = tmp_path / "fidelity-page-images" / image_artifact_id(image.sha256).hex
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())
    original = image.path.read_bytes()
    image.path.unlink()
    assert artifacts.read(metadata, source=source, page_number=1) == original
    with pytest.raises(PageImageError, match="source_page_image_metadata_invalid"):
        artifacts.read(metadata, source=replace(source, document_id=UUID(int=2)), page_number=1)
    with pytest.raises(PageImageError, match="source_page_image_metadata_invalid"):
        artifacts.read(metadata, source=source, page_number=2)


@pytest.mark.parametrize("damage", ["missing", "bytes", "size", "symlink", "permissions"])
def test_persisted_artifact_damage_is_never_treated_as_a_cache_miss(
    tmp_path: Path, damage: str
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png")
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    metadata = artifacts.persist(image, source=source)
    chunk = tmp_path / "images" / image_artifact_id(image.sha256).hex / "0000000000000000.chunk"
    if damage == "missing":
        chunk.unlink()
    elif damage == "bytes":
        with chunk.open("r+b") as stream:
            stream.write(b"wrong")
    elif damage == "size":
        with chunk.open("ab") as stream:
            stream.write(b"x")
    elif damage == "symlink":
        chunk.unlink()
        chunk.symlink_to(image.path)
    else:
        chunk.chmod(0o644)
    with pytest.raises(PageImageError, match="source_page_image_artifact_unavailable"):
        artifacts.read(metadata, source=source, page_number=1)


@pytest.mark.parametrize("damage", ["symlink_root", "root_mode", "owner"])
def test_artifact_directory_security_is_not_weakened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png")
    root = tmp_path / "images"
    if damage == "symlink_root":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        root.symlink_to(other, target_is_directory=True)
    else:
        root.mkdir(mode=0o755 if damage == "root_mode" else 0o700)
    if damage == "owner":
        actual = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: actual + 1)
    with pytest.raises(PageImageError):
        PageImageArtifacts(root=root).persist(image, source=source)


@pytest.mark.parametrize("damage", ["empty", "not_png", "truncated", "dimensions", "hash"])
def test_invalid_rendered_png_cannot_be_persisted(tmp_path: Path, damage: str) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png")
    if damage in {"empty", "not_png", "truncated"}:
        content = {"empty": b"", "not_png": b"not an image", "truncated": png_bytes()[:-12]}[damage]
        image.path.write_bytes(content)
        image = replace(image, sha256=hashlib.sha256(content).hexdigest())
    elif damage == "dimensions":
        image = replace(image, width=400)
    else:
        image = replace(image, sha256="0" * 64)
    with pytest.raises(PageImageError):
        PageImageArtifacts(root=tmp_path / "images").persist(image, source=source)


def test_image_metadata_has_a_64_kib_limit_and_content_derived_reference(tmp_path: Path) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png")
    metadata = PageImageArtifacts(root=tmp_path / "images").persist(image, source=source)
    oversized = {**metadata, "untrusted": "x" * MAX_IMAGE_METADATA_BYTES}
    with pytest.raises(ValidationError):
        SourceCandidateImageMetadata.model_validate(oversized)
    parsed = SourceCandidateImageMetadata.model_validate(metadata)
    wrong = {
        **metadata,
        "artifact": {**parsed.artifact.model_dump(mode="json"), "id": str(UUID(int=1))},
    }
    with pytest.raises(ValidationError):
        SourceCandidateImageMetadata.model_validate(wrong)
    wrong = {**metadata, "source_object_key": "../../outside.pdf"}
    with pytest.raises(ValidationError):
        SourceCandidateImageMetadata.model_validate(wrong)


def test_render_page_1001_is_file_backed_exact_and_not_a_thousand_page_cap(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "1001-pages.pdf", pages=1001)
    source = image_source(path, pages=1001)
    store = FileSourceStore(path)
    with open_verified_original(store, source) as stream:
        with render_page_image(stream, 1001, expected_page_count=1001) as image:
            assert image.page_number == 1001
            result = image.path.read_bytes()
            with pymupdf.open(path) as pdf:
                expected = (
                    pdf[1000]
                    .get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csRGB, alpha=False)
                    .tobytes("png")
                )
                first = (
                    pdf[0]
                    .get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csRGB, alpha=False)
                    .tobytes("png")
                )
            assert result == expected
            assert result != first
            assert image.sha256 == hashlib.sha256(result).hexdigest()
        assert not image.path.exists()
    assert store.opens == store.closes == 1


@pytest.mark.parametrize(
    ("width", "height", "limits"),
    [
        (500, 500, PageImageLimits(max_pixels=100)),
        (10000, 10, PageImageLimits(max_dimension=1000)),
        (50, 50, PageImageLimits(max_png_bytes=32)),
    ],
)
def test_render_bounds_reject_oversized_pages_and_output(
    tmp_path: Path, width: int, height: int, limits: PageImageLimits
) -> None:
    path = tmp_path / "bounded.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page(width=width, height=height)
        pdf.save(path)
    with (
        path.open("rb") as source,
        pytest.raises(PageImageError),
        render_page_image(source, 1, limits=limits),
    ):
        pytest.fail("oversized raster must not escape")


def test_render_deadline_and_concurrency_fail_closed_and_release_slots(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    with path.open("rb") as source:
        with (
            pytest.raises(PageImageError, match="source_page_image_timeout"),
            render_page_image(source, 1, limits=PageImageLimits(timeout_seconds=0.001)),
        ):
            pytest.fail("renderer exceeded its deadline")
        assert page_images._RENDER_SLOTS.acquire(blocking=False)
        assert page_images._RENDER_SLOTS.acquire(blocking=False)
        try:
            with (
                pytest.raises(PageImageError, match="source_page_image_busy"),
                render_page_image(source, 1),
            ):
                pytest.fail("renderer exceeded concurrency")
        finally:
            page_images._RENDER_SLOTS.release()
            page_images._RENDER_SLOTS.release()
        with render_page_image(source, 1) as image:
            assert image.width > 0


def test_source_metadata_size_key_and_content_type_are_verified_before_streaming(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    store = FileSourceStore(path)
    with (
        pytest.raises(PageImageError, match="source_original_unavailable"),
        open_verified_original(store, replace(source, size_bytes=source.size_bytes + 1)),
    ):
        pytest.fail("metadata mismatch must fail before headers")
    assert store.opens == store.closes == 1
    with pytest.raises(PageImageError, match="source_original_unavailable"):
        replace(source, object_key=f"sources/00/{'0' * 64}.pdf")
    with pytest.raises(PageImageError, match="source_original_unavailable"):
        replace(source, object_key="/etc/passwd")


def test_large_sparse_original_streams_in_bounded_reads_without_pdf_bytes_buffer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large.pdf"
    size = 1024**3 + 27
    with path.open("wb") as stream:
        stream.write(b"%PDF-1.7\n")
        stream.truncate(size)
        stream.seek(size - 9)
        stream.write(b"\n%%EOF\n\n\n")
    source = SourceImageIdentity(
        DOCUMENT_ID, "a" * 64, f"sources/aa/{'a' * 64}.pdf", size, None, "large.pdf"
    )
    store = FileSourceStore(path)
    with open_verified_original(store, source) as stream:
        pieces = iter_original(stream, start=size - 9, length=9)
        assert b"".join(pieces) == b"\n%%EOF\n\n\n"
        for index, piece in enumerate(iter_original(stream, start=0, length=size)):
            assert len(piece) <= STREAM_CHUNK_BYTES
            if index == 2:
                break
    assert store.closes == 1
    assert store.stream is not None
    assert max(store.stream.reads) == STREAM_CHUNK_BYTES
    assert store.stream.stream.closed


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("bytes=0-4", (0, 4)),
        ("bytes=7-", (7, 9)),
        ("bytes=-4", (6, 9)),
        ("bytes=0-999", (0, 9)),
    ],
)
def test_single_range_validation(header: str | None, expected: tuple[int, int] | None) -> None:
    assert parse_single_range(header, 10) == expected


@pytest.mark.parametrize(
    "header",
    [
        "bytes=10-",
        "bytes=4-3",
        "bytes=-0",
        "bytes=0-1,3-4",
        "bytes=",
        "items=1-2",
        "bytes=-",
        "bytes=" + "9" * 1000 + "-",
    ],
)
def test_invalid_ranges_are_unsatisfiable(header: str) -> None:
    with pytest.raises(PageImageError) as error:
        parse_single_range(header, 10)
    assert error.value.status_code == 416


def test_factory_uses_a_separate_namespace_without_touching_the_filesystem(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, environment="test", storage_root=str(tmp_path / "data"))
    artifacts = create_page_image_artifacts(settings)
    assert artifacts is not None
    assert not (tmp_path / "data").exists()
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    metadata = artifacts.persist(rendered_fixture(tmp_path / "page.png"), source=source)
    assert (tmp_path / "data" / "fidelity-page-images").is_dir()
    assert artifacts.read(metadata, source=source, page_number=1)


def test_local_storage_integrity_failure_never_returns_original_bytes(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    store = LocalFileObjectStorage(root=tmp_path / "storage", max_object_bytes=1024 * 1024)
    try:
        store.put_immutable(source.object_key, path.read_bytes(), content_type="application/pdf")
        with (tmp_path / "storage" / source.object_key).open("r+b") as stream:
            stream.seek(20)
            stream.write(b"tampered")
        with (
            pytest.raises(PageImageError, match="source_original_unavailable"),
            open_verified_original(store, source),
        ):
            pytest.fail("unverified source must not escape")
    finally:
        store.close()


def test_clean_native_render_is_persisted_without_ocr_or_text_rewriting(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "native.pdf")
    source = image_source(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    runner = FileCommandRunner(tmp_path / "models")
    observed: list[Path] = []

    def capture(image: RenderedPageImage) -> dict[str, object]:
        observed.append(image.path)
        return artifacts.persist(image, source=source)

    reader = FilePageReader(command_runner=runner, on_render=capture)
    with (
        path.open("rb") as stream,
        reader.open(
            stream, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as opened,
    ):
        result = opened.read_page(1)
    assert result.failure_code is None
    assert [candidate.method for candidate in result.candidates] == ["native"]
    assert "Read the question" in result.candidates[0].raw_text
    assert not runner.calls
    assert len(observed) == 1
    assert not observed[0].exists()
    metadata = result.candidates[0].provenance["page_image"]
    assert isinstance(metadata, dict)
    assert artifacts.read(metadata, source=source, page_number=1)
    assert result.candidates[0].provenance["automatic_verification"] is False


def test_ocr_image_artifact_is_exactly_the_raster_passed_to_ocr(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf", font_name="FMAbhaya")
    source = image_source(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    runner = FileCommandRunner(tmp_path / "models")
    reader = FilePageReader(command_runner=runner, on_render=artifacts.observer(source))
    with (
        path.open("rb") as stream,
        reader.open(
            stream, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as opened,
    ):
        result = opened.read_page(1)
    assert result.failure_code is None
    native, ocr = result.candidates
    assert native.raw_text.strip()
    assert ocr.raw_text == "Question"
    metadata = ocr.provenance["page_image"]
    assert isinstance(metadata, dict)
    assert metadata["sha256"] == runner.image_hashes[0]
    assert (
        hashlib.sha256(artifacts.read(metadata, source=source, page_number=1)).hexdigest()
        == runner.image_hashes[0]
    )
    assert ocr.provenance["automatic_verification"] is False


@pytest.mark.parametrize("ocr", [False, True])
def test_observer_failure_preserves_all_candidate_text_and_records_untrusted_failure(
    tmp_path: Path, ocr: bool
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")

    def fail(_image: RenderedPageImage) -> dict[str, object]:
        raise OSError("PRIVATE SOURCE PATH")

    reader = FilePageReader(command_runner=runner, on_render=fail)
    with (
        path.open("rb") as stream,
        reader.open(
            stream,
            configuration=PageReadingConfiguration(ocr=file_config(runner.models), force_ocr=ocr),
        ) as opened,
    ):
        result = opened.read_page(1)
    assert result.failure_code == "page_image_artifact_unavailable"
    assert result.candidates[0].raw_text.strip()
    if ocr:
        assert result.candidates[-1].raw_text == "Question"
    assert "PRIVATE" not in repr(result)
    metadata = result.candidates[-1].provenance["page_image"]
    assert isinstance(metadata, dict)
    assert metadata["failure_code"] == "page_image_artifact_unavailable"


def test_native_blocks_record_bounded_hashes_and_only_proven_character_offsets(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    with (
        path.open("rb") as source,
        FilePageReader().open(source, configuration=PageReadingConfiguration()) as opened,
    ):
        candidate = opened.read_page(1).candidates[0]
    layout = candidate.provenance["blocks"]
    assert isinstance(layout, dict)
    assert len(json.dumps(layout).encode()) <= 16384
    entries = layout["items"]
    assert isinstance(entries, list)
    assert entries
    for index, block in enumerate(entries):
        assert block["reading_order"] == index
        assert "text" not in block
        assert len(block["sha256"]) == 64
        assert len(block["bbox"]) == 4
        if block["character_start"] is not None:
            text = candidate.raw_text[block["character_start"] : block["character_end"]]
            assert hashlib.sha256(text.encode()).hexdigest() == block["sha256"]
        else:
            assert block["offset_status"] == "unknown"


def test_native_layout_disagreement_does_not_invent_alignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    extract = page_reading.extract_native_page

    def disagree(document: pymupdf.Document, number: int) -> page_reading.NativePageText:
        return replace(extract(document, number), raw_text="A different raw text view")

    monkeypatch.setattr(page_reading, "extract_native_page", disagree)
    with (
        path.open("rb") as source,
        FilePageReader().open(source, configuration=PageReadingConfiguration()) as opened,
    ):
        candidate = opened.read_page(1).candidates[0]
    blocks = candidate.provenance["blocks"]
    assert isinstance(blocks, dict)
    assert all(
        block["character_start"] is None for block in cast(list[dict[str, object]], blocks["items"])
    )
    assert candidate.raw_text == "A different raw text view"


def test_render_worker_uses_explicit_cpu_memory_and_output_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "worker.pdf")
    limits = PageImageLimits()
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: calls.append((kind, value)))
    with path.open("rb") as source, (tmp_path / "output.png").open("w+b") as output:
        metadata = page_images._render_worker(source.fileno(), output.fileno(), 1, limits, 1)
    assert isinstance(metadata["width"], int)
    assert metadata["width"] > 0
    assert calls == [
        (resource.RLIMIT_AS, (limits.max_memory_bytes, limits.max_memory_bytes)),
        (resource.RLIMIT_CPU, (20, 20)),
        (resource.RLIMIT_FSIZE, (limits.max_png_bytes, limits.max_png_bytes)),
    ]


def test_png_with_valid_hash_crc_and_deflate_but_invalid_row_filter_is_rejected(
    tmp_path: Path,
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png")
    data = png_bytes(row_filter=255)
    image.path.write_bytes(data)
    image = replace(image, sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(PageImageError, match="source_page_image_invalid"):
        PageImageArtifacts(root=tmp_path / "images").persist(image, source=source)


def test_stream_read_failure_is_sanitized_without_emitting_unverified_bytes(tmp_path: Path) -> None:
    class BrokenStream(BoundedSource):
        def read(self, size: int = -1) -> bytes:
            raise OSError("PRIVATE SOURCE PATH")

    path = source_pdf(tmp_path / "source.pdf")
    with path.open("rb") as stream:
        broken = BrokenStream(stream)
        with pytest.raises(PageImageError, match="source_original_unavailable") as error:
            next(iter_original(cast(BinaryIO, broken), start=0, length=5))
        assert "PRIVATE" not in str(error.value)


def test_render_timeout_kills_and_reaps_the_child_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def sleeping_child(*_args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await original(
            sys.executable, "-I", "-c", "import time; time.sleep(30)", **kwargs
        )
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", sleeping_child)
    path = source_pdf(tmp_path / "source.pdf")
    with (
        path.open("rb") as stream,
        pytest.raises(PageImageError, match="source_page_image_timeout"),
        render_page_image(stream, 1, limits=PageImageLimits(timeout_seconds=0.2)),
    ):
        pytest.fail("sleeping rasterizer must be stopped")
    assert len(children) == 1
    assert children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


def test_hundreds_of_megabytes_pdf_is_rendered_from_a_seekable_file_not_a_byte_buffer(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "large.pdf")
    startxref = path.read_bytes().rsplit(b"startxref\n", 1)[1].splitlines()[0]
    trailer = b"\nstartxref\n" + startxref + b"\n%%EOF\n"
    size = 300 * 1024 * 1024
    with path.open("r+b") as stream:
        stream.truncate(size)
        stream.seek(size - len(trailer))
        stream.write(trailer)
    identity = image_source(path)
    store = FileSourceStore(path)
    with (
        open_verified_original(store, identity) as verified_stream,
        render_page_image(verified_stream, 1, expected_page_count=1) as image,
    ):
        assert image.width > 0
        assert image.page_number == 1
    assert store.opens == store.closes == 1
    assert store.stream is not None
    assert store.stream.reads == []


@pytest.mark.parametrize(
    ("number", "count", "code"),
    [(2, None, "source_page_not_found"), (1, 2, "source_page_count_mismatch")],
)
def test_actual_pdf_page_count_is_checked_even_without_a_persisted_count(
    tmp_path: Path, number: int, count: int | None, code: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    with (
        path.open("rb") as stream,
        pytest.raises(PageImageError, match=code),
        render_page_image(stream, number, expected_page_count=count),
    ):
        pytest.fail("wrong page/count must fail")


def test_layout_is_bounded_and_records_ocr_offsets_without_copying_text() -> None:
    texts = [f"Block {index} " * 10 for index in range(1000)]
    raw = "\n".join(texts)
    layout = page_images.block_provenance(
        raw, [(text, None) for text in texts], separator="\n", coordinate_space="image_pixels"
    )
    assert len(json.dumps(layout).encode()) <= 16 * 1024
    assert layout["total_count"] == 1000
    assert layout["truncated"] is True
    items = cast(list[dict[str, Any]], layout["items"])
    assert 0 < len(items) < 256
    for item in items:
        value = raw[item["character_start"] : item["character_end"]]
        assert hashlib.sha256(value.encode()).hexdigest() == item["sha256"]
        assert "text" not in item


def test_render_observer_cannot_substitute_metadata_for_a_different_image(tmp_path: Path) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "first.png")
    other = rendered_fixture(tmp_path / "second.png", width=33)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    substitute = artifacts.persist(other, source=source)
    metadata, failure = page_images.observe_page_image(image, lambda _image: substitute)
    assert failure == "page_image_artifact_unavailable"
    assert metadata["sha256"] == image.sha256
    assert "artifact" not in metadata


@pytest.mark.parametrize(
    "change",
    [
        {"dpi": 0},
        {"dpi": True},
        {"max_pixels": 40_000_001},
        {"max_dimension": 16_001},
        {"max_png_bytes": 32 * 1024 * 1024 + 1},
        {"max_memory_bytes": 0},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"timeout_seconds": True},
        {"timeout_seconds": "20"},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
    ],
)
def test_invalid_raster_limits_fail_before_process_creation(change: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="invalid page image resource limits"):
        PageImageLimits(**cast(Any, change))


@pytest.mark.parametrize("checksum", [None, "", "A" * 64, "0" * 63, "g" * 64])
def test_artifact_identity_requires_a_complete_lowercase_checksum(checksum: object) -> None:
    with pytest.raises(ValueError, match="invalid image checksum"):
        image_artifact_id(cast(str, checksum))


def test_source_key_policy_rejection_is_sanitized_at_the_identity_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    calls: list[str] = []

    def reject(key: str) -> Never:
        calls.append(key)
        raise InvalidObjectKeyError(key)

    monkeypatch.setattr(page_images, "validate_source_object_key", reject)
    with pytest.raises(PageImageError, match="source_original_unavailable"):
        replace(source)
    assert calls == [source.object_key]


@pytest.mark.parametrize("metadata", [None, [], {"invalid": object()}, {"invalid": float("nan")}])
def test_image_metadata_rejects_non_objects_and_non_json_values(metadata: object) -> None:
    with pytest.raises(ValidationError):
        SourceCandidateImageMetadata.model_validate(metadata)


def test_circular_image_metadata_fails_validation_instead_of_recursing_forever() -> None:
    metadata: dict[str, object] = {}
    metadata["cycle"] = metadata
    with pytest.raises(ValidationError, match="invalid page image metadata"):
        SourceCandidateImageMetadata.model_validate(metadata)


@pytest.mark.parametrize("size", [0, -1, True, 32 * 1024 * 1024 + 1])
def test_bounded_png_reader_rejects_invalid_lengths_without_reading(size: int) -> None:
    source = io.BytesIO(b"png")
    with pytest.raises(PageImageError, match="source_page_image_invalid"):
        page_images._read_bounded(source, size)
    assert source.tell() == 0


@pytest.mark.parametrize("value", [b"", b"too many bytes", "not bytes", bytearray(b"abc")])
def test_png_reader_rejects_short_overlong_or_wrongly_typed_provider_reads(value: object) -> None:
    class BadReader:
        def read(self, size: int) -> bytes:
            assert 0 < size <= STREAM_CHUNK_BYTES
            return cast(bytes, value)

    with pytest.raises(PageImageError, match="source_page_image_invalid"):
        page_images._read_bounded(cast(BinaryIO, BadReader()), 3)


def test_png_reader_rejects_extra_bytes_after_declared_end() -> None:
    with pytest.raises(PageImageError, match="source_page_image_invalid"):
        page_images._read_bounded(io.BytesIO(b"1234"), 3)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _damaged_png(damage: str) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 24, 8, 2, 0, 0, 0))
    pixels = b"\x00" * (24 * (32 * 3 + 1))
    compressed = zlib.compress(pixels)
    data = _png_chunk(b"IDAT", compressed)
    end = _png_chunk(b"IEND", b"")
    ancillary = _png_chunk(b"tEXt", b"kind\x00fixture")
    variants = {
        "short_chunk_header": signature + b"\x00" * 11,
        "truncated_chunk": signature + header[:-1],
        "checksum": signature + header + data + end[:-1] + bytes([end[-1] ^ 1]),
        "missing_header": signature + data + end,
        "header_length": signature + _png_chunk(b"IHDR", b"") + data + end,
        "zero_width": signature
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 24, 8, 2, 0, 0, 0))
        + data
        + end,
        "unsupported_color": signature
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 24, 8, 6, 0, 0, 0))
        + data
        + end,
        "duplicate_header": signature + header + header + data + end,
        "interrupted_idat": signature
        + header
        + _png_chunk(b"IDAT", compressed[:5])
        + ancillary
        + _png_chunk(b"IDAT", compressed[5:])
        + end,
        "expanded_overflow": signature
        + header
        + _png_chunk(b"IDAT", zlib.compress(pixels + b"\x00"))
        + end,
        "trailing_compressed_data": signature
        + header
        + _png_chunk(b"IDAT", compressed + b"PRIVATE")
        + end,
        "invalid_deflate": signature + header + _png_chunk(b"IDAT", b"PRIVATE") + end,
        "incomplete_deflate": signature + header + _png_chunk(b"IDAT", compressed[:-2]) + end,
        "short_pixels": signature + header + _png_chunk(b"IDAT", zlib.compress(pixels[:-1])) + end,
        "iend_payload": signature + header + data + _png_chunk(b"IEND", b"bad"),
        "missing_idat": signature + header + end,
        "trailing_png_data": signature + header + data + end + b"PRIVATE",
        "unknown_critical_chunk": signature + header + _png_chunk(b"ABCD", b"") + data + end,
        "ancillary_after_idat": signature + header + data + ancillary + end,
    }
    return variants[damage]


@pytest.mark.parametrize(
    "damage",
    [
        "short_chunk_header",
        "truncated_chunk",
        "checksum",
        "missing_header",
        "header_length",
        "zero_width",
        "unsupported_color",
        "duplicate_header",
        "interrupted_idat",
        "expanded_overflow",
        "trailing_compressed_data",
        "invalid_deflate",
        "incomplete_deflate",
        "short_pixels",
        "iend_payload",
        "missing_idat",
        "trailing_png_data",
        "unknown_critical_chunk",
    ],
)
def test_structurally_corrupt_png_never_becomes_a_durable_artifact(
    tmp_path: Path, damage: str
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    path = tmp_path / "page.png"
    data = _damaged_png(damage)
    path.write_bytes(data)
    image = RenderedPageImage(1, path, hashlib.sha256(data).hexdigest(), 32, 24, 144)
    with pytest.raises(PageImageError, match="source_page_image_invalid") as error:
        PageImageArtifacts(root=tmp_path / "images").persist(image, source=source)
    assert "PRIVATE" not in str(error.value)
    assert not (tmp_path / "images").exists()


def test_valid_png_ancillary_metadata_after_image_data_is_supported(tmp_path: Path) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    path = tmp_path / "page.png"
    data = _damaged_png("ancillary_after_idat")
    path.write_bytes(data)
    image = RenderedPageImage(1, path, hashlib.sha256(data).hexdigest(), 32, 24, 144)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    metadata = artifacts.persist(image, source=source)
    assert artifacts.read(metadata, source=source, page_number=1) == data


def test_image_changed_during_capture_is_rejected_and_input_descriptor_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = rendered_fixture(tmp_path / "page.png")
    read = page_images._read_bounded
    descriptors: list[int] = []

    def changed(stream: BinaryIO, size: int) -> bytes:
        descriptors.append(stream.fileno())
        data = read(stream, size)
        details = image.path.stat()
        os.utime(image.path, ns=(details.st_atime_ns, details.st_mtime_ns + 1))
        return data

    monkeypatch.setattr(page_images, "_read_bounded", changed)
    with pytest.raises(PageImageError, match="source_page_image_invalid"):
        page_images.read_rendered_image(image)
    assert len(descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("damage", ["missing", "symlink", "checksum_type"])
def test_capture_file_errors_and_invalid_hash_types_fail_closed(
    tmp_path: Path, damage: str
) -> None:
    image = rendered_fixture(tmp_path / "page.png")
    if damage == "checksum_type":
        image = replace(image, sha256=cast(str, 123))
    else:
        image.path.unlink()
        if damage == "symlink":
            other = rendered_fixture(tmp_path / "other.png")
            image.path.symlink_to(other.path)
    with pytest.raises(PageImageError, match="source_page_image_invalid") as error:
        page_images.read_rendered_image(image)
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("damage", ["chunk_hash", "dimensions"])
def test_complete_png_hash_does_not_override_invalid_chunk_or_dimension_provenance(
    tmp_path: Path, damage: str
) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    image = rendered_fixture(tmp_path / "page.png")
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    metadata = artifacts.persist(image, source=source)
    if damage == "chunk_hash":
        cast(dict[str, object], metadata["artifact"])["chunk_sha256"] = ["0" * 64]
    else:
        metadata["width"] = image.width + 1
    with pytest.raises(PageImageError, match="source_page_image_artifact_unavailable"):
        artifacts.read(metadata, source=source, page_number=1)


def test_unavailable_layout_is_explicitly_unknown_and_never_echoes_parser_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")

    def fail(*_args: Any, **_kwargs: Any) -> Never:
        raise RuntimeError("PRIVATE layout data")

    with pymupdf.open(path) as document:
        monkeypatch.setattr(pymupdf.Page, "get_text", fail)
        metadata = page_images.native_block_provenance(document, 1, "raw text")
    assert metadata == {
        "view": "blocks",
        "offset_status": "unknown",
        "failure_code": "native_layout_unavailable",
        "items": [],
    }
    assert "PRIVATE" not in repr(metadata)


def test_optional_remote_storage_has_no_hidden_local_image_store(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, environment="test", storage_root=str(tmp_path / "unused"))
    remote = settings.model_copy(update={"storage_backend": StorageBackend.S3})
    assert create_page_image_artifacts(remote) is None
    assert not (tmp_path / "unused").exists()


def test_original_stream_rejects_memory_only_adapter_and_closes_its_context(tmp_path: Path) -> None:
    source = image_source(source_pdf(tmp_path / "source.pdf"))
    memory = io.BytesIO(b"%PDF-fixture")

    class MemoryStorage:
        @contextmanager
        def open_source(self, key: str) -> Iterator[BinaryIO]:
            assert key == source.object_key
            with memory:
                yield memory

    with (
        pytest.raises(PageImageError, match="source_original_unavailable"),
        open_verified_original(MemoryStorage(), source),
    ):
        pytest.fail("memory-only adapters cannot enable a bytes fallback")
    assert memory.closed


@pytest.mark.parametrize(("start", "length"), [(-1, 5), (0, 0), (0, 2**63), (2**63, 1), (True, 5)])
def test_invalid_original_slice_is_rejected_before_reading(
    tmp_path: Path, start: int, length: int
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    with path.open("rb") as source:
        wrapped = BoundedSource(source)
        with pytest.raises(PageImageError, match="source_original_unavailable"):
            iter_original(cast(BinaryIO, wrapped), start=start, length=length)
        assert wrapped.reads == []


def test_original_slice_seek_failure_is_sanitized(tmp_path: Path) -> None:
    class BrokenSeek(BoundedSource):
        def seek(self, offset: int, whence: int = 0) -> int:
            raise OSError("PRIVATE source descriptor")

    path = source_pdf(tmp_path / "source.pdf")
    with (
        path.open("rb") as source,
        pytest.raises(PageImageError, match="source_original_unavailable") as error,
    ):
        iter_original(cast(BinaryIO, BrokenSeek(source)), start=0, length=5)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("count", "source_page_count_mismatch"),
        ("page", "source_page_not_found"),
        ("pre_raster", "source_page_image_raster_limit"),
        ("post_raster", "source_page_image_raster_limit"),
        ("empty_png", "source_page_image_png_limit"),
        ("png_limit", "source_page_image_png_limit"),
        ("write_progress", "source_page_image_unavailable"),
    ],
)
def test_render_worker_failure_boundaries_leave_no_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, code: str
) -> None:
    path = source_pdf(tmp_path / "worker.pdf")
    limits = (
        PageImageLimits(max_pixels=1)
        if failure == "pre_raster"
        else PageImageLimits(max_png_bytes=1)
        if failure == "png_limit"
        else PageImageLimits()
    )
    limit_calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(
        resource, "setrlimit", lambda kind, value: limit_calls.append((kind, value))
    )
    if failure in {"post_raster", "empty_png"}:
        pixmap = SimpleNamespace(
            width=16_001 if failure == "post_raster" else 360, height=360, tobytes=lambda _kind: b""
        )
        monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_args, **_kwargs: pixmap)
    with path.open("rb") as source, (tmp_path / "output.png").open("w+b") as output:
        if failure == "write_progress":
            descriptor = output.fileno()
            write = os.write
            monkeypatch.setattr(
                os, "write", lambda fd, data: 0 if fd == descriptor else write(fd, data)
            )
        with pytest.raises(PageImageError, match=code):
            page_images._render_worker(
                source.fileno(),
                output.fileno(),
                2 if failure == "page" else 1,
                limits,
                2 if failure == "count" else None,
            )
        assert os.fstat(output.fileno()).st_size == 0
        assert not source.closed
    assert limit_calls == [
        (resource.RLIMIT_AS, (limits.max_memory_bytes, limits.max_memory_bytes)),
        (resource.RLIMIT_CPU, (20, 20)),
        (resource.RLIMIT_FSIZE, (limits.max_png_bytes, limits.max_png_bytes)),
    ]


class _RendererProcess:
    def __init__(self, payload: bytes, *, missing_stdout: bool, exit_code: int) -> None:
        self.stdout = None if missing_stdout else asyncio.StreamReader()
        if self.stdout is not None:
            self.stdout.feed_data(payload)
            self.stdout.feed_eof()
        self.exit_code = exit_code
        self.returncode: int | None = None
        self.kills = 0
        self.waits = 0

    def kill(self) -> None:
        self.kills += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.waits += 1
        if self.returncode is None:
            self.returncode = self.exit_code
        return self.returncode


@pytest.mark.parametrize(
    ("failure", "payload"),
    [
        ("spawn", b""),
        ("stdout", b""),
        ("overflow", b"x" * 4097),
        ("exit", b"{}"),
        ("malformed_json", b"PRIVATE output"),
        ("non_object", b"[]"),
        ("unknown_code", b'{"code":"PRIVATE output"}'),
        ("object_code", b'{"code":{}}'),
        ("list_code", b'{"code":[]}'),
    ],
)
def test_renderer_ipc_failures_are_sanitized_and_children_reaped(
    monkeypatch: pytest.MonkeyPatch, failure: str, payload: bytes
) -> None:
    children: list[_RendererProcess] = []

    async def spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        assert args[:4] == (sys.executable, "-I", "-m", "exam_guru_api.documents.page_images")
        assert kwargs["pass_fds"] == (101, 102)
        if failure == "spawn":
            raise OSError("PRIVATE process launch")
        process = _RendererProcess(
            payload, missing_stdout=failure == "stdout", exit_code=7 if failure == "exit" else 0
        )
        children.append(process)
        return cast(asyncio.subprocess.Process, process)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(PageImageError, match="source_page_image_unavailable") as error:
        asyncio.run(page_images._run_renderer(101, 102, 1, PageImageLimits(), None))
    assert "PRIVATE" not in str(error.value)
    if failure == "spawn":
        assert not children
    else:
        (child,) = children
        assert child.returncode is not None
        assert child.kills == int(failure in {"stdout", "overflow"})
        assert child.waits == (1 if failure in {"stdout", "overflow"} else 2)


@pytest.mark.parametrize("failure", ["empty", "oversized", "width", "height", "rasterizer_version"])
def test_parent_rechecks_worker_output_and_deletes_failed_image_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    paths: list[Path] = []
    payload = b"" if failure == "empty" else png_bytes()
    limits = PageImageLimits(max_png_bytes=1) if failure == "oversized" else PageImageLimits()

    async def render(
        _source: int, output: int, number: int, actual_limits: PageImageLimits, count: int | None
    ) -> dict[str, object]:
        assert number == 1
        assert count == 1
        assert actual_limits == limits
        paths.append(Path(await asyncio.to_thread(os.readlink, f"/proc/self/fd/{output}")))
        await asyncio.to_thread(os.write, output, payload)
        metadata: dict[str, object] = {
            "width": 32,
            "height": 24,
            "rasterizer_version": pymupdf.VersionBind,
        }
        if failure == "width":
            metadata["width"] = 33
        elif failure == "height":
            metadata["height"] = 25
        elif failure == "rasterizer_version":
            metadata["rasterizer_version"] = "other-version"
        return metadata

    monkeypatch.setattr(page_images, "_run_renderer", render)
    with (
        path.open("rb") as source,
        pytest.raises(PageImageError, match="source_page_image_invalid"),
        render_page_image(source, 1, limits=limits, expected_page_count=1),
    ):
        pytest.fail("unverified worker output cannot escape")
    assert len(paths) == 1
    assert not paths[0].exists()
    assert not paths[0].parent.exists()


@pytest.mark.parametrize("number", [0, -1, True, 2**31, "1"])
def test_renderer_page_argument_is_bounded_before_allocating_workspace(
    tmp_path: Path, number: object
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    with (
        path.open("rb") as source,
        pytest.raises(PageImageError, match="source_page_not_found"),
        render_page_image(source, cast(int, number)),
    ):
        pytest.fail("invalid requested page must fail before launching a process")


@pytest.mark.parametrize("failure", [None, "typed", "unexpected"])
def test_renderer_cli_returns_only_bounded_result_or_sanitized_error(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], failure: str | None
) -> None:
    requests: list[tuple[int, int, int, PageImageLimits, int | None]] = []
    diagnostic_routes: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pymupdf, "set_messages", lambda *, fd: diagnostic_routes.append(("messages", fd))
    )
    monkeypatch.setattr(pymupdf, "set_log", lambda *, fd: diagnostic_routes.append(("log", fd)))
    metadata = {"width": 32, "height": 24, "rasterizer_version": pymupdf.VersionBind}

    def worker(
        source: int, output: int, number: int, limits: PageImageLimits, count: int | None
    ) -> dict[str, object]:
        requests.append((source, output, number, limits, count))
        assert diagnostic_routes == [("messages", 2), ("log", 2)]
        if failure == "typed":
            raise PageImageError("source_page_image_raster_limit")
        if failure == "unexpected":
            raise RuntimeError("PRIVATE parser details")
        return metadata

    monkeypatch.setattr(page_images, "_render_worker", worker)
    monkeypatch.setattr(sys, "argv", ["page_images", "101", "102", "1001", "{}", "1001"])
    page_images._main()
    output = capfd.readouterr()
    assert output.err == ""
    assert "PRIVATE" not in output.out
    expected: dict[str, object] = (
        metadata
        if failure is None
        else {
            "code": "source_page_image_raster_limit"
            if failure == "typed"
            else "source_page_image_unavailable"
        }
    )
    assert json.loads(output.out) == expected
    assert requests == [(101, 102, 1001, PageImageLimits(), 1001)]


def test_module_entrypoint_rejects_invalid_cli_arguments_without_setting_resource_limits(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    limit_calls: list[tuple[object, ...]] = []

    def forbidden_limits(*args: object) -> Never:
        limit_calls.append(args)
        raise AssertionError("invalid arguments must not reach resource setup")

    diagnostic_routes: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pymupdf, "set_messages", lambda *, fd: diagnostic_routes.append(("messages", fd))
    )
    monkeypatch.setattr(pymupdf, "set_log", lambda *, fd: diagnostic_routes.append(("log", fd)))
    monkeypatch.setattr(resource, "setrlimit", forbidden_limits)
    monkeypatch.setattr(sys, "argv", ["page_images", "invalid"])
    runpy.run_path(page_images.__file__, run_name="__main__")
    output = capfd.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {"code": "source_page_image_unavailable"}
    assert not limit_calls
    assert diagnostic_routes == [("messages", 2), ("log", 2)]


def native_layer_pdf(path: Path, *, pages: int = 2) -> Path:
    with pymupdf.open() as document:
        layer = document.add_ocg("Fixture layer")
        for number in range(1, pages + 1):
            page = document.new_page(width=180, height=180)
            page.insert_text(
                (15, 30), f"Read the question and choose the answer {number}", fontsize=5, oc=layer
            )
        document.xref_set_key(document.pdf_catalog(), "OCProperties/D", "null")
        document.save(path)
    return path


def test_recoverable_mupdf_diagnostic_cannot_corrupt_renderer_json_or_change_raster(
    tmp_path: Path,
) -> None:
    path = native_layer_pdf(tmp_path / "optional-layer.pdf")
    pymupdf.TOOLS.mupdf_warnings()
    with pymupdf.open(path) as document:
        expected = (
            document[1]
            .get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csRGB, alpha=False)
            .tobytes("png")
        )
    assert "No default Layer config" in pymupdf.TOOLS.mupdf_warnings()
    with path.open("rb") as source, render_page_image(source, 2, expected_page_count=2) as image:
        assert image.page_number == 2
        assert image.path.read_bytes() == expected
        assert image.sha256 == hashlib.sha256(expected).hexdigest()
    assert not image.path.exists()


def test_waiting_for_a_render_slot_expires_within_the_existing_page_budget(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    assert page_images._RENDER_SLOTS.acquire(blocking=False)
    assert page_images._RENDER_SLOTS.acquire(blocking=False)
    try:
        with (
            path.open("rb") as source,
            pytest.raises(PageImageError, match="source_page_image_timeout"),
            render_page_image(
                source, 1, limits=PageImageLimits(timeout_seconds=0.02), wait_for_slot=True
            ),
        ):
            pytest.fail("a waiting reader must not bypass capacity or wait indefinitely")
    finally:
        page_images._RENDER_SLOTS.release()
        page_images._RENDER_SLOTS.release()


@pytest.mark.parametrize("elapsed", [5.0, 20.0, 21.0])
def test_render_slot_wait_is_deducted_from_child_timeout_without_changing_other_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elapsed: float
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    times = iter((100.0, 100.0 + elapsed))
    monkeypatch.setattr(
        page_images, "time", SimpleNamespace(monotonic=lambda: next(times)), raising=False
    )
    observed_limits: list[PageImageLimits] = []

    async def render(
        _source: int, output: int, _page: int, limits: PageImageLimits, _count: int | None
    ) -> dict[str, object]:
        observed_limits.append(limits)
        await asyncio.to_thread(os.write, output, png_bytes())
        return {"width": 32, "height": 24, "rasterizer_version": pymupdf.VersionBind}

    monkeypatch.setattr(page_images, "_run_renderer", render)
    with path.open("rb") as source:
        if elapsed < 20:
            with render_page_image(source, 1, wait_for_slot=True) as image:
                assert image.width == 32
            assert observed_limits == [replace(PageImageLimits(), timeout_seconds=20 - elapsed)]
        else:
            with (
                pytest.raises(PageImageError, match="source_page_image_timeout"),
                render_page_image(source, 1, wait_for_slot=True),
            ):
                pytest.fail("an exhausted render budget cannot launch a child")
            assert not observed_limits
    assert page_images._RENDER_SLOTS.acquire(blocking=False)
    assert page_images._RENDER_SLOTS.acquire(blocking=False)
    page_images._RENDER_SLOTS.release()
    page_images._RENDER_SLOTS.release()
