from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

from exam_guru_api.knowledge.domain import (
    ChunkType,
    DifficultyLabel,
    EmbeddingConfigurationMetadata,
    EmbeddingStatus,
    HistoricalQuestion,
    HistoricalQuestionMarkingData,
    KnowledgeChunk,
    KnowledgeContractError,
    Provenance,
    QuestionType,
    ReviewState,
    marking_data_to_dict,
    transition_review_state,
)
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingContractError,
)


def provenance() -> Provenance:
    return Provenance(source_document_id=UUID(int=1), page_number=2, source_block_id=UUID(int=3))


def historical_question(**changes: object) -> HistoricalQuestion:
    return replace(
        HistoricalQuestion(
            id=UUID(int=10),
            curriculum_version_id=UUID(int=11),
            year=2020,
            paper_code="P1",
            question_number="1",
            text="Synthetic question text",
            question_type=QuestionType.MULTIPLE_CHOICE,
            marks=2,
            provenance=provenance(),
        ),
        **cast(Any, changes),
    )


def test_historical_question_metadata_is_optional_typed_bounded_and_immutable() -> None:
    supplied_marking_data: dict[str, object] = {
        "criteria": [{"description": "Selects sixteen.", "marks": 2}],
        "alternative_answers": ["16"],
    }
    question = historical_question(
        media_references=("source://page/2/figure/1",),
        options=("12", "14", "16", "18"),
        answer="16",
        marking_guidance="Award two marks for the exact option.",
        marking_data=supplied_marking_data,
        question_archetype="single_best_answer",
        difficulty_label=DifficultyLabel.MEDIUM,
        difficulty_confidence=0.85,
        difficulty_source="reviewer_confirmed",
    )
    supplied_marking_data["criteria"] = []

    assert question.media_references == ("source://page/2/figure/1",)
    assert question.options == ("12", "14", "16", "18")
    assert question.answer == "16"
    assert question.marking_guidance == "Award two marks for the exact option."
    assert marking_data_to_dict(question.marking_data) == {
        "alternative_answers": ["16"],
        "criteria": [{"description": "Selects sixteen.", "marks": 2}],
    }
    assert question.question_archetype == "single_best_answer"
    assert question.difficulty_label is DifficultyLabel.MEDIUM
    assert question.difficulty_confidence == 0.85
    assert question.difficulty_source == "reviewer_confirmed"

    unavailable = historical_question(question_number="2")
    assert unavailable.media_references is None
    assert unavailable.options is None
    assert unavailable.answer is None
    assert unavailable.marking_guidance is None
    assert unavailable.marking_data is None
    assert marking_data_to_dict(unavailable.marking_data) is None
    assert unavailable.embedding_status is EmbeddingStatus.NOT_EMBEDDED
    assert unavailable.question_archetype is None
    assert unavailable.difficulty_label is None
    assert unavailable.difficulty_confidence is None
    assert unavailable.difficulty_source is None


@pytest.mark.parametrize(
    "canonical_json",
    ["not-json", "[]", "{}", '{"score":NaN}'],
)
def test_marking_data_wrapper_rejects_malformed_direct_construction(
    canonical_json: str,
) -> None:
    with pytest.raises(KnowledgeContractError, match="marking_data"):
        HistoricalQuestionMarkingData(canonical_json)


def test_marking_data_snapshot_rejects_resource_abuse_and_non_json_values() -> None:
    cyclic_object: dict[str, object] = {}
    cyclic_object["self"] = cyclic_object
    cyclic_array: list[object] = []
    cyclic_array.append(cyclic_array)
    deeply_nested: dict[str, object] = {"value": "leaf"}
    for _ in range(10):
        deeply_nested = {"nested": deeply_nested}
    excessive_nodes: dict[str, object] = {"groups": [list(range(128)) for _ in range(9)]}
    invalid_values: tuple[object, ...] = (
        {"value": "x" * 16_001},
        {f"key-{index}": index for index in range(129)},
        {"values": list(range(129))},
        {" invalid": "value"},
        {"unsupported": UUID(int=1)},
        cyclic_object,
        {"array": cyclic_array},
        deeply_nested,
        excessive_nodes,
        {f"key-{index}": "x" * 14_000 for index in range(5)},
    )

    for marking_data in invalid_values:
        with pytest.raises(KnowledgeContractError, match="marking_data"):
            historical_question(marking_data=marking_data)

    scalar_data = {"none": None, "boolean": True, "ratio": 0.5}
    question = historical_question(marking_data=scalar_data)
    assert marking_data_to_dict(question.marking_data) == scalar_data


