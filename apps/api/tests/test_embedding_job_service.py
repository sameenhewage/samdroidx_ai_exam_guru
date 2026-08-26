import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import MIN_EMBEDDING_WORKER_LEASE_SECONDS
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.embedding_job_repository import (
    EmbeddingSourceRecord,
    StoredEmbeddingJob,
)
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingCurriculumNotFoundError,
    EmbeddingIdempotencyConflictError,
    EmbeddingJobReadService,
    EmbeddingJobService,
    EmbeddingQueueUnavailableError,
    EmbeddingRecoveryPolicy,
    EmbeddingRecoveryService,
    EmbeddingRetryLimitExceededError,
    EmbeddingSourceIdentityError,
    EmbeddingSourceNotFoundError,
    EmbeddingSourceNotReviewedError,
    EmbeddingSourceRemovedError,
    EmbeddingWorkerService,
    _config_snapshot,
    _fingerprint,
    _source_fingerprint,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingContractError,
)
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    EmbeddingJobModel,
    EmbeddingJobStatus,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.knowledge.repository import (
    EmbeddingSourceConflictError,
    EmbeddingSpaceConflictError,
    KnowledgeRecordNotFoundError,
    SqlAlchemyKnowledgeRepository,
)
from exam_guru_api.knowledge.service import (
    EmbeddingRequiresReviewedRecordError,
    KnowledgePersistenceService,
    StoredEmbedding,
)
from exam_guru_api.retrieval.embeddings import (
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
)
from tests.test_operational_telemetry import telemetry

