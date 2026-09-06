import errno
import hashlib
import io
import os
import secrets
import stat
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import BinaryIO, Never, cast
from unittest.mock import Mock

import boto3
import pymupdf
import pytest

from exam_guru_api.infrastructure.object_storage import (
    InvalidObjectKeyError,
    LocalFileObjectStorage,
    ObjectAlreadyExistsError,
    ObjectStorage,
    ObjectStorageOperationError,
    S3ObjectStorage,
    StoredObject,
)

CHUNK_BYTES = 1024 * 1024
LEGACY_BYTE_LIMIT = 256 * CHUNK_BYTES
MAXIMUM_SIZE = 2**63 - 1
HEADER = b"%PDF-1.7\nstreaming source fixture\n"


def source_key(data: bytes) -> str:
    checksum = hashlib.sha256(data).hexdigest()
    return f"sources/{checksum[:2]}/{checksum}.pdf"


def stream_checksum(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def sparse_source(tmp_path: Path, size: int) -> tuple[Path, str]:
    source = tmp_path / "input.pdf"
    with source.open("wb") as stream:
        stream.write(HEADER)
        stream.truncate(size)
    with source.open("rb") as stream:
        checksum = stream_checksum(stream)
    return source, f"sources/{checksum[:2]}/{checksum}.pdf"


class ReadSpy:
    def __init__(self, source: BinaryIO, *, short_reads: bool = False) -> None:
        self.source = source
        self.short_reads = short_reads
        self.amounts: list[int] = []

    def read(self, amount: int = -1) -> bytes:
        assert 0 < amount <= CHUNK_BYTES
        self.amounts.append(amount)
        return self.source.read(min(amount, 65_537) if self.short_reads else amount)


def forbid_byte_api(*_args: object, **_kwargs: object) -> Never:
    raise AssertionError("streaming operations must not use the byte API")


def forbid_byte_paths(storage: LocalFileObjectStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "get_bytes", forbid_byte_api)
    monkeypatch.setattr(storage, "put_immutable", forbid_byte_api)
    monkeypatch.setattr(storage, "_read_source_at", forbid_byte_api)


def descriptor_spies(
    monkeypatch: pytest.MonkeyPatch, *, partial_writes: bool = False
) -> tuple[list[int], list[int]]:
    real_read = os.read
    real_write = os.write
    reads: list[int] = []
    writes: list[int] = []

    def read(file_fd: int, amount: int) -> bytes:
        assert 0 < amount <= CHUNK_BYTES
        reads.append(amount)
        return real_read(file_fd, amount)

    def write(file_fd: int, data: bytes | bytearray | memoryview) -> int:
        assert 0 < len(data) <= CHUNK_BYTES
        writes.append(len(data))
        return real_write(file_fd, data[:65_537] if partial_writes else data)

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(os, "write", write)
    return reads, writes


def s3_storage(
    monkeypatch: pytest.MonkeyPatch, maximum: int = LEGACY_BYTE_LIMIT
) -> tuple[S3ObjectStorage, Mock]:
    client = Mock(spec=["get_object", "head_object", "put_object", "upload_fileobj", "close"])
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: client)
    return (
        S3ObjectStorage(
            endpoint_url="http://localhost:9000",
            access_key_id="test-access",
            secret_access_key="test-secret",
            bucket="test-bucket",
            region="us-east-1",
            max_object_bytes=maximum,
        ),
        client,
    )


def test_stream_round_trip_is_seekable_descriptor_backed_bounded_and_preserves_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = 3 * CHUNK_BYTES + 29
    original, key = sparse_source(tmp_path, size)
    original_stat = original.stat()
    root = tmp_path / "data"
    local = LocalFileObjectStorage(root=root, max_object_bytes=4 * CHUNK_BYTES)
    storage: ObjectStorage = local
    forbid_byte_paths(local, monkeypatch)
    reads, writes = descriptor_spies(monkeypatch, partial_writes=True)
    try:
        with original.open("rb") as input_stream:
            spy = ReadSpy(input_stream, short_reads=True)
            stored = storage.put_stream_immutable(
                key, cast(BinaryIO, spy), content_type="application/pdf", expected_size=size
            )
            assert not input_stream.closed
            assert input_stream.tell() == size
        checksum = key.rsplit("/", maxsplit=1)[1].removesuffix(".pdf")
        assert stored == StoredObject(key=key, checksum_sha256=checksum, size=size, etag=checksum)
        with storage.open_source(key) as source:
            assert source.seekable()
            assert source.readable()
            assert not source.writable()
            assert source.tell() == 0
            assert os.fstat(source.fileno()).st_ino == (root / key).stat().st_ino
            assert os.stat(f"/proc/self/fd/{source.fileno()}").st_size == size
            assert source.read(len(HEADER)) == HEADER
            source.seek(size - 1)
            assert source.read(2) == b"\x00"
            with pytest.raises(io.UnsupportedOperation):
                source.write(b"x")
        assert source.closed
        assert len(spy.amounts) > 3
        assert reads
        assert writes
        assert max((*spy.amounts, *reads, *writes)) <= CHUNK_BYTES
        assert original.stat().st_mtime_ns == original_stat.st_mtime_ns
        assert original.stat().st_size == original_stat.st_size
        assert original.stat().st_ino == original_stat.st_ino
        assert stat.S_IMODE((root / key).stat().st_mode) == 0o600
        assert stat.S_IMODE((root / key).parent.stat().st_mode) == 0o700
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()
    restarted = LocalFileObjectStorage(root=root, max_object_bytes=4 * CHUNK_BYTES)
    try:
        with restarted.open_source(key) as source:
            assert stream_checksum(source) == stored.checksum_sha256
    finally:
        restarted.close()


