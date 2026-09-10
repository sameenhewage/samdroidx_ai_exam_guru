import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.documents import router
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    CurriculumNotAdmittedError,
    get_catalogue_admission,
    record_catalogue_admission,
)
from exam_guru_api.curriculum.admission_models import CatalogueAdmissionDecisionModel
from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel, SourceMetadataCandidateModel
from exam_guru_api.documents.schemas import MaterialStatus
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.migrations import _config_for_database
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    ADMIN_HEADERS,
    PREFIX,
    REVIEWER_HEADERS,
    StaticIdentityProvider,
    add_curriculum,
    admit_curriculum,
    confirm_state,
    database_session,
    record_page,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


def materials(session: AsyncSession) -> SourceDocumentService:
    return SourceDocumentService(session, cast(ObjectStorage, object()), max_upload_bytes=1024)


async def add_intake_source(
    session: AsyncSession, *, intake: bool = True, curriculum_id: UUID | None = None
) -> UUID:
    identifier = uuid4()
    checksum = hashlib.sha256(identifier.bytes).hexdigest()
    metadata = (
        {
            "candidate_grade": 7,
            "subject_label": "Mathematics",
            "year": 2024,
            "evidence": ["Disposable metadata confirmation fixture, not real content approval"],
        }
        if intake
        else None
    )
    session.add(
        SourceDocumentModel(
            id=identifier,
            checksum_sha256=checksum,
            object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
            original_filename="Disposable metadata evidence.pdf",
            content_type="application/pdf",
            size_bytes=100,
            document_type=SourceDocumentType.TEACHER_GUIDE,
            original_page_count=1,
            curriculum_version_id=curriculum_id,
            intake_metadata=metadata,
            metadata_review_required=intake,
            created_by=ADMIN.subject_id,
            updated_by=ADMIN.subject_id,
        )
    )
    session.add(
        AdminAuditEventModel(
            id=uuid4(),
            resource_type="source_document",
            resource_id=identifier,
            action="source_document.uploaded",
            actor_id=ADMIN.subject_id,
            payload={
                "checksum_sha256": checksum,
                "intake_metadata": metadata,
                "metadata_review_required": intake,
            },
        )
    )
    await session.commit()
    return identifier


async def assign(
    session: AsyncSession,
    document_id: UUID,
    curriculum_id: UUID,
    *,
    confirm: bool,
    version: int = 0,
) -> SourceDocumentModel:
    return await materials(session).correct_scope(
        document_id,
        curriculum_version_id=curriculum_id,
        unit_id=None,
        lesson_id=None,
        expected_version=version,
        actor_id=ADMIN.subject_id,
        confirm_intake_metadata=confirm,
    )


async def withhold_admission(session: AsyncSession, curriculum_id: UUID, state: str) -> None:
    current = await get_catalogue_admission(session, curriculum_id)
    await record_catalogue_admission(
        session,
        curriculum_id,
        AdmissionDecisionRequest.model_validate(
            {
                "state": state,
                "educational_approval": False,
                "expected_version": current.version,
                "expected_scope_fingerprint": current.scope_fingerprint,
                "reason": "Explicitly withheld educational use in a disposable integration fixture",
                "source_reference": "Synthetic local review register",
                "evidence": ["This decision is not inferred from the uploading actor"],
            }
        ),
        principal=ADMIN,
    )
    await session.commit()


async def document_snapshot(session: AsyncSession, document_id: UUID) -> object:
    return await session.scalar(
        text("SELECT to_jsonb(d) FROM source_documents d WHERE id=:id"), {"id": document_id}
    )


async def source_audits(session: AsyncSession, document_id: UUID) -> list[AdminAuditEventModel]:
    return list(
        await session.scalars(
            select(AdminAuditEventModel)
            .where(
                AdminAuditEventModel.resource_type == "source_document",
                AdminAuditEventModel.resource_id == document_id,
            )
            .order_by(AdminAuditEventModel.created_at, AdminAuditEventModel.id)
        )
    )


