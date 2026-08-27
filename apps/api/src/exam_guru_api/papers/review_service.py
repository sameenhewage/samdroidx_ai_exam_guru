from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.papers.adapters import (
    GenerationValidationAdapterError,
    adapt_generation_validation,
)
from exam_guru_api.papers.domain import (
    CandidateInvariantError,
    CandidateState,
    ConcurrentVersionError,
    InvalidCandidateTransitionError,
    QuestionContent,
    ReviewAction,
    ValidationNotPassedError,
)
from exam_guru_api.papers.repository import (
    CandidatePersistenceIntegrityError,
    ReviewCandidateSummary,
    SqlAlchemyReviewCandidateRepository,
    StoredQuestionCandidate,
)
from exam_guru_api.papers.service import (
    ApproveCandidateCommand,
    EditCandidateCommand,
    PaperWorkflowService,
    RejectCandidateCommand,
    StartCandidateReviewCommand,
)
from exam_guru_api.validation.service import (
    ValidationGenerationIntegrityError,
    ValidationGenerationNotSucceededError,
    ValidationReportIntegrityError,
    reconstruct_generation_result,
    reconstruct_validation_report,
)


class ReviewValidationNotPassedError(RuntimeError):
    pass


class ReviewUpstreamIntegrityError(RuntimeError):
    pass


class ReviewCandidateIdempotencyConflictError(RuntimeError):
    pass


class ReviewCandidateVersionConflictError(RuntimeError):
    pass


class ReviewCandidateStateConflictError(RuntimeError):
    pass


class ReviewCandidateRevalidationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewCandidateCreationResult:
    record: StoredQuestionCandidate
    deduplicated: bool


