"""Source V2 domain rules. No SQLAlchemy, no FastAPI, no provider types.

Everything here is a rule that must hold regardless of how it is stored or
served, so it is testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RegionType(StrEnum):
    TEXT = "text"
    HEADING = "heading"
    FIGURE = "figure"
    TABLE = "table"
    DECORATIVE = "decorative"
    UNKNOWN = "unknown"


class ReviewAction(StrEnum):
    CONFIRM = "confirm"
    CORRECT = "correct"
    EXCLUDE = "exclude"
    REOPEN = "reopen"


class RegionState(StrEnum):
    """Where one region sits in the human gate."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    EXCLUDED = "excluded"


class SourceV2Error(Exception):
    """A Source V2 rule was violated."""


class NotVerifiedError(SourceV2Error):
    """Downstream use was attempted without current Verified Source Content."""


class StaleReviewError(SourceV2Error):
    """A decision was made against a candidate or revision that is no longer current."""


@dataclass(frozen=True)
class MachineCandidate:
    """What the machine proposes for one region. Never trust.

    One reading, by the executing agent, from the canonical crop. There is no
    competing reader, so there is nothing here to choose between.
    """

    candidate_id: UUID
    region_id: str
    region_type: RegionType
    text: str
    abstained: bool
    revision: int
    parent_candidate_id: UUID | None = None

    @property
    def confirmable(self) -> bool:
        """An abstention has nothing to confirm; it must be corrected or excluded.

        This is what stops a reviewer from rubber-stamping an empty reading
        into Verified Source Content.
        """

        return not self.abstained and bool(self.text.strip())


@dataclass(frozen=True)
class ReviewEvent:
    """Append-only. A decision is never edited and never deleted."""

    event_id: UUID
    region_id: str
    action: ReviewAction
    candidate_id: UUID
    candidate_revision: int
    reviewer_id: UUID
    at: datetime
    compared_with_image_sha256: str
    note: str | None = None
    corrected_text: str | None = None


@dataclass(frozen=True)
class VerifiedRegion:
    """Immutable verified content for one region, bound to what was compared."""

    region_id: str
    text: str
    candidate_id: UUID
    candidate_revision: int
    reviewer_id: UUID
    verified_at: datetime
    image_sha256: str


