import asyncio
import hashlib
import json
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    get_catalogue_admission,
    record_catalogue_admission,
)
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    SubjectModel,
)
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.service import MaterialScopeImmutableError, SourceDocumentService
from exam_guru_api.documents.understanding_contracts import PageUnderstanding, _canonical_json
from exam_guru_api.documents.understanding_models import TrustedPageKnowledgeModel
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.documents.understanding_verification import TrustedPageKnowledge
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.knowledge.unit_models import (
    KnowledgeProjectionModel,
    KnowledgeUnitModel,
    KnowledgeUnitRegionModel,
)
from exam_guru_api.knowledge.unit_service import (
    KnowledgePreparationError,
    KnowledgeUnitService,
    PreparedPageKnowledge,
)
from exam_guru_api.knowledge.units import (
    KnowledgeProjection,
    KnowledgeScope,
    KnowledgeUnit,
    derive_knowledge_units,
    project_knowledge_unit,
)
from tests.integration.test_document_understanding_postgres import approve_page, page_input
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    REVIEWER,
    add_curriculum,
    add_source,
    admit_curriculum,
    database_session,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.test_document_understanding_contracts import counting_candidate, parse
from tests.test_document_understanding_verification import approve
from tests.test_document_understanding_verification import candidate as standalone_candidate
from tests.test_knowledge_units import scope as standalone_scope

pytestmark = pytest.mark.integration


async def verified_source(
    session: AsyncSession,
    *,
    admitted: bool = True,
    resolved: bool = True,
    grade: int = 7,
    subject_name: str = "Mathematics",
    medium_name: str = "Sinhala",
    content: PageUnderstanding | None = None,
) -> tuple[UUID, UUID, UUID]:
    curriculum_id = await add_curriculum(session, medium_name=medium_name)
    curriculum = await session.get(CurriculumVersionModel, curriculum_id)
    assert curriculum is not None
    exam = await session.get(ExamConfigurationModel, curriculum.exam_configuration_id)
    subject = await session.get(SubjectModel, curriculum.subject_id)
    assert exam is not None
    assert subject is not None
    exam.grade = grade
    exam.code = f"G{grade}-" + uuid4().hex[:20].upper()
    exam.name = f"School Grade {grade}"
    subject.code = "SUBJECT-" + uuid4().hex[:20].upper()
    subject.name = subject_name
    curriculum.title = f"{subject_name} curriculum 2026"
    await session.commit()
    if admitted:
        await admit_curriculum(session, curriculum_id)
    document_id = await add_source(session, total=2, curriculum_id=curriculum_id)
    request, result, metadata = await page_input(session, document_id=document_id)
    if content is not None:
        result = result.model_copy(update={"content": content})
    service = PageUnderstandingService(session)
    candidate = await service.record_result(
        principal=ADMIN,
        request_id=uuid4(),
        expected_version=0,
        request=request,
        result=result,
        image_metadata=metadata,
    )
    await approve_page(service, document_id, candidate.id, 1)
    if resolved:
        await service.exclude(
            principal=ADMIN,
            document_id=document_id,
            page_number=2,
            expected_version=0,
            confirm_exclusion=True,
            reason="Unneeded synthetic sibling page",
        )
    page = await service.get_page(principal=ADMIN, document_id=document_id, page_number=1)
    assert page.trusted is not None
    return document_id, page.trusted.id, curriculum_id


def test_prepare_persists_lossless_units_projections_and_exact_region_links(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            result = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            assert result.created
            assert len(result.units) == 2
            assert len(result.projections) == 2
            assert all(unit.scope.curriculum_version_id == curriculum_id for unit in result.units)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitRegionModel)
                    .where(KnowledgeUnitRegionModel.document_id == document_id)
                )
                == 4
            )
            before = await session.scalar(
                select(func.count())
                .select_from(AdminAuditEventModel)
                .where(AdminAuditEventModel.resource_id == document_id)
            )
            replay = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            assert not replay.created
            assert replay.units == result.units
            assert replay.projections == result.projections
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(AdminAuditEventModel.resource_id == document_id)
                )
                == before
            )
            assert (
                await service.list_current_projections(
                    principal=REVIEWER, curriculum_version_id=curriculum_id
                )
                == result.projections
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "gate", ["unresolved_sibling", "unadmitted_scope", "wrong_trusted_version"]
)
def test_prepare_requires_whole_document_resolution_and_current_admitted_scope(
    workspace_database_url: str, gate: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(
                session, admitted=gate != "unadmitted_scope", resolved=gate != "unresolved_sibling"
            )
            with pytest.raises(KnowledgePreparationError):
                await KnowledgeUnitService(session).prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=uuid4()
                    if gate == "wrong_trusted_version"
                    else trusted_id,
                )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 0
            )

    asyncio.run(check())


