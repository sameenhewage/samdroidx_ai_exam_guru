import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.subject_quality import _execute_quality_operation
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.papers.models import QuestionCandidateRevisionModel
from exam_guru_api.subject_quality.domain import (
    EvalCaseState,
    FeedbackAction,
    ReviewReasonCode,
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
    SubjectQualityEvalRunNotFoundError,
    SubjectQualityFeedbackNotFoundError,
    SubjectQualityRepository,
)
from exam_guru_api.subject_quality.schemas import (
    SubjectQualityEvalRunRequest,
    SubjectQualityPromotionRequest,
)
from exam_guru_api.subject_quality.service import (
    SubjectQualityEvalIntegrityError,
    SubjectQualityEvalService,
    SubjectQualityEvalVersionConflictError,
    SubjectQualityFeedbackPersistenceError,
    SubjectQualityFeedbackService,
    SubjectQualityPromotionConflictError,
    SubjectQualitySecondReviewerRequiredError,
    _candidate_for_replay,
    _eval_case_response,
    _eval_run_response,
    _export_case,
    _feedback_response,
    _findings_snapshot,
    _replay_input_snapshot,
    _report_snapshot,
    _validate_idempotency_key,
)
from exam_guru_api.validation.domain import FindingStatus
from exam_guru_api.validation.pipeline import ValidationPipeline
from tests.test_subject_quality_feedback import CURRICULUM_ID, FixedValidator, replay_snapshot

NOW = datetime(2026, 8, 26, tzinfo=UTC)
ACTOR_ID = UUID(int=26_001)
SECOND_ACTOR_ID = UUID(int=26_002)
FEEDBACK_ID = UUID(int=26_003)
CASE_ID = UUID(int=26_004)
RUN_ID = UUID(int=26_005)
SUBJECT_ID = UUID(int=26_006)
MEDIUM_ID = UUID(int=26_007)
UNIT_ID = UUID(int=26_008)
LESSON_ID = UUID(int=26_009)
GENERATION_ID = UUID(int=26_010)
ATTEMPT_ID = UUID(int=26_011)
VALIDATION_ID = UUID(int=26_012)
CANDIDATE_ID = GENERATION_ID


def principal(actor_id: UUID = ACTOR_ID) -> Principal:
    return Principal(actor_id, frozenset({AdminRole.REVIEWER}))


def service_replay_snapshot() -> dict[str, object]:
    snapshot = replay_snapshot()
    snapshot["generation"] = {
        "prompt_id": "fixture-prompt",
        "prompt_version": "fixture-prompt-v1",
        "provider": "fake",
        "provider_version": "provider-v1",
        "model": "fixture",
        "model_version": "model-v1",
        "retrieval_version": "retrieval-v1",
        "generation_schema_version": "question.v1",
    }
    return snapshot


class SessionStub:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: tuple[object, ...]) -> None:
        self.added.extend(values)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def session_stub() -> tuple[SessionStub, AsyncSession]:
    value = SessionStub()
    return value, cast(AsyncSession, value)


def content(stem: str = "What is 2 + 3?") -> dict[str, object]:
    return {
        "question_type": "multiple_choice",
        "stem": stem,
        "options": [
            {"option_id": "A", "text": "4"},
            {"option_id": "B", "text": "5"},
        ],
        "answer": "B",
        "explanation": "Two plus three is five.",
        "marks": 1,
        "marking_guide": [
            json.dumps(
                {"criterion_id": "m1", "description": "Selects 5.", "marks": 1},
                separators=(",", ":"),
                sort_keys=True,
            )
        ],
    }


