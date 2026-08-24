import asyncio
import math
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalScope,
    VectorCandidate,
)
from exam_guru_api.retrieval.repository import (
    MAX_POSTGRES_CANDIDATES,
    PostgresHybridRetrievalRepository,
    RetrievalCandidateSet,
    validate_query_vector,
)
from tests.test_retrieval_fixtures import (
    COMPETENCY_ID,
    CURRICULUM_ID,
    EMBEDDING_FINGERPRINT,
    EXAM_ID,
    LEARNING_CONCEPT_ID,
    MEDIUM_ID,
    PROMPT_INJECTION_TEXT,
    SKILL_ID,
    SUB_SKILL_ID,
    grade_five_filter,
    grade_five_scope,
)


def embedding_config(*, dimension: int = 3) -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="fixture-provider",
        model="fixture-model",
        dimension=dimension,
        version="v1",
        config_fingerprint=EMBEDDING_FINGERPRINT,
    )


class StubMappings:
    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> Sequence[dict[str, Any]]:
        return self._rows


class StubResult:
    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> StubMappings:
        return StubMappings(self._rows)


class RecordingSession:
    def __init__(self, result_rows: Sequence[Sequence[dict[str, Any]]]) -> None:
        self._result_rows = iter(result_rows)
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> StubResult:
        self.statements.append(statement)
        return StubResult(next(self._result_rows))


def candidate_row(*, record_id: int, score: float, text: str) -> dict[str, Any]:
    return {
        "record_id": UUID(int=record_id),
        "text": text,
        "grade": 5,
        "exam_id": EXAM_ID,
        "medium_id": MEDIUM_ID,
        "curriculum_version_id": CURRICULUM_ID,
        "competency_id": COMPETENCY_ID,
        "skill_id": SKILL_ID,
        "sub_skill_id": SUB_SKILL_ID,
        "learning_concept_id": LEARNING_CONCEPT_ID,
        "source_document_id": UUID(int=8_000 + record_id),
        "page_number": 2,
        "source_block_id": UUID(int=9_000 + record_id),
        "score": score,
    }


def compile_postgres(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"render_postcompile": True},
        )
    )


def test_statements_scope_reviewed_records_before_postgres_ranking() -> None:
    repository = PostgresHybridRetrievalRepository(
        cast(AsyncSession, RecordingSession(((), ()))),
        embedding_config=embedding_config(),
        candidate_limit=17,
    )

    lexical_sql = compile_postgres(
        repository.build_lexical_statement(
            query="square perimeter",
            filters=grade_five_filter(),
        )
    )
    vector_sql = compile_postgres(
        repository.build_vector_statement(
            query_vector=(1.0, 0.0, 0.0),
            filters=grade_five_filter(),
        )
    )

    assert "WITH reviewed_scoped_records AS" in lexical_sql
    assert "UNION ALL" in lexical_sql
    assert "knowledge_chunks.review_state =" in lexical_sql
    assert "historical_questions.review_state =" in lexical_sql
    assert "exam_configurations.grade =" in lexical_sql
    assert "exam_configurations.id =" in lexical_sql
    assert "media.id =" in lexical_sql
    assert "curriculum_versions.id =" in lexical_sql
    assert "exam_configurations.active IS true" in lexical_sql
    assert "media.active IS true" in lexical_sql
    assert "curriculum_versions.active IS true" in lexical_sql
    assert "chunk_competency.id =" in lexical_sql
    assert "question_competency.id =" in lexical_sql
    for taxonomy_alias in (
        "chunk_skill",
        "chunk_sub_skill",
        "chunk_learning_concept",
        "question_skill",
        "question_sub_skill",
        "question_learning_concept",
    ):
        assert f"{taxonomy_alias}.active IS true" in lexical_sql
        assert f"{taxonomy_alias}.review_state =" in lexical_sql
    assert lexical_sql.count("exam_configurations.active IS true") == 2
    assert lexical_sql.count("media.active IS true") == 2
    assert lexical_sql.count("curriculum_versions.active IS true") == 2
    assert "knowledge_chunks.skill_id =" in lexical_sql
    assert "historical_questions.skill_id =" in lexical_sql
    assert "websearch_to_tsquery" in lexical_sql
    assert "to_tsvector" in lexical_sql
    assert " @@ " in lexical_sql
    assert "ts_rank_cd" in lexical_sql
    assert "ORDER BY score DESC" in lexical_sql
    assert "LIMIT" in lexical_sql
    assert lexical_sql.index("reviewed_scoped_records AS") < lexical_sql.index("ts_rank_cd")

    assert "WITH reviewed_scoped_records AS" in vector_sql
    assert "knowledge_embeddings" in vector_sql
    assert "embedding_configurations" in vector_sql
    assert "embedding_configurations.provider =" in vector_sql
    assert "embedding_configurations.model =" in vector_sql
    assert "embedding_configurations.version =" in vector_sql
    assert "embedding_configurations.config_fingerprint =" in vector_sql
    assert "embedding_configurations.dimension =" in vector_sql
    assert "knowledge_embeddings.embedding_dimension =" in vector_sql
    assert " <=> " in vector_sql
    assert "ORDER BY score DESC" in vector_sql
    assert "LIMIT" in vector_sql
    assert vector_sql.index("reviewed_scoped_records AS") < vector_sql.index(" <=> ")


