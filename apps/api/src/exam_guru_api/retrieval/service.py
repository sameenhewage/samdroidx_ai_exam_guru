"""Application orchestration for deterministic PostgreSQL hybrid retrieval."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol

from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.retrieval.context import (
    ContextLimits,
    OpaqueRetrievalContext,
    build_context,
)
from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalFilters,
    RetrievalScope,
    RetrievalScopeSet,
    VectorCandidate,
)
from exam_guru_api.retrieval.fusion import FusedCandidate, FusionConfig, fuse_candidates
from exam_guru_api.retrieval.repository import (
    RetrievalCandidateSet,
    validate_embedding_config,
    validate_query_vector,
)

MAX_RETRIEVAL_QUERY_CHARACTERS = 4_096


class HybridCandidateRepository(Protocol):
    """Small candidate-source port used by the retrieval application service."""

    @property
    def embedding_config(self) -> EmbeddingConfig: ...

    async def retrieve_candidates(
        self,
        *,
        query: str,
        query_vector: Sequence[object],
        filters: RetrievalFilters,
    ) -> RetrievalCandidateSet: ...


def _elapsed_ms(start: float, end: float) -> float:
    return round(max(0.0, (end - start) * 1_000), 6)


@dataclass(frozen=True, slots=True)
class HybridRetrievalLatency:
    """Leakage-safe wall-clock durations for each deterministic retrieval phase."""

    candidate_retrieval_ms: float = 0.0
    fusion_ms: float = 0.0
    context_building_ms: float = 0.0

    def __post_init__(self) -> None:
        for field_name, value in (
            ("candidate_retrieval_ms", self.candidate_retrieval_ms),
            ("fusion_ms", self.fusion_ms),
            ("context_building_ms", self.context_building_ms),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise RetrievalContractError(f"{field_name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    """Ranked evidence, opaque context, and reproducible retrieval metadata."""

    ranked_candidates: tuple[FusedCandidate, ...]
    context: OpaqueRetrievalContext
    embedding_config: EmbeddingConfig
    lexical_candidate_count: int
    vector_candidate_count: int
    lexical_candidates: tuple[LexicalCandidate, ...] = ()
    vector_candidates: tuple[VectorCandidate, ...] = ()
    filtered_candidate_count: int = 0
    latency: HybridRetrievalLatency = field(default_factory=HybridRetrievalLatency)

    def __post_init__(self) -> None:
        if not isinstance(self.ranked_candidates, tuple) or any(
            not isinstance(candidate, FusedCandidate) for candidate in self.ranked_candidates
        ):
            raise RetrievalContractError(
                "ranked_candidates must be a tuple of FusedCandidate values"
            )
        if not isinstance(self.context, OpaqueRetrievalContext):
            raise RetrievalContractError("context must be an OpaqueRetrievalContext")
        validate_embedding_config(self.embedding_config)
        for field_name, value in (
            ("lexical_candidate_count", self.lexical_candidate_count),
            ("vector_candidate_count", self.vector_candidate_count),
            ("filtered_candidate_count", self.filtered_candidate_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RetrievalContractError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.lexical_candidates, tuple) or any(
            not isinstance(candidate, LexicalCandidate) for candidate in self.lexical_candidates
        ):
            raise RetrievalContractError(
                "lexical_candidates must be a tuple of LexicalCandidate values"
            )
        if not isinstance(self.vector_candidates, tuple) or any(
            not isinstance(candidate, VectorCandidate) for candidate in self.vector_candidates
        ):
            raise RetrievalContractError(
                "vector_candidates must be a tuple of VectorCandidate values"
            )
        if not isinstance(self.latency, HybridRetrievalLatency):
            raise RetrievalContractError("latency must be a HybridRetrievalLatency")


class HybridRetrievalService:
    """Fuse database channels and build bounded context without interpreting text."""

    def __init__(
        self,
        repository: HybridCandidateRepository,
        *,
        fusion_config: FusionConfig | None = None,
        context_limits: ContextLimits | None = None,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        active_fusion_config = FusionConfig() if fusion_config is None else fusion_config
        active_context_limits = ContextLimits() if context_limits is None else context_limits
        if not isinstance(active_fusion_config, FusionConfig):
            raise RetrievalContractError("fusion_config must be a FusionConfig")
        if not isinstance(active_context_limits, ContextLimits):
            raise RetrievalContractError("context_limits must be ContextLimits")
        if not callable(clock):
            raise RetrievalContractError("clock must be callable")
        embedding_config = validate_embedding_config(getattr(repository, "embedding_config", None))
        self._repository = repository
        self._embedding_config = embedding_config
        self._fusion_config = active_fusion_config
        self._context_limits = active_context_limits
        self._clock = clock

    @property
    def embedding_config(self) -> EmbeddingConfig:
        return self._embedding_config

    async def retrieve(
        self,
        *,
        query: str,
        query_vector: Sequence[object],
        filters: RetrievalFilters,
    ) -> HybridRetrievalResult:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_RETRIEVAL_QUERY_CHARACTERS
        ):
            raise RetrievalContractError("retrieval query must be non-blank and bounded")
        if not isinstance(filters, (RetrievalScope, RetrievalScopeSet)):
            raise RetrievalContractError("filters must be a RetrievalScope or RetrievalScopeSet")
        vector = validate_query_vector(
            query_vector,
            expected_dimension=self._embedding_config.dimension,
        )
        candidate_retrieval_started = self._clock()
        candidates = await self._repository.retrieve_candidates(
            query=query,
            query_vector=vector,
            filters=filters,
        )
        if not isinstance(candidates, RetrievalCandidateSet):
            raise RetrievalContractError("repository must return a RetrievalCandidateSet")
        lexical_candidates = tuple(
            sorted(
                (
                    candidate
                    for candidate in candidates.lexical_candidates
                    if filters.allows(candidate.record.scope)
                ),
                key=lambda candidate: (-candidate.score, candidate.record.chunk_id.int),
            )
        )
        vector_candidates = tuple(
            sorted(
                (
                    candidate
                    for candidate in candidates.vector_candidates
                    if filters.allows(candidate.record.scope)
                ),
                key=lambda candidate: (-candidate.score, candidate.record.chunk_id.int),
            )
        )
        filtered_candidate_count = (
            len(candidates.lexical_candidates)
            + len(candidates.vector_candidates)
            - len(lexical_candidates)
            - len(vector_candidates)
        )
        candidate_retrieval_finished = self._clock()

        fusion_started = self._clock()
        ranked = fuse_candidates(
            lexical_candidates,
            vector_candidates,
            filters=filters,
            embedding_config_fingerprint=self._embedding_config.config_fingerprint,
            config=self._fusion_config,
        )
        fusion_finished = self._clock()

        context_started = self._clock()
        context = build_context(ranked, limits=self._context_limits)
        context_finished = self._clock()
        return HybridRetrievalResult(
            ranked_candidates=ranked,
            context=context,
            embedding_config=self._embedding_config,
            lexical_candidate_count=len(lexical_candidates),
            vector_candidate_count=len(vector_candidates),
            lexical_candidates=lexical_candidates,
            vector_candidates=vector_candidates,
            filtered_candidate_count=filtered_candidate_count,
            latency=HybridRetrievalLatency(
                candidate_retrieval_ms=_elapsed_ms(
                    candidate_retrieval_started,
                    candidate_retrieval_finished,
                ),
                fusion_ms=_elapsed_ms(fusion_started, fusion_finished),
                context_building_ms=_elapsed_ms(context_started, context_finished),
            ),
        )
