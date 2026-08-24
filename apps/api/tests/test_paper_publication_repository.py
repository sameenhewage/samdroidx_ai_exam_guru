import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.blueprints.generator import generate_blueprint
from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.blueprints.serialization import (
    fingerprint_snapshot,
    serialize_blueprint,
)
from exam_guru_api.papers.domain import (
    PaperBlueprintReference,
    PaperDraft,
    PaperState,
    QuestionCandidate,
)
from exam_guru_api.papers.publication_models import (
    PaperArchiveEventModel,
    PaperDraftCandidateModel,
    PaperDraftVersionModel,
    PracticePaperModel,
    PublishedPaperVersionModel,
)
from exam_guru_api.papers.publication_repository import (
    MAX_PAPER_SELECTION_SOURCE_BYTES,
    PaperArchiveNotFoundError,
    PaperBlueprintNotFoundError,
    PaperCandidateSelectionNotFoundError,
    PaperCandidateSelectionSourceLimitError,
    PaperDraftNotFoundError,
    PaperNotFoundError,
    PaperPersistenceIntegrityError,
    PaperPublicationNotFoundError,
    SqlAlchemyPaperPublicationRepository,
    StoredPaperDraft,
    StoredPublication,
)
from exam_guru_api.papers.serialization import serialize_published_snapshot
from exam_guru_api.papers.service import (
    PaperWorkflowService,
    PublishPaperCommand,
    RevisePaperCommand,
)
from tests.test_blueprint_domain import CURRICULUM_VERSION_ID, make_specification
from tests.test_paper_domain import REVIEWER_ID, approved_candidate, assembled_draft
from tests.test_paper_service import ADMIN_ID
from tests.test_review_candidate_repository import (
    ACTOR_ID,
    CURRICULUM_ID,
    PAPER_BLUEPRINT_ID,
    ExecuteResult,
    candidate_models,
    validated_candidate,
)
from tests.test_review_candidate_repository import ScriptedSession as BaseScriptedSession

