import asyncio
from collections.abc import Iterator
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.auth.rate_limits import NoOpRateLimiter
from exam_guru_api.documents.jobs import DeterministicExtractionDispatcher, ExtractionDispatcher
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.infrastructure.object_storage import (
    ObjectPage,
    ObjectTagMutation,
    StoredObject,
)
from exam_guru_api.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
VALID_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"
RECOVERY_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Version /1.7 >>\nendobj\n%%EOF"
WRONG_GRADE_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n%%EOF"
ADMIN_ID = UUID(int=9_000)


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(subject_id=ADMIN_ID, roles=frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(subject_id=UUID(int=9_001), roles=frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class RecordingObjectStorage:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        self.puts.append((key, data, content_type))
        return StoredObject(
            key=key,
            checksum_sha256=key.removesuffix(".pdf").split("/")[-1],
            size=len(data),
            etag="fixture-etag",
        )

    def get_bytes(self, key: str) -> bytes:
        raise AssertionError(key)

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


class FailOnceExtractionDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
        self.calls.append((document_id, actor_id))
        if len(self.calls) == 1:
            raise RuntimeError("private valkey transport diagnostic raw-payload")
        return "recovered-extraction-message"


class DatabaseTestResources:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        await self.engine.dispose()


@pytest.fixture(scope="module")
def upload_database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="upload-" + "only",
        dbname="exam_guru_upload_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        yield database_url


def upload_client(
    database_url: str,
    storage: RecordingObjectStorage,
    dispatcher: ExtractionDispatcher | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            identity_provider=StaticIdentityProvider(),
            object_storage=storage,
            extraction_dispatcher=dispatcher,
            resource_factory=lambda _: DatabaseTestResources(database_url),
            rate_limiter=NoOpRateLimiter(),
        )
    )


@pytest.mark.integration
def test_admin_upload_is_immutable_audited_and_idempotent(upload_database_url: str) -> None:
    storage = RecordingObjectStorage()
    dispatcher = DeterministicExtractionDispatcher()
    headers = {"Authorization": "Bearer admin-token"}

    with upload_client(upload_database_url, storage, dispatcher) as client:
        created = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "syllabus"},
            files={"file": ("grade-5-syllabus.pdf", VALID_PDF, "application/pdf")},
            headers=headers,
        )
        retried = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "syllabus"},
            files={"file": ("renamed-retry.pdf", VALID_PDF, "application/pdf")},
            headers=headers,
        )
        forbidden = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "syllabus"},
            files={"file": ("reviewer.pdf", VALID_PDF, "application/pdf")},
            headers={"Authorization": "Bearer reviewer-token"},
        )
        listed = client.get(
            "/api/v1/admin/source-documents",
            headers={"Authorization": "Bearer reviewer-token"},
        )
        extraction = client.post(
            f"/api/v1/admin/source-documents/{created.json()['id']}/extract",
            headers=headers,
        )
        duplicate_extraction = client.post(
            f"/api/v1/admin/source-documents/{created.json()['id']}/extract",
            headers=headers,
        )
        reviewer_extraction = client.post(
            f"/api/v1/admin/source-documents/{created.json()['id']}/extract",
            headers={"Authorization": "Bearer reviewer-token"},
        )

    assert created.status_code == 201
    assert created.json()["deduplicated"] is False
    assert "object_key" not in created.json()
    assert created.json()["extraction_attempt_count"] == 0
    assert created.json()["extracted_page_count"] is None
    assert retried.status_code == 200
    assert retried.json()["deduplicated"] is True
    assert retried.json()["id"] == created.json()["id"]
    assert forbidden.status_code == 403
    assert listed.status_code == 200
    assert [document["id"] for document in listed.json()] == [created.json()["id"]]
    assert extraction.status_code == 202
    assert extraction.json()["message_id"] == "deterministic-extraction-message-id"
    assert extraction.json()["status"] == "extraction_pending"
    assert duplicate_extraction.status_code == 202
    assert duplicate_extraction.json() == extraction.json()
    assert reviewer_extraction.status_code == 403
    assert dispatcher.dispatched == [(UUID(created.json()["id"]), ADMIN_ID)]
    assert len(storage.puts) == 1

    async def persisted_state() -> tuple[int, list[str]]:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            documents = list(await session.scalars(select(SourceDocumentModel)))
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action).where(
                        AdminAuditEventModel.resource_id == UUID(created.json()["id"])
                    )
                )
            )
        await engine.dispose()
        return len(documents), actions

    document_count, actions = asyncio.run(persisted_state())
    assert document_count == 1
    assert actions == [
        "source_document.uploaded",
        "source_document.extraction_queued",
        "source_document.extraction_dispatched",
    ]


