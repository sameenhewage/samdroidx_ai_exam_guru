import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.types import Message
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes import page_images as routes
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.fidelity_models import (
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceImageIdentity,
    image_artifact_id,
)
from exam_guru_api.documents.page_reading_jobs import queue_source_read, run_source_read
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.infrastructure.object_storage import ObjectStorageOperationError
from tests.test_page_images import DOCUMENT_ID, FileSourceStore, image_source, rendered_fixture
from tests.test_tesseract_file_input import source_pdf

PREFIX = "/api/v1/admin"
ACTOR = UUID(int=98102)
IMAGE = f"{PREFIX}/materials/{DOCUMENT_ID}/pages/1/image"
ORIGINAL = f"{PREFIX}/materials/{DOCUMENT_ID}/original"
HEADERS = {"Authorization": "Bearer reviewer-token"}


class ImageIdentityProvider:
    async def authenticate(self, token: str) -> Principal:
        if token == "reviewer-token":
            return Principal(ACTOR, frozenset({AdminRole.REVIEWER}))
        if token == "no-permissions":
            return Principal(ACTOR, frozenset())
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


def document_model(source: SourceImageIdentity) -> SourceDocumentModel:
    return SourceDocumentModel(
        id=source.document_id,
        checksum_sha256=source.checksum_sha256,
        object_key=source.object_key,
        original_filename=source.filename,
        content_type="application/pdf",
        size_bytes=source.size_bytes,
        original_page_count=source.page_count,
        document_type=SourceDocumentType.TEACHER_GUIDE,
        created_by=ACTOR,
        updated_by=ACTOR,
        quarantined_for_teacher_use=False,
    )


def application(
    tmp_path: Path,
    store: FileSourceStore,
    document: SourceDocumentModel | None,
    *,
    provenance: dict[str, object] | None = None,
) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.state.identity_provider = ImageIdentityProvider()
    app.state.settings = Settings(
        _env_file=None, environment="test", storage_root=str(tmp_path / "data")
    )
    app.state.object_storage = store
    app.include_router(routes.router, prefix=PREFIX)
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = document
    session.scalar.return_value = provenance
    app.dependency_overrides[get_database_session] = lambda: session
    return app, session


def assert_private(headers: object) -> None:
    values = dict(cast(dict[str, str], headers))
    assert values["cache-control"] == "private, no-store"
    assert values["cross-origin-resource-policy"] == "same-origin"
    assert values["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", [IMAGE, ORIGINAL])