def feedback_model() -> SubjectQualityFeedbackModel:
    scope = {
        "grade": 7,
        "medium": "en",
        "subject_code": "MATHEMATICS",
        "subject_id": str(SUBJECT_ID),
        "curriculum_version_id": str(CURRICULUM_ID),
        "unit_id": str(UNIT_ID),
        "lesson_id": str(LESSON_ID),
        "lesson_number": 1,
    }
    return SubjectQualityFeedbackModel(
        id=FEEDBACK_ID,
        schema_version="subject-quality-feedback.v1",
        teacher_paper_job_id=UUID(int=26_020),
        teacher_paper_slot_id=UUID(int=26_021),
        slot_version=2,
        curriculum_version_id=CURRICULUM_ID,
        medium_id=MEDIUM_ID,
        subject_id=SUBJECT_ID,
        unit_id=UNIT_ID,
        lesson_id=LESSON_ID,
        grade=7,
        medium_code="en",
        subject_code="MATHEMATICS",
        lesson_number=1,
        candidate_id=CANDIDATE_ID,
        candidate_revision=1,
        candidate_version=4,
        review_event_version=4,
        generation_run_id=GENERATION_ID,
        generation_attempt_id=ATTEMPT_ID,
        validation_run_id=VALIDATION_ID,
        replacement_generation_run_id=None,
        action="approve",
        reason_code="confirmed_quality",
        note="Confirmed.",
        original_content_snapshot=content(),
        current_content_snapshot=content(),
        findings_snapshot={
            "validation_run_id": str(VALIDATION_ID),
            "overall_status": "pass",
            "findings": [],
        },
        scope_snapshot=scope,
        provenance_snapshot=({"source_document_id": str(UUID(int=1))},),
        replay_input_snapshot=service_replay_snapshot(),
        prompt_version="prompt-v1",
        provider="fake",
        provider_version="provider-v1",
        model="fixture",
        model_version="model-v1",
        retrieval_version="retrieval-v1",
        validator_versions=[{"validator_id": "fixed", "validator_version": "1"}],
        original_content_fingerprint="sha256:" + "1" * 64,
        current_content_fingerprint="sha256:" + "2" * 64,
        findings_fingerprint="sha256:" + "3" * 64,
        scope_fingerprint="sha256:" + "4" * 64,
        provenance_fingerprint="sha256:" + "5" * 64,
        feedback_fingerprint="sha256:" + "6" * 64,
        action_fingerprint="sha256:" + "7" * 64,
        idempotency_key_hash=None,
        actor_id=ACTOR_ID,
        created_at=NOW,
    )


def case_model(
    *,
    version: int = 1,
    state: str = "draft",
    promoted_by: UUID = ACTOR_ID,
) -> SubjectQualityEvalCaseVersionModel:
    return SubjectQualityEvalCaseVersionModel(
        eval_case_id=CASE_ID,
        version=version,
        previous_version=None if version == 1 else 1,
        source_feedback_id=FEEDBACK_ID,
        state=state,
        expected_status="pass",
        expected_finding_codes=[],
        defect_category="no_defect",
        replay_input_snapshot=service_replay_snapshot(),
        subject_scope_snapshot={"curriculum_version_id": str(CURRICULUM_ID)},
        case_fingerprint="sha256:" + "8" * 64,
        idempotency_key_hash="sha256:" + "9" * 64,
        promotion_request_fingerprint="sha256:" + "a" * 64,
        promoted_by=promoted_by,
        approved_by=SECOND_ACTOR_ID if version == 2 else None,
        created_at=NOW,
        approved_at=NOW if version == 2 else None,
    )


def result_model(*, outcome: str = "pass") -> SubjectQualityEvalResultModel:
    return SubjectQualityEvalResultModel(
        id=UUID(int=26_030),
        eval_run_id=RUN_ID,
        eval_case_id=CASE_ID,
        eval_case_version=2,
        expected_status="pass",
        expected_finding_codes=[],
        actual_status="pass" if outcome == "pass" else "fail",
        actual_finding_codes=[] if outcome == "pass" else ["subject.math.answer_mismatch"],
        outcome=outcome,
        passed=outcome == "pass",
        pipeline_version="fixed-pipeline.v1",
        pipeline_fingerprint="sha256:" + "b" * 64,
        validator_versions=[{"validator_id": "fixed", "validator_version": "1"}],
        report_fingerprint="sha256:" + "c" * 64,
        result_fingerprint="sha256:" + "d" * 64,
        report_snapshot={},
        created_at=NOW,
    )


def run_model() -> SubjectQualityEvalRunModel:
    return SubjectQualityEvalRunModel(
        id=RUN_ID,
        runner_version="subject-quality-eval-runner.v1",
        pipeline_version="fixed-pipeline.v1",
        pipeline_fingerprint="sha256:" + "b" * 64,
        request_fingerprint="sha256:" + "e" * 64,
        case_count=1,
        passed_count=1,
        regression_count=0,
        unavailable_count=0,
        created_by=ACTOR_ID,
        created_at=NOW,
    )


