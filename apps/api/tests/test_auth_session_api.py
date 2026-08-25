from uuid import UUID

from fastapi.testclient import TestClient

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.main import create_app

SUBJECT_ID = UUID("b53b84f8-a97b-5d84-b028-76f6f60539a5")


class SessionIdentityProvider:
    def __init__(
        self,
        principal: Principal | None = None,
        failure: AuthenticationFailureCode | None = None,
    ) -> None:
        self.principal = principal
        self.failure = failure

    async def authenticate(self, access_token: str) -> Principal:
        if self.failure is not None:
            raise AuthenticationError(self.failure)
        assert access_token == "opaque-provider-access-token"
        assert self.principal is not None
        return self.principal


def test_auth_session_returns_only_subject_and_sorted_internal_roles() -> None:
    provider = SessionIdentityProvider(
        principal=Principal(
            subject_id=SUBJECT_ID,
            roles=frozenset({AdminRole.REVIEWER, AdminRole.ADMIN}),
        )
    )

    with TestClient(create_app(identity_provider=provider)) as client:
        response = client.get(
            "/api/v1/auth/session",
            headers={
                "Authorization": "Bearer opaque-provider-access-token",
                "Cookie": "role=owner; external_role=super-admin",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "subject_id": str(SUBJECT_ID),
        "roles": ["admin", "reviewer"],
    }
    serialized = response.text
    for forbidden in (
        "opaque-provider-access-token",
        "identity.internal.example",
        "realm_roles",
        "exam-guru-admin",
        "owner",
        "super-admin",
    ):
        assert forbidden not in serialized


def test_auth_session_requires_a_bearer_token_with_stable_401() -> None:
    provider = SessionIdentityProvider(
        principal=Principal(subject_id=SUBJECT_ID, roles=frozenset({AdminRole.ADMIN}))
    )

    with TestClient(create_app(identity_provider=provider)) as client:
        response = client.get("/api/v1/auth/session")

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_session_normalizes_invalid_token_to_stable_401_without_leakage() -> None:
    provider = SessionIdentityProvider(failure=AuthenticationFailureCode.INVALID)

    with TestClient(create_app(identity_provider=provider)) as client:
        response = client.get(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer opaque-provider-access-token"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "invalid_access_token"}}
    assert response.headers["www-authenticate"] == "Bearer"
    assert "opaque-provider-access-token" not in response.text


def test_auth_session_normalizes_provider_outage_to_stable_503_without_challenge() -> None:
    provider = SessionIdentityProvider(failure=AuthenticationFailureCode.UNAVAILABLE)

    with TestClient(create_app(identity_provider=provider)) as client:
        response = client.get(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer opaque-provider-access-token"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "identity_provider_unavailable"}}
    assert "www-authenticate" not in response.headers
    assert "opaque-provider-access-token" not in response.text
