from dataclasses import replace

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.blueprints.domain import QuestionType
from exam_guru_api.core.config import Settings
from exam_guru_api.generation.domain import CandidateDisposition, GenerationIdentity
from exam_guru_api.generation.openai_adapter import OpenAIGenerationAdapter
from exam_guru_api.generation.ports import ProviderError
from exam_guru_api.generation.runtime import (
    GenerationRuntimeUnavailableError,
    create_generation_runtime,
)
from tests.test_generation_provider import request


def secure_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "staging",
        "database_url": SecretStr("postgresql+asyncpg://service:db-secret@db/app?ssl=require"),
        "valkey_url": SecretStr("rediss://:cache-secret@valkey:6379/0"),
        "object_storage_endpoint_url": "https://storage.internal",
        "object_storage_access_key": SecretStr("storage-access"),
        "object_storage_secret_key": SecretStr("storage-secret"),
    }
    values.update(changes)
    return Settings.model_validate(values)


def test_local_and_test_runtime_use_only_the_registered_deterministic_route() -> None:
    runtime = create_generation_runtime(Settings(environment="test"))
    registered = runtime.active_config

    assert registered.provider == "deterministic-fake"
    assert registered.provider_version == "1.0.0"
    assert registered.model == "fixture-model"
    assert registered.model_version == "2026-01"
    assert registered.prompt.prompt_id == "question-generation"
    assert registered.prompt.version == "1.0.0"
    assert registered.prompt.schema_version == "question.v1"
    assert registered.pricing.pricing_version == "deterministic-pricing-v1"
    assert registered.parameters.max_output_tokens <= 8_192
    assert 1 <= registered.budgets.max_attempts <= 3

    base = request()
    generation_request = replace(
        base,
        identity=GenerationIdentity(
            generation_id=base.identity.generation_id,
            attempt_id=base.identity.attempt_id,
            idempotency_key="runtime-deterministic-request",
            attempt_number=1,
        ),
        versions=replace(
            base.versions,
            prompt_id=registered.prompt.prompt_id,
            prompt_version=registered.prompt.version,
            provider=registered.provider,
            provider_version=registered.provider_version,
            model=registered.model,
            model_version=registered.model_version,
            retrieval_version=registered.retrieval_version,
            schema_version=registered.prompt.schema_version,
        ),
        parameters=registered.parameters,
    )

    result = runtime.build_provider(registered).generate(generation_request)

    assert result.request is generation_request
    assert result.disposition is CandidateDisposition.REQUIRES_VALIDATION
    assert result.question.question_type is generation_request.blueprint_slot.question_type
    assert result.question.marking.total_marks == generation_request.blueprint_slot.marks
    assert result.accounting.total_tokens == (
        result.accounting.input_tokens + result.accounting.output_tokens
    )
    assert result.accounting.cost_microusd == registered.pricing.cost_microusd(
        input_tokens=result.accounting.input_tokens,
        output_tokens=result.accounting.output_tokens,
    )


def test_non_test_openai_requires_every_explicit_registered_secret_route_and_price() -> None:
    settings = secure_settings(
        generation_provider="openai",
        generation_openai_api_key=SecretStr("provider-secret-key"),
        generation_model="gpt-generation",
        generation_model_version="gpt-generation-2026-08-01",
        generation_pricing_version="openai-price-2026-08",
        generation_input_microusd_per_million_tokens=250_000,
        generation_output_microusd_per_million_tokens=1_000_000,
        generation_timeout_ms=20_000,
    )

    runtime = create_generation_runtime(settings)
    registered = runtime.active_config
    provider = runtime.build_provider(registered)

    assert registered.provider == "openai"
    assert registered.model == "gpt-generation"
    assert registered.model_version == "gpt-generation-2026-08-01"
    assert registered.pricing.pricing_version == "openai-price-2026-08"
    assert isinstance(provider, OpenAIGenerationAdapter)
    assert "provider-secret-key" not in repr(settings)
    assert "provider-secret-key" not in repr(runtime)


