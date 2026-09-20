import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingSourceNotFoundError,
    EmbeddingSourceNotReviewedError,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import EmbeddingAccounting, EmbeddingConfig, EmbeddingResult
from exam_guru_api.knowledge.models import EmbeddingJobModel, KnowledgeEmbeddingModel
from exam_guru_api.knowledge.repository import KnowledgeRecordNotFoundError
from exam_guru_api.knowledge.service import KnowledgePersistenceService
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.retrieval.embeddings import DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
from tests.integration.test_embedding_jobs_postgres import (
    ADMIN_HEADERS,
    REVIEWER_HEADERS,
    RecordingProvider,
    Seed,
    _client,
    _registry,
)
from tests.integration.test_knowledge_unit_review_postgres import reviewable_unit
from tests.integration.workspace_fixtures import ADMIN, database_session
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


class AccountingProvider(RecordingProvider):
    def embed(self, value: str, config: EmbeddingConfig) -> EmbeddingResult:
        result = super().embed(value, config)
        return EmbeddingResult(
            vector=result.vector,
            config=result.config,
            accounting=EmbeddingAccounting(
                input_tokens=17, total_tokens=17, cost_microusd=3, latency_ms=4
            ),
        )


async def projection_source(
    session: AsyncSession, *, reviewed: bool = True
) -> tuple[UUID, UUID, UUID]:
    unit_id, projection_id, competency_id = await reviewable_unit(session)
    unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
    if reviewed:
        await KnowledgeUnitReviewService(session).review(
            principal=ADMIN,
            unit_id=unit_id,
            request=KnowledgeReviewRequest(
                expected_version=0,
                state="reviewed",
                confirmed_mapping=True,
                competency_id=competency_id,
                reason="Synthetic embedding eligibility review",
            ),
        )
    return unit_id, projection_id, unit.scope.curriculum_version_id


def test_durable_projection_embedding_reuses_existing_provider_jobs_and_vectors(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        provider = RecordingProvider()
        registry = _registry(provider)
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            _unit_id, projection_id, curriculum_id = await projection_source(session)
            dispatcher = DeterministicEmbeddingDispatcher()
            service = EmbeddingJobService(session, registry, dispatcher, config)
            key = "projection-" + str(uuid4())
            created = await service.create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key=key,
                actor_id=ADMIN.subject_id,
            )
            job_id = created.job.id
            assert created.job.knowledge_projection_ids == [str(projection_id)]
            created_audit = await session.scalar(
                select(AdminAuditEventModel).where(
                    AdminAuditEventModel.resource_id == job_id,
                    AdminAuditEventModel.action == "embedding_job.created",
                )
            )
            assert created_audit is not None
            captured = created_audit.payload.get("projection_sources")
            assert isinstance(captured, list)
            assert captured[0]["projection_id"] == str(projection_id)
            assert captured[0]["lineage"]["schema_version"] == "projection-embedding-lineage.v1"
            assert provider.calls == []
            assert await EmbeddingWorkerService(session, registry, config).process(job_id)
            assert not await EmbeddingWorkerService(session, registry, config).process(job_id)
            job = await session.get(EmbeddingJobModel, job_id, populate_existing=True)
            assert job is not None
            assert job.status == "succeeded"
            assert job.embedded_count == 1
            assert len(provider.calls) == 1
            replay = await service.create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key=key,
                actor_id=ADMIN.subject_id,
            )
            assert replay.deduplicated
            second = await service.create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="new-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(second.job.id)
            assert second.job.deduplicated_count == 1
            assert len(provider.calls) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeEmbeddingModel)
                    .where(KnowledgeEmbeddingModel.knowledge_projection_id == projection_id)
                )
                == 1
            )

    asyncio.run(check())


