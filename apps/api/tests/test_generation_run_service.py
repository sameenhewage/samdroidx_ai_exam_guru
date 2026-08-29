import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.blueprints import generate_blueprint
from exam_guru_api.blueprints.domain import BlueprintSlot
from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.blueprints.serialization import serialize_blueprint
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationJobModel, GenerationRunModel
from exam_guru_api.generation.repository import (
    GenerationContextRecord,
    GenerationRunWrite,
    GenerationScopeRecord,
    StoredGeneration,
)
from exam_guru_api.generation.run_service import (
    GenerationBlueprintNotFoundError,
    GenerationBlueprintScopeMismatchError,
    GenerationContextCrossCurriculumError,
    GenerationContextLimitError,
    GenerationContextNotFoundError,
    GenerationContextNotReviewedError,
    GenerationContextScopeInactiveError,
    GenerationContextSourceUntrustedError,
    GenerationContextTaxonomyMismatchError,
    GenerationCreationResult,
    GenerationCurriculumInactiveError,
    GenerationCurriculumNotFoundError,
    GenerationIdempotencyConflictError,
    GenerationQueueUnavailableError,
    GenerationRetryLimitExceededError,
    GenerationRetryStateError,
    GenerationRunService,
    GenerationSlotNotFoundError,
    _context_snapshot,
    _same_generation_retry_request,
    _slot_snapshot,
)
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry, create_generation_runtime
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.retrieval.domain import RetrievalScope, RetrievalScopeSet, TaxonomyScope
from tests.test_blueprint_domain import (
    COMPETENCY_A,
    CURRICULUM_VERSION_ID,
    SKILL_A,
    make_uniform_specification,
)