def test_verified_descriptor_supports_file_backed_pymupdf_for_over_one_thousand_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "thousand-pages.pdf"
    with pymupdf.open() as document:
        for _ in range(1_001):
            document.new_page()
        document.save(original)
    size = original.stat().st_size
    with original.open("rb") as stream:
        checksum = stream_checksum(stream)
    key = f"sources/{checksum[:2]}/{checksum}.pdf"
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=size)
    forbid_byte_paths(storage, monkeypatch)
    try:
        with original.open("rb") as stream:
            stored = storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=size
            )
        assert stored.checksum_sha256 == checksum
        with (
            storage.open_source(key) as source,
            pymupdf.open(filename=f"/proc/self/fd/{source.fileno()}", filetype="pdf") as reopened,
        ):
            assert reopened.page_count == 1_001
            assert reopened.load_page(1_000).number == 1_000
            assert source.tell() == 0
    finally:
        storage.close()


def test_hundreds_of_megabytes_stream_with_constant_memory_but_byte_reads_stay_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = 300 * CHUNK_BYTES + 19
    original, key = sparse_source(tmp_path, size)
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=size)
    reads, writes = descriptor_spies(monkeypatch)
    tracemalloc.start()
    try:
        with original.open("rb") as input_stream:
            spy = ReadSpy(input_stream)
            stored = storage.put_stream_immutable(
                key, cast(BinaryIO, spy), content_type="application/pdf", expected_size=size
            )
        with storage.open_source(key) as source:
            assert source.tell() == 0
            assert os.fstat(source.fileno()).st_size == size
            assert stream_checksum(source) == stored.checksum_sha256
        _, peak = tracemalloc.get_traced_memory()
        assert peak < 16 * CHUNK_BYTES
        assert max((*spy.amounts, *reads, *writes)) <= CHUNK_BYTES
        with pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"):
            storage.get_bytes(key)
    finally:
        tracemalloc.stop()
        storage.close()


@pytest.mark.parametrize("maximum", [LEGACY_BYTE_LIMIT + 1, 1024**3, MAXIMUM_SIZE])
@pytest.mark.parametrize("backend", ["local", "s3"])
def test_storage_constructor_accepts_bounded_streaming_maxima(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maximum: int, backend: str
) -> None:
    storage = (
        LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=maximum)
        if backend == "local"
        else s3_storage(monkeypatch, maximum)[0]
    )
    storage.close()


@pytest.mark.parametrize("maximum", [0, -1, True, False, 1.5, "4", None, MAXIMUM_SIZE + 1])
@pytest.mark.parametrize("backend", ["local", "s3"])
def test_storage_constructor_rejects_non_integer_and_unbounded_maxima(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maximum: object, backend: str
) -> None:
    if backend == "local":
        with pytest.raises(ValueError, match="byte limit"):
            LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=cast(int, maximum))
    else:
        with pytest.raises(ValueError, match="byte limit"):
            s3_storage(monkeypatch, cast(int, maximum))


@pytest.mark.parametrize("expected_size", [True, False, -1, 1.5, "4", None, MAXIMUM_SIZE + 1])
def test_stream_put_rejects_invalid_size_before_reading_or_creating_files(
    tmp_path: Path, expected_size: object
) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=10)
    try:
        with original.open("rb") as stream:
            spy = ReadSpy(stream)
            with pytest.raises(ObjectStorageOperationError, match="object_storage_invalid_size"):
                storage.put_stream_immutable(
                    source_key(b"data"),
                    cast(BinaryIO, spy),
                    content_type="application/pdf",
                    expected_size=cast(int, expected_size),
                )
            assert spy.amounts == []
        assert not root.exists()
    finally:
        storage.close()


@pytest.mark.parametrize("invalid_stream", [None, b"not a stream", object()])
def test_stream_put_rejects_invalid_streams(tmp_path: Path, invalid_stream: object) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=10)
    try:
        with pytest.raises(ObjectStorageOperationError, match="object_storage_invalid_data"):
            storage.put_stream_immutable(
                source_key(b"data"),
                cast(BinaryIO, invalid_stream),
                content_type="application/pdf",
                expected_size=4,
            )
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("content_type", "expected_size", "failure_code"),
    [
        ("text/plain", 4, "object_storage_invalid_content_type"),
        ("application/pdf", 6, "object_storage_write_too_large"),
    ],
)
def test_stream_put_validates_content_type_and_configured_size_before_io(
    tmp_path: Path, content_type: str, expected_size: int, failure_code: str
) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=5)
    try:
        with original.open("rb") as stream:
            spy = ReadSpy(stream)
            with pytest.raises(ObjectStorageOperationError, match=failure_code):
                storage.put_stream_immutable(
                    source_key(b"data"),
                    cast(BinaryIO, spy),
                    content_type=content_type,
                    expected_size=expected_size,
                )
            assert spy.amounts == []
        assert not root.exists()
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("payload", "expected_size", "failure_code"),
    [
        (b"data", 0, "object_storage_size_mismatch"),
        (b"data", 3, "object_storage_size_mismatch"),
        (b"data", 5, "object_storage_size_mismatch"),
        (b"123456", 5, "object_storage_write_too_large"),
        (b"evil", 4, "object_storage_checksum_mismatch"),
    ],
)
def test_stream_put_rejects_short_long_and_mismatched_data_without_publishing(
    tmp_path: Path, payload: bytes, expected_size: int, failure_code: str
) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(payload)
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=5)
    try:
        with original.open("rb") as stream:
            with pytest.raises(ObjectStorageOperationError, match=failure_code):
                storage.put_stream_immutable(
                    source_key(b"data"),
                    stream,
                    content_type="application/pdf",
                    expected_size=expected_size,
                )
            assert not stream.closed
        assert original.read_bytes() == payload
        assert not list(root.rglob("*.pdf"))
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