@pytest.mark.parametrize(
    "changes",
    [
        {"difficulty_label": DifficultyLabel.EASY},
        {"difficulty_confidence": 0.5},
        {"difficulty_source": "reviewer_confirmed"},
        {
            "difficulty_label": DifficultyLabel.HARD,
            "difficulty_confidence": float("nan"),
            "difficulty_source": "reviewer_confirmed",
        },
        {
            "difficulty_label": DifficultyLabel.HARD,
            "difficulty_confidence": float("inf"),
            "difficulty_source": "reviewer_confirmed",
        },
        {
            "difficulty_label": DifficultyLabel.HARD,
            "difficulty_confidence": -0.01,
            "difficulty_source": "reviewer_confirmed",
        },
        {
            "difficulty_label": DifficultyLabel.HARD,
            "difficulty_confidence": 1.01,
            "difficulty_source": "reviewer_confirmed",
        },
        {
            "difficulty_label": DifficultyLabel.HARD,
            "difficulty_confidence": True,
            "difficulty_source": "reviewer_confirmed",
        },
    ],
)
def test_historical_question_requires_complete_finite_difficulty_evidence(
    changes: dict[str, object],
) -> None:
    with pytest.raises(KnowledgeContractError, match="difficulty"):
        historical_question(**changes)


def test_question_options_are_unique_and_constructed_answers_remain_opaque() -> None:
    with pytest.raises(KnowledgeContractError, match="options"):
        historical_question(options=("12", "12"))

    constructed_response = historical_question(
        question_type=QuestionType.SHORT_ANSWER,
        options=("14", "fourteen"),
        answer="Any equivalent expression equal to fourteen",
    )
    assert constructed_response.answer == "Any equivalent expression equal to fourteen"


def test_historical_mcq_answer_preserves_source_label_encoding() -> None:
    question = historical_question(options=("Twelve", "Fourteen"), answer="B")

    assert question.answer == "B"


def test_historical_question_metadata_rejects_unbounded_or_malformed_values() -> None:
    invalid_values: tuple[dict[str, object], ...] = (
        {"media_references": ()},
        {"media_references": ["reference"]},
        {"media_references": (" ",)},
        {"media_references": tuple(f"reference-{index}" for index in range(33))},
        {"options": ("only-one",)},
        {"options": (" ", "valid")},
        {"options": tuple(f"option-{index}" for index in range(9))},
        {"answer": " "},
        {"marking_guidance": " "},
        {"marking_data": {}},
        {"marking_data": {"score": float("nan")}},
        {"question_archetype": " "},
        {
            "difficulty_label": "easy",
            "difficulty_confidence": 0.5,
            "difficulty_source": "reviewer_confirmed",
        },
        {
            "difficulty_label": DifficultyLabel.EASY,
            "difficulty_confidence": 0.5,
            "difficulty_source": " ",
        },
    )

    for changes in invalid_values:
        with pytest.raises(KnowledgeContractError):
            historical_question(**changes)


def test_historical_question_and_knowledge_chunk_preserve_reviewed_provenance() -> None:
    question = HistoricalQuestion(
        id=UUID(int=10),
        curriculum_version_id=UUID(int=11),
        year=2020,
        paper_code="P1",
        question_number="1",
        text="Synthetic question text",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=2,
        provenance=provenance(),
        review_state=ReviewState.REVIEWED,
        competency_id=UUID(int=12),
    )
    chunk = KnowledgeChunk(
        id=UUID(int=20),
        curriculum_version_id=UUID(int=11),
        chunk_type=ChunkType.EXPLANATION,
        text="Synthetic curriculum explanation",
        educational_boundary="Unit 1 / concept 2",
        sequence=0,
        provenance=provenance(),
        review_state=ReviewState.REVIEWED,
        competency_id=UUID(int=12),
    )

    assert question.provenance.page_number == 2
    assert chunk.educational_boundary == "Unit 1 / concept 2"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Provenance(source_document_id=UUID(int=1), page_number=0),
        lambda: HistoricalQuestion(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            year=2020,
            paper_code="P1",
            question_number="1",
            text="text",
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            provenance=provenance(),
            lesson_id=UUID(int=500),
        ),
        lambda: KnowledgeChunk(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            chunk_type=ChunkType.EXPLANATION,
            text="text",
            educational_boundary="Unit",
            sequence=0,
            provenance=provenance(),
            lesson_id=UUID(int=500),
        ),
        lambda: HistoricalQuestion(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            year=1899,
            paper_code="P1",
            question_number="1",
            text="text",
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            provenance=provenance(),
        ),
        lambda: HistoricalQuestion(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            year=2020,
            paper_code="",
            question_number="1",
            text="text",
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            provenance=provenance(),
        ),
        lambda: HistoricalQuestion(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            year=2020,
            paper_code="P1",
            question_number="1",
            text="text",
            question_type=QuestionType.SHORT_ANSWER,
            marks=0,
            provenance=provenance(),
        ),
        lambda: HistoricalQuestion(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            year=2020,
            paper_code="P1",
            question_number="1",
            text="text",
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            provenance=provenance(),
            review_state=ReviewState.REVIEWED,
        ),
        lambda: KnowledgeChunk(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            chunk_type=ChunkType.EXPLANATION,
            text="text",
            educational_boundary="",
            sequence=0,
            provenance=provenance(),
        ),
        lambda: KnowledgeChunk(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            chunk_type=ChunkType.EXPLANATION,
            text="text",
            educational_boundary="Unit",
            sequence=-1,
            provenance=provenance(),
        ),
        lambda: KnowledgeChunk(
            id=UUID(int=1),
            curriculum_version_id=UUID(int=2),
            chunk_type=ChunkType.EXPLANATION,
            text="text",
            educational_boundary="Unit",
            sequence=0,
            provenance=provenance(),
            review_state=ReviewState.REVIEWED,
        ),
    ],
)
def test_knowledge_contract_rejects_invalid_records(factory: object) -> None:
    with pytest.raises(KnowledgeContractError):
        factory()  # type: ignore[operator]


