"""Deterministic worker-resource fakes shared by background job suites."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class Resources:
    def __init__(self) -> None:
        self.closed = False
        self.session = object()

    @asynccontextmanager
    async def session_factory(self) -> AsyncIterator[object]:
        yield self.session

    async def close(self) -> None:
        self.closed = True
