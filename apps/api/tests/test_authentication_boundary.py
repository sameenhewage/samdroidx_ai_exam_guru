from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import AdminRole, Permission, Principal
from exam_guru_api.auth.ports import (
    AuthenticationError,
    AuthenticationFailureCode,
    IdentityProvider,
)
from exam_guru_api.main import create_app


class StubIdentityProvider:
    def __init__(self, principal: Principal | None = None) -> None:
        self.principal = principal
        self.tokens: list[str] = []

    async def authenticate(self, access_token: str) -> Principal:
        self.tokens.append(access_token)
        if self.principal is None:
            raise AuthenticationError(AuthenticationFailureCode.INVALID)
        return self.principal


def protected_client(
    provider: IdentityProvider | None,
    permission: Permission,
) -> TestClient:
    application = create_app(identity_provider=provider)

    @application.get("/protected")
    async def protected(
        principal: Annotated[Principal, Depends(require_permission(permission))],
    ) -> dict[str, str]:
        return {"subject_id": str(principal.subject_id)}

    return TestClient(application)


def test_missing_bearer_token_is_unauthorized() -> None:
    provider = StubIdentityProvider(
        Principal(subject_id=UUID(int=1), roles=frozenset({AdminRole.ADMIN}))
    )

    with protected_client(provider, Permission.TAXONOMY_READ) as client:
        response = client.get("/protected")

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}
    assert provider.tokens == []


def test_default_identity_provider_denies_access() -> None:
    with protected_client(None, Permission.TAXONOMY_READ) as client:
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer unavailable-provider"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "identity_provider_unavailable"}}


@pytest.mark.parametrize("token", ["", "x" * 8_193])
def test_malformed_token_is_rejected_before_identity_provider(token: str) -> None:
    provider = StubIdentityProvider(
        Principal(subject_id=UUID(int=1), roles=frozenset({AdminRole.ADMIN}))
    )

    with protected_client(provider, Permission.TAXONOMY_READ) as client:
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert provider.tokens == []


def test_invalid_token_is_unauthorized() -> None:
    provider = StubIdentityProvider()

    with protected_client(provider, Permission.TAXONOMY_READ) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "invalid_access_token"}}


def test_reviewer_can_read_taxonomy() -> None:
    reviewer = Principal(
        subject_id=UUID(int=2),
        roles=frozenset({AdminRole.REVIEWER}),
    )
    provider = StubIdentityProvider(reviewer)

    with protected_client(provider, Permission.TAXONOMY_READ) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer reviewer-token"})

    assert response.status_code == 200
    assert response.json() == {"subject_id": str(reviewer.subject_id)}
    assert provider.tokens == ["reviewer-token"]


def test_reviewer_cannot_write_taxonomy() -> None:
    reviewer = Principal(
        subject_id=UUID(int=2),
        roles=frozenset({AdminRole.REVIEWER}),
    )

    with protected_client(
        StubIdentityProvider(reviewer),
        Permission.TAXONOMY_WRITE,
    ) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer reviewer-token"})

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "permission_denied"}}


def test_admin_can_write_taxonomy() -> None:
    admin = Principal(
        subject_id=UUID(int=1),
        roles=frozenset({AdminRole.ADMIN}),
    )

    with protected_client(StubIdentityProvider(admin), Permission.TAXONOMY_WRITE) as client:
        response = client.get("/protected", headers={"Authorization": "Bearer admin-token"})

    assert response.status_code == 200
    assert response.json() == {"subject_id": str(admin.subject_id)}
