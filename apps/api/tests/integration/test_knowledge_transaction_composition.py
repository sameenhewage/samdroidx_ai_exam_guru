import asyncio
from itertools import count
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.admission import CatalogueApprovalError, get_catalogue_admission
from exam_guru_api.curriculum.domain import TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    ExamConfigurationModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.knowledge.unit_models import (
    KnowledgeProjectionModel,
    KnowledgeUnitModel,
    KnowledgeUnitRegionModel,
)
from exam_guru_api.knowledge.unit_review import (
    KnowledgeReviewRequest,
    KnowledgeUnitReviewError,
    KnowledgeUnitReviewService,
)
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.knowledge.unit_service import KnowledgePreparationError, KnowledgeUnitService
from tests.integration.test_knowledge_unit_review_postgres import reviewable_unit
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.workspace_fixtures import (
    ADMIN,
    REVIEWER,
    add_curriculum,
    admit_curriculum,
    database_session,
)
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


async def _pending_caller_work(session: AsyncSession) -> UUID:
    identifier = uuid4()
    session.add(
        AdminAuditEventModel(
            id=identifier,
            actor_id=ADMIN.subject_id,
            resource_type="transaction_probe",
            resource_id=identifier,
            action="transaction_probe.pending",
            payload={},
        )
    )
    await session.flush()
    return identifier


async def _preparation_counts(
    session: AsyncSession, document_id: UUID
) -> tuple[int, int, int, int]:
    unit_ids = select(KnowledgeUnitModel.id).where(KnowledgeUnitModel.document_id == document_id)
    with session.no_autoflush:
        counts = (
            await session.execute(
                select(
                    select(func.count()).select_from(unit_ids.subquery()).scalar_subquery(),
                    select(func.count())
                    .select_from(KnowledgeProjectionModel)
                    .where(KnowledgeProjectionModel.unit_id.in_(unit_ids))
                    .scalar_subquery(),
                    select(func.count())
                    .select_from(KnowledgeUnitRegionModel)
                    .where(KnowledgeUnitRegionModel.document_id == document_id)
                    .scalar_subquery(),
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == document_id,
                        AdminAuditEventModel.action == "verified_knowledge.prepared",
                    )
                    .scalar_subquery(),
                )
            )
        ).one()
    return int(counts[0]), int(counts[1]), int(counts[2]), int(counts[3])


async def _review_counts(session: AsyncSession, unit_id: UUID) -> tuple[int, int]:
    with session.no_autoflush:
        counts = (
            await session.execute(
                select(
                    select(func.count())
                    .select_from(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.unit_id == unit_id)
                    .scalar_subquery(),
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == unit_id,
                        AdminAuditEventModel.resource_type == "knowledge_unit_review",
                    )
                    .scalar_subquery(),
                )
            )
        ).one()
    return int(counts[0]), int(counts[1])


def _mapping_request(competency_id: UUID) -> KnowledgeReviewRequest:
    return KnowledgeReviewRequest(
        expected_version=0,
        state="reviewed",
        confirmed_mapping=True,
        competency_id=competency_id,
        reason="Explicit synthetic educational mapping",
    )


