import errno
import fcntl
import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import SecretStr

from exam_guru_api.core.config import Settings, StorageBackend
from exam_guru_api.infrastructure import object_storage as storage_module
from exam_guru_api.infrastructure.object_storage import (
    APP_ORPHAN_CANDIDATE_TAG,
    APP_ORPHAN_DETECTED_AT_TAG,
    InvalidObjectKeyError,
    LocalFileObjectStorage,
    ObjectAlreadyExistsError,
    ObjectStorageOperationError,
    ObjectTagCapacityError,
    S3ObjectStorage,
    create_object_storage,
)


def source_key(data: bytes) -> str:
    checksum = hashlib.sha256(data).hexdigest()
    return f"sources/{checksum[:2]}/{checksum}.pdf"


def sidecar_path(root: Path, key: str) -> Path:
    checksum = key.rsplit("/", maxsplit=1)[1].removesuffix(".pdf")
    return root / ".metadata" / "sources" / checksum[:2] / f"{checksum}.json"


def test_factory_selects_validated_local_default_and_explicit_s3(tmp_path: Path) -> None:
    local = create_object_storage(Settings(environment="test", storage_root=str(tmp_path)))
    assert isinstance(local, LocalFileObjectStorage)
    local.close()

    s3 = create_object_storage(
        Settings(
            environment="test",
            storage_backend=StorageBackend.S3,
            object_storage_endpoint_url="http://localhost:9000",
            object_storage_access_key=SecretStr("integration-access"),
            object_storage_secret_key=SecretStr("integration-secret"),
            object_storage_bucket="exam-guru-test",
            object_storage_region="us-east-1",
        )
    )
    assert isinstance(s3, S3ObjectStorage)
    s3.close()


def test_local_storage_is_content_addressed_private_persistent_and_retry_safe(
    tmp_path: Path,
) -> None:
    root = tmp_path / "studio-data"
    payload = b"%PDF-1.7\nlocal durable fixture\n%%EOF"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)

    stored = storage.put_immutable(key, payload, content_type="application/pdf")
    retried = storage.put_immutable(key, payload, content_type="application/pdf")

    assert retried == stored
    assert stored.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.size == len(payload)
    assert storage.get_bytes(key) == payload
    source = root / key
    assert stat.S_IMODE(root.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(source.parent.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert not [path for path in source.parent.iterdir() if path.name.startswith(".tmp-")]

    storage.close()
    restarted = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    assert restarted.get_bytes(key) == payload
    assert restarted.put_immutable(key, payload, content_type="application/pdf") == stored
    restarted.close()


def test_local_storage_concurrent_publish_is_atomic_and_no_clobber(tmp_path: Path) -> None:
    payload = b"%PDF-1.7\nconcurrent fixture\n%%EOF"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=1_024)
    barrier = Barrier(12)

    def publish() -> object:
        barrier.wait()
        return storage.put_immutable(key, payload, content_type="application/pdf")

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = tuple(executor.map(lambda _index: publish(), range(12)))

    assert len(set(results)) == 1
    assert storage.get_bytes(key) == payload
    assert not [
        path
        for path in (tmp_path / "data" / "sources" / key.split("/")[1]).iterdir()
        if path.name.startswith(".tmp-")
    ]


def test_local_storage_concurrent_publish_across_instances_survives_directory_creation_races(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared-data"
    payload = b"%PDF-1.7\ncross-instance fixture\n%%EOF"
    key = source_key(payload)
    storages = tuple(
        LocalFileObjectStorage(root=root, max_object_bytes=1_024) for _index in range(8)
    )
    barrier = Barrier(len(storages))

    def publish(storage: LocalFileObjectStorage) -> object:
        barrier.wait()
        return storage.put_immutable(key, payload, content_type="application/pdf")

    with ThreadPoolExecutor(max_workers=len(storages)) as executor:
        results = tuple(executor.map(publish, storages))

    assert len(set(results)) == 1
    assert storages[0].get_bytes(key) == payload
    for storage in storages:
        storage.close()


def test_local_storage_duplicate_conflict_never_replaces_source_bytes(tmp_path: Path) -> None:
    original = b"%PDF-1.7\noriginal\n%%EOF"
    replacement = b"%PDF-1.7\nreplacement\n%%EOF"
    key = source_key(original)
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=1_024)
    storage.put_immutable(key, original, content_type="application/pdf")

    with pytest.raises(ObjectAlreadyExistsError):
        storage.put_immutable(key, replacement, content_type="application/pdf")

    assert storage.get_bytes(key) == original


def test_local_storage_rejects_non_content_addressed_and_unsafe_source_keys(
    tmp_path: Path,
) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=1_024)
    payload = b"%PDF-1.7\nfixture\n%%EOF"
    unsafe_keys = (
        "../outside.pdf",
        "/sources/aa/file.pdf",
        "sources/../file.pdf",
        "sources\\aa\\file.pdf",
        "sources/aa/file\n.pdf",
        "sources/aa/not-a-checksum.pdf",
        f"sources/ff/{hashlib.sha256(payload).hexdigest()}.pdf",
    )

    for key in unsafe_keys:
        with pytest.raises(InvalidObjectKeyError):
            storage.put_immutable(key, payload, content_type="application/pdf")

    mismatched_key = source_key(b"different payload")
    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.put_immutable(mismatched_key, payload, content_type="application/pdf")
    assert raised.value.failure_code == "object_storage_checksum_mismatch"


