import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import PageImageArtifacts, PageImageError
from exam_guru_api.documents.understanding_jobs import UnderstandingJobService
from exam_guru_api.documents.understanding_models import (
    DocumentUnderstandingJobModel,
    DocumentUnderstandingRunModel,
    ObservationCandidateModel,
    PageUnderstandingStateModel,
    TrustedPageKnowledgeModel,
)
from exam_guru_api.documents.understanding_runtime import (
    UnderstandingRuntime,
    create_understanding_runtime,
    prepare_understanding_input,
)
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
    UnderstandingSourceError,
)
from tests.integration.test_document_understanding_postgres import page_input
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    ADMIN_HEADERS,
    REVIEWER,
    REVIEWER_HEADERS,
    database_session,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_understanding_api import client as client
from tests.integration.test_understanding_jobs_postgres import CountingProvider
from tests.test_page_images import FileSourceStore, image_source, rendered_fixture

pytestmark = pytest.mark.integration


async def source_candidate(
    session: AsyncSession, directory: Path
) -> tuple[UUID, UUID, FileSourceStore, PageImageArtifacts]:
    path = directory / "synthetic-understanding.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=200)
    page.insert_text((20, 60), "Synthetic visual-understanding fixture.")
    document.save(path)
    document.close()
    source = replace(image_source(path), document_id=uuid4())
    session.add(
        SourceDocumentModel(
            id=source.document_id,
            checksum_sha256=source.checksum_sha256,
            object_key=source.object_key,
            original_filename=source.filename,
            content_type="application/pdf",
            size_bytes=source.size_bytes,
            original_page_count=1,
            document_type=SourceDocumentType.OTHER_APPROVED,
            created_by=ADMIN.subject_id,
            updated_by=ADMIN.subject_id,
        )
    )
    await session.commit()
    storage = FileSourceStore(path)
    artifacts = PageImageArtifacts(root=directory / "fidelity-page-images")
    runtime = create_understanding_runtime(
        Settings(
            environment="test",
            document_understanding_provider="deterministic",
            document_understanding_fixture_runtime_id="ai-exam-guru-e2e-teacher-comparison",
        )
    )
    assert runtime is not None
    prepared = await asyncio.to_thread(
        prepare_understanding_input, source, 1, None, storage, artifacts, runtime
    )
    result = runtime.provider.understand(prepared.request)
    candidate = await PageUnderstandingService(session).record_result(
        principal=ADMIN,
        request_id=uuid4(),
        expected_version=0,
        request=prepared.request,
        result=result,
        image_metadata=prepared.image_metadata,
    )
    return source.document_id, candidate.id, storage, artifacts


def test_analysis_and_correction_cannot_claim_the_same_request_identity_concurrently(
    workspace_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, _metadata = await page_input(session)
            document_id, parent_id, storage, artifacts = await source_candidate(session, tmp_path)
            current = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert current.candidate is not None
            content = current.candidate.content
        identifier = uuid4()
        barrier = asyncio.Barrier(2)
        original_flush = AsyncSession.flush

        async def synchronize(this: AsyncSession, objects: Any = None) -> None:
            if any(
                isinstance(row, (DocumentUnderstandingJobModel, DocumentUnderstandingRunModel))
                and row.request_id == identifier
                for row in this.new
            ):
                await asyncio.wait_for(barrier.wait(), 20)
            await original_flush(this, objects=objects)

        monkeypatch.setattr(AsyncSession, "flush", synchronize)

        async def enqueue() -> object:
            async with database_session(workspace_database_url) as session:
                return await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    request_id=identifier,
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=0,
                    runtime=UnderstandingRuntime(
                        request.profile, request.budget, CountingProvider(result)
                    ),
                    reason="Synthetic competing analysis",
                )

        async def correct() -> object:
            async with database_session(workspace_database_url) as session:
                return await PageUnderstandingService(session).correct(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    parent_candidate_id=parent_id,
                    request_id=identifier,
                    expected_version=1,
                    content=content,
                    reason="Synthetic competing correction",
                    storage=storage,
                    artifacts=artifacts,
                )

        outcomes = await asyncio.gather(enqueue(), correct(), return_exceptions=True)
        errors = [value for value in outcomes if isinstance(value, BaseException)]
        assert len(errors) == 1
        assert isinstance(errors[0], (UnderstandingConflictError, IntegrityError))
        async with database_session(workspace_database_url) as session:
            jobs = await session.scalar(
                select(func.count())
                .select_from(DocumentUnderstandingJobModel)
                .where(DocumentUnderstandingJobModel.request_id == identifier)
            )
            runs = await session.scalar(
                select(func.count())
                .select_from(DocumentUnderstandingRunModel)
                .where(DocumentUnderstandingRunModel.request_id == identifier)
            )
            assert (jobs or 0) + (runs or 0) == 1

    asyncio.run(check())


