import hashlib
import json
import os
from collections.abc import Callable

import pytest
from pydantic import SecretStr

from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.retrieval.openai_embedding_adapter import (
    MAX_OPENAI_EMBEDDING_INPUT_BYTES,
    OPENAI_EMBEDDING_PROVIDER,
    OPENAI_EMBEDDING_SDK_MAX_RETRIES,
    OPENAI_EMBEDDING_SDK_VERSION,
    OpenAIEmbeddingAdapter,
    OpenAIEmbeddingAdapterConfig,
    OpenAIEmbeddingPricing,
)

_REQUIRED_LIVE_ENV = (
    "OPENAI_API_KEY",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_MODEL",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_DIMENSION",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_VERSION",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_CONFIG_FINGERPRINT",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_PRICING_VERSION",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_INPUT_MICROUSD_PER_MILLION_TOKENS",
    "EXAM_GURU_OPENAI_EMBEDDING_LIVE_TIMEOUT_MS",
)
_LIVE_OPT_IN = os.getenv("EXAM_GURU_RUN_OPENAI_EMBEDDING_LIVE") == "1"
_MISSING_LIVE_ENV = tuple(name for name in _REQUIRED_LIVE_ENV if not os.getenv(name))

pytestmark = [
    pytest.mark.live_openai,
    pytest.mark.skipif(
        not _LIVE_OPT_IN or bool(_MISSING_LIVE_ENV),
        reason=(
            "requires EXAM_GURU_RUN_OPENAI_EMBEDDING_LIVE=1 and every explicit live "
            "OpenAI embedding credential/model/pricing/timeout setting"
        ),
    ),
]


def _required_env(name: str) -> str:
    value = os.getenv(name)
    assert value is not None
    return value


def test_live_openai_embedding_contract_records_bounded_accounting(
    record_property: Callable[[str, object], None],
) -> None:
    model = _required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_MODEL")
    dimension = int(_required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_DIMENSION"))
    version = _required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_VERSION")
    fingerprint = _required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_CONFIG_FINGERPRINT")
    pricing = OpenAIEmbeddingPricing(
        pricing_version=_required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_PRICING_VERSION"),
        model=model,
        input_microusd_per_million_tokens=int(
            _required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_INPUT_MICROUSD_PER_MILLION_TOKENS")
        ),
    )
    timeout_ms = int(_required_env("EXAM_GURU_OPENAI_EMBEDDING_LIVE_TIMEOUT_MS"))
    config = EmbeddingConfig(
        provider=OPENAI_EMBEDDING_PROVIDER,
        model=model,
        dimension=dimension,
        version=version,
        config_fingerprint=fingerprint,
    )
    adapter = OpenAIEmbeddingAdapter(
        OpenAIEmbeddingAdapterConfig(
            api_key=SecretStr(_required_env("OPENAI_API_KEY")),
            timeout_ms=timeout_ms,
        ),
        pricing=pricing,
    )

    result = adapter.embed(
        "A square has four equal sides. සමචතුරස්‍රයක සමාන පැති හතරක් ඇත.",
        config,
    )

    assert len(result.vector) == dimension
    assert result.accounting is not None
    assert result.accounting.input_tokens == result.accounting.total_tokens
    assert result.accounting.cost_microusd == pricing.cost_microusd(
        input_tokens=result.accounting.input_tokens
    )
    vector_fingerprint = hashlib.sha256(
        json.dumps(result.vector, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    for key, value in {
        "provider": config.provider,
        "provider_version": OPENAI_EMBEDDING_SDK_VERSION,
        "model": config.model,
        "dimension": config.dimension,
        "embedding_version": config.version,
        "config_fingerprint": config.config_fingerprint,
        "pricing_version": pricing.pricing_version,
        "input_microusd_per_million_tokens": pricing.input_microusd_per_million_tokens,
        "timeout_ms": timeout_ms,
        "sdk_max_retries": OPENAI_EMBEDDING_SDK_MAX_RETRIES,
        "max_input_bytes": MAX_OPENAI_EMBEDDING_INPUT_BYTES,
        "input_tokens": result.accounting.input_tokens,
        "total_tokens": result.accounting.total_tokens,
        "cost_microusd": result.accounting.cost_microusd,
        "latency_ms": result.accounting.latency_ms,
        "vector_sha256": vector_fingerprint,
    }.items():
        record_property(key, value)
