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

from exam_guru_api.api.routes.documents import (
    _extraction_http_exception,
    begin_source_document_review,
    correct_source_document_block,
    correct_source_document_page,
    list_source_document_pages,
    list_source_documents,
    list_source_page_blocks,
    trigger_source_document_extraction,
    trust_source_document,
    upload_source_document,
)
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.extraction import InvalidExtractionTransitionError
from exam_guru_api.documents.extraction_service import (
    ConcurrentReviewVersionError,
    DocumentExtractionService,
    ExtractedBlockNotFoundError,
    ExtractionDocumentNotFoundError,
    ExtractionPersistenceResult,
    ReviewNotActiveError,
    SourcePageNotFoundError,
)
from exam_guru_api.documents.jobs import DeterministicExtractionDispatcher
from exam_guru_api.documents.models import (
    ExtractedBlockModel,
    SourceDocumentModel,
    SourcePageModel,
)
from exam_guru_api.documents.schemas import ReviewedTextUpdate, SourceDocumentResponse
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
        extraction_attempt_count=0,
        extractor=None,
        extractor_version=None,
        extracted_page_count=None,
        extracted_block_count=None,
        extracted_character_count=None,
        native_text_page_ratio=None,
        needs_ocr=None,
        extraction_failure_code=None,
        extraction_started_at=None,
        extraction_completed_at=None,
        created_by=UUID(int=2),
        updated_by=UUID(int=2),
        created_at=now,
        updated_at=now,
    )


class RouteSession:
    def __init__(self, document: SourceDocumentModel | None) -> None:
        self.document = document

    async def get(
        self,
        _model: object,
        _identifier: UUID,
        **_kwargs: object,
    ) -> SourceDocumentModel | None:
        return self.document


def page_models() -> tuple[SourcePageModel, ExtractedBlockModel]:
    now = datetime.now(UTC)
    page = SourcePageModel(
        id=UUID(int=3),
        source_document_id=UUID(int=1),
        page_number=1,
        extractor="pymupdf",
        extractor_version="fixture",
        raw_text="raw",
        reviewed_text=None,
        character_count=3,
        block_count=1,
        version=0,
        created_by=UUID(int=2),
        updated_by=UUID(int=2),
        created_at=now,
        updated_at=now,
    )
    block = ExtractedBlockModel(
        id=UUID(int=4),
        source_page_id=page.id,
        source_document_id=page.source_document_id,
        page_number=1,
        reading_order=0,
        extractor="pymupdf",
        extractor_version="fixture",
        bbox_x0=0,
        bbox_y0=0,
        bbox_x1=1,
        bbox_y1=1,
        raw_text="raw",
        reviewed_text=None,
        character_count=3,
        version=0,
        created_by=UUID(int=2),
        updated_by=UUID(int=2),
        created_at=now,
        updated_at=now,
    )
    return page, block


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