NOW = datetime(2026, 8, 24, tzinfo=UTC)
PAPER_ID = assembled_draft().paper_id
ADMIN = Principal(ADMIN_ID, frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(REVIEWER_ID, frozenset({AdminRole.REVIEWER}))


class ScriptedSession(BaseScriptedSession):
    pass


class RecordingScriptedSession(ScriptedSession):
    def __init__(
        self,
        *,
        scalar_results: tuple[object | None, ...] = (),
        scalar_rows: tuple[tuple[object, ...], ...] = (),
        execute_results: tuple[ExecuteResult, ...] = (),
    ) -> None:
        super().__init__(
            scalar_results=scalar_results,
            scalar_rows=scalar_rows,
            execute_results=execute_results,
        )
        self.scalar_statements: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        self.scalar_statements.append(statement)
        return await super().scalar(statement)


def blueprint_model() -> PaperBlueprintModel:
    blueprint = generate_blueprint(make_specification(), seed=91)
    snapshot = serialize_blueprint(blueprint)
    return PaperBlueprintModel(
        id=PAPER_BLUEPRINT_ID,
        curriculum_version_id=CURRICULUM_VERSION_ID,
        blueprint_id=blueprint.version.blueprint_id,
        result_fingerprint=fingerprint_snapshot(snapshot),
        blueprint=snapshot,
        slot_count=len(blueprint.slots),
    )


def paper_model(*, state: PaperState = PaperState.DRAFT, version: int = 1) -> PracticePaperModel:
    return PracticePaperModel(
        id=PAPER_ID,
        curriculum_version_id=CURRICULUM_ID,
        paper_blueprint_id=PAPER_BLUEPRINT_ID,
        blueprint_id="grade5-paper-blueprint",
        blueprint_version="blueprint-v4",
        state=state.value,
        current_version=version,
        idempotency_key_hash="sha256:" + "1" * 64,
        create_request_fingerprint="sha256:" + "2" * 64,
        created_by=ACTOR_ID,
        created_at=NOW,
        updated_by=ACTOR_ID,
        updated_at=NOW,
    )


def persisted_blueprint_reference() -> PaperBlueprintReference:
    return replace(assembled_draft().blueprint, paper_blueprint_id=PAPER_BLUEPRINT_ID)


def persisted_draft(*, version: int = 1, supersedes_hash: str | None = None) -> PaperDraft:
    source = assembled_draft()
    return PaperDraft(
        paper_id=source.paper_id,
        version=version,
        title=source.title,
        blueprint=persisted_blueprint_reference(),
        candidates=source.candidates,
        previous_version=None if version == 1 else version - 1,
        supersedes_content_hash=supersedes_hash,
    )


def publication_record() -> StoredPublication:
    draft = persisted_draft()
    publication = PaperWorkflowService().publish(
        ADMIN,
        PublishPaperCommand(draft=draft, expected_version=1),
    )
    model = PublishedPaperVersionModel(
        paper_id=publication.paper_id,
        curriculum_version_id=CURRICULUM_ID,
        version=publication.version,
        previous_version=publication.previous_version,
        supersedes_content_hash=publication.supersedes_content_hash,
        snapshot=serialize_published_snapshot(publication),
        content_hash=publication.content_hash,
        published_by=publication.published_by,
        published_at=NOW,
    )
    return StoredPublication(model, publication)


def draft_models(
    draft: PaperDraft,
) -> tuple[PaperDraftVersionModel, tuple[PaperDraftCandidateModel, ...]]:
    model = PaperDraftVersionModel(
        paper_id=draft.paper_id,
        curriculum_version_id=CURRICULUM_ID,
        version=draft.version,
        title=draft.title,
        supersedes_content_hash=draft.supersedes_content_hash,
        created_by=ACTOR_ID,
        created_at=NOW,
    )
    selections = tuple(
        PaperDraftCandidateModel(
            paper_id=draft.paper_id,
            curriculum_version_id=CURRICULUM_ID,
            paper_version=draft.version,
            ordinal=ordinal,
            blueprint_slot_id=candidate.lineage.blueprint_slot_id,
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.version,
            candidate_revision=candidate.revisions[-1].revision,
        )
        for ordinal, candidate in enumerate(draft.candidates, start=1)
    )
    return model, selections


def test_blueprint_reference_reconstruction_rejects_missing_malformed_and_mismatched_rows() -> None:
    async def exercise() -> None:
        missing = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        with pytest.raises(PaperBlueprintNotFoundError):
            await missing.get_blueprint_reference(CURRICULUM_VERSION_ID, PAPER_BLUEPRINT_ID)

        malformed = blueprint_model()
        malformed.blueprint = {"invalid": True}
        repository = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(malformed,)))
        )
        with pytest.raises(PaperPersistenceIntegrityError, match="snapshot"):
            await repository.get_blueprint_reference(CURRICULUM_VERSION_ID, PAPER_BLUEPRINT_ID)

        mutations: tuple[
            tuple[Callable[[PaperBlueprintModel], object], UUID],
            ...,
        ] = (
            (
                lambda model: setattr(
                    model,
                    "result_fingerprint",
                    "sha256:" + "0" * 64,
                ),
                CURRICULUM_VERSION_ID,
            ),
            (
                lambda model: setattr(
                    model,
                    "blueprint_id",
                    "bp_000000000000000000000000",
                ),
                CURRICULUM_VERSION_ID,
            ),
            (lambda _model: None, UUID(int=771_001)),
            (
                lambda model: setattr(model, "slot_count", model.slot_count + 1),
                CURRICULUM_VERSION_ID,
            ),
        )
        for mutate, curriculum_id in mutations:
            model = blueprint_model()
            mutate(model)
            repository = SqlAlchemyPaperPublicationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(model,)))
            )
            with pytest.raises(PaperPersistenceIntegrityError, match="identity"):
                await repository.get_blueprint_reference(curriculum_id, PAPER_BLUEPRINT_ID)

        model = blueprint_model()
        repository = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(model,)))
        )
        reference = await repository.get_blueprint_reference(
            CURRICULUM_VERSION_ID,
            PAPER_BLUEPRINT_ID,
        )
        assert reference.paper_blueprint_id == PAPER_BLUEPRINT_ID
        assert reference.slot_ids

    asyncio.run(exercise())


