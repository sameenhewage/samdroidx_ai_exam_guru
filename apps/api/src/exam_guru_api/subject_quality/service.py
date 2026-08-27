from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.subject_quality.domain import (
    EVAL_EXPORT_SCHEMA_VERSION,
    EVAL_INPUT_SCHEMA_VERSION,
    EVAL_RUNNER_VERSION,
    FEEDBACK_SCHEMA_VERSION,
    DefectCategory,
    EvalCaseState,
    EvalComparisonOutcome,
    FeedbackAction,
    ReviewReasonCode,
    canonical_fingerprint,
    evaluate_snapshot,
)
from exam_guru_api.subject_quality.models import (
    SubjectQualityEvalCaseVersionModel,
    SubjectQualityEvalResultModel,
    SubjectQualityEvalRunModel,
    SubjectQualityFeedbackModel,
)
from exam_guru_api.subject_quality.repository import (
    StoredEvalRun,
    SubjectQualityEvalCaseNotFoundError,
    SubjectQualityRepository,
)
from exam_guru_api.subject_quality.schemas import (
    SubjectQualityEvalCaseListResponse,
    SubjectQualityEvalCaseResponse,
    SubjectQualityEvalExportCaseResponse,
    SubjectQualityEvalExportExpectedResponse,
    SubjectQualityEvalExportResponse,
    SubjectQualityEvalResultResponse,
    SubjectQualityEvalRunRequest,
    SubjectQualityEvalRunResponse,
    SubjectQualityFeedbackListResponse,
    SubjectQualityFeedbackResponse,
    SubjectQualityFingerprintResponse,
    SubjectQualityLineageResponse,
    SubjectQualityPromotionRequest,
    SubjectQualityScopeResponse,
)
from exam_guru_api.teacher_papers.models import TeacherPaperJobModel
from exam_guru_api.teacher_papers.repository import ReviewSlotSource
from exam_guru_api.validation.domain import FindingStatus, ValidationReport
from exam_guru_api.validation.pipeline import ValidationPipeline

_FEEDBACK_NAMESPACE = UUID("b473977c-1506-58a3-a77f-3bb40eb191f8")
_EVAL_NAMESPACE = UUID("ff5517a5-9ebd-58ef-81eb-ce7f55890250")


class SubjectQualityFeedbackPersistenceError(RuntimeError):
    pass


class SubjectQualityPromotionConflictError(RuntimeError):
    pass


class SubjectQualityEvalVersionConflictError(RuntimeError):
    pass


class SubjectQualitySecondReviewerRequiredError(RuntimeError):
    pass


class SubjectQualityEvalIntegrityError(RuntimeError):
    pass