def test_repository_executes_bounded_channels_and_maps_complete_provenance() -> None:
    lexical_row = candidate_row(record_id=101, score=0.75, text=PROMPT_INJECTION_TEXT)
    vector_row = candidate_row(record_id=102, score=0.95, text="Reviewed vector evidence")
    session = RecordingSession(((lexical_row,), (vector_row,)))
    repository = PostgresHybridRetrievalRepository(
        cast(AsyncSession, session),
        embedding_config=embedding_config(),
        candidate_limit=5,
    )

    candidates = asyncio.run(
        repository.retrieve_candidates(
            query="square perimeter",
            query_vector=(1, 0.0, 0),
            filters=grade_five_filter(),
        )
    )

    assert isinstance(candidates, RetrievalCandidateSet)
    assert len(session.statements) == 2
    assert len(candidates.lexical_candidates) == 1
    assert len(candidates.vector_candidates) == 1
    lexical = candidates.lexical_candidates[0]
    vector = candidates.vector_candidates[0]
    assert lexical.record.text == PROMPT_INJECTION_TEXT
    assert lexical.record.provenance.source_document_id == lexical_row["source_document_id"]
    assert lexical.record.provenance.page_number == 2
    assert lexical.record.provenance.source_block_id == lexical_row["source_block_id"]
    assert lexical.record.scope == vector.record.scope
    assert vector.embedding_config_fingerprint == EMBEDDING_FINGERPRINT
    assert vector.score == pytest.approx(0.95)


@pytest.mark.parametrize(
    ("query_vector", "message"),
    [
        ((1.0, 0.0), "dimension"),
        ((1.0, 0.0, 0.0, 0.0), "dimension"),
        ((1.0, math.nan, 0.0), "finite"),
        ((1.0, math.inf, 0.0), "finite"),
        ((1.0, True, 0.0), "finite"),
        ((1.0, "0", 0.0), "finite"),
        ((1e101, 0.0, 0.0), "magnitude"),
        ((0.0, 0.0, 0.0), "non-zero"),
    ],
)
def test_query_vector_dimension_and_finite_values_are_enforced(
    query_vector: tuple[object, ...],
    message: str,
) -> None:
    with pytest.raises(RetrievalContractError, match=message):
        validate_query_vector(query_vector, expected_dimension=3)


def test_query_vector_is_snapshotted_as_floats() -> None:
    assert validate_query_vector((1, -2.5, 3), expected_dimension=3) == (1.0, -2.5, 3.0)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "query"),
        ("   ", "query"),
        ("x" * 4_097, "query"),
        (cast(str, 1), "query"),
    ],
)
def test_repository_rejects_invalid_queries_before_database_access(
    query: str,
    message: str,
) -> None:
    session = RecordingSession(((), ()))
    repository = PostgresHybridRetrievalRepository(
        cast(AsyncSession, session),
        embedding_config=embedding_config(),
    )

    with pytest.raises(RetrievalContractError, match=message):
        asyncio.run(
            repository.retrieve_candidates(
                query=query,
                query_vector=(1.0, 0.0, 0.0),
                filters=grade_five_filter(),
            )
        )
    assert session.statements == []


@pytest.mark.parametrize(
    "candidate_limit",
    [0, True, MAX_POSTGRES_CANDIDATES + 1, cast(int, "5")],
)
def test_repository_candidate_limit_is_bounded(candidate_limit: int) -> None:
    with pytest.raises(RetrievalContractError, match="candidate_limit"):
        PostgresHybridRetrievalRepository(
            cast(AsyncSession, RecordingSession(((), ()))),
            embedding_config=embedding_config(),
            candidate_limit=candidate_limit,
        )