def pipeline(status: FindingStatus = FindingStatus.PASS) -> ValidationPipeline:
    return ValidationPipeline(
        validators=(
            FixedValidator(
                status,
                "schema.completeness"
                if status is FindingStatus.PASS
                else "subject.math.answer_mismatch",
            ),
        ),
        version="fixed-pipeline.v1",
    )


def test_replay_candidate_helper_covers_short_answers_and_marking_fallbacks() -> None:
    original = replay_snapshot()["candidate"]
    assert isinstance(original, dict)
    short = content("Explain the answer.")
    short["question_type"] = "short_answer"
    short["answer"] = "not-json"
    short["marking_guide"] = [
        json.dumps({"criterion_id": "custom", "description": "Custom.", "marks": 1}),
        "Use the existing second criterion.",
        "Append a bounded criterion.",
    ]
    original_marking = original["marking"]
    assert isinstance(original_marking, dict)
    original_marking["criteria"].append(
        {"criterion_id": "m2", "description": "Second.", "marks": 1}
    )
    replayed = _candidate_for_replay(original, short)
    answer = cast(dict[str, object], replayed["answer"])
    criteria = cast(dict[str, list[dict[str, object]]], replayed["marking"])["criteria"]
    assert answer["accepted_responses"] == ["not-json"]
    assert criteria[0]["criterion_id"] == "custom"
    assert criteria[1]["criterion_id"] == "m2"
    assert criteria[2]["criterion_id"] == "reviewer-3"

    short["answer"] = "7"
    assert cast(dict[str, object], _candidate_for_replay(original, short)["answer"])[
        "accepted_responses"
    ] == [7]
    short["answer"] = '["seven"]'
    assert cast(dict[str, object], _candidate_for_replay(original, short)["answer"])[
        "accepted_responses"
    ] == ["seven"]


def test_feedback_snapshot_helpers_fail_closed_without_validation() -> None:
    source = cast(Any, SimpleNamespace(validation=None))
    with pytest.raises(SubjectQualityFeedbackPersistenceError):
        _findings_snapshot(source)
    with pytest.raises(SubjectQualityFeedbackPersistenceError):
        _replay_input_snapshot(
            source,
            replay_content=content(),
            replay_content_fingerprint="sha256:" + "f" * 64,
        )


class FeedbackRepositoryStub:
    def __init__(self) -> None:
        self.existing: SubjectQualityFeedbackModel | None = None
        self.raise_on_add = False
        self.added: SubjectQualityFeedbackModel | None = None
        self.records: tuple[SubjectQualityFeedbackModel, ...] = ()
        self.promoted: SubjectQualityEvalCaseVersionModel | None = None

    async def feedback_by_action(self, _fingerprint: str) -> SubjectQualityFeedbackModel | None:
        return self.existing

    async def revision(self, _candidate_id: UUID, revision: int) -> object:
        return SimpleNamespace(content=content("Original" if revision == 1 else "Current"))

    async def add_feedback(self, model: SubjectQualityFeedbackModel) -> SubjectQualityFeedbackModel:
        if self.raise_on_add:
            raise IntegrityError("insert", {}, Exception("conflict"))
        self.added = model
        model.created_at = NOW
        return model

    async def list_feedback(
        self, **_kwargs: object
    ) -> tuple[tuple[SubjectQualityFeedbackModel, ...], int]:
        return self.records, len(self.records)

    async def latest_case_for_feedback(
        self, _feedback_id: UUID
    ) -> SubjectQualityEvalCaseVersionModel | None:
        return self.promoted