@dataclass
class PageReview:
    """The review state of one rendered page. A page is the unit of resolution."""

    document_id: str
    page_number: int
    image_sha256: str
    candidates: dict[str, MachineCandidate] = field(default_factory=dict)
    states: dict[str, RegionState] = field(default_factory=dict)
    verified: dict[str, VerifiedRegion] = field(default_factory=dict)
    events: list[ReviewEvent] = field(default_factory=list)

    # -- queries -----------------------------------------------------------

    def state_of(self, region_id: str) -> RegionState:
        return self.states.get(region_id, RegionState.UNVERIFIED)

    @property
    def unresolved(self) -> list[str]:
        """Regions that are neither verified nor explicitly excluded."""

        return sorted(
            region_id
            for region_id in self.candidates
            if self.state_of(region_id) is RegionState.UNVERIFIED
        )

    @property
    def resolved(self) -> bool:
        """Every region on this page has been decided: verified or excluded.

        A page whose regions were all excluded *is* resolved — the reviewer
        decided it. It simply contributes no source, which is why "at least one
        verified page" is a separate rule enforced over the whole document.
        """

        return not self.unresolved

    @property
    def carries_source(self) -> bool:
        return any(state is RegionState.VERIFIED for state in self.states.values())

    # -- decisions ---------------------------------------------------------

    def _candidate(self, region_id: str, candidate_id: UUID, revision: int) -> MachineCandidate:
        candidate = self.candidates.get(region_id)
        if candidate is None:
            raise SourceV2Error(f"no candidate for region {region_id}")
        if candidate.candidate_id != candidate_id or candidate.revision != revision:
            raise StaleReviewError(
                f"region {region_id} moved on: the current candidate is "
                f"{candidate.candidate_id} revision {candidate.revision}"
            )
        return candidate

    def confirm(
        self,
        *,
        region_id: str,
        candidate_id: UUID,
        revision: int,
        reviewer_id: UUID,
        at: datetime,
        compared_with_image_sha256: str,
        note: str | None = None,
    ) -> VerifiedRegion:
        """Accept the machine's reading after comparing it with the original page."""

        candidate = self._candidate(region_id, candidate_id, revision)
        if compared_with_image_sha256 != self.image_sha256:
            raise StaleReviewError(
                "confirmation must cite the rendered page that was actually compared"
            )
        if not candidate.confirmable:
            raise SourceV2Error(
                f"region {region_id} abstained or is empty; correct or exclude it instead"
            )
        verified = VerifiedRegion(
            region_id=region_id,
            text=candidate.text,
            candidate_id=candidate.candidate_id,
            candidate_revision=candidate.revision,
            reviewer_id=reviewer_id,
            verified_at=at,
            image_sha256=self.image_sha256,
        )
        self.verified[region_id] = verified
        self.states[region_id] = RegionState.VERIFIED
        self._record(
            region_id,
            ReviewAction.CONFIRM,
            candidate,
            reviewer_id,
            at,
            note=note,
        )
        return verified

    def correct(
        self,
        *,
        region_id: str,
        candidate_id: UUID,
        revision: int,
        reviewer_id: UUID,
        at: datetime,
        corrected_text: str,
        child_candidate_id: UUID,
        note: str | None = None,
    ) -> MachineCandidate:
        """Replace the reading with the reviewer's text.

        A correction produces an **unverified child candidate**. It does not
        verify anything: the reviewer must still confirm the corrected reading
        against the page, which keeps typing and verifying separate acts.
        """

        candidate = self._candidate(region_id, candidate_id, revision)
        child = MachineCandidate(
            candidate_id=child_candidate_id,
            region_id=region_id,
            region_type=candidate.region_type,
            text=corrected_text,
            abstained=False,
            revision=candidate.revision + 1,
            parent_candidate_id=candidate.candidate_id,
        )
        self.candidates[region_id] = child
        self.states[region_id] = RegionState.UNVERIFIED
        self.verified.pop(region_id, None)
        self._record(
            region_id,
            ReviewAction.CORRECT,
            candidate,
            reviewer_id,
            at,
            note=note,
            corrected_text=corrected_text,
        )
        return child

    def exclude(
        self,
        *,
        region_id: str,
        candidate_id: UUID,
        revision: int,
        reviewer_id: UUID,
        at: datetime,
        note: str,
    ) -> None:
        """Take a region out of use without destroying its evidence."""

        candidate = self._candidate(region_id, candidate_id, revision)
        if not note.strip():
            raise SourceV2Error("an exclusion must say why")
        self.states[region_id] = RegionState.EXCLUDED
        self.verified.pop(region_id, None)
        self._record(region_id, ReviewAction.EXCLUDE, candidate, reviewer_id, at, note=note)

    def _record(
        self,
        region_id: str,
        action: ReviewAction,
        candidate: MachineCandidate,
        reviewer_id: UUID,
        at: datetime,
        *,
        note: str | None = None,
        corrected_text: str | None = None,
    ) -> None:
        from uuid import uuid4

        self.events.append(
            ReviewEvent(
                event_id=uuid4(),
                region_id=region_id,
                action=action,
                candidate_id=candidate.candidate_id,
                candidate_revision=candidate.revision,
                reviewer_id=reviewer_id,
                at=at,
                compared_with_image_sha256=self.image_sha256,
                note=note,
                corrected_text=corrected_text,
            )
        )


# --- the hard invariant -------------------------------------------------------


@dataclass(frozen=True)
class DocumentResolution:
    """Whether a whole document may be used downstream at all."""

    document_id: str
    pages: dict[int, PageReview]

    @property
    def unresolved_pages(self) -> list[int]:
        return sorted(number for number, page in self.pages.items() if not page.resolved)

    @property
    def verified_pages(self) -> list[int]:
        return sorted(number for number, page in self.pages.items() if page.carries_source)


def require_verified_source(resolution: DocumentResolution, *, purpose: str) -> None:
    """The gate. Call before analysis, knowledge, embeddings, RAG or generation.

    A verified page cannot carry an unresolved sibling across the line: a
    document is usable only when every page is verified or explicitly excluded
    **and** at least one page is actually verified.
    """

    if not resolution.pages:
        raise NotVerifiedError(f"{purpose} refused: document {resolution.document_id} has no pages")
    unresolved = resolution.unresolved_pages
    if unresolved:
        raise NotVerifiedError(
            f"{purpose} refused: document {resolution.document_id} has unresolved pages "
            f"{unresolved}; every page must be verified or explicitly excluded"
        )
    if not resolution.verified_pages:
        raise NotVerifiedError(
            f"{purpose} refused: document {resolution.document_id} has no verified page; "
            "excluding everything is not verification"
        )
