"""Deterministic, provider-independent retrieval and evaluation core."""

from exam_guru_api.retrieval.context import (
    ContextLimits,
    ContextTrust,
    OpaqueRetrievalContext,
    UntrustedContextItem,
    build_context,
)
from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalRecord,
    RetrievalScope,
    SourceProvenance,
    TaxonomyScope,
    VectorCandidate,
)
from exam_guru_api.retrieval.evaluation import (
    RelevanceJudgment,
    RetrievalEvalCase,
    RetrievalEvalObservation,
    RetrievalEvaluationReport,
    RetrievalMetrics,
    evaluate_case,
    evaluate_suite,
    leakage_rate,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from exam_guru_api.retrieval.fusion import (
    FusedCandidate,
    FusionConfig,
    VectorSpaceMismatchError,
    fuse_candidates,
)
from exam_guru_api.retrieval.repository import (
    MAX_POSTGRES_CANDIDATES,
    PostgresHybridRetrievalRepository,
    RetrievalCandidateSet,
    validate_embedding_config,
    validate_query_vector,
)
from exam_guru_api.retrieval.service import (
    HybridCandidateRepository,
    HybridRetrievalResult,
    HybridRetrievalService,
)

__all__ = [
    "MAX_POSTGRES_CANDIDATES",
    "ContextLimits",
    "ContextTrust",
    "FusedCandidate",
    "FusionConfig",
    "HybridCandidateRepository",
    "HybridRetrievalResult",
    "HybridRetrievalService",
    "LexicalCandidate",
    "OpaqueRetrievalContext",
    "PostgresHybridRetrievalRepository",
    "RelevanceJudgment",
    "RetrievalCandidateSet",
    "RetrievalContractError",
    "RetrievalEvalCase",
    "RetrievalEvalObservation",
    "RetrievalEvaluationReport",
    "RetrievalMetrics",
    "RetrievalRecord",
    "RetrievalScope",
    "SourceProvenance",
    "TaxonomyScope",
    "UntrustedContextItem",
    "VectorCandidate",
    "VectorSpaceMismatchError",
    "build_context",
    "evaluate_case",
    "evaluate_suite",
    "fuse_candidates",
    "leakage_rate",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "validate_embedding_config",
    "validate_query_vector",
]