CURRICULUM_ID = UUID(int=1_832_001)
OTHER_CURRICULUM_ID = UUID(int=1_832_002)
ACTOR_ID = UUID(int=1_832_003)
JOB_ID = UUID(int=1_832_004)
QUESTION_ID = UUID(int=1_832_005)
CHUNK_ID = UUID(int=1_832_006)
CONFIGURATION_ID = UUID(int=1_832_007)
NOW = datetime.now(UTC)
CONFIG = EmbeddingConfig(
    provider="deterministic",
    model="embedding-service-fixture",
    dimension=3,
    version="v1",
    config_fingerprint="embedding-service-fixture-v1",
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _record(
    kind: str = "historical_question",
    *,
    identifier: UUID = QUESTION_ID,
    curriculum_id: UUID = CURRICULUM_ID,
    state: ReviewState = ReviewState.REVIEWED,
    text: str = "persisted reviewed source",
) -> EmbeddingSourceRecord:
    return EmbeddingSourceRecord(
        kind=cast(object, kind),  # type: ignore[arg-type]
        id=identifier,
        curriculum_version_id=curriculum_id,
        review_state=state,
        text=text,
        version=2,
    )


def _job(
    *,
    status: str = EmbeddingJobStatus.QUEUED.value,
    version: int = 0,
    records: tuple[EmbeddingSourceRecord, ...] | None = None,
    requested_count: int | None = None,
    embedded_count: int = 0,
    deduplicated_count: int = 0,
    queue_message_id: str | None = None,
    retry_depth: int = 0,
) -> EmbeddingJobModel:
    active_records = (_record(),) if records is None else records
    question_ids = [str(item.id) for item in active_records if item.kind == "historical_question"]
    chunk_ids = [str(item.id) for item in active_records if item.kind == "knowledge_chunk"]
    return EmbeddingJobModel(
        id=JOB_ID,
        curriculum_version_id=CURRICULUM_ID,
        retry_of_job_id=None,
        retry_depth=retry_depth,
        historical_question_ids=question_ids,
        knowledge_chunk_ids=chunk_ids,
        idempotency_key_hash="sha256:" + "1" * 64,
        request_fingerprint="sha256:" + "2" * 64,
        source_fingerprint=_source_fingerprint(active_records),
        provider=CONFIG.provider,
        model=CONFIG.model,
        dimension=CONFIG.dimension,
        embedding_version=CONFIG.version,
        config_fingerprint=CONFIG.config_fingerprint,
        status=status,
        version=version,
        queue_message_id=queue_message_id,
        requested_count=len(active_records) if requested_count is None else requested_count,
        embedded_count=embedded_count,
        deduplicated_count=deduplicated_count,
        failure_code=None,
        created_by=ACTOR_ID,
        created_at=NOW,
        updated_at=NOW,
        claimed_at=NOW if status != EmbeddingJobStatus.QUEUED.value else None,
        completed_at=None,
    )


def _providers() -> EmbeddingProviderRegistry:
    return EmbeddingProviderRegistry({"deterministic": DeterministicEmbeddingProvider()})


def test_creation_canonicalizes_snapshots_prevalidates_sources_audits_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import embedding_job_service as service_module

    async def exercise() -> None:
        session = FakeSession()
        dispatcher = DeterministicEmbeddingDispatcher("message-id")
        question = _record(identifier=QUESTION_ID)
        chunk = _record("knowledge_chunk", identifier=CHUNK_ID, text="chunk source")
        retry = _job(status=EmbeddingJobStatus.FAILED.value)
        retry.id = UUID(int=1_832_100)
        seen_values: dict[str, object] = {}
        checks: list[tuple[str, UUID]] = []

        class FakePersistence:
            def __init__(self, actual_session: object) -> None:
                assert actual_session is session

            async def question_embedding_exists(
                self, curriculum_id: UUID, identifier: UUID, config: EmbeddingConfig
            ) -> bool:
                assert curriculum_id == CURRICULUM_ID
                assert config == CONFIG
                checks.append(("question", identifier))
                return False

            async def chunk_embedding_exists(
                self, curriculum_id: UUID, identifier: UUID, config: EmbeddingConfig
            ) -> bool:
                assert curriculum_id == CURRICULUM_ID
                assert config == CONFIG
                checks.append(("chunk", identifier))
                return True

        class FakeRepository:
            async def curriculum_exists(self, value: UUID) -> bool:
                return value == CURRICULUM_ID

            async def load_sources(
                self, question_ids: tuple[UUID, ...], chunk_ids: tuple[UUID, ...]
            ) -> tuple[EmbeddingSourceRecord, ...]:
                assert question_ids == (QUESTION_ID,)
                assert chunk_ids == (CHUNK_ID,)
                return (question, chunk)

            async def get_idempotent_job(self, **kwargs: object) -> None:
                return None

            async def latest_failed_retry(self, **kwargs: object) -> EmbeddingJobModel:
                return retry

            async def store_job(self, values: dict[str, object]) -> StoredEmbeddingJob:
                seen_values.update(values)
                model = EmbeddingJobModel(
                    **values,
                    created_at=NOW,
                    updated_at=NOW,
                )
                return StoredEmbeddingJob(model, created=True)

            async def attach_queue_message(
                self, job_id: UUID, message_id: str
            ) -> EmbeddingJobModel:
                assert job_id == seen_values["id"]
                assert message_id == "message-id"
                return EmbeddingJobModel(
                    **{
                        **seen_values,
                        "queue_message_id": message_id,
                        "version": 1,
                    },
                    created_at=NOW,
                    updated_at=NOW,
                )

        monkeypatch.setattr(service_module, "KnowledgePersistenceService", FakePersistence)
        service = EmbeddingJobService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            dispatcher,
            CONFIG,
        )
        service._repository = cast(object, FakeRepository())  # type: ignore[assignment]
        result = await service.create(
            CURRICULUM_ID,
            historical_question_ids=(QUESTION_ID,),
            knowledge_chunk_ids=(CHUNK_ID,),
            idempotency_key="create-key",
            actor_id=ACTOR_ID,
        )

        await service._prevalidate_existing_embeddings(CURRICULUM_ID, ())
        assert result.deduplicated is False
        assert result.job.queue_message_id == "message-id"
        assert seen_values["retry_of_job_id"] == retry.id
        assert seen_values["retry_depth"] == 1
        assert seen_values["historical_question_ids"] == [str(QUESTION_ID)]
        assert seen_values["knowledge_chunk_ids"] == [str(CHUNK_ID)]
        assert checks == [("question", QUESTION_ID), ("chunk", CHUNK_ID)]
        assert dispatcher.dispatched == [cast(UUID, seen_values["id"])]
        assert session.commits == 2
        assert [getattr(item, "action", None) for item in session.added] == [
            "embedding_job.created"
        ]

    asyncio.run(exercise())


