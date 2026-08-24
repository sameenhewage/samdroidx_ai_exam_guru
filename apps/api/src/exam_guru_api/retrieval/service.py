"""Application orchestration for deterministic PostgreSQL hybrid retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.retrieval.context import (
    ContextLimits,
    OpaqueRetrievalContext,
    build_context,
)
from exam_guru_api.retrieval.domain import RetrievalContractError, RetrievalScope
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
        filters: RetrievalScope,
    ) -> RetrievalCandidateSet: ...


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    """Ranked evidence, opaque context, and reproducible retrieval metadata."""

    ranked_candidates: tuple[FusedCandidate, ...]
    context: OpaqueRetrievalContext
    embedding_config: EmbeddingConfig
    lexical_candidate_count: int
    vector_candidate_count: int

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
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RetrievalContractError(f"{field_name} must be a non-negative integer")


class HybridRetrievalService:
    """Fuse database channels and build bounded context without interpreting text."""

    def __init__(
        self,
        repository: HybridCandidateRepository,
        *,
        fusion_config: FusionConfig | None = None,
        context_limits: ContextLimits | None = None,
    ) -> None:
        active_fusion_config = FusionConfig() if fusion_config is None else fusion_config
        active_context_limits = ContextLimits() if context_limits is None else context_limits
        if not isinstance(active_fusion_config, FusionConfig):
            raise RetrievalContractError("fusion_config must be a FusionConfig")
        if not isinstance(active_context_limits, ContextLimits):
            raise RetrievalContractError("context_limits must be ContextLimits")
        embedding_config = validate_embedding_config(getattr(repository, "embedding_config", None))
        self._repository = repository
        self._embedding_config = embedding_config
        self._fusion_config = active_fusion_config
        self._context_limits = active_context_limits

    @property
    def embedding_config(self) -> EmbeddingConfig:
        return self._embedding_config

    async def retrieve(
        self,
        *,
        query: str,
        query_vector: Sequence[object],
        filters: RetrievalScope,
    ) -> HybridRetrievalResult:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_RETRIEVAL_QUERY_CHARACTERS
        ):
            raise RetrievalContractError("retrieval query must be non-blank and bounded")
        if not isinstance(filters, RetrievalScope):
            raise RetrievalContractError("filters must be a RetrievalScope")
        vector = validate_query_vector(
            query_vector,
            expected_dimension=self._embedding_config.dimension,
        )
        candidates = await self._repository.retrieve_candidates(
            query=query,
            query_vector=vector,
            filters=filters,
        )
        if not isinstance(candidates, RetrievalCandidateSet):
            raise RetrievalContractError("repository must return a RetrievalCandidateSet")
        ranked = fuse_candidates(
            candidates.lexical_candidates,
            candidates.vector_candidates,
            filters=filters,
            embedding_config_fingerprint=self._embedding_config.config_fingerprint,
            config=self._fusion_config,
        )
        context = build_context(ranked, limits=self._context_limits)
        return HybridRetrievalResult(
            ranked_candidates=ranked,
            context=context,
            embedding_config=self._embedding_config,
            lexical_candidate_count=len(candidates.lexical_candidates),
            vector_candidate_count=len(candidates.vector_candidates),
        )
