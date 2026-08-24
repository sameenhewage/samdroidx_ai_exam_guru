import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Principal
from exam_guru_api.papers.domain import (
    AssemblyViolation,
    ConcurrentVersionError,
    InvalidPaperTransitionError,
    PaperAssemblyError,
    PaperDraft,
    PaperState,
)
from exam_guru_api.papers.publication_models import (
    PaperArchiveEventModel,
    PracticePaperModel,
)
from exam_guru_api.papers.publication_repository import (
    PaperCandidateSelectionNotFoundError,
    PaperCandidateSelectionSourceLimitError,
    PaperPersistenceIntegrityError,
    SqlAlchemyPaperPublicationRepository,
    StoredPaperArchive,
    StoredPaperDraft,
    StoredPublication,
)
from exam_guru_api.papers.publication_service import (
    PaperCandidateSelectionError,
    PaperCandidateSelectionResourceLimitError,
    PaperCommandInvalidError,
    PaperIdempotencyConflictError,
    PaperIntegrityError,
    PaperPublicationService,
    PaperStateConflictError,
    PaperVersionConflictError,
    _paper_failure_code,
)
from tests.test_operational_telemetry import telemetry
from tests.test_paper_domain import approved_candidate, assembled_draft, validated_candidate

CURRICULUM_ID = UUID(int=997_001)
PAPER_BLUEPRINT_ID = UUID(int=997_002)
ACTOR_ID = UUID(int=997_003)
OTHER_ACTOR_ID = UUID(int=997_004)
NOW = datetime(2026, 8, 24, tzinfo=UTC)
REVIEWER = Principal(ACTOR_ID, frozenset({AdminRole.REVIEWER}))
ADMIN = Principal(ACTOR_ID, frozenset({AdminRole.ADMIN}))


class FakeSession:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.commit_error = commit_error

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1


@dataclass
class DraftRecord:
    domain: object