def action_source(*, candidate: bool = True, validation: bool = True) -> Any:
    snapshot = service_replay_snapshot()
    validation_value = (
        SimpleNamespace(
            id=VALIDATION_ID,
            overall_status="pass",
            pipeline_version="fixed-pipeline.v1",
            pipeline_fingerprint="f" * 64,
            report_fingerprint="e" * 64,
            validator_lineage=[{"validator_id": "fixed", "validator_version": "1"}],
            input_snapshot=snapshot,
        )
        if validation
        else None
    )
    return SimpleNamespace(
        slot=SimpleNamespace(
            id=UUID(int=26_021),
            unit_id=UNIT_ID,
            lesson_id=LESSON_ID,
            lesson_number=1,
        ),
        generation=SimpleNamespace(
            id=GENERATION_ID,
            context_snapshot={"items": [{"provenance": {"source_document_id": "source"}}]},
            prompt_version="prompt-v1",
            provider="fake",
            provider_version="provider-v1",
            model="fixture",
            model_version="model-v1",
            retrieval_version="retrieval-v1",
        ),
        validation=validation_value,
        candidate=(
            SimpleNamespace(
                id=CANDIDATE_ID,
                version=4,
                current_revision=2,
                generation_attempt_id=ATTEMPT_ID,
            )
            if candidate
            else None
        ),
        findings=(
            SimpleNamespace(
                id=UUID(int=26_040),
                ordinal=0,
                validator_id="fixed",
                validator_version="1",
                code="schema.completeness",
                status="pass",
                message="Complete.",
                evidence=[],
            ),
        ),
        unit_title="Numbers",
        lesson_title="Addition",
        taxonomy_title="Arithmetic",
    )


def action_job() -> Any:
    return SimpleNamespace(
        id=UUID(int=26_020),
        curriculum_version_id=CURRICULUM_ID,
        medium_id=MEDIUM_ID,
        subject_id=SUBJECT_ID,
        teacher_intent={"target": {"grade": 7, "medium": "en", "subject": "MATHEMATICS"}},
    )


def test_feedback_service_deduplicates_builds_exact_snapshots_lists_and_maps_integrity() -> None:
    raw_session, typed_session = session_stub()
    service = SubjectQualityFeedbackService(typed_session)
    repository = FeedbackRepositoryStub()
    service._repository = cast(Any, repository)

    with pytest.raises(SubjectQualityFeedbackPersistenceError):
        asyncio.run(
            service.record_action(
                job=action_job(),
                source=action_source(candidate=False),
                slot_version=2,
                action=FeedbackAction.EDIT,
                reason_code=ReviewReasonCode.AMBIGUOUS_WORDING,
                note=None,
                principal=principal(),
            )
        )

    repository.existing = feedback_model()
    deduplicated = asyncio.run(
        service.record_action(
            job=action_job(),
            source=action_source(),
            slot_version=2,
            action=FeedbackAction.EDIT,
            reason_code=ReviewReasonCode.AMBIGUOUS_WORDING,
            note=None,
            principal=principal(),
        )
    )
    assert deduplicated is repository.existing

    repository.existing = None
    created = asyncio.run(
        service.record_action(
            job=action_job(),
            source=action_source(),
            slot_version=3,
            action=FeedbackAction.REGENERATE,
            reason_code=ReviewReasonCode.ANSWER_INCORRECT,
            note="Recalculate.",
            principal=principal(),
            replacement_generation_run_id=UUID(int=26_050),
            idempotency_key="regenerate-key",
        )
    )
    assert created is repository.added
    assert repository.added is not None
    assert created.review_event_version is None
    assert created.idempotency_key_hash is not None
    assert created.original_content_snapshot["stem"] == "Original"
    assert created.current_content_snapshot["stem"] == "Current"
    assert created.replay_input_snapshot["schema_version"] == "subject-quality-eval-input.v1"

    repository.records = (created,)
    repository.promoted = case_model()
    listed = asyncio.run(
        service.list(
            principal=principal(),
            candidate_id=CANDIDATE_ID,
            curriculum_version_id=CURRICULUM_ID,
            limit=10,
            offset=0,
        )
    )
    assert listed.total == 1
    assert listed.items[0].promoted_eval_case_id == CASE_ID
    assert _feedback_response(created, promoted_eval_case_id=None).promoted_eval_case_id is None

    repository.raise_on_add = True
    with pytest.raises(SubjectQualityFeedbackPersistenceError):
        asyncio.run(
            service.record_action(
                job=action_job(),
                source=action_source(),
                slot_version=4,
                action=FeedbackAction.REJECT,
                reason_code=ReviewReasonCode.OTHER_QUALITY_ISSUE,
                note=None,
                principal=principal(),
                replacement_generation_run_id=UUID(int=26_051),
            )
        )
    assert raw_session.commits == 0


