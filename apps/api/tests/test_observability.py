import pytest
import sentry_sdk
from fastapi import FastAPI
from pydantic import SecretStr

from exam_guru_api.core.config import Settings
from exam_guru_api.observability import configure_observability


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

    assert sentry_configuration == {
        "dsn": "https://public@example.com/1",
        "enable_tracing": True,
        "environment": "test",
        "send_default_pii": False,
        "traces_sample_rate": 0.25,
    }
