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
