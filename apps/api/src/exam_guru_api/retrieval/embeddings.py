"""Small query-embedding port and environment-safe provider registry."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, Protocol, cast

from pydantic import SecretStr

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.observability import get_operational_telemetry
from exam_guru_api.retrieval.openai_embedding_adapter import (
    OPENAI_EMBEDDING_PROVIDER,
    OpenAIEmbeddingAdapter,
    OpenAIEmbeddingAdapterConfig,
    OpenAIEmbeddingPricing,
)
from exam_guru_api.retrieval.repository import validate_embedding_config, validate_query_vector

MAX_EMBEDDING_QUERY_CHARACTERS = 4_096
MAX_EMBEDDING_SOURCE_CHARACTERS = 1_000_000
DETERMINISTIC_PROVIDER_NAME: Literal["deterministic"] = "deterministic"
DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG = EmbeddingConfig(
    provider=DETERMINISTIC_PROVIDER_NAME,
    model="grade5-deterministic-shake256",
    dimension=32,
    version="v1",
    config_fingerprint=("sha256:51c1987251b1c8d373ecb6d476c2d00ae60da5173aa544a2ff4524a9141d3d89"),
)


class ActiveEmbeddingConfigUnavailableError(RuntimeError):
    """The deployment has no complete, safe server-owned embedding configuration."""

    def __init__(self) -> None:
        super().__init__("active_embedding_config_unavailable")


class EmbeddingProvider(Protocol):
    """Provider-neutral synchronous port for one bounded query embedding."""

    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult: ...


class EmbeddingProviderUnavailableError(RuntimeError):
    """Stable failure that never includes adapter or credential details."""

    def __init__(self) -> None:
        super().__init__("embedding_provider_unavailable")


class EmbeddingProviderRegistry:
    """Immutable exact-name registry for configured embedding adapters."""

    def __init__(
        self,
        providers: Mapping[str, EmbeddingProvider],
        *,
        active_config: EmbeddingConfig | None = None,
    ) -> None:
        if not isinstance(providers, Mapping):
            raise ValueError("providers must be a mapping")
        snapshot: dict[str, EmbeddingProvider] = {}
        for name, provider in providers.items():
            if not isinstance(name, str) or not name or name != name.strip() or len(name) > 64:
                raise ValueError("provider name must be a bounded non-blank string")
            if not callable(getattr(provider, "embed", None)):
                raise ValueError("provider must implement embed")
            snapshot[name] = provider
        if active_config is not None:
            valid_active_config = validate_embedding_config(active_config)
            if valid_active_config.provider not in snapshot:
                raise ValueError("active embedding configuration requires a registered provider")
        else:
            valid_active_config = None
        self._providers = MappingProxyType(snapshot)
        self._active_config = valid_active_config

    @property
    def active_config(self) -> EmbeddingConfig:
        if self._active_config is None:
            raise ActiveEmbeddingConfigUnavailableError
        return self._active_config

    @property
    def registered_provider_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def without_deterministic_providers(self) -> EmbeddingProviderRegistry:
        providers = {
            name: provider
            for name, provider in self._providers.items()
            if not isinstance(provider, DeterministicEmbeddingProvider)
        }
        active_config = (
            self._active_config
            if self._active_config is not None and self._active_config.provider in providers
            else None
        )
        return EmbeddingProviderRegistry(providers, active_config=active_config)

    def ensure_provider(self, config: EmbeddingConfig) -> None:
        """Fail closed before database work when no exact adapter is registered."""

        try:
            valid_config = validate_embedding_config(config)
        except Exception as error:
            raise EmbeddingProviderUnavailableError from error
        if valid_config.provider not in self._providers:
            raise EmbeddingProviderUnavailableError

    def embed_query(self, query: str, config: EmbeddingConfig) -> EmbeddingResult:
        """Embed a query and normalize every adapter/contract failure."""

        return self._embed_text(
            query,
            config,
            maximum_characters=MAX_EMBEDDING_QUERY_CHARACTERS,
        )

    def embed_source(self, source_text: str, config: EmbeddingConfig) -> EmbeddingResult:
        """Embed one bounded authoritative persisted source value."""

        return self._embed_text(
            source_text,
            config,
            maximum_characters=MAX_EMBEDDING_SOURCE_CHARACTERS,
        )

    async def embed_query_async(self, query: str, config: EmbeddingConfig) -> EmbeddingResult:
        return await asyncio.to_thread(self.embed_query, query, config)

    async def embed_source_async(
        self,
        source_text: str,
        config: EmbeddingConfig,
    ) -> EmbeddingResult:
        return await asyncio.to_thread(self.embed_source, source_text, config)

    def _embed_text(
        self,
        value: str,
        config: EmbeddingConfig,
        *,
        maximum_characters: int,
    ) -> EmbeddingResult:
        try:
            valid_config = validate_embedding_config(config)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum_characters:
                raise ValueError("invalid embedding input")
            provider = self._providers.get(valid_config.provider)
            if provider is None:
                raise ValueError("provider is not registered")
            result = provider.embed(value, valid_config)
            if not isinstance(result, EmbeddingResult) or result.config != valid_config:
                raise ValueError("provider returned mismatched metadata")
            validate_query_vector(result.vector, expected_dimension=valid_config.dimension)
            accounting = result.accounting
            if accounting is not None:
                get_operational_telemetry().embedding_provider_completed(
                    provider=valid_config.provider,
                    model=valid_config.model,
                    dimension=valid_config.dimension,
                    embedding_version=valid_config.version,
                    input_tokens=accounting.input_tokens,
                    total_tokens=accounting.total_tokens,
                    cost_microusd=accounting.cost_microusd,
                    latency_ms=accounting.latency_ms,
                )
            return result
        except EmbeddingContractError:
            raise
        except Exception as error:
            raise EmbeddingProviderUnavailableError from error


def create_active_embedding_config(settings: Settings) -> EmbeddingConfig:
    """Resolve the sole server-owned ingestion configuration for this deployment.

    Local and test deliberately default an unset provider to the deterministic adapter so
    developer acceptance can ingest and retrieve without a network or paid provider. Staging and
    production remain unavailable until a real provider is implemented and explicitly configured.
    """

    provider: str | None = settings.retrieval_embedding_provider
    if provider is None and settings.environment in {"local", "test"}:
        provider = DETERMINISTIC_PROVIDER_NAME
    if provider is None:
        raise ActiveEmbeddingConfigUnavailableError
    try:
        return validate_embedding_config(
            EmbeddingConfig(
                provider=provider,
                model=settings.retrieval_embedding_model,
                dimension=settings.retrieval_embedding_dimension,
                version=settings.retrieval_embedding_version,
                config_fingerprint=settings.retrieval_embedding_config_fingerprint,
            )
        )
    except Exception as error:
        raise ActiveEmbeddingConfigUnavailableError from error


def create_embedding_provider_registry(settings: Settings) -> EmbeddingProviderRegistry:
    selected_provider = settings.retrieval_embedding_provider
    if selected_provider is None and settings.environment in {"local", "test"}:
        selected_provider = DETERMINISTIC_PROVIDER_NAME
    if selected_provider == DETERMINISTIC_PROVIDER_NAME:
        if settings.environment not in {"local", "test"}:
            return EmbeddingProviderRegistry({})
        provider: EmbeddingProvider = DeterministicEmbeddingProvider()
    elif selected_provider == OPENAI_EMBEDDING_PROVIDER:
        provider = OpenAIEmbeddingAdapter(
            OpenAIEmbeddingAdapterConfig(
                api_key=cast(SecretStr, settings.retrieval_embedding_openai_api_key),
                timeout_ms=cast(int, settings.retrieval_embedding_timeout_ms),
            ),
            pricing=OpenAIEmbeddingPricing(
                pricing_version=cast(str, settings.retrieval_embedding_pricing_version),
                model=settings.retrieval_embedding_model,
                input_microusd_per_million_tokens=cast(
                    int,
                    settings.retrieval_embedding_input_microusd_per_million_tokens,
                ),
            ),
        )
    else:
        return EmbeddingProviderRegistry({})
    return EmbeddingProviderRegistry(
        {selected_provider: provider},
        active_config=create_active_embedding_config(settings),
    )
