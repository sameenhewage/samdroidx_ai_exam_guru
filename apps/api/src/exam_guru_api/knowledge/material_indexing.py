import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4, uuid5

from pydantic import Field, field_validator
from sqlalchemy import func, literal, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH
from exam_guru_api.documents.fidelity_service import _reason
from exam_guru_api.documents.understanding_contracts import UnderstandingModel, _canonical_json
from exam_guru_api.knowledge.embedding_job_repository import SqlAlchemyEmbeddingJobRepository
from exam_guru_api.knowledge.embedding_job_service import (
    _EMBEDDING_JOB_NAMESPACE,
    EmbeddingDispatcher,
    EmbeddingJobService,
    EmbeddingQueueUnavailableError,
    EmbeddingRetryLimitExceededError,
    _fingerprint,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
    create_active_embedding_config,
)
from exam_guru_api.retrieval.repository import validate_embedding_config

MAX_MATERIAL_INDEX_ATTEMPTS = 4
_STOPPED = frozenset(
    {"superseded", "not_searchable", "needs_attention", "configuration_changed", "ready"}
)
_KNOWN_FAILURES = frozenset(
    {
        "embedding_config_unavailable",
        "embedding_config_conflict",
        "embedding_source_invalid",
        "embedding_source_conflict",
        "embedding_contract_error",
    }
)


class MaterialKnowledgeError(ValueError):
    pass


class MaterialKnowledgeNotFoundError(LookupError):
    pass


class MaterialKnowledgeIndexingStatus(UnderstandingModel):
    intent_id: UUID | None
    version: int | None
    status: Literal[
        "not_requested",
        "waiting_configuration",
        "pending",
        "queued",
        "ready",
        "needs_attention",
        "superseded",
        "not_searchable",
        "configuration_changed",
    ]
    ready: bool
    retry_allowed: bool


class MaterialKnowledgeIndexRetryRequest(UnderstandingModel):
    expected_version: int = Field(ge=0, le=2_147_483_645)
    reason: str = Field(min_length=1, max_length=2000)
    confirmed_retry: bool

    @field_validator("reason")
    @classmethod
    def retry_reason(cls, value: str) -> str:
        return _reason(value)


def material_embedding_config(
    settings: Settings,
    providers: EmbeddingProviderRegistry,
) -> EmbeddingConfig | None:
    settings = Settings.model_validate(settings.model_dump(exclude_unset=True))
    identity = settings.test_runtime_id
    isolated = (
        settings.environment == "test"
        and identity is not None
        and re.fullmatch(r"ai-exam-guru-e2e-[a-z0-9][a-z0-9-]{0,47}", identity) is not None
    )
    if settings.retrieval_embedding_provider in {None, "deterministic"} and not isolated:
        return None
    try:
        config = create_active_embedding_config(settings)
        if config.provider == "deterministic" and not isolated:
            return None
        if config != providers.active_config:
            return None
        providers.ensure_provider(config)
        return config
    except (ActiveEmbeddingConfigUnavailableError, EmbeddingProviderUnavailableError):
        return None