ACTOR_ID = UUID(int=950_001)
BLUEPRINT_DB_ID = UUID(int=950_002)
CHUNK_ID = UUID(int=950_003)
QUESTION_ID = UUID(int=950_004)
SOURCE_ID = UUID(int=950_005)
BLOCK_ID = UUID(int=950_006)
NOW = datetime(2026, 1, 1, tzinfo=UTC)
PAPER = generate_blueprint(make_uniform_specification((1,), 1), seed=950)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class FakeGenerationRepository:
    def __init__(self) -> None:
        self.scope: GenerationScopeRecord | None = GenerationScopeRecord(
            curriculum_version_id=CURRICULUM_VERSION_ID,
            exam_id=UUID(int=950_010),
            medium_id=UUID(int=950_011),
            grade=5,
            medium="en",
            curriculum_active=True,
            exam_active=True,
            medium_active=True,
        )
        snapshot = serialize_blueprint(PAPER)
        self.blueprint: PaperBlueprintModel | None = PaperBlueprintModel(
            id=BLUEPRINT_DB_ID,
            curriculum_version_id=CURRICULUM_VERSION_ID,
            analytics_run_id=None,
            blueprint_id=PAPER.version.blueprint_id,
            schema_version=PAPER.version.schema_version,
            algorithm_version=PAPER.version.algorithm_version,
            config_version=PAPER.version.config_version,
            seed=PAPER.seed,
            total_marks=PAPER.total_marks,
            slot_count=len(PAPER.slots),
            specification_fingerprint="sha256:" + "a" * 64,
            input_fingerprint="sha256:" + "b" * 64,
            result_fingerprint="sha256:" + "c" * 64,
            specification={},
            blueprint=snapshot,
            taxonomy_snapshot=[],
            created_by=ACTOR_ID,
            created_at=NOW,
        )
        self.records: tuple[GenerationContextRecord, ...] = (
            context_record("knowledge_chunk", CHUNK_ID),
            context_record("historical_question", QUESTION_ID),
        )
        self.by_hash: dict[str, StoredGeneration] = {}
        self.force_conflict = False
        self.force_dispatch_cas_failure = False

    async def get_scope(self, curriculum_id: UUID) -> GenerationScopeRecord | None:
        del curriculum_id
        return self.scope

    async def get_blueprint(
        self,
        curriculum_id: UUID,
        blueprint_id: UUID,
    ) -> PaperBlueprintModel | None:
        del curriculum_id, blueprint_id
        return self.blueprint

    async def list_context_records(
        self,
        chunk_ids: tuple[UUID, ...],
        question_ids: tuple[UUID, ...],
    ) -> tuple[GenerationContextRecord, ...]:
        requested = set(chunk_ids) | set(question_ids)
        return tuple(record for record in self.records if record.id in requested)

    async def store_run(self, write: GenerationRunWrite, *, job_id: UUID) -> StoredGeneration:
        existing = self.by_hash.get(write.idempotency_key_hash)
        if existing is not None:
            if self.force_conflict:
                existing.run.request_fingerprint = "sha256:" + "f" * 64
            return StoredGeneration(existing.run, existing.job, created=False)
        values = write.values()
        run = GenerationRunModel(**values, created_at=NOW)
        job = GenerationJobModel(
            id=job_id,
            generation_run_id=run.id,
            curriculum_version_id=run.curriculum_version_id,
            status="queued",
            version=0,
            queue_message_id=None,
            failure_code=None,
            created_by=run.created_by,
            created_at=NOW,
            claimed_at=None,
            completed_at=None,
        )
        stored = StoredGeneration(run, job, created=True)
        self.by_hash[write.idempotency_key_hash] = stored
        return stored

    async def attach_queue_message(self, job_id: UUID, message_id: str) -> GenerationJobModel:
        for stored in self.by_hash.values():
            if stored.job.id == job_id:
                stored.job.queue_message_id = message_id
                stored.job.version += 1
                return stored.job
        raise AssertionError

    async def fail_dispatch(
        self,
        run_id: UUID,
        job_id: UUID,
        *,
        completed_at: datetime,
        failure_code: str,
    ) -> GenerationJobModel:
        del run_id
        if self.force_dispatch_cas_failure:
            raise RuntimeError("CAS")
        for stored in self.by_hash.values():
            if stored.job.id == job_id:
                stored.run.status = "failed"
                stored.run.failure_code = failure_code
                stored.run.completed_at = completed_at
                stored.job.status = "failed"
                stored.job.failure_code = failure_code
                stored.job.completed_at = completed_at
                return stored.job
        raise AssertionError

    async def get_run(self, curriculum_id: UUID, run_id: UUID) -> GenerationRunModel:
        return next(
            stored.run
            for stored in self.by_hash.values()
            if stored.run.curriculum_version_id == curriculum_id and stored.run.id == run_id
        )

    async def get_job(self, curriculum_id: UUID, job_id: UUID) -> GenerationJobModel:
        return next(
            stored.job
            for stored in self.by_hash.values()
            if stored.job.curriculum_version_id == curriculum_id and stored.job.id == job_id
        )

    async def list_runs(
        self,
        curriculum_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[GenerationRunModel, ...]:
        del curriculum_id, limit, offset
        return tuple(stored.run for stored in self.by_hash.values())

    async def list_attempts(
        self,
        curriculum_id: UUID,
        run_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[object, ...]:
        del curriculum_id, run_id, limit, offset
        return ()


def context_record(kind: str, record_id: UUID) -> GenerationContextRecord:
    return GenerationContextRecord(
        record_kind=kind,
        id=record_id,
        curriculum_version_id=CURRICULUM_VERSION_ID,
        text="Reviewed grounded context.",
        version=2,
        review_state=ReviewState.REVIEWED,
        competency_id=COMPETENCY_A,
        skill_id=SKILL_A,
        sub_skill_id=None,
        learning_concept_id=None,
        source_document_id=SOURCE_ID,
        source_curriculum_version_id=CURRICULUM_VERSION_ID,
        source_checksum_sha256="d" * 64,
        source_status=ExtractionStatus.TRUSTED,
        page_number=1,
        source_block_id=BLOCK_ID,
    )


def retrieval_filters(slot: BlueprintSlot = PAPER.slots[0]) -> RetrievalScope:
    scope = cast(GenerationScopeRecord, FakeGenerationRepository().scope)
    selected = slot.generation_constraints.curriculum_scope
    return RetrievalScope(
        grade=scope.grade,
        exam_id=scope.exam_id,
        medium_id=scope.medium_id,
        subject_id=scope.subject_id,
        curriculum_version_id=scope.curriculum_version_id,
        unit_ids=selected.unit_ids,
        lesson_ids=selected.lesson_ids,
        taxonomy=TaxonomyScope(
            competency_id=slot.taxonomy_target.competency_id,
            skill_id=slot.taxonomy_target.skill_id,
            sub_skill_id=slot.taxonomy_target.sub_skill_id,
            learning_concept_id=slot.taxonomy_target.learning_concept_id,
        ),
    )


def build_service(
    repository: FakeGenerationRepository,
    dispatcher: object | None = None,
) -> tuple[GenerationRunService, FakeSession, DeterministicGenerationDispatcher]:
    session = FakeSession()
    active_dispatcher = (
        DeterministicGenerationDispatcher("unit-generation-message")
        if dispatcher is None
        else dispatcher
    )
    service = GenerationRunService(
        cast(AsyncSession, session),
        create_generation_runtime(Settings(environment="test")),
        cast(DeterministicGenerationDispatcher, active_dispatcher),
    )
    service._repository = cast(object, repository)  # type: ignore[assignment]
    return service, session, cast(DeterministicGenerationDispatcher, active_dispatcher)


async def create(
    service: GenerationRunService,
    *,
    key: str = "unit-generation-key",
) -> GenerationCreationResult:
    return await service.create(
        CURRICULUM_VERSION_ID,
        paper_blueprint_id=BLUEPRINT_DB_ID,
        slot_id=PAPER.slots[0].slot_id,
        knowledge_chunk_ids=(CHUNK_ID,),
        historical_question_ids=(QUESTION_ID,),
        idempotency_key=key,
        actor_id=ACTOR_ID,
    )


def test_service_resolves_snapshots_audits_dispatches_and_deduplicates() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, session, dispatcher = build_service(repository)

        created = await create(service)
        duplicate = await create(service)
        fetched = await service.get_run(CURRICULUM_VERSION_ID, created.run.id)
        job = await service.get_job(CURRICULUM_VERSION_ID, created.job.id)
        listed = await service.list_runs(CURRICULUM_VERSION_ID, limit=10, offset=0)
        attempts = await service.list_attempts(
            CURRICULUM_VERSION_ID,
            created.run.id,
            limit=3,
            offset=0,
        )

        assert created.deduplicated is False
        assert duplicate.deduplicated is True
        assert created.run is duplicate.run is fetched is listed[0]
        assert created.job is duplicate.job is job
        assert attempts == ()
        assert dispatcher.dispatched == [(created.job.id, created.run.id)]
        assert created.run.blueprint_snapshot == serialize_blueprint(PAPER)
        assert created.run.blueprint_slot_snapshot["slot_id"] == PAPER.slots[0].slot_id
        assert created.run.knowledge_chunk_ids == [str(CHUNK_ID)]
        assert created.run.historical_question_ids == [str(QUESTION_ID)]
        items = cast(list[dict[str, object]], created.run.context_snapshot["items"])
        assert [item["record_kind"] for item in items] == [
            "historical_question",
            "knowledge_chunk",
        ]
        assert all(item["trust"] == "untrusted_data" for item in items)
        assert created.run.provider == "deterministic-fake"
        assert created.run.pricing_version == "deterministic-pricing-v1"
        assert created.run.request_fingerprint.startswith("sha256:")
        assert session.commits == 2
        audits = [value for value in session.added if isinstance(value, AdminAuditEventModel)]
        assert len(audits) == 1
        assert audits[0].action == "generation_run.created"
        assert "Reviewed grounded context" not in str(audits[0].payload)

        created.run.status = "failed"
        retried = await service.retry(
            CURRICULUM_VERSION_ID,
            created.run.id,
            idempotency_key="unit-retry-key",
            actor_id=ACTOR_ID,
        )
        assert retried.run.retry_of_run_id == created.run.id
        assert retried.run.request_fingerprint == created.run.request_fingerprint
        retry_audit = cast(AdminAuditEventModel, session.added[-1])
        assert retry_audit.action == "generation_run.retry_created"

    asyncio.run(exercise())


def test_programme_generation_accepts_only_context_in_immutable_cross_grade_scope_set() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        source_scope = RetrievalScope(
            grade=3,
            exam_id=UUID(int=951_010),
            medium_id=cast(GenerationScopeRecord, repository.scope).medium_id,
            subject_id=UUID(int=951_011),
            curriculum_version_id=UUID(int=951_012),
            taxonomy=TaxonomyScope(competency_id=UUID(int=951_013)),
        )
        repository.records = (
            replace(
                context_record("knowledge_chunk", CHUNK_ID),
                competency_id=source_scope.taxonomy.competency_id,
                skill_id=None,
                curriculum_version_id=source_scope.curriculum_version_id,
                source_curriculum_version_id=source_scope.curriculum_version_id,
                retrieval_scope=source_scope,
            ),
        )
        filters = RetrievalScopeSet(
            policy_version="grade5-scholarship-paper-ii.v1",
            scopes=(source_scope,),
        )
        service, _, _ = build_service(repository)

        created = await service.create(
            CURRICULUM_VERSION_ID,
            paper_blueprint_id=BLUEPRINT_DB_ID,
            slot_id=PAPER.slots[0].slot_id,
            knowledge_chunk_ids=(CHUNK_ID,),
            historical_question_ids=(),
            idempotency_key="programme-generation",
            actor_id=ACTOR_ID,
            retrieval_filters=filters,
        )

        assert created.run.context_snapshot["retrieval_filters"] == {
            "kind": "scope_set",
            "policy_version": "grade5-scholarship-paper-ii.v1",
            "scopes": [
                {
                    "curriculum_version_id": str(source_scope.curriculum_version_id),
                    "exam_id": str(source_scope.exam_id),
                    "grade": 3,
                    "lesson_ids": [],
                    "medium_id": str(source_scope.medium_id),
                    "subject_id": str(source_scope.subject_id),
                    "taxonomy": {
                        "competency_id": str(source_scope.taxonomy.competency_id),
                        "learning_concept_id": None,
                        "skill_id": None,
                        "sub_skill_id": None,
                    },
                    "unit_ids": [],
                }
            ],
        }
        items = cast(list[dict[str, object]], created.run.context_snapshot["items"])
        assert cast(dict[str, object], items[0]["retrieval_scope"])["curriculum_version_id"] == str(
            source_scope.curriculum_version_id
        )

        created.run.status = "failed"
        retried = await service.retry(
            CURRICULUM_VERSION_ID,
            created.run.id,
            idempotency_key="programme-generation-retry",
            actor_id=ACTOR_ID,
        )
        assert retried.run.request_fingerprint == created.run.request_fingerprint
        assert (
            retried.run.context_snapshot["retrieval_filters"]
            == created.run.context_snapshot["retrieval_filters"]
        )

    asyncio.run(exercise())


def test_retry_chain_allows_three_failed_retries_then_rejects_the_fourth() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, dispatcher = build_service(repository)

        current = (await create(service, key="bounded-root")).run
        assert current.retry_depth == 0
        for expected_depth in range(1, 4):
            current.status = "failed"
            current = (
                await service.retry(
                    CURRICULUM_VERSION_ID,
                    current.id,
                    idempotency_key=f"bounded-retry-{expected_depth}",
                    actor_id=ACTOR_ID,
                )
            ).run
            assert current.retry_depth == expected_depth

        current.status = "failed"
        with pytest.raises(GenerationRetryLimitExceededError):
            await service.retry(
                CURRICULUM_VERSION_ID,
                current.id,
                idempotency_key="bounded-retry-4",
                actor_id=ACTOR_ID,
            )

        assert len(repository.by_hash) == 4
        assert len(dispatcher.dispatched) == 4

    asyncio.run(exercise())


def test_retry_rejects_active_generation_config_drift_before_creating_a_child() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, dispatcher = build_service(repository)
        original = (await create(service, key="config-drift-root")).run
        original.status = "failed"

        active = service._runtime.active_config
        service._runtime = GenerationRuntimeRegistry(
            replace(active, model_version="changed-model-version")
        )
        with pytest.raises(GenerationRetryStateError):
            await service.retry(
                CURRICULUM_VERSION_ID,
                original.id,
                idempotency_key="config-drift-retry",
                actor_id=ACTOR_ID,
            )

        assert len(repository.by_hash) == 1
        assert len(dispatcher.dispatched) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field_name",
    [
        "curriculum_version_id",
        "paper_blueprint_id",
        "slot_id",
        "request_fingerprint",
        "blueprint_version",
        "blueprint_snapshot",
        "blueprint_slot_snapshot",
        "knowledge_chunk_ids",
        "historical_question_ids",
        "context_snapshot",
        "prompt_id",
        "prompt_version",
        "provider",
        "provider_version",
        "model",
        "model_version",
        "retrieval_version",
        "schema_version",
        "pricing_version",
        "input_microusd_per_million_tokens",
        "output_microusd_per_million_tokens",
        "generation_parameters",
        "max_attempts",
        "max_input_tokens",
        "max_output_tokens",
        "max_cost_microusd",
    ],
)
def test_retry_request_comparison_rejects_every_mutable_snapshot_difference(
    field_name: str,
) -> None:
    from tests.test_generation_repository import run_model, run_write

    predecessor = run_model()
    candidate = run_write()
    assert _same_generation_retry_request(predecessor, candidate)

    value = getattr(predecessor, field_name)
    if isinstance(value, UUID):
        replacement: object = UUID(int=value.int + 1)
    elif isinstance(value, str):
        replacement = f"{value}-changed"
    elif isinstance(value, int):
        replacement = value + 1
    elif isinstance(value, list):
        replacement = [*value, str(UUID(int=999_999))]
    elif isinstance(value, dict):
        replacement = {**value, "changed": True}
    else:
        raise AssertionError(f"unexpected retry snapshot type: {type(value)}")
    setattr(predecessor, field_name, replacement)

    assert not _same_generation_retry_request(predecessor, candidate)


def test_service_rejects_request_scope_blueprint_and_idempotency_violations() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, _ = build_service(repository)
        with pytest.raises(GenerationIdempotencyConflictError):
            await create(service, key="bad key")

        repository.scope = None
        with pytest.raises(GenerationCurriculumNotFoundError):
            await create(service, key="missing-scope")

        repository.scope = replace(
            cast(GenerationScopeRecord, FakeGenerationRepository().scope),
            curriculum_active=False,
        )
        with pytest.raises(GenerationCurriculumInactiveError):
            await create(service, key="inactive-scope")

        repository.scope = FakeGenerationRepository().scope
        repository.blueprint = None
        with pytest.raises(GenerationBlueprintNotFoundError):
            await create(service, key="missing-blueprint")

        repository.blueprint = FakeGenerationRepository().blueprint
        assert repository.blueprint is not None
        repository.blueprint.blueprint_id = "bp_" + "0" * 24
        with pytest.raises(GenerationBlueprintScopeMismatchError):
            await create(service, key="mismatched-blueprint")

        repository.blueprint = FakeGenerationRepository().blueprint
        with pytest.raises(GenerationSlotNotFoundError):
            await service.create(
                CURRICULUM_VERSION_ID,
                paper_blueprint_id=BLUEPRINT_DB_ID,
                slot_id="missing-slot",
                knowledge_chunk_ids=(CHUNK_ID,),
                historical_question_ids=(),
                idempotency_key="missing-slot",
                actor_id=ACTOR_ID,
            )

        pending = await create(service, key="pending-retry")
        with pytest.raises(GenerationRetryStateError):
            await service.retry(
                CURRICULUM_VERSION_ID,
                pending.run.id,
                idempotency_key="invalid-retry",
                actor_id=ACTOR_ID,
            )
        with pytest.raises(GenerationRetryStateError):
            await service.create(
                CURRICULUM_VERSION_ID,
                paper_blueprint_id=BLUEPRINT_DB_ID,
                slot_id=PAPER.slots[0].slot_id,
                knowledge_chunk_ids=(CHUNK_ID,),
                historical_question_ids=(QUESTION_ID,),
                idempotency_key="invalid-direct-retry",
                actor_id=ACTOR_ID,
                retry_of_run_id=pending.run.id,
            )

        pending.run.status = "failed"
        pending.run.retry_depth = 3
        with pytest.raises(GenerationRetryLimitExceededError):
            await service.create(
                CURRICULUM_VERSION_ID,
                paper_blueprint_id=BLUEPRINT_DB_ID,
                slot_id=PAPER.slots[0].slot_id,
                knowledge_chunk_ids=(CHUNK_ID,),
                historical_question_ids=(QUESTION_ID,),
                idempotency_key="invalid-direct-retry-depth",
                actor_id=ACTOR_ID,
                retry_of_run_id=pending.run.id,
            )

        await create(service, key="collision")
        repository.force_conflict = True
        with pytest.raises(GenerationIdempotencyConflictError):
            await create(service, key="collision")

        repository.scope = None
        with pytest.raises(GenerationCurriculumNotFoundError):
            await service.list_runs(CURRICULUM_VERSION_ID, limit=10, offset=0)

    asyncio.run(exercise())


def test_create_rejects_non_boolean_retrieval_filter_persistence_mode() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, _ = build_service(repository)

        with pytest.raises(
            GenerationIdempotencyConflictError,
            match="invalid retrieval filter persistence mode",
        ):
            await service.create(
                CURRICULUM_VERSION_ID,
                paper_blueprint_id=BLUEPRINT_DB_ID,
                slot_id=PAPER.slots[0].slot_id,
                knowledge_chunk_ids=(CHUNK_ID,),
                historical_question_ids=(QUESTION_ID,),
                idempotency_key="invalid-filter-persistence",
                actor_id=ACTOR_ID,
                _persist_retrieval_filters=cast(bool, 1),
            )

        assert repository.by_hash == {}

    asyncio.run(exercise())


def test_create_rejects_filters_outside_blueprint_or_active_medium() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, _ = build_service(repository)
        filters = retrieval_filters()
        invalid_filters: tuple[RetrievalScope | RetrievalScopeSet, ...] = (
            replace(filters, grade=4),
            RetrievalScopeSet(
                policy_version="wrong-medium-policy.v1",
                scopes=(replace(filters, medium_id=UUID(int=960_003)),),
            ),
        )

        for index, requested in enumerate(invalid_filters):
            with pytest.raises(GenerationBlueprintScopeMismatchError):
                await service.create(
                    CURRICULUM_VERSION_ID,
                    paper_blueprint_id=BLUEPRINT_DB_ID,
                    slot_id=PAPER.slots[0].slot_id,
                    knowledge_chunk_ids=(CHUNK_ID,),
                    historical_question_ids=(QUESTION_ID,),
                    idempotency_key=f"invalid-retrieval-scope-{index}",
                    actor_id=ACTOR_ID,
                    retrieval_filters=requested,
                )

        assert repository.by_hash == {}

    asyncio.run(exercise())


def test_retry_preserves_legacy_context_snapshots_without_retrieval_filters() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, dispatcher = build_service(repository)
        original = await service.create(
            CURRICULUM_VERSION_ID,
            paper_blueprint_id=BLUEPRINT_DB_ID,
            slot_id=PAPER.slots[0].slot_id,
            knowledge_chunk_ids=(CHUNK_ID,),
            historical_question_ids=(QUESTION_ID,),
            idempotency_key="legacy-filter-snapshot",
            actor_id=ACTOR_ID,
            _persist_retrieval_filters=False,
        )
        assert "retrieval_filters" not in original.run.context_snapshot

        original.run.status = "failed"
        retried = await service.retry(
            CURRICULUM_VERSION_ID,
            original.run.id,
            idempotency_key="legacy-filter-snapshot-retry",
            actor_id=ACTOR_ID,
        )

        assert retried.run.request_fingerprint == original.run.request_fingerprint
        assert "retrieval_filters" not in retried.run.context_snapshot
        assert len(dispatcher.dispatched) == 2

    asyncio.run(exercise())


def test_retry_rejects_malformed_persisted_retrieval_filters() -> None:
    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, _, dispatcher = build_service(repository)
        original = await create(service, key="malformed-filter-snapshot")
        original.run.status = "failed"
        original.run.context_snapshot["retrieval_filters"] = {"kind": "scope"}

        with pytest.raises(
            GenerationRetryStateError,
            match="persisted retrieval filters are invalid",
        ):
            await service.retry(
                CURRICULUM_VERSION_ID,
                original.run.id,
                idempotency_key="malformed-filter-snapshot-retry",
                actor_id=ACTOR_ID,
            )

        assert len(repository.by_hash) == 1
        assert len(dispatcher.dispatched) == 1

    asyncio.run(exercise())


def test_context_validation_rejects_unscoped_legacy_record_without_competency() -> None:
    record = replace(
        context_record("knowledge_chunk", CHUNK_ID),
        competency_id=None,
    )

    with pytest.raises(GenerationContextCrossCurriculumError):
        GenerationRunService._validate_context_records(
            retrieval_filters(),
            (CHUNK_ID,),
            (),
            (record,),
        )


def test_context_validation_rejects_cross_unit_and_lesson_records_before_generation() -> None:
    selected_unit = UUID(int=960_001)
    selected_lesson = UUID(int=960_002)
    slot = replace(
        PAPER.slots[0],
        generation_constraints=replace(
            PAPER.slots[0].generation_constraints,
            curriculum_scope=replace(
                PAPER.curriculum_scope,
                unit_ids=(selected_unit,),
                lesson_ids=(selected_lesson,),
            ),
        ),
    )
    with pytest.raises(GenerationContextCrossCurriculumError):
        GenerationRunService._validate_context_records(
            retrieval_filters(slot),
            (CHUNK_ID,),
            (),
            (replace(context_record("knowledge_chunk", CHUNK_ID), unit_id=UUID(int=1)),),
        )
    with pytest.raises(GenerationContextCrossCurriculumError):
        GenerationRunService._validate_context_records(
            retrieval_filters(slot),
            (CHUNK_ID,),
            (),
            (
                replace(
                    context_record("knowledge_chunk", CHUNK_ID),
                    unit_id=selected_unit,
                    lesson_id=UUID(int=2),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("records", "error"),
    [
        ((), GenerationContextNotFoundError),
        (
            (
                replace(
                    context_record("knowledge_chunk", CHUNK_ID),
                    curriculum_version_id=UUID(int=1),
                ),
            ),
            GenerationContextCrossCurriculumError,
        ),
        (
            (replace(context_record("knowledge_chunk", CHUNK_ID), review_state=ReviewState.DRAFT),),
            GenerationContextNotReviewedError,
        ),
        (
            (
                replace(
                    context_record("knowledge_chunk", CHUNK_ID),
                    source_status=ExtractionStatus.EXTRACTED,
                ),
            ),
            GenerationContextSourceUntrustedError,
        ),
        (
            (replace(context_record("knowledge_chunk", CHUNK_ID), scope_active=False),),
            GenerationContextScopeInactiveError,
        ),
        (
            (replace(context_record("knowledge_chunk", CHUNK_ID), skill_id=UUID(int=2)),),
            GenerationContextTaxonomyMismatchError,
        ),
    ],
)
def test_context_validation_rejects_missing_unreviewed_untrusted_or_spoofed_records(
    records: tuple[GenerationContextRecord, ...],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        GenerationRunService._validate_context_records(
            retrieval_filters(),
            (CHUNK_ID,),
            (),
            records,
        )


def test_context_and_slot_snapshot_boundaries_are_strict() -> None:
    oversized = tuple(
        replace(
            context_record("knowledge_chunk", UUID(int=951_000 + index)),
            text="x" * 8_000,
        )
        for index in range(5)
    )
    with pytest.raises(GenerationContextLimitError):
        _context_snapshot(oversized)
    with pytest.raises(GenerationBlueprintScopeMismatchError):
        _slot_snapshot({"slots": "not-an-array"}, PAPER.slots[0].slot_id)
    with pytest.raises(GenerationSlotNotFoundError):
        _slot_snapshot({"slots": []}, PAPER.slots[0].slot_id)


def test_duplicate_create_redispatches_a_committed_job_after_process_death() -> None:
    class SimulatedProcessDeath(BaseException):
        pass

    class CrashingDispatcher:
        def dispatch(self, job_id: UUID, run_id: UUID) -> str:
            del job_id, run_id
            raise SimulatedProcessDeath

    async def exercise() -> None:
        repository = FakeGenerationRepository()
        crashed_service, crashed_session, _ = build_service(repository, CrashingDispatcher())
        with pytest.raises(SimulatedProcessDeath):
            await create(crashed_service, key="crash-before-dispatch")

        stored = next(iter(repository.by_hash.values()))
        assert crashed_session.commits == 1
        assert stored.run.status == "pending"
        assert stored.job.status == "queued"
        assert stored.job.queue_message_id is None

        recovery_service, recovery_session, dispatcher = build_service(repository)
        recovered = await create(recovery_service, key="crash-before-dispatch")

        assert recovered.deduplicated is True
        assert recovered.job.queue_message_id == "unit-generation-message"
        assert dispatcher.dispatched == [(stored.job.id, stored.run.id)]
        assert recovery_session.commits == 1

    asyncio.run(exercise())


def test_dispatch_failure_remains_recoverable_and_is_sanitized_and_audited() -> None:
    class FailingDispatcher:
        def dispatch(self, job_id: UUID, run_id: UUID) -> str:
            del job_id, run_id
            raise RuntimeError("raw queue credential and exception")

    async def exercise() -> None:
        repository = FakeGenerationRepository()
        service, session, _ = build_service(repository, FailingDispatcher())
        with pytest.raises(GenerationQueueUnavailableError):
            await create(service, key="queue-failure")
        stored = next(iter(repository.by_hash.values()))
        assert stored.run.status == "pending"
        assert stored.job.status == "queued"
        assert stored.job.queue_message_id is None
        audit = cast(AdminAuditEventModel, session.added[-1])
        assert audit.action == "generation_job.dispatch_failed"
        assert audit.payload == {
            "failure_code": "queue_dispatch_failed",
            "job_id": str(stored.job.id),
        }
        assert "credential" not in str(audit.payload)

    asyncio.run(exercise())