@pytest.mark.parametrize("value", [None, "data", bytearray(b"data"), b"123456"])
def test_stream_put_rejects_non_binary_or_over_returning_reads(
    tmp_path: Path, value: object
) -> None:
    class MalformedStream:
        def read(self, amount: int) -> object:
            assert 0 < amount <= CHUNK_BYTES
            return value

    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=10)
    try:
        with pytest.raises(ObjectStorageOperationError, match="object_storage_invalid_data"):
            storage.put_stream_immutable(
                source_key(b"data"),
                cast(BinaryIO, MalformedStream()),
                content_type="application/pdf",
                expected_size=4,
            )
        assert not list(root.rglob("*.pdf"))
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


def test_stream_put_consumes_from_current_position_and_supports_empty_sources(
    tmp_path: Path,
) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(b"prefix-data")
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=20)
    try:
        with original.open("rb") as stream:
            stream.seek(7)
            stored = storage.put_stream_immutable(
                source_key(b"data"), stream, content_type="application/pdf", expected_size=4
            )
            assert stream.tell() == 11
            empty = storage.put_stream_immutable(
                source_key(b""), stream, content_type="application/pdf", expected_size=0
            )
            assert not stream.closed
        assert stored.size == 4
        assert empty.size == 0
        with storage.open_source(empty.key) as source:
            assert source.read(1) == b""
        assert original.read_bytes() == b"prefix-data"
    finally:
        storage.close()


@pytest.mark.parametrize("replacement", [b"data", b"evil", b"different size"])
def test_retry_verifies_existing_and_incoming_streams_without_creating_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bytes
) -> None:
    root = tmp_path / "data"
    original = tmp_path / "input.pdf"
    original.write_bytes(replacement)
    key = source_key(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    stored = storage.put_immutable(key, b"data", content_type="application/pdf")
    before = (root / key).stat()
    forbid_byte_paths(storage, monkeypatch)
    monkeypatch.setattr(secrets, "token_hex", forbid_byte_api)
    reads, writes = descriptor_spies(monkeypatch)
    try:
        with original.open("rb") as stream:
            spy = ReadSpy(stream)
            if replacement == b"data":
                assert (
                    storage.put_stream_immutable(
                        key, cast(BinaryIO, spy), content_type="application/pdf", expected_size=4
                    )
                    == stored
                )
            else:
                with pytest.raises(ObjectAlreadyExistsError):
                    storage.put_stream_immutable(
                        key,
                        cast(BinaryIO, spy),
                        content_type="application/pdf",
                        expected_size=len(replacement),
                    )
            assert spy.amounts
            assert not stream.closed
        assert reads
        assert not writes
        assert (root / key).read_bytes() == b"data"
        assert (root / key).stat().st_ino == before.st_ino
        assert (root / key).stat().st_mtime_ns == before.st_mtime_ns
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


@pytest.mark.parametrize("corrupted", [b"evil", b"da", b"datax"])
@pytest.mark.parametrize("operation", ["open", "retry"])
def test_corrupt_originals_fail_closed_and_are_never_replaced_or_removed(
    tmp_path: Path, corrupted: bytes, operation: str
) -> None:
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=20)
    key = source_key(b"data")
    storage.put_immutable(key, b"data", content_type="application/pdf")
    (root / key).write_bytes(corrupted)
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    try:
        if operation == "open":
            with (
                pytest.raises(ObjectStorageOperationError, match="object_storage_integrity_failed"),
                storage.open_source(key),
            ):
                pytest.fail("corrupted content must not be yielded")
        else:
            with (
                original.open("rb") as stream,
                pytest.raises(ObjectStorageOperationError, match="object_storage_integrity_failed"),
            ):
                storage.put_stream_immutable(
                    key, stream, content_type="application/pdf", expected_size=4
                )
        assert (root / key).read_bytes() == corrupted
        assert original.read_bytes() == b"data"
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


def test_explicit_stream_limit_does_not_raise_legacy_byte_limits(tmp_path: Path) -> None:
    data = b"%PDF-larger than the byte API"
    key = source_key(data)
    storage = LocalFileObjectStorage(
        root=tmp_path / "data", max_object_bytes=5, max_stream_bytes=MAXIMUM_SIZE
    )
    try:
        stored = storage.put_stream_immutable(
            key, io.BytesIO(data), content_type="application/pdf", expected_size=len(data)
        )
        assert stored.size == len(data)
        with storage.open_source(key) as source:
            assert source.read(100) == data
        with pytest.raises(ObjectStorageOperationError, match="object_storage_write_too_large"):
            storage.put_immutable(key, data, content_type="application/pdf")
        with pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"):
            storage.get_bytes(key)
    finally:
        storage.close()


