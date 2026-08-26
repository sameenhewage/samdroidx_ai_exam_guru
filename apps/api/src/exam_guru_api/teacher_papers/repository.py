from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumLessonTaxonomyMappingModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.generation.models import GenerationJobModel, GenerationRunModel
from exam_guru_api.papers.models import QuestionCandidateModel, QuestionCandidateRevisionModel
from exam_guru_api.teacher_papers.domain import (
    ResolvedCurriculum,
    ResolvedLesson,
    ResolvedTaxonomyTarget,
)
from exam_guru_api.teacher_papers.models import (
    TeacherPaperJobModel,
    TeacherPaperSlotModel,
    TeacherPaperSlotRunModel,
)
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel


class TeacherPaperJobNotFoundError(LookupError):
    pass


class TeacherPaperQuestionNotFoundError(LookupError):
    pass


class TeacherPaperPersistenceConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredTeacherPaper:
    job: TeacherPaperJobModel
    slots: tuple[TeacherPaperSlotModel, ...]


@dataclass(frozen=True, slots=True)
class StoredTeacherPaperInsert:
    record: StoredTeacherPaper
    created: bool


@dataclass(frozen=True, slots=True)
class ReviewSlotSource:
    slot: TeacherPaperSlotModel
    generation: GenerationRunModel
    validation: ValidationRunModel | None
    candidate: QuestionCandidateModel | None
    content: dict[str, object]
    findings: tuple[ValidationFindingModel, ...]
    filenames: dict[UUID, str]
    unit_title: str
    lesson_title: str
    taxonomy_title: str


class TeacherPaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def list_curricula(
        self,
        *,
        grade: int | None = None,
        medium: str | None = None,
        subject: str | None = None,
        assessment_programme: str | None = None,
    ) -> tuple[ResolvedCurriculum, ...]:
        statement = (
            select(
                CurriculumVersionModel,
                ExamConfigurationModel,
                MediumModel,
                SubjectModel,
            )
            .join(
                ExamConfigurationModel,
                ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
            )
            .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
            .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
            .where(
                CurriculumVersionModel.active.is_(True),
                ExamConfigurationModel.active.is_(True),
                MediumModel.active.is_(True),
                SubjectModel.active.is_(True),
            )
            .order_by(
                ExamConfigurationModel.grade,
                ExamConfigurationModel.code,
                MediumModel.code,
                SubjectModel.code,
                CurriculumVersionModel.code,
                CurriculumVersionModel.id,
            )
        )
        if grade is not None:
            statement = statement.where(ExamConfigurationModel.grade == grade)
        if medium is not None:
            statement = statement.where(func.lower(MediumModel.code) == medium.lower())
        if subject is not None:
            statement = statement.where(func.upper(SubjectModel.code) == subject.upper())
        if assessment_programme is not None:
            statement = statement.where(
                func.upper(ExamConfigurationModel.code) == assessment_programme.upper()
            )
        rows = (await self._session.execute(statement)).all()
        resolved: list[ResolvedCurriculum] = []
        for curriculum, exam, medium_model, subject_model in rows:
            resolved.append(
                ResolvedCurriculum(
                    curriculum_version_id=curriculum.id,
                    exam_configuration_id=exam.id,
                    assessment_code=exam.code,
                    assessment_label=exam.name,
                    grade=exam.grade,
                    medium_id=medium_model.id,
                    medium_code=medium_model.code,
                    medium_label=medium_model.name,
                    subject_id=subject_model.id,
                    subject_code=subject_model.code,
                    subject_label=subject_model.name,
                    curriculum_code=curriculum.code,
                    curriculum_title=curriculum.title,
                    lessons=await self._lessons(curriculum.id),
                )
            )
        return tuple(resolved)

    async def _lessons(self, curriculum_id: UUID) -> tuple[ResolvedLesson, ...]:
        lesson_rows = (
            await self._session.execute(
                select(CurriculumLessonModel, CurriculumUnitModel)
                .join(
                    CurriculumUnitModel,
                    and_(
                        CurriculumUnitModel.id == CurriculumLessonModel.unit_id,
                        CurriculumUnitModel.curriculum_version_id
                        == CurriculumLessonModel.curriculum_version_id,
                    ),
                )
                .where(
                    CurriculumLessonModel.curriculum_version_id == curriculum_id,
                    CurriculumLessonModel.active.is_(True),
                    CurriculumUnitModel.active.is_(True),
                )
                .order_by(
                    CurriculumUnitModel.ordinal,
                    CurriculumLessonModel.ordinal,
                    CurriculumLessonModel.id,
                )
            )
        ).all()
        if not lesson_rows:
            return ()
        lesson_ids = tuple(row[0].id for row in lesson_rows)
        mapping_rows = (
            await self._session.execute(
                select(
                    CurriculumLessonTaxonomyMappingModel.lesson_id,
                    CurriculumLessonTaxonomyMappingModel.taxonomy_node_id,
                )
                .where(CurriculumLessonTaxonomyMappingModel.lesson_id.in_(lesson_ids))
                .order_by(
                    CurriculumLessonTaxonomyMappingModel.lesson_id,
                    CurriculumLessonTaxonomyMappingModel.taxonomy_node_id,
                )
            )
        ).all()
        nodes = tuple(
            await self._session.scalars(
                select(TaxonomyNodeModel).where(
                    TaxonomyNodeModel.curriculum_version_id == curriculum_id,
                    TaxonomyNodeModel.active.is_(True),
                    TaxonomyNodeModel.review_state == TaxonomyReviewState.REVIEWED,
                )
            )
        )
        node_by_id = {node.id: node for node in nodes}
        targets_by_lesson: defaultdict[UUID, list[ResolvedTaxonomyTarget]] = defaultdict(list)
        for lesson_id, node_id in mapping_rows:
            target = _taxonomy_target(node_id, node_by_id)
            if target is not None and target not in targets_by_lesson[lesson_id]:
                targets_by_lesson[lesson_id].append(target)
        return tuple(
            ResolvedLesson(
                id=lesson.id,
                unit_id=unit.id,
                unit_code=unit.code,
                unit_title=unit.title,
                unit_ordinal=unit.ordinal,
                code=lesson.code,
                title=lesson.title,
                ordinal=lesson.ordinal,
                taxonomy_targets=tuple(targets_by_lesson[lesson.id]),
            )
            for lesson, unit in lesson_rows
        )

    async def insert_job(self, values: dict[str, object]) -> StoredTeacherPaperInsert:
        inserted = await self._session.scalar(
            insert(TeacherPaperJobModel)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(TeacherPaperJobModel.id)
        )
        created = inserted is not None
        actor_id = cast(UUID, values["created_by"])
        idempotency_hash = cast(str, values["idempotency_key_hash"])
        job = await self._session.scalar(
            select(TeacherPaperJobModel).where(
                TeacherPaperJobModel.created_by == actor_id,
                TeacherPaperJobModel.idempotency_key_hash == idempotency_hash,
            )
        )
        if job is None:
            raise TeacherPaperPersistenceConflictError("teacher paper insert winner is missing")
        return StoredTeacherPaperInsert(
            StoredTeacherPaper(job=job, slots=await self.list_slots(job.id)),
            created=created,
        )

    async def get(self, job_id: UUID, *, for_update: bool = False) -> StoredTeacherPaper:
        statement: Select[tuple[TeacherPaperJobModel]] = select(TeacherPaperJobModel).where(
            TeacherPaperJobModel.id == job_id
        )
        if for_update:
            statement = statement.with_for_update()
        job = await self._session.scalar(statement.execution_options(populate_existing=True))
        if job is None:
            raise TeacherPaperJobNotFoundError(job_id)
        return StoredTeacherPaper(job=job, slots=await self.list_slots(job_id))

    async def list_slots(self, job_id: UUID) -> tuple[TeacherPaperSlotModel, ...]:
        return tuple(
            await self._session.scalars(
                select(TeacherPaperSlotModel)
                .where(TeacherPaperSlotModel.paper_job_id == job_id)
                .order_by(TeacherPaperSlotModel.ordinal)
                .execution_options(populate_existing=True)
            )
        )

    async def find_slot(
        self,
        job_id: UUID,
        question_id: UUID,
        *,
        for_update: bool = False,
    ) -> TeacherPaperSlotModel:
        statement = select(TeacherPaperSlotModel).where(
            TeacherPaperSlotModel.paper_job_id == job_id,
            or_(
                TeacherPaperSlotModel.current_generation_run_id == question_id,
                TeacherPaperSlotModel.current_candidate_id == question_id,
            ),
        )
        if for_update:
            statement = statement.with_for_update()
        slot = await self._session.scalar(statement.execution_options(populate_existing=True))
        if slot is None:
            raise TeacherPaperQuestionNotFoundError(question_id)
        return slot

    async def attach_dispatch_message(self, job_id: UUID, message_id: str) -> None:
        job = (await self.get(job_id)).job
        if job.dispatch_message_id is not None:
            return
        attached = await self._session.scalar(
            update(TeacherPaperJobModel)
            .where(
                TeacherPaperJobModel.id == job_id,
                TeacherPaperJobModel.version == job.version,
                TeacherPaperJobModel.dispatch_message_id.is_(None),
            )
            .values(
                dispatch_message_id=message_id,
                version=TeacherPaperJobModel.version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(TeacherPaperJobModel.id)
        )
        if attached is None:
            winner = (await self.get(job_id)).job
            if winner.dispatch_message_id is None:
                raise TeacherPaperPersistenceConflictError("dispatch message CAS lost")

    async def claim(
        self,
        job_id: UUID,
        *,
        token: UUID,
        lease_seconds: int,
        now: datetime,
    ) -> TeacherPaperJobModel | None:
        claimed = await self._session.scalar(
            update(TeacherPaperJobModel)
            .where(
                TeacherPaperJobModel.id == job_id,
                TeacherPaperJobModel.status.in_(("preparing", "generating", "checking_answers")),
                or_(
                    TeacherPaperJobModel.actor_token.is_(None),
                    TeacherPaperJobModel.actor_lease_expires_at <= now,
                ),
            )
            .values(
                actor_token=token,
                actor_lease_expires_at=now + timedelta(seconds=lease_seconds),
                version=TeacherPaperJobModel.version + 1,
                updated_at=now,
            )
            .returning(TeacherPaperJobModel)
        )
        await self._session.commit()
        return claimed

    async def release(self, job_id: UUID, *, token: UUID) -> bool:
        released = await self._session.scalar(
            update(TeacherPaperJobModel)
            .where(
                TeacherPaperJobModel.id == job_id,
                TeacherPaperJobModel.actor_token == token,
            )
            .values(
                actor_token=None,
                actor_lease_expires_at=None,
                version=TeacherPaperJobModel.version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(TeacherPaperJobModel.id)
        )
        await self._session.commit()
        return released is not None

    async def cas_job(
        self,
        job_id: UUID,
        *,
        token: UUID | None,
        expected_version: int,
        values: Mapping[str, object],
    ) -> TeacherPaperJobModel:
        conditions = [
            TeacherPaperJobModel.id == job_id,
            TeacherPaperJobModel.version == expected_version,
        ]
        if token is not None:
            conditions.append(TeacherPaperJobModel.actor_token == token)
        updated = await self._session.scalar(
            update(TeacherPaperJobModel)
            .where(*conditions)
            .values(
                **values,
                version=expected_version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(TeacherPaperJobModel)
        )
        if updated is None:
            raise TeacherPaperPersistenceConflictError("teacher paper job CAS lost")
        return updated

    async def cas_slot(
        self,
        slot: TeacherPaperSlotModel,
        values: Mapping[str, object],
    ) -> TeacherPaperSlotModel:
        updated = await self._session.scalar(
            update(TeacherPaperSlotModel)
            .where(
                TeacherPaperSlotModel.id == slot.id,
                TeacherPaperSlotModel.version == slot.version,
            )
            .values(
                **values,
                version=slot.version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(TeacherPaperSlotModel)
        )
        if updated is None:
            raise TeacherPaperPersistenceConflictError("teacher paper slot CAS lost")
        return updated

    async def generation_job(self, run_id: UUID) -> GenerationJobModel:
        job = await self._session.scalar(
            select(GenerationJobModel).where(GenerationJobModel.generation_run_id == run_id)
        )
        if job is None:
            raise TeacherPaperPersistenceConflictError("generation job lineage is missing")
        return job

    async def generation_run(self, run_id: UUID) -> GenerationRunModel:
        run = await self._session.get(GenerationRunModel, run_id)
        if run is None:
            raise TeacherPaperPersistenceConflictError("generation run lineage is missing")
        return run

    async def add_slot_run(
        self,
        slot: TeacherPaperSlotModel,
        *,
        generation_run_id: UUID,
        sequence: int,
        reason: str,
        requested_by: UUID,
        link_id: UUID,
    ) -> None:
        self._session.add(
            TeacherPaperSlotRunModel(
                id=link_id,
                paper_job_id=slot.paper_job_id,
                slot_id=slot.id,
                slot_ordinal=slot.ordinal,
                curriculum_version_id=slot.curriculum_version_id,
                generation_run_id=generation_run_id,
                sequence=sequence,
                reason=reason,
                requested_by=requested_by,
            )
        )
        await self._session.flush()

    async def split_context_ids(
        self, record_ids: tuple[UUID, ...]
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        if not record_ids:
            return (), ()
        from exam_guru_api.knowledge.models import HistoricalQuestionModel, KnowledgeChunkModel

        chunk_ids = tuple(
            await self._session.scalars(
                select(KnowledgeChunkModel.id).where(KnowledgeChunkModel.id.in_(record_ids))
            )
        )
        question_ids = tuple(
            await self._session.scalars(
                select(HistoricalQuestionModel.id).where(HistoricalQuestionModel.id.in_(record_ids))
            )
        )
        return (
            tuple(sorted(chunk_ids, key=lambda item: item.int)),
            tuple(sorted(question_ids, key=lambda item: item.int)),
        )

    async def recoverable_job_ids(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[UUID, ...]:
        return tuple(
            await self._session.scalars(
                select(TeacherPaperJobModel.id)
                .where(
                    TeacherPaperJobModel.status.in_(
                        ("preparing", "generating", "checking_answers")
                    ),
                    or_(
                        TeacherPaperJobModel.actor_token.is_(None),
                        TeacherPaperJobModel.actor_lease_expires_at <= now,
                    ),
                )
                .order_by(TeacherPaperJobModel.updated_at, TeacherPaperJobModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    async def list_review_jobs(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[TeacherPaperJobModel, ...]:
        return tuple(
            await self._session.scalars(
                select(TeacherPaperJobModel)
                .where(
                    TeacherPaperJobModel.status.in_(("ready_for_review", "failed")),
                    TeacherPaperJobModel.paper_blueprint_id.is_not(None),
                )
                .order_by(TeacherPaperJobModel.created_at.desc(), TeacherPaperJobModel.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def review_sources(self, job_id: UUID) -> tuple[ReviewSlotSource, ...]:
        slots = await self.list_slots(job_id)
        results: list[ReviewSlotSource] = []
        for slot in slots:
            if slot.current_generation_run_id is None:
                continue
            generation = await self.generation_run(slot.current_generation_run_id)
            validation = (
                None
                if slot.current_validation_run_id is None
                else await self._session.get(ValidationRunModel, slot.current_validation_run_id)
            )
            candidate = (
                None
                if slot.current_candidate_id is None
                else await self._session.get(QuestionCandidateModel, slot.current_candidate_id)
            )
            if candidate is None:
                content = cast(dict[str, object], generation.candidate)
            else:
                revision = await self._session.scalar(
                    select(QuestionCandidateRevisionModel).where(
                        QuestionCandidateRevisionModel.candidate_id == candidate.id,
                        QuestionCandidateRevisionModel.revision == candidate.current_revision,
                    )
                )
                if revision is None:
                    raise TeacherPaperPersistenceConflictError("candidate revision is missing")
                content = revision.content
            findings: tuple[ValidationFindingModel, ...] = ()
            if validation is not None:
                findings = tuple(
                    await self._session.scalars(
                        select(ValidationFindingModel)
                        .where(ValidationFindingModel.validation_run_id == validation.id)
                        .order_by(ValidationFindingModel.ordinal)
                    )
                )
            context_items = cast(
                list[dict[str, object]],
                generation.context_snapshot["items"],
            )
            source_ids = tuple(
                UUID(cast(str, cast(dict[str, object], item["provenance"])["source_document_id"]))
                for item in context_items
            )
            filenames: dict[UUID, str] = {}
            if source_ids:
                from exam_guru_api.documents.models import SourceDocumentModel

                filename_rows = (
                    await self._session.execute(
                        select(
                            SourceDocumentModel.id,
                            SourceDocumentModel.original_filename,
                        ).where(SourceDocumentModel.id.in_(source_ids))
                    )
                ).all()
                filenames = {cast(UUID, row[0]): cast(str, row[1]) for row in filename_rows}
            unit_title = cast(
                str,
                await self._session.scalar(
                    select(CurriculumUnitModel.title).where(CurriculumUnitModel.id == slot.unit_id)
                ),
            )
            lesson_title = cast(
                str,
                await self._session.scalar(
                    select(CurriculumLessonModel.title).where(
                        CurriculumLessonModel.id == slot.lesson_id
                    )
                ),
            )
            leaf_id = (
                slot.learning_concept_id or slot.sub_skill_id or slot.skill_id or slot.competency_id
            )
            taxonomy_title = cast(
                str,
                await self._session.scalar(
                    select(TaxonomyNodeModel.title).where(TaxonomyNodeModel.id == leaf_id)
                ),
            )
            results.append(
                ReviewSlotSource(
                    slot=slot,
                    generation=generation,
                    validation=validation,
                    candidate=candidate,
                    content=content,
                    findings=findings,
                    filenames=filenames,
                    unit_title=unit_title,
                    lesson_title=lesson_title,
                    taxonomy_title=taxonomy_title,
                )
            )
        return tuple(results)


def _taxonomy_target(
    node_id: UUID,
    nodes: dict[UUID, TaxonomyNodeModel],
) -> ResolvedTaxonomyTarget | None:
    node = nodes.get(node_id)
    if node is None:
        return None
    path: list[TaxonomyNodeModel] = [node]
    seen = {node.id}
    while path[-1].parent_id is not None:
        parent = nodes.get(path[-1].parent_id)
        if parent is None or parent.id in seen:
            return None
        path.append(parent)
        seen.add(parent.id)
    path.reverse()
    if path[0].level is not TaxonomyLevel.COMPETENCY:
        return None
    by_level = {item.level: item for item in path}
    expected = (
        (TaxonomyLevel.SKILL, TaxonomyLevel.COMPETENCY),
        (TaxonomyLevel.SUB_SKILL, TaxonomyLevel.SKILL),
        (TaxonomyLevel.LEARNING_CONCEPT, TaxonomyLevel.SUB_SKILL),
    )
    for child_level, parent_level in expected:
        child = by_level.get(child_level)
        if child is not None:
            parent = by_level.get(parent_level)
            if parent is None or child.parent_id != parent.id:
                return None
    skill = by_level.get(TaxonomyLevel.SKILL)
    sub_skill = by_level.get(TaxonomyLevel.SUB_SKILL)
    concept = by_level.get(TaxonomyLevel.LEARNING_CONCEPT)
    return ResolvedTaxonomyTarget(
        competency_id=by_level[TaxonomyLevel.COMPETENCY].id,
        skill_id=None if skill is None else skill.id,
        sub_skill_id=None if sub_skill is None else sub_skill.id,
        learning_concept_id=None if concept is None else concept.id,
        label=" / ".join(item.title for item in path),
    )
