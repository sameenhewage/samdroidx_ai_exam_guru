"""Source V2 API contracts.

Shaped for the review screen: everything a reviewer needs to decide about one
region arrives together — the single machine reading, where it is on the page,
the canonical crop it came from, and what the deterministic checks noticed.

There is one machine source reader, the executing AI agent looking at the
canonical crop. The reviewer sees one proposal, never an ensemble.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RegionTypeName = Literal["text", "heading", "figure", "table", "decorative", "unknown"]
RegionStateName = Literal["unverified", "verified", "excluded"]
SourceKindName = Literal["text_only", "visual_only", "visual_with_text", "decorative", "undecided"]


class SourceV2Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TechnicalEvidence(SourceV2Model):
    """Everything the machine noticed, in one place a card can collapse.

    Provenance, deterministic findings, declared uncertainty and checksums are
    what an auditor needs and what a teacher must not have to wade through to
    reach the picture. Grouping them makes "hide this by default" a property
    of the contract rather than a habit of one component.
    """

    #: The full stored reason string, never truncated or reworded.
    reason: str
    #: Deterministic findings lifted out of the reason, where recognisable.
    findings: list[str] = Field(default_factory=list)
    #: Codes the reader declared about its own reading: `spacing-doubt`,
    #: `no-text`, `layout-doubt`.
    uncertainty: list[str] = Field(default_factory=list)
    abstained: bool = False
    proposed_source_kind: SourceKindName | None = None
    origin: Literal["machine", "human-correction"] = "machine"
    revision: int = 1
    crop_sha256: str | None = None


class RegionView(SourceV2Model):
    region_id: str
    region_type: RegionTypeName
    candidate_id: UUID
    revision: int
    origin: Literal["machine", "human-correction"]
    #: The exact text printed **inside the crop**. Source, and only source.
    #: Never a description, never a diagnostic, never a caption from the page.
    text: str
    abstained: bool
    reason: str
    state: RegionStateName
    bbox: list[int] | None = None
    verified_text: str | None = None
    #: D18. What kind of source this is. The machine proposes, a human decides.
    source_kind: SourceKindName = "undecided"
    proposed_source_kind: SourceKindName | None = None
    crop_sha256: str | None = None
    #: What the picture shows, in the language of the material. **Derived
    #: knowledge, never Verified Source Content** (D18).
    visual_description: str | None = None
    #: Labels legible inside the crop, as separate addressable strings.
    detected_labels: list[str] = Field(default_factory=list)
    #: Where to fetch the canonical crop itself. Null when none is recorded.
    crop_url: str | None = None
    #: Diagnostics, grouped so the review card can keep them out of the way.
    technical_evidence: TechnicalEvidence


class PageProgress(SourceV2Model):
    total: int
    unverified: int
    verified: int
    excluded: int
    resolved: int


class PageView(SourceV2Model):
    page_id: UUID
    document_id: UUID
    page_number: int
    image_sha256: str
    width: int
    height: int
    dpi: float
    language: str
    detector_version: str
    progress: PageProgress
    regions: list[RegionView]


class CandidateInput(SourceV2Model):
    region_id: str = Field(max_length=64)
    region_type: RegionTypeName
    text: str = Field(max_length=200000)
    abstained: bool = False
    reason: str = Field(default="", max_length=400)
    #: The Source Factory does not classify. When omitted the service proposes
    #: a kind from region type and whether any text was transcribed.
    source_kind: SourceKindName | None = None
    crop_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ImportPageRequest(SourceV2Model):
    """One page of Source Factory output, handed to the Studio.

    Carries geometry and the one primary reading per region. Nothing here can
    mark anything verified: that remains a human act performed against the
    original page.
    """

    document_id: UUID
    page_number: int = Field(ge=1)
    language: str = Field(max_length=32)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    dpi: float = Field(ge=72, le=1200)
    detector_version: str = Field(max_length=128)
    layout: dict
    candidates: list[CandidateInput] = Field(max_length=2048)


class ImportPageResponse(SourceV2Model):
    page_id: UUID
    page_number: int
    regions: int
    reused: bool
    superseded: int = 0
    verifications_withdrawn: int = 0


class ConfirmRequest(SourceV2Model):
    candidate_id: UUID
    revision: int = Field(ge=1)
    compared_with_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    note: str | None = Field(default=None, max_length=2000)


class CorrectRequest(SourceV2Model):
    candidate_id: UUID
    revision: int = Field(ge=1)
    corrected_text: str = Field(min_length=1, max_length=20000)
    note: str | None = Field(default=None, max_length=2000)


class ConfirmVisualRequest(SourceV2Model):
    """Confirm an educational figure *as a figure* (D18).

    Separate from Confirm because the reviewer asserts something different:
    not "this text is right" but "this region is source visual". The kind is
    explicit, so the reviewer settles visual-only versus visual-with-text
    rather than the machine.
    """

    candidate_id: UUID
    revision: int = Field(ge=1)
    compared_with_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_kind: Literal["visual_only", "visual_with_text"] = "visual_only"
    #: Required for visual_with_text: the printed labels, verified as text.
    text: str | None = Field(default=None, max_length=20000)
    note: str | None = Field(default=None, max_length=2000)


class DescribeRequest(SourceV2Model):
    """Save what the picture shows. Derived knowledge, not a verification.

    Deliberately carries no `compared_with_image_sha256`: that field exists so
    a confirmation names the evidence a human compared, and describing a
    figure is not a confirmation. Sending one would imply a review decision
    that this endpoint must never make.
    """

    candidate_id: UUID
    revision: int = Field(ge=1)
    #: In the language of the material. Validated before it is stored.
    visual_description: str = Field(min_length=1, max_length=4000)
    #: Labels legible **inside the crop**. Each is checked against the text
    #: transcribed from that same crop.
    detected_labels: list[str] = Field(default_factory=list, max_length=64)


class ReclassifyRequest(SourceV2Model):
    """The reviewer overrules the proposed kind.

    Changing what a region *is* is a human decision and lands as its own
    review event; the machine never applies it silently.
    """

    candidate_id: UUID
    revision: int = Field(ge=1)
    source_kind: SourceKindName
    note: str = Field(min_length=1, max_length=2000)


class ExcludeRequest(SourceV2Model):
    candidate_id: UUID
    revision: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


class RegionMutationResponse(SourceV2Model):
    region: RegionView
    progress: PageProgress


class DocumentGateView(SourceV2Model):
    document_id: UUID
    usable: bool
    reason: str | None = None
    pages: dict[int, dict[str, int]]
