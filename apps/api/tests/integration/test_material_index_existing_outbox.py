import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingRecoveryPolicy,
    EmbeddingRecoveryService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import promote_material_index_intent
from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService
from exam_guru_api.knowledge.models import EmbeddingJobModel
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_material_knowledge_indexing_postgres import (
    indexing_runtime,
    reviewed_intent,
)

pytestmark = pytest.mark.integration


def test_material_intent_reuses_existing_embedding_outbox_after_dispatch_failure(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UnavailableDispatcher:
        def dispatch(self, _identifier: UUID) -> str:
            raise RuntimeError("controlled broker outage")

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, intent_id = await reviewed_intent(session)
            settings, providers = indexing_runtime()
            embedding_call = AsyncMock(wraps=providers.embed_source_async)
            monkeypatch.setattr(providers, "embed_source_async", embedding_call)
            await promote_material_index_intent(
                session,
                intent_id,
                settings=settings,
                providers=providers,
                dispatcher=UnavailableDispatcher(),
            )
            intent = await session.get(
                MaterialKnowledgeIndexIntentModel, intent_id, populate_existing=True
            )
            assert intent is not None
            assert intent.status == "queued"
            assert intent.embedding_job_id is not None
            job_id = intent.embedding_job_id
            job = await session.get(EmbeddingJobModel, job_id, populate_existing=True)
            assert job is not None
            assert job.status == "queued"
            assert job.queue_message_id is None
            embedding_call.assert_not_awaited()
            dispatcher = DeterministicEmbeddingDispatcher()
            recovered = await EmbeddingRecoveryService(
                session, dispatcher, EmbeddingRecoveryPolicy(outbox_min_age_seconds=1)
            ).recover(now=datetime.now(UTC) + timedelta(seconds=2))
            assert recovered.outbox_dispatched == 1
            assert dispatcher.dispatched == [job_id]
            assert await EmbeddingWorkerService(
                session, providers, providers.active_config
            ).process(job_id)
            await promote_material_index_intent(
                session, intent_id, settings=settings, providers=providers, dispatcher=dispatcher
            )
            workspace = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert workspace.indexing.status == "ready"
            assert workspace.indexing.ready
            assert workspace.indexing.intent_id == intent_id
            embedding_call.assert_awaited_once()
            assert dispatcher.dispatched == [job_id]

    asyncio.run(check())
