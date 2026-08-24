"""Immutable domain values and invariants for human-reviewed paper publishing.

Generation and retrieved source text are data only.  They cannot advance review
state or create a publication.  Publication constructors require a private
capability used only by the authorized application service in ``service.py``.
"""

import hashlib
import json
from dataclasses import InitVar, dataclass, field, replace
from enum import StrEnum
from uuid import UUID

MAX_CANDIDATE_REVISIONS = 32
MAX_CANDIDATE_VERSION = MAX_CANDIDATE_REVISIONS + 3
MAX_PAPER_VERSIONS = 32
MAX_PAPER_SLOTS = 200
MAX_PAPER_TITLE_CHARACTERS = 512
MAX_ARCHIVE_REASON_CHARACTERS = 1_024


class CandidateState(StrEnum):
    GENERATED = "generated"
    VALIDATED = "validated"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewAction(StrEnum):
    STARTED = "started"
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"


class PaperState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AssemblyViolation(StrEnum):
    NOT_APPROVED = "not_approved"
    BLUEPRINT_MISMATCH = "blueprint_mismatch"
    SLOT_COVERAGE = "slot_coverage"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


class PaperDomainError(ValueError):
    """Base class for deterministic paper-domain failures."""


class CandidateInvariantError(PaperDomainError):
    """Raised when a candidate value would violate immutable review history."""


class ValidationNotPassedError(PaperDomainError):
    def __init__(self, candidate_id: UUID) -> None:
        self.candidate_id = candidate_id
        super().__init__(f"candidate {candidate_id} did not pass validation")


class InvalidCandidateTransitionError(PaperDomainError):
    def __init__(self, current: CandidateState, target: CandidateState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"candidate cannot transition from {current.value} to {target.value}")


