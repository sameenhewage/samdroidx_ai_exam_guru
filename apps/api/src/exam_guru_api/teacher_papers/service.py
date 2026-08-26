from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AdminRole, Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.blueprints.serialization import deserialize_blueprint
from exam_guru_api.blueprints.service import BlueprintGenerationService
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.models import GenerationRunModel, GenerationRunStatus
from exam_guru_api.generation.run_service import GenerationRunService
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry
from exam_guru_api.papers.domain import CandidateState, ValidationNotPassedError
from exam_guru_api.papers.publication_service import PaperPublicationService
from exam_guru_api.papers.review_service import (
    ReviewCandidateRevalidationRequiredError,
    ReviewCandidateService,
    ReviewCandidateStateConflictError,
    ReviewCandidateVersionConflictError,
)
from exam_guru_api.papers.schemas import QuestionContentResponse
from exam_guru_api.retrieval.context import ContextLimits
from exam_guru_api.retrieval.domain import RetrievalScope, TaxonomyScope
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
)
from exam_guru_api.retrieval.fusion import FusionConfig
from exam_guru_api.retrieval.repository import PostgresHybridRetrievalRepository
from exam_guru_api.retrieval.service import HybridRetrievalService
from exam_guru_api.teacher_papers.domain import (
    MAX_SLOT_REGENERATIONS,
    PaperDifficulty,
    PaperJobStatus,
    PaperScopeError,
    PaperSettings,
    PaperSlotStatus,
    ResolvedCurriculum,
    ResolvedPaperScope,
    TeacherScopeKind,
    TeacherScopeSelection,
    assign_blueprint_lessons,
    build_blueprint_specification,
    translate_teacher_scope,
)
from exam_guru_api.teacher_papers.jobs import PaperGenerationDispatcher
from exam_guru_api.teacher_papers.models import (
    TeacherPaperJobModel,
    TeacherPaperSlotModel,
    TeacherPaperSlotRunModel,
)
from exam_guru_api.teacher_papers.repository import (
    ReviewSlotSource,
    StoredTeacherPaper,
    TeacherPaperQuestionNotFoundError,
    TeacherPaperRepository,
)
from exam_guru_api.teacher_papers.schemas import (
    AssessmentProgrammeOption,
    CurriculumLabelResponse,
    CurriculumLabelsResponse,
    FriendlyValidationStatus,
    LessonLabelsResponse,
    LessonOption,
    MediumOption,
    ReviewMarkingSchemeResponse,
    ReviewPaperCreateDraftRequest,
    ReviewPaperDetailResponse,
    ReviewPaperDraftCreatedResponse,
    ReviewPaperDraftResponse,
    ReviewPaperListResponse,
    ReviewPaperSummaryResponse,
    ReviewPaperTechnicalDetailsResponse,
    ReviewQuestionEditRequest,
    ReviewQuestionOptionResponse,
    ReviewQuestionRegenerateRequest,
    ReviewQuestionRegenerationResponse,
    ReviewQuestionResponse,
    ReviewQuestionScopeResponse,
    ReviewQuestionTechnicalDetailsResponse,
    ReviewSourceResponse,
    ReviewValidationResponse,
    SubjectOption,
    TeacherPaperCountsResponse,
    TeacherPaperFailureResponse,
    TeacherPaperJobCreateRequest,
    TeacherPaperJobResponse,
    TeacherPaperOptionsResponse,
    TeacherPaperSlotProgressResponse,
    TeacherPaperStatus,
    TechnicalFindingStatus,
    TechnicalValidationFindingResponse,
    UnitOption,
)
from exam_guru_api.validation.models import ValidationRunModel
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.service import ValidationRunService

_TEACHER_PAPER_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/teacher-paper-aggregate")
_ACTOR_LEASE_SECONDS = 601


class TeacherPaperCurriculumNotFoundError(LookupError):
    pass


class TeacherPaperCurriculumAmbiguousError(RuntimeError):
    pass


class TeacherPaperIdempotencyConflictError(RuntimeError):
    pass


class TeacherPaperQueueUnavailableError(RuntimeError):
    pass


class TeacherPaperVersionConflictError(RuntimeError):
    pass


class TeacherPaperStateConflictError(RuntimeError):
    pass


class TeacherPaperContextUnavailableError(RuntimeError):
    pass


class TeacherPaperRetryLimitError(RuntimeError):
    pass


class TeacherPaperCostLimitError(RuntimeError):
    pass


class TeacherPaperRevalidationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TeacherPaperCreationResult:
    record: StoredTeacherPaper
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class TeacherPaperRecoveryResult:
    scanned: int
    dispatched: int
    failures: int


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_idempotency_key(value: str) -> None:
    if (
        not value
        or value != value.strip()
        or len(value) > 128
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise TeacherPaperIdempotencyConflictError("invalid idempotency key")


def _selection(request: TeacherPaperJobCreateRequest) -> TeacherScopeSelection:
    if request.scope.kind == "full_subject":
        return TeacherScopeSelection(kind=TeacherScopeKind.FULL_SUBJECT)
    return TeacherScopeSelection(
        kind=TeacherScopeKind.LESSON_RANGE,
        start_lesson=request.scope.start_lesson,
        end_lesson=request.scope.end_lesson,
    )


def _require_context_ids(
    knowledge_ids: tuple[UUID, ...],
    question_ids: tuple[UUID, ...],
    slot_id: str,
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    if not knowledge_ids and not question_ids:
        raise TeacherPaperContextUnavailableError(slot_id)
    return knowledge_ids, question_ids


def _settings(request: TeacherPaperJobCreateRequest) -> PaperSettings:
    return PaperSettings(
        question_count=request.settings.question_count,
        duration_minutes=request.settings.duration_minutes,
        difficulty=request.settings.difficulty,
    )


def _curriculum_label(curriculum: ResolvedCurriculum) -> CurriculumLabelResponse:
    return CurriculumLabelResponse(
        assessment_programme=curriculum.assessment_code,
        assessment_label=curriculum.assessment_label,
        code=curriculum.curriculum_code,
        label=curriculum.curriculum_title,
    )


def _lesson_options(curriculum: ResolvedCurriculum) -> tuple[LessonOption, ...]:
    ordered = tuple(
        sorted(
            curriculum.lessons,
            key=lambda item: (item.unit_ordinal, item.ordinal, item.id.int),
        )
    )
    return tuple(
        LessonOption(
            number=number,
            code=lesson.code,
            label=f"Lesson {number} — {lesson.title}",
            unit=lesson.unit_title,
            taxonomy=tuple(target.label for target in lesson.taxonomy_targets),
        )
        for number, lesson in enumerate(ordered, start=1)
    )


class TeacherPaperQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = TeacherPaperRepository(session)

    async def options(self) -> TeacherPaperOptionsResponse:
        curricula = await self._repository.list_curricula()
        media = {curriculum.medium_code: curriculum.medium_label for curriculum in curricula}
        assessments = {
            (curriculum.assessment_code, curriculum.grade): curriculum.assessment_label
            for curriculum in curricula
        }
        subjects = tuple(
            SubjectOption(
                code=curriculum.subject_code,
                grade=curriculum.grade,
                medium=curriculum.medium_code,
                assessment_programme=curriculum.assessment_code,
                label=curriculum.subject_label,
                units=tuple(
                    UnitOption(code=code, label=label)
                    for code, label in dict.fromkeys(
                        (lesson.unit_code, lesson.unit_title) for lesson in curriculum.lessons
                    )
                ),
                lessons=_lesson_options(curriculum),
            )
            for curriculum in curricula
        )
        return TeacherPaperOptionsResponse(
            grades=tuple(range(1, 14)),
            media=tuple(
                MediumOption(code=code, label=label) for code, label in sorted(media.items())
            ),
            assessment_programmes=tuple(
                AssessmentProgrammeOption(code=code, grade=grade, label=label)
                for (code, grade), label in sorted(assessments.items())
            ),
            subjects=subjects,
        )

    async def curricula(
        self,
        *,
        grade: int,
        medium: str,
        subject: str,
        assessment_programme: str | None,
    ) -> CurriculumLabelsResponse:
        records = await self._repository.list_curricula(
            grade=grade,
            medium=medium,
            subject=subject,
            assessment_programme=assessment_programme,
        )
        return CurriculumLabelsResponse(items=tuple(_curriculum_label(item) for item in records))

    async def lessons(
        self,
        *,
        grade: int,
        medium: str,
        subject: str,
        assessment_programme: str | None,
    ) -> LessonLabelsResponse:
        curriculum = await self._resolve(
            grade=grade,
            medium=medium,
            subject=subject,
            assessment_programme=assessment_programme,
        )
        return LessonLabelsResponse(
            grade=curriculum.grade,
            medium=curriculum.medium_label,
            subject=curriculum.subject_label,
            curriculum=_curriculum_label(curriculum),
            lessons=_lesson_options(curriculum),
        )

    async def _resolve(
        self,
        *,
        grade: int,
        medium: str,
        subject: str,
        assessment_programme: str | None,
    ) -> ResolvedCurriculum:
        records = await self._repository.list_curricula(
            grade=grade,
            medium=medium,
            subject=subject,
            assessment_programme=assessment_programme,
        )
        if not records:
            raise TeacherPaperCurriculumNotFoundError
        if len(records) != 1:
            raise TeacherPaperCurriculumAmbiguousError
        return records[0]


class TeacherPaperJobService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: PaperGenerationDispatcher,
        runtime: GenerationRuntimeRegistry,
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._runtime = runtime
        self._repository = TeacherPaperRepository(session)

    async def create(
        self,
        request: TeacherPaperJobCreateRequest,
        *,
        idempotency_key: str,
        principal: Principal,
    ) -> TeacherPaperCreationResult:
        authorize(principal, Permission.GENERATION_RUN)
        _validate_idempotency_key(idempotency_key)
        target = request.target
        curricula = await self._repository.list_curricula(
            grade=target.grade,
            medium=target.medium,
            subject=target.subject,
            assessment_programme=target.assessment_programme,
        )
        if not curricula:
            raise TeacherPaperCurriculumNotFoundError
        if len(curricula) != 1:
            raise TeacherPaperCurriculumAmbiguousError
        curriculum = curricula[0]
        scope = translate_teacher_scope(curriculum, _selection(request))
        settings = _settings(request)
        active_generation = self._runtime.active_config
        teacher_intent = {
            "schema": "teacher-paper-intent.v1",
            "target": {
                "grade": curriculum.grade,
                "medium": curriculum.medium_code,
                "subject": curriculum.subject_code,
                "assessment_programme": curriculum.assessment_code,
            },
            "scope": request.scope.model_dump(mode="json"),
        }
        paper_settings = {
            "schema": "teacher-paper-settings.v1",
            "question_count": settings.question_count,
            "duration_minutes": settings.duration_minutes,
            "difficulty": settings.difficulty.value,
        }
        resolution = _resolution_snapshot(curriculum, scope)
        request_fingerprint = _fingerprint(
            {
                "curriculum_version_id": str(curriculum.curriculum_version_id),
                "intent": teacher_intent,
                "settings": paper_settings,
                "resolution": resolution,
            }
        )
        idempotency_hash = _fingerprint(
            {
                "actor_id": str(principal.subject_id),
                "idempotency_key": idempotency_key,
            }
        )
        job_id = uuid5(_TEACHER_PAPER_NAMESPACE, idempotency_hash)
        paper_reference = f"EGP-{job_id.hex[:4].upper()}-{job_id.hex[4:12].upper()}"
        title = f"Grade {curriculum.grade} {curriculum.subject_label} practice paper"
        max_cost = (
            settings.question_count
            * active_generation.budgets.max_total_cost_microusd
            * (MAX_SLOT_REGENERATIONS + 1)
        )
        stored = await self._repository.insert_job(
            {
                "id": job_id,
                "paper_reference": paper_reference,
                "created_by": principal.subject_id,
                "idempotency_key_hash": idempotency_hash,
                "request_fingerprint": request_fingerprint,
                "curriculum_version_id": curriculum.curriculum_version_id,
                "exam_configuration_id": curriculum.exam_configuration_id,
                "medium_id": curriculum.medium_id,
                "subject_id": curriculum.subject_id,
                "teacher_intent": teacher_intent,
                "paper_settings": paper_settings,
                "resolution_snapshot": resolution,
                "title": title,
                "paper_blueprint_id": None,
                "practice_paper_id": None,
                "status": PaperJobStatus.PREPARING.value,
                "version": 0,
                "slot_count": settings.question_count,
                "generated_count": 0,
                "validated_count": 0,
                "candidate_count": 0,
                "approved_count": 0,
                "failed_count": 0,
                "total_tokens": 0,
                "cost_microusd": 0,
                "max_cost_microusd": max_cost,
                "failure_code": None,
                "failure_detail": None,
                "actor_token": None,
                "actor_lease_expires_at": None,
                "dispatch_message_id": None,
                "completed_at": None,
            }
        )
        winner = stored.record.job
        if (
            winner.id != job_id
            or winner.request_fingerprint != request_fingerprint
            or winner.curriculum_version_id != curriculum.curriculum_version_id
            or winner.teacher_intent != teacher_intent
            or winner.paper_settings != paper_settings
        ):
            await self._session.rollback()
            raise TeacherPaperIdempotencyConflictError(idempotency_hash)
        if stored.created:
            self._audit(
                principal.subject_id,
                "teacher_paper.created",
                job_id,
                {
                    "paper_reference": paper_reference,
                    "curriculum_version_id": str(curriculum.curriculum_version_id),
                    "request_fingerprint": request_fingerprint,
                    "slot_count": settings.question_count,
                    "status": winner.status,
                },
            )
            await self._session.commit()
        if winner.dispatch_message_id is None:
            try:
                message_id = self._dispatcher.dispatch(job_id)
            except Exception as error:
                raise TeacherPaperQueueUnavailableError from error
            await self._repository.attach_dispatch_message(job_id, message_id)
            await self._session.commit()
        return TeacherPaperCreationResult(
            await self._repository.get(job_id),
            deduplicated=not stored.created,
        )

    async def get(self, job_id: UUID, *, principal: Principal) -> StoredTeacherPaper:
        authorize(principal, Permission.GENERATION_READ)
        return await self._repository.get(job_id)

    async def advance(
        self,
        job_id: UUID,
        *,
        expected_version: int,
        principal: Principal,
    ) -> StoredTeacherPaper:
        authorize(principal, Permission.GENERATION_RUN)
        record = await self._repository.get(job_id)
        if record.job.version != expected_version:
            raise TeacherPaperVersionConflictError(job_id)
        if record.job.status in {
            PaperJobStatus.READY_FOR_REVIEW.value,
            PaperJobStatus.FAILED.value,
        }:
            raise TeacherPaperStateConflictError(job_id)
        message_id = self._dispatch(job_id)
        await self._repository.cas_job(
            job_id,
            token=None,
            expected_version=record.job.version,
            values={"dispatch_message_id": record.job.dispatch_message_id or message_id},
        )
        self._audit(
            principal.subject_id,
            "teacher_paper.advance_requested",
            job_id,
            {"expected_version": expected_version},
        )
        await self._session.commit()
        return await self._repository.get(job_id)

    async def retry(
        self,
        job_id: UUID,
        *,
        expected_version: int,
        idempotency_key: str,
        principal: Principal,
        generation_dispatcher: GenerationDispatcher,
    ) -> StoredTeacherPaper:
        authorize(principal, Permission.GENERATION_RUN)
        _validate_idempotency_key(idempotency_key)
        record = await self._repository.get(job_id)
        job = record.job
        if job.version != expected_version:
            raise TeacherPaperVersionConflictError(job_id)
        failed_slots = tuple(
            slot for slot in record.slots if slot.status == PaperSlotStatus.FAILED.value
        )
        if job.status != PaperJobStatus.FAILED.value or not failed_slots:
            raise TeacherPaperStateConflictError(job_id)
        for slot in failed_slots:
            await _replace_generation_run(
                self._session,
                self._repository,
                job,
                slot,
                runtime=self._runtime,
                generation_dispatcher=generation_dispatcher,
                idempotency_key=f"{idempotency_key}-{slot.ordinal}",
                actor_id=principal.subject_id,
                reason="Explicit retry after a persisted slot failure.",
            )
        current = (await self._repository.get(job_id)).job
        counts = await _aggregate_counts(self._repository, job_id)
        await self._repository.cas_job(
            job_id,
            token=None,
            expected_version=current.version,
            values={
                **counts,
                "status": PaperJobStatus.GENERATING.value,
                "failure_code": None,
                "failure_detail": None,
                "completed_at": None,
            },
        )
        self._audit(
            principal.subject_id,
            "teacher_paper.retry_requested",
            job_id,
            {"slot_count": len(failed_slots)},
        )
        await self._session.commit()
        self._dispatch(job_id)
        return await self._repository.get(job_id)

    def _dispatch(self, job_id: UUID) -> str:
        try:
            return self._dispatcher.dispatch(job_id)
        except Exception as error:
            raise TeacherPaperQueueUnavailableError from error

    def _audit(
        self,
        actor_id: UUID,
        action: str,
        resource_id: UUID,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="teacher_paper",
                resource_id=resource_id,
                payload=payload,
            )
        )


def _resolution_snapshot(
    curriculum: ResolvedCurriculum,
    scope: ResolvedPaperScope,
) -> dict[str, object]:
    return {
        "schema": "teacher-paper-resolution.v1",
        "assessment": {
            "id": str(curriculum.exam_configuration_id),
            "code": curriculum.assessment_code,
            "label": curriculum.assessment_label,
        },
        "medium": {
            "id": str(curriculum.medium_id),
            "code": curriculum.medium_code,
            "label": curriculum.medium_label,
        },
        "subject": {
            "id": str(curriculum.subject_id),
            "code": curriculum.subject_code,
            "label": curriculum.subject_label,
        },
        "curriculum": {
            "id": str(curriculum.curriculum_version_id),
            "code": curriculum.curriculum_code,
            "label": curriculum.curriculum_title,
        },
        "scope_summary": scope.summary,
        "unit_ids": [str(value) for value in scope.unit_ids],
        "lesson_ids": [str(value) for value in scope.lesson_ids],
        "lessons": [
            {
                "number": numbered.number,
                "id": str(numbered.lesson.id),
                "code": numbered.lesson.code,
                "title": numbered.lesson.title,
                "unit_id": str(numbered.lesson.unit_id),
                "unit_code": numbered.lesson.unit_code,
                "unit_title": numbered.lesson.unit_title,
                "taxonomy": [
                    {
                        "competency_id": str(target.competency_id),
                        "skill_id": str(target.skill_id) if target.skill_id else None,
                        "sub_skill_id": str(target.sub_skill_id) if target.sub_skill_id else None,
                        "learning_concept_id": (
                            str(target.learning_concept_id) if target.learning_concept_id else None
                        ),
                        "label": target.label,
                    }
                    for target in numbered.lesson.taxonomy_targets
                ],
            }
            for numbered in scope.lessons
        ],
    }


async def _resolved_job_scope(
    repository: TeacherPaperRepository,
    job: TeacherPaperJobModel,
) -> tuple[ResolvedCurriculum, ResolvedPaperScope, PaperSettings]:
    curricula = await repository.list_curricula()
    curriculum = next(
        (
            item
            for item in curricula
            if item.curriculum_version_id == job.curriculum_version_id
            and item.exam_configuration_id == job.exam_configuration_id
            and item.medium_id == job.medium_id
            and item.subject_id == job.subject_id
        ),
        None,
    )
    if curriculum is None:
        raise TeacherPaperCurriculumNotFoundError
    raw_scope = cast(dict[str, object], job.teacher_intent["scope"])
    kind = TeacherScopeKind(cast(str, raw_scope["kind"]))
    selection = TeacherScopeSelection(
        kind=kind,
        start_lesson=cast(int | None, raw_scope.get("start_lesson")),
        end_lesson=cast(int | None, raw_scope.get("end_lesson")),
    )
    scope = translate_teacher_scope(curriculum, selection)
    settings = PaperSettings(
        question_count=cast(int, job.paper_settings["question_count"]),
        duration_minutes=cast(int, job.paper_settings["duration_minutes"]),
        difficulty=PaperDifficulty(cast(str, job.paper_settings["difficulty"])),
    )
    return curriculum, scope, settings


class TeacherPaperWorkerService:
    def __init__(
        self,
        session: AsyncSession,
        paper_dispatcher: PaperGenerationDispatcher,
        generation_dispatcher: GenerationDispatcher,
        runtime: GenerationRuntimeRegistry,
        embeddings: EmbeddingProviderRegistry,
        pipeline: ValidationPipeline,
        *,
        actor_lease_seconds: int = _ACTOR_LEASE_SECONDS,
    ) -> None:
        if not 1 <= actor_lease_seconds <= 86_400:
            raise ValueError("actor_lease_seconds must be bounded")
        self._session = session
        self._paper_dispatcher = paper_dispatcher
        self._generation_dispatcher = generation_dispatcher
        self._runtime = runtime
        self._embeddings = embeddings
        self._pipeline = pipeline
        self._actor_lease_seconds = actor_lease_seconds
        self._repository = TeacherPaperRepository(session)

    async def advance(self, job_id: UUID) -> bool:
        token = uuid4()
        claimed = await self._repository.claim(
            job_id,
            token=token,
            lease_seconds=self._actor_lease_seconds,
            now=datetime.now(UTC),
        )
        if claimed is None:
            return False
        try:
            if claimed.status == PaperJobStatus.PREPARING.value:
                await self._prepare(claimed, token)
            elif claimed.status == PaperJobStatus.GENERATING.value:
                record = await self._repository.get(job_id)
                if len(record.slots) < record.job.slot_count:
                    await self._prepare(record.job, token)
                else:
                    await self._collect_generation(record.job, token)
            elif claimed.status == PaperJobStatus.CHECKING_ANSWERS.value:
                await self._validate(claimed, token)
        except TeacherPaperContextUnavailableError:
            await self._fail(
                job_id,
                token,
                code="paper_generation_context_unavailable",
                detail="No active reviewed source matched an exact paper slot scope.",
            )
        except (ActiveEmbeddingConfigUnavailableError, EmbeddingProviderUnavailableError):
            await self._fail(
                job_id,
                token,
                code="paper_generation_embedding_unavailable",
                detail="The configured query embedding provider is unavailable.",
            )
        except PaperScopeError as error:
            await self._fail(
                job_id,
                token,
                code=error.code,
                detail="The persisted curriculum scope can no longer be translated safely.",
            )
        except Exception:
            await self._session.rollback()
            await self._fail(
                job_id,
                token,
                code="paper_generation_internal_error",
                detail="The paper worker failed safely; no automatic provider retry was started.",
            )
        finally:
            await self._repository.release(job_id, token=token)
        return True

    async def _prepare(self, job: TeacherPaperJobModel, token: UUID) -> None:
        curriculum, scope, settings = await _resolved_job_scope(self._repository, job)
        specification = build_blueprint_specification(
            curriculum,
            scope,
            settings,
            paper_reference=job.paper_reference,
            request_fingerprint=job.request_fingerprint,
        )
        seed = int(job.request_fingerprint[-16:], 16) % (2**63)
        if job.paper_blueprint_id is None:
            creation = await BlueprintGenerationService(self._session).create_blueprint(
                job.curriculum_version_id,
                specification,
                seed=seed,
                analytics_run_id=None,
                actor_id=job.created_by,
            )
            blueprint_record = creation.record
            current = (await self._repository.get(job.id)).job
            job = await self._repository.cas_job(
                job.id,
                token=token,
                expected_version=current.version,
                values={
                    "paper_blueprint_id": blueprint_record.id,
                    "status": PaperJobStatus.GENERATING.value,
                },
            )
            await self._session.commit()
        else:
            blueprint_record = await BlueprintGenerationService(self._session).get_blueprint(
                job.curriculum_version_id,
                job.paper_blueprint_id,
            )
        blueprint = deserialize_blueprint(blueprint_record.blueprint)
        assignments = assign_blueprint_lessons(blueprint, scope)
        existing = {
            slot.blueprint_slot_id: slot for slot in await self._repository.list_slots(job.id)
        }
        for assignment in assignments:
            if assignment.slot_id in existing:
                continue
            slot = next(item for item in blueprint.slots if item.slot_id == assignment.slot_id)
            query = " | ".join(
                (
                    f"Grade {curriculum.grade}",
                    curriculum.medium_code,
                    curriculum.subject_label,
                    curriculum.curriculum_code,
                    assignment.lesson.unit_title,
                    assignment.lesson.title,
                    assignment.taxonomy_target.label,
                    *slot.generation_constraints.retrieval_query_hints,
                )
            )
            retrieval_scope = RetrievalScope(
                grade=curriculum.grade,
                exam_id=curriculum.exam_configuration_id,
                medium_id=curriculum.medium_id,
                subject_id=curriculum.subject_id,
                curriculum_version_id=curriculum.curriculum_version_id,
                unit_ids=(assignment.lesson.unit_id,),
                lesson_ids=(assignment.lesson.id,),
                taxonomy=TaxonomyScope(
                    competency_id=assignment.taxonomy_target.competency_id,
                    skill_id=assignment.taxonomy_target.skill_id,
                    sub_skill_id=assignment.taxonomy_target.sub_skill_id,
                    learning_concept_id=assignment.taxonomy_target.learning_concept_id,
                ),
            )
            embedding_config = self._embeddings.active_config
            query_vector = self._embeddings.embed_query(query, embedding_config).vector
            retrieval = await HybridRetrievalService(
                PostgresHybridRetrievalRepository(
                    self._session,
                    embedding_config=embedding_config,
                    candidate_limit=16,
                ),
                fusion_config=FusionConfig(limit=8, max_candidates_per_channel=16),
                context_limits=ContextLimits(
                    max_items=8,
                    max_total_characters=24_000,
                    max_item_characters=8_000,
                ),
            ).retrieve(query=query, query_vector=query_vector, filters=retrieval_scope)
            if not retrieval.context.items:
                raise TeacherPaperContextUnavailableError(assignment.slot_id)
            record_ids = tuple(
                dict.fromkeys(
                    record_id
                    for item in retrieval.context.items
                    for record_id in item.source_chunk_ids
                )
            )[:16]
            knowledge_ids, question_ids = _require_context_ids(
                *(await self._repository.split_context_ids(record_ids)),
                assignment.slot_id,
            )
            generation = await GenerationRunService(
                self._session,
                self._runtime,
                self._generation_dispatcher,
            ).create(
                job.curriculum_version_id,
                paper_blueprint_id=blueprint_record.id,
                slot_id=assignment.slot_id,
                knowledge_chunk_ids=knowledge_ids,
                historical_question_ids=question_ids,
                idempotency_key=f"tp-{job.id.hex}-{assignment.ordinal}-1",
                actor_id=job.created_by,
            )
            slot_model = TeacherPaperSlotModel(
                id=uuid5(_TEACHER_PAPER_NAMESPACE, f"{job.id}:{assignment.slot_id}"),
                paper_job_id=job.id,
                curriculum_version_id=job.curriculum_version_id,
                ordinal=assignment.ordinal,
                blueprint_slot_id=assignment.slot_id,
                unit_id=assignment.lesson.unit_id,
                lesson_id=assignment.lesson.id,
                lesson_number=assignment.lesson_number,
                competency_id=assignment.taxonomy_target.competency_id,
                skill_id=assignment.taxonomy_target.skill_id,
                sub_skill_id=assignment.taxonomy_target.sub_skill_id,
                learning_concept_id=assignment.taxonomy_target.learning_concept_id,
                current_generation_run_id=generation.run.id,
                current_validation_run_id=None,
                current_candidate_id=None,
                status=PaperSlotStatus.GENERATING.value,
                version=0,
                regeneration_count=0,
                requires_revalidation=False,
                failure_code=None,
            )
            self._session.add(slot_model)
            await self._session.flush()
            await self._repository.add_slot_run(
                slot_model,
                generation_run_id=generation.run.id,
                sequence=1,
                reason="Initial exact-scope teacher paper generation.",
                requested_by=job.created_by,
                link_id=uuid5(
                    _TEACHER_PAPER_NAMESPACE,
                    f"{slot_model.id}:run:{generation.run.id}",
                ),
            )
            await self._session.commit()
        current = (await self._repository.get(job.id)).job
        counts = await _aggregate_counts(self._repository, job.id)
        await self._repository.cas_job(
            job.id,
            token=token,
            expected_version=current.version,
            values={**counts, "status": PaperJobStatus.GENERATING.value},
        )
        await self._session.commit()

    async def _collect_generation(self, job: TeacherPaperJobModel, token: UUID) -> None:
        slots = await self._repository.list_slots(job.id)
        runs = {
            slot.id: await self._repository.generation_run(
                cast(UUID, slot.current_generation_run_id)
            )
            for slot in slots
        }
        failed = tuple(
            (slot, runs[slot.id])
            for slot in slots
            if runs[slot.id].status == GenerationRunStatus.FAILED.value
        )
        if failed:
            for slot, run in failed:
                if slot.status != PaperSlotStatus.FAILED.value:
                    await self._repository.cas_slot(
                        slot,
                        {
                            "status": PaperSlotStatus.FAILED.value,
                            "failure_code": run.failure_code or "generation_failed",
                        },
                    )
            await self._session.commit()
            await self._fail(
                job.id,
                token,
                code="paper_generation_slot_failed",
                detail="One or more question generations failed; explicit retry is available.",
            )
            return
        if any(run.status != GenerationRunStatus.SUCCEEDED.value for run in runs.values()):
            current = (await self._repository.get(job.id)).job
            await self._repository.cas_job(
                job.id,
                token=token,
                expected_version=current.version,
                values=await _aggregate_counts(self._repository, job.id),
            )
            await self._session.commit()
            return
        for slot in slots:
            if slot.status == PaperSlotStatus.GENERATING.value:
                await self._repository.cas_slot(
                    slot,
                    {
                        "status": PaperSlotStatus.CHECKING_ANSWERS.value,
                        "failure_code": None,
                    },
                )
        await self._session.commit()
        current = (await self._repository.get(job.id)).job
        await self._repository.cas_job(
            job.id,
            token=token,
            expected_version=current.version,
            values={
                **(await _aggregate_counts(self._repository, job.id)),
                "status": PaperJobStatus.CHECKING_ANSWERS.value,
            },
        )
        await self._session.commit()
        await self._validate((await self._repository.get(job.id)).job, token)

    async def _validate(self, job: TeacherPaperJobModel, token: UUID) -> None:
        internal_principal = Principal(job.created_by, frozenset({AdminRole.ADMIN}))
        for slot in await self._repository.list_slots(job.id):
            if slot.current_candidate_id is not None or slot.status == PaperSlotStatus.FAILED.value:
                continue
            generation_run_id = cast(UUID, slot.current_generation_run_id)
            validation = await ValidationRunService(
                self._session,
                self._pipeline,
            ).create(
                job.curriculum_version_id,
                generation_run_id=generation_run_id,
                actor_id=job.created_by,
            )
            slot = await self._repository.find_slot(job.id, generation_run_id)
            if validation.run.overall_status == "fail":
                await self._repository.cas_slot(
                    slot,
                    {
                        "current_validation_run_id": validation.run.id,
                        "status": PaperSlotStatus.FAILED.value,
                        "failure_code": "validation_failed",
                    },
                )
                await self._session.commit()
                continue
            candidate = await ReviewCandidateService(self._session).create(
                job.curriculum_version_id,
                validation_run_id=validation.run.id,
                principal=internal_principal,
            )
            slot = await self._repository.find_slot(job.id, generation_run_id)
            await self._repository.cas_slot(
                slot,
                {
                    "current_validation_run_id": validation.run.id,
                    "current_candidate_id": candidate.record.candidate.id,
                    "status": PaperSlotStatus.AWAITING_REVIEW.value,
                    "requires_revalidation": False,
                    "failure_code": None,
                },
            )
            await self._session.commit()
        current = (await self._repository.get(job.id)).job
        counts = await _aggregate_counts(self._repository, job.id)
        any_failed = counts["failed_count"] > 0
        terminal_status = (
            PaperJobStatus.FAILED.value if any_failed else PaperJobStatus.READY_FOR_REVIEW.value
        )
        values: dict[str, object] = {
            **counts,
            "status": terminal_status,
            "completed_at": datetime.now(UTC),
            "failure_code": "paper_generation_validation_failed" if any_failed else None,
            "failure_detail": (
                "One or more canonical subject checks failed; evidence remains available."
                if any_failed
                else None
            ),
        }
        await self._repository.cas_job(
            job.id,
            token=token,
            expected_version=current.version,
            values=values,
        )
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=job.created_by,
                action=("teacher_paper.failed" if any_failed else "teacher_paper.ready_for_review"),
                resource_type="teacher_paper",
                resource_id=job.id,
                payload={"status": terminal_status, **counts},
            )
        )
        await self._session.commit()

    async def _fail(self, job_id: UUID, token: UUID, *, code: str, detail: str) -> None:
        record = await self._repository.get(job_id)
        if record.job.status == PaperJobStatus.FAILED.value:
            return
        counts = await _aggregate_counts(self._repository, job_id)
        await self._repository.cas_job(
            job_id,
            token=token,
            expected_version=record.job.version,
            values={
                **counts,
                "status": PaperJobStatus.FAILED.value,
                "failure_code": code,
                "failure_detail": detail,
                "completed_at": datetime.now(UTC),
            },
        )
        await self._session.commit()


