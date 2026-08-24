import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge import embedding_jobs as jobs
from exam_guru_api.knowledge.embedding_job_schemas import (
    EmbeddingConfigurationResponse,
    EmbeddingJobCreateRequest,
    EmbeddingJobResponse,
)
from exam_guru_api.knowledge.embedding_jobs import (
    EMBEDDING_JOB_MAX_RETRIES,
    EMBEDDING_JOB_TIME_LIMIT_MS,
    EMBEDDING_QUEUE_NAME,
    DeterministicEmbeddingDispatcher,
    DramatiqEmbeddingDispatcher,
    EmbeddingDispatcher,
    create_embedding_dispatcher,
)
from exam_guru_api.knowledge.models import EmbeddingJobModel, EmbeddingJobStatus
from exam_guru_api.main import create_app

JOB_ID = UUID(int=1_810_001)
CURRICULUM_ID = UUID(int=1_810_002)
QUESTION_ID = UUID(int=1_810_003)
CHUNK_ID = UUID(int=1_810_004)
ACTOR_ID = UUID(int=1_810_005)


class StubMessage:
    message_id = "embedding-broker-message"


class RecordingActor:
    def __init__(self) -> None:
        self.sent: list[tuple[str]] = []

    def send(self, job_id: str) -> StubMessage:
        self.sent.append((job_id,))
        return StubMessage()


def test_create_request_accepts_only_unique_bounded_record_ids() -> None:
    request = EmbeddingJobCreateRequest(
        historical_question_ids=(QUESTION_ID,),
        knowledge_chunk_ids=(CHUNK_ID,),
    )
    assert request.historical_question_ids == (QUESTION_ID,)
    assert request.knowledge_chunk_ids == (CHUNK_ID,)

    invalid_payloads: tuple[dict[str, object], ...] = (
        {"historical_question_ids": (), "knowledge_chunk_ids": ()},
        {
            "historical_question_ids": (QUESTION_ID, QUESTION_ID),
            "knowledge_chunk_ids": (),
        },
        {
            "historical_question_ids": (),
            "knowledge_chunk_ids": (CHUNK_ID, CHUNK_ID),
        },
        {
            "historical_question_ids": tuple(UUID(int=index + 1) for index in range(101)),
            "knowledge_chunk_ids": (),
        },
        {
            "historical_question_ids": (QUESTION_ID,),
            "knowledge_chunk_ids": (),
            "text": "client-controlled text",
        },
        {
            "historical_question_ids": (QUESTION_ID,),
            "knowledge_chunk_ids": (),
            "embedding_config": {"provider": "client"},
        },
        {
            "historical_question_ids": (QUESTION_ID,),
            "knowledge_chunk_ids": (),
            "vector": [0.1],
        },
        {
            "historical_question_ids": (QUESTION_ID,),
            "knowledge_chunk_ids": (),
            "state": "succeeded",
        },
    )
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            EmbeddingJobCreateRequest.model_validate(payload)


def test_job_response_contains_metadata_and_counts_but_never_text_or_vectors() -> None:
    now = datetime.now(UTC)
    model = EmbeddingJobModel(
        id=JOB_ID,
        curriculum_version_id=CURRICULUM_ID,
        retry_of_job_id=None,
        historical_question_ids=[str(QUESTION_ID)],
        knowledge_chunk_ids=[str(CHUNK_ID)],
        idempotency_key_hash="sha256:" + "1" * 64,
        request_fingerprint="sha256:" + "2" * 64,
        source_fingerprint="sha256:" + "3" * 64,
        provider="deterministic",
        model="grade5-fixture",
        dimension=3,
        embedding_version="v1",
        config_fingerprint="sha256:" + "4" * 64,
        status=EmbeddingJobStatus.SUCCEEDED.value,
        version=4,
        queue_message_id="message-id",
        requested_count=2,
        embedded_count=1,
        deduplicated_count=1,
        failure_code=None,
        created_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
        claimed_at=now,
        completed_at=now,
    )

    response = EmbeddingJobResponse.from_model(model)
    body = response.model_dump(mode="json")

    assert response.configuration == EmbeddingConfigurationResponse(
        provider="deterministic",
        model="grade5-fixture",
        dimension=3,
        version="v1",
        config_fingerprint="sha256:" + "4" * 64,
    )
    assert body["counts"] == {"requested": 2, "embedded": 1, "deduplicated": 1}
    assert "source_fingerprint" not in body
    assert "request_fingerprint" not in body
    assert "idempotency_key_hash" not in body
    rendered = str(body).lower()
    assert "vector" not in rendered
    assert "client-controlled text" not in rendered
    with pytest.raises(TypeError, match="EmbeddingJobModel"):
        EmbeddingJobResponse.from_model(object())


def test_openapi_create_contract_has_only_server_selected_record_ids() -> None:
    schema = create_app(settings=Settings(environment="test")).openapi()
    path = schema["paths"]["/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs"]
    operation = path["post"]
    request_reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"][request_reference.rsplit("/", 1)[-1]]

    assert set(request_schema["properties"]) == {
        "historical_question_ids",
        "knowledge_chunk_ids",
    }
    assert request_schema["additionalProperties"] is False
    for forbidden in ("config", "provider", "model", "dimension", "vector", "text", "state"):
        assert forbidden not in request_schema["properties"]
    idempotency = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["required"] is True
    assert idempotency["schema"]["maxLength"] == 128
    assert path["get"]["operationId"] == "list_embedding_jobs"
    item_path = schema["paths"][
        "/api/v1/admin/curricula/{curriculum_version_id}/embedding-jobs/{embedding_job_id}"
    ]
    assert item_path["get"]["operationId"] == "get_embedding_job"
    assert "delete" not in item_path
    assert "patch" not in item_path


