import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import Settings
from exam_guru_api.generation.jobs import GENERATION_JOB_TIME_LIMIT_MS

MAX_PROVIDER_EXECUTION_MS = 3 * 120_000 + 2 * 2_000


def test_generation_recovery_defaults_are_bounded_and_lease_exceeds_all_execution_limits() -> None:
    settings = Settings(environment="test")

    assert 1 <= settings.generation_recovery_batch_size <= 100
    assert 1 <= settings.generation_outbox_min_age_seconds <= 3_600
    assert settings.generation_worker_lease_seconds <= 86_400
    assert settings.generation_worker_lease_seconds * 1_000 > GENERATION_JOB_TIME_LIMIT_MS
    assert settings.generation_worker_lease_seconds * 1_000 > MAX_PROVIDER_EXECUTION_MS


@pytest.mark.parametrize(
    "changes",
    [
        {"generation_recovery_batch_size": 0},
        {"generation_recovery_batch_size": 101},
        {"generation_outbox_min_age_seconds": 0},
        {"generation_outbox_min_age_seconds": 3_601},
        {"generation_worker_lease_seconds": MAX_PROVIDER_EXECUTION_MS // 1_000},
        {"generation_worker_lease_seconds": 86_401},
    ],
)
def test_generation_recovery_configuration_rejects_unbounded_or_unsafe_values(
    changes: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "test", **changes})
