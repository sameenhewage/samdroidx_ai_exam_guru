import asyncio
from copy import deepcopy
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.generation.domain import ProgrammeContextBinding, projection_context_ids
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationRunModel
from exam_guru_api.generation.repository import SqlAlchemyGenerationRepository
from exam_guru_api.generation.run_service import GenerationRunService, GenerationWorkerService
from exam_guru_api.generation.runtime import create_generation_runtime
from exam_guru_api.infrastructure.migrations import _config_for_database
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.papers.publication_service import PaperPublicationService
from exam_guru_api.papers.review_service import ReviewCandidateService
from exam_guru_api.retrieval.domain import deserialize_retrieval_filters
from exam_guru_api.retrieval.embeddings import DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
from exam_guru_api.subject_quality.domain import (
    canonical_fingerprint,
    validation_input_from_eval_snapshot,
)
from exam_guru_api.subject_quality.models import SubjectQualityFeedbackModel
from exam_guru_api.teacher_papers.jobs import DeterministicPaperGenerationDispatcher
from exam_guru_api.teacher_papers.models import (
    AssessmentProgrammePolicyScopeModel,
    AssessmentProgrammePolicyVersionModel,
    TeacherPaperSlotRunModel,
)
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.service import ValidationRunService
from exam_guru_api.validation.validators import SchemaCompletenessValidator
from tests.integration.test_embedding_jobs_postgres import _registry
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.test_teacher_paper_aggregate import (
    ADMIN_HEADERS,
    COMPETENCY_ID,
    CURRICULUM_ID,
    EXAM_ID,
    LESSON_IDS,
    MEDIUM_ID,
    REVIEWER_HEADERS,
    SKILL_IDS,
    UNIT_ID,
    Seed,
    advance_and_run_slots,
    api_client,
    seed_scholarship_supporting_scopes,
)
from tests.integration.test_teacher_paper_aggregate import aggregate_seed as aggregate_seed

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def programme_context_run(aggregate_seed: Seed) -> UUID:
    supporting = asyncio.run(seed_scholarship_supporting_scopes(aggregate_seed.database_url))

    async def prepare() -> UUID:
        curriculum_id, unit_id, lesson_id, competency_id, skill_id = supporting[3]
        async with database_session(aggregate_seed.database_url) as session:
            document_id, trusted_id, _original_curriculum = await verified_source(session, grade=3)
            document = await session.get(SourceDocumentModel, document_id)
            assert document is not None
            document.curriculum_version_id = curriculum_id
            document.unit_id, document.lesson_id = unit_id, lesson_id
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
                    curriculum_unit_id=unit_id,
                    lesson_id=lesson_id,
                    competency_id=competency_id,
                    skill_id=skill_id,
                    reason="Synthetic supporting-grade programme evidence",
                ),
            )
            registry, config = _registry(), DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
            job = await EmbeddingJobService(
                session, registry, DeterministicEmbeddingDispatcher(), config
            ).create(
                curriculum_id,
                historical_question_ids=(),
                knowledge_chunk_ids=(),
                knowledge_projection_ids=(projection.id,),
                idempotency_key="programme-projection-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)
            return projection.id

    projection_id = asyncio.run(prepare())
    paper_dispatcher, generation_dispatcher = (
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
    )
    with api_client(aggregate_seed, paper_dispatcher, generation_dispatcher) as client:
        source_scopes = [
            ("paper_i", 1, (CURRICULUM_ID, UNIT_ID, LESSON_IDS[0], COMPETENCY_ID, SKILL_IDS[0])),
            *(
                ("paper_ii", ordinal, values)
                for ordinal, values in enumerate(
                    (
                        supporting[3],
                        supporting[4],
                        (CURRICULUM_ID, UNIT_ID, LESSON_IDS[1], COMPETENCY_ID, SKILL_IDS[1]),
                    ),
                    start=1,
                )
            ),
        ]
        created_policy = client.post(
            "/api/v1/admin/paper-generation/programme-policies",
            headers=ADMIN_HEADERS,
            json={
                "programme_exam_configuration_id": str(EXAM_ID),
                "medium_id": str(MEDIUM_ID),
                "anchor_curriculum_version_id": str(CURRICULUM_ID),
                "code": "G5-SCHOLARSHIP",
                "version": "knowledge.integration.v1",
                "title": "Synthetic structured Scholarship programme",
                "paper_i_profile_version": "ability.knowledge.v1",
                "paper_ii_profile_version": "coverage.knowledge.v1",
                "paper_i_weight": 1,
                "paper_ii_weight": 1,
                "scopes": [
                    {
                        "part": part,
                        "ordinal": ordinal,
                        "anchor_unit_id": str(UNIT_ID),
                        "anchor_lesson_id": str(LESSON_IDS[0 if part == "paper_i" else 1]),
                        "anchor_competency_id": str(COMPETENCY_ID),
                        "anchor_skill_id": str(SKILL_IDS[0 if part == "paper_i" else 1]),
                        "source_curriculum_version_id": str(values[0]),
                        "source_unit_id": str(values[1]),
                        "source_lesson_id": str(values[2]),
                        "source_competency_id": str(values[3]),
                        "source_skill_id": str(values[4]),
                    }
                    for part, ordinal, values in source_scopes
                ],
            },
        )
        assert created_policy.status_code == 201
        policy_id = created_policy.json()["id"]
        reviewed = client.post(
            f"/api/v1/admin/paper-generation/programme-policies/{policy_id}/review",
            headers=REVIEWER_HEADERS,
            json={"expected_version": 0},
        )
        assert reviewed.status_code == 200
        policy = reviewed.json()
        options = client.get("/api/v1/admin/paper-generation/options", headers=ADMIN_HEADERS).json()
        fingerprint = next(
            item["source_scope_fingerprint"]
            for item in options["paper_types"]
            if item["code"] == "scholarship_practice" and item["medium"] == "si"
        )
        created = client.post(
            "/api/v1/admin/paper-generation/jobs",
            headers={**ADMIN_HEADERS, "Idempotency-Key": "programme-knowledge-" + str(uuid4())},
            json={
                "target": {
                    "grade": 5,
                    "medium": "si",
                    "paper_type": "scholarship_practice",
                    "scholarship_mode": "paper_ii",
                },
                "source_scope_fingerprint": fingerprint,
                "scope": {"kind": "programme"},
                "settings": {
                    "paper_name": "Synthetic structured supporting-grade practice",
                    "mcq_count": 1,
                    "written_count": 0,
                    "structured_count": 0,
                    "duration_minutes": 45,
                    "difficulty": "balanced",
                },
            },
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["job_id"])
    terminal = asyncio.run(
        advance_and_run_slots(aggregate_seed, job_id, paper_dispatcher, generation_dispatcher)
    )
    assert terminal.status == "ready_for_review"

    async def inspect() -> UUID:
        async with database_session(aggregate_seed.database_url) as session:
            run = await session.scalar(
                select(GenerationRunModel)
                .join(
                    TeacherPaperSlotRunModel,
                    TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
                )
                .where(TeacherPaperSlotRunModel.paper_job_id == job_id)
            )
            assert run is not None
            assert str(projection_id) in cast(
                list[str], run.context_snapshot["knowledge_projection_ids"]
            )
            assert run.context_snapshot["schema_version"] == "generation-knowledge-context.v2"
            assert run.context_snapshot["programme_binding"] == {
                "schema_version": "knowledge-programme-binding.v1",
                "policy_id": policy_id,
                "policy_content_hash": policy["content_hash"],
                "scope_ids": sorted(
                    item["id"] for item in policy["scopes"] if item["part"] == "paper_ii"
                ),
            }
            return run.id

    return asyncio.run(inspect())


def test_reviewed_programme_binds_structured_supporting_grade_context(
    aggregate_seed: Seed, programme_context_run: UUID
) -> None:
    async def check() -> None:
        async with database_session(aggregate_seed.database_url) as session:
            run = await session.get(GenerationRunModel, programme_context_run)
            assert run is not None
            assert await SqlAlchemyGenerationRepository(session).context_lineage_is_current(run)
            assert await SqlAlchemyGenerationRepository(session).context_lineage_is_current(
                run, lock_sources=True
            )

    asyncio.run(check())


def test_teacher_programme_regeneration_retains_bound_scope_without_a_subject_input(
    aggregate_seed: Seed, programme_context_run: UUID
) -> None:
    async def original() -> tuple[UUID, dict[str, object]]:
        async with database_session(aggregate_seed.database_url) as session:
            run = await session.get(GenerationRunModel, programme_context_run)
            assert run is not None
            job_id = await session.scalar(
                select(TeacherPaperSlotRunModel.paper_job_id).where(
                    TeacherPaperSlotRunModel.generation_run_id == programme_context_run
                )
            )
            assert job_id is not None
            return job_id, deepcopy(run.context_snapshot)

    job_id, expected_context = asyncio.run(original())
    with api_client(
        aggregate_seed,
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
    ) as client:
        detail = client.get(f"/api/v1/admin/review-papers/{job_id}", headers=REVIEWER_HEADERS)
        assert detail.status_code == 200
        question = detail.json()["questions"][0]
        regenerated = client.post(
            f"/api/v1/admin/review-papers/{job_id}/questions/{question['id']}/regenerate",
            headers={
                **REVIEWER_HEADERS,
                "Idempotency-Key": "programme-regeneration-" + str(uuid4()),
            },
            json={
                "expected_version": question["aggregate_slot_version"],
                "reason_code": "answer_incorrect",
                "note": "Synthetic reviewed-programme regeneration proof",
            },
        )
        assert regenerated.status_code == 202
        replacement_id = UUID(regenerated.json()["question_id"])
        feedback_id = UUID(regenerated.json()["quality_feedback_id"])

    async def inspect() -> None:
        async with database_session(aggregate_seed.database_url) as session:
            replacement = await session.get(GenerationRunModel, replacement_id)
            assert replacement is not None
            assert replacement.context_snapshot == expected_context
            feedback = await session.get(SubjectQualityFeedbackModel, feedback_id)
            assert feedback is not None
            replay = validation_input_from_eval_snapshot(
                feedback.replay_input_snapshot,
                expected_curriculum_version_id=feedback.curriculum_version_id,
            )
            assert any(
                binding.programme_authorized
                and binding.curriculum_version_id != feedback.curriculum_version_id
                for binding in replay.context_scope_bindings
            )
            assert any(source.knowledge_evidence is not None for source in replay.grounding_sources)
            relabeled = deepcopy(feedback.replay_input_snapshot)
            proof = cast(dict[str, Any], relabeled["programme_context"])
            source = next(
                item
                for item in cast(list[dict[str, Any]], relabeled["grounding_sources"])
                if "knowledge_evidence" in item
            )
            alternate = next(
                scope
                for scope in proof["filters"]["scopes"]
                if scope["curriculum_version_id"] == str(CURRICULUM_ID)
            )
            proof["sources"][source["context_id"]] = alternate
            scoped = next(
                item
                for item in cast(list[dict[str, Any]], relabeled["context_scope_bindings"])
                if item["context_id"] == source["context_id"]
            )
            scoped.update(
                curriculum_version_id=alternate["curriculum_version_id"],
                subject_id=alternate["subject_id"],
                unit_id=alternate["unit_ids"][0],
                lesson_id=alternate["lesson_ids"][0],
                snapshot_unit_id=alternate["unit_ids"][0],
                snapshot_lesson_id=alternate["lesson_ids"][0],
            )
            with pytest.raises(ValueError, match="knowledge conflicts"):
                validation_input_from_eval_snapshot(
                    relabeled, expected_curriculum_version_id=CURRICULUM_ID
                )

            async def insert_probe(values: dict[str, object]) -> None:
                async with session.begin_nested():
                    session.add(SubjectQualityFeedbackModel(**values))
                    await session.flush()

            for corruption in ("binding", "sources", "grounding", "missing", "rehash_policy"):
                identifier = uuid4()
                values = {
                    column.name: deepcopy(getattr(feedback, column.name))
                    for column in SubjectQualityFeedbackModel.__table__.columns
                }
                values.update(
                    id=identifier,
                    action_fingerprint=canonical_fingerprint({"action": str(identifier)}),
                    feedback_fingerprint=canonical_fingerprint({"feedback": str(identifier)}),
                    idempotency_key_hash=canonical_fingerprint({"request": str(identifier)}),
                )
                snapshot = cast(dict[str, object], values["replay_input_snapshot"])
                proof = cast(dict[str, object], snapshot["programme_context"])
                if corruption == "binding":
                    cast(dict[str, object], proof["binding"])["policy_content_hash"] = "f" * 64
                elif corruption == "sources":
                    proof["sources"] = {}
                elif corruption == "grounding":
                    snapshot["grounding_sources"] = []
                elif corruption == "rehash_policy":
                    policy = cast(dict[str, object], proof["policy_snapshot"])
                    policy["title"] = "Invented policy evidence"
                    cast(dict[str, object], proof["binding"])["policy_content_hash"] = (
                        canonical_fingerprint(policy).removeprefix("sha256:")
                    )
                else:
                    snapshot.pop("programme_context")
                with pytest.raises(IntegrityError, match="programme replay"):
                    await insert_probe(values)

    asyncio.run(inspect())
    with api_client(
        aggregate_seed,
        DeterministicPaperGenerationDispatcher(),
        DeterministicGenerationDispatcher(),
    ) as client:
        response = client.get(
            "/api/v1/admin/subject-quality/feedback",
            params={"candidate_id": str(programme_context_run)},
            headers=REVIEWER_HEADERS,
        )
        assert response.status_code == 200
        feedback = next(item for item in response.json()["items"] if item["id"] == str(feedback_id))
        findings = feedback["findings_at_action"]
        promoted = client.post(
            f"/api/v1/admin/subject-quality/feedback/{feedback_id}/promote",
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "programme-eval-" + str(uuid4())},
            json={
                "expected_status": findings["overall_status"],
                "expected_finding_codes": sorted(
                    item["code"] for item in findings["findings"] if item["status"] != "pass"
                ),
                "defect_category": "scope_alignment",
            },
        )
        assert promoted.status_code == 201
        case_id = promoted.json()["eval_case_id"]
        approved = client.post(
            f"/api/v1/admin/subject-quality/eval-cases/{case_id}/approve",
            headers=ADMIN_HEADERS,
            json={"expected_version": 1},
        )
        assert approved.status_code == 200
        exported = client.get(
            "/api/v1/admin/subject-quality/eval-cases/export", headers=REVIEWER_HEADERS
        )
        assert exported.status_code == 200
        case = next(item for item in exported.json()["cases"] if item["eval_case_id"] == case_id)
        assert case["programme_context"]["binding"] == expected_context["programme_binding"]
        assert any(item.get("knowledge_evidence") for item in case["grounding_sources"])
        assert case["context_scope_bindings"]
        export_snapshot = {
            key: case[key]
            for key in (
                "candidate_id",
                "candidate",
                "blueprint",
                "subject_scope",
                "generated_scope",
                "context_scope_bindings",
                "grounding_sources",
                "duplicate_references",
                "programme_context",
            )
        }
        export_snapshot.update(
            schema_version="subject-quality-eval-input.v1", generation=case["generation_versions"]
        )
        exported_replay = validation_input_from_eval_snapshot(
            export_snapshot, expected_curriculum_version_id=CURRICULUM_ID
        )
        assert any(
            binding.programme_authorized for binding in exported_replay.context_scope_bindings
        )
        evaluated = client.post(
            "/api/v1/admin/subject-quality/eval-runs",
            headers=ADMIN_HEADERS,
            json={"case_ids": [case_id]},
        )
        assert evaluated.status_code == 201
        assert evaluated.json()["runner_version"] == "subject-quality-eval-runner.v2"


