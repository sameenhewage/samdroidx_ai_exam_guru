import hashlib
from datetime import UTC, datetime
from typing import cast

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client

from exam_guru_api.infrastructure.object_storage import (
    APP_ORPHAN_CANDIDATE_TAG,
    APP_ORPHAN_DETECTED_AT_TAG,
    InvalidObjectKeyError,
    ObjectStorageOperationError,
    ObjectTagCapacityError,
    S3ObjectStorage,
    validate_object_key,
)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/sources/file.pdf",
        "sources//file.pdf",
        "sources/../file.pdf",
        "sources\\file.pdf",
        "sources/file\x00.pdf",
        "sources/file\n.pdf",
        "sources/file\r.pdf",
    ],
)
def test_object_storage_rejects_unsafe_keys(key: str) -> None:
    with pytest.raises(InvalidObjectKeyError):
        validate_object_key(key)


@pytest.mark.parametrize("key", ["sources/file.pdf", "grade-5/si/2025-paper-1.pdf"])
def test_object_storage_accepts_partitioned_keys(key: str) -> None:
    assert validate_object_key(key) == key


def s3_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class ScriptedS3Client:
    def __init__(
        self,
        *,
        head_bucket_results: list[dict[str, object] | ClientError] | None = None,
        head_object_results: list[dict[str, object] | ClientError] | None = None,
        put_object_result: dict[str, object] | ClientError | None = None,
        list_results: list[dict[str, object] | ClientError] | None = None,
        tag_results: list[dict[str, object] | ClientError] | None = None,
        put_tag_result: dict[str, object] | ClientError | None = None,
    ) -> None:
        self.head_bucket_results = head_bucket_results or []
        self.head_object_results = head_object_results or []
        self.put_object_result = put_object_result or {"ETag": '"etag"'}
        self.list_results = list_results or []
        self.tag_results = tag_results or []
        self.put_tag_result = put_tag_result or {}
        self.created_buckets: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.get_tag_calls: list[dict[str, object]] = []
        self.put_tag_calls: list[dict[str, object]] = []
        self.closed = False

    @staticmethod
    def _resolve(result: dict[str, object] | ClientError) -> dict[str, object]:
        if isinstance(result, ClientError):
            raise result
        return result

    def head_bucket(self, **_: object) -> dict[str, object]:
        return self._resolve(self.head_bucket_results.pop(0))

    def create_bucket(self, **values: object) -> dict[str, object]:
        self.created_buckets.append(values)
        return {}

    def head_object(self, **_: object) -> dict[str, object]:
        return self._resolve(self.head_object_results.pop(0))

    def put_object(self, **_: object) -> dict[str, object]:
        return self._resolve(self.put_object_result)

    def list_objects_v2(self, **values: object) -> dict[str, object]:
        self.list_calls.append(values)
        return self._resolve(self.list_results.pop(0))

    def get_object_tagging(self, **values: object) -> dict[str, object]:
        self.get_tag_calls.append(values)
        return self._resolve(self.tag_results.pop(0))

    def put_object_tagging(self, **values: object) -> dict[str, object]:
        self.put_tag_calls.append(values)
        return self._resolve(self.put_tag_result)

    def close(self) -> None:
        self.closed = True


def storage_with_client(
    client: ScriptedS3Client,
    *,
    region: str = "us-east-1",
) -> S3ObjectStorage:
    storage = S3ObjectStorage(
        endpoint_url="http://localhost:9000",
        access_key_id="test-access",
        secret_access_key="x",
        bucket="test-bucket",
        region=region,
    )
    storage._client = cast(S3Client, client)
    return storage


def test_s3_client_has_bounded_timeouts_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def capture_client(_service: str, **kwargs: object) -> ScriptedS3Client:
        captured.update(kwargs)
        return ScriptedS3Client()

    monkeypatch.setattr(boto3, "client", capture_client)
    S3ObjectStorage(
        endpoint_url="http://localhost:9000",
        access_key_id="test-access",
        secret_access_key="x",
        bucket="test-bucket",
        region="us-east-1",
    )

    config = cast(Config, captured["config"])
    config_values = vars(config)
    assert config_values["connect_timeout"] == 5
    assert config_values["read_timeout"] == 30
    assert cast(dict[str, object], config_values["retries"])["total_max_attempts"] == 3


def test_existing_bucket_is_not_recreated() -> None:
    client = ScriptedS3Client(head_bucket_results=[{}])

    storage_with_client(client).ensure_bucket()

    assert client.created_buckets == []


