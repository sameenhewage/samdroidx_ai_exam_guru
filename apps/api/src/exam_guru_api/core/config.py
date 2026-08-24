from typing import Literal, Self, cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LOCAL_DATABASE_URL = "postgresql+asyncpg://exam_guru@localhost:5432/exam_guru"
LOCAL_VALKEY_URL = "redis://localhost:6379/0"
LOCAL_STORAGE_ACCESS_KEY = "exam-guru-local"
LOCAL_STORAGE_SECRET_KEY = ""


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
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0, le=100 * 1024 * 1024)
    retrieval_embedding_provider: Literal["deterministic"] | None = None
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
    deterministic_admin_token: SecretStr | None = None
    deterministic_admin_subject_id: UUID = UUID("00000000-0000-0000-0000-000000000101")
    deterministic_reviewer_token: SecretStr | None = None
    deterministic_reviewer_subject_id: UUID = UUID("00000000-0000-0000-0000-000000000102")

    @model_validator(mode="after")
    def reject_local_credentials_in_production(self) -> Self:
        deterministic_tokens = [
            token.get_secret_value()
            for token in (self.deterministic_admin_token, self.deterministic_reviewer_token)
            if token is not None
        ]
        if self.environment == "production" and deterministic_tokens:
            raise ValueError("production configuration cannot use deterministic identity tokens")
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
        return self
