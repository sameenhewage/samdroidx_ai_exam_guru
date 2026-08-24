import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.papers.review_service as review_service_module
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.papers.domain import (
    CandidateInvariantError,
    QuestionCandidate,
    ReviewAction,
    ValidationNotPassedError,
    approve_candidate,
    start_candidate_review,
)
from exam_guru_api.papers.repository import (
    CandidatePersistenceIntegrityError,
    ReviewCandidateSummary,
    ReviewCreationSource,
    SqlAlchemyReviewCandidateRepository,
    StoredQuestionCandidate,
)
from exam_guru_api.papers.review_service import (
    ReviewCandidateIdempotencyConflictError,
    ReviewCandidateService,
    ReviewCandidateStateConflictError,
    ReviewCandidateVersionConflictError,
    ReviewUpstreamIntegrityError,
    ReviewValidationNotPassedError,
    cast_reason,
)
from exam_guru_api.validation.models import ValidationRunModel
from exam_guru_api.validation.repository import ValidationGenerationRecord
from exam_guru_api.validation.service import ValidationReportIntegrityError
from tests.test_review_candidate_repository import (
    ACTOR_ID,
    CURRICULUM_ID,
    PAPER_BLUEPRINT_ID,
    REVIEWER_ID,
    candidate_models,
    validated_candidate,
)

CANDIDATE_ID = validated_candidate().candidate_id
PRINCIPAL = Principal(REVIEWER_ID, frozenset({AdminRole.REVIEWER}))


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.commit_error = commit_error

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        self.flushes += 1


class FakeRepository:
    def __init__(self, record: StoredQuestionCandidate) -> None:
        self.record = record
        self.existing: StoredQuestionCandidate | None = None
        self.winner: StoredQuestionCandidate | None = record
        self.source = ReviewCreationSource(
            validation=ValidationRunModel(
                id=record.candidate.validation_run_id,
                overall_status="pass",
            ),
            generation=ValidationGenerationRecord(
                GenerationRunModel(paper_blueprint_id=PAPER_BLUEPRINT_ID),
                GenerationAttemptModel(),
            ),
            findings=(),
        )
        self.created = True
        self.cas_result = True
        self.integrity_error = False
        self.find_calls = 0
        self.revisions: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []

    async def find_by_validation(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
    ) -> StoredQuestionCandidate | None:
        del curriculum_version_id, validation_run_id
        self.find_calls += 1
        if self.existing is not None:
            return self.existing
        return self.winner if not self.created and self.find_calls > 1 else None

    async def get_creation_source(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
    ) -> ReviewCreationSource:
        del curriculum_version_id, validation_run_id
        return self.source

    async def insert_initial(self, **values: object) -> bool:
        del values
        return self.created

    async def get(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
    ) -> StoredQuestionCandidate:
        del curriculum_version_id, candidate_id
        if self.integrity_error:
            raise CandidatePersistenceIntegrityError("tampered")
        return self.record

    async def list(
        self,
        curriculum_version_id: UUID,
        **filters: object,
    ) -> tuple[ReviewCandidateSummary, ...]:
        del curriculum_version_id, filters
        if self.integrity_error:
            raise CandidatePersistenceIntegrityError("tampered")
        return (cast(ReviewCandidateSummary, self.record),)

    async def cas_update(self, **values: object) -> bool:
        del values
        return self.cas_result

    def add_revision(self, **values: object) -> None:
        self.revisions.append(values)

    def add_event(self, **values: object) -> None:
        self.events.append(values)


def record_for(domain: QuestionCandidate) -> StoredQuestionCandidate:
    candidate, revisions, events = candidate_models(domain)
    return StoredQuestionCandidate(candidate, revisions, events, domain)