def test_bucket_access_error_is_not_hidden() -> None:
    error = s3_error("AccessDenied", "HeadBucket")
    client = ScriptedS3Client(head_bucket_results=[error])

    with pytest.raises(ClientError) as raised:
        storage_with_client(client).ensure_bucket()

    assert raised.value is error


def test_regional_bucket_creation_sets_location_constraint() -> None:
    client = ScriptedS3Client(head_bucket_results=[s3_error("404", "HeadBucket")])

    storage_with_client(client, region="eu-west-1").ensure_bucket()

    assert client.created_buckets == [
        {
            "Bucket": "test-bucket",
            "CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"},
        }
    ]


def test_unexpected_put_error_is_not_hidden() -> None:
    error = s3_error("AccessDenied", "PutObject")
    client = ScriptedS3Client(
        head_object_results=[s3_error("404", "HeadObject")],
        put_object_result=error,
    )

    with pytest.raises(ClientError) as raised:
        storage_with_client(client).put_immutable(
            "sources/file.pdf", b"data", content_type="text/plain"
        )

    assert raised.value is error


def test_conditional_write_race_resolves_an_idempotent_retry() -> None:
    payload = b"data"
    existing = {
        "ContentLength": len(payload),
        "ETag": '"existing"',
        "Metadata": {"sha256": hashlib.sha256(payload).hexdigest()},
    }
    client = ScriptedS3Client(
        head_object_results=[s3_error("404", "HeadObject"), existing],
        put_object_result=s3_error("PreconditionFailed", "PutObject"),
    )

    stored = storage_with_client(client).put_immutable(
        "sources/file.pdf",
        payload,
        content_type="text/plain",
    )

    assert stored.etag == "existing"


def test_conditional_write_race_reraises_when_object_disappears() -> None:
    error = s3_error("PreconditionFailed", "PutObject")
    client = ScriptedS3Client(
        head_object_results=[s3_error("404", "HeadObject"), s3_error("404", "HeadObject")],
        put_object_result=error,
    )

    with pytest.raises(ClientError) as raised:
        storage_with_client(client).put_immutable(
            "sources/file.pdf", b"data", content_type="text/plain"
        )

    assert raised.value is error


def test_unexpected_head_error_is_not_hidden() -> None:
    error = s3_error("AccessDenied", "HeadObject")
    client = ScriptedS3Client(head_object_results=[error])

    with pytest.raises(ClientError) as raised:
        storage_with_client(client).put_immutable(
            "sources/file.pdf", b"data", content_type="text/plain"
        )

    assert raised.value is error


def canonical_source_key(digit: str) -> str:
    checksum = digit * 64
    return f"sources/{checksum[:2]}/{checksum}.pdf"


def test_source_listing_is_exact_prefix_bounded_paginated_and_uses_list_metadata_only() -> None:
    observed_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    first_key = canonical_source_key("a")
    second_key = canonical_source_key("b")
    client = ScriptedS3Client(
        list_results=[
            {
                "Contents": [{"Key": first_key, "Size": 7, "LastModified": observed_at}],
                "IsTruncated": True,
                "NextContinuationToken": "opaque-next-token",
            },
            {
                "Contents": [{"Key": second_key, "Size": 9, "LastModified": observed_at}],
                "IsTruncated": False,
            },
        ]
    )
    storage = storage_with_client(client)

    first = storage.list_source_objects(max_keys=1)
    second = storage.list_source_objects(
        max_keys=1,
        continuation_token=first.next_continuation_token,
    )

    assert [(item.key, item.size, item.last_modified) for item in first.objects] == [
        (first_key, 7, observed_at)
    ]
    assert first.is_truncated is True
    assert first.next_continuation_token == "opaque-next-token"
    assert [item.key for item in second.objects] == [second_key]
    assert second.is_truncated is False
    assert second.next_continuation_token is None
    assert client.list_calls == [
        {"Bucket": "test-bucket", "Prefix": "sources/", "MaxKeys": 1},
        {
            "Bucket": "test-bucket",
            "Prefix": "sources/",
            "MaxKeys": 1,
            "ContinuationToken": "opaque-next-token",
        },
    ]
    assert client.head_object_results == []


