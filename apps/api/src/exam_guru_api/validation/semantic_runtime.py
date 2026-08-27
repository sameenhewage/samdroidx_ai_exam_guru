from typing import cast

from pydantic import SecretStr

from exam_guru_api.core.config import Settings
from exam_guru_api.validation.openai_semantic_verifier import (
    OpenAISemanticVerifier,
    OpenAISemanticVerifierConfig,
    SemanticVerifierBudget,
    SemanticVerifierPricing,
)
from exam_guru_api.validation.subject import GroundedSemanticVerifier


def create_semantic_verifier(settings: Settings) -> GroundedSemanticVerifier | None:
    if not isinstance(settings, Settings):
        raise TypeError("settings must be Settings")
    if settings.semantic_verifier_provider is None:
        return None
    return OpenAISemanticVerifier(
        config=OpenAISemanticVerifierConfig(
            api_key=cast(SecretStr, settings.semantic_verifier_openai_api_key),
            model=cast(str, settings.semantic_verifier_model),
            model_version=cast(str, settings.semantic_verifier_model_version),
            prompt_version=cast(str, settings.semantic_verifier_prompt_version),
            timeout_ms=cast(int, settings.semantic_verifier_timeout_ms),
        ),
        pricing=SemanticVerifierPricing(
            pricing_version=cast(str, settings.semantic_verifier_pricing_version),
            model=cast(str, settings.semantic_verifier_model),
            model_version=cast(str, settings.semantic_verifier_model_version),
            input_microusd_per_million_tokens=cast(
                int,
                settings.semantic_verifier_input_microusd_per_million_tokens,
            ),
            output_microusd_per_million_tokens=cast(
                int,
                settings.semantic_verifier_output_microusd_per_million_tokens,
            ),
        ),
        budget=SemanticVerifierBudget(
            max_grounding_sources=settings.semantic_verifier_max_grounding_sources,
            max_source_bytes=settings.semantic_verifier_max_source_bytes,
            max_total_source_bytes=settings.semantic_verifier_max_total_source_bytes,
            max_candidate_bytes=settings.semantic_verifier_max_candidate_bytes,
            max_request_bytes=settings.semantic_verifier_max_request_bytes,
            max_output_tokens=settings.semantic_verifier_max_output_tokens,
            max_cost_microusd=settings.semantic_verifier_max_cost_microusd,
        ),
    )


__all__ = ["create_semantic_verifier"]