class ConcurrentVersionError(PaperDomainError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected version {expected}, found {actual}")


class PaperAssemblyError(PaperDomainError):
    def __init__(self, violation: AssemblyViolation, detail: str) -> None:
        self.violation = violation
        self.detail = detail
        super().__init__(f"{violation.value}: {detail}")


class InvalidPaperTransitionError(PaperDomainError):
    def __init__(self, current: PaperState, target: PaperState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"paper cannot transition from {current.value} to {target.value}")


class ServiceBoundaryRequiredError(PermissionError):
    """Raised when code tries to manufacture a published/archive state directly."""

    def __init__(self, target: PaperState) -> None:
        self.target = target
        super().__init__(f"{target.value} snapshots require the authorized service boundary")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CandidateInvariantError(f"{field_name} must be non-blank without surrounding space")
    return value


def _require_optional_text(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name)


def _require_bounded_text(value: object, field_name: str, maximum: int) -> str:
    text = _require_text(value, field_name)
    if len(text) > maximum:
        raise CandidateInvariantError(f"{field_name} cannot exceed {maximum} characters")
    return text


def _require_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise CandidateInvariantError(f"{field_name} must be a UUID")
    return value


def _require_positive_integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CandidateInvariantError(f"{field_name} must be a positive integer")
    return value


def _require_paper_version(value: object) -> int:
    version = _require_positive_integer(value, "paper version")
    if version > MAX_PAPER_VERSIONS:
        raise CandidateInvariantError(f"paper version cannot exceed {MAX_PAPER_VERSIONS}")
    return version


def _require_expected_version(expected: int, actual: int) -> None:
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise CandidateInvariantError("expected_version must be an integer")
    if expected != actual:
        raise ConcurrentVersionError(expected, actual)


def _require_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CandidateInvariantError(f"{field_name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    source_document_id: str
    source_version: str
    page_number: int
    chunk_id: str

    def __post_init__(self) -> None:
        _require_text(self.source_document_id, "source_document_id")
        _require_text(self.source_version, "source_version")
        _require_positive_integer(self.page_number, "page_number")
        _require_text(self.chunk_id, "chunk_id")


@dataclass(frozen=True, slots=True)
class GenerationLineage:
    generation_id: UUID
    generation_attempt_id: UUID
    blueprint_id: str
    blueprint_version: str
    blueprint_slot_id: str
    prompt_version: str
    provider: str
    model_version: str
    retrieval_version: str
    schema_version: str
    provenance: tuple[SourceProvenance, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.generation_id, "generation_id")
        _require_uuid(self.generation_attempt_id, "generation_attempt_id")
        for field_name in (
            "blueprint_id",
            "blueprint_version",
            "blueprint_slot_id",
            "prompt_version",
            "provider",
            "model_version",
            "retrieval_version",
            "schema_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.provenance, tuple) or not self.provenance:
            raise CandidateInvariantError("provenance must be a non-empty tuple")
        if any(not isinstance(item, SourceProvenance) for item in self.provenance):
            raise CandidateInvariantError("provenance entries must be SourceProvenance values")
        if len(set(self.provenance)) != len(self.provenance):
            raise CandidateInvariantError("provenance entries must be unique")


@dataclass(frozen=True, slots=True)
class QuestionOption:
    option_id: str
    text: str

    def __post_init__(self) -> None:
        _require_text(self.option_id, "option_id")
        _require_text(self.text, "option text")


@dataclass(frozen=True, slots=True)
class QuestionContent:
    question_type: str
    stem: str
    options: tuple[QuestionOption, ...]
    answer: str
    explanation: str
    marks: int
    marking_guide: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.question_type, "question_type")
        _require_text(self.stem, "stem")
        _require_text(self.answer, "answer")
        _require_text(self.explanation, "explanation")
        _require_positive_integer(self.marks, "marks")
        if not isinstance(self.options, tuple) or any(
            not isinstance(option, QuestionOption) for option in self.options
        ):
            raise CandidateInvariantError("options must be a tuple of QuestionOption values")
        option_ids = tuple(option.option_id for option in self.options)
        if len(set(option_ids)) != len(option_ids):
            raise CandidateInvariantError("option identifiers must be unique")
        if self.question_type == "multiple_choice" and (
            not option_ids or sum(option_id == self.answer for option_id in option_ids) != 1
        ):
            raise CandidateInvariantError(
                "multiple-choice answer must reference exactly one existing option"
            )
        if not isinstance(self.marking_guide, tuple) or not self.marking_guide:
            raise CandidateInvariantError("marking_guide must be a non-empty tuple")
        for criterion in self.marking_guide:
            _require_text(criterion, "marking criterion")


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    validation_run_id: UUID
    validator_version: str
    finding_refs: tuple[str, ...]
    passed: bool
    validated_revision: int = 1

    def __post_init__(self) -> None:
        _require_uuid(self.validation_run_id, "validation_run_id")
        _require_text(self.validator_version, "validator_version")
        if not isinstance(self.finding_refs, tuple) or not self.finding_refs:
            raise CandidateInvariantError("finding_refs must be a non-empty tuple")
        for finding_ref in self.finding_refs:
            _require_text(finding_ref, "finding_ref")
        if len(set(self.finding_refs)) != len(self.finding_refs):
            raise CandidateInvariantError("finding_refs must be unique")
        if not isinstance(self.passed, bool):
            raise CandidateInvariantError("passed must be a boolean")
        if self.validated_revision != 1 or isinstance(self.validated_revision, bool):
            raise CandidateInvariantError("validated_revision must identify generated revision 1")


@dataclass(frozen=True, slots=True)
class CandidateRevision:
    revision: int
    content: QuestionContent
    reviewer_id: UUID | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_positive_integer(self.revision, "revision")
        if self.revision > MAX_CANDIDATE_REVISIONS:
            raise CandidateInvariantError(f"revision cannot exceed {MAX_CANDIDATE_REVISIONS}")
        if not isinstance(self.content, QuestionContent):
            raise CandidateInvariantError("content must be QuestionContent")
        if self.reviewer_id is None:
            if self.reason is not None:
                raise CandidateInvariantError("a generated revision cannot have an edit reason")
        else:
            _require_uuid(self.reviewer_id, "reviewer_id")
            _require_text(self.reason, "edit reason")


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    action: ReviewAction
    reviewer_id: UUID
    candidate_version: int
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, ReviewAction):
            raise CandidateInvariantError("action must be a ReviewAction")
        _require_uuid(self.reviewer_id, "reviewer_id")
        _require_positive_integer(self.candidate_version, "candidate_version")
        if self.candidate_version > MAX_CANDIDATE_VERSION:
            raise CandidateInvariantError(
                f"candidate version cannot exceed {MAX_CANDIDATE_VERSION}"
            )
        _require_optional_text(self.reason, "review reason")
        if self.action in {ReviewAction.EDITED, ReviewAction.REJECTED} and self.reason is None:
            raise CandidateInvariantError(f"{self.action.value} review action requires a reason")


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    state: CandidateState
    reviewer_id: UUID
    candidate_version: int
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in {CandidateState.APPROVED, CandidateState.REJECTED}:
            raise CandidateInvariantError("review decision must be approved or rejected")
        _require_uuid(self.reviewer_id, "reviewer_id")
        _require_positive_integer(self.candidate_version, "candidate_version")
        if self.candidate_version > MAX_CANDIDATE_VERSION:
            raise CandidateInvariantError(
                f"candidate version cannot exceed {MAX_CANDIDATE_VERSION}"
            )
        _require_optional_text(self.reason, "decision reason")
        if self.state is CandidateState.REJECTED and self.reason is None:
            raise CandidateInvariantError("rejection reason is required")


@dataclass(frozen=True, slots=True)
class QuestionCandidate:
    candidate_id: UUID
    state: CandidateState
    version: int
    lineage: GenerationLineage
    revisions: tuple[CandidateRevision, ...]
    validation: ValidationEvidence | None = None
    review_history: tuple[ReviewRecord, ...] = ()
    decision: ReviewDecision | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.candidate_id, "candidate_id")
        if not isinstance(self.state, CandidateState):
            raise CandidateInvariantError("state must be a CandidateState")
        _require_positive_integer(self.version, "version")
        if self.version > MAX_CANDIDATE_VERSION:
            raise CandidateInvariantError(
                f"candidate version cannot exceed {MAX_CANDIDATE_VERSION}"
            )
        if not isinstance(self.lineage, GenerationLineage):
            raise CandidateInvariantError("lineage must be GenerationLineage")
        if not isinstance(self.revisions, tuple) or not self.revisions:
            raise CandidateInvariantError("revisions must be a non-empty tuple")
        if len(self.revisions) > MAX_CANDIDATE_REVISIONS:
            raise CandidateInvariantError(
                f"candidate cannot exceed {MAX_CANDIDATE_REVISIONS} revisions"
            )
        if any(not isinstance(revision, CandidateRevision) for revision in self.revisions):
            raise CandidateInvariantError("revisions must contain CandidateRevision values")
        if tuple(revision.revision for revision in self.revisions) != tuple(
            range(1, len(self.revisions) + 1)
        ):
            raise CandidateInvariantError("content revision numbers must be contiguous")
        if self.revisions[0].reviewer_id is not None:
            raise CandidateInvariantError("the first content revision must be generated")
        if not isinstance(self.review_history, tuple) or any(
            not isinstance(record, ReviewRecord) for record in self.review_history
        ):
            raise CandidateInvariantError("review_history must be a tuple of ReviewRecord values")

        if self.state is CandidateState.GENERATED:
            self._validate_generated_state()
            return
        self._validate_post_generation_state()

    @property
    def content(self) -> QuestionContent:
        return self.revisions[-1].content

    def _validate_generated_state(self) -> None:
        if (
            self.version != 1
            or len(self.revisions) != 1
            or self.validation is not None
            or self.review_history
            or self.decision is not None
        ):
            raise CandidateInvariantError("generated candidate history is inconsistent")

    def _validate_post_generation_state(self) -> None:
        if not isinstance(self.validation, ValidationEvidence) or not self.validation.passed:
            raise CandidateInvariantError("post-generation candidate requires passed validation")
        if self.state is CandidateState.VALIDATED:
            if self.version != 2 or self.review_history or self.decision is not None:
                raise CandidateInvariantError("validated candidate history is inconsistent")
            return
        if not self.review_history or self.review_history[0].action is not ReviewAction.STARTED:
            raise CandidateInvariantError("review must begin with a started record")
        expected_versions = tuple(range(3, 3 + len(self.review_history)))
        if tuple(record.candidate_version for record in self.review_history) != expected_versions:
            raise CandidateInvariantError("review record versions must be contiguous")
        if self.version != 2 + len(self.review_history):
            raise CandidateInvariantError("candidate version must include every review action")
        edit_count = sum(record.action is ReviewAction.EDITED for record in self.review_history)
        if len(self.revisions) != 1 + edit_count:
            raise CandidateInvariantError("content revisions must match reviewer edit history")
        if self.state is CandidateState.IN_REVIEW:
            if self.decision is not None or self.review_history[-1].action not in {
                ReviewAction.STARTED,
                ReviewAction.EDITED,
            }:
                raise CandidateInvariantError("in-review candidate history is inconsistent")
            return
        self._validate_terminal_state()

    def _validate_terminal_state(self) -> None:
        expected_action = (
            ReviewAction.APPROVED
            if self.state is CandidateState.APPROVED
            else ReviewAction.REJECTED
        )
        if (
            self.state not in {CandidateState.APPROVED, CandidateState.REJECTED}
            or not isinstance(self.decision, ReviewDecision)
            or self.decision.state is not self.state
            or self.decision.candidate_version != self.version
            or self.review_history[-1].action is not expected_action
        ):
            raise CandidateInvariantError("terminal candidate decision history is inconsistent")


