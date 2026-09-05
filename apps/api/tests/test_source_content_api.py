import asyncio
import hashlib
import threading
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.core.config import Settings
from exam_guru_api.documents import service as document_service
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import (
    SourceDocumentContentUnavailableError,
    SourceDocumentService,
)
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


def preview_path(page_number: int = 1) -> str:
    return f"/api/v1/admin/source-documents/{DOCUMENT_ID}/pages/{page_number}/preview"


def preview_pdf(*, width: float = 595, height: float = 842, encrypted: bool = False) -> bytes:
    with pymupdf.open() as pdf:
        for text in ("First original page", "Second original page"):
            page = pdf.new_page(width=width, height=height)
            page.insert_text((20, 30), text)
        if encrypted:
            aes_256_encryption = 5
            return cast(bytes, pdf.tobytes(encryption=aes_256_encryption, user_pw="private"))
        return cast(bytes, pdf.tobytes())


def preview_document(data: bytes) -> SourceDocumentModel:
    checksum = hashlib.sha256(data).hexdigest()
    return source_document(
        checksum=checksum, size=len(data), object_key=f"sources/{checksum[:2]}/{checksum}.pdf"
    )


def assert_preview_headers(headers: object) -> None:
    values = dict(cast(dict[str, str], headers))
    assert values["cache-control"] == "private, no-store"
    assert values["x-content-type-options"] == "nosniff"
    assert values["x-frame-options"] == "SAMEORIGIN"
    assert values["cross-origin-resource-policy"] == "same-origin"


def test_preview_requires_source_read_before_storage_or_render() -> None:
    storage = ContentStorage(PAYLOAD)
    with content_client(source_document(), storage) as client:
        assert client.get(preview_path()).status_code == 401
        assert (
            client.get(
                preview_path(), headers={"Authorization": "Bearer forbidden-token"}
            ).status_code
            == 403
        )
    assert storage.requested_keys == []


@pytest.mark.parametrize("page_number", [-1, 0, 1001])
def test_preview_page_index_is_bounded_before_storage(page_number: int) -> None:
    storage = ContentStorage(PAYLOAD)
    with content_client(source_document(), storage) as client:
        response = client.get(
            preview_path(page_number), headers={"Authorization": "Bearer reader-token"}
        )
    assert response.status_code == 422
    assert storage.requested_keys == []


def test_preview_renders_only_requested_page_off_loop_and_keeps_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = preview_pdf()
    document = preview_document(data)
    storage = ContentStorage(data)
    rendered: list[int] = []
    loaded: list[int] = []
    original_pixmap = pymupdf.Page.get_pixmap
    original_load = cast(
        Callable[[pymupdf.Document, int], pymupdf.Page], pymupdf.Document.load_page
    )
    original_read = SourceDocumentService.read_original
    read_spy = AsyncMock(wraps=original_read)

    async def read_verified(self: SourceDocumentService, *args: object, **kwargs: object) -> object:
        return await read_spy(self, *args, **kwargs)

    def load_page(self: pymupdf.Document, number: int) -> pymupdf.Page:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        loaded.append(number)
        return original_load(self, number)

    def render_page(self: pymupdf.Page, **kwargs: Any) -> pymupdf.Pixmap:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        rendered.append(self.number)
        return original_pixmap(self, **kwargs)

    monkeypatch.setattr(SourceDocumentService, "read_original", read_verified)
    monkeypatch.setattr(pymupdf.Document, "load_page", load_page)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", render_page)
    with content_client(document, storage, max_upload_bytes=256 * 1024 * 1024) as client:
        response = client.get(preview_path(2), headers={"Authorization": "Bearer reader-token"})
        original = client.get(content_path(), headers={"Authorization": "Bearer reader-token"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(response.content) <= 8 * 1024 * 1024
    assert_preview_headers(response.headers)
    assert rendered == [1]
    assert loaded == [1]
    assert read_spy.await_count == 2
    assert original.content == data == storage.value
    assert document.checksum_sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("page_number", [3, 1000])
def test_preview_missing_page_is_sanitized_404(page_number: int) -> None:
    data = preview_pdf()
    with content_client(
        preview_document(data), ContentStorage(data), max_upload_bytes=4096
    ) as client:
        response = client.get(
            preview_path(page_number), headers={"Authorization": "Bearer reader-token"}
        )
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "source_page_not_found"}}
    assert_preview_headers(response.headers)


@pytest.mark.parametrize("missing_row", [True, False])
def test_preview_missing_original_is_sanitized_404(missing_row: bool) -> None:
    storage = ContentStorage(ObjectStorageOperationError("object_storage_not_found"))
    with content_client(None if missing_row else source_document(), storage) as client:
        response = client.get(preview_path(), headers={"Authorization": "Bearer reader-token"})
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "source_document_not_found"}}
    assert_preview_headers(response.headers)


