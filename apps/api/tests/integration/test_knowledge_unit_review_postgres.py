import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.curriculum.domain import TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from exam_guru_api.knowledge.unit_models import KnowledgeUnitModel
from exam_guru_api.knowledge.unit_review import (
    KnowledgeReviewRequest,
    KnowledgeUnitReview,
    KnowledgeUnitReviewError,
    KnowledgeUnitReviewService,
)
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.knowledge.unit_service import KnowledgeUnitService
from tests.integration.test_knowledge_api import ADMIN_HEADERS, REVIEWER_HEADERS, api_client
from tests.integration.test_knowledge_units_postgres import verified_source
from tests.integration.workspace_fixtures import (
    ADMIN,
    REVIEWER,
    add_curriculum,
    database_session,
)
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


async def reviewable_unit(
    session: AsyncSession, *, scoped: bool = False
) -> tuple[UUID, UUID, UUID]:
    document_id, trusted_id, curriculum_id = await verified_source(session)
    if scoped:
        curriculum_unit_id, lesson_id = uuid4(), uuid4()
        session.add(
            CurriculumUnitModel(
                id=curriculum_unit_id,
                curriculum_version_id=curriculum_id,
                code="SOURCE-UNIT",
                title="Approved source unit",
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
                curriculum_version_id=curriculum_id,
                unit_id=curriculum_unit_id,
                code="SOURCE-LESSON",
                title="Approved source lesson",
                ordinal=1,
                active=True,
                created_by=ADMIN.subject_id,
                updated_by=ADMIN.subject_id,
            )
        )
        await session.flush()
        source = await session.get(SourceDocumentModel, document_id)
        assert source is not None
        source.unit_id, source.lesson_id = curriculum_unit_id, lesson_id
        source.metadata_scope_version += 1
        await session.commit()
    prepared = await KnowledgeUnitService(session).prepare_page(
        principal=ADMIN, document_id=document_id, page_number=1, expected_trusted_page_id=trusted_id
    )
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
    return prepared.units[1].id, prepared.projections[1].id, competency_id


def test_unit_review_api_accepts_strict_json_and_preserves_scope_and_privacy(
    workspace_database_url: str,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)
            unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
            return unit_id, competency_id, unit.scope.curriculum_version_id

    unit_id, competency_id, curriculum_id = asyncio.run(seed())
    path = f"/api/v1/admin/curricula/{curriculum_id}/knowledge/units/{unit_id}"
    body = {
        "expected_version": 0,
        "state": "reviewed",
        "confirmed_mapping": True,
        "competency_id": str(competency_id),
        "reason": "Explicit API educational mapping",
    }
    with api_client(workspace_database_url) as client:
        initial = client.get(path, headers=REVIEWER_HEADERS)
        assert initial.status_code == 200
        assert initial.json()["review"] is None
        assert initial.json()["eligible"] is False
        assert initial.headers["cache-control"] == "private, no-store"
        assert (
            client.post(path + "/reviews", headers=REVIEWER_HEADERS, json=body).status_code == 403
        )
        saved = client.post(path + "/reviews", headers=ADMIN_HEADERS, json=body)
        assert saved.status_code == 200
        assert saved.json()["version"] == 1
        assert (
            client.post(path + "/reviews", headers=ADMIN_HEADERS, json=body).json()["id"]
            == saved.json()["id"]
        )
        invalid = client.post(
            path + "/reviews", headers=ADMIN_HEADERS, json={**body, "confirmed_mapping": 1}
        )
        assert invalid.status_code == 422
        assert invalid.headers["cache-control"] == "private, no-store"
        assert (
            client.post(
                path + "/reviews",
                headers=ADMIN_HEADERS,
                json={**body, "reason": "A conflicting stale API request"},
            ).status_code
            == 409
        )
        current = client.get(path, headers=REVIEWER_HEADERS)
        assert current.json()["eligible"] is True
        assert current.json()["review"]["id"] == saved.json()["id"]
        foreign = client.get(
            f"/api/v1/admin/curricula/{uuid4()}/knowledge/units/{unit_id}", headers=ADMIN_HEADERS
        )
        assert foreign.status_code == 404
        assert foreign.headers["cache-control"] == "private, no-store"


def test_projection_requires_explicit_curriculum_review_and_revocation_preserves_history(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, competency_id = await reviewable_unit(session)
            service = KnowledgeUnitReviewService(session)
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is False
            )
            request = KnowledgeReviewRequest(
                expected_version=0,
                state="reviewed",
                confirmed_mapping=True,
                competency_id=competency_id,
                reason="Explicitly reviewed the synthetic educational mapping",
            )
            reviewed = await service.review(principal=ADMIN, unit_id=unit_id, request=request)
            assert reviewed.version == 1
            assert (
                await service.review(principal=ADMIN, unit_id=unit_id, request=request) == reviewed
            )
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is True
            )
            rejected = await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw this synthetic mapping",
                ),
            )
            assert rejected.version == 2
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is False
            )
            assert (await service.get_review(principal=ADMIN, review_id=reviewed.id)) == reviewed

    asyncio.run(check())


