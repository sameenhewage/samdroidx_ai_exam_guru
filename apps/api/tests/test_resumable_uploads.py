import asyncio
import hashlib
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message

from exam_guru_api.api.routes import source_uploads as routes
from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.auth.rate_limits import NoOpRateLimiter, UnavailableRateLimiter
from exam_guru_api.documents.resumable_uploads import (
    ResumableUploadError,
    ResumableUploadService,
    UploadLimits,
)
from exam_guru_api.documents.service import (
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceLearningScopeInactiveError,
    SourceLearningScopeMismatchError,
    SourceLearningScopeNotFoundError,
)
from exam_guru_api.documents.upload_models import SourceUploadChunkModel, SourceUploadSessionModel
from exam_guru_api.documents.upload_schemas import (
    UPLOAD_CHUNK_BYTES,
    SourceUploadCreateRequest,
    SourceUploadResponse,
    UploadStatus,
)
from exam_guru_api.infrastructure.object_storage import (
    ObjectAlreadyExistsError,
    ObjectStorage,
    ObjectStorageOperationError,
    StoredObject,
)
from exam_guru_api.infrastructure.private_artifacts import (
    PrivateArtifactError,
    PrivateUploadArtifacts,
)

ADMIN = Principal(UUID(int=36001), frozenset({AdminRole.ADMIN}))
OTHER_ADMIN = Principal(UUID(int=36002), frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(UUID(int=36003), frozenset({AdminRole.REVIEWER}))
UPLOAD_ID = UUID(int=36004)
PREFIX = "/api/v1/admin/source-uploads"
AUTH = {"Authorization": "Bearer admin-token"}
PDF = b"%PDF-1.7\nfixture\n%%EOF"


class RecordingUploadDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.dispatched: list[UUID] = []
        self.fail = fail

    def dispatch(self, upload_id: UUID) -> str:
        self.dispatched.append(upload_id)
        if self.fail:
            raise ConnectionError("PRIVATE queue details")
        return "upload-message"


class IdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return ADMIN
        if access_token == "reviewer-token":
            return REVIEWER
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


def request_body() -> dict[str, object]:
    return {
        "filename": "source.pdf",
        "size_bytes": len(PDF),
        "document_type": "syllabus",
        "intake_metadata": {"candidate_grade": 11, "subject_label": "Mathematics"},
    }


def upload_view(*, status: UploadStatus = UploadStatus.UPLOADING) -> SourceUploadResponse:
    now = datetime.now(UTC)
    return SourceUploadResponse(
        id=UPLOAD_ID,
        filename="source.pdf",
        size_bytes=len(PDF),
        document_type="syllabus",
        intake_metadata={"candidate_grade": 11},
        status=status,
        next_offset=0 if status is UploadStatus.UPLOADING else len(PDF),
        chunk_size_bytes=UPLOAD_CHUNK_BYTES,
        verified_bytes=0,
        version=0,
        created_at=now,
        updated_at=now,
    )


def application(service: AsyncMock | None = None) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.state.identity_provider = IdentityProvider()
    app.state.rate_limiter = NoOpRateLimiter()
    app.state.source_upload_dispatcher = RecordingUploadDispatcher()
    backend = service if service is not None else AsyncMock(spec=ResumableUploadService)
    backend.create.return_value = upload_view()
    backend.get.return_value = upload_view()
    backend.append_chunk.return_value = upload_view()
    backend.request_completion.return_value = upload_view(status=UploadStatus.PENDING)
    app.dependency_overrides[routes.get_resumable_upload_service] = lambda: backend
    app.include_router(routes.router, prefix="/api/v1/admin")
    return app, backend


def test_request_identity_is_optional_uuid_and_lookup_has_a_typed_private_contract() -> None:
    request_id = UUID(int=38001)
    parsed = SourceUploadCreateRequest.model_validate(
        {**request_body(), "request_id": str(request_id)}
    )
    assert parsed.request_id == request_id
    assert SourceUploadCreateRequest.model_validate(request_body()).request_id is None
    app, backend = application()
    view = upload_view().model_copy(update={"request_id": request_id})
    backend.create.return_value = view
    backend.get_by_request.return_value = view
    with TestClient(app) as client:
        created = client.post(PREFIX, json=parsed.model_dump(mode="json"), headers=AUTH)
        found = client.get(f"{PREFIX}/by-request/{request_id}", headers=AUTH)
        assert client.get(f"{PREFIX}/by-request/not-a-uuid", headers=AUTH).status_code == 422
        schema = client.get("/openapi.json").json()
    assert created.status_code == 201
    assert found.status_code == 200
    assert created.json()["id"] == found.json()["id"] == str(UPLOAD_ID)
    assert found.json()["request_id"] == str(request_id)
    assert found.headers["cache-control"] == "no-store"
    backend.get_by_request.assert_awaited_once_with(request_id, principal=ADMIN)
    operation = schema["paths"][f"{PREFIX}/by-request/{{request_id}}"]["get"]
    assert operation["operationId"] == "get_source_upload_by_request"
    assert operation["parameters"][0]["schema"]["format"] == "uuid"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SourceUploadResponse"
    }


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer reviewer-token"}])
def test_request_identity_lookup_enforces_source_write(headers: dict[str, str]) -> None:
    app, backend = application()
    with TestClient(app) as client:
        found = client.get(f"{PREFIX}/by-request/{UPLOAD_ID}", headers=headers)
    assert found.status_code == (403 if headers else 401)
    assert not backend.mock_calls


