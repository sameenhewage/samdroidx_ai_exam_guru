import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import ClassVar, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.blueprints.generator import generate_blueprint
from exam_guru_api.blueprints.serialization import serialize_blueprint
from exam_guru_api.core.config import Settings
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationRunModel, GenerationRunStatus
from exam_guru_api.generation.runtime import create_generation_runtime
from exam_guru_api.papers.domain import ValidationNotPassedError
from exam_guru_api.papers.models import QuestionCandidateModel
from exam_guru_api.papers.review_service import (
    ReviewCandidateRevalidationRequiredError,
    ReviewCandidateStateConflictError,
    ReviewCandidateVersionConflictError,
)
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProviderUnavailableError,
    create_embedding_provider_registry,
)
from exam_guru_api.teacher_papers.domain import (
    PaperDifficulty,
    PaperJobStatus,
    PaperScopeError,
    PaperSettings,
    PaperSlotStatus,
    TeacherScopeKind,
    TeacherScopeSelection,
    build_blueprint_specification,
    translate_teacher_scope,
)
from exam_guru_api.teacher_papers.jobs import DeterministicPaperGenerationDispatcher
from exam_guru_api.teacher_papers.models import TeacherPaperJobModel, TeacherPaperSlotModel
from exam_guru_api.teacher_papers.repository import (
    ReviewSlotSource,
    StoredTeacherPaper,
    StoredTeacherPaperInsert,
    TeacherPaperQuestionNotFoundError,
    TeacherPaperRepository,
)
from exam_guru_api.teacher_papers.schemas import (
    ReviewPaperCreateDraftRequest,
    ReviewQuestionEditRequest,
    ReviewQuestionRegenerateRequest,
    ReviewQuestionRejectRequest,
    ReviewQuestionResponse,
    TeacherPaperJobCreateRequest,
)
from exam_guru_api.teacher_papers.service import (
    TeacherPaperContextUnavailableError,
    TeacherPaperCostLimitError,
    TeacherPaperCurriculumAmbiguousError,
    TeacherPaperCurriculumNotFoundError,
    TeacherPaperIdempotencyConflictError,
    TeacherPaperJobService,
    TeacherPaperQueryService,
    TeacherPaperQueueUnavailableError,
    TeacherPaperRecoveryService,
    TeacherPaperRetryLimitError,
    TeacherPaperRevalidationRequiredError,
    TeacherPaperReviewService,
    TeacherPaperStateConflictError,
    TeacherPaperVersionConflictError,
    TeacherPaperWorkerService,
    _content_snapshot,
    _friendly_validation,
    _replace_generation_run,
    _require_context_ids,
    _resolved_job_scope,
    _review_question,
    _review_status,
    _validate_idempotency_key,
    teacher_paper_job_response,
)
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel
from exam_guru_api.validation.pipeline import build_default_pipeline
from tests.test_teacher_paper_domain import curriculum, lesson

NOW = datetime(2026, 8, 25, tzinfo=UTC)
JOB_ID = UUID(int=25_910_001)
ACTOR_ID = UUID(int=25_910_002)
SLOT_ID = UUID(int=25_910_003)
RUN_ID = UUID(int=25_910_004)


class DummySession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def session(value: DummySession) -> AsyncSession:
    return cast(AsyncSession, value)


def principal() -> Principal:
    return Principal(ACTOR_ID, frozenset({AdminRole.ADMIN}))


def create_request(*, full: bool = False) -> TeacherPaperJobCreateRequest:
    return TeacherPaperJobCreateRequest.model_validate(
        {
            "target": {
                "grade": 7,
                "medium": "en",
                "subject": "MATHEMATICS",
                "assessment_programme": "SCHOOL-G7",
            },
            "scope": (
                {"kind": "full_subject"}
                if full
                else {"kind": "lesson_range", "start_lesson": 1, "end_lesson": 1}
            ),
            "settings": {
                "question_count": 1,
                "duration_minutes": 45,
                "difficulty": PaperDifficulty.BALANCED,
            },
        }
    )


def job(
    *,
    status: str = PaperJobStatus.PREPARING.value,
    version: int = 0,
    slot_count: int = 1,
) -> TeacherPaperJobModel:
    resolved = curriculum(lessons=(lesson(1),))
    return TeacherPaperJobModel(
        id=JOB_ID,
        paper_reference="EGP-ABCD-12345678",
        created_by=ACTOR_ID,
        idempotency_key_hash="sha256:" + "a" * 64,
        request_fingerprint="sha256:" + "b" * 64,
        curriculum_version_id=resolved.curriculum_version_id,
        exam_configuration_id=resolved.exam_configuration_id,
        medium_id=resolved.medium_id,
        subject_id=resolved.subject_id,
        teacher_intent={
            "target": {"grade": 7},
            "scope": {"kind": "full_subject"},
        },
        paper_settings={
            "question_count": slot_count,
            "duration_minutes": 45,
            "difficulty": "balanced",
        },
        resolution_snapshot={
            "subject": {"label": "Mathematics"},
            "medium": {"label": "English"},
            "scope_summary": "Full syllabus",
            "lessons": [],
        },
        title="Grade 7 Mathematics practice paper",
        status=status,
        version=version,
        slot_count=slot_count,
        generated_count=0,
        validated_count=0,
        candidate_count=0,
        approved_count=0,
        failed_count=0,
        total_tokens=0,
        cost_microusd=0,
        max_cost_microusd=15_000_000,
        created_at=NOW,
        updated_at=NOW,
    )


def paper_slot(*, status: str = PaperSlotStatus.GENERATING.value) -> TeacherPaperSlotModel:
    return TeacherPaperSlotModel(
        id=SLOT_ID,
        paper_job_id=JOB_ID,
        curriculum_version_id=curriculum().curriculum_version_id,
        ordinal=1,
        status=status,
        version=0,
        regeneration_count=0,
        requires_revalidation=False,
    )


class QueryRepository:
    def __init__(self, records: tuple[object, ...]) -> None:
        self.records = records

    async def list_curricula(self, **kwargs: object) -> tuple[object, ...]:
        del kwargs
        return self.records


class RecordingDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[UUID] = []

    def dispatch(self, job_id: UUID) -> str:
        self.calls.append(job_id)
        if self.fail:
            raise RuntimeError("queue private diagnostic")
        return "message-id"


def test_idempotency_and_query_resolution_error_boundaries() -> None:
    for invalid in ("", " padded", "two words", "x" * 129, "bad\nkey"):
        with pytest.raises(TeacherPaperIdempotencyConflictError):
            _validate_idempotency_key(invalid)
    with pytest.raises(TeacherPaperContextUnavailableError):
        _require_context_ids((), (), "slot-1")
    context_id = UUID(int=1)
    assert _require_context_ids((context_id,), (), "slot-1") == ((context_id,), ())
    assert _require_context_ids((), (context_id,), "slot-1") == ((), (context_id,))

    service = TeacherPaperQueryService(session(DummySession()))
    service._repository = QueryRepository(())  # type: ignore[assignment]
    with pytest.raises(TeacherPaperCurriculumNotFoundError):
        asyncio.run(
            service._resolve(
                grade=7,
                medium="en",
                subject="MATHEMATICS",
                assessment_programme=None,
            )
        )
    service._repository = QueryRepository((curriculum(), curriculum()))  # type: ignore[assignment]
    with pytest.raises(TeacherPaperCurriculumAmbiguousError):
        asyncio.run(
            service._resolve(
                grade=7,
                medium="en",
                subject="MATHEMATICS",
                assessment_programme=None,
            )
        )