@pytest.mark.integration
def test_wrong_grade_material_remove_restore_is_readable_audited_and_cas_safe(
    upload_database_url: str,
) -> None:
    storage = RecordingObjectStorage()
    headers = {"Authorization": "Bearer admin-token"}
    with upload_client(upload_database_url, storage) as client:
        subject = client.post(
            "/api/v1/admin/subjects",
            json={"code": "WRONG-GRADE-MATH", "name": "Mathematics"},
            headers=headers,
        )
        exam = client.post(
            "/api/v1/admin/exam-configurations",
            json={"code": "WRONG-GRADE-G5", "name": "Grade 5", "grade": 5},
            headers=headers,
        )
        medium = client.post(
            "/api/v1/admin/media",
            json={"code": "wg-en", "name": "English"},
            headers=headers,
        )
        curriculum = client.post(
            "/api/v1/admin/curriculum-versions",
            json={
                "exam_configuration_id": exam.json()["id"],
                "medium_id": medium.json()["id"],
                "subject_id": subject.json()["id"],
                "code": "WRONG-GRADE-V1",
                "title": "Grade 5 Mathematics",
            },
            headers=headers,
        )
        uploaded = client.post(
            "/api/v1/admin/source-documents",
            data={
                "document_type": "past_paper",
                "curriculum_version_id": curriculum.json()["id"],
                "year": "2025",
                "paper_code": "WRONG-GRADE-PAPER",
            },
            files={
                "file": (
                    "grade-11-paper-uploaded-as-grade-5.pdf",
                    WRONG_GRADE_PDF,
                    "application/pdf",
                )
            },
            headers=headers,
        )
        unchanged_scope = client.patch(
            f"/api/v1/admin/materials/{uploaded.json()['id']}/scope",
            json={
                "curriculum_version_id": curriculum.json()["id"],
                "unit_id": None,
                "lesson_id": None,
                "expected_version": 0,
            },
            headers=headers,
        )
        removed = client.post(
            f"/api/v1/admin/materials/{uploaded.json()['id']}/remove-from-use",
            json={"reason": "This is a Grade 11 paper", "expected_version": 0},
            headers=headers,
        )
        stale_restore = client.post(
            f"/api/v1/admin/materials/{uploaded.json()['id']}/restore",
            json={"expected_version": 0},
            headers=headers,
        )
        materials = client.get(
            "/api/v1/admin/materials",
            params={"grade": 5, "subject_id": subject.json()["id"]},
            headers={"Authorization": "Bearer reviewer-token"},
        )
        summary = client.get(
            "/api/v1/admin/materials/grade-summary",
            headers={"Authorization": "Bearer reviewer-token"},
        )
        restored = client.post(
            f"/api/v1/admin/materials/{uploaded.json()['id']}/restore",
            json={"expected_version": 1},
            headers=headers,
        )

    assert uploaded.status_code == 201
    assert uploaded.json()["subject_id"] == subject.json()["id"]
    assert unchanged_scope.status_code == 200
    assert unchanged_scope.json()["metadata_scope_version"] == 0
    assert removed.status_code == 200
    assert removed.json()["use_state"] == "removed"
    assert removed.json()["metadata_scope_version"] == 1
    assert stale_restore.status_code == 409
    assert stale_restore.json()["detail"] == {
        "code": "concurrent_material_scope_modification",
        "expected_version": 0,
        "actual_version": 1,
    }
    assert materials.status_code == 200
    material = next(item for item in materials.json() if item["id"] == uploaded.json()["id"])
    assert material["title"] == "grade-11-paper-uploaded-as-grade-5.pdf"
    assert material["grade"] == 5
    assert material["subject"] == "Mathematics"
    assert material["medium"] == "English"
    assert material["status"] == "removed"
    grade_five = next(item for item in summary.json() if item["grade"] == 5)
    assert grade_five["removed_count"] == 1
    assert restored.status_code == 200
    assert restored.json()["use_state"] == "active"
    assert restored.json()["metadata_scope_version"] == 2

    async def lifecycle_audits() -> list[str]:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action)
                    .where(AdminAuditEventModel.resource_id == UUID(uploaded.json()["id"]))
                    .order_by(AdminAuditEventModel.created_at, AdminAuditEventModel.id)
                )
            )
        await engine.dispose()
        return actions

    assert asyncio.run(lifecycle_audits()) == [
        "source_document.uploaded",
        "source_document.removed_from_ai_use",
        "source_document.restored_to_ai_use",
    ]