def test_embedding_dispatchers_enqueue_only_the_durable_job_id() -> None:
    actor = RecordingActor()
    dispatcher = DramatiqEmbeddingDispatcher(actor)

    assert dispatcher.dispatch(JOB_ID) == "embedding-broker-message"
    assert actor.sent == [(str(JOB_ID),)]

    deterministic: EmbeddingDispatcher = DeterministicEmbeddingDispatcher("deterministic-id")
    assert deterministic.dispatch(JOB_ID) == "deterministic-id"
    assert deterministic.dispatch(JOB_ID) == "deterministic-id"
    assert deterministic.dispatched == [JOB_ID, JOB_ID]  # type: ignore[attr-defined]


def test_embedding_actors_are_dedicated_bounded_and_registered() -> None:
    assert jobs.ingest_embeddings.queue_name == EMBEDDING_QUEUE_NAME
    assert jobs.ingest_embeddings.queue_name != "default"
    assert jobs.ingest_embeddings.options == {
        "max_retries": EMBEDDING_JOB_MAX_RETRIES,
        "time_limit": EMBEDDING_JOB_TIME_LIMIT_MS,
    }
    assert jobs.recover_embedding_jobs.options == jobs.ingest_embeddings.options
    assert EMBEDDING_JOB_MAX_RETRIES == 0
    assert EMBEDDING_JOB_TIME_LIMIT_MS > 0

    dispatcher = create_embedding_dispatcher(Settings(environment="test"))
    assert isinstance(dispatcher, DramatiqEmbeddingDispatcher)
    assert jobs.ingest_embeddings.actor_name in jobs.ingest_embeddings.broker.get_declared_actors()

    worker = importlib.import_module("exam_guru_api.worker")
    registered = worker.broker.get_actor(jobs.ingest_embeddings.actor_name)
    recovery = worker.broker.get_actor(jobs.recover_embedding_jobs.actor_name)
    assert registered is jobs.ingest_embeddings
    assert recovery is jobs.recover_embedding_jobs
    assert registered.queue_name == EMBEDDING_QUEUE_NAME
    assert recovery.queue_name == EMBEDDING_QUEUE_NAME


def test_embedding_actor_builds_dependencies_handles_unavailable_config_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.infrastructure import resources as resources_module
    from exam_guru_api.knowledge import embedding_job_service as service_module

    settings = Settings(environment="staging")
    session = object()
    calls: list[tuple[UUID, object | None]] = []

    class StubResources:
        def __init__(self) -> None:
            self.closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield session

        async def close(self) -> None:
            self.closed = True

    class StubWorker:
        def __init__(
            self,
            actual_session: object,
            providers: object,
            active_config: object | None,
        ) -> None:
            assert actual_session is session
            assert providers is provider_registry
            calls.append((JOB_ID, active_config))

        async def process(self, job_id: UUID) -> bool:
            assert job_id == JOB_ID
            return True

    resources = StubResources()
    provider_registry = object()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(
        resources_module,
        "create_resources",
        lambda actual: resources if actual is settings else None,
    )
    monkeypatch.setattr(
        jobs,
        "create_embedding_provider_registry",
        lambda actual: provider_registry if actual is settings else None,
    )
    monkeypatch.setattr(service_module, "EmbeddingWorkerService", StubWorker)

    jobs.ingest_embeddings(str(JOB_ID))

    assert calls == [(JOB_ID, None)]
    assert resources.closed is True


def test_embedding_recovery_actor_uses_bounded_settings_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.infrastructure import resources as resources_module
    from exam_guru_api.knowledge import embedding_job_service as service_module

    settings = Settings(
        environment="test",
        embedding_recovery_batch_size=7,
        embedding_outbox_min_age_seconds=11,
        embedding_worker_lease_seconds=701,
    )
    session = object()
    dispatcher = object()
    policies: list[object] = []

    class StubResources:
        def __init__(self) -> None:
            self.closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield session

        async def close(self) -> None:
            self.closed = True

    class StubRecoveryService:
        def __init__(
            self,
            actual_session: object,
            actual_dispatcher: object,
            policy: object,
        ) -> None:
            assert actual_session is session
            assert actual_dispatcher is dispatcher
            policies.append(policy)

        async def recover(self) -> object:
            return object()

    resources = StubResources()
    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(jobs, "DramatiqEmbeddingDispatcher", lambda: dispatcher)
    monkeypatch.setattr(
        resources_module,
        "create_resources",
        lambda actual: resources if actual is settings else None,
    )
    monkeypatch.setattr(service_module, "EmbeddingRecoveryService", StubRecoveryService)

    jobs.recover_embedding_jobs()

    assert len(policies) == 1
    policy = policies[0]
    assert policy.batch_size == 7  # type: ignore[attr-defined]
    assert policy.outbox_min_age_seconds == 11  # type: ignore[attr-defined]
    assert policy.worker_lease_seconds == 701  # type: ignore[attr-defined]
    assert resources.closed is True
