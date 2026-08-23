from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from exam_guru_api.core.config import Settings


class ApplicationResources(Protocol):
    async def check_database(self) -> None: ...

    async def check_valkey(self) -> None: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class RuntimeResources:
    database_engine: AsyncEngine
    valkey: Redis

    async def check_database(self) -> None:
        async with self.database_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_valkey(self) -> None:
        if not await self.valkey.ping():
            raise ConnectionError

    async def close(self) -> None:
        await self.valkey.aclose()
        await self.database_engine.dispose()


def create_resources(settings: Settings) -> RuntimeResources:
    database_engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"timeout": 5},
        pool_pre_ping=True,
        pool_recycle=300,
    )
    valkey = Redis.from_url(
        settings.valkey_url.get_secret_value(),
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    return RuntimeResources(database_engine=database_engine, valkey=valkey)
