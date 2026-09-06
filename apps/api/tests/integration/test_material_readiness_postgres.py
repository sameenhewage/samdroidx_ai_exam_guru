import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, event, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    get_catalogue_admission,
    record_catalogue_admission,
)
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)
from exam_guru_api.documents.fidelity_service import PageFidelityService
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_reading_jobs import queue_source_read
from exam_guru_api.documents.schemas import MaterialStatus
from exam_guru_api.documents.service import SourceDocumentService
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    add_curriculum,
    add_source,
    admit_curriculum,
    confirm_state,
    database_session,
    evidence_counts,
    record_page,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)

pytestmark = pytest.mark.integration


def materials(session: AsyncSession) -> SourceDocumentService:
    return SourceDocumentService(session, cast(ObjectStorage, object()), max_upload_bytes=1024)


async def assert_status(session: AsyncSession, document_id: UUID, expected: MaterialStatus) -> None:
    service = materials(session)
    items = await service.list_materials(document_id=document_id)
    assert [item.id for item in items] == [document_id]
    assert items[0].status is expected
    for status in MaterialStatus:
        filtered = await service.list_materials(document_id=document_id, status=status)
        assert [item.id for item in filtered] == ([document_id] if status is expected else [])


@pytest.mark.parametrize(
    ("proof", "expected"),
    [
        ("verified", MaterialStatus.READY_FOR_AI),
        ("verified_and_excluded", MaterialStatus.READY_FOR_AI),
        ("all_excluded", MaterialStatus.NEEDS_REVIEW),
        ("missing", MaterialStatus.NEEDS_REVIEW),
        ("unreviewed", MaterialStatus.NEEDS_REVIEW),
        ("superseded", MaterialStatus.NEEDS_REVIEW),
        ("unsafe", MaterialStatus.NEEDS_REVIEW),
    ],
)
def test_readiness_requires_all_original_pages_and_one_current_verified_page(
    workspace_database_url: str, proof: str, expected: MaterialStatus
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_source(session, total=2, curriculum_id=curriculum_id)
            if proof == "all_excluded":
                await PageFidelityService(session).exclude_page(
                    document_id,
                    1,
                    expected_version=0,
                    actor_id=ADMIN.subject_id,
                    reason="Original page is blank in this disposable fixture",
                )
            else:
                await confirm_state(session, await record_page(session, document_id, 1))
            if proof in {"all_excluded", "verified_and_excluded"}:
                await PageFidelityService(session).exclude_page(
                    document_id,
                    2,
                    expected_version=0,
                    actor_id=ADMIN.subject_id,
                    reason="Original page is blank in this disposable fixture",
                )
            elif proof != "missing":
                state = await record_page(
                    session,
                    document_id,
                    2,
                    value="broken\ue000text" if proof == "unsafe" else "ගණිතය 3 + 2 = 5",
                )
                if proof in {"verified", "superseded"}:
                    await confirm_state(session, state)
                if proof == "superseded":
                    await PageFidelityService(session).edit_page(
                        document_id,
                        2,
                        text="ගණිතය 3 + 3 = 6",
                        expected_version=state.version,
                        actor_id=ADMIN.subject_id,
                        reason="Explicit correction of the previously verified page",
                    )
            before = await evidence_counts(session)
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await assert_status(session, document_id, expected)
            assert (await materials(session).list_materials(document_id=document_id))[
                0
            ].page_count == 2
            assert await evidence_counts(session) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "obstacle",
    [
        "unreviewed",
        "rejected",
        "quarantined",
        "stale",
        "metadata",
        "curriculum",
        "subject",
        "medium",
        "exam",
    ],
)
def test_verified_pages_do_not_bypass_current_catalogue_or_metadata_admission(
    workspace_database_url: str, obstacle: str
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            if obstacle != "unreviewed":
                await admit_curriculum(session, curriculum_id)
            document_id = await add_source(session, total=1, curriculum_id=curriculum_id)
            await confirm_state(session, await record_page(session, document_id, 1))
            if obstacle in {"rejected", "quarantined"}:
                review = await get_catalogue_admission(session, curriculum_id)
                await record_catalogue_admission(
                    session,
                    curriculum_id,
                    AdmissionDecisionRequest.model_validate(
                        {
                            "state": obstacle,
                            "educational_approval": False,
                            "expected_version": review.version,
                            "expected_scope_fingerprint": review.scope_fingerprint,
                            "reason": "Withheld educational approval in this disposable fixture",
                            "source_reference": "Synthetic integration review register",
                            "evidence": [
                                "Explicit decision, not inference from an actor or filename"
                            ],
                        }
                    ),
                    principal=ADMIN,
                )
            elif obstacle == "stale":
                await session.execute(
                    update(CurriculumVersionModel)
                    .where(CurriculumVersionModel.id == curriculum_id)
                    .values(title="Changed curriculum identity")
                )
            elif obstacle == "metadata":
                await session.execute(
                    update(SourceDocumentModel)
                    .where(SourceDocumentModel.id == document_id)
                    .values(metadata_review_required=True, metadata_scope_version=1)
                )
            elif obstacle in {"curriculum", "subject", "medium", "exam"}:
                curriculum = await session.get(CurriculumVersionModel, curriculum_id)
                assert curriculum is not None
                target: (
                    CurriculumVersionModel
                    | SubjectModel
                    | MediumModel
                    | ExamConfigurationModel
                    | None
                )
                if obstacle == "curriculum":
                    target = curriculum
                elif obstacle == "subject":
                    target = await session.get(SubjectModel, curriculum.subject_id)
                elif obstacle == "medium":
                    target = await session.get(MediumModel, curriculum.medium_id)
                else:
                    target = await session.get(
                        ExamConfigurationModel, curriculum.exam_configuration_id
                    )
                assert target is not None
                target.active = False
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await assert_status(session, document_id, MaterialStatus.NEEDS_REVIEW)

    asyncio.run(scenario())


@pytest.mark.parametrize("job_status", ["queued", "running", "completed", "failed"])
def test_only_active_read_jobs_override_current_page_readiness(
    workspace_database_url: str, job_status: str
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_source(session, total=1, curriculum_id=curriculum_id)
            await confirm_state(session, await record_page(session, document_id, 1))
            job = await queue_source_read(session, document_id, actor_id=ADMIN.subject_id)
            job.status = job_status
            await session.commit()
            expected = (
                MaterialStatus.PROCESSING
                if job_status in {"queued", "running"}
                else MaterialStatus.READY_FOR_AI
            )
            await assert_status(session, document_id, expected)
            await materials(session).remove_from_ai_use(
                document_id,
                reason="Removed from future use",
                expected_version=0,
                actor_id=ADMIN.subject_id,
            )
            await assert_status(session, document_id, MaterialStatus.REMOVED)

    asyncio.run(scenario())


def test_readiness_is_source_scoped_and_original_page_count_wins_over_legacy_count(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            ready_id = await add_source(session, total=1, curriculum_id=curriculum_id)
            await confirm_state(session, await record_page(session, ready_id, 1))
            partial_id = await add_source(
                session,
                total=3,
                curriculum_id=curriculum_id,
                extracted_count=1,
                legacy_trusted=True,
            )
            other_curriculum_id = await add_curriculum(session, medium_name="English")
            unapproved_id = await add_source(session, total=1, curriculum_id=other_curriculum_id)
            await confirm_state(session, await record_page(session, unapproved_id, 1))
            unknown_id = await add_source(
                session,
                total=None,
                curriculum_id=curriculum_id,
                extracted_count=1,
                legacy_trusted=True,
            )
            await assert_status(session, ready_id, MaterialStatus.READY_FOR_AI)
            for identifier in (partial_id, unapproved_id, unknown_id):
                await assert_status(session, identifier, MaterialStatus.NEEDS_REVIEW)
            assert (await materials(session).list_materials(document_id=partial_id))[
                0
            ].page_count == 3
            assert (await materials(session).list_materials(document_id=unknown_id))[
                0
            ].page_count == 1

    asyncio.run(scenario())


def test_material_reads_do_not_autoflush_unrelated_pending_changes(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            curriculum_id = await add_curriculum(session)
            await admit_curriculum(session, curriculum_id)
            document_id = await add_source(session, total=1, curriculum_id=curriculum_id)
            await confirm_state(session, await record_page(session, document_id, 1))
            before = await evidence_counts(session)
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            pending = AdminAuditEventModel(
                id=uuid4(),
                actor_id=ADMIN.subject_id,
                resource_type="source_document",
                resource_id=document_id,
                action="test.pending",
                payload={},
            )
            session.add(pending)
            await assert_status(session, document_id, MaterialStatus.READY_FOR_AI)
            await materials(session).grade_summary()
            assert pending in session.new
            with session.no_autoflush:
                assert await evidence_counts(session) == before

    asyncio.run(scenario())


def test_ready_material_filters_keep_grade_subject_and_medium_boundaries(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            first_id = await add_curriculum(session)
            second_id = await add_curriculum(session, medium_name="English")
            first = await session.get(CurriculumVersionModel, first_id)
            second = await session.get(CurriculumVersionModel, second_id)
            assert first is not None
            assert second is not None
            second_exam = await session.get(ExamConfigurationModel, second.exam_configuration_id)
            assert second_exam is not None
            second_exam.grade = 8
            await session.commit()
            await admit_curriculum(session, first_id)
            await admit_curriculum(session, second_id)
            first_source = await add_source(session, total=1, curriculum_id=first_id)
            second_source = await add_source(session, total=1, curriculum_id=second_id)
            for identifier in (first_source, second_source):
                await confirm_state(session, await record_page(session, identifier, 1))
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            for grade, subject, medium, expected in (
                (7, first.subject_id, first.medium_id, [first_source]),
                (8, second.subject_id, second.medium_id, [second_source]),
                (8, first.subject_id, first.medium_id, []),
                (7, first.subject_id, second.medium_id, []),
                (7, second.subject_id, first.medium_id, []),
            ):
                rows = await materials(session).list_materials(
                    grade=grade,
                    subject_id=subject,
                    medium_id=medium,
                    status=MaterialStatus.READY_FOR_AI,
                )
                assert [row.id for row in rows] == expected

    asyncio.run(scenario())


def test_list_filters_summary_and_pagination_share_one_read_only_status_without_n_plus_one(
    workspace_database_url: str,
) -> None:
    async def scenario() -> None:
        async with database_session(workspace_database_url) as session:
            before = next(
                item for item in await materials(session).grade_summary() if item.grade == 7
            )
            curriculum_id = await add_curriculum(session)
            curriculum = await session.get(CurriculumVersionModel, curriculum_id)
            assert curriculum is not None
            await admit_curriculum(session, curriculum_id)
            ready_ids = []
            for _ in range(8):
                identifier = await add_source(session, total=1, curriculum_id=curriculum_id)
                await confirm_state(session, await record_page(session, identifier, 1))
                ready_ids.append(identifier)
            needs_review = await add_source(session, total=1, curriculum_id=curriculum_id)
            processing = await add_source(session, total=1, curriculum_id=curriculum_id)
            await queue_source_read(session, processing, actor_id=ADMIN.subject_id)
            removed = await add_source(session, total=1, curriculum_id=curriculum_id)
            await materials(session).remove_from_ai_use(
                removed,
                reason="Removed from future use",
                expected_version=0,
                actor_id=ADMIN.subject_id,
            )
            await session.commit()
            await session.execute(text("SET TRANSACTION READ ONLY"))
            statements: list[str] = []

            def capture(
                _connection: Connection,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                statements.append(statement)

            engine = session.bind
            assert isinstance(engine, AsyncEngine)
            event.listen(engine.sync_engine, "before_cursor_execute", capture)
            try:
                items = await materials(session).list_materials(subject_id=curriculum.subject_id)
                assert len(statements) == 1
                by_id = {item.id: item.status for item in items}
                assert {
                    identifier
                    for identifier, status in by_id.items()
                    if status is MaterialStatus.READY_FOR_AI
                } == set(ready_ids)
                assert by_id[needs_review] is MaterialStatus.NEEDS_REVIEW
                assert by_id[processing] is MaterialStatus.PROCESSING
                assert by_id[removed] is MaterialStatus.REMOVED
                statements.clear()
                page = await materials(session).list_materials(
                    subject_id=curriculum.subject_id,
                    status=MaterialStatus.READY_FOR_AI,
                    offset=2,
                    limit=3,
                )
                assert [item.id for item in page] == list(reversed(ready_ids))[2:5]
                assert len(statements) == 1
                statements.clear()
                summary = next(
                    item for item in await materials(session).grade_summary() if item.grade == 7
                )
                assert len(statements) == 1
                assert summary.material_count - before.material_count == 11
                assert summary.ready_count - before.ready_count == 8
                assert summary.needs_review_count - before.needs_review_count == 1
                assert summary.processing_count - before.processing_count == 1
                assert summary.removed_count - before.removed_count == 1
                assert not any("FOR UPDATE" in sql or "FOR SHARE" in sql for sql in statements)
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", capture)

    asyncio.run(scenario())
