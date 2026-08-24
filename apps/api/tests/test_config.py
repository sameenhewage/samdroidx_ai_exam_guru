import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import (
    EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS,
    EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS,
    OCR_PROVIDER_MAX_EXECUTION_SECONDS,
    TESSERACT_PROBE_COMMAND_COUNT,
    Settings,
)


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


def test_ocr_is_explicitly_disabled_by_default_and_controls_are_bounded() -> None:
    settings = Settings(environment="test")

    assert settings.ocr_provider is None
    assert settings.ocr_tesseract_executable == "tesseract"
    assert settings.ocr_tesseract_language == "sin+eng"
    assert 72 <= settings.ocr_tesseract_dpi <= 600
    assert 1 <= settings.ocr_tesseract_batch_size <= settings.ocr_tesseract_max_pages <= 1_000
    assert 0 < settings.ocr_tesseract_timeout_seconds <= 300
    assert 1 <= settings.ocr_tesseract_page_segmentation_mode <= 13
    assert 0 < settings.ocr_tesseract_max_pixels_per_page <= 100_000_000
    assert 0 < settings.ocr_tesseract_max_command_output_bytes <= 64 * 1024 * 1024
    assert settings.ocr_tesseract_max_pages == 16
    assert settings.ocr_tesseract_timeout_seconds == 10.0


def test_tesseract_budget_fits_inside_actor_after_native_storage_headroom() -> None:
    assert EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS == 5 * 60
    assert EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS > 0
    assert OCR_PROVIDER_MAX_EXECUTION_SECONDS <= (
        EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS - EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS
    )
    configured = Settings(environment="test", ocr_provider="tesseract")
    worst_case_seconds = (
        configured.ocr_tesseract_max_pages + TESSERACT_PROBE_COMMAND_COUNT
    ) * configured.ocr_tesseract_timeout_seconds
    assert worst_case_seconds <= OCR_PROVIDER_MAX_EXECUTION_SECONDS


def test_tesseract_budget_accepts_exact_boundary_and_rejects_one_over() -> None:
    max_pages = 16
    exact_timeout = OCR_PROVIDER_MAX_EXECUTION_SECONDS / (max_pages + TESSERACT_PROBE_COMMAND_COUNT)

    exact = Settings(
        environment="test",
        ocr_provider="tesseract",
        ocr_tesseract_max_pages=max_pages,
        ocr_tesseract_timeout_seconds=exact_timeout,
    )
    assert exact.ocr_tesseract_timeout_seconds == exact_timeout

    with pytest.raises(ValidationError, match="OCR execution budget"):
        Settings(
            environment="test",
            ocr_provider="tesseract",
            ocr_tesseract_max_pages=max_pages,
            ocr_tesseract_timeout_seconds=exact_timeout + 0.001,
        )


def test_unconfigured_ocr_does_not_apply_provider_execution_budget() -> None:
    settings = Settings(
        environment="test",
        ocr_provider=None,
        ocr_tesseract_max_pages=1_000,
        ocr_tesseract_batch_size=16,
        ocr_tesseract_timeout_seconds=300,
    )

    assert settings.ocr_provider is None
    assert settings.ocr_tesseract_max_pages == 1_000
    assert settings.ocr_tesseract_timeout_seconds == 300


@pytest.mark.parametrize(
    "overrides",
    [
        {"ocr_provider": "unsupported"},
        {"ocr_tesseract_executable": " tesseract"},
        {"ocr_tesseract_executable": "tesseract\x00other"},
        {"ocr_tesseract_language": "sin;cat /run/secrets/key"},
        {"ocr_tesseract_language": "sin+sin"},
        {"ocr_tesseract_dpi": 601},
        {"ocr_tesseract_max_pages": 0},
        {"ocr_tesseract_max_pages": 1, "ocr_tesseract_batch_size": 2},
        {"ocr_tesseract_timeout_seconds": 301},
        {"ocr_tesseract_page_segmentation_mode": 14},
        {"ocr_tesseract_max_pixels_per_page": 100_000_001},
        {"ocr_tesseract_max_command_output_bytes": (64 * 1024 * 1024) + 1},
        {
            "ocr_provider": "tesseract",
            "ocr_tesseract_max_source_bytes": 1_000,
            "max_upload_bytes": 2_000,
        },
    ],
)
def test_ocr_settings_reject_unsafe_or_unbounded_controls(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **overrides)  # type: ignore[arg-type]