async def _aggregate_counts(
    repository: TeacherPaperRepository,
    job_id: UUID,
) -> dict[str, int]:
    slots = await repository.list_slots(job_id)
    current_runs = [
        await repository.generation_run(slot.current_generation_run_id)
        for slot in slots
        if slot.current_generation_run_id is not None
    ]
    accounting = (
        await repository.session.execute(
            select(
                func.coalesce(func.sum(GenerationRunModel.total_tokens), 0),
                func.coalesce(func.sum(GenerationRunModel.cost_microusd), 0),
            )
            .join(
                TeacherPaperSlotRunModel,
                TeacherPaperSlotRunModel.generation_run_id == GenerationRunModel.id,
            )
            .where(TeacherPaperSlotRunModel.paper_job_id == job_id)
        )
    ).one()
    return {
        "generated_count": sum(
            run.status == GenerationRunStatus.SUCCEEDED.value for run in current_runs
        ),
        "validated_count": sum(slot.current_validation_run_id is not None for slot in slots),
        "candidate_count": sum(slot.current_candidate_id is not None for slot in slots),
        "approved_count": sum(slot.status == PaperSlotStatus.APPROVED.value for slot in slots),
        "failed_count": sum(slot.status == PaperSlotStatus.FAILED.value for slot in slots),
        "total_tokens": int(accounting[0]),
        "cost_microusd": int(accounting[1]),
    }