def test_local_storage_never_follows_source_or_root_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "data"
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside must remain unchanged")
    payload = b"%PDF-1.7\nsymlink fixture\n%%EOF"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    assert storage.list_source_objects(max_keys=10).objects == ()
    destination = root / key
    destination.parent.mkdir(mode=0o700)
    destination.symlink_to(outside)

    for operation in (
        lambda: storage.get_bytes(key),
        lambda: storage.put_immutable(key, payload, content_type="application/pdf"),
    ):
        with pytest.raises(ObjectStorageOperationError) as raised:
            operation()
        assert raised.value.failure_code == "object_storage_unsafe_path"
        assert str(outside) not in str(raised.value)
    assert outside.read_bytes() == b"outside must remain unchanged"

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(tmp_path / "real-root", target_is_directory=True)
    linked = LocalFileObjectStorage(root=linked_root, max_object_bytes=1_024)
    with pytest.raises(ObjectStorageOperationError) as raised:
        linked.list_source_objects(max_keys=1)
    assert raised.value.failure_code == "object_storage_unsafe_path"


def test_local_storage_rejects_unsafe_parent_modes_without_leaking_paths(tmp_path: Path) -> None:
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o700)
    unsafe_parent.chmod(0o777)
    storage = LocalFileObjectStorage(root=unsafe_parent / "data", max_object_bytes=1_024)
    try:
        with pytest.raises(ObjectStorageOperationError) as raised:
            storage.list_source_objects(max_keys=1)
        assert raised.value.failure_code == "object_storage_unsafe_permissions"
        assert str(unsafe_parent) not in str(raised.value)
    finally:
        unsafe_parent.chmod(0o700)


def test_local_storage_enforces_configured_write_and_read_bounds(tmp_path: Path) -> None:
    root = tmp_path / "data"
    payload = b"123456"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=5)

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.put_immutable(key, payload, content_type="application/pdf")
    assert raised.value.failure_code == "object_storage_write_too_large"

    source = root / key
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_bytes(payload)
    source.chmod(0o600)
    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.get_bytes(key)
    assert raised.value.failure_code == "object_storage_read_too_large"