def test_query_service_returns_empty_options_and_exact_readable_labels() -> None:
    service = TeacherPaperQueryService(session(DummySession()))
    service._repository = QueryRepository(())  # type: ignore[assignment]
    empty = asyncio.run(service.options())
    assert empty.media == ()
    assert empty.subjects == ()
    assert (
        asyncio.run(
            service.curricula(
                grade=7,
                medium="en",
                subject="MATHEMATICS",
                assessment_programme=None,
            )
        ).items
        == ()
    )

    resolved = curriculum(lessons=(lesson(1),))
    service._repository = QueryRepository((resolved,))  # type: ignore[assignment]
    exact = asyncio.run(
        service._resolve(
            grade=7,
            medium="en",
            subject="MATHEMATICS",
            assessment_programme="SCHOOL-G7",
        )
    )
    assert exact == resolved
    lessons = asyncio.run(
        service.lessons(
            grade=7,
            medium="en",
            subject="MATHEMATICS",
            assessment_programme="SCHOOL-G7",
        )
    )
    assert lessons.lessons[0].label == "Lesson 1 — Whole numbers"


def test_resolved_job_scope_rejects_missing_server_curriculum_and_restores_settings() -> None:
    missing = QueryRepository(())
    with pytest.raises(TeacherPaperCurriculumNotFoundError):
        asyncio.run(_resolved_job_scope(cast(object, missing), job()))  # type: ignore[arg-type]

    available = QueryRepository((curriculum(lessons=(lesson(1),)),))
    resolved, scope, settings = asyncio.run(
        _resolved_job_scope(cast(object, available), job())  # type: ignore[arg-type]
    )
    assert resolved.subject_code == "MATHEMATICS"
    assert scope.summary == "Full syllabus"
    assert settings.difficulty is PaperDifficulty.BALANCED


class CreateRepository:
    def __init__(
        self,
        *,
        conflict: bool = False,
        dispatch_attached: bool = False,
    ) -> None:
        self.conflict = conflict
        self.dispatch_attached = dispatch_attached
        self.values: dict[str, object] = {}
        self.stored_job: TeacherPaperJobModel | None = None
        self.attachments: list[tuple[UUID, str]] = []

    async def list_curricula(self, **kwargs: object) -> tuple[object, ...]:
        del kwargs
        return (curriculum(lessons=(lesson(1),)),)

    async def insert_job(self, values: dict[str, object]) -> StoredTeacherPaperInsert:
        self.values = values
        stored = TeacherPaperJobModel(**values)
        stored.created_at = NOW
        stored.updated_at = NOW
        stored.dispatch_message_id = "existing" if self.dispatch_attached else None
        if self.conflict:
            stored.request_fingerprint = "sha256:" + "f" * 64
        self.stored_job = stored
        return StoredTeacherPaperInsert(
            StoredTeacherPaper(stored, ()),
            created=not self.dispatch_attached,
        )

    async def attach_dispatch_message(self, job_id: UUID, message_id: str) -> None:
        self.attachments.append((job_id, message_id))
        assert self.stored_job is not None
        self.stored_job.dispatch_message_id = message_id

    async def get(self, job_id: UUID) -> StoredTeacherPaper:
        assert self.stored_job is not None
        assert self.stored_job.id == job_id
        return StoredTeacherPaper(self.stored_job, ())


def job_service(
    dummy: DummySession,
    dispatcher: RecordingDispatcher,
    repository: object,
) -> TeacherPaperJobService:
    service = TeacherPaperJobService(
        session(dummy),
        cast(object, dispatcher),  # type: ignore[arg-type]
        create_generation_runtime(Settings(environment="test")),
    )
    service._repository = repository  # type: ignore[assignment]
    return service


def test_job_create_rejects_missing_and_ambiguous_curriculum() -> None:
    class ScopeRepository(CreateRepository):
        def __init__(self, records: tuple[object, ...]) -> None:
            super().__init__()
            self.records = records

        async def list_curricula(self, **kwargs: object) -> tuple[object, ...]:
            del kwargs
            return self.records

    for records, error in (
        ((), TeacherPaperCurriculumNotFoundError),
        ((curriculum(), curriculum()), TeacherPaperCurriculumAmbiguousError),
    ):
        service = job_service(DummySession(), RecordingDispatcher(), ScopeRepository(records))
        with pytest.raises(error):
            asyncio.run(
                service.create(
                    create_request(),
                    idempotency_key="scope-key",
                    principal=principal(),
                )
            )


def test_job_create_detects_winner_conflict_queue_failure_and_attached_duplicate() -> None:
    conflict_session = DummySession()
    conflict = job_service(conflict_session, RecordingDispatcher(), CreateRepository(conflict=True))
    with pytest.raises(TeacherPaperIdempotencyConflictError):
        asyncio.run(
            conflict.create(
                create_request(full=True),
                idempotency_key="conflict-key",
                principal=principal(),
            )
        )
    assert conflict_session.rollbacks == 1

    queue_session = DummySession()
    queue_failure = job_service(queue_session, RecordingDispatcher(fail=True), CreateRepository())
    with pytest.raises(TeacherPaperQueueUnavailableError):
        asyncio.run(
            queue_failure.create(
                create_request(),
                idempotency_key="queue-key",
                principal=principal(),
            )
        )

    duplicate_session = DummySession()
    dispatcher = RecordingDispatcher()
    duplicate = job_service(
        duplicate_session,
        dispatcher,
        CreateRepository(dispatch_attached=True),
    )
    result = asyncio.run(
        duplicate.create(
            create_request(),
            idempotency_key="duplicate-key",
            principal=principal(),
        )
    )
    assert result.deduplicated is True
    assert dispatcher.calls == []


def test_job_create_attaches_the_successful_queue_dispatch() -> None:
    dummy = DummySession()
    dispatcher = RecordingDispatcher()
    repository = CreateRepository()
    service = job_service(dummy, dispatcher, repository)

    result = asyncio.run(
        service.create(
            create_request(),
            idempotency_key="successful-queue-key",
            principal=principal(),
        )
    )

    assert result.deduplicated is False
    assert len(dispatcher.calls) == 1
    assert repository.attachments == [(dispatcher.calls[0], "message-id")]
    assert dummy.commits == 2


class JobCommandRepository:
    def __init__(self, record: StoredTeacherPaper) -> None:
        self.record = record
        self.cas_values: list[dict[str, object]] = []

    async def get(self, job_id: UUID) -> StoredTeacherPaper:
        assert job_id == JOB_ID
        return self.record

    async def cas_job(self, job_id: UUID, **kwargs: object) -> TeacherPaperJobModel:
        assert job_id == JOB_ID
        self.cas_values.append(cast(dict[str, object], kwargs["values"]))
        self.record.job.version += 1
        return self.record.job


def test_manual_advance_enforces_version_state_and_queue_availability() -> None:
    dummy = DummySession()
    active = StoredTeacherPaper(job(version=2), ())
    repository = JobCommandRepository(active)
    dispatcher = RecordingDispatcher()
    service = job_service(dummy, dispatcher, repository)

    with pytest.raises(TeacherPaperVersionConflictError):
        asyncio.run(service.advance(JOB_ID, expected_version=1, principal=principal()))
    active.job.status = PaperJobStatus.READY_FOR_REVIEW.value
    with pytest.raises(TeacherPaperStateConflictError):
        asyncio.run(service.advance(JOB_ID, expected_version=2, principal=principal()))
    active.job.status = PaperJobStatus.PREPARING.value
    result = asyncio.run(service.advance(JOB_ID, expected_version=2, principal=principal()))
    assert result.job.version == 3
    assert dispatcher.calls == [JOB_ID]
    assert dummy.commits == 1

    queue_service = job_service(DummySession(), RecordingDispatcher(fail=True), repository)
    with pytest.raises(TeacherPaperQueueUnavailableError):
        asyncio.run(queue_service.advance(JOB_ID, expected_version=3, principal=principal()))


