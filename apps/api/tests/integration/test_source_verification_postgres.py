import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from exam_guru_api.documents.page_images import PageImageArtifacts, SourceImageStorage
from exam_guru_api.documents.source_verification import VerifiedSourceContent
from exam_guru_api.documents.source_verification_models import VerifiedSourceContentModel
from exam_guru_api.documents.source_verification_service import SourceVerificationService
from exam_guru_api.documents.understanding_models import TrustedPageKnowledgeModel
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_understanding_review_postgres import source_candidate

pytestmark = pytest.mark.integration


async def confirm(
    service: SourceVerificationService,
    document_id: UUID,
    candidate_id: UUID,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts,
) -> VerifiedSourceContent:
    page = await PageUnderstandingService(service.session).get_page(
        principal=ADMIN,
        document_id=document_id,
        page_number=1,
    )
    assert page.candidate is not None
    return await service.verify(
        principal=ADMIN,
        document_id=document_id,
        page_number=1,
        candidate_id=candidate_id,
        expected_version=page.version,
        compared_with_original=True,
        reviewed_region_keys=tuple(r.key for r in page.candidate.content.observation.regions),
        resolved_uncertainty_keys=tuple(u.key for u in page.candidate.content.uncertainties),
        reason="Compared the synthetic source fixture",
        storage=storage,
        artifacts=artifacts,
    )


def test_source_confirmation_is_durable_immutable_and_does_not_create_trusted_knowledge(
    workspace_database_url: str,
    tmp_path: Path,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            saved = await confirm(
                SourceVerificationService(session), document_id, candidate_id, storage, artifacts
            )
            identifier, fingerprint = saved.id, saved.fingerprint
            assert (
                await session.scalar(select(func.count()).select_from(TrustedPageKnowledgeModel))
                == 0
            )
        async with database_session(workspace_database_url) as session:
            loaded = await SourceVerificationService(session).current(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
            )
            assert loaded is not None
            assert (loaded.id, loaded.fingerprint, loaded.model_dump_json()) == (
                identifier,
                fingerprint,
                saved.model_dump_json(),
            )
            with pytest.raises(IntegrityError):
                await session.execute(
                    text("UPDATE verified_source_contents SET revision=revision+1 WHERE id=:id"),
                    {"id": identifier},
                )
            await session.rollback()
            with pytest.raises(IntegrityError):
                await session.execute(
                    text("DELETE FROM verified_source_contents WHERE id=:id"), {"id": identifier}
                )
            await session.rollback()

    asyncio.run(check())


def test_source_correction_invalidates_current_verification_without_rewriting_history(
    workspace_database_url: str,
    tmp_path: Path,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            first = await confirm(
                SourceVerificationService(session), document_id, candidate_id, storage, artifacts
            )
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=document_id, page_number=1
            )
            assert page.candidate is not None
            correction = await PageUnderstandingService(session).correct(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                parent_candidate_id=candidate_id,
                request_id=uuid4(),
                expected_version=page.version,
                content=page.candidate.content,
                reason="Explicit synthetic correction revision",
                storage=storage,
                artifacts=artifacts,
            )
            assert (
                await SourceVerificationService(session).current(
                    principal=ADMIN, document_id=document_id, page_number=1
                )
                is None
            )
            second = await confirm(
                SourceVerificationService(session), document_id, correction.id, storage, artifacts
            )
            assert second.id != first.id
            assert second.revision == first.revision + 1
            retained = await session.get(VerifiedSourceContentModel, first.id)
            assert retained is not None
            assert retained.fingerprint == first.fingerprint
            assert (
                await session.scalar(select(func.count()).select_from(TrustedPageKnowledgeModel))
                == 0
            )

    asyncio.run(check())


def test_exclusion_and_reopening_do_not_restore_an_old_source_verification(
    workspace_database_url: str,
    tmp_path: Path,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, candidate_id, storage, artifacts = await source_candidate(
                session, tmp_path
            )
            saved = await confirm(
                SourceVerificationService(session), document_id, candidate_id, storage, artifacts
            )
            service = PageUnderstandingService(session)
            page = await service.exclude(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=saved.page_version,
                confirm_exclusion=True,
                reason="Synthetic exclusion",
            )
            await service.reopen(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_version=page.version,
                confirm_reopen=True,
                reason="Synthetic renewed source review",
            )
            assert (
                await SourceVerificationService(session).current(
                    principal=ADMIN, document_id=document_id, page_number=1
                )
                is None
            )
            assert await session.get(VerifiedSourceContentModel, saved.id) is not None

    asyncio.run(check())
