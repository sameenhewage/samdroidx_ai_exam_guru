import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4

import anyio
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer

import exam_guru_api.knowledge.preparation_jobs as jobs
from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import SourceDocumentService
from exam_guru_api.documents.understanding_models import (
    PageUnderstandingStateModel,
    TrustedPageKnowledgeModel,
)
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.knowledge.preparation_models import (
    KnowledgePreparationJobModel,
    MaterialKnowledgeRequestModel,
)
from exam_guru_api.knowledge.preparation_requests import MaterialKnowledgeRequestRecorder
from exam_guru_api.knowledge.preparation_summary import get_material_knowledge_preparation
from exam_guru_api.knowledge.unit_models import KnowledgeProjectionModel, KnowledgeUnitModel
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from exam_guru_api.knowledge.units import KnowledgeScope
from tests.integration.test_document_understanding_postgres import approve_page, page_input
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.workspace_fixtures import (
    ADMIN,
    REVIEWER,
    add_curriculum,
    add_source,
    admit_curriculum,
    database_session,
)
from tests.test_document_understanding_contracts import counting_candidate, parse

pytestmark = pytest.mark.integration


@pytest.fixture
def preparation_database_url() -> Iterator[str]:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="knowledge_preparation_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            upgrade_database(url)
            yield url


class Dispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.ids: list[UUID] = []
        self.fail = fail

    def dispatch(self, identifier: UUID) -> str:
        if self.fail:
            raise RuntimeError("private broker details must not be persisted")
        self.ids.append(identifier)
        return str(identifier)


async def enrolled_source(session: AsyncSession, **kwargs: Any) -> tuple[UUID, UUID, UUID]:
    document_id, _trusted_id, curriculum_id = await verified_source(session, **kwargs)
    page = await session.get(PageUnderstandingStateModel, (document_id, 1))
    assert page is not None
    assert page.current_candidate_id is not None
    trusted = await approve_page(
        PageUnderstandingService(
            session, preparation_recorder=MaterialKnowledgeRequestRecorder(session)
        ),
        document_id,
        page.current_candidate_id,
        page.version,
    )
    return document_id, trusted.id, curriculum_id


async def one_job(session: AsyncSession, **kwargs: Any) -> tuple[UUID, UUID, UUID, UUID]:
    document_id, trusted_id, curriculum_id = await enrolled_source(session, **kwargs)
    identifiers = await jobs.discover_knowledge_preparation(session)
    assert len(identifiers) == 1
    return identifiers[0], document_id, trusted_id, curriculum_id


def test_discovery_requires_new_enrollment_whole_document_resolution_and_later_admission(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            await verified_source(session)
            document_id, _trusted_id, curriculum_id = await enrolled_source(
                session, resolved=False, admitted=False
            )
            assert await jobs.discover_knowledge_preparation(session) == ()
            await PageUnderstandingService(session).exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=2,
                expected_version=0,
                confirm_exclusion=True,
                reason="Resolve the last original page",
            )
            assert await jobs.discover_knowledge_preparation(session) == ()
            await admit_curriculum(session, curriculum_id)
            discovered = await jobs.discover_knowledge_preparation(session)
            assert len(discovered) == 1
            assert await jobs.discover_knowledge_preparation(session) == ()
            row = await session.get(KnowledgePreparationJobModel, discovered[0])
            assert row is not None
            assert row.document_id == document_id
            assert row.status == "queued"
            assert row.attempts == 0
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 0

    asyncio.run(check())


def test_discovery_is_bounded_to_eight_pages_not_a_whole_document(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_source(session, total=10, curriculum_id=curriculum_id)
            service = PageUnderstandingService(
                session, preparation_recorder=MaterialKnowledgeRequestRecorder(session)
            )
            for number in range(1, 10):
                request, result, metadata = await page_input(
                    session, document_id=document_id, page_number=number
                )
                candidate = await service.record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )
                await service.verify(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=number,
                    candidate_id=candidate.id,
                    expected_version=1,
                    compared_with_original=True,
                    reviewed_region_keys=("heading", "sequence", "groups", "answer"),
                    accepted_claim_keys=("grouping",),
                    resolved_uncertainty_keys=(),
                    reason="Verify each bounded synthetic original page",
                )
            assert await jobs.discover_knowledge_preparation(session) == ()
            await service.exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=10,
                expected_version=0,
                confirm_exclusion=True,
                reason="Resolve final synthetic page",
            )
            first = await jobs.discover_knowledge_preparation(session)
            second = await jobs.discover_knowledge_preparation(session)
            assert len(first) == 8
            assert len(second) == 1
            assert set(first).isdisjoint(second)
            assert await jobs.discover_knowledge_preparation(session) == ()

    asyncio.run(check())