def service_with(
    domain: QuestionCandidate,
    *,
    commit_error: Exception | None = None,
) -> tuple[ReviewCandidateService, FakeSession, FakeRepository]:
    session = FakeSession(commit_error=commit_error)
    repository = FakeRepository(record_for(domain))
    service = ReviewCandidateService(cast(AsyncSession, session))
    service._repository = cast(SqlAlchemyReviewCandidateRepository, repository)
    return service, session, repository


def patch_valid_creation(monkeypatch: pytest.MonkeyPatch, candidate: QuestionCandidate) -> None:
    monkeypatch.setattr(
        review_service_module,
        "reconstruct_generation_result",
        lambda _record: object(),
    )
    monkeypatch.setattr(
        review_service_module,
        "reconstruct_validation_report",
        lambda _run, _findings: SimpleNamespace(
            report=SimpleNamespace(passed=True),
            finding_ids=(),
        ),
    )
    monkeypatch.setattr(
        review_service_module,
        "adapt_generation_validation",
        lambda *_args, **_kwargs: candidate,
    )


def test_review_candidate_create_idempotency_nonpass_and_success_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        candidate = validated_candidate()
        service, session, repository = service_with(candidate)
        repository.existing = repository.record
        existing = await service.create(
            CURRICULUM_ID,
            validation_run_id=repository.record.candidate.validation_run_id,
            principal=PRINCIPAL,
        )
        assert existing.deduplicated
        assert session.commits == 0

        service, _session, repository = service_with(candidate)
        repository.source.validation.overall_status = "warn"
        with pytest.raises(ReviewValidationNotPassedError):
            await service.create(
                CURRICULUM_ID,
                validation_run_id=repository.record.candidate.validation_run_id,
                principal=PRINCIPAL,
            )

        patch_valid_creation(monkeypatch, candidate)
        service, session, repository = service_with(candidate)
        created = await service.create(
            CURRICULUM_ID,
            validation_run_id=repository.record.candidate.validation_run_id,
            principal=PRINCIPAL,
        )
        assert not created.deduplicated
        assert session.commits == 1
        assert session.added

        service, session, repository = service_with(candidate)
        repository.created = False
        deduplicated = await service.create(
            CURRICULUM_ID,
            validation_run_id=repository.record.candidate.validation_run_id,
            principal=PRINCIPAL,
        )
        assert deduplicated.deduplicated
        assert session.commits == 1

        service, session, repository = service_with(candidate)
        repository.created = False
        repository.winner = None
        with pytest.raises(ReviewCandidateIdempotencyConflictError):
            await service.create(
                CURRICULUM_ID,
                validation_run_id=repository.record.candidate.validation_run_id,
                principal=PRINCIPAL,
            )
        assert session.rollbacks == 1

    asyncio.run(exercise())


def test_review_candidate_create_rejects_report_and_adapter_integrity_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        candidate = validated_candidate()
        service, _session, repository = service_with(candidate)
        monkeypatch.setattr(
            review_service_module,
            "reconstruct_generation_result",
            lambda _record: object(),
        )
        monkeypatch.setattr(
            review_service_module,
            "reconstruct_validation_report",
            lambda _run, _findings: SimpleNamespace(
                report=SimpleNamespace(passed=False),
                finding_ids=(),
            ),
        )
        with pytest.raises(ReviewValidationNotPassedError):
            await service.create(
                CURRICULUM_ID,
                validation_run_id=repository.record.candidate.validation_run_id,
                principal=PRINCIPAL,
            )

        patch_valid_creation(monkeypatch, candidate)
        for error in (
            ValidationNotPassedError(candidate.candidate_id),
            CandidateInvariantError("invalid"),
            ValidationReportIntegrityError("tampered"),
        ):
            service, _session, repository = service_with(candidate)

            def reject_adapter(
                *_args: object,
                failure: Exception = error,
                **_kwargs: object,
            ) -> object:
                raise failure

            monkeypatch.setattr(
                review_service_module,
                "adapt_generation_validation",
                reject_adapter,
            )
            expected = (
                ReviewValidationNotPassedError
                if isinstance(error, ValidationNotPassedError)
                else ReviewUpstreamIntegrityError
            )
            with pytest.raises(expected):
                await service.create(
                    CURRICULUM_ID,
                    validation_run_id=repository.record.candidate.validation_run_id,
                    principal=PRINCIPAL,
                )

    asyncio.run(exercise())


