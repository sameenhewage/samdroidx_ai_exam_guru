from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from exam_guru_api import __version__
from exam_guru_api.api.request_limits import RequestBodyLimitMiddleware
from exam_guru_api.api.router import api_router
from exam_guru_api.auth.adapters import build_identity_provider
from exam_guru_api.auth.ports import IdentityProvider
from exam_guru_api.auth.rate_limits import (
    RateLimiter,
    UnavailableRateLimiter,
    create_rate_limiter,
)
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.jobs import ExtractionDispatcher, create_extraction_dispatcher
from exam_guru_api.generation.jobs import GenerationDispatcher, create_generation_dispatcher
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry, create_generation_runtime
from exam_guru_api.infrastructure.object_storage import ObjectStorage, create_object_storage
from exam_guru_api.infrastructure.resources import (
    ApplicationResources,
    create_resources,
)
from exam_guru_api.knowledge.embedding_jobs import (
    EmbeddingDispatcher,
    create_embedding_dispatcher,
)
from exam_guru_api.observability import configure_observability
from exam_guru_api.retrieval.embeddings import (
    EmbeddingProviderRegistry,
    create_embedding_provider_registry,
)
from exam_guru_api.teacher_papers.jobs import (
    PaperGenerationDispatcher,
    create_paper_generation_dispatcher,
)
from exam_guru_api.validation.pipeline import ValidationPipeline, build_default_pipeline

ResourceFactory = Callable[[Settings], ApplicationResources]


def create_app(
    *,
    settings: Settings | None = None,
    resource_factory: ResourceFactory = create_resources,
    identity_provider: IdentityProvider | None = None,
    object_storage: ObjectStorage | None = None,
    extraction_dispatcher: ExtractionDispatcher | None = None,
    generation_dispatcher: GenerationDispatcher | None = None,
    paper_generation_dispatcher: PaperGenerationDispatcher | None = None,
    embedding_dispatcher: EmbeddingDispatcher | None = None,
    generation_runtime_registry: GenerationRuntimeRegistry | None = None,
    validation_pipeline: ValidationPipeline | None = None,
    embedding_provider_registry: EmbeddingProviderRegistry | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resources = resource_factory(resolved_settings)
        application.state.resources = resources
        if rate_limiter is None:
            application.state.rate_limiter = create_rate_limiter(resolved_settings, resources)
        try:
            yield
        finally:
            try:
                await resources.close()
            finally:
                try:
                    close_storage = getattr(application.state.object_storage, "close", None)
                    if close_storage is not None:
                        close_storage()
                finally:
                    observability_runtime.shutdown()

    application = FastAPI(
        title="AI Exam Guru API",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=resolved_settings.max_upload_bytes + 1024 * 1024,
    )
    application.state.settings = resolved_settings
    application.state.rate_limiter = (
        rate_limiter if rate_limiter is not None else UnavailableRateLimiter()
    )
    application.state.identity_provider = (
        identity_provider
        if identity_provider is not None
        else build_identity_provider(resolved_settings)
    )
    application.state.object_storage = (
        object_storage if object_storage is not None else create_object_storage(resolved_settings)
    )
    application.state.extraction_dispatcher = (
        extraction_dispatcher
        if extraction_dispatcher is not None
        else create_extraction_dispatcher(resolved_settings)
    )
    application.state.generation_dispatcher = (
        generation_dispatcher
        if generation_dispatcher is not None
        else create_generation_dispatcher(resolved_settings)
    )
    application.state.paper_generation_dispatcher = (
        paper_generation_dispatcher
        if paper_generation_dispatcher is not None
        else create_paper_generation_dispatcher(resolved_settings)
    )
    application.state.embedding_dispatcher = (
        embedding_dispatcher
        if embedding_dispatcher is not None
        else create_embedding_dispatcher(resolved_settings)
    )
    application.state.generation_runtime_registry = (
        generation_runtime_registry
        if generation_runtime_registry is not None
        else create_generation_runtime(resolved_settings)
    )
    application.state.validation_pipeline = (
        validation_pipeline if validation_pipeline is not None else build_default_pipeline()
    )
    resolved_embedding_registry = (
        embedding_provider_registry
        if embedding_provider_registry is not None
        else create_embedding_provider_registry(resolved_settings)
    )
    if resolved_settings.environment in {"staging", "production"}:
        resolved_embedding_registry = resolved_embedding_registry.without_deterministic_providers()
    application.state.embedding_provider_registry = resolved_embedding_registry
    application.include_router(api_router, prefix="/api/v1")
    observability_runtime = configure_observability(application, resolved_settings)
    application.state.operational_telemetry = observability_runtime.operational_telemetry
    return application


app = create_app()