def test_coordinator_and_worker_duplicates_converge_without_rebinding_sources(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            document_id, _trusted_id, _curriculum = await enrolled_source(session)

        async def discover() -> tuple[UUID, ...]:
            async with database_session(preparation_database_url) as session:
                return await jobs.discover_knowledge_preparation(session)

        results = await asyncio.gather(discover(), discover())
        identifiers = [identifier for result in results for identifier in result]
        assert len(identifiers) == 1

        async def execute() -> jobs.KnowledgePreparationJobSnapshot:
            async with database_session(preparation_database_url) as session:
                return await jobs.run_knowledge_preparation_job(session, identifiers[0])

        outcomes = await asyncio.gather(execute(), execute())
        assert any(outcome.status == "succeeded" for outcome in outcomes)
        async with database_session(preparation_database_url) as session:
            replay = await jobs.run_knowledge_preparation_job(session, identifiers[0])
            assert replay.status == "succeeded"
            assert replay.attempts == 1
            assert replay.unit_count == 2
            assert replay.projection_count == 2
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == document_id,
                        AdminAuditEventModel.action == "verified_knowledge.prepared",
                    )
                )
                == 1
            )

    asyncio.run(check())


def test_worker_prepares_one_page_atomically_and_keeps_source_and_curriculum_isolated(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            first_document, _first_trusted, first_curriculum = await enrolled_source(
                session, grade=7
            )
            second_document, _second_trusted, second_curriculum = await enrolled_source(
                session, grade=5, subject_name="Science", medium_name="English"
            )
            identifiers = await jobs.discover_knowledge_preparation(session)
            assert len(identifiers) == 2
            first = await session.get(KnowledgePreparationJobModel, identifiers[0])
            assert first is not None
            prepared_document = first.document_id
            other_document = (
                first_document if prepared_document == second_document else second_document
            )
            result = await jobs.run_knowledge_preparation_job(session, first.id)
            assert result.status == "succeeded"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == other_document)
                )
                == 0
            )
            rows = (await session.scalars(select(KnowledgeUnitModel))).all()
            assert len(rows) == 2
            assert {row.curriculum_version_id for row in rows} == {
                first_curriculum if prepared_document == first_document else second_curriculum
            }
            assert await session.scalar(text("SELECT count(*) FROM knowledge_unit_reviews")) == 0
            assert await session.scalar(text("SELECT count(*) FROM knowledge_embeddings")) == 0
            assert await session.scalar(text("SELECT count(*) FROM embedding_jobs")) == 0

    asyncio.run(check())


def test_exact_prepared_input_replay_reuses_rows_without_rewriting_history(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, trusted_id, _curriculum = await one_job(session)
            prior = await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            snapshot = [unit.model_dump(mode="json") for unit in prior.units]
            completed = await jobs.run_knowledge_preparation_job(session, job_id)
            assert completed.status == "succeeded"
            assert [
                row.payload
                for row in (
                    await session.scalars(
                        select(KnowledgeUnitModel).order_by(KnowledgeUnitModel.sequence)
                    )
                ).all()
            ] == snapshot
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(AdminAuditEventModel.action == "verified_knowledge.prepared")
                )
                == 1
            )

    asyncio.run(check())