class FakeRepository:
    def __init__(self) -> None:
        self.paper = PracticePaperModel(
            id=assembled_draft().paper_id,
            curriculum_version_id=CURRICULUM_ID,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            blueprint_id="grade5-paper-blueprint",
            blueprint_version="blueprint-v4",
            state="draft",
            current_version=1,
            idempotency_key_hash="sha256:" + "1" * 64,
            create_request_fingerprint="sha256:" + "2" * 64,
            created_by=ACTOR_ID,
            created_at=NOW,
            updated_by=ACTOR_ID,
            updated_at=NOW,
        )
        self.existing: PracticePaperModel | None = None
        self.last_initial: dict[str, object] = {}
        self.last_transition: dict[str, object] = {}
        self.created = True
        self.cas_result = True
        self.draft = cast(StoredPaperDraft, DraftRecord(assembled_draft()))
        self.publication: StoredPublication | None = None
        self.archive: StoredPaperArchive | None = None
        self.candidates = (
            approved_candidate("slot-b", candidate_number=2),
            approved_candidate("slot-a", candidate_number=1),
        )
        self.added_drafts: list[object] = []
        self.added_publications: list[object] = []
        self.added_archives: list[object] = []

    async def get_blueprint_reference(self, *_args: object) -> object:
        return assembled_draft().blueprint

    async def load_candidates(self, *_args: object) -> tuple[object, ...]:
        return self.candidates

    async def find_by_idempotency_hash(self, *_args: object) -> PracticePaperModel | None:
        return self.existing

    async def insert_initial(self, **values: object) -> bool:
        self.last_initial = values
        self.draft = cast(StoredPaperDraft, DraftRecord(values["draft"]))
        self.paper.id = cast(UUID, values["paper_id"])
        return self.created

    async def get_paper(self, *_args: object, **_kwargs: object) -> PracticePaperModel:
        return self.paper

    async def get_draft(self, *_args: object, **_kwargs: object) -> StoredPaperDraft:
        return self.draft

    async def get_publication(self, *_args: object, **_kwargs: object) -> object:
        assert self.publication is not None
        return self.publication

    async def get_archive(self, *_args: object, **_kwargs: object) -> StoredPaperArchive:
        assert self.archive is not None
        return self.archive

    async def list_papers(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (self.paper,)

    async def list_drafts(self, *_args: object, **_kwargs: object) -> tuple[StoredPaperDraft, ...]:
        return (self.draft,)

    async def list_publications(self, *_args: object, **_kwargs: object) -> tuple[object, ...]:
        assert self.publication is not None
        return (self.publication,)

    async def cas_transition(self, **values: object) -> bool:
        self.last_transition = values
        return self.cas_result

    async def add_draft(self, **values: object) -> None:
        self.added_drafts.append(values)

    def add_publication(self, **values: object) -> None:
        from exam_guru_api.papers import PublishedPaperSnapshot
        from exam_guru_api.papers.publication_models import PublishedPaperVersionModel
        from exam_guru_api.papers.publication_repository import StoredPublication
        from exam_guru_api.papers.serialization import serialize_published_snapshot

        self.added_publications.append(values)
        domain = values["publication"]
        assert isinstance(domain, PublishedPaperSnapshot)
        model = PublishedPaperVersionModel(
            paper_id=domain.paper_id,
            curriculum_version_id=CURRICULUM_ID,
            version=domain.version,
            previous_version=domain.previous_version,
            supersedes_content_hash=domain.supersedes_content_hash,
            snapshot=serialize_published_snapshot(domain),
            content_hash=domain.content_hash,
            published_by=domain.published_by,
            published_at=NOW,
        )
        self.publication = StoredPublication(model, domain)

    def add_archive(self, **values: object) -> None:
        self.added_archives.append(values)
        assert self.publication is not None
        model = PaperArchiveEventModel(
            paper_id=cast(UUID, values["paper_id"]),
            curriculum_version_id=cast(UUID, values["curriculum_version_id"]),
            version=cast(int, values["version"]),
            reason=cast(str, values["reason"]),
            archived_by=cast(UUID, values["actor_id"]),
            archived_at=NOW,
        )
        self.archive = StoredPaperArchive(model, self.publication)


def service_with(
    *, commit_error: Exception | None = None
) -> tuple[PaperPublicationService, FakeSession, FakeRepository]:
    session = FakeSession(commit_error=commit_error)
    service = PaperPublicationService(cast(AsyncSession, session))
    repository = FakeRepository()
    service._repository = cast(SqlAlchemyPaperPublicationRepository, repository)
    return service, session, repository


def make_published(repository: FakeRepository) -> StoredPublication:
    repository.paper.state = PaperState.PUBLISHED.value
    publication = PaperWorkflowPublication.fixture(repository.draft.domain, ADMIN)
    repository.publication = publication
    return publication


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PaperIdempotencyConflictError(), "paper_idempotency_conflict"),
        (PaperVersionConflictError(), "paper_version_conflict"),
        (PaperStateConflictError(), "paper_state_conflict"),
        (PaperCandidateSelectionResourceLimitError(2, 1), "paper_resource_limit"),
        (PaperCandidateSelectionError(), "paper_candidate_selection_invalid"),
        (PaperIntegrityError(), "paper_integrity_error"),
        (PaperCommandInvalidError(), "paper_command_invalid"),
        (PermissionError("raw permission detail"), "permission_denied"),
        (RuntimeError("raw paper secret"), "paper_internal_error"),
    ],
)
def test_paper_failure_codes_are_fixed(error: Exception, code: str) -> None:
    assert _paper_failure_code(error) == code
    assert "secret" not in code


