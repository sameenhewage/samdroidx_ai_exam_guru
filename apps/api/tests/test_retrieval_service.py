import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import pytest

from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.retrieval.context import ContextLimits, ContextTrust, OpaqueRetrievalContext
from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalScope,
    VectorCandidate,
)
from exam_guru_api.retrieval.fusion import FusedCandidate, FusionConfig
from exam_guru_api.retrieval.repository import RetrievalCandidateSet
from exam_guru_api.retrieval.service import (
    HybridCandidateRepository,
    HybridRetrievalLatency,
    HybridRetrievalResult,
    HybridRetrievalService,
)
from tests.test_retrieval_fixtures import (
    EMBEDDING_FINGERPRINT,
    OTHER_MEDIUM_ID,
    PROMPT_INJECTION_TEXT,
    grade_five_filter,
    grade_five_scope,
    lexical,
    retrieval_record,
    vector,
)


def embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="fixture-provider",
        model="fixture-model",
        dimension=3,
        version="v1",
        config_fingerprint=EMBEDDING_FINGERPRINT,
    )


@dataclass
class FakeCandidateRepository:
    candidates: RetrievalCandidateSet
    embedding_config: EmbeddingConfig = field(default_factory=embedding_config)
    calls: list[tuple[str, tuple[float, ...], RetrievalScope]] = field(default_factory=list)

    async def retrieve_candidates(
        self,
        *,
        query: str,
        query_vector: tuple[float, ...],
        filters: RetrievalScope,
    ) -> RetrievalCandidateSet:
        self.calls.append((query, query_vector, filters))
        return self.candidates


def test_service_feeds_existing_deterministic_fusion_and_opaque_context() -> None:
    injected = retrieval_record(501, PROMPT_INJECTION_TEXT, block_id=1_501)
    second = retrieval_record(502, "A square has four equal sides.", page_number=3, block_id=1_502)
    repository = FakeCandidateRepository(
        RetrievalCandidateSet(
            lexical_candidates=(lexical(injected, 10.0), lexical(second, 5.0)),
            vector_candidates=(vector(second, 0.99), vector(injected, 0.8)),
        )
    )
    service = HybridRetrievalService(
        cast(HybridCandidateRepository, repository),
        fusion_config=FusionConfig(limit=2, rank_constant=60, max_candidates_per_channel=4),
        context_limits=ContextLimits(
            max_items=2,
            max_total_characters=200,
            max_item_characters=100,
        ),
    )

    result = asyncio.run(
        service.retrieve(
            query="square perimeter",
            query_vector=(1, 0.0, 0),
            filters=grade_five_filter(),
        )
    )

    assert isinstance(result, HybridRetrievalResult)
    assert repository.calls == [("square perimeter", (1.0, 0.0, 0.0), grade_five_filter())]
    assert [candidate.record.chunk_id for candidate in result.ranked_candidates] == [
        injected.chunk_id,
        second.chunk_id,
    ]
    assert result.context.items[0].text == PROMPT_INJECTION_TEXT
    assert result.context.items[0].trust is ContextTrust.UNTRUSTED_SOURCE_DATA
    assert result.context.items[0].provenances == (injected.provenance,)
    assert result.embedding_config == embedding_config()
    assert service.embedding_config == embedding_config()
    assert result.lexical_candidate_count == 2
    assert result.vector_candidate_count == 2


@pytest.mark.parametrize(
    "query_vector",
    [
        (1.0, 0.0),
        (1.0, math.nan, 0.0),
        (1.0, math.inf, 0.0),
        (0.0, 0.0, 0.0),
    ],
)
def test_service_rejects_invalid_query_vectors_before_repository_call(
    query_vector: tuple[float, ...],
) -> None:
    repository = FakeCandidateRepository(RetrievalCandidateSet((), ()))
    service = HybridRetrievalService(cast(HybridCandidateRepository, repository))

    with pytest.raises(RetrievalContractError):
        asyncio.run(
            service.retrieve(
                query="square",
                query_vector=query_vector,
                filters=grade_five_filter(),
            )
        )
    assert repository.calls == []


