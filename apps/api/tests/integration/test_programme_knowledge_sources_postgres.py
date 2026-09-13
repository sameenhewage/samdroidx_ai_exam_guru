import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import update

from exam_guru_api.curriculum.models import TaxonomyNodeModel
from exam_guru_api.knowledge.domain import HistoricalQuestion, Provenance, QuestionType, ReviewState
from exam_guru_api.knowledge.embeddings import EmbeddingResult
from exam_guru_api.knowledge.service import KnowledgePersistenceService
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.retrieval.domain import RetrievalScope
from exam_guru_api.retrieval.repository import PostgresHybridRetrievalRepository
from exam_guru_api.teacher_papers.models import AssessmentProgrammePolicyScopeModel
from exam_guru_api.teacher_papers.repository import TeacherPaperRepository
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_projection_retrieval_postgres import indexed_projection
from tests.integration.test_verified_knowledge_lineage_postgres import ACTOR, CONFIG, seed

pytestmark = pytest.mark.integration


def policy_scope(source: RetrievalScope) -> AssessmentProgrammePolicyScopeModel:
    return AssessmentProgrammePolicyScopeModel(
        id=uuid4(),
        source_grade=source.grade,
        source_exam_configuration_id=source.exam_id,
        source_medium_id=source.medium_id,
        source_subject_id=source.subject_id,
        source_curriculum_version_id=source.curriculum_version_id,
        source_unit_id=source.unit_ids[0] if source.unit_ids else None,
        source_lesson_id=source.lesson_ids[0] if source.lesson_ids else None,
        source_competency_id=source.taxonomy.competency_id,
        source_skill_id=source.taxonomy.skill_id,
        source_sub_skill_id=source.taxonomy.sub_skill_id,
        source_learning_concept_id=source.taxonomy.learning_concept_id,
    )


def test_programme_evidence_availability_includes_only_current_reviewed_projections(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=3)
            descriptor = policy_scope(source.scope)
            repository = TeacherPaperRepository(session)
            assert await repository.unavailable_programme_policy_scopes((descriptor,)) == ()
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=source.unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw synthetic programme evidence",
                ),
            )
            assert await repository.unavailable_programme_policy_scopes((descriptor,)) == (
                descriptor.id,
            )

    asyncio.run(check())


def test_programme_availability_matches_rag_for_reviewed_descendant_taxonomy(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            source = await seed(session, trusted=True)
            skill_id = uuid4()
            session.add(
                TaxonomyNodeModel(
                    id=skill_id,
                    curriculum_version_id=source.scope.curriculum_version_id,
                    parent_id=source.scope.taxonomy.competency_id,
                    level="skill",
                    code="EQUAL-PARTS",
                    title="Equal parts",
                    active=True,
                    review_state="reviewed",
                    created_by=ACTOR.subject_id,
                    updated_by=ACTOR.subject_id,
                )
            )
            await session.commit()
            persistence = KnowledgePersistenceService(session)
            chunk = (
                await persistence.import_chunk(
                    replace(source.chunk(), skill_id=skill_id), actor_id=ACTOR.subject_id
                )
            ).record
            await persistence.store_chunk_embedding(
                chunk.id,
                EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                actor_id=ACTOR.subject_id,
            )
            retrieved = await PostgresHybridRetrievalRepository(
                session, embedding_config=CONFIG
            ).retrieve_candidates(
                query="Fractions", query_vector=(1.0, 0.0, 0.0), filters=source.scope
            )
            assert {item.record.chunk_id for item in retrieved.vector_candidates} == {chunk.id}
            repository = TeacherPaperRepository(session)
            assert (
                await repository.unavailable_programme_policy_scopes((policy_scope(source.scope),))
                == ()
            )
            for wrong in (
                replace(source.scope, grade=7),
                replace(source.scope, medium_id=uuid4()),
                replace(source.scope, subject_id=uuid4()),
                replace(source.scope, exam_id=uuid4()),
            ):
                descriptor = policy_scope(wrong)
                assert await repository.unavailable_programme_policy_scopes((descriptor,)) == (
                    descriptor.id,
                )

    asyncio.run(check())


@pytest.mark.parametrize("legacy_trusted", [False, True])
@pytest.mark.parametrize("kind", ["chunk", "question"])
def test_programme_availability_does_not_treat_retained_legacy_vectors_as_current_evidence(
    workspace_database_url: str, legacy_trusted: bool, kind: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            source = await seed(session, trusted=legacy_trusted)
            persistence = KnowledgePersistenceService(session)
            if kind == "chunk":
                chunk = (
                    await persistence.import_chunk(source.chunk(), actor_id=ACTOR.subject_id)
                ).record
                await persistence.store_chunk_embedding(
                    chunk.id,
                    EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                    actor_id=ACTOR.subject_id,
                )
            else:
                question = HistoricalQuestion(
                    id=uuid4(),
                    curriculum_version_id=source.scope.curriculum_version_id,
                    year=2020,
                    paper_code="P1",
                    question_number="1",
                    text="Which answer is correct?",
                    question_type=QuestionType.SHORT_ANSWER,
                    marks=1,
                    review_state=ReviewState.REVIEWED,
                    competency_id=source.scope.taxonomy.competency_id,
                    provenance=Provenance(source.document_id, 1, source.block_id),
                )
                stored = (
                    await persistence.import_question(question, actor_id=ACTOR.subject_id)
                ).record
                await persistence.store_question_embedding(
                    stored.id,
                    EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                    actor_id=ACTOR.subject_id,
                )
            descriptor = policy_scope(source.scope)
            repository = TeacherPaperRepository(session)
            assert await repository.unavailable_programme_policy_scopes((descriptor,)) == ()
            await session.execute(
                update(TaxonomyNodeModel)
                .where(TaxonomyNodeModel.id == source.scope.taxonomy.competency_id)
                .values(active=False, review_state="deprecated")
            )
            await session.commit()
            assert await repository.unavailable_programme_policy_scopes((descriptor,)) == (
                descriptor.id,
            )

    asyncio.run(check())