def test_partial_work_failure_rolls_back_units_and_success_then_retries_at_most_three_times(
    preparation_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = KnowledgeUnitService.prepare_page
    calls = 0

    async def fail_after_writes(service: KnowledgeUnitService, **kwargs: Any) -> Any:
        nonlocal calls
        assert kwargs["commit"] is False
        calls += 1
        await original(service, **kwargs)
        raise RuntimeError("private source text and provider diagnostic")

    monkeypatch.setattr(KnowledgeUnitService, "prepare_page", fail_after_writes)

    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, _trusted, _curriculum = await one_job(session)
            for attempt in range(1, 4):
                result = await jobs.run_knowledge_preparation_job(session, job_id)
                assert result.status == ("failed" if attempt == 3 else "queued")
                assert result.attempts == attempt
                assert result.failure_code == "knowledge_preparation_failed"
                assert (
                    await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 0
                )
                assert (
                    await session.scalar(select(func.count()).select_from(KnowledgeProjectionModel))
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(AdminAuditEventModel.action == "verified_knowledge.prepared")
                    )
                    == 0
                )
            await jobs.run_knowledge_preparation_job(session, job_id)
            assert calls == 3
            events = (
                await session.scalars(
                    select(AdminAuditEventModel).where(
                        AdminAuditEventModel.resource_id == document_id,
                        AdminAuditEventModel.resource_type == "knowledge_preparation_job",
                    )
                )
            ).all()
            assert (
                sum(
                    event.payload["failure_code"] == "knowledge_preparation_failed"
                    for event in events
                )
                == 3
            )
            assert "private source" not in repr([event.payload for event in events])
            summary = await get_material_knowledge_preparation(
                session, principal=REVIEWER, document_id=document_id
            )
            assert summary.status == "needs_attention"
            assert summary.failed_pages == 1

    asyncio.run(check())


