import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge import material_index_jobs as jobs
from exam_guru_api.knowledge.embedding_jobs import (
    DeterministicEmbeddingDispatcher,
    ingest_embeddings,
)
from exam_guru_api.retrieval.embeddings import create_embedding_provider_registry


@pytest.mark.parametrize("age", [-1, True, 3601, 1.5])
def test_material_recovery_age_rejects_invalid_values_before_io(age: Any) -> None:
    settings = Settings(environment="test", test_runtime_id="ai-exam-guru-e2e-index-boundaries")
    session = AsyncMock(spec=AsyncSession)
    dispatcher = DeterministicEmbeddingDispatcher()
    with pytest.raises(ValueError, match="age"):
        asyncio.run(
            jobs.recover_material_indexing(
                session,
                settings=settings,
                providers=create_embedding_provider_registry(settings),
                dispatcher=dispatcher,
                min_age_seconds=age,
            )
        )
    assert not session.mock_calls
    assert dispatcher.dispatched == []


def test_recovery_isolates_one_failed_intent_and_does_not_log_private_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(environment="test", test_runtime_id="ai-exam-guru-e2e-index-boundaries")
    identifiers = (uuid4(), uuid4())
    session = AsyncMock(spec=AsyncSession)
    session.scalars.return_value = identifiers
    promotion = AsyncMock(side_effect=[RuntimeError("private source and provider details"), None])
    monkeypatch.setattr(jobs, "promote_material_index_intent", promotion)
    result = asyncio.run(
        jobs.recover_material_indexing(
            session,
            settings=settings,
            providers=create_embedding_provider_registry(settings),
            dispatcher=DeterministicEmbeddingDispatcher(),
            min_age_seconds=0,
        )
    )
    assert result == jobs.MaterialIndexRecoveryResult(scanned=2, processed=1, failures=1)
    assert [call.args[1] for call in promotion.await_args_list] == list(identifiers)
    session.rollback.assert_awaited_once()
    assert "private source" not in caplog.text
    assert "material knowledge indexing recovery failed" in caplog.text


def test_material_publisher_only_enqueues_existing_embedding_actor_identity() -> None:
    broker = Mock()
    broker.enqueue.return_value = SimpleNamespace(message_id="indexed-message")
    identifier = uuid4()
    assert jobs.MaterialEmbeddingDispatcher(broker).dispatch(identifier) == "indexed-message"
    message = broker.enqueue.call_args.args[0]
    assert message.actor_name == ingest_embeddings.actor_name
    assert message.queue_name == ingest_embeddings.queue_name
    assert message.args == (str(identifier),)
    assert message.kwargs == {}


def test_material_recovery_closes_resources_when_broker_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test", test_runtime_id="ai-exam-guru-e2e-index-boundaries")
    resources = Mock()
    resources.close = AsyncMock()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)
    monkeypatch.setattr(
        jobs, "RedisBroker", Mock(side_effect=RuntimeError("controlled broker failure"))
    )
    with pytest.raises(RuntimeError, match="controlled broker failure"):
        asyncio.run(jobs._recover_material_indexing())
    resources.close.assert_awaited_once()
    resources.session_factory.assert_not_called()


def test_material_recovery_actor_uses_the_bounded_application_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = AsyncMock()
    monkeypatch.setattr(jobs, "_recover_material_indexing", recovery)
    jobs.recover_material_knowledge_indexing()
    recovery.assert_awaited_once_with()
