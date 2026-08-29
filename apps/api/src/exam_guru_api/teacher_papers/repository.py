from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
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
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.generation.models import GenerationJobModel, GenerationRunModel
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.models import (
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.papers.models import QuestionCandidateModel, QuestionCandidateRevisionModel
from exam_guru_api.teacher_papers.domain import (
    ResolvedCurriculum,
    ResolvedLesson,
    ResolvedTaxonomyTarget,
)
from exam_guru_api.teacher_papers.models import (
    AssessmentProgrammePolicyScopeModel,
    AssessmentProgrammePolicyVersionModel,
    TeacherPaperJobModel,
    TeacherPaperMarkingConfirmationModel,
    TeacherPaperSlotModel,
    TeacherPaperSlotRunModel,
)
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel


class TeacherPaperJobNotFoundError(LookupError):
    pass


class TeacherPaperQuestionNotFoundError(LookupError):
    pass


class ProgrammePolicyNotFoundError(LookupError):
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
    marking_confirmation: TeacherPaperMarkingConfirmationModel | None
    content: dict[str, object]
    findings: tuple[ValidationFindingModel, ...]
    filenames: dict[UUID, str]
    unit_title: str
    lesson_title: str
    taxonomy_title: str


@dataclass(frozen=True, slots=True)
class StoredProgrammePolicy:
    policy: AssessmentProgrammePolicyVersionModel
    scopes: tuple[AssessmentProgrammePolicyScopeModel, ...]


class TeacherPaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def curriculum_identities(
        self,
        curriculum_ids: tuple[UUID, ...],
    ) -> dict[UUID, tuple[int, UUID, UUID, UUID]]:
        if not curriculum_ids:
            return {}
        rows = (
            await self._session.execute(
                select(
                    CurriculumVersionModel.id,
                    ExamConfigurationModel.grade,
                    ExamConfigurationModel.id,
                    MediumModel.id,
                    SubjectModel.id,
                )
                .join(
                    ExamConfigurationModel,
                    ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
                )
                .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
                .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
                .where(
                    CurriculumVersionModel.id.in_(curriculum_ids),
                    CurriculumVersionModel.active.is_(True),
                    ExamConfigurationModel.active.is_(True),
                    MediumModel.active.is_(True),
                    SubjectModel.active.is_(True),
                )
            )
        ).all()
        return {
            curriculum_id: (grade, exam_id, medium_id, subject_id)
            for curriculum_id, grade, exam_id, medium_id, subject_id in rows
        }

    async def insert_programme_policy(
        self,
        policy: AssessmentProgrammePolicyVersionModel,
        scopes: tuple[AssessmentProgrammePolicyScopeModel, ...],
    ) -> StoredProgrammePolicy:
        self._session.add(policy)
        await self._session.flush()
        self._session.add_all(scopes)
        await self._session.flush()
        return StoredProgrammePolicy(policy=policy, scopes=scopes)

    async def find_programme_policy(
        self,
        policy_id: UUID,
        *,
        for_update: bool = False,
    ) -> StoredProgrammePolicy | None:
        statement = select(AssessmentProgrammePolicyVersionModel).where(
            AssessmentProgrammePolicyVersionModel.id == policy_id
        )
        if for_update:
            statement = statement.with_for_update()
        policy = await self._session.scalar(statement.execution_options(populate_existing=True))
        if policy is None:
            return None
        scopes = tuple(
            await self._session.scalars(
                select(AssessmentProgrammePolicyScopeModel)
                .where(AssessmentProgrammePolicyScopeModel.policy_version_id == policy_id)
                .order_by(
                    AssessmentProgrammePolicyScopeModel.part,
                    AssessmentProgrammePolicyScopeModel.ordinal,
                    AssessmentProgrammePolicyScopeModel.id,
                )
            )
        )
        return StoredProgrammePolicy(policy=policy, scopes=scopes)

    async def get_programme_policy(
        self,
        policy_id: UUID,
        *,
        for_update: bool = False,
    ) -> StoredProgrammePolicy:
        stored = await self.find_programme_policy(policy_id, for_update=for_update)
        if stored is None:
            raise ProgrammePolicyNotFoundError(policy_id)
        return stored

    async def unavailable_programme_policy_scopes(
        self,
        scopes: tuple[AssessmentProgrammePolicyScopeModel, ...],
    ) -> tuple[UUID, ...]:
        unavailable: list[UUID] = []
        for scope in scopes:
            chunk_conditions: list[Any] = [
                KnowledgeChunkModel.curriculum_version_id == scope.source_curriculum_version_id,
                KnowledgeChunkModel.review_state == ReviewState.REVIEWED,
                KnowledgeChunkModel.competency_id == scope.source_competency_id,
                KnowledgeChunkModel.skill_id.is_(scope.source_skill_id)
                if scope.source_skill_id is None
                else KnowledgeChunkModel.skill_id == scope.source_skill_id,
                KnowledgeChunkModel.sub_skill_id.is_(scope.source_sub_skill_id)
                if scope.source_sub_skill_id is None
                else KnowledgeChunkModel.sub_skill_id == scope.source_sub_skill_id,
                KnowledgeChunkModel.learning_concept_id.is_(scope.source_learning_concept_id)
                if scope.source_learning_concept_id is None
                else KnowledgeChunkModel.learning_concept_id == scope.source_learning_concept_id,
                SourceDocumentModel.extraction_status == ExtractionStatus.TRUSTED,
                SourceDocumentModel.active_for_ai.is_(True),
                KnowledgeEmbeddingModel.knowledge_chunk_id == KnowledgeChunkModel.id,
            ]
            question_conditions: list[Any] = [
                HistoricalQuestionModel.curriculum_version_id == scope.source_curriculum_version_id,
                HistoricalQuestionModel.review_state == ReviewState.REVIEWED,
                HistoricalQuestionModel.competency_id == scope.source_competency_id,
                HistoricalQuestionModel.skill_id.is_(scope.source_skill_id)
                if scope.source_skill_id is None
                else HistoricalQuestionModel.skill_id == scope.source_skill_id,
                HistoricalQuestionModel.sub_skill_id.is_(scope.source_sub_skill_id)
                if scope.source_sub_skill_id is None
                else HistoricalQuestionModel.sub_skill_id == scope.source_sub_skill_id,
                HistoricalQuestionModel.learning_concept_id.is_(scope.source_learning_concept_id)
                if scope.source_learning_concept_id is None
                else HistoricalQuestionModel.learning_concept_id
                == scope.source_learning_concept_id,
                SourceDocumentModel.extraction_status == ExtractionStatus.TRUSTED,
                SourceDocumentModel.active_for_ai.is_(True),
                KnowledgeEmbeddingModel.historical_question_id == HistoricalQuestionModel.id,
            ]
            if scope.source_unit_id is not None:
                chunk_conditions.append(KnowledgeChunkModel.unit_id == scope.source_unit_id)
                question_conditions.append(HistoricalQuestionModel.unit_id == scope.source_unit_id)
            if scope.source_lesson_id is not None:
                chunk_conditions.append(KnowledgeChunkModel.lesson_id == scope.source_lesson_id)
                question_conditions.append(
                    HistoricalQuestionModel.lesson_id == scope.source_lesson_id
                )
            chunk_available = await self._session.scalar(
                select(func.count(KnowledgeChunkModel.id) > 0)
                .select_from(KnowledgeChunkModel)
                .join(
                    SourceDocumentModel,
                    SourceDocumentModel.id == KnowledgeChunkModel.source_document_id,
                )
                .join(
                    KnowledgeEmbeddingModel,
                    KnowledgeEmbeddingModel.knowledge_chunk_id == KnowledgeChunkModel.id,
                )
                .where(*chunk_conditions)
            )
            question_available = await self._session.scalar(
                select(func.count(HistoricalQuestionModel.id) > 0)
                .select_from(HistoricalQuestionModel)
                .join(
                    SourceDocumentModel,
                    SourceDocumentModel.id == HistoricalQuestionModel.source_document_id,
                )
                .join(
                    KnowledgeEmbeddingModel,
                    KnowledgeEmbeddingModel.historical_question_id == HistoricalQuestionModel.id,
                )
                .where(*question_conditions)
            )
            if not chunk_available and not question_available:
                unavailable.append(scope.id)
        return tuple(unavailable)

    async def active_programme_policy(
        self,
        *,
        code: str,
        grade: int,
        medium: str,
    ) -> StoredProgrammePolicy | None:
        policy_id = await self._session.scalar(
            select(AssessmentProgrammePolicyVersionModel.id)
            .join(
                ExamConfigurationModel,
                ExamConfigurationModel.id
                == AssessmentProgrammePolicyVersionModel.programme_exam_configuration_id,
            )
            .join(
                MediumModel,
                MediumModel.id == AssessmentProgrammePolicyVersionModel.medium_id,
            )
            .where(
                AssessmentProgrammePolicyVersionModel.state == "reviewed",
                func.upper(AssessmentProgrammePolicyVersionModel.code) == code.upper(),
                ExamConfigurationModel.grade == grade,
                ExamConfigurationModel.active.is_(True),
                func.lower(MediumModel.code) == medium.lower(),
                MediumModel.active.is_(True),
            )
        )
        if policy_id is None:
            return None
        return await self.get_programme_policy(policy_id)

    async def review_programme_policy(
        self,
        policy_id: UUID,
        *,
        expected_version: int,
        snapshot: dict[str, object],
        content_hash: str,
        reviewed_by: UUID,
        reviewed_at: datetime,
    ) -> AssessmentProgrammePolicyVersionModel | None:
        return await self._session.scalar(
            update(AssessmentProgrammePolicyVersionModel)
            .where(
                AssessmentProgrammePolicyVersionModel.id == policy_id,
                AssessmentProgrammePolicyVersionModel.state == "draft",
                AssessmentProgrammePolicyVersionModel.lock_version == expected_version,
            )
            .values(
                state="reviewed",
                lock_version=AssessmentProgrammePolicyVersionModel.lock_version + 1,
                review_snapshot=snapshot,
                content_hash=content_hash,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
            )
            .returning(AssessmentProgrammePolicyVersionModel)
        )

    async def list_curricula(
        self,
        *,
        grade: int | None = None,
        medium: str | None = None,
        subject: str | None = None,
        assessment_programme: str | None = None,
        curriculum_id: UUID | None = None,
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
        if curriculum_id is not None:
            statement = statement.where(CurriculumVersionModel.id == curriculum_id)
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
        return StoredTeacherPaper(
            job=job,
            slots=await self.list_slots(job_id, for_update=for_update),
        )

    async def list_slots(
        self,
        job_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[TeacherPaperSlotModel, ...]:
        statement = (
            select(TeacherPaperSlotModel)
            .where(TeacherPaperSlotModel.paper_job_id == job_id)
            .order_by(TeacherPaperSlotModel.ordinal)
        )
        if for_update:
            statement = statement.with_for_update()
        return tuple(
            await self._session.scalars(statement.execution_options(populate_existing=True))
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

    async def confirm_marking(
        self,
        values: Mapping[str, object],
    ) -> tuple[TeacherPaperMarkingConfirmationModel, bool]:
        statement = (
            insert(TeacherPaperMarkingConfirmationModel)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_teacher_paper_marking_confirmation_content")
            .returning(TeacherPaperMarkingConfirmationModel)
        )
        created = await self._session.scalar(statement)
        if created is not None:
            return created, True
        existing = await self._session.scalar(
            select(TeacherPaperMarkingConfirmationModel).where(
                TeacherPaperMarkingConfirmationModel.slot_id == values["slot_id"],
                TeacherPaperMarkingConfirmationModel.candidate_id == values["candidate_id"],
                TeacherPaperMarkingConfirmationModel.candidate_revision
                == values["candidate_revision"],
                TeacherPaperMarkingConfirmationModel.marking_fingerprint
                == values["marking_fingerprint"],
            )
        )
        if existing is None:
            raise TeacherPaperPersistenceConflictError(
                "teacher marking confirmation insert conflicted"
            )
        return existing, False

    async def marking_confirmation(
        self,
        *,
        slot_id: UUID,
        candidate_id: UUID,
        candidate_revision: int,
    ) -> TeacherPaperMarkingConfirmationModel | None:
        return cast(
            TeacherPaperMarkingConfirmationModel | None,
            await self._session.scalar(
                select(TeacherPaperMarkingConfirmationModel)
                .where(
                    TeacherPaperMarkingConfirmationModel.slot_id == slot_id,
                    TeacherPaperMarkingConfirmationModel.candidate_id == candidate_id,
                    TeacherPaperMarkingConfirmationModel.candidate_revision == candidate_revision,
                )
                .order_by(TeacherPaperMarkingConfirmationModel.confirmed_at.desc())
                .limit(1)
            ),
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
            marking_confirmation = (
                None
                if candidate is None
                else await self.marking_confirmation(
                    slot_id=slot.id,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.current_revision,
                )
            )
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
                    marking_confirmation=marking_confirmation,
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
