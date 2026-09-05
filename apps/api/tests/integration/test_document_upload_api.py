import asyncio
import json
from collections.abc import Iterator
from datetime import datetime
from uuid import UUID, uuid4

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
        filtered_materials = client.get(
            "/api/v1/admin/materials",
            params={
                "grade": 5,
                "subject_id": subject.json()["id"],
                "medium_id": medium.json()["id"],
                "material_type": "past_paper",
                "year": 2025,
                "status": "removed",
                "search": "grade-11-paper",
            },
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
    assert filtered_materials.status_code == 200
    assert [item["id"] for item in filtered_materials.json()] == [uploaded.json()["id"]]
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


@pytest.mark.integration
@pytest.mark.parametrize(
    "metadata",
    ["", "{", "null", "[]", '{"trusted":true}', '{"candidate_grade":true}', ["{}", "{}"]],
)
def test_upload_rejects_invalid_intake_metadata(
    upload_database_url: str,
    metadata: str | list[str],
) -> None:
    storage = RecordingObjectStorage()
    with upload_client(upload_database_url, storage) as client:
        response = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "other_approved", "intake_metadata": metadata},
            files={"file": ("invalid-intake.pdf", VALID_PDF, "application/pdf")},
            headers={"Authorization": "Bearer admin-token"},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_intake_metadata"
    assert storage.puts == []