def test_service_exposes_only_hard_scoped_channels_with_phase_latency() -> None:
    allowed = retrieval_record(503, "Allowed evidence", block_id=1_503)
    forbidden = retrieval_record(
        504,
        "Stronger forbidden evidence",
        scope=grade_five_scope(medium_id=OTHER_MEDIUM_ID),
        block_id=1_504,
    )
    repository = FakeCandidateRepository(
        RetrievalCandidateSet(
            lexical_candidates=(lexical(forbidden, 100.0), lexical(allowed, 1.0)),
            vector_candidates=(vector(forbidden, 1.0), vector(allowed, 0.5)),
        )
    )
    ticks = iter((1.000, 1.002, 1.002, 1.005, 1.005, 1.009))
    service = HybridRetrievalService(
        cast(HybridCandidateRepository, repository),
        clock=lambda: next(ticks),
    )

    result = asyncio.run(
        service.retrieve(
            query="allowed",
            query_vector=(1.0, 0.0, 0.0),
            filters=grade_five_filter(),
        )
    )

    assert result.lexical_candidates == (lexical(allowed, 1.0),)
    assert result.vector_candidates == (vector(allowed, 0.5),)
    assert result.filtered_candidate_count == 2
    assert result.latency == HybridRetrievalLatency(
        candidate_retrieval_ms=2.0,
        fusion_ms=3.0,
        context_building_ms=4.0,
    )
    assert all(
        grade_five_filter().allows(candidate.record.scope)
        for candidate in (*result.lexical_candidates, *result.vector_candidates)
    )


def test_service_preserves_empty_bounded_result() -> None:
    repository = FakeCandidateRepository(RetrievalCandidateSet((), ()))
    service = HybridRetrievalService(cast(HybridCandidateRepository, repository))

    result = asyncio.run(
        service.retrieve(
            query="unmatched query",
            query_vector=(1.0, 0.0, 0.0),
            filters=grade_five_filter(),
        )
    )

    assert result.ranked_candidates == ()
    assert result.context.items == ()
    assert result.context.character_count == 0
    assert result.context.omitted_candidate_count == 0


def test_service_requires_declared_repository_configuration_and_typed_settings() -> None:
    repository = FakeCandidateRepository(RetrievalCandidateSet((), ()))

    with pytest.raises(RetrievalContractError, match="fusion_config"):
        HybridRetrievalService(
            cast(HybridCandidateRepository, repository),
            fusion_config=cast(FusionConfig, "invalid"),
        )
    with pytest.raises(RetrievalContractError, match="context_limits"):
        HybridRetrievalService(
            cast(HybridCandidateRepository, repository),
            context_limits=cast(ContextLimits, "invalid"),
        )
    with pytest.raises(RetrievalContractError, match="clock"):
        HybridRetrievalService(
            cast(HybridCandidateRepository, repository),
            clock=cast(Callable[[], float], "invalid"),
        )

    repository.embedding_config = cast(EmbeddingConfig, None)
    with pytest.raises(RetrievalContractError, match="embedding configuration"):
        HybridRetrievalService(cast(HybridCandidateRepository, repository))


@pytest.mark.parametrize("query", ["", "   ", "x" * 4_097, cast(str, 1)])
def test_service_rejects_invalid_queries_before_repository_call(query: str) -> None:
    repository = FakeCandidateRepository(RetrievalCandidateSet((), ()))
    service = HybridRetrievalService(cast(HybridCandidateRepository, repository))

    with pytest.raises(RetrievalContractError, match="query"):
        asyncio.run(
            service.retrieve(
                query=query,
                query_vector=(1.0, 0.0, 0.0),
                filters=grade_five_filter(),
            )
        )
    assert repository.calls == []