def test_changed_page_truth_hides_old_units_without_rewriting_them(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            before = tuple(unit.model_dump(mode="json") for unit in prepared.units)
            request, result, metadata = await page_input(session, document_id=document_id)
            await PageUnderstandingService(session).record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=2,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            assert (
                await service.list_current_projections(
                    principal=REVIEWER, curriculum_version_id=curriculum_id
                )
                == ()
            )
            stored = tuple(
                [
                    (await service.get_unit(principal=ADMIN, unit_id=unit.id)).model_dump(
                        mode="json"
                    )
                    for unit in prepared.units
                ]
            )
            assert stored == before
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_unit_is_current(:id)"),
                    {"id": prepared.units[0].id},
                )
                is False
            )

    asyncio.run(check())


def test_knowledge_scope_captures_material_type_year_and_paper_identity(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            document = await session.get(SourceDocumentModel, document_id)
            assert document is not None
            document.year = 2024
            document.paper_code = "P1"
            await session.commit()
            prepared = await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            scope = prepared.units[0].scope
            assert scope.material_type is SourceDocumentType.TEACHER_GUIDE
            assert scope.year == 2024
            assert scope.paper_code == "P1"

    asyncio.run(check())


@pytest.mark.parametrize("field", ["year", "paper_code", "document_type"])
def test_source_descriptor_cannot_be_rewritten_after_knowledge_derivation(
    workspace_database_url: str, field: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            value = {"year": 2024, "paper_code": "P1", "document_type": "syllabus"}[field]
            with pytest.raises(IntegrityError, match=r"knowledge.*metadata"):
                await session.execute(
                    update(SourceDocumentModel)
                    .where(SourceDocumentModel.id == document_id)
                    .values({field: value})
                )
            await session.rollback()

    asyncio.run(check())


def test_material_reclassification_recognizes_derived_knowledge_and_keeps_removal_available(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            other = await add_curriculum(session)
            await admit_curriculum(session, other)
            materials = SourceDocumentService(
                session, Mock(spec=ObjectStorage), max_upload_bytes=1024
            )
            with pytest.raises(MaterialScopeImmutableError):
                await materials.correct_scope(
                    document_id,
                    curriculum_version_id=other,
                    unit_id=None,
                    lesson_id=None,
                    expected_version=0,
                    actor_id=ADMIN.subject_id,
                    confirm_intake_metadata=True,
                )
            await session.rollback()
            await materials.remove_from_ai_use(
                document_id,
                expected_version=0,
                actor_id=ADMIN.subject_id,
                reason="Remove this synthetic material without changing its knowledge history",
            )
            assert (
                await service.list_current_projections(
                    principal=ADMIN, curriculum_version_id=curriculum_id
                )
                == ()
            )
            assert (
                await service.get_unit(principal=ADMIN, unit_id=prepared.units[0].id)
                == prepared.units[0]
            )

    asyncio.run(check())


def test_decorative_components_are_preserved_without_invented_projection_text(
    workspace_database_url: str,
) -> None:
    payload = counting_candidate()
    payload["observation"]["regions"][0].update({"kind": "decorative_image", "exact_text": ""})

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(
                session, content=parse(payload)
            )
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            assert len(prepared.units) == 2
            assert len(prepared.projections) == 1
            assert prepared.unsearchable_unit_ids == (prepared.units[0].id,)
            replay = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            assert replay.unsearchable_unit_ids == prepared.unsearchable_unit_ids
            assert not replay.created

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["missing", "fingerprint"])
def test_unavailable_or_corrupt_trusted_readback_cannot_be_prepared(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            record = await session.get(TrustedPageKnowledgeModel, trusted_id)
            assert record is not None
            original = session.get

            async def missing(model: Any, identity: Any, **kwargs: Any) -> Any:
                if model is TrustedPageKnowledgeModel and identity == trusted_id:
                    return None
                return await original(model, identity, **kwargs)

            if failure == "missing":
                monkeypatch.setattr(session, "get", missing)
            with session.no_autoflush:
                if failure == "fingerprint":
                    record.fingerprint = "f" * 64
                with pytest.raises(KnowledgePreparationError, match="trusted_page"):
                    await KnowledgeUnitService(session).prepare_page(
                        principal=ADMIN,
                        document_id=document_id,
                        page_number=1,
                        expected_trusted_page_id=trusted_id,
                    )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 0
            )

    asyncio.run(check())


def test_incomplete_replay_readback_is_not_silently_repaired(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            scalars = session.scalars

            async def incomplete(statement: Any, **kwargs: Any) -> Any:
                if statement.column_descriptions[0]["entity"] is KnowledgeProjectionModel:
                    return Mock(all=list)
                return await scalars(statement, **kwargs)

            monkeypatch.setattr(session, "scalars", incomplete)
            with pytest.raises(KnowledgePreparationError, match="preparation_conflict"):
                await service.prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 2
            )

    asyncio.run(check())


def test_unit_preparation_is_authorized_and_listing_is_hard_scoped(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            other_document, other_trusted, other_curriculum = await verified_source(
                session, grade=5, subject_name="Science", medium_name="English"
            )
            service = KnowledgeUnitService(session)
            with pytest.raises(AuthorizationError):
                await service.prepare_page(
                    principal=REVIEWER,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )
            first = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            second = await service.prepare_page(
                principal=ADMIN,
                document_id=other_document,
                page_number=1,
                expected_trusted_page_id=other_trusted,
            )
            assert first.units[0].scope.grade == 7
            assert second.units[0].scope.grade == 5
            assert (
                await service.list_current_projections(
                    principal=REVIEWER, curriculum_version_id=curriculum_id
                )
                == first.projections
            )
            assert (
                await service.list_current_projections(
                    principal=REVIEWER, curriculum_version_id=other_curriculum
                )
                == second.projections
            )
            assert (
                await service.list_current_projections(
                    principal=REVIEWER, curriculum_version_id=curriculum_id, limit=1
                )
                == first.projections[:1]
            )
            for limit in (0, 101, True):
                with pytest.raises(KnowledgePreparationError, match="limit"):
                    await service.list_current_projections(
                        principal=ADMIN, curriculum_version_id=curriculum_id, limit=limit
                    )
            with pytest.raises(KnowledgePreparationError, match="not_found"):
                await service.get_unit(principal=ADMIN, unit_id=uuid4())

    asyncio.run(check())


@pytest.mark.parametrize(
    "table", ["knowledge_units", "knowledge_unit_regions", "knowledge_projections"]
)
def test_derived_history_is_immutable(workspace_database_url: str, table: str) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            result = await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            key, identifier = (
                ("unit_id", result.units[0].id)
                if table == "knowledge_unit_regions"
                else (
                    "id",
                    result.projections[0].id
                    if table == "knowledge_projections"
                    else result.units[0].id,
                )
            )
            model = {
                "knowledge_units": KnowledgeUnitModel,
                "knowledge_unit_regions": KnowledgeUnitRegionModel,
                "knowledge_projections": KnowledgeProjectionModel,
            }[table]
            column = getattr(model, key)
            with pytest.raises(IntegrityError, match="append only"):
                await session.execute(
                    update(model).where(column == identifier).values({key: column})
                )
            await session.rollback()
            assert (
                await KnowledgeUnitService(session).get_unit(
                    principal=ADMIN, unit_id=result.units[0].id
                )
                == result.units[0]
            )

    asyncio.run(check())


def test_recomputed_hashes_cannot_smuggle_invented_unit_or_projection_content(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            prepared = await KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            unit = prepared.units[0]
            original = await session.get(KnowledgeUnitModel, unit.id)
            assert original is not None
            audit_id = original.audit_event_id
            payload = unit.model_dump(mode="json", exclude={"id"})
            payload["observation"]["regions"][0]["exact_text"] = "Invented source content"
            digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
            forged = KnowledgeUnit.model_validate_json(
                json.dumps({"id": str(uuid5(trusted_id, "knowledge-unit.v1:" + digest)), **payload})
            )
            session.add(
                KnowledgeUnitModel.from_domain(
                    forged, actor_id=ADMIN.subject_id, audit_event_id=audit_id
                )
            )
            with pytest.raises(IntegrityError, match="exact verified component"):
                await session.flush()
            await session.rollback()
            projection = prepared.projections[0]
            text_value = "Invented retrieval instructions and content"
            text_hash = hashlib.sha256(text_value.encode("utf-8")).hexdigest()
            forged_projection = KnowledgeProjection.model_validate(
                projection.model_copy(
                    update={
                        "id": uuid5(
                            unit.id,
                            projection.transformation_version
                            + ":"
                            + unit.fingerprint
                            + ":"
                            + text_hash,
                        ),
                        "text": text_value,
                        "text_sha256": text_hash,
                    }
                )
            )
            session.add(
                KnowledgeProjectionModel.from_domain(
                    forged_projection, actor_id=ADMIN.subject_id, audit_event_id=audit_id
                )
            )
            with pytest.raises(IntegrityError, match="deterministic rendering"):
                await session.flush()
            await session.rollback()

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["missing_links", "wrong_link", "wrong_audit"])
def test_unit_commit_requires_complete_region_links_and_exact_audit(
    workspace_database_url: str, failure: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            trusted_row = await session.get(TrustedPageKnowledgeModel, trusted_id)
            assert trusted_row is not None
            trusted = TrustedPageKnowledge.model_validate_json(json.dumps(trusted_row.payload))
            scope_payload = await session.scalar(select(func.knowledge_scope_snapshot(document_id)))
            scope = KnowledgeScope.model_validate_json(json.dumps(scope_payload))
            unit = derive_knowledge_units(trusted, scope)[0]
            audit_id = uuid4()
            row = KnowledgeUnitModel.from_domain(
                unit, actor_id=ADMIN.subject_id, audit_event_id=audit_id
            )
            audit = AdminAuditEventModel(
                id=audit_id,
                actor_id=ADMIN.subject_id,
                resource_id=document_id,
                resource_type="verified_knowledge",
                action="verified_knowledge.prepared",
                payload={
                    "page_number": 1,
                    "source_sha256": trusted.source.source_sha256,
                    "trusted_page_id": str(trusted_id),
                    "scope_fingerprint": "f" * 64
                    if failure == "wrong_audit"
                    else row.scope_fingerprint,
                    "unit_ids": [str(unit.id)],
                    "projection_ids": [],
                },
            )
            session.add_all((audit, row))
            await session.flush()
            if failure != "missing_links":
                session.add_all(
                    KnowledgeUnitRegionModel(
                        unit_id=unit.id,
                        region_id=uuid5(unit.candidate_id, "sequence")
                        if failure == "wrong_link"
                        else region_id,
                        candidate_id=unit.candidate_id,
                        document_id=document_id,
                        page_number=1,
                        region_key="sequence" if failure == "wrong_link" else region.key,
                        ordinal=ordinal,
                    )
                    for ordinal, (region_id, region) in enumerate(
                        zip(unit.region_ids, unit.observation.regions, strict=True)
                    )
                )
            message = {
                "missing_links": "all of its region links",
                "wrong_link": "region link differs",
                "wrong_audit": "matching immutable audit",
            }[failure]
            with pytest.raises(IntegrityError, match=message):
                await session.commit()
            await session.rollback()
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 0
            )

    asyncio.run(check())


@pytest.mark.parametrize("change", ["sibling_reopen", "all_excluded"])
def test_a_current_page_never_bypasses_whole_document_resolution(
    workspace_database_url: str, change: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            pages = PageUnderstandingService(session)
            if change == "sibling_reopen":
                await pages.reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=2,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Reopen a different synthetic page",
                )
                assert (
                    await session.scalar(
                        text("SELECT public.trusted_page_knowledge_is_current(:id)"),
                        {"id": trusted_id},
                    )
                    is True
                )
            else:
                await pages.exclude(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_version=2,
                    confirm_exclusion=True,
                    reason="Exclude every synthetic page",
                )
            assert (
                await service.list_current_projections(
                    principal=ADMIN, curriculum_version_id=curriculum_id
                )
                == ()
            )
            assert (
                await service.get_unit(principal=ADMIN, unit_id=prepared.units[0].id)
                == prepared.units[0]
            )
            with pytest.raises(KnowledgePreparationError, match="unresolved"):
                await service.prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )

    asyncio.run(check())


def test_concurrent_preparation_converges_without_duplicate_evidence(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)

        async def prepare() -> PreparedPageKnowledge:
            async with database_session(workspace_database_url) as session:
                return await KnowledgeUnitService(session).prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )

        results = await asyncio.gather(prepare(), prepare())
        assert sorted(result.created for result in results) == [False, True]
        assert results[0].units == results[1].units
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.document_id == document_id)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == document_id,
                        AdminAuditEventModel.action == "verified_knowledge.prepared",
                    )
                )
                == 1
            )

    asyncio.run(check())