def test_create_draft_is_deterministic_order_independent_audited_and_conflict_safe() -> None:
    async def exercise() -> None:
        service, session, repository = service_with()
        result = await service.create_draft(
            CURRICULUM_ID,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            title="Grade 5 Scholarship Practice Paper",
            candidate_ids=tuple(candidate.candidate_id for candidate in repository.candidates),
            idempotency_key="paper-create-1",
            principal=REVIEWER,
        )
        assert result.deduplicated is False
        assert session.commits == 1
        assert session.added
        assert repository.last_initial["paper_id"] == result.record.domain.paper_id
        persisted_draft = cast(PaperDraft, repository.last_initial["draft"])
        assert tuple(item.lineage.blueprint_slot_id for item in persisted_draft.candidates) == (
            "slot-a",
            "slot-b",
        )

        repository.existing = repository.paper
        repository.paper.create_request_fingerprint = cast(
            str, repository.last_initial["request_fingerprint"]
        )
        repository.paper.idempotency_key_hash = cast(
            str, repository.last_initial["idempotency_key_hash"]
        )
        duplicate = await service.create_draft(
            CURRICULUM_ID,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            title="Grade 5 Scholarship Practice Paper",
            candidate_ids=tuple(reversed(tuple(c.candidate_id for c in repository.candidates))),
            idempotency_key="paper-create-1",
            principal=REVIEWER,
        )
        assert duplicate.deduplicated is True

        with pytest.raises(PaperIdempotencyConflictError):
            await service.create_draft(
                CURRICULUM_ID,
                paper_blueprint_id=PAPER_BLUEPRINT_ID,
                title="Changed title",
                candidate_ids=tuple(c.candidate_id for c in repository.candidates),
                idempotency_key="paper-create-1",
                principal=REVIEWER,
            )

    asyncio.run(exercise())


def test_publish_requires_admin_and_duplicate_current_publication_is_safe() -> None:
    async def exercise() -> None:
        service, _session, _repository = service_with()
        with pytest.raises(AuthorizationError):
            await service.publish(
                CURRICULUM_ID,
                assembled_draft().paper_id,
                expected_version=1,
                principal=REVIEWER,
            )

        service, session, repository = service_with()
        operational, telemetry_logger, _tracer = telemetry()
        service._telemetry = operational
        result = await service.publish(
            CURRICULUM_ID,
            repository.paper.id,
            expected_version=1,
            principal=ADMIN,
        )
        assert result.deduplicated is False
        assert repository.added_publications
        assert repository.last_transition["expected_state"] is PaperState.DRAFT
        assert repository.last_transition["state"] is PaperState.PUBLISHED
        assert session.commits == 1
        assert telemetry_logger.records == [
            (
                "Operational event",
                {
                    "event_name": "paper.published",
                    "outcome": "succeeded",
                    "failure_code": None,
                    "version": 1,
                    "question_count": 2,
                    "deduplicated": False,
                },
            )
        ]
        assert str(repository.paper.id) not in str(telemetry_logger.records)

        repository.paper.state = "published"
        repository.publication = result.record
        duplicate = await service.publish(
            CURRICULUM_ID,
            repository.paper.id,
            expected_version=1,
            principal=ADMIN,
        )
        assert duplicate.deduplicated is True
        assert len(repository.added_publications) == 1

        with pytest.raises(PaperVersionConflictError):
            await service.publish(
                CURRICULUM_ID,
                repository.paper.id,
                expected_version=2,
                principal=ADMIN,
            )

    asyncio.run(exercise())


def test_revision_requires_current_publication_and_write_failures_rollback_audit() -> None:
    async def exercise() -> None:
        service, _session, repository = service_with()
        with pytest.raises(PaperStateConflictError):
            await service.revise(
                CURRICULUM_ID,
                repository.paper.id,
                expected_version=1,
                candidate_ids=tuple(c.candidate_id for c in repository.candidates),
                title=None,
                principal=REVIEWER,
            )

        service, session, repository = service_with(commit_error=RuntimeError("commit failed"))
        repository.paper.state = "published"
        published = PaperWorkflowPublication.fixture(repository.draft.domain, ADMIN)
        repository.publication = published
        with pytest.raises(RuntimeError, match="commit failed"):
            await service.revise(
                CURRICULUM_ID,
                repository.paper.id,
                expected_version=1,
                candidate_ids=tuple(c.candidate_id for c in repository.candidates),
                title="Revised paper",
                principal=REVIEWER,
            )
        assert session.rollbacks == 1

    asyncio.run(exercise())