@pytest.mark.parametrize(
    "admission", ["unreviewed", "private_fixture", "rejected", "quarantined", "stale"]
)
def test_explicit_confirmation_requires_current_educational_admission_without_mutation(
    workspace_database_url: str, admission: str
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            if admission == "private_fixture":
                await session.execute(
                    update(CurriculumVersionModel)
                    .where(CurriculumVersionModel.id == curriculum_id)
                    .values(title="Internal E2E fixture curriculum")
                )
                await session.commit()
            elif admission in {"rejected", "quarantined"}:
                await withhold_admission(session, curriculum_id, admission)
            elif admission == "stale":
                await admit_curriculum(session, curriculum_id)
                await session.execute(
                    update(CurriculumVersionModel)
                    .where(CurriculumVersionModel.id == curriculum_id)
                    .values(title="Changed educational scope after approval")
                )
                await session.commit()
            document_id = await add_intake_source(session)
            before = await document_snapshot(session, document_id)
            audits = [item.id for item in await source_audits(session, document_id)]
            with pytest.raises(CurriculumNotAdmittedError):
                await assign(session, document_id, curriculum_id, confirm=True)
            assert await document_snapshot(session, document_id) == before
            assert [item.id for item in await source_audits(session, document_id)] == audits

    asyncio.run(scenario())


@pytest.mark.parametrize("intake", [False, True])
def test_tentative_assignment_remains_unreviewed_and_never_creates_catalogue_admission(
    workspace_database_url: str, intake: bool
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            document_id = await add_intake_source(session, intake=intake)
            await confirm_state(session, await record_page(session, document_id, 1))
            result = await assign(session, document_id, curriculum_id, confirm=False)
            assert result.metadata_review_required
            assert result.metadata_scope_version == 1
            assert result.extraction_status is ExtractionStatus.UPLOADED
            assert not (await get_catalogue_admission(session, curriculum_id)).admitted
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CatalogueAdmissionDecisionModel)
                    .where(CatalogueAdmissionDecisionModel.curriculum_version_id == curriculum_id)
                )
                == 0
            )
            listed = await materials(session).list_materials(document_id=document_id)
            assert listed[0].status is MaterialStatus.NEEDS_REVIEW
            with pytest.raises(CurriculumNotAdmittedError):
                await assign(session, document_id, curriculum_id, confirm=True, version=1)

    asyncio.run(scenario())


@pytest.mark.parametrize("intake", [False, True])
def test_admitted_scope_still_requires_explicit_confirmation_and_preserves_versioned_evidence(
    workspace_database_url: str, intake: bool
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_intake_source(session, intake=intake)
            await confirm_state(session, await record_page(session, document_id, 1))
            tentative = await assign(session, document_id, curriculum_id, confirm=False)
            evidence = tentative.intake_metadata
            assert tentative.metadata_review_required
            assert not await materials(session).list_materials(
                document_id=document_id, status=MaterialStatus.READY_FOR_AI
            )
            confirmed = await assign(session, document_id, curriculum_id, confirm=True, version=1)
            assert not confirmed.metadata_review_required
            assert confirmed.metadata_scope_version == 2
            assert confirmed.intake_metadata == evidence
            assert confirmed.year == (2024 if intake else None)
            assert confirmed.extraction_status is ExtractionStatus.UPLOADED
            ready = await materials(session).list_materials(
                document_id=document_id, status=MaterialStatus.READY_FOR_AI
            )
            assert [item.id for item in ready] == [document_id]
            replay = await assign(session, document_id, curriculum_id, confirm=True, version=2)
            assert replay.metadata_scope_version == 2
            audits = await source_audits(session, document_id)
            assert [item.action for item in audits] == [
                "source_document.uploaded",
                "source_document.scope_corrected",
                "source_document.intake_metadata_confirmed",
            ]
            assert audits[-1].payload["previous_version"] == 1
            assert audits[-1].payload["version"] == 2
            assert audits[-1].payload["intake_metadata"] == evidence
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CatalogueAdmissionDecisionModel)
                    .where(CatalogueAdmissionDecisionModel.curriculum_version_id == curriculum_id)
                )
                == 1
            )

    asyncio.run(scenario())


