import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.api.routes.papers as routes
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.papers.domain import (
    AssemblyViolation,
    CandidateInvariantError,
    PaperAssemblyError,
)
from exam_guru_api.papers.publication_repository import (
    PaperArchiveNotFoundError,
    PaperBlueprintNotFoundError,
    PaperCandidateSelectionSourceLimitError,
    PaperDraftNotFoundError,
    PaperNotFoundError,
    PaperPersistenceIntegrityError,
    PaperPublicationNotFoundError,
)
from exam_guru_api.papers.publication_schemas import (
    PaperAggregateResponse,
    PaperArchiveRequest,
    PaperArchiveResponse,
    PaperDraftCreateRequest,
    PaperDraftVersionResponse,
    PaperPublishRequest,
    PaperRevisionCreateRequest,
    PaperSummaryResponse,
    PublishedPaperVersionResponse,
    PublishedPaperVersionSummaryResponse,
)
from exam_guru_api.papers.publication_service import (
    PaperCandidateSelectionError,
    PaperCandidateSelectionResourceLimitError,
    PaperCommandInvalidError,
    PaperIdempotencyConflictError,
    PaperIntegrityError,
    PaperStateConflictError,
    PaperVersionConflictError,
)

CURRICULUM_ID = UUID(int=999_001)
PAPER_ID = UUID(int=999_002)
BLUEPRINT_ID = UUID(int=999_003)
CANDIDATE_ID = UUID(int=999_004)
PRINCIPAL = Principal(UUID(int=999_005), frozenset({AdminRole.ADMIN}))


class FakeSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakePaperService:
    def __init__(self) -> None:
        self.record = object()
        self.model = object()
        self.summary = object()

    async def create_draft(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(record=self.record, deduplicated=False)

    async def list_papers(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (self.summary,)

    async def get_paper(self, *_args: object, **_kwargs: object) -> object:
        return self.model

    async def list_drafts(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (self.record,)

    async def get_draft(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def revise(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(record=self.record, deduplicated=False)

    async def publish(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(record=self.record, deduplicated=False)

    async def list_publications(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (self.summary,)

    async def get_publication(self, *_args: object, **_kwargs: object) -> object:
        return self.record

    async def archive(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(record=self.record, deduplicated=False)

    async def get_archive(self, *_args: object, **_kwargs: object) -> object:
        return self.record


def test_paper_route_error_mapping_is_stable() -> None:
    async def exercise() -> None:
        session = FakeSession()

        async def success() -> str:
            return "ok"

        assert await routes._execute_paper_operation(cast(AsyncSession, session), success) == "ok"
        cases: tuple[tuple[Exception, int, str], ...] = (
            (
                IntegrityError("statement", {}, RuntimeError("integrity")),
                409,
                "paper_persistence_conflict",
            ),
            (PaperBlueprintNotFoundError(), 404, "paper_blueprint_not_found"),
            (PaperNotFoundError(), 404, "paper_not_found"),
            (PaperDraftNotFoundError(), 404, "paper_draft_not_found"),
            (PaperPublicationNotFoundError(), 404, "paper_publication_not_found"),
            (PaperArchiveNotFoundError(), 404, "paper_archive_not_found"),
            (PaperIdempotencyConflictError(), 409, "paper_idempotency_conflict"),
            (PaperVersionConflictError(), 409, "paper_version_conflict"),
            (PaperStateConflictError(), 409, "paper_state_conflict"),
            (PaperIntegrityError(), 409, "paper_integrity_invalid"),
            (PaperPersistenceIntegrityError(), 409, "paper_integrity_invalid"),
            (PaperCandidateSelectionError(), 422, "paper_candidate_selection_invalid"),
            (
                PaperCandidateSelectionResourceLimitError(17, 16),
                422,
                "paper_candidate_selection_too_large",
            ),
            (
                PaperCandidateSelectionSourceLimitError(17, 16),
                422,
                "paper_candidate_selection_too_large",
            ),
            (
                PaperAssemblyError(AssemblyViolation.NOT_APPROVED, "invalid"),
                422,
                "paper_candidate_selection_invalid",
            ),
            (PaperCommandInvalidError(), 422, "paper_command_invalid"),
            (CandidateInvariantError(), 422, "paper_command_invalid"),
        )
        for error, status_code, code in cases:

            async def fail(failure: Exception = error) -> None:
                raise failure

            with pytest.raises(HTTPException) as raised:
                await routes._execute_paper_operation(cast(AsyncSession, session), fail)
            assert raised.value.status_code == status_code
            assert cast(dict[str, str], raised.value.detail)["code"] == code
        assert session.rollbacks == 1

    asyncio.run(exercise())


def test_paper_route_handlers_delegate_typed_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        service = FakePaperService()
        draft_response = cast(PaperDraftVersionResponse, object())
        aggregate_response = cast(PaperAggregateResponse, object())
        summary_response = cast(PaperSummaryResponse, object())
        publication_response = cast(PublishedPaperVersionResponse, object())
        publication_summary = cast(PublishedPaperVersionSummaryResponse, object())
        archive_response = cast(PaperArchiveResponse, object())
        monkeypatch.setattr(routes, "PaperPublicationService", lambda _session: service)
        monkeypatch.setattr(
            PaperDraftVersionResponse,
            "from_record",
            lambda *_args, **_kwargs: draft_response,
        )
        monkeypatch.setattr(
            PaperAggregateResponse,
            "from_model",
            lambda *_args, **_kwargs: aggregate_response,
        )
        monkeypatch.setattr(
            PaperSummaryResponse,
            "from_record",
            lambda *_args, **_kwargs: summary_response,
        )
        monkeypatch.setattr(
            PublishedPaperVersionResponse,
            "from_record",
            lambda *_args, **_kwargs: publication_response,
        )
        monkeypatch.setattr(
            PublishedPaperVersionSummaryResponse,
            "from_record",
            lambda *_args, **_kwargs: publication_summary,
        )
        monkeypatch.setattr(
            PaperArchiveResponse,
            "from_record",
            lambda *_args, **_kwargs: archive_response,
        )
        session = cast(AsyncSession, FakeSession())
        create_request = PaperDraftCreateRequest(
            paper_blueprint_id=BLUEPRINT_ID,
            title="Paper",
            candidate_ids=(CANDIDATE_ID,),
        )
        assert (
            await routes.create_paper_draft(
                CURRICULUM_ID,
                create_request,
                "idempotency-key",
                PRINCIPAL,
                session,
            )
            is draft_response
        )
        assert await routes.list_practice_papers(
            CURRICULUM_ID,
            PRINCIPAL,
            session,
            state=None,
            paper_blueprint_id=None,
            limit=10,
            offset=0,
        ) == [summary_response]
        assert (
            await routes.get_practice_paper(
                CURRICULUM_ID,
                PAPER_ID,
                PRINCIPAL,
                session,
            )
            is aggregate_response
        )
        assert await routes.list_paper_draft_versions(
            CURRICULUM_ID,
            PAPER_ID,
            PRINCIPAL,
            session,
            limit=10,
            offset=0,
        ) == [draft_response]
        assert (
            await routes.get_paper_draft_version(
                CURRICULUM_ID,
                PAPER_ID,
                1,
                PRINCIPAL,
                session,
            )
            is draft_response
        )
        revision = PaperRevisionCreateRequest(
            expected_version=1,
            candidate_ids=(CANDIDATE_ID,),
            title=None,
        )
        assert (
            await routes.revise_practice_paper(
                CURRICULUM_ID,
                PAPER_ID,
                revision,
                PRINCIPAL,
                session,
            )
            is draft_response
        )
        assert (
            await routes.publish_practice_paper(
                CURRICULUM_ID,
                PAPER_ID,
                PaperPublishRequest(expected_version=1),
                PRINCIPAL,
                session,
            )
            is publication_response
        )
        assert await routes.list_published_paper_versions(
            CURRICULUM_ID,
            PAPER_ID,
            PRINCIPAL,
            session,
            limit=10,
            offset=0,
        ) == [publication_summary]
        assert (
            await routes.get_published_paper_version(
                CURRICULUM_ID,
                PAPER_ID,
                1,
                PRINCIPAL,
                session,
            )
            is publication_response
        )
        archive_request = PaperArchiveRequest(expected_version=1, reason="Retired.")
        assert (
            await routes.archive_practice_paper(
                CURRICULUM_ID,
                PAPER_ID,
                archive_request,
                PRINCIPAL,
                session,
            )
            is archive_response
        )
        assert (
            await routes.get_paper_archive(
                CURRICULUM_ID,
                PAPER_ID,
                PRINCIPAL,
                session,
            )
            is archive_response
        )

    asyncio.run(exercise())