def test_correction_creates_an_unverified_child_without_rewriting_trusted_history(
    workspace_database_url: str, tmp_path: Path
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            before = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            assert before.candidate is not None
            trusted = await service.verify_against_original(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                expected_version=1,
                compared_with_original=True,
                reviewed_region_keys=("fixture",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=("fixture_only",),
                reason="Synthetic comparison before correction",
                storage=storage,
                artifacts=artifacts,
            )
            content = before.candidate.content.model_copy(
                update={
                    "observation": before.candidate.content.observation.model_copy(
                        update={
                            "regions": (
                                before.candidate.content.observation.regions[0].model_copy(
                                    update={"exact_text": "Corrected literal a\u0301"}
                                ),
                            )
                        }
                    )
                }
            )
            request_id = uuid4()
            corrected = await service.correct(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                parent_candidate_id=candidate_id,
                request_id=request_id,
                expected_version=2,
                content=content,
                reason="Synthetic source correction",
                storage=storage,
                artifacts=artifacts,
            )
            assert corrected.method == "human"
            assert corrected.revision == 2
            assert corrected.source == before.candidate.source
            assert (
                corrected.content.observation.regions[0].exact_text == "Corrected literal a\u0301"
            )
            row = await session.get(ObservationCandidateModel, corrected.id)
            assert row is not None
            assert row.parent_candidate_id == candidate_id
            current = await service.get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert current.version == 3
            assert current.state == "corrected"
            assert current.trusted is None
            historical = await session.get(TrustedPageKnowledgeModel, trusted.id)
            assert historical is not None
            assert historical.payload == trusted.model_dump(mode="json")
            replay = await service.correct(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                parent_candidate_id=candidate_id,
                request_id=request_id,
                expected_version=2,
                content=content,
                reason="Synthetic source correction",
                storage=storage,
                artifacts=artifacts,
            )
            assert replay == corrected
            with pytest.raises(UnderstandingConflictError):
                await service.correct(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    parent_candidate_id=candidate_id,
                    request_id=request_id,
                    expected_version=2,
                    content=content,
                    reason="Changed replay",
                    storage=storage,
                    artifacts=artifacts,
                )

    asyncio.run(check())


def test_unprocessed_page_exclusion_is_explicit_replayable_and_not_ground_truth(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, _result, _metadata = await page_input(session)
            service = PageUnderstandingService(session)
            document_id = request.source.document_id
            with pytest.raises(ValueError, match="explicit confirmation"):
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=0,
                    confirm_exclusion=False,
                    reason="Missing explicit consent",
                )
            excluded = await service.exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=0,
                confirm_exclusion=True,
                reason="Exclude an unread synthetic page",
            )
            assert excluded.candidate is None
            assert excluded.trusted is None
            assert excluded.version == 1
            assert excluded.exclusion is not None
            assert excluded.exclusion.source_sha256 == request.source.source_sha256
            assert (
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=0,
                    confirm_exclusion=True,
                    reason="Exclude an unread synthetic page",
                )
                == excluded
            )
            with pytest.raises(UnderstandingConflictError):
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=0,
                    confirm_exclusion=True,
                    reason="Changed exclusion reason",
                )
            with pytest.raises(ValueError, match="explicit confirmation"):
                await service.reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=1,
                    confirm_reopen=False,
                    reason="Missing reopening consent",
                )
            reopened = await service.reopen(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=1,
                confirm_reopen=True,
                reason="Reconsider the unread page",
            )
            assert reopened.state == "unprocessed"
            assert reopened.version == 2
            assert reopened.candidate is None
            assert reopened.trusted is None
            assert reopened.exclusion is None
            assert (
                await service.reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Reconsider the unread page",
                )
                == reopened
            )

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["permission", "stale", "parent", "image", "identity"])
def test_correction_failure_preserves_its_current_candidate(
    workspace_database_url: str, tmp_path: Path, failure: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            before = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            assert before.candidate is not None
            error = {
                "permission": AuthorizationError,
                "stale": UnderstandingConflictError,
                "parent": UnderstandingSourceError,
                "image": PageImageError,
                "identity": ValueError,
            }[failure]
            with pytest.raises(error):
                await service.correct(
                    principal=REVIEWER if failure == "permission" else ADMIN,
                    document_id=document_id,
                    page_number=1,
                    parent_candidate_id=uuid4() if failure == "parent" else candidate_id,
                    request_id=cast(UUID, "invalid") if failure == "identity" else uuid4(),
                    expected_version=0 if failure == "stale" else 1,
                    content=before.candidate.content,
                    reason="Synthetic rejected correction",
                    storage=storage,
                    artifacts=None if failure == "image" else artifacts,
                )
            assert (
                await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
                == before
            )

    asyncio.run(check())


def test_missing_parent_run_cannot_create_a_correction(
    workspace_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            before = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            assert before.candidate is not None
            get = session.get
            run_id = before.candidate.run_id

            async def unavailable(model: Any, identity: Any, **kwargs: Any) -> Any:
                if model is DocumentUnderstandingRunModel and identity == run_id:
                    return None
                return await get(model, identity, **kwargs)

            monkeypatch.setattr(session, "get", unavailable)
            monkeypatch.setattr(
                service, "candidate_image", AsyncMock(return_value=b"synthetic image check")
            )
            with pytest.raises(UnderstandingSourceError, match="run_not_found"):
                await service.correct(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    parent_candidate_id=candidate_id,
                    request_id=uuid4(),
                    expected_version=1,
                    content=before.candidate.content,
                    reason="Synthetic missing run",
                    storage=storage,
                    artifacts=artifacts,
                )
            assert (
                await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
                == before
            )

    asyncio.run(check())


@pytest.mark.parametrize("state", ["processing", "excluded"])
def test_protected_pages_cannot_be_corrected_or_excluded_implicitly(
    workspace_database_url: str, tmp_path: Path, state: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            initial = await service.get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert initial.candidate is not None
            with pytest.raises(UnderstandingConflictError):
                await service.reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Only excluded pages may be reopened",
                )
            if state == "excluded":
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=1,
                    confirm_exclusion=True,
                    reason="Synthetic protected exclusion",
                )
            else:
                runtime = create_understanding_runtime(
                    Settings(
                        environment="test",
                        document_understanding_provider="deterministic",
                        document_understanding_fixture_runtime_id="ai-exam-guru-e2e-protected-review",
                    )
                )
                assert runtime is not None
                await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    request_id=uuid4(),
                    expected_version=1,
                    runtime=runtime,
                    reason="Synthetic in-flight reading",
                )
            before = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            with pytest.raises(UnderstandingConflictError):
                await service.correct(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    parent_candidate_id=candidate_id,
                    request_id=uuid4(),
                    expected_version=2,
                    content=initial.candidate.content,
                    reason="Cannot replace protected work",
                    storage=storage,
                    artifacts=artifacts,
                )
            with pytest.raises(UnderstandingConflictError):
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=2,
                    confirm_exclusion=True,
                    reason="Cannot replace the active lifecycle",
                )
            assert (
                await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
                == before
            )

    asyncio.run(check())