def test_preparation_holds_sibling_review_locks_until_its_commit(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
        locked, release, connected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        backend_ids: list[int] = []
        flush = AsyncSession.flush

        async def pause(this: AsyncSession, objects: Any = None) -> None:
            if any(
                isinstance(row, AdminAuditEventModel)
                and row.action == "verified_knowledge.prepared"
                for row in this.new
            ):
                locked.set()
                await asyncio.wait_for(release.wait(), 20)
            await flush(this, objects=objects)

        monkeypatch.setattr(AsyncSession, "flush", pause)

        async def prepare() -> PreparedPageKnowledge:
            async with database_session(workspace_database_url) as session:
                return await KnowledgeUnitService(session).prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )

        async def reopen() -> None:
            async with database_session(workspace_database_url) as session:
                backend_id = await session.scalar(select(func.pg_backend_pid()))
                assert backend_id is not None
                backend_ids.append(int(backend_id))
                connected.set()
                await PageUnderstandingService(session).reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=2,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Concurrent sibling review",
                )

        preparing = asyncio.create_task(prepare())
        await asyncio.wait_for(locked.wait(), 10)
        reopening = asyncio.create_task(reopen())
        try:
            await asyncio.wait_for(connected.wait(), 10)
            async with database_session(workspace_database_url) as observer:

                async def wait_for_lock() -> None:
                    while True:
                        await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        waiting = await observer.scalar(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                            {"pid": backend_ids[0]},
                        )
                        if waiting == "Lock":
                            return
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_for_lock(), 10)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(preparing, reopening), 20)
        async with database_session(workspace_database_url) as session:
            assert (
                await KnowledgeUnitService(session).list_current_projections(
                    principal=ADMIN, curriculum_version_id=curriculum_id
                )
                == ()
            )

    asyncio.run(check())