class PaperWorkflowPublication:
    @staticmethod
    def fixture(draft: object, principal: Principal) -> StoredPublication:
        from exam_guru_api.papers import PaperWorkflowService, PublishPaperCommand
        from exam_guru_api.papers.publication_models import PublishedPaperVersionModel
        from exam_guru_api.papers.serialization import serialize_published_snapshot

        assert isinstance(draft, PaperDraft)
        domain = PaperWorkflowService().publish(
            principal,
            PublishPaperCommand(draft=draft, expected_version=draft.version),
        )
        model = PublishedPaperVersionModel(
            paper_id=domain.paper_id,
            curriculum_version_id=CURRICULUM_ID,
            version=domain.version,
            previous_version=domain.previous_version,
            supersedes_content_hash=domain.supersedes_content_hash,
            snapshot=serialize_published_snapshot(domain),
            content_hash=domain.content_hash,
            published_by=domain.published_by,
            published_at=NOW,
        )
        return StoredPublication(model, domain)


def test_create_draft_maps_selection_race_integrity_and_unexpected_failures() -> None:
    async def exercise() -> None:
        candidate_ids = tuple(candidate.candidate_id for candidate in FakeRepository().candidates)

        async def create(service: PaperPublicationService) -> object:
            return await service.create_draft(
                CURRICULUM_ID,
                paper_blueprint_id=PAPER_BLUEPRINT_ID,
                title="Paper",
                candidate_ids=candidate_ids,
                idempotency_key="paper-errors",
                principal=REVIEWER,
            )

        service, session, repository = service_with()
        repository.candidates = (
            validated_candidate("slot-a"),
            approved_candidate("slot-b", candidate_number=2),
        )
        with pytest.raises(PaperCandidateSelectionError):
            await create(service)
        assert session.rollbacks == 1

        service, session, repository = service_with()
        repository.created = False
        with pytest.raises(PaperIdempotencyConflictError):
            await create(service)
        assert session.rollbacks == 1

        for source_error, expected_error in (
            (PaperCandidateSelectionNotFoundError(), PaperCandidateSelectionError),
            (
                PaperCandidateSelectionSourceLimitError(17, 16),
                PaperCandidateSelectionResourceLimitError,
            ),
            (PaperPersistenceIntegrityError(), PaperIntegrityError),
            (RuntimeError("unexpected"), RuntimeError),
        ):
            service, session, repository = service_with()
            object.__setattr__(
                repository,
                "load_candidates",
                AsyncMock(side_effect=source_error),
            )
            with pytest.raises(expected_error):
                await create(service)
            assert session.rollbacks == 1

    asyncio.run(exercise())


