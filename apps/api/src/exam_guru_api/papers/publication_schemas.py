from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exam_guru_api.papers.domain import MAX_PAPER_VERSIONS
from exam_guru_api.papers.publication_models import (
    MAX_PAPER_ARCHIVE_REASON_CHARACTERS,
    MAX_PAPER_SLOTS,
    MAX_PAPER_TITLE_CHARACTERS,
)
from exam_guru_api.papers.schemas import QuestionContentResponse

PaperExpectedVersion = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_PAPER_VERSIONS),
]
PaperTitle = Annotated[
    str,
    Field(min_length=1, max_length=MAX_PAPER_TITLE_CHARACTERS),
]
ArchiveReason = Annotated[
    str,
    Field(min_length=1, max_length=MAX_PAPER_ARCHIVE_REASON_CHARACTERS),
]
PaperStateValue = Literal["draft", "published", "archived"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperDraftCreateRequest(_StrictModel):
    paper_blueprint_id: UUID
    title: PaperTitle
    candidate_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=MAX_PAPER_SLOTS)]

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("title must not contain surrounding whitespace")
        return value

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate identifiers must be unique")
        return value


class PaperRevisionCreateRequest(_StrictModel):
    expected_version: PaperExpectedVersion
    candidate_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=MAX_PAPER_SLOTS)]
    title: PaperTitle | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("title must not contain surrounding whitespace")
        return value

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate identifiers must be unique")
        return value


class PaperPublishRequest(_StrictModel):
    expected_version: PaperExpectedVersion


class PaperArchiveRequest(_StrictModel):
    expected_version: PaperExpectedVersion
    reason: ArchiveReason

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("reason must not contain surrounding whitespace")
        return value


class PaperAggregateResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    paper_blueprint_id: UUID
    blueprint_id: str
    blueprint_version: str
    state: PaperStateValue
    current_version: int
    created_by: UUID
    created_at: datetime
    updated_by: UUID
    updated_at: datetime

    @classmethod
    def from_model(cls, value: object) -> Self:
        from exam_guru_api.papers.publication_models import PracticePaperModel

        if not isinstance(value, PracticePaperModel):
            raise TypeError("value must be PracticePaperModel")
        return cls.model_validate(value, from_attributes=True)


class PaperSummaryResponse(PaperAggregateResponse):
    title: str
    latest_publication_hash: str | None

    @classmethod
    def from_record(cls, value: object) -> Self:
        from exam_guru_api.papers.publication_repository import PaperSummary

        if not isinstance(value, PaperSummary):
            raise TypeError("value must be PaperSummary")
        return cls.model_validate(value, from_attributes=True)


class PaperDraftCandidateResponse(_FrozenStrictModel):
    ordinal: int
    blueprint_slot_id: str
    candidate_id: UUID
    candidate_version: int
    candidate_revision: int


class PaperDraftVersionResponse(_FrozenStrictModel):
    paper_id: UUID
    curriculum_version_id: UUID
    version: int
    title: str
    supersedes_content_hash: str | None
    candidates: tuple[PaperDraftCandidateResponse, ...]
    created_by: UUID
    created_at: datetime
    deduplicated: bool = False

    @classmethod
    def from_record(cls, value: object, *, deduplicated: bool = False) -> Self:
        from exam_guru_api.papers.publication_repository import StoredPaperDraft

        if not isinstance(value, StoredPaperDraft):
            raise TypeError("value must be StoredPaperDraft")
        return cls(
            paper_id=value.draft.paper_id,
            curriculum_version_id=value.draft.curriculum_version_id,
            version=value.draft.version,
            title=value.draft.title,
            supersedes_content_hash=value.draft.supersedes_content_hash,
            candidates=tuple(
                PaperDraftCandidateResponse.model_validate(item, from_attributes=True)
                for item in value.selections
            ),
            created_by=value.draft.created_by,
            created_at=value.draft.created_at,
            deduplicated=deduplicated,
        )


class PublishedBlueprintResponse(_FrozenStrictModel):
    blueprint_id: str
    blueprint_version: str
    paper_blueprint_id: UUID
    slot_ids: tuple[str, ...]


class PublishedProvenanceResponse(_FrozenStrictModel):
    chunk_id: str
    page_number: int
    source_document_id: str
    source_version: str


class PublishedLineageResponse(_FrozenStrictModel):
    blueprint_id: str
    blueprint_slot_id: str
    blueprint_version: str
    generation_attempt_id: UUID
    generation_id: UUID
    model_version: str
    prompt_version: str
    provenance: tuple[PublishedProvenanceResponse, ...]
    provider: str
    retrieval_version: str
    schema_version: str


