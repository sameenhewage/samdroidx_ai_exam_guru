from __future__ import annotations

import base64
import binascii
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from bisect import bisect_right
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import SecretStr

from exam_guru_api.core.config import Settings, StorageBackend

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.literals import BucketLocationConstraintType
    from mypy_boto3_s3.type_defs import TagTypeDef

SOURCE_OBJECT_PREFIX = "sources/"
APP_ORPHAN_CANDIDATE_TAG = "exam-guru-orphan-candidate"
APP_ORPHAN_DETECTED_AT_TAG = "exam-guru-orphan-detected-at"
_APP_RECONCILIATION_TAGS = frozenset({APP_ORPHAN_CANDIDATE_TAG, APP_ORPHAN_DETECTED_AT_TAG})
_SOURCE_OBJECT_KEY = re.compile(r"^sources/([0-9a-f]{2})/([0-9a-f]{64})[.]pdf$")
_MAX_LIST_PAGE_SIZE = 1_000
_MAX_CONTINUATION_TOKEN_LENGTH = 2_048
_MAX_S3_TAGS = 10
_MAX_S3_OBJECT_SIZE = 5 * 1024**4
_MAX_LOCAL_OBJECT_SIZE = 100 * 1024 * 1024
_MAX_LOCAL_METADATA_SIZE = 16 * 1024
_LOCAL_METADATA_SCHEMA_VERSION = 1
_LOCAL_TOKEN_PREFIX = b"exam-guru-local-v1\x00"
_LOCAL_DIRECTORY_MODE = 0o700
_LOCAL_FILE_MODE = 0o600
_SAFE_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_SAFE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_SAFE_WRITE_FLAGS = os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_LOCAL_PREFIX = re.compile(r"^[0-9a-f]{2}$")


class InvalidObjectKeyError(ValueError):
    def __init__(self, _key: object = None) -> None:
        super().__init__("invalid object key")


class ObjectAlreadyExistsError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("object already exists")


class ObjectStorageOperationError(RuntimeError):
    def __init__(self, failure_code: str) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code)


class ObjectTagCapacityError(ObjectStorageOperationError):
    def __init__(self) -> None:
        super().__init__("object_storage_tag_capacity_conflict")


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    checksum_sha256: str
    size: int
    etag: str


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    key: str
    size: int
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class ObjectPage:
    objects: tuple[ObjectMetadata, ...]
    is_truncated: bool
    next_continuation_token: str | None


@dataclass(frozen=True, slots=True)
class ObjectTagMutation:
    changed: bool
    tag_count: int


class ObjectStorage(Protocol):
    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject: ...

    def get_bytes(self, key: str) -> bytes: ...

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage: ...

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation: ...

    def close(self) -> None: ...


def validate_object_key(key: str) -> str:
    segments = key.split("/")
    if (
        len(key) > 1024
        or key.startswith("/")
        or "\\" in key
        or any(ord(character) < 32 or ord(character) == 127 for character in key)
    ):
        raise InvalidObjectKeyError(key)
    if any(segment in {"", ".", ".."} for segment in segments):
        raise InvalidObjectKeyError(key)
    return key


def validate_source_object_key(key: str) -> str:
    validated = validate_object_key(key)
    matched = _SOURCE_OBJECT_KEY.fullmatch(validated)
    if matched is None or matched.group(1) != matched.group(2)[:2]:
        raise InvalidObjectKeyError
    return validated


def _local_operation_error(
    error: OSError,
    default_code: str,
    *,
    missing_code: str | None = None,
) -> ObjectStorageOperationError:
    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
        code = "object_storage_unsafe_path"
    elif error.errno in {errno.EACCES, errno.EPERM}:
        code = "object_storage_unsafe_permissions"
    elif error.errno == errno.ENOENT and missing_code is not None:
        code = missing_code
    else:
        code = default_code
    return ObjectStorageOperationError(code)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate metadata key")
        result[key] = value
    return result


