import asyncio
import json
import threading
from copy import deepcopy
from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.domain import TaxonomyTarget
from exam_guru_api.blueprints.service import BlueprintGenerationService
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.models import MediumModel
from exam_guru_api.generation.domain import GenerationRequest, GenerationResult
from exam_guru_api.generation.jobs import DeterministicGenerationDispatcher
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.generation.ports import ProviderError, ProviderFailureCode
from exam_guru_api.generation.repository import SqlAlchemyGenerationRepository
from exam_guru_api.generation.run_service import (
    GenerationCreationResult,
    GenerationRunService,
    GenerationWorkerService,
)
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry, create_generation_runtime
from exam_guru_api.knowledge.unit_review import KnowledgeReviewRequest, KnowledgeUnitReviewService
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.papers.publication_service import PaperPublicationService
from exam_guru_api.papers.review_service import ReviewCandidateService
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.service import ValidationRunService
from exam_guru_api.validation.validators import SchemaCompletenessValidator
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_projection_retrieval_postgres import (
    IndexedProjection,
    indexed_projection,
)
from tests.test_blueprint_domain import make_uniform_specification

pytestmark = pytest.mark.integration


class RecordingKnowledgeGenerator:
    def __init__(self, runtime: GenerationRuntimeRegistry) -> None:
        self.delegate = runtime.build_provider(runtime.active_config)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return self.delegate.generate(request)


async def create_knowledge_run(
    session: AsyncSession, source: IndexedProjection, runtime: GenerationRuntimeRegistry
) -> GenerationCreationResult:
    medium = await session.get(MediumModel, source.scope.medium_id)
    assert medium is not None
    specification = make_uniform_specification((1,), 1)
    specification = replace(
        specification,
        curriculum_scope=replace(
            specification.curriculum_scope,
            curriculum_version_id=source.scope.curriculum_version_id,
            grade=source.scope.grade,
            subject_id=source.scope.subject_id,
            medium=medium.code,
        ),
        taxonomy_requirements=(
            replace(
                specification.taxonomy_requirements[0],
                target=TaxonomyTarget(competency_id=source.scope.taxonomy.competency_id),
            ),
        ),
    )
    blueprint = await BlueprintGenerationService(session).create_blueprint(
        source.scope.curriculum_version_id,
        specification,
        seed=993,
        analytics_run_id=None,
        actor_id=ADMIN.subject_id,
    )
    slot = cast(list[dict[str, object]], blueprint.record.blueprint["slots"])[0]
    return await GenerationRunService(session, runtime, DeterministicGenerationDispatcher()).create(
        source.scope.curriculum_version_id,
        paper_blueprint_id=blueprint.record.id,
        slot_id=str(slot["slot_id"]),
        historical_question_ids=(),
        knowledge_chunk_ids=(),
        knowledge_projection_ids=(source.projection_id,),
        idempotency_key="knowledge-generation-" + str(uuid4()),
        actor_id=ADMIN.subject_id,
    )


