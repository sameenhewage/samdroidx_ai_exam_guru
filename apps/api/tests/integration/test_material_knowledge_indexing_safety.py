import asyncio
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import TaxonomyReviewState
from exam_guru_api.curriculum.models import TaxonomyNodeModel
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import (
    EmbeddingConfig,
    EmbeddingContractError,
    EmbeddingResult,
)
from exam_guru_api.knowledge.material_index_jobs import recover_material_indexing
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import (
    MaterialKnowledgeError,
    MaterialKnowledgeIndexRetryRequest,
    index_transition,
    locked_intent,
    promote_material_index_intent,
)
from exam_guru_api.knowledge.material_review import MaterialKnowledgeReviewService
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.knowledge.preparation_requests import MaterialKnowledgeRequestRecorder
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.test_material_knowledge_indexing_postgres import (
    indexing_runtime,
    reviewed_intent,
)
from tests.integration.test_material_knowledge_review_api import material_unit
from tests.integration.workspace_fixtures import ADMIN, database_session
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)
from tests.test_document_understanding_contracts import counting_candidate, parse

pytestmark = pytest.mark.integration


def test_bounded_recovery_does_not_starve_new_intents_behind_already_queued_jobs(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            identifiers = [(await reviewed_intent(session))[2] for _ in range(8)]
            settings, providers = indexing_runtime()
            dispatcher = DeterministicEmbeddingDispatcher()
            first = await recover_material_indexing(
                session,
                settings=settings,
                providers=providers,
                dispatcher=dispatcher,
                min_age_seconds=0,
            )
            assert first.scanned == first.processed == 8
            assert first.failures == 0
            assert len(dispatcher.dispatched) == 8
            identifiers.append((await reviewed_intent(session))[2])
            second = await recover_material_indexing(
                session,
                settings=settings,
                providers=providers,
                dispatcher=dispatcher,
                min_age_seconds=0,
            )
            assert second.scanned <= 8
            assert second.failures == 0
            linked = await session.scalar(
                select(func.count())
                .select_from(MaterialKnowledgeIndexIntentModel)
                .where(
                    MaterialKnowledgeIndexIntentModel.id.in_(identifiers),
                    MaterialKnowledgeIndexIntentModel.embedding_job_id.is_not(None),
                )
            )
            assert linked == 9
            assert len(set(dispatcher.dispatched)) == 9

    asyncio.run(check())


def test_technical_review_and_0052_enrollment_are_never_indexing_authority(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, projection_id, competency_id = await material_unit(session)
            await PageUnderstandingService(
                session, preparation_recorder=MaterialKnowledgeRequestRecorder(session)
            ).exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=2,
                expected_version=0,
                confirm_exclusion=True,
                reason="Unneeded synthetic sibling page",
            )
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Technical review only",
                ),
            )
            settings, providers = indexing_runtime()
            dispatcher = DeterministicEmbeddingDispatcher()
            await recover_material_indexing(
                session,
                settings=settings,
                providers=providers,
                dispatcher=dispatcher,
                min_age_seconds=0,
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MaterialKnowledgeIndexIntentModel)
                    .where(MaterialKnowledgeIndexIntentModel.unit_id == unit_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EmbeddingJobModel)
                    .where(EmbeddingJobModel.knowledge_projection_ids == [str(projection_id)])
                )
                == 0
            )

    asyncio.run(check())


