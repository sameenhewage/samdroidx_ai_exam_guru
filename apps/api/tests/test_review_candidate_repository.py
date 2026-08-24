import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.papers.domain import (
    CandidateState,
    QuestionCandidate,
    ReviewAction,
    approve_candidate,
    create_generated_candidate,
    edit_candidate,
    reject_candidate,
    start_candidate_review,
)
from exam_guru_api.papers.models import (
    CandidateReviewEventModel,
    QuestionCandidateModel,
    QuestionCandidateRevisionModel,
)
from exam_guru_api.papers.repository import (
    CandidatePersistenceIntegrityError,
    ReviewCandidateNotFoundError,
    ReviewCandidateSummary,
    ReviewCurriculumNotFoundError,
    ReviewValidationRunNotFoundError,
    SqlAlchemyReviewCandidateRepository,
    _domain_candidate,
    _integer,
    _lineage_from_payload,
    _mapping,
    _sequence,
    _text,
    _validation_from_payload,
    generation_lineage_payload,
    question_content_from_payload,
    question_content_payload,
    validation_evidence_payload,
)
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel
from tests.test_paper_generation_validation_integration import (
    VALIDATION_RUN_ID,
    adapt_persisted,
    generation_result,
    passing_report,
)

NOW = datetime(2026, 8, 24, tzinfo=UTC)
CURRICULUM_ID = UUID(int=991_001)
PAPER_BLUEPRINT_ID = UUID(int=991_002)
ACTOR_ID = UUID(int=991_003)
REVIEWER_ID = UUID(int=991_004)


class ScalarRows:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


class ExecuteResult:
    def __init__(
        self,
        row: object | None = None,
        *,
        rows: tuple[tuple[object, ...], ...] = (),
    ) -> None:
        self._row = row
        self._rows = rows

    def one_or_none(self) -> object | None:
        return self._row

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class ScriptedSession:
    def __init__(
        self,
        *,
        scalar_results: tuple[object | None, ...] = (),
        scalar_rows: tuple[tuple[object, ...], ...] = (),
        execute_results: tuple[ExecuteResult, ...] = (),
    ) -> None:
        self.scalar_results = list(scalar_results)
        self.scalar_rows = list(scalar_rows)
        self.execute_results = list(execute_results)
        self.added: list[object] = []
        self.flushes = 0

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_results.pop(0)

    async def scalars(self, statement: object) -> ScalarRows:
        del statement
        return ScalarRows(self.scalar_rows.pop(0))

    async def execute(self, statement: object) -> ExecuteResult:
        del statement
        return self.execute_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def validated_candidate() -> QuestionCandidate:
    result = generation_result()
    return adapt_persisted(result, passing_report(result))


def candidate_models(
    domain: QuestionCandidate,
) -> tuple[
    QuestionCandidateModel,
    tuple[QuestionCandidateRevisionModel, ...],
    tuple[CandidateReviewEventModel, ...],
]:
    assert domain.validation is not None
    candidate = QuestionCandidateModel(
        id=domain.candidate_id,
        curriculum_version_id=CURRICULUM_ID,
        generation_run_id=domain.lineage.generation_id,
        generation_attempt_id=domain.lineage.generation_attempt_id,
        validation_run_id=domain.validation.validation_run_id,
        paper_blueprint_id=PAPER_BLUEPRINT_ID,
        blueprint_id=domain.lineage.blueprint_id,
        blueprint_version=domain.lineage.blueprint_version,
        blueprint_slot_id=domain.lineage.blueprint_slot_id,
        state=domain.state.value,
        version=domain.version,
        current_revision=domain.revisions[-1].revision,
        generation_lineage=generation_lineage_payload(
            domain.lineage,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
        ),
        validation_evidence=validation_evidence_payload(domain.validation),
        created_by=ACTOR_ID,
        created_at=NOW,
    )
    revisions = tuple(
        QuestionCandidateRevisionModel(
            candidate_id=domain.candidate_id,
            revision=revision.revision,
            candidate_version=2 if revision.revision == 1 else revision.revision + 2,
            content=question_content_payload(revision.content),
            reviewer_id=revision.reviewer_id,
            reason=revision.reason,
            created_at=NOW,
        )
        for revision in domain.revisions
    )
    current_revision = 1
    events: list[CandidateReviewEventModel] = []
    for record in domain.review_history:
        if record.action is ReviewAction.EDITED:
            current_revision += 1
        events.append(
            CandidateReviewEventModel(
                candidate_id=domain.candidate_id,
                candidate_version=record.candidate_version,
                action=record.action.value,
                reviewer_id=record.reviewer_id,
                revision=current_revision,
                reason=record.reason,
                created_at=NOW,
            )
        )
    return candidate, revisions, tuple(events)