@pytest.mark.parametrize("caller_commits", [False, True], ids=["rollback", "commit"])
def test_prepare_and_group_review_leave_completion_to_caller(
    workspace_database_url: str, caller_commits: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, curriculum_id = await verified_source(session)
            competency_id = uuid4()
            session.add(
                TaxonomyNodeModel(
                    id=competency_id,
                    curriculum_version_id=curriculum_id,
                    parent_id=None,
                    level="competency",
                    code="COUNT-" + uuid4().hex[:16].upper(),
                    title="Counting groups",
                    active=True,
                    review_state=TaxonomyReviewState.REVIEWED,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            await session.commit()
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            assert transaction is not None
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
                commit=False,
            )
            assert prepared.created
            assert session.get_transaction() is transaction
            assert transaction.is_active
            assert not session.new
            assert await _preparation_counts(session, document_id) == (2, 2, 4, 1)
            replay = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
                commit=False,
            )
            assert not replay.created
            assert replay.units == prepared.units
            assert replay.projections == prepared.projections
            with pytest.raises(KnowledgePreparationError, match="trusted_page_changed"):
                await service.prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=uuid4(),
                    commit=False,
                )
            assert session.get_transaction() is transaction
            assert transaction.is_active
            assert await _preparation_counts(session, document_id) == (2, 2, 4, 1)
            reviews = [
                await KnowledgeUnitReviewService(session).review(
                    principal=ADMIN,
                    unit_id=unit.id,
                    curriculum_version_id=curriculum_id,
                    request=_mapping_request(competency_id),
                    commit=False,
                )
                for unit in prepared.units
            ]
            assert session.get_transaction() is transaction
            assert transaction.is_active
            assert not session.new
            for unit in prepared.units:
                assert await _review_counts(session, unit.id) == (1, 1)
            async with database_session(workspace_database_url) as observer:
                assert await _preparation_counts(observer, document_id) == (0, 0, 0, 0)
                assert await observer.get(AdminAuditEventModel, marker_id) is None
                for unit in prepared.units:
                    assert await _review_counts(observer, unit.id) == (0, 0)
            if caller_commits:
                await session.commit()
            else:
                await session.rollback()
        async with database_session(workspace_database_url) as observer:
            assert await _preparation_counts(observer, document_id) == (
                (2, 2, 4, 1) if caller_commits else (0, 0, 0, 0)
            )
            assert (
                await observer.get(AdminAuditEventModel, marker_id) is not None
            ) == caller_commits
            for reviewed in reviews:
                assert await _review_counts(observer, reviewed.unit_id) == (
                    (1, 1) if caller_commits else (0, 0)
                )
                if caller_commits:
                    assert (
                        await KnowledgeUnitReviewService(observer).get_review(
                            principal=REVIEWER, review_id=reviewed.id
                        )
                        == reviewed
                    )

    asyncio.run(check())