@pytest.mark.parametrize("mutation", ["source", "reason", "version_bool", "page_bool"])
def test_exclusion_readback_rejects_corrupt_audit_snapshots(
    workspace_database_url: str, mutation: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, _result, _metadata = await page_input(session)
            service = PageUnderstandingService(session)
            excluded = await service.exclude(
                principal=ADMIN,
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                confirm_exclusion=True,
                reason="Synthetic exact exclusion",
            )
            assert excluded.exclusion is not None
            audit = await session.get(AdminAuditEventModel, excluded.exclusion.event_id)
            assert audit is not None
            field, value = {
                "source": ("source_sha256", "f" * 64),
                "reason": ("reason", None),
                "version_bool": ("version", True),
                "page_bool": ("page_number", True),
            }[mutation]
            with session.no_autoflush:
                audit.payload = {**audit.payload, field: value}
                with pytest.raises(ValueError, match="stored page exclusion"):
                    await service.get_page(
                        principal=ADMIN, document_id=request.source.document_id, page_number=1
                    )
            await session.rollback()

    asyncio.run(check())


def test_human_correction_never_bypasses_source_fidelity_checks(
    workspace_database_url: str, tmp_path: Path
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            before = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            assert before.candidate is not None
            content = before.candidate.content.model_copy(
                update={
                    "observation": before.candidate.content.observation.model_copy(
                        update={
                            "regions": (
                                before.candidate.content.observation.regions[0].model_copy(
                                    update={"exact_text": "Unreadable \ufffd text"}
                                ),
                            )
                        }
                    )
                }
            )
            corrected = await service.correct(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                parent_candidate_id=candidate_id,
                request_id=uuid4(),
                expected_version=1,
                content=content,
                reason="Synthetic corrupt source evidence",
                storage=storage,
                artifacts=artifacts,
            )
            page = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            assert page.state == "needs_reprocessing"
            assert page.report is not None
            assert any(item.severity == "block" for item in page.report.findings)
            with pytest.raises(ValueError, match="blocking source findings"):
                await service.verify_against_original(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    candidate_id=corrected.id,
                    expected_version=2,
                    compared_with_original=True,
                    reviewed_region_keys=("fixture",),
                    accepted_claim_keys=(),
                    resolved_uncertainty_keys=("fixture_only",),
                    reason="Human method is not a source-fidelity exception",
                    storage=storage,
                    artifacts=artifacts,
                )
            assert (
                await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            ).trusted is None
            await service.exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=2,
                confirm_exclusion=True,
                reason="Exclude unresolved synthetic content",
            )
            reopened = await service.reopen(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=3,
                confirm_reopen=True,
                reason="Reopen without clearing source failures",
            )
            assert reopened.state == "needs_reprocessing"
            assert reopened.trusted is None

    asyncio.run(check())


def test_explicit_exclusion_and_reopening_preserve_evidence_and_require_fresh_review(
    workspace_database_url: str, tmp_path: Path
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            trusted = await service.verify_against_original(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                expected_version=1,
                compared_with_original=True,
                reviewed_region_keys=("fixture",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=("fixture_only",),
                reason="Synthetic comparison before exclusion",
                storage=storage,
                artifacts=artifacts,
            )
            excluded = await service.exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=2,
                confirm_exclusion=True,
                reason="Not suitable for this source collection",
            )
            assert excluded.state == "excluded"
            assert excluded.version == 3
            assert excluded.candidate is not None
            assert excluded.candidate.id == candidate_id
            assert excluded.trusted is None
            assert excluded.exclusion is not None
            assert excluded.exclusion.reason == "Not suitable for this source collection"
            assert await session.get(TrustedPageKnowledgeModel, trusted.id) is not None
            with pytest.raises(UnderstandingConflictError):
                await service.verify_against_original(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    candidate_id=candidate_id,
                    expected_version=3,
                    compared_with_original=True,
                    reviewed_region_keys=("fixture",),
                    accepted_claim_keys=(),
                    resolved_uncertainty_keys=("fixture_only",),
                    reason="Cannot verify an excluded page",
                    storage=storage,
                    artifacts=artifacts,
                )
            reopened = await service.reopen(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=3,
                confirm_reopen=True,
                reason="Explicitly restore this page to review",
            )
            assert reopened.version == 4
            assert reopened.state == "needs_human_review"
            assert reopened.exclusion is None
            assert reopened.trusted is None
            rechecked = await service.verify_against_original(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                expected_version=4,
                compared_with_original=True,
                reviewed_region_keys=("fixture",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=("fixture_only",),
                reason="Fresh comparison after explicit reopening",
                storage=storage,
                artifacts=artifacts,
            )
            assert rechecked.id != trusted.id
            assert rechecked.revision == 2

    asyncio.run(check())


@pytest.mark.parametrize("tamper", ["restore_trust", "missing_consent"])
def test_database_fences_exclusion_and_cannot_restore_historical_trust(
    workspace_database_url: str, tmp_path: Path, tamper: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            trusted = await service.verify_against_original(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                expected_version=1,
                compared_with_original=True,
                reviewed_region_keys=("fixture",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=("fixture_only",),
                reason="Synthetic database guard proof",
                storage=storage,
                artifacts=artifacts,
            )
            if tamper == "restore_trust":
                await service.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=2,
                    confirm_exclusion=True,
                    reason="Explicit exclusion before SQL probe",
                )
            before = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            page = await session.get(PageUnderstandingStateModel, (document_id, 1))
            assert page is not None
            assert before.candidate is not None
            target = "verified" if tamper == "restore_trust" else "excluded"
            trusted_id = trusted.id if tamper == "restore_trust" else None
            audit = service._audit(
                ADMIN,
                page,
                action="page_understanding." + target,
                candidate_id=candidate_id,
                report_id=page.current_report_id,
                run_id=before.candidate.run_id,
                trusted_id=trusted_id,
            )
            audit.payload = {
                **audit.payload,
                "source_sha256": trusted.source.source_sha256,
                "reason": "Synthetic forged lifecycle transition",
                "state": target,
                "confirmed_exclusion": False,
            }
            session.add(audit)
            await session.flush()
            with pytest.raises(IntegrityError):
                await session.execute(
                    update(PageUnderstandingStateModel)
                    .where(
                        PageUnderstandingStateModel.document_id == document_id,
                        PageUnderstandingStateModel.page_number == 1,
                    )
                    .values(
                        state=target,
                        current_trusted_id=trusted_id,
                        version=page.version + 1,
                        event_id=audit.id,
                        updated_by=ADMIN.subject_id,
                    )
                )
            await session.rollback()
            assert (
                await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
                == before
            )

    asyncio.run(check())


def test_teacher_correction_and_exclusion_api_keeps_versioned_source_choices_separate(
    client: TestClient, workspace_database_url: str, tmp_path: Path
) -> None:
    async def seed() -> tuple[UUID, UUID, FileSourceStore, Any]:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, _artifacts = await source_candidate(
                session, tmp_path
            )
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert page.candidate is not None
            return (
                document_id,
                candidate_id,
                storage,
                page.candidate.content.model_dump(mode="json"),
            )

    document_id, parent_id, storage, content = asyncio.run(seed())
    state = cast(FastAPI, client.app).state
    state.object_storage = storage
    state.settings = Settings(environment="test", storage_root=str(tmp_path))
    content["observation"]["regions"][0]["exact_text"] = (
        "A deliberately unverified synthetic correction."
    )
    path = f"/api/v1/admin/materials/{document_id}/pages/1/understanding"
    body = {
        "parent_candidate_id": str(parent_id),
        "request_id": str(uuid4()),
        "expected_version": 1,
        "content": content,
        "reason": "Synthetic correction through the normal review API",
    }
    assert (
        client.post(path + "/corrections", headers=REVIEWER_HEADERS, json=body).status_code == 403
    )
    corrected = client.post(path + "/corrections", headers=ADMIN_HEADERS, json=body)
    assert corrected.status_code == 200
    assert corrected.json()["method"] == "human"
    assert (
        client.post(path + "/corrections", headers=ADMIN_HEADERS, json=body).json()["id"]
        == corrected.json()["id"]
    )
    current = client.get(path, headers=ADMIN_HEADERS).json()
    assert current["version"] == 2
    assert current["parent_candidate_id"] == str(parent_id)
    assert current["trusted"] is None
    exclusion = {
        "expected_version": 2,
        "confirm_exclusion": True,
        "reason": "Do not use this synthetic page",
    }
    assert (
        client.post(
            path + "/exclude", headers=ADMIN_HEADERS, json={**exclusion, "confirm_exclusion": 1}
        ).status_code
        == 422
    )
    excluded = client.post(path + "/exclude", headers=ADMIN_HEADERS, json=exclusion)
    assert excluded.status_code == 200
    assert excluded.json()["state"] == "excluded"
    current = client.get(path, headers=ADMIN_HEADERS).json()
    assert current["exclusion"]["reason"] == exclusion["reason"]
    reopened = client.post(
        path + "/reopen",
        headers=ADMIN_HEADERS,
        json={
            "expected_version": 3,
            "confirm_reopen": True,
            "reason": "Return this synthetic page to review",
        },
    )
    assert reopened.status_code == 200
    assert reopened.json()["state"] == "needs_human_review"
    assert client.get(path, headers=ADMIN_HEADERS).json()["trusted"] is None


@pytest.mark.parametrize("image_missing", [False, True])
def test_teacher_api_compares_the_exact_image_before_accepting_knowledge(
    client: TestClient, workspace_database_url: str, tmp_path: Path, image_missing: bool
) -> None:
    async def seed() -> tuple[UUID, UUID, FileSourceStore, PageImageArtifacts]:
        async with database_session(workspace_database_url) as session:
            return await source_candidate(session, tmp_path)

    document_id, candidate_id, storage, _artifacts = asyncio.run(seed())
    state = cast(FastAPI, client.app).state
    state.object_storage = storage
    state.settings = Settings(
        environment="test", storage_root=str(tmp_path / "missing" if image_missing else tmp_path)
    )
    path = f"/api/v1/admin/materials/{document_id}/pages/1/understanding"
    image = client.get(f"{path}/candidates/{candidate_id}/image", headers=REVIEWER_HEADERS)
    assert image.status_code == (503 if image_missing else 200)
    assert image.headers["Cache-Control"] == "private, no-store"
    body = {
        "candidate_id": str(candidate_id),
        "expected_version": 1,
        "compared_with_original": True,
        "reviewed_region_keys": ["fixture"],
        "accepted_claim_keys": [],
        "resolved_uncertainty_keys": ["fixture_only"],
        "reason": "Compared every part of this synthetic page",
    }
    assert client.post(path + "/verify", headers=REVIEWER_HEADERS, json=body).status_code == 403
    assert (
        client.post(
            path + "/verify", headers=ADMIN_HEADERS, json={**body, "compared_with_original": 1}
        ).status_code
        == 422
    )
    response = client.post(path + "/verify", headers=ADMIN_HEADERS, json=body)
    assert response.status_code == (503 if image_missing else 200)
    page = client.get(path, headers=ADMIN_HEADERS).json()
    if image_missing:
        assert page["trusted"] is None
        assert page["version"] == 1
    else:
        assert page["trusted"]["id"] == response.json()["id"]
        assert page["version"] == 2


def test_explicit_teacher_verification_requires_the_exact_available_original(
    workspace_database_url: str, tmp_path: Path
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            image = await service.candidate_image(
                principal=REVIEWER,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                storage=storage,
                artifacts=artifacts,
            )
            assert image.startswith(b"\x89PNG")
            trusted = await service.verify_against_original(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                candidate_id=candidate_id,
                expected_version=1,
                compared_with_original=True,
                reviewed_region_keys=("fixture",),
                accepted_claim_keys=(),
                resolved_uncertainty_keys=("fixture_only",),
                reason="Compared with the synthetic original",
                storage=storage,
                artifacts=artifacts,
            )
            assert trusted.source.document_id == document_id
            current = await service.get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert current.trusted == trusted
            assert current.version == 2

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["missing_image", "original_changed", "unsupported_storage"])
def test_source_comparison_failures_never_write_trusted_knowledge(
    workspace_database_url: str, tmp_path: Path, failure: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            if failure == "missing_image":
                artifacts = PageImageArtifacts(root=tmp_path / "missing-images")
            if failure == "original_changed":
                storage.path.write_bytes(storage.path.read_bytes() + b"changed")
            with pytest.raises(PageImageError):
                await PageUnderstandingService(session).verify_against_original(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    candidate_id=candidate_id,
                    expected_version=1,
                    compared_with_original=True,
                    reviewed_region_keys=("fixture",),
                    accepted_claim_keys=(),
                    resolved_uncertainty_keys=("fixture_only",),
                    reason="Synthetic failed comparison",
                    storage=storage,
                    artifacts=None if failure == "unsupported_storage" else artifacts,
                )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TrustedPageKnowledgeModel)
                    .where(TrustedPageKnowledgeModel.document_id == document_id)
                )
                == 0
            )
            current = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert current.version == 1
            assert current.trusted is None

    asyncio.run(check())


@pytest.mark.parametrize("malformed", [False, True])
def test_candidate_comparison_cannot_substitute_a_different_valid_page_image(
    workspace_database_url: str, tmp_path: Path, malformed: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            row = await session.get(ObservationCandidateModel, candidate_id)
            assert row is not None
            run = await session.get(DocumentUnderstandingRunModel, row.run_id)
            assert run is not None
            source = replace(image_source(storage.path), document_id=document_id)
            replacement = artifacts.persist(
                rendered_fixture(tmp_path / "other-render.png"), source=source
            )
            with session.no_autoflush:
                run.image_metadata = {"broken": True} if malformed else replacement
                with pytest.raises(PageImageError, match="metadata_invalid"):
                    await PageUnderstandingService(session).candidate_image(
                        principal=ADMIN,
                        document_id=document_id,
                        page_number=1,
                        candidate_id=candidate_id,
                        storage=storage,
                        artifacts=artifacts,
                    )
            await session.rollback()

    asyncio.run(check())


def test_candidate_image_keeps_source_identity_distinct_from_the_record_fingerprint(
    workspace_database_url: str, tmp_path: Path
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            page = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
            assert page.candidate is not None
            row = await session.get(ObservationCandidateModel, candidate_id)
            assert row is not None
            altered = page.candidate.model_copy(
                update={
                    "source": page.candidate.source.model_copy(update={"source_sha256": "f" * 64})
                }
            )
            with session.no_autoflush:
                row.source_sha256 = altered.source.source_sha256
                row.fingerprint = altered.fingerprint
                with pytest.raises(PageImageError, match="source_original_unavailable"):
                    await service.candidate_image(
                        principal=ADMIN,
                        document_id=document_id,
                        page_number=1,
                        candidate_id=candidate_id,
                        storage=storage,
                        artifacts=artifacts,
                    )
            await session.rollback()

    asyncio.run(check())


def test_review_permission_cannot_verify_or_read_another_candidate_scope(
    workspace_database_url: str, tmp_path: Path
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            service = PageUnderstandingService(session)
            opens = storage.opens
            with pytest.raises(AuthorizationError):
                await service.verify_against_original(
                    principal=REVIEWER,
                    document_id=document_id,
                    page_number=1,
                    candidate_id=candidate_id,
                    expected_version=1,
                    compared_with_original=True,
                    reviewed_region_keys=("fixture",),
                    accepted_claim_keys=(),
                    resolved_uncertainty_keys=("fixture_only",),
                    reason="Synthetic unauthorized verification",
                    storage=storage,
                    artifacts=artifacts,
                )
            assert storage.opens == opens
            with pytest.raises(UnderstandingSourceError):
                await service.candidate_image(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    candidate_id=uuid4(),
                    storage=storage,
                    artifacts=artifacts,
                )

    asyncio.run(check())
