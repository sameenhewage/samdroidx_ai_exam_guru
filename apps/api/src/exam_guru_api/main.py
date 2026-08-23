from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from exam_guru_api import __version__
from exam_guru_api.api.router import api_router
from exam_guru_api.auth.adapters import build_identity_provider
from exam_guru_api.auth.ports import IdentityProvider
from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.resources import (
    ApplicationResources,
    create_resources,
)
from exam_guru_api.observability import configure_observability

ResourceFactory = Callable[[Settings], ApplicationResources]


def create_app(
    *,
    settings: Settings | None = None,
    resource_factory: ResourceFactory = create_resources,
    identity_provider: IdentityProvider | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resources = resource_factory(resolved_settings)
        application.state.resources = resources
        try:
            yield
        finally:
            try:
                await resources.close()
            finally:
                observability_runtime.shutdown()

    application = FastAPI(
        title="AI Exam Guru API",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.identity_provider = (
        identity_provider
        if identity_provider is not None
        else build_identity_provider(resolved_settings)
    )
    application.include_router(api_router, prefix="/api/v1")
    observability_runtime = configure_observability(application, resolved_settings)
    return application


app = create_app()