async def _replace_generation_run(
    session: AsyncSession,
    repository: TeacherPaperRepository,
    job: TeacherPaperJobModel,
    slot: TeacherPaperSlotModel,
    *,
    runtime: GenerationRuntimeRegistry,
    generation_dispatcher: GenerationDispatcher,
    idempotency_key: str,
    actor_id: UUID,
    reason: str,
) -> TeacherPaperSlotModel:
    if slot.regeneration_count >= MAX_SLOT_REGENERATIONS:
        raise TeacherPaperRetryLimitError(slot.id)
    current_run_id = cast(UUID, slot.current_generation_run_id)
    current_run = await repository.generation_run(current_run_id)
    accounting = await _aggregate_counts(repository, job.id)
    projected_cost = (
        accounting["cost_microusd"] + runtime.active_config.budgets.max_total_cost_microusd
    )
    if projected_cost > job.max_cost_microusd:
        raise TeacherPaperCostLimitError(job.id)
    generation_service = GenerationRunService(session, runtime, generation_dispatcher)
    if current_run.status == GenerationRunStatus.FAILED.value:
        creation = await generation_service.retry(
            job.curriculum_version_id,
            current_run.id,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
        )
    else:
        creation = await generation_service.create(
            job.curriculum_version_id,
            paper_blueprint_id=current_run.paper_blueprint_id,
            slot_id=current_run.slot_id,
            knowledge_chunk_ids=tuple(UUID(value) for value in current_run.knowledge_chunk_ids),
            historical_question_ids=tuple(
                UUID(value) for value in current_run.historical_question_ids
            ),
            idempotency_key=idempotency_key,
            actor_id=actor_id,
        )
    slot = await repository.find_slot(job.id, current_run_id)
    slot = await repository.cas_slot(
        slot,
        {
            "current_generation_run_id": creation.run.id,
            "current_validation_run_id": None,
            "current_candidate_id": None,
            "status": PaperSlotStatus.GENERATING.value,
            "regeneration_count": slot.regeneration_count + 1,
            "requires_revalidation": False,
            "failure_code": None,
        },
    )
    await repository.add_slot_run(
        slot,
        generation_run_id=creation.run.id,
        sequence=slot.regeneration_count + 1,
        reason=reason,
        requested_by=actor_id,
        link_id=uuid5(
            _TEACHER_PAPER_NAMESPACE,
            f"{slot.id}:run:{creation.run.id}",
        ),
    )
    await session.commit()
    return slot