def test_retry_rejects_stale_or_nonfailed_aggregate_before_provider_work() -> None:
    stale_record = StoredTeacherPaper(job(status=PaperJobStatus.FAILED.value, version=3), ())
    stale = job_service(DummySession(), RecordingDispatcher(), JobCommandRepository(stale_record))
    with pytest.raises(TeacherPaperVersionConflictError):
        asyncio.run(
            stale.retry(
                JOB_ID,
                expected_version=2,
                idempotency_key="retry-key",
                principal=principal(),
                generation_dispatcher=DeterministicGenerationDispatcher(),
            )
        )
    with pytest.raises(TeacherPaperStateConflictError):
        asyncio.run(
            stale.retry(
                JOB_ID,
                expected_version=3,
                idempotency_key="retry-key",
                principal=principal(),
                generation_dispatcher=DeterministicGenerationDispatcher(),
            )
        )


class WorkerRepository:
    def __init__(
        self,
        claimed: TeacherPaperJobModel | None,
        record: StoredTeacherPaper | None = None,
    ) -> None:
        self.claimed = claimed
        self.record = record
        self.releases: list[UUID] = []

    async def claim(self, *args: object, **kwargs: object) -> TeacherPaperJobModel | None:
        del args, kwargs
        return self.claimed

    async def get(self, job_id: UUID) -> StoredTeacherPaper:
        assert job_id == JOB_ID
        assert self.record is not None
        return self.record

    async def release(self, job_id: UUID, *, token: UUID) -> bool:
        assert job_id == JOB_ID
        self.releases.append(token)
        return True


def worker_service(dummy: DummySession, repository: WorkerRepository) -> TeacherPaperWorkerService:
    service = TeacherPaperWorkerService(
        session(dummy),
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
        create_generation_runtime(Settings(environment="test")),
        create_embedding_provider_registry(Settings(environment="test")),
        build_default_pipeline(),
    )
    service._repository = repository  # type: ignore[assignment]
    return service


def test_retry_happy_path_replaces_failed_slots_updates_counts_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.teacher_papers import service as service_module

    failed_job = job(status=PaperJobStatus.FAILED.value, version=3)
    failed_slot = paper_slot(status=PaperSlotStatus.FAILED.value)
    failed_slot.ordinal = 1
    record = StoredTeacherPaper(failed_job, (failed_slot,))
    repository = JobCommandRepository(record)
    replacements: list[UUID] = []

    async def replace_run(*args: object, **kwargs: object) -> TeacherPaperSlotModel:
        del args, kwargs
        replacements.append(failed_slot.id)
        return failed_slot

    async def counts(repository: object, job_id: UUID) -> dict[str, int]:
        del repository, job_id
        return {
            "generated_count": 0,
            "validated_count": 0,
            "candidate_count": 0,
            "approved_count": 0,
            "failed_count": 0,
            "total_tokens": 0,
            "cost_microusd": 0,
        }

    monkeypatch.setattr(service_module, "_replace_generation_run", replace_run)
    monkeypatch.setattr(service_module, "_aggregate_counts", counts)
    dummy = DummySession()
    dispatcher = RecordingDispatcher()
    service = job_service(dummy, dispatcher, repository)
    result = asyncio.run(
        service.retry(
            JOB_ID,
            expected_version=3,
            idempotency_key="retry-key",
            principal=principal(),
            generation_dispatcher=DeterministicGenerationDispatcher(),
        )
    )
    assert replacements == [failed_slot.id]
    assert result.job.version == 4
    assert repository.cas_values[-1]["status"] == PaperJobStatus.GENERATING.value
    assert dispatcher.calls == [JOB_ID]
    assert dummy.commits == 1


def test_replacement_generation_enforces_retry_and_cost_caps_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = create_generation_runtime(Settings(environment="test"))
    capped_slot = paper_slot()
    capped_slot.regeneration_count = 2
    with pytest.raises(TeacherPaperRetryLimitError):
        asyncio.run(
            _replace_generation_run(
                session(DummySession()),
                cast(TeacherPaperRepository, object()),
                job(),
                capped_slot,
                runtime=runtime,
                generation_dispatcher=DeterministicGenerationDispatcher(),
                idempotency_key="replacement",
                actor_id=ACTOR_ID,
                reason="Bounded replacement.",
            )
        )

    class CostRepository:
        async def generation_run(self, run_id: UUID) -> GenerationRunModel:
            return GenerationRunModel(
                id=run_id,
                status=GenerationRunStatus.SUCCEEDED.value,
            )

    async def expensive_counts(repository: object, job_id: UUID) -> dict[str, int]:
        del repository, job_id
        return {"cost_microusd": 15_000_000}

    from exam_guru_api.teacher_papers import service as service_module

    monkeypatch.setattr(service_module, "_aggregate_counts", expensive_counts)
    uncapped_slot = paper_slot()
    uncapped_slot.current_generation_run_id = UUID(int=123)
    with pytest.raises(TeacherPaperCostLimitError):
        asyncio.run(
            _replace_generation_run(
                session(DummySession()),
                cast(object, CostRepository()),  # type: ignore[arg-type]
                job(),
                uncapped_slot,
                runtime=runtime,
                generation_dispatcher=DeterministicGenerationDispatcher(),
                idempotency_key="replacement",
                actor_id=ACTOR_ID,
                reason="Cost-capped replacement.",
            )
        )