def test_request_identity_lookup_and_conflict_errors_are_opaque() -> None:
    app, backend = application()
    backend.get_by_request.side_effect = ResumableUploadError("source_upload_not_found", 404)
    backend.create.side_effect = ResumableUploadError("source_upload_request_conflict", 409)
    with TestClient(app) as client:
        missing = client.get(f"{PREFIX}/by-request/{UPLOAD_ID}", headers=AUTH)
        conflict = client.post(
            PREFIX, json={**request_body(), "request_id": str(UPLOAD_ID)}, headers=AUTH
        )
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"code": "source_upload_not_found"}}
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": {"code": "source_upload_request_conflict"}}


def test_upload_schema_accepts_operationally_configured_large_files_and_candidate_grades() -> None:
    body = request_body()
    body["size_bytes"] = 300 * 1024 * 1024
    parsed = SourceUploadCreateRequest.model_validate(body)
    assert parsed.size_bytes == 300 * 1024 * 1024
    assert parsed.intake_metadata.candidate_grade == 11
    assert SourceUploadCreateRequest.model_validate({**body, "size_bytes": 2**63 - 1})
    assert UPLOAD_CHUNK_BYTES == 4 * 1024 * 1024
    limits = UploadLimits(
        max_total_bytes=2**63 - 1,
        max_owner_staged_bytes=2**63 - 1,
        max_staged_bytes=2**63 - 1,
    )
    assert limits.max_total_bytes == 2**63 - 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filename", "../secret.pdf"),
        ("filename", "source.txt"),
        ("filename", "source\x00.pdf"),
        ("filename", "e\u0301.pdf"),
        ("filename", "x" * 252 + ".pdf"),
        ("size_bytes", 0),
        ("size_bytes", True),
        ("size_bytes", "300"),
        ("size_bytes", 2**63),
        ("expected_checksum_sha256", "bad"),
        ("request_id", "bad"),
        ("request_id", True),
        ("request_id", 123),
        ("intake_metadata", {"candidate_grade": 14}),
        ("intake_metadata", {"metadata_review_required": False}),
        ("object_key", "/private/source.pdf"),
        ("owner_id", str(OTHER_ADMIN.subject_id)),
        ("unit_id", str(UUID(int=42))),
    ],
)
def test_upload_schema_rejects_unsafe_metadata(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SourceUploadCreateRequest.model_validate({**request_body(), field: value})


@pytest.mark.parametrize("value", [0, -1, True, 2**63])
def test_upload_limits_do_not_accept_invalid_operational_budgets(value: int) -> None:
    with pytest.raises(ValueError, match="upload byte budgets"):
        UploadLimits(max_total_bytes=value, max_owner_staged_bytes=100, max_staged_bytes=100)


def test_create_fails_closed_on_unsafe_staging_permissions_before_reserving_quota(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    session = AsyncMock(spec=AsyncSession)
    backend = ResumableUploadService(
        session,
        cast(ObjectStorage, object()),
        PrivateUploadArtifacts(root=root),
        limits=UploadLimits(max_total_bytes=100, max_owner_staged_bytes=100, max_staged_bytes=100),
    )
    with pytest.raises(ResumableUploadError, match="source_upload_staging_unavailable") as raised:
        asyncio.run(
            backend.create(
                SourceUploadCreateRequest.model_validate(request_body()), principal=ADMIN
            )
        )
    assert raised.value.status_code == 503
    session.execute.assert_not_called()
    session.add.assert_not_called()
    assert not list(root.iterdir())
    assert root.stat().st_mode & 0o777 == 0o755


def test_upload_tables_store_only_metadata_with_bigint_offsets_and_sizes() -> None:
    session_columns = SourceUploadSessionModel.__table__.c
    chunk_columns = SourceUploadChunkModel.__table__.c
    for column in (session_columns.size_bytes, session_columns.next_offset, chunk_columns.offset):
        assert str(column.type) == "BIGINT"
    for table in (SourceUploadSessionModel.__table__, SourceUploadChunkModel.__table__):
        assert not any(str(column.type) in {"BLOB", "BYTEA", "BINARY"} for column in table.c)
    assert set(chunk_columns.keys()) == {
        "upload_id",
        "offset",
        "size_bytes",
        "checksum_sha256",
        "created_at",
    }


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), "1"])
def test_finalization_rejects_invalid_deadlines_before_claiming_a_lease(
    tmp_path: Path,
    deadline: object,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    backend = ResumableUploadService(
        session,
        cast(ObjectStorage, object()),
        PrivateUploadArtifacts(root=tmp_path / "private"),
        limits=UploadLimits(max_total_bytes=100, max_owner_staged_bytes=100, max_staged_bytes=100),
    )
    with pytest.raises(ValueError, match="deadline"):
        asyncio.run(backend.finalize(UPLOAD_ID, execution_deadline=cast(float, deadline)))
    assert not session.mock_calls


@pytest.mark.parametrize(
    "method",
    ["create", "get", "get_by_request", "list_chunks", "append_chunk", "request_completion"],
)
def test_service_authorizes_before_accessing_database_or_staging(
    tmp_path: Path, method: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    service = ResumableUploadService(
        session,
        cast(ObjectStorage, object()),
        PrivateUploadArtifacts(root=tmp_path / "staging"),
        limits=UploadLimits(max_total_bytes=100, max_owner_staged_bytes=100, max_staged_bytes=100),
    )
    calls: dict[str, Callable[[], Coroutine[object, object, object]]] = {
        "create": lambda: service.create(
            SourceUploadCreateRequest.model_validate(request_body()), principal=REVIEWER
        ),
        "get": lambda: service.get(UPLOAD_ID, principal=REVIEWER),
        "get_by_request": lambda: service.get_by_request(UPLOAD_ID, principal=REVIEWER),
        "list_chunks": lambda: service.list_chunks(UPLOAD_ID, principal=REVIEWER),
        "append_chunk": lambda: service.append_chunk(
            UPLOAD_ID, principal=REVIEWER, offset=0, data=PDF
        ),
        "request_completion": lambda: service.request_completion(UPLOAD_ID, principal=REVIEWER),
    }
    with pytest.raises(AuthorizationError):
        asyncio.run(calls[method]())
    assert not session.mock_calls
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", PREFIX),
        ("GET", f"{PREFIX}/{UPLOAD_ID}"),
        ("PUT", f"{PREFIX}/{UPLOAD_ID}/chunks?offset=0"),
        ("POST", f"{PREFIX}/{UPLOAD_ID}/complete"),
    ],
)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer reviewer-token"}])
def test_all_upload_routes_require_source_write(
    method: str, path: str, headers: dict[str, str]
) -> None:
    app, service = application()
    with TestClient(app) as client:
        response = client.request(method, path, json=request_body(), headers=headers)
    assert response.status_code == (403 if headers else 401)
    assert not service.mock_calls


