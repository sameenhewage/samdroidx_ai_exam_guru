from collections.abc import AsyncIterator
from typing import Protocol, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.resources import ApplicationResources


class SessionResources(Protocol):
    session_factory: async_sessionmaker[AsyncSession]


def get_resources(request: Request) -> ApplicationResources:
    return cast(ApplicationResources, request.app.state.resources)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    resources = cast(SessionResources, request.app.state.resources)
    async with resources.session_factory() as session:
        yield session
