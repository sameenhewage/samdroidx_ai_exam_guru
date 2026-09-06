import asyncio
import hashlib
import tracemalloc
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Never
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.fidelity_models import SourceReadJobModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.resumable_uploads import (
    ResumableUploadError,
    ResumableUploadService,
    UploadLimits,
)
from exam_guru_api.documents.upload_models import SourceUploadChunkModel, SourceUploadSessionModel
from exam_guru_api.documents.upload_schemas import (
    UPLOAD_CHUNK_BYTES,
    SourceUploadCreateRequest,
    UploadStatus,
)
from exam_guru_api.infrastructure.migrations import _config_for_database, upgrade_database
from exam_guru_api.infrastructure.object_storage import LocalFileObjectStorage, StoredObject
from exam_guru_api.infrastructure.private_artifacts import (
    PrivateArtifactError,
    PrivateUploadArtifacts,
)

pytestmark = pytest.mark.integration
PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
PDF = b"%PDF-1.7\nfixture\n%%EOF"
LIMITS = UploadLimits(
    max_total_bytes=2**40,
    max_owner_staged_bytes=2**40,
    max_staged_bytes=2**40,
    max_active_sessions_per_owner=100,
    lease_seconds=1,
)


@pytest.fixture(scope="module")
def upload_database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password=uuid4().hex,
        dbname="exam_guru_resumable_upload_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        yield database_url


def actor() -> Principal:
    return Principal(uuid4(), frozenset({AdminRole.ADMIN}))


def upload_request(data: bytes = PDF, **values: object) -> SourceUploadCreateRequest:
    return SourceUploadCreateRequest.model_validate(
        {
            "filename": "resumable.pdf",
            "size_bytes": len(data),
            "document_type": "teacher_guide",
            "intake_metadata": {"candidate_grade": 11, "subject_label": "Mathematics"},
            **values,
        }
    )


class CountingStorage(LocalFileObjectStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root=root, max_object_bytes=2**40)
        self.publishes = 0

    def put_stream_immutable(
        self, key: str, stream: BinaryIO, *, content_type: str, expected_size: int
    ) -> StoredObject:
        self.publishes += 1
        return super().put_stream_immutable(
            key, stream, content_type=content_type, expected_size=expected_size
        )


def service(
    session: AsyncSession,
    storage: LocalFileObjectStorage,
    artifacts: PrivateUploadArtifacts,
    limits: UploadLimits = LIMITS,
) -> ResumableUploadService:
    return ResumableUploadService(session, storage, artifacts, limits=limits)


