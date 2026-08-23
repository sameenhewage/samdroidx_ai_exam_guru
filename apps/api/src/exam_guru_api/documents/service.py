from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import cast
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.documents.domain import (
    ExtractionStatus,
    SourceDocumentType,
    validate_pdf_upload,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.infrastructure.object_storage import ObjectStorage


class SourceCurriculumNotFoundError(LookupError):
    pass


class SourceCurriculumInactiveError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceUploadResult:
    document: SourceDocumentModel
    deduplicated: bool


class SourceDocumentService:
    def __init__(
        self,
        session: AsyncSession,
        object_storage: ObjectStorage,
        *,
        max_upload_bytes: int,
    ) -> None:
        self._session = session
        self._object_storage = object_storage
        self._max_upload_bytes = max_upload_bytes

    async def list_documents(self) -> Sequence[SourceDocumentModel]:
        return (
            await self._session.scalars(
                select(SourceDocumentModel).order_by(SourceDocumentModel.created_at.desc())
            )
        ).all()

    async def upload_pdf(
        self,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        document_type: SourceDocumentType,
        actor_id: UUID,
        curriculum_version_id: UUID | None = None,
        year: int | None = None,
        paper_code: str | None = None,
    ) -> SourceUploadResult:
        upload = validate_pdf_upload(
            filename=filename,
            content_type=content_type,
            data=data,
            max_bytes=self._max_upload_bytes,
        )
        existing = await self._find_by_checksum(upload.checksum_sha256)
        if existing is not None:
            return SourceUploadResult(existing, deduplicated=True)

        if curriculum_version_id is not None:
            curriculum = await self._session.get(CurriculumVersionModel, curriculum_version_id)
            if curriculum is None:
                raise SourceCurriculumNotFoundError
            if not curriculum.active:
                raise SourceCurriculumInactiveError

        await to_thread.run_sync(
            partial(
                self._object_storage.put_immutable,
                upload.object_key,
                upload.data,
                content_type="application/pdf",
            )
        )
        document = SourceDocumentModel(
            id=uuid4(),
            checksum_sha256=upload.checksum_sha256,
            object_key=upload.object_key,
            original_filename=upload.filename,
            content_type="application/pdf",
            size_bytes=upload.size_bytes,
            document_type=document_type,
            extraction_status=ExtractionStatus.UPLOADED,
            curriculum_version_id=curriculum_version_id,
            year=year,
            paper_code=paper_code,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(document)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="source_document.uploaded",
                resource_type="source_document",
                resource_id=document.id,
                payload={
                    "checksum_sha256": upload.checksum_sha256,
                    "document_type": document_type.value,
                    "original_filename": upload.filename,
                    "size_bytes": upload.size_bytes,
                },
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raced = await self._find_by_checksum(upload.checksum_sha256)
            if raced is None:
                raise
            return SourceUploadResult(raced, deduplicated=True)
        await self._session.refresh(document)
        return SourceUploadResult(document, deduplicated=False)

    async def _find_by_checksum(self, checksum: str) -> SourceDocumentModel | None:
        return cast(
            SourceDocumentModel | None,
            await self._session.scalar(
                select(SourceDocumentModel).where(SourceDocumentModel.checksum_sha256 == checksum)
            ),
        )
