import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes import configuration as configuration_routes
from exam_guru_api.api.routes.configuration import (
    _write,
    create_curriculum_version,
    create_exam_configuration,
    create_medium,
    deactivate_curriculum_version,
    deactivate_exam_configuration,
    deactivate_medium,
    list_curriculum_versions,
    list_exam_configurations,
    list_media,
    update_curriculum_version,
    update_exam_configuration,
    update_medium,
)
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.curriculum.configuration_schemas import (
    CurriculumLessonCreate,
    CurriculumLessonTaxonomyUpdate,
    CurriculumLessonUpdate,
    CurriculumUnitCreate,
    CurriculumUnitUpdate,
    CurriculumVersionCreate,
    CurriculumVersionUpdate,
    ExamConfigurationCreate,
    ExamConfigurationUpdate,
    MediumCreate,
    MediumUpdate,
    SubjectCreate,
    SubjectUpdate,
)
from exam_guru_api.curriculum.configuration_service import (
    ConfigurationInactiveError,
    ConfigurationInUseError,
    ConfigurationNotFoundError,
    ConfigurationScopeMismatchError,
    ConfigurationService,
    LessonConfigurationRecord,
)
from exam_guru_api.curriculum.models import (
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)

ACTOR_ID = UUID(int=1)


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


def models() -> tuple[ExamConfigurationModel, MediumModel, CurriculumVersionModel]:
    now = datetime.now(UTC)
    exam = ExamConfigurationModel(
        id=UUID(int=2),
        code="G5S",
        name="Exam",
        grade=5,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )
    medium = MediumModel(
        id=UUID(int=3),
        code="si",
        name="Sinhala",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )
    curriculum = CurriculumVersionModel(
        id=UUID(int=4),
        exam_configuration_id=exam.id,
        medium_id=medium.id,
        code="2026",
        title="Curriculum",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )
    return exam, medium, curriculum


def test_configuration_route_wrappers_return_typed_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exam, medium, curriculum = models()
    principal = Principal(subject_id=ACTOR_ID, roles=frozenset({AdminRole.ADMIN}))
    session = cast(AsyncSession, RollbackSession())

    async def return_exams(
        _service: ConfigurationService,
    ) -> list[ExamConfigurationModel]:
        return [exam]

    async def return_media(_service: ConfigurationService) -> list[MediumModel]:
        return [medium]

    async def return_curricula(
        _service: ConfigurationService,
    ) -> list[CurriculumVersionModel]:
        return [curriculum]

    async def return_exam(
        _service: ConfigurationService,
        *_args: object,
        **_kwargs: object,
    ) -> ExamConfigurationModel:
        return exam

    async def return_medium(
        _service: ConfigurationService,
        *_args: object,
        **_kwargs: object,
    ) -> MediumModel:
        return medium

    async def return_curriculum(
        _service: ConfigurationService,
        *_args: object,
        **_kwargs: object,
    ) -> CurriculumVersionModel:
        return curriculum

    monkeypatch.setattr(ConfigurationService, "list_exams", return_exams)
    monkeypatch.setattr(ConfigurationService, "list_media", return_media)
    monkeypatch.setattr(ConfigurationService, "list_curricula", return_curricula)
    for method in ("create_exam", "update_exam", "deactivate_exam"):
        monkeypatch.setattr(ConfigurationService, method, return_exam)
    for method in ("create_medium", "update_medium", "deactivate_medium"):
        monkeypatch.setattr(ConfigurationService, method, return_medium)
    for method in ("create_curriculum", "update_curriculum", "deactivate_curriculum"):
        monkeypatch.setattr(ConfigurationService, method, return_curriculum)

    async def exercise() -> list[object]:
        return [
            await list_exam_configurations(principal, session),
            await create_exam_configuration(
                ExamConfigurationCreate(code="G5S", name="Exam", grade=5), principal, session
            ),
            await update_exam_configuration(
                exam.id, ExamConfigurationUpdate(name="Updated"), principal, session
            ),
            await deactivate_exam_configuration(exam.id, principal, session),
            await list_media(principal, session),
            await create_medium(MediumCreate(code="si", name="Sinhala"), principal, session),
            await update_medium(medium.id, MediumUpdate(name="Updated"), principal, session),
            await deactivate_medium(medium.id, principal, session),
            await list_curriculum_versions(principal, session),
            await create_curriculum_version(
                CurriculumVersionCreate(
                    exam_configuration_id=exam.id,
                    medium_id=medium.id,
                    code="2026",
                    title="Curriculum",
                ),
                principal,
                session,
            ),
            await update_curriculum_version(
                curriculum.id,
                CurriculumVersionUpdate(title="Updated"),
                principal,
                session,
            ),
            await deactivate_curriculum_version(curriculum.id, principal, session),
        ]

    responses = asyncio.run(exercise())

    assert len(responses) == 12


