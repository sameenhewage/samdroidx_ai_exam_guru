import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from exam_guru_api.api.routes.documents import upload_source_document
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import SourceDocumentResponse
from exam_guru_api.documents.service import (
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentService,
    SourceUploadResult,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage


def upload_file() -> UploadFile:
    return UploadFile(
        BytesIO(b"%PDF-1.7\nfixture\n%%EOF"),
        filename="source.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )


def source_document() -> SourceDocumentModel:
    now = datetime.now(UTC)
    return SourceDocumentModel(
        id=UUID(int=1),
        checksum_sha256="a" * 64,
        object_key=f"sources/aa/{'a' * 64}.pdf",
        original_filename="source.pdf",
        content_type="application/pdf",
        size_bytes=24,
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.UPLOADED,
        curriculum_version_id=None,
        year=None,
        paper_code=None,
        created_by=UUID(int=2),
        updated_by=UUID(int=2),
        created_at=now,
        updated_at=now,
    )


@dataclass(slots=True)
class UploadArguments:
    response: Response
    file: UploadFile
    document_type: SourceDocumentType
    principal: Principal
    session: AsyncSession
    object_storage: ObjectStorage
    settings: Settings
    curriculum_version_id: UUID | None = None
    year: int | None = None
    paper_code: str | None = None


async def call_upload(values: UploadArguments) -> SourceDocumentResponse:
    return await upload_source_document(
        response=values.response,
        file=values.file,
        document_type=values.document_type,
        principal=values.principal,
        session=values.session,
        object_storage=values.object_storage,
        settings=values.settings,
        curriculum_version_id=values.curriculum_version_id,
        year=values.year,
        paper_code=values.paper_code,
    )


def arguments() -> UploadArguments:
    return UploadArguments(
        response=Response(),
        file=upload_file(),
        document_type=SourceDocumentType.SYLLABUS,
        principal=Principal(
            subject_id=UUID(int=2),
            roles=frozenset({AdminRole.ADMIN}),
        ),
        session=cast(AsyncSession, object()),
        object_storage=cast(ObjectStorage, object()),
        settings=Settings(),
    )


def test_upload_route_returns_idempotent_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def return_existing(
        _service: SourceDocumentService,
        **_kwargs: object,
    ) -> SourceUploadResult:
        return SourceUploadResult(source_document(), deduplicated=True)

    monkeypatch.setattr(SourceDocumentService, "upload_pdf", return_existing)
    values = arguments()

    result = asyncio.run(call_upload(values))

    assert result.deduplicated is True
    assert values.response.status_code == 200


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (SourceCurriculumNotFoundError(), 404, "curriculum_version_not_found"),
        (SourceCurriculumInactiveError(), 409, "curriculum_version_inactive"),
    ],
)
def test_upload_route_maps_curriculum_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def fail(
        _service: SourceDocumentService,
        **_kwargs: object,
    ) -> SourceUploadResult:
        raise error

    monkeypatch.setattr(SourceDocumentService, "upload_pdf", fail)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(call_upload(arguments()))

    assert raised.value.status_code == status_code
    assert cast(dict[str, str], raised.value.detail)["code"] == code
