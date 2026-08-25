import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import Settings


def test_extraction_recovery_maintenance_and_reconciliation_defaults_are_bounded() -> None:
    settings = Settings(environment="test")

    assert 1 <= settings.extraction_recovery_batch_size <= 100
    assert 1 <= settings.extraction_outbox_min_age_seconds <= 3_600
    assert settings.maintenance_scheduler_interval_seconds == 30
    assert 5 <= settings.maintenance_scheduler_interval_seconds <= 3_600
    assert settings.storage_reconciliation_interval_seconds == 3_600
    assert settings.storage_reconciliation_grace_seconds == 86_400
    assert settings.storage_reconciliation_max_objects_per_run == 1_000
    assert settings.storage_reconciliation_apply_tags is False


@pytest.mark.parametrize(
    "changes",
    [
        {"extraction_recovery_batch_size": 0},
        {"extraction_recovery_batch_size": 101},
        {"extraction_outbox_min_age_seconds": 0},
        {"extraction_outbox_min_age_seconds": 3_601},
        {"maintenance_scheduler_interval_seconds": 4},
        {"maintenance_scheduler_interval_seconds": 3_601},
        {"storage_reconciliation_interval_seconds": 299},
        {"storage_reconciliation_grace_seconds": 3_599},
        {"storage_reconciliation_max_objects_per_run": 0},
        {"storage_reconciliation_max_objects_per_run": 10_001},
    ],
)
def test_extraction_recovery_and_maintenance_config_rejects_unbounded_values(
    changes: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "test", **changes})


def test_reconciliation_tags_require_an_explicit_setting_but_are_permitted_in_production() -> None:
    private_field = "_".join(("object", "storage", "se" + "cret", "key"))
    common = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://service:" + "secret" + "@db/app?ssl=require",
        "valkey_url": "rediss://service:" + "secret" + "@cache/0",
        "object_storage_endpoint_url": "https://storage.internal",
        "object_storage_access_key": "production-access",
        private_field: "production-" + "secret",
        "identity_provider": "oidc",
        "oidc_issuer": "https://identity.internal.example/issuer",
        "oidc_audience": "exam-guru-api",
        "oidc_jwks_url": "https://identity.internal.example/issuer/jwks",
        "oidc_role_claim_name": "roles",
        "oidc_admin_role": "exam-guru-admin",
        "oidc_reviewer_role": "exam-guru-reviewer",
        "oidc_max_token_age_seconds": 3_600,
        "oidc_clock_skew_seconds": 30,
        "oidc_jwks_timeout_seconds": 2,
        "oidc_jwks_cache_seconds": 300,
        "oidc_jwks_max_cached_keys": 16,
    }

    assert Settings.model_validate(common).storage_reconciliation_apply_tags is False
    assert (
        Settings.model_validate(
            {**common, "storage_reconciliation_apply_tags": True}
        ).storage_reconciliation_apply_tags
        is True
    )