class TeacherPaperRecoveryService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: PaperGenerationDispatcher,
        *,
        batch_size: int,
        actor_lease_seconds: int,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if not 1 <= actor_lease_seconds <= 86_400:
            raise ValueError("actor_lease_seconds must be bounded")
        self._session = session
        self._dispatcher = dispatcher
        self._batch_size = batch_size
        self._actor_lease_seconds = actor_lease_seconds
        self._repository = TeacherPaperRepository(session)

    async def recover(self) -> TeacherPaperRecoveryResult:
        job_ids = await self._repository.recoverable_job_ids(
            now=datetime.now(UTC),
            limit=self._batch_size,
        )
        await self._session.commit()
        dispatched = 0
        failures = 0
        for job_id in job_ids:
            try:
                self._dispatcher.dispatch(job_id)
            except Exception:
                failures += 1
            else:
                dispatched += 1
        return TeacherPaperRecoveryResult(
            scanned=len(job_ids),
            dispatched=dispatched,
            failures=failures,
        )


async def teacher_paper_job_response(
    repository: TeacherPaperRepository,
    record: StoredTeacherPaper,
    *,
    deduplicated: bool = False,
) -> TeacherPaperJobResponse:
    job = record.job
    resolution = job.resolution_snapshot
    subject = cast(dict[str, object], resolution["subject"])
    medium = cast(dict[str, object], resolution["medium"])
    lesson_labels = {
        cast(int, cast(dict[str, object], item)["number"]): (
            f"Lesson {cast(dict[str, object], item)['number']} — "
            f"{cast(dict[str, object], item)['title']}"
        )
        for item in cast(list[object], resolution["lessons"])
    }
    slots: list[TeacherPaperSlotProgressResponse] = []
    for slot in record.slots:
        validation_status = None
        if slot.current_validation_run_id is not None:
            validation = await repository.session.get(
                ValidationRunModel,
                slot.current_validation_run_id,
            )
            if validation is not None:
                validation_status = {
                    "pass": "ready",
                    "warn": "needs_attention",
                    "fail": "failed_check",
                }[validation.overall_status]
        slots.append(
            TeacherPaperSlotProgressResponse(
                id=slot.id,
                number=slot.ordinal,
                status=slot.status,
                version=slot.version,
                lesson=lesson_labels.get(slot.lesson_number),
                validation=cast(FriendlyValidationStatus | None, validation_status),
                generation_run_id=slot.current_generation_run_id,
                candidate_id=slot.current_candidate_id,
                failure=(
                    None
                    if slot.failure_code is None
                    else TeacherPaperFailureResponse(
                        code=slot.failure_code,
                        message="This question needs an explicit retry or regeneration.",
                    )
                ),
            )
        )
    stages = ["preparing", "generating", "checking_answers", "ready_for_review"]
    if job.status == PaperJobStatus.FAILED.value:
        progress = (
            *stages[: max(1, min(3, 1 + int(bool(record.slots))))],
            "failed",
        )
    else:
        index = {
            PaperJobStatus.PREPARING.value: 1,
            PaperJobStatus.GENERATING.value: 2,
            PaperJobStatus.CHECKING_ANSWERS.value: 3,
            PaperJobStatus.READY_FOR_REVIEW.value: 4,
        }[job.status]
        progress = tuple(stages[:index])
    return TeacherPaperJobResponse(
        job_id=job.id,
        paper_id=job.id,
        paper_reference=job.paper_reference,
        title=job.title,
        grade=cast(int, cast(dict[str, object], job.teacher_intent["target"])["grade"]),
        medium=cast(str, medium["label"]),
        subject=cast(str, subject["label"]),
        scope_summary=cast(str, resolution["scope_summary"]),
        status=cast(TeacherPaperStatus, job.status),
        progress=progress,
        counts=TeacherPaperCountsResponse(
            requested=job.slot_count,
            generated=job.generated_count,
            validated=job.validated_count,
            candidates=job.candidate_count,
            approved=job.approved_count,
            failed=job.failed_count,
        ),
        cost_microusd=job.cost_microusd,
        total_tokens=job.total_tokens,
        version=job.version,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
        review_url=(
            f"/admin/review-approve?paper={job.id}"
            if job.status in {PaperJobStatus.READY_FOR_REVIEW.value, PaperJobStatus.FAILED.value}
            else None
        ),
        failure=(
            None
            if job.failure_code is None
            else TeacherPaperFailureResponse(
                code=job.failure_code,
                message=job.failure_detail or "The paper job failed safely.",
            )
        ),
        slots=tuple(slots),
        deduplicated=deduplicated,
    )


