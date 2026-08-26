import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.configuration_schemas import (
    CurriculumLessonCreate,
    CurriculumLessonTaxonomyUpdate,
    CurriculumUnitCreate,
    CurriculumVersionCreate,
    ExamConfigurationCreate,
    MediumCreate,
    SubjectCreate,
)
from exam_guru_api.curriculum.configuration_service import (
    ConfigurationInactiveError,
    ConfigurationInUseError,
    ConfigurationNotFoundError,
    ConfigurationScopeMismatchError,
    ConfigurationService,
)
from exam_guru_api.curriculum.domain import (
    LEGACY_UNCLASSIFIED_SUBJECT_ID,
    TaxonomyLevel,
)
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumLessonTaxonomyMappingModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)

ACTOR_ID = UUID(int=1)


def test_exam_configuration_accepts_every_school_grade_boundary() -> None:
    assert ExamConfigurationCreate(code="G1", name="Grade 1", grade=1).grade == 1
    assert ExamConfigurationCreate(code="G7", name="Grade 7", grade=7).grade == 7
    assert ExamConfigurationCreate(code="G13", name="Grade 13", grade=13).grade == 13


def test_normalized_subject_unit_and_lesson_requests_are_bounded() -> None:
    subject = SubjectCreate(code="MATHEMATICS", name="Mathematics")
    unit = CurriculumUnitCreate(code="UNIT-01", title="Numbers", ordinal=1)
    lesson = CurriculumLessonCreate(
        unit_id=UUID(int=50),
        code="LESSON-01",
        title="Whole numbers",
        ordinal=1,
        taxonomy_node_ids=(UUID(int=60), UUID(int=61)),
    )

    assert subject.code == "MATHEMATICS"
    assert unit.ordinal == 1
    assert lesson.taxonomy_node_ids == (UUID(int=60), UUID(int=61))

    with pytest.raises(ValidationError, match="taxonomy_node_ids must be unique"):
        CurriculumLessonCreate(
            unit_id=UUID(int=50),
            code="LESSON-02",
            title="Duplicate taxonomy",
            ordinal=2,
            taxonomy_node_ids=(UUID(int=60), UUID(int=60)),
        )
    with pytest.raises(ValidationError, match="taxonomy_node_ids must be unique"):
        CurriculumLessonTaxonomyUpdate(
            taxonomy_node_ids=(UUID(int=60), UUID(int=60)),
        )


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self) -> Iterator[object]:
        return iter(self._rows)

    def all(self) -> list[object]:
        return self._rows


class StubSession:
    def __init__(self) -> None:
        self.objects: dict[tuple[type[object], UUID], object] = {}
        self.added: list[object] = []
        self.scalar_rows: list[list[object]] = []
        self.execute_rows: list[list[object]] = []
        self.scalar_values: list[bool] = []
        self.commits = 0
        self.flushes = 0

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

    async def execute(self, _query: object) -> ScalarRows:
        rows = self.execute_rows.pop(0) if self.execute_rows else []
        return ScalarRows(rows)

    async def flush(self) -> None:
        self.flushes += 1

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