def test_normalized_configuration_route_wrappers_return_typed_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    principal = Principal(subject_id=ACTOR_ID, roles=frozenset({AdminRole.ADMIN}))
    session = cast(AsyncSession, RollbackSession())
    curriculum_id = UUID(int=500)
    subject = SubjectModel(
        id=UUID(int=501),
        code="MATHEMATICS",
        name="Mathematics",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )
    unit = CurriculumUnitModel(
        id=UUID(int=502),
        curriculum_version_id=curriculum_id,
        code="UNIT-1",
        title="Numbers",
        ordinal=1,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )
    lesson = LessonConfigurationRecord(
        id=UUID(int=503),
        curriculum_version_id=curriculum_id,
        unit_id=unit.id,
        code="LESSON-1",
        title="Whole numbers",
        ordinal=1,
        active=True,
        taxonomy_node_ids=(),
        created_at=now,
        updated_at=now,
    )

    async def subjects(_service: ConfigurationService) -> list[SubjectModel]:
        return [subject]

    async def units(
        _service: ConfigurationService,
        _curriculum_id: UUID,
    ) -> list[CurriculumUnitModel]:
        return [unit]

    async def lessons(
        _service: ConfigurationService,
        _curriculum_id: UUID,
    ) -> tuple[LessonConfigurationRecord, ...]:
        return (lesson,)

    async def return_subject(
        _service: ConfigurationService,
        *_args: object,
        **_kwargs: object,
    ) -> SubjectModel:
        return subject

    async def return_unit(
        _service: ConfigurationService,
        *_args: object,
        **_kwargs: object,
    ) -> CurriculumUnitModel:
        return unit

    async def return_lesson(
        _service: ConfigurationService,
        *_args: object,
        **_kwargs: object,
    ) -> LessonConfigurationRecord:
        return lesson

    monkeypatch.setattr(ConfigurationService, "list_subjects", subjects)
    monkeypatch.setattr(ConfigurationService, "list_units", units)
    monkeypatch.setattr(ConfigurationService, "list_lessons", lessons)
    for method in ("create_subject", "update_subject", "deactivate_subject"):
        monkeypatch.setattr(ConfigurationService, method, return_subject)
    for method in ("create_unit", "update_unit", "deactivate_unit"):
        monkeypatch.setattr(ConfigurationService, method, return_unit)
    for method in (
        "create_lesson",
        "update_lesson",
        "replace_lesson_taxonomy",
        "deactivate_lesson",
    ):
        monkeypatch.setattr(ConfigurationService, method, return_lesson)

    async def exercise() -> list[object]:
        return [
            await configuration_routes.list_subjects(principal, session),
            await configuration_routes.create_subject(
                SubjectCreate(code="MATHEMATICS", name="Mathematics"), principal, session
            ),
            await configuration_routes.update_subject(
                subject.id, SubjectUpdate(name="Maths"), principal, session
            ),
            await configuration_routes.deactivate_subject(subject.id, principal, session),
            await configuration_routes.list_curriculum_units(curriculum_id, principal, session),
            await configuration_routes.create_curriculum_unit(
                curriculum_id,
                CurriculumUnitCreate(code="UNIT-1", title="Numbers", ordinal=1),
                principal,
                session,
            ),
            await configuration_routes.update_curriculum_unit(
                curriculum_id,
                unit.id,
                CurriculumUnitUpdate(title="Updated"),
                principal,
                session,
            ),
            await configuration_routes.deactivate_curriculum_unit(
                curriculum_id, unit.id, principal, session
            ),
            await configuration_routes.list_curriculum_lessons(curriculum_id, principal, session),
            await configuration_routes.create_curriculum_lesson(
                curriculum_id,
                CurriculumLessonCreate(
                    unit_id=unit.id,
                    code="LESSON-1",
                    title="Whole numbers",
                    ordinal=1,
                ),
                principal,
                session,
            ),
            await configuration_routes.update_curriculum_lesson(
                curriculum_id,
                lesson.id,
                CurriculumLessonUpdate(title="Updated"),
                principal,
                session,
            ),
            await configuration_routes.replace_curriculum_lesson_taxonomy(
                curriculum_id,
                lesson.id,
                CurriculumLessonTaxonomyUpdate(taxonomy_node_ids=()),
                principal,
                session,
            ),
            await configuration_routes.deactivate_curriculum_lesson(
                curriculum_id, lesson.id, principal, session
            ),
        ]

    assert len(asyncio.run(exercise())) == 13


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ConfigurationNotFoundError("exam", UUID(int=1)), 404, "configuration_not_found"),
        (ConfigurationInactiveError("exam", UUID(int=1)), 409, "configuration_inactive"),
        (ConfigurationInUseError("exam", UUID(int=1)), 409, "configuration_in_use"),
        (
            ConfigurationScopeMismatchError("curriculum_unit", UUID(int=1)),
            422,
            "configuration_scope_mismatch",
        ),
    ],
)
def test_configuration_write_maps_domain_errors(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def fail() -> object:
        raise error

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_write(cast(AsyncSession, RollbackSession()), fail))

    assert raised.value.status_code == status_code
    assert cast(dict[str, str], raised.value.detail)["code"] == code


def test_configuration_write_rolls_back_integrity_conflict() -> None:
    session = RollbackSession()

    async def fail() -> object:
        raise IntegrityError("INSERT", {}, RuntimeError("conflict"))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_write(cast(AsyncSession, session), fail))

    assert raised.value.status_code == 409
    assert session.rolled_back