def test_edit_feedback_replays_the_judged_pre_edit_revision() -> None:
    _raw_session, typed_session = session_stub()
    service = SubjectQualityFeedbackService(typed_session)
    repository = FeedbackRepositoryStub()
    service._repository = cast(Any, repository)

    created = asyncio.run(
        service.record_action(
            job=action_job(),
            source=action_source(),
            slot_version=2,
            action=FeedbackAction.EDIT,
            reason_code=ReviewReasonCode.AMBIGUOUS_WORDING,
            note="The generated wording had two readings.",
            principal=principal(),
        )
    )

    assert created.original_content_snapshot["stem"] == "Original"
    assert created.current_content_snapshot["stem"] == "Current"
    replayed = cast(dict[str, object], created.replay_input_snapshot["candidate"])
    assert replayed["stem"] == "Original"

    invalid_source = action_source()
    invalid_source.candidate.current_revision = 1
    with pytest.raises(SubjectQualityFeedbackPersistenceError, match="prior revision"):
        asyncio.run(
            service.record_action(
                job=action_job(),
                source=invalid_source,
                slot_version=2,
                action=FeedbackAction.EDIT,
                reason_code=ReviewReasonCode.AMBIGUOUS_WORDING,
                note=None,
                principal=principal(),
            )
        )


class EvalRepositoryStub:
    def __init__(self) -> None:
        self.feedback_record = feedback_model()
        self.inserted: SubjectQualityEvalCaseVersionModel | str | None = "dynamic"
        self.latest_feedback_case: SubjectQualityEvalCaseVersionModel | None = case_model()
        self.latest = case_model()
        self.raise_case_integrity = False
        self.list_records: tuple[SubjectQualityEvalCaseVersionModel, ...] = (self.latest,)
        self.total = 1
        self.approved: tuple[SubjectQualityEvalCaseVersionModel, ...] = (
            case_model(version=2, state="approved"),
        )
        self.run_records: list[StoredEvalRun | None] = [None]
        self.raise_run_integrity = False
        self.stored_run = StoredEvalRun(run_model(), (result_model(),))
        self.added_runs: list[
            tuple[SubjectQualityEvalRunModel, tuple[SubjectQualityEvalResultModel, ...]]
        ] = []

    async def feedback(self, _feedback_id: UUID) -> SubjectQualityFeedbackModel:
        return self.feedback_record

    async def insert_draft_case(self, values: dict[str, object]) -> Any:
        if self.inserted == "dynamic":
            return SubjectQualityEvalCaseVersionModel(**values)
        return self.inserted

    async def latest_case_for_feedback(self, _feedback_id: UUID) -> Any:
        return self.latest_feedback_case

    async def latest_case(self, _eval_case_id: UUID, *, for_update: bool = False) -> Any:
        assert for_update
        return self.latest

    async def add_case_version(self, model: SubjectQualityEvalCaseVersionModel) -> Any:
        if self.raise_case_integrity:
            raise IntegrityError("insert", {}, Exception("race"))
        return model

    async def list_cases(self, **_kwargs: object) -> Any:
        return self.list_records, self.total

    async def approved_cases(self, _case_ids: tuple[UUID, ...]) -> Any:
        return self.approved

    async def run_by_request(self, _fingerprint: str) -> StoredEvalRun | None:
        return self.run_records.pop(0) if self.run_records else None

    async def add_run(
        self,
        run: SubjectQualityEvalRunModel,
        results: tuple[SubjectQualityEvalResultModel, ...],
    ) -> StoredEvalRun:
        if self.raise_run_integrity:
            raise IntegrityError("insert", {}, Exception("race"))
        run.created_at = NOW
        for result in results:
            result.created_at = NOW
        self.added_runs.append((run, results))
        return StoredEvalRun(run, results)

    async def run(self, _run_id: UUID) -> StoredEvalRun:
        return self.stored_run


def eval_service() -> tuple[SessionStub, SubjectQualityEvalService, EvalRepositoryStub]:
    raw_session, typed_session = session_stub()
    service = SubjectQualityEvalService(typed_session, pipeline())
    repository = EvalRepositoryStub()
    service._repository = cast(Any, repository)
    return raw_session, service, repository


def promotion_request() -> SubjectQualityPromotionRequest:
    return SubjectQualityPromotionRequest(
        expected_status="pass",
        expected_finding_codes=(),
        defect_category="no_defect",
    )