@pytest.mark.parametrize("max_keys", [0, 1_001, True])
def test_source_listing_rejects_unbounded_page_sizes(max_keys: int) -> None:
    client = ScriptedS3Client()

    with pytest.raises(ValueError, match="max_keys"):
        storage_with_client(client).list_source_objects(max_keys=max_keys)

    assert client.list_calls == []


@pytest.mark.parametrize("token", ["", "x" * 2_049, "unsafe\ncontinuation"])
def test_source_listing_rejects_invalid_continuation_tokens(token: str) -> None:
    client = ScriptedS3Client()

    with pytest.raises(ValueError, match="continuation"):
        storage_with_client(client).list_source_objects(
            max_keys=10,
            continuation_token=token,
        )

    assert client.list_calls == []


@pytest.mark.parametrize(
    "result",
    [
        {
            "Contents": [
                {
                    "Key": "sources/../unsafe.pdf",
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ],
            "IsTruncated": False,
        },
        {
            "Contents": [
                {
                    "Key": "other/prefix.pdf",
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ],
            "IsTruncated": False,
        },
        {
            "Contents": [
                {
                    "Key": canonical_source_key("c"),
                    "Size": -1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ],
            "IsTruncated": False,
        },
        {
            "Contents": [
                {
                    "Key": canonical_source_key("d"),
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1),
                }
            ],
            "IsTruncated": False,
        },
        {"Contents": "not-a-list", "IsTruncated": False},
        {"Contents": [], "IsTruncated": "not-a-boolean"},
        {"Contents": [42], "IsTruncated": False},
        {
            "Contents": [
                {
                    "Key": 42,
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ],
            "IsTruncated": False,
        },
        {
            "Contents": [
                {
                    "Key": canonical_source_key("4"),
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                },
                {
                    "Key": canonical_source_key("4"),
                    "Size": 1,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                },
            ],
            "IsTruncated": False,
        },
        {"Contents": [], "IsTruncated": False, "NextContinuationToken": "unexpected"},
        {"Contents": [], "IsTruncated": True},
    ],
)
def test_source_listing_rejects_unsafe_or_malformed_provider_results_without_key_leak(
    result: dict[str, object],
) -> None:
    client = ScriptedS3Client(list_results=[result])

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage_with_client(client).list_source_objects(max_keys=10)

    assert raised.value.failure_code == "object_storage_invalid_response"
    assert "unsafe" not in str(raised.value)
    assert "other/prefix" not in str(raised.value)


def test_source_listing_rejects_provider_page_larger_than_requested_bound() -> None:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    client = ScriptedS3Client(
        list_results=[
            {
                "Contents": [
                    {
                        "Key": canonical_source_key("5"),
                        "Size": 1,
                        "LastModified": observed_at,
                    },
                    {
                        "Key": canonical_source_key("6"),
                        "Size": 1,
                        "LastModified": observed_at,
                    },
                ],
                "IsTruncated": False,
            }
        ]
    )

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage_with_client(client).list_source_objects(max_keys=1)

    assert raised.value.failure_code == "object_storage_invalid_response"


def test_source_listing_sanitizes_provider_errors() -> None:
    client = ScriptedS3Client(
        list_results=[s3_error("AccessDenied-private-bucket-name", "ListObjectsV2")]
    )

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage_with_client(client).list_source_objects(max_keys=10)

    assert raised.value.failure_code == "object_storage_list_failed"
    assert "private" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_reconciliation_tag_merge_rejects_naive_detected_timestamp_before_provider_call() -> None:
    client = ScriptedS3Client()

    with pytest.raises(ValueError, match="timezone"):
        storage_with_client(client).merge_reconciliation_tags(
            canonical_source_key("7"),
            candidate_detected_at=datetime(2026, 1, 1),
        )

    assert client.get_tag_calls == []


def test_reconciliation_tag_merge_preserves_operator_tags_and_writes_only_app_tags() -> None:
    key = canonical_source_key("e")
    detected_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    client = ScriptedS3Client(
        tag_results=[
            {
                "TagSet": [
                    {"Key": "operator-retention", "Value": "legal-hold"},
                    {"Key": APP_ORPHAN_CANDIDATE_TAG, "Value": "old"},
                ]
            }
        ]
    )

    result = storage_with_client(client).merge_reconciliation_tags(
        key,
        candidate_detected_at=detected_at,
    )

    assert result.changed is True
    assert result.tag_count == 3
    assert client.get_tag_calls == [{"Bucket": "test-bucket", "Key": key}]
    assert client.put_tag_calls == [
        {
            "Bucket": "test-bucket",
            "Key": key,
            "Tagging": {
                "TagSet": [
                    {"Key": APP_ORPHAN_CANDIDATE_TAG, "Value": "true"},
                    {"Key": APP_ORPHAN_DETECTED_AT_TAG, "Value": "2026-01-02T03:04:05Z"},
                    {"Key": "operator-retention", "Value": "legal-hold"},
                ]
            },
        }
    ]


def test_reconciliation_tag_removal_removes_only_app_owned_tags() -> None:
    key = canonical_source_key("f")
    client = ScriptedS3Client(
        tag_results=[
            {
                "TagSet": [
                    {"Key": APP_ORPHAN_CANDIDATE_TAG, "Value": "true"},
                    {"Key": APP_ORPHAN_DETECTED_AT_TAG, "Value": "2026-01-01T00:00:00Z"},
                    {"Key": "operator-owner", "Value": "records"},
                ]
            }
        ]
    )

    result = storage_with_client(client).merge_reconciliation_tags(
        key,
        candidate_detected_at=None,
    )

    assert result.changed is True
    assert result.tag_count == 1
    assert client.put_tag_calls[0]["Tagging"] == {
        "TagSet": [{"Key": "operator-owner", "Value": "records"}]
    }


def test_reconciliation_tag_merge_skips_write_when_owned_tags_already_match() -> None:
    key = canonical_source_key("1")
    detected_at = datetime(2026, 1, 1, tzinfo=UTC)
    client = ScriptedS3Client(
        tag_results=[
            {
                "TagSet": [
                    {"Key": APP_ORPHAN_CANDIDATE_TAG, "Value": "true"},
                    {"Key": APP_ORPHAN_DETECTED_AT_TAG, "Value": "2026-01-01T00:00:00Z"},
                ]
            }
        ]
    )

    result = storage_with_client(client).merge_reconciliation_tags(
        key,
        candidate_detected_at=detected_at,
    )

    assert result.changed is False
    assert result.tag_count == 2
    assert client.put_tag_calls == []


def test_reconciliation_tag_merge_rejects_capacity_conflict_without_overwriting_tags() -> None:
    key = canonical_source_key("2")
    operator_tags = [{"Key": f"operator-{index}", "Value": "keep"} for index in range(9)]
    client = ScriptedS3Client(tag_results=[{"TagSet": operator_tags}])

    with pytest.raises(ObjectTagCapacityError) as raised:
        storage_with_client(client).merge_reconciliation_tags(
            key,
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    assert raised.value.failure_code == "object_storage_tag_capacity_conflict"
    assert client.put_tag_calls == []


@pytest.mark.parametrize(
    ("tag_results", "put_result", "failure_code"),
    [
        (
            [s3_error("AccessDenied-private", "GetObjectTagging")],
            None,
            "object_storage_tag_read_failed",
        ),
        (
            [{"TagSet": []}],
            s3_error("AccessDenied-private", "PutObjectTagging"),
            "object_storage_tag_write_failed",
        ),
        (
            [{"TagSet": [{"Key": APP_ORPHAN_CANDIDATE_TAG, "Value": 4}]}],
            None,
            "object_storage_invalid_response",
        ),
        ([{"TagSet": "not-a-list"}], None, "object_storage_invalid_response"),
        ([{"TagSet": [42]}], None, "object_storage_invalid_response"),
        (
            [{"TagSet": [{"Key": f"tag-{index}", "Value": "x"} for index in range(11)]}],
            None,
            "object_storage_invalid_response",
        ),
        (
            [
                {
                    "TagSet": [
                        {"Key": "operator", "Value": "first"},
                        {"Key": "operator", "Value": "duplicate"},
                    ]
                }
            ],
            None,
            "object_storage_invalid_response",
        ),
    ],
)
def test_reconciliation_tag_errors_are_sanitized(
    tag_results: list[dict[str, object] | ClientError],
    put_result: dict[str, object] | ClientError | None,
    failure_code: str,
) -> None:
    client = ScriptedS3Client(tag_results=tag_results, put_tag_result=put_result)

    with pytest.raises(ObjectStorageOperationError) as raised:
        storage_with_client(client).merge_reconciliation_tags(
            canonical_source_key("3"),
            candidate_detected_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    assert raised.value.failure_code == failure_code
    assert "private" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_storage_close_closes_the_sdk_client() -> None:
    client = ScriptedS3Client()

    storage_with_client(client).close()

    assert client.closed is True