def test_review_state_machine_is_forward_only() -> None:
    assert (
        transition_review_state(ReviewState.DRAFT, ReviewState.IN_REVIEW) is ReviewState.IN_REVIEW
    )
    assert (
        transition_review_state(ReviewState.IN_REVIEW, ReviewState.REVIEWED) is ReviewState.REVIEWED
    )
    assert (
        transition_review_state(ReviewState.IN_REVIEW, ReviewState.REJECTED) is ReviewState.REJECTED
    )
    assert (
        transition_review_state(ReviewState.REVIEWED, ReviewState.REVIEWED) is ReviewState.REVIEWED
    )

    with pytest.raises(KnowledgeContractError):
        transition_review_state(ReviewState.REVIEWED, ReviewState.DRAFT)


def test_persisted_knowledge_metadata_tracks_version_timestamps_and_embedding_status() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    configuration = EmbeddingConfigurationMetadata(
        id=UUID(int=30),
        provider="fixture-provider",
        model="fixture-model",
        dimension=3,
        version="v1",
        config_fingerprint="fixture-space-v1",
    )
    question = HistoricalQuestion(
        id=UUID(int=10),
        curriculum_version_id=UUID(int=11),
        year=2020,
        paper_code="P1",
        question_number="1",
        text="Persisted question",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=2,
        provenance=provenance(),
        version=4,
        created_at=timestamp,
        updated_at=timestamp,
        embedding_configurations=(configuration,),
    )
    chunk = KnowledgeChunk(
        id=UUID(int=20),
        curriculum_version_id=UUID(int=11),
        chunk_type=ChunkType.EXPLANATION,
        text="Persisted chunk",
        educational_boundary="Unit",
        sequence=0,
        provenance=provenance(),
    )

    embedded_chunk = KnowledgeChunk(
        id=UUID(int=21),
        curriculum_version_id=UUID(int=11),
        chunk_type=ChunkType.EXPLANATION,
        text="Embedded chunk",
        educational_boundary="Unit",
        sequence=1,
        provenance=provenance(),
        embedding_configurations=(configuration,),
    )

    assert question.embedding_status is EmbeddingStatus.EMBEDDED
    assert chunk.embedding_status is EmbeddingStatus.NOT_EMBEDDED
    assert embedded_chunk.embedding_status is EmbeddingStatus.EMBEDDED
    assert question.version == 4
    assert question.created_at == question.updated_at == timestamp

    with pytest.raises(KnowledgeContractError, match="version"):
        HistoricalQuestion(
            id=UUID(int=40),
            curriculum_version_id=UUID(int=11),
            year=2020,
            paper_code="P1",
            question_number="2",
            text="Invalid version",
            question_type=QuestionType.SHORT_ANSWER,
            marks=1,
            provenance=provenance(),
            version=-1,
        )
    with pytest.raises(KnowledgeContractError, match="version"):
        KnowledgeChunk(
            id=UUID(int=41),
            curriculum_version_id=UUID(int=11),
            chunk_type=ChunkType.EXPLANATION,
            text="Invalid version",
            educational_boundary="Unit",
            sequence=0,
            provenance=provenance(),
            version=-1,
        )


def test_deterministic_embedding_is_versioned_bounded_and_repeatable() -> None:
    config = EmbeddingConfig(
        provider="deterministic",
        model="sha256-fixture",
        dimension=8,
        version="v1",
        config_fingerprint="fixture-config-v1",
    )
    provider = DeterministicEmbeddingProvider()

    first = provider.embed("Synthetic reviewed chunk", config)
    second = provider.embed("Synthetic reviewed chunk", config)
    different = provider.embed("Different chunk", config)

    assert first == second
    assert first.vector != different.vector
    assert len(first.vector) == 8
    assert first.config == config
    assert all(-1 <= value <= 1 for value in first.vector)


@pytest.mark.parametrize(
    "config",
    [
        EmbeddingConfig("", "model", 8, "v1", "fingerprint"),
        EmbeddingConfig("provider", "model", 0, "v1", "fingerprint"),
        EmbeddingConfig("provider", "model", 4097, "v1", "fingerprint"),
    ],
)
def test_embedding_contract_rejects_invalid_configuration(config: EmbeddingConfig) -> None:
    with pytest.raises(EmbeddingContractError):
        DeterministicEmbeddingProvider().embed("text", config)


def test_embedding_contract_rejects_blank_or_untrusted_dimension_mismatch() -> None:
    config = EmbeddingConfig("provider", "model", 4, "v1", "fingerprint")
    with pytest.raises(EmbeddingContractError):
        DeterministicEmbeddingProvider().embed(" ", config)