def test_candidate_loading_is_exact_ordered_and_fails_closed() -> None:
    async def exercise() -> None:
        assert MAX_PAPER_SELECTION_SOURCE_BYTES == 16 * 1024 * 1024
        repository = SqlAlchemyPaperPublicationRepository(cast(AsyncSession, ScriptedSession()))
        for candidate_ids in ((), (UUID(int=1), UUID(int=1))):
            with pytest.raises(PaperCandidateSelectionNotFoundError):
                await repository.load_candidates(CURRICULUM_ID, candidate_ids)

        domain = validated_candidate()
        candidate, revisions, events = candidate_models(domain)
        missing = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_rows=((),)))
        )
        with pytest.raises(PaperCandidateSelectionNotFoundError, match="curriculum"):
            await missing.load_candidates(CURRICULUM_ID, (domain.candidate_id,))

        valid_session = RecordingScriptedSession(
            scalar_results=(MAX_PAPER_SELECTION_SOURCE_BYTES,),
            scalar_rows=((candidate.id,), (candidate,), revisions, events),
        )
        loaded = await SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, valid_session)
        ).load_candidates(CURRICULUM_ID, (domain.candidate_id,))
        assert loaded == (domain,)
        assert len(valid_session.scalar_statements) == 1
        estimate_sql = str(valid_session.scalar_statements[0])
        assert "pg_column_size" in estimate_sql
        assert "octet_length" in estimate_sql
        assert "question_candidate_revisions" in estimate_sql
        assert "candidate_review_events" in estimate_sql

        approved = approved_candidate("slot-a")
        approved_model, approved_revisions, approved_events = candidate_models(approved)
        approved_session = ScriptedSession(
            scalar_results=(1,),
            scalar_rows=(
                (approved_model.id,),
                (approved_model,),
                approved_revisions,
                approved_events,
            ),
        )
        assert await SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, approved_session)
        ).load_candidates(CURRICULUM_ID, (approved.candidate_id,)) == (approved,)

        untouched_candidates = (object(),)
        untouched_revisions = (object(),)
        untouched_events = (object(),)
        oversized_session = ScriptedSession(
            scalar_results=(MAX_PAPER_SELECTION_SOURCE_BYTES + 1,),
            scalar_rows=(
                (candidate.id,),
                untouched_candidates,
                untouched_revisions,
                untouched_events,
            ),
        )
        with pytest.raises(PaperCandidateSelectionSourceLimitError) as raised:
            await SqlAlchemyPaperPublicationRepository(
                cast(AsyncSession, oversized_session)
            ).load_candidates(CURRICULUM_ID, (domain.candidate_id,))
        assert raised.value.estimated_bytes == MAX_PAPER_SELECTION_SOURCE_BYTES + 1
        assert raised.value.limit_bytes == MAX_PAPER_SELECTION_SOURCE_BYTES
        assert oversized_session.scalar_rows == [
            untouched_candidates,
            untouched_revisions,
            untouched_events,
        ]

        for invalid_estimate in (None, True, -1):
            invalid_estimate_session = ScriptedSession(
                scalar_results=(invalid_estimate,),
                scalar_rows=(
                    (candidate.id,),
                    untouched_candidates,
                    untouched_revisions,
                    untouched_events,
                ),
            )
            with pytest.raises(PaperPersistenceIntegrityError, match="estimate is invalid"):
                await SqlAlchemyPaperPublicationRepository(
                    cast(AsyncSession, invalid_estimate_session)
                ).load_candidates(CURRICULUM_ID, (domain.candidate_id,))
            assert invalid_estimate_session.scalar_rows == [
                untouched_candidates,
                untouched_revisions,
                untouched_events,
            ]

        changed_selection_session = ScriptedSession(
            scalar_results=(1,),
            scalar_rows=((candidate.id,), (), untouched_revisions, untouched_events),
        )
        with pytest.raises(PaperPersistenceIntegrityError, match="changed during"):
            await SqlAlchemyPaperPublicationRepository(
                cast(AsyncSession, changed_selection_session)
            ).load_candidates(CURRICULUM_ID, (domain.candidate_id,))
        assert changed_selection_session.scalar_rows == [
            untouched_revisions,
            untouched_events,
        ]

        corrupt_session = ScriptedSession(
            scalar_results=(1,),
            scalar_rows=((candidate.id,), (candidate,), (), events),
        )
        with pytest.raises(PaperPersistenceIntegrityError, match="reconstructed"):
            await SqlAlchemyPaperPublicationRepository(
                cast(AsyncSession, corrupt_session)
            ).load_candidates(CURRICULUM_ID, (domain.candidate_id,))

    asyncio.run(exercise())


