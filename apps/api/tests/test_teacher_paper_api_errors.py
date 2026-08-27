import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.api.routes.review_papers as review_routes
import exam_guru_api.api.routes.teacher_papers as generation_routes
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.generation.jobs import GenerationDispatcher
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    GenerationRuntimeUnavailableError,
)
from exam_guru_api.papers.publication_service import (
    PaperCandidateSelectionError,
    PaperIdempotencyConflictError,
    PaperIntegrityError,
)
from exam_guru_api.papers.repository import ReviewCandidateNotFoundError
from exam_guru_api.papers.schemas import ReviewCandidateStartRequest
from exam_guru_api.subject_quality.service import SubjectQualityFeedbackPersistenceError
from exam_guru_api.teacher_papers.domain import PaperScopeError
from exam_guru_api.teacher_papers.jobs import PaperGenerationDispatcher
from exam_guru_api.teacher_papers.repository import (
    TeacherPaperJobNotFoundError,
    TeacherPaperPersistenceConflictError,
    TeacherPaperQuestionNotFoundError,
)
from exam_guru_api.teacher_papers.schemas import (
    ReviewPaperCreateDraftRequest,
    ReviewQuestionApproveRequest,
    ReviewQuestionEditRequest,
    ReviewQuestionRegenerateRequest,
    ReviewQuestionRejectRequest,
    TeacherPaperAdvanceRequest,
    TeacherPaperJobCreateRequest,
    TeacherPaperRetryRequest,
)
from exam_guru_api.teacher_papers.service import (
    TeacherPaperContextUnavailableError,
    TeacherPaperCostLimitError,
    TeacherPaperCurriculumAmbiguousError,
    TeacherPaperCurriculumNotFoundError,
    TeacherPaperIdempotencyConflictError,
    TeacherPaperQueueUnavailableError,
    TeacherPaperRetryLimitError,
    TeacherPaperRevalidationRequiredError,
    TeacherPaperStateConflictError,
    TeacherPaperVersionConflictError,
)

JOB_ID = UUID(int=25_950_001)
QUESTION_ID = UUID(int=25_950_002)
PRINCIPAL = Principal(UUID(int=25_950_003), frozenset({AdminRole.ADMIN}))
RouteExecutor = Callable[
    [AsyncSession, Callable[[], Awaitable[object]]],
    Awaitable[object],
]


class FakeSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def session(value: FakeSession) -> AsyncSession:
    return cast(AsyncSession, value)


async def assert_http_error(
    operation: RouteExecutor,
    error: Exception,
    *,
    status_code: int,
    code: str,
    fake_session: FakeSession,
) -> None:
    async def fail() -> None:
        raise error

    with pytest.raises(HTTPException) as captured:
        await operation(session(fake_session), fail)
    assert captured.value.status_code == status_code
    assert cast(dict[str, object], captured.value.detail)["code"] == code


def test_generation_route_error_mapping_is_stable_and_bounded() -> None:
    async def exercise() -> None:
        fake_session = FakeSession()

        async def success() -> str:
            return "ok"

        assert await generation_routes._execute(session(fake_session), success) == "ok"
        cases = (
            (
                IntegrityError("statement", {}, RuntimeError("integrity")),
                409,
                "paper_generation_persistence_conflict",
            ),
            (
                TeacherPaperCurriculumNotFoundError(),
                404,
                "paper_generation_curriculum_not_found",
            ),
            (TeacherPaperJobNotFoundError(), 404, "paper_generation_job_not_found"),
            (
                TeacherPaperCurriculumAmbiguousError(),
                409,
                "paper_generation_curriculum_ambiguous",
            ),
            (
                TeacherPaperIdempotencyConflictError(),
                409,
                "paper_generation_idempotency_conflict",
            ),
            (
                TeacherPaperVersionConflictError(),
                409,
                "paper_generation_version_conflict",
            ),
            (
                TeacherPaperStateConflictError(),
                409,
                "paper_generation_state_conflict",
            ),
            (
                TeacherPaperRetryLimitError(),
                409,
                "paper_generation_retry_limit_exceeded",
            ),
            (
                TeacherPaperCostLimitError(),
                409,
                "paper_generation_cost_limit_exceeded",
            ),
            (
                PaperScopeError("paper_generation_lesson_unmapped", lesson_number=2),
                422,
                "paper_generation_lesson_unmapped",
            ),
            (
                PaperScopeError("paper_generation_scope_invalid"),
                422,
                "paper_generation_scope_invalid",
            ),
            (
                TeacherPaperContextUnavailableError(),
                422,
                "paper_generation_context_unavailable",
            ),
            (
                GenerationRuntimeUnavailableError(),
                503,
                "paper_generation_runtime_unavailable",
            ),
            (
                TeacherPaperQueueUnavailableError(),
                503,
                "paper_generation_queue_unavailable",
            ),
            (
                TeacherPaperPersistenceConflictError(),
                409,
                "paper_generation_lineage_conflict",
            ),
        )
        for error, expected_status, expected_code in cases:
            await assert_http_error(
                generation_routes._execute,
                error,
                status_code=expected_status,
                code=expected_code,
                fake_session=fake_session,
            )
        assert fake_session.rollbacks == 1

    asyncio.run(exercise())


