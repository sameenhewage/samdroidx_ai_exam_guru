import asyncio
import hashlib
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from exam_guru_api.cli import main as run_api
from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.main import create_app


class StubResources:
    def __init__(self, *, database_available: bool = True, valkey_available: bool = True) -> None:
        self.database_available = database_available
        self.valkey_available = valkey_available
        self.closed = False

    async def check_database(self) -> None:
        if not self.database_available:
            raise RuntimeError

    async def check_valkey(self) -> None:
        if not self.valkey_available:
            raise RuntimeError

    async def close(self) -> None:
        self.closed = True


class SlowDatabaseResources(StubResources):
    async def check_database(self) -> None:
        await asyncio.sleep(0.05)


class ClosingStorage:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_application_exposes_injected_page_reading_dispatcher() -> None:
    from exam_guru_api.documents.page_reading_jobs import SourceReadDispatcher

    dispatcher = cast(SourceReadDispatcher, object())
    app = create_app(source_read_dispatcher=dispatcher)
    assert app.state.source_read_dispatcher is dispatcher


def test_application_initializes_lazy_private_upload_factory_and_operational_quotas(
    tmp_path: Path,
) -> None:
    from exam_guru_api.documents.resumable_uploads import UploadLimits
    from exam_guru_api.infrastructure.private_artifacts import PrivateUploadArtifacts

    root = tmp_path / "durable"
    settings = Settings.model_validate(
        {
            "environment": "test",
            "storage_root": str(root),
            "max_upload_bytes": 1_024,
            "source_upload_max_owner_staged_bytes": 9 * 1024**3,
            "source_upload_max_staged_bytes": 40 * 1024**3,
            "source_upload_max_active_sessions_per_owner": 3,
        }
    )
    app = create_app(settings=settings)
    limits = app.state.source_upload_limits
    artifacts = app.state.source_upload_artifacts
    assert isinstance(limits, UploadLimits)
    assert isinstance(artifacts, PrivateUploadArtifacts)
    assert limits.max_total_bytes == 2**63 - 1
    assert limits.max_owner_staged_bytes == 9 * 1024**3
    assert limits.max_staged_bytes == 40 * 1024**3
    assert limits.max_active_sessions_per_owner == 3
    assert settings.max_upload_bytes == 1_024
    assert not root.exists()
    upload_id = uuid4()
    data = b"%PDF-private fixture"
    artifacts.put_chunk(upload_id, 0, data, checksum_sha256=hashlib.sha256(data).hexdigest())
    assert (root / ".source-uploads" / upload_id.hex / "0000000000000000.chunk").is_file()
    app.state.object_storage.close()


def test_application_uses_default_upload_budgets_without_a_product_file_cap() -> None:
    app = create_app(settings=Settings(environment="test"))
    limits = app.state.source_upload_limits
    assert limits.max_total_bytes == 2**63 - 1
    assert limits.max_owner_staged_bytes == 8 * 1024**3
    assert limits.max_staged_bytes == 32 * 1024**3
    app.state.object_storage.close()


def test_application_exposes_injected_upload_dispatcher() -> None:
    from exam_guru_api.documents.upload_jobs import SourceUploadDispatcher

    dispatcher = cast(SourceUploadDispatcher, object())
    app = create_app(source_upload_dispatcher=dispatcher)
    assert app.state.source_upload_dispatcher is dispatcher
    app.state.object_storage.close()


def test_s3_upload_factory_fails_explicitly_without_staging_or_database_access(
    tmp_path: Path,
) -> None:
    from exam_guru_api.api.dependencies import get_database_session
    from exam_guru_api.auth.domain import AdminRole, Principal
    from exam_guru_api.auth.rate_limits import NoOpRateLimiter

    class Identity:
        async def authenticate(self, _token: str) -> Principal:
            return Principal(UUID(int=36020), frozenset({AdminRole.ADMIN}))

    root = tmp_path / "unsupported"
    settings = Settings(
        environment="test",
        storage_backend="s3",
        storage_root=str(root),
        object_storage_endpoint_url="http://localhost:9000",
        object_storage_access_key="fixture-access",
        object_storage_secret_key="fixture-secret",
        object_storage_bucket="fixture-sources",
        object_storage_region="us-east-1",
    )
    session = AsyncMock()
    app = create_app(
        settings=settings, identity_provider=Identity(), rate_limiter=NoOpRateLimiter()
    )
    app.dependency_overrides[get_database_session] = lambda: session
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/admin/source-uploads",
            headers={"Authorization": "Bearer fixture"},
            json={
                "filename": "large.pdf",
                "size_bytes": 300 * 1024**2,
                "document_type": "syllabus",
            },
        )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "source_upload_storage_unsupported"}}
    assert not session.mock_calls
    assert not root.exists()


