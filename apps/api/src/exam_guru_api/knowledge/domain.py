from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast
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


class DifficultyLabel(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


MAX_MEDIA_REFERENCES = 32
MAX_MEDIA_REFERENCE_CHARACTERS = 2_048
MIN_QUESTION_OPTIONS = 2
MAX_QUESTION_OPTIONS = 8
MAX_QUESTION_OPTION_CHARACTERS = 2_000
MAX_ANSWER_CHARACTERS = 8_000
MAX_MARKING_GUIDANCE_CHARACTERS = 16_000
MAX_MARKING_DATA_BYTES = 65_536
MAX_MARKING_DATA_DEPTH = 8
MAX_MARKING_DATA_NODES = 1_024
MAX_MARKING_DATA_COLLECTION_ITEMS = 128
MAX_MARKING_DATA_KEY_CHARACTERS = 128
MAX_MARKING_DATA_STRING_CHARACTERS = 16_000
MAX_QUESTION_ARCHETYPE_CHARACTERS = 128
MAX_DIFFICULTY_SOURCE_CHARACTERS = 128


class ChunkType(StrEnum):
    COMPETENCY_SECTION = "competency_section"
    LEARNING_OUTCOME = "learning_outcome"
    EXPLANATION = "explanation"
    EXAMPLE = "example"
    PRACTICE_QUESTION = "practice_question"
    KEY_TERM = "key_term"


class EmbeddingStatus(StrEnum):
    NOT_EMBEDDED = "not_embedded"
    EMBEDDED = "embedded"


@dataclass(frozen=True, slots=True)
class EmbeddingConfigurationMetadata:
    id: UUID
    provider: str
    model: str
    dimension: int
    version: str
    config_fingerprint: str


@dataclass(frozen=True, slots=True)
class Provenance:
    source_document_id: UUID
    page_number: int
    source_block_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise KnowledgeContractError("page_number must be positive")


def _snapshot_marking_value(
    value: object,
    *,
    depth: int,
    active_container_ids: set[int],
    node_count: list[int],
) -> object:
    node_count[0] += 1
    if node_count[0] > MAX_MARKING_DATA_NODES:
        raise KnowledgeContractError("marking_data exceeds the node limit")
    if depth > MAX_MARKING_DATA_DEPTH:
        raise KnowledgeContractError("marking_data exceeds the depth limit")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise KnowledgeContractError("marking_data numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value) > MAX_MARKING_DATA_STRING_CHARACTERS:
            raise KnowledgeContractError("marking_data contains an oversized string")
        return value
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            raise KnowledgeContractError("marking_data cannot contain cycles")
        if len(value) > MAX_MARKING_DATA_COLLECTION_ITEMS:
            raise KnowledgeContractError("marking_data object exceeds the item limit")
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            for key, item in value.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or key != key.strip()
                    or len(key) > MAX_MARKING_DATA_KEY_CHARACTERS
                ):
                    raise KnowledgeContractError("marking_data keys must be bounded clean text")
                snapshot[key] = _snapshot_marking_value(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    node_count=node_count,
                )
            return snapshot
        finally:
            active_container_ids.remove(container_id)
    if isinstance(value, list | tuple):
        container_id = id(value)
        if container_id in active_container_ids:
            raise KnowledgeContractError("marking_data cannot contain cycles")
        if len(value) > MAX_MARKING_DATA_COLLECTION_ITEMS:
            raise KnowledgeContractError("marking_data array exceeds the item limit")
        active_container_ids.add(container_id)
        try:
            return [
                _snapshot_marking_value(
                    item,
                    depth=depth + 1,
                    active_container_ids=active_container_ids,
                    node_count=node_count,
                )
                for item in value
            ]
        finally:
            active_container_ids.remove(container_id)
    raise KnowledgeContractError("marking_data must contain only JSON values")


@dataclass(frozen=True, slots=True)
class HistoricalQuestionMarkingData:
    canonical_json: str

    def __post_init__(self) -> None:
        try:
            decoded = json.loads(self.canonical_json)
        except (json.JSONDecodeError, TypeError) as error:
            raise KnowledgeContractError("marking_data must contain valid JSON") from error
        if not isinstance(decoded, Mapping) or not decoded:
            raise KnowledgeContractError("marking_data must be a non-empty JSON object")
        snapshot = _snapshot_marking_value(
            decoded,
            depth=0,
            active_container_ids=set(),
            node_count=[0],
        )
        canonical_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(canonical_json.encode("utf-8")) > MAX_MARKING_DATA_BYTES:
            raise KnowledgeContractError("marking_data exceeds the byte limit")
        object.__setattr__(self, "canonical_json", canonical_json)

    @classmethod
    def from_value(
        cls,
        value: HistoricalQuestionMarkingData | Mapping[str, object],
    ) -> HistoricalQuestionMarkingData:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or not value:
            raise KnowledgeContractError("marking_data must be a non-empty JSON object")
        snapshot = _snapshot_marking_value(
            value,
            depth=0,
            active_container_ids=set(),
            node_count=[0],
        )
        return cls(
            canonical_json=json.dumps(
                snapshot,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], json.loads(self.canonical_json))


def marking_data_to_dict(
    value: HistoricalQuestionMarkingData | Mapping[str, object] | None,
) -> dict[str, object] | None:
    if value is None:
        return None
    return HistoricalQuestionMarkingData.from_value(value).to_dict()


def _validate_text_collection(
    values: tuple[str, ...] | None,
    *,
    field_name: str,
    minimum_items: int,
    maximum_items: int,
    maximum_characters: int,
) -> None:
    if values is None:
        return
    if not isinstance(values, tuple) or not minimum_items <= len(values) <= maximum_items:
        raise KnowledgeContractError(f"{field_name} must be a bounded tuple")
    if any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum_characters
        for value in values
    ):
        raise KnowledgeContractError(f"{field_name} must contain bounded clean text")
    if len(set(values)) != len(values):
        raise KnowledgeContractError(f"{field_name} must be unique")


