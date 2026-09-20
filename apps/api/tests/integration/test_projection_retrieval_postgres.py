import asyncio
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.domain import TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    TaxonomyNodeModel,
)
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingWorkerService,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import DeterministicEmbeddingProvider
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.retrieval.context import ContextLimits
from exam_guru_api.retrieval.domain import RetrievalScope, TaxonomyScope
from exam_guru_api.retrieval.embeddings import DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
from exam_guru_api.retrieval.evaluation import RelevanceJudgment, RetrievalEvalCase, evaluate_case
from exam_guru_api.retrieval.fusion import FusionConfig
from exam_guru_api.retrieval.repository import PostgresHybridRetrievalRepository
from exam_guru_api.retrieval.service import HybridRetrievalService
from tests.integration.test_embedding_jobs_postgres import _registry
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.workspace_fixtures import ADMIN, database_session
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)
from tests.test_document_understanding_contracts import counting_candidate, parse

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class IndexedProjection:
    document_id: UUID
    unit_id: UUID
    projection_id: UUID
    unreviewed_projection_id: UUID
    scope: RetrievalScope
    vector: tuple[float, ...]


async def indexed_projection(session: AsyncSession, *, grade: int) -> IndexedProjection:
    payload = counting_candidate()
    payload["observation"]["regions"][0]["exact_text"] += " groups"
    document_id, trusted_id, curriculum_id = await verified_source(
        session, grade=grade, content=parse(payload)
    )
    prepared = await KnowledgeUnitService(session).prepare_page(
        principal=ADMIN, document_id=document_id, page_number=1, expected_trusted_page_id=trusted_id
    )
    unit, projection = prepared.units[1], prepared.projections[1]
    competency_id = uuid4()
    session.add(
        TaxonomyNodeModel(
            id=competency_id,
            curriculum_version_id=curriculum_id,
            parent_id=None,
            level="competency",
            code="GROUPS-" + uuid4().hex[:16].upper(),
            title="Count equal groups",
            active=True,
            review_state=TaxonomyReviewState.REVIEWED,
            created_by=ADMIN.subject_id,
            updated_by=ADMIN.subject_id,
        )
    )
    await session.commit()
    await KnowledgeUnitReviewService(session).review(
        principal=ADMIN,
        unit_id=unit.id,
        request=KnowledgeReviewRequest(
            expected_version=0,
            state="reviewed",
            confirmed_mapping=True,
            competency_id=competency_id,
            reason="Synthetic fixed retrieval mapping",
        ),
    )
    config = DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
    registry = _registry()
    job = await EmbeddingJobService(
        session, registry, DeterministicEmbeddingDispatcher(), config
    ).create(
        curriculum_id,
        historical_question_ids=(),
        knowledge_chunk_ids=(),
        knowledge_projection_ids=(projection.id,),
        idempotency_key="retrieval-" + str(uuid4()),
        actor_id=ADMIN.subject_id,
    )
    assert await EmbeddingWorkerService(session, registry, config).process(job.job.id)
    curriculum = await session.get(CurriculumVersionModel, curriculum_id)
    assert curriculum is not None
    scope = RetrievalScope(
        grade=grade,
        exam_id=curriculum.exam_configuration_id,
        medium_id=unit.scope.medium_id,
        subject_id=unit.scope.subject_id,
        curriculum_version_id=curriculum_id,
        taxonomy=TaxonomyScope(competency_id=competency_id),
    )
    return IndexedProjection(
        document_id,
        unit.id,
        projection.id,
        prepared.projections[0].id,
        scope,
        DeterministicEmbeddingProvider().embed(projection.text, config).vector,
    )


