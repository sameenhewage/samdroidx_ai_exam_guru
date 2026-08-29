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
from exam_guru_api.subject_quality.domain import MAX_REVIEW_NOTE_CHARACTERS, CorrectionReasonCode
from exam_guru_api.teacher_papers.domain import (
    MAX_TEACHER_DURATION_MINUTES,
    MAX_TEACHER_QUESTIONS,
    PaperDifficulty,
    ScholarshipPaperMode,
    SchoolTerm,
    TeacherPaperType,
)
from exam_guru_api.validation.schemas import (
    SemanticVerificationDetailsResponse,
    ValidationFindingEvidenceResponse,
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
BoundedReviewNote = Annotated[str, Field(min_length=1, max_length=MAX_REVIEW_NOTE_CHARACTERS)]
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
    paper_type: TeacherPaperType
    subject: CodeText | None = None
    term: SchoolTerm | None = None
    scholarship_mode: ScholarshipPaperMode | None = None

    @model_validator(mode="after")
    def validate_combination(self) -> Self:
        if self.paper_type is TeacherPaperType.SUBJECT_PRACTICE:
            if self.subject is None or self.term is not None or self.scholarship_mode is not None:
                raise ValueError("subject practice requires only a subject")
        elif self.paper_type is TeacherPaperType.TERM_TEST:
            if self.subject is None or self.term is None or self.scholarship_mode is not None:
                raise ValueError("term test requires a subject and term")
        elif (
            self.grade != 5
            or self.subject is not None
            or self.term is not None
            or self.scholarship_mode is None
        ):
            raise ValueError("Grade 5 Scholarship requires a paper mode without a subject or term")
        return self


class FullSubjectScopeRequest(_StrictModel):
    kind: Literal["full_subject"]


class FullTermScopeRequest(_StrictModel):
    kind: Literal["full_term"]


class ProgrammeScopeRequest(_StrictModel):
    kind: Literal["programme"]


class LessonRangeScopeRequest(_StrictModel):
    kind: Literal["lesson_range"]
    start_lesson: Annotated[int, Field(strict=True, ge=1, le=10_000)]
    end_lesson: Annotated[int, Field(strict=True, ge=1, le=10_000)]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_lesson < self.start_lesson:
            raise ValueError("end_lesson must be greater than or equal to start_lesson")
        return self


LessonNumber = Annotated[int, Field(strict=True, ge=1, le=10_000)]


class SelectedLessonsScopeRequest(_StrictModel):
    kind: Literal["selected_lessons"]
    lesson_numbers: Annotated[tuple[LessonNumber, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def validate_lesson_numbers(self) -> Self:
        if tuple(sorted(set(self.lesson_numbers))) != self.lesson_numbers:
            raise ValueError("lesson_numbers must be unique and strictly increasing")
        return self


TeacherPaperScopeRequest = Annotated[
    FullSubjectScopeRequest
    | FullTermScopeRequest
    | ProgrammeScopeRequest
    | LessonRangeScopeRequest
    | SelectedLessonsScopeRequest,
    Field(discriminator="kind"),
]

PaperName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
TeacherInstruction = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_048),
]
QuestionCount = Annotated[int, Field(strict=True, ge=0, le=MAX_TEACHER_QUESTIONS)]


class TeacherPaperSettingsRequest(_StrictModel):
    paper_name: PaperName
    mcq_count: QuestionCount
    written_count: QuestionCount
    structured_count: QuestionCount
    duration_minutes: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_TEACHER_DURATION_MINUTES),
    ]
    difficulty: PaperDifficulty
    teacher_instruction: TeacherInstruction | None = None

    @model_validator(mode="after")
    def validate_total_questions(self) -> Self:
        if not 1 <= self.total_questions <= MAX_TEACHER_QUESTIONS:
            raise ValueError(f"total questions must be between 1 and {MAX_TEACHER_QUESTIONS}")
        return self

    @property
    def total_questions(self) -> int:
        return self.mcq_count + self.written_count + self.structured_count


