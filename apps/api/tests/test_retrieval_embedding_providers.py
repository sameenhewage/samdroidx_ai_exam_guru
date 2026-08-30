from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingAccounting,
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.main import create_app
from exam_guru_api.retrieval import embeddings as embedding_module
from exam_guru_api.retrieval.embeddings import (
    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProvider,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
    create_active_embedding_config,
    create_embedding_provider_registry,
)
from exam_guru_api.retrieval.openai_embedding_adapter import (
    MAX_OPENAI_EMBEDDING_TIMEOUT_MS,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_EMBEDDING_PROVIDER,
)
from tests.test_operational_telemetry import telemetry

CONFIG = EmbeddingConfig(
    provider="deterministic",
    model="grade5-fixture",
    dimension=3,
    version="v1",
    config_fingerprint="grade5-fixture-v1-d3",
)


def production_oidc_config() -> dict[str, Any]:
    return {
        "identity_provider": "oidc",
        "oidc_issuer": "https://identity.internal.example/issuer",
        "oidc_audience": "exam-guru-api",
        "oidc_jwks_url": "https://identity.internal.example/issuer/jwks",
        "oidc_role_claim_name": "roles",
        "oidc_admin_role": "exam-guru-admin",
        "oidc_reviewer_role": "exam-guru-reviewer",
        "oidc_max_token_age_seconds": 3_600,
        "oidc_clock_skew_seconds": 30,
        "oidc_jwks_timeout_seconds": 2.0,
        "oidc_jwks_cache_seconds": 300,
        "oidc_jwks_max_cached_keys": 16,
    }


class FailingProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        del text, config
        raise RuntimeError("secret provider detail")


class ContractFailingProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        del text, config
        raise EmbeddingContractError("safe contract failure")


class AccountingProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        del text
        return EmbeddingResult(
            vector=(1.0, 0.0, 0.0),
            config=config,
            accounting=EmbeddingAccounting(7, 7, 2, 19),
        )


class InvalidProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        del text
        return EmbeddingResult(vector=(1.0,), config=config)


class MismatchedProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        del text
        mismatched = EmbeddingConfig(
            provider=config.provider,
            model="another-model",
            dimension=config.dimension,
            version=config.version,
            config_fingerprint=config.config_fingerprint,
        )
        return EmbeddingResult(vector=(1.0, 0.0, 0.0), config=mismatched)


class MissingEmbedProvider:
    pass


def test_local_and_test_registry_use_only_the_deterministic_provider() -> None:
    for environment in ("local", "test"):
        registry = create_embedding_provider_registry(Settings(environment=environment))

        result = registry.embed_query("square perimeter", CONFIG)

        assert result == DeterministicEmbeddingProvider().embed("square perimeter", CONFIG)
        assert registry.registered_provider_names == ("deterministic",)


def test_staging_and_production_registry_fail_closed_without_a_real_adapter() -> None:
    staging = create_embedding_provider_registry(Settings(environment="staging"))
    production = create_embedding_provider_registry(
        Settings(
            environment="production",
            database_url=SecretStr(
                "postgresql+asyncpg://service:" + "database-credential" + "@db/app?ssl=require"
            ),
            object_storage_access_key=SecretStr("storage-access"),
            object_storage_secret_key=SecretStr("storage-" + "credential"),
            object_storage_endpoint_url="https://storage.internal",
            valkey_url=SecretStr("rediss://:" + "cache-credential" + "@valkey:6379/0"),
            **production_oidc_config(),
        )
    )

    for registry in (staging, production):
        assert registry.registered_provider_names == ()
        with pytest.raises(EmbeddingProviderUnavailableError) as raised:
            registry.embed_query("square perimeter", CONFIG)
        assert str(raised.value) == "embedding_provider_unavailable"