def test_s3_separate_stream_limit_still_fails_without_provider_or_byte_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: client)
    storage = S3ObjectStorage(
        endpoint_url="http://localhost:9000",
        access_key_id="fixture-access",
        secret_access_key="fixture-secret",
        bucket="fixture-sources",
        region="us-east-1",
        max_object_bytes=5,
        max_stream_bytes=MAXIMUM_SIZE,
    )
    data = b"%PDF-fixture"
    try:
        with pytest.raises(
            ObjectStorageOperationError, match="object_storage_streaming_unsupported"
        ):
            storage.put_stream_immutable(
                source_key(data),
                io.BytesIO(data),
                content_type="application/pdf",
                expected_size=len(data),
            )
        with pytest.raises(ObjectStorageOperationError, match="object_storage_write_too_large"):
            storage.put_immutable(source_key(data), data, content_type="application/pdf")
        assert not client.mock_calls
    finally:
        storage.close()


@pytest.mark.parametrize("maximum", [0, -1, True, False, 1.5, "4", MAXIMUM_SIZE + 1])
@pytest.mark.parametrize("backend", ["local", "s3"])
def test_separate_stream_limit_rejects_unsafe_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, maximum: object, backend: str
) -> None:
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: Mock())
    if backend == "local":
        with pytest.raises(ValueError, match="stream byte limit"):
            LocalFileObjectStorage(
                root=tmp_path / "data", max_object_bytes=5, max_stream_bytes=cast(int, maximum)
            )
    else:
        with pytest.raises(ValueError, match="stream byte limit"):
            S3ObjectStorage(
                endpoint_url="http://localhost:9000",
                access_key_id="fixture-access",
                secret_access_key="fixture-secret",
                bucket="fixture-sources",
                region="us-east-1",
                max_object_bytes=5,
                max_stream_bytes=cast(int, maximum),
            )
    assert not (tmp_path / "data").exists()


def test_storage_factory_enables_streaming_originals_without_widening_the_byte_api(
    tmp_path: Path,
) -> None:
    from exam_guru_api.core.config import Settings
    from exam_guru_api.infrastructure.object_storage import create_object_storage

    data = b"%PDF-factory streaming fixture"
    key = source_key(data)
    settings = Settings(environment="test", storage_root=str(tmp_path / "data"), max_upload_bytes=5)
    storage = create_object_storage(settings)
    try:
        assert not (tmp_path / "data").exists()
        storage.put_stream_immutable(
            key, io.BytesIO(data), content_type="application/pdf", expected_size=len(data)
        )
        with storage.open_source(key) as stream:
            assert stream.read(100) == data
        with pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"):
            storage.get_bytes(key)
        with pytest.raises(ObjectStorageOperationError, match="object_storage_write_too_large"):
            storage.put_immutable(key, data, content_type="application/pdf")
    finally:
        storage.close()


def test_streaming_operations_keep_small_configured_byte_limits(tmp_path: Path) -> None:
    root = tmp_path / "data"
    key = source_key(b"123456")
    source = root / key
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_bytes(b"123456")
    source.chmod(0o600)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=5)
    try:
        with (
            pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"),
            storage.open_source(key),
        ):
            pytest.fail("oversized content must not be yielded")
        with source.open("rb") as stream:
            with pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"):
                storage.put_stream_immutable(
                    key, stream, content_type="application/pdf", expected_size=5
                )
            assert stream.tell() == 0
        with pytest.raises(ObjectStorageOperationError, match="object_storage_write_too_large"):
            storage.put_immutable(key, b"123456", content_type="application/pdf")
        with pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"):
            storage.get_bytes(key)
        assert source.read_bytes() == b"123456"
    finally:
        storage.close()


def test_concurrent_stream_publish_is_fsynced_atomic_and_idempotent_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    original, key = sparse_source(tmp_path, CHUNK_BYTES + 3)
    storages = [
        LocalFileObjectStorage(root=root, max_object_bytes=2 * CHUNK_BYTES) for _ in range(4)
    ]
    barrier = Barrier(len(storages))
    real_link = os.link
    real_fsync = os.fsync
    synced_files: set[int] = set()
    synced_directories: list[int] = []
    links: list[str] = []

    def fsync(file_fd: int) -> None:
        details = os.fstat(file_fd)
        real_fsync(file_fd)
        if stat.S_ISREG(details.st_mode):
            synced_files.add(details.st_ino)
        else:
            synced_directories.append(details.st_ino)

    def link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        assert src_dir_fd is not None
        assert src_dir_fd == dst_dir_fd
        assert not follow_symlinks
        assert os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False).st_ino in synced_files
        links.append(source)
        barrier.wait(timeout=10)
        real_link(
            source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=False
        )

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "link", link)

    def publish(storage: LocalFileObjectStorage) -> StoredObject:
        with original.open("rb") as stream:
            return storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=CHUNK_BYTES + 3
            )

    try:
        with ThreadPoolExecutor(max_workers=len(storages)) as executor:
            results = tuple(executor.map(publish, storages))
        assert len(set(results)) == 1
        assert len(links) == len(storages)
        assert synced_directories
        assert (root / key).stat().st_nlink == 1
        assert not list(root.rglob(".tmp-*"))
        with storages[0].open_source(key) as stream:
            assert stream_checksum(stream) == results[0].checksum_sha256
    finally:
        for storage in storages:
            storage.close()


