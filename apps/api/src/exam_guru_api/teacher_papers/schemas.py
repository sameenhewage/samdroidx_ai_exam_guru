from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from exam_guru_api.papers.schemas import (
    QuestionContentRequest,
    QuestionContentResponse,
    ReviewCandidateApproveRequest,
    ReviewCandidateRejectRequest,
    ReviewCandidateStartRequest,
)
from exam_guru_api.teacher_papers.domain import (
    MAX_TEACHER_DURATION_MINUTES,
    MAX_TEACHER_QUESTIONS,
    PaperDifficulty,
)

CodeText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=64),
]
MediumCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, min_length=2, max_length=16),
]
ExpectedAggregateVersion = Annotated[int, Field(strict=True, ge=0, le=100_000)]
BoundedReason = Annotated[str, Field(min_length=1, max_length=1_024)]
FriendlyValidationStatus = Literal["ready", "needs_attention", "failed_check"]
TeacherPaperStatus = Literal[
    "preparing",
    "generating",
    "checking_answers",
    "ready_for_review",
    "failed",
]
TechnicalFindingStatus = Literal["pass", "warn", "fail"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TeacherPaperTargetRequest(_StrictModel):
    grade: Annotated[int, Field(strict=True, ge=1, le=13)]
    medium: MediumCode
    subject: CodeText
    assessment_programme: CodeText | None = None


class FullSubjectScopeRequest(_StrictModel):
    kind: Literal["full_subject"]


class LessonRangeScopeRequest(_StrictModel):
    kind: Literal["lesson_range"]
    start_lesson: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    end_lesson: Annotated[int, Field(strict=True, ge=1, le=10_000)]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_lesson < self.start_lesson:
            raise ValueError("end_lesson must be greater than or equal to start_lesson")
        return self


TeacherPaperScopeRequest = Annotated[
    FullSubjectScopeRequest | LessonRangeScopeRequest,
    Field(discriminator="kind"),
]


class TeacherPaperSettingsRequest(_StrictModel):
    question_count: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_TEACHER_QUESTIONS),
    ]
    duration_minutes: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_TEACHER_DURATION_MINUTES),
    ]
    difficulty: PaperDifficulty


class TeacherPaperJobCreateRequest(_StrictModel):
    target: TeacherPaperTargetRequest
    scope: TeacherPaperScopeRequest
    settings: TeacherPaperSettingsRequest


class TeacherPaperAdvanceRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class TeacherPaperRetryRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class AssessmentProgrammeOption(_FrozenStrictModel):
    code: str
    grade: int
    label: str


class MediumOption(_FrozenStrictModel):
    code: str
    label: str


class UnitOption(_FrozenStrictModel):
    code: str
    label: str


class LessonOption(_FrozenStrictModel):
    number: int
    code: str
    label: str
    unit: str
    taxonomy: tuple[str, ...]


class SubjectOption(_FrozenStrictModel):
    code: str
    grade: int
    medium: str
    assessment_programme: str
    label: str
    units: tuple[UnitOption, ...]
    lessons: tuple[LessonOption, ...]


class TeacherPaperDefaults(_FrozenStrictModel):
    question_count: int = 10
    duration_minutes: int = 45
    difficulty: PaperDifficulty = PaperDifficulty.BALANCED


class TeacherPaperOptionsResponse(_FrozenStrictModel):
    grades: tuple[int, ...]
    media: tuple[MediumOption, ...]
    assessment_programmes: tuple[AssessmentProgrammeOption, ...]
    subjects: tuple[SubjectOption, ...]
    defaults: TeacherPaperDefaults = TeacherPaperDefaults()


class CurriculumLabelResponse(_FrozenStrictModel):
    assessment_programme: str
    assessment_label: str
    code: str
    label: str


class CurriculumLabelsResponse(_FrozenStrictModel):
    items: tuple[CurriculumLabelResponse, ...]


class LessonLabelsResponse(_FrozenStrictModel):
    grade: int
    medium: str
    subject: str
    curriculum: CurriculumLabelResponse
    lessons: tuple[LessonOption, ...]


class TeacherPaperCountsResponse(_FrozenStrictModel):
    requested: int
    generated: int
    validated: int
    candidates: int
    approved: int
    failed: int


class TeacherPaperFailureResponse(_FrozenStrictModel):
    code: str
    message: str


class TeacherPaperSlotProgressResponse(_FrozenStrictModel):
    id: UUID
    number: int
    status: str
    version: int
    lesson: str | None
    validation: FriendlyValidationStatus | None
    generation_run_id: UUID | None
    candidate_id: UUID | None
    failure: TeacherPaperFailureResponse | None


