import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import anyio
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.resources import create_resources
from exam_guru_api.knowledge.embedding_job_service import EmbeddingDispatcher
from exam_guru_api.knowledge.embedding_jobs import ingest_embeddings
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import promote_material_index_intent
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.retrieval.embeddings import (
    EmbeddingProviderRegistry,
    create_embedding_provider_registry,
)

MATERIAL_INDEX_QUEUE_NAME = "material-knowledge-indexing"
MATERIAL_INDEX_TIME_LIMIT_MS = 120_000
_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaterialIndexRecoveryResult:
    scanned: int
    processed: int
    failures: int


async def recover_material_indexing(
    session: AsyncSession,
    *,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    dispatcher: EmbeddingDispatcher,
    batch_size: int = 8,
    min_age_seconds: int = 5,
) -> MaterialIndexRecoveryResult:
    if type(batch_size) is not int or not 1 <= batch_size <= 8:
        raise ValueError("material indexing recovery limit must be between one and eight")
    if type(min_age_seconds) is not int or not 0 <= min_age_seconds <= 3600:
        raise ValueError("material indexing recovery age must be bounded")
    with anyio.fail_after(90):
        await session.execute(text("SET LOCAL statement_timeout = '30s'"))
        identifiers = tuple(
            await session.scalars(
                select(MaterialKnowledgeIndexIntentModel.id)
                .where(
                    MaterialKnowledgeIndexIntentModel.updated_at
                    <= datetime.now(UTC) - timedelta(seconds=min_age_seconds),
                    or_(
                        MaterialKnowledgeIndexIntentModel.status.in_(
                            ("pending", "waiting_configuration", "dispatching")
                        ),
                        and_(
                            MaterialKnowledgeIndexIntentModel.status == "queued",
                            select(EmbeddingJobModel.id)
                            .where(
                                EmbeddingJobModel.id
                                == MaterialKnowledgeIndexIntentModel.embedding_job_id,
                                EmbeddingJobModel.status.in_(("succeeded", "failed")),
                            )
                            .exists(),
                        ),
                        and_(
                            MaterialKnowledgeIndexIntentModel.status != "superseded",
                            func.knowledge_unit_review_is_eligible(
                                MaterialKnowledgeIndexIntentModel.review_id
                            ).is_(False),
                        ),
                    ),
                )
                .order_by(
                    MaterialKnowledgeIndexIntentModel.updated_at,
                    MaterialKnowledgeIndexIntentModel.id,
                )
                .limit(batch_size)
            )
        )
        await session.commit()
        processed = failures = 0
        for identifier in identifiers:
            try:
                await promote_material_index_intent(
                    session,
                    identifier,
                    settings=settings,
                    providers=providers,
                    dispatcher=dispatcher,
                )
                processed += 1
            except Exception:
                await session.rollback()
                failures += 1
                _logger.error("material knowledge indexing recovery failed")
        return MaterialIndexRecoveryResult(len(identifiers), processed, failures)


class _QueuedMessage(Protocol):
    @property
    def message_id(self) -> str: ...


class _EmbeddingBroker(Protocol):
    def enqueue(self, message: dramatiq.Message[Any]) -> _QueuedMessage: ...


class MaterialEmbeddingDispatcher:
    def __init__(self, broker: _EmbeddingBroker) -> None:
        self.broker = broker

    def dispatch(self, job_id: UUID) -> str:
        return self.broker.enqueue(ingest_embeddings.message(str(job_id))).message_id


async def _recover_material_indexing() -> None:
    settings = Settings()
    resources = create_resources(settings)
    broker = None
    try:
        broker = RedisBroker(
            url=settings.valkey_url.get_secret_value(),
            socket_connect_timeout=5,
            socket_timeout=5,
            retry=Retry(NoBackoff(), 0),
        )
        broker.declare_actor(ingest_embeddings)
        async with resources.session_factory() as session:
            await recover_material_indexing(
                session,
                settings=settings,
                providers=create_embedding_provider_registry(settings),
                dispatcher=MaterialEmbeddingDispatcher(broker),
            )
    finally:
        try:
            if broker is not None:
                broker.close()
        finally:
            await resources.close()


@dramatiq.actor(
    queue_name=MATERIAL_INDEX_QUEUE_NAME, max_retries=0, time_limit=MATERIAL_INDEX_TIME_LIMIT_MS
)
def recover_material_knowledge_indexing() -> None:
    asyncio.run(_recover_material_indexing())