class LocalFileObjectStorage:
    """POSIX local storage with descriptor-relative, no-follow file operations."""

    def __init__(self, *, root: str | os.PathLike[str], max_object_bytes: int) -> None:
        root_value = os.fspath(root)
        if not isinstance(root_value, str):
            raise ValueError("storage root must be text")
        segments = root_value.split("/")
        if (
            not root_value
            or len(root_value) > 1_024
            or not PurePosixPath(root_value).is_absolute()
            or root_value == "/"
            or root_value != root_value.strip()
            or not root_value.isprintable()
            or "\\" in root_value
            or any(segment in {"", ".", ".."} for segment in segments[1:])
        ):
            raise ValueError("storage root must be a bounded normalized absolute path")
        if (
            not isinstance(max_object_bytes, int)
            or isinstance(max_object_bytes, bool)
            or not 1 <= max_object_bytes <= _MAX_LOCAL_OBJECT_SIZE
        ):
            raise ValueError("local object byte limit is invalid")
        self._root = root_value
        self._max_object_bytes = max_object_bytes
        self._state_lock = threading.Lock()
        self._root_fd: int | None = None
        self._closed = False

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        validated_key, prefix, filename, expected_checksum = self._source_parts(key)
        if not isinstance(data, bytes):
            raise ObjectStorageOperationError("object_storage_invalid_data")
        if content_type != "application/pdf":
            raise ObjectStorageOperationError("object_storage_invalid_content_type")
        if len(data) > self._max_object_bytes:
            raise ObjectStorageOperationError("object_storage_write_too_large")
        checksum = hashlib.sha256(data).hexdigest()
        root_fd = self._root_handle()
        try:
            parent_fd = self._source_parent(root_fd, prefix=prefix, create=True)
            try:
                existing = self._read_source_at(
                    parent_fd,
                    filename=filename,
                    expected_checksum=expected_checksum,
                    missing_ok=True,
                )
                if existing is not None:
                    return self._resolve_local_retry(
                        validated_key,
                        existing,
                        checksum=checksum,
                        size=len(data),
                    )
                if checksum != expected_checksum:
                    raise ObjectStorageOperationError("object_storage_checksum_mismatch")
                return self._publish_source(
                    parent_fd,
                    key=validated_key,
                    filename=filename,
                    data=data,
                    checksum=checksum,
                )
            finally:
                os.close(parent_fd)
        finally:
            os.close(root_fd)

    def get_bytes(self, key: str) -> bytes:
        _, prefix, filename, expected_checksum = self._source_parts(key)
        root_fd = self._root_handle()
        try:
            parent_fd = self._source_parent(root_fd, prefix=prefix, create=False)
            try:
                value = self._read_source_at(
                    parent_fd,
                    filename=filename,
                    expected_checksum=expected_checksum,
                    missing_ok=False,
                )
            finally:
                os.close(parent_fd)
        finally:
            os.close(root_fd)
        return cast(tuple[bytes, os.stat_result], value)[0]

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        if (
            not isinstance(max_keys, int)
            or isinstance(max_keys, bool)
            or not 1 <= max_keys <= _MAX_LIST_PAGE_SIZE
        ):
            raise ValueError("max_keys must be between 1 and 1000")
        cursor = self._decode_continuation_token(continuation_token)
        root_fd = self._root_handle()
        try:
            sources_fd = self._open_directory(root_fd, "sources", create=True)
            try:
                objects = self._scan_sources(sources_fd)
            finally:
                os.close(sources_fd)
        finally:
            os.close(root_fd)

        keys = [item.key for item in objects]
        start = 0 if cursor is None else bisect_right(keys, cursor)
        page_objects = tuple(objects[start : start + max_keys])
        truncated = start + len(page_objects) < len(objects)
        next_token = (
            self._encode_continuation_token(page_objects[-1].key)
            if truncated and page_objects
            else None
        )
        return ObjectPage(
            objects=page_objects,
            is_truncated=truncated,
            next_continuation_token=next_token,
        )

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        _, prefix, filename, checksum = self._source_parts(key)
        if candidate_detected_at is not None and candidate_detected_at.utcoffset() is None:
            raise ValueError("candidate detected timestamp must include a timezone")
        root_fd = self._root_handle()
        try:
            source_parent_fd = self._source_parent(root_fd, prefix=prefix, create=False)
            try:
                self._stat_source_at(source_parent_fd, filename)
            finally:
                os.close(source_parent_fd)
            metadata_fd = self._metadata_parent(root_fd, prefix)
            try:
                return self._merge_sidecar_tags(
                    metadata_fd,
                    checksum=checksum,
                    candidate_detected_at=candidate_detected_at,
                )
            finally:
                os.close(metadata_fd)
        finally:
            os.close(root_fd)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._root_fd is not None:
                os.close(self._root_fd)
                self._root_fd = None

    @staticmethod
    def _source_parts(key: str) -> tuple[str, str, str, str]:
        validated = validate_source_object_key(key)
        matched = cast(re.Match[str], _SOURCE_OBJECT_KEY.fullmatch(validated))
        checksum = matched.group(2)
        return validated, matched.group(1), f"{checksum}.pdf", checksum

    def _root_handle(self) -> int:
        with self._state_lock:
            if self._closed:
                raise ObjectStorageOperationError("object_storage_closed")
            if self._root_fd is None:
                self._root_fd = self._open_root()
            try:
                return os.dup(self._root_fd)
            except OSError as error:
                raise _local_operation_error(error, "object_storage_open_failed") from None

    def _open_root(self) -> int:
        try:
            current_fd = os.open("/", _SAFE_DIRECTORY_FLAGS)
        except OSError as error:
            raise _local_operation_error(error, "object_storage_open_failed") from None
        parts = self._root.split("/")[1:]
        try:
            self._assert_safe_parent(current_fd)
            for index, part in enumerate(parts):
                final = index == len(parts) - 1
                created = False
                try:
                    next_fd = os.open(part, _SAFE_DIRECTORY_FLAGS, dir_fd=current_fd)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, _LOCAL_DIRECTORY_MODE, dir_fd=current_fd)
                        created = True
                    except FileExistsError:
                        pass
                    except OSError as error:
                        raise _local_operation_error(
                            error,
                            "object_storage_open_failed",
                        ) from None
                    try:
                        next_fd = os.open(part, _SAFE_DIRECTORY_FLAGS, dir_fd=current_fd)
                    except OSError as error:
                        raise _local_operation_error(
                            error,
                            "object_storage_open_failed",
                        ) from None
                except OSError as error:
                    raise _local_operation_error(error, "object_storage_open_failed") from None
                os.close(current_fd)
                current_fd = next_fd
                if final or created:
                    self._secure_app_directory(current_fd)
                else:
                    self._assert_safe_parent(current_fd)
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    @staticmethod
    def _assert_safe_parent(directory_fd: int) -> None:
        try:
            details = os.fstat(directory_fd)
        except OSError as error:
            raise _local_operation_error(error, "object_storage_open_failed") from None
        if not stat.S_ISDIR(details.st_mode):
            raise ObjectStorageOperationError("object_storage_unsafe_path")
        unsafe_write_bits = stat.S_IMODE(details.st_mode) & 0o022
        sticky_owner = bool(details.st_mode & stat.S_ISVTX) and details.st_uid in {
            0,
            os.geteuid(),
        }
        if unsafe_write_bits and not sticky_owner:
            raise ObjectStorageOperationError("object_storage_unsafe_permissions")

    @staticmethod
    def _secure_app_directory(directory_fd: int) -> None:
        try:
            details = os.fstat(directory_fd)
            if not stat.S_ISDIR(details.st_mode):
                raise ObjectStorageOperationError("object_storage_unsafe_path")
            if details.st_uid != os.geteuid():
                raise ObjectStorageOperationError("object_storage_unsafe_permissions")
            os.fchmod(directory_fd, _LOCAL_DIRECTORY_MODE)
        except ObjectStorageOperationError:
            raise
        except OSError as error:
            raise _local_operation_error(error, "object_storage_open_failed") from None

    def _open_directory(self, parent_fd: int, name: str, *, create: bool) -> int:
        try:
            directory_fd = os.open(name, _SAFE_DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as error:
            if not create:
                raise _local_operation_error(
                    error,
                    "object_storage_open_failed",
                    missing_code="object_storage_not_found",
                ) from None
            try:
                os.mkdir(name, _LOCAL_DIRECTORY_MODE, dir_fd=parent_fd)
            except FileExistsError:
                pass
            except OSError as create_error:
                raise _local_operation_error(
                    create_error,
                    "object_storage_open_failed",
                ) from None
            try:
                directory_fd = os.open(name, _SAFE_DIRECTORY_FLAGS, dir_fd=parent_fd)
            except OSError as open_error:
                raise _local_operation_error(
                    open_error,
                    "object_storage_open_failed",
                ) from None
        except OSError as error:
            raise _local_operation_error(error, "object_storage_open_failed") from None
        try:
            self._secure_app_directory(directory_fd)
        except Exception:
            os.close(directory_fd)
            raise
        return directory_fd

    def _source_parent(self, root_fd: int, *, prefix: str, create: bool) -> int:
        sources_fd = self._open_directory(root_fd, "sources", create=create)
        try:
            return self._open_directory(sources_fd, prefix, create=create)
        finally:
            os.close(sources_fd)

    def _metadata_parent(self, root_fd: int, prefix: str) -> int:
        metadata_root_fd = self._open_directory(root_fd, ".metadata", create=True)
        try:
            metadata_sources_fd = self._open_directory(
                metadata_root_fd,
                "sources",
                create=True,
            )
        finally:
            os.close(metadata_root_fd)
        try:
            return self._open_directory(metadata_sources_fd, prefix, create=True)
        finally:
            os.close(metadata_sources_fd)

    @staticmethod
    def _validate_source_stat(details: os.stat_result, *, max_links: int = 1) -> None:
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_nlink <= max_links:
            raise ObjectStorageOperationError("object_storage_unsafe_path")
        if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != _LOCAL_FILE_MODE:
            raise ObjectStorageOperationError("object_storage_unsafe_permissions")

    def _stat_source_at(self, parent_fd: int, filename: str) -> os.stat_result:
        try:
            file_fd = os.open(filename, _SAFE_READ_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            raise _local_operation_error(
                error,
                "object_storage_read_failed",
                missing_code="object_storage_not_found",
            ) from None
        try:
            details = os.fstat(file_fd)
            self._validate_source_stat(details, max_links=2)
            if details.st_size > self._max_object_bytes:
                raise ObjectStorageOperationError("object_storage_read_too_large")
            return details
        except OSError as error:
            raise _local_operation_error(error, "object_storage_read_failed") from None
        finally:
            os.close(file_fd)

    def _read_source_at(
        self,
        parent_fd: int,
        *,
        filename: str,
        expected_checksum: str,
        missing_ok: bool,
    ) -> tuple[bytes, os.stat_result] | None:
        try:
            file_fd = os.open(filename, _SAFE_READ_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ObjectStorageOperationError("object_storage_not_found") from None
        except OSError as error:
            raise _local_operation_error(error, "object_storage_read_failed") from None
        try:
            details = os.fstat(file_fd)
            self._validate_source_stat(details, max_links=2)
            if details.st_size > self._max_object_bytes:
                raise ObjectStorageOperationError("object_storage_read_too_large")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(file_fd, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > self._max_object_bytes:
                    raise ObjectStorageOperationError("object_storage_read_too_large")
                chunks.append(chunk)
            value = b"".join(chunks)
            if (
                len(value) != details.st_size
                or hashlib.sha256(value).hexdigest() != expected_checksum
            ):
                raise ObjectStorageOperationError("object_storage_integrity_failed")
            return value, details
        except OSError as error:
            raise _local_operation_error(error, "object_storage_read_failed") from None
        finally:
            os.close(file_fd)

    @staticmethod
    def _resolve_local_retry(
        key: str,
        existing: tuple[bytes, os.stat_result],
        *,
        checksum: str,
        size: int,
    ) -> StoredObject:
        existing_data, _details = existing
        if len(existing_data) != size or hashlib.sha256(existing_data).hexdigest() != checksum:
            raise ObjectAlreadyExistsError
        return StoredObject(
            key=key,
            checksum_sha256=checksum,
            size=size,
            etag=checksum,
        )

    def _publish_source(
        self,
        parent_fd: int,
        *,
        key: str,
        filename: str,
        data: bytes,
        checksum: str,
    ) -> StoredObject:
        temporary_name = f".tmp-{secrets.token_hex(16)}"
        temporary_exists = False
        try:
            try:
                temporary_fd = os.open(
                    temporary_name,
                    _SAFE_WRITE_FLAGS | os.O_CREAT | os.O_EXCL,
                    _LOCAL_FILE_MODE,
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise _local_operation_error(error, "object_storage_write_failed") from None
            temporary_exists = True
            try:
                self._write_all(temporary_fd, data)
                os.fchmod(temporary_fd, _LOCAL_FILE_MODE)
                os.fsync(temporary_fd)
            except OSError as error:
                raise _local_operation_error(error, "object_storage_write_failed") from None
            finally:
                os.close(temporary_fd)
            try:
                os.link(
                    temporary_name,
                    filename,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                existing = self._read_source_at(
                    parent_fd,
                    filename=filename,
                    expected_checksum=checksum,
                    missing_ok=False,
                )
                return self._resolve_local_retry(
                    key,
                    cast(tuple[bytes, os.stat_result], existing),
                    checksum=checksum,
                    size=len(data),
                )
            except OSError as error:
                raise _local_operation_error(error, "object_storage_write_failed") from None
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
                temporary_exists = False
                os.fsync(parent_fd)
            except OSError as error:
                raise _local_operation_error(error, "object_storage_write_failed") from None
            return StoredObject(
                key=key,
                checksum_sha256=checksum,
                size=len(data),
                etag=checksum,
            )
        finally:
            if temporary_exists:
                with suppress(OSError):
                    os.unlink(temporary_name, dir_fd=parent_fd)

    @staticmethod
    def _write_all(file_fd: int, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            written = os.write(file_fd, remaining)
            if written <= 0:
                raise OSError(errno.EIO, "bounded write failed")
            remaining = remaining[written:]

    def _scan_sources(self, sources_fd: int) -> list[ObjectMetadata]:
        try:
            prefixes = sorted(os.listdir(sources_fd))
        except OSError as error:
            raise _local_operation_error(error, "object_storage_list_failed") from None
        objects: list[ObjectMetadata] = []
        for prefix in prefixes:
            if _LOCAL_PREFIX.fullmatch(prefix) is None:
                continue
            prefix_fd = self._open_directory(sources_fd, prefix, create=False)
            try:
                try:
                    filenames = sorted(os.listdir(prefix_fd))
                except OSError as error:
                    raise _local_operation_error(error, "object_storage_list_failed") from None
                for filename in filenames:
                    key = f"sources/{prefix}/{filename}"
                    try:
                        validate_source_object_key(key)
                    except InvalidObjectKeyError:
                        continue
                    try:
                        details = os.stat(filename, dir_fd=prefix_fd, follow_symlinks=False)
                    except OSError as error:
                        raise _local_operation_error(error, "object_storage_list_failed") from None
                    self._validate_source_stat(details, max_links=2)
                    if not 0 <= details.st_size <= self._max_object_bytes:
                        raise ObjectStorageOperationError("object_storage_invalid_metadata")
                    objects.append(
                        ObjectMetadata(
                            key=key,
                            size=details.st_size,
                            last_modified=datetime.fromtimestamp(details.st_mtime, UTC),
                        )
                    )
            finally:
                os.close(prefix_fd)
        objects.sort(key=lambda item: item.key)
        return objects

    @staticmethod
    def _encode_continuation_token(key: str) -> str:
        encoded = base64.urlsafe_b64encode(_LOCAL_TOKEN_PREFIX + key.encode("ascii"))
        return encoded.rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_continuation_token(value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= _MAX_CONTINUATION_TOKEN_LENGTH
            or not value.isascii()
            or not value.isprintable()
        ):
            raise ValueError("continuation token is invalid")
        try:
            padding = "=" * (-len(value) % 4)
            decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
            if not decoded.startswith(_LOCAL_TOKEN_PREFIX):
                raise ValueError
            key = decoded[len(_LOCAL_TOKEN_PREFIX) :].decode("ascii")
            return validate_source_object_key(key)
        except (binascii.Error, UnicodeDecodeError, InvalidObjectKeyError, ValueError):
            raise ValueError("continuation token is invalid") from None

    def _merge_sidecar_tags(
        self,
        metadata_fd: int,
        *,
        checksum: str,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        lock_name = f".{checksum}.lock"
        try:
            lock_fd = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                _LOCAL_FILE_MODE,
                dir_fd=metadata_fd,
            )
        except OSError as error:
            raise _local_operation_error(error, "object_storage_tag_write_failed") from None
        try:
            details = os.fstat(lock_fd)
            self._validate_source_stat(details)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            sidecar_name = f"{checksum}.json"
            application_tags, operator_tags = self._read_sidecar(metadata_fd, sidecar_name)
            merged_application_tags: dict[str, str] = {}
            if candidate_detected_at is not None:
                timestamp = candidate_detected_at.astimezone(UTC).isoformat(timespec="seconds")
                merged_application_tags = {
                    APP_ORPHAN_CANDIDATE_TAG: "true",
                    APP_ORPHAN_DETECTED_AT_TAG: timestamp.replace("+00:00", "Z"),
                }
            combined_count = len(operator_tags) + len(merged_application_tags)
            if combined_count > _MAX_S3_TAGS:
                raise ObjectTagCapacityError
            if application_tags == merged_application_tags:
                return ObjectTagMutation(changed=False, tag_count=combined_count)
            document: dict[str, object] = {
                "application_tags": merged_application_tags,
                "operator_tags": operator_tags,
                "schema_version": _LOCAL_METADATA_SCHEMA_VERSION,
            }
            self._write_sidecar(metadata_fd, sidecar_name, document)
            return ObjectTagMutation(changed=True, tag_count=combined_count)
        except OSError as error:
            raise _local_operation_error(error, "object_storage_tag_write_failed") from None
        finally:
            try:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError as error:
                    raise _local_operation_error(
                        error,
                        "object_storage_tag_write_failed",
                    ) from None
            finally:
                os.close(lock_fd)

    @staticmethod
    def _valid_local_tag(key: object, value: object) -> bool:
        return (
            isinstance(key, str)
            and isinstance(value, str)
            and 1 <= len(key) <= 128
            and len(value) <= 256
            and key.isprintable()
            and value.isprintable()
            and not any(ord(character) < 32 or ord(character) == 127 for character in key + value)
        )

    def _read_sidecar(
        self,
        metadata_fd: int,
        sidecar_name: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        try:
            sidecar_fd = os.open(sidecar_name, _SAFE_READ_FLAGS, dir_fd=metadata_fd)
        except FileNotFoundError:
            return {}, {}
        except OSError as error:
            raise _local_operation_error(error, "object_storage_tag_read_failed") from None
        try:
            details = os.fstat(sidecar_fd)
            self._validate_source_stat(details)
            if not 0 <= details.st_size <= _MAX_LOCAL_METADATA_SIZE:
                raise ValueError
            value = os.read(sidecar_fd, _MAX_LOCAL_METADATA_SIZE + 1)
            if len(value) != details.st_size or len(value) > _MAX_LOCAL_METADATA_SIZE:
                raise ValueError
            raw = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_json_object)
            if not isinstance(raw, dict) or set(raw) != {
                "application_tags",
                "operator_tags",
                "schema_version",
            }:
                raise ValueError
            if raw["schema_version"] != _LOCAL_METADATA_SCHEMA_VERSION:
                raise ValueError
            raw_application = raw["application_tags"]
            raw_operator = raw["operator_tags"]
            if not isinstance(raw_application, dict) or not isinstance(raw_operator, dict):
                raise ValueError
            if not all(
                self._valid_local_tag(tag_key, tag_value)
                for tag_key, tag_value in (*raw_application.items(), *raw_operator.items())
            ):
                raise ValueError
            application_tags = cast(dict[str, str], raw_application)
            operator_tags = cast(dict[str, str], raw_operator)
            if (
                not set(application_tags) <= _APP_RECONCILIATION_TAGS
                or set(application_tags).intersection(operator_tags)
                or len(application_tags) + len(operator_tags) > _MAX_S3_TAGS
            ):
                raise ValueError
            return application_tags, operator_tags
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            raise ObjectStorageOperationError("object_storage_invalid_metadata") from None
        finally:
            os.close(sidecar_fd)

    def _write_sidecar(
        self,
        metadata_fd: int,
        sidecar_name: str,
        document: dict[str, object],
    ) -> None:
        encoded = (
            json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_LOCAL_METADATA_SIZE:
            raise ObjectStorageOperationError("object_storage_invalid_metadata")
        temporary_name = f".tmp-{secrets.token_hex(16)}"
        temporary_exists = False
        try:
            try:
                temporary_fd = os.open(
                    temporary_name,
                    _SAFE_WRITE_FLAGS | os.O_CREAT | os.O_EXCL,
                    _LOCAL_FILE_MODE,
                    dir_fd=metadata_fd,
                )
                temporary_exists = True
                try:
                    self._write_all(temporary_fd, encoded)
                    os.fchmod(temporary_fd, _LOCAL_FILE_MODE)
                    os.fsync(temporary_fd)
                finally:
                    os.close(temporary_fd)
                os.replace(
                    temporary_name,
                    sidecar_name,
                    src_dir_fd=metadata_fd,
                    dst_dir_fd=metadata_fd,
                )
                temporary_exists = False
                os.fsync(metadata_fd)
            except OSError as error:
                raise _local_operation_error(error, "object_storage_tag_write_failed") from None
        finally:
            if temporary_exists:
                with suppress(OSError):
                    os.unlink(temporary_name, dir_fd=metadata_fd)


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        region: str,
        max_object_bytes: int = _MAX_LOCAL_OBJECT_SIZE,
    ) -> None:
        if (
            not isinstance(max_object_bytes, int)
            or isinstance(max_object_bytes, bool)
            or not 1 <= max_object_bytes <= _MAX_LOCAL_OBJECT_SIZE
        ):
            raise ValueError("S3 object byte limit is invalid")
        self._bucket = bucket
        self._region = region
        self._max_object_bytes = max_object_bytes
        self._closed = False
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 3, "mode": "standard"},
            ),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
                raise

        if self._region == "us-east-1":
            self._client.create_bucket(Bucket=self._bucket)
        else:
            location = cast("BucketLocationConstraintType", self._region)
            self._client.create_bucket(
                Bucket=self._bucket,
                CreateBucketConfiguration={"LocationConstraint": location},
            )

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        validated_key = validate_object_key(key)
        if len(data) > self._max_object_bytes:
            raise ObjectStorageOperationError("object_storage_write_too_large")
        checksum = hashlib.sha256(data).hexdigest()
        existing = self._head(validated_key)
        if existing is not None:
            return self._resolve_retry(existing, checksum, len(data))

        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=validated_key,
                Body=data,
                ContentLength=len(data),
                ContentType=content_type,
                IfNoneMatch="*",
                Metadata={"sha256": checksum},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] not in {
                "409",
                "412",
                "ConditionalRequestConflict",
                "PreconditionFailed",
            }:
                raise
            raced_object = self._head(validated_key)
            if raced_object is None:
                raise
            return self._resolve_retry(raced_object, checksum, len(data))

        return StoredObject(
            key=validated_key,
            checksum_sha256=checksum,
            size=len(data),
            etag=response["ETag"].strip('"'),
        )

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=validate_object_key(key))
        except ClientError as error:
            if error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                raise ObjectStorageOperationError("object_storage_not_found") from None
            raise ObjectStorageOperationError("object_storage_read_failed") from None
        except Exception:
            raise ObjectStorageOperationError("object_storage_read_failed") from None
        body = response.get("Body")
        try:
            size = response.get("ContentLength")
            if (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or size > self._max_object_bytes
            ):
                code = (
                    "object_storage_read_too_large"
                    if (
                        isinstance(size, int)
                        and not isinstance(size, bool)
                        and size > self._max_object_bytes
                    )
                    else "object_storage_invalid_response"
                )
                raise ObjectStorageOperationError(code)
            if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
                raise ObjectStorageOperationError("object_storage_invalid_response")
            value = body.read(self._max_object_bytes + 1)
            if not isinstance(value, bytes) or len(value) != size:
                if isinstance(value, bytes) and len(value) > self._max_object_bytes:
                    raise ObjectStorageOperationError("object_storage_read_too_large")
                raise ObjectStorageOperationError("object_storage_invalid_response")
            return value
        except ObjectStorageOperationError:
            raise
        except Exception:
            raise ObjectStorageOperationError("object_storage_read_failed") from None
        finally:
            if body is not None and hasattr(body, "close"):
                body.close()

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        if (
            not isinstance(max_keys, int)
            or isinstance(max_keys, bool)
            or not 1 <= max_keys <= _MAX_LIST_PAGE_SIZE
        ):
            raise ValueError("max_keys must be between 1 and 1000")
        if continuation_token is not None and not self._valid_continuation_token(
            continuation_token
        ):
            raise ValueError("continuation token is invalid")

        request: dict[str, object] = {
            "Bucket": self._bucket,
            "Prefix": SOURCE_OBJECT_PREFIX,
            "MaxKeys": max_keys,
        }
        if continuation_token is not None:
            request["ContinuationToken"] = continuation_token
        try:
            response = self._client.list_objects_v2(**request)  # type: ignore[arg-type]
        except Exception:
            raise ObjectStorageOperationError("object_storage_list_failed") from None

        try:
            contents = response.get("Contents", [])
            is_truncated = response.get("IsTruncated", False)
            if not isinstance(contents, list) or not isinstance(is_truncated, bool):
                raise ValueError
            if len(contents) > max_keys:
                raise ValueError
            objects = tuple(self._object_metadata(value) for value in contents)
            if len({value.key for value in objects}) != len(objects):
                raise ValueError

            raw_token = response.get("NextContinuationToken")
            if is_truncated:
                if not objects or not self._valid_continuation_token(raw_token):
                    raise ValueError
                next_token = raw_token
            else:
                if raw_token is not None:
                    raise ValueError
                next_token = None
        except Exception:
            raise ObjectStorageOperationError("object_storage_invalid_response") from None
        return ObjectPage(
            objects=objects,
            is_truncated=is_truncated,
            next_continuation_token=next_token,
        )

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        validated_key = validate_source_object_key(key)
        if candidate_detected_at is not None and candidate_detected_at.utcoffset() is None:
            raise ValueError("candidate detected timestamp must include a timezone")

        existing = self._read_object_tags(validated_key)
        merged = {
            tag_key: tag_value
            for tag_key, tag_value in existing.items()
            if tag_key not in _APP_RECONCILIATION_TAGS
        }
        if candidate_detected_at is not None:
            timestamp = candidate_detected_at.astimezone(UTC).isoformat(timespec="seconds")
            merged[APP_ORPHAN_CANDIDATE_TAG] = "true"
            merged[APP_ORPHAN_DETECTED_AT_TAG] = timestamp.replace("+00:00", "Z")
        if len(merged) > _MAX_S3_TAGS:
            raise ObjectTagCapacityError
        if merged == existing:
            return ObjectTagMutation(changed=False, tag_count=len(merged))

        tag_set: list[TagTypeDef] = [
            {"Key": tag_key, "Value": merged[tag_key]} for tag_key in sorted(merged)
        ]
        self._write_object_tags(validated_key, tag_set)
        return ObjectTagMutation(changed=True, tag_count=len(merged))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def _read_object_tags(self, key: str) -> dict[str, str]:
        try:
            response = self._client.get_object_tagging(Bucket=self._bucket, Key=key)
        except Exception:
            raise ObjectStorageOperationError("object_storage_tag_read_failed") from None
        try:
            raw_tags = response["TagSet"]
            if not isinstance(raw_tags, list) or len(raw_tags) > _MAX_S3_TAGS:
                raise ValueError
            tags: dict[str, str] = {}
            for raw_tag in raw_tags:
                if not isinstance(raw_tag, dict):
                    raise ValueError
                tag_key = raw_tag["Key"]
                tag_value = raw_tag["Value"]
                if not self._valid_tag(tag_key, tag_value) or tag_key in tags:
                    raise ValueError
                tags[tag_key] = tag_value
            return tags
        except Exception:
            raise ObjectStorageOperationError("object_storage_invalid_response") from None

    def _write_object_tags(self, key: str, tag_set: list[TagTypeDef]) -> None:
        try:
            self._client.put_object_tagging(
                Bucket=self._bucket,
                Key=key,
                Tagging={"TagSet": tag_set},
            )
        except Exception:
            raise ObjectStorageOperationError("object_storage_tag_write_failed") from None

    @staticmethod
    def _object_metadata(value: object) -> ObjectMetadata:
        if not isinstance(value, dict):
            raise ValueError
        key = value["Key"]
        size = value["Size"]
        last_modified = value["LastModified"]
        if not isinstance(key, str):
            raise ValueError
        validated_key = validate_source_object_key(key)
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= _MAX_S3_OBJECT_SIZE
            or not isinstance(last_modified, datetime)
            or last_modified.utcoffset() is None
        ):
            raise ValueError
        return ObjectMetadata(
            key=validated_key,
            size=size,
            last_modified=last_modified.astimezone(UTC),
        )

    @staticmethod
    def _valid_continuation_token(value: object) -> bool:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= _MAX_CONTINUATION_TOKEN_LENGTH
            and value.isprintable()
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        )

    @staticmethod
    def _valid_tag(key: object, value: object) -> bool:
        return (
            isinstance(key, str)
            and isinstance(value, str)
            and 1 <= len(key) <= 128
            and len(value) <= 256
            and key.isprintable()
            and value.isprintable()
            and not any(ord(character) < 32 or ord(character) == 127 for character in key + value)
        )

    def _head(self, key: str) -> StoredObject | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return StoredObject(
            key=key,
            checksum_sha256=response["Metadata"].get("sha256", ""),
            size=response["ContentLength"],
            etag=response["ETag"].strip('"'),
        )

    @staticmethod
    def _resolve_retry(existing: StoredObject, checksum: str, size: int) -> StoredObject:
        if existing.checksum_sha256 == checksum and existing.size == size:
            return existing
        raise ObjectAlreadyExistsError


def create_object_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend is StorageBackend.LOCAL:
        return LocalFileObjectStorage(
            root=settings.storage_root,
            max_object_bytes=settings.max_upload_bytes,
        )
    endpoint_url = cast(str, settings.object_storage_endpoint_url)
    access_key = cast(SecretStr, settings.object_storage_access_key)
    secret_key = cast(SecretStr, settings.object_storage_secret_key)
    bucket = cast(str, settings.object_storage_bucket)
    region = cast(str, settings.object_storage_region)
    return S3ObjectStorage(
        endpoint_url=endpoint_url,
        access_key_id=access_key.get_secret_value(),
        secret_access_key=secret_key.get_secret_value(),
        bucket=bucket,
        region=region,
        max_object_bytes=settings.max_upload_bytes,
    )
