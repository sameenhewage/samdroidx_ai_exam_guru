from dataclasses import dataclass, field
from typing import cast

from pydantic import SecretStr

from exam_guru_api.core.config import Settings
from exam_guru_api.documents.evaluation_references import _capture_image
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceCandidateImageMetadata,
    SourceImageIdentity,
    SourceImageStorage,
)
from exam_guru_api.documents.understanding_contracts import (
    EducationalUnderstanding,
    PageObservation,
    PageRegionObservation,
    PageUnderstanding,
    UnderstandingUncertainty,
)
from exam_guru_api.documents.understanding_openai import (
    OPENAI_UNDERSTANDING_SDK_VERSION,
    UNDERSTANDING_PROMPT_VERSION,
    OpenAIUnderstandingConfig,
    OpenAIUnderstandingProvider,
)
from exam_guru_api.documents.understanding_provider import (
    DocumentUnderstandingProvider,
    UnderstandingBudget,
    UnderstandingProviderError,
    UnderstandingProviderProfile,
    UnderstandingProviderResult,
    UnderstandingRequest,
)
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity
from exam_guru_api.generation.domain import GenerationAccounting
from exam_guru_api.generation.ports import ProviderFailureCode


@dataclass(frozen=True, slots=True)
class UnderstandingRuntime:
    profile: UnderstandingProviderProfile
    budget: UnderstandingBudget
    provider: DocumentUnderstandingProvider = field(repr=False)
    fixture_runtime_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedUnderstandingInput:
    request: UnderstandingRequest
    image_metadata: SourceCandidateImageMetadata


def prepare_understanding_input(
    source: SourceImageIdentity,
    page_number: int,
    provenance: dict[str, object] | None,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts | None,
    runtime: UnderstandingRuntime,
) -> PreparedUnderstandingInput:
    source.check_page(page_number)
    if artifacts is None:
        raise PageImageError("source_page_image_artifact_unavailable")
    metadata = _capture_image(source, page_number, provenance, storage, artifacts)
    image = artifacts.read(metadata, source=source, page_number=page_number)
    parsed = SourceCandidateImageMetadata.model_validate(metadata)
    request = UnderstandingRequest(
        source=PageArtifactIdentity(
            document_id=source.document_id,
            source_sha256=source.checksum_sha256,
            page_number=page_number,
            image_sha256=parsed.sha256,
        ),
        image_png=image,
        profile=runtime.profile,
        budget=runtime.budget,
    )
    return PreparedUnderstandingInput(request, parsed)


class _FixtureUnderstandingProvider:
    def __init__(self, profile: UnderstandingProviderProfile) -> None:
        self.profile = profile

    def understand(self, request: UnderstandingRequest) -> UnderstandingProviderResult:
        request = UnderstandingRequest.model_validate(request)
        if request.profile != self.profile:
            raise UnderstandingProviderError(ProviderFailureCode.INVALID_REQUEST)
        content = PageUnderstanding(
            schema_version="page-understanding.v1",
            observation=PageObservation(
                language="en",
                relationships=(),
                regions=(
                    PageRegionObservation(
                        key="fixture",
                        kind="paragraph",
                        reading_order=0,
                        parent_key=None,
                        bounds=None,
                        polygon=(),
                        exact_text="Synthetic visual-understanding fixture.",
                        equations=(),
                        table=None,
                        visual_facts=(),
                    ),
                ),
            ),
            education=EducationalUnderstanding(claims=()),
            uncertainties=(
                UnderstandingUncertainty(
                    key="fixture_only",
                    region_keys=("fixture",),
                    field="source",
                    reason=(
                        "Synthetic contract fixture, not actual source understanding "
                        "or human truth."
                    ),
                    alternatives=(),
                ),
            ),
        )
        return UnderstandingProviderResult(
            source=request.source,
            profile=self.profile,
            content=content,
            accounting=GenerationAccounting(
                input_tokens=1, output_tokens=1, total_tokens=2, cost_microusd=0, latency_ms=0
            ),
        )


def create_understanding_runtime(settings: Settings) -> UnderstandingRuntime | None:
    settings = Settings.model_validate(settings.model_dump(exclude_unset=True))
    if settings.document_understanding_provider is None:
        return None
    budget = UnderstandingBudget(
        timeout_ms=settings.document_understanding_timeout_ms,
        max_output_tokens=settings.document_understanding_max_output_tokens,
        max_cost_microusd=settings.document_understanding_max_cost_microusd,
    )
    if settings.document_understanding_provider == "deterministic":
        profile = UnderstandingProviderProfile(
            provider="deterministic-fixture",
            provider_version="1.0.0",
            model="synthetic-source-fixture",
            model_version="1.0.0",
            prompt_version=UNDERSTANDING_PROMPT_VERSION,
            schema_version="page-understanding.v1",
            pricing_version="non-billable-fixture.v1",
            input_microusd_per_million_tokens=0,
            output_microusd_per_million_tokens=0,
            temperature=0.0,
        )
        return UnderstandingRuntime(
            profile,
            budget,
            _FixtureUnderstandingProvider(profile),
            settings.document_understanding_fixture_runtime_id or settings.test_runtime_id,
        )
    profile = UnderstandingProviderProfile(
        provider="openai",
        provider_version=OPENAI_UNDERSTANDING_SDK_VERSION,
        model=cast(str, settings.document_understanding_model),
        model_version=cast(str, settings.document_understanding_model_version),
        prompt_version=UNDERSTANDING_PROMPT_VERSION,
        schema_version="page-understanding.v1",
        pricing_version=cast(str, settings.document_understanding_pricing_version),
        input_microusd_per_million_tokens=cast(
            int, settings.document_understanding_input_microusd_per_million_tokens
        ),
        output_microusd_per_million_tokens=cast(
            int, settings.document_understanding_output_microusd_per_million_tokens
        ),
        temperature=cast(float, settings.document_understanding_temperature),
    )
    provider = OpenAIUnderstandingProvider(
        OpenAIUnderstandingConfig(
            api_key=cast(SecretStr, settings.document_understanding_openai_api_key),
            profile=profile,
            image_input_verified=settings.document_understanding_image_input_verified,
            structured_output_verified=settings.document_understanding_structured_output_verified,
        )
    )
    return UnderstandingRuntime(profile, budget, provider)
