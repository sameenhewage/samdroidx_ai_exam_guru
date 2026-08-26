from collections.abc import Mapping
from typing import Any, cast

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import (
    MAX_OIDC_AUDIENCE_LENGTH,
    MAX_OIDC_CLAIM_NAME_LENGTH,
    MAX_OIDC_JWKS_CACHE_SECONDS,
    MAX_OIDC_JWKS_CACHED_KEYS,
    MAX_OIDC_JWKS_TIMEOUT_SECONDS,
    MAX_OIDC_ROLE_VALUE_LENGTH,
    MAX_OIDC_TOKEN_AGE_SECONDS,
    MAX_OIDC_URL_LENGTH,
    Settings,
)


def oidc_config(**overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "identity_provider": "oidc",
        "oidc_issuer": "https://identity.internal.example/realms/exam-guru",
        "oidc_audience": "exam-guru-api",
        "oidc_jwks_url": (
            "https://identity.internal.example/realms/exam-guru/protocol/openid-connect/certs"
        ),
        "oidc_role_claim_name": "roles",
        "oidc_admin_role": "exam-guru-admin",
        "oidc_reviewer_role": "exam-guru-reviewer",
        "oidc_max_token_age_seconds": 3_600,
        "oidc_clock_skew_seconds": 30,
        "oidc_jwks_timeout_seconds": 2.0,
        "oidc_jwks_cache_seconds": 300,
        "oidc_jwks_max_cached_keys": 16,
    }
    values.update(overrides)
    return values


def production_config(**overrides: object) -> dict[str, Any]:
    values = oidc_config(
        environment="production",
        database_url=SecretStr("postgresql+asyncpg://service:database-secret@db/app?ssl=require"),
        object_storage_access_key=SecretStr("storage-access"),
        object_storage_secret_key=SecretStr("storage-secret"),
        object_storage_endpoint_url="https://storage.internal",
        valkey_url=SecretStr("rediss://:cache-secret@valkey:6379/0"),
    )
    values.update(overrides)
    return values


def test_local_identity_mode_defaults_to_deterministic_for_existing_development_config() -> None:
    settings = Settings(
        deterministic_admin_token=SecretStr("configured-admin-token"),
        deterministic_reviewer_token=SecretStr("configured-reviewer-token"),
    )

    assert settings.identity_provider == "deterministic"


@pytest.mark.parametrize("identity_provider", ["OIDC", "oidc ", "auto", ""])
def test_identity_provider_mode_is_an_exact_closed_value(identity_provider: str) -> None:
    with pytest.raises(ValidationError):
        Settings(identity_provider=cast(Any, identity_provider))


def test_oidc_mode_requires_every_explicit_provider_control() -> None:
    complete = oidc_config()
    required_names = tuple(name for name in complete if name != "identity_provider")

    for missing_name in required_names:
        partial = complete.copy()
        del partial[missing_name]
        with pytest.raises(ValidationError, match="complete explicit OIDC configuration"):
            Settings(**partial)


def test_partial_oidc_configuration_is_rejected_even_when_provider_is_not_oidc() -> None:
    for identity_provider in ("deterministic", "deny"):
        with pytest.raises(ValidationError, match="complete explicit OIDC configuration"):
            Settings(
                identity_provider=identity_provider,
                oidc_issuer="https://identity.internal.example/issuer",
            )


def test_complete_oidc_configuration_may_be_staged_while_identity_fails_closed() -> None:
    settings = Settings(**oidc_config(identity_provider="deny"))

    assert settings.identity_provider == "deny"
    assert settings.oidc_issuer == "https://identity.internal.example/realms/exam-guru"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("oidc_issuer", " https://identity.internal.example/issuer"),
        ("oidc_audience", "exam guru api"),
        ("oidc_role_claim_name", "roles\x00admin"),
        ("oidc_admin_role", "exam-guru-admin\n"),
        ("oidc_reviewer_role", " exam-guru-reviewer"),
        ("oidc_issuer", "https://identity.internal.example/" + "a" * MAX_OIDC_URL_LENGTH),
        ("oidc_audience", "a" * (MAX_OIDC_AUDIENCE_LENGTH + 1)),
        ("oidc_role_claim_name", "r" * (MAX_OIDC_CLAIM_NAME_LENGTH + 1)),
        ("oidc_admin_role", "r" * (MAX_OIDC_ROLE_VALUE_LENGTH + 1)),
    ],
)
def test_oidc_text_controls_reject_whitespace_control_and_oversize(
    name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        Settings(**oidc_config(**{name: value}))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("oidc_issuer", "identity.internal.example/issuer"),
        ("oidc_jwks_url", "/.well-known/jwks.json"),
        ("oidc_issuer", "ftp://identity.internal.example/issuer"),
        ("oidc_jwks_url", "https://user@identity.internal.example/jwks"),
        ("oidc_issuer", "https://identity.internal.example/issuer?tenant=secret"),
        ("oidc_jwks_url", "https://identity.internal.example/jwks#key"),
        ("oidc_jwks_url", "https://identity.internal.example:invalid/jwks"),
    ],
)
def test_oidc_urls_are_absolute_http_urls_without_ambiguous_components(
    name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="OIDC URL"):
        Settings(**oidc_config(**{name: value}))