@pytest.mark.parametrize("caller_commits", [False, True], ids=["rollback", "commit"])
def test_reviewed_lesson_refinement_is_flushed_but_owned_by_caller(
    workspace_database_url: str, caller_commits: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, competency_id = await reviewable_unit(session)
            unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
            curriculum_id = unit.scope.curriculum_version_id
            curriculum_unit_id, lesson_id = uuid4(), uuid4()
            session.add(
                CurriculumUnitModel(
                    id=curriculum_unit_id,
                    curriculum_version_id=curriculum_id,
                    code="REFINED-UNIT",
                    title="Reviewed counting unit",
                    ordinal=1,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            await session.flush()
            session.add(
                CurriculumLessonModel(
                    id=lesson_id,
                    unit_id=curriculum_unit_id,
                    curriculum_version_id=curriculum_id,
                    code="REFINED-LESSON",
                    title="Reviewed counting lesson",
                    ordinal=1,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            await session.commit()
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            assert transaction is not None
            request = _mapping_request(competency_id).model_copy(
                update={"curriculum_unit_id": curriculum_unit_id, "lesson_id": lesson_id}
            )
            service = KnowledgeUnitReviewService(session)
            reviewed = await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                curriculum_version_id=curriculum_id,
                request=request,
                commit=False,
            )
            assert session.get_transaction() is transaction
            assert transaction.is_active
            assert not session.new
            with session.no_autoflush:
                row = await session.get(KnowledgeUnitReviewModel, reviewed.id)
                assert row is not None
                audit = await session.get(AdminAuditEventModel, row.audit_event_id)
                assert audit is not None
                assert audit.payload == reviewed.model_dump(mode="json")
                assert audit.actor_id == ADMIN.subject_id
                assert (
                    await session.scalar(
                        select(func.knowledge_projection_is_eligible(projection_id))
                    )
                    is True
                )
            assert (
                await service.review(
                    principal=ADMIN, unit_id=unit_id, request=request, commit=False
                )
                == reviewed
            )
            with pytest.raises(KnowledgeUnitReviewError, match="knowledge_review_version_conflict"):
                await service.review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    request=request.model_copy(update={"reason": "A different stale mapping"}),
                    commit=False,
                )
            assert session.get_transaction() is transaction
            assert transaction.is_active
            assert await _review_counts(session, unit_id) == (1, 1)
            async with database_session(workspace_database_url) as observer:
                assert await _review_counts(observer, unit_id) == (0, 0)
                assert await observer.get(AdminAuditEventModel, marker_id) is None
            if caller_commits:
                await session.commit()
            else:
                await session.rollback()
        async with database_session(workspace_database_url) as observer:
            assert await _review_counts(observer, unit_id) == ((1, 1) if caller_commits else (0, 0))
            assert (
                await observer.get(AdminAuditEventModel, marker_id) is not None
            ) == caller_commits
            workspace = await KnowledgeUnitReviewService(observer).get_workspace(
                principal=REVIEWER, unit_id=unit_id, curriculum_version_id=curriculum_id
            )
            assert workspace.source_current
            assert workspace.eligible == caller_commits
            assert workspace.unit.scope.lesson_id is None
            assert workspace.review == (reviewed if caller_commits else None)

    asyncio.run(check())


@pytest.mark.parametrize("caller_owned", [False, True], ids=["public-default", "caller-owned"])
def test_prepare_replay_commits_unrelated_work_only_by_public_default(
    workspace_database_url: str, caller_owned: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            service = KnowledgeUnitService(session)
            prepared = await service.prepare_page(
                principal=ADMIN,
                document_id=document_id,
                page_number=1,
                expected_trusted_page_id=trusted_id,
            )
            assert not session.in_transaction()
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            if caller_owned:
                replay = await service.prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                    commit=False,
                )
                assert session.get_transaction() is transaction
            else:
                replay = await service.prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                )
                assert not session.in_transaction()
            assert not replay.created
            assert replay.units == prepared.units
            await session.rollback()
        async with database_session(workspace_database_url) as observer:
            assert (await observer.get(AdminAuditEventModel, marker_id) is None) == caller_owned
            assert await _preparation_counts(observer, document_id) == (2, 2, 4, 1)

    asyncio.run(check())


@pytest.mark.parametrize("caller_owned", [False, True], ids=["public-default", "caller-owned"])
def test_review_replay_commits_unrelated_work_only_by_public_default(
    workspace_database_url: str, caller_owned: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)
            service = KnowledgeUnitReviewService(session)
            request = _mapping_request(competency_id)
            reviewed = await service.review(principal=ADMIN, unit_id=unit_id, request=request)
            assert not session.in_transaction()
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            if caller_owned:
                replay = await service.review(
                    principal=ADMIN, unit_id=unit_id, request=request, commit=False
                )
                assert session.get_transaction() is transaction
            else:
                replay = await service.review(principal=ADMIN, unit_id=unit_id, request=request)
                assert not session.in_transaction()
            assert replay == reviewed
            await session.rollback()
        async with database_session(workspace_database_url) as observer:
            assert (await observer.get(AdminAuditEventModel, marker_id) is None) == caller_owned
            assert await _review_counts(observer, unit_id) == (1, 1)

    asyncio.run(check())


@pytest.mark.parametrize("commit", [False, True], ids=["caller-owned", "service-owned"])
@pytest.mark.parametrize(
    ("gate", "error"),
    [
        ("unresolved_sibling", "source_document_unresolved"),
        ("unadmitted_scope", "knowledge_scope_unavailable"),
        ("wrong_trusted_version", "trusted_page_changed"),
    ],
)
def test_prepare_domain_errors_preserve_only_caller_owned_transactions(
    workspace_database_url: str, commit: bool, gate: str, error: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(
                session, admitted=gate != "unadmitted_scope", resolved=gate != "unresolved_sibling"
            )
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            assert transaction is not None
            with pytest.raises(KnowledgePreparationError, match=error):
                await KnowledgeUnitService(session).prepare_page(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=uuid4()
                    if gate == "wrong_trusted_version"
                    else trusted_id,
                    commit=commit,
                )
            if commit:
                assert not session.in_transaction()
            else:
                assert session.get_transaction() is transaction
                assert transaction.is_active
                assert await session.get(AdminAuditEventModel, marker_id) is not None
            async with database_session(workspace_database_url) as observer:
                assert await observer.get(AdminAuditEventModel, marker_id) is None
            await session.commit()
        async with database_session(workspace_database_url) as observer:
            assert (await observer.get(AdminAuditEventModel, marker_id) is None) == commit
            assert await _preparation_counts(observer, document_id) == (0, 0, 0, 0)

    asyncio.run(check())


@pytest.mark.parametrize("commit", [False, True], ids=["caller-owned", "service-owned"])
@pytest.mark.parametrize(
    ("gate", "error"),
    [
        ("version", "knowledge_review_version_conflict"),
        ("curriculum", "knowledge_unit_not_found"),
        ("source", "knowledge_review_source_not_current"),
        ("taxonomy", "knowledge_review_taxonomy_invalid"),
        ("widening", "knowledge_review_taxonomy_invalid"),
    ],
)
def test_review_domain_errors_preserve_only_caller_owned_transactions(
    workspace_database_url: str, commit: bool, gate: str, error: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(
                session, scoped=gate == "widening"
            )
            unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
            if gate == "source":
                await PageUnderstandingService(session).exclude(
                    principal=ADMIN,
                    document_id=unit.source.document_id,
                    page_number=1,
                    expected_version=2,
                    confirm_exclusion=True,
                    reason="Withdraw this synthetic source",
                )
            request = _mapping_request(competency_id).model_copy(
                update={
                    "expected_version": 1 if gate == "version" else 0,
                    "competency_id": uuid4() if gate == "taxonomy" else competency_id,
                }
            )
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            assert transaction is not None
            with pytest.raises(KnowledgeUnitReviewError, match=error):
                await KnowledgeUnitReviewService(session).review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    curriculum_version_id=uuid4()
                    if gate == "curriculum"
                    else unit.scope.curriculum_version_id,
                    request=request,
                    commit=commit,
                )
            if commit:
                assert not session.in_transaction()
            else:
                assert session.get_transaction() is transaction
                assert transaction.is_active
                assert await session.get(AdminAuditEventModel, marker_id) is not None
            async with database_session(workspace_database_url) as observer:
                assert await observer.get(AdminAuditEventModel, marker_id) is None
            await session.commit()
        async with database_session(workspace_database_url) as observer:
            assert (await observer.get(AdminAuditEventModel, marker_id) is None) == commit
            assert await _review_counts(observer, unit_id) == (0, 0)

    asyncio.run(check())


def test_noncommitting_prepare_still_requires_knowledge_write(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            document_id, trusted_id, _curriculum_id = await verified_source(session)
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            with pytest.raises(AuthorizationError):
                await KnowledgeUnitService(session).prepare_page(
                    principal=REVIEWER,
                    document_id=document_id,
                    page_number=1,
                    expected_trusted_page_id=trusted_id,
                    commit=False,
                )
            assert session.get_transaction() is transaction
            await session.commit()
        async with database_session(workspace_database_url) as observer:
            assert await observer.get(AdminAuditEventModel, marker_id) is not None
            assert await _preparation_counts(observer, document_id) == (0, 0, 0, 0)

    asyncio.run(check())


def test_noncommitting_review_still_requires_knowledge_write(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)
            marker_id = await _pending_caller_work(session)
            transaction = session.get_transaction()
            with pytest.raises(AuthorizationError):
                await KnowledgeUnitReviewService(session).review(
                    principal=REVIEWER,
                    unit_id=unit_id,
                    request=_mapping_request(competency_id),
                    commit=False,
                )
            assert session.get_transaction() is transaction
            await session.commit()
        async with database_session(workspace_database_url) as observer:
            assert await observer.get(AdminAuditEventModel, marker_id) is not None
            assert await _review_counts(observer, unit_id) == (0, 0)

    asyncio.run(check())


def test_shared_curriculum_fixture_uses_valid_codes_for_e2e_uuid_prefixes(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sequence = count(100)

    def e2e_uuid4() -> UUID:
        return UUID(f"e2e{next(sequence):05x}-0000-4000-8000-000000000000")

    monkeypatch.setattr("tests.integration.workspace_fixtures.uuid4", e2e_uuid4)

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            admission = await get_catalogue_admission(session, curriculum_id)
            assert admission.admitted
            assert admission.scope.exam_configuration_code.startswith("G7-CE2E")
            assert admission.scope.subject_code.startswith("MATHS-CE2E")

    asyncio.run(check())


def test_verified_source_fixture_identifiers_admit_unique_e2e_uuid_prefixes(
    workspace_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sequence = count(1)
    generated: list[UUID] = []

    def e2e_uuid4() -> UUID:
        identifier = UUID(f"e2e{next(sequence):05x}-0000-4000-8000-000000000000")
        generated.append(identifier)
        return identifier

    monkeypatch.setattr("tests.integration.test_knowledge_units_postgres.uuid4", e2e_uuid4)

    async def check() -> None:
        exam_codes: set[str] = set()
        subject_codes: set[str] = set()
        async with database_session(workspace_database_url) as session:
            for _ in range(2):
                _document_id, _trusted_id, curriculum_id = await verified_source(session)
                admission = await get_catalogue_admission(session, curriculum_id)
                assert admission.admitted
                assert admission.labels_approvable
                assert admission.scope.exam_configuration_code.startswith("G7-CE2E")
                assert admission.scope.subject_code.startswith("SUBJECT-CE2E")
                exam_codes.add(admission.scope.exam_configuration_code)
                subject_codes.add(admission.scope.subject_code)
        assert len(exam_codes) == 2
        assert len(subject_codes) == 2

    asyncio.run(check())
    assert len(generated) >= 4
    assert len(generated) == len(set(generated))
    assert all(
        identifier.hex.startswith("e2e") and identifier.version == 4 for identifier in generated
    )


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("exam", "G7-E2E0001"),
        ("subject", "SUBJECT-E2E0001"),
        ("exam", "G7-C-E2E0001"),
        ("subject", "SUBJECT-C-E2E0001"),
    ],
)
def test_catalogue_still_rejects_explicit_reserved_e2e_fixture_labels(
    workspace_database_url: str, field: str, code: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            _document_id, _trusted_id, curriculum_id = await verified_source(
                session, admitted=False
            )
            admission = await get_catalogue_admission(session, curriculum_id)
            record: ExamConfigurationModel | SubjectModel | None
            if field == "exam":
                record = await session.get(
                    ExamConfigurationModel, admission.scope.exam_configuration_id
                )
            else:
                record = await session.get(SubjectModel, admission.scope.subject_id)
            assert record is not None
            record.code = code
            await session.commit()
            with pytest.raises(CatalogueApprovalError, match="catalogue_labels_not_approvable"):
                await admit_curriculum(session, curriculum_id)
            await session.rollback()
            rejected = await get_catalogue_admission(session, curriculum_id)
            assert not rejected.admitted
            assert not rejected.labels_approvable
            assert rejected.latest_decision is None

    asyncio.run(check())
