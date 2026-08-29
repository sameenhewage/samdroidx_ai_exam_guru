import hashlib
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.observability import OperationalTelemetry, get_operational_telemetry
from exam_guru_api.papers.domain import (
    AssemblyViolation,
    ConcurrentVersionError,
    InvalidPaperTransitionError,
    PaperAssemblyError,
    PaperState,
)
from exam_guru_api.papers.publication_models import PracticePaperModel
from exam_guru_api.papers.publication_repository import (
    PaperCandidateSelectionNotFoundError,
    PaperCandidateSelectionSourceLimitError,
    PaperPersistenceIntegrityError,
    PaperSummary,
    PublicationVersionSummary,
    SqlAlchemyPaperPublicationRepository,
    StoredPaperArchive,
    StoredPaperDraft,
    StoredPublication,
)
from exam_guru_api.papers.serialization import canonical_publication_bytes
from exam_guru_api.papers.service import (
    ArchivePaperCommand,
    AssemblePaperCommand,
    PaperWorkflowService,
    PublishPaperCommand,
    RevisePaperCommand,
)

_PAPER_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/practice-papers")


class PaperPublicationError(RuntimeError):
    pass


class PaperIdempotencyConflictError(PaperPublicationError):
    pass


class PaperVersionConflictError(PaperPublicationError):
    pass


class PaperStateConflictError(PaperPublicationError):
    pass


class PaperCandidateSelectionError(PaperPublicationError):
    def __init__(self, violation: AssemblyViolation | None = None) -> None:
        self.violation = violation
        super().__init__(
            "paper candidate selection is invalid"
            if violation is None
            else f"paper candidate selection violates {violation.value}"
        )


class PaperCandidateSelectionResourceLimitError(PaperPublicationError):
    def __init__(self, estimated_bytes: int, limit_bytes: int) -> None:
        self.estimated_bytes = estimated_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"paper candidate selection requires {estimated_bytes} estimated source bytes; "
            f"limit is {limit_bytes}"
        )


class PaperIntegrityError(PaperPublicationError):
    pass


class PaperCommandInvalidError(PaperPublicationError):
    pass


def _paper_failure_code(error: Exception) -> str:
    if isinstance(error, PaperIdempotencyConflictError):
        return "paper_idempotency_conflict"
    if isinstance(error, PaperVersionConflictError):
        return "paper_version_conflict"
    if isinstance(error, PaperStateConflictError):
        return "paper_state_conflict"
    if isinstance(error, PaperCandidateSelectionResourceLimitError):
        return "paper_resource_limit"
    if isinstance(error, PaperCandidateSelectionError):
        return "paper_candidate_selection_invalid"
    if isinstance(error, PaperIntegrityError):
        return "paper_integrity_error"
    if isinstance(error, PaperCommandInvalidError):
        return "paper_command_invalid"
    if isinstance(error, PermissionError):
        return "permission_denied"
    return "paper_internal_error"


@dataclass(frozen=True, slots=True)
class PaperDraftCreationResult:
    record: StoredPaperDraft
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class PaperPublicationResult:
    record: StoredPublication
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class PaperArchiveResult:
    record: StoredPaperArchive
    deduplicated: bool


class PaperPublicationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        telemetry: OperationalTelemetry | None = None,
    ) -> None:
        self._session = session
        self._telemetry = telemetry or get_operational_telemetry()
        self._repository = SqlAlchemyPaperPublicationRepository(session)
        self._workflow = PaperWorkflowService()

    async def create_draft(
        self,
        curriculum_version_id: UUID,
        *,
        paper_blueprint_id: UUID,
        title: str,
        candidate_ids: tuple[UUID, ...],
        idempotency_key: str,
        principal: Principal,
        commit: bool = True,
    ) -> PaperDraftCreationResult:
        authorize(principal, Permission.CONTENT_REVIEW)
        self._validate_idempotency_key(idempotency_key)
        request_fingerprint = _fingerprint(
            {
                "candidate_ids": sorted(str(candidate_id) for candidate_id in candidate_ids),
                "curriculum_version_id": str(curriculum_version_id),
                "paper_blueprint_id": str(paper_blueprint_id),
                "schema": "paper-draft-request.v1",
                "title": title,
            }
        )
        idempotency_key_hash = _fingerprint(
            {
                "actor_id": str(principal.subject_id),
                "curriculum_version_id": str(curriculum_version_id),
                "idempotency_key": idempotency_key,
            }
        )
        paper_id = uuid5(_PAPER_NAMESPACE, idempotency_key_hash)
        existing = await self._repository.find_by_idempotency_hash(idempotency_key_hash)
        if existing is not None:
            self._assert_idempotent_winner(
                existing,
                paper_id=paper_id,
                curriculum_version_id=curriculum_version_id,
                actor_id=principal.subject_id,
                request_fingerprint=request_fingerprint,
            )
            return PaperDraftCreationResult(
                await self._repository.get_draft(
                    curriculum_version_id,
                    paper_id,
                    1,
                    paper=existing,
                ),
                deduplicated=True,
            )

        try:
            blueprint = await self._repository.get_blueprint_reference(
                curriculum_version_id,
                paper_blueprint_id,
            )
            candidates = await self._repository.load_candidates(
                curriculum_version_id,
                candidate_ids,
            )
            try:
                draft = self._workflow.assemble(
                    principal,
                    AssemblePaperCommand(
                        paper_id=paper_id,
                        title=title,
                        blueprint=blueprint,
                        candidates=candidates,
                    ),
                )
            except PaperAssemblyError as error:
                raise PaperCandidateSelectionError(error.violation) from error
            created = await self._repository.insert_initial(
                paper_id=paper_id,
                curriculum_version_id=curriculum_version_id,
                blueprint=blueprint,
                draft=draft,
                idempotency_key_hash=idempotency_key_hash,
                request_fingerprint=request_fingerprint,
                actor_id=principal.subject_id,
            )
            if not created:
                winner = await self._repository.find_by_idempotency_hash(idempotency_key_hash)
                if winner is None:
                    raise PaperIdempotencyConflictError(idempotency_key_hash)
                self._assert_idempotent_winner(
                    winner,
                    paper_id=paper_id,
                    curriculum_version_id=curriculum_version_id,
                    actor_id=principal.subject_id,
                    request_fingerprint=request_fingerprint,
                )
                if commit:
                    await self._session.commit()
                return PaperDraftCreationResult(
                    await self._repository.get_draft(
                        curriculum_version_id,
                        paper_id,
                        1,
                        paper=winner,
                    ),
                    deduplicated=True,
                )
            self._add_audit(
                actor_id=principal.subject_id,
                action="practice_paper.created",
                paper_id=paper_id,
                payload={
                    "blueprint_id": blueprint.blueprint_id,
                    "blueprint_version": blueprint.blueprint_version,
                    "candidate_count": len(draft.candidates),
                    "curriculum_version_id": str(curriculum_version_id),
                    "paper_blueprint_id": str(paper_blueprint_id),
                    "request_fingerprint": request_fingerprint,
                    "state": PaperState.DRAFT.value,
                    "version": 1,
                },
            )
            if commit:
                await self._session.commit()
            else:
                await self._session.flush()
            return PaperDraftCreationResult(
                await self._repository.get_draft(curriculum_version_id, paper_id, 1),
                deduplicated=False,
            )
        except PaperCandidateSelectionSourceLimitError as error:
            await self._session.rollback()
            raise PaperCandidateSelectionResourceLimitError(
                error.estimated_bytes,
                error.limit_bytes,
            ) from error
        except PaperCandidateSelectionNotFoundError as error:
            await self._session.rollback()
            raise PaperCandidateSelectionError() from error
        except PaperPersistenceIntegrityError as error:
            await self._session.rollback()
            raise PaperIntegrityError(paper_id) from error
        except Exception:
            await self._session.rollback()
            raise

    async def revise(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        expected_version: int,
        candidate_ids: tuple[UUID, ...],
        title: str | None,
        principal: Principal,
    ) -> PaperDraftCreationResult:
        authorize(principal, Permission.CONTENT_REVIEW)
        try:
            paper = await self._repository.get_paper(
                curriculum_version_id,
                paper_id,
                for_update=True,
            )
            state = self._paper_state(paper)
            self._require_version(paper, expected_version)
            if state is not PaperState.PUBLISHED:
                raise PaperStateConflictError(paper_id)
            source = await self._repository.get_publication(
                curriculum_version_id,
                paper_id,
                expected_version,
                paper=paper,
            )
            candidates = await self._repository.load_candidates(
                curriculum_version_id,
                candidate_ids,
            )
            try:
                draft = self._workflow.revise(
                    principal,
                    RevisePaperCommand(
                        source=source.domain,
                        candidates=candidates,
                        expected_version=expected_version,
                        title=title,
                    ),
                )
            except PaperAssemblyError as error:
                raise PaperCandidateSelectionError(error.violation) from error
            except (ConcurrentVersionError, InvalidPaperTransitionError) as error:
                raise PaperStateConflictError(paper_id) from error
            transitioned = await self._repository.cas_transition(
                curriculum_version_id=curriculum_version_id,
                paper_id=paper_id,
                expected_state=PaperState.PUBLISHED,
                expected_version=expected_version,
                state=PaperState.DRAFT,
                version=draft.version,
                actor_id=principal.subject_id,
            )
            if not transitioned:
                raise PaperVersionConflictError(paper_id)
            await self._repository.add_draft(
                curriculum_version_id=curriculum_version_id,
                draft=draft,
                actor_id=principal.subject_id,
            )
            await self._session.flush()
            self._add_audit(
                actor_id=principal.subject_id,
                action="practice_paper.revised",
                paper_id=paper_id,
                payload={
                    "candidate_count": len(draft.candidates),
                    "curriculum_version_id": str(curriculum_version_id),
                    "from_state": PaperState.PUBLISHED.value,
                    "from_version": expected_version,
                    "supersedes_content_hash": source.domain.content_hash,
                    "to_state": PaperState.DRAFT.value,
                    "version": draft.version,
                },
            )
            await self._session.commit()
            return PaperDraftCreationResult(
                await self._repository.get_draft(
                    curriculum_version_id,
                    paper_id,
                    draft.version,
                ),
                deduplicated=False,
            )
        except PaperCandidateSelectionSourceLimitError as error:
            await self._session.rollback()
            raise PaperCandidateSelectionResourceLimitError(
                error.estimated_bytes,
                error.limit_bytes,
            ) from error
        except PaperCandidateSelectionNotFoundError as error:
            await self._session.rollback()
            raise PaperCandidateSelectionError() from error
        except PaperPersistenceIntegrityError as error:
            await self._session.rollback()
            raise PaperIntegrityError(paper_id) from error
        except Exception:
            await self._session.rollback()
            raise

    async def publish(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        expected_version: int,
        principal: Principal,
    ) -> PaperPublicationResult:
        try:
            result = await self._publish(
                curriculum_version_id,
                paper_id,
                expected_version=expected_version,
                principal=principal,
            )
        except Exception as error:
            self._telemetry.paper_transition(
                action="published",
                outcome="failed",
                failure_code=_paper_failure_code(error),
                version=None,
                question_count=None,
                deduplicated=False,
            )
            raise
        self._telemetry.paper_transition(
            action="published",
            outcome="succeeded",
            failure_code=None,
            version=result.record.domain.version,
            question_count=len(result.record.domain.questions),
            deduplicated=result.deduplicated,
        )
        return result

    async def _publish(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        expected_version: int,
        principal: Principal,
    ) -> PaperPublicationResult:
        authorize(principal, Permission.PAPER_PUBLISH)
        try:
            paper = await self._repository.get_paper(
                curriculum_version_id,
                paper_id,
                for_update=True,
            )
            state = self._paper_state(paper)
            self._require_version(paper, expected_version)
            if state is PaperState.PUBLISHED:
                record = await self._repository.get_publication(
                    curriculum_version_id,
                    paper_id,
                    expected_version,
                    paper=paper,
                )
                await self._session.commit()
                return PaperPublicationResult(record, deduplicated=True)
            if state is not PaperState.DRAFT:
                raise PaperStateConflictError(paper_id)
            draft = await self._repository.get_draft(
                curriculum_version_id,
                paper_id,
                expected_version,
                paper=paper,
            )
            try:
                publication = self._workflow.publish(
                    principal,
                    PublishPaperCommand(
                        draft=draft.domain,
                        expected_version=expected_version,
                    ),
                )
            except ConcurrentVersionError as error:
                raise PaperVersionConflictError(paper_id) from error
            except (InvalidPaperTransitionError, PaperAssemblyError) as error:
                raise PaperStateConflictError(paper_id) from error
            self._repository.add_publication(
                curriculum_version_id=curriculum_version_id,
                publication=publication,
            )
            await self._session.flush()
            transitioned = await self._repository.cas_transition(
                curriculum_version_id=curriculum_version_id,
                paper_id=paper_id,
                expected_state=PaperState.DRAFT,
                expected_version=expected_version,
                state=PaperState.PUBLISHED,
                version=expected_version,
                actor_id=principal.subject_id,
            )
            if not transitioned:
                raise PaperVersionConflictError(paper_id)
            self._add_audit(
                actor_id=principal.subject_id,
                action="practice_paper.published",
                paper_id=paper_id,
                payload={
                    "content_hash": publication.content_hash,
                    "curriculum_version_id": str(curriculum_version_id),
                    "from_state": PaperState.DRAFT.value,
                    "question_count": len(publication.questions),
                    "to_state": PaperState.PUBLISHED.value,
                    "version": publication.version,
                },
            )
            await self._session.commit()
            return PaperPublicationResult(
                await self._repository.get_publication(
                    curriculum_version_id,
                    paper_id,
                    expected_version,
                ),
                deduplicated=False,
            )
        except PaperPersistenceIntegrityError as error:
            await self._session.rollback()
            raise PaperIntegrityError(paper_id) from error
        except Exception:
            await self._session.rollback()
            raise

    async def archive(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        expected_version: int,
        reason: str,
        principal: Principal,
    ) -> PaperArchiveResult:
        try:
            result = await self._archive(
                curriculum_version_id,
                paper_id,
                expected_version=expected_version,
                reason=reason,
                principal=principal,
            )
        except Exception as error:
            self._telemetry.paper_transition(
                action="archived",
                outcome="failed",
                failure_code=_paper_failure_code(error),
                version=None,
                question_count=None,
                deduplicated=False,
            )
            raise
        self._telemetry.paper_transition(
            action="archived",
            outcome="succeeded",
            failure_code=None,
            version=result.record.archive.version,
            question_count=len(result.record.publication.domain.questions),
            deduplicated=result.deduplicated,
        )
        return result

    async def _archive(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        expected_version: int,
        reason: str,
        principal: Principal,
    ) -> PaperArchiveResult:
        authorize(principal, Permission.PAPER_PUBLISH)
        try:
            paper = await self._repository.get_paper(
                curriculum_version_id,
                paper_id,
                for_update=True,
            )
            state = self._paper_state(paper)
            self._require_version(paper, expected_version)
            if state is PaperState.ARCHIVED:
                existing = await self._repository.get_archive(
                    curriculum_version_id,
                    paper_id,
                    paper=paper,
                )
                if existing.archive.reason != reason:
                    raise PaperIdempotencyConflictError(paper_id)
                await self._session.commit()
                return PaperArchiveResult(existing, deduplicated=True)
            if state is not PaperState.PUBLISHED:
                raise PaperStateConflictError(paper_id)
            publication = await self._repository.get_publication(
                curriculum_version_id,
                paper_id,
                expected_version,
                paper=paper,
            )
            try:
                archived = self._workflow.archive(
                    principal,
                    ArchivePaperCommand(
                        publication=publication.domain,
                        reason=reason,
                        expected_version=expected_version,
                    ),
                )
            except ConcurrentVersionError as error:
                raise PaperVersionConflictError(paper_id) from error
            except InvalidPaperTransitionError as error:
                raise PaperStateConflictError(paper_id) from error
            self._repository.add_archive(
                curriculum_version_id=curriculum_version_id,
                paper_id=paper_id,
                version=archived.version,
                reason=archived.reason,
                actor_id=principal.subject_id,
            )
            await self._session.flush()
            transitioned = await self._repository.cas_transition(
                curriculum_version_id=curriculum_version_id,
                paper_id=paper_id,
                expected_state=PaperState.PUBLISHED,
                expected_version=expected_version,
                state=PaperState.ARCHIVED,
                version=expected_version,
                actor_id=principal.subject_id,
            )
            if not transitioned:
                raise PaperVersionConflictError(paper_id)
            self._add_audit(
                actor_id=principal.subject_id,
                action="practice_paper.archived",
                paper_id=paper_id,
                payload={
                    "content_hash": archived.content_hash,
                    "curriculum_version_id": str(curriculum_version_id),
                    "from_state": PaperState.PUBLISHED.value,
                    "reason_recorded": True,
                    "to_state": PaperState.ARCHIVED.value,
                    "version": archived.version,
                },
            )
            await self._session.commit()
            return PaperArchiveResult(
                await self._repository.get_archive(curriculum_version_id, paper_id),
                deduplicated=False,
            )
        except PaperPersistenceIntegrityError as error:
            await self._session.rollback()
            raise PaperIntegrityError(paper_id) from error
        except Exception:
            await self._session.rollback()
            raise

    async def get_paper(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        principal: Principal,
    ) -> PracticePaperModel:
        authorize(principal, Permission.CONTENT_REVIEW)
        return await self._repository.get_paper(curriculum_version_id, paper_id)

    async def list_papers(
        self,
        curriculum_version_id: UUID,
        *,
        principal: Principal,
        state: PaperState | None,
        paper_blueprint_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[PaperSummary, ...]:
        authorize(principal, Permission.CONTENT_REVIEW)
        return await self._repository.list_papers(
            curriculum_version_id,
            state=state,
            paper_blueprint_id=paper_blueprint_id,
            limit=limit,
            offset=offset,
        )

    async def get_draft(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        version: int,
        *,
        principal: Principal,
    ) -> StoredPaperDraft:
        authorize(principal, Permission.CONTENT_REVIEW)
        return await self._repository.get_draft(curriculum_version_id, paper_id, version)

    async def list_drafts(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        principal: Principal,
        limit: int,
        offset: int,
    ) -> tuple[StoredPaperDraft, ...]:
        authorize(principal, Permission.CONTENT_REVIEW)
        return await self._repository.list_drafts(
            curriculum_version_id,
            paper_id,
            limit=limit,
            offset=offset,
        )

    async def get_publication(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        version: int,
        *,
        principal: Principal,
    ) -> StoredPublication:
        authorize(principal, Permission.CONTENT_REVIEW)
        try:
            return await self._repository.get_publication(
                curriculum_version_id,
                paper_id,
                version,
            )
        except PaperPersistenceIntegrityError as error:
            raise PaperIntegrityError(paper_id) from error

    async def list_publications(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        principal: Principal,
        limit: int,
        offset: int,
    ) -> tuple[PublicationVersionSummary, ...]:
        authorize(principal, Permission.CONTENT_REVIEW)
        return await self._repository.list_publications(
            curriculum_version_id,
            paper_id,
            limit=limit,
            offset=offset,
        )

    async def get_archive(
        self,
        curriculum_version_id: UUID,
        paper_id: UUID,
        *,
        principal: Principal,
    ) -> StoredPaperArchive:
        authorize(principal, Permission.CONTENT_REVIEW)
        try:
            return await self._repository.get_archive(curriculum_version_id, paper_id)
        except PaperPersistenceIntegrityError as error:
            raise PaperIntegrityError(paper_id) from error

    @staticmethod
    def _validate_idempotency_key(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 128
            or any(character.isspace() or not character.isprintable() for character in value)
        ):
            raise PaperCommandInvalidError("invalid idempotency key")

    @staticmethod
    def _assert_idempotent_winner(
        paper: PracticePaperModel,
        *,
        paper_id: UUID,
        curriculum_version_id: UUID,
        actor_id: UUID,
        request_fingerprint: str,
    ) -> None:
        if (
            paper.id != paper_id
            or paper.curriculum_version_id != curriculum_version_id
            or paper.created_by != actor_id
            or paper.create_request_fingerprint != request_fingerprint
        ):
            raise PaperIdempotencyConflictError(paper_id)

    @staticmethod
    def _paper_state(paper: PracticePaperModel) -> PaperState:
        try:
            return PaperState(paper.state)
        except ValueError as error:
            raise PaperIntegrityError(paper.id) from error

    @staticmethod
    def _require_version(paper: PracticePaperModel, expected_version: int) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version != paper.current_version
        ):
            raise PaperVersionConflictError(paper.id)

    def _add_audit(
        self,
        *,
        actor_id: UUID,
        action: str,
        paper_id: UUID,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="practice_paper",
                resource_id=paper_id,
                payload=payload,
            )
        )


def _fingerprint(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_publication_bytes(value)).hexdigest()}"
