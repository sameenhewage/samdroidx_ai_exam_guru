import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import DOCUMENT_UNDERSTANDING_ACTOR_MAX_EXECUTION_SECONDS, Settings
from exam_guru_api.documents.source_reading import SourceReadingBudget
from exam_guru_api.documents.understanding_provider import UnderstandingBudget


def test_multicrop_deadlines_fit_inside_the_actor_and_lease_without_becoming_unbounded() -> None:
    budget = UnderstandingBudget(timeout_ms=180000, pipeline=SourceReadingBudget())
    assert budget.pipeline is not None
    assert budget.pipeline.total_timeout_ms == 900000
    assert (
        budget.pipeline.total_timeout_ms < DOCUMENT_UNDERSTANDING_ACTOR_MAX_EXECUTION_SECONDS * 1000
    )
    settings = Settings(environment="test")
    assert (
        settings.document_understanding_worker_lease_seconds
        > DOCUMENT_UNDERSTANDING_ACTOR_MAX_EXECUTION_SECONDS
    )
    assert settings.document_understanding_worker_lease_seconds == 1500
    with pytest.raises(ValidationError):
        UnderstandingBudget(timeout_ms=180001)
    with pytest.raises(ValidationError):
        SourceReadingBudget(total_timeout_ms=900001)
    with pytest.raises(ValidationError):
        Settings(environment="test", document_understanding_worker_lease_seconds=1200)