def test_embedding_retry_cap_is_checked_after_validation_but_idempotent_replay_wins_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import embedding_job_service as service_module

    async def exercise() -> None:
        session = FakeSession()
        source = _record()
        source_fingerprint = _source_fingerprint((source,))
        request_fingerprint = _fingerprint(
            {
                "configuration": _config_snapshot(CONFIG),
                "curriculum_version_id": str(CURRICULUM_ID),
                "historical_question_ids": [str(QUESTION_ID)],
                "knowledge_chunk_ids": [],
                "source_fingerprint": source_fingerprint,
            }
        )
        terminal = _job(status=EmbeddingJobStatus.FAILED.value, retry_depth=3)
        terminal.idempotency_key_hash = _fingerprint("terminal-replay")
        terminal.request_fingerprint = request_fingerprint
        terminal.source_fingerprint = source_fingerprint
        calls: list[str] = []

        class FakePersistence:
            def __init__(self, actual_session: object) -> None:
                assert actual_session is session

            async def question_embedding_exists(self, *args: object) -> bool:
                calls.append("validated")
                return False

        class Repository:
            replay = True

            async def curriculum_exists(self, _value: UUID) -> bool:
                return True

            async def load_sources(self, *args: object) -> tuple[EmbeddingSourceRecord, ...]:
                return (source,)

            async def get_idempotent_job(
                self, *, actor_id: UUID, idempotency_key_hash: str
            ) -> EmbeddingJobModel | None:
                calls.append("idempotency")
                assert actor_id == ACTOR_ID
                return (
                    terminal
                    if self.replay and idempotency_key_hash == terminal.idempotency_key_hash
                    else None
                )

            async def latest_failed_retry(self, **kwargs: object) -> EmbeddingJobModel:
                calls.append("predecessor")
                return terminal

            async def attach_queue_message(
                self, job_id: UUID, message_id: str
            ) -> EmbeddingJobModel:
                assert job_id == terminal.id
                terminal.queue_message_id = message_id
                return terminal

            async def store_job(self, values: dict[str, object]) -> StoredEmbeddingJob:
                raise AssertionError(f"retry cap must reject before insert: {values}")

        monkeypatch.setattr(service_module, "KnowledgePersistenceService", FakePersistence)
        repository = Repository()
        dispatcher = DeterministicEmbeddingDispatcher()
        service = EmbeddingJobService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            dispatcher,
            CONFIG,
        )
        service._repository = cast(object, repository)  # type: ignore[assignment]

        terminal.request_fingerprint = "sha256:" + "f" * 64
        with pytest.raises(EmbeddingIdempotencyConflictError):
            await service.create(
                CURRICULUM_ID,
                historical_question_ids=(QUESTION_ID,),
                knowledge_chunk_ids=(),
                idempotency_key="terminal-replay",
                actor_id=ACTOR_ID,
            )
        assert calls == ["validated", "idempotency"]
        terminal.request_fingerprint = request_fingerprint
        calls.clear()
        terminal.status = EmbeddingJobStatus.QUEUED.value
        terminal.queue_message_id = None
        queued_replay = await service.create(
            CURRICULUM_ID,
            historical_question_ids=(QUESTION_ID,),
            knowledge_chunk_ids=(),
            idempotency_key="terminal-replay",
            actor_id=ACTOR_ID,
        )
        assert queued_replay.deduplicated is True
        assert terminal.queue_message_id == "deterministic-embedding-message-id"
        assert dispatcher.dispatched == [terminal.id]

        terminal.status = EmbeddingJobStatus.FAILED.value
        calls.clear()
        dispatcher.dispatched.clear()
        replay = await service.create(
            CURRICULUM_ID,
            historical_question_ids=(QUESTION_ID,),
            knowledge_chunk_ids=(),
            idempotency_key="terminal-replay",
            actor_id=ACTOR_ID,
        )
        assert replay.job is terminal
        assert replay.deduplicated is True
        assert calls == ["validated", "idempotency"]
        assert dispatcher.dispatched == []

        calls.clear()
        repository.replay = False
        with pytest.raises(EmbeddingRetryLimitExceededError):
            await service.create(
                CURRICULUM_ID,
                historical_question_ids=(QUESTION_ID,),
                knowledge_chunk_ids=(),
                idempotency_key="fourth-explicit-retry",
                actor_id=ACTOR_ID,
            )
        assert calls == ["validated", "idempotency", "predecessor"]
        assert dispatcher.dispatched == []

    asyncio.run(exercise())