def test_registry_routes_exact_provider_and_normalizes_adapter_failures() -> None:
    failing = EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, FailingProvider())}
    )
    invalid = EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, InvalidProvider())}
    )
    mismatched = EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, MismatchedProvider())}
    )

    for registry in (failing, invalid, mismatched):
        with pytest.raises(EmbeddingProviderUnavailableError) as raised:
            registry.embed_query("square perimeter", CONFIG)
        assert str(raised.value) == "embedding_provider_unavailable"
        assert "secret provider detail" not in str(raised.value)

    unavailable = EmbeddingProviderRegistry({})
    other_config = EmbeddingConfig(
        provider="other",
        model=CONFIG.model,
        dimension=CONFIG.dimension,
        version=CONFIG.version,
        config_fingerprint=CONFIG.config_fingerprint,
    )
    with pytest.raises(EmbeddingProviderUnavailableError):
        unavailable.embed_query("square perimeter", other_config)


def test_registry_preserves_embedding_contract_failures_for_worker_classification() -> None:
    registry = EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, ContractFailingProvider())}
    )

    with pytest.raises(EmbeddingContractError, match="safe contract failure"):
        registry.embed_source("reviewed source", CONFIG)


def test_registry_emits_content_free_accounting_for_paid_provider_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operational, telemetry_logger, _tracer = telemetry()
    monkeypatch.setattr(
        embedding_module,
        "get_operational_telemetry",
        lambda: operational,
    )
    registry = EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, AccountingProvider())}
    )

    result = registry.embed_query("private query text", CONFIG)

    assert result.accounting == EmbeddingAccounting(7, 7, 2, 19)
    assert telemetry_logger.records == [
        (
            "Operational event",
            {
                "event_name": "embedding.provider_completed",
                "outcome": "succeeded",
                "provider": CONFIG.provider,
                "model": CONFIG.model,
                "dimension": CONFIG.dimension,
                "embedding_version": CONFIG.version,
                "input_tokens": 7,
                "total_tokens": 7,
                "cost_microusd": 2,
                "latency_ms": 19,
            },
        )
    ]
    assert "private query text" not in str(telemetry_logger.records)
    assert "1.0" not in str(telemetry_logger.records)


def test_registry_rejects_malformed_registration_and_inputs() -> None:
    with pytest.raises(ValueError, match="provider name"):
        EmbeddingProviderRegistry({" ": cast(EmbeddingProvider, DeterministicEmbeddingProvider())})
    with pytest.raises(ValueError, match="provider"):
        EmbeddingProviderRegistry(cast(dict[str, EmbeddingProvider], []))
    with pytest.raises(ValueError, match="implement embed"):
        EmbeddingProviderRegistry(
            {"deterministic": cast(EmbeddingProvider, MissingEmbedProvider())}
        )

    registry = EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, DeterministicEmbeddingProvider())}
    )
    with pytest.raises(EmbeddingProviderUnavailableError):
        registry.embed_query(" ", CONFIG)
    with pytest.raises(EmbeddingProviderUnavailableError):
        registry.embed_query("text", cast(EmbeddingConfig, "invalid"))
    with pytest.raises(EmbeddingProviderUnavailableError):
        registry.ensure_provider(cast(EmbeddingConfig, "invalid"))


def test_registry_active_configuration_requires_and_exposes_an_exact_provider() -> None:
    with pytest.raises(ValueError, match="active embedding configuration"):
        EmbeddingProviderRegistry({}, active_config=CONFIG)

    unavailable = EmbeddingProviderRegistry({})
    with pytest.raises(ActiveEmbeddingConfigUnavailableError):
        _ = unavailable.active_config

    available = EmbeddingProviderRegistry(
        {"deterministic": DeterministicEmbeddingProvider()},
        active_config=CONFIG,
    )
    assert available.active_config == CONFIG


def test_staging_app_cannot_register_the_deterministic_test_adapter() -> None:
    injected = EmbeddingProviderRegistry(
        {
            "deterministic": cast(EmbeddingProvider, DeterministicEmbeddingProvider()),
            "aliased-test-adapter": cast(
                EmbeddingProvider,
                DeterministicEmbeddingProvider(),
            ),
            "real": cast(EmbeddingProvider, FailingProvider()),
        }
    )

    app = create_app(
        settings=Settings(environment="staging"),
        embedding_provider_registry=injected,
    )

    assert app.state.embedding_provider_registry.registered_provider_names == ("real",)


