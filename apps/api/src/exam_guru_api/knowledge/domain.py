from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class KnowledgeContractError(ValueError):
    pass


class ReviewState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    STRUCTURED = "structured"


class ChunkType(StrEnum):
    COMPETENCY_SECTION = "competency_section"
    LEARNING_OUTCOME = "learning_outcome"
    EXPLANATION = "explanation"
    EXAMPLE = "example"
    PRACTICE_QUESTION = "practice_question"
    KEY_TERM = "key_term"


@dataclass(frozen=True, slots=True)
class Provenance:
    source_document_id: UUID
    page_number: int
    source_block_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise KnowledgeContractError("page_number must be positive")


@dataclass(frozen=True, slots=True)
class HistoricalQuestion:
    id: UUID
    curriculum_version_id: UUID
    year: int
    paper_code: str
    question_number: str
    text: str
    question_type: QuestionType
    marks: int
    provenance: Provenance
    review_state: ReviewState = ReviewState.DRAFT
    competency_id: UUID | None = None
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None

    def __post_init__(self) -> None:
        if not 1900 <= self.year <= 2100:
            raise KnowledgeContractError("year is outside the supported range")
        if not self.paper_code.strip() or not self.question_number.strip() or not self.text.strip():
            raise KnowledgeContractError("question identity and text must be non-blank")
        if self.marks < 1:
            raise KnowledgeContractError("marks must be positive")
        if self.review_state is ReviewState.REVIEWED and self.competency_id is None:
            raise KnowledgeContractError("reviewed questions require competency classification")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: UUID
    curriculum_version_id: UUID
    chunk_type: ChunkType
    text: str
    educational_boundary: str
    sequence: int
    provenance: Provenance
    review_state: ReviewState = ReviewState.DRAFT
    competency_id: UUID | None = None
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.educational_boundary.strip():
            raise KnowledgeContractError("chunk text and educational boundary must be non-blank")
        if self.sequence < 0:
            raise KnowledgeContractError("chunk sequence must be non-negative")
        if self.review_state is ReviewState.REVIEWED and self.competency_id is None:
            raise KnowledgeContractError("reviewed chunks require competency classification")


_ALLOWED_REVIEW_TRANSITIONS = frozenset(
    {
        (ReviewState.DRAFT, ReviewState.IN_REVIEW),
        (ReviewState.IN_REVIEW, ReviewState.REVIEWED),
        (ReviewState.IN_REVIEW, ReviewState.REJECTED),
    }
)


def transition_review_state(current: ReviewState, target: ReviewState) -> ReviewState:
    if current is not target and (current, target) not in _ALLOWED_REVIEW_TRANSITIONS:
        raise KnowledgeContractError(
            f"cannot transition review from {current.value} to {target.value}"
        )
    return target
