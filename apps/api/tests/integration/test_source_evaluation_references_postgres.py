import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exam_guru_api.api.dependencies import get_database_session, get_object_storage, get_settings
from exam_guru_api.api.routes.source_fidelity import router
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.rate_limits import NoOpRateLimiter
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.fidelity_models import (
    PageGroundTruthModel,
    PageReviewEventModel,
    SourceEvaluationReferenceModel,
)
from exam_guru_api.documents.fidelity_service import PageFidelityService
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.infrastructure.migrations import _config_for_database
from exam_guru_api.infrastructure.object_storage import LocalFileObjectStorage
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    ADMIN_HEADERS,
    REVIEWER,
    REVIEWER_HEADERS,
    StaticIdentityProvider,
    database_session,
    workspace_database_url,
)

__all__ = ["workspace_database_url"]
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/admin"


@pytest.fixture
def reference_client(
    workspace_database_url: str, tmp_path: Path
) -> Iterator[tuple[TestClient, LocalFileObjectStorage]]:
    storage = LocalFileObjectStorage(root=tmp_path / "originals", max_object_bytes=1024 * 1024)
    settings = Settings(_env_file=None, storage_root=str(tmp_path / "originals"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(workspace_database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def session() -> AsyncIterator[AsyncSession]:
            async with factory() as value:
                yield value

        app.dependency_overrides[get_database_session] = session
        app.dependency_overrides[get_object_storage] = lambda: storage
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.identity_provider = StaticIdentityProvider()
    app.state.rate_limiter = NoOpRateLimiter()
    app.include_router(router, prefix=PREFIX)
    with TestClient(app) as client:
        yield client, storage


async def failed_source(database_url: str, storage: LocalFileObjectStorage) -> tuple[UUID, UUID]:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 60), "The original value is 4.")
        content = pdf.tobytes()
    checksum = hashlib.sha256(content).hexdigest()
    key = f"sources/{checksum[:2]}/{checksum}.pdf"
    storage.put_immutable(key, content, content_type="application/pdf")
    document_id = uuid4()
    intake = {"medium_label": "Sinhala", "candidate_grade": 5}
    async with database_session(database_url) as session:
        session.add(
            SourceDocumentModel(
                id=document_id,
                checksum_sha256=checksum,
                object_key=key,
                original_filename="Synthetic evaluation reference fixture.pdf",
                content_type="application/pdf",
                size_bytes=len(content),
                document_type=SourceDocumentType.OTHER_APPROVED,
                original_page_count=1,
                intake_metadata=intake,
                metadata_review_required=True,
                created_by=ADMIN.subject_id,
                updated_by=ADMIN.subject_id,
            )
        )
        session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=ADMIN.subject_id,
                resource_type="source_document",
                resource_id=document_id,
                action="source_document.uploaded",
                payload={"intake_metadata": intake, "metadata_review_required": True},
            )
        )
        await session.commit()
        service = PageFidelityService(session)
        await service.record_candidate(
            document_id,
            1,
            raw_text="lvqZZaa;%%",
            method="native",
            actor_id=ADMIN.subject_id,
            provenance={"fonts": ["FMAbhaya"], "source_languages": ["si"], "languages": ["en"]},
        )
        benchmark_id = await service.create_benchmark(
            name=f"Synthetic evaluation-only reference {document_id}",
            pages=((document_id, 1, ("blocked",)),),
            actor_id=ADMIN.subject_id,
            selection={"purpose": "disposable workflow test"},
        )
    return document_id, benchmark_id


def test_empty_evaluation_migration_roundtrip_keeps_existing_source_tables(
    workspace_database_url: str,
) -> None:
    async def count_sources() -> int:
        async with database_session(workspace_database_url) as session:
            return int(
                await session.scalar(select(func.count()).select_from(SourceDocumentModel)) or 0
            )

    before = asyncio.run(count_sources())
    configuration = _config_for_database(workspace_database_url)
    command.downgrade(configuration, "0041_source_metadata_candidates")
    assert asyncio.run(count_sources()) == before
    command.upgrade(configuration, "head")
    assert asyncio.run(count_sources()) == before