def test_create_and_complete_return_typed_private_resources_without_synchronous_finalization() -> (
    None
):
    app, service = application()
    with TestClient(app) as client:
        created = client.post(PREFIX, json=request_body(), headers=AUTH)
        completed = client.post(f"{PREFIX}/{UPLOAD_ID}/complete", headers=AUTH)
        polled = client.get(f"{PREFIX}/{UPLOAD_ID}", headers=AUTH)
        schema = client.get("/openapi.json").json()
    assert created.status_code == 201
    assert created.headers["location"] == f"{PREFIX}/{UPLOAD_ID}"
    assert completed.status_code == 202
    assert completed.json()["status"] == "pending"
    assert polled.status_code == 200
    assert not {"object_key", "staging_path", "owner_id", "lease_token"} & created.json().keys()
    service.finalize.assert_not_called()
    assert service.create.await_args is not None
    assert service.create.await_args.kwargs["principal"] == ADMIN
    chunk_schema = schema["paths"][f"{PREFIX}/{{upload_id}}/chunks"]["put"]
    assert "application/octet-stream" in chunk_schema["requestBody"]["content"]
    for code in ("415", "422", "503"):
        assert chunk_schema["responses"][code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiErrorResponse"
        }


@pytest.mark.parametrize("queue_fails", [False, True])
def test_completion_enqueues_a_durable_id_and_remains_accepted_on_broker_failure(
    queue_fails: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, service = application()
    dispatcher = RecordingUploadDispatcher(fail=queue_fails)
    app.state.source_upload_dispatcher = dispatcher
    with TestClient(app) as client:
        response = client.post(f"{PREFIX}/{UPLOAD_ID}/complete", headers=AUTH)
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert dispatcher.dispatched == [UPLOAD_ID]
    service.request_completion.assert_awaited_once()
    service.finalize.assert_not_called()
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("status", [UploadStatus.COMPLETED, UploadStatus.FAILED])
def test_terminal_completion_is_idempotent_and_does_not_requeue(status: UploadStatus) -> None:
    app, service = application()
    service.request_completion.return_value = upload_view(status=status)
    with TestClient(app) as client:
        response = client.post(f"{PREFIX}/{UPLOAD_ID}/complete", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == status.value
    assert not app.state.source_upload_dispatcher.dispatched
    service.finalize.assert_not_called()


def test_completion_fails_closed_before_mutation_when_dispatcher_is_unconfigured() -> None:
    app, service = application()
    app.state.source_upload_dispatcher = None
    with TestClient(app) as client:
        response = client.post(f"{PREFIX}/{UPLOAD_ID}/complete", headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "source_upload_unavailable"}}
    service.request_completion.assert_not_called()


def test_chunk_route_streams_only_bounded_body_and_passes_checksum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, service = application()

    async def forbidden_body(_self: Request) -> bytes:
        raise AssertionError("chunk route must not call Request.body")

    monkeypatch.setattr(Request, "body", forbidden_body)
    checksum = hashlib.sha256(PDF).hexdigest()
    with TestClient(app) as client:
        response = client.put(
            f"{PREFIX}/{UPLOAD_ID}/chunks?offset=0",
            content=PDF,
            headers={
                **AUTH,
                "Content-Type": "application/octet-stream",
                "X-Chunk-SHA256": checksum,
            },
        )
    assert response.status_code == 200
    assert service.append_chunk.await_args is not None
    assert service.append_chunk.await_args.kwargs == {
        "principal": ADMIN,
        "offset": 0,
        "data": PDF,
        "checksum_sha256": checksum,
    }


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"Content-Length": str(UPLOAD_CHUNK_BYTES + 1)}, b"x", 413),
        ({"Content-Length": "bad"}, b"x", 422),
        ({"Content-Length": "1"}, b"xx", 422),
        ({"Content-Length": "5"}, b"x", 422),
        ({"Content-Type": "text/plain"}, b"x", 415),
        ({"X-Chunk-SHA256": "bad"}, b"x", 422),
    ],
)
def test_chunk_contract_rejects_oversize_truncated_and_malformed_requests_before_staging(
    headers: dict[str, str], body: bytes, status: int
) -> None:
    app, service = application()
    with TestClient(app) as client:
        response = client.put(
            f"{PREFIX}/{UPLOAD_ID}/chunks?offset=0",
            content=body,
            headers={**AUTH, "Content-Type": "application/octet-stream", **headers},
        )
    assert response.status_code == status
    service.append_chunk.assert_not_called()