def test_creation_rejects_missing_curriculum_changed_idempotent_request_and_bad_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import embedding_job_service as service_module

    async def exercise() -> None:
        session = FakeSession()
        source = _record()

        class FakePersistence:
            def __init__(self, _session: object) -> None:
                pass

            async def question_embedding_exists(self, *args: object) -> bool:
                return False

        class Repository:
            exists = True
            changed = False

            async def curriculum_exists(self, _value: UUID) -> bool:
                return self.exists

            async def load_sources(self, *args: object) -> tuple[EmbeddingSourceRecord, ...]:
                return (source,)

            async def get_idempotent_job(self, **kwargs: object) -> None:
                return None

            async def latest_failed_retry(self, **kwargs: object) -> None:
                return None

            async def store_job(self, values: dict[str, object]) -> StoredEmbeddingJob:
                model = EmbeddingJobModel(**values, created_at=NOW, updated_at=NOW)
                model.queue_message_id = "already-dispatched"
                if self.changed:
                    model.request_fingerprint = "sha256:" + "f" * 64
                return StoredEmbeddingJob(model, created=False)

        monkeypatch.setattr(service_module, "KnowledgePersistenceService", FakePersistence)
        repository = Repository()
        service = EmbeddingJobService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            DeterministicEmbeddingDispatcher(),
            CONFIG,
        )
        service._repository = cast(object, repository)  # type: ignore[assignment]

        repository.exists = False
        with pytest.raises(EmbeddingCurriculumNotFoundError):
            await service.create(
                CURRICULUM_ID,
                historical_question_ids=(QUESTION_ID,),
                knowledge_chunk_ids=(),
                idempotency_key="missing-curriculum",
                actor_id=ACTOR_ID,
            )

        repository.exists = True
        repository.changed = True
        with pytest.raises(EmbeddingIdempotencyConflictError):
            await service.create(
                CURRICULUM_ID,
                historical_question_ids=(QUESTION_ID,),
                knowledge_chunk_ids=(),
                idempotency_key="changed-request",
                actor_id=ACTOR_ID,
            )

        repository.changed = False
        deduplicated = await service.create(
            CURRICULUM_ID,
            historical_question_ids=(QUESTION_ID,),
            knowledge_chunk_ids=(),
            idempotency_key="same-request",
            actor_id=ACTOR_ID,
        )
        assert deduplicated.deduplicated is True
        assert deduplicated.job.queue_message_id == "already-dispatched"

        job = _job()

        class InvalidDispatcher:
            def dispatch(self, _job_id: UUID) -> str:
                return "invalid message"

        service._dispatcher = InvalidDispatcher()
        with pytest.raises(EmbeddingQueueUnavailableError):
            await service._dispatch(job)
        assert session.commits == 1
        assert (
            cast(AdminAuditEventModel, session.added[-1]).action == "embedding_job.dispatch_failed"
        )

    asyncio.run(exercise())


def test_command_validators_and_source_checks_cover_adversarial_values() -> None:
    for value in ("", " key", "x" * 129, "has space", "control\x00"):
        with pytest.raises(EmbeddingIdempotencyConflictError):
            EmbeddingJobService._validate_idempotency_key(value)
    EmbeddingJobService._validate_idempotency_key("valid-key")

    for value in (cast(str, 1), "", " id", "x" * 129, "has space", "control\x00"):
        with pytest.raises(ValueError, match="message"):
            EmbeddingJobService._validate_message_id(value)
    EmbeddingJobService._validate_message_id("valid-message")

    with pytest.raises(EmbeddingSourceNotFoundError):
        EmbeddingJobService._validate_sources(CURRICULUM_ID, (QUESTION_ID,), (), ())
    with pytest.raises(EmbeddingSourceNotFoundError):
        EmbeddingJobService._validate_sources(
            CURRICULUM_ID,
            (QUESTION_ID,),
            (),
            (_record(curriculum_id=OTHER_CURRICULUM_ID),),
        )
    with pytest.raises(EmbeddingSourceNotReviewedError):
        EmbeddingJobService._validate_sources(
            CURRICULUM_ID,
            (QUESTION_ID,),
            (),
            (_record(state=ReviewState.IN_REVIEW),),
        )
    with pytest.raises(EmbeddingSourceRemovedError):
        EmbeddingJobService._validate_sources(
            CURRICULUM_ID,
            (QUESTION_ID,),
            (),
            (replace(_record(), active_for_ai=False),),
        )

    job = _job()
    assert EmbeddingJobService._same_request(
        job,
        curriculum_version_id=CURRICULUM_ID,
        question_ids=(QUESTION_ID,),
        chunk_ids=(),
        request_fingerprint=job.request_fingerprint,
    )
    assert not EmbeddingJobService._same_request(
        job,
        curriculum_version_id=OTHER_CURRICULUM_ID,
        question_ids=(QUESTION_ID,),
        chunk_ids=(),
        request_fingerprint=job.request_fingerprint,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"batch_size": 101},
        {"outbox_min_age_seconds": 0},
        {"outbox_min_age_seconds": 3_601},
        {"worker_lease_seconds": MIN_EMBEDDING_WORKER_LEASE_SECONDS - 1},
        {"worker_lease_seconds": 86_401},
        {"worker_lease_seconds": True},
        {"worker_lease_seconds": "600"},
    ],
)
def test_recovery_policy_rejects_unbounded_controls(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="embedding recovery"):
        EmbeddingRecoveryPolicy(**kwargs)  # type: ignore[arg-type]


