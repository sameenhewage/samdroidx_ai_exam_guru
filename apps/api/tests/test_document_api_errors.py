import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from exam_guru_api.api.routes.documents import (
    _material_http_exception,
    _source_document_response,
    correct_material_metadata_candidate,
    correct_material_scope,
    list_material_grade_summary,
    list_materials,
    list_source_documents,
    remove_material_from_use,
    restore_material_to_use,
    upload_source_document,
)
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import (
    MaterialMetadataCandidateRequest,
    MaterialRemoveRequest,
    MaterialRestoreRequest,
    MaterialScopeCorrectionRequest,
    SourceDocumentResponse,
    SourceIntakeMetadata,
)
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    InvalidMaterialRemovalReasonError,
    MaterialScopeImmutableError,
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentNotFoundError,
    SourceDocumentService,
    SourceLearningScopeInactiveError,
    SourceLearningScopeMismatchError,
    SourceLearningScopeNotFoundError,
    SourceUploadResult,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage


@pytest.fixture(autouse=True)
def no_metadata_candidate_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "exam_guru_api.api.routes.documents.get_metadata_candidate", AsyncMock(return_value=None)
    )


@pytest.mark.parametrize("failure", [None, "missing", "immutable", "version"])
def test_candidate_route_forwards_versioned_input_and_maps_domain_failures(
    failure: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = {
        "missing": (SourceDocumentNotFoundError(), 404),
        "immutable": (MaterialScopeImmutableError(), 409),
        "version": (ConcurrentMaterialScopeVersionError(0, 1), 409),
    }
    operation = AsyncMock(return_value=source_document())
    if failure is not None:
        operation.side_effect = errors[failure][0]
    monkeypatch.setattr(SourceDocumentService, "correct_candidate_metadata", operation)
    values = arguments()
    body = MaterialMetadataCandidateRequest(
        expected_scope_version=0,
        expected_candidate_version=0,
        metadata=SourceIntakeMetadata(candidate_grade=3),
        reason="Unverified description correction",
    )
    request = correct_material_metadata_candidate(
        UUID(int=1),
        body,
        values.principal,
        values.session,
        values.object_storage,
        values.settings,
    )
    if failure is None:
        result = asyncio.run(request)
        assert result.id == UUID(int=1)
    else:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(request)
        assert caught.value.status_code == errors[failure][1]
    assert operation.await_args is not None
    assert operation.await_args.kwargs["expected_candidate_version"] == 0
    assert operation.await_args.kwargs["expected_scope_version"] == 0
    assert operation.await_args.kwargs["actor_id"] == values.principal.subject_id


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
        request=Request({"type": "http", "headers": []}),
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


@pytest.mark.parametrize("requested_id", [None, UUID(int=1)])
def test_list_documents_route_returns_typed_responses(
    monkeypatch: pytest.MonkeyPatch,
    requested_id: UUID | None,
) -> None:
    async def return_documents(
        _service: SourceDocumentService,
        *,
        document_id: UUID | None = None,
    ) -> list[SourceDocumentModel]:
        assert document_id == requested_id
        return [source_document()]

    monkeypatch.setattr(SourceDocumentService, "list_documents", return_documents)
    values = arguments()
    responses = asyncio.run(
        list_source_documents(
            values.principal,
            values.session,
            values.object_storage,
            values.settings,
            document_id=requested_id,
        )
    )

    assert responses[0].intake_metadata is None
    assert responses[0].metadata_review_required is False
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


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (SourceCurriculumNotFoundError(), 404, "curriculum_version_not_found"),
        (SourceCurriculumInactiveError(), 409, "curriculum_version_inactive"),
        (SourceLearningScopeNotFoundError(), 404, "learning_scope_not_found"),
        (SourceLearningScopeInactiveError(), 409, "learning_scope_inactive"),
        (SourceLearningScopeMismatchError(), 422, "learning_scope_mismatch"),
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


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (SourceDocumentNotFoundError(UUID(int=1)), 404, "source_document_not_found"),
        (SourceCurriculumNotFoundError(), 404, "material_scope_not_found"),
        (SourceLearningScopeNotFoundError(), 404, "material_scope_not_found"),
        (
            ConcurrentMaterialScopeVersionError(0, 1),
            409,
            "concurrent_material_scope_modification",
        ),
        (
            MaterialScopeImmutableError(UUID(int=1)),
            409,
            "trusted_material_scope_immutable_remove_from_use",
        ),
        (SourceCurriculumInactiveError(), 409, "material_scope_inactive"),
        (SourceLearningScopeInactiveError(), 409, "material_scope_inactive"),
        (SourceLearningScopeMismatchError(), 422, "material_scope_mismatch"),
        (InvalidMaterialRemovalReasonError(), 422, "invalid_removal_reason"),
    ],
)
def test_material_error_mapping(error: Exception, status_code: int, code: str) -> None:
    response = _material_http_exception(error)
    assert response.status_code == status_code
    assert cast(dict[str, object], response.detail)["code"] == code