def create_generated_candidate(
    *,
    candidate_id: UUID,
    lineage: GenerationLineage,
    content: QuestionContent,
) -> QuestionCandidate:
    return QuestionCandidate(
        candidate_id=candidate_id,
        state=CandidateState.GENERATED,
        version=1,
        lineage=lineage,
        revisions=(CandidateRevision(revision=1, content=content),),
    )


def _require_candidate_state(
    candidate: QuestionCandidate,
    current: CandidateState,
    target: CandidateState,
) -> None:
    if candidate.state is not current:
        raise InvalidCandidateTransitionError(candidate.state, target)


def record_candidate_validation(
    candidate: QuestionCandidate,
    evidence: ValidationEvidence,
    *,
    expected_version: int,
) -> QuestionCandidate:
    _require_expected_version(expected_version, candidate.version)
    _require_candidate_state(candidate, CandidateState.GENERATED, CandidateState.VALIDATED)
    if not isinstance(evidence, ValidationEvidence):
        raise CandidateInvariantError("evidence must be ValidationEvidence")
    if not evidence.passed:
        raise ValidationNotPassedError(candidate.candidate_id)
    return replace(
        candidate,
        state=CandidateState.VALIDATED,
        version=candidate.version + 1,
        validation=evidence,
    )


def start_candidate_review(
    candidate: QuestionCandidate,
    *,
    reviewer_id: UUID,
    expected_version: int,
) -> QuestionCandidate:
    _require_expected_version(expected_version, candidate.version)
    _require_candidate_state(candidate, CandidateState.VALIDATED, CandidateState.IN_REVIEW)
    _require_uuid(reviewer_id, "reviewer_id")
    version = candidate.version + 1
    record = ReviewRecord(
        action=ReviewAction.STARTED,
        reviewer_id=reviewer_id,
        candidate_version=version,
    )
    return replace(
        candidate,
        state=CandidateState.IN_REVIEW,
        version=version,
        review_history=(*candidate.review_history, record),
    )


