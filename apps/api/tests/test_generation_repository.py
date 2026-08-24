import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.generation.models import (
    GenerationAttemptModel,
    GenerationJobModel,
    GenerationRunModel,
)
from exam_guru_api.generation.repository import (
    GenerationJobNotFoundError,
    GenerationRunNotFoundError,
    GenerationRunWrite,
    SqlAlchemyGenerationRepository,
)
from exam_guru_api.knowledge.domain import ChunkType, QuestionType, ReviewState
from exam_guru_api.knowledge.models import HistoricalQuestionModel, KnowledgeChunkModel

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RUN_ID = UUID(int=960_001)
JOB_ID = UUID(int=960_002)
CURRICULUM_ID = UUID(int=960_003)
BLUEPRINT_ID = UUID(int=960_004)
ACTOR_ID = UUID(int=960_005)
SOURCE_ID = UUID(int=960_006)
BLOCK_ID = UUID(int=960_007)
CHUNK_ID = UUID(int=960_008)
QUESTION_ID = UUID(int=960_009)


class Rows:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


class ExecuteResult:
    def __init__(self, *, row: object | None = None, rows: tuple[object, ...] = ()) -> None:
        self._row = row
        self._rows = rows

    def one_or_none(self) -> object | None:
        return self._row

    def all(self) -> list[object]:
        return list(self._rows)


class ScriptedSession:
    def __init__(
        self,
        *,
        scalar_results: tuple[object | None, ...] = (),
        execute_results: tuple[ExecuteResult, ...] = (),
        scalar_rows: tuple[object, ...] = (),
        get_result: object | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results)
        self.execute_results = list(execute_results)
        self.scalar_rows = scalar_rows
        self.get_result = get_result
        self.added: list[object] = []
        self.flushes = 0

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_results.pop(0)

    async def execute(self, statement: object) -> ExecuteResult:
        del statement
        return self.execute_results.pop(0)

    async def scalars(self, statement: object) -> Rows:
        del statement
        return Rows(self.scalar_rows)

    async def get(self, model: object, identifier: UUID) -> object | None:
        del model, identifier
        return self.get_result

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def run_write() -> GenerationRunWrite:
    return GenerationRunWrite(
        id=RUN_ID,
        curriculum_version_id=CURRICULUM_ID,
        paper_blueprint_id=BLUEPRINT_ID,
        retry_of_run_id=None,
        slot_id="slot-1",
        idempotency_key_hash="sha256:" + "a" * 64,
        request_fingerprint="sha256:" + "b" * 64,
        blueprint_version="bp_" + "c" * 24,
        blueprint_snapshot={"slots": [{"slot_id": "slot-1"}]},
        blueprint_slot_snapshot={"slot_id": "slot-1"},
        knowledge_chunk_ids=[str(CHUNK_ID)],
        historical_question_ids=[str(QUESTION_ID)],
        context_snapshot={"items": [{"text": "context"}], "trust": "untrusted_data"},
        prompt_id="question-generation",
        prompt_version="1.0.0",
        provider="deterministic-fake",
        provider_version="1.0.0",
        model="fixture-model",
        model_version="2026-01",
        retrieval_version="retrieval-v1",
        schema_version="question.v1",
        pricing_version="pricing-v1",
        input_microusd_per_million_tokens=0,
        output_microusd_per_million_tokens=0,
        generation_parameters={"temperature": 0.0, "max_output_tokens": 100, "seed": 1},
        max_attempts=3,
        max_input_tokens=1_000,
        max_output_tokens=500,
        max_cost_microusd=1_000,
        created_by=ACTOR_ID,
    )


def run_model() -> GenerationRunModel:
    return GenerationRunModel(**run_write().values(), created_at=NOW)


def job_model() -> GenerationJobModel:
    return GenerationJobModel(
        id=JOB_ID,
        generation_run_id=RUN_ID,
        curriculum_version_id=CURRICULUM_ID,
        status="queued",
        version=0,
        queue_message_id=None,
        failure_code=None,
        created_by=ACTOR_ID,
        created_at=NOW,
        claimed_at=None,
        completed_at=None,
    )


def source_model() -> SourceDocumentModel:
    return SourceDocumentModel(
        id=SOURCE_ID,
        checksum_sha256="d" * 64,
        object_key="sources/repository.pdf",
        original_filename="repository.pdf",
        content_type="application/pdf",
        size_bytes=100,
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.TRUSTED,
        curriculum_version_id=CURRICULUM_ID,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )


def chunk_model() -> KnowledgeChunkModel:
    return KnowledgeChunkModel(
        id=CHUNK_ID,
        curriculum_version_id=CURRICULUM_ID,
        chunk_type=ChunkType.EXPLANATION,
        text="Chunk context",
        educational_boundary="Boundary",
        sequence=0,
        source_document_id=SOURCE_ID,
        page_number=1,
        source_block_id=BLOCK_ID,
        review_state=ReviewState.REVIEWED,
        competency_id=UUID(int=960_010),
        version=2,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )


def question_model() -> HistoricalQuestionModel:
    return HistoricalQuestionModel(
        id=QUESTION_ID,
        curriculum_version_id=CURRICULUM_ID,
        year=2025,
        paper_code="P1",
        question_number="1",
        text="Question context",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=1,
        source_document_id=SOURCE_ID,
        page_number=1,
        source_block_id=BLOCK_ID,
        review_state=ReviewState.REVIEWED,
        competency_id=UUID(int=960_010),
        version=3,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )


def test_repository_loads_scope_blueprint_and_both_context_kinds() -> None:
    async def exercise() -> None:
        scope_session = ScriptedSession(
            execute_results=(
                ExecuteResult(
                    row=(
                        CURRICULUM_ID,
                        UUID(int=1),
                        UUID(int=2),
                        5,
                        "en",
                        True,
                        True,
                        True,
                    )
                ),
            )
        )
        scope = await SqlAlchemyGenerationRepository(cast(AsyncSession, scope_session)).get_scope(
            CURRICULUM_ID
        )
        assert scope is not None
        assert (scope.grade, scope.medium, scope.curriculum_active) == (5, "en", True)
        assert (
            await SqlAlchemyGenerationRepository(
                cast(
                    AsyncSession,
                    ScriptedSession(execute_results=(ExecuteResult(row=None),)),
                )
            ).get_scope(CURRICULUM_ID)
            is None
        )

        blueprint = PaperBlueprintModel(id=BLUEPRINT_ID)
        fetched = await SqlAlchemyGenerationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(blueprint,)))
        ).get_blueprint(CURRICULUM_ID, BLUEPRINT_ID)
        assert fetched is blueprint

        source = source_model()
        records = await SqlAlchemyGenerationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    execute_results=(
                        ExecuteResult(rows=((chunk_model(), source),)),
                        ExecuteResult(rows=((question_model(), source),)),
                    )
                ),
            )
        ).list_context_records((CHUNK_ID,), (QUESTION_ID,))
        assert [record.record_kind for record in records] == [
            "knowledge_chunk",
            "historical_question",
        ]
        assert [record.version for record in records] == [2, 3]
        assert all(record.source_checksum_sha256 == "d" * 64 for record in records)

        empty = await SqlAlchemyGenerationRepository(
            cast(AsyncSession, ScriptedSession())
        ).list_context_records((), ())
        assert empty == ()

    asyncio.run(exercise())


def test_repository_stores_created_and_idempotent_runs_with_defensive_failures() -> None:
    async def exercise() -> None:
        write = run_write()
        run = run_model()
        created_session = ScriptedSession(scalar_results=(run,))
        created = await SqlAlchemyGenerationRepository(
            cast(AsyncSession, created_session)
        ).store_run(write, job_id=JOB_ID)
        assert created.created is True
        assert created.run is run
        assert isinstance(created.job, GenerationJobModel)
        assert created_session.flushes == 1

        job = job_model()
        duplicate = await SqlAlchemyGenerationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None, run, job)))
        ).store_run(write, job_id=JOB_ID)
        assert duplicate == replace_stored(run, job, created=False)

        with pytest.raises(RuntimeError, match="winner"):
            await SqlAlchemyGenerationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None, None)))
            ).store_run(write, job_id=JOB_ID)
        with pytest.raises(RuntimeError, match="job"):
            await SqlAlchemyGenerationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None, run, None)))
            ).store_run(write, job_id=JOB_ID)

    asyncio.run(exercise())


def replace_stored(
    run: GenerationRunModel,
    job: GenerationJobModel,
    *,
    created: bool,
) -> object:
    from exam_guru_api.generation.repository import StoredGeneration

    return StoredGeneration(run, job, created)


def test_repository_cas_message_dispatch_failure_and_lookup_boundaries() -> None:
    async def exercise() -> None:
        run = run_model()
        job = job_model()
        attached = await SqlAlchemyGenerationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(job,)))
        ).attach_queue_message(JOB_ID, "message")
        assert attached is job
        fallback = await SqlAlchemyGenerationRepository(
            cast(
                AsyncSession,
                ScriptedSession(scalar_results=(None,), get_result=job),
            )
        ).attach_queue_message(JOB_ID, "message")
        assert fallback is job
        with pytest.raises(GenerationJobNotFoundError):
            await SqlAlchemyGenerationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None,), get_result=None))
            ).attach_queue_message(JOB_ID, "message")

        failed = await SqlAlchemyGenerationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(run, job)))
        ).fail_dispatch(
            RUN_ID,
            JOB_ID,
            completed_at=NOW,
            failure_code="queue_dispatch_failed",
        )
        assert failed is job
        with pytest.raises(RuntimeError, match="CAS"):
            await SqlAlchemyGenerationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None, job)))
            ).fail_dispatch(
                RUN_ID,
                JOB_ID,
                completed_at=NOW,
                failure_code="queue_dispatch_failed",
            )

        repository = SqlAlchemyGenerationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    scalar_results=(run, job),
                    scalar_rows=(run,),
                ),
            )
        )
        assert await repository.get_run(CURRICULUM_ID, RUN_ID) is run
        assert await repository.get_job(CURRICULUM_ID, JOB_ID) is job
        assert await repository.list_runs(CURRICULUM_ID, limit=10, offset=0) == (run,)

        attempt = GenerationAttemptModel(
            id=UUID(int=960_020),
            generation_run_id=RUN_ID,
            attempt_number=1,
        )
        attempt_repository = SqlAlchemyGenerationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    scalar_results=(run,),
                    scalar_rows=(attempt,),
                ),
            )
        )
        assert await attempt_repository.list_attempts(
            CURRICULUM_ID,
            RUN_ID,
            limit=3,
            offset=0,
        ) == (attempt,)

        with pytest.raises(GenerationRunNotFoundError):
            await SqlAlchemyGenerationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
            ).get_run(CURRICULUM_ID, RUN_ID)
        with pytest.raises(GenerationJobNotFoundError):
            await SqlAlchemyGenerationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
            ).get_job(CURRICULUM_ID, JOB_ID)

    asyncio.run(exercise())
