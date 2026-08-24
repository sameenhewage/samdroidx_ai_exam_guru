from collections.abc import AsyncIterator
from typing import Annotated, Protocol, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from exam_guru_api.core.config import Settings
from exam_guru_api.documents.jobs import ExtractionDispatcher
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.infrastructure.resources import ApplicationResources
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry
from exam_guru_api.retrieval.explorer import RetrievalExplorerService


class SessionResources(Protocol):
    session_factory: async_sessionmaker[AsyncSession]


def get_resources(request: Request) -> ApplicationResources:
    return cast(ApplicationResources, request.app.state.resources)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_object_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.object_storage)


def get_extraction_dispatcher(request: Request) -> ExtractionDispatcher:
    return cast(ExtractionDispatcher, request.app.state.extraction_dispatcher)


def get_embedding_provider_registry(request: Request) -> EmbeddingProviderRegistry:
    return cast(EmbeddingProviderRegistry, request.app.state.embedding_provider_registry)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    resources = cast(SessionResources, request.app.state.resources)
    async with resources.session_factory() as session:
        yield session


def get_retrieval_explorer_service(
    session: Annotated[AsyncSession, Depends(get_database_session)],
    providers: Annotated[EmbeddingProviderRegistry, Depends(get_embedding_provider_registry)],
) -> RetrievalExplorerService:
    return RetrievalExplorerService(session, providers)