def test_revision_maps_assembly_domain_cas_selection_and_integrity_failures() -> None:
    async def exercise() -> None:
        configurations: tuple[
            tuple[Callable[[PaperPublicationService, FakeRepository], object], type[Exception]],
            ...,
        ] = (
            (
                lambda service, repository: setattr(
                    repository,
                    "candidates",
                    (validated_candidate("slot-a"), repository.candidates[1]),
                ),
                PaperCandidateSelectionError,
            ),
            (
                lambda service, _repository: object.__setattr__(
                    service._workflow,
                    "revise",
                    Mock(side_effect=ConcurrentVersionError(1, 2)),
                ),
                PaperStateConflictError,
            ),
            (
                lambda _service, repository: setattr(repository, "cas_result", False),
                PaperVersionConflictError,
            ),
            (
                lambda _service, repository: object.__setattr__(
                    repository,
                    "load_candidates",
                    AsyncMock(side_effect=PaperCandidateSelectionNotFoundError()),
                ),
                PaperCandidateSelectionError,
            ),
            (
                lambda _service, repository: object.__setattr__(
                    repository,
                    "load_candidates",
                    AsyncMock(side_effect=PaperCandidateSelectionSourceLimitError(17, 16)),
                ),
                PaperCandidateSelectionResourceLimitError,
            ),
            (
                lambda _service, repository: object.__setattr__(
                    repository,
                    "get_publication",
                    AsyncMock(side_effect=PaperPersistenceIntegrityError()),
                ),
                PaperIntegrityError,
            ),
        )
        for configure, expected_error in configurations:
            service, session, repository = service_with()
            make_published(repository)
            configure(service, repository)
            with pytest.raises(expected_error):
                await service.revise(
                    CURRICULUM_ID,
                    repository.paper.id,
                    expected_version=1,
                    candidate_ids=tuple(c.candidate_id for c in repository.candidates),
                    title=None,
                    principal=REVIEWER,
                )
            assert session.rollbacks == 1

    asyncio.run(exercise())


def test_publish_maps_terminal_domain_cas_integrity_and_unexpected_failures() -> None:
    async def exercise() -> None:
        service, session, repository = service_with()
        repository.paper.state = PaperState.ARCHIVED.value
        with pytest.raises(PaperStateConflictError):
            await service.publish(
                CURRICULUM_ID,
                repository.paper.id,
                expected_version=1,
                principal=ADMIN,
            )
        assert session.rollbacks == 1

        for domain_error, expected_error in (
            (ConcurrentVersionError(1, 2), PaperVersionConflictError),
            (
                InvalidPaperTransitionError(PaperState.ARCHIVED, PaperState.PUBLISHED),
                PaperStateConflictError,
            ),
            (
                PaperAssemblyError(AssemblyViolation.NOT_APPROVED, "invalid"),
                PaperStateConflictError,
            ),
        ):
            service, session, repository = service_with()
            object.__setattr__(
                service._workflow,
                "publish",
                Mock(side_effect=domain_error),
            )
            with pytest.raises(expected_error):
                await service.publish(
                    CURRICULUM_ID,
                    repository.paper.id,
                    expected_version=1,
                    principal=ADMIN,
                )
            assert session.rollbacks == 1

        service, session, repository = service_with()
        repository.cas_result = False
        with pytest.raises(PaperVersionConflictError):
            await service.publish(
                CURRICULUM_ID,
                repository.paper.id,
                expected_version=1,
                principal=ADMIN,
            )
        assert session.rollbacks == 1

        for error, expected in (
            (PaperPersistenceIntegrityError(), PaperIntegrityError),
            (RuntimeError("unexpected"), RuntimeError),
        ):
            service, session, repository = service_with()
            object.__setattr__(repository, "get_paper", AsyncMock(side_effect=error))
            with pytest.raises(expected):
                await service.publish(
                    CURRICULUM_ID,
                    repository.paper.id,
                    expected_version=1,
                    principal=ADMIN,
                )
            assert session.rollbacks == 1

    asyncio.run(exercise())


