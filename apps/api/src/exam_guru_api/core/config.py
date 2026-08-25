from typing import Literal, Self, cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
IdentityProviderMode = Literal["deterministic", "oidc", "deny"]
OIDCAlgorithm = Literal["RS256", "ES256"]
LOCAL_DATABASE_URL = "postgresql+asyncpg://exam_guru@localhost:5432/exam_guru"
LOCAL_VALKEY_URL = "redis://localhost:6379/0"
LOCAL_STORAGE_ACCESS_KEY = "exam-guru-local"
LOCAL_STORAGE_SECRET_KEY = ""
EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS = 5 * 60
EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS = 60
OCR_PROVIDER_MAX_EXECUTION_SECONDS = (
    EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS - EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS
)
TESSERACT_PROBE_COMMAND_COUNT = 2
GENERATION_ACTOR_MAX_EXECUTION_SECONDS = 5 * 60
EMBEDDING_ACTOR_MAX_EXECUTION_SECONDS = 5 * 60
STORAGE_RECONCILIATION_ACTOR_MAX_EXECUTION_SECONDS = 5 * 60
MIN_EMBEDDING_WORKER_LEASE_SECONDS = EMBEDDING_ACTOR_MAX_EXECUTION_SECONDS + 1
GENERATION_PROVIDER_MAX_EXECUTION_SECONDS = 3 * 120 + 2 * 2
MIN_GENERATION_WORKER_LEASE_SECONDS = (
    max(GENERATION_ACTOR_MAX_EXECUTION_SECONDS, GENERATION_PROVIDER_MAX_EXECUTION_SECONDS) + 1
)
MAX_RATE_LIMIT_WINDOW_SECONDS = 3_600
MAX_RATE_LIMIT_PER_WINDOW = 10_000
MAX_OIDC_URL_LENGTH = 2_048
MAX_OIDC_AUDIENCE_LENGTH = 256
MAX_OIDC_CLAIM_NAME_LENGTH = 128
MAX_OIDC_ROLE_VALUE_LENGTH = 256
MAX_OIDC_TOKEN_AGE_SECONDS = 86_400
MAX_OIDC_CLOCK_SKEW_SECONDS = 300
MAX_OIDC_JWKS_TIMEOUT_SECONDS = 10.0
MAX_OIDC_JWKS_CACHE_SECONDS = 86_400
MAX_OIDC_JWKS_CACHED_KEYS = 128