def test_repository_insert_append_cas_and_lookup_helpers() -> None:
    async def exercise() -> None:
        draft = persisted_draft()
        no_blueprint_id = replace(draft.blueprint, paper_blueprint_id=None)
        repository = SqlAlchemyPaperPublicationRepository(cast(AsyncSession, ScriptedSession()))
        with pytest.raises(PaperPersistenceIntegrityError, match="identifier"):
            await repository.insert_initial(
                paper_id=draft.paper_id,
                curriculum_version_id=CURRICULUM_ID,
                blueprint=no_blueprint_id,
                draft=draft,
                idempotency_key_hash="sha256:" + "1" * 64,
                request_fingerprint="sha256:" + "2" * 64,
                actor_id=ACTOR_ID,
            )

        duplicate = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        assert not await duplicate.insert_initial(
            paper_id=draft.paper_id,
            curriculum_version_id=CURRICULUM_ID,
            blueprint=draft.blueprint,
            draft=draft,
            idempotency_key_hash="sha256:" + "1" * 64,
            request_fingerprint="sha256:" + "2" * 64,
            actor_id=ACTOR_ID,
        )

        inserted_session = ScriptedSession(scalar_results=(draft.paper_id,))
        inserted = SqlAlchemyPaperPublicationRepository(cast(AsyncSession, inserted_session))
        assert await inserted.insert_initial(
            paper_id=draft.paper_id,
            curriculum_version_id=CURRICULUM_ID,
            blueprint=draft.blueprint,
            draft=draft,
            idempotency_key_hash="sha256:" + "1" * 64,
            request_fingerprint="sha256:" + "2" * 64,
            actor_id=ACTOR_ID,
        )
        assert any(isinstance(item, PaperDraftVersionModel) for item in inserted_session.added)
        assert (
            sum(isinstance(item, PaperDraftCandidateModel) for item in inserted_session.added) == 2
        )

        publication = publication_record()
        inserted.add_publication(
            curriculum_version_id=CURRICULUM_ID,
            publication=publication.domain,
        )
        inserted.add_archive(
            curriculum_version_id=CURRICULUM_ID,
            paper_id=draft.paper_id,
            version=1,
            reason="Retired.",
            actor_id=ACTOR_ID,
        )
        assert isinstance(inserted_session.added[-2], PublishedPaperVersionModel)
        assert isinstance(inserted_session.added[-1], PaperArchiveEventModel)

        cas_session = ScriptedSession(scalar_results=(draft.paper_id, None))
        cas_repository = SqlAlchemyPaperPublicationRepository(cast(AsyncSession, cas_session))
        assert await cas_repository.cas_transition(
            curriculum_version_id=CURRICULUM_ID,
            paper_id=draft.paper_id,
            expected_state=PaperState.DRAFT,
            expected_version=1,
            state=PaperState.PUBLISHED,
            version=1,
            actor_id=ACTOR_ID,
        )
        assert not await cas_repository.cas_transition(
            curriculum_version_id=CURRICULUM_ID,
            paper_id=draft.paper_id,
            expected_state=PaperState.DRAFT,
            expected_version=1,
            state=PaperState.PUBLISHED,
            version=1,
            actor_id=ACTOR_ID,
        )

        paper = paper_model()
        lookup = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(paper, paper, None)))
        )
        assert await lookup.find_by_idempotency_hash(paper.idempotency_key_hash) is paper
        assert await lookup.get_paper(CURRICULUM_ID, PAPER_ID, for_update=True) is paper
        with pytest.raises(PaperNotFoundError):
            await lookup.get_paper(CURRICULUM_ID, UUID(int=9))

    asyncio.run(exercise())


