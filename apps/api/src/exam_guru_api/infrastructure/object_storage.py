from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from exam_guru_api.core.config import Settings

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


class InvalidObjectKeyError(ValueError):
    def __init__(self, _key: object = None) -> None:
        super().__init__("invalid object key")


class ObjectAlreadyExistsError(RuntimeError):
    pass


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
        raise ObjectAlreadyExistsError(existing.key)


def create_object_storage(settings: Settings) -> S3ObjectStorage:
    return S3ObjectStorage(
        endpoint_url=settings.object_storage_endpoint_url,
        access_key_id=settings.object_storage_access_key.get_secret_value(),
        secret_access_key=settings.object_storage_secret_key.get_secret_value(),
        bucket=settings.object_storage_bucket,
        region=settings.object_storage_region,
    )