def _parse_oidc_url(value: str, *, label: str = "OIDC URL") -> tuple[str, str, int]:
    if (
        not value
        or len(value) > MAX_OIDC_URL_LENGTH
        or value != value.strip()
        or not value.isprintable()
        or any(character.isspace() for character in value)
        or "\\" in value
    ):
        raise ValueError(f"{label} must be bounded control-free text")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except ValueError as error:
        raise ValueError(f"{label} must be a valid absolute HTTP URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or username is not None
        or password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be absolute HTTP(S) without userinfo, query, or fragment")
    normalized_port = port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower(), normalized_port


def _validate_oidc_token(value: str, *, maximum_length: int, label: str) -> None:
    if (
        not value
        or len(value) > maximum_length
        or value != value.strip()
        or not value.isprintable()
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{label} must be a bounded printable token")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EXAM_GURU_",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "local"
    database_url: SecretStr = SecretStr(LOCAL_DATABASE_URL)
    valkey_url: SecretStr = SecretStr(LOCAL_VALKEY_URL)
    object_storage_endpoint_url: str = "http://localhost:9000"
    object_storage_access_key: SecretStr = SecretStr(LOCAL_STORAGE_ACCESS_KEY)
    object_storage_secret_key: SecretStr = SecretStr(LOCAL_STORAGE_SECRET_KEY)
    object_storage_bucket: str = "exam-guru-sources"
    object_storage_region: str = "us-east-1"
    sentry_dsn: SecretStr | None = None
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "exam-guru-api"
    trace_sample_ratio: float = Field(default=0.1, ge=0, le=1)
    readiness_timeout_seconds: float = Field(default=5, gt=0, le=30)
    rate_limits_enabled: bool = True
    rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        le=MAX_RATE_LIMIT_WINDOW_SECONDS,
    )
    rate_limit_source_upload: int = Field(default=30, ge=1, le=MAX_RATE_LIMIT_PER_WINDOW)
    rate_limit_extraction_trigger: int = Field(default=60, ge=1, le=MAX_RATE_LIMIT_PER_WINDOW)
    rate_limit_embedding_job_create: int = Field(default=30, ge=1, le=MAX_RATE_LIMIT_PER_WINDOW)
    rate_limit_generation_create_retry: int = Field(
        default=20,
        ge=1,
        le=MAX_RATE_LIMIT_PER_WINDOW,
    )
    rate_limit_validation_run: int = Field(default=60, ge=1, le=MAX_RATE_LIMIT_PER_WINDOW)
    rate_limit_paper_publish_archive: int = Field(
        default=30,
        ge=1,
        le=MAX_RATE_LIMIT_PER_WINDOW,
    )
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0, le=100 * 1024 * 1024)
    extraction_recovery_batch_size: int = Field(default=50, ge=1, le=100)
    extraction_outbox_min_age_seconds: int = Field(default=5, ge=1, le=3_600)
    maintenance_scheduler_interval_seconds: int = Field(default=30, ge=5, le=3_600)
    storage_reconciliation_interval_seconds: int = Field(
        default=3_600,
        ge=300,
        le=31_536_000,
    )
    storage_reconciliation_grace_seconds: int = Field(
        default=86_400,
        ge=3_600,
        le=31_536_000,
    )
    storage_reconciliation_max_objects_per_run: int = Field(
        default=1_000,
        ge=1,
        le=10_000,
    )
    storage_reconciliation_apply_tags: bool = False
    ocr_provider: Literal["tesseract"] | None = None
    ocr_tesseract_executable: str = Field(default="tesseract", min_length=1, max_length=255)
    ocr_tesseract_language: str = Field(default="sin+eng", min_length=1, max_length=64)
    ocr_tesseract_max_source_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
        le=100 * 1024 * 1024,
    )
    ocr_tesseract_max_pages: int = Field(default=16, ge=1, le=1_000)
    ocr_tesseract_dpi: int = Field(default=300, ge=72, le=600)
    ocr_tesseract_batch_size: int = Field(default=4, ge=1, le=16)
    ocr_tesseract_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    ocr_tesseract_page_segmentation_mode: int = Field(default=3, ge=1, le=13)
    ocr_tesseract_max_pixels_per_page: int = Field(
        default=40_000_000,
        ge=1,
        le=100_000_000,
    )
    ocr_tesseract_max_command_output_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1,
        le=64 * 1024 * 1024,
    )
    retrieval_embedding_provider: Literal["deterministic"] | None = None
    retrieval_embedding_model: str = Field(
        default="grade5-deterministic-shake256",
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    retrieval_embedding_dimension: int = Field(default=32, ge=1, le=4_096)
    retrieval_embedding_version: str = Field(
        default="v1",
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
    )
    retrieval_embedding_config_fingerprint: str = Field(
        default="sha256:51c1987251b1c8d373ecb6d476c2d00ae60da5173aa544a2ff4524a9141d3d89",
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    embedding_recovery_batch_size: int = Field(default=50, ge=1, le=100)
    embedding_outbox_min_age_seconds: int = Field(default=5, ge=1, le=3_600)
    embedding_worker_lease_seconds: int = Field(
        default=600,
        ge=MIN_EMBEDDING_WORKER_LEASE_SECONDS,
        le=86_400,
    )
    generation_provider: Literal["deterministic", "openai"] | None = None
    generation_openai_api_key: SecretStr | None = None
    generation_model: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    generation_model_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    generation_pricing_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^\S+$",
    )
    generation_input_microusd_per_million_tokens: int | None = Field(
        default=None,
        ge=0,
        le=100_000_000_000,
    )
    generation_output_microusd_per_million_tokens: int | None = Field(
        default=None,
        ge=0,
        le=100_000_000_000,
    )
    generation_timeout_ms: int | None = Field(default=None, ge=1, le=120_000)
    generation_recovery_batch_size: int = Field(default=50, ge=1, le=100)
    generation_outbox_min_age_seconds: int = Field(default=5, ge=1, le=3_600)
    generation_worker_lease_seconds: int = Field(
        default=600,
        ge=MIN_GENERATION_WORKER_LEASE_SECONDS,
        le=86_400,
    )
    identity_provider: IdentityProviderMode = "deterministic"
    oidc_issuer: str | None = Field(default=None, min_length=1, max_length=MAX_OIDC_URL_LENGTH)
    oidc_audience: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_OIDC_AUDIENCE_LENGTH,
    )
    oidc_jwks_url: str | None = Field(default=None, min_length=1, max_length=MAX_OIDC_URL_LENGTH)
    oidc_trusted_jwks_origin: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_OIDC_URL_LENGTH,
    )
    oidc_role_claim_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_OIDC_CLAIM_NAME_LENGTH,
    )
    oidc_admin_role: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_OIDC_ROLE_VALUE_LENGTH,
    )
    oidc_reviewer_role: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_OIDC_ROLE_VALUE_LENGTH,
    )
    oidc_max_token_age_seconds: int | None = Field(
        default=None,
        ge=1,
        le=MAX_OIDC_TOKEN_AGE_SECONDS,
    )
    oidc_clock_skew_seconds: int | None = Field(
        default=None,
        ge=0,
        le=MAX_OIDC_CLOCK_SKEW_SECONDS,
    )
    oidc_jwks_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        le=MAX_OIDC_JWKS_TIMEOUT_SECONDS,
    )
    oidc_jwks_cache_seconds: int | None = Field(
        default=None,
        ge=1,
        le=MAX_OIDC_JWKS_CACHE_SECONDS,
    )
    oidc_jwks_max_cached_keys: int | None = Field(
        default=None,
        ge=1,
        le=MAX_OIDC_JWKS_CACHED_KEYS,
    )
    oidc_algorithms: tuple[OIDCAlgorithm, ...] = Field(
        default=("RS256", "ES256"),
        min_length=1,
        max_length=2,
    )
    deterministic_admin_token: SecretStr | None = None
    deterministic_admin_subject_id: UUID = UUID("00000000-0000-0000-0000-000000000101")
    deterministic_reviewer_token: SecretStr | None = None
    deterministic_reviewer_subject_id: UUID = UUID("00000000-0000-0000-0000-000000000102")

    @model_validator(mode="after")
    def reject_local_credentials_in_production(self) -> Self:
        if (
            self.ocr_tesseract_executable != self.ocr_tesseract_executable.strip()
            or not self.ocr_tesseract_executable.isprintable()
        ):
            raise ValueError("Tesseract executable must be bounded control-free text")
        selected_languages = tuple(self.ocr_tesseract_language.split("+"))
        if (
            not 1 <= len(selected_languages) <= 4
            or len(set(selected_languages)) != len(selected_languages)
            or any(
                not language
                or any(
                    not (character.isascii() and (character.isalnum() or character == "_"))
                    for character in language
                )
                for language in selected_languages
            )
        ):
            raise ValueError("Tesseract language must contain unique safe language codes")
        if self.ocr_tesseract_batch_size > self.ocr_tesseract_max_pages:
            raise ValueError("Tesseract batch size cannot exceed its page limit")
        if self.ocr_provider == "tesseract":
            if self.ocr_tesseract_max_source_bytes < self.max_upload_bytes:
                raise ValueError("configured OCR must accept the configured upload byte limit")
            worst_case_ocr_seconds = (
                self.ocr_tesseract_max_pages + TESSERACT_PROBE_COMMAND_COUNT
            ) * self.ocr_tesseract_timeout_seconds
            if worst_case_ocr_seconds > OCR_PROVIDER_MAX_EXECUTION_SECONDS:
                raise ValueError("configured Tesseract commands exceed the OCR execution budget")

        embedding_identifiers = (
            self.retrieval_embedding_model,
            self.retrieval_embedding_version,
            self.retrieval_embedding_config_fingerprint,
        )
        if any(
            value != value.strip()
            or not value.isprintable()
            or any(character.isspace() for character in value)
            for value in embedding_identifiers
        ):
            raise ValueError("embedding configuration identifiers must be bounded printable tokens")

        oidc_required_values = (
            self.oidc_issuer,
            self.oidc_audience,
            self.oidc_jwks_url,
            self.oidc_role_claim_name,
            self.oidc_admin_role,
            self.oidc_reviewer_role,
            self.oidc_max_token_age_seconds,
            self.oidc_clock_skew_seconds,
            self.oidc_jwks_timeout_seconds,
            self.oidc_jwks_cache_seconds,
            self.oidc_jwks_max_cached_keys,
        )
        oidc_has_any_value = (
            any(value is not None for value in oidc_required_values)
            or self.oidc_trusted_jwks_origin is not None
            or "oidc_algorithms" in self.model_fields_set
        )
        oidc_is_complete = all(value is not None for value in oidc_required_values)
        if (oidc_has_any_value or self.identity_provider == "oidc") and not oidc_is_complete:
            raise ValueError("identity provider requires complete explicit OIDC configuration")
        if len(self.oidc_algorithms) != len(set(self.oidc_algorithms)):
            raise ValueError("OIDC algorithms must be distinct")
        if oidc_is_complete:
            issuer = cast(str, self.oidc_issuer)
            audience = cast(str, self.oidc_audience)
            jwks_url = cast(str, self.oidc_jwks_url)
            role_claim_name = cast(str, self.oidc_role_claim_name)
            admin_role = cast(str, self.oidc_admin_role)
            reviewer_role = cast(str, self.oidc_reviewer_role)
            issuer_origin = _parse_oidc_url(issuer)
            jwks_origin = _parse_oidc_url(jwks_url)
            _validate_oidc_token(
                audience,
                maximum_length=MAX_OIDC_AUDIENCE_LENGTH,
                label="OIDC audience",
            )
            _validate_oidc_token(
                role_claim_name,
                maximum_length=MAX_OIDC_CLAIM_NAME_LENGTH,
                label="OIDC role claim name",
            )
            _validate_oidc_token(
                admin_role,
                maximum_length=MAX_OIDC_ROLE_VALUE_LENGTH,
                label="OIDC admin role",
            )
            _validate_oidc_token(
                reviewer_role,
                maximum_length=MAX_OIDC_ROLE_VALUE_LENGTH,
                label="OIDC reviewer role",
            )
            if admin_role == reviewer_role:
                raise ValueError("OIDC role values must be distinct")
            trusted_origin = None
            if self.oidc_trusted_jwks_origin is not None:
                trusted_url = urlsplit(self.oidc_trusted_jwks_origin)
                trusted_origin = _parse_oidc_url(
                    self.oidc_trusted_jwks_origin,
                    label="trusted JWKS origin",
                )
                if trusted_url.path not in {"", "/"}:
                    raise ValueError("trusted JWKS origin must contain only scheme and authority")
            if issuer_origin != jwks_origin:
                if trusted_origin is None:
                    raise ValueError("OIDC JWKS origin must match the issuer origin")
                if trusted_origin != jwks_origin:
                    raise ValueError("configured trusted JWKS origin must match the JWKS URL")
            elif trusted_origin is not None and trusted_origin != jwks_origin:
                raise ValueError("configured trusted JWKS origin must match the JWKS URL")
            if self.environment == "production" and (
                issuer_origin[0] != "https" or jwks_origin[0] != "https"
            ):
                raise ValueError("production OIDC URLs must use HTTPS")

        deterministic_tokens = [
            token.get_secret_value()
            for token in (self.deterministic_admin_token, self.deterministic_reviewer_token)
            if token is not None
        ]
        if self.environment == "production" and deterministic_tokens:
            raise ValueError("production configuration cannot use deterministic identity tokens")
        if deterministic_tokens and self.identity_provider != "deterministic":
            raise ValueError(
                "deterministic identity tokens require identity_provider=deterministic"
            )
        if self.environment == "production" and not self.rate_limits_enabled:
            raise ValueError("production cost controls cannot be disabled")
        if (
            self.environment in {"staging", "production"}
            and self.retrieval_embedding_provider == "deterministic"
        ):
            raise ValueError("staging and production cannot use deterministic retrieval embeddings")
        if (
            self.environment in {"staging", "production"}
            and self.generation_provider == "deterministic"
        ):
            raise ValueError("staging and production cannot use deterministic generation")
        openai_generation_values = (
            self.generation_openai_api_key,
            self.generation_model,
            self.generation_model_version,
            self.generation_pricing_version,
            self.generation_input_microusd_per_million_tokens,
            self.generation_output_microusd_per_million_tokens,
            self.generation_timeout_ms,
        )
        if self.generation_provider == "openai":
            if self.environment == "test":
                raise ValueError(
                    "test configuration cannot use the paid OpenAI generation provider"
                )
            if any(value is None for value in openai_generation_values):
                raise ValueError(
                    "OpenAI generation requires explicit model, pricing, key, and timeout"
                )
            provider_key = cast(SecretStr, self.generation_openai_api_key).get_secret_value()
            if (
                not provider_key
                or provider_key != provider_key.strip()
                or len(provider_key) > 4_096
                or any(
                    character.isspace() or not character.isprintable() for character in provider_key
                )
            ):
                raise ValueError("OpenAI generation API key must be bounded secret text")
        elif any(value is not None for value in openai_generation_values):
            raise ValueError("OpenAI generation settings require generation_provider=openai")
        if any(len(token) < 16 for token in deterministic_tokens):
            raise ValueError("deterministic identity tokens must contain at least 16 characters")
        if len(deterministic_tokens) != len(set(deterministic_tokens)):
            raise ValueError("deterministic identity tokens must be distinct")

        local_credentials = (
            self.database_url.get_secret_value() == LOCAL_DATABASE_URL
            or self.valkey_url.get_secret_value() == LOCAL_VALKEY_URL
            or self.object_storage_access_key.get_secret_value() == LOCAL_STORAGE_ACCESS_KEY
            or self.object_storage_secret_key.get_secret_value() == LOCAL_STORAGE_SECRET_KEY
        )
        if self.environment == "production" and local_credentials:
            raise ValueError("production configuration must replace local development credentials")
        if self.environment == "production":
            database_query = parse_qs(urlsplit(self.database_url.get_secret_value()).query)
            database_ssl = database_query.get("ssl", database_query.get("sslmode", []))
            encrypted_connections = (
                any(
                    value in {"require", "true", "verify-ca", "verify-full"}
                    for value in database_ssl
                )
                and urlsplit(self.valkey_url.get_secret_value()).scheme == "rediss"
                and urlsplit(self.object_storage_endpoint_url).scheme == "https"
            )
            if not encrypted_connections:
                raise ValueError("production configuration requires encrypted service connections")
            if self.identity_provider != "oidc":
                raise ValueError("production identity provider must be OIDC")
        return self