def test_review_route_error_mapping_is_stable() -> None:
    async def exercise() -> None:
        fake_session = FakeSession()

        async def success() -> str:
            return "ok"

        assert await review_routes._execute_review(session(fake_session), success) == "ok"
        cases = (
            (
                IntegrityError("statement", {}, RuntimeError("integrity")),
                409,
                "review_paper_persistence_conflict",
            ),
            (
                SubjectQualityFeedbackPersistenceError(),
                409,
                "quality_feedback_persistence_conflict",
            ),
            (TeacherPaperJobNotFoundError(), 404, "review_paper_not_found"),
            (TeacherPaperQuestionNotFoundError(), 404, "review_question_not_found"),
            (ReviewCandidateNotFoundError(), 404, "review_question_not_found"),
            (
                TeacherPaperVersionConflictError(),
                409,
                "review_question_version_conflict",
            ),
            (
                TeacherPaperRevalidationRequiredError(),
                409,
                "review_question_revalidation_required",
            ),
            (TeacherPaperStateConflictError(), 409, "review_paper_state_conflict"),
            (
                TeacherPaperRetryLimitError(),
                409,
                "review_question_regeneration_limit_exceeded",
            ),
            (
                TeacherPaperCostLimitError(),
                409,
                "review_question_cost_limit_exceeded",
            ),
            (
                TeacherPaperQueueUnavailableError(),
                503,
                "review_question_queue_unavailable",
            ),
            (
                TeacherPaperPersistenceConflictError(),
                409,
                "review_paper_lineage_conflict",
            ),
            (PaperCandidateSelectionError(), 409, "review_paper_lineage_conflict"),
            (PaperIdempotencyConflictError(), 409, "review_paper_lineage_conflict"),
            (PaperIntegrityError(), 409, "review_paper_lineage_conflict"),
        )
        for error, expected_status, expected_code in cases:
            await assert_http_error(
                review_routes._execute_review,
                error,
                status_code=expected_status,
                code=expected_code,
                fake_session=fake_session,
            )
        assert fake_session.rollbacks == 2

    asyncio.run(exercise())