def test_knowledge_mapping_review_is_authorized_versioned_and_source_bound(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)
            service = KnowledgeUnitReviewService(session)
            request = KnowledgeReviewRequest(
                expected_version=0,
                state="reviewed",
                confirmed_mapping=True,
                competency_id=competency_id,
                reason="Synthetic explicit mapping",
            )
            with pytest.raises(AuthorizationError):
                await service.review(principal=REVIEWER, unit_id=unit_id, request=request)
            await service.review(principal=ADMIN, unit_id=unit_id, request=request)
            with pytest.raises(KnowledgeUnitReviewError, match="version"):
                await service.review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    request=request.model_copy(update={"reason": "A different stale request"}),
                )

    asyncio.run(check())


def test_unknown_unit_and_review_fail_without_granting_any_authority(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            service = KnowledgeUnitReviewService(session)
            with pytest.raises(KnowledgeUnitReviewError, match="unit_not_found"):
                await service.review(
                    principal=ADMIN,
                    unit_id=uuid4(),
                    request=KnowledgeReviewRequest(
                        expected_version=0,
                        state="rejected",
                        confirmed_mapping=False,
                        reason="Unknown unit cannot be reviewed",
                    ),
                )
            with pytest.raises(KnowledgeUnitReviewError, match="review_not_found"):
                await service.get_review(principal=ADMIN, review_id=uuid4())

    asyncio.run(check())


def test_review_requires_matching_audit_and_rejects_sql_updates(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)
            unit_row = await session.get(KnowledgeUnitModel, unit_id)
            assert unit_row is not None
            unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
            value = KnowledgeUnitReview(
                id=uuid4(),
                unit_id=unit_id,
                unit_fingerprint=unit.fingerprint,
                curriculum_version_id=unit.scope.curriculum_version_id,
                version=1,
                actor_id=ADMIN.subject_id,
                state="reviewed",
                confirmed_mapping=True,
                competency_id=competency_id,
                reason="A different audit must not authorize this mapping",
            )
            session.add(
                KnowledgeUnitReviewModel.from_domain(value, audit_event_id=unit_row.audit_event_id)
            )
            with pytest.raises(IntegrityError, match="exact immutable audit"):
                await session.commit()
            await session.rollback()
            service = KnowledgeUnitReviewService(session)
            reviewed = await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Valid synthetic mapping",
                ),
            )
            with pytest.raises(IntegrityError, match="append only"):
                await session.execute(
                    update(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.id == reviewed.id)
                    .values(reason=reviewed.reason)
                )
            await session.rollback()
            assert await service.get_review(principal=ADMIN, review_id=reviewed.id) == reviewed

    asyncio.run(check())


def test_review_can_refine_an_unassigned_lesson_only_with_current_reviewed_taxonomy(
    workspace_database_url: str,
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
                    code="UNIT1",
                    title="Counting",
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
                    code="LESSON1",
                    title="Equal groups",
                    ordinal=1,
                    active=True,
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
            )
            skill_id, sub_skill_id, concept_id = uuid4(), uuid4(), uuid4()
            for identifier, parent, level in (
                (skill_id, competency_id, "skill"),
                (sub_skill_id, skill_id, "sub_skill"),
                (concept_id, sub_skill_id, "learning_concept"),
            ):
                session.add(
                    TaxonomyNodeModel(
                        id=identifier,
                        parent_id=parent,
                        curriculum_version_id=curriculum_id,
                        level=level,
                        code="NODE-" + uuid4().hex[:16].upper(),
                        title="Reviewed counting scope",
                        active=True,
                        review_state=TaxonomyReviewState.REVIEWED,
                        created_by=ADMIN.subject_id,
                        updated_by=ADMIN.subject_id,
                    )
                )
                await session.flush()
            await session.commit()
            service = KnowledgeUnitReviewService(session)
            reviewed = await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    curriculum_unit_id=curriculum_unit_id,
                    lesson_id=lesson_id,
                    competency_id=competency_id,
                    skill_id=skill_id,
                    sub_skill_id=sub_skill_id,
                    learning_concept_id=concept_id,
                    reason="Explicit same-curriculum lesson and taxonomy mapping",
                ),
            )
            assert reviewed.lesson_id == lesson_id
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is True
            )
            concept = await session.get(TaxonomyNodeModel, concept_id)
            assert concept is not None
            concept.active = False
            concept.review_state = TaxonomyReviewState.DEPRECATED
            await session.commit()
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is False
            )
            assert await service.get_review(principal=REVIEWER, review_id=reviewed.id) == reviewed

    asyncio.run(check())