@pytest.mark.parametrize("race", ["corrupt", "symlink"])
def test_publish_losing_to_an_unsafe_original_never_overwrites_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside private data")
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    real_link = os.link

    def link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if race == "corrupt":
            (root / key).write_bytes(b"evil")
            (root / key).chmod(0o600)
        else:
            (root / key).symlink_to(outside)
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", link)
    failure_code = (
        "object_storage_integrity_failed" if race == "corrupt" else "object_storage_unsafe_path"
    )
    try:
        with (
            original.open("rb") as stream,
            pytest.raises(ObjectStorageOperationError, match=failure_code),
        ):
            storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=4
            )
        assert (
            (root / key).is_symlink() if race == "symlink" else (root / key).read_bytes() == b"evil"
        )
        assert outside.read_bytes() == b"outside private data"
        assert original.read_bytes() == b"data"
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


@pytest.mark.parametrize(
    "key",
    [
        "../outside.pdf",
        "/sources/a.pdf",
        "sources//a.pdf",
        "sources/../a.pdf",
        "sources\\a.pdf",
        "sources/a\x00.pdf",
        "sources/aa/not-a-hash.pdf",
        "sources/ff/" + "a" * 64 + ".pdf",
    ],
)
def test_streaming_rejects_unsafe_keys_before_consuming_input(tmp_path: Path, key: str) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=20)
    try:
        with original.open("rb") as stream:
            spy = ReadSpy(stream)
            with pytest.raises(InvalidObjectKeyError):
                storage.put_stream_immutable(
                    key, cast(BinaryIO, spy), content_type="application/pdf", expected_size=4
                )
            with pytest.raises(InvalidObjectKeyError), storage.open_source(key):
                pytest.fail("unsafe key must not be opened")
            assert spy.amounts == []
    finally:
        storage.close()


@pytest.mark.parametrize("component", ["root", "sources", "prefix", "file"])
def test_streaming_never_follows_symlinked_path_components(tmp_path: Path, component: str) -> None:
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    sentinel = outside / "sentinel.pdf"
    sentinel.write_bytes(b"outside private data")
    key = source_key(b"data")
    target = {
        "root": root,
        "sources": root / "sources",
        "prefix": (root / key).parent,
        "file": root / key,
    }[component]
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.symlink_to(
        sentinel if component == "file" else outside, target_is_directory=component != "file"
    )
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    try:
        with original.open("rb") as stream:
            with pytest.raises(ObjectStorageOperationError, match="object_storage_unsafe_path"):
                storage.put_stream_immutable(
                    key, stream, content_type="application/pdf", expected_size=4
                )
            assert stream.tell() == 0
        with (
            pytest.raises(ObjectStorageOperationError, match="object_storage_unsafe_path"),
            storage.open_source(key),
        ):
            pytest.fail("symlinks must not be followed")
        assert sentinel.read_bytes() == b"outside private data"
        assert list(outside.iterdir()) == [sentinel]
    finally:
        storage.close()


@pytest.mark.parametrize("unsafe", ["permissions", "owner", "hardlinks", "directory", "fifo"])
def test_streaming_rejects_unsafe_file_metadata_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    source = root / key
    source.parent.mkdir(parents=True, mode=0o700)
    if unsafe == "directory":
        source.mkdir(mode=0o700)
    elif unsafe == "fifo":
        os.mkfifo(source, 0o600)
    else:
        source.write_bytes(b"data")
        source.chmod(0o644 if unsafe == "permissions" else 0o600)
        if unsafe == "hardlinks":
            (root / "other-one").hardlink_to(source)
            (root / "other-two").hardlink_to(source)
    inode = source.stat().st_ino
    real_open = os.open
    real_fstat = os.fstat

    def open_file(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if path == source.name:
            assert flags & os.O_NOFOLLOW
            assert flags & os.O_NONBLOCK
            assert dir_fd is not None
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def fstat(file_fd: int) -> os.stat_result:
        details = real_fstat(file_fd)
        if unsafe == "owner" and details.st_ino == inode:
            values = list(details)
            values[4] += 1
            return os.stat_result(values)
        return details

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "fstat", fstat)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    code = (
        "object_storage_unsafe_permissions"
        if unsafe in {"owner", "permissions"}
        else "object_storage_unsafe_path"
    )
    try:
        with pytest.raises(ObjectStorageOperationError, match=code), storage.open_source(key):
            pytest.fail("unsafe files must not be yielded")
    finally:
        storage.close()


@pytest.mark.parametrize("mutation", ["truncate", "grow", "oversize", "mtime"])
def test_open_source_detects_mutation_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    storage = LocalFileObjectStorage(
        root=root, max_object_bytes=4 if mutation == "oversize" else 20
    )
    storage.put_immutable(key, b"data", content_type="application/pdf")
    source = root / key
    before = source.stat()
    real_read = os.read
    changed = False

    def read(file_fd: int, amount: int) -> bytes:
        nonlocal changed
        assert 0 < amount <= CHUNK_BYTES
        if not changed and os.fstat(file_fd).st_ino == before.st_ino:
            changed = True
            if mutation == "truncate":
                source.write_bytes(b"da")
            elif mutation == "mtime":
                os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
            else:
                with source.open("ab") as stream:
                    stream.write(b"x")
        return real_read(file_fd, amount)

    monkeypatch.setattr(os, "read", read)
    code = (
        "object_storage_read_too_large"
        if mutation == "oversize"
        else "object_storage_integrity_failed"
    )
    try:
        with pytest.raises(ObjectStorageOperationError, match=code), storage.open_source(key):
            pytest.fail("changing source must not be yielded")
        assert changed
        assert source.exists()
    finally:
        storage.close()