def test_chunk_stream_enforces_cap_without_content_length() -> None:
    app, service = application()
    with TestClient(app) as client:
        response = client.put(
            f"{PREFIX}/{UPLOAD_ID}/chunks?offset=0",
            content=iter([b"x" * (1024 * 1024)] * 5),
            headers={**AUTH, "Content-Type": "application/octet-stream"},
        )
    assert response.status_code == 413
    service.append_chunk.assert_not_called()


def test_owner_isolation_errors_are_opaque_and_do_not_consume_chunk_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, service = application()
    service.get.side_effect = ResumableUploadError("source_upload_not_found", 404)
    stream = AsyncMock(side_effect=AssertionError("wrong owner's stream consumed"))
    monkeypatch.setattr(Request, "stream", stream)
    with TestClient(app) as client:
        response = client.put(
            f"{PREFIX}/{UPLOAD_ID}/chunks?offset=0",
            content=PDF,
            headers={**AUTH, "Content-Type": "application/octet-stream"},
        )
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "source_upload_not_found"}}
    stream.assert_not_called()


def test_receipt_endpoint_is_typed_private_bounded_and_suitable_for_prefix_verification() -> None:
    from exam_guru_api.documents.upload_schemas import SourceUploadChunkPageResponse

    app, service = application()
    checksum = hashlib.sha256(PDF).hexdigest()
    page = SourceUploadChunkPageResponse(
        upload_id=UPLOAD_ID,
        next_offset=len(PDF),
        receipts=[{"offset": 0, "size_bytes": len(PDF), "checksum_sha256": checksum}],
        next_receipt_offset=None,
    )
    service.list_chunks.return_value = page
    with TestClient(app) as client:
        response = client.get(f"{PREFIX}/{UPLOAD_ID}/chunks?offset=0&limit=1", headers=AUTH)
        schema = client.get("/openapi.json").json()
        for query in ("limit=0", "limit=65", "offset=-1", f"offset={2**63}"):
            assert (
                client.get(f"{PREFIX}/{UPLOAD_ID}/chunks?{query}", headers=AUTH).status_code == 422
            )
    assert response.status_code == 200
    assert response.json() == page.model_dump(mode="json")
    assert response.headers["cache-control"] == "no-store"
    service.list_chunks.assert_awaited_once_with(UPLOAD_ID, principal=ADMIN, offset=0, limit=1)
    receipt_schema = schema["paths"][f"{PREFIX}/{{upload_id}}/chunks"]["get"]
    assert receipt_schema["operationId"] == "list_source_upload_chunks"
    assert receipt_schema["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SourceUploadChunkPageResponse"
    }


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer reviewer-token"}])
def test_receipt_endpoint_requires_source_write(headers: dict[str, str]) -> None:
    app, service = application()
    with TestClient(app) as client:
        response = client.get(f"{PREFIX}/{UPLOAD_ID}/chunks", headers=headers)
    assert response.status_code == (403 if headers else 401)
    assert not service.mock_calls


def test_receipt_endpoint_never_discloses_another_owners_hashes() -> None:
    app, service = application()
    service.list_chunks.side_effect = ResumableUploadError("source_upload_not_found", 404)
    with TestClient(app) as client:
        response = client.get(f"{PREFIX}/{UPLOAD_ID}/chunks", headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "source_upload_not_found"}}


