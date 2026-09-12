import asyncio
import threading
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from testcontainers.community.postgres import PostgresContainer

import exam_guru_api.documents.understanding_jobs as jobs
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import PageImageError, SourceImageStorage
from exam_guru_api.documents.understanding_jobs import (
    UnderstandingJobService,
    UnderstandingJobSnapshot,
    run_understanding_job,
)
from exam_guru_api.documents.understanding_models import (
    DocumentUnderstandingJobModel,
    DocumentUnderstandingRunModel,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderError,
    UnderstandingProviderResult,
    UnderstandingRequest,
)
from exam_guru_api.documents.understanding_runtime import (
    PreparedUnderstandingInput,
    UnderstandingRuntime,
)
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
)
from exam_guru_api.generation.ports import ProviderFailureCode
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from tests.integration.test_document_understanding_postgres import approve_page, page_input
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


class CountingProvider:
    def __init__(self, result: UnderstandingProviderResult, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls = 0

    def understand(self, request: UnderstandingRequest) -> UnderstandingProviderResult:
        self.calls += 1
        assert request.source == self.result.source
        if self.fail:
            raise UnderstandingProviderError(
                ProviderFailureCode.INVALID_RESPONSE, accounting=self.result.accounting
            )
        return self.result


@pytest.mark.parametrize("stage", ["before_dispatch", "after_dispatch"])
def test_expired_leases_cannot_dispatch_or_publish_observations(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    class Clock:
        current = datetime.now(UTC)

        @classmethod
        def now(cls, zone: tzinfo | None = None) -> datetime:
            return cls.current

    monkeypatch.setattr(jobs, "datetime", Clock)

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    response = super().understand(value)
                    if stage == "after_dispatch":
                        Clock.current += timedelta(seconds=601)
                    return response

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic lease boundary",
            )

            def prepare(_: UnderstandingJobSnapshot) -> PreparedUnderstandingInput:
                if stage == "before_dispatch":
                    Clock.current += timedelta(seconds=601)
                return PreparedUnderstandingInput(request, metadata)

            outcome = await run_understanding_job(
                session, job.id, runtime=runtime, input_factory=prepare
            )
            assert outcome.status == "failed"
            assert outcome.failure_code == "source_understanding_lease_expired"
            assert provider.calls == int(stage == "after_dispatch")
            assert outcome.accounting == (result.accounting if stage == "after_dispatch" else None)
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.candidate is None
            assert page.trusted is None

    asyncio.run(check())


def test_inflight_analysis_prevents_verifying_the_previous_observation(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=1,
                runtime=runtime,
                reason="Synthetic newer analysis",
            )
            with pytest.raises(UnderstandingConflictError):
                await approve_page(service, request.source.document_id, candidate.id, 2)

    asyncio.run(check())


def test_concurrent_duplicate_enqueue_converges_after_the_page_lock(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, _metadata = await page_input(session)
        runtime = UnderstandingRuntime(request.profile, request.budget, CountingProvider(result))
        identifier = uuid4()
        barrier = asyncio.Barrier(2)
        original = PageUnderstandingService._page

        async def page_lock(service: PageUnderstandingService, *args: Any, **kwargs: Any) -> Any:
            await barrier.wait()
            return await original(service, *args, **kwargs)

        monkeypatch.setattr(PageUnderstandingService, "_page", page_lock)

        async def enqueue() -> UnderstandingJobSnapshot:
            async with database_session(workspace_database_url) as session:
                return await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    request_id=identifier,
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=0,
                    runtime=runtime,
                    reason="Synthetic duplicate creation",
                )

        outcomes = await asyncio.gather(enqueue(), enqueue(), return_exceptions=True)
        assert all(isinstance(value, UnderstandingJobSnapshot) for value in outcomes)
        assert outcomes[0] == outcomes[1]

    asyncio.run(check())


def test_duplicate_worker_delivery_never_repeats_an_inflight_provider_call(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        started, release = threading.Event(), threading.Event()
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    self.calls += 1
                    started.set()
                    assert release.wait(10)
                    return self.result

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic duplicate worker delivery",
            )

        async def deliver() -> UnderstandingJobSnapshot:
            async with database_session(workspace_database_url) as session:
                return await run_understanding_job(
                    session,
                    job.id,
                    runtime=runtime,
                    input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
                )

        first = asyncio.create_task(deliver())
        try:
            assert await asyncio.to_thread(started.wait, 10)
            duplicate = await deliver()
            assert duplicate.status == "running"
            assert provider.calls == 1
        finally:
            release.set()
        assert (await first).status == "succeeded"
        assert provider.calls == 1

    asyncio.run(check())


def test_unstructured_provider_result_cannot_escape_failure_accounting(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    self.calls += 1
                    return cast(UnderstandingProviderResult, None)

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic malformed provider result",
            )
            outcome = await run_understanding_job(
                session,
                job.id,
                runtime=runtime,
                input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
            )
            assert outcome.status == "unknown"
            assert outcome.failure_code == "invalid_response"
            assert outcome.accounting is None
            assert outcome.candidate_id is None
            assert provider.calls == 1

    asyncio.run(check())


@pytest.mark.parametrize("stage", ["before_dispatch", "after_dispatch", "after_commit"])
def test_crash_recovery_requeues_only_unbilled_work_and_reconciles_saved_results(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    class Crash(BaseException):
        pass

    class Clock:
        current = datetime.now(UTC) - (
            timedelta(seconds=1000) if stage != "after_commit" else timedelta()
        )

        @classmethod
        def now(cls, zone: tzinfo | None = None) -> datetime:
            return cls.current

    class Dispatcher:
        def __init__(self) -> None:
            self.ids: list[UUID] = []

        def dispatch(self, identifier: UUID) -> str:
            self.ids.append(identifier)
            return str(identifier)

    monkeypatch.setattr(jobs, "datetime", Clock)
    original = PageUnderstandingService.record_result

    async def crash_after_commit(service: PageUnderstandingService, **kwargs: Any) -> Any:
        await original(service, **kwargs)
        raise Crash()

    if stage == "after_commit":
        monkeypatch.setattr(PageUnderstandingService, "record_result", crash_after_commit)

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    self.calls += 1
                    if stage == "after_dispatch":
                        raise Crash()
                    return self.result

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic crash recovery",
            )
            prepared = PreparedUnderstandingInput(request, metadata)

            def prepare(_: UnderstandingJobSnapshot) -> PreparedUnderstandingInput:
                if stage == "before_dispatch":
                    raise Crash()
                return prepared

            with pytest.raises(Crash):
                await run_understanding_job(session, job.id, runtime=runtime, input_factory=prepare)
            Clock.current = datetime.now(UTC) + timedelta(seconds=1000)
            dispatcher = Dispatcher()
            await jobs.recover_understanding_jobs(
                session, dispatcher, batch_size=100, min_age_seconds=1
            )
            recovered = await UnderstandingJobService(session).get(principal=ADMIN, job_id=job.id)
            if stage == "before_dispatch":
                assert recovered.status == "queued"
                assert job.id in dispatcher.ids
                assert provider.calls == 0
                Clock.current = datetime.now(UTC)
                outcome = await run_understanding_job(
                    session, job.id, runtime=runtime, input_factory=lambda _: prepared
                )
                assert outcome.status == "succeeded"
                assert provider.calls == 1
            else:
                assert recovered.status == ("succeeded" if stage == "after_commit" else "unknown")
                assert job.id not in dispatcher.ids
                assert provider.calls == 1
                replay = await run_understanding_job(
                    session, job.id, runtime=runtime, input_factory=lambda _: prepared
                )
                assert replay.status == recovered.status
                assert provider.calls == 1

    asyncio.run(check())


@pytest.mark.parametrize("case", ["source_checksum", "page_number", "chunk_hash", "dimensions"])
def test_invalid_render_lineage_is_rejected_before_any_provider_charge(
    workspace_database_url: str, case: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            if case == "source_checksum":
                metadata = metadata.model_copy(update={"source_checksum_sha256": "f" * 64})
            elif case == "page_number":
                metadata = metadata.model_copy(update={"page_number": 2})
            elif case == "dimensions":
                metadata = metadata.model_copy(update={"width": metadata.width + 1})
            else:
                metadata = metadata.model_copy(
                    update={
                        "artifact": metadata.artifact.model_copy(
                            update={"chunk_sha256": ("0" * 64,)}
                        )
                    }
                )
            provider = CountingProvider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic image lineage rejection",
            )
            outcome = await run_understanding_job(
                session,
                job.id,
                runtime=runtime,
                input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
            )
            assert outcome.status == "failed"
            assert outcome.failure_code == "source_understanding_image_mismatch"
            assert outcome.accounting is None
            assert provider.calls == 0

    asyncio.run(check())


def test_job_migration_preserves_verified_knowledge_and_refuses_job_history_loss() -> None:
    async def seed(url: str, *, queued: bool) -> UUID:
        async with database_session(url) as session:
            request, result, metadata = await page_input(session)
            if queued:
                await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    request_id=uuid4(),
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=0,
                    runtime=UnderstandingRuntime(
                        request.profile, request.budget, CountingProvider(result)
                    ),
                    reason="Synthetic preservation job",
                )
            else:
                service = PageUnderstandingService(session)
                candidate = await service.record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )
                await approve_page(service, request.source.document_id, candidate.id, 1)
            return request.source.document_id

    async def snapshot(url: str, identifier: UUID) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'source',(SELECT to_jsonb(d) FROM source_documents d WHERE d.id=:id),
                    'page',(SELECT to_jsonb(p)-'active_job_id' FROM source_understanding_pages p
                        WHERE p.document_id=:id),
                    'runs',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_runs r WHERE r.document_id=:id),
                    'candidates',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
                        FROM source_understanding_candidates c WHERE c.document_id=:id),
                    'regions',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_regions r WHERE r.document_id=:id),
                    'reports',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_understanding_reports r WHERE r.document_id=:id),
                    'decisions',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.id)
                        FROM source_understanding_decisions d WHERE d.document_id=:id),
                    'knowledge',(SELECT jsonb_agg(to_jsonb(k) ORDER BY k.id)
                        FROM trusted_page_knowledge k WHERE k.document_id=:id),
                    'audit',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id)
                        FROM admin_audit_events a WHERE a.resource_id=:id))
            """),
                {"id": identifier},
            )

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="understanding_job_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            configuration = _config_for_database(url)
            command.upgrade(configuration, "head")
            identifier = asyncio.run(seed(url, queued=False))
            before = asyncio.run(snapshot(url, identifier))
            command.downgrade(configuration, "0043_document_understanding")
            assert asyncio.run(snapshot(url, identifier)) == before
            command.upgrade(configuration, "head")
            assert asyncio.run(snapshot(url, identifier)) == before
            assert_database_schema_current(url)
            asyncio.run(seed(url, queued=True))
            with pytest.raises(
                DBAPIError, match="cannot discard document understanding job history"
            ):
                command.downgrade(configuration, "0043_document_understanding")
            assert asyncio.run(snapshot(url, identifier)) == before
            assert_database_schema_current(url)


@pytest.mark.parametrize("prior", [False, True])
def test_worker_input_factory_binds_current_declared_evidence(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch, prior: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            if prior:
                await PageUnderstandingService(session).record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=int(prior),
                runtime=runtime,
                reason="Synthetic input selection",
            )
            captured: list[tuple[Any, ...]] = []

            def prepare(*args: Any) -> PreparedUnderstandingInput:
                captured.append(args)
                return PreparedUnderstandingInput(request, metadata)

            monkeypatch.setattr(jobs, "prepare_understanding_input", prepare)
            factory = await jobs._prepare_job_input_factory(
                session, job.id, cast(SourceImageStorage, object()), None, runtime
            )
            prepared = factory(job)
            assert prepared.request == request
            assert captured[0][0].document_id == job.document_id
            assert captured[0][1] == 1
            if prior:
                assert captured[0][2]["page_image"] == metadata.model_dump(mode="json")
            else:
                assert captured[0][2] is None

    asyncio.run(check())


def test_missing_job_paths_and_disabled_runtime_do_not_touch_source_truth(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, _metadata = await page_input(session)
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            with pytest.raises(jobs.UnderstandingJobNotFoundError):
                await UnderstandingJobService(session).get(principal=ADMIN, job_id=uuid4())
            with pytest.raises(jobs.UnderstandingJobNotFoundError):
                await jobs._locked(session, uuid4())
            with pytest.raises(jobs.UnderstandingJobNotFoundError):
                await jobs._prepare_job_input_factory(
                    session, uuid4(), cast(SourceImageStorage, object()), None, runtime
                )
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic disabled runtime",
            )
            await jobs._fail_unconfigured_job(session, job.id)
            failed = await UnderstandingJobService(session).get(principal=ADMIN, job_id=job.id)
            assert failed.status == "failed"
            assert failed.accounting is None
            await jobs._fail_unconfigured_job(session, job.id)
            assert (
                await UnderstandingJobService(session).get(principal=ADMIN, job_id=job.id) == failed
            )

    asyncio.run(check())


def test_completed_understanding_job_cannot_be_rewritten_or_deleted(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            service = UnderstandingJobService(session)
            job = await service.create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic immutable completion",
            )
            saved = await run_understanding_job(
                session,
                job.id,
                runtime=runtime,
                input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
            )
            for statement in (
                "UPDATE source_understanding_jobs SET version=version+1,reason='rewritten' "
                "WHERE id=:id",
                "DELETE FROM source_understanding_jobs WHERE id=:id",
            ):
                with pytest.raises(DBAPIError):
                    await session.execute(text(statement), {"id": job.id})
                await session.rollback()
                assert await service.get(principal=ADMIN, job_id=job.id) == saved
            assert await jobs._failure(session, job.id, None, code="late_failure") == saved
            job_model, page, _source = await jobs._locked(session, job.id)
            assert (
                await jobs._success(
                    session,
                    job_model,
                    page,
                    cast(UUID, saved.run_id),
                    cast(UUID, saved.candidate_id),
                    result.accounting,
                )
                == saved
            )

    asyncio.run(check())


def test_existing_run_identity_cannot_be_reused_as_a_new_job(workspace_database_url: str) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            request_id = uuid4()
            await PageUnderstandingService(session).record_result(
                principal=ADMIN,
                request_id=request_id,
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            with pytest.raises(UnderstandingConflictError):
                await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    request_id=request_id,
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=1,
                    runtime=runtime,
                    reason="Synthetic reused run identity",
                )

    asyncio.run(check())


def test_reviewed_pages_are_not_silently_reanalyzed_and_retries_are_bounded(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            reading = PageUnderstandingService(session)
            candidate = await reading.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            await approve_page(reading, request.source.document_id, candidate.id, 1)
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            with pytest.raises(UnderstandingConflictError):
                await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    request_id=uuid4(),
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=2,
                    runtime=runtime,
                    reason="Synthetic protected source",
                )
            request, result, metadata = await page_input(session)
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result, fail=True)
            )
            service = UnderstandingJobService(session)
            parent = None
            for depth in range(4):
                job = await service.create(
                    principal=ADMIN,
                    request_id=uuid4(),
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=depth * 2,
                    runtime=runtime,
                    reason="Synthetic retry depth",
                    retry_of_job_id=parent,
                )
                assert job.retry_depth == depth
                await run_understanding_job(
                    session,
                    job.id,
                    runtime=runtime,
                    input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
                )
                parent = job.id
            for predecessor in (parent, uuid4()):
                with pytest.raises(UnderstandingConflictError):
                    await service.create(
                        principal=ADMIN,
                        request_id=uuid4(),
                        document_id=request.source.document_id,
                        page_number=1,
                        expected_version=8,
                        runtime=runtime,
                        reason="Synthetic invalid retry",
                        retry_of_job_id=predecessor,
                    )

    asyncio.run(check())


@pytest.mark.parametrize(
    "case",
    [
        "configuration",
        "removed_before",
        "removed_after",
        "image",
        "input",
        "provider",
        "result_identity",
    ],
)
def test_job_failures_preserve_history_without_extra_provider_calls(
    workspace_database_url: str, case: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)

            async def remove() -> None:
                async with database_session(workspace_database_url) as removal:
                    document = await removal.get(SourceDocumentModel, request.source.document_id)
                    assert document is not None
                    document.active_for_ai = False
                    document.removal_reason = "Synthetic source removal"
                    document.removed_by = ADMIN.subject_id
                    document.removed_at = datetime.now(UTC)
                    document.metadata_scope_version += 1
                    await removal.commit()

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    self.calls += 1
                    if case == "provider":
                        raise RuntimeError("private provider detail")
                    if case == "removed_after":
                        asyncio.run(remove())
                    if case == "result_identity":
                        return self.result.model_copy(
                            update={
                                "source": self.result.source.model_copy(update={"page_number": 2})
                            }
                        )
                    return self.result

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic bounded failure",
            )
            if case == "removed_before":
                await remove()
            if case == "configuration":
                runtime = UnderstandingRuntime(
                    request.profile.model_copy(update={"temperature": 1.0}),
                    request.budget,
                    provider,
                )

            def prepare(_: UnderstandingJobSnapshot) -> PreparedUnderstandingInput:
                if case == "image":
                    raise PageImageError("source_page_image_artifact_unavailable")
                if case == "input":
                    raise RuntimeError("private input detail")
                return PreparedUnderstandingInput(request, metadata)

            outcome = await run_understanding_job(
                session, job.id, runtime=runtime, input_factory=prepare
            )
            assert outcome.status == ("unknown" if case == "provider" else "failed")
            assert outcome.candidate_id is None
            assert provider.calls == int(case in {"provider", "removed_after", "result_identity"})
            assert outcome.accounting == (
                result.accounting if case in {"removed_after", "result_identity"} else None
            )

    asyncio.run(check())


@pytest.mark.parametrize("stage", ["before_dispatch", "after_dispatch"])
def test_stale_workers_cannot_overwrite_a_finalized_attempt(
    workspace_database_url: str, stage: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            job_id: UUID | None = None

            async def finalize() -> None:
                assert job_id is not None
                async with database_session(workspace_database_url) as other:
                    job, _page, _source = await jobs._locked(other, job_id)
                    await jobs._failure(
                        other, job_id, job.lease_token, code="synthetic_interruption"
                    )

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    self.calls += 1
                    if stage == "after_dispatch":
                        asyncio.run(finalize())
                    return self.result

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic concurrent finalization",
            )
            job_id = job.id

            def prepare(_: UnderstandingJobSnapshot) -> PreparedUnderstandingInput:
                if stage == "before_dispatch":
                    asyncio.run(finalize())
                return PreparedUnderstandingInput(request, metadata)

            outcome = await run_understanding_job(
                session, job.id, runtime=runtime, input_factory=prepare
            )
            assert outcome.status == ("unknown" if stage == "after_dispatch" else "failed")
            assert outcome.candidate_id is None
            assert outcome.failure_code == "synthetic_interruption"
            assert provider.calls == int(stage == "after_dispatch")

    asyncio.run(check())


def test_input_request_and_job_identity_cannot_be_swapped(workspace_database_url: str) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            provider = CountingProvider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic identity mismatch",
            )
            altered = request.model_copy(
                update={"source": request.source.model_copy(update={"document_id": uuid4()})}
            )
            outcome = await run_understanding_job(
                session,
                job.id,
                runtime=runtime,
                input_factory=lambda _: PreparedUnderstandingInput(altered, metadata),
            )
            assert outcome.failure_code == "source_understanding_image_mismatch"
            assert provider.calls == 0
            with pytest.raises(UnderstandingConflictError):
                await PageUnderstandingService(session).record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=2,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                    job_id=uuid4(),
                )

    asyncio.run(check())


def test_unbilled_crash_recovery_stops_after_bounded_claims(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Crash(BaseException):
        pass

    class Clock:
        current = datetime.now(UTC) - timedelta(seconds=1000)

        @classmethod
        def now(cls, zone: tzinfo | None = None) -> datetime:
            return cls.current

    class Dispatcher:
        def dispatch(self, identifier: UUID) -> str:
            return str(identifier)

    monkeypatch.setattr(jobs, "datetime", Clock)

    def fail(_: UnderstandingJobSnapshot) -> PreparedUnderstandingInput:
        raise Crash()

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, _metadata = await page_input(session)
            provider = CountingProvider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic bounded infrastructure retries",
            )
            for _attempt in range(3):
                Clock.current = datetime.now(UTC) - timedelta(seconds=1000)
                with pytest.raises(Crash):
                    await run_understanding_job(
                        session, job.id, runtime=runtime, input_factory=fail
                    )
                Clock.current = datetime.now(UTC) + timedelta(seconds=1000)
                await jobs.recover_understanding_jobs(
                    session, Dispatcher(), batch_size=100, min_age_seconds=1
                )
            current = await UnderstandingJobService(session).get(principal=ADMIN, job_id=job.id)
            assert current.status == "failed"
            assert current.failure_code == "source_understanding_claims_exhausted"
            assert current.attempts == 3
            assert provider.calls == 0

    asyncio.run(check())


def test_saved_observation_survives_an_ambiguous_post_commit_error(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PageUnderstandingService.record_result

    async def lost_ack(service: PageUnderstandingService, **kwargs: Any) -> Any:
        await original(service, **kwargs)
        raise RuntimeError("synthetic lost commit acknowledgement")

    monkeypatch.setattr(PageUnderstandingService, "record_result", lost_ack)

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            provider = CountingProvider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic ambiguous commit",
            )
            outcome = await run_understanding_job(
                session,
                job.id,
                runtime=runtime,
                input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
            )
            assert outcome.status == "succeeded"
            assert outcome.accounting == result.accounting
            assert provider.calls == 1

    asyncio.run(check())


def test_preexisting_unknown_run_ledger_is_reused_without_new_provider_work(
    workspace_database_url: str,
) -> None:
    class Crash(BaseException):
        pass

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)

            class Provider(CountingProvider):
                def understand(self, value: UnderstandingRequest) -> UnderstandingProviderResult:
                    self.calls += 1
                    raise Crash()

            provider = Provider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            snapshot = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic recorded unknown attempt",
            )
            with pytest.raises(Crash):
                await run_understanding_job(
                    session,
                    snapshot.id,
                    runtime=runtime,
                    input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
                )
            job = await session.get(
                DocumentUnderstandingJobModel, snapshot.id, populate_existing=True
            )
            assert job is not None
            run_id = uuid4()
            audit = AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                resource_type="page_understanding",
                resource_id=job.document_id,
                action="page_understanding.observed",
                payload={"run_id": str(run_id), "page_number": 1},
            )
            session.add(audit)
            await session.flush()
            session.add(
                DocumentUnderstandingRunModel(
                    id=run_id,
                    document_id=job.document_id,
                    page_number=1,
                    source_sha256=job.source_sha256,
                    image_sha256=metadata.sha256,
                    request_id=job.request_id,
                    request_fingerprint=job.provider_request_key,
                    method="visual_ai",
                    outcome="unknown",
                    failure_code="source_understanding_outcome_unknown",
                    image_metadata=job.image_metadata,
                    provider_profile=job.profile,
                    budget=job.budget,
                    accounting=None,
                    created_by=job.created_by,
                    audit_event_id=audit.id,
                )
            )
            await session.commit()
            outcome = await jobs._failure(
                session, job.id, job.lease_token, code="source_understanding_outcome_unknown"
            )
            assert outcome.run_id == run_id
            assert outcome.status == "unknown"
            assert provider.calls == 1

    asyncio.run(check())


def test_latest_job_history_respects_the_observed_page_version(workspace_database_url: str) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, _metadata = await page_input(session)
            runtime = UnderstandingRuntime(
                request.profile, request.budget, CountingProvider(result)
            )
            service = UnderstandingJobService(session)
            first = await service.create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic first visible job",
            )
            assert (
                await service.latest_for_page(
                    principal=ADMIN, document_id=first.document_id, page_number=1, page_version=0
                )
                is None
            )
            assert (
                await service.latest_for_page(
                    principal=ADMIN, document_id=first.document_id, page_number=1, page_version=1
                )
                == first
            )
            await jobs._fail_unconfigured_job(session, first.id)
            second = await service.create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=first.document_id,
                page_number=1,
                expected_version=2,
                runtime=runtime,
                reason="Synthetic next visible job",
            )
            observed = await service.latest_for_page(
                principal=ADMIN, document_id=first.document_id, page_number=1, page_version=2
            )
            assert observed is not None
            assert observed.id == first.id
            assert (
                await service.latest_for_page(
                    principal=ADMIN, document_id=first.document_id, page_number=1, page_version=3
                )
                == second
            )

    asyncio.run(check())


def test_queue_is_idempotent_and_never_runs_or_verifies_a_provider(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, _metadata = await page_input(session)
            provider = CountingProvider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            service = UnderstandingJobService(session)
            request_id = uuid4()
            job = await service.create(
                principal=ADMIN,
                request_id=request_id,
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic queued analysis",
            )
            replay = await service.create(
                principal=ADMIN,
                request_id=request_id,
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic queued analysis",
            )
            assert replay.id == job.id
            assert job.status == "queued"
            assert job.expected_page_version == 1
            assert provider.calls == 0
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.state == "processing"
            assert page.version == 1
            assert page.trusted is None
            assert page.candidate is None
            with pytest.raises(UnderstandingConflictError):
                await service.create(
                    principal=ADMIN,
                    request_id=request_id,
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=0,
                    runtime=runtime,
                    reason="Different request body",
                )

    asyncio.run(check())


def test_durable_delivery_persists_a_candidate_once_without_source_trust(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            provider = CountingProvider(result)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic queued analysis",
            )
            prepared = PreparedUnderstandingInput(request, metadata)
            outcome = await run_understanding_job(
                session, job.id, runtime=runtime, input_factory=lambda _: prepared
            )
            assert outcome.status == "succeeded"
            assert outcome.accounting == result.accounting
            assert outcome.candidate_id is not None
            replay = await run_understanding_job(
                session, job.id, runtime=runtime, input_factory=lambda _: prepared
            )
            assert replay.id == job.id
            assert replay.status == "succeeded"
            assert provider.calls == 1
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.state == "needs_human_review"
            assert page.candidate is not None
            assert page.candidate.id == outcome.candidate_id
            assert page.trusted is None

    asyncio.run(check())


def test_invalid_provider_output_retains_cost_and_a_reviewable_failure(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            provider = CountingProvider(result, fail=True)
            runtime = UnderstandingRuntime(request.profile, request.budget, provider)
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic queued analysis",
            )
            prepared = PreparedUnderstandingInput(request, metadata)
            outcome = await run_understanding_job(
                session, job.id, runtime=runtime, input_factory=lambda _: prepared
            )
            assert outcome.status == "failed"
            assert outcome.accounting == result.accounting
            assert outcome.failure_code == "invalid_response"
            assert outcome.candidate_id is None
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.state == "needs_reprocessing"
            assert page.trusted is None
            assert provider.calls == 1

    asyncio.run(check())