def test_open_source_pins_verified_descriptor_instead_of_reopening_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    storage.put_immutable(key, b"data", content_type="application/pdf")
    source = root / key
    original = tmp_path / "pinned.pdf"
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside private data")
    inode = source.stat().st_ino
    real_read = os.read
    swapped = False

    def read(file_fd: int, amount: int) -> bytes:
        nonlocal swapped
        if not swapped and os.fstat(file_fd).st_ino == inode:
            swapped = True
            source.rename(original)
            source.symlink_to(outside)
        return real_read(file_fd, amount)

    monkeypatch.setattr(os, "read", read)
    try:
        with storage.open_source(key) as stream:
            assert stream.read(5) == b"data"
            assert os.fstat(stream.fileno()).st_ino == inode
        assert swapped
        assert outside.read_bytes() == b"outside private data"
    finally:
        storage.close()


@pytest.mark.parametrize("preexisting", ["file", "symlink"])
def test_exclusive_temp_creation_failure_does_not_delete_preexisting_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preexisting: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    parent = (root / key).parent
    parent.mkdir(parents=True, mode=0o700)
    unrelated = tmp_path / "unrelated.pdf"
    unrelated.write_bytes(b"keep this original")
    temp = parent / (".tmp-" + "a" * 32)
    if preexisting == "file":
        temp.write_bytes(b"keep this temporary")
    else:
        temp.symlink_to(unrelated)
    monkeypatch.setattr(secrets, "token_hex", lambda _amount: "a" * 32)
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    try:
        with (
            original.open("rb") as stream,
            pytest.raises(ObjectStorageOperationError, match="object_storage_write_failed"),
        ):
            storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=4
            )
        assert (
            temp.is_symlink()
            if preexisting == "symlink"
            else temp.read_bytes() == b"keep this temporary"
        )
        assert unrelated.read_bytes() == b"keep this original"
        assert not (root / key).exists()
    finally:
        storage.close()


@pytest.mark.parametrize(
    "failure", ["read", "write", "zero_write", "file_sync", "link", "unlink", "directory_sync"]
)
def test_stream_failures_clean_only_new_temp_and_preserve_published_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    parent = (root / key).parent
    parent.mkdir(parents=True, mode=0o700)
    unrelated = parent / ".tmp-unrelated"
    unrelated.write_bytes(b"keep")
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    real_fsync = os.fsync
    real_unlink = os.unlink
    unlink_calls = 0

    def fail(*_args: object, **_kwargs: object) -> Never:
        raise OSError(errno.EIO, "private path or provider detail")

    def fsync(file_fd: int) -> None:
        is_file = stat.S_ISREG(os.fstat(file_fd).st_mode)
        if (failure == "file_sync" and is_file) or (failure == "directory_sync" and not is_file):
            fail()
        real_fsync(file_fd)

    def unlink(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal unlink_calls
        unlink_calls += 1
        if failure == "unlink" and unlink_calls == 1:
            fail()
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "unlink", unlink)
    if failure == "write":
        monkeypatch.setattr(os, "write", fail)
    elif failure == "zero_write":
        monkeypatch.setattr(os, "write", lambda _fd, _data: 0)
    elif failure == "link":
        monkeypatch.setattr(os, "link", fail)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    try:
        with original.open("rb") as stream:
            spy = ReadSpy(stream)
            if failure == "read":
                monkeypatch.setattr(spy, "read", fail)
            with pytest.raises(ObjectStorageOperationError) as raised:
                storage.put_stream_immutable(
                    key, cast(BinaryIO, spy), content_type="application/pdf", expected_size=4
                )
            assert raised.value.failure_code == (
                "object_storage_read_failed" if failure == "read" else "object_storage_write_failed"
            )
            assert "private" not in str(raised.value)
            assert raised.value.__cause__ is None
            assert not stream.closed
        assert unrelated.read_bytes() == b"keep"
        assert original.read_bytes() == b"data"
        assert list(parent.glob(".tmp-*")) == [unrelated]
        if failure in {"unlink", "directory_sync"}:
            assert (root / key).read_bytes() == b"data"
        else:
            assert not (root / key).exists()
    finally:
        storage.close()


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.EIO, "private read failure"),
        RuntimeError("private failure"),
        ValueError("closed file"),
    ],
)
def test_input_read_errors_are_sanitized_and_cleanup_is_complete(
    tmp_path: Path, error: Exception
) -> None:
    class FailingStream:
        def read(self, _amount: int) -> Never:
            raise error

    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    try:
        with pytest.raises(
            ObjectStorageOperationError, match="object_storage_read_failed"
        ) as raised:
            storage.put_stream_immutable(
                source_key(b"data"),
                cast(BinaryIO, FailingStream()),
                content_type="application/pdf",
                expected_size=4,
            )
        assert raised.value.__cause__ is None
        assert not list(root.rglob(".tmp-*"))
        assert not list(root.rglob("*.pdf"))
    finally:
        storage.close()


def test_open_source_closes_file_on_consumer_error_without_relabeling_it(tmp_path: Path) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=30)
    key = source_key(b"data")
    storage.put_immutable(key, b"data", content_type="application/pdf")
    error = OSError("caller-owned failure")
    try:
        with (
            pytest.raises(OSError, match="caller-owned failure") as raised,
            storage.open_source(key) as source,
        ):
            raise error
        assert raised.value is error
        assert source.closed
    finally:
        storage.close()


