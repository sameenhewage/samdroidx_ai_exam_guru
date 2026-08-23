import asyncio
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.auth.adapters import DeterministicIdentityProvider, build_identity_provider
from exam_guru_api.auth.domain import AdminRole
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.core.config import Settings

ADMIN_ID = UUID(int=8_100)
REVIEWER_ID = UUID(int=8_101)


def deterministic_settings() -> Settings:
    return Settings(
        deterministic_admin_subject_id=ADMIN_ID,
        deterministic_admin_token=SecretStr("configured-admin-token"),
        deterministic_reviewer_subject_id=REVIEWER_ID,
        deterministic_reviewer_token=SecretStr("configured-reviewer-token"),
    )


def test_deterministic_identity_maps_configured_tokens_to_roles() -> None:
    provider = build_identity_provider(deterministic_settings())
    assert isinstance(provider, DeterministicIdentityProvider)

    admin = asyncio.run(provider.authenticate("configured-admin-token"))
    reviewer = asyncio.run(provider.authenticate("configured-reviewer-token"))

    assert admin.subject_id == ADMIN_ID
    assert admin.roles == frozenset({AdminRole.ADMIN})
    assert reviewer.subject_id == REVIEWER_ID
    assert reviewer.roles == frozenset({AdminRole.REVIEWER})


def test_deterministic_identity_rejects_unknown_token() -> None:
    provider = build_identity_provider(deterministic_settings())

    with pytest.raises(AuthenticationError) as raised:
        asyncio.run(provider.authenticate("wrong-token"))

    assert raised.value.code is AuthenticationFailureCode.INVALID


def test_identity_provider_is_deny_all_without_configured_tokens() -> None:
    provider = build_identity_provider(Settings())

    with pytest.raises(AuthenticationError) as raised:
        asyncio.run(provider.authenticate("anything"))

    assert raised.value.code is AuthenticationFailureCode.UNAVAILABLE


@pytest.mark.parametrize(
    ("admin_token", "reviewer_token", "message"),
    [
        ("short", None, "at least 16 characters"),
        ("same-configured-token", "same-configured-token", "must be distinct"),
    ],
)
def test_deterministic_identity_token_configuration_is_validated(
    admin_token: str,
    reviewer_token: str | None,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            deterministic_admin_token=SecretStr(admin_token),
            deterministic_reviewer_token=(
                SecretStr(reviewer_token) if reviewer_token is not None else None
            ),
        )


def test_production_rejects_deterministic_identity_tokens() -> None:
    with pytest.raises(ValidationError, match="deterministic identity"):
        Settings(
            environment="production",
            database_url=SecretStr(
                "postgresql+asyncpg://service:database-secret@db/app?ssl=require"
            ),
            valkey_url=SecretStr("rediss://:cache-secret@valkey:6379/0"),
            object_storage_access_key=SecretStr("storage-access"),
            object_storage_secret_key=SecretStr("storage-secret"),
            object_storage_endpoint_url="https://storage.internal",
            deterministic_admin_token=SecretStr("forbidden"),
        )