def test_unassigned_intake_year_filters_and_removed_inventory_remain_read_only(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            document_id = await add_intake_source(session)
            service = materials(session)
            initial = await service.list_materials(document_id=document_id, grade=7, year=2024)
            assert initial[0].status is MaterialStatus.NEEDS_REVIEW
            assert initial[0].subject_id is None
            assert initial[0].subject == "Mathematics"
            assert initial[0].year == 2024
            before = next(item for item in await service.grade_summary() if item.grade == 7)
            await service.remove_from_ai_use(
                document_id,
                reason="Wrong material removed without destroying candidate evidence",
                expected_version=0,
                actor_id=ADMIN.subject_id,
            )
            await session.execute(text("SET TRANSACTION READ ONLY"))
            result = await service.list_materials(
                document_id=document_id,
                grade=7,
                year=2024,
                unassigned_only=True,
                status=MaterialStatus.REMOVED,
            )
            assert [item.id for item in result] == [document_id]
            assert result[0].metadata_review_required
            assert result[0].year == 2024
            assert not await service.list_materials(document_id=document_id, grade=8)
            assert not await service.list_materials(document_id=document_id, year=2023)
            after = next(item for item in await service.grade_summary() if item.grade == 7)
            assert after.material_count == before.material_count
            assert after.needs_review_count == before.needs_review_count - 1
            assert after.removed_count == before.removed_count + 1
            source = await session.get(SourceDocumentModel, document_id)
            assert source is not None
            assert source.year is None
            assert source.intake_metadata is not None
            assert source.intake_metadata["year"] == 2024

    asyncio.run(scenario())


def test_noop_confirmation_cannot_reuse_revoked_curriculum_approval(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_intake_source(session)
            await assign(session, document_id, curriculum_id, confirm=True)
            before = await document_snapshot(session, document_id)
            await withhold_admission(session, curriculum_id, "quarantined")
            with pytest.raises(CurriculumNotAdmittedError):
                await assign(session, document_id, curriculum_id, confirm=True, version=1)
            assert await document_snapshot(session, document_id) == before
            assert len(await source_audits(session, document_id)) == 2

    asyncio.run(scenario())


def test_confirmation_keeps_admission_locked_until_transaction_end(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as confirming:
            curriculum_id = await add_curriculum(confirming)
            await admit_curriculum(confirming, curriculum_id)
            await materials(confirming)._validate_confirmation_scope(curriculum_id)
            async with database_session(workspace_database_url) as revoking:
                await revoking.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    await withhold_admission(revoking, curriculum_id, "quarantined")
                await revoking.rollback()
            await confirming.rollback()
            await withhold_admission(confirming, curriculum_id, "quarantined")
            with pytest.raises(CurriculumNotAdmittedError):
                await materials(confirming)._validate_confirmation_scope(curriculum_id)

    asyncio.run(scenario())


def test_stale_confirmation_cannot_create_another_audit_for_the_same_metadata_version(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as stale:
            curriculum_id = await add_curriculum(stale)
            await admit_curriculum(stale, curriculum_id)
            document_id = await add_intake_source(stale)
            cached = await stale.get(SourceDocumentModel, document_id)
            assert cached is not None
            assert cached.metadata_scope_version == 0
            async with database_session(workspace_database_url) as writer:
                await assign(writer, document_id, curriculum_id, confirm=True)
            with pytest.raises(ConcurrentMaterialScopeVersionError):
                await assign(stale, document_id, curriculum_id, confirm=True)
            assert len(await source_audits(stale, document_id)) == 2

    asyncio.run(scenario())


@pytest.fixture
def materials_client(workspace_database_url: str) -> Iterator[TestClient]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(workspace_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def session_dependency() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_database_session] = session_dependency
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.identity_provider = StaticIdentityProvider()
    app.state.settings = Settings(_env_file=None, environment="test")
    app.state.object_storage = cast(ObjectStorage, object())
    app.include_router(router, prefix=PREFIX)
    with TestClient(app) as client:
        yield client


def test_raw_scope_api_requires_source_write_and_returns_stable_admission_conflicts(
    workspace_database_url: str, materials_client: TestClient
) -> None:
    async def prepare() -> tuple[UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await add_curriculum(session), await add_intake_source(session)

    curriculum_id, document_id = asyncio.run(prepare())
    path = f"{PREFIX}/materials/{document_id}/scope"
    body = {
        "curriculum_version_id": str(curriculum_id),
        "expected_version": 0,
        "confirm_intake_metadata": True,
    }
    assert materials_client.patch(path, json=body).status_code == 401
    assert materials_client.patch(path, json=body, headers=REVIEWER_HEADERS).status_code == 403
    rejected = materials_client.patch(path, json=body, headers=ADMIN_HEADERS)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": {"code": "curriculum_not_admitted"}}

    async def approve() -> None:
        async with database_session(workspace_database_url) as session:
            await admit_curriculum(session, curriculum_id)

    asyncio.run(approve())
    confirmed = materials_client.patch(path, json=body, headers=ADMIN_HEADERS)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["metadata_review_required"] is False
    assert confirmed.json()["metadata_scope_version"] == 1
    assert confirmed.json()["extraction_status"] == "uploaded"
    assert confirmed.json()["year"] == 2024
    listed = materials_client.get(
        f"{PREFIX}/materials", params={"document_id": str(document_id)}, headers=ADMIN_HEADERS
    )
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "needs_review"


def test_empty_candidate_migration_roundtrip_preserves_existing_source_evidence(
    workspace_database_url: str,
) -> None:
    async def snapshot() -> object:
        async with database_session(workspace_database_url) as session:
            return await session.scalar(
                text("SELECT jsonb_agg(to_jsonb(d) ORDER BY id) FROM source_documents d")
            )

    before = asyncio.run(snapshot())
    config = _config_for_database(workspace_database_url)
    command.downgrade(config, "0040_source_fidelity_rules_v2")
    assert asyncio.run(snapshot()) == before
    command.upgrade(config, "head")
    assert asyncio.run(snapshot()) == before


def test_candidate_metadata_correction_preserves_original_without_catalogue_authority(
    workspace_database_url: str, materials_client: TestClient
) -> None:
    async def prepare() -> UUID:
        async with database_session(workspace_database_url) as session:
            return await add_intake_source(session)

    document_id = asyncio.run(prepare())
    path = f"{PREFIX}/materials/{document_id}/metadata-candidates"
    body = {
        "expected_scope_version": 0,
        "expected_candidate_version": 0,
        "metadata": {
            "candidate_grade": 3,
            "subject_label": "English",
            "medium_label": "English",
            "document_type_label": "Worksheet",
            "year": None,
        },
        "reason": "Correct candidate descriptions without approving a curriculum",
    }
    assert materials_client.post(path, json=body).status_code == 401
    assert materials_client.post(path, json=body, headers=REVIEWER_HEADERS).status_code == 403
    response = materials_client.post(path, json=body, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    corrected = response.json()
    assert corrected["metadata_review_required"] is True
    assert corrected["curriculum_version_id"] is None
    assert corrected["intake_metadata"]["candidate_grade"] == 7
    assert corrected["intake_metadata"]["year"] == 2024
    assert corrected["metadata_candidate"]["version"] == 1
    assert corrected["metadata_candidate"]["metadata"]["candidate_grade"] == 3
    assert corrected["metadata_candidate"]["metadata"]["year"] is None
    assert materials_client.post(path, json=body, headers=ADMIN_HEADERS).status_code == 409
    assert (
        materials_client.post(
            path, json={**body, "confirm_intake_metadata": True}, headers=ADMIN_HEADERS
        ).status_code
        == 422
    )
    listed = materials_client.get(f"{PREFIX}/materials", params={"grade": 3}, headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    item = next(value for value in listed.json() if value["id"] == str(document_id))
    assert item["grade"] == 3
    assert item["subject"] == "English"
    assert item["medium"] == "English"
    assert item["year"] is None
    assert item["status"] == "needs_review"
    assert item["intake_metadata"]["candidate_grade"] == 7
    filtered = materials_client.get(
        f"{PREFIX}/materials", params={"year": 2024}, headers=ADMIN_HEADERS
    )
    assert all(value["id"] != str(document_id) for value in filtered.json())


def test_metadata_candidate_history_is_append_only_and_requires_bound_audit(
    workspace_database_url: str, materials_client: TestClient
) -> None:
    async def prepare() -> tuple[UUID, object]:
        async with database_session(workspace_database_url) as session:
            document_id = await add_intake_source(session)
            return document_id, await document_snapshot(session, document_id)

    document_id, original = asyncio.run(prepare())
    path = f"{PREFIX}/materials/{document_id}/metadata-candidates"
    for version, grade in enumerate((3, 4)):
        response = materials_client.post(
            path,
            headers=ADMIN_HEADERS,
            json={
                "expected_scope_version": 0,
                "expected_candidate_version": version,
                "metadata": {"candidate_grade": grade, "subject_label": "English"},
                "reason": "Correct unverified descriptions in a disposable fixture",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["metadata_candidate"]["version"] == version + 1

    async def check_history() -> None:
        async with database_session(workspace_database_url) as session:
            assert await document_snapshot(session, document_id) == original
            candidates = list(
                await session.scalars(
                    select(SourceMetadataCandidateModel)
                    .where(SourceMetadataCandidateModel.document_id == document_id)
                    .order_by(SourceMetadataCandidateModel.version)
                )
            )
            assert [row.payload["candidate_grade"] for row in candidates] == [3, 4]
            audits = [
                event
                for event in await source_audits(session, document_id)
                if event.action == "source_document.metadata_candidate_corrected"
            ]
            assert len(audits) == 2
            for statement in (
                "UPDATE source_metadata_candidates SET reason='changed' WHERE document_id=:id",
                "DELETE FROM source_metadata_candidates WHERE document_id=:id",
            ):
                with pytest.raises(DBAPIError, match="append only"):
                    await session.execute(text(statement), {"id": document_id})
                await session.rollback()
            source = await session.get(SourceDocumentModel, document_id)
            assert source is not None
            checksum = source.checksum_sha256
            for candidate_checksum, message in (
                (checksum, "matching audit"),
                ("0" * 64, "unassigned untrusted source"),
            ):
                session.add(
                    SourceMetadataCandidateModel(
                        id=uuid4(),
                        document_id=document_id,
                        source_checksum_sha256=candidate_checksum,
                        version=3,
                        scope_version=0,
                        payload={"candidate_grade": 5},
                        material_type=SourceDocumentType.TEACHER_GUIDE,
                        reason="Missing or mismatched audit fixture",
                        created_by=ADMIN.subject_id,
                    )
                )
                with pytest.raises(DBAPIError, match=message):
                    await session.commit()
                await session.rollback()
            assert await document_snapshot(session, document_id) == original

    asyncio.run(check_history())
    with pytest.raises(DBAPIError, match="cannot discard source metadata candidate evidence"):
        command.downgrade(
            _config_for_database(workspace_database_url), "0040_source_fidelity_rules_v2"
        )
    asyncio.run(check_history())


def test_metadata_confirmation_binds_the_current_candidate_revision(
    workspace_database_url: str, materials_client: TestClient
) -> None:
    async def prepare() -> tuple[UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            return await add_intake_source(session), curriculum_id

    document_id, curriculum_id = asyncio.run(prepare())
    candidate = materials_client.post(
        f"{PREFIX}/materials/{document_id}/metadata-candidates",
        headers=ADMIN_HEADERS,
        json={
            "expected_scope_version": 0,
            "expected_candidate_version": 0,
            "metadata": {"candidate_grade": 7, "year": 2021, "document_type_label": "Worksheet"},
            "material_type": "other_approved",
            "reason": "Correct the candidate source year before scope confirmation",
        },
    )
    assert candidate.status_code == 200
    body = {
        "expected_version": 0,
        "curriculum_version_id": str(curriculum_id),
        "confirm_intake_metadata": True,
    }
    stale = materials_client.patch(
        f"{PREFIX}/materials/{document_id}/scope", headers=ADMIN_HEADERS, json=body
    )
    assert stale.status_code == 409
    confirmed = materials_client.patch(
        f"{PREFIX}/materials/{document_id}/scope",
        headers=ADMIN_HEADERS,
        json={**body, "metadata_candidate_id": candidate.json()["metadata_candidate"]["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["year"] == 2021
    assert confirmed.json()["document_type"] == "other_approved"
    assert confirmed.json()["intake_metadata"]["year"] == 2024
    assert confirmed.json()["metadata_review_required"] is False