def test_open_descriptor_remains_usable_after_storage_close_and_new_operations_fail(
    tmp_path: Path,
) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=30)
    key = source_key(b"data")
    storage.put_immutable(key, b"data", content_type="application/pdf")
    with storage.open_source(key) as source:
        storage.close()
        storage.close()
        assert source.read(5) == b"data"
        with (
            pytest.raises(ObjectStorageOperationError, match="object_storage_closed"),
            storage.open_source(key),
        ):
            pytest.fail("closed storage must reject new opens")
        source.seek(0)
        with pytest.raises(ObjectStorageOperationError, match="object_storage_closed"):
            storage.put_stream_immutable(
                key, source, content_type="application/pdf", expected_size=4
            )
    assert source.closed


@pytest.mark.parametrize("existing_parent", [False, True])
def test_missing_source_open_fails_explicitly(tmp_path: Path, existing_parent: bool) -> None:
    root = tmp_path / "data"
    key = source_key(b"missing")
    if existing_parent:
        (root / key).parent.mkdir(parents=True, mode=0o700)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    try:
        with (
            pytest.raises(ObjectStorageOperationError, match="object_storage_not_found"),
            storage.open_source(key),
        ):
            pytest.fail("missing source must not be yielded")
    finally:
        storage.close()


def test_s3_streaming_is_explicitly_unsupported_without_reading_upload_or_using_byte_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, client = s3_storage(monkeypatch, 1024**3)
    monkeypatch.setattr(storage, "get_bytes", forbid_byte_api)
    monkeypatch.setattr(storage, "put_immutable", forbid_byte_api)
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    key = source_key(b"data")
    try:
        with original.open("rb") as source:
            spy = ReadSpy(source)
            with pytest.raises(
                ObjectStorageOperationError, match="object_storage_streaming_unsupported"
            ):
                storage.put_stream_immutable(
                    key, cast(BinaryIO, spy), content_type="application/pdf", expected_size=4
                )
            with (
                pytest.raises(
                    ObjectStorageOperationError, match="object_storage_streaming_unsupported"
                ),
                storage.open_source(key),
            ):
                pytest.fail("unsupported source must not be yielded")
            assert spy.amounts == []
            assert not source.closed
        assert client.mock_calls == []
    finally:
        storage.close()


def test_s3_byte_read_limit_is_not_raised_by_streaming_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, client = s3_storage(monkeypatch, 1024**3)
    body = Mock(spec=["read", "close"])
    client.get_object.return_value = {"ContentLength": LEGACY_BYTE_LIMIT + 1, "Body": body}
    try:
        with pytest.raises(ObjectStorageOperationError, match="object_storage_read_too_large"):
            storage.get_bytes(source_key(b"data"))
        body.read.assert_not_called()
        body.close.assert_called_once_with()
    finally:
        storage.close()


@pytest.mark.parametrize("failure", ["read", "fstat", "lseek", "fdopen"])
def test_source_verification_os_errors_are_sanitized_and_close_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    storage.put_immutable(key, b"data", content_type="application/pdf")
    source = root / key
    inode = source.stat().st_ino
    real_open = os.open
    real_fstat = os.fstat
    opened: list[int] = []

    def open_file(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        file_fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == source.name:
            opened.append(file_fd)
        return file_fd

    def fail(*_args: object, **_kwargs: object) -> Never:
        raise OSError(errno.EIO, "private source path")

    def fstat(file_fd: int) -> os.stat_result:
        details = real_fstat(file_fd)
        if details.st_ino == inode:
            fail()
        return details

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "open", open_file)
            scoped.setattr(os, failure, fstat if failure == "fstat" else fail)
            with (
                pytest.raises(
                    ObjectStorageOperationError, match="object_storage_read_failed"
                ) as raised,
                storage.open_source(key),
            ):
                pytest.fail("failed verification must not yield a source")
            assert raised.value.__cause__ is None
            assert "private" not in str(raised.value)
        assert len(opened) == 1
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(opened[0])
        assert source.read_bytes() == b"data"
    finally:
        storage.close()


def test_source_verification_rejects_negative_stat_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    storage.put_immutable(key, b"data", content_type="application/pdf")
    inode = (root / key).stat().st_ino
    real_fstat = os.fstat

    def fstat(file_fd: int) -> os.stat_result:
        details = real_fstat(file_fd)
        if details.st_ino == inode:
            values = list(details)
            values[6] = -1
            return os.stat_result(values)
        return details

    monkeypatch.setattr(os, "fstat", fstat)
    try:
        with (
            pytest.raises(ObjectStorageOperationError, match="object_storage_integrity_failed"),
            storage.open_source(key),
        ):
            pytest.fail("invalid source size must not be yielded")
    finally:
        storage.close()


@pytest.mark.parametrize("mutation", ["truncate", "grow", "oversize", "permissions"])
def test_source_verification_rechecks_metadata_after_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = tmp_path / "data"
    key = source_key(b"data")
    storage = LocalFileObjectStorage(
        root=root, max_object_bytes=4 if mutation == "oversize" else 30
    )
    storage.put_immutable(key, b"data", content_type="application/pdf")
    source = root / key
    inode = source.stat().st_ino
    real_read = os.read
    changed = False

    def read(file_fd: int, amount: int) -> bytes:
        nonlocal changed
        chunk = real_read(file_fd, amount)
        if not chunk and not changed and os.fstat(file_fd).st_ino == inode:
            changed = True
            if mutation == "truncate":
                source.write_bytes(b"da")
            elif mutation == "permissions":
                source.chmod(0o644)
            else:
                with source.open("ab") as stream:
                    stream.write(b"x")
        return chunk

    monkeypatch.setattr(os, "read", read)
    code = {
        "truncate": "object_storage_integrity_failed",
        "grow": "object_storage_integrity_failed",
        "oversize": "object_storage_read_too_large",
        "permissions": "object_storage_unsafe_permissions",
    }[mutation]
    try:
        with pytest.raises(ObjectStorageOperationError, match=code), storage.open_source(key):
            pytest.fail("source changed after EOF must not be yielded")
        assert changed
        assert source.exists()
    finally:
        storage.close()