def edit_candidate(
    candidate: QuestionCandidate,
    *,
    content: QuestionContent,
    reviewer_id: UUID,
    reason: str,
    expected_version: int,
) -> QuestionCandidate:
    _require_expected_version(expected_version, candidate.version)
    _require_candidate_state(candidate, CandidateState.IN_REVIEW, CandidateState.IN_REVIEW)
    _require_uuid(reviewer_id, "reviewer_id")
    _require_text(reason, "edit reason")
    if not isinstance(content, QuestionContent):
        raise CandidateInvariantError("content must be QuestionContent")
    generated_content = candidate.revisions[0].content
    if content.question_type != generated_content.question_type:
        raise CandidateInvariantError("reviewer cannot change generated question_type")
    if content.marks != generated_content.marks:
        raise CandidateInvariantError("reviewer cannot change generated marks")
    version = candidate.version + 1
    revision = CandidateRevision(
        revision=len(candidate.revisions) + 1,
        content=content,
        reviewer_id=reviewer_id,
        reason=reason,
    )
    record = ReviewRecord(
        action=ReviewAction.EDITED,
        reviewer_id=reviewer_id,
        candidate_version=version,
        reason=reason,
    )
    return replace(
        candidate,
        version=version,
        revisions=(*candidate.revisions, revision),
        review_history=(*candidate.review_history, record),
    )


def approve_candidate(
    candidate: QuestionCandidate,
    *,
    reviewer_id: UUID,
    expected_version: int,
    note: str | None = None,
) -> QuestionCandidate:
    _require_expected_version(expected_version, candidate.version)
    _require_candidate_state(candidate, CandidateState.IN_REVIEW, CandidateState.APPROVED)
    _require_uuid(reviewer_id, "reviewer_id")
    _require_optional_text(note, "approval note")
    version = candidate.version + 1
    record = ReviewRecord(
        action=ReviewAction.APPROVED,
        reviewer_id=reviewer_id,
        candidate_version=version,
        reason=note,
    )
    decision = ReviewDecision(
        state=CandidateState.APPROVED,
        reviewer_id=reviewer_id,
        candidate_version=version,
        reason=note,
    )
    return replace(
        candidate,
        state=CandidateState.APPROVED,
        version=version,
        review_history=(*candidate.review_history, record),
        decision=decision,
    )


