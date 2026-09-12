import asyncio
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents import evaluation_references as module
from exam_guru_api.documents.fidelity_models import (
    SourceEvaluationPreviewModel,
    SourceEvaluationReferenceModel,
)
from exam_guru_api.documents.fidelity_schemas import EvaluationReferenceSaveRequest
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceImageIdentity,
    SourceImageStorage,
)
from tests.test_source_fidelity_api import ADMIN, BENCHMARK_ID, DOCUMENT_ID, REVIEWER

PREVIEW_ID = UUID(int=85991)
SOURCE = SourceImageIdentity(
    DOCUMENT_ID, "a" * 64, f"sources/aa/{'a' * 64}.pdf", 100, 1, "Fixture.pdf"
)
METADATA: dict[str, object] = {
    "sha256": "b" * 64,
    "width": 800,
    "height": 1200,
    "page_number": 1,
    "artifact": {},
}


@pytest.mark.parametrize(
    "metadata", [None, {}, {"failure_code": "unavailable"}, {"page_number": 2}]
)
def test_declared_invalid_comparison_images_are_never_regenerated(
    metadata: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    render = Mock()
    monkeypatch.setattr(module, "render_page_image", render)
    with pytest.raises(PageImageError, match="metadata_invalid"):
        module._capture_image(
            SOURCE,
            1,
            {"page_image": metadata},
            Mock(spec=SourceImageStorage),
            Mock(spec=PageImageArtifacts),
        )
    render.assert_not_called()


def test_declared_image_is_verified_and_reused_without_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = Mock(spec=SourceImageStorage)
    artifacts = Mock(spec=PageImageArtifacts)
    render = Mock()
    monkeypatch.setattr(module, "render_page_image", render)
    monkeypatch.setattr(module, "open_verified_original", Mock(return_value=nullcontext(BytesIO())))
    provenance: dict[str, object] = {
        "source_checksum_sha256": SOURCE.checksum_sha256,
        "page_image": METADATA,
    }
    assert module._capture_image(SOURCE, 1, provenance, storage, artifacts) == METADATA
    artifacts.read.assert_called_once_with(METADATA, source=SOURCE, page_number=1)
    render.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", chr(0xD800)),
        ("text", chr(0xDFFF)),
        ("reason", "\t"),
        ("reason", "private\x00reason"),
        ("reason", chr(0xD800)),
    ],
)
def test_reference_schema_revalidates_unsafe_internal_unicode(field: str, value: str) -> None:
    request = EvaluationReferenceSaveRequest.model_construct(
        preview_id=PREVIEW_ID,
        expected_version=0,
        text=value if field == "text" else "Human text",
        compared_with_original=True,
        human_reviewed=True,
        reason=value if field == "reason" else "Compared original",
    )
    validate = cast(Callable[[], EvaluationReferenceSaveRequest], request.validate_reference)
    with pytest.raises(ValueError, match=r"reference|unicode"):
        validate()
    with pytest.raises(ValueError, match=r"reference|unicode"):
        EvaluationReferenceSaveRequest.model_validate(request)


def test_service_boundaries_and_responses_without_database_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.add = Mock()
    session.scalar.return_value = object()
    session.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    rows = MagicMock()
    rows.first.return_value = None
    rows.__iter__.return_value = iter([])
    session.scalars.return_value = rows
    storage = Mock(spec=SourceImageStorage)
    artifacts = Mock(spec=PageImageArtifacts)
    monkeypatch.setattr(module, "load_material_source", AsyncMock(return_value=SOURCE))
    monkeypatch.setattr(
        module,
        "get_review_workspace",
        AsyncMock(return_value=SimpleNamespace(document_title="Fixture.pdf", language="si")),
    )
    monkeypatch.setattr(module, "_capture_image", Mock(return_value=METADATA))
    monkeypatch.setattr(module, "_verified_preview", Mock(return_value=b"verified image"))
    preview = SourceEvaluationPreviewModel(
        id=PREVIEW_ID,
        benchmark_id=BENCHMARK_ID,
        document_id=DOCUMENT_ID,
        page_number=1,
        created_by=REVIEWER.subject_id,
    )
    session.get.return_value = preview
    request = EvaluationReferenceSaveRequest(
        preview_id=PREVIEW_ID,
        expected_version=0,
        text="Human text",
        human_reviewed=True,
        compared_with_original=True,
        reason="Compared the original",
    )

    async def refresh(reference: SourceEvaluationReferenceModel) -> None:
        reference.reviewed_at = datetime(2026, 9, 12, tzinfo=UTC)

    session.refresh.side_effect = refresh

    async def scenario() -> None:
        service = module.EvaluationReferenceService(session, storage, artifacts)
        unavailable = module.EvaluationReferenceService(session, storage, None)
        session.scalar.return_value = None
        with pytest.raises(module.EvaluationReferenceError, match="page_not_selected"):
            await service.prepare(BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER)
        session.scalar.return_value = object()
        with monkeypatch.context() as patch:
            patch.setattr(
                module,
                "load_material_source",
                AsyncMock(return_value=replace(SOURCE, page_count=None)),
            )
            with pytest.raises(module.EvaluationReferenceError, match="identity_unavailable"):
                await service.prepare(BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER)
        with pytest.raises(PageImageError, match="artifact_unavailable"):
            await unavailable.prepare(BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER)
        prepared = await service.prepare(BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER)
        assert prepared.reference_version == 0
        assert prepared.latest_reference is None
        assert prepared.language == "si"
        assert (
            await service.image(BENCHMARK_ID, PREVIEW_ID, principal=REVIEWER) == b"verified image"
        )
        with pytest.raises(module.EvaluationReferenceError, match="preview_not_found"):
            await service.image(UUID(int=1), PREVIEW_ID, principal=REVIEWER)
        with pytest.raises(PageImageError, match="artifact_unavailable"):
            await unavailable.image(BENCHMARK_ID, PREVIEW_ID, principal=REVIEWER)
        with pytest.raises(module.EvaluationReferenceError, match="preview_not_found"):
            await service.save(BENCHMARK_ID, DOCUMENT_ID, 1, request, principal=ADMIN)
        with pytest.raises(module.EvaluationReferenceError, match="version_conflict"):
            await service.save(
                BENCHMARK_ID,
                DOCUMENT_ID,
                1,
                request.model_copy(update={"expected_version": 9}),
                principal=REVIEWER,
            )
        with pytest.raises(PageImageError, match="artifact_unavailable"):
            await unavailable.save(BENCHMARK_ID, DOCUMENT_ID, 1, request, principal=REVIEWER)
        saved = await service.save(BENCHMARK_ID, DOCUMENT_ID, 1, request, principal=REVIEWER)
        assert saved.text == "Human text"
        assert saved.version == 1
        assert saved.evaluation_only is True
        stored = next(
            call.args[0]
            for call in reversed(session.add.call_args_list)
            if isinstance(call.args[0], SourceEvaluationReferenceModel)
        )
        rows.first.return_value = stored
        rows.__iter__.return_value = iter([stored])
        prepared_again = await service.prepare(BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER)
        assert prepared_again.latest_reference == saved
        assert await service.history(BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER) == [saved]
        for limit, offset in ((0, 0), (21, 0), (True, 0), (1, -1), (1, True)):
            with pytest.raises(ValueError, match="pagination"):
                await service.history(
                    BENCHMARK_ID, DOCUMENT_ID, 1, principal=REVIEWER, limit=limit, offset=offset
                )

    asyncio.run(scenario())