@pytest.mark.parametrize("shape", ["baseline", "nfc", "parent", "reading_next"])
def test_postgres_and_python_derive_the_same_components_and_projection(
    workspace_database_url: str, shape: str
) -> None:
    payload = counting_candidate()
    if shape == "nfc":
        payload["observation"]["regions"][0]["exact_text"] += " — a\u0301"
    elif shape == "parent":
        payload["observation"]["regions"][1]["parent_key"] = "heading"
    elif shape == "reading_next":
        payload["observation"]["relationships"].append(
            {"source_key": "heading", "target_key": "sequence", "kind": "reading_next"}
        )
    trusted = approve(standalone_candidate().model_copy(update={"content": parse(payload)}))
    units = derive_knowledge_units(trusted, standalone_scope(trusted))

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            keys = await session.scalar(
                text("SELECT public.knowledge_unit_component_keys(CAST(:payload AS jsonb))"),
                {"payload": trusted.model_dump_json()},
            )
            assert keys == [[region.key for region in unit.observation.regions] for unit in units]
            for unit in units:
                rendered = await session.scalar(
                    text("SELECT public.knowledge_projection_text(CAST(:payload AS jsonb))"),
                    {"payload": unit.model_dump_json()},
                )
                assert rendered == project_knowledge_unit(unit).text
            whitespace = "".join(chr(value) for value in range(0x3100) if chr(value).isspace())
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_has_text(:value)"), {"value": whitespace}
                )
                is False
            )
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_has_text(:value)"), {"value": whitespace + "x"}
                )
                is True
            )

    asyncio.run(check())


def test_revoked_admission_cannot_remain_a_projection_scope(workspace_database_url: str) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            review = await get_catalogue_admission(session, curriculum_id)
            await record_catalogue_admission(
                session,
                curriculum_id,
                AdmissionDecisionRequest(
                    state="rejected",
                    expected_version=review.version,
                    expected_scope_fingerprint=review.scope_fingerprint,
                    educational_approval=False,
                    reason="Withdraw synthetic approval",
                    source_reference="Controlled scope test",
                    evidence=("Scope review has been withdrawn",),
                ),
                principal=ADMIN,
            )
            await session.commit()
            assert (
                await service.list_current_projections(
                    principal=ADMIN, curriculum_version_id=curriculum_id
                )
                == ()
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeProjectionModel)
                    .where(
                        KnowledgeProjectionModel.unit_id.in_([unit.id for unit in prepared.units])
                    )
                )
                == 2
            )

    asyncio.run(check())
