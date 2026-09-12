import unicodedata
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


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


class EvaluationReferenceSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    preview_id: UUID
    expected_version: int = Field(strict=True, ge=0)
    text: str = Field(strict=True, max_length=100000)
    blank_reference: bool = Field(default=False, strict=True)
    compared_with_original: ExplicitConfirmation
    human_reviewed: ExplicitConfirmation
    reason: str = Field(strict=True, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_reference(self) -> "EvaluationReferenceSaveRequest":
        try:
            self.text.encode("utf-8")
        except UnicodeError:
            raise ValueError("reference text must be valid Unicode") from None
        if any(
            unicodedata.category(character) == "Cc" and character not in "\n\r\t"
            for character in self.text
        ):
            raise ValueError("reference text contains unsafe characters")
        if (self.blank_reference and self.text != "") or (
            not self.blank_reference and not self.text.strip()
        ):
            raise ValueError("blank reference requires explicit empty-page confirmation")
        if not self.reason.strip() or any(
            unicodedata.category(character) in {"Cc", "Cs"} for character in self.reason
        ):
            raise ValueError("reference reason contains unsafe characters")
        return self


class EvaluationReferenceResponse(BaseModel):
    id: UUID
    preview_id: UUID
    benchmark_id: UUID
    document_id: UUID
    page_number: int
    version: int
    text: str
    normalized_text: str
    text_sha256: str
    normalized_sha256: str
    blank_reference: bool
    reviewer_id: UUID
    reviewed_at: datetime
    reason: str
    evaluation_only: Literal[True] = True


class EvaluationPreviewResponse(BaseModel):
    id: UUID
    benchmark_id: UUID
    document_id: UUID
    document_title: str
    page_number: int
    source_checksum_sha256: str
    image_sha256: str
    image_width: int = Field(ge=1, le=16000)
    image_height: int = Field(ge=1, le=16000)
    preview_url: str
    language: str
    reference_version: int
    latest_reference: EvaluationReferenceResponse | None
    evaluation_only: Literal[True] = True


class EvaluationReferenceProgress(BaseModel):
    selected_pages: int = Field(ge=0)
    referenced_pages: int = Field(ge=0)
    pending_pages: int = Field(ge=0)
    evaluation_only: Literal[True] = True


class SourceBenchmarkPageView(BaseModel):
    document_id: UUID
    document_title: str
    page_number: int
    categories: list[str]
    state: str
    ground_truth_versions: int
    evaluation_reference_versions: int = 0


class SourceBenchmarkResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    pages: list[SourceBenchmarkPageView]
    adjudicated_pages: int
    pending_pages: int
    accuracy_status: Literal["awaiting_human_adjudication", "references_available"]
    evaluation_references: EvaluationReferenceProgress | None = None
