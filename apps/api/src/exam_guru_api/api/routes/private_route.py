from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


class PrivateStudioRoute(APIRoute):
    """Force private caching headers and a stable bounded validation failure code."""

    invalid_request_code = "invalid_request"

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                result = await original(request)
                result.headers.update(PRIVATE_HEADERS)
                return result
            except StarletteHTTPException as error:
                raise HTTPException(
                    error.status_code,
                    detail=error.detail,
                    headers={**(error.headers or {}), **PRIVATE_HEADERS},
                ) from None
            except RequestValidationError:
                raise HTTPException(
                    422,
                    detail={"code": self.invalid_request_code},
                    headers=PRIVATE_HEADERS,
                ) from None

        return handler
