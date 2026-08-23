import hashlib
from typing import cast

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client

from exam_guru_api.infrastructure.object_storage import (
    InvalidObjectKeyError,
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
    ) -> None:
        self.head_bucket_results = head_bucket_results or []
        self.head_object_results = head_object_results or []
        self.put_object_result = put_object_result or {"ETag": '"etag"'}
        self.created_buckets: list[dict[str, object]] = []

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