def _validate_optional_text(value: str | None, *, field_name: str, maximum: int) -> None:
    if value is not None and (
        not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum
    ):
        raise KnowledgeContractError(f"{field_name} must be bounded clean text")


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
    unit_id: UUID | None = None
    lesson_id: UUID | None = None
    media_references: tuple[str, ...] | None = None
    options: tuple[str, ...] | None = None
    answer: str | None = None
    marking_guidance: str | None = None
    marking_data: HistoricalQuestionMarkingData | Mapping[str, object] | None = None
    question_archetype: str | None = None
    difficulty_label: DifficultyLabel | None = None
    difficulty_confidence: float | None = None
    difficulty_source: str | None = None
    review_state: ReviewState = ReviewState.DRAFT
    competency_id: UUID | None = None
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding_configurations: tuple[EmbeddingConfigurationMetadata, ...] = ()

    def __post_init__(self) -> None:
        if self.lesson_id is not None and self.unit_id is None:
            raise KnowledgeContractError("lesson_id requires unit_id")
        if not 1900 <= self.year <= 2100:
            raise KnowledgeContractError("year is outside the supported range")
        if not self.paper_code.strip() or not self.question_number.strip() or not self.text.strip():
            raise KnowledgeContractError("question identity and text must be non-blank")
        if self.marks < 1:
            raise KnowledgeContractError("marks must be positive")
        _validate_text_collection(
            self.media_references,
            field_name="media_references",
            minimum_items=1,
            maximum_items=MAX_MEDIA_REFERENCES,
            maximum_characters=MAX_MEDIA_REFERENCE_CHARACTERS,
        )
        _validate_text_collection(
            self.options,
            field_name="options",
            minimum_items=MIN_QUESTION_OPTIONS,
            maximum_items=MAX_QUESTION_OPTIONS,
            maximum_characters=MAX_QUESTION_OPTION_CHARACTERS,
        )
        _validate_optional_text(
            self.answer,
            field_name="answer",
            maximum=MAX_ANSWER_CHARACTERS,
        )
        _validate_optional_text(
            self.marking_guidance,
            field_name="marking_guidance",
            maximum=MAX_MARKING_GUIDANCE_CHARACTERS,
        )
        _validate_optional_text(
            self.question_archetype,
            field_name="question_archetype",
            maximum=MAX_QUESTION_ARCHETYPE_CHARACTERS,
        )
        if self.marking_data is not None:
            object.__setattr__(
                self,
                "marking_data",
                HistoricalQuestionMarkingData.from_value(self.marking_data),
            )
        difficulty_values = (
            self.difficulty_label,
            self.difficulty_confidence,
            self.difficulty_source,
        )
        difficulty_fields_supplied = sum(value is not None for value in difficulty_values)
        if difficulty_fields_supplied not in {0, len(difficulty_values)}:
            raise KnowledgeContractError("difficulty evidence must be absent or complete")
        if difficulty_fields_supplied:
            if not isinstance(self.difficulty_label, DifficultyLabel):
                raise KnowledgeContractError("difficulty_label must be a DifficultyLabel")
            if (
                not isinstance(self.difficulty_confidence, int | float)
                or isinstance(self.difficulty_confidence, bool)
                or not math.isfinite(self.difficulty_confidence)
                or not 0.0 <= self.difficulty_confidence <= 1.0
            ):
                raise KnowledgeContractError("difficulty_confidence must be finite between 0 and 1")
            _validate_optional_text(
                self.difficulty_source,
                field_name="difficulty_source",
                maximum=MAX_DIFFICULTY_SOURCE_CHARACTERS,
            )
        if self.version < 0:
            raise KnowledgeContractError("version must be non-negative")
        if self.review_state is ReviewState.REVIEWED and (
            self.competency_id is None or self.provenance.source_block_id is None
        ):
            raise KnowledgeContractError(
                "reviewed questions require block provenance and competency classification"
            )

    @property
    def embedding_status(self) -> EmbeddingStatus:
        if self.embedding_configurations:
            return EmbeddingStatus.EMBEDDED
        return EmbeddingStatus.NOT_EMBEDDED


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: UUID
    curriculum_version_id: UUID
    chunk_type: ChunkType
    text: str
    educational_boundary: str
    sequence: int
    provenance: Provenance
    unit_id: UUID | None = None
    lesson_id: UUID | None = None
    review_state: ReviewState = ReviewState.DRAFT
    competency_id: UUID | None = None
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding_configurations: tuple[EmbeddingConfigurationMetadata, ...] = ()

    def __post_init__(self) -> None:
        if self.lesson_id is not None and self.unit_id is None:
            raise KnowledgeContractError("lesson_id requires unit_id")
        if not self.text.strip() or not self.educational_boundary.strip():
            raise KnowledgeContractError("chunk text and educational boundary must be non-blank")
        if self.sequence < 0:
            raise KnowledgeContractError("chunk sequence must be non-negative")
        if self.version < 0:
            raise KnowledgeContractError("version must be non-negative")
        if self.review_state is ReviewState.REVIEWED and (
            self.competency_id is None or self.provenance.source_block_id is None
        ):
            raise KnowledgeContractError(
                "reviewed chunks require block provenance and competency classification"
            )

    @property
    def embedding_status(self) -> EmbeddingStatus:
        if self.embedding_configurations:
            return EmbeddingStatus.EMBEDDED
        return EmbeddingStatus.NOT_EMBEDDED


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