def test_local_listing_is_sorted_bounded_restart_safe_and_uses_opaque_tokens(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    payloads = (b"third", b"first", b"fourth", b"second")
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    for payload in payloads:
        storage.put_immutable(source_key(payload), payload, content_type="application/pdf")
    expected = sorted(source_key(payload) for payload in payloads)

    first = storage.list_source_objects(max_keys=2)
    assert [item.key for item in first.objects] == expected[:2]
    assert all(item.last_modified.tzinfo is UTC for item in first.objects)
    assert first.is_truncated is True
    assert first.next_continuation_token is not None
    assert len(first.next_continuation_token) <= 2_048
    assert expected[1] not in first.next_continuation_token
    storage.close()

    restarted = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    second = restarted.list_source_objects(
        max_keys=2,
        continuation_token=first.next_continuation_token,
    )
    assert [item.key for item in second.objects] == expected[2:]
    assert second.is_truncated is False
    assert second.next_continuation_token is None

    for max_keys in (0, 1_001, True):
        with pytest.raises(ValueError, match="max_keys"):
            restarted.list_source_objects(max_keys=max_keys)
    for token in ("", "not-an-opaque-token", "x" * 2_049, "unsafe\ntoken"):
        with pytest.raises(ValueError, match="continuation"):
            restarted.list_source_objects(max_keys=1, continuation_token=token)


def test_local_reconciliation_sidecar_is_atomic_bounded_and_preserves_operator_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    payload = b"%PDF-1.7\nmetadata fixture\n%%EOF"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    storage.put_immutable(key, payload, content_type="application/pdf")
    source = root / key
    source_bytes = source.read_bytes()
    source_stat = source.stat()
    metadata = sidecar_path(root, key)
    metadata.parent.mkdir(parents=True, mode=0o700)
    metadata.write_text(
        json.dumps(
            {
                "application_tags": {},
                "operator_tags": {"operator-retention": "legal-hold"},
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    metadata.chmod(0o600)
    detected_at = datetime(2026, 4, 5, 6, 7, 8, tzinfo=UTC)

    applied = storage.merge_reconciliation_tags(key, candidate_detected_at=detected_at)
    unchanged = storage.merge_reconciliation_tags(key, candidate_detected_at=detected_at)
    document = json.loads(metadata.read_text(encoding="utf-8"))

    assert applied.changed is True
    assert applied.tag_count == 3
    assert unchanged.changed is False
    assert document == {
        "application_tags": {
            APP_ORPHAN_CANDIDATE_TAG: "true",
            APP_ORPHAN_DETECTED_AT_TAG: "2026-04-05T06:07:08Z",
        },
        "operator_tags": {"operator-retention": "legal-hold"},
        "schema_version": 1,
    }
    assert metadata.stat().st_size <= 16 * 1024
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o600
    assert source.read_bytes() == source_bytes
    assert source.stat().st_mtime_ns == source_stat.st_mtime_ns

    removed = storage.merge_reconciliation_tags(key, candidate_detected_at=None)
    assert removed.changed is True
    assert removed.tag_count == 1
    assert json.loads(metadata.read_text(encoding="utf-8"))["operator_tags"] == {
        "operator-retention": "legal-hold"
    }
    assert json.loads(metadata.read_text(encoding="utf-8"))["application_tags"] == {}


def test_local_reconciliation_rejects_capacity_corruption_and_naive_timestamps(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    payload = b"%PDF-1.7\nmetadata failure fixture\n%%EOF"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    storage.put_immutable(key, payload, content_type="application/pdf")
    metadata = sidecar_path(root, key)
    metadata.parent.mkdir(parents=True, mode=0o700)

    with pytest.raises(ValueError, match="timezone"):
        storage.merge_reconciliation_tags(key, candidate_detected_at=datetime(2026, 1, 1))

    metadata.write_text(
        json.dumps(
            {
                "application_tags": {},
                "operator_tags": {f"operator-{index}": "keep" for index in range(9)},
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    metadata.chmod(0o600)
    with pytest.raises(ObjectTagCapacityError):
        storage.merge_reconciliation_tags(
            key,
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    metadata.write_text("{corrupt private metadata", encoding="utf-8")
    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.merge_reconciliation_tags(
            key,
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert raised.value.failure_code == "object_storage_invalid_metadata"
    assert "corrupt" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_local_storage_close_is_idempotent_and_closed_operations_are_sanitized(
    tmp_path: Path,
) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=1_024)
    storage.close()
    storage.close()

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.list_source_objects(max_keys=1)
    assert raised.value.failure_code == "object_storage_closed"


@pytest.mark.parametrize(
    "root",
    [
        cast(str, b"/data"),
        "",
        "relative/data",
        "/",
        "/data/",
        "/data/../outside",
        "/data\\outside",
        "/data\nprivate",
        "/" + ("x" * 1_025),
    ],
)
def test_local_storage_constructor_rejects_invalid_roots(root: str) -> None:
    with pytest.raises(ValueError, match="storage root"):
        LocalFileObjectStorage(root=root, max_object_bytes=1)


def test_local_storage_accepts_bounded_large_original_pdf_configuration(tmp_path: Path) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=256 * 1024 * 1024)
    storage.close()


@pytest.mark.parametrize("maximum", [0, True, (256 * 1024 * 1024) + 1])
def test_local_storage_constructor_rejects_invalid_byte_limits(maximum: int) -> None:
    with pytest.raises(ValueError, match="byte limit"):
        LocalFileObjectStorage(root="/data", max_object_bytes=maximum)


def test_local_storage_rejects_invalid_data_and_content_type(tmp_path: Path) -> None:
    payload = b"data"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=10)

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.put_immutable(
            key,
            cast(bytes, bytearray(payload)),
            content_type="application/pdf",
        )
    assert raised.value.failure_code == "object_storage_invalid_data"

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.put_immutable(key, payload, content_type="text/plain")
    assert raised.value.failure_code == "object_storage_invalid_content_type"


def test_local_storage_missing_and_corrupt_objects_return_sanitized_failures(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    missing_payload = b"missing"
    with pytest.raises(ObjectStorageOperationError) as missing:
        storage.get_bytes(source_key(missing_payload))
    assert missing.value.failure_code == "object_storage_not_found"

    expected = b"expected"
    key = source_key(expected)
    source = root / key
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_bytes(b"tampered")
    source.chmod(0o600)
    with pytest.raises(ObjectStorageOperationError) as corrupt:
        storage.get_bytes(key)
    assert corrupt.value.failure_code == "object_storage_integrity_failed"


def test_local_storage_rejects_unsafe_file_modes_types_links_and_prefix_symlink(
    tmp_path: Path,
) -> None:
    for index, prepare in enumerate(("mode", "directory", "links", "prefix-symlink")):
        root = tmp_path / f"case-{index}"
        storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
        payload = f"payload-{index}".encode()
        key = source_key(payload)
        source = root / key
        source.parent.mkdir(parents=True, mode=0o700)
        if prepare == "mode":
            source.write_bytes(payload)
            source.chmod(0o644)
            expected_code = "object_storage_unsafe_permissions"
        elif prepare == "directory":
            source.mkdir(mode=0o700)
            expected_code = "object_storage_unsafe_path"
        elif prepare == "links":
            source.write_bytes(payload)
            source.chmod(0o600)
            (root / "extra-one").hardlink_to(source)
            (root / "extra-two").hardlink_to(source)
            expected_code = "object_storage_unsafe_path"
        else:
            source.parent.rmdir()
            outside = tmp_path / f"outside-{index}"
            outside.mkdir(mode=0o700)
            source.parent.symlink_to(outside, target_is_directory=True)
            expected_code = "object_storage_unsafe_path"
        with pytest.raises(ObjectStorageOperationError) as raised:
            storage.get_bytes(key)
        assert raised.value.failure_code == expected_code


def test_local_listing_ignores_non_source_names_and_rejects_oversized_source_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=5)
    assert storage.list_source_objects(max_keys=10).objects == ()
    (root / "sources" / "not-a-prefix").mkdir(mode=0o700)
    valid_prefix = root / "sources" / "aa"
    valid_prefix.mkdir(mode=0o700)
    (valid_prefix / "not-a-source.tmp").write_bytes(b"ignored")
    (valid_prefix / "not-a-source.tmp").chmod(0o600)
    assert storage.list_source_objects(max_keys=10).objects == ()

    payload = b"123456"
    oversized = root / source_key(payload)
    oversized.parent.mkdir(mode=0o700, exist_ok=True)
    oversized.write_bytes(payload)
    oversized.chmod(0o600)
    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.list_source_objects(max_keys=10)
    assert raised.value.failure_code == "object_storage_invalid_metadata"


@pytest.mark.parametrize(
    "metadata_bytes",
    [
        b"[]",
        b'{"application_tags":{},"schema_version":1}',
        b'{"application_tags":{},"operator_tags":{},"schema_version":2}',
        b'{"application_tags":[],"operator_tags":{},"schema_version":1}',
        b'{"application_tags":{"bad":4},"operator_tags":{},"schema_version":1}',
        b'{"application_tags":{"unknown":"value"},"operator_tags":{},"schema_version":1}',
        (
            b'{"application_tags":{"exam-guru-orphan-candidate":"true"},'
            b'"operator_tags":{"exam-guru-orphan-candidate":"keep"},"schema_version":1}'
        ),
        (
            b'{"application_tags":{},"operator_tags":{'
            + b",".join(f'"operator-{index}":"keep"'.encode() for index in range(11))
            + b'},"schema_version":1}'
        ),
        (b'{"application_tags":{},"operator_tags":{},"schema_version":1,"schema_version":1}'),
        b"\xff",
        b"x" * ((16 * 1024) + 1),
    ],
)
def test_local_reconciliation_rejects_malformed_bounded_sidecars(
    tmp_path: Path,
    metadata_bytes: bytes,
) -> None:
    root = tmp_path / "data"
    payload = b"metadata validation"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    storage.put_immutable(key, payload, content_type="application/pdf")
    metadata = sidecar_path(root, key)
    metadata.parent.mkdir(parents=True, mode=0o700)
    metadata.write_bytes(metadata_bytes)
    metadata.chmod(0o600)

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.merge_reconciliation_tags(
            key,
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    assert raised.value.failure_code == "object_storage_invalid_metadata"
    assert raised.value.__cause__ is None


def test_local_reconciliation_rejects_symlinked_lock_and_sidecar(tmp_path: Path) -> None:
    for use_lock in (False, True):
        root = tmp_path / ("lock" if use_lock else "sidecar")
        payload = f"metadata-link-{use_lock}".encode()
        key = source_key(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
        storage.put_immutable(key, payload, content_type="application/pdf")
        metadata = sidecar_path(root, key)
        metadata.parent.mkdir(parents=True, mode=0o700)
        target = tmp_path / f"metadata-target-{use_lock}"
        target.write_text("private", encoding="utf-8")
        linked = metadata.parent / (f".{checksum}.lock" if use_lock else metadata.name)
        linked.symlink_to(target)

        with pytest.raises(ObjectStorageOperationError) as raised:
            storage.merge_reconciliation_tags(
                key,
                candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        assert raised.value.failure_code == "object_storage_unsafe_path"


def test_local_error_mapping_duplicate_json_and_tag_validation_are_deterministic() -> None:
    cases = (
        (OSError(errno.ELOOP, "private"), None, "object_storage_unsafe_path"),
        (PermissionError(errno.EACCES, "private"), None, "object_storage_unsafe_permissions"),
        (FileNotFoundError(errno.ENOENT, "private"), "missing", "missing"),
        (OSError(errno.EIO, "private"), None, "fallback"),
    )
    for error, missing_code, expected in cases:
        mapped = storage_module._local_operation_error(
            error,
            "fallback",
            missing_code=missing_code,
        )
        assert mapped.failure_code == expected
        assert "private" not in str(mapped)

    with pytest.raises(ValueError, match="duplicate metadata key"):
        storage_module._unique_json_object([("duplicate", 1), ("duplicate", 2)])

    valid_tag = LocalFileObjectStorage._valid_local_tag
    assert valid_tag("operator", "keep") is True
    assert valid_tag(1, "keep") is False
    assert valid_tag("operator", 1) is False
    assert valid_tag("", "keep") is False
    assert valid_tag("x" * 129, "keep") is False
    assert valid_tag("operator", "x" * 257) is False
    assert valid_tag("operator\n", "keep") is False
    assert valid_tag("operator", "keep\x00") is False


def test_local_private_stat_and_directory_guards_reject_invalid_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = tmp_path / "regular"
    regular.write_bytes(b"value")
    regular.chmod(0o600)
    regular_fd = os.open(regular, os.O_RDONLY)
    try:
        with pytest.raises(ObjectStorageOperationError) as parent_error:
            LocalFileObjectStorage._assert_safe_parent(regular_fd)
        assert parent_error.value.failure_code == "object_storage_unsafe_path"
        with pytest.raises(ObjectStorageOperationError) as app_directory_error:
            LocalFileObjectStorage._secure_app_directory(regular_fd)
        assert app_directory_error.value.failure_code == "object_storage_unsafe_path"
    finally:
        os.close(regular_fd)

    monkeypatch.setattr(
        os,
        "fstat",
        lambda _fd: cast(
            os.stat_result,
            SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=os.geteuid() + 1),
        ),
    )
    with pytest.raises(ObjectStorageOperationError) as owner_error:
        LocalFileObjectStorage._secure_app_directory(1)
    assert owner_error.value.failure_code == "object_storage_unsafe_permissions"


def test_local_reconciliation_creates_sidecar_and_rejects_missing_or_oversized_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    payload = b"new sidecar"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=20)
    storage.put_immutable(key, payload, content_type="application/pdf")

    created = storage.merge_reconciliation_tags(
        key,
        candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert created.changed is True
    assert created.tag_count == 2
    assert sidecar_path(root, key).is_file()

    with pytest.raises(ObjectStorageOperationError) as missing:
        storage.merge_reconciliation_tags(
            source_key(b"missing source"),
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert missing.value.failure_code == "object_storage_not_found"

    oversized_payload = b"x" * 21
    oversized_key = source_key(oversized_payload)
    oversized = root / oversized_key
    oversized.parent.mkdir(mode=0o700, exist_ok=True)
    oversized.write_bytes(oversized_payload)
    oversized.chmod(0o600)
    with pytest.raises(ObjectStorageOperationError) as too_large:
        storage.merge_reconciliation_tags(
            oversized_key,
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert too_large.value.failure_code == "object_storage_read_too_large"


def test_local_sidecar_permission_corruption_is_sanitized(tmp_path: Path) -> None:
    root = tmp_path / "data"
    payload = b"sidecar permissions"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    storage.put_immutable(key, payload, content_type="application/pdf")
    metadata = sidecar_path(root, key)
    metadata.parent.mkdir(parents=True, mode=0o700)
    metadata.write_text(
        '{"application_tags":{},"operator_tags":{},"schema_version":1}',
        encoding="utf-8",
    )
    metadata.chmod(0o644)

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage.merge_reconciliation_tags(
            key,
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert raised.value.failure_code == "object_storage_unsafe_permissions"


def test_local_root_and_descriptor_os_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=10)
    storage.list_source_objects(max_keys=1)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, "dup", lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "private")))
        with pytest.raises(ObjectStorageOperationError) as duplicate_error:
            storage.list_source_objects(max_keys=1)
        assert duplicate_error.value.failure_code == "object_storage_open_failed"

    real_open = os.open
    unopened = LocalFileObjectStorage(root=tmp_path / "unopened", max_object_bytes=10)
    with monkeypatch.context() as scoped:

        def fail_root_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
            if path == "/":
                raise PermissionError(errno.EACCES, "private")
            return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

        scoped.setattr(os, "open", fail_root_open)
        with pytest.raises(ObjectStorageOperationError) as root_error:
            unopened.list_source_objects(max_keys=1)
        assert root_error.value.failure_code == "object_storage_unsafe_permissions"


def test_local_directory_creation_races_and_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=10)
    real_mkdir = os.mkdir
    real_open = os.open
    try:
        with monkeypatch.context() as scoped:

            def race_mkdir(path: str, mode: int, *, dir_fd: int | None = None) -> None:
                real_mkdir(path, mode, dir_fd=dir_fd)
                raise FileExistsError(errno.EEXIST, "raced")

            scoped.setattr(os, "mkdir", race_mkdir)
            raced_fd = storage._open_directory(parent_fd, "raced", create=True)
            os.close(raced_fd)

        with monkeypatch.context() as scoped:
            scoped.setattr(
                os,
                "mkdir",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    PermissionError(errno.EACCES, "private")
                ),
            )
            with pytest.raises(ObjectStorageOperationError) as create_error:
                storage._open_directory(parent_fd, "denied", create=True)
            assert create_error.value.failure_code == "object_storage_unsafe_permissions"

        with monkeypatch.context() as scoped:
            attempts = 0

            def fail_second_open(
                path: str,
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                nonlocal attempts
                if path == "blocked":
                    attempts += 1
                    if attempts == 1:
                        raise FileNotFoundError(errno.ENOENT, "missing")
                    raise OSError(errno.EIO, "private")
                return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

            scoped.setattr(os, "open", fail_second_open)
            with pytest.raises(ObjectStorageOperationError) as open_error:
                storage._open_directory(parent_fd, "blocked", create=True)
            assert open_error.value.failure_code == "object_storage_open_failed"

        with monkeypatch.context() as scoped:
            (parent / "unsafe-link").symlink_to(tmp_path)
            with pytest.raises(ObjectStorageOperationError) as linked:
                storage._open_directory(parent_fd, "unsafe-link", create=False)
            assert linked.value.failure_code == "object_storage_unsafe_path"

        (parent / "guarded").mkdir(mode=0o700)
        with monkeypatch.context() as scoped:
            scoped.setattr(
                LocalFileObjectStorage,
                "_secure_app_directory",
                staticmethod(
                    lambda _fd: (_ for _ in ()).throw(
                        ObjectStorageOperationError("object_storage_unsafe_permissions")
                    )
                ),
            )
            with pytest.raises(ObjectStorageOperationError):
                storage._open_directory(parent_fd, "guarded", create=False)
    finally:
        os.close(parent_fd)


def test_local_guard_syscall_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir(mode=0o700)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                os,
                "fstat",
                lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
            )
            with pytest.raises(ObjectStorageOperationError) as parent_error:
                LocalFileObjectStorage._assert_safe_parent(directory_fd)
            assert parent_error.value.failure_code == "object_storage_open_failed"

        with monkeypatch.context() as scoped:
            scoped.setattr(
                os,
                "fchmod",
                lambda *_args: (_ for _ in ()).throw(PermissionError(errno.EACCES, "private")),
            )
            with pytest.raises(ObjectStorageOperationError) as chmod_error:
                LocalFileObjectStorage._secure_app_directory(directory_fd)
            assert chmod_error.value.failure_code == "object_storage_unsafe_permissions"
    finally:
        os.close(directory_fd)


def test_local_read_write_and_publish_syscall_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    payload = b"syscall fixture"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    assert storage.list_source_objects(max_keys=1).objects == ()

    with monkeypatch.context() as scoped:
        real_open = os.open

        def deny_temporary(path: str, flags: int, *args: object, **kwargs: object) -> int:
            if path.startswith(".tmp-"):
                raise PermissionError(errno.EACCES, "private")
            return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

        scoped.setattr(os, "open", deny_temporary)
        with pytest.raises(ObjectStorageOperationError) as write_error:
            storage.put_immutable(key, payload, content_type="application/pdf")
        assert write_error.value.failure_code == "object_storage_unsafe_permissions"

    with monkeypatch.context() as scoped:
        scoped.setattr(
            os,
            "fsync",
            lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
        )
        with pytest.raises(ObjectStorageOperationError) as sync_error:
            storage.put_immutable(key, payload, content_type="application/pdf")
        assert sync_error.value.failure_code == "object_storage_write_failed"

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "write", lambda _fd, _data: 0)
        with pytest.raises(OSError, match="bounded write failed"):
            LocalFileObjectStorage._write_all(1, b"x")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            os,
            "link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
        )
        with pytest.raises(ObjectStorageOperationError) as link_error:
            storage.put_immutable(key, payload, content_type="application/pdf")
        assert link_error.value.failure_code == "object_storage_write_failed"


def test_local_scan_and_sidecar_syscall_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    payload = b"scan failure"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    storage.put_immutable(key, payload, content_type="application/pdf")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            os,
            "listdir",
            lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
        )
        with pytest.raises(ObjectStorageOperationError) as list_error:
            storage.list_source_objects(max_keys=1)
        assert list_error.value.failure_code == "object_storage_list_failed"

    with monkeypatch.context() as scoped:
        scoped.setattr(
            os,
            "stat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
        )
        with pytest.raises(ObjectStorageOperationError) as stat_error:
            storage.list_source_objects(max_keys=1)
        assert stat_error.value.failure_code == "object_storage_list_failed"

    with monkeypatch.context() as scoped:
        scoped.setattr(
            fcntl,
            "flock",
            lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
        )
        with pytest.raises(ObjectStorageOperationError) as lock_error:
            storage.merge_reconciliation_tags(
                key,
                candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        assert lock_error.value.failure_code == "object_storage_tag_write_failed"


def test_local_root_component_creation_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_mkdir = os.mkdir
    real_open = os.open

    denied = LocalFileObjectStorage(root=tmp_path / "root-denied", max_object_bytes=10)
    with monkeypatch.context() as scoped:

        def deny_root_mkdir(path: str, mode: int, *, dir_fd: int | None = None) -> None:
            if path == "root-denied":
                raise PermissionError(errno.EACCES, "private")
            real_mkdir(path, mode, dir_fd=dir_fd)

        scoped.setattr(os, "mkdir", deny_root_mkdir)
        with pytest.raises(ObjectStorageOperationError) as denied_error:
            denied.list_source_objects(max_keys=1)
        assert denied_error.value.failure_code == "object_storage_unsafe_permissions"

    raced = LocalFileObjectStorage(root=tmp_path / "root-raced", max_object_bytes=10)
    with monkeypatch.context() as scoped:

        def race_root_mkdir(path: str, mode: int, *, dir_fd: int | None = None) -> None:
            real_mkdir(path, mode, dir_fd=dir_fd)
            if path == "root-raced":
                raise FileExistsError(errno.EEXIST, "raced")

        scoped.setattr(os, "mkdir", race_root_mkdir)
        assert raced.list_source_objects(max_keys=1).objects == ()

    blocked = LocalFileObjectStorage(root=tmp_path / "root-blocked", max_object_bytes=10)
    with monkeypatch.context() as scoped:
        attempts = 0

        def block_second_root_open(
            path: str,
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            nonlocal attempts
            if path == "root-blocked":
                attempts += 1
                if attempts > 1:
                    raise OSError(errno.EIO, "private")
            return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

        scoped.setattr(os, "open", block_second_root_open)
        with pytest.raises(ObjectStorageOperationError) as blocked_error:
            blocked.list_source_objects(max_keys=1)
        assert blocked_error.value.failure_code == "object_storage_open_failed"


def test_local_source_read_and_stat_failure_paths_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    storage = LocalFileObjectStorage(root=root, max_object_bytes=5)
    assert storage.list_source_objects(max_keys=1).objects == ()
    payload = b"abcdef"
    key = source_key(payload)
    prefix = key.split("/")[1]
    parent = root / "sources" / prefix
    parent.mkdir(mode=0o700)

    with pytest.raises(ObjectStorageOperationError) as missing:
        storage.get_bytes(key)
    assert missing.value.failure_code == "object_storage_not_found"

    root_fd = storage._root_handle()
    try:
        parent_fd = storage._source_parent(root_fd, prefix=prefix, create=False)
    finally:
        os.close(root_fd)
    try:
        with monkeypatch.context() as scoped:
            real_open = os.open

            def fail_source_open(
                path: str,
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                if path == key.rsplit("/", maxsplit=1)[1]:
                    raise OSError(errno.EIO, "private")
                return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

            scoped.setattr(os, "open", fail_source_open)
            with pytest.raises(ObjectStorageOperationError) as stat_open_error:
                storage._stat_source_at(parent_fd, key.rsplit("/", maxsplit=1)[1])
            assert stat_open_error.value.failure_code == "object_storage_read_failed"

        source = root / key
        source.write_bytes(payload)
        source.chmod(0o600)
        with monkeypatch.context() as scoped:
            scoped.setattr(
                os,
                "fstat",
                lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
            )
            with pytest.raises(ObjectStorageOperationError) as stat_error:
                storage._stat_source_at(parent_fd, source.name)
            assert stat_error.value.failure_code == "object_storage_read_failed"

        actual = source.stat()
        bounded_details = cast(
            os.stat_result,
            SimpleNamespace(
                st_mode=actual.st_mode,
                st_nlink=actual.st_nlink,
                st_uid=actual.st_uid,
                st_size=5,
            ),
        )
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "fstat", lambda _fd: bounded_details)
            with pytest.raises(ObjectStorageOperationError) as grew:
                storage._read_source_at(
                    parent_fd,
                    filename=source.name,
                    expected_checksum=hashlib.sha256(payload).hexdigest(),
                    missing_ok=False,
                )
            assert grew.value.failure_code == "object_storage_read_too_large"

        with monkeypatch.context() as scoped:
            scoped.setattr(os, "fstat", lambda _fd: bounded_details)
            scoped.setattr(
                os,
                "read",
                lambda *_args: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
            )
            with pytest.raises(ObjectStorageOperationError) as read_error:
                storage._read_source_at(
                    parent_fd,
                    filename=source.name,
                    expected_checksum=hashlib.sha256(payload).hexdigest(),
                    missing_ok=False,
                )
            assert read_error.value.failure_code == "object_storage_read_failed"
    finally:
        os.close(parent_fd)


def test_local_publish_directory_sync_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    payload = b"unlink failure"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    real_unlink = os.unlink

    with monkeypatch.context() as scoped:

        def fail_temp_unlink(path: str, *args: object, **kwargs: object) -> None:
            if path.startswith(".tmp-"):
                raise OSError(errno.EIO, "private")
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        scoped.setattr(os, "unlink", fail_temp_unlink)
        with pytest.raises(ObjectStorageOperationError) as raised:
            storage.put_immutable(key, payload, content_type="application/pdf")
        assert raised.value.failure_code == "object_storage_write_failed"

    assert (root / key).read_bytes() == payload


def test_local_prefix_scan_read_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalFileObjectStorage(root=tmp_path / "data", max_object_bytes=1_024)
    payload = b"prefix scan"
    storage.put_immutable(source_key(payload), payload, content_type="application/pdf")
    real_listdir = os.listdir
    calls = 0

    def fail_prefix_list(fd: int) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "private")
        return real_listdir(fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "listdir", fail_prefix_list)
        with pytest.raises(ObjectStorageOperationError) as raised:
            storage.list_source_objects(max_keys=1)
    assert raised.value.failure_code == "object_storage_list_failed"


def test_local_sidecar_read_length_and_write_failures_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    payload = b"sidecar io"
    key = source_key(payload)
    storage = LocalFileObjectStorage(root=root, max_object_bytes=1_024)
    storage.put_immutable(key, payload, content_type="application/pdf")
    metadata = sidecar_path(root, key)
    metadata.parent.mkdir(parents=True, mode=0o700)
    metadata.write_text(
        '{"application_tags":{},"operator_tags":{},"schema_version":1}',
        encoding="utf-8",
    )
    metadata.chmod(0o600)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "read", lambda _fd, _amount: b"{}")
        with pytest.raises(ObjectStorageOperationError) as short_read:
            storage.merge_reconciliation_tags(
                key,
                candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        assert short_read.value.failure_code == "object_storage_invalid_metadata"

    root_fd = storage._root_handle()
    try:
        metadata_fd = storage._metadata_parent(root_fd, key.split("/")[1])
    finally:
        os.close(root_fd)
    try:
        with pytest.raises(ObjectStorageOperationError) as oversized:
            storage._write_sidecar(
                metadata_fd,
                "oversized.json",
                {"value": "x" * (17 * 1024)},
            )
        assert oversized.value.failure_code == "object_storage_invalid_metadata"
    finally:
        os.close(metadata_fd)

    metadata.unlink()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            os,
            "replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.EIO, "private")),
        )
        with pytest.raises(ObjectStorageOperationError) as write_error:
            storage.merge_reconciliation_tags(
                key,
                candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        assert write_error.value.failure_code == "object_storage_tag_write_failed"