def restored_service(
    tmp_path: Path, *, status: UploadStatus = UploadStatus.UPLOADING
) -> tuple[ResumableUploadService, AsyncMock, SourceUploadSessionModel, Mock]:
    row = SourceUploadSessionModel(
        **upload_view(status=status).model_dump(exclude={"chunk_size_bytes", "source_read_job_id"}),
        owner_id=ADMIN.subject_id,
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = row
    session.scalars.return_value = ()
    storage = Mock(spec=ObjectStorage)
    backend = ResumableUploadService(
        session,
        storage,
        PrivateUploadArtifacts(root=tmp_path / "staging"),
        limits=UploadLimits(
            max_total_bytes=2**63 - 1, max_owner_staged_bytes=1000, max_staged_bytes=1000
        ),
    )
    return backend, session, row, storage


@pytest.mark.parametrize("value", [0, -1, True, 2**63])
def test_upload_concurrency_and_lease_budgets_fail_closed(value: int) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        UploadLimits(
            max_total_bytes=100,
            max_owner_staged_bytes=100,
            max_staged_bytes=100,
            max_active_sessions_per_owner=value,
        )
    with pytest.raises(ValueError, match="lease"):
        UploadLimits(
            max_total_bytes=100,
            max_owner_staged_bytes=100,
            max_staged_bytes=100,
            lease_seconds=value,
        )


@pytest.mark.parametrize("paper_code", [" x", "x ", "x\n", "x\x00"])
def test_upload_metadata_rejects_untrimmed_or_controlled_paper_codes(paper_code: str) -> None:
    with pytest.raises(ValidationError, match="trimmed and printable"):
        SourceUploadCreateRequest.model_validate({**request_body(), "paper_code": paper_code})


def test_lesson_upload_requires_a_unit_even_with_a_curriculum() -> None:
    with pytest.raises(ValidationError, match="lesson_id requires unit_id"):
        SourceUploadCreateRequest.model_validate(
            {**request_body(), "curriculum_version_id": UUID(int=1), "lesson_id": UUID(int=2)}
        )


@pytest.mark.parametrize("offset", [-1, 1, True, 2**63])
def test_receipt_lookup_rejects_invalid_offsets_before_access(tmp_path: Path, offset: int) -> None:
    backend, session, _row, _storage = restored_service(tmp_path)
    with pytest.raises(ResumableUploadError, match="invalid_upload_offset"):
        asyncio.run(backend.list_chunks(UPLOAD_ID, principal=ADMIN, offset=offset))
    assert not session.mock_calls


@pytest.mark.parametrize("limit", [0, True, 65])
def test_receipt_lookup_rejects_unbounded_page_sizes_before_access(
    tmp_path: Path, limit: int
) -> None:
    backend, session, _row, _storage = restored_service(tmp_path)
    with pytest.raises(ResumableUploadError, match="invalid_upload_receipt_limit"):
        asyncio.run(backend.list_chunks(UPLOAD_ID, principal=ADMIN, limit=limit))
    assert not session.mock_calls


def test_invalid_request_identity_and_receipt_database_failures_leave_no_open_transaction(
    tmp_path: Path,
) -> None:
    backend, session, row, _storage = restored_service(tmp_path)
    with pytest.raises(ResumableUploadError, match="invalid_upload_request_id"):
        asyncio.run(backend.get_by_request(cast(UUID, "bad"), principal=ADMIN))
    assert not session.mock_calls
    session.scalars.side_effect = ConnectionError("PRIVATE database diagnostic")
    with pytest.raises(ConnectionError):
        asyncio.run(backend.list_chunks(UPLOAD_ID, principal=ADMIN))
    session.rollback.assert_awaited_once()
    session.commit.assert_not_called()
    session.add.assert_not_called()
    assert row.next_offset == 0


@pytest.mark.parametrize("failure", ["data", "oversize", "checksum"])
def test_malformed_chunks_fail_before_staging_or_database_access(
    tmp_path: Path, failure: str
) -> None:
    backend, session, _row, storage = restored_service(tmp_path)
    data = cast(bytes, bytearray(PDF)) if failure == "data" else PDF
    if failure == "oversize":
        data = b"x" * (UPLOAD_CHUNK_BYTES + 1)
    code = {
        "data": "invalid_upload_chunk",
        "oversize": "source_upload_chunk_too_large",
        "checksum": "invalid_upload_checksum",
    }[failure]
    with pytest.raises(ResumableUploadError, match=code):
        asyncio.run(
            backend.append_chunk(
                UPLOAD_ID,
                principal=ADMIN,
                offset=0,
                data=data,
                checksum_sha256="bad" if failure == "checksum" else None,
            )
        )
    assert not session.mock_calls
    assert not storage.mock_calls
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("receipt", [None, "size", "checksum"])
def test_replayed_chunks_require_matching_immutable_receipts(
    tmp_path: Path, receipt: str | None
) -> None:
    backend, session, row, _storage = restored_service(tmp_path)
    row.next_offset = len(PDF)
    row.version = 1
    session.get.return_value = (
        None
        if receipt is None
        else SourceUploadChunkModel(
            upload_id=UPLOAD_ID,
            offset=0,
            size_bytes=len(PDF) - int(receipt == "size"),
            checksum_sha256="0" * 64 if receipt == "checksum" else hashlib.sha256(PDF).hexdigest(),
        )
    )
    with pytest.raises(ResumableUploadError, match="source_upload_chunk_conflict") as error:
        asyncio.run(backend.append_chunk(UPLOAD_ID, principal=ADMIN, offset=0, data=PDF))
    assert error.value.next_offset == len(PDF)
    assert row.version == 1
    session.add.assert_not_called()
    session.rollback.assert_awaited_once()
    assert not (tmp_path / "staging").exists()


def test_restored_sealed_state_cannot_accept_a_chunk_even_with_a_bad_offset_snapshot(
    tmp_path: Path,
) -> None:
    backend, session, row, _storage = restored_service(tmp_path, status=UploadStatus.PENDING)
    row.next_offset = 0
    with pytest.raises(ResumableUploadError, match="source_upload_state_conflict"):
        asyncio.run(backend.append_chunk(UPLOAD_ID, principal=ADMIN, offset=0, data=PDF))
    session.rollback.assert_awaited_once()
    session.add.assert_not_called()
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize(
    "code", ["staged_chunk_mismatch", "unsafe_artifact_permissions", "private_artifact_unavailable"]
)
def test_chunk_staging_failures_rollback_and_expose_only_stable_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    backend, session, row, _storage = restored_service(tmp_path)
    monkeypatch.setattr(
        backend._artifacts, "put_chunk", Mock(side_effect=PrivateArtifactError(code))
    )
    with pytest.raises(ResumableUploadError) as raised:
        asyncio.run(backend.append_chunk(UPLOAD_ID, principal=ADMIN, offset=0, data=PDF))
    assert raised.value.code == (
        "source_upload_chunk_conflict"
        if code == "staged_chunk_mismatch"
        else "source_upload_staging_unavailable"
    )
    assert raised.value.status_code == (409 if code == "staged_chunk_mismatch" else 503)
    session.rollback.assert_awaited_once()
    session.add.assert_not_called()
    assert row.next_offset == row.version == 0


@pytest.mark.parametrize("version", [-1, True, 2**63])
def test_completion_rejects_invalid_versions_without_loading_or_sealing_upload(
    tmp_path: Path, version: int
) -> None:
    backend, session, row, _storage = restored_service(tmp_path)
    with pytest.raises(ResumableUploadError, match="invalid_upload_version"):
        asyncio.run(
            backend.request_completion(UPLOAD_ID, principal=ADMIN, expected_version=version)
        )
    assert not session.mock_calls
    assert row.status is UploadStatus.UPLOADING


@pytest.mark.parametrize("limit", [0, True, 1001])
def test_recovery_scan_limit_is_bounded_before_querying(tmp_path: Path, limit: int) -> None:
    backend, session, _row, _storage = restored_service(tmp_path)
    with pytest.raises(ValueError, match="recovery batch"):
        asyncio.run(backend.pending_upload_ids(limit=limit))
    assert not session.mock_calls


@pytest.mark.parametrize("age", [-1, True, 3601])
def test_recovery_scan_age_is_bounded_before_querying(tmp_path: Path, age: int) -> None:
    backend, session, _row, _storage = restored_service(tmp_path)
    with pytest.raises(ValueError, match="minimum age"):
        asyncio.run(backend.pending_upload_ids(outbox_min_age_seconds=age))
    assert not session.mock_calls


def test_finalizer_rejects_incomplete_uploads_before_claiming_or_publishing(tmp_path: Path) -> None:
    backend, session, row, storage = restored_service(tmp_path)
    with pytest.raises(ResumableUploadError, match="source_upload_incomplete"):
        asyncio.run(backend.finalize(UPLOAD_ID))
    assert row.status is UploadStatus.UPLOADING
    assert row.lease_token is None
    session.rollback.assert_awaited_once()
    assert not storage.mock_calls


@pytest.mark.parametrize("receipt", ["missing", "gap", "size", "bad_pdf"])
def test_finalizer_independently_validates_restored_receipts_and_pdf_header(
    tmp_path: Path, receipt: str
) -> None:
    backend, session, row, storage = restored_service(tmp_path, status=UploadStatus.PENDING)
    data = b"image" + PDF[5:] if receipt == "bad_pdf" else PDF
    checksum = hashlib.sha256(data).hexdigest()
    if receipt != "missing":
        session.scalars.return_value = (
            SourceUploadChunkModel(
                upload_id=UPLOAD_ID,
                offset=UPLOAD_CHUNK_BYTES if receipt == "gap" else 0,
                size_bytes=len(data) - int(receipt == "size"),
                checksum_sha256=checksum,
            ),
        )
    if receipt == "bad_pdf":
        backend._artifacts.put_chunk(UPLOAD_ID, 0, data, checksum_sha256=checksum)
    result = asyncio.run(backend.finalize(UPLOAD_ID))
    assert result.status is UploadStatus.FAILED
    assert (
        result.failure_code
        == {
            "missing": "source_upload_receipt_missing",
            "gap": "source_upload_receipt_mismatch",
            "size": "source_upload_receipt_mismatch",
            "bad_pdf": "invalid_pdf_signature",
        }[receipt]
    )
    assert result.document_id is None
    assert row.lease_token is None
    storage.put_stream_immutable.assert_not_called()
    assert [call.args[0].action for call in session.add.call_args_list] == ["source_upload.failed"]


@pytest.mark.parametrize("fault", ["key", "checksum", "size", "storage", "conflict", "unexpected"])
def test_finalization_storage_failures_preserve_staging_and_never_create_documents(
    tmp_path: Path, fault: str
) -> None:
    backend, session, row, storage = restored_service(tmp_path, status=UploadStatus.PENDING)
    checksum = hashlib.sha256(PDF).hexdigest()
    backend._artifacts.put_chunk(UPLOAD_ID, 0, PDF, checksum_sha256=checksum)
    session.scalars.return_value = (
        SourceUploadChunkModel(
            upload_id=UPLOAD_ID, offset=0, size_bytes=len(PDF), checksum_sha256=checksum
        ),
    )
    if fault in {"storage", "conflict", "unexpected"}:
        storage.put_stream_immutable.side_effect = {
            "storage": ObjectStorageOperationError("PRIVATE provider diagnostic"),
            "conflict": ObjectAlreadyExistsError(),
            "unexpected": RuntimeError("PRIVATE provider diagnostic"),
        }[fault]
    else:
        storage.put_stream_immutable.return_value = StoredObject(
            key="wrong-key" if fault == "key" else backend._object_key(checksum),
            checksum_sha256="0" * 64 if fault == "checksum" else checksum,
            size=len(PDF) + int(fault == "size"),
            etag=checksum,
        )
    result = asyncio.run(backend.finalize(UPLOAD_ID))
    assert result.status is (UploadStatus.FAILED if fault == "conflict" else UploadStatus.PENDING)
    assert (
        result.failure_code
        == {
            "key": "source_upload_storage_mismatch",
            "checksum": "source_upload_storage_mismatch",
            "size": "source_upload_storage_mismatch",
            "storage": "source_upload_storage_unavailable",
            "conflict": "source_upload_storage_conflict",
            "unexpected": "source_upload_finalize_retryable",
        }[fault]
    )
    assert result.document_id is None
    assert row.lease_token is None
    assert next((tmp_path / "staging").rglob("*.chunk")).read_bytes() == PDF
    assert "PRIVATE" not in result.model_dump_json()
    assert all(
        call.args[0].action.startswith("source_upload.") for call in session.add.call_args_list
    )


@pytest.mark.parametrize("existing", [False, True])
def test_finalizer_integrity_errors_are_bounded_and_do_not_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    backend, session, _row, storage = restored_service(tmp_path, status=UploadStatus.PENDING)
    checksum = hashlib.sha256(PDF).hexdigest()
    monkeypatch.setattr(backend, "_hash_upload", AsyncMock(return_value=checksum))
    storage.put_stream_immutable.return_value = StoredObject(
        key=backend._object_key(checksum), checksum_sha256=checksum, size=len(PDF), etag=checksum
    )
    finish = AsyncMock(side_effect=IntegrityError("PRIVATE failed transaction", {}, RuntimeError()))
    monkeypatch.setattr(backend, "_finish", finish)
    find = AsyncMock(return_value=object() if existing else None)
    monkeypatch.setattr(backend._documents, "_find_by_checksum", find)
    result = asyncio.run(backend.finalize(UPLOAD_ID))
    assert result.status is UploadStatus.PENDING
    assert result.failure_code == "source_upload_finalize_retryable"
    assert finish.await_count == (2 if existing else 1)
    find.assert_awaited_once_with(checksum)
    assert "PRIVATE" not in result.model_dump_json()
    assert all(
        call.args[0].action == "source_upload.retry_pending" for call in session.add.call_args_list
    )


def test_exhausted_retry_schedule_never_reports_completion_or_discards_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from exam_guru_api.documents import resumable_uploads

    backend, session, _row, storage = restored_service(tmp_path, status=UploadStatus.PENDING)
    checksum = hashlib.sha256(PDF).hexdigest()
    backend._artifacts.put_chunk(UPLOAD_ID, 0, PDF, checksum_sha256=checksum)
    session.scalars.return_value = (
        SourceUploadChunkModel(
            upload_id=UPLOAD_ID, offset=0, size_bytes=len(PDF), checksum_sha256=checksum
        ),
    )
    storage.put_stream_immutable.return_value = StoredObject(
        key=backend._object_key(checksum), checksum_sha256=checksum, size=len(PDF), etag=checksum
    )
    finish = AsyncMock(
        side_effect=IntegrityError("fixture duplicate transaction", {}, RuntimeError())
    )
    monkeypatch.setattr(backend, "_finish", finish)
    monkeypatch.setattr(backend._documents, "_find_by_checksum", AsyncMock(return_value=object()))

    def exhausted_after_one_attempt(requested: int) -> range:
        assert requested == 2
        return range(1)

    monkeypatch.setattr(resumable_uploads, "range", exhausted_after_one_attempt, raising=False)
    result = asyncio.run(backend.finalize(UPLOAD_ID))
    finish.assert_awaited_once()
    assert result.status is UploadStatus.PENDING
    assert result.failure_code == "source_upload_finalize_retryable"
    assert result.document_id is None
    assert next((tmp_path / "staging").rglob("*.chunk")).read_bytes() == PDF
    assert [call.args[0].action for call in session.add.call_args_list] == [
        "source_upload.retry_pending"
    ]


@pytest.mark.parametrize("problem", ["size_bytes", "object_key", "content_type"])
def test_deduplication_requires_original_storage_identity_to_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    from exam_guru_api.documents.models import SourceDocumentModel

    backend, _session, _row, storage = restored_service(tmp_path, status=UploadStatus.PENDING)
    checksum = hashlib.sha256(PDF).hexdigest()
    monkeypatch.setattr(backend, "_hash_upload", AsyncMock(return_value=checksum))
    storage.put_stream_immutable.return_value = StoredObject(
        key=backend._object_key(checksum), checksum_sha256=checksum, size=len(PDF), etag=checksum
    )
    document = SourceDocumentModel(
        id=UUID(int=38099),
        size_bytes=len(PDF),
        object_key=backend._object_key(checksum),
        content_type="application/pdf",
    )
    setattr(document, problem, len(PDF) + 1 if problem == "size_bytes" else "invalid")
    monkeypatch.setattr(backend._documents, "_find_by_checksum", AsyncMock(return_value=document))
    result = asyncio.run(backend.finalize(UPLOAD_ID))
    assert result.status is UploadStatus.FAILED
    assert result.failure_code == "source_document_integrity_conflict"
    assert result.document_id is None


@pytest.mark.parametrize(
    ("error", "code", "status", "confirmation"),
    [
        (
            SourceCurriculumNotFoundError("PRIVATE scope detail"),
            "curriculum_version_not_found",
            404,
            True,
        ),
        (
            SourceCurriculumInactiveError("PRIVATE scope detail"),
            "curriculum_version_inactive",
            409,
            True,
        ),
        (
            SourceLearningScopeNotFoundError("PRIVATE scope detail"),
            "learning_scope_not_found",
            404,
            False,
        ),
        (
            SourceLearningScopeInactiveError("PRIVATE scope detail"),
            "learning_scope_inactive",
            409,
            False,
        ),
        (
            SourceLearningScopeMismatchError("PRIVATE scope detail"),
            "learning_scope_mismatch",
            422,
            False,
        ),
    ],
)
def test_upload_scope_errors_are_sanitized_before_quota_or_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: str,
    status: int,
    confirmation: bool,
) -> None:
    backend, session, _row, _storage = restored_service(tmp_path)
    learning = AsyncMock(side_effect=None if confirmation else error)
    curriculum = AsyncMock(side_effect=error if confirmation else None)
    monkeypatch.setattr(backend._documents, "_validate_learning_scope", learning)
    monkeypatch.setattr(backend._documents, "_validate_confirmation_scope", curriculum)
    request = SourceUploadCreateRequest.model_validate(
        {
            **request_body(),
            "curriculum_version_id": UUID(int=1),
            "unit_id": UUID(int=2),
            "lesson_id": UUID(int=3),
        }
    )
    with pytest.raises(ResumableUploadError) as raised:
        asyncio.run(backend.create(request, principal=ADMIN))
    assert raised.value.code == code
    assert raised.value.status_code == status
    assert "PRIVATE" not in str(raised.value)
    session.rollback.assert_awaited_once()
    session.execute.assert_not_called()
    session.add.assert_not_called()
    learning.assert_awaited_once_with(UUID(int=1), UUID(int=2), UUID(int=3))
    assert curriculum.await_count == int(confirmation)


