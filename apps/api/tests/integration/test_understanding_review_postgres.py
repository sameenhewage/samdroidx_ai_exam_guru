import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import PageImageArtifacts, PageImageError
from exam_guru_api.documents.understanding_models import (
    DocumentUnderstandingRunModel,
    ObservationCandidateModel,
    TrustedPageKnowledgeModel,
)
from exam_guru_api.documents.understanding_runtime import (
    create_understanding_runtime,
    prepare_understanding_input,
)
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingSourceError,
)
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
