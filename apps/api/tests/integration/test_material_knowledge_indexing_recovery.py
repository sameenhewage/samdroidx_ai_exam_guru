import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select

from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import (
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import (
    MaterialKnowledgeError,
    MaterialKnowledgeIndexRetryRequest,
    promote_material_index_intent,
)
from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_material_knowledge_indexing_postgres import (
    indexing_runtime,
    reviewed_intent,
)
from tests.integration.test_material_knowledge_review_api import material_unit

pytestmark = pytest.mark.integration


class KnownFailedProvider:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
        self.calls += 1
        raise EmbeddingContractError("Known rejected fixture result")


def test_material_global_attempt_limit_survives_configuration_changes(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            fake = KnownFailedProvider()
            settings, base = indexing_runtime()
            providers = EmbeddingProviderRegistry(
                {"deterministic": fake}, active_config=base.active_config
            )
            dispatcher = DeterministicEmbeddingDispatcher()
            service = MaterialKnowledgeReviewService(session)
            for attempt in range(1, 5):
                await promote_material_index_intent(
                    session,
                    identifier,
                    settings=settings,
                    providers=providers,
                    dispatcher=dispatcher,
                )
                job_id = dispatcher.dispatched[-1]
                assert await EmbeddingWorkerService(
                    session, providers, providers.active_config
                ).process(job_id)
                job = await session.get(EmbeddingJobModel, job_id, populate_existing=True)
                assert job is not None
                assert job.retry_depth == (attempt - 1) // 2
                await session.rollback()
                settings, base = indexing_runtime(changed=attempt % 2 == 1)
                providers = EmbeddingProviderRegistry(
                    {"deterministic": fake}, active_config=base.active_config
                )
                workspace = await service.get_workspace(
                    principal=ADMIN,
                    document_id=document_id,
                    unit_id=unit_id,
                    settings=settings,
                    providers=providers,
                )
                assert workspace.indexing.status == "configuration_changed"
                assert workspace.indexing.retry_allowed is (attempt < 4)
                assert workspace.indexing.version is not None
                request = MaterialKnowledgeIndexRetryRequest(
                    expected_version=workspace.indexing.version,
                    reason="Explicitly approve the next bounded configuration attempt",
                    confirmed_retry=True,
                )
                if attempt < 4:
                    await service.retry(
                        principal=ADMIN,
                        document_id=document_id,
                        unit_id=unit_id,
                        request=request,
                        settings=settings,
                        providers=providers,
                    )
                else:
                    with pytest.raises(MaterialKnowledgeError, match="not_allowed"):
                        await service.retry(
                            principal=ADMIN,
                            document_id=document_id,
                            unit_id=unit_id,
                            request=request,
                            settings=settings,
                            providers=providers,
                        )
            assert fake.calls == 4
            assert len(set(dispatcher.dispatched)) == 4
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.unit_id == unit_id)
                )
                == 1
            )

    asyncio.run(check())


def test_material_initial_dispatch_honors_existing_provider_retry_depth(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            intent = await session.get(MaterialKnowledgeIndexIntentModel, identifier)
            assert intent is not None
            assert intent.projection_id is not None
            projection_id, curriculum_id = intent.projection_id, intent.curriculum_version_id
            settings, base = indexing_runtime()
            fake = KnownFailedProvider()
            providers = EmbeddingProviderRegistry(
                {"deterministic": fake}, active_config=base.active_config
            )
            dispatcher = DeterministicEmbeddingDispatcher()
            for attempt in range(4):
                created = await EmbeddingJobService(
                    session, providers, dispatcher, providers.active_config
                ).create(
                    curriculum_id,
                    historical_question_ids=(),
                    knowledge_chunk_ids=(),
                    knowledge_projection_ids=(projection_id,),
                    actor_id=ADMIN.subject_id,
                    idempotency_key=f"advanced-retry-{identifier}-{attempt}",
                )
                assert await EmbeddingWorkerService(
                    session, providers, providers.active_config
                ).process(created.job.id)
            await promote_material_index_intent(
                session, identifier, settings=settings, providers=providers, dispatcher=dispatcher
            )
            await session.refresh(intent)
            assert intent.status == "needs_attention"
            assert intent.failure_code == "indexing_retry_exhausted"
            assert intent.embedding_job_id is None
            assert len(dispatcher.dispatched) == 4
            assert fake.calls == 4
            workspace = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert workspace.indexing.retry_allowed is False

    asyncio.run(check())


def test_current_advanced_vector_is_ready_without_inventing_a_material_intent(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, projection_id, competency_id = await material_unit(session)
            review = await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Advanced technical review",
                ),
            )
            settings, providers = indexing_runtime()
            dispatcher = DeterministicEmbeddingDispatcher()
            created = await EmbeddingJobService(
                session, providers, dispatcher, providers.active_config
            ).create(
                review.curriculum_version_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                actor_id=ADMIN.subject_id,
                idempotency_key=f"advanced-vector-{unit_id}",
            )
            assert await EmbeddingWorkerService(
                session, providers, providers.active_config
            ).process(created.job.id)
            workspace = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert workspace.indexing.status == "ready"
            assert workspace.indexing.ready is True
            assert workspace.indexing.intent_id is None
            assert workspace.indexing.retry_allowed is False

    asyncio.run(check())


