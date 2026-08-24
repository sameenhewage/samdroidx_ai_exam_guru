"""Authorized application commands for review, assembly, publication, and archive."""

from dataclasses import dataclass
from uuid import UUID

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.papers.domain import (
    ArchivedPaperSnapshot,
    PaperBlueprintReference,
    PaperDraft,
    PublishedPaperSnapshot,
    QuestionCandidate,
    QuestionContent,
    _archive_paper,
    _publish_paper,
    _revise_paper,
    approve_candidate,
    assemble_paper_draft,
    edit_candidate,
    reject_candidate,
    start_candidate_review,
)


@dataclass(frozen=True, slots=True)
class StartCandidateReviewCommand:
    candidate: QuestionCandidate
    expected_version: int


@dataclass(frozen=True, slots=True)
class EditCandidateCommand:
    candidate: QuestionCandidate
    content: QuestionContent
    reason: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class ApproveCandidateCommand:
    candidate: QuestionCandidate
    expected_version: int
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RejectCandidateCommand:
    candidate: QuestionCandidate
    reason: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class AssemblePaperCommand:
    paper_id: UUID
    title: str
    blueprint: PaperBlueprintReference
    candidates: tuple[QuestionCandidate, ...]


@dataclass(frozen=True, slots=True)
class RevisePaperCommand:
    source: PaperDraft | PublishedPaperSnapshot | ArchivedPaperSnapshot
    candidates: tuple[QuestionCandidate, ...]
    expected_version: int
    title: str | None = None


@dataclass(frozen=True, slots=True)
class PublishPaperCommand:
    draft: PaperDraft
    expected_version: int


@dataclass(frozen=True, slots=True)
class ArchivePaperCommand:
    publication: PublishedPaperSnapshot | ArchivedPaperSnapshot
    reason: str
    expected_version: int


class PaperWorkflowService:
    """Stateless authorization boundary around immutable domain transitions."""

    def start_review(
        self,
        principal: Principal,
        command: StartCandidateReviewCommand,
    ) -> QuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        return start_candidate_review(
            command.candidate,
            reviewer_id=principal.subject_id,
            expected_version=command.expected_version,
        )

    def edit(
        self,
        principal: Principal,
        command: EditCandidateCommand,
    ) -> QuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        return edit_candidate(
            command.candidate,
            content=command.content,
            reviewer_id=principal.subject_id,
            reason=command.reason,
            expected_version=command.expected_version,
        )

    def approve(
        self,
        principal: Principal,
        command: ApproveCandidateCommand,
    ) -> QuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        return approve_candidate(
            command.candidate,
            reviewer_id=principal.subject_id,
            expected_version=command.expected_version,
            note=command.note,
        )

    def reject(
        self,
        principal: Principal,
        command: RejectCandidateCommand,
    ) -> QuestionCandidate:
        authorize(principal, Permission.CONTENT_REVIEW)
        return reject_candidate(
            command.candidate,
            reviewer_id=principal.subject_id,
            reason=command.reason,
            expected_version=command.expected_version,
        )

    def assemble(
        self,
        principal: Principal,
        command: AssemblePaperCommand,
    ) -> PaperDraft:
        authorize(principal, Permission.CONTENT_REVIEW)
        return assemble_paper_draft(
            paper_id=command.paper_id,
            title=command.title,
            blueprint=command.blueprint,
            candidates=command.candidates,
        )

    def revise(
        self,
        principal: Principal,
        command: RevisePaperCommand,
    ) -> PaperDraft:
        authorize(principal, Permission.CONTENT_REVIEW)
        return _revise_paper(
            command.source,
            candidates=command.candidates,
            expected_version=command.expected_version,
            title=command.title,
        )

    def publish(
        self,
        principal: Principal,
        command: PublishPaperCommand,
    ) -> PublishedPaperSnapshot:
        if not isinstance(command, PublishPaperCommand):
            raise TypeError("publish requires an explicit PublishPaperCommand")
        authorize(principal, Permission.PAPER_PUBLISH)
        return _publish_paper(
            command.draft,
            published_by=principal.subject_id,
            expected_version=command.expected_version,
        )

    def archive(
        self,
        principal: Principal,
        command: ArchivePaperCommand,
    ) -> ArchivedPaperSnapshot:
        authorize(principal, Permission.PAPER_PUBLISH)
        return _archive_paper(
            command.publication,
            archived_by=principal.subject_id,
            reason=command.reason,
            expected_version=command.expected_version,
        )
