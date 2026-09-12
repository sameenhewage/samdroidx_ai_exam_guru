import hashlib
from typing import cast
from uuid import UUID

import pymupdf
import pytest
from pydantic import ValidationError

from exam_guru_api.documents.understanding_provider import (
    UnderstandingBudget,
    UnderstandingProviderError,
    UnderstandingProviderProfile,
    UnderstandingProviderResult,
    UnderstandingRequest,
    understanding_request_key,
)
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity
from exam_guru_api.generation.domain import GenerationAccounting
from exam_guru_api.generation.ports import ProviderFailureCode
from tests.test_document_understanding_contracts import counting_candidate, parse


def profile() -> UnderstandingProviderProfile:
    return UnderstandingProviderProfile(
        provider="fixture",
        provider_version="fixture.v1",
        model="visual-fixture",
        model_version="visual-fixture.v1",
        prompt_version="document-understanding.v1",
        schema_version="page-understanding.v1",
        pricing_version="fixture-pricing.v1",
        input_microusd_per_million_tokens=1000000,
        output_microusd_per_million_tokens=2000000,
        temperature=0.0,
    )


def request() -> UnderstandingRequest:
    with pymupdf.open() as document:
        page = document.new_page(width=30, height=40)
        image = page.get_pixmap(alpha=False).tobytes("png")
    return UnderstandingRequest(
        source=PageArtifactIdentity(
            document_id=UUID(int=99301),
            source_sha256="a" * 64,
            page_number=1,
            image_sha256=hashlib.sha256(image).hexdigest(),
        ),
        image_png=image,
        profile=profile(),
        budget=UnderstandingBudget(),
    )


def test_understanding_requires_a_real_bounded_source_bound_rendered_page() -> None:
    value = request()
    assert value.image_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert value.image_dimensions == (30, 40)
    assert "image_png=" not in repr(value)
    with pytest.raises(ValidationError, match="image"):
        UnderstandingRequest.model_validate(
            value.model_copy(update={"image_png": b"plain extracted text"})
        )
    with pytest.raises(ValidationError, match="image"):
        UnderstandingRequest.model_validate(
            value.model_copy(
                update={"source": value.source.model_copy(update={"image_sha256": "b" * 64})}
            )
        )


def test_rendered_input_is_not_a_whole_document_or_unbounded_provider_request() -> None:
    value = request()
    with pytest.raises(ValidationError, match="image"):
        UnderstandingRequest.model_validate(
            value.model_copy(
                update={"budget": value.budget.model_copy(update={"max_image_bytes": 8})}
            )
        )
    with pytest.raises(ValidationError, match="image"):
        UnderstandingRequest.model_validate(
            value.model_copy(
                update={"budget": value.budget.model_copy(update={"max_image_pixels": 1})}
            )
        )


def test_unchanged_source_and_configuration_have_stable_opaque_idempotency() -> None:
    value = request()
    key = understanding_request_key(value)
    assert len(key) == 64
    assert key == understanding_request_key(value)
    assert key != understanding_request_key(
        value.model_copy(update={"source": value.source.model_copy(update={"page_number": 2})})
    )
    changed_profile = value.profile.model_copy(update={"model_version": "visual-fixture.v2"})
    assert key != understanding_request_key(value.model_copy(update={"profile": changed_profile}))
    assert "visual-fixture" not in key


def test_provider_profiles_cannot_contain_secrets_or_a_trust_decision() -> None:
    for name in ("api_key", "verified", "approve_source"):
        payload = profile().model_dump()
        payload[name] = "not-a-credential"
        with pytest.raises(ValidationError, match="Extra inputs"):
            UnderstandingProviderProfile.model_validate(payload)


def test_provider_results_remain_candidates_and_retain_accounting_on_failure() -> None:
    value = request()
    accounting = GenerationAccounting(
        input_tokens=10, output_tokens=20, total_tokens=30, cost_microusd=50, latency_ms=100
    )
    result = UnderstandingProviderResult(
        source=value.source,
        profile=value.profile,
        content=parse(counting_candidate()),
        accounting=accounting,
    )
    assert result.disposition == "requires_verification"
    assert result.profile.fingerprint == profile().fingerprint
    assert (
        result.profile.fingerprint != profile().model_copy(update={"temperature": 1.0}).fingerprint
    )
    with pytest.raises(ValidationError):
        UnderstandingProviderResult.model_validate(
            result.model_copy(update={"disposition": "verified"})
        )
    failure = UnderstandingProviderError(
        ProviderFailureCode.RATE_LIMITED, accounting=accounting, retry_after_ms=1000
    )
    assert failure.retryable is True
    assert failure.accounting == accounting
    assert failure.retry_after_ms == 1000
    assert str(failure) == "rate_limited"
    assert UnderstandingProviderError(ProviderFailureCode.TIMEOUT).accounting is None


def test_malformed_failure_accounting_cannot_cross_the_provider_boundary() -> None:
    with pytest.raises(ValueError, match="accounting"):
        UnderstandingProviderError(
            ProviderFailureCode.INVALID_RESPONSE,
            accounting=cast(GenerationAccounting, {"untrusted": "not accounting"}),
        )


def test_understanding_pricing_uses_integer_accounting_without_rounding_away_cost() -> None:
    assert profile().cost_microusd(1, 1) == 3
    assert profile().cost_microusd(0, 1) == 2
    with pytest.raises(ValueError, match="token"):
        profile().cost_microusd(-1, 1)
