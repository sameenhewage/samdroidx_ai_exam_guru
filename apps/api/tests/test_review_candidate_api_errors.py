import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.api.routes.review_candidates as routes
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.papers.domain import CandidateInvariantError
from exam_guru_api.papers.repository import (
    CandidatePersistenceIntegrityError,
    ReviewCandidateNotFoundError,
    ReviewCurriculumNotFoundError,
    ReviewValidationRunNotFoundError,
)
from exam_guru_api.papers.review_service import (
    ReviewCandidateIdempotencyConflictError,
    ReviewCandidateStateConflictError,
    ReviewCandidateVersionConflictError,
    ReviewUpstreamIntegrityError,
    ReviewValidationNotPassedError,
)
from exam_guru_api.papers.schemas import (
    ReviewCandidateApproveRequest,
    ReviewCandidateCreateRequest,
    ReviewCandidateEditRequest,
    ReviewCandidateRejectRequest,
    ReviewCandidateResponse,
    ReviewCandidateStartRequest,
    ReviewCandidateSummaryResponse,
)
from tests.test_review_candidate_api_contract import content_payload

CURRICULUM_ID = UUID(int=992_001)
CANDIDATE_ID = UUID(int=992_002)
VALIDATION_ID = UUID(int=992_003)
PRINCIPAL = Principal(UUID(int=992_004), frozenset({AdminRole.REVIEWER}))


class FakeSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeReviewService:
    def __init__(self) -> None:
        self.record = object()

    async def create(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(record=self.record, deduplicated=False)

    async def list(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (self.record,)

    async def get(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def start_review(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def edit(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def approve(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def reject(self, *_args: object, **_kwargs: object) -> object:
        return self.record


def test_review_route_error_mapping_is_stable() -> None:
    async def exercise() -> None:
        session = FakeSession()

        async def success() -> str:
            return "ok"

        assert (
            await routes._execute_review_operation(
                cast(AsyncSession, session),
                success,
            )
            == "ok"
        )
        cases: tuple[tuple[Exception, int, str], ...] = (
            (
                IntegrityError("statement", {}, RuntimeError("integrity")),
                409,
                "review_persistence_conflict",
            ),
            (
                ReviewValidationRunNotFoundError(),
                404,
                "review_validation_run_not_found",
            ),
            (ReviewCandidateNotFoundError(), 404, "review_candidate_not_found"),
            (ReviewCurriculumNotFoundError(), 404, "review_curriculum_not_found"),
            (ReviewValidationNotPassedError(), 409, "review_validation_not_passed"),
            (ReviewUpstreamIntegrityError(), 409, "review_upstream_integrity_invalid"),
            (
                CandidatePersistenceIntegrityError(),
                409,
                "review_upstream_integrity_invalid",
            ),
            (
                ReviewCandidateIdempotencyConflictError(),
                409,
                "review_candidate_idempotency_conflict",
            ),
            (
                ReviewCandidateVersionConflictError(),
                409,
                "review_candidate_version_conflict",
            ),
            (
                ReviewCandidateStateConflictError(),
                409,
                "review_candidate_state_conflict",
            ),
            (CandidateInvariantError(), 422, "review_candidate_content_invalid"),
        )
        for error, status_code, code in cases:

            async def fail(failure: Exception = error) -> None:
                raise failure

            with pytest.raises(HTTPException) as raised:
                await routes._execute_review_operation(
                    cast(AsyncSession, session),
                    fail,
                )
            assert raised.value.status_code == status_code
            assert cast(dict[str, str], raised.value.detail)["code"] == code
        assert session.rollbacks == 1

    asyncio.run(exercise())


def test_review_route_handlers_delegate_typed_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        service = FakeReviewService()
        detail_sentinel = cast(ReviewCandidateResponse, object())
        summary_sentinel = cast(ReviewCandidateSummaryResponse, object())
        monkeypatch.setattr(routes, "ReviewCandidateService", lambda _session: service)
        monkeypatch.setattr(
            ReviewCandidateResponse,
            "from_record",
            lambda *_args, **_kwargs: detail_sentinel,
        )
        monkeypatch.setattr(
            ReviewCandidateSummaryResponse,
            "from_record",
            lambda *_args, **_kwargs: summary_sentinel,
        )
        session = cast(AsyncSession, FakeSession())
        assert (
            await routes.create_review_candidate(
                CURRICULUM_ID,
                ReviewCandidateCreateRequest(validation_run_id=VALIDATION_ID),
                PRINCIPAL,
                session,
            )
            is detail_sentinel
        )
        assert await routes.list_review_candidates(
            CURRICULUM_ID,
            PRINCIPAL,
            session,
            state="validated",
            paper_blueprint_id=None,
            blueprint_slot_id=None,
            limit=10,
            offset=0,
        ) == [summary_sentinel]
        assert (
            await routes.get_review_candidate(
                CURRICULUM_ID,
                CANDIDATE_ID,
                PRINCIPAL,
                session,
            )
            is detail_sentinel
        )
        assert (
            await routes.start_review_candidate(
                CURRICULUM_ID,
                CANDIDATE_ID,
                ReviewCandidateStartRequest(expected_version=2),
                PRINCIPAL,
                session,
            )
            is detail_sentinel
        )
        assert (
            await routes.edit_review_candidate(
                CURRICULUM_ID,
                CANDIDATE_ID,
                ReviewCandidateEditRequest(
                    content=content_payload(),
                    reason="Edit reason.",
                    expected_version=3,
                ),
                PRINCIPAL,
                session,
            )
            is detail_sentinel
        )
        assert (
            await routes.approve_review_candidate(
                CURRICULUM_ID,
                CANDIDATE_ID,
                ReviewCandidateApproveRequest(expected_version=3, note=None),
                PRINCIPAL,
                session,
            )
            is detail_sentinel
        )
        assert (
            await routes.reject_review_candidate(
                CURRICULUM_ID,
                CANDIDATE_ID,
                ReviewCandidateRejectRequest(
                    expected_version=3,
                    reason="Reject reason.",
                ),
                PRINCIPAL,
                session,
            )
            is detail_sentinel
        )

    asyncio.run(exercise())
