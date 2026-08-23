from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import boto3
from botocore.exceptions import ClientError

from exam_guru_api.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.literals import BucketLocationConstraintType


class InvalidObjectKeyError(ValueError):
    pass


class ObjectAlreadyExistsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    checksum_sha256: str
    size: int
    etag: str


class ObjectStorage(Protocol):
    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject: ...

    def get_bytes(self, key: str) -> bytes: ...


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


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
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
        response = self._client.get_object(Bucket=self._bucket, Key=validate_object_key(key))
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

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
        raise ObjectAlreadyExistsError(existing.key)


def create_object_storage(settings: Settings) -> S3ObjectStorage:
    return S3ObjectStorage(
        endpoint_url=settings.object_storage_endpoint_url,
        access_key_id=settings.object_storage_access_key.get_secret_value(),
        secret_access_key=settings.object_storage_secret_key.get_secret_value(),
        bucket=settings.object_storage_bucket,
        region=settings.object_storage_region,
    )