def test_archive_success_duplicate_terminal_and_failure_paths() -> None:
    async def exercise() -> None:
        service, session, repository = service_with()
        publication = make_published(repository)
        operational, telemetry_logger, _tracer = telemetry()
        service._telemetry = operational
        archived = await service.archive(
            CURRICULUM_ID,
            repository.paper.id,
            expected_version=1,
            reason="Retired.",
            principal=ADMIN,
        )
        assert archived.deduplicated is False
        assert archived.record.publication is publication
        assert repository.added_archives
        assert session.commits == 1
        assert telemetry_logger.records == [
            (
                "Operational event",
                {
                    "event_name": "paper.archived",
                    "outcome": "succeeded",
                    "failure_code": None,
                    "version": 1,
                    "question_count": 2,
                    "deduplicated": False,
                },
            )
        ]

        repository.paper.state = PaperState.ARCHIVED.value
        duplicate = await service.archive(
            CURRICULUM_ID,
            repository.paper.id,
            expected_version=1,
            reason="Retired.",
            principal=ADMIN,
        )
        assert duplicate.deduplicated is True
        with pytest.raises(PaperIdempotencyConflictError):
            await service.archive(
                CURRICULUM_ID,
                repository.paper.id,
                expected_version=1,
                reason="Changed.",
                principal=ADMIN,
            )

        configurations: tuple[
            tuple[Callable[[PaperPublicationService, FakeRepository], object], type[Exception]],
            ...,
        ] = (
            (
                lambda service, repository: setattr(
                    repository.paper, "state", PaperState.DRAFT.value
                ),
                PaperStateConflictError,
            ),
            (
                lambda service, _repository: object.__setattr__(
                    service._workflow,
                    "archive",
                    Mock(side_effect=ConcurrentVersionError(1, 2)),
                ),
                PaperVersionConflictError,
            ),
            (
                lambda service, _repository: object.__setattr__(
                    service._workflow,
                    "archive",
                    Mock(
                        side_effect=InvalidPaperTransitionError(
                            PaperState.DRAFT,
                            PaperState.ARCHIVED,
                        )
                    ),
                ),
                PaperStateConflictError,
            ),
            (
                lambda _service, repository: setattr(repository, "cas_result", False),
                PaperVersionConflictError,
            ),
            (
                lambda _service, repository: object.__setattr__(
                    repository,
                    "get_publication",
                    AsyncMock(side_effect=PaperPersistenceIntegrityError()),
                ),
                PaperIntegrityError,
            ),
            (
                lambda _service, repository: object.__setattr__(
                    repository,
                    "get_publication",
                    AsyncMock(side_effect=RuntimeError("unexpected")),
                ),
                RuntimeError,
            ),
        )
        for configure, expected_error in configurations:
            service, session, repository = service_with()
            make_published(repository)
            configure(service, repository)
            with pytest.raises(expected_error):
                await service.archive(
                    CURRICULUM_ID,
                    repository.paper.id,
                    expected_version=1,
                    reason="Retired.",
                    principal=ADMIN,
                )
            assert session.rollbacks == 1

    asyncio.run(exercise())


def test_read_boundaries_delegate_and_map_snapshot_integrity() -> None:
    async def exercise() -> None:
        service, _session, repository = service_with()
        publication = make_published(repository)
        repository.archive = StoredPaperArchive(
            PaperArchiveEventModel(
                paper_id=repository.paper.id,
                curriculum_version_id=CURRICULUM_ID,
                version=1,
                reason="Retired.",
                archived_by=ACTOR_ID,
                archived_at=NOW,
            ),
            publication,
        )
        assert (
            await service.get_paper(CURRICULUM_ID, repository.paper.id, principal=REVIEWER)
            is repository.paper
        )
        paper_list = await service.list_papers(
            CURRICULUM_ID,
            principal=REVIEWER,
            state=None,
            paper_blueprint_id=None,
            limit=10,
            offset=0,
        )
        assert cast(tuple[object, ...], paper_list) == (repository.paper,)
        assert (
            await service.get_draft(CURRICULUM_ID, repository.paper.id, 1, principal=REVIEWER)
            is repository.draft
        )
        assert await service.list_drafts(
            CURRICULUM_ID,
            repository.paper.id,
            principal=REVIEWER,
            limit=10,
            offset=0,
        ) == (repository.draft,)
        assert (
            await service.get_publication(CURRICULUM_ID, repository.paper.id, 1, principal=REVIEWER)
            is publication
        )
        publication_list = await service.list_publications(
            CURRICULUM_ID,
            repository.paper.id,
            principal=REVIEWER,
            limit=10,
            offset=0,
        )
        assert cast(tuple[object, ...], publication_list) == (publication,)
        assert (
            await service.get_archive(CURRICULUM_ID, repository.paper.id, principal=REVIEWER)
            is repository.archive
        )

        service, _session, repository = service_with()
        object.__setattr__(
            repository,
            "get_publication",
            AsyncMock(side_effect=PaperPersistenceIntegrityError()),
        )
        with pytest.raises(PaperIntegrityError):
            await service.get_publication(
                CURRICULUM_ID,
                repository.paper.id,
                1,
                principal=REVIEWER,
            )

        service, _session, repository = service_with()
        object.__setattr__(
            repository,
            "get_archive",
            AsyncMock(side_effect=PaperPersistenceIntegrityError()),
        )
        with pytest.raises(PaperIntegrityError):
            await service.get_archive(
                CURRICULUM_ID,
                repository.paper.id,
                principal=REVIEWER,
            )

    asyncio.run(exercise())