@pytest.mark.parametrize(
    "settings",
    [
        lambda: Settings(environment="test", generation_provider="openai"),
        lambda: secure_settings(generation_provider="deterministic"),
        lambda: secure_settings(
            generation_provider="openai",
            generation_openai_api_key=SecretStr("provider-secret-key"),
        ),
        lambda: secure_settings(generation_model="unowned-model-override"),
        lambda: secure_settings(
            generation_provider="openai",
            generation_openai_api_key=SecretStr(" "),
            generation_model="gpt-generation",
            generation_model_version="gpt-generation-2026-08-01",
            generation_pricing_version="openai-price-2026-08",
            generation_input_microusd_per_million_tokens=250_000,
            generation_output_microusd_per_million_tokens=1_000_000,
            generation_timeout_ms=20_000,
        ),
    ],
)
def test_unsafe_or_incomplete_provider_configuration_is_rejected(settings: object) -> None:
    with pytest.raises(ValidationError):
        settings()  # type: ignore[operator]


def test_non_local_runtime_without_an_explicit_provider_is_unavailable_not_fake() -> None:
    runtime = create_generation_runtime(secure_settings())

    with pytest.raises(GenerationRuntimeUnavailableError):
        _ = runtime.active_config


def test_deterministic_runtime_rejects_route_and_prompt_registry_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = create_generation_runtime(Settings(environment="test"))
    config = runtime.active_config
    base = request()
    canonical = replace(
        base,
        versions=replace(
            base.versions,
            prompt_id=config.prompt.prompt_id,
            prompt_version=config.prompt.version,
            provider=config.provider,
            provider_version=config.provider_version,
            model=config.model,
            model_version=config.model_version,
            retrieval_version=config.retrieval_version,
            schema_version=config.prompt.schema_version,
        ),
        parameters=config.parameters,
    )
    provider = runtime.build_provider(config)

    with pytest.raises(ProviderError):
        provider.generate(replace(canonical, versions=replace(canonical.versions, model="forged")))

    def fail_binding(value: object) -> object:
        del value
        raise ValueError("registry failure")

    monkeypatch.setattr(runtime._prompt_registry, "bind", fail_binding)
    with pytest.raises(ProviderError):
        provider.generate(canonical)


def test_deterministic_runtime_generates_constructed_response_and_rejects_unregistered_config() -> (
    None
):
    runtime = create_generation_runtime(Settings(environment="test"))
    config = runtime.active_config
    base = request()
    constraints = replace(
        base.blueprint_slot.generation_constraints,
        required_question_type=QuestionType.SHORT_ANSWER,
    )
    short_answer_slot = replace(
        base.blueprint_slot,
        question_type=QuestionType.SHORT_ANSWER,
        generation_constraints=constraints,
    )
    generation_request = replace(
        base,
        blueprint_slot=short_answer_slot,
        versions=replace(
            base.versions,
            prompt_id=config.prompt.prompt_id,
            prompt_version=config.prompt.version,
            provider=config.provider,
            provider_version=config.provider_version,
            model=config.model,
            model_version=config.model_version,
            retrieval_version=config.retrieval_version,
            schema_version=config.prompt.schema_version,
        ),
        parameters=config.parameters,
    )

    result = runtime.build_provider(config).generate(generation_request)

    assert result.question.question_type is QuestionType.SHORT_ANSWER
    assert result.question.options == ()
    assert result.question.answer.accepted_responses
    with pytest.raises(GenerationRuntimeUnavailableError):
        runtime.build_provider(replace(config, model="unregistered"))

    unavailable_adapter = type(runtime)(replace(config, provider="openai"))
    with pytest.raises(GenerationRuntimeUnavailableError):
        unavailable_adapter.build_provider(unavailable_adapter.active_config)
