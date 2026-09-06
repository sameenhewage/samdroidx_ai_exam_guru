import errno
import hashlib
import io
import os
import stat
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID, uuid4

import pytest

from exam_guru_api.infrastructure.private_artifacts import (
    ARTIFACT_CHUNK_BYTES,
    ARTIFACT_READ_BYTES,
    PrivateArtifactError,
    PrivateUploadArtifacts,
)


def put(artifacts: PrivateUploadArtifacts, upload_id: UUID, offset: int, data: bytes) -> None:
    artifacts.put_chunk(upload_id, offset, data, checksum_sha256=hashlib.sha256(data).hexdigest())


def test_chunk_hash_collects_the_pdf_signature_across_short_reads() -> None:
    data = b"%PDF-a valid short-read source"
    source = io.BytesIO(data)
    amounts: list[int] = []

    class ShortReader:
        def read(self, size: int) -> bytes:
            assert 0 < size <= ARTIFACT_READ_BYTES
            amounts.append(size)
            return source.read(min(size, 2))

    digest = hashlib.sha256()
    prefix = PrivateUploadArtifacts._hash_file(
        cast(BinaryIO, ShortReader()),
        size=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        digest=digest,
    )
    assert prefix == b"%PDF-"
    assert digest.hexdigest() == hashlib.sha256(data).hexdigest()
    assert len(amounts) > 2


def test_chunks_are_private_atomic_immutable_and_restart_safe(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    upload_id = uuid4()
    data = b"%PDF-1.7\nfixture\n%%EOF"
    put(PrivateUploadArtifacts(root=root), upload_id, 0, data)
    restarted = PrivateUploadArtifacts(root=root)
    put(restarted, upload_id, 0, data)
    digest = hashlib.sha256()
    prefix = restarted.hash_chunk(
        upload_id,
        0,
        size=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        digest=digest,
    )
    assert prefix == b"%PDF-"
    assert digest.hexdigest() == hashlib.sha256(data).hexdigest()
    with restarted.open_upload(upload_id, expected_size=len(data)) as stream:
        assert stream.read(ARTIFACT_READ_BYTES) == data
        assert stream.read(ARTIFACT_READ_BYTES) == b""
        with pytest.raises(PrivateArtifactError, match="unbounded_artifact_read"):
            stream.read()
    for path in root.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert len(list(root.rglob("*.chunk"))) == 1
    assert not list(root.rglob(".tmp-*"))
    with pytest.raises(PrivateArtifactError, match="staged_chunk_mismatch"):
        put(restarted, upload_id, 0, b"%PDF-different")
    with restarted.open_upload(upload_id, expected_size=len(data)) as stream:
        assert stream.read(100) == data


@pytest.mark.parametrize("attack", ["root", "ancestor", "upload", "chunk", "fifo", "mode"])
def test_staging_rejects_symlink_components_special_files_and_insecure_modes(
    tmp_path: Path, attack: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    upload_id = uuid4()
    root = tmp_path / "staging"
    data = b"%PDF-private"
    if attack == "root":
        root.symlink_to(outside, target_is_directory=True)
    elif attack == "ancestor":
        root.symlink_to(outside, target_is_directory=True)
        root = root / "child"
    else:
        artifacts = PrivateUploadArtifacts(root=root)
        put(artifacts, upload_id, 0, data)
        chunk = next(root.rglob("*.chunk"))
        if attack == "upload":
            chunk.unlink()
            chunk.parent.rmdir()
            chunk.parent.symlink_to(outside, target_is_directory=True)
        elif attack == "chunk":
            secret = outside / "secret.pdf"
            secret.write_bytes(data)
            chunk.unlink()
            chunk.symlink_to(secret)
        elif attack == "fifo":
            chunk.unlink()
            os.mkfifo(chunk, mode=0o600)
        else:
            chunk.chmod(0o644)
    with pytest.raises(PrivateArtifactError):
        put(PrivateUploadArtifacts(root=root), upload_id, 0, data)
    assert not list(outside.glob("*.chunk"))


def test_staging_rejects_wrong_owner_without_chmod_or_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "staging"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(os, "geteuid", lambda: root.stat().st_uid + 1)
    with pytest.raises(PrivateArtifactError, match="unsafe_artifact_permissions"):
        put(PrivateUploadArtifacts(root=root), uuid4(), 0, b"%PDF-test")
    assert not list(root.iterdir())
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "root", ["relative", "/", "/private/../escape", "/private//bad", "/private/bad\\path"]
)
def test_staging_root_is_normalized_absolute_and_not_caller_selected(root: str) -> None:
    with pytest.raises(ValueError, match="normalized absolute private directory"):
        PrivateUploadArtifacts(root=root)


def test_failed_staging_write_removes_only_its_own_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "staging"
    artifacts = PrivateUploadArtifacts(root=root)
    upload_id = uuid4()
    put(artifacts, upload_id, 0, b"%PDF-original")
    original = next(root.rglob("*.chunk"))
    unrelated = original.parent / ".tmp-unrelated-evidence"
    unrelated.write_bytes(b"retain")

    def failed_sync(_fd: int) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(PrivateArtifactError):
        put(artifacts, upload_id, ARTIFACT_CHUNK_BYTES, b"next chunk")
    assert original.read_bytes() == b"%PDF-original"
    assert unrelated.read_bytes() == b"retain"
    assert list(root.rglob(".tmp-*")) == [unrelated]


def test_conflicting_writers_never_overwrite_a_chunk(tmp_path: Path) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    upload_id = uuid4()
    contents = [b"%PDF-first", b"%PDF-other"]

    def attempt(data: bytes) -> bool:
        try:
            put(artifacts, upload_id, 0, data)
        except PrivateArtifactError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, contents))
    assert sorted(results) == [False, True]
    with artifacts.open_upload(upload_id, expected_size=len(contents[0])) as stream:
        assert stream.read(100) == contents[results.index(True)]