def test_review_candidate_read_boundaries_map_persistence_integrity() -> None:
    async def exercise() -> None:
        candidate = validated_candidate()
        service, _session, repository = service_with(candidate)
        assert (
            await service.get(
                CURRICULUM_ID,
                candidate.candidate_id,
                principal=PRINCIPAL,
            )
            is repository.record
        )
        assert await service.list(
            CURRICULUM_ID,
            principal=PRINCIPAL,
            state=None,
            paper_blueprint_id=None,
            blueprint_slot_id=None,
            limit=10,
            offset=0,
        ) == (cast(ReviewCandidateSummary, repository.record),)
        repository.integrity_error = True
        with pytest.raises(ReviewUpstreamIntegrityError):
            await service.get(
                CURRICULUM_ID,
                candidate.candidate_id,
                principal=PRINCIPAL,
            )
        with pytest.raises(ReviewUpstreamIntegrityError):
            await service.list(
                CURRICULUM_ID,
                principal=PRINCIPAL,
                state=None,
                paper_blueprint_id=None,
                blueprint_slot_id=None,
                limit=10,
                offset=0,
            )

    asyncio.run(exercise())


def test_review_candidate_transition_commands_and_domain_conflicts() -> None:
    async def exercise() -> None:
        validated = validated_candidate()
        service, session, repository = service_with(validated)
        await service.start_review(
            CURRICULUM_ID,
            validated.candidate_id,
            expected_version=2,
            principal=PRINCIPAL,
        )
        assert repository.events[-1]["action"] is ReviewAction.STARTED
        assert session.commits == 1

        reviewing = start_candidate_review(
            validated,
            reviewer_id=REVIEWER_ID,
            expected_version=2,
        )
        revised_content = reviewing.content.__class__(
            question_type=reviewing.content.question_type,
            stem="Edited stem.",
            options=reviewing.content.options,
            answer=reviewing.content.answer,
            explanation="Edited explanation.",
            marks=reviewing.content.marks,
            marking_guide=("Edited guide.",),
        )
        service, session, repository = service_with(reviewing)
        await service.edit(
            CURRICULUM_ID,
            reviewing.candidate_id,
            content=revised_content,
            reason="Edit reason.",
            expected_version=3,
            principal=PRINCIPAL,
        )
        assert repository.revisions
        assert repository.events[-1]["action"] is ReviewAction.EDITED
        assert session.flushes == 1

        service, _session, repository = service_with(reviewing)
        await service.approve(
            CURRICULUM_ID,
            reviewing.candidate_id,
            expected_version=3,
            note=None,
            principal=PRINCIPAL,
        )
        assert repository.events[-1]["action"] is ReviewAction.APPROVED

        service, _session, repository = service_with(reviewing)
        await service.reject(
            CURRICULUM_ID,
            reviewing.candidate_id,
            expected_version=3,
            reason="Reject reason.",
            principal=PRINCIPAL,
        )
        assert repository.events[-1]["action"] is ReviewAction.REJECTED

        service, _session, _repository = service_with(validated)
        with pytest.raises(ReviewCandidateVersionConflictError):
            await service.start_review(
                CURRICULUM_ID,
                validated.candidate_id,
                expected_version=99,
                principal=PRINCIPAL,
            )
        approved = approve_candidate(
            reviewing,
            reviewer_id=REVIEWER_ID,
            expected_version=3,
        )
        service, _session, _repository = service_with(approved)
        with pytest.raises(ReviewCandidateStateConflictError):
            await service.approve(
                CURRICULUM_ID,
                approved.candidate_id,
                expected_version=4,
                note=None,
                principal=PRINCIPAL,
            )
        service, _session, _repository = service_with(approved)
        with pytest.raises(ReviewCandidateStateConflictError):
            await service.start_review(
                CURRICULUM_ID,
                approved.candidate_id,
                expected_version=4,
                principal=PRINCIPAL,
            )
        service, _session, _repository = service_with(approved)
        with pytest.raises(ReviewCandidateStateConflictError):
            await service.edit(
                CURRICULUM_ID,
                approved.candidate_id,
                content=revised_content,
                reason="Edit reason.",
                expected_version=4,
                principal=PRINCIPAL,
            )

        service, _session, _repository = service_with(reviewing)
        with pytest.raises(ReviewCandidateVersionConflictError):
            await service.edit(
                CURRICULUM_ID,
                reviewing.candidate_id,
                content=revised_content,
                reason="Edit reason.",
                expected_version=99,
                principal=PRINCIPAL,
            )
        service, _session, _repository = service_with(reviewing)
        with pytest.raises(ReviewCandidateVersionConflictError):
            await service.approve(
                CURRICULUM_ID,
                reviewing.candidate_id,
                expected_version=99,
                note=None,
                principal=PRINCIPAL,
            )
        service, _session, _repository = service_with(reviewing)
        with pytest.raises(ReviewCandidateVersionConflictError):
            await service.reject(
                CURRICULUM_ID,
                reviewing.candidate_id,
                expected_version=99,
                reason="Reject reason.",
                principal=PRINCIPAL,
            )
        service, _session, _repository = service_with(approved)
        with pytest.raises(ReviewCandidateStateConflictError):
            await service.reject(
                CURRICULUM_ID,
                approved.candidate_id,
                expected_version=4,
                reason="Reject reason.",
                principal=PRINCIPAL,
            )

    asyncio.run(exercise())


