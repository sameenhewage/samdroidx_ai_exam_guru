from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, Text, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.blueprints.serialization import (
    BlueprintSnapshotError,
    deserialize_blueprint,
    fingerprint_snapshot,
)
from exam_guru_api.papers.domain import (
    PaperBlueprintReference,
    PaperDraft,
    PaperState,
    PublishedPaperSnapshot,
    QuestionCandidate,
)
from exam_guru_api.papers.models import (
    CandidateReviewEventModel,
    QuestionCandidateModel,
    QuestionCandidateRevisionModel,
)
from exam_guru_api.papers.publication_models import (
    PaperArchiveEventModel,
    PaperDraftCandidateModel,
    PaperDraftVersionModel,
    PracticePaperModel,
    PublishedPaperVersionModel,
)
from exam_guru_api.papers.repository import (
    CandidatePersistenceIntegrityError,
    _domain_candidate,
)
from exam_guru_api.papers.serialization import (
    PaperSnapshotIntegrityError,
    reconstruct_published_snapshot,
    serialize_published_snapshot,
)

MAX_PAPER_SELECTION_SOURCE_BYTES = 16 * 1024 * 1024
_SELECTION_SOURCE_SIZE_MULTIPLIER = 2
_SELECTION_CANDIDATE_OVERHEAD_BYTES = 512
_SELECTION_REVISION_OVERHEAD_BYTES = 512
_SELECTION_REVIEW_EVENT_OVERHEAD_BYTES = 256


class PaperNotFoundError(LookupError):
    pass


class PaperDraftNotFoundError(LookupError):
    pass


class PaperPublicationNotFoundError(LookupError):
    pass


class PaperArchiveNotFoundError(LookupError):
    pass


class PaperBlueprintNotFoundError(LookupError):
    pass


class PaperCandidateSelectionNotFoundError(LookupError):
    pass


class PaperCandidateSelectionSourceLimitError(RuntimeError):
    def __init__(self, estimated_bytes: int, limit_bytes: int) -> None:
        self.estimated_bytes = estimated_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"paper candidate selection source estimate {estimated_bytes} exceeds "
            f"{limit_bytes} bytes"
        )


class PaperPersistenceIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredPaperDraft:
    paper: PracticePaperModel
    draft: PaperDraftVersionModel
    selections: tuple[PaperDraftCandidateModel, ...]
    domain: PaperDraft


@dataclass(frozen=True, slots=True)
class StoredPublication:
    publication: PublishedPaperVersionModel
    domain: PublishedPaperSnapshot


@dataclass(frozen=True, slots=True)
class StoredPaperArchive:
    archive: PaperArchiveEventModel
    publication: StoredPublication


@dataclass(frozen=True, slots=True)
class PaperSummary:
    id: UUID
    curriculum_version_id: UUID
    paper_blueprint_id: UUID
    blueprint_id: str
    blueprint_version: str
    state: str
    current_version: int
    created_by: UUID
    created_at: datetime
    updated_by: UUID
    updated_at: datetime
    title: str
    latest_publication_hash: str | None


@dataclass(frozen=True, slots=True)
class PublicationVersionSummary:
    paper_id: UUID
    curriculum_version_id: UUID
    version: int
    content_hash: str
    published_by: UUID
    published_at: datetime


class SqlAlchemyPaperPublicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_blueprint_reference(
        self,
        curriculum_version_id: UUID,
        paper_blueprint_id: UUID,
    ) -> PaperBlueprintReference:
        model = await self._session.scalar(
            select(PaperBlueprintModel).where(
                PaperBlueprintModel.id == paper_blueprint_id,
                PaperBlueprintModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if model is None:
            raise PaperBlueprintNotFoundError(paper_blueprint_id)
        try:
            blueprint = deserialize_blueprint(model.blueprint)
        except BlueprintSnapshotError as error:
            raise PaperPersistenceIntegrityError("paper blueprint snapshot is invalid") from error
        if (
            fingerprint_snapshot(model.blueprint) != model.result_fingerprint
            or blueprint.version.blueprint_id != model.blueprint_id
            or blueprint.curriculum_scope.curriculum_version_id != curriculum_version_id
            or len(blueprint.slots) != model.slot_count
            or model.slot_count > 200
        ):
            raise PaperPersistenceIntegrityError("paper blueprint identity is inconsistent")
        return PaperBlueprintReference(
            blueprint_id=model.blueprint_id,
            blueprint_version=blueprint.version.blueprint_id,
            slot_ids=tuple(slot.slot_id for slot in blueprint.slots),
            paper_blueprint_id=model.id,
        )

    async def load_candidates(
        self,
        curriculum_version_id: UUID,
        candidate_ids: tuple[UUID, ...],
    ) -> tuple[QuestionCandidate, ...]:
        if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
            raise PaperCandidateSelectionNotFoundError("candidate selection is empty or duplicated")
        existing_ids = tuple(
            await self._session.scalars(
                select(QuestionCandidateModel.id).where(
                    QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                    QuestionCandidateModel.id.in_(candidate_ids),
                )
            )
        )
        if set(existing_ids) != set(candidate_ids):
            raise PaperCandidateSelectionNotFoundError(
                "candidate selection is not curriculum scoped"
            )

        candidate_source_bytes = (
            select(
                func.coalesce(
                    func.sum(
                        func.greatest(
                            func.pg_column_size(QuestionCandidateModel.generation_lineage),
                            func.octet_length(QuestionCandidateModel.generation_lineage.cast(Text)),
                        )
                        + func.greatest(
                            func.pg_column_size(QuestionCandidateModel.validation_evidence),
                            func.octet_length(
                                QuestionCandidateModel.validation_evidence.cast(Text)
                            ),
                        )
                        + _SELECTION_CANDIDATE_OVERHEAD_BYTES
                    ),
                    0,
                )
            )
            .where(
                QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                QuestionCandidateModel.id.in_(candidate_ids),
            )
            .scalar_subquery()
        )
        revision_source_bytes = (
            select(
                func.coalesce(
                    func.sum(
                        func.greatest(
                            func.pg_column_size(QuestionCandidateRevisionModel.content),
                            func.octet_length(QuestionCandidateRevisionModel.content.cast(Text)),
                        )
                        + func.coalesce(
                            func.greatest(
                                func.pg_column_size(QuestionCandidateRevisionModel.reason),
                                func.octet_length(QuestionCandidateRevisionModel.reason),
                            ),
                            0,
                        )
                        + _SELECTION_REVISION_OVERHEAD_BYTES
                    ),
                    0,
                )
            )
            .where(QuestionCandidateRevisionModel.candidate_id.in_(candidate_ids))
            .scalar_subquery()
        )
        event_source_bytes = (
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            func.greatest(
                                func.pg_column_size(CandidateReviewEventModel.reason),
                                func.octet_length(CandidateReviewEventModel.reason),
                            ),
                            0,
                        )
                        + _SELECTION_REVIEW_EVENT_OVERHEAD_BYTES
                    ),
                    0,
                )
            )
            .where(CandidateReviewEventModel.candidate_id.in_(candidate_ids))
            .scalar_subquery()
        )
        estimated_source_bytes = await self._session.scalar(
            select(
                (candidate_source_bytes + revision_source_bytes + event_source_bytes)
                * _SELECTION_SOURCE_SIZE_MULTIPLIER
            )
        )
        if (
            not isinstance(estimated_source_bytes, int)
            or isinstance(estimated_source_bytes, bool)
            or estimated_source_bytes < 0
        ):
            raise PaperPersistenceIntegrityError(
                "paper candidate selection source estimate is invalid"
            )
        if estimated_source_bytes > MAX_PAPER_SELECTION_SOURCE_BYTES:
            raise PaperCandidateSelectionSourceLimitError(
                estimated_source_bytes,
                MAX_PAPER_SELECTION_SOURCE_BYTES,
            )

        models = tuple(
            await self._session.scalars(
                select(QuestionCandidateModel).where(
                    QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                    QuestionCandidateModel.id.in_(candidate_ids),
                )
            )
        )
        by_id = {model.id: model for model in models}
        if set(by_id) != set(candidate_ids):
            raise PaperPersistenceIntegrityError(
                "paper candidate selection changed during source preflight"
            )

        revisions = tuple(
            await self._session.scalars(
                select(QuestionCandidateRevisionModel)
                .where(QuestionCandidateRevisionModel.candidate_id.in_(candidate_ids))
                .order_by(
                    QuestionCandidateRevisionModel.candidate_id,
                    QuestionCandidateRevisionModel.revision,
                )
            )
        )
        events = tuple(
            await self._session.scalars(
                select(CandidateReviewEventModel)
                .where(CandidateReviewEventModel.candidate_id.in_(candidate_ids))
                .order_by(
                    CandidateReviewEventModel.candidate_id,
                    CandidateReviewEventModel.candidate_version,
                )
            )
        )
        revisions_by_id: dict[UUID, list[QuestionCandidateRevisionModel]] = defaultdict(list)
        events_by_id: dict[UUID, list[CandidateReviewEventModel]] = defaultdict(list)
        for revision in revisions:
            revisions_by_id[revision.candidate_id].append(revision)
        for event in events:
            events_by_id[event.candidate_id].append(event)
        try:
            return tuple(
                _domain_candidate(
                    by_id[candidate_id],
                    tuple(revisions_by_id[candidate_id]),
                    tuple(events_by_id[candidate_id]),
                )
                for candidate_id in candidate_ids
            )
        except (CandidatePersistenceIntegrityError, KeyError, TypeError, ValueError) as error:
            raise PaperPersistenceIntegrityError(
                "paper candidate selection cannot be reconstructed"
            ) from error

    async def find_by_idempotency_hash(
        self,
        idempotency_key_hash: str,
    ) -> PracticePaperModel | None:
        model: PracticePaperModel | None = await self._session.scalar(
            select(PracticePaperModel).where(
                PracticePaperModel.idempotency_key_hash == idempotency_key_hash
            )
        )
        return model

    async def insert_initial(
        self,
        *,
        paper_id: UUID,
        curriculum_version_id: UUID,
        blueprint: PaperBlueprintReference,
        draft: PaperDraft,
        idempotency_key_hash: str,
        request_fingerprint: str,
        actor_id: UUID,
    ) -> bool:
        if blueprint.paper_blueprint_id is None:
            raise PaperPersistenceIntegrityError("persisted blueprint identifier is required")
        inserted_id = await self._session.scalar(
            insert(PracticePaperModel)
            .values(
                id=paper_id,
                curriculum_version_id=curriculum_version_id,
                paper_blueprint_id=blueprint.paper_blueprint_id,
                blueprint_id=blueprint.blueprint_id,
                blueprint_version=blueprint.blueprint_version,
                state=PaperState.DRAFT.value,
                current_version=1,
                idempotency_key_hash=idempotency_key_hash,
                create_request_fingerprint=request_fingerprint,
                created_by=actor_id,
                updated_by=actor_id,
            )
            .on_conflict_do_nothing()
            .returning(PracticePaperModel.id)
        )
        if inserted_id is None:
            return False
        await self.add_draft(
            curriculum_version_id=curriculum_version_id,
            draft=draft,
            actor_id=actor_id,
        )
        await self._session.flush()
        return True

    async def add_draft(
        self,
        *,
        curriculum_version_id: UUID,
        draft: PaperDraft,
        actor_id: UUID,
    ) -> None:
        self._session.add(
            PaperDraftVersionModel(
                paper_id=draft.paper_id,
                curriculum_version_id=curriculum_version_id,
                version=draft.version,
                title=draft.title,
                supersedes_content_hash=draft.supersedes_content_hash,
                created_by=actor_id,
            )
        )
        await self._session.flush()
        for ordinal, candidate in enumerate(draft.candidates, start=1):
            self._session.add(
                PaperDraftCandidateModel(
                    paper_id=draft.paper_id,
                    curriculum_version_id=curriculum_version_id,
                    paper_version=draft.version,
                    ordinal=ordinal,
                    blueprint_slot_id=candidate.lineage.blueprint_slot_id,
                    candidate_id=candidate.candidate_id,
                    candidate_version=candidate.version,
                    candidate_revision=candidate.revisions[-1].revision,
                )
            )

    def add_publication(
        self,
        *,
        curriculum_version_id: UUID,
        publication: PublishedPaperSnapshot,
    ) -> None:
        self._session.add(
            PublishedPaperVersionModel(
                paper_id=publication.paper_id,
                curriculum_version_id=curriculum_version_id,
                version=publication.version,
                previous_version=publication.previous_version,
                supersedes_content_hash=publication.supersedes_content_hash,
                snapshot=serialize_published_snapshot(publication),
                content_hash=publication.content_hash,
                published_by=publication.published_by,
            )
        )

    def add_archive(
        self,
        *,
        curriculum_version_id: UUID,
        paper_id: UUID,
        version: int,
        reason: str,
        actor_id: UUID,
    ) -> None:
        self._session.add(
            PaperArchiveEventModel(
                paper_id=paper_id,
                curriculum_version_id=curriculum_version_id,
                version=version,
                reason=reason,
                archived_by=actor_id,
            )
        )

    async def cas_transition(
        self,
        *,
        curriculum_version_id: UUID,
        paper_id: UUID,
        expected_state: PaperState,
        expected_version: int,
        state: PaperState,
        version: int,
        actor_id: UUID,
    ) -> bool:
        updated_id = await self._session.scalar(
            update(PracticePaperModel)
            .where(
                PracticePaperModel.id == paper_id,
                PracticePaperModel.curriculum_version_id == curriculum_version_id,
                PracticePaperModel.state == expected_state.value,
                PracticePaperModel.current_version == expected_version,
            )
            .values(
                state=state.value,
                current_version=version,
                updated_by=actor_id,
                updated_at=func.now(),
            )
            .returning(PracticePaperModel.id)
        )
        return updated_id is not None

    async def get_paper(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        for_update: bool = False,
    ) -> PracticePaperModel:
        statement: Select[tuple[PracticePaperModel]] = select(PracticePaperModel).where(
            PracticePaperModel.id == paper_id,
            PracticePaperModel.curriculum_version_id == curriculum_version_id,
        )
        if for_update:
            statement = statement.with_for_update()
        model = await self._session.scalar(statement.execution_options(populate_existing=True))
        if model is None:
            raise PaperNotFoundError(paper_id)
        return model

    async def get_draft(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        version: int,
        *,
        paper: PracticePaperModel | None = None,
    ) -> StoredPaperDraft:
        aggregate = paper or await self.get_paper(curriculum_version_id, paper_id)
        draft = await self._session.scalar(
            select(PaperDraftVersionModel).where(
                PaperDraftVersionModel.paper_id == paper_id,
                PaperDraftVersionModel.curriculum_version_id == curriculum_version_id,
                PaperDraftVersionModel.version == version,
            )
        )
        if draft is None:
            raise PaperDraftNotFoundError((paper_id, version))
        selections = tuple(
            await self._session.scalars(
                select(PaperDraftCandidateModel)
                .where(
                    PaperDraftCandidateModel.paper_id == paper_id,
                    PaperDraftCandidateModel.curriculum_version_id == curriculum_version_id,
                    PaperDraftCandidateModel.paper_version == version,
                )
                .order_by(PaperDraftCandidateModel.ordinal)
            )
        )
        try:
            blueprint = await self.get_blueprint_reference(
                curriculum_version_id,
                aggregate.paper_blueprint_id,
            )
            candidates = await self.load_candidates(
                curriculum_version_id,
                tuple(item.candidate_id for item in selections),
            )
            if any(
                item.ordinal != ordinal
                or item.blueprint_slot_id != candidate.lineage.blueprint_slot_id
                or item.candidate_version != candidate.version
                or item.candidate_revision != candidate.revisions[-1].revision
                for ordinal, (item, candidate) in enumerate(
                    zip(selections, candidates, strict=True),
                    start=1,
                )
            ):
                raise PaperPersistenceIntegrityError(
                    "paper draft selection columns are inconsistent"
                )
            domain = PaperDraft(
                paper_id=paper_id,
                version=version,
                title=draft.title,
                blueprint=blueprint,
                candidates=candidates,
                previous_version=None if version == 1 else version - 1,
                supersedes_content_hash=draft.supersedes_content_hash,
            )
        except (ValueError, TypeError) as error:
            raise PaperPersistenceIntegrityError("paper draft cannot be reconstructed") from error
        return StoredPaperDraft(aggregate, draft, selections, domain)

    async def get_publication(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        version: int,
        *,
        paper: PracticePaperModel | None = None,
    ) -> StoredPublication:
        aggregate = paper or await self.get_paper(curriculum_version_id, paper_id)
        model = await self._session.scalar(
            select(PublishedPaperVersionModel).where(
                PublishedPaperVersionModel.paper_id == paper_id,
                PublishedPaperVersionModel.curriculum_version_id == curriculum_version_id,
                PublishedPaperVersionModel.version == version,
            )
        )
        if model is None:
            raise PaperPublicationNotFoundError((paper_id, version))
        try:
            domain = reconstruct_published_snapshot(
                model.snapshot,
                content_hash=model.content_hash,
                published_by=model.published_by,
                previous_version=model.previous_version,
                supersedes_content_hash=model.supersedes_content_hash,
            )
        except PaperSnapshotIntegrityError as error:
            raise PaperPersistenceIntegrityError("publication snapshot integrity failed") from error
        if (
            domain.paper_id != model.paper_id
            or domain.version != model.version
            or domain.blueprint.paper_blueprint_id != aggregate.paper_blueprint_id
            or domain.blueprint.blueprint_id != aggregate.blueprint_id
            or domain.blueprint.blueprint_version != aggregate.blueprint_version
        ):
            raise PaperPersistenceIntegrityError(
                "publication columns and snapshot are inconsistent"
            )
        return StoredPublication(model, domain)

    async def get_archive(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        paper: PracticePaperModel | None = None,
    ) -> StoredPaperArchive:
        aggregate = paper or await self.get_paper(curriculum_version_id, paper_id)
        archive = await self._session.scalar(
            select(PaperArchiveEventModel).where(
                PaperArchiveEventModel.paper_id == paper_id,
                PaperArchiveEventModel.curriculum_version_id == curriculum_version_id,
            )
        )
        if archive is None:
            raise PaperArchiveNotFoundError(paper_id)
        publication = await self.get_publication(
            curriculum_version_id,
            paper_id,
            archive.version,
            paper=aggregate,
        )
        return StoredPaperArchive(archive, publication)

    async def list_papers(
        self,
        curriculum_version_id: UUID,
        *,
        state: PaperState | None,
        paper_blueprint_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[PaperSummary, ...]:
        latest_draft = PaperDraftVersionModel
        latest_publication = PublishedPaperVersionModel
        statement = (
            select(
                PracticePaperModel,
                latest_draft.title,
                latest_publication.content_hash,
            )
            .join(
                latest_draft,
                (latest_draft.paper_id == PracticePaperModel.id)
                & (latest_draft.version == PracticePaperModel.current_version),
            )
            .outerjoin(
                latest_publication,
                (latest_publication.paper_id == PracticePaperModel.id)
                & (latest_publication.version == PracticePaperModel.current_version),
            )
            .where(PracticePaperModel.curriculum_version_id == curriculum_version_id)
            .order_by(PracticePaperModel.updated_at.desc(), PracticePaperModel.id.desc())
            .offset(offset)
            .limit(limit)
        )
        if state is not None:
            statement = statement.where(PracticePaperModel.state == state.value)
        if paper_blueprint_id is not None:
            statement = statement.where(PracticePaperModel.paper_blueprint_id == paper_blueprint_id)
        rows = (await self._session.execute(statement)).all()
        return tuple(
            PaperSummary(
                id=paper.id,
                curriculum_version_id=paper.curriculum_version_id,
                paper_blueprint_id=paper.paper_blueprint_id,
                blueprint_id=paper.blueprint_id,
                blueprint_version=paper.blueprint_version,
                state=paper.state,
                current_version=paper.current_version,
                created_by=paper.created_by,
                created_at=paper.created_at,
                updated_by=paper.updated_by,
                updated_at=paper.updated_at,
                title=title,
                latest_publication_hash=content_hash,
            )
            for paper, title, content_hash in rows
        )

    async def list_drafts(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[StoredPaperDraft, ...]:
        paper = await self.get_paper(curriculum_version_id, paper_id)
        versions = tuple(
            await self._session.scalars(
                select(PaperDraftVersionModel.version)
                .where(
                    PaperDraftVersionModel.paper_id == paper_id,
                    PaperDraftVersionModel.curriculum_version_id == curriculum_version_id,
                )
                .order_by(PaperDraftVersionModel.version.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return tuple(
            [
                await self.get_draft(
                    curriculum_version_id,
                    paper_id,
                    version,
                    paper=paper,
                )
                for version in versions
            ]
        )

    async def list_publications(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[PublicationVersionSummary, ...]:
        await self.get_paper(curriculum_version_id, paper_id)
        models = tuple(
            await self._session.scalars(
                select(PublishedPaperVersionModel)
                .where(
                    PublishedPaperVersionModel.paper_id == paper_id,
                    PublishedPaperVersionModel.curriculum_version_id == curriculum_version_id,
                )
                .order_by(PublishedPaperVersionModel.version.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return tuple(
            PublicationVersionSummary(
                paper_id=model.paper_id,
                curriculum_version_id=model.curriculum_version_id,
                version=model.version,
                content_hash=model.content_hash,
                published_by=model.published_by,
                published_at=model.published_at,
            )
            for model in models
        )