def test_restart_resume_receipts_finalize_domain_audit_and_checksum_dedup(
    upload_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = actor()
        first = b"%PDF-1.7\n" + b"x" * (UPLOAD_CHUNK_BYTES - 9)
        tail = b"\n%%EOF"
        checksum = hashlib.sha256(first + tail).hexdigest()
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(
                    upload_request(
                        size_bytes=len(first) + len(tail), expected_checksum_sha256=checksum
                    ),
                    principal=owner,
                )
                partial = await backend.append_chunk(
                    created.id, principal=owner, offset=0, data=first
                )
                assert partial.next_offset == UPLOAD_CHUNK_BYTES
                assert partial.version == 1
            async with sessions() as session:
                restarted = service(
                    session, storage, PrivateUploadArtifacts(root=tmp_path / "staging")
                )
                restored = await restarted.get(created.id, principal=owner)
                assert restored.next_offset == UPLOAD_CHUNK_BYTES
                assert restored.expected_checksum_sha256 == checksum
                receipts = await restarted.list_chunks(created.id, principal=owner, limit=1)
                assert receipts.next_offset == UPLOAD_CHUNK_BYTES
                assert receipts.next_receipt_offset is None
                assert len(receipts.receipts) == 1
                assert receipts.receipts[0].offset == 0
                assert receipts.receipts[0].checksum_sha256 == hashlib.sha256(first).hexdigest()
                repeated = await restarted.append_chunk(
                    created.id, principal=owner, offset=0, data=first
                )
                assert repeated.version == restored.version
                done = await restarted.append_chunk(
                    created.id,
                    principal=owner,
                    offset=UPLOAD_CHUNK_BYTES,
                    data=tail,
                    checksum_sha256=hashlib.sha256(tail).hexdigest(),
                )
                with pytest.raises(ResumableUploadError, match="source_upload_version_conflict"):
                    await restarted.request_completion(
                        created.id, principal=owner, expected_version=0
                    )
                queued = await restarted.request_completion(
                    created.id, principal=owner, expected_version=done.version
                )
                assert queued.status is UploadStatus.PENDING
                assert storage.publishes == 0
            async with sessions() as session:
                worker = service(session, storage, artifacts)
                assert created.id in await worker.pending_upload_ids(limit=100)
                completed = await worker.finalize(created.id)
                assert completed.status is UploadStatus.COMPLETED
                assert completed.checksum_sha256 == checksum
                assert completed.document_id is not None
                assert completed.deduplicated is False
                assert (await worker.finalize(created.id)).document_id == completed.document_id
                document = await session.get(SourceDocumentModel, completed.document_id)
                assert document is not None
                assert document.metadata_review_required is True
                assert document.intake_metadata is not None
                assert document.intake_metadata["candidate_grade"] == 11
                assert document.extraction_status is ExtractionStatus.UPLOADED
                assert document.extraction_attempt_count == 0
                assert document.curriculum_version_id is None
                assert document.created_by == owner.subject_id
                reading = await session.scalar(
                    select(SourceReadJobModel).where(SourceReadJobModel.document_id == document.id)
                )
                assert reading is not None
                assert reading.page_number is None
                assert reading.next_page == 1
                assert reading.status == "queued"
                assert reading.requested_by == owner.subject_id
                assert completed.source_read_job_id == reading.id
                assert (
                    await worker.get(created.id, principal=owner)
                ).source_read_job_id == reading.id
                audit = await session.scalar(
                    select(AdminAuditEventModel).where(
                        AdminAuditEventModel.resource_id == document.id,
                        AdminAuditEventModel.action == "source_document.uploaded",
                    )
                )
                assert audit is not None
                assert audit.payload["intake_metadata"] == document.intake_metadata
                assert audit.payload["metadata_review_required"] is True
                assert audit.payload["checksum_sha256"] == checksum
                receipt_count = await session.scalar(
                    select(func.count())
                    .select_from(SourceUploadChunkModel)
                    .where(SourceUploadChunkModel.upload_id == created.id)
                )
                assert receipt_count == 2
                second = await worker.create(
                    upload_request(first + tail, filename="renamed.pdf"), principal=owner
                )
                await worker.append_chunk(second.id, principal=owner, offset=0, data=first)
                await worker.append_chunk(second.id, principal=owner, offset=len(first), data=tail)
                await worker.request_completion(second.id, principal=owner)
                duplicate = await worker.finalize(second.id)
                assert duplicate.document_id == document.id
                assert duplicate.deduplicated is True
                count = await session.scalar(
                    select(func.count())
                    .select_from(SourceDocumentModel)
                    .where(SourceDocumentModel.checksum_sha256 == checksum)
                )
                assert count == 1
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceReadJobModel)
                        .where(SourceReadJobModel.document_id == document.id)
                    )
                    == 1
                )
                for action, resource_id in (
                    ("source_upload.completed", created.id),
                    ("source_document.uploaded", document.id),
                    ("source_read.queued", reading.id),
                ):
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(AdminAuditEventModel)
                            .where(
                                AdminAuditEventModel.action == action,
                                AdminAuditEventModel.resource_id == resource_id,
                            )
                        )
                        == 1
                    )
            assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_owners_offsets_checksum_and_truncation_are_fail_closed(
    upload_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner, outsider = actor(), actor()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(), principal=owner)
                for operation in (
                    backend.get(created.id, principal=outsider),
                    backend.append_chunk(created.id, principal=outsider, offset=0, data=PDF),
                    backend.request_completion(created.id, principal=outsider),
                ):
                    with pytest.raises(ResumableUploadError, match="source_upload_not_found"):
                        await operation
                for offset, data, checksum, code in (
                    (1, PDF, None, "source_upload_offset_conflict"),
                    (2**63, PDF, None, "invalid_upload_offset"),
                    (0, PDF[:-1], None, "source_upload_chunk_size_mismatch"),
                    (0, PDF + b"x", None, "source_upload_chunk_size_mismatch"),
                    (0, PDF, "0" * 64, "source_upload_chunk_checksum_mismatch"),
                    (0, b"x" * len(PDF), None, "invalid_pdf_signature"),
                ):
                    with pytest.raises(ResumableUploadError, match=code):
                        await backend.append_chunk(
                            created.id,
                            principal=owner,
                            offset=offset,
                            data=data,
                            checksum_sha256=checksum,
                        )
                with pytest.raises(ResumableUploadError, match="source_upload_incomplete"):
                    await backend.request_completion(created.id, principal=owner)
                assert (await backend.get(created.id, principal=owner)).next_offset == 0
                assert not await asyncio.to_thread(lambda: list(tmp_path.rglob("*.chunk")))
                await backend.append_chunk(created.id, principal=owner, offset=0, data=PDF)
                with pytest.raises(ResumableUploadError, match="source_upload_chunk_conflict"):
                    await backend.append_chunk(
                        created.id, principal=owner, offset=0, data=PDF[:-1] + b"x"
                    )
                assert (await backend.get(created.id, principal=owner)).next_offset == len(PDF)
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("same_bytes", [True, False])
def test_chunk_race_serializes_offset_and_keeps_one_immutable_receipt(
    upload_database_url: str, tmp_path: Path, same_bytes: bool
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        try:
            async with sessions() as session:
                created = await service(session, storage, artifacts).create(
                    upload_request(), principal=owner
                )

            async def append(data: bytes) -> str:
                async with sessions() as session:
                    try:
                        result = await service(session, storage, artifacts).append_chunk(
                            created.id, principal=owner, offset=0, data=data
                        )
                        assert result.next_offset == len(PDF)
                    except ResumableUploadError as error:
                        return error.code
                    return "accepted"

            other = PDF if same_bytes else PDF[:-1] + b"x"
            outcomes = await asyncio.gather(append(PDF), append(other))
            assert outcomes.count("accepted") == (2 if same_bytes else 1)
            if not same_bytes:
                assert "source_upload_chunk_conflict" in outcomes
            async with sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(SourceUploadChunkModel)
                    .where(SourceUploadChunkModel.upload_id == created.id)
                )
                assert count == 1
                upload = await session.get(SourceUploadSessionModel, created.id)
                assert upload is not None
                assert upload.version == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["expected_checksum", "staging"])