def legacy_subject() -> SubjectModel:
    now = datetime.now(UTC)
    return SubjectModel(
        id=LEGACY_UNCLASSIFIED_SUBJECT_ID,
        code="LEGACY_UNCLASSIFIED",
        name="Legacy unclassified subject",
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
    session.add(legacy_subject())
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

    session.scalar_values = [False, False, False, False]
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


def test_subject_unit_lesson_service_crud_mapping_scope_and_in_use_rules() -> None:
    session = StubSession()
    service = ConfigurationService(cast(AsyncSession, session))
    exam = active_exam(110)
    medium = active_medium(120)
    session.add(exam)
    session.add(medium)
    subject = asyncio.run(
        service.create_subject(
            SubjectCreate(code="MATHEMATICS", name="Mathematics"),
            actor_id=ACTOR_ID,
        )
    )
    curriculum = asyncio.run(
        service.create_curriculum(
            CurriculumVersionCreate(
                exam_configuration_id=exam.id,
                medium_id=medium.id,
                subject_id=subject.id,
                code="G7-MATH-V1",
                title="Grade 7 Mathematics",
            ),
            actor_id=ACTOR_ID,
        )
    )
    unit = asyncio.run(
        service.create_unit(
            curriculum.id,
            CurriculumUnitCreate(code="UNIT-01", title="Numbers", ordinal=1),
            actor_id=ACTOR_ID,
        )
    )
    unit.curriculum_version_id = UUID(int=999)
    with pytest.raises(ConfigurationScopeMismatchError):
        asyncio.run(
            service.create_lesson(
                curriculum.id,
                CurriculumLessonCreate(
                    unit_id=unit.id,
                    code="WRONG-SCOPE",
                    title="Wrong scope",
                    ordinal=2,
                ),
                actor_id=ACTOR_ID,
            )
        )
    unit.curriculum_version_id = curriculum.id
    node = TaxonomyNodeModel(
        id=UUID(int=130),
        curriculum_version_id=curriculum.id,
        parent_id=None,
        level=TaxonomyLevel.COMPETENCY,
        code="COMP-1",
        title="Number competency",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    session.add(node)
    session.scalar_rows = [[node.id]]
    lesson = asyncio.run(
        service.create_lesson(
            curriculum.id,
            CurriculumLessonCreate(
                unit_id=unit.id,
                code="LESSON-01",
                title="Whole numbers",
                ordinal=1,
                taxonomy_node_ids=(node.id,),
            ),
            actor_id=ACTOR_ID,
        )
    )
    lesson_model = cast(
        CurriculumLessonModel,
        session.objects[(CurriculumLessonModel, lesson.id)],
    )

    session.scalar_rows = [[subject], [unit], [lesson_model]]
    session.execute_rows = [[(lesson.id, node.id)]]
    assert asyncio.run(service.list_subjects()) == [subject]
    assert asyncio.run(service.list_units(curriculum.id)) == [unit]
    assert asyncio.run(service.list_lessons(curriculum.id))[0].taxonomy_node_ids == (node.id,)

    assert (
        asyncio.run(service.update_subject(subject.id, "Maths", actor_id=ACTOR_ID)).name == "Maths"
    )
    assert (
        asyncio.run(
            service.update_unit(curriculum.id, unit.id, "Numbers updated", actor_id=ACTOR_ID)
        ).title
        == "Numbers updated"
    )
    session.execute_rows = [[(lesson.id, node.id)]]
    assert (
        asyncio.run(
            service.update_lesson(
                curriculum.id,
                lesson.id,
                "Whole numbers updated",
                actor_id=ACTOR_ID,
            )
        ).title
        == "Whole numbers updated"
    )

    session.scalar_rows = [[node.id]]
    session.execute_rows = [[(lesson.id, node.id)]]
    unchanged = asyncio.run(
        service.replace_lesson_taxonomy(
            curriculum.id,
            lesson.id,
            (node.id,),
            actor_id=ACTOR_ID,
        )
    )
    assert unchanged.taxonomy_node_ids == (node.id,)

    replacement_node = TaxonomyNodeModel(
        id=UUID(int=131),
        curriculum_version_id=curriculum.id,
        parent_id=None,
        level=TaxonomyLevel.COMPETENCY,
        code="COMP-2",
        title="Second competency",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    session.add(replacement_node)
    session.scalar_rows = [[replacement_node.id]]
    session.execute_rows = [[(lesson.id, node.id)], []]
    replaced = asyncio.run(
        service.replace_lesson_taxonomy(
            curriculum.id,
            lesson.id,
            (replacement_node.id,),
            actor_id=ACTOR_ID,
        )
    )
    assert replaced.taxonomy_node_ids == (replacement_node.id,)
    assert any(
        isinstance(item, CurriculumLessonTaxonomyMappingModel)
        and item.taxonomy_node_id == replacement_node.id
        for item in session.added
    )

    session.scalar_values = [True]
    with pytest.raises(ConfigurationInUseError):
        asyncio.run(service.deactivate_subject(subject.id, actor_id=ACTOR_ID))
    session.scalar_values = [True, False, False, False]
    with pytest.raises(ConfigurationInUseError):
        asyncio.run(service.deactivate_unit(curriculum.id, unit.id, actor_id=ACTOR_ID))
    session.scalar_values = [True, False, False]
    with pytest.raises(ConfigurationInUseError):
        asyncio.run(service.deactivate_lesson(curriculum.id, lesson.id, actor_id=ACTOR_ID))

    session.scalar_rows = [[]]
    with pytest.raises(ConfigurationScopeMismatchError):
        asyncio.run(
            service.replace_lesson_taxonomy(
                curriculum.id,
                lesson.id,
                (UUID(int=999),),
                actor_id=ACTOR_ID,
            )
        )
    with pytest.raises(ConfigurationScopeMismatchError):
        asyncio.run(service.update_unit(UUID(int=999), unit.id, "Wrong scope", actor_id=ACTOR_ID))
    assert asyncio.run(service._taxonomy_ids_for_lessons(())) == {}
    with pytest.raises(ConfigurationNotFoundError):
        asyncio.run(service.list_units(UUID(int=999)))

    subject.active = False
    assert asyncio.run(service.deactivate_subject(subject.id, actor_id=ACTOR_ID)) is subject
    unit.active = False
    session.scalar_values = [False, False, False, False]
    assert asyncio.run(service.deactivate_unit(curriculum.id, unit.id, actor_id=ACTOR_ID)) is unit
    lesson_model.active = False
    session.scalar_values = [False, False, False]
    session.execute_rows = [[]]
    inactive_lesson = asyncio.run(
        service.deactivate_lesson(curriculum.id, lesson.id, actor_id=ACTOR_ID)
    )
    assert inactive_lesson.active is False
    assert inactive_lesson.taxonomy_node_ids == ()