@pytest.mark.parametrize("mutation", ["truncate", "extend", "corrupt"])
def test_staging_hash_fails_closed_on_modified_chunks(tmp_path: Path, mutation: str) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    upload_id = uuid4()
    data = b"%PDF-original"
    put(artifacts, upload_id, 0, data)
    chunk = next(tmp_path.rglob("*.chunk"))
    damaged = data[:-1] if mutation == "truncate" else data + b"x"
    if mutation == "corrupt":
        damaged = b"x" + data[1:]
    chunk.write_bytes(damaged)
    with pytest.raises(PrivateArtifactError, match="staged_chunk_mismatch"):
        artifacts.hash_chunk(
            upload_id,
            0,
            size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            digest=hashlib.sha256(),
        )


@pytest.mark.parametrize("offset", [-1, True, 1, 2**63])
def test_invalid_chunk_offsets_fail_before_creating_directories(
    tmp_path: Path, offset: int
) -> None:
    root = tmp_path / "staging"
    with pytest.raises(PrivateArtifactError, match="invalid_artifact_identity"):
        put(PrivateUploadArtifacts(root=root), uuid4(), offset, b"%PDF-test")
    assert not root.exists()


def test_non_uuid_identity_and_non_bytes_data_fail_before_io(tmp_path: Path) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    with pytest.raises(PrivateArtifactError, match="invalid_artifact_identity"):
        put(artifacts, cast(UUID, "../source"), 0, b"%PDF-test")
    with pytest.raises(PrivateArtifactError, match="invalid_artifact_data"):
        artifacts.put_chunk(uuid4(), 0, cast(bytes, bytearray(b"%PDF-")), checksum_sha256="0" * 64)
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("size", [0, True, -1, 2**63])
def test_stream_size_and_chunk_size_validation_are_bounded(tmp_path: Path, size: int) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    with (
        pytest.raises(PrivateArtifactError, match="invalid_artifact_size"),
        artifacts.open_upload(uuid4(), expected_size=size),
    ):
        pytest.fail("invalid stream size must never open artifacts")
    with pytest.raises(PrivateArtifactError, match="invalid_artifact_size"):
        artifacts.hash_chunk(uuid4(), 0, size=size, checksum_sha256="0" * 64)
    with pytest.raises(PrivateArtifactError, match="invalid_artifact_size"):
        put(artifacts, uuid4(), 0, b"")
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("checksum", ["bad", "0" * 64])
def test_invalid_or_mismatched_digest_never_publishes_a_chunk(
    tmp_path: Path, checksum: str
) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    with pytest.raises(PrivateArtifactError, match="staged_chunk_mismatch"):
        artifacts.put_chunk(uuid4(), 0, b"%PDF-test", checksum_sha256=checksum)
    with pytest.raises(PrivateArtifactError, match="staged_chunk_mismatch"):
        artifacts.hash_chunk(uuid4(), 0, size=5, checksum_sha256="bad")
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("existing_root", [False, True])
def test_missing_roots_or_sessions_are_reported_without_creating_them(
    tmp_path: Path, existing_root: bool
) -> None:
    root = tmp_path / "staging"
    artifacts = PrivateUploadArtifacts(root=root)
    if existing_root:
        artifacts.ensure_available()
    with pytest.raises(PrivateArtifactError, match="staged_chunk_missing"):
        artifacts.hash_chunk(uuid4(), 0, size=5, checksum_sha256="0" * 64)
    assert root.exists() is existing_root
    assert not list(root.glob("*"))