def _review_status(job: TeacherPaperJobModel) -> str:
    if job.practice_paper_id is not None:
        return "draft_created"
    if job.status == PaperJobStatus.FAILED.value:
        return "failed_check"
    if job.approved_count == job.slot_count:
        return "approved"
    if job.approved_count > 0:
        return "in_review"
    return "awaiting_review"


def _content_snapshot(raw: dict[str, object]) -> dict[str, object]:
    answer_value = raw.get("answer")
    marking_value = raw.get("marking")
    if isinstance(answer_value, dict):
        answer = answer_value.get("correct_option_id")
        if answer is None:
            accepted = answer_value.get("accepted_responses")
            answer = (
                " / ".join(str(item) for item in accepted)
                if isinstance(accepted, list)
                else "Answer requires review"
            )
        explanation = str(answer_value.get("explanation") or "Explanation requires review")
        marking = marking_value if isinstance(marking_value, dict) else {}
        criteria_value = marking.get("criteria")
        criteria = (
            tuple(
                str(item.get("description"))
                for item in criteria_value
                if isinstance(item, dict) and item.get("description")
            )
            if isinstance(criteria_value, list)
            else ()
        )
        return {
            "question_type": raw["question_type"],
            "stem": raw["stem"],
            "options": raw.get("options", []),
            "answer": str(answer),
            "explanation": explanation,
            "marks": int(cast(int, marking.get("total_marks", 1))),
            "marking_guide": list(criteria or ("Review the proposed answer.",)),
        }
    return raw


def _question_content(raw: dict[str, object]) -> QuestionContentResponse:
    return QuestionContentResponse.model_validate(_content_snapshot(raw))


def _friendly_validation(source: ReviewSlotSource) -> ReviewValidationResponse:
    if source.validation is None:
        return ReviewValidationResponse(
            status="failed_check",
            summary="Automated validation did not complete.",
            findings=("This question cannot be approved until a fresh check completes.",),
        )
    status = {
        "pass": "ready",
        "warn": "needs_attention",
        "fail": "failed_check",
    }[source.validation.overall_status]
    messages = tuple(
        finding.message for finding in source.findings if finding.status in {"warn", "fail"}
    )
    if source.slot.requires_revalidation:
        return ReviewValidationResponse(
            status="needs_attention",
            summary="This edited question requires fresh generation and validation.",
            findings=(
                "The previous validation applies only to the generated revision, not this edit.",
                *messages,
            ),
        )
    summaries = {
        "ready": "The automated answer and source checks passed.",
        "needs_attention": "One or more checks need human attention.",
        "failed_check": "A canonical automated check failed.",
    }
    return ReviewValidationResponse(
        status=cast(FriendlyValidationStatus, status),
        summary=summaries[status],
        findings=messages,
    )


