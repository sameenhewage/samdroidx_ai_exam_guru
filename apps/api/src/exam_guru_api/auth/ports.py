from enum import StrEnum
from typing import Protocol

from exam_guru_api.auth.domain import Principal


class AuthenticationFailureCode(StrEnum):
    REQUIRED = "authentication_required"
    INVALID = "invalid_access_token"
    UNAVAILABLE = "identity_provider_unavailable"


class AuthenticationError(PermissionError):
    def __init__(self, code: AuthenticationFailureCode) -> None:
        self.code = code
        super().__init__(code.value)


class IdentityProvider(Protocol):
    async def authenticate(self, access_token: str) -> Principal: ...


class DenyAllIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        del access_token
        raise AuthenticationError(AuthenticationFailureCode.UNAVAILABLE)