def _validate_idempotency_key(value: str) -> None:
    if (
        not value
        or value != value.strip()
        or len(value) > 128
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise SubjectQualityPromotionConflictError("invalid idempotency key")


def _candidate_for_replay(
    original_candidate: dict[str, object], current_content: dict[str, object]
) -> dict[str, object]:
    candidate = deepcopy(original_candidate)
    candidate["question_type"] = current_content["question_type"]
    candidate["stem"] = current_content["stem"]
    candidate["options"] = deepcopy(current_content["options"])
    answer = cast(dict[str, object], candidate["answer"])
    if current_content["question_type"] == "multiple_choice":
        answer["correct_option_id"] = current_content["answer"]
        answer["accepted_responses"] = []
    else:
        answer["correct_option_id"] = None
        try:
            accepted = json.loads(cast(str, current_content["answer"]))
        except (TypeError, json.JSONDecodeError):
            accepted = [current_content["answer"]]
        answer["accepted_responses"] = accepted if isinstance(accepted, list) else [accepted]
    answer["explanation"] = current_content["explanation"]

    marking = cast(dict[str, object], candidate["marking"])
    original_criteria = cast(list[dict[str, object]], marking.get("criteria", []))
    criteria: list[dict[str, object]] = []
    for index, item in enumerate(cast(list[str], current_content["marking_guide"])):
        try:
            decoded = json.loads(item)
        except json.JSONDecodeError:
            decoded = None
        if (
            isinstance(decoded, dict)
            and {
                "criterion_id",
                "description",
                "marks",
            }
            <= decoded.keys()
        ):
            criterion = cast(dict[str, object], decoded)
        elif index < len(original_criteria):
            criterion = {**original_criteria[index], "description": item}
        else:
            criterion = {
                "criterion_id": f"reviewer-{index + 1}",
                "description": item,
                "marks": 1,
            }
        criteria.append(criterion)
    marking["total_marks"] = current_content["marks"]
    marking["criteria"] = criteria
    return candidate


def _findings_snapshot(source: ReviewSlotSource) -> dict[str, object]:
    validation = source.validation
    if validation is None:
        raise SubjectQualityFeedbackPersistenceError("feedback requires persisted validation")
    return {
        "validation_run_id": str(validation.id),
        "overall_status": validation.overall_status,
        "pipeline_version": validation.pipeline_version,
        "pipeline_fingerprint": f"sha256:{validation.pipeline_fingerprint}",
        "report_fingerprint": f"sha256:{validation.report_fingerprint}",
        "findings": [
            {
                "id": str(finding.id),
                "ordinal": finding.ordinal,
                "validator_id": finding.validator_id,
                "validator_version": finding.validator_version,
                "code": finding.code,
                "status": finding.status,
                "message": finding.message,
                "evidence": deepcopy(finding.evidence),
            }
            for finding in source.findings
        ],
    }


def _provenance_snapshot(source: ReviewSlotSource) -> list[dict[str, object]]:
    items = cast(list[dict[str, object]], source.generation.context_snapshot["items"])
    return [deepcopy(cast(dict[str, object], item["provenance"])) for item in items]


def _scope_snapshot(job: TeacherPaperJobModel, source: ReviewSlotSource) -> dict[str, object]:
    target = cast(dict[str, object], job.teacher_intent["target"])
    return {
        "grade": target["grade"],
        "medium": target["medium"],
        "medium_id": str(job.medium_id),
        "subject_code": target["subject"],
        "subject_id": str(job.subject_id),
        "curriculum_version_id": str(job.curriculum_version_id),
        "unit_id": str(source.slot.unit_id),
        "unit_title": source.unit_title,
        "lesson_id": str(source.slot.lesson_id),
        "lesson_number": source.slot.lesson_number,
        "lesson_title": source.lesson_title,
        "taxonomy_title": source.taxonomy_title,
    }


def _replay_input_snapshot(
    source: ReviewSlotSource,
    *,
    replay_content: dict[str, object],
    replay_content_fingerprint: str,
) -> dict[str, object]:
    validation = source.validation
    if validation is None:
        raise SubjectQualityFeedbackPersistenceError("feedback requires validation input")
    stored = validation.input_snapshot
    original_candidate = deepcopy(cast(dict[str, object], stored["candidate"]))
    return {
        "schema_version": EVAL_INPUT_SCHEMA_VERSION,
        "candidate_id": replay_content_fingerprint,
        "candidate": _candidate_for_replay(original_candidate, replay_content),
        "blueprint": deepcopy(cast(dict[str, object], stored["blueprint"])),
        "subject_scope": deepcopy(cast(dict[str, object], stored["subject_scope"])),
        "generated_scope": deepcopy(cast(dict[str, object], stored["generated_scope"])),
        "context_scope_bindings": deepcopy(
            cast(list[dict[str, object]], stored["context_scope_bindings"])
        ),
        "grounding_sources": deepcopy(cast(list[dict[str, object]], stored["grounding_sources"])),
        "duplicate_references": deepcopy(
            cast(list[dict[str, object]], stored["duplicate_references"])
        ),
        "generation": deepcopy(cast(dict[str, object], stored["generation"])),
    }


def _feedback_response(
    model: SubjectQualityFeedbackModel,
    *,
    promoted_eval_case_id: UUID | None,
) -> SubjectQualityFeedbackResponse:
    return SubjectQualityFeedbackResponse(
        id=model.id,
        schema_version=model.schema_version,
        action=FeedbackAction(model.action),
        reason_code=model.reason_code,
        note=model.note,
        actor_id=model.actor_id,
        created_at=model.created_at,
        original_content=model.original_content_snapshot,
        current_content=model.current_content_snapshot,
        findings_at_action=model.findings_snapshot,
        scope=SubjectQualityScopeResponse(
            grade=model.grade,
            medium=model.medium_code,
            subject_code=model.subject_code,
            curriculum_version_id=model.curriculum_version_id,
            unit_id=model.unit_id,
            lesson_id=model.lesson_id,
            lesson_number=model.lesson_number,
        ),
        lineage=SubjectQualityLineageResponse(
            candidate_id=model.candidate_id,
            candidate_revision=model.candidate_revision,
            candidate_version=model.candidate_version,
            generation_run_id=model.generation_run_id,
            generation_attempt_id=model.generation_attempt_id,
            validation_run_id=model.validation_run_id,
            replacement_generation_run_id=model.replacement_generation_run_id,
            prompt_version=model.prompt_version,
            provider=model.provider,
            provider_version=model.provider_version,
            model=model.model,
            model_version=model.model_version,
            retrieval_version=model.retrieval_version,
            validator_versions=tuple(model.validator_versions),
            provenance=tuple(model.provenance_snapshot),
        ),
        fingerprints=SubjectQualityFingerprintResponse(
            original_content=model.original_content_fingerprint,
            current_content=model.current_content_fingerprint,
            findings=model.findings_fingerprint,
            scope=model.scope_fingerprint,
            provenance=model.provenance_fingerprint,
            feedback=model.feedback_fingerprint,
        ),
        promoted_eval_case_id=promoted_eval_case_id,
    )


class SubjectQualityFeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SubjectQualityRepository(session)

    async def record_action(
        self,
        *,
        job: TeacherPaperJobModel,
        source: ReviewSlotSource,
        slot_version: int,
        action: FeedbackAction,
        reason_code: ReviewReasonCode,
        note: str | None,
        principal: Principal,
        replacement_generation_run_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> SubjectQualityFeedbackModel:
        authorize(principal, Permission.CONTENT_REVIEW)
        if source.candidate is None or source.validation is None:
            raise SubjectQualityFeedbackPersistenceError("feedback requires a review candidate")
        candidate = source.candidate
        action_identity = {
            "candidate_id": str(candidate.id),
            "candidate_version": candidate.version,
            "action": action.value,
            "replacement_generation_run_id": (
                str(replacement_generation_run_id)
                if replacement_generation_run_id is not None
                else None
            ),
        }
        action_fingerprint = canonical_fingerprint(action_identity)
        existing = await self._repository.feedback_by_action(action_fingerprint)
        if existing is not None:
            return existing

        original_revision = await self._repository.revision(candidate.id, 1)
        current_revision = await self._repository.revision(candidate.id, candidate.current_revision)
        original_content = deepcopy(original_revision.content)
        current_content = deepcopy(current_revision.content)
        replay_content = current_content
        if action is FeedbackAction.EDIT:
            if candidate.current_revision <= 1:
                raise SubjectQualityFeedbackPersistenceError(
                    "edit feedback requires a prior revision"
                )
            prior_revision = await self._repository.revision(
                candidate.id, candidate.current_revision - 1
            )
            replay_content = deepcopy(prior_revision.content)
        findings = _findings_snapshot(source)
        scope = _scope_snapshot(job, source)
        provenance = _provenance_snapshot(source)
        original_fingerprint = canonical_fingerprint(original_content)
        current_fingerprint = canonical_fingerprint(current_content)
        replay_input = _replay_input_snapshot(
            source,
            replay_content=replay_content,
            replay_content_fingerprint=canonical_fingerprint(replay_content),
        )
        idempotency_hash = (
            None
            if idempotency_key is None
            else canonical_fingerprint(
                {"actor_id": str(principal.subject_id), "idempotency_key": idempotency_key}
            )
        )
        fingerprints = {
            "original_content": original_fingerprint,
            "current_content": current_fingerprint,
            "findings": canonical_fingerprint(findings),
            "scope": canonical_fingerprint(scope),
            "provenance": canonical_fingerprint(provenance),
        }
        feedback_material = {
            "schema_version": FEEDBACK_SCHEMA_VERSION,
            "action_identity": action_identity,
            "reason_code": reason_code.value,
            "note": note,
            "actor_id": str(principal.subject_id),
            "fingerprints": fingerprints,
            "versions": {
                "prompt": source.generation.prompt_version,
                "model": source.generation.model_version,
                "retrieval": source.generation.retrieval_version,
                "validators": source.validation.validator_lineage,
            },
        }
        feedback_fingerprint = canonical_fingerprint(feedback_material)
        model = SubjectQualityFeedbackModel(
            id=uuid5(_FEEDBACK_NAMESPACE, action_fingerprint),
            schema_version=FEEDBACK_SCHEMA_VERSION,
            teacher_paper_job_id=job.id,
            teacher_paper_slot_id=source.slot.id,
            slot_version=slot_version,
            curriculum_version_id=job.curriculum_version_id,
            medium_id=job.medium_id,
            subject_id=job.subject_id,
            unit_id=source.slot.unit_id,
            lesson_id=source.slot.lesson_id,
            grade=cast(int, cast(dict[str, object], job.teacher_intent["target"])["grade"]),
            medium_code=cast(str, cast(dict[str, object], job.teacher_intent["target"])["medium"]),
            subject_code=cast(
                str, cast(dict[str, object], job.teacher_intent["target"])["subject"]
            ),
            lesson_number=source.slot.lesson_number,
            candidate_id=candidate.id,
            candidate_revision=candidate.current_revision,
            candidate_version=candidate.version,
            review_event_version=(
                None if action is FeedbackAction.REGENERATE else candidate.version
            ),
            generation_run_id=source.generation.id,
            generation_attempt_id=candidate.generation_attempt_id,
            validation_run_id=source.validation.id,
            replacement_generation_run_id=replacement_generation_run_id,
            action=action.value,
            reason_code=reason_code.value,
            note=note,
            original_content_snapshot=original_content,
            current_content_snapshot=current_content,
            findings_snapshot=findings,
            scope_snapshot=scope,
            provenance_snapshot=provenance,
            replay_input_snapshot=replay_input,
            prompt_version=source.generation.prompt_version,
            provider=source.generation.provider,
            provider_version=source.generation.provider_version,
            model=source.generation.model,
            model_version=source.generation.model_version,
            retrieval_version=source.generation.retrieval_version,
            validator_versions=deepcopy(source.validation.validator_lineage),
            original_content_fingerprint=original_fingerprint,
            current_content_fingerprint=current_fingerprint,
            findings_fingerprint=fingerprints["findings"],
            scope_fingerprint=fingerprints["scope"],
            provenance_fingerprint=fingerprints["provenance"],
            feedback_fingerprint=feedback_fingerprint,
            action_fingerprint=action_fingerprint,
            idempotency_key_hash=idempotency_hash,
            actor_id=principal.subject_id,
        )
        try:
            return await self._repository.add_feedback(model)
        except IntegrityError as error:
            raise SubjectQualityFeedbackPersistenceError(model.id) from error

    async def list(
        self,
        *,
        principal: Principal,
        candidate_id: UUID | None,
        curriculum_version_id: UUID | None,
        limit: int,
        offset: int,
    ) -> SubjectQualityFeedbackListResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        records, total = await self._repository.list_feedback(
            candidate_id=candidate_id,
            curriculum_version_id=curriculum_version_id,
            limit=limit,
            offset=offset,
        )
        responses: list[SubjectQualityFeedbackResponse] = []
        for record in records:
            eval_case = await self._repository.latest_case_for_feedback(record.id)
            responses.append(
                _feedback_response(
                    record,
                    promoted_eval_case_id=(None if eval_case is None else eval_case.eval_case_id),
                )
            )
        return SubjectQualityFeedbackListResponse(
            items=tuple(responses), total=total, limit=limit, offset=offset
        )


class SubjectQualityEvalService:
    def __init__(self, session: AsyncSession, pipeline: ValidationPipeline) -> None:
        self._session = session
        self._pipeline = pipeline
        self._repository = SubjectQualityRepository(session)

    async def promote(
        self,
        feedback_id: UUID,
        request: SubjectQualityPromotionRequest,
        *,
        idempotency_key: str,
        principal: Principal,
    ) -> SubjectQualityEvalCaseResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        _validate_idempotency_key(idempotency_key)
        feedback = await self._repository.feedback(feedback_id)
        idempotency_hash = canonical_fingerprint(
            {"actor_id": str(principal.subject_id), "idempotency_key": idempotency_key}
        )
        request_payload = {
            "feedback_id": str(feedback.id),
            "expected_status": request.expected_status.value,
            "expected_finding_codes": list(request.expected_finding_codes),
            "defect_category": request.defect_category.value,
        }
        request_fingerprint = canonical_fingerprint(request_payload)
        case_fingerprint = canonical_fingerprint(
            {
                "source_feedback_fingerprint": feedback.feedback_fingerprint,
                "request": request_payload,
                "replay_input": feedback.replay_input_snapshot,
            }
        )
        eval_case_id = uuid5(_EVAL_NAMESPACE, f"feedback:{feedback.id}")
        now = datetime.now(UTC)
        created = await self._repository.insert_draft_case(
            {
                "eval_case_id": eval_case_id,
                "version": 1,
                "previous_version": None,
                "source_feedback_id": feedback.id,
                "state": EvalCaseState.DRAFT.value,
                "expected_status": request.expected_status.value,
                "expected_finding_codes": list(request.expected_finding_codes),
                "defect_category": request.defect_category.value,
                "replay_input_snapshot": deepcopy(feedback.replay_input_snapshot),
                "subject_scope_snapshot": deepcopy(feedback.scope_snapshot),
                "case_fingerprint": case_fingerprint,
                "idempotency_key_hash": idempotency_hash,
                "promotion_request_fingerprint": request_fingerprint,
                "promoted_by": principal.subject_id,
                "approved_by": None,
                "created_at": now,
                "approved_at": None,
            }
        )
        record = created
        deduplicated = created is None
        if record is None:
            record = await self._repository.latest_case_for_feedback(feedback.id)
        if record is None or any(
            (
                record.eval_case_id != eval_case_id,
                record.expected_status != request.expected_status.value,
                tuple(record.expected_finding_codes) != request.expected_finding_codes,
                record.defect_category != request.defect_category.value,
                record.idempotency_key_hash != idempotency_hash,
                record.promotion_request_fingerprint != request_fingerprint,
                record.case_fingerprint != case_fingerprint,
            )
        ):
            raise SubjectQualityPromotionConflictError(feedback.id)
        await self._session.commit()
        return _eval_case_response(record, principal=principal, deduplicated=deduplicated)

    async def approve(
        self,
        eval_case_id: UUID,
        *,
        expected_version: int,
        principal: Principal,
    ) -> SubjectQualityEvalCaseResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        record = await self._repository.latest_case(eval_case_id, for_update=True)
        if record.version != expected_version or record.state != EvalCaseState.DRAFT.value:
            raise SubjectQualityEvalVersionConflictError(eval_case_id)
        if record.promoted_by == principal.subject_id:
            raise SubjectQualitySecondReviewerRequiredError(eval_case_id)
        approved = SubjectQualityEvalCaseVersionModel(
            eval_case_id=record.eval_case_id,
            version=2,
            previous_version=1,
            source_feedback_id=record.source_feedback_id,
            state=EvalCaseState.APPROVED.value,
            expected_status=record.expected_status,
            expected_finding_codes=deepcopy(record.expected_finding_codes),
            defect_category=record.defect_category,
            replay_input_snapshot=deepcopy(record.replay_input_snapshot),
            subject_scope_snapshot=deepcopy(record.subject_scope_snapshot),
            case_fingerprint=record.case_fingerprint,
            idempotency_key_hash=record.idempotency_key_hash,
            promotion_request_fingerprint=record.promotion_request_fingerprint,
            promoted_by=record.promoted_by,
            approved_by=principal.subject_id,
            created_at=record.created_at,
            approved_at=datetime.now(UTC),
        )
        try:
            await self._repository.add_case_version(approved)
            await self._session.commit()
        except IntegrityError as error:
            await self._session.rollback()
            raise SubjectQualityEvalVersionConflictError(eval_case_id) from error
        return _eval_case_response(approved, principal=principal)

    async def list_cases(
        self,
        *,
        principal: Principal,
        state: EvalCaseState | None,
        limit: int,
        offset: int,
    ) -> SubjectQualityEvalCaseListResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        records, total = await self._repository.list_cases(
            state=None if state is None else state.value,
            limit=limit,
            offset=offset,
        )
        return SubjectQualityEvalCaseListResponse(
            items=tuple(_eval_case_response(record, principal=principal) for record in records),
            total=total,
            limit=limit,
            offset=offset,
        )

    async def export(
        self,
        *,
        principal: Principal,
        limit: int,
        offset: int,
    ) -> SubjectQualityEvalExportResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        records, total = await self._repository.list_cases(
            state=EvalCaseState.APPROVED.value,
            limit=limit,
            offset=offset,
        )
        cases = tuple(_export_case(record) for record in records)
        next_offset = offset + len(cases) if offset + len(cases) < total else None
        return SubjectQualityEvalExportResponse(
            schema_version=EVAL_EXPORT_SCHEMA_VERSION,
            runner_version=EVAL_RUNNER_VERSION,
            cases=cases,
            limit=limit,
            offset=offset,
            next_offset=next_offset,
        )

    async def run(
        self,
        request: SubjectQualityEvalRunRequest,
        *,
        principal: Principal,
    ) -> SubjectQualityEvalRunResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        cases = await self._repository.approved_cases(request.case_ids)
        request_fingerprint = canonical_fingerprint(
            {
                "runner_version": EVAL_RUNNER_VERSION,
                "pipeline_version": self._pipeline.version,
                "pipeline_fingerprint": self._pipeline.pipeline_fingerprint,
                "cases": [
                    {
                        "eval_case_id": str(record.eval_case_id),
                        "version": record.version,
                        "case_fingerprint": record.case_fingerprint,
                    }
                    for record in cases
                ],
            }
        )
        existing = await self._repository.run_by_request(request_fingerprint)
        if existing is not None:
            return _eval_run_response(existing, deduplicated=True)

        evaluations = tuple(
            (
                record,
                evaluate_snapshot(
                    snapshot=record.replay_input_snapshot,
                    expected_curriculum_version_id=UUID(
                        cast(str, record.subject_scope_snapshot["curriculum_version_id"])
                    ),
                    expected_status=FindingStatus(record.expected_status),
                    expected_finding_codes=tuple(record.expected_finding_codes),
                    pipeline=self._pipeline,
                ),
            )
            for record in cases
        )
        pass_count = sum(
            result.comparison.outcome is EvalComparisonOutcome.PASS for _, result in evaluations
        )
        regression_count = sum(
            result.comparison.outcome is EvalComparisonOutcome.REGRESSION
            for _, result in evaluations
        )
        unavailable_count = sum(
            result.comparison.outcome is EvalComparisonOutcome.UNAVAILABLE
            for _, result in evaluations
        )
        run_id = uuid5(_EVAL_NAMESPACE, f"run:{request_fingerprint}")
        run = SubjectQualityEvalRunModel(
            id=run_id,
            runner_version=EVAL_RUNNER_VERSION,
            pipeline_version=self._pipeline.version,
            pipeline_fingerprint=f"sha256:{self._pipeline.pipeline_fingerprint}",
            request_fingerprint=request_fingerprint,
            case_count=len(cases),
            passed_count=pass_count,
            regression_count=regression_count,
            unavailable_count=unavailable_count,
            created_by=principal.subject_id,
        )
        results = tuple(
            SubjectQualityEvalResultModel(
                id=uuid5(_EVAL_NAMESPACE, f"result:{run_id}:{record.eval_case_id}"),
                eval_run_id=run_id,
                eval_case_id=record.eval_case_id,
                eval_case_version=record.version,
                expected_status=result.comparison.expected_status.value,
                expected_finding_codes=list(result.comparison.expected_finding_codes),
                actual_status=result.comparison.actual_status.value,
                actual_finding_codes=list(result.comparison.actual_finding_codes),
                outcome=result.comparison.outcome.value,
                passed=result.comparison.passed,
                pipeline_version=result.pipeline_version,
                pipeline_fingerprint=f"sha256:{result.pipeline_fingerprint}",
                validator_versions=list(result.validator_versions),
                report_fingerprint=f"sha256:{result.report.report_fingerprint}",
                result_fingerprint=canonical_fingerprint(
                    {
                        "eval_run_request_fingerprint": request_fingerprint,
                        "eval_case_id": str(record.eval_case_id),
                        "eval_case_version": record.version,
                        "case_fingerprint": record.case_fingerprint,
                        "comparison_fingerprint": result.comparison.fingerprint,
                    }
                ),
                report_snapshot=_report_snapshot(result.report),
            )
            for record, result in evaluations
        )
        try:
            stored = await self._repository.add_run(run, results)
            await self._session.commit()
        except IntegrityError as error:
            await self._session.rollback()
            winner = await self._repository.run_by_request(request_fingerprint)
            if winner is None:
                raise SubjectQualityEvalIntegrityError(run_id) from error
            return _eval_run_response(winner, deduplicated=True)
        return _eval_run_response(stored)

    async def get_run(self, run_id: UUID, *, principal: Principal) -> SubjectQualityEvalRunResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        return _eval_run_response(await self._repository.run(run_id))


def _eval_case_response(
    model: SubjectQualityEvalCaseVersionModel,
    *,
    principal: Principal,
    deduplicated: bool = False,
) -> SubjectQualityEvalCaseResponse:
    return SubjectQualityEvalCaseResponse(
        eval_case_id=model.eval_case_id,
        version=model.version,
        state=EvalCaseState(model.state),
        source_feedback_id=model.source_feedback_id,
        expected_status=FindingStatus(model.expected_status),
        expected_finding_codes=tuple(model.expected_finding_codes),
        defect_category=DefectCategory(model.defect_category),
        case_fingerprint=model.case_fingerprint,
        promoted_by=model.promoted_by,
        approved_by=model.approved_by,
        created_at=model.created_at,
        approved_at=model.approved_at,
        can_approve=(
            model.state == EvalCaseState.DRAFT.value and model.promoted_by != principal.subject_id
        ),
        deduplicated=deduplicated,
    )


def _export_case(model: SubjectQualityEvalCaseVersionModel) -> SubjectQualityEvalExportCaseResponse:
    snapshot = model.replay_input_snapshot
    generation = cast(dict[str, object], snapshot.get("generation", {}))
    allowed_generation_fields = (
        "prompt_id",
        "prompt_version",
        "provider",
        "provider_version",
        "model",
        "model_version",
        "retrieval_version",
        "generation_schema_version",
    )
    return SubjectQualityEvalExportCaseResponse(
        eval_case_id=model.eval_case_id,
        version=model.version,
        source_feedback_id=model.source_feedback_id,
        case_fingerprint=model.case_fingerprint,
        subject_scope=deepcopy(cast(dict[str, object], snapshot["subject_scope"])),
        candidate=deepcopy(cast(dict[str, object], snapshot["candidate"])),
        blueprint=deepcopy(cast(dict[str, object], snapshot["blueprint"])),
        grounding_sources=tuple(
            {
                key: deepcopy(value)
                for key, value in source.items()
                if key
                in {
                    "context_id",
                    "text",
                    "source_document_id",
                    "source_version",
                    "page_number",
                    "chunk_id",
                    "trust",
                }
            }
            for source in cast(list[dict[str, object]], snapshot["grounding_sources"])
        ),
        duplicate_references=tuple(
            {
                key: deepcopy(value)
                for key, value in reference.items()
                if key in {"question_id", "text", "content_sha256", "provenance"}
            }
            for reference in cast(list[dict[str, object]], snapshot["duplicate_references"])
        ),
        generation_versions={
            field_name: deepcopy(generation[field_name])
            for field_name in allowed_generation_fields
            if field_name in generation
        },
        expected=SubjectQualityEvalExportExpectedResponse(
            status=FindingStatus(model.expected_status),
            finding_codes=tuple(model.expected_finding_codes),
            defect_category=DefectCategory(model.defect_category),
        ),
    )


def _report_snapshot(report: ValidationReport) -> dict[str, object]:
    return {
        "pipeline_version": report.pipeline_version,
        "report_schema_version": report.report_schema_version,
        "overall_status": report.overall_status.value,
        "report_fingerprint": f"sha256:{report.report_fingerprint}",
        "findings": [
            {
                "validator_id": finding.validator_id,
                "validator_version": finding.validator_version,
                "code": finding.code,
                "status": finding.status.value,
                "message": finding.message,
                "evidence": [
                    {
                        "location": evidence.location,
                        "expected": evidence.expected,
                        "observed": evidence.observed,
                    }
                    for evidence in finding.evidence
                ],
            }
            for finding in report.findings
        ],
    }


def _eval_result_response(model: SubjectQualityEvalResultModel) -> SubjectQualityEvalResultResponse:
    return SubjectQualityEvalResultResponse(
        id=model.id,
        eval_case_id=model.eval_case_id,
        eval_case_version=model.eval_case_version,
        expected_status=FindingStatus(model.expected_status),
        expected_finding_codes=tuple(model.expected_finding_codes),
        actual_status=FindingStatus(model.actual_status),
        actual_finding_codes=tuple(model.actual_finding_codes),
        outcome=EvalComparisonOutcome(model.outcome),
        passed=model.passed,
        pipeline_version=model.pipeline_version,
        pipeline_fingerprint=model.pipeline_fingerprint,
        validator_versions=tuple(model.validator_versions),
        report_fingerprint=model.report_fingerprint,
        fingerprint=model.result_fingerprint,
    )


def _eval_run_response(
    stored: StoredEvalRun, *, deduplicated: bool = False
) -> SubjectQualityEvalRunResponse:
    run = stored.run
    return SubjectQualityEvalRunResponse(
        run_id=run.id,
        runner_version=EVAL_RUNNER_VERSION,
        pipeline_version=run.pipeline_version,
        pipeline_fingerprint=run.pipeline_fingerprint,
        request_fingerprint=run.request_fingerprint,
        created_by=run.created_by,
        created_at=run.created_at,
        results=tuple(_eval_result_response(result) for result in stored.results),
        passed_count=run.passed_count,
        regression_count=run.regression_count,
        unavailable_count=run.unavailable_count,
        deduplicated=deduplicated,
    )


__all__ = [
    "SubjectQualityEvalCaseNotFoundError",
    "SubjectQualityEvalIntegrityError",
    "SubjectQualityEvalService",
    "SubjectQualityEvalVersionConflictError",
    "SubjectQualityFeedbackPersistenceError",
    "SubjectQualityFeedbackService",
    "SubjectQualityPromotionConflictError",
    "SubjectQualitySecondReviewerRequiredError",
]
