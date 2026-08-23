import asyncio
from typing import cast

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from exam_guru_api.infrastructure.resources import RuntimeResources


class UnavailableValkey:
    async def ping(self) -> bool:
        return False


def test_valkey_readiness_rejects_a_false_ping() -> None:
    resources = RuntimeResources(
        database_engine=cast(AsyncEngine, None),
        valkey=cast(Redis, UnavailableValkey()),
    )

    with pytest.raises(ConnectionError):
        asyncio.run(resources.check_valkey())