class ReviewCandidateService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SqlAlchemyReviewCandidateRepository(session)
        self._workflow = PaperWorkflowService()

    async def create(
        self,
        curriculum_version_id: UUID,
        *,
        validation_run_id: UUID,
        principal: Principal,
    ) -> ReviewCandidateCreationResult:
        authorize(principal, Permission.CONTENT_REVIEW)
        existing = await self._repository.find_by_validation(
            curriculum_version_id,
            validation_run_id,
        )
        if existing is not None:
            return ReviewCandidateCreationResult(existing, deduplicated=True)

        source = await self._repository.get_creation_source(
            curriculum_version_id,
            validation_run_id,
        )
        if source.validation.overall_status == "fail":
            raise ReviewValidationNotPassedError(validation_run_id)
        try:
            generation_result = reconstruct_generation_result(source.generation)
            reconstructed = reconstruct_validation_report(source.validation, source.findings)
            if reconstructed.report.blocked:
                raise ReviewValidationNotPassedError(validation_run_id)
            candidate = adapt_generation_validation(
                generation_result,
                reconstructed.report,
                validation_run_id=source.validation.id,
                finding_ids=reconstructed.finding_ids,
            )
        except ReviewValidationNotPassedError:
            raise
        except ValidationNotPassedError as error:
            raise ReviewValidationNotPassedError(validation_run_id) from error
        except (
            CandidateInvariantError,
            GenerationValidationAdapterError,
            ValidationGenerationIntegrityError,
            ValidationGenerationNotSucceededError,
            ValidationReportIntegrityError,
        ) as error:
            raise ReviewUpstreamIntegrityError(validation_run_id) from error

        try:
            created = await self._repository.insert_initial(
                curriculum_version_id=curriculum_version_id,
                paper_blueprint_id=source.generation.run.paper_blueprint_id,
                candidate=candidate,
                actor_id=principal.subject_id,
            )
            if created:
                self._add_audit(
                    actor_id=principal.subject_id,
                    action="question_candidate.created",
                    candidate_id=candidate.candidate_id,
                    payload={
                        "curriculum_version_id": str(curriculum_version_id),
                        "generation_run_id": str(candidate.lineage.generation_id),
                        "generation_attempt_id": str(candidate.lineage.generation_attempt_id),
                        "validation_run_id": str(validation_run_id),
                        "paper_blueprint_id": str(source.generation.run.paper_blueprint_id),
                        "blueprint_slot_id": candidate.lineage.blueprint_slot_id,
                        "state": candidate.state.value,
                        "version": candidate.version,
                        "current_revision": 1,
                        "validated_revision": 1,
                    },
                )
                await self._session.commit()
                record = await self._repository.get(
                    curriculum_version_id,
                    candidate.candidate_id,
                )
                return ReviewCandidateCreationResult(record, deduplicated=False)

            winner = await self._repository.find_by_validation(
                curriculum_version_id,
                validation_run_id,
            )
            if winner is None or winner.candidate.id != candidate.candidate_id:
                raise ReviewCandidateIdempotencyConflictError(validation_run_id)
            await self._session.commit()
            return ReviewCandidateCreationResult(winner, deduplicated=True)
        except Exception:
            await self._session.rollback()
            raise

    async def get(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
        *,
        principal: Principal,
    ) -> StoredQuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        try:
            return await self._repository.get(curriculum_version_id, candidate_id)
        except CandidatePersistenceIntegrityError as error:
            raise ReviewUpstreamIntegrityError(candidate_id) from error

    async def list(
        self,
        curriculum_version_id: UUID,
        *,
        principal: Principal,
        state: CandidateState | None,
        paper_blueprint_id: UUID | None,
        blueprint_slot_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[ReviewCandidateSummary, ...]:
        authorize(principal, Permission.CONTENT_REVIEW)
        try:
            return await self._repository.list(
                curriculum_version_id,
                state=state,
                paper_blueprint_id=paper_blueprint_id,
                blueprint_slot_id=blueprint_slot_id,
                limit=limit,
                offset=offset,
            )
        except CandidatePersistenceIntegrityError as error:
            raise ReviewUpstreamIntegrityError(curriculum_version_id) from error

    async def start_review(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
        *,
        expected_version: int,
        principal: Principal,
        commit: bool = True,
    ) -> StoredQuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.get(curriculum_version_id, candidate_id)
        try:
            transitioned = self._workflow.start_review(
                principal,
                StartCandidateReviewCommand(
                    candidate=record.domain,
                    expected_version=expected_version,
                ),
            )
        except ConcurrentVersionError as error:
            raise ReviewCandidateVersionConflictError(candidate_id) from error
        except InvalidCandidateTransitionError as error:
            raise ReviewCandidateStateConflictError(candidate_id) from error
        return await self._persist_transition(
            curriculum_version_id=curriculum_version_id,
            record=record,
            transitioned=transitioned,
            expected_version=expected_version,
            actor_id=principal.subject_id,
            action=ReviewAction.STARTED,
            reason=None,
            commit=commit,
        )

    async def edit(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
        *,
        content: QuestionContent,
        reason: str,
        expected_version: int,
        principal: Principal,
        commit: bool = True,
    ) -> StoredQuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.get(curriculum_version_id, candidate_id)
        try:
            transitioned = self._workflow.edit(
                principal,
                EditCandidateCommand(
                    candidate=record.domain,
                    content=content,
                    reason=reason,
                    expected_version=expected_version,
                ),
            )
        except ConcurrentVersionError as error:
            raise ReviewCandidateVersionConflictError(candidate_id) from error
        except InvalidCandidateTransitionError as error:
            raise ReviewCandidateStateConflictError(candidate_id) from error
        return await self._persist_transition(
            curriculum_version_id=curriculum_version_id,
            record=record,
            transitioned=transitioned,
            expected_version=expected_version,
            actor_id=principal.subject_id,
            action=ReviewAction.EDITED,
            reason=reason,
            commit=commit,
        )

    async def approve(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
        *,
        expected_version: int,
        note: str | None,
        principal: Principal,
        commit: bool = True,
    ) -> StoredQuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.get(curriculum_version_id, candidate_id)
        try:
            transitioned = self._workflow.approve(
                principal,
                ApproveCandidateCommand(
                    candidate=record.domain,
                    expected_version=expected_version,
                    note=note,
                ),
            )
        except ConcurrentVersionError as error:
            raise ReviewCandidateVersionConflictError(candidate_id) from error
        except InvalidCandidateTransitionError as error:
            raise ReviewCandidateStateConflictError(candidate_id) from error
        except ValidationNotPassedError as error:
            raise ReviewCandidateRevalidationRequiredError(candidate_id) from error
        return await self._persist_transition(
            curriculum_version_id=curriculum_version_id,
            record=record,
            transitioned=transitioned,
            expected_version=expected_version,
            actor_id=principal.subject_id,
            action=ReviewAction.APPROVED,
            reason=note,
            commit=commit,
        )

    async def reject(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
        *,
        expected_version: int,
        reason: str,
        principal: Principal,
        commit: bool = True,
    ) -> StoredQuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.get(curriculum_version_id, candidate_id)
        try:
            transitioned = self._workflow.reject(
                principal,
                RejectCandidateCommand(
                    candidate=record.domain,
                    reason=reason,
                    expected_version=expected_version,
                ),
            )
        except ConcurrentVersionError as error:
            raise ReviewCandidateVersionConflictError(candidate_id) from error
        except InvalidCandidateTransitionError as error:
            raise ReviewCandidateStateConflictError(candidate_id) from error
        return await self._persist_transition(
            curriculum_version_id=curriculum_version_id,
            record=record,
            transitioned=transitioned,
            expected_version=expected_version,
            actor_id=principal.subject_id,
            action=ReviewAction.REJECTED,
            reason=reason,
            commit=commit,
        )

    async def _persist_transition(
        self,
        *,
        curriculum_version_id: UUID,
        record: StoredQuestionCandidate,
        transitioned: object,
        expected_version: int,
        actor_id: UUID,
        action: ReviewAction,
        reason: str | None,
        commit: bool = True,
    ) -> StoredQuestionCandidate:
        from exam_guru_api.papers.domain import QuestionCandidate

        if not isinstance(transitioned, QuestionCandidate):
            raise TypeError("transitioned must be QuestionCandidate")
        candidate_id = record.domain.candidate_id
        try:
            updated = await self._repository.cas_update(
                curriculum_version_id=curriculum_version_id,
                candidate_id=candidate_id,
                expected_version=expected_version,
                expected_state=record.domain.state,
                state=transitioned.state,
                version=transitioned.version,
                current_revision=transitioned.revisions[-1].revision,
            )
            if not updated:
                raise ReviewCandidateVersionConflictError(candidate_id)
            if action is ReviewAction.EDITED:
                revision = transitioned.revisions[-1]
                self._repository.add_revision(
                    candidate_id=transitioned.candidate_id,
                    revision=revision.revision,
                    candidate_version=transitioned.version,
                    content=revision.content,
                    reviewer_id=actor_id,
                    reason=cast_reason(reason),
                )
                await self._session.flush()
            self._repository.add_event(
                candidate_id=transitioned.candidate_id,
                candidate_version=transitioned.version,
                action=action,
                reviewer_id=actor_id,
                revision=transitioned.revisions[-1].revision,
                reason=reason,
            )
            self._add_audit(
                actor_id=actor_id,
                action={
                    ReviewAction.STARTED: "question_candidate.review_started",
                    ReviewAction.EDITED: "question_candidate.edited",
                    ReviewAction.APPROVED: "question_candidate.approved",
                    ReviewAction.REJECTED: "question_candidate.rejected",
                }[action],
                candidate_id=transitioned.candidate_id,
                payload={
                    "curriculum_version_id": str(curriculum_version_id),
                    "from_state": record.domain.state.value,
                    "to_state": transitioned.state.value,
                    "from_version": record.domain.version,
                    "version": transitioned.version,
                    "current_revision": transitioned.revisions[-1].revision,
                    "validated_revision": 1,
                    "reason_recorded": reason is not None,
                },
            )
            if commit:
                await self._session.commit()
            else:
                await self._session.flush()
            return await self._repository.get(
                curriculum_version_id,
                transitioned.candidate_id,
            )
        except Exception:
            await self._session.rollback()
            raise

    def _add_audit(
        self,
        *,
        actor_id: UUID,
        action: str,
        candidate_id: UUID,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="question_candidate",
                resource_id=candidate_id,
                payload=payload,
            )
        )


def cast_reason(reason: str | None) -> str:
    if reason is None:
        raise CandidateInvariantError("edit reason is required")
    return reason