def test_retired_programme_cannot_publish_again_but_preserves_published_history(
    aggregate_seed: Seed, programme_context_run: UUID
) -> None:
    async def check() -> None:
        runtime = create_generation_runtime(Settings(environment="test"))
        async with database_session(aggregate_seed.database_url) as session:
            original = await session.get(GenerationRunModel, programme_context_run)
            assert original is not None
            binding = ProgrammeContextBinding.from_snapshot(
                original.context_snapshot["programme_binding"]
            )
            created = await GenerationRunService(
                session, runtime, DeterministicGenerationDispatcher()
            ).create(
                original.curriculum_version_id,
                paper_blueprint_id=original.paper_blueprint_id,
                slot_id=original.slot_id,
                knowledge_chunk_ids=tuple(UUID(value) for value in original.knowledge_chunk_ids),
                historical_question_ids=tuple(
                    UUID(value) for value in original.historical_question_ids
                ),
                knowledge_projection_ids=projection_context_ids(original.context_snapshot),
                programme_binding=binding,
                retrieval_filters=deserialize_retrieval_filters(
                    original.context_snapshot["retrieval_filters"]
                ),
                idempotency_key="programme-publication-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            run_id, blueprint_id, curriculum_id = (
                created.run.id,
                created.run.paper_blueprint_id,
                created.run.curriculum_version_id,
            )
            assert await GenerationWorkerService(session, runtime).process(created.job.id, run_id)
            validation = await ValidationRunService(
                session, ValidationPipeline((SchemaCompletenessValidator(),))
            ).create(curriculum_id, generation_run_id=run_id, actor_id=ADMIN.subject_id)
            review = ReviewCandidateService(session)
            await review.create(curriculum_id, validation_run_id=validation.run.id, principal=ADMIN)
            await review.start_review(curriculum_id, run_id, expected_version=2, principal=ADMIN)
            await review.approve(
                curriculum_id,
                run_id,
                expected_version=3,
                note="Synthetic programme publication mechanics",
                principal=ADMIN,
            )
            publication = PaperPublicationService(session)
            draft = await publication.create_draft(
                curriculum_id,
                paper_blueprint_id=blueprint_id,
                title="Synthetic programme-bound paper",
                candidate_ids=(run_id,),
                idempotency_key="programme-paper-" + str(uuid4()),
                principal=ADMIN,
            )
            paper_id = draft.record.paper.id
            published = await publication.publish(
                curriculum_id, paper_id, expected_version=1, principal=ADMIN
            )
            snapshot = deepcopy(published.record.publication.snapshot)
            await publication.revise(
                curriculum_id,
                paper_id,
                expected_version=1,
                candidate_ids=(run_id,),
                title=None,
                principal=ADMIN,
            )
            await session.execute(
                update(AssessmentProgrammePolicyVersionModel)
                .where(AssessmentProgrammePolicyVersionModel.id == binding.policy_id)
                .values(
                    state="retired",
                    lock_version=AssessmentProgrammePolicyVersionModel.lock_version + 1,
                )
            )
            with pytest.raises(IntegrityError, match="current verified"):
                await publication.publish(
                    curriculum_id, paper_id, expected_version=2, principal=ADMIN
                )
            historical = await publication.get_publication(
                curriculum_id, paper_id, 1, principal=ADMIN
            )
            assert historical.publication.snapshot == snapshot

    asyncio.run(check())