def intent_fingerprint(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def bound_config(intent: MaterialKnowledgeIndexIntentModel) -> EmbeddingConfig | None:
    if intent.config_snapshot is None:
        return None
    snapshot = intent.config_snapshot
    if set(snapshot) != {"provider", "model", "dimension", "version", "config_fingerprint"}:
        raise MaterialKnowledgeError("material_indexing_binding_invalid")
    if intent.config_snapshot_fingerprint != intent_fingerprint(snapshot):
        raise MaterialKnowledgeError("material_indexing_binding_invalid")
    if type(snapshot["dimension"]) is not int or any(
        not isinstance(snapshot[key], str)
        for key in ("provider", "model", "version", "config_fingerprint")
    ):
        raise MaterialKnowledgeError("material_indexing_binding_invalid")
    return validate_embedding_config(
        EmbeddingConfig(
            provider=cast(str, snapshot["provider"]),
            model=cast(str, snapshot["model"]),
            dimension=snapshot["dimension"],
            version=cast(str, snapshot["version"]),
            config_fingerprint=cast(str, snapshot["config_fingerprint"]),
        )
    )


def index_audit(intent: MaterialKnowledgeIndexIntentModel) -> AdminAuditEventModel:
    payload: dict[str, object] = {
        "intent_id": str(intent.id),
        "document_id": str(intent.document_id),
        "unit_id": str(intent.unit_id),
        "review_id": str(intent.review_id),
        "review_version": intent.review_version,
        "review_fingerprint": intent.review_fingerprint,
        "projection_id": None if intent.projection_id is None else str(intent.projection_id),
        "input_fingerprint": intent.input_fingerprint,
        "requested_by": str(intent.requested_by),
        "status": intent.status,
        "version": intent.version,
        "attempt_number": intent.attempt_number,
        "dispatch_key": intent.dispatch_key,
        "config_snapshot": intent.config_snapshot,
        "config_snapshot_fingerprint": intent.config_snapshot_fingerprint,
        "embedding_job_id": None
        if intent.embedding_job_id is None
        else str(intent.embedding_job_id),
        "failure_code": intent.failure_code,
        "event": intent.event,
        "reason": intent.reason,
        "confirmed_retry": intent.confirmed_retry,
        "previous_audit_event_id": None
        if intent.previous_audit_event_id is None
        else str(intent.previous_audit_event_id),
    }
    return AdminAuditEventModel(
        id=uuid4(),
        actor_id=intent.updated_by,
        resource_type="material_knowledge_index",
        resource_id=intent.unit_id,
        action="material_knowledge_index." + intent.event,
        payload=payload,
    )


async def index_transition(
    session: AsyncSession,
    intent: MaterialKnowledgeIndexIntentModel,
    status: str,
    *,
    event: str = "observed",
    reason: str = "Reconciled the durable indexing intent",
    actor_id: UUID | None = None,
    config: EmbeddingConfig | None = None,
    job_id: UUID | None = None,
    failure_code: str | None = None,
) -> None:
    intent.previous_audit_event_id = intent.audit_event_id
    intent.version += 1
    intent.status, intent.event, intent.reason = status, event, reason
    intent.updated_at = datetime.now(UTC)
    intent.updated_by = actor_id or intent.requested_by
    intent.confirmed_retry = event == "retry_approved"
    intent.failure_code = failure_code
    if event in {"dispatch_bound", "retry_approved"}:
        if config is None:
            raise MaterialKnowledgeError("material_indexing_configuration_required")
        intent.attempt_number += 1
        intent.dispatch_key = f"material-knowledge:{intent.id}:attempt:{intent.attempt_number}"
        intent.config_snapshot = asdict(config)
        intent.config_snapshot_fingerprint = intent_fingerprint(intent.config_snapshot)
        intent.embedding_job_id = None
    elif event == "linked":
        intent.embedding_job_id = job_id
    audit = index_audit(intent)
    intent.audit_event_id = audit.id
    session.add(audit)
    await session.flush()


async def source_lock(session: AsyncSession, document_id: UUID) -> None:
    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    await session.execute(select(func.lock_knowledge_unit_source(document_id)))


async def locked_intent(
    session: AsyncSession, intent_id: UUID
) -> MaterialKnowledgeIndexIntentModel:
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    identity = (
        await session.execute(
            select(
                MaterialKnowledgeIndexIntentModel.document_id,
                MaterialKnowledgeIndexIntentModel.projection_id,
                MaterialKnowledgeIndexIntentModel.review_id,
            ).where(MaterialKnowledgeIndexIntentModel.id == intent_id)
        )
    ).one_or_none()
    if identity is None:
        raise MaterialKnowledgeNotFoundError("material_knowledge_intent_not_found")
    await source_lock(session, identity.document_id)
    if identity.projection_id is not None:
        await session.execute(
            select(
                func.lock_projection_embedding_source(identity.projection_id, identity.review_id)
            )
        )
    intent = await session.scalar(
        select(MaterialKnowledgeIndexIntentModel)
        .where(MaterialKnowledgeIndexIntentModel.id == intent_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if intent is None:
        raise MaterialKnowledgeNotFoundError("material_knowledge_intent_not_found")
    if intent.input_fingerprint != intent_fingerprint(intent.input_snapshot):
        raise MaterialKnowledgeError("material_indexing_binding_invalid")
    return intent


async def index_current(session: AsyncSession, intent: MaterialKnowledgeIndexIntentModel) -> bool:
    return bool(
        await session.scalar(select(func.knowledge_unit_review_is_eligible(intent.review_id)))
    )


async def current_vector(
    session: AsyncSession, projection_id: UUID | None, config: EmbeddingConfig | None
) -> bool:
    if projection_id is None or config is None:
        return False
    return bool(
        await session.scalar(
            select(
                func.material_index_has_vector(projection_id, literal(asdict(config), type_=JSONB))
            )
        )
    )


async def bound_job(
    session: AsyncSession, intent: MaterialKnowledgeIndexIntentModel
) -> EmbeddingJobModel | None:
    if intent.dispatch_key is None:
        if intent.embedding_job_id is not None:
            raise MaterialKnowledgeError("material_indexing_binding_invalid")
        return None
    key_hash = _fingerprint(intent.dispatch_key)
    job = await SqlAlchemyEmbeddingJobRepository(session).get_idempotent_job(
        actor_id=intent.requested_by,
        idempotency_key_hash=key_hash,
    )
    if job is None:
        if intent.embedding_job_id is not None:
            raise MaterialKnowledgeError("material_indexing_binding_invalid")
        return None
    if (
        job.id != uuid5(_EMBEDDING_JOB_NAMESPACE, f"{intent.requested_by}\0{key_hash}")
        or (intent.embedding_job_id is not None and intent.embedding_job_id != job.id)
        or job.embedding_config != bound_config(intent)
        or not await session.scalar(
            text(
                "SELECT public.material_index_job_matches(i,:job_id) "
                "FROM material_knowledge_index_intents i WHERE i.id=:id"
            ),
            {"id": intent.id, "job_id": job.id},
        )
    ):
        raise MaterialKnowledgeError("material_indexing_binding_invalid")
    return job


async def retry_allowed(
    session: AsyncSession,
    intent: MaterialKnowledgeIndexIntentModel,
    config: EmbeddingConfig | None,
    job: EmbeddingJobModel | None,
) -> bool:
    if config is None or intent.attempt_number >= MAX_MATERIAL_INDEX_ATTEMPTS:
        return False
    if job is not None and job.status == "failed":
        latest = await SqlAlchemyEmbeddingJobRepository(session).latest_failed_retry(
            curriculum_version_id=intent.curriculum_version_id,
            actor_id=intent.requested_by,
            request_fingerprint=job.request_fingerprint,
        )
        if (
            job.failure_code not in _KNOWN_FAILURES
            or job.retry_depth >= MAX_PROVIDER_JOB_RETRY_DEPTH
        ):
            return False
        if latest is not None and latest.retry_depth >= MAX_PROVIDER_JOB_RETRY_DEPTH:
            return False
    return bool(
        await session.scalar(
            text(
                "SELECT public.material_index_retryable(i,CAST(:config AS jsonb)) "
                "FROM material_knowledge_index_intents i WHERE i.id=:id"
            ),
            {"id": intent.id, "config": json.dumps(asdict(config))},
        )
    )


async def indexing_status(
    session: AsyncSession,
    *,
    intent: MaterialKnowledgeIndexIntentModel | None,
    review_id: UUID | None,
    eligible: bool,
    projection_id: UUID | None,
    config: EmbeddingConfig | None,
) -> MaterialKnowledgeIndexingStatus:
    ready = eligible and await current_vector(session, projection_id, config)
    status: str = "not_requested"
    can_retry = False
    if ready:
        status = "ready"
    elif intent is not None:
        if intent.status == "superseded" or intent.review_id != review_id or not eligible:
            status = "superseded"
        elif intent.projection_id is None:
            status = "not_searchable"
        elif config is None:
            status = "waiting_configuration"
        else:
            job = await bound_job(session, intent)
            pinned = bound_config(intent)
            if pinned is not None and pinned != config:
                status = "configuration_changed"
            elif job is not None:
                status = "queued" if job.status in {"queued", "claimed"} else "needs_attention"
            elif intent.status in {"needs_attention", "configuration_changed"}:
                status = intent.status
            else:
                status = "pending"
            can_retry = await retry_allowed(session, intent, config, job)
    return MaterialKnowledgeIndexingStatus.model_validate(
        {
            "intent_id": None if intent is None else intent.id,
            "version": None if intent is None else intent.version,
            "status": status,
            "ready": ready,
            "retry_allowed": can_retry,
        }
    )


def _job_status(job: EmbeddingJobModel) -> tuple[str, str | None]:
    if job.status in {"queued", "claimed"}:
        return "queued", None
    return "needs_attention", (
        "indexing_job_failed"
        if job.status == "failed" and job.failure_code in _KNOWN_FAILURES
        else "indexing_outcome_unknown"
    )


async def _observe_job(
    session: AsyncSession,
    intent: MaterialKnowledgeIndexIntentModel,
    job: EmbeddingJobModel,
    *,
    eligible: bool,
) -> None:
    if not eligible or intent.status == "superseded":
        status, code = "superseded", "indexing_source_superseded"
    elif await current_vector(session, intent.projection_id, bound_config(intent)):
        status, code = "ready", None
    else:
        status, code = _job_status(job)
    if intent.embedding_job_id is None:
        await index_transition(
            session, intent, status, event="linked", job_id=job.id, failure_code=code
        )
    elif intent.status != status or intent.failure_code != code:
        await index_transition(session, intent, status, failure_code=code)
    await session.commit()


async def promote_material_index_intent(
    session: AsyncSession,
    intent_id: UUID,
    *,
    settings: Settings,
    providers: EmbeddingProviderRegistry,
    dispatcher: EmbeddingDispatcher,
) -> None:
    try:
        intent = await locked_intent(session, intent_id)
        job = await bound_job(session, intent)
        eligible = await index_current(session, intent)
        if job is not None:
            await _observe_job(session, intent, job, eligible=eligible)
            return
        if intent.status == "superseded":
            await session.commit()
            return
        if not eligible:
            await index_transition(
                session,
                intent,
                "superseded",
                event="superseded",
                failure_code="indexing_source_superseded",
            )
            await session.commit()
            return
        if intent.status in _STOPPED:
            await session.commit()
            return
        config = material_embedding_config(settings, providers)
        if config is None:
            await index_transition(
                session, intent, "waiting_configuration", failure_code="configuration_unavailable"
            )
            await session.commit()
            return
        pinned = bound_config(intent)
        if pinned is not None and pinned != config:
            await index_transition(
                session, intent, "configuration_changed", failure_code="configuration_changed"
            )
            await session.commit()
            return
        if pinned is None:
            await index_transition(
                session, intent, "dispatching", event="dispatch_bound", config=config
            )
            await session.commit()
            intent = await locked_intent(session, intent_id)
            job = await bound_job(session, intent)
            eligible = await index_current(session, intent)
            if job is not None:
                await _observe_job(session, intent, job, eligible=eligible)
                return
            if not eligible or intent.status == "superseded":
                if intent.status != "superseded":
                    await index_transition(
                        session,
                        intent,
                        "superseded",
                        event="superseded",
                        failure_code="indexing_source_superseded",
                    )
                await session.commit()
                return
            pinned = bound_config(intent)
        if intent.status in _STOPPED:
            await session.commit()
            return
        if pinned != config or intent.dispatch_key is None or intent.projection_id is None:
            raise MaterialKnowledgeError("material_indexing_binding_invalid")
        if await current_vector(session, intent.projection_id, config):
            await index_transition(session, intent, "ready")
            await session.commit()
            return
        if intent.status != "dispatching":
            await index_transition(session, intent, "dispatching")
            await session.commit()
            intent = await locked_intent(session, intent_id)
            if not await index_current(session, intent):
                await index_transition(
                    session,
                    intent,
                    "superseded",
                    event="superseded",
                    failure_code="indexing_source_superseded",
                )
                await session.commit()
                return
        job = await bound_job(session, intent)
        if job is not None:
            await _observe_job(session, intent, job, eligible=await index_current(session, intent))
            return
        if intent.status in _STOPPED:
            await session.commit()
            return
        if (
            intent.dispatch_key is None
            or intent.projection_id is None
            or bound_config(intent) != pinned
        ):
            raise MaterialKnowledgeError("material_indexing_binding_invalid")
        binding = (intent.attempt_number, intent.dispatch_key, intent.config_snapshot_fingerprint)
        try:
            await EmbeddingJobService(session, providers, dispatcher, pinned).create(
                intent.curriculum_version_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(intent.projection_id,),
                idempotency_key=intent.dispatch_key,
                actor_id=intent.requested_by,
            )
        except EmbeddingQueueUnavailableError:
            pass
        except EmbeddingRetryLimitExceededError:
            await session.rollback()
            intent = await locked_intent(session, intent_id)
            if (
                intent.attempt_number,
                intent.dispatch_key,
                intent.config_snapshot_fingerprint,
            ) == binding:
                await index_transition(
                    session, intent, "needs_attention", failure_code="indexing_retry_exhausted"
                )
                await session.commit()
            return
        intent = await locked_intent(session, intent_id)
        if (
            intent.attempt_number,
            intent.dispatch_key,
            intent.config_snapshot_fingerprint,
        ) != binding:
            raise MaterialKnowledgeError("material_indexing_version_conflict")
        job = await bound_job(session, intent)
        if job is None:
            raise MaterialKnowledgeError("material_indexing_binding_invalid")
        await _observe_job(session, intent, job, eligible=await index_current(session, intent))
    except Exception:
        await session.rollback()
        raise
