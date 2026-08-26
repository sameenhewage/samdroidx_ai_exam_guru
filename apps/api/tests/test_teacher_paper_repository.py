import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    TaxonomyNodeModel,
)
from exam_guru_api.generation.models import GenerationJobModel, GenerationRunModel
from exam_guru_api.papers.models import QuestionCandidateModel, QuestionCandidateRevisionModel
from exam_guru_api.teacher_papers.models import TeacherPaperJobModel, TeacherPaperSlotModel
from exam_guru_api.teacher_papers.repository import (
    TeacherPaperJobNotFoundError,
    TeacherPaperPersistenceConflictError,
    TeacherPaperQuestionNotFoundError,
    TeacherPaperRepository,
    _taxonomy_target,
)
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel

NOW = datetime(2026, 8, 25, tzinfo=UTC)
JOB_ID = UUID(int=25_900_001)
SLOT_ID = UUID(int=25_900_002)
RUN_ID = UUID(int=25_900_003)
ACTOR_ID = UUID(int=25_900_004)
CURRICULUM_ID = UUID(int=25_900_005)


class Rows:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


class Result:
    def __init__(self, rows: tuple[object, ...] = (), row: object | None = None) -> None:
        self.rows = rows
        self.row = row

    def all(self) -> list[object]:
        return list(self.rows)

    def one(self) -> object:
        if self.row is None:
            raise AssertionError("a scripted row is required")
        return self.row


class ScriptedSession:
    def __init__(
        self,
        *,
        scalar_results: tuple[object | None, ...] = (),
        scalar_rows: tuple[tuple[object, ...], ...] = (),
        execute_results: tuple[Result, ...] = (),
        get_results: tuple[object | None, ...] = (),
    ) -> None:
        self.scalar_results = list(scalar_results)
        self.scalar_rows = list(scalar_rows)
        self.execute_results = list(execute_results)
        self.get_results = list(get_results)
        self.added: list[object] = []
        self.commits = 0
        self.flushes = 0

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_results.pop(0)

    async def scalars(self, statement: object) -> Rows:
        del statement
        values = self.scalar_rows.pop(0) if self.scalar_rows else ()
        return Rows(values)

    async def execute(self, statement: object) -> Result:
        del statement
        return self.execute_results.pop(0)

    async def get(self, model: object, identifier: UUID) -> object | None:
        del model, identifier
        return self.get_results.pop(0) if self.get_results else None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushes += 1


def job(*, message: str | None = None, version: int = 0) -> TeacherPaperJobModel:
    return TeacherPaperJobModel(
        id=JOB_ID,
        dispatch_message_id=message,
        version=version,
        created_by=ACTOR_ID,
        idempotency_key_hash="sha256:" + "a" * 64,
    )


def slot(
    *,
    generation_run_id: UUID | None = RUN_ID,
    candidate_id: UUID | None = None,
) -> TeacherPaperSlotModel:
    return TeacherPaperSlotModel(
        id=SLOT_ID,
        paper_job_id=JOB_ID,
        curriculum_version_id=CURRICULUM_ID,
        ordinal=1,
        version=0,
        current_generation_run_id=generation_run_id,
        current_candidate_id=candidate_id,
        current_validation_run_id=None,
    )


def repository(session: ScriptedSession) -> TeacherPaperRepository:
    return TeacherPaperRepository(cast(AsyncSession, session))


def test_repository_empty_curriculum_and_missing_insert_winner_fail_closed() -> None:
    empty_session = ScriptedSession(execute_results=(Result(),))
    assert asyncio.run(repository(empty_session)._lessons(CURRICULUM_ID)) == ()

    missing = ScriptedSession(scalar_results=(JOB_ID, None))
    with pytest.raises(TeacherPaperPersistenceConflictError, match="winner"):
        asyncio.run(
            repository(missing).insert_job(
                {
                    "created_by": ACTOR_ID,
                    "idempotency_key_hash": "sha256:" + "a" * 64,
                }
            )
        )


def test_lesson_resolution_ignores_missing_and_duplicate_taxonomy_mappings() -> None:
    competency = node(11, TaxonomyLevel.COMPETENCY)
    skill = node(12, TaxonomyLevel.SKILL, parent_id=competency.id)
    unit = CurriculumUnitModel(
        id=UUID(int=13),
        code="UNIT",
        title="Unit",
        ordinal=1,
    )
    lesson_model = CurriculumLessonModel(
        id=UUID(int=14),
        unit_id=unit.id,
        code="LESSON",
        title="Lesson",
        ordinal=1,
    )
    scripted = ScriptedSession(
        execute_results=(
            Result(rows=((lesson_model, unit),)),
            Result(
                rows=(
                    (lesson_model.id, skill.id),
                    (lesson_model.id, skill.id),
                    (lesson_model.id, UUID(int=999)),
                )
            ),
        ),
        scalar_rows=((competency, skill),),
    )
    resolved = asyncio.run(repository(scripted)._lessons(CURRICULUM_ID))
    assert len(resolved) == 1
    assert len(resolved[0].taxonomy_targets) == 1
    assert resolved[0].taxonomy_targets[0].skill_id == skill.id


