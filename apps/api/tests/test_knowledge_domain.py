from uuid import UUID

import pytest

from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    KnowledgeContractError,
    Provenance,
    QuestionType,
    ReviewState,
    transition_review_state,
)
from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
    EmbeddingContractError,
)


def provenance() -> Provenance:
    return Provenance(source_document_id=UUID(int=1), page_number=2, source_block_id=UUID(int=3))


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