@pytest.mark.parametrize(
    ("token", "expected"), [(None, 401), ("forged", 401), ("no-permissions", 403)]
)
def test_material_image_and_original_authorize_before_storage_access(
    tmp_path: Path, path: str, token: str | None, expected: int
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    app, session = application(tmp_path, store, document_model(image_source(pdf)))
    with TestClient(app) as client:
        response = client.get(path, headers={"Authorization": f"Bearer {token}"} if token else {})
    assert response.status_code == expected
    assert_private(response.headers)
    session.get.assert_not_awaited()
    assert store.opens == 0


@pytest.mark.parametrize("path", [IMAGE, ORIGINAL])
def test_quarantined_material_is_not_visible_through_normal_source_read_paths(
    tmp_path: Path, path: str
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    document = document_model(image_source(pdf))
    document.quarantined_for_teacher_use = True
    app, session = application(tmp_path, store, document)
    with TestClient(app) as client:
        response = client.get(path, headers=HEADERS)
    assert response.status_code == 404
    assert_private(response.headers)
    assert store.opens == 0
    session.commit.assert_not_awaited()


def test_persisted_current_candidate_png_serves_without_rerendering_or_trust_writes(
    tmp_path: Path,
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    source = image_source(pdf)
    store = FileSourceStore(pdf)
    image = rendered_fixture(tmp_path / "page.png")
    artifacts = PageImageArtifacts(root=tmp_path / "data" / "fidelity-page-images")
    metadata = artifacts.persist(image, source=source)
    app, session = application(
        tmp_path,
        store,
        document_model(source),
        provenance={"source_checksum_sha256": source.checksum_sha256, "page_image": metadata},
    )
    pdf.unlink()
    with TestClient(app) as client:
        response = client.get(IMAGE, headers=HEADERS)
    assert response.status_code == 200
    assert response.content == image.path.read_bytes()
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-source-image-origin"] == "persisted"
    assert_private(response.headers)
    assert store.opens == 0
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.add.assert_not_called()
    assert session.scalar.await_args is not None
    query = str(session.scalar.await_args.args[0])
    assert "source_page_review_states" in query
    assert "current_candidate_id" in query
    assert "raw_text_utf8" not in query


@pytest.mark.parametrize(
    "damage", ["missing", "hash", "wrong_page", "wrong_source", "wrong_size", "empty_metadata"]
)
def test_artifact_failure_is_503_not_a_silent_regeneration(tmp_path: Path, damage: str) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    source = image_source(pdf)
    store = FileSourceStore(pdf)
    image = rendered_fixture(tmp_path / "page.png")
    artifacts = PageImageArtifacts(root=tmp_path / "data" / "fidelity-page-images")
    metadata = artifacts.persist(image, source=source)
    if damage == "missing":
        chunk = (
            tmp_path
            / "data"
            / "fidelity-page-images"
            / image_artifact_id(image.sha256).hex
            / "0000000000000000.chunk"
        )
        chunk.unlink()
    elif damage == "hash":
        metadata["sha256"] = "0" * 64
    elif damage == "wrong_page":
        metadata["page_number"] = 2
    elif damage == "wrong_source":
        metadata["source_checksum_sha256"] = "0" * 64
    elif damage == "wrong_size":
        metadata["source_size_bytes"] = source.size_bytes + 1
    else:
        metadata = {}
    app, session = application(
        tmp_path,
        store,
        document_model(source),
        provenance={"source_checksum_sha256": source.checksum_sha256, "page_image": metadata},
    )
    with TestClient(app) as client:
        response = client.get(IMAGE, headers=HEADERS)
    assert response.status_code == 503
    assert "source_page_image" in response.json()["detail"]["code"]
    assert_private(response.headers)
    assert store.opens == 0
    session.commit.assert_not_awaited()


def test_fallback_renders_exact_page_1001_without_mutating_candidates(tmp_path: Path) -> None:
    pdf = source_pdf(tmp_path / "1001.pdf", pages=1001)
    store = FileSourceStore(pdf)
    app, session = application(tmp_path, store, document_model(image_source(pdf, pages=1001)))
    with TestClient(app) as client:
        response = client.get(IMAGE.replace("pages/1/", "pages/1001/"), headers=HEADERS)
        missing = client.get(IMAGE.replace("pages/1/", "pages/1002/"), headers=HEADERS)
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.headers["x-source-image-origin"] == "original-render"
    assert missing.status_code == 404
    assert_private(response.headers)
    assert store.opens == store.closes == 1
    assert not (tmp_path / "data").exists()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("header", "expected", "body"),
    [
        (None, 200, None),
        ("bytes=0-4", 206, b"%PDF-"),
        ("bytes=-5", 206, None),
        ("bytes=9999999-", 416, None),
        ("bytes=0-1,4-5", 416, None),
    ],
)
def test_original_stream_supports_single_ranges_and_closes_every_context(
    tmp_path: Path, header: str | None, expected: int, body: bytes | None
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    document = document_model(image_source(pdf))
    document.original_filename = '../../PRIVATE\r\nX-Evil: yes".pdf'
    app, session = application(tmp_path, store, document)
    with TestClient(app) as client:
        response = client.get(
            ORIGINAL, headers={**HEADERS, **({"Range": header} if header else {})}
        )
    assert response.status_code == expected
    assert_private(response.headers)
    if expected in {200, 206}:
        assert response.headers["content-type"] == "application/pdf"
        assert (
            response.headers["content-security-policy"]
            == "default-src 'none'; frame-ancestors 'self'; sandbox"
        )
        assert response.headers["accept-ranges"] == "bytes"
        assert "\r" not in response.headers["content-disposition"]
        assert "\n" not in response.headers["content-disposition"]
        assert "x-evil" not in response.headers
        assert response.content == (
            body if body is not None else pdf.read_bytes()[-5:] if header else pdf.read_bytes()
        )
        assert int(response.headers["content-length"]) == len(response.content)
    if expected == 416:
        assert response.headers["content-range"] == f"bytes */{pdf.stat().st_size}"
    assert store.opens == store.closes == 1
    assert store.stream is not None
    assert store.stream.stream.closed
    session.commit.assert_not_awaited()


def test_head_original_verifies_and_closes_without_reading_response_body(tmp_path: Path) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    app, _ = application(tmp_path, store, document_model(image_source(pdf)))
    with TestClient(app) as client:
        response = client.head(ORIGINAL, headers={**HEADERS, "Range": "bytes=0-4"})
    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) == pdf.stat().st_size
    assert store.stream is not None
    assert not store.stream.reads
    assert store.opens == store.closes == 1


@pytest.mark.parametrize("damage", ["size", "key", "content_type", "unsupported"])
@pytest.mark.parametrize("path", [ORIGINAL, IMAGE])
def test_source_integrity_or_unsupported_streaming_fails_before_bytes(
    tmp_path: Path, path: str, damage: str
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    document = document_model(image_source(pdf))
    if damage == "size":
        document.size_bytes += 1
    elif damage == "key":
        document.object_key = f"sources/00/{'0' * 64}.pdf"
    elif damage == "content_type":
        document.content_type = "text/html"
    else:

        class UnsupportedStore:
            opens = 0
            closes = 0

            def open_source(self, key: str) -> AbstractContextManager[BinaryIO]:
                raise ObjectStorageOperationError("object_storage_streaming_unsupported")

        store = cast(FileSourceStore, UnsupportedStore())
    app, _ = application(tmp_path, store, document)
    with TestClient(app) as client:
        response = client.get(path, headers=HEADERS)
    assert response.status_code == 503
    assert_private(response.headers)
    assert not response.content.startswith(b"%PDF-")
    assert store.opens == store.closes
    if damage == "unsupported":
        assert response.json()["detail"]["code"] == "source_streaming_unsupported"


def test_stream_context_closes_when_asgi_send_disconnects(tmp_path: Path) -> None:
    from starlette.requests import ClientDisconnect

    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    source = image_source(pdf)

    async def scenario() -> None:
        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        response = routes.OriginalPDFResponse(source, store, head=False, range_header=None)
        with pytest.raises((OSError, ClientDisconnect)):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    asyncio.run(scenario())
    assert store.opens == store.closes == 1
    assert store.stream is not None
    assert store.stream.stream.closed


@pytest.fixture(scope="module")
def image_database_url() -> Iterator[str]:
    with PostgresContainer(
        image="pgvector/pgvector:0.8.6-pg18-trixie",
        username="exam_guru",
        password=uuid4().hex,
        dbname="page_images_test",
        driver="asyncpg",
    ) as postgres:
        url = postgres.get_connection_url()
        upgrade_database(url)
        yield url


@pytest.mark.integration
def test_worker_persists_image_provenance_and_gets_do_not_change_review_state(
    image_database_url: str, tmp_path: Path
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(path)
    source = image_source(path)
    artifacts = PageImageArtifacts(root=tmp_path / "data" / "fidelity-page-images")

    async def prepare() -> tuple[int, int]:
        engine = create_async_engine(image_database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                session.add(document_model(source))
                await session.commit()
                job = await queue_source_read(session, DOCUMENT_ID, actor_id=ACTOR)
                result = await run_source_read(
                    session, job.id, storage=store, image_artifacts=artifacts
                )
                assert result.status == "completed"
                candidate = await session.scalar(
                    select(PageTextCandidateModel).where(
                        PageTextCandidateModel.document_id == DOCUMENT_ID
                    )
                )
                assert candidate is not None
                assert candidate.raw_text_utf8
                assert "artifact" in cast(dict[str, object], candidate.provenance["page_image"])
                state = await session.get(PageReviewStateModel, (DOCUMENT_ID, 1))
                assert state is not None
                assert state.state == "needs_review"
                event_count = int(
                    await session.scalar(select(func.count()).select_from(PageReviewEventModel))
                    or 0
                )
                return state.version, event_count
        finally:
            await engine.dispose()

    version, event_count = asyncio.run(prepare())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(image_database_url)
        app.state.sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.identity_provider = ImageIdentityProvider()
    app.state.object_storage = store
    app.state.settings = Settings(
        _env_file=None, environment="test", storage_root=str(tmp_path / "data")
    )
    app.include_router(routes.router, prefix=PREFIX)

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with app.state.sessions() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_dependency
    with TestClient(app) as client:
        assert client.get(IMAGE, headers=HEADERS).status_code == 200
        assert client.get(ORIGINAL, headers={**HEADERS, "Range": "bytes=0-4"}).status_code == 206

    async def unchanged() -> None:
        engine = create_async_engine(image_database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                state = await session.get(PageReviewStateModel, (DOCUMENT_ID, 1))
                assert state is not None
                assert state.version == version
                assert state.state == "needs_review"
                assert (
                    await session.scalar(select(func.count()).select_from(PageReviewEventModel))
                    == event_count
                )
        finally:
            await engine.dispose()

    asyncio.run(unchanged())


def test_gigabyte_original_range_uses_bounded_stream_not_get_bytes(tmp_path: Path) -> None:
    pdf = tmp_path / "large.pdf"
    size = 1024**3 + 123
    with pdf.open("wb") as stream:
        stream.write(b"%PDF-1.7\n")
        stream.truncate(size)
    source = SourceImageIdentity(
        DOCUMENT_ID, "b" * 64, f"sources/bb/{'b' * 64}.pdf", size, None, "large.pdf"
    )
    store = FileSourceStore(pdf)
    app, session = application(tmp_path, store, document_model(source))
    with TestClient(app) as client:
        response = client.get(ORIGINAL, headers={**HEADERS, "Range": "bytes=0-4"})
    assert response.status_code == 206
    assert response.content == b"%PDF-"
    assert response.headers["content-range"] == f"bytes 0-4/{size}"
    assert store.stream is not None
    assert store.stream.reads == [5]
    assert store.stream.stream.closed
    session.commit.assert_not_awaited()


def test_source_context_closes_if_request_is_cancelled_while_storage_verifies(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class SlowStore(FileSourceStore):
        @contextmanager
        def open_source(self, key: str) -> Iterator[BinaryIO]:
            try:
                with super().open_source(key) as stream:
                    started.set()
                    if not release.wait(5):
                        raise OSError("fixture deadline")
                    yield stream
            finally:
                closed.set()

    pdf = source_pdf(tmp_path / "source.pdf")
    store = SlowStore(pdf)
    source = image_source(pdf)
    messages: list[Message] = []

    async def scenario() -> None:
        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            messages.append(message)

        response = routes.OriginalPDFResponse(source, store, head=False, range_header=None)
        task = asyncio.create_task(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        )
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0.05)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await asyncio.to_thread(closed.wait, 5)
        finally:
            release.set()

    asyncio.run(scenario())
    assert store.opens == store.closes == 1
    assert not messages


def test_original_mutation_after_headers_does_not_escape_as_verified_body(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    store = FileSourceStore(path)
    messages: list[Message] = []

    def mutate() -> None:
        with path.open("r+b") as stream:
            stream.seek(20)
            stream.write(b"tampered")

    async def scenario() -> None:
        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            messages.append(message)
            if message["type"] == "http.response.start":
                await asyncio.to_thread(mutate)

        response = routes.OriginalPDFResponse(source, store, head=False, range_header=None)
        with pytest.raises(PageImageError, match="source_original_unavailable"):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    asyncio.run(scenario())
    assert all(message["type"] != "http.response.body" for message in messages)
    assert store.opens == store.closes == 1


@pytest.mark.parametrize("page", ["0", "-1", str(2**31), "not-a-page"])
def test_invalid_page_numbers_have_private_validation_errors(tmp_path: Path, page: str) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    store = FileSourceStore(pdf)
    app, _ = application(tmp_path, store, document_model(image_source(pdf)))
    with TestClient(app) as client:
        response = client.get(IMAGE.replace("pages/1/", f"pages/{page}/"), headers=HEADERS)
    assert response.status_code == 422
    assert_private(response.headers)
    assert store.opens == 0


def test_hash_only_legacy_image_metadata_uses_explicit_original_render_without_cache_writes(
    tmp_path: Path,
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    source = image_source(pdf)
    old_image = rendered_fixture(tmp_path / "old-page.png")
    provenance: dict[str, object] = {
        "source_checksum_sha256": source.checksum_sha256,
        "page_image": old_image.metadata(),
    }
    store = FileSourceStore(pdf)
    app, session = application(tmp_path, store, document_model(source), provenance=provenance)
    with TestClient(app) as client:
        response = client.get(IMAGE, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["x-source-image-origin"] == "original-render"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.content != old_image.path.read_bytes()
    assert_private(response.headers)
    assert store.opens == store.closes == 1
    assert provenance["page_image"] == old_image.metadata()
    assert not (tmp_path / "data").exists()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()


def test_artifact_service_unavailable_never_silently_regenerates_existing_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = source_pdf(tmp_path / "source.pdf")
    source = image_source(pdf)
    artifacts = PageImageArtifacts(root=tmp_path / "data" / "fidelity-page-images")
    metadata = artifacts.persist(rendered_fixture(tmp_path / "page.png"), source=source)
    store = FileSourceStore(pdf)
    app, session = application(
        tmp_path,
        store,
        document_model(source),
        provenance={"source_checksum_sha256": source.checksum_sha256, "page_image": metadata},
    )
    monkeypatch.setattr(routes, "create_page_image_artifacts", lambda _settings: None)
    with TestClient(app) as client:
        response = client.get(IMAGE, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "source_page_image_artifact_unavailable"
    assert_private(response.headers)
    assert store.opens == 0
    session.commit.assert_not_awaited()