def test_source_review_runtime_routes_are_registered_on_the_root_application() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/admin/studio-safety/runtime-identity" in paths
    assert "/api/v1/admin/materials/{document_id}/pages/{page_number}/image" in paths
    assert "/api/v1/admin/materials/{document_id}/original" in paths


def test_liveness_contract() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_oversized_declared_request_is_rejected_before_body_parsing() -> None:
    settings = Settings(max_upload_bytes=1_024)

    with TestClient(create_app(settings=settings)) as client:
        response = client.post(
            "/api/v1/admin/source-documents",
            content=b"",
            headers={"Content-Length": str(2 * 1024 * 1024)},
        )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_request_id_is_preserved_when_it_is_a_valid_uuid() -> None:
    request_id = str(uuid4())

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": request_id})

    assert response.headers["X-Request-ID"] == request_id


def test_invalid_request_id_is_replaced() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": "untrusted-value"})

    generated_request_id = response.headers["X-Request-ID"]
    assert generated_request_id != "untrusted-value"
    assert str(UUID(generated_request_id)) == generated_request_id


def test_readiness_reports_dependencies_and_closes_resources() -> None:
    resources = StubResources()

    with TestClient(create_app(resource_factory=lambda _: resources)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "valkey": "ok"},
    }
    assert resources.closed


def test_application_shutdown_closes_object_storage_once() -> None:
    storage = ClosingStorage()

    with TestClient(create_app(object_storage=cast(ObjectStorage, storage))) as client:
        assert client.get("/api/v1/health/live").status_code == 200

    assert storage.close_calls == 1


def test_readiness_returns_service_unavailable_without_leaking_errors() -> None:
    resources = StubResources(database_available=False)

    with TestClient(create_app(resource_factory=lambda _: resources)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "unavailable", "valkey": "ok"},
    }


def test_readiness_times_out_slow_dependencies() -> None:
    resources = SlowDatabaseResources()
    settings = Settings(readiness_timeout_seconds=0.01)

    with TestClient(create_app(settings=settings, resource_factory=lambda _: resources)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "unavailable", "valkey": "ok"},
    }


def test_openapi_contract_identifies_service_and_liveness_schema() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"] == {
        "title": "AI Exam Guru API",
        "version": "0.1.0",
    }
    operation = schema["paths"]["/api/v1/health/live"]["get"]
    assert operation["operationId"] == "get_liveness"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }
    readiness_operation = schema["paths"]["/api/v1/health/ready"]["get"]
    assert readiness_operation["operationId"] == "get_readiness"
    assert readiness_operation["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReadinessResponse"
    }
    taxonomy_path = "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes"
    taxonomy_operation = schema["paths"][taxonomy_path]["post"]
    assert taxonomy_operation["operationId"] == "create_taxonomy_node"
    assert taxonomy_operation["security"] == [{"HTTPBearer": []}]
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "scheme": "bearer",
        "type": "http",
    }


def test_cli_starts_the_api_server(monkeypatch: pytest.MonkeyPatch) -> None:
    invocation: dict[str, object] = {}

    def capture_run(app_path: str, *, host: str, port: int) -> None:
        invocation.update(app_path=app_path, host=host, port=port)

    monkeypatch.setattr("exam_guru_api.cli.uvicorn.run", capture_run)

    run_api()

    assert invocation == {
        "app_path": "exam_guru_api.main:app",
        "host": "0.0.0.0",
        "port": 8000,
    }