class PublishedValidationResponse(_FrozenStrictModel):
    finding_refs: tuple[str, ...]
    passed: Literal[True]
    validated_revision: Literal[1]
    validation_run_id: UUID
    validator_version: str


class PublishedRevisionResponse(_FrozenStrictModel):
    content: QuestionContentResponse
    reason: str | None
    reviewer_id: UUID | None
    revision: int


class PublishedReviewRecordResponse(_FrozenStrictModel):
    action: Literal["started", "edited", "approved"]
    candidate_version: int
    reason: str | None
    reviewer_id: UUID


class PublishedDecisionResponse(_FrozenStrictModel):
    candidate_version: int
    reason: str | None
    reviewer_id: UUID
    state: Literal["approved"]


class PublishedQuestionResponse(_FrozenStrictModel):
    candidate_id: UUID
    candidate_version: int
    content: QuestionContentResponse
    content_revision: int
    decision: PublishedDecisionResponse
    lineage: PublishedLineageResponse
    review_history: tuple[PublishedReviewRecordResponse, ...]
    revisions: tuple[PublishedRevisionResponse, ...]
    slot_id: str
    validation: PublishedValidationResponse

    @model_validator(mode="after")
    def validate_final_revision(self) -> Self:
        if not self.revisions or self.revisions[-1].revision != self.content_revision:
            raise ValueError("content_revision must identify the final revision")
        if self.revisions[-1].content != self.content:
            raise ValueError("published content must equal the final revision")
        return self


class PublishedPaperSnapshotResponse(_FrozenStrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    blueprint: PublishedBlueprintResponse
    paper_id: UUID
    paper_version: int
    questions: tuple[PublishedQuestionResponse, ...]
    schema_version: Literal["published-paper.v1"] = Field(
        alias="schema",
        serialization_alias="schema",
    )
    title: str


class PublishedPaperVersionSummaryResponse(_FrozenStrictModel):
    paper_id: UUID
    curriculum_version_id: UUID
    version: int
    content_hash: str
    published_by: UUID
    published_at: datetime

    @classmethod
    def from_record(cls, value: object) -> Self:
        from exam_guru_api.papers.publication_repository import PublicationVersionSummary

        if not isinstance(value, PublicationVersionSummary):
            raise TypeError("value must be PublicationVersionSummary")
        return cls.model_validate(value, from_attributes=True)


class PublishedPaperVersionResponse(_FrozenStrictModel):
    paper_id: UUID
    curriculum_version_id: UUID
    version: int
    previous_version: int | None
    supersedes_content_hash: str | None
    content_hash: str
    snapshot: PublishedPaperSnapshotResponse
    published_by: UUID
    published_at: datetime
    deduplicated: bool = False

    @classmethod
    def from_record(cls, value: object, *, deduplicated: bool = False) -> Self:
        from exam_guru_api.papers.publication_repository import StoredPublication

        if not isinstance(value, StoredPublication):
            raise TypeError("value must be StoredPublication")
        model = value.publication
        return cls(
            paper_id=model.paper_id,
            curriculum_version_id=model.curriculum_version_id,
            version=model.version,
            previous_version=model.previous_version,
            supersedes_content_hash=model.supersedes_content_hash,
            content_hash=model.content_hash,
            snapshot=PublishedPaperSnapshotResponse.model_validate(model.snapshot),
            published_by=model.published_by,
            published_at=model.published_at,
            deduplicated=deduplicated,
        )


class PaperArchiveResponse(_FrozenStrictModel):
    paper_id: UUID
    curriculum_version_id: UUID
    version: int
    reason: str
    archived_by: UUID
    archived_at: datetime
    content_hash: str
    deduplicated: bool = False

    @classmethod
    def from_record(cls, value: object, *, deduplicated: bool = False) -> Self:
        from exam_guru_api.papers.publication_repository import StoredPaperArchive

        if not isinstance(value, StoredPaperArchive):
            raise TypeError("value must be StoredPaperArchive")
        return cls(
            paper_id=value.archive.paper_id,
            curriculum_version_id=value.archive.curriculum_version_id,
            version=value.archive.version,
            reason=value.archive.reason,
            archived_by=value.archive.archived_by,
            archived_at=value.archive.archived_at,
            content_hash=value.publication.publication.content_hash,
            deduplicated=deduplicated,
        )
