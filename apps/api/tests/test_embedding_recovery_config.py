import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import (
    EMBEDDING_ACTOR_MAX_EXECUTION_SECONDS,
    MIN_EMBEDDING_WORKER_LEASE_SECONDS,
    Settings,
)
from exam_guru_api.knowledge.embedding_jobs import EMBEDDING_JOB_TIME_LIMIT_MS


def test_embedding_recovery_defaults_are_bounded_and_lease_exceeds_actor_limit() -> None:
    settings = Settings(environment="test")

    assert 1 <= settings.embedding_recovery_batch_size <= 100
    assert 1 <= settings.embedding_outbox_min_age_seconds <= 3_600
    assert settings.embedding_worker_lease_seconds == 600
    assert MIN_EMBEDDING_WORKER_LEASE_SECONDS == EMBEDDING_ACTOR_MAX_EXECUTION_SECONDS + 1
    assert MIN_EMBEDDING_WORKER_LEASE_SECONDS <= settings.embedding_worker_lease_seconds <= 86_400
    assert settings.embedding_worker_lease_seconds * 1_000 > EMBEDDING_JOB_TIME_LIMIT_MS


@pytest.mark.parametrize(
    "overrides",
    [
        {"embedding_recovery_batch_size": 0},
        {"embedding_recovery_batch_size": 101},
        {"embedding_outbox_min_age_seconds": 0},
        {"embedding_outbox_min_age_seconds": 3_601},
        {"embedding_worker_lease_seconds": MIN_EMBEDDING_WORKER_LEASE_SECONDS - 1},
        {"embedding_worker_lease_seconds": 86_401},
    ],
)
def test_embedding_recovery_configuration_rejects_unbounded_or_unsafe_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **overrides)  # type: ignore[arg-type]
