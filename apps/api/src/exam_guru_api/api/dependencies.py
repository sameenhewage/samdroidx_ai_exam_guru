from collections.abc import AsyncIterator
from typing import Annotated, Protocol, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from exam_guru_api.auth.rate_limits import RateLimiter
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.jobs import ExtractionDispatcher
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.infrastructure.resources import ApplicationResources
from exam_guru_api.knowledge.embedding_jobs import EmbeddingDispatcher
from exam_guru_api.observability import OperationalTelemetry
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry
from exam_guru_api.retrieval.explorer import RetrievalExplorerService
from exam_guru_api.teacher_papers.jobs import PaperGenerationDispatcher
from exam_guru_api.validation.pipeline import ValidationPipeline


class SessionResources(Protocol):
    session_factory: async_sessionmaker[AsyncSession]


def get_resources(request: Request) -> ApplicationResources:
    return cast(ApplicationResources, request.app.state.resources)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_rate_limiter(request: Request) -> RateLimiter:
    return cast(RateLimiter, request.app.state.rate_limiter)


def get_object_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.object_storage)


def get_extraction_dispatcher(request: Request) -> ExtractionDispatcher:
    return cast(ExtractionDispatcher, request.app.state.extraction_dispatcher)


def get_generation_dispatcher(request: Request) -> GenerationDispatcher:
    return cast(GenerationDispatcher, request.app.state.generation_dispatcher)


def get_paper_generation_dispatcher(request: Request) -> PaperGenerationDispatcher:
    return cast(PaperGenerationDispatcher, request.app.state.paper_generation_dispatcher)


def get_embedding_dispatcher(request: Request) -> EmbeddingDispatcher:
    return cast(EmbeddingDispatcher, request.app.state.embedding_dispatcher)


def get_generation_runtime_registry(request: Request) -> GenerationRuntimeRegistry:
    return cast(GenerationRuntimeRegistry, request.app.state.generation_runtime_registry)


def get_validation_pipeline(request: Request) -> ValidationPipeline:
    return cast(ValidationPipeline, request.app.state.validation_pipeline)


def get_embedding_provider_registry(request: Request) -> EmbeddingProviderRegistry:
    return cast(EmbeddingProviderRegistry, request.app.state.embedding_provider_registry)


def get_operational_telemetry(request: Request) -> OperationalTelemetry:
    return cast(OperationalTelemetry, request.app.state.operational_telemetry)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    resources = cast(SessionResources, request.app.state.resources)
    async with resources.session_factory() as session:
        yield session


def get_retrieval_explorer_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
    providers: Annotated[EmbeddingProviderRegistry, Depends(get_embedding_provider_registry)],
    telemetry: Annotated[OperationalTelemetry, Depends(get_operational_telemetry)],
) -> RetrievalExplorerService:
    return RetrievalExplorerService(session, providers, telemetry=telemetry)