def _review_question(job: TeacherPaperJobModel, source: ReviewSlotSource) -> ReviewQuestionResponse:
    content = _question_content(source.content)
    context_items = cast(list[dict[str, object]], source.generation.context_snapshot["items"])
    sources: list[ReviewSourceResponse] = []
    seen_sources: set[tuple[UUID, int]] = set()
    for item in context_items:
        provenance = cast(dict[str, object], item["provenance"])
        source_id = UUID(cast(str, provenance["source_document_id"]))
        page = cast(int, provenance["page_number"])
        key = (source_id, page)
        if key in seen_sources:
            continue
        seen_sources.add(key)
        filename = source.filenames.get(source_id, "Reviewed source material")
        sources.append(ReviewSourceResponse(filename=filename, title=filename, page=page))
    validation_run_id = source.validation.id if source.validation is not None else None
    candidate_id = source.candidate.id if source.candidate is not None else None
    review_state = (
        "failed_check"
        if source.candidate is None
        else (
            "awaiting_review"
            if source.candidate.state == CandidateState.VALIDATED.value
            else source.candidate.state
        )
    )
    technical_findings = tuple(
        TechnicalValidationFindingResponse(
            code=finding.code,
            status=cast(TechnicalFindingStatus, finding.status),
            message=finding.message,
            evidence=tuple(finding.evidence),
        )
        for finding in source.findings
    )
    return ReviewQuestionResponse(
        id=source.generation.id,
        number=source.slot.ordinal,
        version=source.candidate.version if source.candidate is not None else 0,
        aggregate_slot_version=source.slot.version,
        review_state=review_state,
        requires_revalidation=source.slot.requires_revalidation,
        stem=content.stem,
        options=tuple(
            ReviewQuestionOptionResponse(label=option.option_id, text=option.text)
            for option in content.options
        ),
        answer=content.answer,
        explanation=content.explanation,
        marking_scheme=ReviewMarkingSchemeResponse(
            total_marks=content.marks,
            criteria=content.marking_guide,
        ),
        content=content,
        scope=ReviewQuestionScopeResponse(
            grade=cast(int, cast(dict[str, object], job.teacher_intent["target"])["grade"]),
            subject=cast(str, cast(dict[str, object], job.resolution_snapshot["subject"])["label"]),
            lessons=cast(str, job.resolution_snapshot["scope_summary"]),
            unit=source.unit_title,
            lesson=f"Lesson {source.slot.lesson_number} — {source.lesson_title}",
            taxonomy=source.taxonomy_title,
        ),
        sources=tuple(sources),
        validation=_friendly_validation(source),
        technical_details=ReviewQuestionTechnicalDetailsResponse(
            generation_run_id=source.generation.id,
            validation_run_id=validation_run_id,
            candidate_id=candidate_id,
            blueprint_slot_id=source.slot.blueprint_slot_id,
            context_ids=tuple(str(item["context_id"]) for item in context_items),
            provider=source.generation.provider,
            model_version=source.generation.model_version,
            validator_findings=technical_findings,
        ),
    )


