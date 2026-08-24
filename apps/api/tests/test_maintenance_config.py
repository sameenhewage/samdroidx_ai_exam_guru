import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import Settings


def test_extraction_recovery_and_maintenance_defaults_are_bounded() -> None:
    settings = Settings(environment="test")

    assert 1 <= settings.extraction_recovery_batch_size <= 100
    assert 1 <= settings.extraction_outbox_min_age_seconds <= 3_600
    assert settings.maintenance_scheduler_interval_seconds == 30
    assert 5 <= settings.maintenance_scheduler_interval_seconds <= 3_600


@pytest.mark.parametrize(
    "changes",
    [
        {"extraction_recovery_batch_size": 0},
        {"extraction_recovery_batch_size": 101},
        {"extraction_outbox_min_age_seconds": 0},
        {"extraction_outbox_min_age_seconds": 3_601},
        {"maintenance_scheduler_interval_seconds": 4},
        {"maintenance_scheduler_interval_seconds": 3_601},
    ],
)
def test_extraction_recovery_and_maintenance_config_rejects_unbounded_values(
    changes: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "test", **changes})
