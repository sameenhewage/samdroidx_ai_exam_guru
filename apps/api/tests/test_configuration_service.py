import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.configuration_schemas import (
    CurriculumVersionCreate,
    ExamConfigurationCreate,
    MediumCreate,
)
from exam_guru_api.curriculum.configuration_service import (
    ConfigurationInactiveError,
    ConfigurationInUseError,
    ConfigurationNotFoundError,
    ConfigurationService,
)
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
)

ACTOR_ID = UUID(int=1)


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class StubSession:
    def __init__(self) -> None:
        self.objects: dict[tuple[type[object], UUID], object] = {}
        self.added: list[object] = []
        self.scalar_rows: list[list[object]] = []
        self.scalar_values: list[bool] = []
        self.commits = 0

    def add(self, model: object) -> None:
        self.added.append(model)
        identifier = getattr(model, "id", None)
        if isinstance(identifier, UUID) and not isinstance(model, AdminAuditEventModel):
            self.objects[(type(model), identifier)] = model

    async def get(
        self,
        model_type: type[object],
        resource_id: UUID,
        **_kwargs: object,
    ) -> object | None:
        return self.objects.get((model_type, resource_id))

    async def scalars(self, _query: object) -> ScalarRows:
        return ScalarRows(self.scalar_rows.pop(0))

    async def scalar(self, _query: object) -> bool:
        return self.scalar_values.pop(0)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _model: object) -> None:
        return None


def active_exam(identifier: int = 10) -> ExamConfigurationModel:
    now = datetime.now(UTC)
    return ExamConfigurationModel(
        id=UUID(int=identifier),
        code=f"G5S-{identifier}",
        name="Grade 5 Scholarship",
        grade=5,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )


def active_medium(identifier: int = 20) -> MediumModel:
    now = datetime.now(UTC)
    return MediumModel(
        id=UUID(int=identifier),
        code=f"m{identifier}",
        name="Medium",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )


def test_configuration_service_create_list_update_and_deactivate() -> None:
    session = StubSession()
    service = ConfigurationService(cast(AsyncSession, session))

    exam = asyncio.run(
        service.create_exam(
            ExamConfigurationCreate(code="G5S-2026", name="Grade 5 Scholarship", grade=5),
            actor_id=ACTOR_ID,
        )
    )
    medium = asyncio.run(
        service.create_medium(MediumCreate(code="si", name="Sinhala"), actor_id=ACTOR_ID)
    )
    curriculum = asyncio.run(
        service.create_curriculum(
            CurriculumVersionCreate(
                exam_configuration_id=exam.id,
                medium_id=medium.id,
                code="2026-V1",
                title="Curriculum",
            ),
            actor_id=ACTOR_ID,
        )
    )

    session.scalar_rows = [[exam], [medium], [curriculum]]
    assert asyncio.run(service.list_exams()) == [exam]
    assert asyncio.run(service.list_media()) == [medium]
    assert asyncio.run(service.list_curricula()) == [curriculum]

    assert (
        asyncio.run(service.update_exam(exam.id, "Updated exam", actor_id=ACTOR_ID)).name
        == "Updated exam"
    )
    assert (
        asyncio.run(service.update_medium(medium.id, "Updated medium", actor_id=ACTOR_ID)).name
        == "Updated medium"
    )
    assert (
        asyncio.run(
            service.update_curriculum(curriculum.id, "Updated curriculum", actor_id=ACTOR_ID)
        ).title
        == "Updated curriculum"
    )

    session.scalar_values = [False, False, False]
    assert not asyncio.run(service.deactivate_curriculum(curriculum.id, actor_id=ACTOR_ID)).active
    assert not asyncio.run(service.deactivate_curriculum(curriculum.id, actor_id=ACTOR_ID)).active
    assert not asyncio.run(service.deactivate_exam(exam.id, actor_id=ACTOR_ID)).active
    assert not asyncio.run(service.deactivate_medium(medium.id, actor_id=ACTOR_ID)).active
    assert session.commits == 9
    assert any(isinstance(model, AdminAuditEventModel) for model in session.added)


def test_configuration_service_errors_are_explicit() -> None:
    session = StubSession()
    service = ConfigurationService(cast(AsyncSession, session))
    missing_id = UUID(int=999)

    with pytest.raises(ConfigurationNotFoundError):
        asyncio.run(service.update_exam(missing_id, "Missing", actor_id=ACTOR_ID))

    exam = active_exam()
    medium = active_medium()
    session.add(exam)
    session.add(medium)
    exam.active = False
    with pytest.raises(ConfigurationInactiveError):
        asyncio.run(service.update_exam(exam.id, "Inactive", actor_id=ACTOR_ID))
    exam.active = True

    session.scalar_values = [True, True]
    with pytest.raises(ConfigurationInUseError):
        asyncio.run(service.deactivate_exam(exam.id, actor_id=ACTOR_ID))
    with pytest.raises(ConfigurationInUseError):
        asyncio.run(service.deactivate_medium(medium.id, actor_id=ACTOR_ID))

    curriculum = CurriculumVersionModel(
        id=UUID(int=30),
        exam_configuration_id=exam.id,
        medium_id=medium.id,
        code="2026-V1",
        title="Curriculum",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    session.add(curriculum)
    session.scalar_values = [True]
    with pytest.raises(ConfigurationInUseError):
        asyncio.run(service.deactivate_curriculum(curriculum.id, actor_id=ACTOR_ID))
    with pytest.raises(ConfigurationNotFoundError):
        asyncio.run(service.deactivate_medium(missing_id, actor_id=ACTOR_ID))

    with pytest.raises(ConfigurationNotFoundError):
        asyncio.run(
            service.create_curriculum(
                CurriculumVersionCreate(
                    exam_configuration_id=missing_id,
                    medium_id=medium.id,
                    code="MISSING",
                    title="Missing",
                ),
                actor_id=ACTOR_ID,
            )
        )

    exam.active = False
    with pytest.raises(ConfigurationInactiveError):
        asyncio.run(
            service.create_curriculum(
                CurriculumVersionCreate(
                    exam_configuration_id=exam.id,
                    medium_id=medium.id,
                    code="INACTIVE",
                    title="Inactive",
                ),
                actor_id=ACTOR_ID,
            )
        )