def test_projection_selection_uses_the_existing_authorized_job_api(
    workspace_database_url: str,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await projection_source(session)

    _unit_id, projection_id, curriculum_id = asyncio.run(seed())
    provider = RecordingProvider()
    dispatcher = DeterministicEmbeddingDispatcher()
    path = f"/api/v1/admin/curricula/{curriculum_id}/embedding-jobs"
    body = {"knowledge_projection_ids": [str(projection_id)]}
    headers = {**ADMIN_HEADERS, "Idempotency-Key": "projection-api-" + str(uuid4())}
    with _client(
        Seed(workspace_database_url, "redis://127.0.0.1:1/0", {}), dispatcher, provider=provider
    ) as client:
        assert (
            client.post(
                path,
                headers={**REVIEWER_HEADERS, "Idempotency-Key": headers["Idempotency-Key"]},
                json=body,
            ).status_code
            == 403
        )
        created = client.post(path, headers=headers, json=body)
        assert created.status_code == 202
        assert created.json()["knowledge_projection_ids"] == [str(projection_id)]
        assert created.json()["counts"]["requested"] == 1
        replay = client.post(path, headers=headers, json=body)
        assert replay.status_code == 202
        assert replay.json()["id"] == created.json()["id"]
        assert replay.json()["deduplicated"] is True
        assert provider.calls == []


def test_projection_embedding_preserves_reported_provider_accounting(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        provider = AccountingProvider()
        registry = _registry(provider)
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            _unit_id, projection_id, curriculum_id = await projection_source(session)
            created = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="accounting-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(created.job.id)
            audit = await session.scalar(
                select(AdminAuditEventModel).where(
                    AdminAuditEventModel.resource_id == projection_id,
                    AdminAuditEventModel.action == "knowledge.projection.embedded",
                )
            )
            assert audit is not None
            assert audit.payload.get("accounting") == {
                "input_tokens": 17,
                "total_tokens": 17,
                "cost_microusd": 3,
                "latency_ms": 4,
            }

    asyncio.run(check())


def test_unreviewed_projection_cannot_trigger_a_provider_call(workspace_database_url: str) -> None:
    async def check() -> None:
        provider = RecordingProvider()
        async with database_session(workspace_database_url) as session:
            _unit_id, projection_id, curriculum_id = await projection_source(
                session, reviewed=False
            )
            service = EmbeddingJobService(
                session,
                _registry(provider),
                DeterministicEmbeddingDispatcher(),
                DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
            )
            with pytest.raises((EmbeddingSourceNotReviewedError, EmbeddingSourceNotFoundError)):
                await service.create(
                    curriculum_id,
                    historical_question_ids=(),
                    knowledge_chunk_ids=(),
                    knowledge_projection_ids=(projection_id,),
                    idempotency_key="unreviewed-" + str(uuid4()),
                    actor_id=ADMIN.subject_id,
                )
            assert provider.calls == []

    asyncio.run(check())


def test_a_new_positive_review_does_not_silently_rebind_an_enqueued_embedding(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        provider = RecordingProvider()
        registry = _registry(provider)
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, curriculum_id = await projection_source(session)
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="changed-review-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            previous = await session.scalar(
                select(KnowledgeUnitReviewModel).where(KnowledgeUnitReviewModel.unit_id == unit_id)
            )
            assert previous is not None
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=previous.competency_id,
                    reason="A new explicit mapping decision",
                ),
            )
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)
            await session.refresh(job.job)
            assert job.job.status == "failed"
            assert job.job.failure_code == "embedding_source_conflict"
            assert provider.calls == []

    asyncio.run(check())


def test_database_rejects_vector_hashes_not_bound_to_the_exact_projection(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        registry = _registry()
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            _unit_id, projection_id, curriculum_id = await projection_source(session)
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="hash-proof-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)
            existing = await session.scalar(
                select(KnowledgeEmbeddingModel).where(
                    KnowledgeEmbeddingModel.knowledge_projection_id == projection_id
                )
            )
            assert existing is not None
            session.add(
                KnowledgeEmbeddingModel(
                    id=uuid4(),
                    historical_question_id=None,
                    knowledge_chunk_id=None,
                    knowledge_projection_id=projection_id,
                    embedding_configuration_id=existing.embedding_configuration_id,
                    embedding_dimension=existing.embedding_dimension,
                    embedding=existing.vector,
                    source_text_sha256="f" * 64,
                    created_by=ADMIN.subject_id,
                )
            )
            with pytest.raises(IntegrityError, match="hash differs"):
                await session.flush()
            await session.rollback()
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeEmbeddingModel)
                    .where(KnowledgeEmbeddingModel.knowledge_projection_id == projection_id)
                )
                == 1
            )
            with pytest.raises(IntegrityError, match="immutable"):
                await session.execute(
                    update(KnowledgeEmbeddingModel)
                    .where(KnowledgeEmbeddingModel.knowledge_projection_id == projection_id)
                    .values(source_text_sha256=KnowledgeEmbeddingModel.source_text_sha256)
                )
            await session.rollback()

    asyncio.run(check())