@pytest.mark.parametrize(
    "failure",
    ["checksum", "unicode_checksum", "size", "input_limit", "malformed", "encrypted", "storage"],
)
def test_preview_invalid_original_fails_closed_without_render(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = preview_pdf(encrypted=failure == "encrypted") if failure != "malformed" else PAYLOAD
    document = preview_document(data)
    storage = ContentStorage(data)
    if failure == "checksum":
        document.checksum_sha256 = "f" * 64
    elif failure == "unicode_checksum":
        document.checksum_sha256 = "අ" * 64
    elif failure == "size":
        document.size_bytes += 1
    elif failure == "input_limit":
        document.size_bytes = 256 * 1024 * 1024 + 1
    elif failure == "storage":
        storage.value = RuntimeError("private root and credentials")
    rendered: list[bool] = []

    def no_render(*args: object, **kwargs: object) -> None:
        rendered.append(True)
        raise AssertionError("invalid original must not render")

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", no_render)
    with content_client(document, storage, max_upload_bytes=256 * 1024 * 1024) as client:
        response = client.get(preview_path(), headers={"Authorization": "Bearer reader-token"})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "source_page_preview_unavailable"}}
    assert_preview_headers(response.headers)
    assert rendered == []
    if failure == "input_limit":
        assert storage.requested_keys == []


@pytest.mark.parametrize(("width", "height"), [(20000, 20000), (20000, 100), (100, 20000)])
def test_preview_downscales_before_pixel_allocation(width: float, height: float) -> None:
    data = preview_pdf(width=width, height=height)
    with content_client(
        preview_document(data), ContentStorage(data), max_upload_bytes=4096
    ) as client:
        response = client.get(preview_path(), headers={"Authorization": "Bearer reader-token"})
    assert response.status_code == 200
    pixmap = pymupdf.Pixmap(response.content)
    assert 0 < pixmap.width * pixmap.height <= 4_000_000
    assert max(pixmap.width, pixmap.height) <= 4096


def test_preview_png_size_cap_and_render_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = preview_pdf()
    document = preview_document(data)

    def too_large(*args: object, **kwargs: object) -> bytes:
        return b"x" * (8 * 1024 * 1024 + 1)

    monkeypatch.setattr(pymupdf.Pixmap, "tobytes", too_large)
    with content_client(document, ContentStorage(data), max_upload_bytes=4096) as client:
        response = client.get(preview_path(), headers={"Authorization": "Bearer reader-token"})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "source_page_preview_unavailable"}}


def test_preview_timeout_holds_render_slot_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = preview_pdf()
    storage = ContentStorage(data)
    service = SourceDocumentService(
        cast(AsyncSession, ContentSession(preview_document(data))), storage, max_upload_bytes=4096
    )
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_pixmap = pymupdf.Page.get_pixmap
    monkeypatch.setattr(document_service, "_PREVIEW_TIMEOUT_SECONDS", 0.05)

    def slow_render(self: pymupdf.Page, **kwargs: Any) -> pymupdf.Pixmap:
        started.set()
        try:
            assert release.wait(2)
            return original_pixmap(self, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", slow_render)

    async def exercise() -> None:
        try:
            with pytest.raises(SourceDocumentContentUnavailableError):
                await service.read_page_preview(DOCUMENT_ID, page_number=1)
            assert started.is_set()
            assert not finished.is_set()
            with pytest.raises(SourceDocumentContentUnavailableError):
                await service.read_page_preview(DOCUMENT_ID, page_number=2)
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(exercise())


@pytest.mark.parametrize("page_number", [0, 1001])
def test_preview_service_rejects_page_bounds_before_read(page_number: int) -> None:
    storage = ContentStorage(PAYLOAD)
    service = SourceDocumentService(
        cast(AsyncSession, ContentSession(source_document())), storage, max_upload_bytes=4096
    )
    with pytest.raises(document_service.SourceDocumentPageNotFoundError):
        asyncio.run(service.read_page_preview(DOCUMENT_ID, page_number=page_number))
    assert storage.requested_keys == []


@pytest.mark.parametrize("failure", ["expired", "input", "rectangle", "pixel_bounds"])
def test_preview_renderer_rejects_unsafe_allocation_before_render(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = preview_pdf()
    deadline = float("inf")
    if failure == "expired":
        deadline = 0
    elif failure == "input":
        monkeypatch.setattr(document_service, "_MAX_PREVIEW_INPUT_BYTES", 1)
    elif failure == "rectangle":
        monkeypatch.setattr(pymupdf.Page, "rect", property(lambda _: pymupdf.Rect(0, 0, 0, 842)))
    else:
        monkeypatch.setattr(document_service, "_MAX_PREVIEW_PIXELS", 1)

    def no_render(*args: object, **kwargs: object) -> None:
        pytest.fail("unsafe dimensions must be rejected before pixel allocation")

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", no_render)
    with pytest.raises(SourceDocumentContentUnavailableError):
        document_service._render_original_page(data, 1, deadline)
    assert not document_service._PREVIEW_RENDER_LOCK.locked()


def test_preview_openapi_declares_auth_binary_and_page_bounds() -> None:
    operation = create_app().openapi()["paths"][
        "/api/v1/admin/source-documents/{document_id}/pages/{page_number}/preview"
    ]["get"]
    assert operation["operationId"] == "get_source_page_preview"
    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["responses"]["200"]["content"]["image/png"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    page = next(item for item in operation["parameters"] if item["name"] == "page_number")
    assert page["schema"]["minimum"] == 1
    assert page["schema"]["maximum"] == 1000
    assert set(operation["responses"]) >= {"200", "401", "403", "404", "422", "503"}
