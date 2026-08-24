"""Small query-embedding port and environment-safe provider registry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingResult,
)
from exam_guru_api.retrieval.repository import validate_embedding_config, validate_query_vector

MAX_EMBEDDING_QUERY_CHARACTERS = 4_096
DETERMINISTIC_PROVIDER_NAME = "deterministic"


class EmbeddingProvider(Protocol):
    """Provider-neutral synchronous port for one bounded query embedding."""

    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult: ...


class EmbeddingProviderUnavailableError(RuntimeError):
    """Stable failure that never includes adapter or credential details."""

    def __init__(self) -> None:
        super().__init__("embedding_provider_unavailable")


class EmbeddingProviderRegistry:
    """Immutable exact-name registry for configured embedding adapters."""

    def __init__(self, providers: Mapping[str, EmbeddingProvider]) -> None:
        if not isinstance(providers, Mapping):
            raise ValueError("providers must be a mapping")
        snapshot: dict[str, EmbeddingProvider] = {}
        for name, provider in providers.items():
            if not isinstance(name, str) or not name or name != name.strip() or len(name) > 64:
                raise ValueError("provider name must be a bounded non-blank string")
            if not callable(getattr(provider, "embed", None)):
                raise ValueError("provider must implement embed")
            snapshot[name] = provider
        self._providers = MappingProxyType(snapshot)

    @property
    def registered_provider_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def without_deterministic_providers(self) -> EmbeddingProviderRegistry:
        return EmbeddingProviderRegistry(
            {
                name: provider
                for name, provider in self._providers.items()
                if not isinstance(provider, DeterministicEmbeddingProvider)
            }
        )

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

        try:
            valid_config = validate_embedding_config(config)
            if (
                not isinstance(query, str)
                or not query.strip()
                or len(query) > MAX_EMBEDDING_QUERY_CHARACTERS
            ):
                raise ValueError("invalid embedding query")
            provider = self._providers.get(valid_config.provider)
            if provider is None:
                raise ValueError("provider is not registered")
            result = provider.embed(query, valid_config)
            if not isinstance(result, EmbeddingResult) or result.config != valid_config:
                raise ValueError("provider returned mismatched metadata")
            validate_query_vector(result.vector, expected_dimension=valid_config.dimension)
            return result
        except Exception as error:
            raise EmbeddingProviderUnavailableError from error


def create_embedding_provider_registry(settings: Settings) -> EmbeddingProviderRegistry:
    """Register deterministic embeddings only in explicitly non-production environments."""

    if settings.environment in {"local", "test"}:
        return EmbeddingProviderRegistry(
            {DETERMINISTIC_PROVIDER_NAME: DeterministicEmbeddingProvider()}
        )
    return EmbeddingProviderRegistry({})