def test_unit_review_cannot_widen_an_already_approved_source_lesson(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, competency_id = await reviewable_unit(session, scoped=True)
            unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
            service = KnowledgeUnitReviewService(session)
            with pytest.raises(KnowledgeUnitReviewError, match="taxonomy"):
                await service.review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    request=KnowledgeReviewRequest(
                        expected_version=0,
                        state="reviewed",
                        confirmed_mapping=True,
                        competency_id=competency_id,
                        reason="Cannot omit the approved source lesson",
                    ),
                )
            reviewed = await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    curriculum_unit_id=unit.scope.curriculum_unit_id,
                    lesson_id=unit.scope.lesson_id,
                    reason="Keep the exact approved source scope",
                ),
            )
            assert reviewed.lesson_id == unit.scope.lesson_id
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is True
            )

    asyncio.run(check())


def test_new_mapping_approval_requires_current_source_but_withdrawal_remains_available(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, projection_id, competency_id = await reviewable_unit(session)
            unit = await KnowledgeUnitService(session).get_unit(principal=ADMIN, unit_id=unit_id)
            service = KnowledgeUnitReviewService(session)
            await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=0,
                    state="reviewed",
                    confirmed_mapping=True,
                    competency_id=competency_id,
                    reason="Synthetic first mapping",
                ),
            )
            await PageUnderstandingService(session).exclude(
                principal=ADMIN,
                document_id=unit.source.document_id,
                page_number=1,
                expected_version=2,
                confirm_exclusion=True,
                reason="Source withdrawn from use",
            )
            with pytest.raises(KnowledgeUnitReviewError, match="source_not_current"):
                await service.review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    request=KnowledgeReviewRequest(
                        expected_version=1,
                        state="reviewed",
                        confirmed_mapping=True,
                        competency_id=competency_id,
                        reason="Cannot approve withdrawn source",
                    ),
                )
            rejected = await service.review(
                principal=ADMIN,
                unit_id=unit_id,
                request=KnowledgeReviewRequest(
                    expected_version=1,
                    state="rejected",
                    confirmed_mapping=False,
                    reason="Withdraw obsolete educational mapping",
                ),
            )
            assert rejected.version == 2
            assert (
                await session.scalar(
                    text("SELECT public.knowledge_projection_is_eligible(:id)"),
                    {"id": projection_id},
                )
                is False
            )

    asyncio.run(check())


def test_competing_mapping_decisions_cannot_both_claim_one_version(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)

        async def decide(reason: str) -> object:
            async with database_session(workspace_database_url) as session:
                return await KnowledgeUnitReviewService(session).review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    request=KnowledgeReviewRequest(
                        expected_version=0,
                        state="reviewed",
                        confirmed_mapping=True,
                        competency_id=competency_id,
                        reason=reason,
                    ),
                )

        outcomes = await asyncio.gather(
            decide("First synthetic mapping"),
            decide("Second synthetic mapping"),
            return_exceptions=True,
        )
        assert sum(isinstance(value, KnowledgeUnitReviewError) for value in outcomes) == 1
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.unit_id == unit_id)
                )
                == 1
            )

    asyncio.run(check())


@pytest.mark.parametrize("invalid_scope", ["foreign_curriculum", "unreviewed_node"])
def test_unit_mapping_cannot_authorize_foreign_or_unreviewed_taxonomy(
    workspace_database_url: str, invalid_scope: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection_id, competency_id = await reviewable_unit(session)
            if invalid_scope == "foreign_curriculum":
                other_curriculum = await add_curriculum(session)
                competency_id = uuid4()
                session.add(
                    TaxonomyNodeModel(
                        id=competency_id,
                        curriculum_version_id=other_curriculum,
                        parent_id=None,
                        level="competency",
                        code="COUNT-" + uuid4().hex[:16].upper(),
                        title="A different curriculum",
                        active=True,
                        review_state=TaxonomyReviewState.REVIEWED,
                        created_by=ADMIN.subject_id,
                        updated_by=ADMIN.subject_id,
                    )
                )
            else:
                node = await session.get(TaxonomyNodeModel, competency_id)
                assert node is not None
                competency_id = uuid4()
                session.add(
                    TaxonomyNodeModel(
                        id=competency_id,
                        curriculum_version_id=node.curriculum_version_id,
                        parent_id=None,
                        level="competency",
                        code="PENDING-" + uuid4().hex[:16].upper(),
                        title="Unreviewed educational mapping",
                        active=True,
                        review_state=TaxonomyReviewState.DRAFT,
                        created_by=ADMIN.subject_id,
                        updated_by=ADMIN.subject_id,
                    )
                )
            await session.commit()
            with pytest.raises(KnowledgeUnitReviewError, match="taxonomy"):
                await KnowledgeUnitReviewService(session).review(
                    principal=ADMIN,
                    unit_id=unit_id,
                    request=KnowledgeReviewRequest(
                        expected_version=0,
                        state="reviewed",
                        confirmed_mapping=True,
                        competency_id=competency_id,
                        reason="A scope that must not be accepted",
                    ),
                )

    asyncio.run(check())
