from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class _RequestBodyTooLargeError(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = next(
            (value for key, value in scope["headers"] if key.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await JSONResponse(
                    status_code=400,
                    content={"detail": {"code": "invalid_content_length"}},
                )(scope, receive, send)
                return
            if declared_size > self._max_body_bytes:
                await self._too_large(scope, receive, send)
                return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    raise _RequestBodyTooLargeError
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _RequestBodyTooLargeError:
            await self._too_large(scope, receive, send)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            status_code=413,
            content={"detail": {"code": "request_too_large"}},
        )(scope, receive, send)
