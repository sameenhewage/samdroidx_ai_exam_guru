import pytest
import sentry_sdk
from fastapi import FastAPI
from pydantic import SecretStr

from exam_guru_api.core.config import Settings
from exam_guru_api.observability import (
    ObservabilityRuntime,
    configure_observability,
    configure_worker_observability,
    get_operational_telemetry,
)
from exam_guru_api.worker import WorkerObservabilityMiddleware


def test_configured_observability_enables_otlp_and_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_configuration: dict[str, object] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **values: sentry_configuration.update(values))
    settings = Settings(
        environment="test",
        otel_exporter_otlp_endpoint="http://localhost:4318/v1/traces",
        sentry_dsn=SecretStr("https://public@example.com/1"),
        trace_sample_ratio=0.25,
    )

    runtime = configure_observability(FastAPI(), settings)
    runtime.shutdown()

    assert runtime.service_name == "exam-guru-api"
    assert sentry_configuration == {
        "dsn": "https://public@example.com/1",
        "enable_tracing": True,
        "environment": "test",
        "send_default_pii": False,
        "traces_sample_rate": 0.25,
    }


def test_api_observability_installs_and_safely_replaces_global_operational_telemetry() -> None:
    runtimes: list[ObservabilityRuntime] = []
    try:
        first = configure_observability(FastAPI(), Settings(environment="test"))
        runtimes.append(first)
        assert get_operational_telemetry() is first.operational_telemetry

        second = configure_observability(FastAPI(), Settings(environment="test"))
        runtimes.append(second)
        assert get_operational_telemetry() is second.operational_telemetry

        first.shutdown()
        first.shutdown()
        assert first.is_shutdown is True
        assert get_operational_telemetry() is second.operational_telemetry
    finally:
        for runtime in runtimes:
            runtime.shutdown()
            runtime.shutdown()

    assert all(runtime.is_shutdown for runtime in runtimes)


def test_worker_observability_uses_worker_service_name_and_flushes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_configuration: dict[str, object] = {}
    flush_timeouts: list[float] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **values: sentry_configuration.update(values))
    monkeypatch.setattr(sentry_sdk, "flush", lambda timeout: flush_timeouts.append(timeout))
    settings = Settings(
        environment="test",
        sentry_dsn=SecretStr("https://public@example.com/1"),
        otel_service_name="exam-guru-control",
        trace_sample_ratio=0.5,
    )

    runtime = configure_worker_observability(settings)
    runtime.shutdown()
    runtime.shutdown()

    assert runtime.service_name == "exam-guru-control-worker"
    assert runtime.tracer_provider.resource.attributes["service.name"] == (
        "exam-guru-control-worker"
    )
    assert runtime.is_shutdown is True
    assert sentry_configuration == {
        "dsn": "https://public@example.com/1",
        "enable_tracing": True,
        "environment": "test",
        "send_default_pii": False,
        "server_name": "exam-guru-control-worker",
        "traces_sample_rate": 0.5,
    }
    assert flush_timeouts == [2.0]


def test_worker_middleware_closes_observability_runtime() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.shutdown_calls = 0

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    runtime = Runtime()
    middleware = WorkerObservabilityMiddleware(runtime)  # type: ignore[arg-type]

    middleware.after_worker_shutdown(object(), object())

    assert runtime.shutdown_calls == 1


def test_runtime_shutdown_without_sentry_does_not_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    flush_timeouts: list[float] = []
    monkeypatch.setattr(sentry_sdk, "flush", lambda timeout: flush_timeouts.append(timeout))
    runtime = configure_worker_observability(Settings(environment="test"))

    runtime.shutdown()

    assert isinstance(runtime, ObservabilityRuntime)
    assert flush_timeouts == []
