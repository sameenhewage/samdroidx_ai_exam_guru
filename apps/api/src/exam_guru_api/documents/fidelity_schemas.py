from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _explicit_confirmation(value: object) -> Literal[True]:
    if value is not True:
        raise ValueError("an explicit true confirmation is required")
    return True


ExplicitConfirmation = Annotated[Literal[True], BeforeValidator(_explicit_confirmation)]


class PageReviewProgress(BaseModel):
    total_pages: int = Field(ge=0)
    processed_pages: int = Field(ge=0)
    verified_pages: int = Field(ge=0)
    excluded_pages: int = Field(ge=0)
    flagged_pages: int = Field(ge=0)
    remaining_pages: int = Field(ge=0)


class ReviewCandidateSummary(BaseModel):
    id: UUID
    method: str
    created_at: datetime
    text_sha256: str
    is_current: bool


class PageReviewView(BaseModel):
    page_number: int = Field(ge=1)
    state: Literal["pending", "needs_review", "verified", "excluded", "processing", "failed"]
    version: int = Field(ge=0)
    candidate_id: UUID | None
    system_text: str
    language: str
    can_confirm: bool
    risk_codes: list[str]
    preview_url: str
    provenance: dict[str, object]
    diagnostics: dict[str, object]
    history: list[ReviewCandidateSummary]


class PageReviewWorkspaceResponse(BaseModel):
    document_id: UUID
    document_title: str
    language: str
    metadata_review_required: bool
    source_active: bool
    ready_for_ai: bool
    progress: PageReviewProgress
    page: PageReviewView | None
    previous_flagged_page: int | None
    next_flagged_page: int | None


class PageConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)
    candidate_id: UUID
    compared_with_original: ExplicitConfirmation
    reason: str = Field(min_length=1, max_length=2000)


class PageEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)
    text: str = Field(min_length=1, max_length=100000)
    reason: str = Field(min_length=1, max_length=2000)


class PageExcludeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)
    confirm_exclusion: ExplicitConfirmation
    reason: str = Field(min_length=1, max_length=2000)


class PageRereadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(strict=True, ge=0)


class SourceReadJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    page_number: int | None
    status: str
    next_page: int = Field(ge=1)
    version: int = Field(ge=0)
    failure_code: str | None


class PageReviewMutationResponse(BaseModel):
    document_id: UUID
    page_number: int
    version: int
    state: str
    candidate_id: UUID | None


class BenchmarkPageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    page_number: int = Field(strict=True, ge=1)
    categories: list[str] = Field(min_length=1, max_length=32)


class SourceBenchmarkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    pages: list[BenchmarkPageSelection] = Field(min_length=1, max_length=1000)
    selection: dict[str, object]


class SourceBenchmarkPageView(BaseModel):
    document_id: UUID
    document_title: str
    page_number: int
    categories: list[str]
    state: str
    ground_truth_versions: int


class SourceBenchmarkResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    pages: list[SourceBenchmarkPageView]
    adjudicated_pages: int
    pending_pages: int
    accuracy_status: Literal["awaiting_human_adjudication", "references_available"]
