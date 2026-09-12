from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import Settings
from exam_guru_api.documents.understanding_openai import OpenAIUnderstandingProvider
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderError,
    UnderstandingRequest,
)
from exam_guru_api.documents.understanding_runtime import create_understanding_runtime
from tests.test_document_understanding_provider import request as fixture_request


def openai_settings() -> dict[str, Any]:
    return {
        "environment": "local",
        "document_understanding_provider": "openai",
        "document_understanding_openai_api_key": SecretStr(
            "unit-test-placeholder-not-a-credential"
        ),
        "document_understanding_model": "fixture-vision",
        "document_understanding_model_version": "fixture-vision-2026-08-01",
        "document_understanding_pricing_version": "fixture-pricing.v1",
        "document_understanding_input_microusd_per_million_tokens": 1000000,
        "document_understanding_output_microusd_per_million_tokens": 2000000,
        "document_understanding_temperature": 0.0,
        "document_understanding_image_input_verified": True,
        "document_understanding_structured_output_verified": True,
    }


def test_document_understanding_is_disabled_without_explicit_configuration() -> None:
    settings = Settings(environment="test")
    assert settings.document_understanding_provider is None
    assert create_understanding_runtime(settings) is None


def test_configured_visual_runtime_uses_server_owned_profile_and_budgets() -> None:
    settings = Settings.model_validate(openai_settings())
    runtime = create_understanding_runtime(settings)
    assert runtime is not None
    assert isinstance(runtime.provider, OpenAIUnderstandingProvider)
    assert runtime.profile.provider == "openai"
    assert runtime.profile.model_version == "fixture-vision-2026-08-01"
    assert runtime.profile.prompt_version == "document-understanding.v1"
    assert runtime.budget.timeout_ms == 30000
    assert runtime.budget.max_output_tokens == 8192
    assert runtime.budget.max_cost_microusd == 1000000
    assert "unit-test-placeholder" not in repr(settings)
    assert "unit-test-placeholder" not in repr(runtime)


@pytest.mark.parametrize(
    "field",
    [
        "document_understanding_openai_api_key",
        "document_understanding_model",
        "document_understanding_model_version",
        "document_understanding_pricing_version",
        "document_understanding_input_microusd_per_million_tokens",
        "document_understanding_output_microusd_per_million_tokens",
        "document_understanding_temperature",
    ],
)
def test_hosted_understanding_requires_complete_explicit_configuration(field: str) -> None:
    values = openai_settings()
    values[field] = None
    with pytest.raises(ValidationError, match="understanding"):
        Settings.model_validate(values)


@pytest.mark.parametrize(
    "field",
    [
        "document_understanding_image_input_verified",
        "document_understanding_structured_output_verified",
    ],
)
def test_unverified_visual_capabilities_do_not_enable_runtime(field: str) -> None:
    values = openai_settings()
    values[field] = False
    with pytest.raises(ValidationError, match="capabilit"):
        Settings.model_validate(values)


def test_paid_understanding_is_not_enabled_in_normal_test_configuration() -> None:
    values = openai_settings()
    values["environment"] = "test"
    with pytest.raises(ValidationError, match="test"):
        Settings.model_validate(values)


def test_fixture_understanding_is_only_allowed_in_the_isolated_test_environment() -> None:
    with pytest.raises(ValidationError, match="test"):
        Settings(environment="local", document_understanding_provider="deterministic")
    runtime = create_understanding_runtime(
        Settings(
            environment="test",
            test_runtime_id="ai-exam-guru-e2e-understanding-unit",
            document_understanding_provider="deterministic",
        )
    )
    assert runtime is not None
    fixture = fixture_request()
    response = runtime.provider.understand(
        UnderstandingRequest(
            source=fixture.source,
            image_png=fixture.image_png,
            profile=runtime.profile,
            budget=runtime.budget,
        )
    )
    assert response.disposition == "requires_verification"
    assert response.content.observation.regions[0].exact_text.startswith("Synthetic")
    assert response.accounting.cost_microusd == 0


def test_fixture_provider_cannot_be_rebound_to_another_profile() -> None:
    runtime = create_understanding_runtime(
        Settings(
            environment="test",
            test_runtime_id="ai-exam-guru-e2e-understanding-unit",
            document_understanding_provider="deterministic",
        )
    )
    assert runtime is not None
    with pytest.raises(UnderstandingProviderError):
        runtime.provider.understand(fixture_request())


@pytest.mark.parametrize("value", ["", "has space", "invalid\x01value", "x" * 4097])
def test_understanding_runtime_requires_a_bounded_secret(value: str) -> None:
    values = openai_settings()
    values["document_understanding_openai_api_key"] = SecretStr(value)
    with pytest.raises(ValidationError, match="bounded secret"):
        Settings.model_validate(values)


def test_fixture_understanding_requires_an_explicit_isolated_runtime_identity() -> None:
    with pytest.raises(ValidationError, match="isolated"):
        Settings(environment="test", document_understanding_provider="deterministic")


@pytest.mark.parametrize("case", ["paid_test", "unknown_provider", "missing_fixture_identity"])
def test_runtime_factory_rechecks_untrusted_or_bypassed_configuration(case: str) -> None:
    if case == "missing_fixture_identity":
        settings = Settings(
            environment="test",
            test_runtime_id="ai-exam-guru-e2e-understanding-unit",
            document_understanding_provider="deterministic",
        ).model_copy(update={"test_runtime_id": None})
    else:
        settings = Settings.model_validate(openai_settings()).model_copy(
            update={
                "environment": "test" if case == "paid_test" else "local",
                "document_understanding_provider": "openai"
                if case == "paid_test"
                else "unapproved",
            }
        )
    with pytest.raises(ValueError, match="understanding"):
        create_understanding_runtime(settings)


def test_fixture_worker_identity_is_separate_from_api_attestation() -> None:
    settings = Settings(
        environment="test",
        document_understanding_provider="deterministic",
        document_understanding_fixture_runtime_id="ai-exam-guru-e2e-understanding-worker",
    )
    assert settings.test_runtime_id is None
    runtime = create_understanding_runtime(settings)
    assert runtime is not None
    assert runtime.fixture_runtime_id == "ai-exam-guru-e2e-understanding-worker"


@pytest.mark.parametrize("case", ["mismatch", "non_fixture", "invalid_identity"])
def test_fixture_identity_cannot_leak_or_disagree_with_api_identity(case: str) -> None:
    values: dict[str, Any] = {
        "environment": "test",
        "document_understanding_provider": "deterministic",
        "test_runtime_id": "ai-exam-guru-e2e-understanding-api",
        "document_understanding_fixture_runtime_id": "ai-exam-guru-e2e-understanding-api",
    }
    if case == "mismatch":
        values["document_understanding_fixture_runtime_id"] = "ai-exam-guru-e2e-another"
    elif case == "non_fixture":
        values = {"document_understanding_fixture_runtime_id": "ai-exam-guru-e2e-understanding-api"}
    else:
        values["document_understanding_fixture_runtime_id"] = "production"
    with pytest.raises(ValidationError, match="fixture"):
        Settings.model_validate(values)


def test_stray_provider_fields_and_unbounded_runtime_limits_are_rejected() -> None:
    values = openai_settings()
    values["document_understanding_provider"] = None
    with pytest.raises(ValidationError, match="understanding"):
        Settings.model_validate(values)
    for field, value in (
        ("document_understanding_timeout_ms", 60001),
        ("document_understanding_max_output_tokens", 16385),
        ("document_understanding_max_cost_microusd", 0),
        ("document_understanding_worker_lease_seconds", 300),
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate({"environment": "test", field: value})