class TeacherPaperJobCreateRequest(_StrictModel):
    target: TeacherPaperTargetRequest
    scope: TeacherPaperScopeRequest
    settings: TeacherPaperSettingsRequest

    @model_validator(mode="after")
    def validate_target_scope(self) -> Self:
        kind = self.scope.kind
        if self.target.paper_type is TeacherPaperType.SCHOLARSHIP_PRACTICE and kind != "programme":
            raise ValueError("Scholarship practice requires programme scope")
        if self.target.paper_type is TeacherPaperType.TERM_TEST and kind not in {
            "full_term",
            "lesson_range",
            "selected_lessons",
        }:
            raise ValueError("term test requires term-bounded scope")
        if self.target.paper_type is TeacherPaperType.SUBJECT_PRACTICE and kind not in {
            "full_subject",
            "lesson_range",
            "selected_lessons",
        }:
            raise ValueError("subject practice requires subject scope")
        return self


class ProgrammePolicyScopeCreateRequest(_StrictModel):
    part: Literal["paper_i", "paper_ii"]
    ordinal: Annotated[int, Field(strict=True, ge=1, le=64)]
    anchor_unit_id: UUID
    anchor_lesson_id: UUID
    anchor_competency_id: UUID
    anchor_skill_id: UUID | None = None
    anchor_sub_skill_id: UUID | None = None
    anchor_learning_concept_id: UUID | None = None
    source_curriculum_version_id: UUID
    source_unit_id: UUID | None = None
    source_lesson_id: UUID | None = None
    source_competency_id: UUID
    source_skill_id: UUID | None = None
    source_sub_skill_id: UUID | None = None
    source_learning_concept_id: UUID | None = None

    @model_validator(mode="after")
    def validate_hierarchy(self) -> Self:
        if self.anchor_sub_skill_id is not None and self.anchor_skill_id is None:
            raise ValueError("anchor sub-skill requires an anchor skill")
        if self.anchor_learning_concept_id is not None and self.anchor_sub_skill_id is None:
            raise ValueError("anchor learning concept requires an anchor sub-skill")
        if self.source_lesson_id is not None and self.source_unit_id is None:
            raise ValueError("source lesson requires a source unit")
        if self.source_sub_skill_id is not None and self.source_skill_id is None:
            raise ValueError("source sub-skill requires a source skill")
        if self.source_learning_concept_id is not None and self.source_sub_skill_id is None:
            raise ValueError("source learning concept requires a source sub-skill")
        return self