def test_service_rejects_invalid_filters_and_repository_result() -> None:
    repository = FakeCandidateRepository(RetrievalCandidateSet((), ()))
    service = HybridRetrievalService(cast(HybridCandidateRepository, repository))

    with pytest.raises(RetrievalContractError, match="filters"):
        asyncio.run(
            service.retrieve(
                query="square",
                query_vector=(1.0, 0.0, 0.0),
                filters=cast(RetrievalScope, "invalid"),
            )
        )
    assert repository.calls == []

    repository.candidates = cast(RetrievalCandidateSet, "invalid")
    with pytest.raises(RetrievalContractError, match="RetrievalCandidateSet"):
        asyncio.run(
            service.retrieve(
                query="square",
                query_vector=(1.0, 0.0, 0.0),
                filters=grade_five_filter(),
            )
        )


def test_hybrid_result_rejects_untyped_payloads_and_invalid_counts() -> None:
    repository = FakeCandidateRepository(RetrievalCandidateSet((), ()))
    valid = asyncio.run(
        HybridRetrievalService(cast(HybridCandidateRepository, repository)).retrieve(
            query="square",
            query_vector=(1.0, 0.0, 0.0),
            filters=grade_five_filter(),
        )
    )

    with pytest.raises(RetrievalContractError, match="FusedCandidate"):
        HybridRetrievalResult(
            ranked_candidates=cast(tuple[FusedCandidate, ...], []),
            context=valid.context,
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
        )
    with pytest.raises(RetrievalContractError, match="FusedCandidate"):
        HybridRetrievalResult(
            ranked_candidates=cast(tuple[FusedCandidate, ...], ("invalid",)),
            context=valid.context,
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
        )
    with pytest.raises(RetrievalContractError, match="OpaqueRetrievalContext"):
        HybridRetrievalResult(
            ranked_candidates=(),
            context=cast(OpaqueRetrievalContext, None),
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
        )
    for lexical_count, vector_count, field_name in (
        (-1, 0, "lexical_candidate_count"),
        (0, -1, "vector_candidate_count"),
        (cast(int, "0"), 0, "lexical_candidate_count"),
        (0, True, "vector_candidate_count"),
    ):
        with pytest.raises(RetrievalContractError, match=field_name):
            HybridRetrievalResult(
                ranked_candidates=(),
                context=valid.context,
                embedding_config=embedding_config(),
                lexical_candidate_count=lexical_count,
                vector_candidate_count=vector_count,
            )

    with pytest.raises(RetrievalContractError, match="filtered_candidate_count"):
        HybridRetrievalResult(
            ranked_candidates=(),
            context=valid.context,
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
            filtered_candidate_count=-1,
        )
    with pytest.raises(RetrievalContractError, match="LexicalCandidate"):
        HybridRetrievalResult(
            ranked_candidates=(),
            context=valid.context,
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
            lexical_candidates=cast(tuple[LexicalCandidate, ...], ("invalid",)),
        )
    with pytest.raises(RetrievalContractError, match="VectorCandidate"):
        HybridRetrievalResult(
            ranked_candidates=(),
            context=valid.context,
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
            vector_candidates=cast(tuple[VectorCandidate, ...], ("invalid",)),
        )
    with pytest.raises(RetrievalContractError, match="HybridRetrievalLatency"):
        HybridRetrievalResult(
            ranked_candidates=(),
            context=valid.context,
            embedding_config=embedding_config(),
            lexical_candidate_count=0,
            vector_candidate_count=0,
            latency=cast(HybridRetrievalLatency, "invalid"),
        )


@pytest.mark.parametrize("value", [-1.0, math.inf, math.nan, True, cast(float, "1")])
def test_hybrid_retrieval_latency_rejects_invalid_values(value: float) -> None:
    with pytest.raises(RetrievalContractError, match="candidate_retrieval_ms"):
        HybridRetrievalLatency(candidate_retrieval_ms=value)