def test_review_transition_cas_conflicts_rollback_and_boundary_helpers() -> None:
    async def exercise() -> None:
        validated = validated_candidate()
        reviewing = start_candidate_review(
            validated,
            reviewer_id=REVIEWER_ID,
            expected_version=2,
        )
        transitioned = approve_candidate(
            reviewing,
            reviewer_id=REVIEWER_ID,
            expected_version=3,
        )
        service, session, repository = service_with(reviewing)
        repository.cas_result = False
        get = AsyncMock(side_effect=AssertionError("CAS loss must not perform a follow-up read"))
        object.__setattr__(repository, "get", get)
        with pytest.raises(ReviewCandidateVersionConflictError):
            await service._persist_transition(
                curriculum_version_id=CURRICULUM_ID,
                record=record_for(reviewing),
                transitioned=transitioned,
                expected_version=3,
                actor_id=ACTOR_ID,
                action=ReviewAction.APPROVED,
                reason=None,
            )
        assert session.rollbacks == 1
        assert get.await_count == 0

        service, _session, _repository = service_with(reviewing)
        with pytest.raises(TypeError, match="QuestionCandidate"):
            await service._persist_transition(
                curriculum_version_id=CURRICULUM_ID,
                record=record_for(reviewing),
                transitioned=object(),
                expected_version=3,
                actor_id=ACTOR_ID,
                action=ReviewAction.APPROVED,
                reason=None,
            )

        service, session, _repository = service_with(
            reviewing,
            commit_error=RuntimeError("commit failed"),
        )
        with pytest.raises(RuntimeError, match="commit failed"):
            await service._persist_transition(
                curriculum_version_id=CURRICULUM_ID,
                record=record_for(reviewing),
                transitioned=transitioned,
                expected_version=3,
                actor_id=ACTOR_ID,
                action=ReviewAction.APPROVED,
                reason=None,
            )
        assert session.rollbacks == 1

    asyncio.run(exercise())
    assert cast_reason("Reason.") == "Reason."
    with pytest.raises(CandidateInvariantError, match="reason"):
        cast_reason(None)
