import asyncio

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from exam_guru_api.api.request_limits import RequestBodyLimitMiddleware


async def run_middleware(
    *,
    scope: Scope,
    request_messages: list[Message],
    app: ASGIApp | None = None,
) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return request_messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    async def consume(
        _scope: Scope,
        limited_receive: Receive,
        limited_send: Send,
    ) -> None:
        await limited_receive()
        await limited_send({"type": "http.response.start", "status": 204, "headers": []})
        await limited_send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app or consume, max_body_bytes=4)
    await middleware(scope, receive, send)
    return sent


def test_request_limit_rejects_invalid_content_length() -> None:
    messages = asyncio.run(
        run_middleware(
            scope={"type": "http", "headers": [(b"content-length", b"invalid")]},
            request_messages=[{"type": "http.request", "body": b""}],
        )
    )

    assert messages[0]["status"] == 400


def test_request_limit_counts_streamed_body_without_content_length() -> None:
    messages = asyncio.run(
        run_middleware(
            scope={"type": "http", "headers": []},
            request_messages=[{"type": "http.request", "body": b"12345"}],
        )
    )

    assert messages[0]["status"] == 413


def test_request_limit_passes_non_body_http_messages() -> None:
    messages = asyncio.run(
        run_middleware(
            scope={"type": "http", "headers": []},
            request_messages=[{"type": "http.disconnect"}],
        )
    )

    assert messages[0]["status"] == 204


def test_request_limit_allows_bounded_streamed_body() -> None:
    messages = asyncio.run(
        run_middleware(
            scope={"type": "http", "headers": []},
            request_messages=[{"type": "http.request", "body": b"1234"}],
        )
    )

    assert messages[0]["status"] == 204


def test_request_limit_passes_non_http_scopes_through() -> None:
    called = False

    async def app(
        _scope: Scope,
        _receive: Receive,
        _send: Send,
    ) -> None:
        nonlocal called
        called = True

    asyncio.run(
        run_middleware(
            scope={"type": "lifespan", "headers": []},
            request_messages=[],
            app=app,
        )
    )

    assert called