def test_upload_service_dependency_fails_closed_without_both_private_artifacts_and_limits(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    request = Request({"type": "http", "app": app})
    session = AsyncMock(spec=AsyncSession)
    storage = Mock(spec=ObjectStorage)
    for limits, artifacts in (
        (None, None),
        (UploadLimits(max_total_bytes=100, max_owner_staged_bytes=100, max_staged_bytes=100), None),
        (None, PrivateUploadArtifacts(root=tmp_path / "staging")),
    ):
        app.state.source_upload_limits = limits
        app.state.source_upload_artifacts = artifacts
        with pytest.raises(HTTPException) as raised:
            routes.get_resumable_upload_service(request, session, storage)
        assert raised.value.status_code == 503
        assert cast(object, raised.value.detail) == {"code": "source_upload_unavailable"}
    assert not session.mock_calls
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("method", ["get", "complete"])
def test_get_and_complete_errors_return_stable_conflicts_with_resume_offset(method: str) -> None:
    app, service = application()
    error = ResumableUploadError("source_upload_offset_conflict", next_offset=UPLOAD_CHUNK_BYTES)
    with TestClient(app) as client:
        if method == "get":
            service.get.side_effect = error
            response = client.get(f"{PREFIX}/{UPLOAD_ID}", headers=AUTH)
        else:
            service.request_completion.side_effect = error
            response = client.post(f"{PREFIX}/{UPLOAD_ID}/complete", headers=AUTH)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": error.code, "next_offset": UPLOAD_CHUNK_BYTES}}
    service.finalize.assert_not_called()
    assert not app.state.source_upload_dispatcher.dispatched


def test_disconnected_chunk_stream_is_rejected_without_acknowledgement() -> None:
    messages: list[Message] = [
        {"type": "http.request", "body": b"%PDF-", "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive() -> Message:
        return messages.pop(0)

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"application/octet-stream")]},
        receive=receive,
    )
    with pytest.raises(ResumableUploadError, match="source_upload_chunk_truncated") as raised:
        asyncio.run(routes._read_chunk(request))
    assert raised.value.status_code == 422
    assert not messages


def test_upload_mutations_fail_closed_when_existing_rate_limiter_is_unavailable() -> None:
    app, service = application()
    app.state.rate_limiter = UnavailableRateLimiter()
    with TestClient(app) as client:
        response = client.post(PREFIX, json=request_body(), headers=AUTH)
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "rate_limiter_unavailable"}}
    assert not service.mock_calls