def test_human_evaluation_reference_is_independent_of_source_confirmation(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
) -> None:
    client, storage = reference_client
    document_id, benchmark_id = asyncio.run(failed_source(workspace_database_url, storage))
    page_path = f"{PREFIX}/source-benchmarks/{benchmark_id}/pages/{document_id}/1"
    workspace_path = f"{PREFIX}/materials/{document_id}/review-workspace"
    before = client.get(workspace_path, headers=ADMIN_HEADERS).json()
    assert before["page"]["can_confirm"] is False
    assert before["page"]["state"] == "failed"
    assert client.post(f"{page_path}/evaluation-preview").status_code == 401
    prepared = client.post(f"{page_path}/evaluation-preview", headers=REVIEWER_HEADERS)
    assert prepared.status_code == 201, prepared.text
    preview = prepared.json()
    image = client.get(preview["preview_url"], headers=REVIEWER_HEADERS)
    assert image.status_code == 200
    assert hashlib.sha256(image.content).hexdigest() == preview["image_sha256"]
    body = {
        "preview_id": preview["id"],
        "expected_version": 0,
        "text": "The original value is 4.",
        "blank_reference": False,
        "compared_with_original": True,
        "human_reviewed": True,
        "reason": "Human reference in the disposable evaluation scenario",
    }
    path = f"{page_path}/evaluation-references"
    rejected = client.post(path, headers=REVIEWER_HEADERS, json={**body, "human_reviewed": False})
    assert rejected.status_code == 422
    saved = client.post(path, headers=REVIEWER_HEADERS, json=body)
    assert saved.status_code == 201, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["text"] == body["text"]
    assert saved.json()["evaluation_only"] is True
    for response in (prepared, image, rejected, saved):
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    confirmation = client.post(
        f"{PREFIX}/materials/{document_id}/pages/1/confirm",
        headers=ADMIN_HEADERS,
        json={
            "candidate_id": before["page"]["candidate_id"],
            "expected_version": before["page"]["version"],
            "compared_with_original": True,
            "reason": "Evaluation does not approve the source",
        },
    )
    assert confirmation.status_code == 409
    assert client.post(path, headers=REVIEWER_HEADERS, json=body).status_code == 409
    after = client.get(workspace_path, headers=ADMIN_HEADERS).json()
    assert after == before
    benchmark = client.get(
        f"{PREFIX}/source-benchmarks/{benchmark_id}", headers=REVIEWER_HEADERS
    ).json()
    assert benchmark["adjudicated_pages"] == 0
    assert benchmark["pending_pages"] == 1
    assert benchmark["evaluation_references"] == {
        "selected_pages": 1,
        "referenced_pages": 1,
        "pending_pages": 0,
        "evaluation_only": True,
    }
    assert benchmark["pages"][0]["ground_truth_versions"] == 0
    assert benchmark["pages"][0]["evaluation_reference_versions"] == 1

    async def check_gates() -> None:
        async with database_session(workspace_database_url) as session:
            assert await session.scalar(select(func.count()).select_from(PageGroundTruthModel)) == 0
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PageReviewEventModel)
                    .where(
                        PageReviewEventModel.document_id == document_id,
                        PageReviewEventModel.action == "confirmed",
                    )
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT public.source_document_fidelity_is_current(:id)"),
                    {"id": document_id},
                )
                is False
            )

    asyncio.run(check_gates())