def test_create_race_winner_and_successful_revision_return_authoritative_records() -> None:
    async def exercise() -> None:
        service, session, repository = service_with()
        repository.created = False
        calls = 0

        async def race_winner(_key_hash: str) -> PracticePaperModel | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            repository.paper.id = cast(UUID, repository.last_initial["paper_id"])
            repository.paper.create_request_fingerprint = cast(
                str,
                repository.last_initial["request_fingerprint"],
            )
            return repository.paper

        object.__setattr__(repository, "find_by_idempotency_hash", race_winner)
        result = await service.create_draft(
            CURRICULUM_ID,
            paper_blueprint_id=PAPER_BLUEPRINT_ID,
            title="Paper",
            candidate_ids=tuple(c.candidate_id for c in repository.candidates),
            idempotency_key="race-winner",
            principal=REVIEWER,
        )
        assert result.deduplicated is True
        assert session.commits == 1

        service, session, repository = service_with()
        make_published(repository)
        revised = await service.revise(
            CURRICULUM_ID,
            repository.paper.id,
            expected_version=1,
            candidate_ids=tuple(c.candidate_id for c in repository.candidates),
            title="Revised paper",
            principal=REVIEWER,
        )
        assert revised.deduplicated is False
        assert repository.added_drafts
        assert session.commits == 1

    asyncio.run(exercise())


def test_paper_service_defensive_helpers_reject_invalid_keys_identity_state_and_versions() -> None:
    for invalid in (cast(str, 1), "", " surrounded ", "x" * 129, "line\nbreak", "\x00"):
        with pytest.raises(PaperCommandInvalidError):
            PaperPublicationService._validate_idempotency_key(invalid)

    service, _session, repository = service_with()
    paper_id = repository.paper.id
    valid_fingerprint = repository.paper.create_request_fingerprint
    mismatches = (
        ("id", UUID(int=1)),
        ("curriculum_version_id", UUID(int=2)),
        ("created_by", OTHER_ACTOR_ID),
        ("create_request_fingerprint", "sha256:" + "f" * 64),
    )
    for field_name, value in mismatches:
        original = getattr(repository.paper, field_name)
        setattr(repository.paper, field_name, value)
        with pytest.raises(PaperIdempotencyConflictError):
            service._assert_idempotent_winner(
                repository.paper,
                paper_id=paper_id,
                curriculum_version_id=CURRICULUM_ID,
                actor_id=ACTOR_ID,
                request_fingerprint=valid_fingerprint,
            )
        setattr(repository.paper, field_name, original)

    repository.paper.state = "forged"
    with pytest.raises(PaperIntegrityError):
        service._paper_state(repository.paper)
    repository.paper.state = PaperState.DRAFT.value
    for invalid_version in (cast(int, True), 2):
        with pytest.raises(PaperVersionConflictError):
            service._require_version(repository.paper, invalid_version)