def test_traversal_io_permission_error_is_sanitized_and_closes_all_open_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "denied"
    artifacts = PrivateUploadArtifacts(root=root)
    real_open = os.open
    opened: list[int] = []

    def deny_leaf(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if path == "denied":
            raise PermissionError(errno.EACCES, "PRIVATE path and OS diagnostic")
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", deny_leaf)
    with pytest.raises(PrivateArtifactError, match="unsafe_artifact_permissions") as raised:
        artifacts.ensure_available()
    assert "PRIVATE" not in str(raised.value)
    assert opened
    for fd in set(opened):
        with pytest.raises(OSError, match="Bad file descriptor") as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF
    assert not root.exists()


@pytest.mark.parametrize("location", ["root", "session"])
def test_directory_creation_races_validate_the_winner_and_preserve_one_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    root = tmp_path / "staging"
    upload_id = uuid4()
    artifacts = PrivateUploadArtifacts(root=root)
    real_mkdir = os.mkdir
    raced: list[str] = []
    target = root.name if location == "root" else upload_id.hex

    def competing_create(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(path, mode, dir_fd=dir_fd)
        if path == target:
            raced.append(path)
            raise FileExistsError(errno.EEXIST, "competing worker created the directory")

    monkeypatch.setattr(os, "mkdir", competing_create)
    put(artifacts, upload_id, 0, b"%PDF-race")
    assert raced == [target]
    assert len(list(root.rglob("*.chunk"))) == 1
    with artifacts.open_upload(upload_id, expected_size=9) as stream:
        assert stream.read(9) == b"%PDF-race"


def test_non_directory_descriptor_and_unexpected_file_ownership_or_links_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from exam_guru_api.infrastructure.private_artifacts import _check_directory, _check_file

    path = tmp_path / "source"
    path.write_bytes(b"source")
    path.chmod(0o600)
    with path.open("rb") as source:
        with pytest.raises(PrivateArtifactError, match="unsafe_artifact_path"):
            _check_directory(source.fileno(), private=True)
        details = os.fstat(source.fileno())
        monkeypatch.setattr(os, "geteuid", lambda: details.st_uid + 1)
        with pytest.raises(PrivateArtifactError, match="unsafe_artifact_permissions"):
            _check_file(details, len(b"source"))
    os.link(path, tmp_path / "extra-one")
    os.link(path, tmp_path / "extra-two")
    with pytest.raises(PrivateArtifactError, match="unsafe_artifact_path"):
        _check_file(path.stat(), len(b"source"))


@pytest.mark.parametrize("failure", [None, "zero", "disk"])
def test_partial_writes_are_completed_or_leave_only_original_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    root = tmp_path / "staging"
    upload_id = uuid4()
    artifacts = PrivateUploadArtifacts(root=root)
    put(artifacts, upload_id, 0, b"%PDF-existing")
    original = next(root.rglob("*.chunk"))
    unrelated = original.parent / ".tmp-unrelated"
    unrelated.write_bytes(b"keep this evidence")
    real_write = os.write
    counts: list[int] = []

    def short_write(fd: int, data: bytes | bytearray | memoryview) -> int:
        assert 0 < len(data) <= ARTIFACT_READ_BYTES
        counts.append(len(data))
        if len(counts) == 2 and failure == "zero":
            return 0
        if len(counts) == 2 and failure == "disk":
            raise OSError(errno.ENOSPC, "PRIVATE filesystem diagnostic")
        return real_write(fd, data[:3])

    monkeypatch.setattr(os, "write", short_write)
    if failure is None:
        put(artifacts, upload_id, ARTIFACT_CHUNK_BYTES, b"second chunk")
        assert len(list(root.rglob("*.chunk"))) == 2
    else:
        with pytest.raises(PrivateArtifactError, match="private_artifact_unavailable") as raised:
            put(artifacts, upload_id, ARTIFACT_CHUNK_BYTES, b"second chunk")
        assert "PRIVATE" not in str(raised.value)
        assert list(root.rglob("*.chunk")) == [original]
    assert len(counts) >= 2
    assert original.read_bytes() == b"%PDF-existing"
    assert unrelated.read_bytes() == b"keep this evidence"
    assert list(root.rglob(".tmp-*")) == [unrelated]


def test_cleanup_does_not_unlink_a_replacement_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    real_link = os.link

    def replace_temporary(
        src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int, follow_symlinks: bool
    ) -> None:
        real_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks
        )
        os.unlink(src, dir_fd=src_dir_fd)
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=src_dir_fd)
        try:
            os.write(fd, b"replacement evidence")
        finally:
            os.close(fd)

    monkeypatch.setattr(os, "link", replace_temporary)
    put(artifacts, uuid4(), 0, b"%PDF-original")
    assert next(tmp_path.rglob("*.chunk")).read_bytes() == b"%PDF-original"
    assert next(tmp_path.rglob(".tmp-*")).read_bytes() == b"replacement evidence"