class ProgrammePolicyCreateRequest(_StrictModel):
    programme_exam_configuration_id: UUID
    medium_id: UUID
    anchor_curriculum_version_id: UUID
    code: CodeText
    version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    paper_i_profile_version: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]
    paper_ii_profile_version: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]
    paper_i_weight: Annotated[int, Field(strict=True, ge=1, le=100)]
    paper_ii_weight: Annotated[int, Field(strict=True, ge=1, le=100)]
    scopes: Annotated[
        tuple[ProgrammePolicyScopeCreateRequest, ...], Field(min_length=2, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_scopes(self) -> Self:
        identities = tuple((scope.part, scope.ordinal) for scope in self.scopes)
        if len(set(identities)) != len(identities):
            raise ValueError("programme policy scope ordinals must be unique within each part")
        if {scope.part for scope in self.scopes} != {"paper_i", "paper_ii"}:
            raise ValueError("programme policy requires Paper I and Paper II scopes")
        return self


class ProgrammePolicyReviewRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class ProgrammePolicyScopeResponse(_FrozenStrictModel):
    id: UUID
    part: Literal["paper_i", "paper_ii"]
    ordinal: int
    anchor_lesson_id: UUID
    source_grade: int
    source_curriculum_version_id: UUID
    source_unit_id: UUID | None
    source_lesson_id: UUID | None


class ProgrammePolicyResponse(_FrozenStrictModel):
    id: UUID
    code: str
    version: str
    title: str
    state: Literal["draft", "reviewed", "retired"]
    lock_version: int
    programme_exam_configuration_id: UUID
    medium_id: UUID
    anchor_curriculum_version_id: UUID
    paper_i_profile_version: str
    paper_ii_profile_version: str
    paper_i_weight: int
    paper_ii_weight: int
    scopes: tuple[ProgrammePolicyScopeResponse, ...]
    content_hash: str | None
    created_at: datetime
    reviewed_at: datetime | None


class TeacherPaperAdvanceRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class TeacherPaperRetryRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class PaperTypeOption(_FrozenStrictModel):
    code: TeacherPaperType
    grade: int
    label: str


class ScholarshipModeOption(_FrozenStrictModel):
    code: ScholarshipPaperMode
    label: str


class TermOption(_FrozenStrictModel):
    code: SchoolTerm
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
    label: str
    units: tuple[UnitOption, ...]
    lessons: tuple[LessonOption, ...]


class TeacherPaperDefaults(_FrozenStrictModel):
    paper_name: str = "Grade 5 practice paper"
    mcq_count: int = 5
    written_count: int = 5
    structured_count: int = 0
    duration_minutes: int = 45
    difficulty: PaperDifficulty = PaperDifficulty.BALANCED
    teacher_instruction: str | None = None


class TeacherPaperOptionsResponse(_FrozenStrictModel):
    grades: tuple[int, ...]
    media: tuple[MediumOption, ...]
    paper_types: tuple[PaperTypeOption, ...]
    scholarship_modes: tuple[ScholarshipModeOption, ...]
    subjects: tuple[SubjectOption, ...]
    terms: tuple[TermOption, ...]
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
    point_marks: tuple[int, ...]


class ReviewMarkingConfirmationResponse(_FrozenStrictModel):
    confirmed: bool
    status: Literal["teacher_confirmation_required", "teacher_confirmed"]
    confirmed_at: datetime | None


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
    evidence: tuple[ValidationFindingEvidenceResponse, ...]
    semantic_verification: SemanticVerificationDetailsResponse | None = None


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
    marking_confirmation: ReviewMarkingConfirmationResponse
    content: QuestionContentResponse | None = None
    scope: ReviewQuestionScopeResponse
    sources: tuple[ReviewSourceResponse, ...]
    validation: ReviewValidationResponse
    technical_details: ReviewQuestionTechnicalDetailsResponse
    quality_feedback_id: UUID | None = None


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


class _ReviewReasonRequest(_StrictModel):
    reason_code: CorrectionReasonCode
    note: BoundedReviewNote | None = None

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("note must not contain surrounding whitespace")
        return value


class ReviewQuestionEditRequest(_ReviewReasonRequest):
    content: QuestionContentRequest
    expected_version: Annotated[int, Field(strict=True, ge=1, le=100_000)]

    @model_validator(mode="after")
    def require_marking_allocations(self) -> Self:
        if not self.content.marking_point_marks:
            raise ValueError("teacher review edits require per-point mark allocations")
        return self


class ReviewQuestionRejectRequest(_ReviewReasonRequest):
    expected_version: Annotated[int, Field(strict=True, ge=1, le=100_000)]


class ReviewQuestionRegenerateRequest(_ReviewReasonRequest):
    expected_version: ExpectedAggregateVersion


class ReviewQuestionApproveRequest(_StrictModel):
    expected_version: Annotated[int, Field(strict=True, ge=1, le=100_000)]
    marking_confirmed: Literal[True]
    note: BoundedReviewNote | None = None

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("note must not contain surrounding whitespace")
        return value


class ReviewQuestionRegenerationResponse(_FrozenStrictModel):
    job_id: UUID
    paper_id: UUID
    question_id: UUID
    status: Literal["generating"]
    version: int
    quality_feedback_id: UUID


class ReviewPaperCreateDraftRequest(_StrictModel):
    expected_version: ExpectedAggregateVersion


class ReviewPaperDraftCreatedResponse(_FrozenStrictModel):
    paper_job_id: UUID
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