def test_read_service_rejects_missing_curriculum_and_delegates_reads() -> None:
    async def exercise() -> None:
        model = _job()

        class Repository:
            exists = False

            async def curriculum_exists(self, _value: UUID) -> bool:
                return self.exists

            async def get_job(self, *_args: object) -> EmbeddingJobModel:
                return model

            async def list_jobs(
                self, *_args: object, **_kwargs: object
            ) -> tuple[EmbeddingJobModel, ...]:
                return (model,)

        repository = Repository()
        service = EmbeddingJobReadService(cast(AsyncSession, object()))
        service._repository = cast(object, repository)  # type: ignore[assignment]
        assert await service.get(CURRICULUM_ID, JOB_ID) is model
        with pytest.raises(EmbeddingCurriculumNotFoundError):
            await service.list(CURRICULUM_ID, status=None, limit=10, offset=0)
        repository.exists = True
        assert await service.list(
            CURRICULUM_ID,
            status=EmbeddingJobStatus.QUEUED,
            limit=10,
            offset=0,
        ) == (model,)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (EmbeddingProviderUnavailableError(), "embedding_provider_unavailable"),
        (
            EmbeddingSourceConflictError(QUESTION_ID, CONFIGURATION_ID),
            "embedding_source_conflict",
        ),
        (EmbeddingSourceIdentityError(), "embedding_source_conflict"),
        (EmbeddingSpaceConflictError(CONFIG), "embedding_config_conflict"),
        (EmbeddingSourceNotFoundError(), "embedding_source_invalid"),
        (EmbeddingSourceNotReviewedError(), "embedding_source_invalid"),
        (EmbeddingSourceRemovedError(), "embedding_source_invalid"),
        (
            EmbeddingRequiresReviewedRecordError(QUESTION_ID, ReviewState.DRAFT),
            "embedding_source_invalid",
        ),
        (
            KnowledgeRecordNotFoundError("historical_question", QUESTION_ID),
            "embedding_source_invalid",
        ),
        (EmbeddingContractError("bad result"), "embedding_contract_error"),
        (RuntimeError("secret internal detail"), "embedding_internal_error"),
    ],
)
def test_worker_sanitizes_every_failure_family(
    error: Exception,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        session = FakeSession()
        record = _record()
        job = _job(status=EmbeddingJobStatus.CLAIMED.value, version=1, records=(record,))
        worker = EmbeddingWorkerService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            CONFIG,
        )
        codes: list[str] = []

        async def claim(_job_id: UUID) -> EmbeddingJobModel:
            return job

        async def load_sources(*_args: object) -> tuple[EmbeddingSourceRecord, ...]:
            return (record,)

        async def process_record(
            _job: EmbeddingJobModel, _record: EmbeddingSourceRecord
        ) -> EmbeddingJobModel:
            raise error

        async def complete_failure(_job_id: UUID, code: str) -> bool:
            codes.append(code)
            return True

        worker._repository = cast(object, SimpleNamespace(load_sources=load_sources))  # type: ignore[assignment]
        monkeypatch.setattr(worker, "_claim", claim)
        monkeypatch.setattr(worker, "_process_record", process_record)
        monkeypatch.setattr(worker, "_complete_failure", complete_failure)

        assert await worker.process(JOB_ID)
        assert codes == [expected_code]

    asyncio.run(exercise())


def test_worker_handles_config_source_progress_and_claim_completion_cas_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        session = FakeSession()
        record = _record()
        base_job = _job(status=EmbeddingJobStatus.CLAIMED.value, version=1, records=(record,))

        async def run_with(
            job: EmbeddingJobModel,
            records: tuple[EmbeddingSourceRecord, ...],
            providers: EmbeddingProviderRegistry | None = None,
        ) -> str:
            worker = EmbeddingWorkerService(
                cast(object, session),  # type: ignore[arg-type]
                _providers() if providers is None else providers,
                CONFIG,
            )
            codes: list[str] = []

            async def claim(_job_id: UUID) -> EmbeddingJobModel:
                return job

            async def load_sources(*_args: object) -> tuple[EmbeddingSourceRecord, ...]:
                return records

            async def complete_failure(_job_id: UUID, code: str) -> bool:
                codes.append(code)
                return True

            worker._repository = cast(object, SimpleNamespace(load_sources=load_sources))  # type: ignore[assignment]
            monkeypatch.setattr(worker, "_claim", claim)
            monkeypatch.setattr(worker, "_complete_failure", complete_failure)
            assert await worker.process(JOB_ID)
            return codes[0]

        mismatched = _job(status=EmbeddingJobStatus.CLAIMED.value, version=1, records=(record,))
        mismatched.model = "different"
        assert await run_with(mismatched, (record,)) == "embedding_config_unavailable"
        assert (
            await run_with(base_job, (record,), EmbeddingProviderRegistry({}))
            == "embedding_provider_unavailable"
        )
        changed_source = _record(text="changed")
        assert await run_with(base_job, (changed_source,)) == "embedding_source_conflict"
        assert await run_with(base_job, ()) == "embedding_source_invalid"

        worker = EmbeddingWorkerService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            CONFIG,
        )
        incomplete = _job(
            status=EmbeddingJobStatus.CLAIMED.value,
            version=1,
            records=(record,),
            requested_count=2,
        )

        async def claim_incomplete(_job_id: UUID) -> EmbeddingJobModel:
            return incomplete

        async def load_one(*_args: object) -> tuple[EmbeddingSourceRecord, ...]:
            return (record,)

        async def no_progress(
            job: EmbeddingJobModel, _record: EmbeddingSourceRecord
        ) -> EmbeddingJobModel:
            return job

        codes: list[str] = []

        async def complete_failure(_job_id: UUID, code: str) -> bool:
            codes.append(code)
            return True

        worker._repository = cast(object, SimpleNamespace(load_sources=load_one))  # type: ignore[assignment]
        monkeypatch.setattr(worker, "_claim", claim_incomplete)
        monkeypatch.setattr(worker, "_process_record", no_progress)
        monkeypatch.setattr(worker, "_complete_failure", complete_failure)
        assert await worker.process(JOB_ID)
        assert codes == ["embedding_internal_error"]

        class CasRepository:
            async def claim(self, *_args: object, **_kwargs: object) -> None:
                return None

            async def get_job_unscoped(self, _job_id: UUID) -> None:
                return None

            async def complete(self, *_args: object, **_kwargs: object) -> None:
                return None

        worker = EmbeddingWorkerService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            CONFIG,
        )
        worker._repository = cast(object, CasRepository())  # type: ignore[assignment]
        assert await worker._claim(JOB_ID) is None
        assert not await worker._complete_failure(JOB_ID, "embedding_internal_error")
        assert not await worker._complete(base_job, succeeded=True, failure_code=None)
        assert session.rollbacks >= 3

    asyncio.run(exercise())