def test_stream_publish_checks_written_size_before_linking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    real_write = os.write

    def short_write(file_fd: int, data: memoryview) -> int:
        real_write(file_fd, data[:-1])
        return len(data)

    monkeypatch.setattr(os, "write", short_write)
    try:
        with (
            original.open("rb") as stream,
            pytest.raises(ObjectStorageOperationError, match="object_storage_integrity_failed"),
        ):
            storage.put_stream_immutable(
                source_key(b"data"), stream, content_type="application/pdf", expected_size=4
            )
        assert not list(root.rglob(".tmp-*"))
        assert not list(root.rglob("*.pdf"))
        assert original.read_bytes() == b"data"
    finally:
        storage.close()


def test_raced_retry_requires_size_match_even_under_a_simulated_digest_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    key = source_key(b"data")
    checksum = hashlib.sha256(b"data").hexdigest()
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    real_link = os.link

    class CollidingDigest:
        def update(self, _chunk: bytes) -> None:
            pass

        def hexdigest(self) -> str:
            return checksum

    def link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        (root / key).write_bytes(b"other")
        (root / key).chmod(0o600)
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(hashlib, "sha256", CollidingDigest)
    monkeypatch.setattr(os, "link", link)
    try:
        with original.open("rb") as stream, pytest.raises(ObjectAlreadyExistsError):
            storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=4
            )
        assert (root / key).read_bytes() == b"other"
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


def test_closed_s3_streaming_operations_fail_without_provider_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, client = s3_storage(monkeypatch)
    storage.close()
    client.reset_mock()
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    key = source_key(b"data")
    with (
        pytest.raises(ObjectStorageOperationError, match="object_storage_closed"),
        storage.open_source(key),
    ):
        pytest.fail("closed storage must not yield sources")
    with (
        original.open("rb") as stream,
        pytest.raises(ObjectStorageOperationError, match="object_storage_closed"),
    ):
        storage.put_stream_immutable(key, stream, content_type="application/pdf", expected_size=4)
    assert client.mock_calls == []


def test_streaming_rejects_unsafe_parent_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "unsafe"
    parent.mkdir(mode=0o700)
    parent.chmod(0o777)
    storage = LocalFileObjectStorage(root=parent / "data", max_object_bytes=30)
    try:
        with (
            pytest.raises(ObjectStorageOperationError, match="object_storage_unsafe_permissions"),
            storage.open_source(source_key(b"data")),
        ):
            pytest.fail("unsafe parent must not be traversed")
        assert not list(parent.iterdir())
    finally:
        parent.chmod(0o700)
        storage.close()


def test_interrupted_input_cleans_new_temporary_and_does_not_close_caller_stream(
    tmp_path: Path,
) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)

    class InterruptedStream(ReadSpy):
        def read(self, amount: int = -1) -> bytes:
            if self.amounts:
                raise KeyboardInterrupt
            return super().read(amount)

    try:
        with original.open("rb") as stream:
            spy = InterruptedStream(stream)
            with pytest.raises(KeyboardInterrupt):
                storage.put_stream_immutable(
                    source_key(b"data"),
                    cast(BinaryIO, spy),
                    content_type="application/pdf",
                    expected_size=4,
                )
            assert not stream.closed
        assert original.read_bytes() == b"data"
        assert not list(root.rglob(".tmp-*"))
        assert not list(root.rglob("*.pdf"))
    finally:
        storage.close()


def test_short_retry_input_does_not_bypass_length_verification(tmp_path: Path) -> None:
    original = tmp_path / "input.pdf"
    original.write_bytes(b"da")
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    key = source_key(b"data")
    storage.put_immutable(key, b"data", content_type="application/pdf")
    try:
        with (
            original.open("rb") as stream,
            pytest.raises(ObjectStorageOperationError, match="object_storage_size_mismatch"),
        ):
            storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=4
            )
        assert (root / key).read_bytes() == b"data"
        assert not list(root.rglob(".tmp-*"))
    finally:
        storage.close()


def test_stream_publication_stays_descriptor_relative_when_parent_path_is_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    original = tmp_path / "input.pdf"
    original.write_bytes(b"data")
    key = source_key(b"data")
    source = root / key
    pinned_parent = root / "pinned-prefix"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=30)
    real_link = os.link

    def link(
        temporary: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        source.parent.rename(pinned_parent)
        source.parent.symlink_to(outside, target_is_directory=True)
        real_link(
            temporary,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", link)
    try:
        with original.open("rb") as stream:
            stored = storage.put_stream_immutable(
                key, stream, content_type="application/pdf", expected_size=4
            )
        assert stored.size == 4
        assert (pinned_parent / source.name).read_bytes() == b"data"
        assert not list(outside.iterdir())
        assert not list(pinned_parent.glob(".tmp-*"))
        source.parent.unlink()
        pinned_parent.rename(source.parent)
        with storage.open_source(key) as verified:
            assert verified.read(5) == b"data"
    finally:
        storage.close()