def test_draft_reconstruction_is_strict_for_missing_selection_columns_and_versions() -> None:
    async def exercise() -> None:
        aggregate = paper_model()
        draft = persisted_draft()
        draft_model, selections = draft_models(draft)

        missing = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        with pytest.raises(PaperDraftNotFoundError):
            await missing.get_draft(CURRICULUM_ID, PAPER_ID, 1, paper=aggregate)

        async def reconstruct(
            candidate_selections: tuple[PaperDraftCandidateModel, ...],
            candidates: tuple[QuestionCandidate, ...] = draft.candidates,
            *,
            model: PaperDraftVersionModel = draft_model,
        ) -> StoredPaperDraft:
            repository = SqlAlchemyPaperPublicationRepository(
                cast(
                    AsyncSession,
                    ScriptedSession(
                        scalar_results=(model,),
                        scalar_rows=(candidate_selections,),
                    ),
                )
            )
            object.__setattr__(
                repository,
                "get_blueprint_reference",
                AsyncMock(return_value=draft.blueprint),
            )
            object.__setattr__(
                repository,
                "load_candidates",
                AsyncMock(return_value=candidates),
            )
            return await repository.get_draft(
                CURRICULUM_ID,
                PAPER_ID,
                model.version,
                paper=aggregate,
            )

        stored = await reconstruct(selections)
        assert stored.domain == draft

        for field_name, value in (
            ("ordinal", 9),
            ("blueprint_slot_id", "wrong-slot"),
            ("candidate_version", 2),
            ("candidate_revision", 2),
        ):
            altered = tuple(selections)
            original = getattr(altered[0], field_name)
            setattr(altered[0], field_name, value)
            with pytest.raises(PaperPersistenceIntegrityError, match="columns"):
                await reconstruct(altered)
            setattr(altered[0], field_name, original)

        repository = SqlAlchemyPaperPublicationRepository(
            cast(
                AsyncSession,
                ScriptedSession(scalar_results=(draft_model,), scalar_rows=(selections,)),
            )
        )
        object.__setattr__(
            repository,
            "get_blueprint_reference",
            AsyncMock(side_effect=ValueError("invalid")),
        )
        with pytest.raises(PaperPersistenceIntegrityError, match="cannot be reconstructed"):
            await repository.get_draft(CURRICULUM_ID, PAPER_ID, 1, paper=aggregate)

        published = publication_record().domain
        draft_v2 = PaperWorkflowService().revise(
            REVIEWER,
            RevisePaperCommand(
                source=published,
                candidates=draft.candidates,
                expected_version=1,
            ),
        )
        model_v2, selections_v2 = draft_models(draft_v2)
        stored_v2 = await reconstruct(selections_v2, draft_v2.candidates, model=model_v2)
        assert stored_v2.domain.previous_version == 1

    asyncio.run(exercise())