def test_review_withdrawal_after_job_commit_before_intent_link_retains_job_without_authority(
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = EmbeddingJobService.create

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            settings, providers = indexing_runtime()
            dispatcher = DeterministicEmbeddingDispatcher()

            async def withdraw(service: EmbeddingJobService, *args: Any, **kwargs: Any) -> object:
                result = await original(service, *args, **kwargs)
                async with database_session(workspace_database_url) as other:
                    await MaterialKnowledgeReviewService(other).review(
                        principal=ADMIN,
                        document_id=document_id,
                        unit_id=unit_id,
                        request=KnowledgeReviewRequest(
                            expected_version=1,
                            state="rejected",
                            confirmed_mapping=False,
                            reason="Concurrent explicit withdrawal after job commit",
                        ),
                    )
                return result

            monkeypatch.setattr(EmbeddingJobService, "create", withdraw)
            await promote_material_index_intent(
                session, identifier, settings=settings, providers=providers, dispatcher=dispatcher
            )
            intent = await session.get(
                MaterialKnowledgeIndexIntentModel, identifier, populate_existing=True
            )
            assert intent is not None
            assert intent.status == "superseded"
            assert intent.embedding_job_id == dispatcher.dispatched[0]
            assert len(dispatcher.dispatched) == 1
            workspace = await MaterialKnowledgeReviewService(session).get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert workspace.indexing.status == "superseded"
            assert workspace.indexing.ready is False
            assert workspace.indexing.retry_allowed is False

    asyncio.run(check())


def test_paused_coordinator_cannot_resume_after_another_observes_configuration_change(
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import material_indexing

    original_lock = material_indexing.locked_intent

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            _document, _unit, identifier = await reviewed_intent(session)
            settings, providers = indexing_runtime()
            changed, changed_providers = indexing_runtime(changed=True)
            dispatcher = DeterministicEmbeddingDispatcher()
            locks = 0

            async def observe_change(
                *args: Any, **kwargs: Any
            ) -> MaterialKnowledgeIndexIntentModel:
                nonlocal locks
                locks += 1
                if locks == 2:
                    async with database_session(workspace_database_url) as other:
                        await promote_material_index_intent(
                            other,
                            identifier,
                            settings=changed,
                            providers=changed_providers,
                            dispatcher=dispatcher,
                        )
                return await original_lock(*args, **kwargs)

            monkeypatch.setattr(material_indexing, "locked_intent", observe_change)
            await promote_material_index_intent(
                session,
                identifier,
                settings=settings,
                providers=providers,
                dispatcher=dispatcher,
            )
            intent = await session.get(
                MaterialKnowledgeIndexIntentModel, identifier, populate_existing=True
            )
            assert intent is not None
            assert intent.status == "configuration_changed"
            assert intent.attempt_number == 1
            assert intent.embedding_job_id is None
            assert dispatcher.dispatched == []

    asyncio.run(check())


@pytest.mark.parametrize("resumed_status", ["dispatching", "waiting_configuration"])
def test_sql_cannot_resume_configuration_changed_intent_without_explicit_retry(
    workspace_database_url: str,
    resumed_status: str,
) -> None:
    from sqlalchemy.exc import DBAPIError

    from exam_guru_api.knowledge.material_indexing import index_transition, locked_intent

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            _document, _unit, identifier = await reviewed_intent(session)
            _settings, providers = indexing_runtime()
            intent = await locked_intent(session, identifier)
            await index_transition(
                session,
                intent,
                "dispatching",
                event="dispatch_bound",
                config=providers.active_config,
            )
            await session.commit()
            intent = await locked_intent(session, identifier)
            await index_transition(
                session, intent, "configuration_changed", failure_code="configuration_changed"
            )
            await session.commit()
            intent = await locked_intent(session, identifier)
            with pytest.raises(DBAPIError):
                await index_transition(
                    session,
                    intent,
                    resumed_status,
                    failure_code="configuration_unavailable"
                    if resumed_status == "waiting_configuration"
                    else None,
                )
            await session.rollback()

    asyncio.run(check())