def test_eval_promotion_validation_deduplication_and_conflict_paths() -> None:
    for invalid in ("", " padded", "two words", "x" * 129, "bad\nkey"):
        with pytest.raises(SubjectQualityPromotionConflictError):
            _validate_idempotency_key(invalid)

    raw_session, service, repository = eval_service()
    created = asyncio.run(
        service.promote(
            FEEDBACK_ID,
            promotion_request(),
            idempotency_key="promote-key",
            principal=principal(),
        )
    )
    assert created.state is EvalCaseState.DRAFT
    assert created.deduplicated is False
    assert raw_session.commits == 1

    repository.inserted = None
    repository.latest_feedback_case = None
    with pytest.raises(SubjectQualityPromotionConflictError):
        asyncio.run(
            service.promote(
                FEEDBACK_ID,
                promotion_request(),
                idempotency_key="promote-key",
                principal=principal(),
            )
        )

    repository.latest_feedback_case = case_model()
    repository.latest_feedback_case.case_fingerprint = "sha256:" + "0" * 64
    with pytest.raises(SubjectQualityPromotionConflictError):
        asyncio.run(
            service.promote(
                FEEDBACK_ID,
                promotion_request(),
                idempotency_key="promote-key",
                principal=principal(),
            )
        )


def test_eval_approval_listing_export_and_response_branches() -> None:
    raw_session, service, repository = eval_service()
    repository.latest.version = 2
    with pytest.raises(SubjectQualityEvalVersionConflictError):
        asyncio.run(
            service.approve(
                CASE_ID,
                expected_version=1,
                principal=principal(SECOND_ACTOR_ID),
            )
        )

    repository.latest = case_model()
    with pytest.raises(SubjectQualitySecondReviewerRequiredError):
        asyncio.run(service.approve(CASE_ID, expected_version=1, principal=principal()))

    repository.raise_case_integrity = True
    with pytest.raises(SubjectQualityEvalVersionConflictError):
        asyncio.run(
            service.approve(
                CASE_ID,
                expected_version=1,
                principal=principal(SECOND_ACTOR_ID),
            )
        )
    assert raw_session.rollbacks == 1

    repository.raise_case_integrity = False
    approved = asyncio.run(
        service.approve(CASE_ID, expected_version=1, principal=principal(SECOND_ACTOR_ID))
    )
    assert approved.version == 2
    assert approved.can_approve is False

    repository.list_records = (case_model(),)
    listed = asyncio.run(
        service.list_cases(
            principal=principal(SECOND_ACTOR_ID),
            state=None,
            limit=10,
            offset=0,
        )
    )
    assert listed.items[0].can_approve is True
    listed_state = asyncio.run(
        service.list_cases(
            principal=principal(),
            state=EvalCaseState.DRAFT,
            limit=10,
            offset=0,
        )
    )
    assert listed_state.total == 1

    repository.list_records = (case_model(version=2, state="approved"),)
    repository.total = 2
    exported = asyncio.run(service.export(principal=principal(), limit=1, offset=0))
    assert exported.next_offset == 1
    assert "note" not in exported.model_dump_json()
    repository.total = 1
    assert asyncio.run(service.export(principal=principal(), limit=1, offset=0)).next_offset is None

    draft = case_model()
    assert _eval_case_response(draft, principal=principal(SECOND_ACTOR_ID)).can_approve is True
    exported_case = _export_case(case_model(version=2, state="approved"))
    assert exported_case.generation_versions["prompt_version"] == "fixture-prompt-v1"


def test_eval_run_deduplication_integrity_winner_and_get_paths() -> None:
    raw_session, service, repository = eval_service()
    existing = StoredEvalRun(run_model(), (result_model(),))
    created = asyncio.run(
        service.run(SubjectQualityEvalRunRequest(case_ids=(CASE_ID,)), principal=principal())
    )
    assert created.deduplicated is False
    assert raw_session.commits == 1

    repository.run_records = [existing]
    deduplicated = asyncio.run(
        service.run(SubjectQualityEvalRunRequest(case_ids=(CASE_ID,)), principal=principal())
    )
    assert deduplicated.deduplicated is True

    repository.run_records = [None, existing]
    repository.raise_run_integrity = True
    winner = asyncio.run(
        service.run(SubjectQualityEvalRunRequest(case_ids=(CASE_ID,)), principal=principal())
    )
    assert winner.deduplicated is True
    assert raw_session.rollbacks == 1

    repository.run_records = [None, None]
    with pytest.raises(SubjectQualityEvalIntegrityError):
        asyncio.run(
            service.run(SubjectQualityEvalRunRequest(case_ids=(CASE_ID,)), principal=principal())
        )

    fetched = asyncio.run(service.get_run(RUN_ID, principal=principal()))
    assert fetched.run_id == RUN_ID
    response = _eval_run_response(existing)
    assert response.results[0].fingerprint.endswith("d" * 64)


