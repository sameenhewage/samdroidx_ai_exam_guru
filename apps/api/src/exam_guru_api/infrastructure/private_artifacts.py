import errno
import hashlib
import hmac
import io
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol, cast
from uuid import UUID

ARTIFACT_CHUNK_BYTES = 4 * 1024 * 1024
ARTIFACT_READ_BYTES = 1024 * 1024
_MAX_INTEGER = 2**63 - 1
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")


class PrivateArtifactError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...


def _operation_error(error: OSError) -> PrivateArtifactError:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        return PrivateArtifactError("unsafe_artifact_path")
    if error.errno in {errno.EACCES, errno.EPERM}:
        return PrivateArtifactError("unsafe_artifact_permissions")
    if error.errno == errno.ENOENT:
        return PrivateArtifactError("staged_chunk_missing")
    return PrivateArtifactError("private_artifact_unavailable")


def _validate_offset(upload_id: UUID, offset: int) -> str:
    if (
        not isinstance(upload_id, UUID)
        or type(offset) is not int
        or not 0 <= offset <= _MAX_INTEGER
        or offset % ARTIFACT_CHUNK_BYTES
    ):
        raise PrivateArtifactError("invalid_artifact_identity")
    return f"{offset:016x}.chunk"


def _validate_size(size: int, maximum: int) -> None:
    if type(size) is not int or not 1 <= size <= maximum:
        raise PrivateArtifactError("invalid_artifact_size")


def _check_directory(fd: int, *, private: bool) -> None:
    details = os.fstat(fd)
    if not stat.S_ISDIR(details.st_mode):
        raise PrivateArtifactError("unsafe_artifact_path")
    mode = stat.S_IMODE(details.st_mode)
    if private:
        if details.st_uid != os.geteuid() or mode != 0o700:
            raise PrivateArtifactError("unsafe_artifact_permissions")
    elif details.st_uid not in {0, os.geteuid()} or (
        mode & 0o022 and not details.st_mode & stat.S_ISVTX
    ):
        raise PrivateArtifactError("unsafe_artifact_permissions")


def _check_file(details: os.stat_result, size: int) -> None:
    if not stat.S_ISREG(details.st_mode) or details.st_nlink not in {1, 2}:
        raise PrivateArtifactError("unsafe_artifact_path")
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o600:
        raise PrivateArtifactError("unsafe_artifact_permissions")
    if details.st_size != size:
        raise PrivateArtifactError("staged_chunk_mismatch")


def _unchanged(before: os.stat_result, after: os.stat_result, size: int) -> None:
    _check_file(after, size)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PrivateArtifactError("staged_chunk_mismatch")