def reject_candidate(
    candidate: QuestionCandidate,
    *,
    reviewer_id: UUID,
    reason: str,
    expected_version: int,
) -> QuestionCandidate:
    _require_expected_version(expected_version, candidate.version)
    _require_candidate_state(candidate, CandidateState.IN_REVIEW, CandidateState.REJECTED)
    _require_uuid(reviewer_id, "reviewer_id")
    _require_text(reason, "rejection reason")
    version = candidate.version + 1
    record = ReviewRecord(
        action=ReviewAction.REJECTED,
        reviewer_id=reviewer_id,
        candidate_version=version,
        reason=reason,
    )
    decision = ReviewDecision(
        state=CandidateState.REJECTED,
        reviewer_id=reviewer_id,
        candidate_version=version,
        reason=reason,
    )
    return replace(
        candidate,
        state=CandidateState.REJECTED,
        version=version,
        review_history=(*candidate.review_history, record),
        decision=decision,
    )


@dataclass(frozen=True, slots=True)
class PaperBlueprintReference:
    blueprint_id: str
    blueprint_version: str
    slot_ids: tuple[str, ...]
    paper_blueprint_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_text(self.blueprint_id, "blueprint_id")
        _require_text(self.blueprint_version, "blueprint_version")
        if not isinstance(self.slot_ids, tuple) or not self.slot_ids:
            raise CandidateInvariantError("slot_ids must be a non-empty tuple")
        if len(self.slot_ids) > MAX_PAPER_SLOTS:
            raise CandidateInvariantError(f"slot_ids cannot exceed {MAX_PAPER_SLOTS} entries")
        for slot_id in self.slot_ids:
            _require_text(slot_id, "slot_id")
        if len(set(self.slot_ids)) != len(self.slot_ids):
            raise CandidateInvariantError("slot_ids must be unique")
        if self.paper_blueprint_id is not None:
            _require_uuid(self.paper_blueprint_id, "paper_blueprint_id")


def _ordered_approved_candidates(
    blueprint: PaperBlueprintReference,
    candidates: tuple[QuestionCandidate, ...],
) -> tuple[QuestionCandidate, ...]:
    if not isinstance(blueprint, PaperBlueprintReference):
        raise CandidateInvariantError("blueprint must be PaperBlueprintReference")
    if not isinstance(candidates, tuple) or any(
        not isinstance(candidate, QuestionCandidate) for candidate in candidates
    ):
        raise PaperAssemblyError(
            AssemblyViolation.SLOT_COVERAGE,
            "candidates must be a tuple of QuestionCandidate values",
        )
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise PaperAssemblyError(
            AssemblyViolation.DUPLICATE_CANDIDATE,
            "one candidate identity cannot fill more than one blueprint slot",
        )
    if any(candidate.state is not CandidateState.APPROVED for candidate in candidates):
        raise PaperAssemblyError(
            AssemblyViolation.NOT_APPROVED,
            "every draft candidate must have a terminal approved review decision",
        )
    if any(
        candidate.lineage.blueprint_id != blueprint.blueprint_id
        or candidate.lineage.blueprint_version != blueprint.blueprint_version
        for candidate in candidates
    ):
        raise PaperAssemblyError(
            AssemblyViolation.BLUEPRINT_MISMATCH,
            "candidate blueprint identity/version must match the paper blueprint",
        )
    candidate_by_slot = {candidate.lineage.blueprint_slot_id: candidate for candidate in candidates}
    if (
        len(candidate_by_slot) != len(candidates)
        or set(candidate_by_slot) != set(blueprint.slot_ids)
        or len(candidates) != len(blueprint.slot_ids)
    ):
        raise PaperAssemblyError(
            AssemblyViolation.SLOT_COVERAGE,
            "candidates must cover every blueprint slot exactly once with no extra slots",
        )
    return tuple(candidate_by_slot[slot_id] for slot_id in blueprint.slot_ids)


