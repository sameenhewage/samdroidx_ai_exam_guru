from typing import cast

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import Settings
from exam_guru_api.main import create_app
from exam_guru_api.validation.openai_semantic_verifier import OpenAISemanticVerifier
from exam_guru_api.validation.pipeline import DEFAULT_PIPELINE_VERSION
from exam_guru_api.validation.semantic_runtime import create_semantic_verifier


def configured_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "semantic_verifier_provider": "openai",
        "semantic_verifier_openai_api_key": SecretStr("semantic-unit-test-placeholder"),
        "semantic_verifier_model": "gpt-test-mini",
        "semantic_verifier_model_version": "gpt-test-mini-2026-08-01",
        "semantic_verifier_prompt_version": "grounded-factual-verifier.v1",
        "semantic_verifier_pricing_version": "semantic-pricing-v1",
        "semantic_verifier_input_microusd_per_million_tokens": 2_000_000,
        "semantic_verifier_output_microusd_per_million_tokens": 8_000_000,
        "semantic_verifier_timeout_ms": 10_000,
        "semantic_verifier_max_grounding_sources": 6,
        "semantic_verifier_max_source_bytes": 4_096,
        "semantic_verifier_max_total_source_bytes": 16_384,
        "semantic_verifier_max_candidate_bytes": 24_000,
        "semantic_verifier_max_request_bytes": 48_000,
        "semantic_verifier_max_output_tokens": 400,
        "semantic_verifier_max_cost_microusd": 250_000,
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def test_empty_optional_compose_environment_values_keep_the_verifier_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXAM_GURU_SEMANTIC_VERIFIER_PROVIDER", "")
    monkeypatch.setenv("EXAM_GURU_SEMANTIC_VERIFIER_TIMEOUT_MS", "")
    settings = Settings()
    assert settings.semantic_verifier_provider is None
    assert settings.semantic_verifier_timeout_ms is None


def test_runtime_is_optional_and_builds_exact_explicit_openai_configuration() -> None:
    assert create_semantic_verifier(Settings()) is None
    with pytest.raises(TypeError, match="settings"):
        create_semantic_verifier(cast(Settings, object()))

    verifier = create_semantic_verifier(configured_settings())
    assert isinstance(verifier, OpenAISemanticVerifier)
    assert verifier.model == "gpt-test-mini"
    assert verifier.model_version == "gpt-test-mini-2026-08-01"
    assert verifier.prompt_version == "grounded-factual-verifier.v1"
    assert verifier.pricing_version == "semantic-pricing-v1"
    assert verifier.budget.max_grounding_sources == 6
    assert verifier.budget.max_request_bytes == 48_000
    assert verifier.budget.max_cost_microusd == 250_000
    assert "semantic-unit-test-placeholder" not in repr(verifier)


def test_settings_reject_partial_dormant_or_paid_test_semantic_configuration() -> None:
    with pytest.raises(ValidationError, match="semantic verifier"):
        Settings(semantic_verifier_model="gpt-test-mini")
    with pytest.raises(ValidationError, match="explicit model"):
        Settings(semantic_verifier_provider="openai")
    with pytest.raises(ValidationError, match="test configuration"):
        configured_settings(environment="test")
    with pytest.raises(ValidationError, match="API key"):
        configured_settings(semantic_verifier_openai_api_key=SecretStr(" padded "))
    with pytest.raises(ValidationError, match="total source budget"):
        configured_settings(
            semantic_verifier_max_source_bytes=2,
            semantic_verifier_max_total_source_bytes=1,
        )
    with pytest.raises(ValidationError, match="request budget"):
        configured_settings(
            semantic_verifier_max_candidate_bytes=2,
            semantic_verifier_max_request_bytes=1,
        )


def test_application_pipeline_binds_semantic_runtime_lineage() -> None:
    baseline = create_app(settings=Settings()).state.validation_pipeline
    configured = create_app(settings=configured_settings()).state.validation_pipeline
    changed_model = create_app(
        settings=configured_settings(semantic_verifier_model_version="gpt-test-mini-2026-08-02")
    ).state.validation_pipeline

    assert baseline.version == DEFAULT_PIPELINE_VERSION
    assert configured.version.startswith("deterministic-question-validation.v5+semantic-")
    assert configured.version != baseline.version
    assert changed_model.version != configured.version
    assert baseline.pipeline_fingerprint != configured.pipeline_fingerprint
    assert baseline.subject_router is not None
    assert configured.subject_router is not None
    baseline_factual = next(
        validator
        for validator in baseline.subject_router.validators
        if validator.validator_id == "grounded-factual-subject"
    )
    configured_factual = next(
        validator
        for validator in configured.subject_router.validators
        if validator.validator_id == "grounded-factual-subject"
    )
    assert baseline_factual.validator_version.endswith("+unconfigured")
    assert "+configured-" in configured_factual.validator_version
