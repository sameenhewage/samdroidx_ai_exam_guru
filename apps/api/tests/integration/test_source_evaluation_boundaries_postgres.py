import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from exam_guru_api.documents import evaluation_references as module
from exam_guru_api.documents.fidelity_models import SourceEvaluationPreviewModel
from exam_guru_api.documents.fidelity_schemas import EvaluationReferenceSaveRequest
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    load_material_source,
)
from exam_guru_api.infrastructure.object_storage import LocalFileObjectStorage
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    REVIEWER,
    database_session,
    workspace_database_url,
)
from tests.integration.test_source_evaluation_references_postgres import (
    failed_source,
    reference_client,
)

__all__ = ["reference_client", "workspace_database_url"]
pytestmark = pytest.mark.integration


def test_direct_reference_service_keeps_image_identity_and_version_boundaries(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, storage = reference_client
    artifacts = PageImageArtifacts(root=tmp_path / "originals" / "fidelity-page-images")

    async def scenario() -> None:
        document_id, benchmark_id = await failed_source(workspace_database_url, storage)
        async with database_session(workspace_database_url) as session:
            service = module.EvaluationReferenceService(session, storage, artifacts)
            unavailable = module.EvaluationReferenceService(session, storage, None)
            with pytest.raises(
                module.EvaluationReferenceError, match="evaluation_page_not_selected"
            ):
                await service.prepare(uuid4(), document_id, 1, principal=REVIEWER)
            with pytest.raises(PageImageError, match="artifact_unavailable"):
                await unavailable.prepare(benchmark_id, document_id, 1, principal=REVIEWER)
            identity = await load_material_source(session, document_id, principal=REVIEWER)
            with monkeypatch.context() as patch:
                patch.setattr(
                    module,
                    "load_material_source",
                    AsyncMock(return_value=replace(identity, page_count=None)),
                )
                with pytest.raises(module.EvaluationReferenceError, match="identity_unavailable"):
                    await service.prepare(benchmark_id, document_id, 1, principal=REVIEWER)
            preview = await service.prepare(benchmark_id, document_id, 1, principal=REVIEWER)
            assert preview.reference_version == 0
            assert await service.history(benchmark_id, document_id, 1, principal=REVIEWER) == []
            with pytest.raises(module.EvaluationReferenceError, match="preview_not_found"):
                await service.image(uuid4(), preview.id, principal=REVIEWER)
            with pytest.raises(PageImageError, match="artifact_unavailable"):
                await unavailable.image(benchmark_id, preview.id, principal=REVIEWER)
            content = await service.image(benchmark_id, preview.id, principal=REVIEWER)
            assert hashlib.sha256(content).hexdigest() == preview.image_sha256
            stored = await session.get(SourceEvaluationPreviewModel, preview.id)
            assert stored is not None
            evidence: dict[str, object] = {
                "page_image": stored.image_metadata,
                "source_checksum_sha256": identity.checksum_sha256,
            }
            assert (
                module._capture_image(identity, 1, evidence, storage, artifacts)
                == stored.image_metadata
            )
            for invalid in (None, {}, {"failure_code": "unavailable"}, {"page_number": 2}):
                with pytest.raises(PageImageError, match="metadata_invalid"):
                    module._capture_image(
                        identity, 1, {**evidence, "page_image": invalid}, storage, artifacts
                    )
            request = EvaluationReferenceSaveRequest(
                preview_id=preview.id,
                expected_version=0,
                text="Human fixture reference",
                human_reviewed=True,
                compared_with_original=True,
                reason="Compared synthetic original",
            )
            with pytest.raises(module.EvaluationReferenceError, match="preview_not_found"):
                await service.save(benchmark_id, document_id, 1, request, principal=ADMIN)
            with pytest.raises(PageImageError, match="artifact_unavailable"):
                await unavailable.save(benchmark_id, document_id, 1, request, principal=REVIEWER)
            first = await service.save(benchmark_id, document_id, 1, request, principal=REVIEWER)
            with pytest.raises(module.EvaluationReferenceError, match="version_conflict"):
                await service.save(benchmark_id, document_id, 1, request, principal=REVIEWER)
            second = await service.save(
                benchmark_id,
                document_id,
                1,
                request.model_copy(
                    update={"expected_version": 1, "text": "Revised human fixture reference"}
                ),
                principal=REVIEWER,
            )
            assert [
                entry.id
                for entry in await service.history(benchmark_id, document_id, 1, principal=REVIEWER)
            ] == [second.id, first.id]
            latest = await service.prepare(benchmark_id, document_id, 1, principal=REVIEWER)
            assert latest.latest_reference == second
            for limit, offset in ((0, 0), (21, 0), (True, 0), (1, -1), (1, True)):
                with pytest.raises(ValueError, match="pagination"):
                    await service.history(
                        benchmark_id, document_id, 1, principal=REVIEWER, limit=limit, offset=offset
                    )

    asyncio.run(scenario())