def test_extraction_route_wrappers_return_typed_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = source_document()
    page, block = page_models()
    session = cast(AsyncSession, RouteSession(document))
    object_storage = cast(ObjectStorage, object())
    principal = Principal(subject_id=UUID(int=2), roles=frozenset({AdminRole.ADMIN}))
    dispatcher = DeterministicExtractionDispatcher("message-id")
    persistence_result = ExtractionPersistenceResult(
        document_id=document.id,
        status=ExtractionStatus.EXTRACTED,
        page_count=1,
        block_count=1,
        deduplicated=False,
    )

    async def return_pages(
        _service: DocumentExtractionService,
        _document_id: UUID,
    ) -> list[SourcePageModel]:
        return [page]

    async def return_blocks(
        _service: DocumentExtractionService,
        _document_id: UUID,
        *,
        page_number: int,
    ) -> list[ExtractedBlockModel]:
        assert page_number == 1
        return [block]

    async def return_result(
        _service: DocumentExtractionService,
        _document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        assert actor_id == principal.subject_id
        return persistence_result

    async def return_page(
        _service: DocumentExtractionService,
        _document_id: UUID,
        **_kwargs: object,
    ) -> SourcePageModel:
        return page

    async def return_block(
        _service: DocumentExtractionService,
        _document_id: UUID,
        **_kwargs: object,
    ) -> ExtractedBlockModel:
        return block

    monkeypatch.setattr(DocumentExtractionService, "queue_extraction", return_result)
    monkeypatch.setattr(DocumentExtractionService, "list_pages", return_pages)
    monkeypatch.setattr(DocumentExtractionService, "list_blocks", return_blocks)
    monkeypatch.setattr(DocumentExtractionService, "begin_review", return_result)
    monkeypatch.setattr(DocumentExtractionService, "trust_document", return_result)
    monkeypatch.setattr(DocumentExtractionService, "correct_page", return_page)
    monkeypatch.setattr(DocumentExtractionService, "correct_block", return_block)

    async def exercise() -> tuple[object, ...]:
        return (
            await trigger_source_document_extraction(
                document.id,
                principal,
                session,
                object_storage,
                dispatcher,
            ),
            await list_source_document_pages(
                document.id,
                principal,
                session,
                object_storage,
            ),
            await list_source_page_blocks(
                document.id,
                1,
                principal,
                session,
                object_storage,
            ),
            await begin_source_document_review(
                document.id,
                principal,
                session,
                object_storage,
            ),
            await correct_source_document_page(
                document.id,
                1,
                ReviewedTextUpdate(reviewed_text="reviewed", expected_version=0),
                principal,
                session,
                object_storage,
            ),
            await correct_source_document_block(
                document.id,
                1,
                0,
                ReviewedTextUpdate(reviewed_text="reviewed", expected_version=0),
                principal,
                session,
                object_storage,
            ),
            await trust_source_document(
                document.id,
                principal,
                session,
                object_storage,
            ),
        )

    responses = asyncio.run(exercise())

    assert len(responses) == 7
    assert dispatcher.dispatched == [(document.id, principal.subject_id)]


def test_extraction_route_wrappers_map_service_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = source_document()
    principal = Principal(subject_id=UUID(int=2), roles=frozenset({AdminRole.ADMIN}))
    session = cast(AsyncSession, RouteSession(document))
    object_storage = cast(ObjectStorage, object())

    async def fail(
        _service: DocumentExtractionService,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        raise ExtractionDocumentNotFoundError(document.id)

    async def exercise() -> None:
        monkeypatch.setattr(DocumentExtractionService, "list_pages", fail)
        with pytest.raises(HTTPException):
            await list_source_document_pages(document.id, principal, session, object_storage)
        monkeypatch.setattr(DocumentExtractionService, "list_blocks", fail)
        with pytest.raises(HTTPException):
            await list_source_page_blocks(document.id, 1, principal, session, object_storage)
        monkeypatch.setattr(DocumentExtractionService, "begin_review", fail)
        with pytest.raises(HTTPException):
            await begin_source_document_review(document.id, principal, session, object_storage)
        monkeypatch.setattr(DocumentExtractionService, "correct_page", fail)
        with pytest.raises(HTTPException):
            await correct_source_document_page(
                document.id,
                1,
                ReviewedTextUpdate(reviewed_text="reviewed", expected_version=0),
                principal,
                session,
                object_storage,
            )
        monkeypatch.setattr(DocumentExtractionService, "correct_block", fail)
        with pytest.raises(HTTPException):
            await correct_source_document_block(
                document.id,
                1,
                0,
                ReviewedTextUpdate(reviewed_text="reviewed", expected_version=0),
                principal,
                session,
                object_storage,
            )
        monkeypatch.setattr(DocumentExtractionService, "trust_document", fail)
        with pytest.raises(HTTPException):
            await trust_source_document(document.id, principal, session, object_storage)

    asyncio.run(exercise())


def test_review_routes_reject_disappeared_document_after_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = source_document()
    principal = Principal(subject_id=UUID(int=2), roles=frozenset({AdminRole.ADMIN}))
    session = cast(AsyncSession, RouteSession(None))
    object_storage = cast(ObjectStorage, object())
    result = ExtractionPersistenceResult(
        document_id=document.id,
        status=ExtractionStatus.IN_REVIEW,
        page_count=1,
        block_count=1,
        deduplicated=False,
    )

    async def return_result(
        _service: DocumentExtractionService,
        *_args: object,
        **_kwargs: object,
    ) -> ExtractionPersistenceResult:
        return result

    monkeypatch.setattr(DocumentExtractionService, "begin_review", return_result)
    with pytest.raises(HTTPException):
        asyncio.run(begin_source_document_review(document.id, principal, session, object_storage))
    monkeypatch.setattr(DocumentExtractionService, "trust_document", return_result)
    with pytest.raises(HTTPException):
        asyncio.run(trust_source_document(document.id, principal, session, object_storage))


def test_list_documents_route_returns_typed_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def return_documents(
        _service: SourceDocumentService,
    ) -> list[SourceDocumentModel]:
        return [source_document()]

    monkeypatch.setattr(SourceDocumentService, "list_documents", return_documents)
    values = arguments()
    responses = asyncio.run(
        list_source_documents(
            values.principal,
            values.session,
            values.object_storage,
            values.settings,
        )
    )

    assert responses[0].original_filename == "source.pdf"


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


def test_extraction_trigger_rejects_missing_and_non_dispatchable_documents() -> None:
    principal = Principal(subject_id=UUID(int=2), roles=frozenset({AdminRole.ADMIN}))
    dispatcher = DeterministicExtractionDispatcher()

    with pytest.raises(HTTPException) as missing:
        asyncio.run(
            trigger_source_document_extraction(
                UUID(int=99),
                principal,
                cast(AsyncSession, RouteSession(None)),
                cast(ObjectStorage, object()),
                dispatcher,
            )
        )
    final = source_document()
    final.extraction_status = ExtractionStatus.EXTRACTED
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(
            trigger_source_document_extraction(
                final.id,
                principal,
                cast(AsyncSession, RouteSession(final)),
                cast(ObjectStorage, object()),
                dispatcher,
            )
        )

    assert missing.value.status_code == 404
    assert conflict.value.status_code == 409


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ExtractionDocumentNotFoundError(UUID(int=1)), 404, "extraction_resource_not_found"),
        (SourcePageNotFoundError(1), 404, "extraction_resource_not_found"),
        (ExtractedBlockNotFoundError(1), 404, "extraction_resource_not_found"),
        (
            ConcurrentReviewVersionError(0, 1),
            409,
            "concurrent_review_modification",
        ),
        (ReviewNotActiveError(), 409, "invalid_extraction_transition"),
        (
            InvalidExtractionTransitionError(
                ExtractionStatus.UPLOADED,
                ExtractionStatus.TRUSTED,
            ),
            409,
            "invalid_extraction_transition",
        ),
    ],
)
def test_extraction_error_mapping(error: Exception, status_code: int, code: str) -> None:
    response = _extraction_http_exception(error)

    assert response.status_code == status_code
    assert cast(dict[str, object], response.detail)["code"] == code


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
