from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol, cast

from openai import BadRequestError, OpenAI, OpenAIError, UnprocessableEntityError
from pydantic import SecretStr

from exam_guru_api.knowledge.embeddings import (
    EmbeddingAccounting,
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.retrieval.repository import (
    validate_embedding_config,
    validate_query_vector,
)

OPENAI_EMBEDDING_PROVIDER = "openai"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_EMBEDDING_SDK_VERSION = "3.1.0"
OPENAI_EMBEDDING_SDK_MAX_RETRIES = 0
OPENAI_EMBEDDING_MAX_DIMENSIONS = 1_536
OPENAI_EMBEDDING_MAX_TOKENS = 8_192
MAX_OPENAI_EMBEDDING_INPUT_BYTES = 8_192
MAX_OPENAI_EMBEDDING_TIMEOUT_MS = 5_000
OPENAI_EMBEDDING_MAX_JOB_RECORDS = 40
_MAX_API_KEY_CHARACTERS = 4_096
_MAX_IDENTIFIER_CHARACTERS = 128
_MAX_PRICE_MICROUSD_PER_MILLION_TOKENS = 100_000_000_000
_MICROUSD_TOKEN_DENOMINATOR = 1_000_000


class _EmbeddingsResource(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _OpenAIClient(Protocol):
    embeddings: _EmbeddingsResource


class _EmbeddingItem(Protocol):
    object: str
    index: int
    embedding: Any


class _EmbeddingUsage(Protocol):
    prompt_tokens: object
    total_tokens: object


class _EmbeddingResponse(Protocol):
    object: str
    model: str
    data: Any
    usage: Any


class OpenAIEmbeddingAdapterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingAdapterConfig:
    api_key: SecretStr
    timeout_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, SecretStr):
            raise TypeError("api_key must be SecretStr")
        api_key = self.api_key.get_secret_value()
        if (
            not api_key
            or api_key != api_key.strip()
            or len(api_key) > _MAX_API_KEY_CHARACTERS
            or any(character.isspace() or not character.isprintable() for character in api_key)
        ):
            raise ValueError("api_key must be bounded non-blank secret text")
        if (
            not isinstance(self.timeout_ms, int)
            or isinstance(self.timeout_ms, bool)
            or not 1 <= self.timeout_ms <= MAX_OPENAI_EMBEDDING_TIMEOUT_MS
        ):
            raise ValueError("timeout_ms is outside the embedding request budget")


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingPricing:
    pricing_version: str
    model: str
    input_microusd_per_million_tokens: int

    def __post_init__(self) -> None:
        for value in (self.pricing_version, self.model):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value) > _MAX_IDENTIFIER_CHARACTERS
                or any(character.isspace() or not character.isprintable() for character in value)
            ):
                raise ValueError("embedding pricing identifiers must be bounded tokens")
        if self.model != OPENAI_EMBEDDING_MODEL:
            raise ValueError("embedding pricing model is unsupported")
        rate = self.input_microusd_per_million_tokens
        if (
            not isinstance(rate, int)
            or isinstance(rate, bool)
            or not 0 <= rate <= _MAX_PRICE_MICROUSD_PER_MILLION_TOKENS
        ):
            raise ValueError("embedding input price is outside the accounting bound")

    def cost_microusd(self, *, input_tokens: int) -> int:
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not 0 <= input_tokens <= 10_000_000
        ):
            raise ValueError("input_tokens is outside the accounting bound")
        numerator = input_tokens * self.input_microusd_per_million_tokens
        return (numerator + _MICROUSD_TOKEN_DENOMINATOR - 1) // _MICROUSD_TOKEN_DENOMINATOR


