import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.retrieval.embeddings import create_embedding_provider_registry


@pytest.mark.parametrize(
    ("environment", "identity", "provider", "available"),
    [
        ("local", None, None, False),
        ("local", None, "deterministic", False),
        ("test", None, None, False),
        ("test", None, "deterministic", False),
        ("test", "ai-exam-guru-e2e-material-index", None, True),
        ("test", "ai-exam-guru-e2e-material-index", "deterministic", True),
    ],
)
def test_material_automatic_embedding_gate_requires_attested_test_identity(
    environment: str,
    identity: str | None,
    provider: str | None,
    available: bool,
) -> None:
    from exam_guru_api.knowledge.material_indexing import material_embedding_config

    settings = Settings.model_validate(
        {
            "environment": environment,
            "test_runtime_id": identity,
            "retrieval_embedding_provider": provider,
        }
    )
    assert (
        material_embedding_config(settings, create_embedding_provider_registry(settings))
        is not None
    ) is available


@pytest.mark.parametrize("value", [-1, 0, 9, 100, True, 1.2])
def test_material_recovery_is_bounded_before_database_or_transport(value: Any) -> None:
    from exam_guru_api.knowledge.material_index_jobs import recover_material_indexing

    settings = Settings(environment="test", test_runtime_id="ai-exam-guru-e2e-material-index")
    session = AsyncMock(spec=AsyncSession)
    dispatcher = DeterministicEmbeddingDispatcher()
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(
            recover_material_indexing(
                session,
                settings=settings,
                providers=create_embedding_provider_registry(settings),
                dispatcher=dispatcher,
                batch_size=value,
            )
        )
    assert not session.mock_calls
    assert not dispatcher.dispatched


def test_material_publisher_has_finite_transport_no_retries_and_closes_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import material_index_jobs

    settings = Settings(environment="test", test_runtime_id="ai-exam-guru-e2e-material-index")
    resources = Mock()
    resources.close = AsyncMock()
    session_context = AsyncMock()
    resources.session_factory.return_value = session_context
    broker = Mock()
    constructor = Mock(return_value=broker)
    recovery = AsyncMock(side_effect=RuntimeError("private transport failure"))
    monkeypatch.setattr(material_index_jobs, "Settings", lambda: settings)
    monkeypatch.setattr(material_index_jobs, "create_resources", lambda _settings: resources)
    monkeypatch.setattr(material_index_jobs, "RedisBroker", constructor)
    monkeypatch.setattr(material_index_jobs, "recover_material_indexing", recovery)
    with pytest.raises(RuntimeError):
        asyncio.run(material_index_jobs._recover_material_indexing())
    kwargs = constructor.call_args.kwargs
    assert kwargs["socket_connect_timeout"] == 5
    assert kwargs["socket_timeout"] == 5
    assert kwargs["retry"]._retries == 0
    broker.close.assert_called_once()
    resources.close.assert_awaited_once()
    assert material_index_jobs.recover_material_knowledge_indexing.options["max_retries"] == 0
    assert material_index_jobs.recover_material_knowledge_indexing.options["time_limit"] == 120_000


@pytest.mark.parametrize(
    "changes", [{"expected_version": True}, {"confirmed_retry": 1}, {"reason": " "}]
)
def test_material_retry_request_is_strict(changes: dict[str, object]) -> None:
    from exam_guru_api.knowledge.material_indexing import MaterialKnowledgeIndexRetryRequest

    with pytest.raises(ValidationError):
        MaterialKnowledgeIndexRetryRequest.model_validate(
            {
                "expected_version": 0,
                "confirmed_retry": True,
                "reason": "Explicit retry",
                **changes,
            }
        )