class TeacherPaperReviewService:
    def __init__(
        self,
        session: AsyncSession,
        paper_dispatcher: PaperGenerationDispatcher,
        generation_dispatcher: GenerationDispatcher,
        runtime: GenerationRuntimeRegistry,
    ) -> None:
        self._session = session
        self._paper_dispatcher = paper_dispatcher
        self._generation_dispatcher = generation_dispatcher
        self._runtime = runtime
        self._repository = TeacherPaperRepository(session)

    async def list(
        self,
        *,
        principal: Principal,
        limit: int,
        offset: int,
    ) -> ReviewPaperListResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        jobs = await self._repository.list_review_jobs(limit=limit, offset=offset)
        return ReviewPaperListResponse(
            items=tuple(
                ReviewPaperSummaryResponse(
                    id=job.id,
                    paper_reference=job.paper_reference,
                    title=job.title,
                    grade=cast(int, cast(dict[str, object], job.teacher_intent["target"])["grade"]),
                    subject=cast(
                        str,
                        cast(dict[str, object], job.resolution_snapshot["subject"])["label"],
                    ),
                    scope_summary=cast(str, job.resolution_snapshot["scope_summary"]),
                    status=_review_status(job),
                    question_count=job.slot_count,
                    approved_count=job.approved_count,
                    created_at=job.created_at,
                )
                for job in jobs
            )
        )

    async def get(self, job_id: UUID, *, principal: Principal) -> ReviewPaperDetailResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.get(job_id)
        job = record.job
        if job.paper_blueprint_id is None:
            raise TeacherPaperStateConflictError(job_id)
        sources = await self._repository.review_sources(job_id)
        return ReviewPaperDetailResponse(
            id=job.id,
            paper_reference=job.paper_reference,
            title=job.title,
            grade=cast(int, cast(dict[str, object], job.teacher_intent["target"])["grade"]),
            medium=cast(
                str,
                cast(dict[str, object], job.resolution_snapshot["medium"])["label"],
            ),
            subject=cast(
                str,
                cast(dict[str, object], job.resolution_snapshot["subject"])["label"],
            ),
            scope_summary=cast(str, job.resolution_snapshot["scope_summary"]),
            status=_review_status(job),
            version=job.version,
            created_at=job.created_at,
            questions=tuple(_review_question(job, source) for source in sources),
            draft=(
                None
                if job.practice_paper_id is None
                else ReviewPaperDraftResponse(draft_id=job.practice_paper_id, version=1)
            ),
            technical_details=ReviewPaperTechnicalDetailsResponse(
                curriculum_version_id=job.curriculum_version_id,
                paper_blueprint_id=job.paper_blueprint_id,
                request_fingerprint=job.request_fingerprint,
                cost_microusd=job.cost_microusd,
                total_tokens=job.total_tokens,
            ),
        )

    async def start(
        self,
        job_id: UUID,
        question_id: UUID,
        *,
        expected_version: int,
        principal: Principal,
    ) -> ReviewQuestionResponse:
        slot = await self._require_candidate_slot(job_id, question_id, principal)
        candidate_id = cast(UUID, slot.current_candidate_id)
        try:
            await ReviewCandidateService(self._session).start_review(
                slot.curriculum_version_id,
                candidate_id,
                expected_version=expected_version,
                principal=principal,
            )
        except ReviewCandidateVersionConflictError as error:
            raise TeacherPaperVersionConflictError(question_id) from error
        except ReviewCandidateStateConflictError as error:
            raise TeacherPaperStateConflictError(question_id) from error
        await self._sync_slot(job_id, question_id, PaperSlotStatus.IN_REVIEW)
        return await self._get_question(job_id, question_id, principal)

    async def edit(
        self,
        job_id: UUID,
        question_id: UUID,
        request: ReviewQuestionEditRequest,
        *,
        principal: Principal,
    ) -> ReviewQuestionResponse:
        slot = await self._require_candidate_slot(job_id, question_id, principal)
        candidate_id = cast(UUID, slot.current_candidate_id)
        try:
            await ReviewCandidateService(self._session).edit(
                slot.curriculum_version_id,
                candidate_id,
                content=request.content.to_domain(),
                reason=request.reason,
                expected_version=request.expected_version,
                principal=principal,
            )
        except ReviewCandidateVersionConflictError as error:
            raise TeacherPaperVersionConflictError(question_id) from error
        except ReviewCandidateStateConflictError as error:
            raise TeacherPaperStateConflictError(question_id) from error
        await self._sync_slot(
            job_id,
            question_id,
            PaperSlotStatus.REVALIDATION_REQUIRED,
            requires_revalidation=True,
        )
        return await self._get_question(job_id, question_id, principal)

    async def approve(
        self,
        job_id: UUID,
        question_id: UUID,
        *,
        expected_version: int,
        note: str | None,
        principal: Principal,
    ) -> ReviewQuestionResponse:
        slot = await self._require_candidate_slot(job_id, question_id, principal)
        if slot.requires_revalidation:
            raise TeacherPaperRevalidationRequiredError(question_id)
        try:
            await ReviewCandidateService(self._session).approve(
                slot.curriculum_version_id,
                cast(UUID, slot.current_candidate_id),
                expected_version=expected_version,
                note=note,
                principal=principal,
            )
        except (
            ReviewCandidateRevalidationRequiredError,
            ValidationNotPassedError,
        ) as error:
            raise TeacherPaperRevalidationRequiredError(question_id) from error
        except ReviewCandidateVersionConflictError as error:
            raise TeacherPaperVersionConflictError(question_id) from error
        except ReviewCandidateStateConflictError as error:
            raise TeacherPaperStateConflictError(question_id) from error
        await self._sync_slot(job_id, question_id, PaperSlotStatus.APPROVED)
        return await self._get_question(job_id, question_id, principal)

    async def reject(
        self,
        job_id: UUID,
        question_id: UUID,
        *,
        expected_version: int,
        reason: str,
        principal: Principal,
    ) -> ReviewQuestionResponse:
        slot = await self._require_candidate_slot(job_id, question_id, principal)
        try:
            await ReviewCandidateService(self._session).reject(
                slot.curriculum_version_id,
                cast(UUID, slot.current_candidate_id),
                expected_version=expected_version,
                reason=reason,
                principal=principal,
            )
        except ReviewCandidateVersionConflictError as error:
            raise TeacherPaperVersionConflictError(question_id) from error
        except ReviewCandidateStateConflictError as error:
            raise TeacherPaperStateConflictError(question_id) from error
        await self._sync_slot(job_id, question_id, PaperSlotStatus.REJECTED)
        return await self._get_question(job_id, question_id, principal)

    async def regenerate(
        self,
        job_id: UUID,
        question_id: UUID,
        request: ReviewQuestionRegenerateRequest,
        *,
        idempotency_key: str,
        principal: Principal,
    ) -> ReviewQuestionRegenerationResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        _validate_idempotency_key(idempotency_key)
        record = await self._repository.get(job_id)
        slot = await self._repository.find_slot(job_id, question_id)
        if slot.version != request.expected_version:
            raise TeacherPaperVersionConflictError(question_id)
        replacement = await _replace_generation_run(
            self._session,
            self._repository,
            record.job,
            slot,
            runtime=self._runtime,
            generation_dispatcher=self._generation_dispatcher,
            idempotency_key=idempotency_key,
            actor_id=principal.subject_id,
            reason=request.reason,
        )
        current = (await self._repository.get(job_id)).job
        await self._repository.cas_job(
            job_id,
            token=None,
            expected_version=current.version,
            values={
                **(await _aggregate_counts(self._repository, job_id)),
                "status": PaperJobStatus.GENERATING.value,
                "failure_code": None,
                "failure_detail": None,
                "completed_at": None,
            },
        )
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=principal.subject_id,
                action="teacher_paper.question_regenerated",
                resource_type="teacher_paper",
                resource_id=job_id,
                payload={
                    "slot_id": str(replacement.id),
                    "generation_run_id": str(replacement.current_generation_run_id),
                    "reason_recorded": True,
                },
            )
        )
        await self._session.commit()
        try:
            self._paper_dispatcher.dispatch(job_id)
        except Exception as error:
            raise TeacherPaperQueueUnavailableError from error
        return ReviewQuestionRegenerationResponse(
            job_id=job_id,
            paper_id=job_id,
            question_id=cast(UUID, replacement.current_generation_run_id),
            status="generating",
            version=replacement.version,
        )

    async def create_draft(
        self,
        job_id: UUID,
        request: ReviewPaperCreateDraftRequest,
        *,
        principal: Principal,
    ) -> ReviewPaperDraftCreatedResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.get(job_id)
        job = record.job
        if job.version != request.expected_version:
            raise TeacherPaperVersionConflictError(job_id)
        if (
            job.practice_paper_id is not None
            or job.paper_blueprint_id is None
            or len(record.slots) != job.slot_count
            or any(
                slot.status != PaperSlotStatus.APPROVED.value
                or slot.current_candidate_id is None
                or slot.requires_revalidation
                for slot in record.slots
            )
        ):
            raise TeacherPaperStateConflictError(job_id)
        result = await PaperPublicationService(self._session).create_draft(
            job.curriculum_version_id,
            paper_blueprint_id=job.paper_blueprint_id,
            title=job.title,
            candidate_ids=tuple(cast(UUID, slot.current_candidate_id) for slot in record.slots),
            idempotency_key=f"teacher-paper-draft-{job.id.hex}",
            principal=principal,
        )
        current = (await self._repository.get(job_id)).job
        await self._repository.cas_job(
            job_id,
            token=None,
            expected_version=current.version,
            values={"practice_paper_id": result.record.draft.paper_id},
        )
        await self._session.commit()
        return ReviewPaperDraftCreatedResponse(
            paper_id=job_id,
            paper_reference=job.paper_reference,
            draft_id=result.record.draft.paper_id,
            draft_version=result.record.draft.version,
            publication_path=(
                f"/api/v1/admin/curricula/{job.curriculum_version_id}/papers/"
                f"{result.record.draft.paper_id}"
            ),
        )

    async def _require_candidate_slot(
        self,
        job_id: UUID,
        question_id: UUID,
        principal: Principal,
    ) -> TeacherPaperSlotModel:
        authorize(principal, Permission.CONTENT_REVIEW)
        slot = await self._repository.find_slot(job_id, question_id)
        if slot.current_candidate_id is None:
            raise TeacherPaperStateConflictError(question_id)
        return slot

    async def _sync_slot(
        self,
        job_id: UUID,
        question_id: UUID,
        status: PaperSlotStatus,
        *,
        requires_revalidation: bool = False,
    ) -> None:
        slot = await self._repository.find_slot(job_id, question_id)
        await self._repository.cas_slot(
            slot,
            {
                "status": status.value,
                "requires_revalidation": requires_revalidation,
                "failure_code": None,
            },
        )
        await self._session.commit()
        job = (await self._repository.get(job_id)).job
        await self._repository.cas_job(
            job_id,
            token=None,
            expected_version=job.version,
            values=await _aggregate_counts(self._repository, job_id),
        )
        await self._session.commit()

    async def _get_question(
        self,
        job_id: UUID,
        question_id: UUID,
        principal: Principal,
    ) -> ReviewQuestionResponse:
        detail = await self.get(job_id, principal=principal)
        question = next(
            (item for item in detail.questions if item.id == question_id),
            None,
        )
        if question is None:
            # The stable question identity changes only on explicit regeneration.
            slot = await self._repository.find_slot(job_id, question_id)
            question = next(
                (item for item in detail.questions if item.id == slot.current_generation_run_id),
                None,
            )
        if question is None:
            raise TeacherPaperQuestionNotFoundError(question_id)
        return question
