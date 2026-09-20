import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from exam_guru_api.documents.understanding_service import PageUnderstandingService
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from exam_guru_api.curriculum.domain import TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    TaxonomyNodeModel,
)
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.unit_models import KnowledgeUnitModel
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from tests.integration.test_knowledge_unit_review_postgres import reviewable_unit
from tests.integration.test_material_knowledge_preparation_api import (
    materials_client as materials_client,
)
from tests.integration.test_material_knowledge_review_api import (
    disabled_configuration,
    material_unit,
    review_body,
)
from tests.integration.workspace_fixtures import (
    ADMIN,
    ADMIN_HEADERS,
    database_session,
)
from tests.integration.workspace_fixtures import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


async def lesson(session: AsyncSession, curriculum_id: UUID) -> tuple[UUID, UUID]:
    parent_id, lesson_id = uuid4(), uuid4()
    ordinal = (
        int(
            await session.scalar(
                select(func.max(CurriculumUnitModel.ordinal)).where(
                    CurriculumUnitModel.curriculum_version_id == curriculum_id
                )
            )
            or 0
        )
        + 1
    )
    session.add(
        CurriculumUnitModel(
            id=parent_id,
            curriculum_version_id=curriculum_id,
            code="UNIT-C" + parent_id.hex[:16].upper(),
            title="Human-readable counting unit",
            ordinal=ordinal,
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
            unit_id=parent_id,
            code="LESSON-C" + lesson_id.hex[:16].upper(),
            title="Human-readable grouping lesson",
            ordinal=1,
            active=True,
            created_by=ADMIN.subject_id,
            updated_by=ADMIN.subject_id,
        )
    )
    await session.commit()
    return parent_id, lesson_id


@pytest.mark.parametrize(
    "invalid",
    [
        "foreign_document",
        "unknown_unit",
        "foreign_taxonomy",
        "draft_taxonomy",
        "inactive_taxonomy",
        "noncurrent_source",
    ],
)
def test_material_review_rejects_foreign_or_unapproved_source_and_curriculum_without_writes(
    materials_client: TestClient,
    workspace_database_url: str,
    invalid: str,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, _projection, competency_id = await material_unit(session)
            if invalid == "foreign_document":
                document_id = (await material_unit(session))[0]
            elif invalid == "foreign_taxonomy":
                competency_id = (await material_unit(session))[3]
            elif invalid in {"draft_taxonomy", "inactive_taxonomy"}:
                taxonomy = await session.get(TaxonomyNodeModel, competency_id)
                assert taxonomy is not None
                if invalid == "draft_taxonomy":
                    competency_id = uuid4()
                    session.add(
                        TaxonomyNodeModel(
                            id=competency_id,
                            curriculum_version_id=taxonomy.curriculum_version_id,
                            level="competency",
                            code="DRAFT-C" + competency_id.hex[:16].upper(),
                            title="Unapproved classification",
                            active=True,
                            review_state=TaxonomyReviewState.DRAFT,
                            created_by=ADMIN.subject_id,
                            updated_by=ADMIN.subject_id,
                        )
                    )
                else:
                    taxonomy.active = False
                    taxonomy.review_state = TaxonomyReviewState.DEPRECATED
                await session.commit()
            elif invalid == "noncurrent_source":
                await PageUnderstandingService(session).reopen(
                    principal=ADMIN,
                    document_id=document_id,
                    page_number=2,
                    expected_version=1,
                    confirm_reopen=True,
                    reason="Reopen a synthetic sibling",
                )
            return document_id, unit_id, competency_id

    document_id, unit_id, competency_id = asyncio.run(seed())
    disabled_configuration(materials_client)
    target = uuid4() if invalid == "unknown_unit" else unit_id
    result = materials_client.post(
        f"/api/v1/admin/materials/{document_id}/knowledge-units/{target}/curriculum-review",
        headers=ADMIN_HEADERS,
        json=review_body(competency_id),
    )
    assert result.status_code == (
        404 if invalid in {"foreign_document", "unknown_unit"} else 409
    ), result.text
    assert result.headers["cache-control"] == "private, no-store"

    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeUnitReviewModel)
                    .where(KnowledgeUnitReviewModel.unit_id == unit_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MaterialKnowledgeIndexIntentModel)
                    .where(MaterialKnowledgeIndexIntentModel.unit_id == unit_id)
                )
                == 0
            )

    asyncio.run(check())