def test_repository_scoped_get_and_slot_lookup_report_not_found() -> None:
    missing_job = ScriptedSession(scalar_results=(None,))
    with pytest.raises(TeacherPaperJobNotFoundError):
        asyncio.run(repository(missing_job).get(JOB_ID, for_update=True))

    missing_slot = ScriptedSession(scalar_results=(None,))
    with pytest.raises(TeacherPaperQuestionNotFoundError):
        asyncio.run(repository(missing_slot).find_slot(JOB_ID, RUN_ID, for_update=True))


def test_repository_insert_attachment_and_generation_job_success_paths() -> None:
    stored_job = job()
    inserted = ScriptedSession(
        scalar_results=(JOB_ID, stored_job),
        scalar_rows=((),),
    )
    result = asyncio.run(
        repository(inserted).insert_job(
            {
                "created_by": ACTOR_ID,
                "idempotency_key_hash": "sha256:" + "a" * 64,
            }
        )
    )
    assert result.created is True
    assert result.record.job is stored_job

    attached = ScriptedSession(
        scalar_results=(job(), JOB_ID),
        scalar_rows=((),),
    )
    asyncio.run(repository(attached).attach_dispatch_message(JOB_ID, "message"))

    generation_job = GenerationJobModel(id=UUID(int=55), generation_run_id=RUN_ID)
    generation_session = ScriptedSession(scalar_results=(generation_job,))
    assert asyncio.run(repository(generation_session).generation_job(RUN_ID)) is generation_job


def test_dispatch_attachment_is_idempotent_and_detects_a_lost_cas() -> None:
    already = ScriptedSession(scalar_results=(job(message="existing"),), scalar_rows=((),))
    asyncio.run(repository(already).attach_dispatch_message(JOB_ID, "new"))
    assert already.scalar_results == []

    winner = job(message="winner", version=1)
    won_elsewhere = ScriptedSession(
        scalar_results=(job(), None, winner),
        scalar_rows=((), ()),
    )
    asyncio.run(repository(won_elsewhere).attach_dispatch_message(JOB_ID, "new"))

    lost = ScriptedSession(
        scalar_results=(job(), None, job(version=1)),
        scalar_rows=((), ()),
    )
    with pytest.raises(TeacherPaperPersistenceConflictError, match="dispatch"):
        asyncio.run(repository(lost).attach_dispatch_message(JOB_ID, "new"))


def test_repository_cas_and_lineage_guards_cover_absent_rows() -> None:
    release = ScriptedSession(scalar_results=(None,))
    assert asyncio.run(repository(release).release(JOB_ID, token=ACTOR_ID)) is False
    assert release.commits == 1

    job_cas = ScriptedSession(scalar_results=(None,))
    with pytest.raises(TeacherPaperPersistenceConflictError, match="job CAS"):
        asyncio.run(
            repository(job_cas).cas_job(
                JOB_ID,
                token=None,
                expected_version=0,
                values={"status": "generating"},
            )
        )

    slot_cas = ScriptedSession(scalar_results=(None,))
    with pytest.raises(TeacherPaperPersistenceConflictError, match="slot CAS"):
        asyncio.run(repository(slot_cas).cas_slot(slot(), {"status": "failed"}))

    missing_generation_job = ScriptedSession(scalar_results=(None,))
    with pytest.raises(TeacherPaperPersistenceConflictError, match="generation job"):
        asyncio.run(repository(missing_generation_job).generation_job(RUN_ID))

    missing_generation_run = ScriptedSession(get_results=(None,))
    with pytest.raises(TeacherPaperPersistenceConflictError, match="generation run"):
        asyncio.run(repository(missing_generation_run).generation_run(RUN_ID))

    empty_split = ScriptedSession()
    assert asyncio.run(repository(empty_split).split_context_ids(())) == ((), ())