@pytest.mark.parametrize("data", [b"%PDF", b"%PDF-extra"])
def test_hashing_detects_streams_shorter_or_longer_than_the_expected_snapshot(data: bytes) -> None:
    with pytest.raises(PrivateArtifactError, match="staged_chunk_mismatch"):
        PrivateUploadArtifacts._hash_file(
            io.BytesIO(data),
            size=5,
            checksum_sha256=hashlib.sha256(b"%PDF-").hexdigest(),
            digest=None,
        )


@pytest.mark.parametrize("mutation", ["timestamp", "truncate"])
def test_streaming_detects_changes_after_open_and_closes_even_on_failure(
    tmp_path: Path, mutation: str
) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    upload_id = uuid4()
    data = b"%PDF-immutable original"
    put(artifacts, upload_id, 0, data)
    chunk = next(tmp_path.rglob("*.chunk"))
    opened: list[BinaryIO] = []

    def read_during_mutation() -> None:
        with artifacts.open_upload(upload_id, expected_size=len(data)) as stream:
            opened.append(stream)
            assert stream.readable()
            assert stream.read(1) == b"%"
            if mutation == "timestamp":
                details = chunk.stat()
                os.utime(chunk, ns=(details.st_atime_ns, details.st_mtime_ns + 1))
            else:
                chunk.write_bytes(b"%")
            stream.read(ARTIFACT_READ_BYTES)

    with pytest.raises(PrivateArtifactError, match="staged_chunk_mismatch"):
        read_during_mutation()
    assert opened[0].closed
    with pytest.raises(ValueError, match="closed artifact stream"):
        opened[0].read(1)


def test_zero_length_read_is_lazy_and_an_interrupted_stream_closes_the_open_chunk(
    tmp_path: Path,
) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    upload_id = uuid4()
    with artifacts.open_upload(upload_id, expected_size=5) as stream:
        assert stream.read(0) == b""
    assert not (tmp_path / "staging").exists()
    put(artifacts, upload_id, 0, b"%PDF-original")
    with artifacts.open_upload(upload_id, expected_size=13) as stream:
        assert stream.read(1) == b"%"
    assert stream.closed


def test_generated_300_mib_staging_stream_has_bounded_memory(tmp_path: Path) -> None:
    artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
    upload_id = uuid4()
    total = 300 * 1024 * 1024
    block = b"%PDF-1.7\n" + b"x" * (ARTIFACT_CHUNK_BYTES - 9)
    block_checksum = hashlib.sha256(block).hexdigest()
    expected = hashlib.sha256()
    tracemalloc.start()
    try:
        for offset in range(0, total, ARTIFACT_CHUNK_BYTES):
            artifacts.put_chunk(upload_id, offset, block, checksum_sha256=block_checksum)
            expected.update(block)
        digest = hashlib.sha256()
        for offset in range(0, total, ARTIFACT_CHUNK_BYTES):
            artifacts.hash_chunk(
                upload_id,
                offset,
                size=ARTIFACT_CHUNK_BYTES,
                checksum_sha256=block_checksum,
                digest=digest,
            )
        streamed = hashlib.sha256()
        received = 0
        with artifacts.open_upload(upload_id, expected_size=total) as stream:
            while piece := stream.read(ARTIFACT_READ_BYTES):
                received += len(piece)
                streamed.update(piece)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert received == total
    assert streamed.hexdigest() == digest.hexdigest() == expected.hexdigest()
    assert peak < 12 * 1024 * 1024