@pytest.mark.integration
def test_unassigned_intake_display_confirmation_and_database_guards(
    upload_database_url: str,
) -> None:
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    headers = {"Authorization": "Bearer admin-token"}
    storage = RecordingObjectStorage()
    metadata = {
        "candidate_grade": 7,
        "subject_label": "Unverified mathematics",
        "medium_label": "Unverified English",
        "curriculum_label": "Folder claim only",
        "document_type_label": "Worksheet",
        "year": 2024,
        "term": "Term 2",
        "publisher": "Unknown publisher",
        "source_reference": "local-folder/source.pdf",
        "evidence": ["Folder named Grade 7"],
        "warnings": ["Curriculum not established"],
    }
    with upload_client(upload_database_url, storage) as client:
        created = client.post(
            "/api/v1/admin/source-documents",
            data={"document_type": "other_approved", "intake_metadata": json.dumps(metadata)},
            files={"file": ("unassigned-intake.pdf", VALID_PDF + b"\nintake", "application/pdf")},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        assert created.json()["intake_metadata"] == metadata
        assert created.json()["metadata_review_required"] is True
        assert created.json()["curriculum_version_id"] is None
        listed = client.get(
            "/api/v1/admin/materials",
            params={"grade": 7, "year": 2024, "unassigned_only": True, "document_id": document_id},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        material = listed.json()[0]
        assert material["grade"] == 7
        assert material["subject"] == metadata["subject_label"]
        assert material["subject_id"] is None
        assert material["medium"] == metadata["medium_label"]
        assert material["curriculum"] == metadata["curriculum_label"]
        assert material["metadata_review_required"] is True
        assert material["intake_metadata"] == metadata
        assert material["status"] == "needs_review"
        assert material["year"] == 2024
        summary = client.get("/api/v1/admin/materials/grade-summary", headers=headers).json()
        grade = next(row for row in summary if row["grade"] == 7)
        assert grade["material_count"] == 1
        assert grade["subject_count"] == 1
        assert grade["needs_review_count"] == 1
        assert grade["ready_count"] == 0
        exact = client.get(
            "/api/v1/admin/source-documents",
            params={"document_id": document_id},
            headers=headers,
        )
        assert [row["id"] for row in exact.json()] == [document_id]
        unknown = client.post(
            "/api/v1/admin/source-documents",
            data={
                "document_type": "other_approved",
                "intake_metadata": json.dumps(
                    {
                        "candidate_grade": None,
                        "warnings": ["Ambiguous grades 5 and 6"],
                    }
                ),
            },
            files={"file": ("ambiguous-intake.pdf", VALID_PDF + b"\nunknown", "application/pdf")},
            headers=headers,
        )
        assert unknown.status_code == 201
        unknown_item = client.get(
            "/api/v1/admin/materials",
            params={"document_id": unknown.json()["id"]},
            headers=headers,
        ).json()[0]
        assert unknown_item["grade"] is None
        summary = client.get("/api/v1/admin/materials/grade-summary", headers=headers).json()
        assert next(row for row in summary if row["grade"] is None)["material_count"] >= 1

        async def database_guards(version: int = 0) -> None:
            engine = create_async_engine(upload_database_url)
            try:
                async with engine.connect() as connection:

                    async def mutate(values: dict[str, object]) -> None:
                        await connection.execute(
                            update(SourceDocumentModel)
                            .where(
                                SourceDocumentModel.id == UUID(document_id),
                            )
                            .values(**values)
                        )
                        await connection.commit()

                    mutations: tuple[dict[str, object], ...] = (
                        {"intake_metadata": None},
                        {"intake_metadata": {}},
                        {"metadata_review_required": False},
                        {"metadata_review_required": False, "metadata_scope_version": version + 1},
                        {"extraction_status": "trusted"},
                    )
                    for mutation in mutations:
                        with pytest.raises(IntegrityError):
                            await mutate(mutation)
                        await connection.rollback()
            finally:
                await engine.dispose()

        asyncio.run(database_guards())
        subject = client.post(
            "/api/v1/admin/subjects",
            json={
                "code": "INTAKE-MATH",
                "name": "Reviewed mathematics",
            },
            headers=headers,
        ).json()
        exam = client.post(
            "/api/v1/admin/exam-configurations",
            json={
                "code": "INTAKE-G8",
                "name": "Grade 8",
                "grade": 8,
            },
            headers=headers,
        ).json()
        medium = client.post(
            "/api/v1/admin/media",
            json={
                "code": "intake-en",
                "name": "Reviewed English",
            },
            headers=headers,
        ).json()
        curriculum = client.post(
            "/api/v1/admin/curriculum-versions",
            json={
                "exam_configuration_id": exam["id"],
                "medium_id": medium["id"],
                "subject_id": subject["id"],
                "code": "INTAKE-V1",
                "title": "Reviewed scope",
            },
            headers=headers,
        ).json()
        scope_url = f"/api/v1/admin/materials/{document_id}/scope"
        for invalid_scope, code in ((None, 422), (str(UUID(int=998877)), 404)):
            invalid_confirmation = client.patch(
                scope_url,
                json={
                    "curriculum_version_id": invalid_scope,
                    "expected_version": 0,
                    "confirm_intake_metadata": True,
                },
                headers=headers,
            )
            assert invalid_confirmation.status_code == code
        inactive = client.post(
            "/api/v1/admin/curriculum-versions",
            json={
                "exam_configuration_id": exam["id"],
                "medium_id": medium["id"],
                "subject_id": subject["id"],
                "code": "INTAKE-INACTIVE",
                "title": "Inactive",
            },
            headers=headers,
        ).json()
        assert (
            client.post(
                f"/api/v1/admin/curriculum-versions/{inactive['id']}/deactivate",
                headers=headers,
            ).status_code
            == 200
        )
        invalid_confirmation = client.patch(
            scope_url,
            json={
                "curriculum_version_id": inactive["id"],
                "expected_version": 0,
                "confirm_intake_metadata": True,
            },
            headers=headers,
        )
        assert invalid_confirmation.status_code == 409
        assert invalid_confirmation.json()["detail"]["code"] == "material_scope_inactive"
        request = {"curriculum_version_id": curriculum["id"], "expected_version": 0}
        forbidden = client.patch(
            scope_url,
            json={**request, "confirm_intake_metadata": True},
            headers={"Authorization": "Bearer reviewer-token"},
        )
        assert forbidden.status_code == 403
        assigned = client.patch(scope_url, json=request, headers=headers)
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["metadata_review_required"] is True
        assert assigned.json()["year"] is None
        pending_items = client.get(
            "/api/v1/admin/materials",
            params={"document_id": document_id, "year": 2024},
            headers=headers,
        ).json()
        assert [item["id"] for item in pending_items] == [document_id]
        assert pending_items[0]["year"] == 2024
        asyncio.run(database_guards(version=1))
        confirmed = client.patch(
            scope_url,
            json={
                **request,
                "expected_version": 1,
                "confirm_intake_metadata": True,
            },
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["metadata_review_required"] is False
        assert confirmed.json()["metadata_scope_version"] == 2
        assert confirmed.json()["intake_metadata"] == metadata
        assert confirmed.json()["year"] == 2024
        assert confirmed.json()["extraction_status"] == "uploaded"
        confirmed_items = client.get(
            "/api/v1/admin/materials",
            params={"document_id": document_id, "year": 2024},
            headers=headers,
        ).json()
        assert [item["id"] for item in confirmed_items] == [document_id]
        assert confirmed_items[0]["year"] == 2024
        assert (
            client.get(
                "/api/v1/admin/materials",
                params={"document_id": document_id, "year": 2023},
                headers=headers,
            ).json()
            == []
        )
        stale = client.patch(
            scope_url,
            json={
                **request,
                "expected_version": 1,
                "confirm_intake_metadata": True,
            },
            headers=headers,
        )
        assert stale.status_code == 409
        assigned_item = client.get(
            "/api/v1/admin/materials",
            params={"document_id": document_id},
            headers=headers,
        ).json()[0]
        assert assigned_item["grade"] == 8
        assert assigned_item["subject"] == "Reviewed mathematics"
        assert assigned_item["medium"] == "Reviewed English"
        assert (
            client.get(
                "/api/v1/admin/materials",
                params={
                    "document_id": document_id,
                    "unassigned_only": True,
                },
                headers=headers,
            ).json()
            == []
        )

    async def audits() -> list[AdminAuditEventModel]:
        engine = create_async_engine(upload_database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return list(
                    await session.scalars(
                        select(AdminAuditEventModel)
                        .where(
                            AdminAuditEventModel.resource_id == UUID(document_id),
                        )
                        .order_by(AdminAuditEventModel.created_at)
                    )
                )
        finally:
            await engine.dispose()

    events = asyncio.run(audits())
    assert [event.action for event in events] == [
        "source_document.uploaded",
        "source_document.scope_corrected",
        "source_document.intake_metadata_confirmed",
    ]
    assert events[0].payload["intake_metadata"] == metadata
    assert events[-1].payload["intake_metadata"] == metadata
    assert events[-1].payload["previous_version"] == 1


@pytest.mark.integration
def test_intake_migration_preserves_legacy_rows_and_bounded_database_contract() -> None:
    from alembic import command
    from sqlalchemy import text

    from exam_guru_api.infrastructure.migrations import (
        _config_for_database,
        assert_database_schema_current,
    )

    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password=uuid4().hex,
        dbname="source_intake_migration_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        config = _config_for_database(database_url)
        command.upgrade(config, "0031_teacher_draft_race_guards")

        async def legacy_insert() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO source_documents (id, checksum_sha256, object_key, "
                            "original_filename, content_type, size_bytes, document_type, "
                            "created_by, updated_by) VALUES "
                            "(:id, :checksum, :key, 'legacy.pdf', 'application/pdf', 20, "
                            "'syllabus', :actor, :actor)"
                        ),
                        {
                            "id": UUID(int=30100),
                            "checksum": "b" * 64,
                            "key": "sources/bb/" + "b" * 64 + ".pdf",
                            "actor": ADMIN_ID,
                        },
                    )
            finally:
                await engine.dispose()

        asyncio.run(legacy_insert())
        command.upgrade(config, "head")
        assert_database_schema_current(database_url)

        async def check_contract() -> None:
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    row = (
                        await connection.execute(
                            text(
                                "SELECT intake_metadata, metadata_review_required "
                                "FROM source_documents "
                                "WHERE id = :id"
                            ),
                            {"id": UUID(int=30100)},
                        )
                    ).one()
                    assert row == (None, False)
                    valid: dict[str, object] = {
                        "candidate_grade": None,
                        "subject_label": "Mathematics",
                        "evidence": [],
                    }
                    invalid: list[object] = [
                        None,
                        [],
                        {"trusted": False},
                        {"candidate_grade": True},
                        {"candidate_grade": 14},
                        {"candidate_grade": "7"},
                        {"year": 1899},
                        {"year": 2101},
                        {"year": 2024.5},
                        {"warnings": None},
                        {"evidence": ["x"] * 33},
                        {"evidence": ["x" * 1025]},
                        {"evidence": [True]},
                        {"evidence": [""]},
                        {"evidence": [" padded"]},
                        {"evidence": ["bad\ntext"]},
                        {"term": "x" * 65},
                        {"source_reference": "x" * 1025},
                        {"publisher": "x" * 201},
                        {"subject_label": 5},
                        {"medium_label": ""},
                        {"warnings": ["x" * 1024] * 32},
                    ]
                    for payload, expected in [(valid, True), *[(item, False) for item in invalid]]:
                        assert (
                            await connection.scalar(
                                text(
                                    "SELECT source_intake_metadata_is_bounded("
                                    "CAST(:value AS jsonb))"
                                ),
                                {"value": json.dumps(payload)},
                            )
                            is expected
                        )
            finally:
                await engine.dispose()

        asyncio.run(check_contract())
        command.downgrade(config, "0031_teacher_draft_race_guards")
        command.upgrade(config, "head")
        asyncio.run(check_contract())