def test_review_source_skips_an_unstarted_slot_and_rejects_missing_revision() -> None:
    skipped = ScriptedSession(scalar_rows=((slot(generation_run_id=None),),))
    assert asyncio.run(repository(skipped).review_sources(JOB_ID)) == ()

    generation = GenerationRunModel(
        id=RUN_ID,
        candidate={"stem": "candidate"},
        context_snapshot={"items": []},
    )
    candidate = type(
        "Candidate",
        (),
        {"id": RUN_ID, "current_revision": 2},
    )()
    missing_revision = ScriptedSession(
        scalar_results=(None,),
        scalar_rows=((slot(candidate_id=RUN_ID),),),
        get_results=(generation, candidate),
    )
    with pytest.raises(TeacherPaperPersistenceConflictError, match="revision"):
        asyncio.run(repository(missing_revision).review_sources(JOB_ID))


def test_review_sources_reconstructs_generated_and_revised_content_paths() -> None:
    active_slot = slot()
    active_slot.unit_id = UUID(int=70)
    active_slot.lesson_id = UUID(int=71)
    active_slot.competency_id = UUID(int=72)
    generated = GenerationRunModel(
        id=RUN_ID,
        candidate={"stem": "generated"},
        context_snapshot={"items": []},
    )
    generated_session = ScriptedSession(
        scalar_results=("Unit", "Lesson", "Taxonomy"),
        scalar_rows=((active_slot,),),
        get_results=(generated,),
    )
    generated_sources = asyncio.run(repository(generated_session).review_sources(JOB_ID))
    assert generated_sources[0].content == {"stem": "generated"}
    assert generated_sources[0].validation is None

    source_id = UUID(int=73)
    generated.context_snapshot = {
        "items": [
            {
                "provenance": {
                    "source_document_id": str(source_id),
                }
            }
        ]
    }
    validation = ValidationRunModel(id=UUID(int=74))
    candidate = QuestionCandidateModel(id=RUN_ID, current_revision=2)
    active_slot.current_validation_run_id = validation.id
    active_slot.current_candidate_id = candidate.id
    revision = QuestionCandidateRevisionModel(
        candidate_id=candidate.id,
        revision=2,
        content={"stem": "revised"},
    )
    finding = ValidationFindingModel(id=UUID(int=75), ordinal=0)
    revised_session = ScriptedSession(
        scalar_results=(revision, "Unit", "Lesson", "Taxonomy"),
        scalar_rows=((active_slot,), (finding,)),
        execute_results=(Result(rows=((source_id, "source.pdf"),)),),
        get_results=(generated, validation, candidate),
    )
    revised_sources = asyncio.run(repository(revised_session).review_sources(JOB_ID))
    assert revised_sources[0].content == {"stem": "revised"}
    assert revised_sources[0].findings == (finding,)
    assert revised_sources[0].filenames == {source_id: "source.pdf"}


def node(
    identifier: int,
    level: TaxonomyLevel,
    *,
    parent_id: UUID | None = None,
) -> TaxonomyNodeModel:
    return TaxonomyNodeModel(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        parent_id=parent_id,
        level=level,
        code=f"N{identifier}",
        title=f"Node {identifier}",
        active=True,
        review_state=TaxonomyReviewState.REVIEWED,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )


def test_taxonomy_path_reconstruction_rejects_missing_cycles_and_malformed_hierarchy() -> None:
    assert _taxonomy_target(UUID(int=1), {}) is None

    missing_parent = node(2, TaxonomyLevel.SKILL, parent_id=UUID(int=1))
    assert _taxonomy_target(missing_parent.id, {missing_parent.id: missing_parent}) is None

    first = node(3, TaxonomyLevel.SKILL, parent_id=UUID(int=4))
    second = node(4, TaxonomyLevel.SUB_SKILL, parent_id=first.id)
    assert _taxonomy_target(first.id, {first.id: first, second.id: second}) is None

    root_skill = node(5, TaxonomyLevel.SKILL)
    assert _taxonomy_target(root_skill.id, {root_skill.id: root_skill}) is None

    competency = node(6, TaxonomyLevel.COMPETENCY)
    malformed_sub_skill = node(7, TaxonomyLevel.SUB_SKILL, parent_id=competency.id)
    assert (
        _taxonomy_target(
            malformed_sub_skill.id,
            {competency.id: competency, malformed_sub_skill.id: malformed_sub_skill},
        )
        is None
    )

    skill = node(8, TaxonomyLevel.SKILL, parent_id=competency.id)
    sub_skill = node(9, TaxonomyLevel.SUB_SKILL, parent_id=skill.id)
    concept = node(10, TaxonomyLevel.LEARNING_CONCEPT, parent_id=sub_skill.id)
    rebuilt = _taxonomy_target(
        concept.id,
        {item.id: item for item in (competency, skill, sub_skill, concept)},
    )
    assert rebuilt is not None
    assert rebuilt.competency_id == competency.id
    assert rebuilt.skill_id == skill.id
    assert rebuilt.sub_skill_id == sub_skill.id
    assert rebuilt.learning_concept_id == concept.id
