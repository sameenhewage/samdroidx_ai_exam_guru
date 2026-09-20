"""Source V2 API contracts.

Shaped for the review screen: everything a reviewer needs to decide about one
region arrives together — the machine's reading, where it is on the page, which
reader produced it, and where the readers disagreed.
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


class ReaderEvidence(SourceV2Model):
    reader: str
    text: str
    abstained: bool = False
    failure: str | None = None
    seconds: float = 0.0


class RegionView(SourceV2Model):
    region_id: str
    region_type: RegionTypeName
    candidate_id: UUID
    revision: int
    origin: Literal["machine", "human-correction"]
    text: str
    abstained: bool
    chosen_reader: str | None
    reason: str
    critical_conflict: bool
    agreement_ratio: float
    disagreement: dict = Field(default_factory=dict)
    state: RegionStateName
    bbox: list[int] | None = None
    verified_text: str | None = None
    readers: list[ReaderEvidence] = Field(default_factory=list)
    #: D18. What kind of source this is. The machine proposes, a human decides.
    source_kind: SourceKindName = "undecided"
    proposed_source_kind: SourceKindName | None = None
    crop_sha256: str | None = None


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


class ReaderResultInput(SourceV2Model):
    region_id: str = Field(max_length=64)
    reader: str = Field(max_length=64)
    text: str = Field(max_length=200000)
    abstained: bool = False
    failure: str | None = Field(default=None, max_length=400)
    seconds: float = 0.0
    signals: dict = Field(default_factory=dict)


class CandidateInput(SourceV2Model):
    region_id: str = Field(max_length=64)
    region_type: RegionTypeName
    text: str = Field(max_length=200000)
    abstained: bool = False
    chosen_reader: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=400)
    critical_conflict: bool = False
    agreement_ratio: float = 1.0
    disagreement: dict = Field(default_factory=dict)
    #: The Source Factory does not classify. When omitted the service proposes
    #: a kind from region type and whether any text was transcribed.
    source_kind: SourceKindName | None = None
    crop_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ImportPageRequest(SourceV2Model):
    """One page of Source Factory output, handed to the Studio.

    Carries geometry and proposed readings only. Nothing here can mark anything
    verified: that remains a human act performed against the original page.
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
    reader_results: list[ReaderResultInput] = Field(default_factory=list, max_length=8192)


class ImportPageResponse(SourceV2Model):
    page_id: UUID
    page_number: int
    regions: int
    reader_rows: int
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


class ReclassifyRequest(SourceV2Model):
    """The reviewer disagrees with the proposed kind.

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