def test_embedding_worker_emits_terminal_counts_after_commit() -> None:
    async def exercise() -> None:
        session = FakeSession()
        terminal = _job(
            status=EmbeddingJobStatus.SUCCEEDED.value,
            version=3,
            requested_count=4,
            embedded_count=3,
            deduplicated_count=1,
        )

        class Repository:
            async def complete(self, *args: object, **kwargs: object) -> EmbeddingJobModel:
                del args, kwargs
                return terminal

        operational, telemetry_logger, _tracer = telemetry()
        worker = EmbeddingWorkerService(
            cast(AsyncSession, session),
            EmbeddingProviderRegistry({}),
            None,
            telemetry=operational,
        )
        worker._repository = cast(object, Repository())  # type: ignore[assignment]

        assert await worker._complete(terminal, succeeded=True, failure_code=None)
        assert session.commits == 1
        assert telemetry_logger.records == [
            (
                "Operational event",
                {
                    "event_name": "embedding.terminal",
                    "outcome": "succeeded",
                    "failure_code": None,
                    "status": "succeeded",
                    "requested_count": 4,
                    "embedded_count": 3,
                    "deduplicated_count": 1,
                },
            )
        ]
        assert str(terminal.id) not in str(telemetry_logger.records)

    asyncio.run(exercise())


def test_scoped_existence_checks_default_to_unlocked_and_forward_worker_lock() -> None:
    async def exercise() -> None:
        lock_values: list[tuple[str, bool]] = []
        record = SimpleNamespace(
            id=QUESTION_ID,
            review_state=ReviewState.REVIEWED,
            text="authoritative source",
        )

        class Model:
            def to_domain(self) -> object:
                return record

        class Repository:
            async def get_question(
                self,
                _identifier: UUID,
                *,
                curriculum_version_id: UUID,
                for_update: bool = False,
            ) -> Model:
                assert curriculum_version_id == CURRICULUM_ID
                lock_values.append(("question", for_update))
                return Model()

            async def get_chunk(
                self,
                _identifier: UUID,
                *,
                curriculum_version_id: UUID,
                for_update: bool = False,
            ) -> Model:
                assert curriculum_version_id == CURRICULUM_ID
                lock_values.append(("chunk", for_update))
                return Model()

            async def find_embedding(self, **_kwargs: object) -> None:
                return None

        persistence = KnowledgePersistenceService(cast(AsyncSession, object()))
        persistence._repository = cast(object, Repository())  # type: ignore[assignment]
        assert not await persistence.question_embedding_exists(
            CURRICULUM_ID,
            QUESTION_ID,
            CONFIG,
        )
        assert not await persistence.question_embedding_exists(
            CURRICULUM_ID,
            QUESTION_ID,
            CONFIG,
            for_update=True,
        )
        assert not await persistence.chunk_embedding_exists(
            CURRICULUM_ID,
            CHUNK_ID,
            CONFIG,
        )
        assert not await persistence.chunk_embedding_exists(
            CURRICULUM_ID,
            CHUNK_ID,
            CONFIG,
            for_update=True,
        )
        assert lock_values == [
            ("question", False),
            ("question", True),
            ("chunk", False),
            ("chunk", True),
        ]

    asyncio.run(exercise())