class TeacherPaperJobResponse(_FrozenStrictModel):
    job_id: UUID
    paper_id: UUID
    paper_reference: str
    title: str
    grade: int
    medium: str
    subject: str
    scope_summary: str
    status: TeacherPaperStatus
    progress: tuple[str, ...]
    counts: TeacherPaperCountsResponse
    cost_microusd: int
    total_tokens: int
    version: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    review_url: str | None
    failure: TeacherPaperFailureResponse | None
    slots: tuple[TeacherPaperSlotProgressResponse, ...]
    deduplicated: bool = False


class ReviewPaperSummaryResponse(_FrozenStrictModel):
    id: UUID
    paper_reference: str
    title: str
    grade: int
    subject: str
    scope_summary: str
    status: str
    question_count: int
    approved_count: int
    created_at: datetime


class ReviewPaperListResponse(_FrozenStrictModel):
    items: tuple[ReviewPaperSummaryResponse, ...]


class ReviewQuestionOptionResponse(_FrozenStrictModel):
    label: str
    text: str


class ReviewMarkingSchemeResponse(_FrozenStrictModel):
    total_marks: int
    criteria: tuple[str, ...]


class ReviewQuestionScopeResponse(_FrozenStrictModel):
    grade: int
    subject: str
    lessons: str
    unit: str
    lesson: str
    taxonomy: str


class ReviewSourceResponse(_FrozenStrictModel):
    filename: str
    title: str
    page: int


class ReviewValidationResponse(_FrozenStrictModel):
    status: FriendlyValidationStatus
    summary: str
    findings: tuple[str, ...]


class TechnicalValidationFindingResponse(_FrozenStrictModel):
    code: str
    status: TechnicalFindingStatus
    message: str
    evidence: tuple[dict[str, str], ...]


class ReviewQuestionTechnicalDetailsResponse(_FrozenStrictModel):
    generation_run_id: UUID
    validation_run_id: UUID | None
    candidate_id: UUID | None
    blueprint_slot_id: str
    context_ids: tuple[str, ...]
    provider: str
    model_version: str
    validator_findings: tuple[TechnicalValidationFindingResponse, ...]


class ReviewQuestionResponse(_FrozenStrictModel):
    id: UUID
    number: int
    version: int
    aggregate_slot_version: int = 0
    review_state: str
    requires_revalidation: bool = False
    stem: str
    options: tuple[ReviewQuestionOptionResponse, ...]
    answer: str
    explanation: str
    marking_scheme: ReviewMarkingSchemeResponse
    content: QuestionContentResponse | None = None
    scope: ReviewQuestionScopeResponse
    sources: tuple[ReviewSourceResponse, ...]
    validation: ReviewValidationResponse
    technical_details: ReviewQuestionTechnicalDetailsResponse


class ReviewPaperTechnicalDetailsResponse(_FrozenStrictModel):
    curriculum_version_id: UUID
    paper_blueprint_id: UUID
    request_fingerprint: str
    cost_microusd: int
    total_tokens: int


class ReviewPaperDraftResponse(_FrozenStrictModel):
    draft_id: UUID
    version: int


class ReviewPaperDetailResponse(_FrozenStrictModel):
    id: UUID
    paper_reference: str
    title: str
    grade: int
    medium: str
    subject: str
    scope_summary: str
    status: str
    version: int
    created_at: datetime
    questions: tuple[ReviewQuestionResponse, ...]
    draft: ReviewPaperDraftResponse | None
    technical_details: ReviewPaperTechnicalDetailsResponse


class ReviewQuestionEditRequest(_StrictModel):
    content: QuestionContentRequest
    reason: BoundedReason
    expected_version: Annotated[int, Field(strict=True, ge=1, le=100_000)]

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason must not contain surrounding whitespace")
        return value


class ReviewQuestionRegenerateRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion
    reason: BoundedReason

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason must not contain surrounding whitespace")
        return value


class ReviewQuestionRegenerationResponse(_FrozenStrictModel):
    job_id: UUID
    paper_id: UUID
    question_id: UUID
    status: Literal["generating"]
    version: int


class ReviewPaperCreateDraftRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class ReviewPaperDraftCreatedResponse(_FrozenStrictModel):
    paper_id: UUID
    paper_reference: str
    draft_id: UUID
    draft_version: int
    publication_path: str


__all__ = [
    "ReviewCandidateApproveRequest",
    "ReviewCandidateRejectRequest",
    "ReviewCandidateStartRequest",
]