@dataclass(frozen=True, slots=True)
class PaperDraft:
    paper_id: UUID
    version: int
    title: str
    blueprint: PaperBlueprintReference
    candidates: tuple[QuestionCandidate, ...]
    previous_version: int | None = None
    supersedes_content_hash: str | None = None
    state: PaperState = field(default=PaperState.DRAFT, init=False)

    def __post_init__(self) -> None:
        _require_uuid(self.paper_id, "paper_id")
        _require_paper_version(self.version)
        _require_bounded_text(self.title, "paper title", MAX_PAPER_TITLE_CHARACTERS)
        if not isinstance(self.blueprint, PaperBlueprintReference):
            raise CandidateInvariantError("blueprint must be PaperBlueprintReference")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(candidate, QuestionCandidate) for candidate in self.candidates
        ):
            raise PaperAssemblyError(
                AssemblyViolation.SLOT_COVERAGE,
                "candidates must be a tuple of QuestionCandidate values",
            )
        if self.version == 1:
            if self.previous_version is not None:
                raise CandidateInvariantError("the first draft cannot have a previous version")
        elif self.previous_version != self.version - 1:
            raise CandidateInvariantError("draft versions must advance exactly once")
        if self.supersedes_content_hash is not None:
            _require_sha256(self.supersedes_content_hash, "supersedes_content_hash")
        ordered = _ordered_approved_candidates(self.blueprint, self.candidates)
        if self.candidates != ordered:
            raise PaperAssemblyError(
                AssemblyViolation.SLOT_COVERAGE,
                "draft candidates must use deterministic blueprint slot order",
            )

    def assert_publishable(self) -> None:
        ordered = _ordered_approved_candidates(self.blueprint, self.candidates)
        if ordered != self.candidates:
            raise PaperAssemblyError(
                AssemblyViolation.SLOT_COVERAGE,
                "publishable candidates must remain in blueprint slot order",
            )


def assemble_paper_draft(
    *,
    paper_id: UUID,
    title: str,
    blueprint: PaperBlueprintReference,
    candidates: tuple[QuestionCandidate, ...],
) -> PaperDraft:
    ordered = _ordered_approved_candidates(blueprint, candidates)
    return PaperDraft(
        paper_id=paper_id,
        version=1,
        title=title,
        blueprint=blueprint,
        candidates=ordered,
    )


@dataclass(frozen=True, slots=True)
class PublishedQuestion:
    candidate_id: UUID
    candidate_version: int
    slot_id: str
    content: QuestionContent
    lineage: GenerationLineage
    validation: ValidationEvidence
    revisions: tuple[CandidateRevision, ...]
    review_history: tuple[ReviewRecord, ...]
    decision: ReviewDecision

    def __post_init__(self) -> None:
        _require_uuid(self.candidate_id, "candidate_id")
        _require_positive_integer(self.candidate_version, "candidate_version")
        _require_text(self.slot_id, "slot_id")
        if self.slot_id != self.lineage.blueprint_slot_id:
            raise CandidateInvariantError("published slot must match generation lineage")
        if self.revisions[-1].content != self.content:
            raise CandidateInvariantError("published content must be the final candidate revision")
        if not self.validation.passed:
            raise CandidateInvariantError("published question must retain passed validation")
        if self.decision.state is not CandidateState.APPROVED:
            raise CandidateInvariantError("published question must retain reviewer approval")
        if self.decision.candidate_version != self.candidate_version:
            raise CandidateInvariantError("published candidate version must match its decision")


_PUBLISH_CAPABILITY = object()
_ARCHIVE_CAPABILITY = object()