def test_worker_record_paths_are_scoped_deduplicated_and_progress_cas_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import embedding_job_service as service_module

    async def exercise() -> None:
        session = FakeSession()
        question = _record()
        chunk = _record("knowledge_chunk", identifier=CHUNK_ID, text="chunk")
        job = _job(
            status=EmbeddingJobStatus.CLAIMED.value,
            version=1,
            records=(question, chunk),
        )
        existing = True
        stored_deduplicated = False
        lock_values: list[bool] = []
        store_commit_values: list[bool] = []

        class FakePersistence:
            def __init__(self, _session: object) -> None:
                pass

            async def question_embedding_exists(
                self,
                *_args: object,
                **kwargs: object,
            ) -> bool:
                lock_values.append(cast(bool, kwargs.get("for_update", False)))
                return existing

            async def chunk_embedding_exists(
                self,
                *_args: object,
                **kwargs: object,
            ) -> bool:
                lock_values.append(cast(bool, kwargs.get("for_update", False)))
                return existing

            async def store_curriculum_question_embedding(
                self, *_args: object, **kwargs: object
            ) -> StoredEmbedding:
                store_commit_values.append(cast(bool, kwargs.get("commit", True)))
                return _stored(stored_deduplicated)

            async def store_curriculum_chunk_embedding(
                self, *_args: object, **kwargs: object
            ) -> StoredEmbedding:
                store_commit_values.append(cast(bool, kwargs.get("commit", True)))
                return _stored(stored_deduplicated)

        class ProgressRepository:
            def __init__(self) -> None:
                self.result: EmbeddingJobModel | None = job
                self.embedded_values: list[bool] = []

            async def advance_progress(
                self,
                *_args: object,
                **kwargs: object,
            ) -> EmbeddingJobModel | None:
                self.embedded_values.append(cast(bool, kwargs["embedded"]))
                return self.result

        def _stored(deduplicated: bool) -> StoredEmbedding:
            return StoredEmbedding(
                id=JOB_ID,
                configuration_id=CONFIGURATION_ID,
                config=CONFIG,
                source_text_sha256="a" * 64,
                vector=(0.1, 0.2, 0.3),
                deduplicated=deduplicated,
            )

        monkeypatch.setattr(service_module, "KnowledgePersistenceService", FakePersistence)
        repository = ProgressRepository()
        worker = EmbeddingWorkerService(
            cast(object, session),  # type: ignore[arg-type]
            _providers(),
            CONFIG,
        )
        worker._repository = cast(object, repository)  # type: ignore[assignment]

        assert await worker._process_record(job, question) is job
        existing = False
        stored_deduplicated = False
        assert await worker._process_record(job, question) is job
        stored_deduplicated = True
        assert await worker._process_record(job, chunk) is job
        assert repository.embedded_values == [False, True, False]

        repository.result = None
        with pytest.raises(RuntimeError, match="progress"):
            await worker._process_record(job, chunk)
        assert lock_values == [True, True, True, True]
        assert store_commit_values == [False, False, False]

    asyncio.run(exercise())