@pytest.mark.integration
def test_extraction_queue_failure_is_recoverable_by_same_endpoint_replay(
    upload_database_url: str,
) -> None:
    storage = RecordingObjectStorage()
    dispatcher = FailOnceExtractionDispatcher()
    headers = {"Authorization": "Bearer admin-token"}

    with upload_client(upload_database_url, storage, dispatcher) as client:
        created = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "teacher_guide"},
            files={"file": ("recovery.pdf", RECOVERY_PDF, "application/pdf")},
            headers=headers,
        )
        document_id = UUID(created.json()["id"])
        failed = client.post(
            f"/api/v1/admin/source-documents/{document_id}/extract",
            headers=headers,
        )
        recovered = client.post(
            f"/api/v1/admin/source-documents/{document_id}/extract",
            headers=headers,
        )
        replayed = client.post(
            f"/api/v1/admin/source-documents/{document_id}/extract",
            headers=headers,
        )

    assert created.status_code == 201
    assert failed.status_code == 503
    assert failed.json() == {"detail": {"code": "extraction_queue_unavailable"}}
    assert "raw-payload" not in failed.text
    assert recovered.status_code == 202
    assert recovered.json()["message_id"] == "recovered-extraction-message"
    assert replayed.status_code == 202
    assert replayed.json() == recovered.json()
    assert dispatcher.calls == [(document_id, ADMIN_ID), (document_id, ADMIN_ID)]

    async def persisted_state() -> tuple[SourceDocumentModel | None, list[AdminAuditEventModel]]:
        engine = create_async_engine(upload_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            document = await session.get(SourceDocumentModel, document_id)
            audits = list(
                await session.scalars(
                    select(AdminAuditEventModel)
                    .where(AdminAuditEventModel.resource_id == document_id)
                    .order_by(AdminAuditEventModel.created_at)
                )
            )
        await engine.dispose()
        return document, audits

    document, audits = asyncio.run(persisted_state())
    assert document is not None
    assert document.extraction_status.value == "extraction_pending"
    assert document.extraction_attempt_count == 1
    assert document.extraction_queue_message_id == "recovered-extraction-message"
    assert [audit.action for audit in audits] == [
        "source_document.uploaded",
        "source_document.extraction_queued",
        "source_document.extraction_dispatch_failed",
        "source_document.extraction_dispatched",
    ]
    assert audits[2].payload == {
        "attempt": 1,
        "failure_code": "queue_dispatch_failed",
    }
    assert "raw-payload" not in repr(audits[2].payload)


@pytest.mark.integration
def test_upload_rejects_spoofed_pdf(upload_database_url: str) -> None:
    with upload_client(upload_database_url, RecordingObjectStorage()) as client:
        response = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "past_paper"},
            files={"file": ("spoofed.pdf", b"not a pdf", "application/pdf")},
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_pdf_signature"