def test_replacement_generation_records_both_failed_retry_and_fresh_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from exam_guru_api.teacher_papers import service as service_module

    runtime = create_generation_runtime(Settings(environment="test"))

    async def zero_counts(repository: object, job_id: UUID) -> dict[str, int]:
        del repository, job_id
        return {"cost_microusd": 0}

    monkeypatch.setattr(service_module, "_aggregate_counts", zero_counts)

    class GenerationService:
        modes: ClassVar[list[str]] = []

        def __init__(self, *args: object) -> None:
            del args

        async def retry(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.modes.append("retry")
            return SimpleNamespace(run=SimpleNamespace(id=UUID(int=401)))

        async def create(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.modes.append("create")
            return SimpleNamespace(run=SimpleNamespace(id=UUID(int=402)))

    monkeypatch.setattr(service_module, "GenerationRunService", GenerationService)

    class ReplacementRepository:
        def __init__(self, run_status: str) -> None:
            self.run_status = run_status
            self.links: list[UUID] = []
            self.active_slot = paper_slot()
            self.active_slot.current_generation_run_id = RUN_ID

        async def generation_run(self, run_id: UUID) -> GenerationRunModel:
            assert run_id == RUN_ID
            return GenerationRunModel(
                id=run_id,
                status=self.run_status,
                paper_blueprint_id=UUID(int=403),
                slot_id="slot-1",
                knowledge_chunk_ids=[str(UUID(int=404))],
                historical_question_ids=[],
            )

        async def find_slot(self, job_id: UUID, run_id: UUID) -> TeacherPaperSlotModel:
            assert (job_id, run_id) == (JOB_ID, RUN_ID)
            return self.active_slot

        async def cas_slot(
            self,
            active_slot: TeacherPaperSlotModel,
            values: dict[str, object],
        ) -> TeacherPaperSlotModel:
            for field, value in values.items():
                setattr(active_slot, field, value)
            active_slot.version += 1
            return active_slot

        async def add_slot_run(self, active_slot: TeacherPaperSlotModel, **kwargs: object) -> None:
            del active_slot
            self.links.append(cast(UUID, kwargs["generation_run_id"]))

    modes: list[str] = []
    for run_status, expected_run in (
        (GenerationRunStatus.FAILED.value, UUID(int=401)),
        (GenerationRunStatus.SUCCEEDED.value, UUID(int=402)),
    ):
        repository = ReplacementRepository(run_status)
        dummy = DummySession()
        replacement = asyncio.run(
            _replace_generation_run(
                session(dummy),
                cast(object, repository),  # type: ignore[arg-type]
                job(),
                repository.active_slot,
                runtime=runtime,
                generation_dispatcher=DeterministicGenerationDispatcher(),
                idempotency_key=f"replacement-{run_status}",
                actor_id=ACTOR_ID,
                reason="Auditable replacement.",
                commit=run_status == GenerationRunStatus.SUCCEEDED.value,
            )
        )
        modes.extend(GenerationService.modes)
        GenerationService.modes.clear()
        assert replacement.current_generation_run_id == expected_run
        assert repository.links == [expected_run]
        assert dummy.commits == int(run_status == GenerationRunStatus.SUCCEEDED.value)
        assert dummy.flushes == int(run_status == GenerationRunStatus.FAILED.value)
    assert modes == ["retry", "create"]


def test_worker_claim_and_state_dispatch_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="lease"):
        TeacherPaperWorkerService(
            session(DummySession()),
            DeterministicPaperGenerationDispatcher(),
            DeterministicGenerationDispatcher(),
            create_generation_runtime(Settings(environment="test")),
            create_embedding_provider_registry(Settings(environment="test")),
            build_default_pipeline(),
            actor_lease_seconds=0,
        )

    unclaimed = worker_service(DummySession(), WorkerRepository(None))
    assert asyncio.run(unclaimed.advance(JOB_ID)) is False

    calls: list[str] = []

    async def prepare(actual_job: object, token: UUID) -> None:
        del actual_job, token
        calls.append("prepare")

    async def collect(actual_job: object, token: UUID) -> None:
        del actual_job, token
        calls.append("collect")

    async def validate(actual_job: object, token: UUID) -> None:
        del actual_job, token
        calls.append("validate")

    preparing_job = job(status=PaperJobStatus.PREPARING.value)
    preparing = worker_service(
        DummySession(),
        WorkerRepository(preparing_job, StoredTeacherPaper(preparing_job, ())),
    )
    monkeypatch.setattr(preparing, "_prepare", prepare)
    assert asyncio.run(preparing.advance(JOB_ID)) is True

    generating_job = job(status=PaperJobStatus.GENERATING.value)
    missing_slots = worker_service(
        DummySession(),
        WorkerRepository(generating_job, StoredTeacherPaper(generating_job, ())),
    )
    monkeypatch.setattr(missing_slots, "_prepare", prepare)
    assert asyncio.run(missing_slots.advance(JOB_ID)) is True

    complete_slots = worker_service(
        DummySession(),
        WorkerRepository(
            generating_job,
            StoredTeacherPaper(generating_job, (paper_slot(),)),
        ),
    )
    monkeypatch.setattr(complete_slots, "_collect_generation", collect)
    assert asyncio.run(complete_slots.advance(JOB_ID)) is True

    checking_job = job(status=PaperJobStatus.CHECKING_ANSWERS.value)
    checking = worker_service(
        DummySession(),
        WorkerRepository(checking_job, StoredTeacherPaper(checking_job, (paper_slot(),))),
    )
    monkeypatch.setattr(checking, "_validate", validate)
    assert asyncio.run(checking.advance(JOB_ID)) is True
    terminal_job = job(status=PaperJobStatus.FAILED.value)
    terminal_repository = WorkerRepository(
        terminal_job,
        StoredTeacherPaper(terminal_job, ()),
    )
    terminal = worker_service(DummySession(), terminal_repository)
    assert asyncio.run(terminal.advance(JOB_ID)) is True
    assert len(terminal_repository.releases) == 1
    assert calls == ["prepare", "prepare", "collect", "validate"]


def test_worker_resume_reuses_blueprint_and_existing_slot_without_new_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from exam_guru_api.teacher_papers import service as service_module

    resolved = curriculum(lessons=(lesson(1),))
    selected = translate_teacher_scope(
        resolved,
        TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT),
    )
    specification = build_blueprint_specification(
        resolved,
        selected,
        PaperSettings(1, 45, PaperDifficulty.BALANCED),
        paper_reference="EGP-ABCD-12345678",
        request_fingerprint="sha256:" + "b" * 64,
    )
    blueprint = generate_blueprint(specification, seed=1)
    blueprint_id = UUID(int=501)
    blueprint_record = SimpleNamespace(
        id=blueprint_id,
        blueprint=serialize_blueprint(blueprint),
    )

    class BlueprintService:
        def __init__(self, actual_session: object) -> None:
            del actual_session

        async def get_blueprint(self, curriculum_id: UUID, paper_id: UUID) -> object:
            assert (curriculum_id, paper_id) == (
                resolved.curriculum_version_id,
                blueprint_id,
            )
            return blueprint_record

    monkeypatch.setattr(service_module, "BlueprintGenerationService", BlueprintService)

    active_job = job(status=PaperJobStatus.GENERATING.value)
    active_job.paper_blueprint_id = blueprint_id
    existing = paper_slot()
    existing.blueprint_slot_id = blueprint.slots[0].slot_id

    class ResumeRepository:
        async def list_curricula(self) -> tuple[object, ...]:
            return (resolved,)

        async def list_slots(self, job_id: UUID) -> tuple[TeacherPaperSlotModel, ...]:
            assert job_id == JOB_ID
            return (existing,)

        async def get(self, job_id: UUID) -> StoredTeacherPaper:
            assert job_id == JOB_ID
            return StoredTeacherPaper(active_job, (existing,))

        async def cas_job(self, job_id: UUID, **kwargs: object) -> TeacherPaperJobModel:
            assert job_id == JOB_ID
            del kwargs
            return active_job

    async def zero_counts(repository: object, job_id: UUID) -> dict[str, int]:
        del repository, job_id
        return {
            "generated_count": 0,
            "validated_count": 0,
            "candidate_count": 0,
            "approved_count": 0,
            "failed_count": 0,
            "total_tokens": 0,
            "cost_microusd": 0,
        }

    monkeypatch.setattr(service_module, "_aggregate_counts", zero_counts)
    dummy = DummySession()
    worker = worker_service(
        dummy,
        cast(WorkerRepository, ResumeRepository()),
    )
    asyncio.run(worker._prepare(active_job, UUID(int=502)))
    assert dummy.commits == 1