def test_eval_result_fingerprint_is_scoped_to_its_run_request() -> None:
    _raw_session, service, repository = eval_service()
    first = case_model(version=2, state="approved")
    second = case_model(version=2, state="approved")
    second.eval_case_id = UUID(int=26_099)
    second.source_feedback_id = UUID(int=26_098)
    second.case_fingerprint = "sha256:" + "c" * 64

    repository.approved = (first,)
    asyncio.run(
        service.run(
            SubjectQualityEvalRunRequest(case_ids=(first.eval_case_id,)),
            principal=principal(),
        )
    )
    first_fingerprint = repository.added_runs[-1][1][0].result_fingerprint

    repository.run_records = [None]
    repository.approved = (first, second)
    asyncio.run(
        service.run(
            SubjectQualityEvalRunRequest(case_ids=(first.eval_case_id, second.eval_case_id)),
            principal=principal(),
        )
    )
    overlapping_result = next(
        result
        for result in repository.added_runs[-1][1]
        if result.eval_case_id == first.eval_case_id
    )

    assert overlapping_result.result_fingerprint != first_fingerprint


def test_report_snapshot_is_stable_and_contains_bounded_evidence() -> None:
    validation_pipeline = pipeline(FindingStatus.FAIL)
    validation_input = replay_snapshot()
    from exam_guru_api.subject_quality.domain import validation_input_from_eval_snapshot

    report = validation_pipeline.validate(
        validation_input_from_eval_snapshot(
            validation_input,
            expected_curriculum_version_id=CURRICULUM_ID,
        )
    )
    snapshot = _report_snapshot(report)
    assert snapshot["overall_status"] == "fail"
    findings = cast(list[dict[str, object]], snapshot["findings"])
    evidence = cast(list[dict[str, str]], findings[0]["evidence"])
    assert evidence[0]["location"] == "$.candidate"


class CountResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class RepositorySessionStub(SessionStub):
    def __init__(self) -> None:
        super().__init__()
        self.get_values: list[object | None] = []
        self.scalar_values: list[object | None] = []
        self.scalars_values: list[tuple[object, ...]] = []
        self.count_values: list[int] = []

    async def get(self, _model: object, _identity: object) -> object | None:
        return self.get_values.pop(0)

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.pop(0)

    async def scalars(self, _statement: object) -> tuple[object, ...]:
        return self.scalars_values.pop(0)

    async def execute(self, _statement: object) -> CountResult:
        return CountResult(self.count_values.pop(0))


def repository_stub() -> tuple[RepositorySessionStub, SubjectQualityRepository]:
    raw = RepositorySessionStub()
    return raw, SubjectQualityRepository(cast(AsyncSession, raw))


def test_repository_feedback_revision_and_case_paths() -> None:
    raw, repository = repository_stub()
    revision = cast(QuestionCandidateRevisionModel, SimpleNamespace(content=content()))
    raw.get_values = [revision, None]
    assert asyncio.run(repository.revision(CANDIDATE_ID, 1)) is revision
    with pytest.raises(SubjectQualityFeedbackNotFoundError):
        asyncio.run(repository.revision(CANDIDATE_ID, 2))

    feedback = feedback_model()
    raw.scalar_values = [feedback]
    assert asyncio.run(repository.feedback_by_action("sha256:" + "0" * 64)) is feedback
    assert asyncio.run(repository.add_feedback(feedback)) is feedback
    raw.get_values = [feedback, None]
    assert asyncio.run(repository.feedback(FEEDBACK_ID)) is feedback
    with pytest.raises(SubjectQualityFeedbackNotFoundError):
        asyncio.run(repository.feedback(UUID(int=999)))

    raw.scalars_values = [(feedback,), ()]
    raw.scalar_values = [1, 0]
    records, total = asyncio.run(
        repository.list_feedback(
            candidate_id=CANDIDATE_ID,
            curriculum_version_id=CURRICULUM_ID,
            limit=10,
            offset=0,
        )
    )
    assert records == (feedback,)
    assert total == 1
    assert asyncio.run(
        repository.list_feedback(
            candidate_id=None,
            curriculum_version_id=None,
            limit=10,
            offset=0,
        )
    ) == ((), 0)

    draft = case_model()
    raw.scalar_values = [draft, draft]
    assert asyncio.run(repository.latest_case_for_feedback(FEEDBACK_ID)) is draft
    assert asyncio.run(repository.insert_draft_case({"eval_case_id": CASE_ID})) is draft

    raw.scalar_values = [draft, draft, None]
    assert asyncio.run(repository.latest_case(CASE_ID)) is draft
    assert asyncio.run(repository.latest_case(CASE_ID, for_update=True)) is draft
    with pytest.raises(SubjectQualityEvalCaseNotFoundError):
        asyncio.run(repository.latest_case(UUID(int=999)))
    assert asyncio.run(repository.add_case_version(draft)) is draft
    assert len(raw.added) == 2