@dataclass(frozen=True, slots=True)
class PublishedPaperSnapshot:
    paper_id: UUID
    version: int
    title: str
    blueprint: PaperBlueprintReference
    questions: tuple[PublishedQuestion, ...]
    published_by: UUID
    previous_version: int | None = None
    supersedes_content_hash: str | None = None
    state: PaperState = field(default=PaperState.PUBLISHED, init=False)
    content_hash: str = field(init=False)
    _service_capability: InitVar[object | None] = None

    def __post_init__(self, _service_capability: object | None) -> None:
        if _service_capability is not _PUBLISH_CAPABILITY:
            raise ServiceBoundaryRequiredError(PaperState.PUBLISHED)
        _require_uuid(self.paper_id, "paper_id")
        _require_paper_version(self.version)
        _require_bounded_text(self.title, "paper title", MAX_PAPER_TITLE_CHARACTERS)
        _require_uuid(self.published_by, "published_by")
        if not isinstance(self.blueprint, PaperBlueprintReference):
            raise CandidateInvariantError("blueprint must be PaperBlueprintReference")
        if not isinstance(self.questions, tuple) or any(
            not isinstance(question, PublishedQuestion) for question in self.questions
        ):
            raise PaperAssemblyError(
                AssemblyViolation.SLOT_COVERAGE,
                "published questions must be immutable PublishedQuestion values",
            )
        if tuple(question.slot_id for question in self.questions) != self.blueprint.slot_ids:
            raise PaperAssemblyError(
                AssemblyViolation.SLOT_COVERAGE,
                "published questions must retain exact ordered blueprint coverage",
            )
        if any(
            question.lineage.blueprint_id != self.blueprint.blueprint_id
            or question.lineage.blueprint_version != self.blueprint.blueprint_version
            for question in self.questions
        ):
            raise PaperAssemblyError(
                AssemblyViolation.BLUEPRINT_MISMATCH,
                "published questions must retain the draft blueprint identity and version",
            )
        candidate_ids = tuple(question.candidate_id for question in self.questions)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise PaperAssemblyError(
                AssemblyViolation.DUPLICATE_CANDIDATE,
                "published questions cannot duplicate a candidate identity",
            )
        if self.version == 1:
            if self.previous_version is not None:
                raise CandidateInvariantError(
                    "the first publication cannot have a previous version"
                )
        elif self.previous_version != self.version - 1:
            raise CandidateInvariantError("published versions must advance exactly once")
        if self.supersedes_content_hash is not None:
            _require_sha256(self.supersedes_content_hash, "supersedes_content_hash")
        object.__setattr__(self, "content_hash", self.recompute_content_hash())

    def recompute_content_hash(self) -> str:
        serialized = json.dumps(
            _published_content_payload(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()


@dataclass(frozen=True, slots=True)
class ArchivedPaperSnapshot:
    publication: PublishedPaperSnapshot
    archived_by: UUID
    reason: str
    state: PaperState = field(default=PaperState.ARCHIVED, init=False)
    _service_capability: InitVar[object | None] = None

    def __post_init__(self, _service_capability: object | None) -> None:
        if _service_capability is not _ARCHIVE_CAPABILITY:
            raise ServiceBoundaryRequiredError(PaperState.ARCHIVED)
        if not isinstance(self.publication, PublishedPaperSnapshot):
            raise CandidateInvariantError("publication must be PublishedPaperSnapshot")
        _require_uuid(self.archived_by, "archived_by")
        _require_bounded_text(
            self.reason,
            "archive reason",
            MAX_ARCHIVE_REASON_CHARACTERS,
        )

    @property
    def paper_id(self) -> UUID:
        return self.publication.paper_id

    @property
    def version(self) -> int:
        return self.publication.version

    @property
    def content_hash(self) -> str:
        return self.publication.content_hash


def _published_question(candidate: QuestionCandidate) -> PublishedQuestion:
    if candidate.state is not CandidateState.APPROVED or candidate.validation is None:
        raise PaperAssemblyError(
            AssemblyViolation.NOT_APPROVED,
            "only approved, validated candidates can become published questions",
        )
    if candidate.decision is None:
        raise CandidateInvariantError("approved candidate must retain its review decision")
    return PublishedQuestion(
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        slot_id=candidate.lineage.blueprint_slot_id,
        content=candidate.content,
        lineage=candidate.lineage,
        validation=candidate.validation,
        revisions=candidate.revisions,
        review_history=candidate.review_history,
        decision=candidate.decision,
    )


def _publish_paper(
    draft: PaperDraft,
    *,
    published_by: UUID,
    expected_version: int,
) -> PublishedPaperSnapshot:
    if not isinstance(draft, PaperDraft):
        state = getattr(draft, "state", PaperState.PUBLISHED)
        raise InvalidPaperTransitionError(state, PaperState.PUBLISHED)
    if draft.state is not PaperState.DRAFT:
        raise InvalidPaperTransitionError(draft.state, PaperState.PUBLISHED)
    _require_expected_version(expected_version, draft.version)
    draft.assert_publishable()
    return PublishedPaperSnapshot(
        paper_id=draft.paper_id,
        version=draft.version,
        title=draft.title,
        blueprint=draft.blueprint,
        questions=tuple(_published_question(candidate) for candidate in draft.candidates),
        published_by=published_by,
        previous_version=draft.previous_version,
        supersedes_content_hash=draft.supersedes_content_hash,
        _service_capability=_PUBLISH_CAPABILITY,
    )


def _revise_paper(
    source: PaperDraft | PublishedPaperSnapshot | ArchivedPaperSnapshot,
    *,
    candidates: tuple[QuestionCandidate, ...],
    expected_version: int,
    title: str | None,
) -> PaperDraft:
    if isinstance(source, ArchivedPaperSnapshot):
        raise InvalidPaperTransitionError(PaperState.ARCHIVED, PaperState.DRAFT)
    if not isinstance(source, PublishedPaperSnapshot):
        current_state = getattr(source, "state", PaperState.PUBLISHED)
        raise InvalidPaperTransitionError(current_state, PaperState.DRAFT)
    if source.state is not PaperState.PUBLISHED:
        raise InvalidPaperTransitionError(source.state, PaperState.DRAFT)
    _require_expected_version(expected_version, source.version)
    ordered = _ordered_approved_candidates(source.blueprint, candidates)
    next_title = (
        source.title
        if title is None
        else _require_bounded_text(title, "paper title", MAX_PAPER_TITLE_CHARACTERS)
    )
    return PaperDraft(
        paper_id=source.paper_id,
        version=source.version + 1,
        title=next_title,
        blueprint=source.blueprint,
        candidates=ordered,
        previous_version=source.version,
        supersedes_content_hash=source.content_hash,
    )


def _archive_paper(
    publication: PublishedPaperSnapshot | ArchivedPaperSnapshot,
    *,
    archived_by: UUID,
    reason: str,
    expected_version: int,
) -> ArchivedPaperSnapshot:
    if isinstance(publication, ArchivedPaperSnapshot):
        raise InvalidPaperTransitionError(PaperState.ARCHIVED, PaperState.ARCHIVED)
    if not isinstance(publication, PublishedPaperSnapshot):
        raise InvalidPaperTransitionError(PaperState.DRAFT, PaperState.ARCHIVED)
    if publication.state is not PaperState.PUBLISHED:
        raise InvalidPaperTransitionError(publication.state, PaperState.ARCHIVED)
    _require_expected_version(expected_version, publication.version)
    return ArchivedPaperSnapshot(
        publication=publication,
        archived_by=archived_by,
        reason=reason,
        _service_capability=_ARCHIVE_CAPABILITY,
    )


def _question_content_payload(content: QuestionContent) -> dict[str, object]:
    return {
        "answer": content.answer,
        "explanation": content.explanation,
        "marking_guide": list(content.marking_guide),
        "marks": content.marks,
        "options": [
            {"option_id": option.option_id, "text": option.text} for option in content.options
        ],
        "question_type": content.question_type,
        "stem": content.stem,
    }


def _lineage_payload(lineage: GenerationLineage) -> dict[str, object]:
    return {
        "blueprint_id": lineage.blueprint_id,
        "blueprint_slot_id": lineage.blueprint_slot_id,
        "blueprint_version": lineage.blueprint_version,
        "generation_attempt_id": str(lineage.generation_attempt_id),
        "generation_id": str(lineage.generation_id),
        "model_version": lineage.model_version,
        "prompt_version": lineage.prompt_version,
        "provenance": [
            {
                "chunk_id": item.chunk_id,
                "page_number": item.page_number,
                "source_document_id": item.source_document_id,
                "source_version": item.source_version,
            }
            for item in lineage.provenance
        ],
        "provider": lineage.provider,
        "retrieval_version": lineage.retrieval_version,
        "schema_version": lineage.schema_version,
    }


def _published_content_payload(snapshot: PublishedPaperSnapshot) -> dict[str, object]:
    return {
        "blueprint": {
            "blueprint_id": snapshot.blueprint.blueprint_id,
            "blueprint_version": snapshot.blueprint.blueprint_version,
            "paper_blueprint_id": (
                str(snapshot.blueprint.paper_blueprint_id)
                if snapshot.blueprint.paper_blueprint_id is not None
                else None
            ),
            "slot_ids": list(snapshot.blueprint.slot_ids),
        },
        "paper_id": str(snapshot.paper_id),
        "paper_version": snapshot.version,
        "questions": [
            {
                "candidate_id": str(question.candidate_id),
                "candidate_version": question.candidate_version,
                "content": _question_content_payload(question.content),
                "content_revision": question.revisions[-1].revision,
                "decision": {
                    "candidate_version": question.decision.candidate_version,
                    "reason": question.decision.reason,
                    "reviewer_id": str(question.decision.reviewer_id),
                    "state": question.decision.state.value,
                },
                "lineage": _lineage_payload(question.lineage),
                "review_history": [
                    {
                        "action": record.action.value,
                        "candidate_version": record.candidate_version,
                        "reason": record.reason,
                        "reviewer_id": str(record.reviewer_id),
                    }
                    for record in question.review_history
                ],
                "revisions": [
                    {
                        "content": _question_content_payload(revision.content),
                        "reason": revision.reason,
                        "reviewer_id": (
                            str(revision.reviewer_id) if revision.reviewer_id is not None else None
                        ),
                        "revision": revision.revision,
                    }
                    for revision in question.revisions
                ],
                "slot_id": question.slot_id,
                "validation": {
                    "finding_refs": list(question.validation.finding_refs),
                    "passed": question.validation.passed,
                    "validated_revision": question.validation.validated_revision,
                    "validation_run_id": str(question.validation.validation_run_id),
                    "validator_version": question.validation.validator_version,
                },
            }
            for question in snapshot.questions
        ],
        "schema": "published-paper.v1",
        "title": snapshot.title,
    }
