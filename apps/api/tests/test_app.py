import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from exam_guru_api.cli import main as run_api
from exam_guru_api.core.config import Settings
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


def test_liveness_contract() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