def reference_case(
    database_url: str, client: TestClient, storage: LocalFileObjectStorage
) -> tuple[str, dict[str, object], dict[str, object]]:
    document_id, benchmark_id = asyncio.run(failed_source(database_url, storage))
    page_path = f"{PREFIX}/source-benchmarks/{benchmark_id}/pages/{document_id}/1"
    response = client.post(f"{page_path}/evaluation-preview", headers=REVIEWER_HEADERS)
    assert response.status_code == 201, response.text
    preview = response.json()
    body = {
        "preview_id": preview["id"],
        "expected_version": 0,
        "text": "The original value is 4.",
        "blank_reference": False,
        "compared_with_original": True,
        "human_reviewed": True,
        "reason": "Reviewed evaluation reference in a disposable fixture",
    }
    return page_path, preview, body


def test_reference_versions_preserve_raw_unicode_and_remain_distinct_from_source_truth(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
) -> None:
    client, storage = reference_client
    page_path, preview, body = reference_case(workspace_database_url, client, storage)
    path = f"{page_path}/evaluation-references"
    for version, value in enumerate(("cafe\u0301", "ශ්‍රී ලංකාව")):
        saved = client.post(
            path,
            headers=REVIEWER_HEADERS,
            json={**body, "text": value, "expected_version": version},
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["text"] == value
        assert saved.json()["text_sha256"] == hashlib.sha256(value.encode()).hexdigest()
    history = client.get(path, headers=ADMIN_HEADERS).json()
    assert [entry["version"] for entry in history] == [2, 1]
    assert history[1]["text"] == "cafe\u0301"
    assert history[1]["normalized_text"] == "café"
    assert all(entry["evaluation_only"] for entry in history)
    assert (
        client.post(path, headers=ADMIN_HEADERS, json={**body, "expected_version": 2}).status_code
        == 404
    )
    foreign_path = f"{PREFIX}/source-benchmarks/{uuid4()}/evaluation-previews/{preview['id']}/image"
    assert client.get(foreign_path, headers=REVIEWER_HEADERS).status_code == 404


@pytest.mark.parametrize(
    "update",
    [
        {"compared_with_original": False},
        {"compared_with_original": "true"},
        {"human_reviewed": None},
        {"expected_version": True},
        {"blank_reference": True},
        {"text": ""},
        {"text": "\t\n"},
        {"text": "private-reference-marker\x00"},
        {"text": "x" * 100001},
        {"can_confirm": True},
    ],
)
def test_reference_requires_explicit_bounded_human_input_without_echoing_rejected_text(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
    update: dict[str, object],
) -> None:
    client, storage = reference_client
    page_path, _, body = reference_case(workspace_database_url, client, storage)
    response = client.post(
        f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS, json={**body, **update}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_evaluation_reference_request"}}
    assert "private-reference-marker" not in response.text
    assert client.get(f"{page_path}/evaluation-references", headers=ADMIN_HEADERS).json() == []


def test_empty_reference_requires_explicit_blank_confirmation(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
) -> None:
    client, storage = reference_client
    page_path, _, body = reference_case(workspace_database_url, client, storage)
    saved = client.post(
        f"{page_path}/evaluation-references",
        headers=REVIEWER_HEADERS,
        json={**body, "text": "", "blank_reference": True},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["blank_reference"] is True
    assert saved.json()["text_sha256"] == hashlib.sha256(b"").hexdigest()


def test_evaluation_reference_evidence_is_append_only_and_cannot_be_downgraded_away(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
) -> None:
    client, storage = reference_client
    page_path, preview, body = reference_case(workspace_database_url, client, storage)
    response = client.post(
        f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS, json=body
    )
    assert response.status_code == 201
    identifier = response.json()["id"]

    async def tamper() -> None:
        async with database_session(workspace_database_url) as session:
            for statement, value in (
                (
                    "UPDATE source_evaluation_references SET reason='changed' WHERE id=:id",
                    identifier,
                ),
                ("DELETE FROM source_evaluation_references WHERE id=:id", identifier),
                (
                    "UPDATE source_evaluation_previews SET image_metadata='{}'::jsonb WHERE id=:id",
                    preview["id"],
                ),
                ("DELETE FROM source_evaluation_previews WHERE id=:id", preview["id"]),
            ):
                with pytest.raises(DBAPIError, match="append only"):
                    await session.execute(text(statement), {"id": value})
                await session.rollback()
            for tables in (
                "source_evaluation_references",
                "source_evaluation_references, source_evaluation_previews",
            ):
                with pytest.raises(DBAPIError, match="append only"):
                    await session.execute(text(f"TRUNCATE {tables}"))
                await session.rollback()

    asyncio.run(tamper())
    with pytest.raises(DBAPIError, match="cannot discard evaluation reference evidence"):
        command.downgrade(
            _config_for_database(workspace_database_url), "0041_source_metadata_candidates"
        )
    history = client.get(f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS).json()
    assert [item["id"] for item in history] == [identifier]


def test_concurrent_human_reference_saves_do_not_overwrite_a_revision(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
) -> None:
    client, storage = reference_client
    page_path, preview, body = reference_case(workspace_database_url, client, storage)
    second = client.post(f"{page_path}/evaluation-preview", headers=REVIEWER_HEADERS).json()

    def save(identifier: object) -> int:
        return client.post(
            f"{page_path}/evaluation-references",
            headers=REVIEWER_HEADERS,
            json={**body, "preview_id": identifier},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(save, (preview["id"], second["id"])))
    assert sorted(statuses) == [201, 409]
    history = client.get(f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS).json()
    assert [item["version"] for item in history] == [1]


def test_reference_write_serializes_with_source_identity_and_quarantine_changes(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
) -> None:
    client, storage = reference_client
    _, preview, _ = reference_case(workspace_database_url, client, storage)

    async def scenario() -> None:
        async with database_session(workspace_database_url) as first:
            raw = b"Reference fixture"
            digest = hashlib.sha256(raw).hexdigest()
            first.add(
                SourceEvaluationReferenceModel(
                    id=uuid4(),
                    preview_id=UUID(str(preview["id"])),
                    benchmark_id=UUID(str(preview["benchmark_id"])),
                    document_id=UUID(str(preview["document_id"])),
                    page_number=1,
                    version=1,
                    raw_text_utf8=raw,
                    normalized_text=raw.decode(),
                    text_sha256=digest,
                    normalized_sha256=digest,
                    blank_reference=False,
                    reason="Source serialization test",
                    reviewer_id=REVIEWER.subject_id,
                )
            )
            await first.flush()
            async with database_session(workspace_database_url) as second:
                await second.execute(text("SET LOCAL lock_timeout='100ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    await second.execute(
                        text("UPDATE source_documents SET updated_at=updated_at WHERE id=:id"),
                        {"id": preview["document_id"]},
                    )
                await second.rollback()
            await first.rollback()

    asyncio.run(scenario())


def test_reference_save_rechecks_the_original_not_just_the_preview(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
    tmp_path: Path,
) -> None:
    client, storage = reference_client
    page_path, _, body = reference_case(workspace_database_url, client, storage)
    original = next((tmp_path / "originals" / "sources").rglob("*.pdf"))
    original.write_bytes(b"damaged disposable original")
    response = client.post(
        f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS, json=body
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "source_original_unavailable"}}
    assert client.get(f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS).json() == []


def test_reference_image_loss_fails_closed_without_regeneration_or_source_mutation(
    workspace_database_url: str,
    reference_client: tuple[TestClient, LocalFileObjectStorage],
    tmp_path: Path,
) -> None:
    client, storage = reference_client
    page_path, preview, body = reference_case(workspace_database_url, client, storage)
    artifact = next(
        path
        for path in (tmp_path / "originals" / "fidelity-page-images").rglob("*")
        if path.is_file()
    )
    artifact.write_bytes(b"damaged disposable image")
    image = client.get(str(preview["preview_url"]), headers=REVIEWER_HEADERS)
    assert image.status_code == 503
    response = client.post(
        f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS, json=body
    )
    assert response.status_code == 503
    assert client.get(f"{page_path}/evaluation-references", headers=REVIEWER_HEADERS).json() == []