def test_source_response_backfills_legacy_defaults_and_exposes_derived_subject() -> None:
    document = source_document()
    document.curriculum_version_id = UUID(int=700)
    document.active_for_ai = None  # type: ignore[assignment]
    document.metadata_scope_version = None  # type: ignore[assignment]
    curriculum = CurriculumVersionModel(
        id=document.curriculum_version_id,
        exam_configuration_id=UUID(int=701),
        medium_id=UUID(int=702),
        subject_id=UUID(int=703),
        code="CURR",
        title="Curriculum",
        active=True,
        created_by=UUID(int=2),
        updated_by=UUID(int=2),
    )

    class CurriculumSession:
        async def get(self, _model: object, _identifier: UUID) -> CurriculumVersionModel:
            return curriculum

    response = asyncio.run(
        _source_document_response(
            cast(AsyncSession, CurriculumSession()),
            document,
            deduplicated=True,
            likely_metadata_duplicate_of_id=UUID(int=704),
        )
    )
    assert response.active_for_ai is True
    assert response.metadata_scope_version == 0
    assert response.subject_id == curriculum.subject_id
    assert response.deduplicated is True
    assert response.likely_metadata_duplicate_of_id == UUID(int=704)

    class MissingCurriculumSession:
        async def get(self, _model: object, _identifier: UUID) -> None:
            return None

    missing_curriculum_response = asyncio.run(
        _source_document_response(
            cast(AsyncSession, MissingCurriculumSession()),
            document,
        )
    )
    assert missing_curriculum_response.subject_id is None


def test_material_transition_routes_return_updated_typed_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = source_document()
    session_value = cast(AsyncSession, RouteSession(document))
    values = arguments()

    async def return_document(
        _service: SourceDocumentService,
        *_args: object,
        **_kwargs: object,
    ) -> SourceDocumentModel:
        return document

    monkeypatch.setattr(SourceDocumentService, "remove_from_ai_use", return_document)
    removed = asyncio.run(
        remove_material_from_use(
            document.id,
            MaterialRemoveRequest(reason="Wrong assignment.", expected_version=0),
            values.principal,
            session_value,
            values.object_storage,
            values.settings,
        )
    )
    monkeypatch.setattr(SourceDocumentService, "restore_to_ai_use", return_document)
    restored = asyncio.run(
        restore_material_to_use(
            document.id,
            MaterialRestoreRequest(expected_version=0),
            values.principal,
            session_value,
            values.object_storage,
            values.settings,
        )
    )
    monkeypatch.setattr(SourceDocumentService, "correct_scope", return_document)
    corrected = asyncio.run(
        correct_material_scope(
            document.id,
            MaterialScopeCorrectionRequest(curriculum_version_id=None, expected_version=0),
            values.principal,
            session_value,
            values.object_storage,
            values.settings,
        )
    )

    assert (removed.id, restored.id, corrected.id) == (document.id, document.id, document.id)


def test_material_routes_cover_reads_and_sanitized_transition_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = arguments()

    async def empty_materials(
        _service: SourceDocumentService,
        **_kwargs: object,
    ) -> tuple[object, ...]:
        return ()

    async def empty_summary(_service: SourceDocumentService) -> tuple[object, ...]:
        return ()

    async def fail_remove(
        _service: SourceDocumentService,
        *_args: object,
        **_kwargs: object,
    ) -> SourceDocumentModel:
        raise InvalidMaterialRemovalReasonError

    async def fail_restore(
        _service: SourceDocumentService,
        *_args: object,
        **_kwargs: object,
    ) -> SourceDocumentModel:
        raise SourceLearningScopeInactiveError

    async def fail_scope(
        _service: SourceDocumentService,
        *_args: object,
        **_kwargs: object,
    ) -> SourceDocumentModel:
        raise MaterialScopeImmutableError(UUID(int=1))

    monkeypatch.setattr(SourceDocumentService, "list_materials", empty_materials)
    monkeypatch.setattr(SourceDocumentService, "grade_summary", empty_summary)
    assert (
        asyncio.run(
            list_materials(
                values.principal,
                values.session,
                values.object_storage,
                values.settings,
                grade=7,
                subject_id=UUID(int=1),
                limit=50,
                offset=0,
            )
        )
        == []
    )
    assert (
        asyncio.run(
            list_material_grade_summary(
                values.principal,
                values.session,
                values.object_storage,
                values.settings,
            )
        )
        == []
    )

    monkeypatch.setattr(SourceDocumentService, "remove_from_ai_use", fail_remove)
    with pytest.raises(HTTPException) as removed:
        asyncio.run(
            remove_material_from_use(
                UUID(int=1),
                MaterialRemoveRequest(reason="reason", expected_version=0),
                values.principal,
                values.session,
                values.object_storage,
                values.settings,
            )
        )
    assert removed.value.status_code == 422

    monkeypatch.setattr(SourceDocumentService, "restore_to_ai_use", fail_restore)
    with pytest.raises(HTTPException) as restored:
        asyncio.run(
            restore_material_to_use(
                UUID(int=1),
                MaterialRestoreRequest(expected_version=0),
                values.principal,
                values.session,
                values.object_storage,
                values.settings,
            )
        )
    assert restored.value.status_code == 409

    monkeypatch.setattr(SourceDocumentService, "correct_scope", fail_scope)
    with pytest.raises(HTTPException) as corrected:
        asyncio.run(
            correct_material_scope(
                UUID(int=1),
                MaterialScopeCorrectionRequest(
                    curriculum_version_id=None,
                    expected_version=0,
                ),
                values.principal,
                values.session,
                values.object_storage,
                values.settings,
            )
        )
    assert corrected.value.status_code == 409