def test_publication_archive_and_list_reads_verify_integrity_without_list_snapshots() -> None:
    async def exercise() -> None:
        aggregate = paper_model(state=PaperState.PUBLISHED)
        publication = publication_record()

        missing = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        with pytest.raises(PaperPublicationNotFoundError):
            await missing.get_publication(CURRICULUM_ID, PAPER_ID, 1, paper=aggregate)

        invalid_model = publication.publication
        invalid_model.content_hash = "0" * 64
        invalid = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(invalid_model,)))
        )
        with pytest.raises(PaperPersistenceIntegrityError, match="snapshot integrity"):
            await invalid.get_publication(CURRICULUM_ID, PAPER_ID, 1, paper=aggregate)
        invalid_model.content_hash = publication.domain.content_hash

        valid = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(invalid_model,)))
        )
        stored = await valid.get_publication(CURRICULUM_ID, PAPER_ID, 1, paper=aggregate)
        assert stored.domain == publication.domain

        invalid_model.paper_id = UUID(int=17)
        mismatched = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(invalid_model,)))
        )
        with pytest.raises(PaperPersistenceIntegrityError, match="columns"):
            await mismatched.get_publication(CURRICULUM_ID, PAPER_ID, 1, paper=aggregate)
        invalid_model.paper_id = PAPER_ID

        archive_missing = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        with pytest.raises(PaperArchiveNotFoundError):
            await archive_missing.get_archive(CURRICULUM_ID, PAPER_ID, paper=aggregate)

        archive = PaperArchiveEventModel(
            paper_id=PAPER_ID,
            curriculum_version_id=CURRICULUM_ID,
            version=1,
            reason="Retired.",
            archived_by=ACTOR_ID,
            archived_at=NOW,
        )
        archive_repository = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(archive,)))
        )
        object.__setattr__(
            archive_repository,
            "get_publication",
            AsyncMock(return_value=publication),
        )
        stored_archive = await archive_repository.get_archive(
            CURRICULUM_ID,
            PAPER_ID,
            paper=aggregate,
        )
        assert stored_archive.archive is archive

        summary_session = ScriptedSession(
            execute_results=(ExecuteResult(rows=((aggregate, "Paper", "a" * 64),)),)
        )
        summary_repository = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, summary_session)
        )
        summaries = await summary_repository.list_papers(
            CURRICULUM_ID,
            state=PaperState.PUBLISHED,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            limit=10,
            offset=0,
        )
        assert summaries[0].latest_publication_hash == "a" * 64

        unfiltered = SqlAlchemyPaperPublicationRepository(
            cast(
                AsyncSession,
                ScriptedSession(execute_results=(ExecuteResult(rows=()),)),
            )
        )
        assert (
            await unfiltered.list_papers(
                CURRICULUM_ID,
                state=None,
                paper_blueprint_id=None,
                limit=10,
                offset=0,
            )
            == ()
        )

        list_repository = SqlAlchemyPaperPublicationRepository(
            cast(AsyncSession, ScriptedSession(scalar_rows=((2, 1), (invalid_model,))))
        )
        object.__setattr__(list_repository, "get_paper", AsyncMock(return_value=aggregate))
        get_draft = AsyncMock(side_effect=("draft-2", "draft-1"))
        object.__setattr__(list_repository, "get_draft", get_draft)
        listed_drafts = await list_repository.list_drafts(
            CURRICULUM_ID,
            PAPER_ID,
            limit=2,
            offset=0,
        )
        assert cast(tuple[object, ...], listed_drafts) == ("draft-2", "draft-1")
        publications = await list_repository.list_publications(
            CURRICULUM_ID,
            PAPER_ID,
            limit=10,
            offset=0,
        )
        assert publications[0].content_hash == invalid_model.content_hash

    asyncio.run(exercise())