def test_candidate_payloads_round_trip_every_persisted_review_state() -> None:
    validated = validated_candidate()
    reviewing = start_candidate_review(
        validated,
        reviewer_id=REVIEWER_ID,
        expected_version=2,
    )
    revised_content = reviewing.content.__class__(
        question_type=reviewing.content.question_type,
        stem="Reviewer clarified stem.",
        options=reviewing.content.options,
        answer=reviewing.content.answer,
        explanation="Reviewer clarified explanation.",
        marks=reviewing.content.marks,
        marking_guide=("Reviewer clarified guide.",),
    )
    edited = edit_candidate(
        reviewing,
        content=revised_content,
        reviewer_id=REVIEWER_ID,
        reason="Clarify content.",
        expected_version=3,
    )
    approved = approve_candidate(
        edited,
        reviewer_id=REVIEWER_ID,
        expected_version=4,
        note="Reviewed.",
    )
    rejected = reject_candidate(
        reviewing,
        reviewer_id=REVIEWER_ID,
        reason="Unsupported answer.",
        expected_version=3,
    )

    for expected in (validated, reviewing, edited, approved, rejected):
        candidate, revisions, events = candidate_models(expected)
        assert _domain_candidate(candidate, revisions, events) == expected
    assert (
        question_content_from_payload(question_content_payload(revised_content)) == revised_content
    )
    assert validated.validation is not None
    assert validation_evidence_payload(validated.validation) == {
        "validation_run_id": str(VALIDATION_RUN_ID),
        "validator_version": validated.validation.validator_version,
        "finding_refs": list(validated.validation.finding_refs),
        "passed": True,
        "validated_revision": 1,
    }


def test_candidate_payload_reconstruction_fails_closed_on_malformed_values() -> None:
    with pytest.raises(CandidatePersistenceIntegrityError, match="shape"):
        _mapping([], keys=frozenset(), label="fixture")
    with pytest.raises(CandidatePersistenceIntegrityError, match="array"):
        _sequence("text", label="fixture")
    with pytest.raises(CandidatePersistenceIntegrityError, match="text"):
        _text(1, label="fixture")
    with pytest.raises(CandidatePersistenceIntegrityError, match="integer"):
        _integer(True, label="fixture")

    domain = validated_candidate()
    candidate, revisions, events = candidate_models(domain)
    candidate.generation_lineage = {
        **candidate.generation_lineage,
        "generation_id": "not-a-uuid",
    }
    with pytest.raises(CandidatePersistenceIntegrityError, match="UUID"):
        _lineage_from_payload(candidate)

    candidate, revisions, events = candidate_models(domain)
    candidate.generation_lineage = {
        **candidate.generation_lineage,
        "generation_id": str(UUID(int=123)),
    }
    with pytest.raises(CandidatePersistenceIntegrityError, match="columns"):
        _lineage_from_payload(candidate)

    candidate, revisions, events = candidate_models(domain)
    candidate.validation_evidence = {
        **candidate.validation_evidence,
        "validation_run_id": "not-a-uuid",
    }
    with pytest.raises(CandidatePersistenceIntegrityError, match="validation_run_id"):
        _validation_from_payload(candidate)

    candidate, revisions, events = candidate_models(domain)
    candidate.validation_evidence = {
        **candidate.validation_evidence,
        "validation_run_id": str(UUID(int=124)),
    }
    with pytest.raises(CandidatePersistenceIntegrityError, match="columns"):
        _validation_from_payload(candidate)

    candidate, revisions, events = candidate_models(domain)
    candidate.validation_evidence = {**candidate.validation_evidence, "passed": 1}
    with pytest.raises(CandidatePersistenceIntegrityError, match="boolean"):
        _validation_from_payload(candidate)

    candidate, revisions, events = candidate_models(domain)
    candidate.state = "forged"
    with pytest.raises(CandidatePersistenceIntegrityError, match="cannot be reconstructed"):
        _domain_candidate(candidate, revisions, events)

    candidate, revisions, events = candidate_models(domain)
    candidate.current_revision = 2
    with pytest.raises(CandidatePersistenceIntegrityError, match="current revision"):
        _domain_candidate(candidate, revisions, events)


