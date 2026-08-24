from typing import cast

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingResult,
)
from exam_guru_api.main import create_app
from exam_guru_api.retrieval.embeddings import (
    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProvider,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
    create_active_embedding_config,
    create_embedding_provider_registry,
)

CONFIG = EmbeddingConfig(
    provider="deterministic",
    model="grade5-fixture",
    dimension=3,
    version="v1",
    config_fingerprint="grade5-fixture-v1-d3",
)


class FailingProvider:
    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        del text, config
        raise RuntimeError("secret provider detail")


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