def test_generation_worker_receives_exact_structured_knowledge_not_just_retrieval_text(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        base = create_generation_runtime(Settings(environment="test"))
        provider = RecordingKnowledgeGenerator(base)
        runtime = GenerationRuntimeRegistry(base.active_config, provider_factory=lambda _: provider)
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            created = await create_knowledge_run(session, source, runtime)
            job_id, run_id = created.job.id, created.run.id
            unit = await KnowledgeUnitService(session).get_unit(
                principal=ADMIN, unit_id=source.unit_id
            )
            assert created.run.context_snapshot["knowledge_projection_ids"] == [
                str(source.projection_id)
            ]
            assert (
                created.run.context_snapshot["schema_version"] == "generation-knowledge-context.v1"
            )
            assert await GenerationWorkerService(session, runtime).process(
                created.job.id, created.run.id
            )
            await session.refresh(created.run)
            assert created.run.status == "succeeded"
            assert len(provider.requests) == 1
            item = provider.requests[0].context.items[0]
            assert item.knowledge_evidence is not None
            assert item.knowledge_evidence.unit == unit
            assert item.knowledge_evidence.reference.projection_id == source.projection_id
            assert item.knowledge_evidence.reference.review_version == 1
            assert item.content_character_count > len(item.text)
            assert not await GenerationWorkerService(session, runtime).process(job_id, run_id)
            assert len(provider.requests) == 1
            validation = await ValidationRunService(
                session, ValidationPipeline((SchemaCompletenessValidator(),))
            ).create(
                source.scope.curriculum_version_id,
                generation_run_id=run_id,
                actor_id=ADMIN.subject_id,
            )
            grounding = cast(
                list[dict[str, object]], validation.run.input_snapshot["grounding_sources"]
            )
            assert grounding[0]["knowledge_evidence"] == item.knowledge_evidence.model_dump(
                mode="json"
            )

    asyncio.run(check())


def test_projection_review_withdrawal_blocks_new_publication_without_rewriting_published_history(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        runtime = create_generation_runtime(Settings(environment="test"))
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            created = await create_knowledge_run(session, source, runtime)
            run_id, blueprint_id, curriculum_id = (
                created.run.id,
                created.run.paper_blueprint_id,
                source.scope.curriculum_version_id,
            )
            assert await GenerationWorkerService(session, runtime).process(created.job.id, run_id)
            report = await ValidationRunService(
                session, ValidationPipeline((SchemaCompletenessValidator(),))
            ).create(curriculum_id, generation_run_id=run_id, actor_id=ADMIN.subject_id)
            review = ReviewCandidateService(session)
            await review.create(curriculum_id, validation_run_id=report.run.id, principal=ADMIN)
            await review.start_review(curriculum_id, run_id, expected_version=2, principal=ADMIN)
            await review.approve(
                curriculum_id,
                run_id,
                expected_version=3,
                note="Synthetic source-lineage proof",
                principal=ADMIN,
            )
            publication = PaperPublicationService(session)
            draft = await publication.create_draft(
                curriculum_id,
                paper_blueprint_id=blueprint_id,
                title="Synthetic verified-knowledge lineage paper",
                candidate_ids=(run_id,),
                idempotency_key="knowledge-paper-" + str(uuid4()),
                principal=ADMIN,
            )
            paper_id = draft.record.paper.id
            published = await publication.publish(
                curriculum_id, paper_id, expected_version=1, principal=ADMIN
            )
            snapshot = deepcopy(published.record.publication.snapshot)
            assert "knowledge_evidence" not in json.dumps(snapshot, ensure_ascii=False)
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=source.unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw curriculum approval after historical publication",
                ),
            )
            historical = await publication.get_publication(
                curriculum_id, paper_id, 1, principal=ADMIN
            )
            assert historical.publication.snapshot == snapshot
            await publication.revise(
                curriculum_id,
                paper_id,
                expected_version=1,
                candidate_ids=(run_id,),
                title=None,
                principal=ADMIN,
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


@pytest.mark.parametrize(
    "corruption",
    [
        "unit",
        "unit_fingerprint",
        "review_version",
        "review_fingerprint",
        "projection_ids",
        "provenance",
        "trust",
        "source_scope",
        "filter_decimal",
        "policy_type",
        "unbound_policy",
        "foreign_root",
    ],
)
def test_structured_context_sql_rejects_forged_evidence_and_scope(
    workspace_database_url: str, corruption: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            created = await create_knowledge_run(
                session, source, create_generation_runtime(Settings(environment="test"))
            )
            repository = SqlAlchemyGenerationRepository(session)
            assert await repository.context_lineage_is_current(created.run)
            snapshot = deepcopy(created.run.context_snapshot)
            item = cast(list[dict[str, object]], snapshot["items"])[0]
            evidence = cast(dict[str, object], item["knowledge_evidence"])
            reference = cast(dict[str, object], evidence["reference"])
            curriculum_id = source.scope.curriculum_version_id
            if corruption == "unit":
                cast(dict[str, object], evidence["unit"])["sequence"] = 99
            elif corruption == "unit_fingerprint":
                reference["unit_fingerprint"] = "f" * 64
            elif corruption == "review_version":
                item["record_version"] = True
            elif corruption == "review_fingerprint":
                reference["review_fingerprint"] = "f" * 64
            elif corruption == "projection_ids":
                snapshot["knowledge_projection_ids"] = [str(uuid4())]
            elif corruption == "provenance":
                cast(dict[str, object], item["provenance"])["source_document_id"] = str(uuid4())
            elif corruption == "trust":
                item["trust"] = "trusted"
            elif corruption == "source_scope":
                cast(dict[str, object], item["retrieval_scope"])["grade"] = 7
            elif corruption == "filter_decimal":
                filters = cast(dict[str, object], snapshot["retrieval_filters"])
                cast(dict[str, object], filters["scope"])["grade"] = 5.0
            elif corruption in {"policy_type", "unbound_policy"}:
                filters = cast(dict[str, object], snapshot["retrieval_filters"])
                snapshot["retrieval_filters"] = {
                    "kind": "scope_set",
                    "scopes": [filters["scope"]],
                    "policy_version": 1 if corruption == "policy_type" else "programme:" + "a" * 64,
                }
            else:
                other = await indexed_projection(session, grade=7)
                curriculum_id = other.scope.curriculum_version_id
            forged = GenerationRunModel(
                curriculum_version_id=curriculum_id,
                knowledge_chunk_ids=[],
                historical_question_ids=[],
                context_snapshot=snapshot,
            )
            assert not await repository.context_lineage_is_current(forged)
            assert not await repository.context_lineage_is_current(forged, lock_sources=True)

    asyncio.run(check())


def test_inflight_generation_rejects_withdrawn_knowledge_but_retains_known_accounting(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        base = create_generation_runtime(Settings(environment="test"))
        provider = base.build_provider(base.active_config)
        entered, release = threading.Event(), threading.Event()

        class PausedGenerator:
            def generate(self, request: GenerationRequest) -> GenerationResult:
                entered.set()
                assert release.wait(timeout=10)
                return provider.generate(request)

        runtime = GenerationRuntimeRegistry(
            base.active_config, provider_factory=lambda _: PausedGenerator()
        )
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            created = await create_knowledge_run(session, source, runtime)
            run_id, job_id = created.run.id, created.job.id

        async def work() -> bool:
            async with database_session(workspace_database_url) as session:
                return await GenerationWorkerService(session, runtime).process(job_id, run_id)

        task = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(work())))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            async with database_session(workspace_database_url) as session:
                await KnowledgeUnitReviewService(session).review(
                    principal=ADMIN,
                    unit_id=source.unit_id,
                    request=KnowledgeReviewRequest(
                        expected_version=1,
                        state="rejected",
                        confirmed_mapping=False,
                        reason="Withdraw knowledge while generation is in flight",
                    ),
                )
            release.set()
            assert await asyncio.wait_for(task, timeout=15)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        async with database_session(workspace_database_url) as session:
            run = await session.get(GenerationRunModel, run_id)
            assert run is not None
            assert run.status == "failed"
            assert run.failure_code == "generation_source_invalid"
            assert run.candidate is None
            attempts = tuple(
                await session.scalars(
                    select(GenerationAttemptModel).where(
                        GenerationAttemptModel.generation_run_id == run_id
                    )
                )
            )
            assert len(attempts) == 1
            assert attempts[0].accounting_known is True
            assert attempts[0].input_tokens is not None
            assert attempts[0].input_tokens > 0
            assert attempts[0].cost_microusd is not None

    asyncio.run(check())


def test_explicit_generation_retry_preserves_structured_context_and_failed_history(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        base = create_generation_runtime(Settings(environment="test"))

        class UnavailableGenerator:
            def generate(self, request: GenerationRequest) -> GenerationResult:
                raise ProviderError(ProviderFailureCode.UNAVAILABLE, identity=request.identity)

        unavailable = GenerationRuntimeRegistry(
            base.active_config, provider_factory=lambda _: UnavailableGenerator()
        )
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            created = await create_knowledge_run(session, source, unavailable)
            run_id = created.run.id
            before = deepcopy(created.run.context_snapshot)
            assert await GenerationWorkerService(
                session, unavailable, sleep=lambda _: None
            ).process(created.job.id, run_id)
            await session.refresh(created.run)
            assert created.run.status == "failed"
            retried = await GenerationRunService(
                session, base, DeterministicGenerationDispatcher()
            ).retry(
                source.scope.curriculum_version_id,
                run_id,
                idempotency_key="retry-knowledge-" + str(uuid4()),
                actor_id=ADMIN.subject_id,
            )
            assert retried.run.id != run_id
            assert retried.run.retry_of_run_id == run_id
            assert retried.run.context_snapshot == before
            assert await GenerationWorkerService(session, base).process(
                retried.job.id, retried.run.id
            )
            await session.refresh(retried.run)
            assert retried.run.status == "succeeded"
            await session.refresh(created.run)
            assert created.run.status == "failed"
            assert created.run.context_snapshot == before

    asyncio.run(check())


def test_mapping_withdrawal_after_generation_enqueue_prevents_the_model_call(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        base = create_generation_runtime(Settings(environment="test"))
        provider = RecordingKnowledgeGenerator(base)
        runtime = GenerationRuntimeRegistry(base.active_config, provider_factory=lambda _: provider)
        async with database_session(workspace_database_url) as session:
            source = await indexed_projection(session, grade=5)
            created = await create_knowledge_run(session, source, runtime)
            await KnowledgeUnitReviewService(session).review(
                principal=ADMIN,
                unit_id=source.unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw before generation uses the evidence",
                ),
            )
            assert await GenerationWorkerService(session, runtime).process(
                created.job.id, created.run.id
            )
            await session.refresh(created.run)
            assert created.run.status == "failed"
            assert provider.requests == []

    asyncio.run(check())