def test_finalization_rejects_corruption_without_creating_a_document(
    upload_database_url: str, tmp_path: Path, corruption: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                request = (
                    upload_request(expected_checksum_sha256="0" * 64)
                    if corruption == "expected_checksum"
                    else upload_request()
                )
                created = await backend.create(request, principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=PDF)
                await backend.request_completion(created.id, principal=owner)
                if corruption == "staging":
                    await asyncio.to_thread(
                        lambda: next(tmp_path.rglob("*.chunk")).write_bytes(PDF[:-1])
                    )
                failed = await backend.finalize(created.id)
                assert failed.status is UploadStatus.FAILED
                assert failed.document_id is None
                assert failed.failure_code in {
                    "source_upload_checksum_mismatch",
                    "staged_chunk_mismatch",
                }
                assert storage.publishes == 0
                assert (await backend.finalize(created.id)).status is UploadStatus.FAILED
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("crash_at", ["published", "database_flush"])
def test_finalization_crash_after_immutable_publication_replays_atomically(
    upload_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_at: str
) -> None:
    class ProcessCrash(BaseException):
        pass

    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        original_put = storage.put_stream_immutable
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(data), principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=data)
                await backend.request_completion(created.id, principal=owner)
            async with sessions() as session:
                if crash_at == "published":

                    def crash_after_publish(
                        key: str, stream: BinaryIO, *, content_type: str, expected_size: int
                    ) -> StoredObject:
                        original_put(
                            key, stream, content_type=content_type, expected_size=expected_size
                        )
                        raise ProcessCrash

                    monkeypatch.setattr(storage, "put_stream_immutable", crash_after_publish)
                else:

                    def crash_after_flush(sync_session: object, _context: object) -> None:
                        if any(isinstance(value, SourceDocumentModel) for value in session.new):
                            raise ProcessCrash

                    event.listen(session.sync_session, "after_flush", crash_after_flush)
                with pytest.raises(ProcessCrash):
                    await service(session, storage, artifacts).finalize(created.id)
            assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
            monkeypatch.setattr(storage, "put_stream_immutable", original_put)
            await asyncio.sleep(1.05)
            async with sessions() as session:
                from exam_guru_api.documents.upload_jobs import recover_source_uploads

                worker = service(session, storage, artifacts)
                assert created.id in await worker.pending_upload_ids(limit=100)
                recovery_dispatcher = RecordingDispatcher()
                recovered = await recover_source_uploads(worker, recovery_dispatcher)
                assert recovered.enqueued >= 1
                assert created.id in recovery_dispatcher.dispatched
                completed = await worker.finalize(created.id)
                assert completed.status is UploadStatus.COMPLETED
                assert completed.document_id is not None
                count = await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == completed.document_id,
                        AdminAuditEventModel.action == "source_document.uploaded",
                    )
                )
                assert count == 1
                assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_session_quota_is_transactional_and_retained_staging_stays_accounted(
    upload_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        limits = UploadLimits(
            max_total_bytes=len(PDF), max_owner_staged_bytes=len(PDF), max_staged_bytes=2**40
        )
        try:

            async def create() -> str:
                async with sessions() as session:
                    try:
                        await service(session, storage, artifacts, limits).create(
                            upload_request(), principal=owner
                        )
                    except ResumableUploadError as error:
                        return error.code
                    return "created"

            outcomes = await asyncio.gather(create(), create())
            assert sorted(outcomes) == ["created", "source_upload_quota_exceeded"]
            async with sessions() as session:
                backend = service(session, storage, artifacts, limits)
                with pytest.raises(ResumableUploadError, match="source_upload_too_large"):
                    await backend.create(upload_request(size_bytes=len(PDF) + 1), principal=owner)
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_rejects_receipt_mutation_deletion_and_forged_progress(
    upload_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(), principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=PDF)
            for statement in (
                "UPDATE source_upload_chunks SET checksum_sha256 = repeat('0', 64) "
                "WHERE upload_id = :id",
                "DELETE FROM source_upload_chunks WHERE upload_id = :id",
                "UPDATE source_upload_sessions SET next_offset = 0, version = version + 1 "
                "WHERE id = :id",
                "UPDATE source_upload_sessions SET owner_id = gen_random_uuid(), "
                "version = version + 1 WHERE id = :id",
                "DELETE FROM source_upload_sessions WHERE id = :id",
            ):
                with pytest.raises(DBAPIError):
                    async with engine.begin() as connection:
                        await connection.execute(text(statement), {"id": created.id})
            with pytest.raises(DBAPIError):
                async with engine.begin() as connection:
                    await connection.execute(text("TRUNCATE source_upload_chunks"))
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("same_upload", [False, True])
def test_parallel_finalizers_keep_one_document_read_job_and_atomic_audits(
    upload_database_url: str,
    tmp_path: Path,
    same_upload: bool,
) -> None:
    from exam_guru_api.documents.upload_jobs import run_source_upload_finalization

    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        checksum = hashlib.sha256(data).hexdigest()
        upload_ids: list[UUID] = []
        dispatcher = RecordingDispatcher()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                for _ in range(1 if same_upload else 2):
                    upload = await backend.create(upload_request(data), principal=owner)
                    upload_ids.append(upload.id)
                    await backend.append_chunk(upload.id, principal=owner, offset=0, data=data)
                    await backend.request_completion(upload.id, principal=owner)

            async def run(upload_id: UUID) -> None:
                async with sessions() as session:
                    await run_source_upload_finalization(
                        session,
                        upload_id,
                        storage=storage,
                        artifacts=artifacts,
                        limits=LIMITS,
                        read_dispatcher=dispatcher,
                    )

            await asyncio.gather(run(upload_ids[0]), run(upload_ids[-1]))
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                views = [await backend.get(upload_id, principal=owner) for upload_id in upload_ids]
                assert all(view.status is UploadStatus.COMPLETED for view in views)
                document_id = views[0].document_id
                assert document_id is not None
                assert all(view.document_id == document_id for view in views)
                assert sum(view.deduplicated for view in views) == (0 if same_upload else 1)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceDocumentModel)
                        .where(SourceDocumentModel.checksum_sha256 == checksum)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceReadJobModel)
                        .where(SourceReadJobModel.document_id == document_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.action == "source_document.uploaded",
                            AdminAuditEventModel.resource_id == document_id,
                        )
                    )
                    == 1
                )
                for upload_id in upload_ids:
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(AdminAuditEventModel)
                            .where(
                                AdminAuditEventModel.action == "source_upload.completed",
                                AdminAuditEventModel.resource_id == upload_id,
                            )
                        )
                        == 1
                    )
                assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_crashes_at_read_outbox_boundary_never_leave_completed_upload_without_a_read_job(
    upload_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_commit: bool,
) -> None:
    from exam_guru_api.documents import resumable_uploads

    class ProcessCrash(BaseException):
        pass

    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        checksum = hashlib.sha256(data).hexdigest()
        from exam_guru_api.documents.page_reading_jobs import queue_source_read

        original_queue = queue_source_read

        async def interrupted_queue(
            session: AsyncSession,
            document_id: UUID,
            *,
            actor_id: UUID,
        ) -> SourceReadJobModel:
            if after_commit:
                await original_queue(session, document_id, actor_id=actor_id)
            raise ProcessCrash

        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(data), principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=data)
                await backend.request_completion(created.id, principal=owner)
                monkeypatch.setattr(resumable_uploads, "queue_source_read", interrupted_queue)
                with pytest.raises(ProcessCrash):
                    await backend.finalize(created.id)
            monkeypatch.setattr(resumable_uploads, "queue_source_read", original_queue)
            async with sessions() as session:
                stored = await session.scalar(
                    select(SourceDocumentModel).where(
                        SourceDocumentModel.checksum_sha256 == checksum
                    )
                )
                assert (stored is not None) is after_commit
            if not after_commit:
                await asyncio.sleep(1.05)
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                completed = await backend.finalize(created.id)
                assert completed.status is UploadStatus.COMPLETED
                assert completed.document_id is not None
                read_job = await session.scalar(
                    select(SourceReadJobModel).where(
                        SourceReadJobModel.document_id == completed.document_id
                    )
                )
                assert read_job is not None
                assert read_job.page_number is None
                assert read_job.status == "queued"
                for action, resource_id in (
                    ("source_upload.completed", created.id),
                    ("source_document.uploaded", completed.document_id),
                    ("source_read.queued", read_job.id),
                ):
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(AdminAuditEventModel)
                            .where(
                                AdminAuditEventModel.action == action,
                                AdminAuditEventModel.resource_id == resource_id,
                            )
                        )
                        == 1
                    )
                assert (await backend.finalize(created.id)).version == completed.version
                assert (
                    await backend.request_completion(created.id, principal=owner)
                ).version == completed.version
                assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_expired_finalizer_cannot_renew_its_lease_or_commit_before_recovery(
    upload_database_url: str,
    tmp_path: Path,
) -> None:
    from exam_guru_api.documents.resumable_uploads import _LeaseLostError

    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(data), principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=data)
                await backend.request_completion(created.id, principal=owner)
                claim, view = await backend._claim(created.id)
                assert claim is not None
                assert view.status is UploadStatus.FINALIZING
                await asyncio.sleep(1.05)
                with pytest.raises(_LeaseLostError):
                    await backend._renew(claim, len(data))
                await session.rollback()
                with pytest.raises(_LeaseLostError):
                    await backend._finish(claim, hashlib.sha256(data).hexdigest())
                await session.rollback()
                assert created.id in await backend.pending_upload_ids()
            async with sessions() as session:
                recovered = await service(session, storage, artifacts).finalize(created.id)
                assert recovered.status is UploadStatus.COMPLETED
                assert recovered.document_id is not None
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["hash", "publish"])
def test_execution_budget_leaves_a_recoverable_upload_without_a_partial_document(
    upload_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from exam_guru_api.documents import resumable_uploads

    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        original_put = storage.put_stream_immutable
        clock = [0.0]
        monkeypatch.setattr(resumable_uploads, "time", SimpleNamespace(monotonic=lambda: clock[0]))

        def over_budget(
            key: str, stream: BinaryIO, *, content_type: str, expected_size: int
        ) -> StoredObject:
            clock[0] = 2.0
            return original_put(key, stream, content_type=content_type, expected_size=expected_size)

        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(data), principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=data)
                await backend.request_completion(created.id, principal=owner)
                if phase == "hash":
                    clock[0] = 2.0
                else:
                    monkeypatch.setattr(storage, "put_stream_immutable", over_budget)
                result = await backend.finalize(created.id, execution_deadline=1.0)
                assert result.status is UploadStatus.PENDING
                assert result.document_id is None
                assert result.failure_code == "source_upload_execution_budget_exhausted"
                assert not list((tmp_path / "objects").rglob("*.pdf"))
                assert not list((tmp_path / "objects").rglob(".tmp-*"))
                assert created.id in await backend.pending_upload_ids()
                clock[0] = 0.0
                monkeypatch.setattr(storage, "put_stream_immutable", original_put)
                completed = await backend.finalize(created.id, execution_deadline=1.0)
                assert completed.status is UploadStatus.COMPLETED
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


class RecordingDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.dispatched: list[UUID] = []
        self.fail = fail

    def dispatch(self, identifier: UUID) -> str:
        self.dispatched.append(identifier)
        if self.fail:
            raise ConnectionError("fixture queue unavailable")
        return str(uuid4())


def test_root_api_300_mib_restart_resume_worker_progress_and_read_outbox_are_memory_bounded(
    upload_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.auth.rate_limits import NoOpRateLimiter
    from exam_guru_api.core.config import Settings
    from exam_guru_api.documents.fidelity_models import PageReviewStateModel
    from exam_guru_api.documents.page_reading_jobs import recover_source_reads
    from exam_guru_api.documents.resumable_uploads import _Claim
    from exam_guru_api.documents.upload_jobs import run_source_upload_finalization
    from exam_guru_api.main import create_app

    owner = actor()

    class Identity:
        async def authenticate(self, _token: str) -> Principal:
            return owner

    def forbidden_bytes(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("resumable ingestion cannot use the whole-file byte API")

    monkeypatch.setattr(LocalFileObjectStorage, "get_bytes", forbidden_bytes)
    monkeypatch.setattr(LocalFileObjectStorage, "put_immutable", forbidden_bytes)
    total = 300 * 1024 * 1024
    prefix = b"%PDF-1.7\n" + str(uuid4()).encode()
    block = prefix + b"x" * (UPLOAD_CHUNK_BYTES - len(prefix))
    checksum = hashlib.sha256(block).hexdigest()
    expected = hashlib.sha256()
    for _ in range(total // UPLOAD_CHUNK_BYTES):
        expected.update(block)
    root = tmp_path / "durable"
    settings = Settings(
        environment="test",
        database_url=upload_database_url,
        storage_backend="local",
        storage_root=str(root),
    )
    dispatcher = RecordingDispatcher(fail=True)
    headers = {"Authorization": "Bearer fixture"}
    base = "/api/v1/admin/source-uploads"
    application = create_app(
        settings=settings,
        identity_provider=Identity(),
        rate_limiter=NoOpRateLimiter(),
        source_upload_dispatcher=dispatcher,
    )
    assert not root.exists()
    tracemalloc.start()
    try:
        with TestClient(application) as client:
            created = client.post(
                base,
                json=upload_request(size_bytes=total).model_dump(mode="json"),
                headers=headers,
            )
            assert created.status_code == 201
            upload_id = UUID(created.json()["id"])
            path = f"{base}/{upload_id}"
            for offset in range(0, 65 * UPLOAD_CHUNK_BYTES, UPLOAD_CHUNK_BYTES):
                appended = client.put(
                    f"{path}/chunks?offset={offset}",
                    content=block,
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "X-Chunk-SHA256": checksum,
                    },
                )
                assert appended.status_code == 200
                assert appended.json()["next_offset"] == offset + UPLOAD_CHUNK_BYTES
        restarted = create_app(
            settings=settings,
            identity_provider=Identity(),
            rate_limiter=NoOpRateLimiter(),
            source_upload_dispatcher=dispatcher,
        )
        with TestClient(restarted) as client:
            resumed = client.get(path, headers=headers)
            assert resumed.status_code == 200
            assert resumed.json()["next_offset"] == 65 * UPLOAD_CHUNK_BYTES
            first_page = client.get(f"{path}/chunks", headers=headers).json()
            assert len(first_page["receipts"]) == 64
            assert first_page["next_receipt_offset"] == 64 * UPLOAD_CHUNK_BYTES
            second_page = client.get(
                f"{path}/chunks?offset={first_page['next_receipt_offset']}",
                headers=headers,
            ).json()
            assert len(second_page["receipts"]) == 1
            assert second_page["next_receipt_offset"] is None
            for receipt in first_page["receipts"] + second_page["receipts"]:
                assert receipt["checksum_sha256"] == checksum
                assert receipt["size_bytes"] == UPLOAD_CHUNK_BYTES
            for offset in range(65 * UPLOAD_CHUNK_BYTES, total, UPLOAD_CHUNK_BYTES):
                appended = client.put(
                    f"{path}/chunks?offset={offset}",
                    content=block,
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "X-Chunk-SHA256": checksum,
                    },
                )
                assert appended.status_code == 200
            accepted = client.post(f"{path}/complete", headers=headers)
            assert accepted.status_code == 202
            assert accepted.json()["status"] == "pending"
            repeated = client.post(f"{path}/complete", headers=headers)
            assert repeated.json()["version"] == accepted.json()["version"]
            assert not list((root / "sources").rglob("*.pdf"))
            progress: list[int] = []
            original_renew = ResumableUploadService._renew

            async def observe_progress(
                backend: ResumableUploadService, claim: _Claim, verified: int
            ) -> None:
                await original_renew(backend, claim, verified)
                if verified in {UPLOAD_CHUNK_BYTES, total}:
                    polled = await asyncio.to_thread(client.get, path, headers=headers)
                    assert polled.json()["status"] == "finalizing"
                    progress.append(polled.json()["verified_bytes"])

            monkeypatch.setattr(ResumableUploadService, "_renew", observe_progress)
            read_dispatcher = RecordingDispatcher(fail=True)

            async def finalize_and_recover_read() -> UUID:
                engine = create_async_engine(upload_database_url)
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                try:
                    async with sessions() as session:
                        result = await run_source_upload_finalization(
                            session,
                            upload_id,
                            storage=restarted.state.object_storage,
                            artifacts=restarted.state.source_upload_artifacts,
                            limits=restarted.state.source_upload_limits,
                            read_dispatcher=read_dispatcher,
                        )
                        assert result.status is UploadStatus.COMPLETED
                        assert result.checksum_sha256 == expected.hexdigest()
                        assert result.document_id is not None
                        document = await session.get(SourceDocumentModel, result.document_id)
                        assert document is not None
                        assert document.size_bytes == total
                        assert document.metadata_review_required is True
                        assert document.extraction_status is ExtractionStatus.UPLOADED
                        assert (
                            await session.scalar(
                                select(func.count())
                                .select_from(PageReviewStateModel)
                                .where(PageReviewStateModel.document_id == document.id)
                            )
                            == 0
                        )
                        read_job = await session.scalar(
                            select(SourceReadJobModel).where(
                                SourceReadJobModel.document_id == document.id
                            )
                        )
                        assert read_job is not None
                        assert read_job.page_number is None
                        assert read_job.status == "queued"
                        assert read_dispatcher.dispatched == [read_job.id]
                        recovered = RecordingDispatcher()
                        await recover_source_reads(
                            session, recovered, now=datetime.now(UTC) + timedelta(minutes=1)
                        )
                        assert read_job.id in recovered.dispatched
                        return document.id
                finally:
                    await engine.dispose()

            document_id = asyncio.run(finalize_and_recover_read())
            assert progress == [UPLOAD_CHUNK_BYTES, total]
            completed = client.get(path, headers=headers)
            assert completed.json()["status"] == "completed"
            assert completed.json()["document_id"] == str(document_id)
            assert completed.json()["source_read_job_id"] == str(read_dispatcher.dispatched[0])
            assert client.post(f"{path}/complete", headers=headers).status_code == 200
            assert dispatcher.dispatched == [upload_id, upload_id]
            originals = list((root / "sources").rglob("*.pdf"))
            assert len(originals) == 1
            assert originals[0].stat().st_size == total
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024 * 1024


def test_request_identity_http_race_and_restart_recover_the_same_owner_session(
    upload_database_url: str,
    tmp_path: Path,
) -> None:
    from exam_guru_api.auth.rate_limits import NoOpRateLimiter
    from exam_guru_api.core.config import Settings
    from exam_guru_api.main import create_app

    owner, other = actor(), actor()

    class Identity:
        async def authenticate(self, token: str) -> Principal:
            return owner if token == "owner" else other

    settings = Settings(
        environment="test",
        database_url=upload_database_url,
        storage_backend="local",
        storage_root=str(tmp_path / "private"),
    )
    request_id = uuid4()
    body = upload_request(request_id=request_id).model_dump(mode="json")
    base = "/api/v1/admin/source-uploads"
    headers = {"Authorization": "Bearer owner"}
    with TestClient(
        create_app(settings=settings, identity_provider=Identity(), rate_limiter=NoOpRateLimiter())
    ) as client:

        def create(_index: int) -> dict[str, object]:
            response = client.post(base, json=body, headers=headers)
            assert response.status_code == 201
            value: dict[str, object] = response.json()
            assert response.headers["location"] == f"{base}/{value['id']}"
            return value

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        assert results[0] == results[1]
    with TestClient(
        create_app(settings=settings, identity_provider=Identity(), rate_limiter=NoOpRateLimiter())
    ) as client:
        found = client.get(f"{base}/by-request/{request_id}", headers=headers)
        assert found.status_code == 200
        assert found.json() == results[0]
        assert found.headers["cache-control"] == "no-store"
        assert client.post(base, json=body, headers=headers).json() == results[0]
        hidden = client.get(
            f"{base}/by-request/{request_id}", headers={"Authorization": "Bearer other"}
        )
        assert hidden.status_code == 404
        assert hidden.json() == {"detail": {"code": "source_upload_not_found"}}
        changed = client.post(base, json={**body, "filename": "changed.pdf"}, headers=headers)
        assert changed.status_code == 409
        assert changed.json() == {"detail": {"code": "source_upload_request_conflict"}}


def test_request_identity_replays_current_state_without_quota_or_audit_changes(
    upload_database_url: str,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        request_id = uuid4()
        request = upload_request(data, request_id=request_id)
        limits = UploadLimits(
            max_total_bytes=len(data),
            max_owner_staged_bytes=len(data),
            max_staged_bytes=2**40,
            max_active_sessions_per_owner=1,
        )
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts, limits)
                created = await backend.create(request, principal=owner)
                assert created.request_id == request_id
                canonical = SourceUploadCreateRequest.model_validate(
                    request.model_dump(mode="json")
                )
                assert await backend.create(canonical, principal=owner) == created
                with pytest.raises(ResumableUploadError, match="source_upload_request_conflict"):
                    await backend.create(
                        upload_request(data, request_id=request_id, size_bytes=len(data) + 1),
                        principal=owner,
                    )
                uploaded = await backend.append_chunk(
                    created.id, principal=owner, offset=0, data=data
                )
                assert await backend.create(request, principal=owner) == uploaded
                pending = await backend.request_completion(created.id, principal=owner)
                assert await backend.create(request, principal=owner) == pending
                completed = await backend.finalize(created.id)
            async with sessions() as session:
                restarted = service(
                    session, storage, PrivateUploadArtifacts(root=tmp_path / "staging"), limits
                )
                assert await restarted.create(request, principal=owner) == completed
                assert await restarted.get_by_request(request_id, principal=owner) == completed
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceUploadSessionModel)
                        .where(SourceUploadSessionModel.owner_id == owner.subject_id)
                    )
                    == 1
                )
                assert await session.scalar(
                    select(func.sum(SourceUploadSessionModel.size_bytes)).where(
                        SourceUploadSessionModel.owner_id == owner.subject_id
                    )
                ) == len(data)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.resource_id == created.id,
                            AdminAuditEventModel.action == "source_upload.created",
                        )
                    )
                    == 1
                )
                assert completed.document_id is not None
                document = await session.get(SourceDocumentModel, completed.document_id)
                assert document is not None
                assert document.metadata_review_required
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("same_payload", [True, False])
def test_request_identity_concurrent_creation_has_one_winner(
    upload_database_url: str,
    tmp_path: Path,
    same_payload: bool,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        request_id = uuid4()
        limits = UploadLimits(
            max_total_bytes=1000,
            max_owner_staged_bytes=len(PDF),
            max_staged_bytes=2**40,
            max_active_sessions_per_owner=1,
        )
        try:

            async def create(year: int) -> UUID | str:
                async with sessions() as session:
                    try:
                        response = await service(session, storage, artifacts, limits).create(
                            upload_request(request_id=request_id, year=year),
                            principal=owner,
                        )
                        return response.id
                    except ResumableUploadError as error:
                        return error.code

            results = await asyncio.gather(create(2025), create(2025 if same_payload else 2024))
            winners = [result for result in results if isinstance(result, UUID)]
            assert len(winners) == (2 if same_payload else 1)
            assert len(set(winners)) == 1
            if not same_payload:
                assert "source_upload_request_conflict" in results
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceUploadSessionModel)
                        .where(SourceUploadSessionModel.owner_id == owner.subject_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.resource_id == winners[0],
                            AdminAuditEventModel.action == "source_upload.created",
                        )
                    )
                    == 1
                )
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_request_identity_is_owner_private_and_every_creation_field_is_immutable(
    upload_database_url: str,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner, other = actor(), actor()
        request_id = uuid4()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(
                    upload_request(request_id=request_id), principal=owner
                )
                with pytest.raises(ResumableUploadError, match="source_upload_not_found"):
                    await backend.get_by_request(request_id, principal=other)
                for changes in (
                    {"filename": "changed.pdf"},
                    {"document_type": "syllabus"},
                    {"intake_metadata": {"candidate_grade": 12}},
                    {"expected_checksum_sha256": "0" * 64},
                    {"curriculum_version_id": uuid4()},
                    {"year": 2024},
                    {"paper_code": "other"},
                ):
                    with pytest.raises(
                        ResumableUploadError, match="source_upload_request_conflict"
                    ):
                        await backend.create(
                            upload_request(request_id=request_id, **changes), principal=owner
                        )
                independent = await backend.create(
                    upload_request(request_id=request_id, filename="other.pdf"), principal=other
                )
                assert independent.id != created.id
                assert (await backend.get_by_request(request_id, principal=owner)).id == created.id
                assert (
                    await backend.get_by_request(request_id, principal=other)
                ).id == independent.id
                legacy = await backend.create(upload_request(), principal=owner)
                assert legacy.request_id is None
                assert (await backend.create(upload_request(), principal=owner)).id != legacy.id
            for upload_id, replacement in (
                (created.id, None),
                (created.id, uuid4()),
                (legacy.id, uuid4()),
            ):
                with pytest.raises(DBAPIError):
                    async with engine.begin() as connection:
                        await connection.execute(
                            text(
                                "UPDATE source_upload_sessions SET request_id = :request_id, "
                                "version = version + 1 WHERE id = :id"
                            ),
                            {"id": upload_id, "request_id": replacement},
                        )
            with pytest.raises(IntegrityError) as duplicate:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO source_upload_sessions "
                            "(id, owner_id, request_id, filename, size_bytes, "
                            "document_type, intake_metadata) "
                            "VALUES (:id, :owner, :request, 'duplicate.pdf', "
                            "5, 'syllabus', '{}'::jsonb)"
                        ),
                        {"id": uuid4(), "owner": owner.subject_id, "request": request_id},
                    )
            assert getattr(duplicate.value.orig, "sqlstate", None) == "23505"
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_request_identity_migration_preserves_legacy_rows_and_refuses_history_loss(
    tmp_path: Path,
) -> None:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password=uuid4().hex,
        dbname="exam_guru_upload_identity_migration",
        driver="asyncpg",
    ) as postgres:
        url = postgres.get_connection_url()
        config = _config_for_database(url)
        command.upgrade(config, "0037_studio_fixture_quarantine")
        upload_id, owner_id = uuid4(), uuid4()

        async def legacy_row() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO source_upload_sessions "
                            "(id, owner_id, filename, size_bytes, document_type, intake_metadata) "
                            "VALUES (:id, :owner, 'legacy.pdf', 5, 'syllabus', '{}'::jsonb)"
                        ),
                        {"id": upload_id, "owner": owner_id},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO admin_audit_events "
                            "(id, actor_id, action, resource_type, resource_id, payload) "
                            "VALUES (:audit, :owner, 'source_upload.created', "
                            "'source_upload', :id, "
                            "jsonb_build_object('size_bytes', 5, 'intake_metadata', '{}'::jsonb))"
                        ),
                        {"audit": uuid4(), "owner": owner_id, "id": upload_id},
                    )
            finally:
                await engine.dispose()

        asyncio.run(legacy_row())
        command.upgrade(config, "0038_upload_request_identity")

        async def verify() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as connection:
                    row = (
                        await connection.execute(
                            text(
                                "SELECT request_id, version, status, next_offset, "
                                "created_at = updated_at FROM source_upload_sessions WHERE id = :id"
                            ),
                            {"id": upload_id},
                        )
                    ).one()
                    assert tuple(row) == (None, 0, "uploading", 0, True)
            finally:
                await engine.dispose()

        asyncio.run(verify())
        command.downgrade(config, "0037_studio_fixture_quarantine")
        command.upgrade(config, "0038_upload_request_identity")

        async def keyed_row() -> None:
            engine = create_async_engine(url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            storage = CountingStorage(tmp_path / "objects")
            try:
                async with sessions() as session:
                    await service(
                        session, storage, PrivateUploadArtifacts(root=tmp_path / "staging")
                    ).create(upload_request(request_id=uuid4()), principal=actor())
            finally:
                storage.close()
                await engine.dispose()

        asyncio.run(keyed_row())
        with pytest.raises(DBAPIError):
            command.downgrade(config, "0037_studio_fixture_quarantine")
        asyncio.run(verify())


def test_chunk_commit_failure_keeps_durable_bytes_but_never_acknowledges_uncommitted_progress(
    upload_database_url: str, tmp_path: Path
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(), principal=owner)

                def fail_receipt_commit(_session: object, _context: object) -> None:
                    if any(isinstance(value, SourceUploadChunkModel) for value in session.new):
                        raise RuntimeError(
                            "fixture receipt transaction failed after durable staging"
                        )

                event.listen(session.sync_session, "after_flush", fail_receipt_commit)
                with pytest.raises(RuntimeError, match="receipt transaction failed"):
                    await backend.append_chunk(created.id, principal=owner, offset=0, data=PDF)
                event.remove(session.sync_session, "after_flush", fail_receipt_commit)
                assert (await backend.get(created.id, principal=owner)).next_offset == 0
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceUploadChunkModel)
                        .where(SourceUploadChunkModel.upload_id == created.id)
                    )
                    == 0
                )
                chunk = next((tmp_path / "staging").rglob("*.chunk"))
                before = chunk.stat()
                assert chunk.read_bytes() == PDF
                with pytest.raises(ResumableUploadError, match="source_upload_chunk_conflict"):
                    await backend.append_chunk(
                        created.id, principal=owner, offset=0, data=PDF[:-1] + b"x"
                    )
                accepted = await backend.append_chunk(
                    created.id, principal=owner, offset=0, data=PDF
                )
                assert accepted.next_offset == len(PDF)
                assert accepted.version == 1
                assert chunk.stat().st_ino == before.st_ino
                assert not list((tmp_path / "staging").rglob(".tmp-*"))
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceUploadChunkModel)
                        .where(SourceUploadChunkModel.upload_id == created.id)
                    )
                    == 1
                )
                assert storage.publishes == 0
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("late_failure", [False, True])
def test_stale_finalizer_completion_or_failure_cannot_overwrite_a_recovered_result(
    upload_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late_failure: bool
) -> None:
    from exam_guru_api.documents.resumable_uploads import _Claim

    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts)
                created = await backend.create(upload_request(data), principal=owner)
                await backend.append_chunk(created.id, principal=owner, offset=0, data=data)
                await backend.request_completion(created.id, principal=owner)
            async with sessions() as old_session:
                old_worker = service(old_session, storage, artifacts)
                hash_upload = old_worker._hash_upload
                hashed = asyncio.Event()
                release = asyncio.Event()

                async def stalled_hash(claim: _Claim) -> str:
                    checksum = await hash_upload(claim)
                    hashed.set()
                    await release.wait()
                    if late_failure:
                        raise PrivateArtifactError("private_artifact_unavailable")
                    return checksum

                monkeypatch.setattr(old_worker, "_hash_upload", stalled_hash)
                old_task = asyncio.create_task(old_worker.finalize(created.id))
                try:
                    await asyncio.wait_for(hashed.wait(), timeout=5)
                    await asyncio.sleep(1.05)
                    async with sessions() as new_session:
                        recovered = await service(new_session, storage, artifacts).finalize(
                            created.id
                        )
                        assert recovered.status is UploadStatus.COMPLETED
                finally:
                    release.set()
                stale = await asyncio.wait_for(old_task, timeout=5)
                assert stale == recovered
            async with sessions() as session:
                assert (
                    await service(session, storage, artifacts).get(created.id, principal=owner)
                ) == recovered
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.resource_id == created.id,
                            AdminAuditEventModel.action == "source_upload.completed",
                        )
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.resource_id == created.id,
                            AdminAuditEventModel.action == "source_upload.retry_pending",
                        )
                    )
                    == 0
                )
                assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_finalizer_retries_a_real_unique_constraint_race_after_its_stale_checksum_read(
    upload_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner = actor()
        data = PDF + str(uuid4()).encode()
        limits = replace(LIMITS, lease_seconds=30)
        try:
            async with sessions() as session:
                backend = service(session, storage, artifacts, limits)
                first = await backend.create(upload_request(data), principal=owner)
                second = await backend.create(upload_request(data), principal=owner)
                for upload in (first, second):
                    await backend.append_chunk(upload.id, principal=owner, offset=0, data=data)
                    await backend.request_completion(upload.id, principal=owner)
            async with sessions() as racing_session:
                racing_worker = service(racing_session, storage, artifacts, limits)
                find = racing_worker._documents._find_by_checksum
                looked_up = asyncio.Event()
                release = asyncio.Event()
                calls: list[str] = []

                async def stale_first_lookup(checksum: str) -> SourceDocumentModel | None:
                    document = await find(checksum)
                    calls.append(checksum)
                    if len(calls) == 1:
                        assert document is None
                        looked_up.set()
                        await release.wait()
                    return document

                monkeypatch.setattr(
                    racing_worker._documents, "_find_by_checksum", stale_first_lookup
                )
                racing_task = asyncio.create_task(racing_worker.finalize(second.id))
                try:
                    await asyncio.wait_for(looked_up.wait(), timeout=5)
                    async with sessions() as winner_session:
                        winner = await service(winner_session, storage, artifacts, limits).finalize(
                            first.id
                        )
                        assert winner.status is UploadStatus.COMPLETED
                finally:
                    release.set()
                recovered = await asyncio.wait_for(racing_task, timeout=5)
                assert recovered.status is UploadStatus.COMPLETED
                assert recovered.deduplicated
                assert recovered.document_id == winner.document_id
                assert len(calls) == 3
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceDocumentModel)
                        .where(SourceDocumentModel.id == winner.document_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceReadJobModel)
                        .where(SourceReadJobModel.document_id == winner.document_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.resource_id == winner.document_id,
                            AdminAuditEventModel.action == "source_document.uploaded",
                        )
                    )
                    == 1
                )
                assert len(list((tmp_path / "objects").rglob("*.pdf"))) == 1
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("quota", ["global", "active"])
def test_global_and_active_session_quotas_preserve_existing_uploads(
    upload_database_url: str, tmp_path: Path, quota: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        storage = CountingStorage(tmp_path / "objects")
        artifacts = PrivateUploadArtifacts(root=tmp_path / "staging")
        owner, other = actor(), actor()
        try:
            async with sessions() as session:
                reserved = int(
                    await session.scalar(
                        select(func.coalesce(func.sum(SourceUploadSessionModel.size_bytes), 0))
                    )
                    or 0
                )
                limits = replace(
                    LIMITS,
                    max_staged_bytes=reserved + len(PDF)
                    if quota == "global"
                    else LIMITS.max_staged_bytes,
                    max_active_sessions_per_owner=1,
                )
                backend = service(session, storage, artifacts, limits)
                first = await backend.create(upload_request(), principal=owner)
                with pytest.raises(ResumableUploadError, match="source_upload_quota_exceeded"):
                    await backend.create(
                        upload_request(), principal=other if quota == "global" else owner
                    )
                assert await backend.get(first.id, principal=owner) == first
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceUploadSessionModel)
                        .where(SourceUploadSessionModel.owner_id == owner.subject_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SourceUploadSessionModel)
                        .where(SourceUploadSessionModel.owner_id == other.subject_id)
                    )
                    == 0
                )
                assert not list((tmp_path / "staging").rglob("*.chunk"))
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(scenario())


def test_clean_resumable_migration_upgrade_downgrade_and_no_bulk_blob_columns() -> None:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password=uuid4().hex,
        dbname="exam_guru_resumable_migration_test",
        driver="asyncpg",
    ) as postgres:
        url = postgres.get_connection_url()
        config = _config_for_database(url)
        command.upgrade(config, "0036_resumable_source_uploads")

        async def inspect_schema() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as connection:
                    rows = (
                        await connection.execute(
                            text(
                                "SELECT table_name, column_name, data_type "
                                "FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name IN "
                                "('source_upload_sessions', 'source_upload_chunks', "
                                "'source_documents')"
                            )
                        )
                    ).all()
                    columns = {(row[0], row[1]): row[2] for row in rows}
                    assert columns[("source_upload_sessions", "size_bytes")] == "bigint"
                    assert columns[("source_upload_chunks", "offset")] == "bigint"
                    assert columns[("source_documents", "size_bytes")] == "bigint"
                    assert "bytea" not in columns.values()
            finally:
                await engine.dispose()

        asyncio.run(inspect_schema())
        command.downgrade(config, "0035_verified_knowledge_lineage")
        command.upgrade(config, "0036_resumable_source_uploads")
