from collections.abc import Awaitable, Callable
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from exam_guru_api.auth.domain import (
    AuthorizationError,
    Permission,
    Principal,
    authorize,
)
from exam_guru_api.auth.ports import (
    AuthenticationError,
    AuthenticationFailureCode,
    IdentityProvider,
)

_bearer = HTTPBearer(auto_error=False)


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _authentication_exception(AuthenticationFailureCode.REQUIRED)
    access_token = credentials.credentials
    if not access_token or len(access_token) > 8_192:
        raise _authentication_exception(AuthenticationFailureCode.INVALID)
    provider = cast(IdentityProvider, request.app.state.identity_provider)
    try:
        return await provider.authenticate(access_token)
    except AuthenticationError as error:
        raise _authentication_exception(error.code) from error


def require_permission(
    permission: Permission,
) -> Callable[[Principal], Awaitable[Principal]]:
    async def dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        try:
            return authorize(principal, permission)
        except AuthorizationError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "permission_denied"},
            ) from error

    return dependency


def _authentication_exception(code: AuthenticationFailureCode) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code.value},
        headers={"WWW-Authenticate": "Bearer"},
    )
