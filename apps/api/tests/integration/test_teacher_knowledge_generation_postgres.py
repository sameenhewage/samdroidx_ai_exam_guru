import asyncio
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import SourceDocumentService
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationRunModel
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.retrieval.embeddings import DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
from exam_guru_api.teacher_papers.jobs import DeterministicPaperGenerationDispatcher
from exam_guru_api.teacher_papers.models import TeacherPaperSlotRunModel
from tests.integration.test_embedding_jobs_postgres import _registry
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.test_teacher_paper_aggregate import (
    ADMIN_HEADERS,
    COMPETENCY_ID,
    CURRICULUM_ID,
    LESSON_IDS,
    REVIEWER_HEADERS,
    SKILL_IDS,
    UNIT_ID,
    Seed,
    advance_and_run_slots,
    api_client,
    request_payload,
)
from tests.integration.test_teacher_paper_aggregate import aggregate_seed as aggregate_seed

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("legacy_available", [True, False])
def test_normal_teacher_lesson_generation_carries_reviewed_projection_evidence(
    aggregate_seed: Seed, legacy_available: bool
) -> None:
    lesson_index = 0 if legacy_available else 1

    async def prepare() -> UUID:
        async with database_session(aggregate_seed.database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session, grade=5)
            document = await session.get(SourceDocumentModel, document_id)
            assert document is not None
            document.curriculum_version_id = CURRICULUM_ID
            document.unit_id = UNIT_ID
            document.lesson_id = LESSON_IDS[lesson_index]
            document.metadata_scope_version += 1
            await session.commit()
            prepared = await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            unit, projection = prepared.units[1], prepared.projections[1]
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=unit.id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    curriculum_unit_id=UNIT_ID,
                    lesson_id=LESSON_IDS[lesson_index],
                    competency_id=COMPETENCY_ID,
                    skill_id=SKILL_IDS[lesson_index],
                    reason="Synthetic teacher-flow source classification",
                ),
            )
            registry = _registry()
            config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                CURRICULUM_ID,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection.id,),
                idempotency_key="teacher-projection-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)
            if not legacy_available:
                others = tuple(
                    await session.scalars(
                        select(SourceDocumentModel).where(
                            SourceDocumentModel.curriculum_version_id == CURRICULUM_ID,
                            SourceDocumentModel.lesson_id == LESSON_IDS[lesson_index],
                            SourceDocumentModel.id != document_id,
                        )
                    )
                )
                materials = SourceDocumentService(
                    session, Mock(spec=ObjectStorage), max_upload_bytes=1024
                )
                for previous in others:
                    await materials.remove_from_ai_use(
                        previous.id,
                        expected_version=previous.metadata_scope_version,
                        reason="Synthetic projection-only generation coverage",
                        actor_id=ADMIN.subject_id,
                    )
            return projection.id

    projection_id = asyncio.run(prepare())
    paper_dispatcher = DeterministicPaperGenerationDispatcher()
    generation_dispatcher = DeterministicGenerationDispatcher()
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            headers={**ADMIN_HEADERS, "Idempotency-Key": "teacher-knowledge-" + str(uuid4())},
            json=request_payload(
                client,
                scope={"kind": "selected_lessons", "lesson_numbers": [lesson_index + 1]},
                question_count=1,
            ),
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
    terminal = asyncio.run(
        advance_and_run_slots(aggregate_seed, job_id, paper_dispatcher, generation_dispatcher)
    )
    assert terminal.status == "ready_for_review"

    async def inspect(run_id: UUID | None = None) -> None:
        async with database_session(aggregate_seed.database_url) as session:
            statement = (
                select(GenerationRunModel)
                .join(
                    TeacherPaperSlotRunModel,
                    TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
                )
                .where(TeacherPaperSlotRunModel.paper_job_id == job_id)
            )
            if run_id is not None:
                statement = statement.where(GenerationRunModel.id == run_id)
            run = await session.scalar(statement)
            assert run is not None
            assert run.context_snapshot.get("knowledge_projection_ids") == [str(projection_id)]

    asyncio.run(inspect())
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        review = client.get(f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS)
        assert review.status_code == 200
        question = review.json()["questions"][0]
        regenerated = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/regenerate",
            headers={
                **REVIEWER_HEADERS,
                "Idempotency-Key": "teacher-knowledge-regenerate-" + str(uuid4()),
            },
            json={
                "expected_version": question["aggregate_slot_version"],
                "reason_code": "answer_incorrect",
                "note": "Synthetic structured-evidence regeneration proof",
            },
        )
        assert regenerated.status_code == 202
        replacement_id = UUID(regenerated.json()["question_id"])
    asyncio.run(inspect(replacement_id))