def test_programme_context_migration_refuses_to_discard_bound_history(
    aggregate_seed: Seed, programme_context_run: UUID
) -> None:
    async def snapshot() -> dict[str, object]:
        async with database_session(aggregate_seed.database_url) as session:
            run = await session.get(GenerationRunModel, programme_context_run)
            assert run is not None
            assert (
                await session.scalar(text("SELECT version_num FROM alembic_version"))
                == "0053_material_knowledge_review"
            )
            return deepcopy(run.context_snapshot)

    before = asyncio.run(snapshot())
    with pytest.raises(
        DBAPIError, match=r"cannot discard programme (knowledge context|evaluation replay) history"
    ):
        command.downgrade(
            _config_for_database(aggregate_seed.database_url), "0049_knowledge_generation"
        )
    assert asyncio.run(snapshot()) == before


@pytest.mark.parametrize(
    "corruption",
    [
        "policy",
        "policy_case",
        "policy_hash",
        "scope_ids",
        "duplicate_ids",
        "policy_label",
        "filter_scopes",
        "slot_part",
        "slot_taxonomy",
        "slot_lessons",
        "slot_grade_float",
        "evidence",
        "missing_binding",
    ],
)
def test_programme_context_rejects_forged_policy_scope_and_source(
    aggregate_seed: Seed, programme_context_run: UUID, corruption: str
) -> None:
    async def check() -> None:
        async with database_session(aggregate_seed.database_url) as session:
            run = await session.get(GenerationRunModel, programme_context_run)
            assert run is not None
            snapshot, slot = deepcopy(run.context_snapshot), deepcopy(run.blueprint_slot_snapshot)
            binding = cast(dict[str, object], snapshot["programme_binding"])
            filters = cast(dict[str, object], snapshot["retrieval_filters"])
            if corruption == "policy":
                binding["policy_id"] = str(uuid4())
            elif corruption == "policy_case":
                binding["policy_id"] = cast(str, binding["policy_id"]).upper()
            elif corruption == "policy_hash":
                binding["policy_content_hash"] = "f" * 64
            elif corruption == "scope_ids":
                binding["scope_ids"] = cast(list[str], binding["scope_ids"])[1:]
            elif corruption == "duplicate_ids":
                binding["scope_ids"] = cast(list[str], binding["scope_ids"])[:1] * 2
            elif corruption == "policy_label":
                filters["policy_version"] = "programme:" + "a" * 64
            elif corruption == "filter_scopes":
                filters["scopes"] = cast(list[object], filters["scopes"])[1:]
            elif corruption == "slot_part":
                slot["section_id"] = "paper_i-multiple_choice"
            elif corruption == "slot_taxonomy":
                cast(dict[str, object], slot["taxonomy_target"])["competency_id"] = str(uuid4())
            elif corruption == "slot_lessons":
                constraints = cast(dict[str, object], slot["generation_constraints"])
                cast(dict[str, object], constraints["curriculum_scope"])["lesson_ids"] = []
            elif corruption == "slot_grade_float":
                constraints = cast(dict[str, object], slot["generation_constraints"])
                cast(dict[str, object], constraints["curriculum_scope"])["grade"] = 5.0
            elif corruption == "evidence":
                items = cast(list[dict[str, object]], snapshot["items"])
                item = next(item for item in items if item["record_kind"] == "knowledge_projection")
                cast(dict[str, object], item["knowledge_evidence"])["unit"] = {}
            else:
                snapshot.pop("programme_binding")
            forged = GenerationRunModel(
                curriculum_version_id=run.curriculum_version_id,
                knowledge_chunk_ids=run.knowledge_chunk_ids,
                historical_question_ids=run.historical_question_ids,
                context_snapshot=snapshot,
                blueprint_slot_snapshot=slot,
            )
            repository = SqlAlchemyGenerationRepository(session)
            assert not await repository.context_lineage_is_current(forged)
            assert not await repository.context_lineage_is_current(forged, lock_sources=True)

    asyncio.run(check())


