import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import Settings


def test_settings_redact_connection_credentials() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://user:database-secret@db/app"),
        object_storage_access_key=SecretStr("storage-access-secret"),
        object_storage_secret_key=SecretStr("storage-secret"),
        valkey_url=SecretStr("redis://:cache-secret@valkey:6379/0"),
    )

    rendered = repr(settings)

    assert "database-secret" not in rendered
    assert "storage-access-secret" not in rendered
    assert "storage-secret" not in rendered
    assert "cache-secret" not in rendered


def test_production_rejects_local_development_credentials() -> None:
    with pytest.raises(ValidationError, match="production configuration"):
        Settings(environment="production")


@pytest.mark.parametrize(
    ("database_url", "valkey_url", "object_storage_endpoint_url"),
    [
        (
            "postgresql+asyncpg://service:database-secret@db/app",
            "rediss://:cache-secret@valkey:6379/0",
            "https://storage.internal",
        ),
        (
            "postgresql+asyncpg://service:database-secret@db/app?ssl=require",
            "redis://:cache-secret@valkey:6379/0",
            "https://storage.internal",
        ),
        (
            "postgresql+asyncpg://service:database-secret@db/app?ssl=require",
            "rediss://:cache-secret@valkey:6379/0",
            "http://storage.internal",
        ),
    ],
)
def test_production_requires_encrypted_service_connections(
    database_url: str,
    valkey_url: str,
    object_storage_endpoint_url: str,
) -> None:
    with pytest.raises(ValidationError, match="encrypted service connections"):
        Settings(
            environment="production",
            database_url=SecretStr(database_url),
            object_storage_access_key=SecretStr("storage-access"),
            object_storage_secret_key=SecretStr("storage-secret"),
            object_storage_endpoint_url=object_storage_endpoint_url,
            valkey_url=SecretStr(valkey_url),
        )


def test_production_accepts_explicit_service_credentials() -> None:
    settings = Settings(
        environment="production",
        database_url=SecretStr("postgresql+asyncpg://service:database-secret@db/app?ssl=require"),
        object_storage_access_key=SecretStr("storage-access"),
        object_storage_secret_key=SecretStr("storage-secret"),
        object_storage_endpoint_url="https://storage.internal",
        valkey_url=SecretStr("rediss://:cache-secret@valkey:6379/0"),
    )

    assert settings.environment == "production"