def test_jwks_must_share_issuer_origin_by_default() -> None:
    with pytest.raises(ValidationError, match="JWKS origin"):
        Settings(**oidc_config(oidc_jwks_url="https://keys.internal.example/exam-guru/jwks"))


def test_explicit_trusted_jwks_origin_allows_only_that_cross_origin() -> None:
    settings = Settings(
        **oidc_config(
            oidc_jwks_url="https://keys.internal.example/exam-guru/jwks",
            oidc_trusted_jwks_origin="https://keys.internal.example",
        )
    )
    assert settings.oidc_trusted_jwks_origin == "https://keys.internal.example"

    with pytest.raises(ValidationError, match="trusted JWKS origin"):
        Settings(
            **oidc_config(
                oidc_jwks_url="https://keys.internal.example/exam-guru/jwks",
                oidc_trusted_jwks_origin="https://other.internal.example",
            )
        )


@pytest.mark.parametrize(
    "trusted_origin",
    [
        "https://keys.internal.example/path",
        "https://keys.internal.example?tenant=x",
        "https://user@keys.internal.example",
        "keys.internal.example",
    ],
)
def test_trusted_jwks_origin_is_an_exact_origin(trusted_origin: str) -> None:
    with pytest.raises(ValidationError, match="trusted JWKS origin"):
        Settings(
            **oidc_config(
                oidc_jwks_url="https://keys.internal.example/jwks",
                oidc_trusted_jwks_origin=trusted_origin,
            )
        )


def test_trusted_jwks_origin_cannot_conflict_with_same_origin_jwks() -> None:
    with pytest.raises(ValidationError, match="trusted JWKS origin"):
        Settings(
            **oidc_config(
                oidc_trusted_jwks_origin="https://keys.internal.example",
            )
        )


def test_non_deterministic_mode_rejects_local_deterministic_tokens() -> None:
    with pytest.raises(ValidationError, match="require identity_provider=deterministic"):
        Settings(
            identity_provider="deny",
            deterministic_admin_token=SecretStr("configured-admin-token"),
        )


def test_production_requires_oidc_and_rejects_deterministic_tokens() -> None:
    with pytest.raises(ValidationError, match="production identity provider must be OIDC"):
        Settings(**production_config(identity_provider="deny"))

    with pytest.raises(ValidationError, match="deterministic identity"):
        Settings(
            **production_config(
                deterministic_admin_token=SecretStr("forbidden-admin-token"),
            )
        )


def test_production_oidc_requires_https_but_accepts_intentional_private_hosts() -> None:
    with pytest.raises(ValidationError, match="production OIDC URLs must use HTTPS"):
        Settings(
            **production_config(
                oidc_issuer="http://10.0.0.8:8443/issuer",
                oidc_jwks_url="http://10.0.0.8:8443/issuer/jwks",
            )
        )
    with pytest.raises(ValidationError, match="production OIDC URLs must use HTTPS"):
        Settings(
            **production_config(
                oidc_issuer="https://identity.internal.example/issuer",
                oidc_jwks_url="http://keys.internal.example/jwks",
                oidc_trusted_jwks_origin="http://keys.internal.example",
            )
        )

    settings = Settings(
        **production_config(
            oidc_issuer="https://127.0.0.1:8443/issuer",
            oidc_jwks_url="https://127.0.0.1:8443/issuer/jwks",
        )
    )
    assert settings.oidc_issuer == "https://127.0.0.1:8443/issuer"


@pytest.mark.parametrize(
    "overrides",
    [
        {"oidc_max_token_age_seconds": 0},
        {"oidc_max_token_age_seconds": MAX_OIDC_TOKEN_AGE_SECONDS + 1},
        {"oidc_clock_skew_seconds": -1},
        {"oidc_jwks_timeout_seconds": 0},
        {"oidc_jwks_timeout_seconds": MAX_OIDC_JWKS_TIMEOUT_SECONDS + 0.1},
        {"oidc_jwks_cache_seconds": 0},
        {"oidc_jwks_cache_seconds": MAX_OIDC_JWKS_CACHE_SECONDS + 1},
        {"oidc_jwks_max_cached_keys": 0},
        {"oidc_jwks_max_cached_keys": MAX_OIDC_JWKS_CACHED_KEYS + 1},
    ],
)
def test_oidc_time_network_and_cache_controls_are_bounded(
    overrides: Mapping[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**oidc_config(**overrides))


def test_oidc_role_values_and_algorithms_must_be_distinct() -> None:
    with pytest.raises(ValidationError, match="OIDC role values must be distinct"):
        Settings(**oidc_config(oidc_reviewer_role="exam-guru-admin"))

    with pytest.raises(ValidationError, match="OIDC algorithms must be distinct"):
        Settings(**oidc_config(oidc_algorithms=("RS256", "RS256")))


@pytest.mark.parametrize(
    "algorithms",
    [(), ("HS256",), ("none",), ("RS512",)],
)
def test_oidc_algorithm_configuration_is_a_nonempty_subset_of_rs256_es256(
    algorithms: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**oidc_config(oidc_algorithms=algorithms))