class OpenAIEmbeddingAdapter:
    def __init__(
        self,
        config: OpenAIEmbeddingAdapterConfig,
        *,
        pricing: OpenAIEmbeddingPricing,
        client: object | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not isinstance(config, OpenAIEmbeddingAdapterConfig):
            raise TypeError("config must be OpenAIEmbeddingAdapterConfig")
        if not isinstance(pricing, OpenAIEmbeddingPricing):
            raise TypeError("pricing must be OpenAIEmbeddingPricing")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")
        self._config = config
        self._pricing = pricing
        self._clock_ns = clock_ns
        if client is None:
            client = OpenAI(
                api_key=config.api_key.get_secret_value(),
                timeout=config.timeout_ms / 1_000,
                max_retries=OPENAI_EMBEDDING_SDK_MAX_RETRIES,
            )
        self._client = cast(_OpenAIClient, client)

    @property
    def pricing(self) -> OpenAIEmbeddingPricing:
        return self._pricing

    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        encoded_text = self._validate_input(text)
        validated_config = self._validate_config(config)
        request_identity = self._request_identity(encoded_text, validated_config)
        started_ns = self._clock_reading()
        try:
            response = self._client.embeddings.create(
                input=text,
                model=validated_config.model,
                dimensions=validated_config.dimension,
                encoding_format="float",
                extra_headers={
                    "Idempotency-Key": request_identity,
                    "X-Client-Request-Id": request_identity,
                },
            )
        except (BadRequestError, UnprocessableEntityError) as error:
            raise EmbeddingContractError("embedding provider rejected the request") from error
        except OpenAIError as error:
            raise OpenAIEmbeddingAdapterError("openai_embedding_provider_unavailable") from error
        finished_ns = self._clock_reading()
        try:
            vector, input_tokens = self._response_vector(response, validated_config)
            accounting = EmbeddingAccounting(
                input_tokens=input_tokens,
                total_tokens=input_tokens,
                cost_microusd=self._pricing.cost_microusd(input_tokens=input_tokens),
                latency_ms=self._latency_ms(started_ns, finished_ns),
            )
        except (AttributeError, EmbeddingContractError, TypeError, ValueError) as error:
            raise OpenAIEmbeddingAdapterError("openai_embedding_invalid_response") from error
        return EmbeddingResult(
            vector=vector,
            config=validated_config,
            accounting=accounting,
        )

    @staticmethod
    def _validate_input(text: object) -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingContractError("embedding text must be a non-blank string")
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise EmbeddingContractError("embedding text must be valid Unicode") from error
        if len(encoded) > MAX_OPENAI_EMBEDDING_INPUT_BYTES:
            raise EmbeddingContractError("embedding text exceeds the provider input limit")
        return encoded

    @staticmethod
    def _validate_config(config: object) -> EmbeddingConfig:
        try:
            validated = validate_embedding_config(config)
        except ValueError as error:
            raise EmbeddingContractError("embedding configuration is invalid") from error
        if (
            validated.provider != OPENAI_EMBEDDING_PROVIDER
            or validated.model != OPENAI_EMBEDDING_MODEL
            or validated.dimension > OPENAI_EMBEDDING_MAX_DIMENSIONS
        ):
            raise EmbeddingContractError("embedding configuration is unsupported")
        return validated

    @staticmethod
    def _request_identity(encoded_text: bytes, config: EmbeddingConfig) -> str:
        digest = sha256(b"exam-guru-openai-embedding-request-v1")
        values = (
            config.provider.encode("utf-8"),
            config.model.encode("utf-8"),
            str(config.dimension).encode("ascii"),
            config.version.encode("utf-8"),
            config.config_fingerprint.encode("utf-8"),
            encoded_text,
        )
        for value in values:
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return f"embedding-{digest.hexdigest()}"

    @staticmethod
    def _response_vector(
        response: object,
        config: EmbeddingConfig,
    ) -> tuple[tuple[float, ...], int]:
        candidate = cast(_EmbeddingResponse, response)
        if candidate.object != "list" or candidate.model != config.model:
            raise ValueError("provider response route is invalid")
        data = candidate.data
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError("provider response cardinality is invalid")
        item = cast(_EmbeddingItem, data[0])
        if item.object != "embedding" or type(item.index) is not int or item.index != 0:
            raise ValueError("provider response item is invalid")
        vector = validate_query_vector(
            item.embedding,
            expected_dimension=config.dimension,
        )
        usage = cast(_EmbeddingUsage, candidate.usage)
        prompt_tokens = usage.prompt_tokens
        total_tokens = usage.total_tokens
        if (
            not isinstance(prompt_tokens, int)
            or isinstance(prompt_tokens, bool)
            or not isinstance(total_tokens, int)
            or isinstance(total_tokens, bool)
            or not 1 <= prompt_tokens <= OPENAI_EMBEDDING_MAX_TOKENS
            or total_tokens != prompt_tokens
        ):
            raise ValueError("provider response usage is invalid")
        return vector, prompt_tokens

    def _clock_reading(self) -> int:
        reading = self._clock_ns()
        if not isinstance(reading, int) or isinstance(reading, bool) or reading < 0:
            raise ValueError("clock_ns must return a non-negative integer")
        return reading

    @staticmethod
    def _latency_ms(started_ns: int, finished_ns: int) -> int:
        elapsed_ns = finished_ns - started_ns
        if elapsed_ns < 0:
            raise ValueError("clock_ns must be monotonic")
        return (elapsed_ns + 999_999) // 1_000_000