@pytest.mark.parametrize("gate", ["sibling", "admission"])
def test_temporarily_unavailable_inputs_defer_without_spending_attempts_and_resume_same_job(
    preparation_database_url: str,
    gate: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, _trusted, curriculum_id = await one_job(session)
            for _ in range(4):
                if gate == "sibling":
                    page = await session.get(
                        PageUnderstandingStateModel, (document_id, 2), populate_existing=True
                    )
                    assert page is not None
                    await PageUnderstandingService(session).reopen(
                        principal=ADMIN,
                        document_id=document_id,
                        page_number=2,
                        expected_version=page.version,
                        confirm_reopen=True,
                        reason="Review the synthetic sibling again",
                    )
                else:
                    await session.execute(
                        update(CurriculumVersionModel)
                        .where(CurriculumVersionModel.id == curriculum_id)
                        .values(active=False)
                    )
                    await session.commit()
                result = await jobs.run_knowledge_preparation_job(session, job_id)
                assert result.status == "deferred"
                assert result.attempts == 0
                assert await jobs.discover_knowledge_preparation(session) == ()
                if gate == "sibling":
                    page = await session.get(
                        PageUnderstandingStateModel, (document_id, 2), populate_existing=True
                    )
                    assert page is not None
                    await PageUnderstandingService(session).exclude(
                        principal=ADMIN,
                        document_id=document_id,
                        page_number=2,
                        expected_version=page.version,
                        confirm_exclusion=True,
                        reason="Resolve the synthetic sibling again",
                    )
                else:
                    await session.execute(
                        update(CurriculumVersionModel)
                        .where(CurriculumVersionModel.id == curriculum_id)
                        .values(active=True)
                    )
                    await session.commit()
            result = await jobs.run_knowledge_preparation_job(session, job_id)
            assert result.status == "succeeded"
            assert result.attempts == 1
            assert (
                await session.scalar(select(func.count()).select_from(KnowledgePreparationJobModel))
                == 1
            )

    asyncio.run(check())


def test_currentness_is_rechecked_after_claim_before_actual_work(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, _trusted, _curriculum = await one_job(session)
            claim = await jobs._claim(session, job_id, lease_seconds=300)
            await PageUnderstandingService(session).reopen(
                principal=ADMIN,
                document_id=document_id,
                page_number=2,
                expected_version=1,
                confirm_reopen=True,
                reason="Make sibling unresolved between transactions",
            )
            result = await jobs._execute_claim(session, claim)
            assert result.status == "deferred"
            assert result.attempts == 0
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 0

    asyncio.run(check())


@pytest.mark.parametrize("change", ["trusted", "scope", "removed", "revoked"])
def test_changed_inputs_are_superseded_and_current_summary_never_counts_stale_work(
    preparation_database_url: str,
    change: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, _trusted, curriculum_id = await one_job(session)
            if change == "trusted":
                request, observation, metadata = await page_input(session, document_id=document_id)
                await PageUnderstandingService(session).record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=3,
                    request=request,
                    result=observation,
                    image_metadata=metadata,
                )
            elif change == "scope":
                await session.execute(
                    update(SourceDocumentModel)
                    .where(SourceDocumentModel.id == document_id)
                    .values(year=2025)
                )
                await session.commit()
            elif change == "removed":
                await SourceDocumentService(
                    session, Mock(spec=ObjectStorage), max_upload_bytes=1024
                ).remove_from_ai_use(
                    document_id,
                    expected_version=0,
                    actor_id=ADMIN.subject_id,
                    reason="Remove this synthetic original",
                )
            else:
                from exam_guru_api.curriculum.admission import (
                    AdmissionDecisionRequest,
                    get_catalogue_admission,
                    record_catalogue_admission,
                )

                current = await get_catalogue_admission(session, curriculum_id)
                await record_catalogue_admission(
                    session,
                    curriculum_id,
                    AdmissionDecisionRequest(
                        state="quarantined",
                        expected_version=current.version,
                        expected_scope_fingerprint=current.scope_fingerprint,
                        educational_approval=False,
                        reason="Withdraw synthetic catalogue approval",
                        source_reference="Synthetic admission review",
                        evidence=("The earlier approval is withdrawn",),
                    ),
                    principal=ADMIN,
                )
                await session.commit()
            result = await jobs.run_knowledge_preparation_job(session, job_id)
            assert result.status == "superseded"
            assert result.attempts == 0
            summary = await get_material_knowledge_preparation(
                session, principal=REVIEWER, document_id=document_id
            )
            assert summary.unit_count == summary.projection_count == summary.prepared_pages == 0
            assert summary.status == (
                "removed"
                if change == "removed"
                else "preparing"
                if change == "scope"
                else "waiting"
            )
            if change == "scope":
                new = await jobs.discover_knowledge_preparation(session)
                assert len(new) == 1
                assert new[0] != job_id
                assert (
                    await jobs.run_knowledge_preparation_job(session, new[0])
                ).status == "succeeded"

    asyncio.run(check())


def test_outbox_recovers_lost_dispatch_and_never_loses_enrollment(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            document_id, _trusted, _curriculum = await enrolled_source(session)
            failed = await jobs.recover_knowledge_preparation_jobs(
                session, Dispatcher(fail=True), min_age_seconds=0
            )
            assert failed.discovered == 1
            assert failed.failures == 1
            assert (
                await session.scalar(
                    select(func.count()).select_from(MaterialKnowledgeRequestModel)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgePreparationJobModel)
                    .where(KnowledgePreparationJobModel.status == "queued")
                )
                == 1
            )
            dispatcher = Dispatcher()
            recovered = await jobs.recover_knowledge_preparation_jobs(
                session, dispatcher, min_age_seconds=0
            )
            assert recovered.enqueued == 1
            assert len(dispatcher.ids) == 1
            result = await jobs.run_knowledge_preparation_job(session, dispatcher.ids[0])
            assert result.status == "succeeded"
            summary = await get_material_knowledge_preparation(
                session, principal=REVIEWER, document_id=document_id
            )
            assert summary.status == "prepared"

    asyncio.run(check())


def test_expired_claims_are_cas_fenced_and_restart_retries_are_bounded(
    preparation_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, _document, _trusted, _curriculum = await one_job(session)
            old_claim = None
            for attempt in range(3):
                monkeypatch.setattr(
                    jobs, "_now", lambda: datetime.now(UTC) - timedelta(seconds=1000)
                )
                claim = await jobs._claim(session, job_id, lease_seconds=300)
                assert claim.status == "running"
                monkeypatch.setattr(jobs, "_now", lambda: datetime.now(UTC))
                if old_claim is not None:
                    late = await jobs._execute_claim(session, old_claim)
                    assert late.version == claim.version
                old_claim = claim
                await jobs.recover_knowledge_preparation_jobs(
                    session, Dispatcher(), min_age_seconds=0
                )
                row = await session.get(
                    KnowledgePreparationJobModel, job_id, populate_existing=True
                )
                assert row is not None
                assert row.attempts == attempt + 1
            assert row is not None
            assert row.status == "failed"
            assert row.failure_code == "knowledge_preparation_lease_expired"
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 0

    asyncio.run(check())


def test_decorative_page_is_prepared_without_any_projection_or_automatic_review(
    preparation_database_url: str,
) -> None:
    payload = counting_candidate()
    payload["observation"]["regions"] = [
        {
            **payload["observation"]["regions"][0],
            "key": "decoration",
            "kind": "decorative_image",
            "reading_order": 0,
            "exact_text": "",
        }
    ]
    payload["observation"]["relationships"] = []
    payload["education"]["claims"] = []
    payload["uncertainties"] = []

    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_source(session, total=1, curriculum_id=curriculum_id)
            request, result, metadata = await page_input(session, document_id=document_id)
            result = result.model_copy(update={"content": parse(payload)})
            service = PageUnderstandingService(
                session, preparation_recorder=MaterialKnowledgeRequestRecorder(session)
            )
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            await service.verify(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate.id,
                expected_version=1,
                compared_with_original=True,
                reviewed_region_keys=("decoration",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=(),
                reason="The original contains decoration only",
            )
            identifiers = await jobs.discover_knowledge_preparation(session)
            outcome = await jobs.run_knowledge_preparation_job(session, identifiers[0])
            assert outcome.status == "succeeded"
            assert outcome.unit_count == 1
            assert outcome.projection_count == 0
            summary = await get_material_knowledge_preparation(
                session, principal=REVIEWER, document_id=document_id
            )
            assert summary.status == "prepared"
            assert summary.prepared_pages == 1
            assert summary.projection_count == 0
            assert summary.pending_pages == 0

    asyncio.run(check())


@pytest.mark.parametrize(
    "corruption", ["input", "source_audit", "actor", "success", "lease", "terminal", "delete"]
)
def test_low_level_job_corruption_and_terminal_history_mutation_are_rejected(
    preparation_database_url: str,
    corruption: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, _document, _trusted, _curriculum = await one_job(session)
            if corruption in {"terminal", "delete"}:
                await jobs.run_knowledge_preparation_job(session, job_id)
            changes: dict[str, dict[str, object]] = {
                "input": {"input_snapshot": {}},
                "source_audit": {"source_audit_event_id": uuid4()},
                "actor": {"requested_by": REVIEWER.subject_id},
                "success": {
                    "status": "succeeded",
                    "unit_count": 0,
                    "projection_count": 0,
                    "completed_at": datetime.now(UTC),
                },
                "lease": {"status": "running", "lease_token": None},
                "terminal": {"status": "queued"},
            }

            async def corrupt() -> None:
                if corruption == "delete":
                    await session.execute(
                        text("DELETE FROM knowledge_preparation_jobs WHERE id=:id"), {"id": job_id}
                    )
                else:
                    await session.execute(
                        update(KnowledgePreparationJobModel)
                        .where(KnowledgePreparationJobModel.id == job_id)
                        .values(**changes[corruption])
                    )
                await session.commit()

            with pytest.raises(IntegrityError):
                await corrupt()
            await session.rollback()

    asyncio.run(check())


def test_request_recorder_rejects_unauthorized_or_mismatched_source_audits(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            document_id, _trusted_id, _curriculum = await verified_source(session)
            source = await session.get(SourceDocumentModel, document_id)
            page = await session.get(PageUnderstandingStateModel, (document_id, 1))
            assert source is not None
            assert page is not None
            assert page.event_id is not None
            recorder = MaterialKnowledgeRequestRecorder(session)
            with pytest.raises(AuthorizationError):
                await recorder(
                    principal=REVIEWER,
                    document_id=document_id,
                    source_sha256=source.checksum_sha256,
                    source_audit_event_id=page.event_id,
                )
            with pytest.raises((IntegrityError, ValueError)):
                await recorder(
                    principal=ADMIN,
                    document_id=document_id,
                    source_sha256=source.checksum_sha256,
                    source_audit_event_id=uuid4(),
                )
            await session.rollback()
            assert (
                await session.scalar(
                    select(func.count()).select_from(MaterialKnowledgeRequestModel)
                )
                == 0
            )

    asyncio.run(check())


@pytest.mark.parametrize("missing", ["result_counts", "deferral_code"])
def test_nullable_state_fields_cannot_bypass_matching_audit_and_current_input_guards(
    preparation_database_url: str,
    missing: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, trusted_id, curriculum_id = await one_job(session)
            if missing == "result_counts":
                await jobs._claim(session, job_id, lease_seconds=300)
                await KnowledgeUnitService(session).prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )
            else:
                await session.execute(
                    update(CurriculumVersionModel)
                    .where(CurriculumVersionModel.id == curriculum_id)
                    .values(active=False)
                )
                await session.commit()
            job = await jobs._locked(session, job_id)

            async def incomplete_state() -> None:
                await jobs._transition(
                    session,
                    job,
                    "succeeded" if missing == "result_counts" else "deferred",
                    consume_attempt=missing == "result_counts",
                )
                await session.commit()

            with pytest.raises(IntegrityError):
                await incomplete_state()
            await session.rollback()

    asyncio.run(check())


@pytest.mark.parametrize("gate", ["sibling", "removed", "admission", "trusted"])
def test_current_summary_ignores_success_history_until_original_gates_recover(
    preparation_database_url: str,
    gate: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, _trusted, curriculum_id = await one_job(session)
            await jobs.run_knowledge_preparation_job(session, job_id)
            before = await session.scalar(
                text("SELECT to_jsonb(j) FROM knowledge_preparation_jobs j WHERE id=:id"),
                {"id": job_id},
            )
            if gate == "sibling":
                await PageUnderstandingService(session).reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=2,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Review sibling again",
                )
            elif gate == "removed":
                await SourceDocumentService(
                    session, Mock(spec=ObjectStorage), max_upload_bytes=1024
                ).remove_from_ai_use(
                    document_id,
                    expected_version=0,
                    actor_id=ADMIN.subject_id,
                    reason="Remove the prepared synthetic material",
                )
            elif gate == "admission":
                await session.execute(
                    update(CurriculumVersionModel)
                    .where(CurriculumVersionModel.id == curriculum_id)
                    .values(active=False)
                )
                await session.commit()
            else:
                request, observation, metadata = await page_input(session, document_id=document_id)
                await PageUnderstandingService(session).record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=3,
                    request=request,
                    result=observation,
                    image_metadata=metadata,
                )
            blocked = await get_material_knowledge_preparation(
                session,
                principal=REVIEWER,
                document_id=document_id,
            )
            assert blocked.prepared_pages == 0
            assert blocked.unit_count == 0
            assert blocked.projection_count == 0
            assert blocked.failed_pages == 0
            assert blocked.status == ("removed" if gate == "removed" else "waiting")
            if gate == "sibling":
                await PageUnderstandingService(session).exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=2,
                    expected_version=2,
                    confirm_exclusion=True,
                    reason="Resolve sibling again",
                )
            elif gate == "removed":
                await SourceDocumentService(
                    session, Mock(spec=ObjectStorage), max_upload_bytes=1024
                ).restore_to_ai_use(
                    document_id,
                    expected_version=1,
                    actor_id=ADMIN.subject_id,
                )
            elif gate == "admission":
                await session.execute(
                    update(CurriculumVersionModel)
                    .where(CurriculumVersionModel.id == curriculum_id)
                    .values(active=True)
                )
                await session.commit()
            else:
                page = await session.get(
                    PageUnderstandingStateModel, (document_id, 1), populate_existing=True
                )
                assert page is not None
                assert page.current_candidate_id is not None
                await approve_page(
                    PageUnderstandingService(session),
                    document_id,
                    page.current_candidate_id,
                    page.version,
                )
            discovered = await jobs.discover_knowledge_preparation(session)
            if gate in {"removed", "trusted"}:
                assert len(discovered) == 1
                assert discovered[0] != job_id
                await jobs.run_knowledge_preparation_job(session, discovered[0])
            else:
                assert discovered == ()
            current = await get_material_knowledge_preparation(
                session, principal=REVIEWER, document_id=document_id
            )
            assert current.status == "prepared"
            assert current.unit_count == 2
            assert current.projection_count == 2
            assert (
                await session.scalar(
                    text("SELECT to_jsonb(j) FROM knowledge_preparation_jobs j WHERE id=:id"),
                    {"id": job_id},
                )
                == before
            )
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == (
                4 if gate in {"removed", "trusted"} else 2
            )

    asyncio.run(check())


def test_summary_works_in_a_read_only_transaction_without_flushing_pending_writes(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            document_id, _trusted, _curriculum = await enrolled_source(session)
            await session.execute(text("SET TRANSACTION READ ONLY"))
            unrelated = AdminAuditEventModel(
                id=uuid4(),
                actor_id=ADMIN.subject_id,
                resource_type="verification",
                resource_id=document_id,
                action="verification.pending",
                payload={},
            )
            session.add(unrelated)
            result = await get_material_knowledge_preparation(
                session, principal=REVIEWER, document_id=document_id
            )
            assert result.status == "preparing"
            assert unrelated in session.new
            await session.rollback()
            assert (
                await session.scalar(select(func.count()).select_from(KnowledgePreparationJobModel))
                == 0
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "operation", ["request_update", "request_delete", "request_truncate", "job_truncate"]
)
def test_retained_intent_and_job_history_cannot_be_erased(
    preparation_database_url: str, operation: str
) -> None:
    statements = {
        "request_update": "UPDATE material_knowledge_requests SET requested_by=:actor",
        "request_delete": "DELETE FROM material_knowledge_requests",
        "request_truncate": "TRUNCATE material_knowledge_requests CASCADE",
        "job_truncate": "TRUNCATE knowledge_preparation_jobs CASCADE",
    }

    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            await one_job(session)
            with pytest.raises(IntegrityError, match="append only"):
                await session.execute(text(statements[operation]), {"actor": REVIEWER.subject_id})
            await session.rollback()
            assert (
                await session.scalar(
                    select(func.count()).select_from(MaterialKnowledgeRequestModel)
                )
                == 1
            )
            assert (
                await session.scalar(select(func.count()).select_from(KnowledgePreparationJobModel))
                == 1
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "corruption", ["scope", "trusted_fingerprint", "source_audit", "actor", "request", "audit"]
)
def test_forged_insert_cannot_authorize_work_even_with_recomputed_input_hashes(
    preparation_database_url: str,
    corruption: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            document_id, trusted_id, _curriculum = await enrolled_source(session)
            trusted = await session.get(TrustedPageKnowledgeModel, trusted_id)
            intent = await session.scalar(
                select(MaterialKnowledgeRequestModel).where(
                    MaterialKnowledgeRequestModel.document_id == document_id
                )
            )
            assert trusted is not None
            assert intent is not None
            scope = KnowledgeScope.model_validate_json(
                json.dumps(await session.scalar(select(func.knowledge_scope_snapshot(document_id))))
            )
            value = jobs._input(trusted, scope)
            scope_payload = value["scope"]
            assert isinstance(scope_payload, dict)
            if corruption == "scope":
                scope_payload["grade"] = 11
            if corruption == "trusted_fingerprint":
                value["trusted_page_fingerprint"] = "0" * 64
            job = KnowledgePreparationJobModel(
                id=uuid4(),
                request_id=uuid4() if corruption == "request" else intent.id,
                document_id=document_id,
                page_number=1,
                source_sha256=intent.source_sha256,
                requested_by=REVIEWER.subject_id if corruption == "actor" else ADMIN.subject_id,
                trusted_page_id=trusted_id,
                candidate_id=trusted.candidate_id,
                source_audit_event_id=intent.audit_event_id
                if corruption == "source_audit"
                else trusted.audit_event_id,
                scope_fingerprint=jobs._fingerprint(scope_payload),
                derivation_version=jobs.DERIVATION_VERSION,
                transformation_version=jobs.TRANSFORMATION_VERSION,
                input_snapshot=value,
                input_fingerprint=jobs._fingerprint(value),
                status="queued",
                version=0,
                attempts=0,
            )
            audit = jobs._audit(job)
            if corruption == "audit":
                audit.payload = {**audit.payload, "input_fingerprint": "0" * 64}
            session.add(audit)
            job.audit_event_id = audit.id
            session.add(job)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
            assert (
                await session.scalar(select(func.count()).select_from(KnowledgePreparationJobModel))
                == 0
            )
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 0

    asyncio.run(check())


def test_intent_requires_its_own_matching_audit_not_just_a_source_event(
    preparation_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            document_id, _trusted, _curriculum = await verified_source(session)
            source = await session.get(SourceDocumentModel, document_id)
            page = await session.get(PageUnderstandingStateModel, (document_id, 1))
            assert source is not None
            assert page is not None
            assert page.event_id is not None
            session.add(
                MaterialKnowledgeRequestModel(
                    id=uuid4(),
                    document_id=document_id,
                    source_sha256=source.checksum_sha256,
                    requested_by=ADMIN.subject_id,
                    source_audit_event_id=page.event_id,
                    audit_event_id=page.event_id,
                )
            )
            with pytest.raises(IntegrityError, match="matching audit"):
                await session.commit()
            await session.rollback()
            assert (
                await session.scalar(
                    select(func.count()).select_from(MaterialKnowledgeRequestModel)
                )
                == 0
            )

    asyncio.run(check())


def test_timed_out_delivery_can_arrive_late_without_duplicating_preparation(
    preparation_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, delivered = Event(), Event()
    identifiers: list[UUID] = []
    original_deadline = anyio.fail_after

    def dispatch(identifier: UUID) -> str:
        if not release.wait(5):
            raise TimeoutError("test dispatch was not released")
        identifiers.append(identifier)
        delivered.set()
        return str(identifier)

    def short_deadline(seconds: float) -> Any:
        assert seconds == 10
        return original_deadline(0.1)

    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, document_id, _trusted_id, _curriculum_id = await one_job(session)
            monkeypatch.setattr(anyio, "fail_after", short_deadline)
            result = await jobs.recover_knowledge_preparation_jobs(
                session,
                SimpleNamespace(dispatch=dispatch),
                min_age_seconds=0,
            )
            assert result.failures == 1
            assert result.enqueued == 0
            row = await session.get(KnowledgePreparationJobModel, job_id, populate_existing=True)
            assert row is not None
            assert row.status == "queued"
            assert row.attempts == 0
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 0
            release.set()
            assert await anyio.to_thread.run_sync(delivered.wait, 2)
            assert identifiers == [job_id]
            monkeypatch.setattr(anyio, "fail_after", original_deadline)
            again = Dispatcher()
            recovered = await jobs.recover_knowledge_preparation_jobs(
                session, again, min_age_seconds=0
            )
            assert recovered.enqueued == 1
            assert again.ids == identifiers
            first = await jobs.run_knowledge_preparation_job(session, identifiers[0])
            second = await jobs.run_knowledge_preparation_job(session, again.ids[0])
            assert first.status == "succeeded"
            assert first.attempts == 1
            assert second == first
            assert (
                await session.scalar(select(func.count()).select_from(KnowledgeUnitModel))
                == first.unit_count
            )
            summary = await get_material_knowledge_preparation(
                session, principal=ADMIN, document_id=document_id
            )
            assert summary.status == "prepared"

    try:
        asyncio.run(check())
    finally:
        release.set()


def test_lost_success_acknowledgement_does_not_duplicate_derivation(
    preparation_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        async with database_session(preparation_database_url) as session:
            job_id, _document, _trusted, _curriculum = await one_job(session)
            original_commit = session.commit
            commits = 0

            async def lose_acknowledgement() -> None:
                nonlocal commits
                commits += 1
                await original_commit()
                if commits == 2:
                    raise RuntimeError("The success acknowledgement was lost")

            monkeypatch.setattr(session, "commit", lose_acknowledgement)
            result = await jobs.run_knowledge_preparation_job(session, job_id)
            assert result.status == "succeeded"
            assert result.attempts == 1
            replay = await jobs.run_knowledge_preparation_job(session, job_id)
            assert replay == result
            assert await session.scalar(select(func.count()).select_from(KnowledgeUnitModel)) == 2
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(AdminAuditEventModel.action == "verified_knowledge.prepared")
                )
                == 1
            )

    asyncio.run(check())