@pytest.mark.parametrize("unknown", [False, True])
def test_no_automatic_provider_retry_and_explicit_retry_preserves_review_and_depth(
    workspace_database_url: str,
    unknown: bool,
) -> None:
    class FailedProvider:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, text: str, config: EmbeddingConfig) -> EmbeddingResult:
            self.calls += 1
            if unknown:
                raise RuntimeError("ambiguous provider transport outcome")
            raise EmbeddingContractError("known invalid provider result")

    async def check() -> None:
        settings, base = indexing_runtime()
        fake = FailedProvider()
        providers = EmbeddingProviderRegistry(
            {"deterministic": fake}, active_config=base.active_config
        )
        dispatcher = DeterministicEmbeddingDispatcher()
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
            service = MaterialKnowledgeReviewService(session)
            for attempt in range(1, 2 if unknown else 5):
                await promote_material_index_intent(
                    session,
                    identifier,
                    settings=settings,
                    providers=providers,
                    dispatcher=dispatcher,
                )
                job_id = dispatcher.dispatched[-1]
                before = await service.get_workspace(
                    principal=ADMIN,
                    document_id=document_id,
                    unit_id=unit_id,
                    settings=settings,
                    providers=providers,
                )
                assert before.indexing.retry_allowed is False
                assert before.indexing.version is not None
                with pytest.raises(MaterialKnowledgeError):
                    await service.retry(
                        principal=ADMIN,
                        document_id=document_id,
                        unit_id=unit_id,
                        request=MaterialKnowledgeIndexRetryRequest(
                            expected_version=before.indexing.version,
                            reason="Active work cannot retry",
                            confirmed_retry=True,
                        ),
                        settings=settings,
                        providers=providers,
                    )
                assert await EmbeddingWorkerService(
                    session, providers, providers.active_config
                ).process(job_id)
                for _ in range(2):
                    await promote_material_index_intent(
                        session,
                        identifier,
                        settings=settings,
                        providers=providers,
                        dispatcher=dispatcher,
                    )
                assert fake.calls == attempt
                assert len(dispatcher.dispatched) == attempt
                job = await session.get(EmbeddingJobModel, job_id, populate_existing=True)
                assert job is not None
                assert job.status == "failed"
                assert job.retry_depth == attempt - 1
                current = await service.get_workspace(
                    principal=ADMIN,
                    document_id=document_id,
                    unit_id=unit_id,
                    settings=settings,
                    providers=providers,
                )
                assert current.indexing.status == "needs_attention"
                assert current.indexing.ready is False
                assert current.indexing.retry_allowed is (not unknown and attempt < 4)
                assert current.indexing.version is not None
                request = MaterialKnowledgeIndexRetryRequest(
                    expected_version=current.indexing.version,
                    reason="Explicitly retry this known failed indexing attempt",
                    confirmed_retry=True,
                )
                if unknown or attempt == 4:
                    with pytest.raises(MaterialKnowledgeError):
                        await service.retry(
                            principal=ADMIN,
                            document_id=document_id,
                            unit_id=unit_id,
                            request=request,
                            settings=settings,
                            providers=providers,
                        )
                    changed, changed_providers = indexing_runtime(changed=True)
                    assert not (
                        await service.get_workspace(
                            principal=ADMIN,
                            document_id=document_id,
                            unit_id=unit_id,
                            settings=changed,
                            providers=changed_providers,
                        )
                    ).indexing.retry_allowed
                else:
                    with pytest.raises(MaterialKnowledgeError, match="version_conflict"):
                        await service.retry(
                            principal=ADMIN,
                            document_id=document_id,
                            unit_id=unit_id,
                            request=request.model_copy(
                                update={"expected_version": request.expected_version - 1}
                            ),
                            settings=settings,
                            providers=providers,
                        )
                    await service.retry(
                        principal=ADMIN,
                        document_id=document_id,
                        unit_id=unit_id,
                        request=request,
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
                await session.rollback()

    asyncio.run(check())


def test_config_change_after_persisted_binding_requires_explicit_retry_even_before_any_job(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, identifier = await reviewed_intent(session)
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
            first_key = intent.dispatch_key
            changed, changed_providers = indexing_runtime(changed=True)
            dispatcher = DeterministicEmbeddingDispatcher()
            for _ in range(2):
                await promote_material_index_intent(
                    session,
                    identifier,
                    settings=changed,
                    providers=changed_providers,
                    dispatcher=dispatcher,
                )
            await session.refresh(intent)
            assert intent.status == "configuration_changed"
            assert intent.attempt_number == 1
            assert intent.dispatch_key == first_key
            assert intent.config_snapshot == asdict(providers.active_config)
            assert dispatcher.dispatched == []
            service = MaterialKnowledgeReviewService(session)
            await service.retry(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                request=MaterialKnowledgeIndexRetryRequest(
                    expected_version=intent.version,
                    reason="Approve the changed profile",
                    confirmed_retry=True,
                ),
                settings=changed,
                providers=changed_providers,
            )
            await session.refresh(intent)
            assert intent.attempt_number == 2
            assert intent.dispatch_key != first_key
            assert intent.config_snapshot == asdict(changed_providers.active_config)
            events = (
                await session.scalars(
                    select(AdminAuditEventModel).where(
                        AdminAuditEventModel.resource_id == unit_id,
                        AdminAuditEventModel.action == "material_knowledge_index.dispatch_bound",
                    )
                )
            ).all()
            assert len(events) == 1
            assert events[0].payload["dispatch_key"] == first_key
            assert events[0].payload["config_snapshot"] == asdict(providers.active_config)

    asyncio.run(check())


def test_decorative_review_has_explicit_not_searchable_intent_and_can_be_withdrawn(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            payload = counting_candidate()
            payload["observation"]["regions"][0].update(
                {"kind": "decorative_image", "exact_text": ""}
            )
            document_id, trusted_id, curriculum_id = await verified_source(
                session, content=parse(payload)
            )
            prepared = await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            unit_id = prepared.unsearchable_unit_ids[0]
            competency_id = uuid4()
            session.add(
                TaxonomyNodeModel(
                    id=competency_id,
                    curriculum_version_id=curriculum_id,
                    level="competency",
                    code="DECORATIVE",
                    title="Reviewed decorative unit",
                    active=True,
                    review_state=TaxonomyReviewState.REVIEWED,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            await session.commit()
            service = MaterialKnowledgeReviewService(session)
            await service.review(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Reviewed non-searchable evidence",
                ),
            )
            settings, providers = indexing_runtime()
            current = await service.get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert current.indexing.status == "not_searchable"
            assert current.indexing.ready is False
            assert current.indexing.retry_allowed is False
            assert current.indexing.intent_id is not None
            dispatcher = DeterministicEmbeddingDispatcher()
            await promote_material_index_intent(
                session,
                current.indexing.intent_id,
                settings=settings,
                providers=providers,
                dispatcher=dispatcher,
            )
            assert dispatcher.dispatched == []
            await service.review(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw the educational mapping without fabricated vectors",
                ),
            )
            withdrawn = await service.get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert withdrawn.indexing.status == "superseded"
            intent = await session.get(
                MaterialKnowledgeIndexIntentModel, current.indexing.intent_id
            )
            assert intent is not None
            assert intent.projection_id is None
            assert intent.status == "superseded"
            assert intent.attempt_number == 0

    asyncio.run(check())


def test_material_reads_do_not_flush_and_have_finite_sql_waits(
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, _projection, _competency = await material_unit(session)
            statements: list[str] = []
            connection = await session.connection()

            def executed(
                _connection: Any,
                _cursor: Any,
                statement: str,
                _parameters: Any,
                _context: Any,
                _many: Any,
            ) -> None:
                statements.append(statement)

            event.listen(connection.sync_connection, "before_cursor_execute", executed)
            session.add(
                AdminAuditEventModel(
                    id=uuid4(),
                    actor_id=ADMIN.subject_id,
                    resource_type="unrelated",
                    resource_id=unit_id,
                    action="unrelated.pending",
                    payload={},
                )
            )
            flush = AsyncMock(side_effect=AssertionError("GET cannot flush caller writes"))
            commit = AsyncMock(side_effect=AssertionError("GET cannot commit caller writes"))
            monkeypatch.setattr(session, "flush", flush)
            monkeypatch.setattr(session, "commit", commit)
            settings, providers = indexing_runtime()
            service = MaterialKnowledgeReviewService(session)
            result = await service.list_units(
                principal=ADMIN, document_id=document_id, settings=settings, providers=providers
            )
            assert result.total == 2
            assert not any("knowledge_units.payload" in statement for statement in statements)
            assert any("statement_timeout" in statement for statement in statements)
            await service.get_workspace(
                principal=ADMIN,
                document_id=document_id,
                unit_id=unit_id,
                settings=settings,
                providers=providers,
            )
            assert not flush.mock_calls
            assert not commit.mock_calls
            assert not any(
                statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for statement in statements
            )

    asyncio.run(check())
