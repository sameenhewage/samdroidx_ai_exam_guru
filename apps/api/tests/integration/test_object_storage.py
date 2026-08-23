from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.object_storage import (
    ObjectAlreadyExistsError,
    S3ObjectStorage,
    create_object_storage,
)

MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"
MINIO_ACCESS_KEY = "integration-access"
MINIO_SECRET_KEY = "integration-secret"


@pytest.fixture(scope="module")
def object_storage() -> Iterator[S3ObjectStorage]:
    with (
        DockerContainer(MINIO_IMAGE)
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data --console-address :9001")
        .with_exposed_ports(9000)
        .waiting_for(LogMessageWaitStrategy("API:"))
    ) as minio:
        host = minio.get_container_host_ip()
        port = minio.get_exposed_port(9000)
        settings = Settings(
            object_storage_endpoint_url=f"http://{host}:{port}",
            object_storage_access_key=SecretStr(MINIO_ACCESS_KEY),
            object_storage_secret_key=SecretStr(MINIO_SECRET_KEY),
            object_storage_bucket="exam-guru-integration",
        )
        storage = create_object_storage(settings)
        storage.ensure_bucket()
        yield storage


@pytest.mark.integration
def test_immutable_object_round_trip_and_idempotent_retry(object_storage: S3ObjectStorage) -> None:
    key = "sources/grade-5/fixture.pdf"
    payload = b"representative fixture bytes"

    stored = object_storage.put_immutable(key, payload, content_type="application/pdf")
    retried = object_storage.put_immutable(key, payload, content_type="application/pdf")

    assert retried == stored
    assert stored.size == len(payload)
    assert object_storage.get_bytes(key) == payload


@pytest.mark.integration
def test_immutable_object_rejects_different_content_at_existing_key(
    object_storage: S3ObjectStorage,
) -> None:
    key = "sources/grade-5/conflict.pdf"
    object_storage.put_immutable(key, b"original", content_type="application/pdf")

    with pytest.raises(ObjectAlreadyExistsError):
        object_storage.put_immutable(key, b"replacement", content_type="application/pdf")