def test_fixed_grade_five_projection_retrieval_has_full_recall_without_scope_or_review_leakage(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            allowed = await indexed_projection(session, grade=5)
            forbidden = await indexed_projection(session, grade=7)
            repository = PostgresHybridRetrievalRepository(
                session, embedding_config=DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG, candidate_limit=10
            )
            service = HybridRetrievalService(
                repository,
                fusion_config=FusionConfig(
                    limit=1, rank_constant=60, max_candidates_per_channel=10
                ),
                context_limits=ContextLimits(
                    max_items=1, max_total_characters=2000, max_item_characters=2000
                ),
            )
            result = await service.retrieve(
                query="groups", query_vector=allowed.vector, filters=allowed.scope
            )
            metrics = evaluate_case(
                RetrievalEvalCase(
                    name="fixed-grade5-reviewed-projection-groups",
                    query="groups",
                    filters=allowed.scope,
                    judgments=(RelevanceJudgment(allowed.projection_id),),
                    forbidden_chunk_ids=frozenset(
                        {forbidden.projection_id, allowed.unreviewed_projection_id}
                    ),
                ),
                result.ranked_candidates,
                k=1,
            )
            assert metrics.recall_at_k == 1.0
            assert metrics.precision_at_k == 1.0
            assert metrics.leakage_rate == 0.0
            assert {item.record.chunk_id for item in result.lexical_candidates} == {
                allowed.projection_id
            }
            assert {item.record.chunk_id for item in result.vector_candidates} == {
                allowed.projection_id
            }
            assert (
                result.ranked_candidates[0].record.provenance.source_document_id
                == allowed.document_id
            )
            reference = result.ranked_candidates[0].record.provenance.knowledge_reference
            assert reference is not None
            unit = await KnowledgeUnitService(session).get_unit(
                principal=ADMIN, unit_id=allowed.unit_id
            )
            assert reference.projection_id == allowed.projection_id
            assert reference.unit_id == unit.id
            assert reference.unit_fingerprint == unit.fingerprint
            assert reference.trusted_page_id == unit.trusted_page_id
            assert reference.trusted_fingerprint == unit.trusted_fingerprint
            assert result.context.items[0].provenances[0].knowledge_reference == reference
            for filters in (
                replace(allowed.scope, grade=7),
                replace(allowed.scope, medium_id=forbidden.scope.medium_id),
                replace(allowed.scope, subject_id=forbidden.scope.subject_id),
            ):
                excluded = await repository.retrieve_candidates(
                    query="groups", query_vector=allowed.vector, filters=filters
                )
                assert excluded.lexical_candidates == ()
                assert excluded.vector_candidates == ()

    asyncio.run(check())


def test_projection_retrieval_uses_current_reviewed_lesson_scope_before_ranking(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            unit_id, first_lesson, second_lesson = uuid4(), uuid4(), uuid4()
            curriculum_id = source.scope.curriculum_version_id
            session.add(
                CurriculumUnitModel(
                    id=unit_id,
                    curriculum_version_id=curriculum_id,
                    code="COUNTING",
                    title="Counting",
                    ordinal=1,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            await session.flush()
            for ordinal, lesson_id in enumerate((first_lesson, second_lesson), start=1):
                session.add(
                    CurriculumLessonModel(
                        id=lesson_id,
                        unit_id=unit_id,
                        curriculum_version_id=curriculum_id,
                        code=f"LESSON-{ordinal}",
                        title=f"Counting lesson {ordinal}",
                        ordinal=ordinal,
                        active=True,
                        created_by=ADMIN.subject_id,
                        updated_by=ADMIN.subject_id,
                    )
                )
            await session.commit()
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=source.unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="reviewed",
                    confirmed_mapping=True,
                    curriculum_unit_id=unit_id,
                    lesson_id=first_lesson,
                    competency_id=source.scope.taxonomy.competency_id,
                    reason="Refine this unchanged source to the first lesson",
                ),
            )
            scope = replace(source.scope, unit_ids=(unit_id,), lesson_ids=(first_lesson,))
            repository = PostgresHybridRetrievalRepository(
                session, embedding_config=DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
            )
            selected = await repository.retrieve_candidates(
                query="groups", query_vector=source.vector, filters=scope
            )
            assert {item.record.chunk_id for item in selected.vector_candidates} == {
                source.projection_id
            }
            reference = selected.vector_candidates[0].record.provenance.knowledge_reference
            assert reference is not None
            assert reference.review_version == 2
            other = await repository.retrieve_candidates(
                query="groups",
                query_vector=source.vector,
                filters=replace(scope, lesson_ids=(second_lesson,)),
            )
            assert other.lexical_candidates == ()
            assert other.vector_candidates == ()
            await session.execute(
                update(CurriculumLessonModel)
                .where(CurriculumLessonModel.id == first_lesson)
                .values(active=False)
            )
            await session.commit()
            stale = await repository.retrieve_candidates(
                query="groups", query_vector=source.vector, filters=scope
            )
            assert stale.lexical_candidates == ()
            assert stale.vector_candidates == ()

    asyncio.run(check())


@pytest.mark.parametrize("change", ["mapping", "sibling"])
def test_saved_projection_vectors_never_bypass_current_review_or_sibling_resolution(
    workspace_database_url: str, change: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            repository = PostgresHybridRetrievalRepository(
                session, embedding_config=DEFAULT_DETERMINISTIC_EMBEDDING_CONFIG
            )
            before = await repository.retrieve_candidates(
                query="groups", query_vector=source.vector, filters=source.scope
            )
            assert before.vector_candidates
            if change == "mapping":
                await KnowledgeUnitReviewService(session).review(
                    principal=ADMIN,
                    unit_id=source.unit_id,
                    request=KnowledgeReviewRequest(
                        expected_version=1,
                        state="rejected",
                        confirmed_mapping=False,
                        reason="Withdraw the retrieval mapping",
                    ),
                )
            else:
                await PageUnderstandingService(session).reopen(
                    principal=ADMIN,
                    document_id=source.document_id,
                    page_number=2,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Recheck an unresolved sibling",
                )
            after = await repository.retrieve_candidates(
                query="groups", query_vector=source.vector, filters=source.scope
            )
            assert after.lexical_candidates == ()
            assert after.vector_candidates == ()

    asyncio.run(check())