@pytest.mark.parametrize("mismatch", ["curriculum", "review", "projection"])
def test_projection_embedding_lookup_cannot_cross_its_captured_scope(
    workspace_database_url: str, mismatch: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, curriculum_id = await projection_source(session)
            review = await session.scalar(
                select(KnowledgeUnitReviewModel).where(KnowledgeUnitReviewModel.unit_id == unit_id)
            )
            assert review is not None
            with pytest.raises(KnowledgeRecordNotFoundError):
                await KnowledgePersistenceService(session).projection_embedding_exists(
                    uuid4() if mismatch == "curriculum" else curriculum_id,
                    uuid4() if mismatch == "projection" else projection_id,
                    DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG,
                    expected_review_id=uuid4() if mismatch == "review" else review.id,
                )

    asyncio.run(check())


def test_partial_projection_work_reuses_finished_vectors_on_an_explicit_retry(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        provider = RecordingProvider(fail_call=2)
        registry = _registry(provider)
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, curriculum_id = await projection_source(session)
            review = await session.scalar(
                select(KnowledgeUnitReviewModel).where(KnowledgeUnitReviewModel.unit_id == unit_id)
            )
            assert review is not None
            projections = await KnowledgeUnitService(session).list_current_projections(
                principal=ADMIN, curriculum_version_id=curriculum_id
            )
            for projection in projections:
                if projection.unit_id != unit_id:
                    await KnowledgeUnitReviewService(session).review(
                        principal=ADMIN,
                        unit_id=projection.unit_id,
                        request=KnowledgeReviewRequest(
                            expected_version=0,
                            state="reviewed",
                            confirmed_mapping=True,
                            competency_id=review.competency_id,
                            reason="Review the second synthetic component",
                        ),
                    )
            service = EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            )
            first = await service.create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=tuple(item.id for item in projections),
                idempotency_key="partial-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(first.job.id)
            await session.refresh(first.job)
            assert first.job.status == "failed"
            assert first.job.embedded_count == 1
            assert len(provider.calls) == 2
            retry = await service.create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=tuple(item.id for item in projections),
                idempotency_key="retry-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert retry.job.retry_of_job_id == first.job.id
            assert await EmbeddingWorkerService(session, registry, config).process(retry.job.id)
            await session.refresh(retry.job)
            assert retry.job.status == "succeeded"
            assert retry.job.embedded_count == 1
            assert retry.job.deduplicated_count == 1
            assert len(provider.calls) == 3

    asyncio.run(check())


def test_review_change_between_snapshot_and_source_lock_is_not_billed(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        provider = RecordingProvider()
        registry = _registry(provider)
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, curriculum_id = await projection_source(session)
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="lock-race-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            original = KnowledgePersistenceService.projection_embedding_exists

            async def changed(this: KnowledgePersistenceService, *args: Any, **kwargs: Any) -> bool:
                async with database_session(workspace_database_url) as another:
                    await KnowledgeUnitReviewService(another).review(
                        principal=ADMIN,
                        unit_id=unit_id,
                        request=KnowledgeReviewRequest(
                            expected_version=1,
                            state="rejected",
                            confirmed_mapping=False,
                            reason="Withdraw before source locks are acquired",
                        ),
                    )
                return await original(this, *args, **kwargs)

            monkeypatch.setattr(KnowledgePersistenceService, "projection_embedding_exists", changed)
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)
            await session.refresh(job.job)
            assert job.job.failure_code == "embedding_source_conflict"
            assert provider.calls == []

    asyncio.run(check())


def test_projection_job_target_identity_is_immutable_during_a_valid_claim(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        registry = _registry()
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            _unit_id, projection_id, curriculum_id = await projection_source(session)
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="request-identity-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            job_id, version = job.job.id, job.job.version
            now = datetime.now(UTC)
            with pytest.raises(IntegrityError, match="projection request identity"):
                await session.execute(
                    update(EmbeddingJobModel)
                    .where(EmbeddingJobModel.id == job_id)
                    .values(
                        knowledge_projection_ids=[str(uuid4())],
                        status="claimed",
                        version=version + 1,
                        claimed_at=now,
                        updated_at=now,
                    )
                )
            await session.rollback()
            assert await EmbeddingWorkerService(session, registry, config).process(job_id)

    asyncio.run(check())


def test_revoked_mapping_after_enqueue_blocks_projection_embedding(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        provider = RecordingProvider()
        registry = _registry(provider)
        config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, curriculum_id = await projection_source(session)
            created = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection_id,),
                idempotency_key="revoked-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw mapping before the worker can bill it",
                ),
            )
            assert await EmbeddingWorkerService(session, registry, config).process(created.job.id)
            await session.refresh(created.job)
            assert created.job.status == "failed"
            assert created.job.failure_code == "embedding_source_invalid"
            assert provider.calls == []

    asyncio.run(check())