class FakeJobService:
    def __init__(self, *_args: object) -> None:
        self.record = object()

    async def create(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(record=self.record, deduplicated=True)

    async def get(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def advance(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def retry(self, *_args: object, **_kwargs: object) -> object:
        return self.record


def job_request() -> TeacherPaperJobCreateRequest:
    return TeacherPaperJobCreateRequest.model_validate(
        {
            "target": {"grade": 7, "medium": "en", "subject": "MATHEMATICS"},
            "scope": {"kind": "full_subject"},
            "settings": {
                "question_count": 1,
                "duration_minutes": 45,
                "difficulty": "balanced",
            },
        }
    )


def test_generation_job_handlers_delegate_and_return_typed_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        sentinel = cast(object, SimpleNamespace())
        fake_service = FakeJobService()
        monkeypatch.setattr(generation_routes, "TeacherPaperJobService", lambda *_: fake_service)
        monkeypatch.setattr(generation_routes, "TeacherPaperRepository", lambda _: object())

        async def response(*args: object, **kwargs: object) -> object:
            del args, kwargs
            return sentinel

        monkeypatch.setattr(generation_routes, "teacher_paper_job_response", response)
        fake_session = session(FakeSession())
        dispatcher = cast(PaperGenerationDispatcher, object())
        generation_dispatcher = cast(GenerationDispatcher, object())
        runtime = cast(GenerationRuntimeRegistry, object())
        assert (
            await generation_routes.create_teacher_paper_job(
                job_request(),
                "key",
                PRINCIPAL,
                fake_session,
                dispatcher,
                runtime,
            )
            is sentinel
        )
        assert (
            await generation_routes.get_teacher_paper_job(
                JOB_ID,
                PRINCIPAL,
                fake_session,
                dispatcher,
                runtime,
            )
            is sentinel
        )
        assert (
            await generation_routes.advance_teacher_paper_job(
                JOB_ID,
                TeacherPaperAdvanceRequest(expected_version=1),
                PRINCIPAL,
                fake_session,
                dispatcher,
                runtime,
            )
            is sentinel
        )
        assert (
            await generation_routes.retry_teacher_paper_job(
                JOB_ID,
                TeacherPaperRetryRequest(expected_version=2),
                "retry-key",
                PRINCIPAL,
                fake_session,
                dispatcher,
                generation_dispatcher,
                runtime,
            )
            is sentinel
        )

    asyncio.run(exercise())


class FakeReviewService:
    def __init__(self, *_args: object) -> None:
        self.sentinel: Any = object()

    async def list(self, **_kwargs: object) -> object:
        return self.sentinel

    async def get(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel

    async def edit(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel

    async def start(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel

    async def approve(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel

    async def reject(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel

    async def regenerate(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel

    async def create_draft(self, *_args: object, **_kwargs: object) -> object:
        return self.sentinel


def test_review_handlers_delegate_every_expected_version_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        service = FakeReviewService()
        monkeypatch.setattr(review_routes, "_service", lambda *_: service)
        fake_session = session(FakeSession())
        paper_dispatcher = cast(PaperGenerationDispatcher, object())
        generation_dispatcher = cast(GenerationDispatcher, object())
        runtime = cast(GenerationRuntimeRegistry, object())
        edit = ReviewQuestionEditRequest.model_validate(
            {
                "content": {
                    "question_type": "multiple_choice",
                    "stem": "Which answer?",
                    "options": [
                        {"option_id": "A", "text": "First"},
                        {"option_id": "B", "text": "Second"},
                    ],
                    "answer": "B",
                    "explanation": "B is supported.",
                    "marks": 1,
                    "marking_guide": ["Selects B."],
                },
                "reason_code": "ambiguous_wording",
                "note": "Clarify.",
                "expected_version": 3,
            }
        )
        common = (PRINCIPAL, fake_session, paper_dispatcher, generation_dispatcher, runtime)

        def is_sentinel(value: object) -> bool:
            return value is service.sentinel

        assert is_sentinel(
            await review_routes.list_teacher_review_papers(*common, limit=10, offset=0)
        )
        assert is_sentinel(await review_routes.get_teacher_review_paper(JOB_ID, *common))
        assert is_sentinel(
            await review_routes.edit_teacher_review_question(JOB_ID, QUESTION_ID, edit, *common)
        )
        start_request = ReviewCandidateStartRequest(expected_version=2)
        assert is_sentinel(
            await review_routes.start_teacher_review_question(
                JOB_ID, QUESTION_ID, start_request, *common
            )
        )
        approve_request = ReviewQuestionApproveRequest(expected_version=3, note=None)
        assert is_sentinel(
            await review_routes.approve_teacher_review_question(
                JOB_ID, QUESTION_ID, approve_request, *common
            )
        )
        reject_request = ReviewQuestionRejectRequest(
            expected_version=3,
            reason_code="other_quality_issue",
            note="Reject.",
        )
        assert is_sentinel(
            await review_routes.reject_teacher_review_question(
                JOB_ID, QUESTION_ID, reject_request, *common
            )
        )
        regenerate = ReviewQuestionRegenerateRequest(
            expected_version=4,
            reason_code="answer_incorrect",
            note="Replace.",
        )
        assert is_sentinel(
            await review_routes.regenerate_teacher_review_question(
                JOB_ID,
                QUESTION_ID,
                regenerate,
                "replacement-key",
                *common,
            )
        )
        assert is_sentinel(
            await review_routes.create_teacher_review_paper_draft(
                JOB_ID,
                ReviewPaperCreateDraftRequest(expected_version=5),
                *common,
            )
        )

    asyncio.run(exercise())
