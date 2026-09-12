import hashlib
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, model_validator

from exam_guru_api.documents.page_images import PageImageError, PageImageLimits, _png_dimensions
from exam_guru_api.documents.understanding_contracts import (
    PageUnderstanding,
    UnderstandingModel,
    _canonical_bytes,
)
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity
from exam_guru_api.generation.domain import GenerationAccounting
from exam_guru_api.generation.ports import ProviderError, ProviderFailureCode

Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]*$")
]


class UnderstandingBudget(UnderstandingModel):
    max_image_bytes: int = Field(default=8 * 1024 * 1024, ge=8, le=8 * 1024 * 1024)
    max_image_pixels: int = Field(default=16_000_000, ge=1, le=16_000_000)
    max_output_tokens: int = Field(default=8192, ge=1, le=16384)
    timeout_ms: int = Field(default=30000, ge=1, le=60000)
    max_cost_microusd: int = Field(default=1_000_000, ge=1, le=100_000_000)


class UnderstandingProviderProfile(UnderstandingModel):
    provider: Identifier
    provider_version: Identifier
    model: Identifier
    model_version: Identifier
    prompt_version: Identifier
    schema_version: Literal["page-understanding.v1"]
    pricing_version: Identifier
    input_microusd_per_million_tokens: int = Field(ge=0, le=100_000_000_000)
    output_microusd_per_million_tokens: int = Field(ge=0, le=100_000_000_000)
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()

    def cost_microusd(self, input_tokens: int, output_tokens: int) -> int:
        if any(
            type(value) is not int or not 0 <= value <= 10_000_000
            for value in (input_tokens, output_tokens)
        ):
            raise ValueError("provider token counts must be bounded non-negative integers")
        numerator = (
            input_tokens * self.input_microusd_per_million_tokens
            + output_tokens * self.output_microusd_per_million_tokens
        )
        return (numerator + 999999) // 1000000


class UnderstandingRequest(UnderstandingModel):
    source: PageArtifactIdentity
    image_png: bytes = Field(min_length=8, max_length=8 * 1024 * 1024, repr=False)
    profile: UnderstandingProviderProfile
    budget: UnderstandingBudget

    @property
    def image_dimensions(self) -> tuple[int, int]:
        return _png_dimensions(
            self.image_png,
            PageImageLimits(
                max_png_bytes=self.budget.max_image_bytes, max_pixels=self.budget.max_image_pixels
            ),
        )

    @model_validator(mode="after")
    def verified_visual_input(self) -> Self:
        if hashlib.sha256(self.image_png).hexdigest() != self.source.image_sha256:
            raise ValueError("rendered image does not match the bound source identity")
        try:
            _ = self.image_dimensions
        except PageImageError:
            raise ValueError("rendered source image is invalid or exceeds its budget") from None
        return self


class UnderstandingProviderResult(UnderstandingModel):
    source: PageArtifactIdentity
    profile: UnderstandingProviderProfile
    content: PageUnderstanding = Field(repr=False)
    accounting: GenerationAccounting
    disposition: Literal["requires_verification"] = "requires_verification"


class DocumentUnderstandingProvider(Protocol):
    def understand(self, request: UnderstandingRequest) -> UnderstandingProviderResult: ...


class UnderstandingProviderError(ProviderError):
    def __init__(
        self,
        code: ProviderFailureCode,
        *,
        accounting: GenerationAccounting | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        if accounting is not None and not isinstance(accounting, GenerationAccounting):
            raise ValueError("provider failure accounting must use the first-party contract")
        self.accounting = accounting
        super().__init__(code, retry_after_ms=retry_after_ms)


def understanding_request_key(request: UnderstandingRequest) -> str:
    request = UnderstandingRequest.model_validate(request)
    digest = hashlib.sha256(b"document-understanding-request.v1\x00")
    for part in (request.source, request.profile, request.budget):
        encoded = _canonical_bytes(part)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()