@pytest.mark.parametrize(
    "config",
    [
        EmbeddingConfig("", "model", 3, "v1", "fingerprint"),
        EmbeddingConfig("provider", " ", 3, "v1", "fingerprint"),
        EmbeddingConfig("provider", "model", 0, "v1", "fingerprint"),
        EmbeddingConfig("provider", "model", 3, "", "fingerprint"),
        EmbeddingConfig("provider", "model", 3, "v1", " "),
    ],
)
def test_repository_requires_one_valid_declared_embedding_configuration(
    config: EmbeddingConfig,
) -> None:
    with pytest.raises(RetrievalContractError, match="embedding configuration"):
        PostgresHybridRetrievalRepository(
            cast(AsyncSession, RecordingSession(((), ()))),
            embedding_config=config,
        )


@pytest.mark.parametrize(
    "config",
    [
        EmbeddingConfig(cast(str, 1), "model", 3, "v1", "fingerprint"),
        EmbeddingConfig("p" * 65, "model", 3, "v1", "fingerprint"),
        EmbeddingConfig("provider", "model", True, "v1", "fingerprint"),
        EmbeddingConfig("provider", "model", 4_097, "v1", "fingerprint"),
    ],
)
def test_embedding_configuration_rejects_wrong_types_and_database_overflow(
    config: EmbeddingConfig,
) -> None:
    with pytest.raises(RetrievalContractError, match="embedding configuration"):
        PostgresHybridRetrievalRepository(
            cast(AsyncSession, RecordingSession(((), ()))),
            embedding_config=config,
        )


def test_candidate_set_rejects_untyped_channel_values() -> None:
    with pytest.raises(RetrievalContractError, match="LexicalCandidate"):
        RetrievalCandidateSet(
            lexical_candidates=cast(tuple[LexicalCandidate, ...], ("invalid",)),
            vector_candidates=(),
        )
    with pytest.raises(RetrievalContractError, match="VectorCandidate"):
        RetrievalCandidateSet(
            lexical_candidates=(),
            vector_candidates=cast(tuple[VectorCandidate, ...], ("invalid",)),
        )
    with pytest.raises(RetrievalContractError, match="LexicalCandidate"):
        RetrievalCandidateSet(
            lexical_candidates=cast(tuple[LexicalCandidate, ...], []),
            vector_candidates=(),
        )
    with pytest.raises(RetrievalContractError, match="VectorCandidate"):
        RetrievalCandidateSet(
            lexical_candidates=(),
            vector_candidates=cast(tuple[VectorCandidate, ...], []),
        )


def test_repository_builders_reject_untyped_filters() -> None:
    repository = PostgresHybridRetrievalRepository(
        cast(AsyncSession, RecordingSession(((), ()))),
        embedding_config=embedding_config(),
    )
    with pytest.raises(RetrievalContractError, match="filters"):
        repository.build_lexical_statement(
            query="square",
            filters=cast(RetrievalScope, "invalid"),
        )
    with pytest.raises(RetrievalContractError, match="filters"):
        repository.build_vector_statement(
            query_vector=(1.0, 0.0, 0.0),
            filters=cast(RetrievalScope, "invalid"),
        )


@pytest.mark.parametrize("expected_dimension", [0, True, 4_097, cast(int, "3")])
def test_query_vector_rejects_invalid_declared_dimension(expected_dimension: int) -> None:
    with pytest.raises(RetrievalContractError, match="dimension"):
        validate_query_vector((1.0, 0.0, 0.0), expected_dimension=expected_dimension)


@pytest.mark.parametrize("query_vector", ["100", b"100"])
def test_query_vector_rejects_string_like_sequences(query_vector: object) -> None:
    with pytest.raises(RetrievalContractError, match="numeric sequence"):
        validate_query_vector(
            cast(Sequence[object], query_vector),
            expected_dimension=3,
        )


def test_all_taxonomy_levels_are_hard_filters_in_both_scoped_branches() -> None:
    repository = PostgresHybridRetrievalRepository(
        cast(AsyncSession, RecordingSession(((), ()))),
        embedding_config=embedding_config(),
    )

    sql = compile_postgres(
        repository.build_lexical_statement(
            query="square",
            filters=grade_five_scope(),
        )
    )

    for table in ("knowledge_chunks", "historical_questions"):
        assert f"{table}.skill_id =" in sql
        assert f"{table}.sub_skill_id =" in sql
        assert f"{table}.learning_concept_id =" in sql
    assert repository.candidate_limit == 50
    assert repository.embedding_config == embedding_config()