@pytest.mark.parametrize("known", [False, True])
def test_material_review_refines_only_unknown_source_ids_and_uses_readable_titles(
    materials_client: TestClient,
    workspace_database_url: str,
    known: bool,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID, UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            unit_id, _projection, competency = await reviewable_unit(session, scoped=known)
            row = await session.get(KnowledgeUnitModel, unit_id)
            assert row is not None
            unit, topic = await lesson(session, row.curriculum_version_id)
            expected_unit = row.curriculum_unit_id if known else unit
            expected_lesson = row.lesson_id if known else topic
            assert expected_unit is not None
            assert expected_lesson is not None
            return row.document_id, unit_id, competency, expected_unit, expected_lesson, unit, topic

    (
        document_id,
        unit_id,
        competency,
        expected_unit,
        expected_lesson,
        replacement_unit,
        replacement_lesson,
    ) = asyncio.run(seed())
    disabled_configuration(materials_client)
    path = f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}/curriculum-review"
    if known:
        for fields in [
            {},
            {"curriculum_unit_id": str(replacement_unit), "lesson_id": str(replacement_lesson)},
        ]:
            denied = materials_client.post(
                path, headers=ADMIN_HEADERS, json=review_body(competency, **fields)
            )
            assert denied.status_code == 409
    saved = materials_client.post(
        path,
        headers=ADMIN_HEADERS,
        json=review_body(
            competency, curriculum_unit_id=str(expected_unit), lesson_id=str(expected_lesson)
        ),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["workspace"]["review"]["lesson_id"] == str(expected_lesson)
    rows = materials_client.get(
        f"/api/v1/admin/materials/{document_id}/knowledge-units", headers=ADMIN_HEADERS
    )
    assert rows.status_code == 200
    item = next(item for item in rows.json()["items"] if item["unit_id"] == str(unit_id))
    assert item["source_curriculum_unit_id"] == (str(expected_unit) if known else None)
    assert item["source_lesson_id"] == (str(expected_lesson) if known else None)
    assert item["source_unit_title"] == ("Approved source unit" if known else None)
    assert item["source_lesson_title"] == ("Approved source lesson" if known else None)


@pytest.mark.parametrize("foreign", ["unit", "lesson"])
def test_material_mapping_rejects_cross_curriculum_unit_or_lesson(
    materials_client: TestClient,
    workspace_database_url: str,
    foreign: str,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            document_id, unit_id, _projection, competency = await material_unit(session)
            _other_document, other_unit, _other_projection, _other_competency = await material_unit(
                session
            )
            row = await session.get(KnowledgeUnitModel, unit_id)
            other = await session.get(KnowledgeUnitModel, other_unit)
            assert row is not None
            assert other is not None
            own_parent, _own_lesson = await lesson(session, row.curriculum_version_id)
            foreign_parent, foreign_lesson = await lesson(session, other.curriculum_version_id)
            return (
                document_id,
                unit_id,
                competency,
                foreign_parent if foreign == "unit" else own_parent,
                foreign_lesson,
            )

    document_id, unit_id, competency, parent, topic = asyncio.run(seed())
    disabled_configuration(materials_client)
    result = materials_client.post(
        f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}/curriculum-review",
        headers=ADMIN_HEADERS,
        json=review_body(competency, curriculum_unit_id=str(parent), lesson_id=str(topic)),
    )
    assert result.status_code == 409


def test_new_rejection_supersedes_intent_without_indexing_and_strict_json_is_preserved(
    materials_client: TestClient,
    workspace_database_url: str,
) -> None:
    async def seed() -> tuple[UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await material_unit(session)

    document_id, unit_id, _projection, competency = asyncio.run(seed())
    disabled_configuration(materials_client)
    path = f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}"
    invalid_fields: list[dict[str, object]] = [
        {"expected_version": True},
        {"confirmed_mapping": 1},
        {"competency_id": []},
        {"unknown": "value"},
    ]
    for changes in invalid_fields:
        invalid = materials_client.post(
            path + "/curriculum-review",
            headers=ADMIN_HEADERS,
            json={**review_body(competency), **changes},
        )
        assert invalid.status_code == 422
        assert invalid.headers["cache-control"] == "private, no-store"
    saved = materials_client.post(
        path + "/curriculum-review", headers=ADMIN_HEADERS, json=review_body(competency)
    )
    assert saved.status_code == 200
    rejected = materials_client.post(
        path + "/curriculum-review",
        headers=ADMIN_HEADERS,
        json={
            "expected_version": 1,
            "state": "rejected",
            "confirmed_mapping": False,
            "reason": "Withdraw this mapping",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["workspace"]["review"]["version"] == 2
    assert rejected.json()["indexing"]["status"] == "superseded"
    assert rejected.json()["indexing"]["intent_id"] == saved.json()["indexing"]["intent_id"]
    retry = materials_client.post(
        path + "/indexing-retry",
        headers=ADMIN_HEADERS,
        json={
            "expected_version": rejected.json()["indexing"]["version"],
            "reason": "Cannot retry a withdrawn mapping",
            "confirmed_retry": True,
        },
    )
    assert retry.status_code == 409


def test_provider_configuration_is_not_looked_up_until_review_and_intent_commit(
    materials_client: TestClient,
    workspace_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.knowledge import material_review

    async def seed() -> tuple[UUID, UUID, UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            return await material_unit(session)

    document_id, unit_id, _projection, competency = asyncio.run(seed())
    disabled_configuration(materials_client)
    committed: list[bool] = []
    looked_up: list[bool] = []

    def after_commit(_session: Session) -> None:
        committed.append(True)

    def lookup(*_args: Any) -> None:
        assert committed
        looked_up.append(True)

    event.listen(Session, "after_commit", after_commit)
    monkeypatch.setattr(material_review, "material_embedding_config", lookup)
    try:
        result = materials_client.post(
            f"/api/v1/admin/materials/{document_id}/knowledge-units/{unit_id}/curriculum-review",
            headers=ADMIN_HEADERS,
            json=review_body(competency),
        )
        assert result.status_code == 200
        assert committed == [True]
        assert looked_up == [True]
    finally:
        event.remove(Session, "after_commit", after_commit)