def test_worker_collection_and_validation_skip_already_terminal_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.teacher_papers import service as service_module

    async def counts(repository: object, job_id: UUID) -> dict[str, int]:
        del repository, job_id
        return {
            "generated_count": 1,
            "validated_count": 1,
            "candidate_count": 1,
            "approved_count": 0,
            "failed_count": 0,
            "total_tokens": 1,
            "cost_microusd": 0,
        }

    monkeypatch.setattr(service_module, "_aggregate_counts", counts)
    active_job = job(status=PaperJobStatus.GENERATING.value)

    class CollectionRepository:
        def __init__(self, active_slot: TeacherPaperSlotModel, run_status: str) -> None:
            self.active_slot = active_slot
            self.run_status = run_status
            self.slot_cas = 0
            self.job_cas = 0

        async def list_slots(self, job_id: UUID) -> tuple[TeacherPaperSlotModel, ...]:
            assert job_id == JOB_ID
            return (self.active_slot,)

        async def generation_run(self, run_id: UUID) -> GenerationRunModel:
            del run_id
            return GenerationRunModel(
                id=RUN_ID,
                status=self.run_status,
                failure_code="provider_unavailable",
            )

        async def get(self, job_id: UUID) -> StoredTeacherPaper:
            assert job_id == JOB_ID
            return StoredTeacherPaper(active_job, (self.active_slot,))

        async def cas_slot(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            self.slot_cas += 1
            return self.active_slot

        async def cas_job(self, *args: object, **kwargs: object) -> TeacherPaperJobModel:
            del args, kwargs
            self.job_cas += 1
            return active_job

    failed_slot = paper_slot(status=PaperSlotStatus.FAILED.value)
    failed_slot.current_generation_run_id = RUN_ID
    failed_repository = CollectionRepository(
        failed_slot,
        GenerationRunStatus.FAILED.value,
    )
    failed_worker = worker_service(
        DummySession(),
        cast(WorkerRepository, failed_repository),
    )
    failures: list[str] = []

    async def record_fail(job_id: UUID, token: UUID, *, code: str, detail: str) -> None:
        del job_id, token, detail
        failures.append(code)

    monkeypatch.setattr(failed_worker, "_fail", record_fail)
    asyncio.run(failed_worker._collect_generation(active_job, UUID(int=601)))
    assert failed_repository.slot_cas == 0
    assert failures == ["paper_generation_slot_failed"]

    checking_slot = paper_slot(status=PaperSlotStatus.CHECKING_ANSWERS.value)
    checking_slot.current_generation_run_id = RUN_ID
    success_repository = CollectionRepository(
        checking_slot,
        GenerationRunStatus.SUCCEEDED.value,
    )
    success_worker = worker_service(
        DummySession(),
        cast(WorkerRepository, success_repository),
    )
    validations: list[UUID] = []

    async def record_validation(actual_job: TeacherPaperJobModel, token: UUID) -> None:
        del actual_job
        validations.append(token)

    monkeypatch.setattr(success_worker, "_validate", record_validation)
    asyncio.run(success_worker._collect_generation(active_job, UUID(int=602)))
    assert success_repository.slot_cas == 0
    assert success_repository.job_cas == 1
    assert validations == [UUID(int=602)]

    candidate_slot = paper_slot(status=PaperSlotStatus.AWAITING_REVIEW.value)
    candidate_slot.current_candidate_id = UUID(int=603)
    skip_repository = CollectionRepository(
        candidate_slot,
        GenerationRunStatus.SUCCEEDED.value,
    )
    skip_worker = worker_service(DummySession(), cast(WorkerRepository, skip_repository))
    asyncio.run(skip_worker._validate(active_job, UUID(int=604)))
    assert skip_repository.job_cas == 1

    failed_job = job(status=PaperJobStatus.FAILED.value)
    failed_repository.active_slot = failed_slot
    failed_repository.active_slot.current_candidate_id = None
    failed_repository_record = StoredTeacherPaper(failed_job, (failed_slot,))

    async def failed_get(job_id: UUID) -> StoredTeacherPaper:
        assert job_id == JOB_ID
        return failed_repository_record

    failed_repository.get = failed_get  # type: ignore[method-assign]
    terminal_worker = worker_service(
        DummySession(),
        cast(WorkerRepository, failed_repository),
    )
    asyncio.run(
        terminal_worker._fail(
            JOB_ID,
            UUID(int=605),
            code="already_failed",
            detail="Already failed.",
        )
    )


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            TeacherPaperContextUnavailableError(),
            "paper_generation_context_unavailable",
        ),
        (
            ActiveEmbeddingConfigUnavailableError(),
            "paper_generation_embedding_unavailable",
        ),
        (
            EmbeddingProviderUnavailableError(),
            "paper_generation_embedding_unavailable",
        ),
        (
            PaperScopeError("paper_generation_scope_invalid"),
            "paper_generation_scope_invalid",
        ),
        (RuntimeError("private"), "paper_generation_internal_error"),
    ],
)
def test_worker_normalizes_failures_and_always_releases_claim(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_code: str,
) -> None:
    dummy = DummySession()
    claimed = job(status=PaperJobStatus.PREPARING.value)
    repository = WorkerRepository(claimed, StoredTeacherPaper(claimed, ()))
    worker = worker_service(dummy, repository)
    failures: list[str] = []

    async def fail_prepare(actual_job: object, token: UUID) -> None:
        del actual_job, token
        raise error

    async def record_failure(
        job_id: UUID,
        token: UUID,
        *,
        code: str,
        detail: str,
    ) -> None:
        del job_id, token, detail
        failures.append(code)

    monkeypatch.setattr(worker, "_prepare", fail_prepare)
    monkeypatch.setattr(worker, "_fail", record_failure)
    assert asyncio.run(worker.advance(JOB_ID)) is True
    assert failures == [expected_code]
    assert len(repository.releases) == 1
    assert dummy.rollbacks == int(isinstance(error, RuntimeError) and type(error) is RuntimeError)


def test_recovery_bounds_isolates_dispatch_failures_and_reports_counts() -> None:
    for batch_size, lease in ((0, 601), (1, 0)):
        with pytest.raises(ValueError, match=r"batch_size|actor_lease_seconds"):
            TeacherPaperRecoveryService(
                session(DummySession()),
                DeterministicPaperGenerationDispatcher(),
                batch_size=batch_size,
                actor_lease_seconds=lease,
            )

    class RecoveryRepository:
        async def recoverable_job_ids(self, **kwargs: object) -> tuple[UUID, ...]:
            del kwargs
            return (JOB_ID, UUID(int=25_910_099))

    class SelectiveDispatcher:
        def dispatch(self, job_id: UUID) -> str:
            if job_id == JOB_ID:
                raise RuntimeError
            return "ok"

    dummy = DummySession()
    recovery = TeacherPaperRecoveryService(
        session(dummy),
        cast(object, SelectiveDispatcher()),  # type: ignore[arg-type]
        batch_size=2,
        actor_lease_seconds=601,
    )
    recovery._repository = RecoveryRepository()  # type: ignore[assignment]
    result = asyncio.run(recovery.recover())
    assert (result.scanned, result.dispatched, result.failures) == (2, 1, 1)
    assert dummy.commits == 1


def test_review_status_and_constructed_answer_snapshot_cover_safe_fallbacks() -> None:
    current = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    assert _review_status(current) == "awaiting_review"
    current.approved_count = 1
    assert _review_status(current) == "approved"
    current.slot_count = 2
    assert _review_status(current) == "in_review"
    current.status = PaperJobStatus.FAILED.value
    assert _review_status(current) == "failed_check"
    current.practice_paper_id = UUID(int=99)
    assert _review_status(current) == "draft_created"

    accepted = _content_snapshot(
        {
            "question_type": "short_answer",
            "stem": "Name the value.",
            "options": [],
            "answer": {
                "correct_option_id": None,
                "accepted_responses": ["four", "4"],
                "explanation": "Both forms are accepted.",
            },
            "marking": {"total_marks": 1, "criteria": []},
        }
    )
    assert accepted["answer"] == "four / 4"
    assert accepted["marking_guide"] == ["Review the proposed answer."]

    missing = _content_snapshot(
        {
            "question_type": "short_answer",
            "stem": "Name the value.",
            "options": [],
            "answer": {"correct_option_id": None, "accepted_responses": None},
        }
    )
    assert missing["answer"] == "Answer requires review"
    assert missing["explanation"] == "Explanation requires review"


