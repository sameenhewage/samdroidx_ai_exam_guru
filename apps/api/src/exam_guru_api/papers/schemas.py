import json
from datetime import datetime
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exam_guru_api.papers.domain import MAX_CANDIDATE_VERSION, QuestionContent, QuestionOption
from exam_guru_api.papers.models import MAX_CANDIDATE_CONTENT_BYTES

BoundedText = Annotated[str, Field(min_length=1, max_length=32_768)]
BoundedReason = Annotated[str, Field(min_length=1, max_length=1_024)]
ExpectedVersion = Annotated[int, Field(strict=True, ge=1, le=MAX_CANDIDATE_VERSION)]
QuestionTypeValue = Literal[
    "multiple_choice",
    "short_answer",
    "structured",
    "structured_response",
]
CandidateStateValue = Literal["validated", "in_review", "approved", "rejected"]
ReviewActionValue = Literal["started", "edited", "approved", "rejected"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuestionOptionRequest(_StrictModel):
    option_id: Annotated[str, Field(min_length=1, max_length=128)]
    text: Annotated[str, Field(min_length=1, max_length=8_192)]

    @field_validator("option_id", "text")
    @classmethod
    def validate_trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("text must not contain surrounding whitespace")
        return value


class QuestionContentRequest(_StrictModel):
    question_type: QuestionTypeValue
    stem: BoundedText
    options: Annotated[tuple[QuestionOptionRequest, ...], Field(max_length=16)]
    answer: BoundedText
    explanation: BoundedText
    marks: Annotated[int, Field(strict=True, ge=1, le=100)]
    marking_guide: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=8_192)], ...],
        Field(min_length=1, max_length=64),
    ]

    @field_validator("stem", "answer", "explanation")
    @classmethod
    def validate_trimmed_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("text must not contain surrounding whitespace")
        return value

    @field_validator("marking_guide")
    @classmethod
    def validate_marking_guide(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(item != item.strip() for item in value):
            raise ValueError("marking guide items must not contain surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_option_answer(self) -> Self:
        option_ids = tuple(option.option_id for option in self.options)
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("option identifiers must be unique")
        if self.question_type == "multiple_choice" and (
            len(option_ids) < 2 or option_ids.count(self.answer) != 1
        ):
            raise ValueError("multiple-choice answer must reference exactly one option")
        serialized = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > MAX_CANDIDATE_CONTENT_BYTES:
            raise ValueError("question content exceeds the persisted size bound")
        return self

    def to_domain(self) -> QuestionContent:
        return QuestionContent(
            question_type=self.question_type,
            stem=self.stem,
            options=tuple(
                QuestionOption(option_id=option.option_id, text=option.text)
                for option in self.options
            ),
            answer=self.answer,
            explanation=self.explanation,
            marks=self.marks,
            marking_guide=self.marking_guide,
        )


class ReviewCandidateCreateRequest(_StrictModel):
    validation_run_id: UUID


class ReviewCandidateStartRequest(_StrictModel):
    expected_version: ExpectedVersion


class ReviewCandidateEditRequest(_StrictModel):
    content: QuestionContentRequest
    reason: BoundedReason
    expected_version: ExpectedVersion

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason must not contain surrounding whitespace")
        return value


class ReviewCandidateApproveRequest(_StrictModel):
    expected_version: ExpectedVersion
    note: BoundedReason | None = None

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("note must not contain surrounding whitespace")
        return value


class ReviewCandidateRejectRequest(_StrictModel):
    expected_version: ExpectedVersion
    reason: BoundedReason

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason must not contain surrounding whitespace")
        return value


class QuestionOptionResponse(_FrozenStrictModel):
    option_id: str
    text: str


class QuestionContentResponse(_FrozenStrictModel):
    question_type: QuestionTypeValue
    stem: str
    options: tuple[QuestionOptionResponse, ...]
    answer: str
    explanation: str
    marks: int
    marking_guide: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: QuestionContent) -> Self:
        return cls(
            question_type=cast(QuestionTypeValue, value.question_type),
            stem=value.stem,
            options=tuple(
                QuestionOptionResponse(option_id=option.option_id, text=option.text)
                for option in value.options
            ),
            answer=value.answer,
            explanation=value.explanation,
            marks=value.marks,
            marking_guide=value.marking_guide,
        )


class SourceProvenanceResponse(_FrozenStrictModel):
    source_document_id: str
    source_version: str
    page_number: int
    chunk_id: str


class GenerationLineageResponse(_FrozenStrictModel):
    generation_id: UUID
    generation_attempt_id: UUID
    paper_blueprint_id: UUID
    blueprint_id: str
    blueprint_version: str
    blueprint_slot_id: str
    prompt_version: str
    provider: str
    model_version: str
    retrieval_version: str
    schema_version: str
    provenance: tuple[SourceProvenanceResponse, ...]


class ValidationEvidenceResponse(_FrozenStrictModel):
    validation_run_id: UUID
    validator_version: str
    finding_refs: tuple[UUID, ...]
    passed: bool
    validated_revision: Literal[1]


class CandidateRevisionResponse(_FrozenStrictModel):
    revision: int
    candidate_version: int
    content: QuestionContentResponse
    reviewer_id: UUID | None
    reason: str | None
    created_at: datetime


class CandidateReviewEventResponse(_FrozenStrictModel):
    action: ReviewActionValue
    reviewer_id: UUID
    candidate_version: int
    revision: int
    reason: str | None
    created_at: datetime


class ReviewCandidateSummaryResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    generation_run_id: UUID
    generation_attempt_id: UUID
    validation_run_id: UUID
    paper_blueprint_id: UUID
    blueprint_id: str
    blueprint_version: str
    blueprint_slot_id: str
    state: CandidateStateValue
    version: int
    current_revision: int
    question_type: QuestionTypeValue
    stem_preview: Annotated[str, Field(min_length=1, max_length=512)]
    marks: int
    created_by: UUID
    created_at: datetime
    current_revision_created_at: datetime

    @classmethod
    def from_record(cls, value: object) -> Self:
        from exam_guru_api.papers.repository import ReviewCandidateSummary

        if not isinstance(value, ReviewCandidateSummary):
            raise TypeError("value must be ReviewCandidateSummary")
        return cls(
            id=value.id,
            curriculum_version_id=value.curriculum_version_id,
            generation_run_id=value.generation_run_id,
            generation_attempt_id=value.generation_attempt_id,
            validation_run_id=value.validation_run_id,
            paper_blueprint_id=value.paper_blueprint_id,
            blueprint_id=value.blueprint_id,
            blueprint_version=value.blueprint_version,
            blueprint_slot_id=value.blueprint_slot_id,
            state=cast(CandidateStateValue, value.state.value),
            version=value.version,
            current_revision=value.current_revision,
            question_type=cast(QuestionTypeValue, value.question_type),
            stem_preview=value.stem_preview,
            marks=value.marks,
            created_by=value.created_by,
            created_at=value.created_at,
            current_revision_created_at=value.current_revision_created_at,
        )


class ReviewCandidateResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    generation_run_id: UUID
    generation_attempt_id: UUID
    validation_run_id: UUID
    paper_blueprint_id: UUID
    blueprint_id: str
    blueprint_version: str
    blueprint_slot_id: str
    state: CandidateStateValue
    version: int
    current_revision: int
    lineage: GenerationLineageResponse
    validation: ValidationEvidenceResponse
    current_content: QuestionContentResponse
    revisions: tuple[CandidateRevisionResponse, ...]
    events: tuple[CandidateReviewEventResponse, ...]
    created_by: UUID
    created_at: datetime
    deduplicated: bool = False

    @classmethod
    def from_record(cls, value: object, *, deduplicated: bool = False) -> Self:
        from exam_guru_api.papers.repository import StoredQuestionCandidate

        if not isinstance(value, StoredQuestionCandidate):
            raise TypeError("value must be StoredQuestionCandidate")
        candidate = value.candidate
        domain = value.domain
        lineage = candidate.generation_lineage
        evidence = candidate.validation_evidence
        return cls(
            id=candidate.id,
            curriculum_version_id=candidate.curriculum_version_id,
            generation_run_id=candidate.generation_run_id,
            generation_attempt_id=candidate.generation_attempt_id,
            validation_run_id=candidate.validation_run_id,
            paper_blueprint_id=candidate.paper_blueprint_id,
            blueprint_id=candidate.blueprint_id,
            blueprint_version=candidate.blueprint_version,
            blueprint_slot_id=candidate.blueprint_slot_id,
            state=cast(CandidateStateValue, candidate.state),
            version=candidate.version,
            current_revision=candidate.current_revision,
            lineage=GenerationLineageResponse.model_validate(lineage),
            validation=ValidationEvidenceResponse.model_validate(evidence),
            current_content=QuestionContentResponse.from_domain(domain.content),
            revisions=tuple(
                CandidateRevisionResponse(
                    revision=revision_model.revision,
                    candidate_version=revision_model.candidate_version,
                    content=QuestionContentResponse.from_domain(domain_revision.content),
                    reviewer_id=revision_model.reviewer_id,
                    reason=revision_model.reason,
                    created_at=revision_model.created_at,
                )
                for revision_model, domain_revision in zip(
                    value.revisions,
                    domain.revisions,
                    strict=True,
                )
            ),
            events=tuple(
                CandidateReviewEventResponse(
                    action=cast(ReviewActionValue, event_model.action),
                    reviewer_id=event_model.reviewer_id,
                    candidate_version=event_model.candidate_version,
                    revision=event_model.revision,
                    reason=event_model.reason,
                    created_at=event_model.created_at,
                )
                for event_model in value.events
            ),
            created_by=candidate.created_by,
            created_at=candidate.created_at,
            deduplicated=deduplicated,
        )