def test_nonproduction_embedding_setting_is_explicit_and_production_rejects_fake() -> None:
    assert Settings(environment="test").retrieval_embedding_provider is None
    assert (
        Settings(
            environment="local", retrieval_embedding_provider="deterministic"
        ).retrieval_embedding_provider
        == "deterministic"
    )

    with pytest.raises(ValidationError, match="deterministic retrieval embeddings"):
        Settings(
            environment="production",
            retrieval_embedding_provider="deterministic",
            database_url=SecretStr(
                "postgresql+asyncpg://service:" + "database-credential" + "@db/app?ssl=require"
            ),
            object_storage_access_key=SecretStr("storage-access"),
            object_storage_secret_key=SecretStr("storage-" + "credential"),
            object_storage_endpoint_url="https://storage.internal",
            valkey_url=SecretStr("rediss://:" + "cache-credential" + "@valkey:6379/0"),
        )


def test_registry_rejects_deterministic_provider_if_settings_validation_is_bypassed() -> None:
    for environment in ("staging", "production"):
        settings = Settings.model_construct(
            environment=environment,
            retrieval_embedding_provider="deterministic",
        )
        assert create_embedding_provider_registry(settings).registered_provider_names == ()


def test_server_owned_active_config_uses_documented_nonproduction_default() -> None:
    for environment in ("local", "test"):
        settings = Settings(environment=environment)

        assert create_active_embedding_config(settings) == DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG


def test_active_config_factory_normalizes_unexpected_contract_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.retrieval import embeddings as embedding_module

    def fail(_config: EmbeddingConfig) -> EmbeddingConfig:
        raise RuntimeError("unsafe internal validation detail")

    monkeypatch.setattr(embedding_module, "validate_embedding_config", fail)
    with pytest.raises(ActiveEmbeddingConfigUnavailableError) as raised:
        create_active_embedding_config(Settings(environment="test"))
    assert str(raised.value) == "active_embedding_config_unavailable"
    assert "unsafe" not in str(raised.value)