def test_repository_case_listing_approved_and_run_paths() -> None:
    raw, repository = repository_stub()
    approved = case_model(version=2, state="approved")
    raw.scalars_values = [(approved,), (approved,), (approved,), ()]
    raw.count_values = [1, 1]
    assert asyncio.run(repository.list_cases(state=None, limit=10, offset=0)) == ((approved,), 1)
    assert asyncio.run(repository.list_cases(state="approved", limit=10, offset=0)) == (
        (approved,),
        1,
    )
    assert asyncio.run(repository.approved_cases((CASE_ID,))) == (approved,)
    with pytest.raises(SubjectQualityEvalCaseNotFoundError):
        asyncio.run(repository.approved_cases((CASE_ID,)))

    run = run_model()
    result = result_model()
    raw.scalar_values = [None, run]
    raw.scalars_values = [(result,), (result,), (result,)]
    assert asyncio.run(repository.run_by_request("sha256:" + "0" * 64)) is None
    stored = asyncio.run(repository.run_by_request("sha256:" + "1" * 64))
    assert stored == StoredEvalRun(run, (result,))
    assert asyncio.run(repository.add_run(run, (result,))) == StoredEvalRun(run, (result,))
    assert asyncio.run(repository.results(RUN_ID)) == (result,)
    raw.get_values = [run, None]
    assert asyncio.run(repository.run(RUN_ID)) == StoredEvalRun(run, (result,))
    with pytest.raises(SubjectQualityEvalRunNotFoundError):
        asyncio.run(repository.run(UUID(int=999)))


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (SubjectQualityFeedbackNotFoundError(), 404, "subject_quality_feedback_not_found"),
        (SubjectQualityEvalCaseNotFoundError(), 404, "subject_quality_eval_case_not_found"),
        (SubjectQualityEvalRunNotFoundError(), 404, "subject_quality_eval_run_not_found"),
        (
            SubjectQualitySecondReviewerRequiredError(),
            409,
            "eval_case_second_reviewer_required",
        ),
        (SubjectQualityEvalVersionConflictError(), 409, "subject_quality_eval_version_conflict"),
        (SubjectQualityPromotionConflictError(), 409, "subject_quality_promotion_conflict"),
        (SubjectQualityFeedbackPersistenceError(), 409, "subject_quality_persistence_conflict"),
        (SubjectQualityEvalIntegrityError(), 409, "subject_quality_persistence_conflict"),
        (
            IntegrityError("insert", {}, Exception("constraint")),
            409,
            "subject_quality_persistence_conflict",
        ),
    ],
)
def test_quality_route_error_mapping_is_stable(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    raw_session, typed_session = session_stub()

    async def operation() -> object:
        raise error

    with pytest.raises(HTTPException) as captured:
        asyncio.run(_execute_quality_operation(typed_session, operation))
    assert captured.value.status_code == status_code
    assert cast(object, captured.value.detail) == {"code": code}
    if status_code == 409:
        assert raw_session.rollbacks == 1


def test_quality_route_executor_returns_success_without_rollback() -> None:
    raw_session, typed_session = session_stub()

    async def operation() -> str:
        return "ok"

    assert asyncio.run(_execute_quality_operation(typed_session, operation)) == "ok"
    assert raw_session.rollbacks == 0