def test_review_repository_source_lookup_insert_get_and_missing_paths() -> None:
    async def exercise() -> None:
        domain = validated_candidate()
        candidate, revisions, events = candidate_models(domain)
        validation = ValidationRunModel(id=VALIDATION_RUN_ID)
        generation = generation_result()
        generation_model = cast(object, generation.request)
        attempt_model = cast(object, generation.request.identity)
        finding = ValidationFindingModel(id=UUID(int=1), validation_run_id=VALIDATION_RUN_ID)

        source_session = ScriptedSession(
            execute_results=(ExecuteResult((validation, generation_model, attempt_model)),),
            scalar_rows=((finding,),),
        )
        source = await SqlAlchemyReviewCandidateRepository(
            cast(AsyncSession, source_session)
        ).get_creation_source(CURRICULUM_ID, VALIDATION_RUN_ID)
        assert source.validation is validation
        assert source.findings == (finding,)

        with pytest.raises(ReviewValidationRunNotFoundError):
            await SqlAlchemyReviewCandidateRepository(
                cast(
                    AsyncSession,
                    ScriptedSession(execute_results=(ExecuteResult(None),)),
                )
            ).get_creation_source(CURRICULUM_ID, VALIDATION_RUN_ID)

        missing = SqlAlchemyReviewCandidateRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        assert await missing.find_by_validation(CURRICULUM_ID, VALIDATION_RUN_ID) is None
        with pytest.raises(ReviewCandidateNotFoundError):
            await SqlAlchemyReviewCandidateRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
            ).get(CURRICULUM_ID, domain.candidate_id)

        get_session = ScriptedSession(
            scalar_results=(candidate,),
            scalar_rows=(revisions, events),
        )
        record = await SqlAlchemyReviewCandidateRepository(cast(AsyncSession, get_session)).get(
            CURRICULUM_ID, domain.candidate_id
        )
        assert record.domain == domain

        inserted_session = ScriptedSession(scalar_results=(domain.candidate_id,))
        inserted_repository = SqlAlchemyReviewCandidateRepository(
            cast(AsyncSession, inserted_session)
        )
        assert await inserted_repository.insert_initial(
            curriculum_version_id=CURRICULUM_ID,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            candidate=domain,
            actor_id=ACTOR_ID,
        )
        assert isinstance(inserted_session.added[0], QuestionCandidateRevisionModel)
        assert inserted_session.flushes == 1

        duplicate_repository = SqlAlchemyReviewCandidateRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        assert not await duplicate_repository.insert_initial(
            curriculum_version_id=CURRICULUM_ID,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            candidate=domain,
            actor_id=ACTOR_ID,
        )

        generated = create_generated_candidate(
            candidate_id=domain.candidate_id,
            lineage=domain.lineage,
            content=domain.content,
        )
        with pytest.raises(CandidatePersistenceIntegrityError, match="evidence"):
            await duplicate_repository.insert_initial(
                curriculum_version_id=CURRICULUM_ID,
                paper_blueprint_id=PAPER_BLUEPRINT_ID,
                candidate=generated,
                actor_id=ACTOR_ID,
            )

    asyncio.run(exercise())