def test_job_response_maps_passing_validation_and_safe_failure_message() -> None:
    active_job = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    active_job.resolution_snapshot["lessons"] = [{"number": 1, "title": "Whole numbers"}]
    active_slot = paper_slot(status=PaperSlotStatus.AWAITING_REVIEW.value)
    active_slot.lesson_number = 1
    active_slot.current_validation_run_id = UUID(int=701)

    class ValidationSession:
        async def get(self, model: object, identifier: UUID) -> ValidationRunModel:
            del model
            assert identifier == active_slot.current_validation_run_id
            return ValidationRunModel(id=identifier, overall_status="pass")

    class ResponseRepository:
        session = ValidationSession()

    response = asyncio.run(
        teacher_paper_job_response(
            cast(object, ResponseRepository()),  # type: ignore[arg-type]
            StoredTeacherPaper(active_job, (active_slot,)),
        )
    )
    assert response.slots[0].validation == "ready"
    assert response.slots[0].lesson == "Lesson 1 — Whole numbers"

    class MissingValidationSession:
        async def get(self, model: object, identifier: UUID) -> None:
            del model, identifier

    class MissingValidationRepository:
        session = MissingValidationSession()

    missing_validation = asyncio.run(
        teacher_paper_job_response(
            cast(object, MissingValidationRepository()),  # type: ignore[arg-type]
            StoredTeacherPaper(active_job, (active_slot,)),
        )
    )
    assert missing_validation.slots[0].validation is None

    active_job.status = PaperJobStatus.FAILED.value
    active_job.failure_code = "failed"
    active_job.failure_detail = None
    active_job.completed_at = NOW
    failed = asyncio.run(
        teacher_paper_job_response(
            cast(object, ResponseRepository()),  # type: ignore[arg-type]
            StoredTeacherPaper(active_job, ()),
        )
    )
    assert failed.failure is not None
    assert failed.failure.message == "The paper job failed safely."
    assert failed.progress[-1] == "failed"


def review_slot_source(
    *,
    validation_status: str | None,
    requires_revalidation: bool = False,
    candidate: bool = True,
) -> ReviewSlotSource:
    source_id = UUID(int=25_920_001)
    active_slot = paper_slot(status=PaperSlotStatus.AWAITING_REVIEW.value)
    active_slot.requires_revalidation = requires_revalidation
    active_slot.lesson_number = 1
    active_slot.blueprint_slot_id = "slot-1"
    generation = GenerationRunModel(
        id=UUID(int=25_920_002),
        candidate={
            "question_type": "multiple_choice",
            "stem": "Which response is supported?",
            "options": [
                {"option_id": "A", "text": "First"},
                {"option_id": "B", "text": "Second"},
            ],
            "answer": {
                "correct_option_id": "B",
                "accepted_responses": [],
                "explanation": "The source supports B.",
            },
            "marking": {
                "total_marks": 1,
                "criteria": [{"description": "Selects B."}],
            },
        },
        context_snapshot={
            "items": [
                {
                    "context_id": "knowledge_chunk:1",
                    "provenance": {
                        "source_document_id": str(source_id),
                        "page_number": 2,
                    },
                },
                {
                    "context_id": "knowledge_chunk:2",
                    "provenance": {
                        "source_document_id": str(source_id),
                        "page_number": 2,
                    },
                },
            ]
        },
        provider="deterministic-fake",
        model_version="fixture",
    )
    validation = (
        None
        if validation_status is None
        else ValidationRunModel(
            id=UUID(int=25_920_003),
            overall_status=validation_status,
        )
    )
    finding = ValidationFindingModel(
        id=UUID(int=25_920_004),
        code="subject.factual.verifier_unavailable",
        status="warn",
        message="Human review is required.",
        evidence=[],
    )
    candidate_model = (
        QuestionCandidateModel(
            id=generation.id,
            state="validated",
            version=2,
        )
        if candidate
        else None
    )
    return ReviewSlotSource(
        slot=active_slot,
        generation=generation,
        validation=validation,
        candidate=candidate_model,
        content=cast(dict[str, object], generation.candidate),
        findings=(finding,),
        filenames={},
        unit_title="Numbers",
        lesson_title="Whole numbers",
        taxonomy_title="Number skill",
    )


def test_friendly_validation_and_review_question_cover_unfinished_edits_and_source_dedup() -> None:
    unfinished = review_slot_source(validation_status=None, candidate=False)
    assert _friendly_validation(unfinished).status == "failed_check"
    failed_question = _review_question(job(), unfinished)
    assert failed_question.review_state == "failed_check"
    assert len(failed_question.sources) == 1
    assert failed_question.sources[0].filename == "Reviewed source material"

    ready = review_slot_source(validation_status="pass")
    assert _friendly_validation(ready).status == "ready"
    edited = review_slot_source(validation_status="warn", requires_revalidation=True)
    result = _friendly_validation(edited)
    assert result.status == "needs_attention"
    assert "previous validation" in result.findings[0].casefold()


class ReviewRepository:
    def __init__(self, record: StoredTeacherPaper, active_slot: TeacherPaperSlotModel) -> None:
        self.record = record
        self.active_slot = active_slot
        self.slot_updates: list[dict[str, object]] = []

    async def get(self, job_id: UUID) -> StoredTeacherPaper:
        assert job_id == JOB_ID
        return self.record

    async def find_slot(self, job_id: UUID, question_id: UUID) -> TeacherPaperSlotModel:
        assert job_id == JOB_ID
        del question_id
        return self.active_slot

    async def review_sources(self, job_id: UUID) -> tuple[ReviewSlotSource, ...]:
        assert job_id == JOB_ID
        return ()

    async def cas_job(self, job_id: UUID, **kwargs: object) -> TeacherPaperJobModel:
        assert job_id == JOB_ID
        values = cast(dict[str, object], kwargs["values"])
        for field, value in values.items():
            setattr(self.record.job, field, value)
        self.record.job.version += 1
        return self.record.job

    async def cas_slot(
        self,
        slot: TeacherPaperSlotModel,
        values: dict[str, object],
    ) -> TeacherPaperSlotModel:
        self.slot_updates.append(values)
        for field, value in values.items():
            setattr(slot, field, value)
        slot.version += 1
        return slot


