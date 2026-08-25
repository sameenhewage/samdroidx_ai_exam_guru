import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.core.config import (
    EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS,
    EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS,
    MAX_RATE_LIMIT_PER_WINDOW,
    MAX_RATE_LIMIT_WINDOW_SECONDS,
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
    settings = Settings.model_validate(
        {
            "environment": "production",
            "database_url": SecretStr(
                "postgresql+asyncpg://service:database-secret@db/app?ssl=require"
            ),
            "object_storage_access_key": SecretStr("storage-access"),
            "object_storage_secret_key": SecretStr("storage-secret"),
            "object_storage_endpoint_url": "https://storage.internal",
            "valkey_url": SecretStr("rediss://:cache-secret@valkey:6379/0"),
            **production_oidc_config(),
        }
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


def test_authenticated_cost_controls_are_enabled_with_bounded_scope_defaults() -> None:
    settings = Settings(environment="test")

    assert settings.rate_limits_enabled is True
    assert 1 <= settings.rate_limit_window_seconds <= MAX_RATE_LIMIT_WINDOW_SECONDS
    limits = (
        settings.rate_limit_source_upload,
        settings.rate_limit_extraction_trigger,
        settings.rate_limit_embedding_job_create,
        settings.rate_limit_generation_create_retry,
        settings.rate_limit_validation_run,
        settings.rate_limit_paper_publish_archive,
    )
    assert all(1 <= limit <= MAX_RATE_LIMIT_PER_WINDOW for limit in limits)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("rate_limit_window_seconds", 0),
        ("rate_limit_window_seconds", MAX_RATE_LIMIT_WINDOW_SECONDS + 1),
        ("rate_limit_source_upload", 0),
        ("rate_limit_extraction_trigger", MAX_RATE_LIMIT_PER_WINDOW + 1),
        ("rate_limit_embedding_job_create", 0),
        ("rate_limit_generation_create_retry", MAX_RATE_LIMIT_PER_WINDOW + 1),
        ("rate_limit_validation_run", 0),
        ("rate_limit_paper_publish_archive", MAX_RATE_LIMIT_PER_WINDOW + 1),
    ],
)
def test_authenticated_cost_control_configuration_rejects_unbounded_values(
    name: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **{name: value})  # type: ignore[arg-type]


def test_production_cannot_disable_authenticated_cost_controls() -> None:
    with pytest.raises(ValidationError, match="production cost controls cannot be disabled"):
        Settings(
            environment="production",
            database_url=SecretStr(
                "postgresql+asyncpg://service:database-secret@db/app?ssl=require"
            ),
            object_storage_access_key=SecretStr("storage-access"),
            object_storage_secret_key=SecretStr("storage-secret"),
            object_storage_endpoint_url="https://storage.internal",
            valkey_url=SecretStr("rediss://:cache-secret@valkey:6379/0"),
            rate_limits_enabled=False,
        )


def test_test_runtime_can_explicitly_disable_cost_controls_for_isolated_injection() -> None:
    settings = Settings(environment="test", rate_limits_enabled=False)

    assert settings.rate_limits_enabled is False


def production_oidc_config() -> dict[str, object]:
    return {
        "identity_provider": "oidc",
        "oidc_issuer": "https://identity.internal.example/issuer",
        "oidc_audience": "exam-guru-api",
        "oidc_jwks_url": "https://identity.internal.example/issuer/jwks",
        "oidc_role_claim_name": "roles",
        "oidc_admin_role": "exam-guru-admin",
        "oidc_reviewer_role": "exam-guru-reviewer",
        "oidc_max_token_age_seconds": 3_600,
        "oidc_clock_skew_seconds": 30,
        "oidc_jwks_timeout_seconds": 2.0,
        "oidc_jwks_cache_seconds": 300,
        "oidc_jwks_max_cached_keys": 16,
    }