def test_review_repository_list_uses_lightweight_rows_without_history_loads() -> None:
    async def exercise() -> None:
        domain = validated_candidate()
        candidate, revisions, _events = candidate_models(domain)
        long_stem = "s" * 700
        summary_row: tuple[object, ...] = (
            candidate.id,
            candidate.curriculum_version_id,
            candidate.generation_run_id,
            candidate.generation_attempt_id,
            candidate.validation_run_id,
            candidate.paper_blueprint_id,
            candidate.blueprint_id,
            candidate.blueprint_version,
            candidate.blueprint_slot_id,
            candidate.state,
            candidate.version,
            candidate.current_revision,
            candidate.created_by,
            candidate.created_at,
            domain.content.question_type,
            long_stem[:512],
            domain.content.marks,
            revisions[-1].created_at,
        )
        session = ScriptedSession(
            scalar_results=(CURRICULUM_ID,),
            execute_results=(ExecuteResult(rows=(summary_row,)),),
        )
        repository = SqlAlchemyReviewCandidateRepository(cast(AsyncSession, session))
        get = AsyncMock(side_effect=AssertionError("list must not load candidate history"))
        object.__setattr__(repository, "get", get)

        records = await repository.list(
            CURRICULUM_ID,
            state=CandidateState.VALIDATED,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            blueprint_slot_id=domain.lineage.blueprint_slot_id,
            limit=10,
            offset=0,
        )

        assert records == (
            ReviewCandidateSummary(
                id=candidate.id,
                curriculum_version_id=candidate.curriculum_version_id,
                generation_run_id=candidate.generation_run_id,
                generation_attempt_id=candidate.generation_attempt_id,
                validation_run_id=candidate.validation_run_id,
                paper_blueprint_id=candidate.paper_blueprint_id,
                blueprint_id=candidate.blueprint_id,
                blueprint_version=candidate.blueprint_version,
                blueprint_slot_id=candidate.blueprint_slot_id,
                state=CandidateState.VALIDATED,
                version=2,
                current_revision=1,
                created_by=ACTOR_ID,
                created_at=NOW,
                question_type=domain.content.question_type,
                stem_preview="s" * 512,
                marks=domain.content.marks,
                current_revision_created_at=NOW,
            ),
        )
        assert get.await_count == 0
        assert session.scalar_rows == []

        unfiltered_session = ScriptedSession(
            scalar_results=(CURRICULUM_ID,),
            execute_results=(ExecuteResult(rows=()),),
        )
        assert (
            await SqlAlchemyReviewCandidateRepository(cast(AsyncSession, unfiltered_session)).list(
                CURRICULUM_ID,
                state=None,
                paper_blueprint_id=None,
                blueprint_slot_id=None,
                limit=10,
                offset=0,
            )
            == ()
        )

        missing_repository = SqlAlchemyReviewCandidateRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        with pytest.raises(ReviewCurriculumNotFoundError):
            await missing_repository.list(
                CURRICULUM_ID,
                state=None,
                paper_blueprint_id=None,
                blueprint_slot_id=None,
                limit=10,
                offset=0,
            )

        find_repository = SqlAlchemyReviewCandidateRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(domain.candidate_id,)))
        )
        expected_record = cast(object, "record")
        object.__setattr__(find_repository, "get", AsyncMock(return_value=expected_record))
        assert (
            await find_repository.find_by_validation(
                CURRICULUM_ID,
                VALIDATION_RUN_ID,
            )
            == expected_record
        )

    asyncio.run(exercise())


def test_review_repository_cas_and_append_helpers() -> None:
    async def exercise() -> None:
        domain = validated_candidate()
        session = ScriptedSession(scalar_results=(domain.candidate_id, None))
        repository = SqlAlchemyReviewCandidateRepository(cast(AsyncSession, session))
        assert await repository.cas_update(
            curriculum_version_id=CURRICULUM_ID,
            candidate_id=domain.candidate_id,
            expected_version=2,
            expected_state=CandidateState.VALIDATED,
            state=CandidateState.IN_REVIEW,
            version=3,
            current_revision=1,
        )
        assert not await repository.cas_update(
            curriculum_version_id=CURRICULUM_ID,
            candidate_id=domain.candidate_id,
            expected_version=2,
            expected_state=CandidateState.VALIDATED,
            state=CandidateState.IN_REVIEW,
            version=3,
            current_revision=1,
        )
        repository.add_revision(
            candidate_id=domain.candidate_id,
            revision=2,
            candidate_version=4,
            content=domain.content,
            reviewer_id=REVIEWER_ID,
            reason="Reason.",
        )
        repository.add_event(
            candidate_id=domain.candidate_id,
            candidate_version=3,
            action=ReviewAction.STARTED,
            reviewer_id=REVIEWER_ID,
            revision=1,
            reason=None,
        )
        assert isinstance(session.added[-2], QuestionCandidateRevisionModel)
        assert isinstance(session.added[-1], CandidateReviewEventModel)

    asyncio.run(exercise())