class SuccessfulCandidateService:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def start_review(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.actions.append("start")

    async def edit(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.actions.append("edit")

    async def approve(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.actions.append("approve")

    async def reject(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.actions.append("reject")


class SuccessfulFeedbackService:
    calls: ClassVar[list[str]] = []

    def __init__(self, _session: object) -> None:
        pass

    async def record_action(self, **kwargs: object) -> object:
        self.calls.append(str(kwargs["action"]))
        return type("FeedbackMarker", (), {"id": UUID(int=25_930_099)})()


class RaisingCandidateService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def start_review(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise self.error

    async def edit(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise self.error

    async def approve(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise self.error

    async def reject(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise self.error


def review_service(repository: ReviewRepository) -> TeacherPaperReviewService:
    service = TeacherPaperReviewService(
        session(DummySession()),
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
        create_generation_runtime(Settings(environment="test")),
    )
    service._repository = repository  # type: ignore[assignment]
    return service


def edit_request() -> ReviewQuestionEditRequest:
    return ReviewQuestionEditRequest.model_validate(
        {
            "content": {
                "question_type": "multiple_choice",
                "stem": "Which answer is supported?",
                "options": [
                    {"option_id": "A", "text": "First"},
                    {"option_id": "B", "text": "Second"},
                ],
                "answer": "B",
                "explanation": "The source supports B.",
                "marks": 1,
                "marking_guide": ["Selects B."],
            },
            "reason_code": "ambiguous_wording",
            "note": "Clarify wording.",
            "expected_version": 3,
        }
    )


def test_review_get_candidate_and_draft_preconditions_fail_closed() -> None:
    no_blueprint = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    active_slot = paper_slot()
    active_slot.current_candidate_id = None
    service = review_service(ReviewRepository(StoredTeacherPaper(no_blueprint, ()), active_slot))
    with pytest.raises(TeacherPaperStateConflictError):
        asyncio.run(service.get(JOB_ID, principal=principal()))
    with pytest.raises(TeacherPaperStateConflictError):
        asyncio.run(service._require_candidate_slot(JOB_ID, RUN_ID, principal()))

    no_blueprint.version = 4
    with pytest.raises(TeacherPaperVersionConflictError):
        asyncio.run(
            service.create_draft(
                JOB_ID,
                ReviewPaperCreateDraftRequest(expected_version=3),
                principal=principal(),
            )
        )
    with pytest.raises(TeacherPaperStateConflictError):
        asyncio.run(
            service.create_draft(
                JOB_ID,
                ReviewPaperCreateDraftRequest(expected_version=4),
                principal=principal(),
            )
        )

    no_blueprint.paper_blueprint_id = UUID(int=801)
    no_blueprint.practice_paper_id = UUID(int=802)
    detail = asyncio.run(service.get(JOB_ID, principal=principal()))
    assert detail.draft is not None
    assert detail.draft.draft_id == UUID(int=802)


def test_review_source_requires_exact_candidate_and_validation_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_slot = paper_slot()
    repository = ReviewRepository(StoredTeacherPaper(job(), (active_slot,)), active_slot)
    service = review_service(repository)
    with pytest.raises(TeacherPaperStateConflictError):
        asyncio.run(service._review_source(JOB_ID, active_slot.id))

    valid_source = review_slot_source(validation_status="warn")

    async def sources(_job_id: UUID) -> tuple[ReviewSlotSource, ...]:
        return (valid_source,)

    monkeypatch.setattr(repository, "review_sources", sources)
    assert asyncio.run(service._review_source(JOB_ID, valid_source.slot.id)) is valid_source


def test_review_actions_normalize_candidate_version_state_and_revalidation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.teacher_papers import service as service_module

    active_job = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    active_slot = paper_slot(status=PaperSlotStatus.AWAITING_REVIEW.value)
    active_slot.current_candidate_id = UUID(int=25_930_001)
    repository = ReviewRepository(
        StoredTeacherPaper(active_job, (active_slot,)),
        active_slot,
    )
    service = review_service(repository)

    action_calls: tuple[tuple[str, Callable[[], Awaitable[object]]], ...] = (
        (
            "start",
            lambda: service.start(
                JOB_ID,
                RUN_ID,
                expected_version=2,
                principal=principal(),
            ),
        ),
        (
            "edit",
            lambda: service.edit(JOB_ID, RUN_ID, edit_request(), principal=principal()),
        ),
        (
            "approve",
            lambda: service.approve(
                JOB_ID,
                RUN_ID,
                expected_version=3,
                note=None,
                principal=principal(),
            ),
        ),
        (
            "reject",
            lambda: service.reject(
                JOB_ID,
                RUN_ID,
                ReviewQuestionRejectRequest(
                    expected_version=3,
                    reason_code="other_quality_issue",
                    note="Reject.",
                ),
                principal=principal(),
            ),
        ),
    )

    async def invoke(action: Callable[[], Awaitable[object]]) -> object:
        return await action()

    for _action, call in action_calls:
        for upstream, expected in (
            (ReviewCandidateVersionConflictError(), TeacherPaperVersionConflictError),
            (ReviewCandidateStateConflictError(), TeacherPaperStateConflictError),
        ):
            raising = RaisingCandidateService(upstream)
            monkeypatch.setattr(
                service_module, "ReviewCandidateService", lambda _, value=raising: value
            )
            with pytest.raises(expected):
                asyncio.run(invoke(call))

    for revalidation_error in (
        ReviewCandidateRevalidationRequiredError(),
        ValidationNotPassedError(active_slot.current_candidate_id),
    ):
        raising = RaisingCandidateService(revalidation_error)
        monkeypatch.setattr(
            service_module, "ReviewCandidateService", lambda _, value=raising: value
        )
        with pytest.raises(TeacherPaperRevalidationRequiredError):
            asyncio.run(
                service.approve(
                    JOB_ID,
                    RUN_ID,
                    expected_version=3,
                    note=None,
                    principal=principal(),
                )
            )

    active_slot.requires_revalidation = True
    with pytest.raises(TeacherPaperRevalidationRequiredError):
        asyncio.run(
            service.approve(
                JOB_ID,
                RUN_ID,
                expected_version=3,
                note=None,
                principal=principal(),
            )
        )


def test_review_actions_sync_successful_candidate_state_into_the_paper_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.teacher_papers import service as service_module

    active_job = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    active_slot = paper_slot(status=PaperSlotStatus.AWAITING_REVIEW.value)
    active_slot.current_candidate_id = UUID(int=25_930_001)
    repository = ReviewRepository(
        StoredTeacherPaper(active_job, (active_slot,)),
        active_slot,
    )
    dummy_session = DummySession()
    service = TeacherPaperReviewService(
        session(dummy_session),
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
        create_generation_runtime(Settings(environment="test")),
    )
    service._repository = repository  # type: ignore[assignment]
    candidate_service = SuccessfulCandidateService()
    monkeypatch.setattr(service_module, "ReviewCandidateService", lambda _: candidate_service)
    SuccessfulFeedbackService.calls.clear()
    monkeypatch.setattr(
        service_module,
        "SubjectQualityFeedbackService",
        SuccessfulFeedbackService,
    )
    feedback_source = review_slot_source(validation_status="warn")

    async def source_for_slot(_job_id: UUID, _slot_id: UUID) -> ReviewSlotSource:
        return feedback_source

    monkeypatch.setattr(service, "_review_source", source_for_slot)

    async def aggregate_counts(_repository: object, _job_id: UUID) -> dict[str, int]:
        return {
            "generated_count": 1,
            "validated_count": 1,
            "candidate_count": 1,
            "approved_count": 0,
            "failed_count": 0,
            "total_tokens": 10,
            "cost_microusd": 20,
        }

    class QuestionMarker:
        def model_copy(self, *, update: dict[str, object]) -> object:
            assert "quality_feedback_id" in update
            return self

    marker = cast(ReviewQuestionResponse, QuestionMarker())

    async def get_question(
        _job_id: UUID,
        _question_id: UUID,
        _principal: Principal,
    ) -> ReviewQuestionResponse:
        return marker

    monkeypatch.setattr(service_module, "_aggregate_counts", aggregate_counts)
    monkeypatch.setattr(service, "_get_question", get_question)

    assert (
        asyncio.run(
            service.start(
                JOB_ID,
                RUN_ID,
                expected_version=2,
                principal=principal(),
            )
        )
        is marker
    )
    assert (
        asyncio.run(service.edit(JOB_ID, RUN_ID, edit_request(), principal=principal())) is marker
    )
    active_slot.requires_revalidation = False
    assert (
        asyncio.run(
            service.approve(
                JOB_ID,
                RUN_ID,
                expected_version=3,
                note=None,
                principal=principal(),
            )
        )
        is marker
    )
    assert (
        asyncio.run(
            service.reject(
                JOB_ID,
                RUN_ID,
                ReviewQuestionRejectRequest(
                    expected_version=4,
                    reason_code="other_quality_issue",
                    note="Replace this question.",
                ),
                principal=principal(),
            )
        )
        is marker
    )

    assert candidate_service.actions == ["start", "edit", "approve", "reject"]
    assert [update["status"] for update in repository.slot_updates] == [
        PaperSlotStatus.IN_REVIEW.value,
        PaperSlotStatus.REVALIDATION_REQUIRED.value,
        PaperSlotStatus.APPROVED.value,
        PaperSlotStatus.REJECTED.value,
    ]
    assert repository.slot_updates[1]["requires_revalidation"] is True
    assert SuccessfulFeedbackService.calls == ["edit", "reject"]
    assert dummy_session.commits == 4


def test_meaningful_approval_confirmation_appends_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.teacher_papers import service as service_module

    active_job = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    active_slot = paper_slot(status=PaperSlotStatus.IN_REVIEW.value)
    active_slot.current_candidate_id = UUID(int=25_930_001)
    repository = ReviewRepository(StoredTeacherPaper(active_job, (active_slot,)), active_slot)
    service = review_service(repository)
    candidate_service = SuccessfulCandidateService()
    monkeypatch.setattr(service_module, "ReviewCandidateService", lambda _: candidate_service)
    SuccessfulFeedbackService.calls.clear()
    monkeypatch.setattr(
        service_module,
        "SubjectQualityFeedbackService",
        SuccessfulFeedbackService,
    )

    async def source_for_slot(_job_id: UUID, _slot_id: UUID) -> ReviewSlotSource:
        return review_slot_source(validation_status="warn")

    async def sync_slot(*_args: object, **_kwargs: object) -> None:
        pass

    class QuestionMarker:
        def model_copy(self, *, update: dict[str, object]) -> object:
            assert update["quality_feedback_id"] == UUID(int=25_930_099)
            return self

    async def get_question(
        _job_id: UUID,
        _question_id: UUID,
        _principal: Principal,
    ) -> ReviewQuestionResponse:
        return cast(ReviewQuestionResponse, QuestionMarker())

    monkeypatch.setattr(service, "_review_source", source_for_slot)
    monkeypatch.setattr(service, "_sync_slot", sync_slot)
    monkeypatch.setattr(service, "_get_question", get_question)
    result = asyncio.run(
        service.approve(
            JOB_ID,
            RUN_ID,
            expected_version=3,
            note="Answer, marking, wording, and source confirmed.",
            principal=principal(),
        )
    )
    assert isinstance(result, QuestionMarker)
    assert SuccessfulFeedbackService.calls == ["approve"]


def test_regeneration_dispatch_and_draft_creation_happy_and_queue_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from exam_guru_api.teacher_papers import service as service_module

    SuccessfulFeedbackService.calls.clear()
    monkeypatch.setattr(
        service_module,
        "SubjectQualityFeedbackService",
        SuccessfulFeedbackService,
    )

    async def source_for_slot(
        _service: TeacherPaperReviewService,
        _job_id: UUID,
        _slot_id: UUID,
    ) -> ReviewSlotSource:
        return review_slot_source(validation_status="warn")

    monkeypatch.setattr(TeacherPaperReviewService, "_review_source", source_for_slot)
    active_job = job(status=PaperJobStatus.READY_FOR_REVIEW.value, version=4)
    active_job.paper_blueprint_id = UUID(int=901)
    active_slot = paper_slot(status=PaperSlotStatus.APPROVED.value)
    active_slot.version = 4
    active_slot.current_candidate_id = UUID(int=902)
    active_slot.current_generation_run_id = UUID(int=903)
    record = StoredTeacherPaper(active_job, (active_slot,))
    repository = ReviewRepository(record, active_slot)
    replacement = paper_slot()
    replacement.version = 5
    replacement.current_generation_run_id = UUID(int=904)

    async def replace_run(*args: object, **kwargs: object) -> TeacherPaperSlotModel:
        del args, kwargs
        return replacement

    async def counts(repository: object, job_id: UUID) -> dict[str, int]:
        del repository, job_id
        return {
            "generated_count": 0,
            "validated_count": 0,
            "candidate_count": 0,
            "approved_count": 0,
            "failed_count": 0,
            "total_tokens": 0,
            "cost_microusd": 0,
        }

    monkeypatch.setattr(service_module, "_replace_generation_run", replace_run)
    monkeypatch.setattr(service_module, "_aggregate_counts", counts)
    dummy = DummySession()
    queue_failure = TeacherPaperReviewService(
        session(dummy),
        cast(object, RecordingDispatcher(fail=True)),  # type: ignore[arg-type]
        DeterministicGenerationDispatcher(),
        create_generation_runtime(Settings(environment="test")),
    )
    queue_failure._repository = repository  # type: ignore[assignment]
    with pytest.raises(TeacherPaperQueueUnavailableError):
        asyncio.run(
            queue_failure.regenerate(
                JOB_ID,
                RUN_ID,
                ReviewQuestionRegenerateRequest(
                    expected_version=4,
                    reason_code="answer_incorrect",
                    note="Replace safely.",
                ),
                idempotency_key="replacement-key",
                principal=principal(),
            )
        )
    assert dummy.commits == 1
    assert any(
        getattr(item, "action", None) == "teacher_paper.question_regenerated"
        for item in dummy.added
    )

    active_job.version = 6
    active_job.status = PaperJobStatus.READY_FOR_REVIEW.value
    active_slot.version = 4
    dispatcher = RecordingDispatcher()
    success = TeacherPaperReviewService(
        session(DummySession()),
        cast(object, dispatcher),  # type: ignore[arg-type]
        DeterministicGenerationDispatcher(),
        create_generation_runtime(Settings(environment="test")),
    )
    success._repository = repository  # type: ignore[assignment]
    regenerated = asyncio.run(
        success.regenerate(
            JOB_ID,
            RUN_ID,
            ReviewQuestionRegenerateRequest(
                expected_version=4,
                reason_code="answer_incorrect",
                note="Replace safely.",
            ),
            idempotency_key="replacement-key-2",
            principal=principal(),
        )
    )
    assert regenerated.question_id == replacement.current_generation_run_id
    assert dispatcher.calls == [JOB_ID]

    draft_id = UUID(int=905)

    class PublicationService:
        def __init__(self, actual_session: object) -> None:
            del actual_session

        async def create_draft(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(
                record=SimpleNamespace(draft=SimpleNamespace(paper_id=draft_id, version=1))
            )

    monkeypatch.setattr(service_module, "PaperPublicationService", PublicationService)
    active_job.status = PaperJobStatus.READY_FOR_REVIEW.value
    active_job.version = 8
    active_job.practice_paper_id = None
    active_job.slot_count = 1
    active_slot.status = PaperSlotStatus.APPROVED.value
    active_slot.current_candidate_id = UUID(int=906)
    active_slot.requires_revalidation = False
    draft_service = review_service(repository)
    draft = asyncio.run(
        draft_service.create_draft(
            JOB_ID,
            ReviewPaperCreateDraftRequest(expected_version=8),
            principal=principal(),
        )
    )
    assert draft.draft_id == draft_id
    assert draft.publication_path.endswith(str(draft_id))


def test_regeneration_and_question_lookup_version_and_not_found_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    active_job = job(status=PaperJobStatus.READY_FOR_REVIEW.value)
    active_slot = paper_slot()
    active_slot.version = 4
    active_slot.current_candidate_id = UUID(int=25_940_001)
    active_slot.current_generation_run_id = UUID(int=25_940_002)
    service = review_service(
        ReviewRepository(StoredTeacherPaper(active_job, (active_slot,)), active_slot)
    )
    with pytest.raises(TeacherPaperVersionConflictError):
        asyncio.run(
            service.regenerate(
                JOB_ID,
                RUN_ID,
                ReviewQuestionRegenerateRequest(
                    expected_version=3,
                    reason_code="answer_incorrect",
                    note="Replace.",
                ),
                idempotency_key="replacement-key",
                principal=principal(),
            )
        )

    exact = cast(ReviewQuestionResponse, SimpleNamespace(id=RUN_ID))

    async def exact_detail(job_id: UUID, *, principal: Principal) -> object:
        del job_id, principal
        return SimpleNamespace(questions=(exact,))

    monkeypatch.setattr(service, "get", exact_detail)
    assert asyncio.run(service._get_question(JOB_ID, RUN_ID, principal())) is exact

    async def empty_detail(job_id: UUID, *, principal: Principal) -> object:
        del job_id, principal
        return SimpleNamespace(questions=())

    monkeypatch.setattr(service, "get", empty_detail)
    with pytest.raises(TeacherPaperQuestionNotFoundError):
        asyncio.run(service._get_question(JOB_ID, RUN_ID, principal()))

    replacement = cast(
        ReviewQuestionResponse,
        SimpleNamespace(id=active_slot.current_generation_run_id),
    )

    async def replacement_detail(job_id: UUID, *, principal: Principal) -> object:
        del job_id, principal
        return SimpleNamespace(questions=(replacement,))

    monkeypatch.setattr(service, "get", replacement_detail)
    assert asyncio.run(service._get_question(JOB_ID, RUN_ID, principal())) is replacement