def test_recovery_redispatches_outbox_expires_claims_and_audits_exact_counts() -> None:
    async def exercise() -> None:
        session = FakeSession()
        successful = _job()
        failed = _job()
        failed.id = UUID(int=1_832_200)
        claim = _job(
            status=EmbeddingJobStatus.CLAIMED.value,
            version=4,
            requested_count=3,
            embedded_count=1,
            deduplicated_count=1,
        )
        claim.id = UUID(int=1_832_201)
        terminal = _job(
            status=EmbeddingJobStatus.FAILED.value,
            version=5,
            requested_count=3,
            embedded_count=1,
            deduplicated_count=1,
        )
        terminal.id = claim.id
        terminal.failure_code = "worker_lease_expired"
        attached: list[tuple[UUID, str]] = []
        claim_calls: list[tuple[datetime, int]] = []

        class Repository:
            async def lock_recoverable_outbox_jobs(self, **kwargs: object):  # type: ignore[no-untyped-def]
                assert kwargs["limit"] == 2
                return (successful, failed)

            async def attach_queue_message(
                self, job_id: UUID, message_id: str
            ) -> EmbeddingJobModel:
                attached.append((job_id, message_id))
                return successful

            async def lock_expired_claims(
                self,
                *,
                claimed_before: datetime,
                limit: int,
            ) -> tuple[EmbeddingJobModel, ...]:
                claim_calls.append((claimed_before, limit))
                return (claim,)

            async def expire_claim(
                self,
                actual: EmbeddingJobModel,
                *,
                completed_at: datetime,
            ) -> EmbeddingJobModel:
                assert actual is claim
                assert completed_at == NOW
                return terminal

        class Dispatcher:
            def dispatch(self, job_id: UUID) -> str:
                if job_id == failed.id:
                    raise RuntimeError("queue secret")
                return "recovery-message"

        service = EmbeddingRecoveryService(
            cast(object, session),  # type: ignore[arg-type]
            Dispatcher(),
            EmbeddingRecoveryPolicy(
                batch_size=2,
                outbox_min_age_seconds=7,
                worker_lease_seconds=700,
            ),
        )
        service._repository = cast(object, Repository())  # type: ignore[assignment]
        result = await service.recover(now=NOW)

        assert (
            result.outbox_scanned,
            result.outbox_dispatched,
            result.outbox_failures,
            result.claims_scanned,
            result.claims_expired,
        ) == (2, 1, 1, 1, 1)
        assert attached == [(successful.id, "recovery-message")]
        assert claim_calls == [(NOW - timedelta(seconds=700), 2)]
        audits = [cast(AdminAuditEventModel, item) for item in session.added]
        assert [item.action for item in audits] == [
            "embedding_job.redispatched",
            "embedding_job.redispatch_failed",
            "embedding_job.worker_lease_expired",
        ]
        assert audits[-1].payload == {
            "failure_code": "worker_lease_expired",
            "requested_count": 3,
            "embedded_count": 1,
            "deduplicated_count": 1,
        }
        assert "secret" not in str(audits[-1].payload)
        assert session.commits == 1

        class EmptyRepository:
            async def lock_recoverable_outbox_jobs(
                self, **_kwargs: object
            ) -> tuple[EmbeddingJobModel, ...]:
                return ()

            async def lock_expired_claims(self, **_kwargs: object) -> tuple[EmbeddingJobModel, ...]:
                return ()

        empty = EmbeddingRecoveryService(
            cast(object, session),  # type: ignore[arg-type]
            Dispatcher(),
            EmbeddingRecoveryPolicy(),
        )
        empty._repository = cast(object, EmptyRepository())  # type: ignore[assignment]
        empty_result = await empty.recover()
        assert (empty_result.outbox_scanned, empty_result.claims_scanned) == (0, 0)

        class LostCasRepository(EmptyRepository):
            async def lock_expired_claims(self, **_kwargs: object) -> tuple[EmbeddingJobModel, ...]:
                return (claim,)

            async def expire_claim(self, *_args: object, **_kwargs: object) -> None:
                return None

        lost_session = FakeSession()
        lost = EmbeddingRecoveryService(
            cast(object, lost_session),  # type: ignore[arg-type]
            Dispatcher(),
            EmbeddingRecoveryPolicy(),
        )
        lost._repository = cast(object, LostCasRepository())  # type: ignore[assignment]
        lost_result = await lost.recover(now=NOW)
        assert (lost_result.claims_scanned, lost_result.claims_expired) == (1, 0)
        assert lost_session.added == []

    asyncio.run(exercise())


def test_persistence_defense_rejects_configuration_dimension_and_source_hash_conflicts() -> None:
    async def exercise() -> None:
        mismatched = EmbeddingConfigurationModel(
            id=CONFIGURATION_ID,
            provider=CONFIG.provider,
            model=CONFIG.model,
            dimension=4,
            version=CONFIG.version,
            config_fingerprint=CONFIG.config_fingerprint,
            created_by=ACTOR_ID,
            updated_by=ACTOR_ID,
        )
        scalar = AsyncMock(return_value=mismatched)
        session = cast(AsyncSession, SimpleNamespace(scalar=scalar))
        repository = SqlAlchemyKnowledgeRepository(session)
        with pytest.raises(EmbeddingSpaceConflictError):
            await repository.find_embedding(
                historical_question_id=QUESTION_ID,
                knowledge_chunk_id=None,
                config=CONFIG,
            )

        existing = KnowledgeEmbeddingModel(
            id=JOB_ID,
            historical_question_id=QUESTION_ID,
            knowledge_chunk_id=None,
            embedding_configuration_id=CONFIGURATION_ID,
            embedding_dimension=CONFIG.dimension,
            source_text_sha256="f" * 64,
            embedding=[0.1, 0.2, 0.3],
            created_by=ACTOR_ID,
        )

        class ExistingRepository:
            async def find_embedding(self, **_kwargs: object) -> KnowledgeEmbeddingModel:
                return existing

        persistence = KnowledgePersistenceService(cast(AsyncSession, object()))
        persistence._repository = cast(object, ExistingRepository())  # type: ignore[assignment]
        with pytest.raises(EmbeddingSourceConflictError):
            await persistence._embedding_exists(
                historical_question_id=QUESTION_ID,
                knowledge_chunk_id=None,
                text="authoritative source",
                config=CONFIG,
            )

    asyncio.run(exercise())
