import asyncio
from dataclasses import asdict
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import DeterministicEmbeddingProvider, EmbeddingConfig
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.retrieval.embeddings import (
    EmbeddingProviderRegistry,
    create_active_embedding_config,
)
from tests.integration.test_material_knowledge_review_api import material_unit
from tests.integration.workspace_fixtures import ADMIN, database_session
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


def indexing_runtime(*, changed: bool = False) -> tuple[Settings, EmbeddingProviderRegistry]:
    settings = Settings(
        environment="test",
        test_runtime_id="ai-exam-guru-e2e-material-review",
        retrieval_embedding_provider="deterministic",
        retrieval_embedding_version="v2" if changed else "v1",
        retrieval_embedding_config_fingerprint="sha256:" + ("b" if changed else "a") * 64,
    )
    providers = EmbeddingProviderRegistry(
        {"deterministic": DeterministicEmbeddingProvider()},
        active_config=create_active_embedding_config(settings),
    )
    return settings, providers


async def reviewed_intent(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService

    document_id, unit_id, _projection, competency_id = await material_unit(session)
    await MaterialKnowledgeReviewService(session).review(
        principal=ADMIN,
        document_id=document_id,
        unit_id=unit_id,
        request=KnowledgeReviewRequest(
            expected_version=0,
            state="reviewed",
            confirmed_mapping=True,
            competency_id=competency_id,
            reason="Explicit material-owned unit review",
        ),
    )
    identifier = await session.scalar(
        text("SELECT id FROM material_knowledge_index_intents WHERE unit_id=:id"), {"id": unit_id}
    )
    assert isinstance(identifier, UUID)
    await session.commit()
    return document_id, unit_id, identifier


def test_automatic_indexing_blocks_real_local_deterministic_fallback_without_spending_attempts(
    workspace_database_url: str,
) -> None:
    from exam_guru_api.knowledge.material_indexing import promote_material_index_intent

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            _document, _unit, identifier = await reviewed_intent(session)
            _settings, providers = indexing_runtime()
            dispatcher = DeterministicEmbeddingDispatcher()
            await promote_material_index_intent(
                session,
                identifier,
                settings=Settings(environment="local"),
                providers=providers,
                dispatcher=dispatcher,
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM material_knowledge_index_intents WHERE id=:id"),
                        {"id": identifier},
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "waiting_configuration"
            assert row["attempt_number"] == 0
            assert row["dispatch_key"] is None
            assert row["config_snapshot"] is None
            assert dispatcher.dispatched == []

    asyncio.run(check())


def test_promotion_pins_before_existing_service_creation_and_real_fake_vector_is_ready(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from exam_guru_api.knowledge.material_indexing import promote_material_index_intent
    from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService

    original = EmbeddingJobService.create
    inspected: list[UUID] = []

    async def create(service: EmbeddingJobService, *args: Any, **kwargs: Any) -> object:
        async with database_session(workspace_database_url) as independent:
            row = (
                (
                    await independent.execute(
                        text(
                            "SELECT * FROM material_knowledge_index_intents WHERE dispatch_key=:key"
                        ),
                        {"key": kwargs["idempotency_key"]},
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "dispatching"
            assert row["attempt_number"] == 1
            assert row["config_snapshot"] == asdict(service._active_config)
            assert row["embedding_job_id"] is None
            inspected.append(row["id"])
        return await original(service, *args, **kwargs)

    monkeypatch.setattr(EmbeddingJobService, "create", create)

    async def check() -> None:
        settings, providers = indexing_runtime()
        dispatcher = DeterministicEmbeddingDispatcher()
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            await promote_material_index_intent(
                session, identifier, settings=settings, providers=providers, dispatcher=dispatcher
            )
            assert inspected == [identifier]
            assert len(dispatcher.dispatched) == 1
            job_id = dispatcher.dispatched[0]
            before = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert before.indexing.status == "queued"
            assert before.indexing.ready is False
            await session.rollback()
            assert await EmbeddingWorkerService(
                session, providers, providers.active_config
            ).process(job_id)
            current = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert current.indexing.status == "ready"
            assert current.indexing.ready is True
            assert current.indexing.retry_allowed is False
            await session.rollback()
            await promote_material_index_intent(
                session, identifier, settings=settings, providers=providers, dispatcher=dispatcher
            )
            assert len(dispatcher.dispatched) == 1
            changed_settings, changed_providers = indexing_runtime(changed=True)
            old = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=changed_settings,
                providers=changed_providers,
            )
            assert old.indexing.status == "configuration_changed"
            assert old.indexing.ready is False
            assert old.indexing.retry_allowed is True

    asyncio.run(check())


@pytest.mark.parametrize("changed", [False, True])
def test_lost_job_ack_reconciles_fixed_binding_before_active_configuration(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    from exam_guru_api.knowledge.material_indexing import promote_material_index_intent

    original = EmbeddingJobService.create

    async def lose_ack(service: EmbeddingJobService, *args: Any, **kwargs: Any) -> object:
        await original(service, *args, **kwargs)
        raise RuntimeError("lost acknowledgement after durable embedding job")

    async def check() -> None:
        settings, providers = indexing_runtime()
        dispatcher = DeterministicEmbeddingDispatcher()
        async with database_session(workspace_database_url) as session:
            _document, _unit, identifier = await reviewed_intent(session)
            monkeypatch.setattr(EmbeddingJobService, "create", lose_ack)
            with pytest.raises(RuntimeError):
                await promote_material_index_intent(
                    session,
                    identifier,
                    settings=settings,
                    providers=providers,
                    dispatcher=dispatcher,
                )
            await session.rollback()
            first = (
                (
                    await session.execute(
                        text(
                            "SELECT dispatch_key, config_snapshot, attempt_number "
                            "FROM material_knowledge_index_intents WHERE id=:id"
                        ),
                        {"id": identifier},
                    )
                )
                .mappings()
                .one()
            )
            await session.rollback()
            monkeypatch.setattr(EmbeddingJobService, "create", original)
            next_settings, next_providers = indexing_runtime(changed=changed)
            await promote_material_index_intent(
                session,
                identifier,
                settings=next_settings,
                providers=next_providers,
                dispatcher=dispatcher,
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM material_knowledge_index_intents WHERE id=:id"),
                        {"id": identifier},
                    )
                )
                .mappings()
                .one()
            )
            assert row["embedding_job_id"] == dispatcher.dispatched[0]
            assert row["dispatch_key"] == first["dispatch_key"]
            assert row["config_snapshot"] == first["config_snapshot"]
            assert row["attempt_number"] == 1
            assert len(dispatcher.dispatched) == 1
            job = await session.get(EmbeddingJobModel, row["embedding_job_id"])
            assert job is not None
            assert job.embedding_config == EmbeddingConfig(**first["config_snapshot"])

    asyncio.run(check())


def test_concurrent_promotion_converges_on_one_embedding_job(workspace_database_url: str) -> None:
    from exam_guru_api.knowledge.material_indexing import promote_material_index_intent

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            _document, _unit, identifier = await reviewed_intent(session)
        settings, providers = indexing_runtime()
        dispatcher = DeterministicEmbeddingDispatcher()

        async def promote() -> None:
            async with database_session(workspace_database_url) as session:
                await promote_material_index_intent(
                    session,
                    identifier,
                    settings=settings,
                    providers=providers,
                    dispatcher=dispatcher,
                )

        await asyncio.wait_for(asyncio.gather(promote(), promote()), timeout=30)
        assert len(set(dispatcher.dispatched)) == 1
        async with database_session(workspace_database_url) as session:
            job_id = await session.scalar(
                text("SELECT embedding_job_id FROM material_knowledge_index_intents WHERE id=:id"),
                {"id": identifier},
            )
            assert job_id == dispatcher.dispatched[0]

    asyncio.run(check())


def test_explicit_configuration_retry_preserves_human_review_and_global_attempts(
    workspace_database_url: str,
) -> None:
    from exam_guru_api.knowledge.material_indexing import (
        MaterialKnowledgeIndexRetryRequest,
        promote_material_index_intent,
    )
    from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            settings, providers = indexing_runtime()
            dispatcher = DeterministicEmbeddingDispatcher()
            await promote_material_index_intent(
                session, identifier, settings=settings, providers=providers, dispatcher=dispatcher
            )
            assert await EmbeddingWorkerService(
                session, providers, providers.active_config
            ).process(dispatcher.dispatched[0])
            settings, providers = indexing_runtime(changed=True)
            service = MaterialKnowledgeReviewService(session)
            current = await service.get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert current.indexing.version is not None
            await session.rollback()
            await service.retry(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                request=MaterialKnowledgeIndexRetryRequest(
                    expected_version=current.indexing.version,
                    confirmed_retry=True,
                    reason="Explicitly approve indexing with the changed configuration",
                ),
                settings=settings,
                providers=providers,
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.unit_id == unit_id)
                )
                == 1
            )
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM material_knowledge_index_intents WHERE id=:id"),
                        {"id": identifier},
                    )
                )
                .mappings()
                .one()
            )
            assert row["attempt_number"] == 2
            assert row["config_snapshot"] == asdict(providers.active_config)
            assert row["embedding_job_id"] is None
            assert len(dispatcher.dispatched) == 1
            await session.rollback()
            await promote_material_index_intent(
                session, identifier, settings=settings, providers=providers, dispatcher=dispatcher
            )
            assert len(set(dispatcher.dispatched)) == 2

    asyncio.run(check())