def test_server_owned_active_config_uses_only_bounded_settings_controls() -> None:
    settings = Settings(
        environment="test",
        retrieval_embedding_provider="deterministic",
        retrieval_embedding_model="grade5-custom",
        retrieval_embedding_dimension=7,
        retrieval_embedding_version="2026-03",
        retrieval_embedding_config_fingerprint="sha256:" + "a" * 64,
    )

    assert create_active_embedding_config(settings) == EmbeddingConfig(
        provider="deterministic",
        model="grade5-custom",
        dimension=7,
        version="2026-03",
        config_fingerprint="sha256:" + "a" * 64,
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_server_owned_active_config_fails_closed_without_real_provider(
    environment: str,
) -> None:
    kwargs: dict[str, object] = {"environment": environment}
    if environment == "production":
        kwargs.update(
            database_url=SecretStr(
                "postgresql+asyncpg://service:" + "database-credential" + "@db/app?ssl=require"
            ),
            object_storage_access_key=SecretStr("storage-access"),
            object_storage_secret_key=SecretStr("storage-" + "credential"),
            object_storage_endpoint_url="https://storage.internal",
            valkey_url=SecretStr("rediss://:" + "cache-credential" + "@valkey:6379/0"),
            **production_oidc_config(),
        )

    with pytest.raises(ActiveEmbeddingConfigUnavailableError) as raised:
        create_active_embedding_config(Settings(**kwargs))  # type: ignore[arg-type]

    assert str(raised.value) == "active_embedding_config_unavailable"


@pytest.mark.parametrize(
    "override",
    [
        {"retrieval_embedding_model": " model"},
        {"retrieval_embedding_model": "x" * 129},
        {"retrieval_embedding_dimension": 0},
        {"retrieval_embedding_dimension": 4_097},
        {"retrieval_embedding_version": "version with space"},
        {"retrieval_embedding_config_fingerprint": "\x00"},
        {"retrieval_embedding_config_fingerprint": "x" * 129},
    ],
)
def test_embedding_config_settings_are_bounded(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **override)  # type: ignore[arg-type]


def openai_embedding_values(environment: str = "local") -> dict[str, object]:
    values: dict[str, object] = {
        "environment": environment,
        "retrieval_embedding_provider": OPENAI_EMBEDDING_PROVIDER,
        "retrieval_embedding_model": OPENAI_EMBEDDING_MODEL,
        "retrieval_embedding_dimension": 1_536,
        "retrieval_embedding_version": "2026-08",
        "retrieval_embedding_config_fingerprint": "sha256:" + "b" * 64,
        "retrieval_embedding_openai_api_key": SecretStr("unit-test-placeholder-not-a-credential"),
        "retrieval_embedding_pricing_version": "openai-2026-08-30",
        "retrieval_embedding_input_microusd_per_million_tokens": 20_000,
        "retrieval_embedding_timeout_ms": MAX_OPENAI_EMBEDDING_TIMEOUT_MS,
    }
    if environment == "production":
        values.update(
            database_url=SecretStr(
                "postgresql+asyncpg://service:" + "database-credential" + "@db/app?ssl=require"
            ),
            valkey_url=SecretStr("rediss://:" + "cache-credential" + "@valkey:6379/0"),
            **production_oidc_config(),
        )
    return values


def openai_embedding_settings(environment: str = "local") -> Settings:
    return Settings(**openai_embedding_values(environment))  # type: ignore[arg-type]


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
def test_configured_openai_registry_is_active_in_non_test_environments(environment: str) -> None:
    settings = openai_embedding_settings(environment)

    registry = create_embedding_provider_registry(settings)

    assert registry.registered_provider_names == (OPENAI_EMBEDDING_PROVIDER,)
    assert registry.active_config == EmbeddingConfig(
        provider=OPENAI_EMBEDDING_PROVIDER,
        model=OPENAI_EMBEDDING_MODEL,
        dimension=1_536,
        version="2026-08",
        config_fingerprint="sha256:" + "b" * 64,
    )
    assert "unit-test-placeholder-not-a-credential" not in repr(settings)
    app = create_app(settings=settings, embedding_provider_registry=registry)
    assert app.state.embedding_provider_registry.registered_provider_names == (
        OPENAI_EMBEDDING_PROVIDER,
    )


def test_paid_openai_embedding_provider_is_rejected_in_test_environment() -> None:
    with pytest.raises(ValidationError, match="test configuration"):
        openai_embedding_settings("test")


@pytest.mark.parametrize(
    "override",
    [
        {"retrieval_embedding_openai_api_key": None},
        {"retrieval_embedding_openai_api_key": SecretStr(" leading")},
        {"retrieval_embedding_openai_api_key": SecretStr("x" * 4_097)},
        {"retrieval_embedding_pricing_version": None},
        {"retrieval_embedding_pricing_version": "pricing with spaces"},
        {"retrieval_embedding_pricing_version": "pricing\x00"},
        {"retrieval_embedding_input_microusd_per_million_tokens": None},
        {"retrieval_embedding_input_microusd_per_million_tokens": -1},
        {"retrieval_embedding_timeout_ms": None},
        {"retrieval_embedding_timeout_ms": 0},
        {"retrieval_embedding_timeout_ms": MAX_OPENAI_EMBEDDING_TIMEOUT_MS + 1},
        {"retrieval_embedding_model": "other-model"},
        {"retrieval_embedding_dimension": 1_537},
        {"retrieval_embedding_config_fingerprint": "not-a-sha256-fingerprint"},
    ],
)
def test_openai_embedding_settings_require_complete_bounded_exact_configuration(
    override: dict[str, object],
) -> None:
    values = openai_embedding_values()
    values.update(override)

    with pytest.raises(ValidationError):
        Settings(**cast(Any, values))


@pytest.mark.parametrize(
    "override",
    [
        {"retrieval_embedding_openai_api_key": SecretStr("unexpected-key")},
        {"retrieval_embedding_pricing_version": "unexpected-pricing"},
        {"retrieval_embedding_input_microusd_per_million_tokens": 20_000},
        {"retrieval_embedding_timeout_ms": 1},
    ],
)
def test_openai_embedding_transport_settings_require_openai_provider(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="require retrieval_embedding_provider=openai"):
        Settings(environment="local", **override)  # type: ignore[arg-type]