class PrivateUploadArtifacts:
    def __init__(self, *, root: str | os.PathLike[str]) -> None:
        value = os.fspath(root)
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 1024
            or not PurePosixPath(value).is_absolute()
            or value == "/"
            or value != value.strip()
            or not value.isprintable()
            or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])
        ):
            raise ValueError("artifact root must be a normalized absolute private directory")
        self._root = value

    def ensure_available(self) -> None:
        try:
            os.close(self._root_directory(create=True))
        except OSError as error:
            raise _operation_error(error) from None

    def _root_directory(self, *, create: bool) -> int:
        fd = os.open("/", _DIRECTORY_FLAGS)
        parts = self._root.split("/")[1:]
        try:
            _check_directory(fd, private=False)
            for index, part in enumerate(parts):
                created = False
                try:
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                        created = True
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                _check_directory(fd, private=created or index == len(parts) - 1)
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def _upload_directory(self, upload_id: UUID, *, create: bool) -> Iterator[int]:
        _validate_offset(upload_id, 0)
        try:
            root_fd = self._root_directory(create=create)
            try:
                try:
                    directory = os.open(upload_id.hex, _DIRECTORY_FLAGS, dir_fd=root_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(upload_id.hex, mode=0o700, dir_fd=root_fd)
                        os.fsync(root_fd)
                    except FileExistsError:
                        pass
                    directory = os.open(upload_id.hex, _DIRECTORY_FLAGS, dir_fd=root_fd)
                try:
                    _check_directory(directory, private=True)
                    yield directory
                finally:
                    os.close(directory)
            finally:
                os.close(root_fd)
        except OSError as error:
            raise _operation_error(error) from None

    @staticmethod
    @contextmanager
    def _chunk_file(directory: int, name: str, *, size: int) -> Iterator[BinaryIO]:
        fd = os.open(name, _READ_FLAGS, dir_fd=directory)
        try:
            before = os.fstat(fd)
            _check_file(before, size)
            stream = os.fdopen(fd, "rb", buffering=0, closefd=False)
            try:
                yield cast(BinaryIO, stream)
                _unchanged(before, os.fstat(fd), size)
            finally:
                stream.close()
        finally:
            os.close(fd)

    @contextmanager
    def _open_chunk(self, upload_id: UUID, offset: int, *, size: int) -> Iterator[BinaryIO]:
        name = _validate_offset(upload_id, offset)
        _validate_size(size, ARTIFACT_CHUNK_BYTES)
        with (
            self._upload_directory(upload_id, create=False) as directory,
            self._chunk_file(directory, name, size=size) as stream,
        ):
            yield stream

    @staticmethod
    def _hash_file(
        stream: BinaryIO, *, size: int, checksum_sha256: str, digest: _Digest | None
    ) -> bytes:
        checksum = hashlib.sha256()
        remaining = size
        prefix = b""
        while remaining:
            piece = stream.read(min(remaining, ARTIFACT_READ_BYTES))
            if not piece:
                raise PrivateArtifactError("staged_chunk_mismatch")
            if len(prefix) < 5:
                prefix += piece[: 5 - len(prefix)]
            checksum.update(piece)
            if digest is not None:
                digest.update(piece)
            remaining -= len(piece)
        if stream.read(1) or not hmac.compare_digest(checksum.hexdigest(), checksum_sha256):
            raise PrivateArtifactError("staged_chunk_mismatch")
        return prefix

    def put_chunk(self, upload_id: UUID, offset: int, data: bytes, *, checksum_sha256: str) -> None:
        name = _validate_offset(upload_id, offset)
        if not isinstance(data, bytes):
            raise PrivateArtifactError("invalid_artifact_data")
        _validate_size(len(data), ARTIFACT_CHUNK_BYTES)
        if not _CHECKSUM.fullmatch(checksum_sha256) or not hmac.compare_digest(
            hashlib.sha256(data).hexdigest(), checksum_sha256
        ):
            raise PrivateArtifactError("staged_chunk_mismatch")
        with self._upload_directory(upload_id, create=True) as directory:
            temporary = f".tmp-{secrets.token_hex(16)}"
            fd = os.open(temporary, _WRITE_FLAGS, mode=0o600, dir_fd=directory)
            identity = os.fstat(fd)
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(data)
                position = 0
                while position < len(data):
                    written = os.write(fd, view[position : position + ARTIFACT_READ_BYTES])
                    if written <= 0:
                        raise PrivateArtifactError("private_artifact_unavailable")
                    position += written
                os.fsync(fd)
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    with self._chunk_file(directory, name, size=len(data)) as stream:
                        self._hash_file(
                            stream, size=len(data), checksum_sha256=checksum_sha256, digest=None
                        )
                os.fsync(directory)
            finally:
                os.close(fd)
                with suppress(OSError):
                    current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        os.unlink(temporary, dir_fd=directory)
                        os.fsync(directory)

    def hash_chunk(
        self,
        upload_id: UUID,
        offset: int,
        *,
        size: int,
        checksum_sha256: str,
        digest: _Digest | None = None,
    ) -> bytes:
        if not _CHECKSUM.fullmatch(checksum_sha256):
            raise PrivateArtifactError("staged_chunk_mismatch")
        with self._open_chunk(upload_id, offset, size=size) as stream:
            return self._hash_file(
                stream, size=size, checksum_sha256=checksum_sha256, digest=digest
            )

    @contextmanager
    def open_upload(self, upload_id: UUID, *, expected_size: int) -> Iterator[BinaryIO]:
        _validate_offset(upload_id, 0)
        _validate_size(expected_size, _MAX_INTEGER)
        stream = _UploadStream(self, upload_id, expected_size)
        try:
            yield cast(BinaryIO, stream)
        finally:
            stream.close()


class _UploadStream(io.RawIOBase):
    def __init__(self, artifacts: PrivateUploadArtifacts, upload_id: UUID, size: int) -> None:
        super().__init__()
        self._artifacts = artifacts
        self._upload_id = upload_id
        self._size = size
        self._position = 0
        self._chunk_offset = 0
        self._chunk_size = 0
        self._context: AbstractContextManager[BinaryIO] | None = None
        self._stream: BinaryIO | None = None

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed artifact stream")
        if type(size) is not int or not 0 <= size <= ARTIFACT_READ_BYTES:
            raise PrivateArtifactError("unbounded_artifact_read")
        if size == 0 or self._position == self._size:
            return b""
        if self._stream is None:
            self._chunk_offset = self._position
            self._chunk_size = min(ARTIFACT_CHUNK_BYTES, self._size - self._position)
            self._context = self._artifacts._open_chunk(
                self._upload_id, self._chunk_offset, size=self._chunk_size
            )
            self._stream = self._context.__enter__()
        remaining = self._chunk_size - (self._position - self._chunk_offset)
        piece = self._stream.read(min(size, remaining))
        if not piece:
            raise PrivateArtifactError("staged_chunk_mismatch")
        self._position += len(piece)
        if self._position == self._chunk_offset + self._chunk_size:
            self._close_chunk()
        return piece

    def _close_chunk(self) -> None:
        context = self._context
        self._context = None
        self._stream = None
        if context is not None:
            context.__exit__(None, None, None)

    def close(self) -> None:
        try:
            self._close_chunk()
        finally:
            super().close()
