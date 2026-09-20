import asyncio
import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid5

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.core.provider_jobs import MAX_PROVIDER_JOB_RETRY_DEPTH
from exam_guru_api.knowledge import material_indexing
from exam_guru_api.knowledge.embedding_job_service import (
    _EMBEDDING_JOB_NAMESPACE,
    EmbeddingJobCreationResult,
    EmbeddingJobService,
    EmbeddingQueueUnavailableError,
    EmbeddingRetryLimitExceededError,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import DeterministicEmbeddingProvider, EmbeddingConfig
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import (
    MaterialKnowledgeError,
    MaterialKnowledgeNotFoundError,
)
from exam_guru_api.knowledge.models import EmbeddingJobModel
from exam_guru_api.retrieval.domain import RetrievalContractError
from exam_guru_api.retrieval.embeddings import (
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
    create_active_embedding_config,
)
from tests.test_retrieval_embedding_providers import openai_embedding_settings


@dataclass(frozen=True)
class _Runtime:
    settings: Settings
    config: EmbeddingConfig
    providers: EmbeddingProviderRegistry
    provider: Mock
    dispatcher: DeterministicEmbeddingDispatcher


@pytest.fixture
def runtime() -> Iterator[_Runtime]:
    settings = Settings(
        environment="test",
        test_runtime_id="ai-exam-guru-e2e-index-boundaries",
        retrieval_embedding_provider="deterministic",
    )
    config = create_active_embedding_config(settings)
    provider = Mock(spec=DeterministicEmbeddingProvider)
    provider.embed.side_effect = AssertionError("Index coordination must not embed inline")
    value = _Runtime(
        settings,
        config,
        EmbeddingProviderRegistry({"deterministic": provider}, active_config=config),
        provider,
        DeterministicEmbeddingDispatcher(),
    )
    yield value
    provider.embed.assert_not_called()
    assert value.dispatcher.dispatched == []


@pytest.fixture
def create_job(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime) -> AsyncMock:
    service = EmbeddingJobService(
        AsyncMock(spec=AsyncSession), runtime.providers, runtime.dispatcher, runtime.config
    )
    create = AsyncMock(spec=service.create)
    monkeypatch.setattr(EmbeddingJobService, "create", create)
    return create


def _digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _set_binding(
    intent: MaterialKnowledgeIndexIntentModel,
    config: EmbeddingConfig,
    *,
    attempt: int = 1,
) -> None:
    intent.attempt_number = attempt
    intent.dispatch_key = f"material-knowledge:{intent.id}:attempt:{attempt}"
    intent.config_snapshot = asdict(config)
    intent.config_snapshot_fingerprint = _digest(intent.config_snapshot)


def _intent(
    config: EmbeddingConfig | None = None,
    *,
    status: str | None = None,
    searchable: bool = True,
) -> MaterialKnowledgeIndexIntentModel:
    document_id, unit_id, review_id, curriculum_id = uuid4(), uuid4(), uuid4(), uuid4()
    projection_id = uuid4() if searchable else None
    actor_id = uuid4()
    snapshot: dict[str, object] = {
        "schema_version": "material-knowledge-index-input.v1",
        "document_id": str(document_id),
        "unit_id": str(unit_id),
        "review_id": str(review_id),
        "review_version": 1,
        "review_fingerprint": "a" * 64,
        "curriculum_version_id": str(curriculum_id),
        "projection_id": None if projection_id is None else str(projection_id),
    }
    now = datetime.now(UTC)
    intent = MaterialKnowledgeIndexIntentModel(
        id=uuid4(),
        document_id=document_id,
        unit_id=unit_id,
        review_id=review_id,
        review_version=1,
        review_fingerprint="a" * 64,
        curriculum_version_id=curriculum_id,
        projection_id=projection_id,
        input_snapshot=snapshot,
        input_fingerprint=_digest(snapshot),
        requested_by=actor_id,
        status=status or ("dispatching" if config else "pending"),
        version=1 if config else 0,
        attempt_number=0,
        dispatch_key=None,
        config_snapshot=None,
        config_snapshot_fingerprint=None,
        embedding_job_id=None,
        failure_code=None,
        event="dispatch_bound" if config else "requested",
        reason="Explicit review of synthetic curriculum evidence",
        confirmed_retry=False,
        updated_by=actor_id,
        previous_audit_event_id=uuid4() if config else None,
        audit_event_id=uuid4(),
        created_at=now,
        updated_at=now,
    )
    if config is not None:
        _set_binding(intent, config)
    return intent


def _job(
    intent: MaterialKnowledgeIndexIntentModel,
    config: EmbeddingConfig,
    *,
    status: str = "queued",
    failure_code: str | None = None,
    retry_depth: int = 0,
    idempotency_key: str | None = None,
) -> EmbeddingJobModel:
    key = idempotency_key or intent.dispatch_key or f"material-knowledge:{intent.id}:attempt:1"
    key_hash = "sha256:" + hashlib.sha256(json.dumps(key).encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    return EmbeddingJobModel(
        id=uuid5(_EMBEDDING_JOB_NAMESPACE, f"{intent.requested_by}\0{key_hash}"),
        curriculum_version_id=intent.curriculum_version_id,
        retry_of_job_id=uuid4() if retry_depth else None,
        retry_depth=retry_depth,
        historical_question_ids=[],
        knowledge_chunk_ids=[],
        knowledge_projection_ids=[str(intent.projection_id)],
        idempotency_key_hash=key_hash,
        request_fingerprint="sha256:" + "b" * 64,
        source_fingerprint="sha256:" + "c" * 64,
        provider=config.provider,
        model=config.model,
        dimension=config.dimension,
        embedding_version=config.version,
        config_fingerprint=config.config_fingerprint,
        status=status,
        version=0,
        queue_message_id=None,
        requested_count=1,
        embedded_count=1 if status == "succeeded" else 0,
        deduplicated_count=0,
        failure_code=failure_code,
        created_by=intent.requested_by,
        created_at=now,
        updated_at=now,
        claimed_at=None if status == "queued" else now,
        completed_at=now if status in {"failed", "succeeded"} else None,
    )


def _session(intent: MaterialKnowledgeIndexIntentModel, *scalar_results: object) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = Mock()
    session.execute.return_value.one_or_none.return_value = SimpleNamespace(
        document_id=intent.document_id,
        projection_id=intent.projection_id,
        review_id=intent.review_id,
    )
    session.scalar.side_effect = scalar_results
    return session


def _promote(
    session: AsyncMock, intent: MaterialKnowledgeIndexIntentModel, runtime: _Runtime
) -> None:
    asyncio.run(
        material_indexing.promote_material_index_intent(
            session,
            intent.id,
            settings=runtime.settings,
            providers=runtime.providers,
            dispatcher=runtime.dispatcher,
        )
    )


def _audits(session: AsyncMock) -> list[AdminAuditEventModel]:
    result: list[AdminAuditEventModel] = []
    for added in session.add.call_args_list:
        row = added.args[0]
        assert isinstance(row, AdminAuditEventModel)
        result.append(row)
    return result


def _after_first_commit(session: AsyncMock, change: Callable[[], None]) -> None:
    commits = 0

    def commit() -> None:
        nonlocal commits
        commits += 1
        if commits == 1:
            change()

    session.commit.side_effect = commit


def test_intent_fingerprint_is_order_independent_but_preserves_source_content() -> None:
    first: dict[str, object] = {"source": "සිංහල", "revision": {"b": 2, "a": 1}}
    reordered: dict[str, object] = {"revision": {"a": 1, "b": 2}, "source": "සිංහල"}
    assert material_indexing.intent_fingerprint(first) == _digest(first)
    assert material_indexing.intent_fingerprint(reordered) == _digest(first)
    assert material_indexing.intent_fingerprint({**first, "source": "සිංහල "}) != _digest(first)


@pytest.mark.parametrize("registered_config", ["absent", "different"])
def test_material_config_rejects_missing_or_mismatched_active_registry(
    runtime: _Runtime, registered_config: str
) -> None:
    config = None if registered_config == "absent" else replace(runtime.config, version="v2")
    providers = EmbeddingProviderRegistry({"deterministic": runtime.provider}, active_config=config)
    assert material_indexing.material_embedding_config(runtime.settings, providers) is None
    assert (
        material_indexing.material_embedding_config(runtime.settings, runtime.providers)
        == runtime.config
    )


def test_material_config_rejects_a_factory_result_contradicting_explicit_provider(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = openai_embedding_settings()
    factory = Mock(return_value=runtime.config)
    preflight = Mock()
    monkeypatch.setattr(material_indexing, "create_active_embedding_config", factory)
    monkeypatch.setattr(runtime.providers, "ensure_provider", preflight)
    assert settings.retrieval_embedding_provider == "openai"
    assert runtime.config.provider == "deterministic"
    assert material_indexing.material_embedding_config(settings, runtime.providers) is None
    factory.assert_called_once_with(settings)
    preflight.assert_not_called()


def test_material_config_fails_closed_when_registry_preflight_is_unavailable(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    preflight = Mock(side_effect=EmbeddingProviderUnavailableError())
    monkeypatch.setattr(runtime.providers, "ensure_provider", preflight)
    assert material_indexing.material_embedding_config(runtime.settings, runtime.providers) is None
    preflight.assert_called_once_with(runtime.config)


@pytest.mark.parametrize(
    "changes",
    [
        {"test_runtime_id": "unattested-runtime"},
        {"retrieval_embedding_dimension": 4097},
    ],
)
def test_material_config_revalidates_copied_settings_before_using_registry(
    runtime: _Runtime, changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        material_indexing.material_embedding_config(
            runtime.settings.model_copy(update=changes), runtime.providers
        )


@pytest.mark.parametrize("dimension", [1, 4096])
def test_bound_config_accepts_exact_valid_dimension_boundaries(
    runtime: _Runtime, dimension: int
) -> None:
    config = replace(runtime.config, dimension=dimension)
    assert material_indexing.bound_config(_intent(config)) == config
    assert material_indexing.bound_config(_intent()) is None


@pytest.mark.parametrize(
    ("changes", "removed", "rehash", "error"),
    [
        pytest.param({}, "dimension", True, MaterialKnowledgeError, id="missing-field"),
        pytest.param({"extra": "unbound"}, None, True, MaterialKnowledgeError, id="extra-field"),
        pytest.param({"model": "changed"}, None, False, MaterialKnowledgeError, id="changed-hash"),
        pytest.param(
            {"dimension": True}, None, True, MaterialKnowledgeError, id="boolean-dimension"
        ),
        pytest.param({"dimension": 32.0}, None, True, MaterialKnowledgeError, id="float-dimension"),
        pytest.param(
            {"dimension": "32"}, None, True, MaterialKnowledgeError, id="string-dimension"
        ),
        pytest.param({"dimension": None}, None, True, MaterialKnowledgeError, id="null-dimension"),
        pytest.param({"provider": None}, None, True, MaterialKnowledgeError, id="null-provider"),
        pytest.param({"model": 32}, None, True, MaterialKnowledgeError, id="integer-model"),
        pytest.param({"version": ["v1"]}, None, True, MaterialKnowledgeError, id="list-version"),
        pytest.param(
            {"config_fingerprint": False},
            None,
            True,
            MaterialKnowledgeError,
            id="boolean-fingerprint",
        ),
        pytest.param({"dimension": 0}, None, True, RetrievalContractError, id="zero-dimension"),
        pytest.param(
            {"dimension": 4097}, None, True, RetrievalContractError, id="oversized-dimension"
        ),
        pytest.param({"model": " "}, None, True, RetrievalContractError, id="blank-model"),
        pytest.param(
            {"version": "v1 "}, None, True, RetrievalContractError, id="untrimmed-version"
        ),
    ],
)
def test_corrupt_config_rolls_back_promotion_before_any_job_or_audit(
    runtime: _Runtime,
    create_job: AsyncMock,
    changes: dict[str, object],
    removed: str | None,
    rehash: bool,
    error: type[ValueError],
) -> None:
    intent = _intent(runtime.config)
    assert intent.config_snapshot is not None
    snapshot = {**intent.config_snapshot, **changes}
    if removed is not None:
        snapshot.pop(removed)
    intent.config_snapshot = snapshot
    if rehash:
        intent.config_snapshot_fingerprint = _digest(snapshot)
    original_version, original_key = intent.version, intent.dispatch_key
    session = _session(intent, intent, None, True)
    message = "binding_invalid" if error is MaterialKnowledgeError else "configuration is invalid"
    with pytest.raises(error, match=message):
        _promote(session, intent, runtime)
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.add.assert_not_called()
    create_job.assert_not_awaited()
    assert intent.version == original_version
    assert intent.attempt_number == 1
    assert intent.dispatch_key == original_key
    assert intent.embedding_job_id is None


@pytest.mark.parametrize("event", ["dispatch_bound", "retry_approved"])
def test_dispatch_transition_without_config_cannot_spend_attempt_or_write_audit(event: str) -> None:
    intent = _intent()
    session = _session(intent)
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_configuration_required"):
        asyncio.run(material_indexing.index_transition(session, intent, "dispatching", event=event))
    assert intent.attempt_number == 0
    assert intent.dispatch_key is None
    assert intent.config_snapshot is None
    assert intent.embedding_job_id is None
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("missing", ["identity", "locked-row", "input-digest"])
def test_missing_or_corrupt_locked_intent_rolls_back_without_dispatch(
    runtime: _Runtime, create_job: AsyncMock, missing: str
) -> None:
    intent = _intent()
    session = _session(intent, None if missing == "locked-row" else intent)
    if missing == "identity":
        session.execute.return_value.one_or_none.return_value = None
    elif missing == "input-digest":
        intent.input_snapshot = {**intent.input_snapshot, "review_version": 2}
    error = MaterialKnowledgeError if missing == "input-digest" else MaterialKnowledgeNotFoundError
    message = "binding_invalid" if missing == "input-digest" else "intent_not_found"
    with pytest.raises(error, match=message):
        _promote(session, intent, runtime)
    assert session.scalar.await_count == (0 if missing == "identity" else 1)
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("searchable", [False, True])
def test_locked_intent_uses_bounded_source_then_projection_locks_and_fresh_row(
    searchable: bool,
) -> None:
    intent = _intent(searchable=searchable, status="pending" if searchable else "not_searchable")
    session = _session(intent, intent)
    assert asyncio.run(material_indexing.locked_intent(session, intent.id)) is intent
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert statements[0] == "SET LOCAL statement_timeout = '30s'"
    assert statements[2:4] == [
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL statement_timeout = '30s'",
    ]
    assert "lock_knowledge_unit_source" in statements[4]
    assert len(statements) == (6 if searchable else 5)
    assert session.execute.await_args_list[4].args[0].compile().params == {
        "lock_knowledge_unit_source_2": intent.document_id
    }
    if searchable:
        assert "lock_projection_embedding_source" in statements[5]
        assert set(session.execute.await_args_list[5].args[0].compile().params.values()) == {
            intent.projection_id,
            intent.review_id,
        }
    query = session.scalar.await_args.args[0]
    assert "FOR UPDATE" in str(query)
    assert query.get_execution_options()["populate_existing"] is True
    assert query.compile().params == {"id_1": intent.id}
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "broken",
    ["link-without-key", "missing-job", "job-id", "linked-id", "job-config", "source-binding"],
)
def test_bound_job_rejects_orphaned_and_mismatched_identity_without_creating_replacement(
    runtime: _Runtime, create_job: AsyncMock, broken: str
) -> None:
    intent = _intent(runtime.config)
    job = _job(intent, runtime.config)
    if broken == "link-without-key":
        intent.dispatch_key = None
        intent.embedding_job_id = job.id
    elif broken == "missing-job":
        intent.embedding_job_id = job.id
    elif broken == "job-id":
        job.id = uuid4()
    elif broken == "linked-id":
        intent.embedding_job_id = uuid4()
    elif broken == "job-config":
        job.embedding_version = "v2"
    results: list[object] = [intent]
    if broken != "link-without-key":
        results.append(None if broken == "missing-job" else job)
    if broken == "source-binding":
        results.append(False)
    session = _session(intent, *results)
    original_link = intent.embedding_job_id
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_binding_invalid"):
        _promote(session, intent, runtime)
    assert session.scalar.await_count == len(results)
    assert intent.embedding_job_id == original_link
    assert intent.attempt_number == 1
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("linked", [False, True])
def test_bound_job_requires_actor_scoped_key_and_database_source_binding(
    runtime: _Runtime, linked: bool
) -> None:
    intent = _intent(runtime.config)
    job = _job(intent, runtime.config)
    if linked:
        intent.embedding_job_id = job.id
    session = _session(intent, job, True)
    assert asyncio.run(material_indexing.bound_job(session, intent)) is job
    lookup, binding = session.scalar.await_args_list
    assert lookup.args[0].compile().params == {
        "created_by_1": intent.requested_by,
        "idempotency_key_hash_1": job.idempotency_key_hash,
    }
    assert "material_index_job_matches" in str(binding.args[0])
    assert binding.args[1] == {"id": intent.id, "job_id": job.id}
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "latest_depth", [None, MAX_PROVIDER_JOB_RETRY_DEPTH - 1, MAX_PROVIDER_JOB_RETRY_DEPTH]
)
def test_retry_checks_latest_actor_request_depth_before_database_eligibility(
    runtime: _Runtime, latest_depth: int | None
) -> None:
    intent = _intent(runtime.config, status="needs_attention")
    job = _job(intent, runtime.config, status="failed", failure_code="embedding_contract_error")
    latest = (
        None
        if latest_depth is None
        else _job(
            intent,
            runtime.config,
            status="failed",
            failure_code="embedding_contract_error",
            retry_depth=latest_depth,
            idempotency_key=f"advanced-retry:{intent.id}",
        )
    )
    if latest is not None:
        assert latest.id != job.id
        assert latest.request_fingerprint == job.request_fingerprint
    exhausted = latest_depth == MAX_PROVIDER_JOB_RETRY_DEPTH
    session = _session(intent, *([latest] if exhausted else [latest, True]))
    assert (
        asyncio.run(material_indexing.retry_allowed(session, intent, runtime.config, job))
        is not exhausted
    )
    assert session.scalar.await_count == (1 if exhausted else 2)
    query = session.scalar.await_args_list[0].args[0]
    assert query.compile().params == {
        "curriculum_version_id_1": intent.curriculum_version_id,
        "created_by_1": intent.requested_by,
        "request_fingerprint_1": job.request_fingerprint,
        "status_1": "failed",
        "param_1": 1,
    }
    if not exhausted:
        call = session.scalar.await_args_list[1]
        assert "material_index_retryable" in str(call.args[0])
        assert call.args[1]["id"] == intent.id
        assert json.loads(call.args[1]["config"]) == asdict(runtime.config)
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "failure_code", ["worker_lease_expired", "embedding_internal_error", "embedding_contract_error"]
)
def test_unknown_or_exhausted_job_cannot_gain_retry_authority(
    runtime: _Runtime, failure_code: str
) -> None:
    intent = _intent(runtime.config, status="needs_attention")
    job = _job(
        intent,
        runtime.config,
        status="failed",
        failure_code=failure_code,
        retry_depth=MAX_PROVIDER_JOB_RETRY_DEPTH
        if failure_code == "embedding_contract_error"
        else 0,
    )
    session = _session(intent, None)
    assert (
        asyncio.run(material_indexing.retry_allowed(session, intent, runtime.config, job)) is False
    )
    session.scalar.assert_awaited_once()
    session.add.assert_not_called()


def test_pending_version_zero_status_remains_read_only_and_is_not_retry_authority(
    runtime: _Runtime,
) -> None:
    intent = _intent()
    session = _session(intent, False, False)
    result = asyncio.run(
        material_indexing.indexing_status(
            session,
            intent=intent,
            review_id=intent.review_id,
            eligible=True,
            projection_id=intent.projection_id,
            config=runtime.config,
        )
    )
    assert result.status == "pending"
    assert result.version == 0
    assert result.intent_id == intent.id
    assert result.ready is False
    assert result.retry_allowed is False
    assert session.scalar.await_count == 2
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.parametrize("enrolled", [False, True])
def test_current_advanced_vector_is_ready_without_needing_a_dispatched_intent(
    runtime: _Runtime, enrolled: bool
) -> None:
    intent = _intent()
    session = _session(intent, True)
    result = asyncio.run(
        material_indexing.indexing_status(
            session,
            intent=intent if enrolled else None,
            review_id=intent.review_id,
            eligible=True,
            projection_id=intent.projection_id,
            config=runtime.config,
        )
    )
    assert result.status == "ready"
    assert result.ready is True
    assert result.retry_allowed is False
    assert result.intent_id == (intent.id if enrolled else None)
    assert result.version == (0 if enrolled else None)
    session.scalar.assert_awaited_once()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("job_status", "failure_code", "eligible", "has_vector", "expected_status", "expected_failure"),
    [
        (
            "failed",
            "worker_lease_expired",
            True,
            False,
            "needs_attention",
            "indexing_outcome_unknown",
        ),
        (
            "failed",
            "embedding_internal_error",
            True,
            False,
            "needs_attention",
            "indexing_outcome_unknown",
        ),
        (
            "failed",
            "embedding_contract_error",
            True,
            False,
            "needs_attention",
            "indexing_job_failed",
        ),
        ("succeeded", None, True, False, "needs_attention", "indexing_outcome_unknown"),
        ("succeeded", None, True, True, "ready", None),
        ("claimed", None, True, False, "queued", None),
        ("queued", None, False, False, "superseded", "indexing_source_superseded"),
    ],
)
@pytest.mark.parametrize("linked", [False, True])
def test_reconciliation_preserves_bound_job_and_does_not_repeat_audit_or_provider_work(
    runtime: _Runtime,
    create_job: AsyncMock,
    job_status: str,
    failure_code: str | None,
    eligible: bool,
    has_vector: bool,
    expected_status: str,
    expected_failure: str | None,
    linked: bool,
) -> None:
    intent = _intent(runtime.config, status="queued" if linked else "dispatching")
    job = _job(intent, runtime.config, status=job_status, failure_code=failure_code)
    if linked:
        intent.embedding_job_id = job.id
    original_version = intent.version
    original_audit = intent.audit_event_id
    results: list[object] = [intent, job, True, eligible]
    if eligible:
        results.append(has_vector)
    session = _session(intent, *results)
    _promote(session, intent, runtime)
    assert intent.status == expected_status
    assert intent.failure_code == expected_failure
    assert intent.embedding_job_id == job.id
    assert intent.attempt_number == 1
    audits = _audits(session)
    changed = not linked or expected_status != "queued"
    assert len(audits) == int(changed)
    assert intent.version == original_version + int(changed)
    if changed:
        audit = audits[0]
        assert audit.action == "material_knowledge_index." + ("observed" if linked else "linked")
        assert audit.actor_id == intent.requested_by
        assert audit.payload["previous_audit_event_id"] == str(original_audit)
        assert audit.payload["embedding_job_id"] == str(job.id)
        assert audit.payload["failure_code"] == expected_failure
    session.scalar.side_effect = results
    _promote(session, intent, runtime)
    assert len(_audits(session)) == len(audits)
    assert intent.version == original_version + int(changed)
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize(
    "status", ["superseded", "not_searchable", "needs_attention", "configuration_changed", "ready"]
)
def test_stopped_intent_cannot_automatically_rebind_or_dispatch(
    runtime: _Runtime, create_job: AsyncMock, status: str
) -> None:
    config = None if status in {"superseded", "not_searchable"} else runtime.config
    intent = _intent(config, status=status, searchable=status != "not_searchable")
    intent.failure_code = {
        "superseded": "indexing_source_superseded",
        "needs_attention": "indexing_retry_exhausted",
        "configuration_changed": "configuration_changed",
    }.get(status)
    results: list[object] = [intent]
    if config is not None:
        results.append(None)
    results.append(True)
    session = _session(intent, *results)
    version, attempt = intent.version, intent.attempt_number
    _promote(session, intent, runtime)
    assert (intent.status, intent.version, intent.attempt_number) == (status, version, attempt)
    assert session.scalar.await_count == len(results)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.add.assert_not_called()
    create_job.assert_not_awaited()


def test_ineligible_unbound_source_is_superseded_without_consuming_attempt(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent()
    session = _session(intent, intent, False)
    _promote(session, intent, runtime)
    assert intent.status == "superseded"
    assert intent.failure_code == "indexing_source_superseded"
    assert intent.attempt_number == 0
    assert intent.dispatch_key is None
    assert intent.config_snapshot is None
    assert intent.embedding_job_id is None
    assert [audit.action for audit in _audits(session)] == ["material_knowledge_index.superseded"]
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("eligible", [False, True])
def test_job_committed_by_competing_coordinator_is_reconciled_after_initial_pin(
    runtime: _Runtime, create_job: AsyncMock, eligible: bool
) -> None:
    intent = _intent()
    job = _job(intent, runtime.config)
    results: list[object] = [intent, True, intent, job, True, eligible]
    if eligible:
        results.append(False)
    session = _session(intent, *results)
    _promote(session, intent, runtime)
    assert intent.status == ("queued" if eligible else "superseded")
    assert intent.embedding_job_id == job.id
    assert intent.attempt_number == 1
    assert intent.config_snapshot == asdict(runtime.config)
    assert [audit.action for audit in _audits(session)] == [
        "material_knowledge_index.dispatch_bound",
        "material_knowledge_index.linked",
    ]
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("already_superseded", [False, True])
def test_source_withdrawal_after_binding_stops_before_job_creation(
    runtime: _Runtime, create_job: AsyncMock, already_superseded: bool
) -> None:
    intent = _intent()
    session = _session(intent, intent, True, intent, None, already_superseded)
    if already_superseded:

        def withdraw() -> None:
            intent.status = "superseded"
            intent.failure_code = "indexing_source_superseded"
            intent.version += 1

        _after_first_commit(session, withdraw)
    _promote(session, intent, runtime)
    assert intent.status == "superseded"
    assert intent.failure_code == "indexing_source_superseded"
    assert intent.attempt_number == 1
    assert intent.embedding_job_id is None
    assert [audit.action for audit in _audits(session)] == [
        "material_knowledge_index.dispatch_bound",
        *([] if already_superseded else ["material_knowledge_index.superseded"]),
    ]
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("pinned", [False, True])
def test_existing_current_vector_short_circuits_job_creation_after_binding(
    runtime: _Runtime, create_job: AsyncMock, pinned: bool
) -> None:
    intent = _intent(runtime.config if pinned else None)
    results: list[object] = (
        [intent, None, True, True] if pinned else [intent, True, intent, None, True, True]
    )
    session = _session(intent, *results)
    _promote(session, intent, runtime)
    assert intent.status == "ready"
    assert intent.failure_code is None
    assert intent.embedding_job_id is None
    assert intent.attempt_number == 1
    assert intent.config_snapshot == asdict(runtime.config)
    assert _audits(session)[-1].action == "material_knowledge_index.observed"
    assert _audits(session)[-1].payload["status"] == "ready"
    assert session.commit.await_count == (1 if pinned else 2)
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("missing", ["dispatch_key", "projection_id"])
def test_missing_pinned_dispatch_inputs_fail_before_vector_or_job_work(
    runtime: _Runtime, create_job: AsyncMock, missing: str
) -> None:
    intent = _intent(runtime.config)
    setattr(intent, missing, None)
    results: list[object] = [intent, True] if missing == "dispatch_key" else [intent, None, True]
    session = _session(intent, *results)
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_binding_invalid"):
        _promote(session, intent, runtime)
    assert session.scalar.await_count == len(results)
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()
    create_job.assert_not_awaited()


def test_reloaded_binding_cannot_change_configuration_between_pin_and_dispatch(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent()
    session = _session(intent, intent, True, intent, None, True)
    changed = replace(runtime.config, version="v2")
    _after_first_commit(session, lambda: _set_binding(intent, changed))
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_binding_invalid"):
        _promote(session, intent, runtime)
    assert intent.config_snapshot == asdict(changed)
    assert intent.embedding_job_id is None
    assert len(_audits(session)) == 1
    assert _audits(session)[0].payload["config_snapshot"] == asdict(runtime.config)
    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()
    create_job.assert_not_awaited()


def test_resuming_waiting_configuration_rechecks_currentness_after_dispatch_commit(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent(runtime.config, status="waiting_configuration")
    intent.failure_code = "configuration_unavailable"
    session = _session(intent, intent, None, True, False, intent, False)
    original_key = intent.dispatch_key
    _promote(session, intent, runtime)
    assert intent.status == "superseded"
    assert intent.failure_code == "indexing_source_superseded"
    assert intent.dispatch_key == original_key
    assert intent.attempt_number == 1
    assert intent.embedding_job_id is None
    assert [audit.payload["status"] for audit in _audits(session)] == ["dispatching", "superseded"]
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


def test_competing_job_after_resumed_dispatch_is_linked_instead_of_created_again(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent(runtime.config, status="waiting_configuration")
    intent.failure_code = "configuration_unavailable"
    job = _job(intent, runtime.config)
    session = _session(intent, intent, None, True, False, intent, True, job, True, True, False)
    original_key = intent.dispatch_key
    _promote(session, intent, runtime)
    assert intent.status == "queued"
    assert intent.embedding_job_id == job.id
    assert intent.attempt_number == 1
    assert intent.dispatch_key == original_key
    assert [audit.action for audit in _audits(session)] == [
        "material_knowledge_index.observed",
        "material_knowledge_index.linked",
    ]
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("initially_pinned", [False, True])
def test_configuration_stop_written_by_another_coordinator_is_not_resumed(
    runtime: _Runtime, create_job: AsyncMock, initially_pinned: bool
) -> None:
    intent = (
        _intent(runtime.config, status="waiting_configuration") if initially_pinned else _intent()
    )
    results: list[object] = (
        [intent, None, True, False, intent, True, None]
        if initially_pinned
        else [intent, True, intent, None, True]
    )
    session = _session(intent, *results)

    def stop() -> None:
        intent.status = "configuration_changed"
        intent.failure_code = "configuration_changed"
        intent.version += 1

    _after_first_commit(session, stop)
    _promote(session, intent, runtime)
    assert intent.status == "configuration_changed"
    assert intent.failure_code == "configuration_changed"
    assert intent.attempt_number == 1
    assert intent.embedding_job_id is None
    assert len(_audits(session)) == 1
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("changed_field", ["dispatch_key", "projection_id", "config_snapshot"])
def test_resumed_dispatch_rechecks_all_pinned_inputs_before_creating_job(
    runtime: _Runtime, create_job: AsyncMock, changed_field: str
) -> None:
    intent = _intent(runtime.config, status="waiting_configuration")
    intent.failure_code = "configuration_unavailable"
    results: list[object] = [intent, None, True, False, intent, True]
    if changed_field != "dispatch_key":
        results.append(None)
    session = _session(intent, *results)

    def change() -> None:
        if changed_field == "config_snapshot":
            _set_binding(intent, replace(runtime.config, version="v2"))
        else:
            setattr(intent, changed_field, None)

    _after_first_commit(session, change)
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_binding_invalid"):
        _promote(session, intent, runtime)
    assert intent.embedding_job_id is None
    assert intent.attempt_number == 1
    assert session.scalar.await_count == len(results)
    assert len(_audits(session)) == 1
    session.commit.assert_awaited_once()
    session.rollback.assert_awaited_once()
    create_job.assert_not_awaited()


@pytest.mark.parametrize("queue_ack_lost", [False, True])
def test_creation_relinks_durable_job_even_without_queue_ack_and_never_resends(
    runtime: _Runtime, create_job: AsyncMock, queue_ack_lost: bool
) -> None:
    intent = _intent(runtime.config)
    job = _job(intent, runtime.config)
    session = _session(intent, intent, None, True, False, None, intent, job, True, True, False)
    if queue_ack_lost:
        create_job.side_effect = EmbeddingQueueUnavailableError()
    else:
        create_job.return_value = EmbeddingJobCreationResult(job=job, deduplicated=False)
    _promote(session, intent, runtime)
    create_job.assert_awaited_once_with(
        intent.curriculum_version_id,
        historical_question_ids=(),
        knowledge_chunk_ids=(),
        knowledge_projection_ids=(intent.projection_id,),
        idempotency_key=intent.dispatch_key,
        actor_id=intent.requested_by,
    )
    assert intent.status == "queued"
    assert intent.embedding_job_id == job.id
    assert intent.attempt_number == 1
    assert job.queue_message_id is None
    assert [audit.action for audit in _audits(session)] == ["material_knowledge_index.linked"]
    session.scalar.side_effect = [intent, job, True, True, False]
    _promote(session, intent, runtime)
    assert create_job.await_count == 1
    assert len(_audits(session)) == 1
    assert session.commit.await_count == 2
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("binding_changed", [False, True])
def test_retry_limit_failure_is_recorded_only_for_the_same_relocked_attempt(
    runtime: _Runtime, create_job: AsyncMock, binding_changed: bool
) -> None:
    intent = _intent(runtime.config)
    session = _session(intent, intent, None, True, False, None, intent)
    create_job.side_effect = EmbeddingRetryLimitExceededError()
    if binding_changed:
        session.rollback.side_effect = lambda: _set_binding(intent, runtime.config, attempt=2)
    _promote(session, intent, runtime)
    create_job.assert_awaited_once()
    session.rollback.assert_awaited_once()
    assert intent.embedding_job_id is None
    assert intent.attempt_number == (2 if binding_changed else 1)
    if binding_changed:
        assert intent.status == "dispatching"
        assert intent.failure_code is None
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
    else:
        assert intent.status == "needs_attention"
        assert intent.failure_code == "indexing_retry_exhausted"
        assert _audits(session)[0].payload["failure_code"] == "indexing_retry_exhausted"
        session.commit.assert_awaited_once()


def test_created_job_cannot_be_relinked_to_a_new_attempt_after_service_commit(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent(runtime.config)
    old_job = _job(intent, runtime.config)
    original_key = intent.dispatch_key
    session = _session(intent, intent, None, True, False, None, intent)

    def create(*_args: object, **_kwargs: object) -> EmbeddingJobCreationResult:
        _set_binding(intent, replace(runtime.config, version="v2"), attempt=2)
        return EmbeddingJobCreationResult(job=old_job, deduplicated=False)

    create_job.side_effect = create
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_version_conflict"):
        _promote(session, intent, runtime)
    create_job.assert_awaited_once()
    assert create_job.await_args is not None
    assert create_job.await_args.kwargs["idempotency_key"] == original_key
    assert intent.attempt_number == 2
    assert intent.embedding_job_id is None
    assert intent.config_snapshot == asdict(replace(runtime.config, version="v2"))
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


def test_service_return_value_is_not_enough_to_link_a_missing_persisted_job(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent(runtime.config)
    job = _job(intent, runtime.config)
    session = _session(intent, intent, None, True, False, None, intent, None)
    create_job.return_value = EmbeddingJobCreationResult(job=job, deduplicated=False)
    with pytest.raises(MaterialKnowledgeError, match="material_indexing_binding_invalid"):
        _promote(session, intent, runtime)
    create_job.assert_awaited_once()
    assert intent.embedding_job_id is None
    assert intent.attempt_number == 1
    assert intent.status == "dispatching"
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


def test_unexpected_creation_error_is_rolled_back_without_retry_or_reclassification(
    runtime: _Runtime, create_job: AsyncMock
) -> None:
    intent = _intent(runtime.config)
    session = _session(intent, intent, None, True, False, None)
    failure = RuntimeError("Uncertain creation acknowledgement")
    create_job.side_effect = failure
    with pytest.raises(RuntimeError, match="Uncertain creation acknowledgement") as caught:
        _promote(session, intent, runtime)
    assert caught.value is failure
    assert intent.status == "dispatching"
    assert intent.failure_code is None
    assert intent.attempt_number == 1
    assert intent.embedding_job_id is None
    create_job.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()