@pytest.mark.parametrize("target", ["policy", "source_lesson", "anchor_lesson", "source_taxonomy"])
def test_programme_context_locks_its_policy_and_declared_scope_until_commit(
    aggregate_seed: Seed, programme_context_run: UUID, target: str
) -> None:
    async def check() -> None:
        async with database_session(aggregate_seed.database_url) as session:
            run = await session.get(GenerationRunModel, programme_context_run)
            assert run is not None
            binding = ProgrammeContextBinding.from_snapshot(
                run.context_snapshot["programme_binding"]
            )
            mapping = await session.scalar(
                select(AssessmentProgrammePolicyScopeModel).where(
                    AssessmentProgrammePolicyScopeModel.policy_version_id == binding.policy_id,
                    AssessmentProgrammePolicyScopeModel.source_grade == 4,
                )
            )
            assert mapping is not None
            if target == "policy":
                statement = (
                    update(AssessmentProgrammePolicyVersionModel)
                    .where(AssessmentProgrammePolicyVersionModel.id == binding.policy_id)
                    .values(
                        state="retired",
                        lock_version=AssessmentProgrammePolicyVersionModel.lock_version + 1,
                    )
                )
            elif target == "source_taxonomy":
                statement = (
                    update(TaxonomyNodeModel)
                    .where(TaxonomyNodeModel.id == mapping.source_skill_id)
                    .values(active=False, review_state="deprecated")
                )
            else:
                identifier = (
                    mapping.source_lesson_id
                    if target == "source_lesson"
                    else mapping.anchor_lesson_id
                )
                statement = (
                    update(CurriculumLessonModel)
                    .where(CurriculumLessonModel.id == identifier)
                    .values(active=False)
                )
            assert await SqlAlchemyGenerationRepository(session).context_lineage_is_current(
                run, lock_sources=True
            )
            process: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def mutate() -> None:
                async with database_session(aggregate_seed.database_url) as other:
                    process.set_result(
                        cast(int, await other.scalar(text("SELECT pg_backend_pid()")))
                    )
                    await other.execute(statement)
                    await other.rollback()

            task = asyncio.create_task(mutate())
            try:
                writer_pid = await asyncio.wait_for(process, timeout=5)
                blocked = False
                for _attempt in range(100):
                    blocked = (
                        await session.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid))>0"),
                            {"pid": writer_pid},
                        )
                        is True
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked
                assert not task.done()
                await session.rollback()
                await asyncio.wait_for(task, timeout=5)
            finally:
                await session.rollback()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize(
    "change",
    [
        "retired_policy",
        "anchor_unit",
        "anchor_lesson",
        "anchor_taxonomy",
        "anchor_parent",
        "source_unit",
        "source_lesson",
        "source_taxonomy",
    ],
)
def test_programme_binding_requires_current_whole_declared_scope(
    aggregate_seed: Seed, programme_context_run: UUID, change: str
) -> None:
    async def check() -> None:
        async with database_session(aggregate_seed.database_url) as session:
            original = await session.get(GenerationRunModel, programme_context_run)
            assert original is not None
            snapshot = deepcopy(original.context_snapshot)
            snapshot["items"] = [
                item
                for item in cast(list[dict[str, object]], snapshot["items"])
                if item["record_kind"] == "knowledge_projection"
            ]
            run = GenerationRunModel(
                curriculum_version_id=original.curriculum_version_id,
                knowledge_chunk_ids=[],
                historical_question_ids=[],
                context_snapshot=snapshot,
                blueprint_slot_snapshot=original.blueprint_slot_snapshot,
            )
            binding = cast(dict[str, object], snapshot["programme_binding"])
            policy_id = UUID(cast(str, binding["policy_id"]))
            mapping = await session.scalar(
                select(AssessmentProgrammePolicyScopeModel).where(
                    AssessmentProgrammePolicyScopeModel.policy_version_id == policy_id,
                    AssessmentProgrammePolicyScopeModel.source_grade == 4,
                )
            )
            assert mapping is not None
            repository = SqlAlchemyGenerationRepository(session)
            assert await repository.context_lineage_is_current(run)
            try:
                if change == "retired_policy":
                    await session.execute(
                        update(AssessmentProgrammePolicyVersionModel)
                        .where(AssessmentProgrammePolicyVersionModel.id == policy_id)
                        .values(
                            state="retired",
                            lock_version=AssessmentProgrammePolicyVersionModel.lock_version + 1,
                        )
                    )
                elif change in {"anchor_unit", "source_unit"}:
                    identifier = (
                        mapping.anchor_unit_id
                        if change == "anchor_unit"
                        else mapping.source_unit_id
                    )
                    await session.execute(
                        update(CurriculumLessonModel)
                        .where(CurriculumLessonModel.unit_id == identifier)
                        .values(active=False)
                    )
                    await session.execute(
                        update(CurriculumUnitModel)
                        .where(CurriculumUnitModel.id == identifier)
                        .values(active=False)
                    )
                elif change in {"anchor_lesson", "source_lesson"}:
                    identifier = (
                        mapping.anchor_lesson_id
                        if change == "anchor_lesson"
                        else mapping.source_lesson_id
                    )
                    await session.execute(
                        update(CurriculumLessonModel)
                        .where(CurriculumLessonModel.id == identifier)
                        .values(active=False)
                    )
                elif change == "anchor_parent":
                    parent_id = uuid4()
                    session.add(
                        TaxonomyNodeModel(
                            id=parent_id,
                            curriculum_version_id=original.curriculum_version_id,
                            parent_id=None,
                            level="competency",
                            code="UNRELATED-ANCHOR",
                            title="Unrelated anchor",
                            active=True,
                            review_state="reviewed",
                            created_by=ADMIN.subject_id,
                            updated_by=ADMIN.subject_id,
                        )
                    )
                    await session.flush()
                    with pytest.raises(IntegrityError, match="hierarchy are immutable"):
                        await session.execute(
                            update(TaxonomyNodeModel)
                            .where(TaxonomyNodeModel.id == mapping.anchor_skill_id)
                            .values(parent_id=parent_id)
                        )
                    return
                else:
                    identifier = (
                        mapping.anchor_skill_id
                        if change == "anchor_taxonomy"
                        else mapping.source_skill_id
                    )
                    await session.execute(
                        update(TaxonomyNodeModel)
                        .where(TaxonomyNodeModel.id == identifier)
                        .values(active=False, review_state="deprecated")
                    )
                assert not await repository.context_lineage_is_current(run)
                assert not await repository.context_lineage_is_current(run, lock_sources=True)
            finally:
                await session.rollback()

    asyncio.run(check())
