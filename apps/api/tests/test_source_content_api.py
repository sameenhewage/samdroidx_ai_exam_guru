import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.infrastructure.object_storage import (
    ObjectPage,
    ObjectStorageOperationError,
    ObjectTagMutation,
    StoredObject,
)
from exam_guru_api.main import create_app

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000901")
PAYLOAD = b"%PDF-1.7\nprivate original fixture\n%%EOF"
CHECKSUM = hashlib.sha256(PAYLOAD).hexdigest()
OBJECT_KEY = f"sources/{CHECKSUM[:2]}/{CHECKSUM}.pdf"


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "reader-token":
            return Principal(subject_id=UUID(int=902), roles=frozenset({AdminRole.REVIEWER}))
        if access_token == "forbidden-token":
            return Principal(subject_id=UUID(int=903), roles=frozenset())
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class NoOpResources:
    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        return None


class ContentSession:
    def __init__(self, document: SourceDocumentModel | None) -> None:
        self.document = document

    async def get(
        self,
        _model: object,
        identifier: UUID,
        **_kwargs: object,
    ) -> SourceDocumentModel | None:
        assert identifier == DOCUMENT_ID
        return self.document


class ContentStorage:
    def __init__(self, value: bytes | Exception) -> None:
        self.value = value
        self.requested_keys: list[str] = []

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        raise AssertionError((key, data, content_type))

    def get_bytes(self, key: str) -> bytes:
        self.requested_keys.append(key)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        raise AssertionError((max_keys, continuation_token))

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        raise AssertionError((key, candidate_detected_at))

    def close(self) -> None:
        return None


def source_document(
    *,
    checksum: str = CHECKSUM,
    size: int = len(PAYLOAD),
    object_key: str = OBJECT_KEY,
    filename: str = 'Scholarship ප්‍රශ්න "2026".pdf',
) -> SourceDocumentModel:
    now = datetime.now(UTC)
    return SourceDocumentModel(
        id=DOCUMENT_ID,
        checksum_sha256=checksum,
        object_key=object_key,
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=size,
        document_type=SourceDocumentType.PAST_PAPER,
        extraction_status=ExtractionStatus.UPLOADED,
        curriculum_version_id=None,
        year=2026,
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
        created_by=UUID(int=902),
        updated_by=UUID(int=902),
        created_at=now,
        updated_at=now,
    )


def content_client(
    document: SourceDocumentModel | None,
    storage: ContentStorage,
    *,
    max_upload_bytes: int = 1_024,
) -> TestClient:
    application = create_app(
        settings=Settings(environment="test", max_upload_bytes=max_upload_bytes),
        identity_provider=StaticIdentityProvider(),
        object_storage=storage,
        resource_factory=lambda _: NoOpResources(),
    )

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield ContentSession(document)  # type: ignore[misc]

    application.dependency_overrides[get_database_session] = session_override
    return TestClient(application)


def content_path() -> str:
    return f"/api/v1/admin/source-documents/{DOCUMENT_ID}/content"


def test_authenticated_source_reader_gets_verified_inline_private_pdf_from_exact_db_key() -> None:
    storage = ContentStorage(PAYLOAD)
    with content_client(source_document(), storage) as client:
        response = client.get(content_path(), headers={"Authorization": "Bearer reader-token"})

    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert storage.requested_keys == [OBJECT_KEY]
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'self'; sandbox"
    )
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline; filename=")
    assert "filename*=UTF-8''" in disposition
    assert "%" in disposition
    assert not {"\r", "\n", "/", "\\"}.intersection(disposition)


def test_source_content_disposition_remains_bounded_for_maximum_unicode_filename() -> None:
    document = source_document(filename=("අ" * 251) + ".pdf")
    with content_client(document, ContentStorage(PAYLOAD)) as client:
        response = client.get(content_path(), headers={"Authorization": "Bearer reader-token"})

    disposition = response.headers["content-disposition"]
    assert response.status_code == 200
    assert len(disposition.encode("ascii")) <= 1_024
    assert disposition.startswith("inline; filename=\"source.pdf\"; filename*=UTF-8''")
    assert "අ" not in disposition


def test_source_content_requires_source_read_permission() -> None:
    storage = ContentStorage(PAYLOAD)
    with content_client(source_document(), storage) as client:
        unauthenticated = client.get(content_path())
        forbidden = client.get(
            content_path(),
            headers={"Authorization": "Bearer forbidden-token"},
        )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert storage.requested_keys == []


def test_missing_database_row_and_missing_private_object_share_generic_404() -> None:
    missing_storage = ContentStorage(ObjectStorageOperationError("object_storage_not_found"))
    with content_client(None, ContentStorage(PAYLOAD)) as client:
        missing_row = client.get(
            content_path(),
            headers={"Authorization": "Bearer reader-token"},
        )
    with content_client(source_document(), missing_storage) as client:
        missing_object = client.get(
            content_path(),
            headers={"Authorization": "Bearer reader-token"},
        )

    assert missing_row.status_code == missing_object.status_code == 404
    assert (
        missing_row.json()
        == missing_object.json()
        == {"detail": {"code": "source_document_not_found"}}
    )
    assert "private" not in missing_object.text


@pytest.mark.parametrize(
    ("document", "stored_value"),
    [
        (source_document(checksum="f" * 64), PAYLOAD),
        (source_document(size=len(PAYLOAD) + 1), PAYLOAD),
        (source_document(size=2_000), PAYLOAD),
        (source_document(object_key="sources/../private.pdf"), PAYLOAD),
        (
            source_document(),
            ObjectStorageOperationError("object_storage_read_failed-private-root"),
        ),
        (source_document(), RuntimeError("private storage implementation failure")),
    ],
)
def test_source_content_corruption_or_storage_failure_is_sanitized_503(
    document: SourceDocumentModel,
    stored_value: bytes | Exception,
) -> None:
    with content_client(document, ContentStorage(stored_value)) as client:
        response = client.get(
            content_path(),
            headers={"Authorization": "Bearer reader-token"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "source_document_content_unavailable"}}
    assert "private" not in response.text
    assert OBJECT_KEY not in response.text


def test_source_content_openapi_is_generated_as_authenticated_binary_get() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v1/admin/source-documents/{document_id}/content"]["get"]

    assert operation["operationId"] == "get_source_document_content"
    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["responses"]["200"]["content"]["application/pdf"]["schema"] == {
        "format": "binary",
        "type": "string",
    }
    assert set(operation["responses"]) >= {"200", "401", "403", "404", "503"}
