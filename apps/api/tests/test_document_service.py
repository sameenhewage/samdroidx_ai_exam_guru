import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import (
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage, StoredObject

VALID_PDF = b"%PDF-1.7\nfixture\n%%EOF"
ACTOR_ID = UUID(int=1)


class StubStorage:
    def __init__(self) -> None:
        self.puts = 0

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        self.puts += 1
        return StoredObject(key, key.split("/")[-1][:-4], len(data), "etag")

    def get_bytes(self, key: str) -> bytes:
        raise AssertionError(key)


class StubSession:
    def __init__(self) -> None:
        self.scalar_results: list[SourceDocumentModel | None] = []
        self.curriculum: object | None = SimpleNamespace(active=True)
        self.added: list[object] = []
        self.fail_commit = False
        self.rolled_back = False

    async def scalar(self, _query: object) -> SourceDocumentModel | None:
        return self.scalar_results.pop(0)

    async def get(self, _model: object, _identifier: UUID) -> object | None:
        return self.curriculum

    def add(self, model: object) -> None:
        self.added.append(model)

    async def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("INSERT", {}, RuntimeError("race"))

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, model: SourceDocumentModel) -> None:
        now = datetime.now(UTC)
        model.created_at = now
        model.updated_at = now


def existing_document() -> SourceDocumentModel:
    now = datetime.now(UTC)
    return SourceDocumentModel(
        id=UUID(int=2),
        checksum_sha256="a" * 64,
        object_key=f"sources/aa/{'a' * 64}.pdf",
        original_filename="existing.pdf",
        content_type="application/pdf",
        size_bytes=10,
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.UPLOADED,
        curriculum_version_id=None,
        year=None,
        paper_code=None,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )


def service(session: StubSession, storage: StubStorage) -> SourceDocumentService:
    return SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, storage),
        max_upload_bytes=1_024,
    )


def test_source_document_service_uploads_and_deduplicates() -> None:
    session = StubSession()
    storage = StubStorage()
    session.scalar_results = [None]

    created = asyncio.run(
        service(session, storage).upload_pdf(
            filename="source.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.SYLLABUS,
            actor_id=ACTOR_ID,
            curriculum_version_id=UUID(int=3),
        )
    )

    assert created.deduplicated is False
    assert storage.puts == 1
    assert len(session.added) == 2

    session.scalar_results = [created.document]
    duplicate = asyncio.run(
        service(session, storage).upload_pdf(
            filename="renamed.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.SYLLABUS,
            actor_id=ACTOR_ID,
        )
    )

    assert duplicate.deduplicated is True
    assert duplicate.document is created.document
    assert storage.puts == 1


def test_source_document_service_validates_curriculum_scope() -> None:
    session = StubSession()
    storage = StubStorage()
    session.scalar_results = [None]
    session.curriculum = None

    with pytest.raises(SourceCurriculumNotFoundError):
        asyncio.run(
            service(session, storage).upload_pdf(
                filename="source.pdf",
                content_type="application/pdf",
                data=VALID_PDF,
                document_type=SourceDocumentType.SYLLABUS,
                actor_id=ACTOR_ID,
                curriculum_version_id=UUID(int=3),
            )
        )

    session.scalar_results = [None]
    session.curriculum = SimpleNamespace(active=False)
    with pytest.raises(SourceCurriculumInactiveError):
        asyncio.run(
            service(session, storage).upload_pdf(
                filename="source.pdf",
                content_type="application/pdf",
                data=VALID_PDF,
                document_type=SourceDocumentType.SYLLABUS,
                actor_id=ACTOR_ID,
                curriculum_version_id=UUID(int=3),
            )
        )


def test_source_document_service_recovers_unique_insert_race() -> None:
    session = StubSession()
    storage = StubStorage()
    raced = existing_document()
    session.scalar_results = [None, raced]
    session.fail_commit = True

    result = asyncio.run(
        service(session, storage).upload_pdf(
            filename="source.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.SYLLABUS,
            actor_id=ACTOR_ID,
        )
    )

    assert result.deduplicated is True
    assert result.document is raced
    assert session.rolled_back


def test_source_document_service_reraises_unresolved_insert_race() -> None:
    session = StubSession()
    storage = StubStorage()
    session.scalar_results = [None, None]
    session.fail_commit = True

    with pytest.raises(IntegrityError):
        asyncio.run(
            service(session, storage).upload_pdf(
                filename="source.pdf",
                content_type="application/pdf",
                data=VALID_PDF,
                document_type=SourceDocumentType.SYLLABUS,
                actor_id=ACTOR_ID,
            )
        )
