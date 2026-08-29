"""Deterministic retrieval metrics over fixed, identifier-based judgments.

Relevance and leakage are decided only from fixture identifiers and hard scope;
retrieved text (including prompt-like text) is never interpreted.
"""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from exam_guru_api.retrieval.domain import (
    RetrievalContractError,
    RetrievalFilters,
    RetrievalScope,
    RetrievalScopeSet,
)
from exam_guru_api.retrieval.fusion import FusedCandidate

MAX_EVALUATION_K = 100
MAX_EVALUATION_CASES = 10_000
MAX_RELEVANCE = 100.0


def _validate_k(k: object) -> int:
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_EVALUATION_K:
        raise RetrievalContractError(f"k must be between 1 and {MAX_EVALUATION_K}")
    return k


def _ranked_ids(values: Sequence[UUID]) -> tuple[UUID, ...]:
    snapshot = tuple(values)
    if any(not isinstance(identifier, UUID) for identifier in snapshot):
        raise RetrievalContractError("ranked identifiers must be UUIDs")
    if len(set(snapshot)) != len(snapshot):
        raise RetrievalContractError("ranked identifiers must not contain duplicates")
    return snapshot


def _relevant_ids(values: Collection[UUID]) -> frozenset[UUID]:
    snapshot = frozenset(values)
    if any(not isinstance(identifier, UUID) for identifier in snapshot):
        raise RetrievalContractError("relevant identifiers must be UUIDs")
    return snapshot


def recall_at_k(
    ranked_chunk_ids: Sequence[UUID],
    relevant_chunk_ids: Collection[UUID],
    *,
    k: int,
) -> float:
    """Return binary recall among the first ``k`` unique ranked identifiers."""

    valid_k = _validate_k(k)
    ranked = _ranked_ids(ranked_chunk_ids)
    relevant = _relevant_ids(relevant_chunk_ids)
    if not relevant:
        return 0.0
    return len(set(ranked[:valid_k]) & relevant) / len(relevant)


def precision_at_k(
    ranked_chunk_ids: Sequence[UUID],
    relevant_chunk_ids: Collection[UUID],
    *,
    k: int,
) -> float:
    """Return standard precision@k, treating unfilled ranks as non-relevant."""

    valid_k = _validate_k(k)
    ranked = _ranked_ids(ranked_chunk_ids)
    relevant = _relevant_ids(relevant_chunk_ids)
    return len(set(ranked[:valid_k]) & relevant) / valid_k


def reciprocal_rank(
    ranked_chunk_ids: Sequence[UUID],
    relevant_chunk_ids: Collection[UUID],
) -> float:
    """Return reciprocal rank of the first relevant result, or zero."""

    ranked = _ranked_ids(ranked_chunk_ids)
    relevant = _relevant_ids(relevant_chunk_ids)
    for rank, identifier in enumerate(ranked, start=1):
        if identifier in relevant:
            return 1.0 / rank
    return 0.0


def _validate_relevance_mapping(relevance_by_chunk_id: Mapping[UUID, float]) -> dict[UUID, float]:
    if not isinstance(relevance_by_chunk_id, Mapping):
        raise RetrievalContractError("relevance_by_chunk_id must be a mapping")
    snapshot: dict[UUID, float] = {}
    for identifier, relevance in relevance_by_chunk_id.items():
        if not isinstance(identifier, UUID):
            raise RetrievalContractError("relevance identifiers must be UUIDs")
        if (
            not isinstance(relevance, (int, float))
            or isinstance(relevance, bool)
            or not math.isfinite(relevance)
            or not 0 < relevance <= MAX_RELEVANCE
        ):
            raise RetrievalContractError("relevance grades must be finite and positive")
        snapshot[identifier] = float(relevance)
    return snapshot


def _discounted_cumulative_gain(relevances: Sequence[float]) -> float:
    total = 0.0
    for rank, relevance in enumerate(relevances, start=1):
        total += ((2.0**relevance) - 1.0) / math.log2(rank + 1)
    return total


def ndcg_at_k(
    ranked_chunk_ids: Sequence[UUID],
    relevance_by_chunk_id: Mapping[UUID, float],
    *,
    k: int,
) -> float:
    """Return graded normalized discounted cumulative gain at ``k``."""

    valid_k = _validate_k(k)
    ranked = _ranked_ids(ranked_chunk_ids)
    relevance = _validate_relevance_mapping(relevance_by_chunk_id)
    actual = [relevance.get(identifier, 0.0) for identifier in ranked[:valid_k]]
    ideal = sorted(relevance.values(), reverse=True)[:valid_k]
    ideal_dcg = _discounted_cumulative_gain(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _discounted_cumulative_gain(actual) / ideal_dcg


@dataclass(frozen=True, slots=True)
class RelevanceJudgment:
    """A fixed graded relevance judgment for one source chunk."""

    chunk_id: UUID
    relevance: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, UUID):
            raise RetrievalContractError("judgment chunk_id must be a UUID")
        if (
            not isinstance(self.relevance, (int, float))
            or isinstance(self.relevance, bool)
            or not math.isfinite(self.relevance)
            or not 0 < self.relevance <= MAX_RELEVANCE
        ):
            raise RetrievalContractError("judgment relevance must be finite and positive")


@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    """One fixed query, hard scope, judgments, and explicit forbidden IDs."""

    name: str
    query: str
    filters: RetrievalFilters
    judgments: tuple[RelevanceJudgment, ...]
    forbidden_chunk_ids: frozenset[UUID] = frozenset()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name
            or self.name != self.name.strip()
            or len(self.name) > 255
        ):
            raise RetrievalContractError("eval case name must be non-blank and bounded")
        if not isinstance(self.query, str) or not self.query.strip() or len(self.query) > 4_096:
            raise RetrievalContractError("eval query must be non-blank and bounded")
        if not isinstance(self.filters, (RetrievalScope, RetrievalScopeSet)):
            raise RetrievalContractError(
                "eval filters must be a RetrievalScope or RetrievalScopeSet"
            )
        if not isinstance(self.judgments, tuple) or any(
            not isinstance(judgment, RelevanceJudgment) for judgment in self.judgments
        ):
            raise RetrievalContractError("judgments must contain RelevanceJudgment values")
        judgment_ids = tuple(judgment.chunk_id for judgment in self.judgments)
        if len(set(judgment_ids)) != len(judgment_ids):
            raise RetrievalContractError("eval judgments contain duplicate chunk ids")
        if not isinstance(self.forbidden_chunk_ids, frozenset) or any(
            not isinstance(identifier, UUID) for identifier in self.forbidden_chunk_ids
        ):
            raise RetrievalContractError("forbidden_chunk_ids must be a UUID frozenset")
        if set(judgment_ids) & self.forbidden_chunk_ids:
            raise RetrievalContractError("relevant and forbidden chunk ids overlap")


@dataclass(frozen=True, slots=True)
class RetrievalEvalObservation:
    """A case paired with the deterministic ranked output being evaluated."""

    case: RetrievalEvalCase
    ranked_candidates: tuple[FusedCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.case, RetrievalEvalCase):
            raise RetrievalContractError("observation case must be RetrievalEvalCase")
        _validate_ranked_candidates(self.ranked_candidates)


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """Per-case metrics at one recorded cutoff."""

    case_name: str
    k: int
    recall_at_k: float
    precision_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    leakage_rate: float
    retrieved_count: int
    relevant_retrieved_count: int


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    """Macro metrics and individual results for a fixed evaluation suite."""

    k: int
    cases: tuple[RetrievalMetrics, ...]
    mean_recall_at_k: float
    mean_precision_at_k: float
    mean_reciprocal_rank: float
    mean_ndcg_at_k: float
    mean_leakage_rate: float


def _validate_ranked_candidates(
    ranked_candidates: object,
) -> tuple[FusedCandidate, ...]:
    if not isinstance(ranked_candidates, tuple) or any(
        not isinstance(candidate, FusedCandidate) for candidate in ranked_candidates
    ):
        raise RetrievalContractError("ranked_candidates must contain FusedCandidate values")
    seen_chunk_ids: set[UUID] = set()
    for candidate in ranked_candidates:
        source_ids = set(candidate.source_chunk_ids)
        if seen_chunk_ids & source_ids:
            raise RetrievalContractError("ranked candidates contain duplicate source chunks")
        seen_chunk_ids.update(source_ids)
    return ranked_candidates


def leakage_rate(
    ranked_candidates: tuple[FusedCandidate, ...],
    *,
    filters: RetrievalFilters,
    forbidden_chunk_ids: frozenset[UUID] = frozenset(),
    k: int,
) -> float:
    """Return the fraction of returned top-k items crossing a hard boundary."""

    valid_k = _validate_k(k)
    ranked = _validate_ranked_candidates(ranked_candidates)
    if not isinstance(filters, (RetrievalScope, RetrievalScopeSet)):
        raise RetrievalContractError(
            "leakage filters must be a RetrievalScope or RetrievalScopeSet"
        )
    forbidden = _relevant_ids(forbidden_chunk_ids)
    top_ranked = ranked[:valid_k]
    if not top_ranked:
        return 0.0
    leaked = sum(
        not filters.allows(candidate.record.scope)
        or bool(set(candidate.source_chunk_ids) & forbidden)
        for candidate in top_ranked
    )
    return leaked / len(top_ranked)


def evaluate_case(
    case: RetrievalEvalCase,
    ranked_candidates: tuple[FusedCandidate, ...],
    *,
    k: int,
) -> RetrievalMetrics:
    """Evaluate one ranked result without inspecting any retrieved source text."""

    valid_k = _validate_k(k)
    if not isinstance(case, RetrievalEvalCase):
        raise RetrievalContractError("case must be a RetrievalEvalCase")
    ranked = _validate_ranked_candidates(ranked_candidates)
    relevance_by_id = {judgment.chunk_id: float(judgment.relevance) for judgment in case.judgments}
    relevant_ids = set(relevance_by_id)
    top_ranked = ranked[:valid_k]

    found_relevant_ids: set[UUID] = set()
    ranked_relevances: list[float] = []
    relevant_item_count = 0
    first_relevant_rank: int | None = None
    for rank, candidate in enumerate(top_ranked, start=1):
        matching_ids = set(candidate.source_chunk_ids) & relevant_ids
        found_relevant_ids.update(matching_ids)
        if matching_ids:
            relevant_item_count += 1
            if first_relevant_rank is None:
                first_relevant_rank = rank
        ranked_relevances.append(
            max((relevance_by_id[identifier] for identifier in matching_ids), default=0.0)
        )

    recall = len(found_relevant_ids) / len(relevant_ids) if relevant_ids else 0.0
    precision = relevant_item_count / valid_k
    reciprocal = 1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0
    ideal_relevances = sorted(relevance_by_id.values(), reverse=True)[:valid_k]
    ideal_dcg = _discounted_cumulative_gain(ideal_relevances)
    ndcg = _discounted_cumulative_gain(ranked_relevances) / ideal_dcg if ideal_dcg else 0.0

    return RetrievalMetrics(
        case_name=case.name,
        k=valid_k,
        recall_at_k=recall,
        precision_at_k=precision,
        reciprocal_rank=reciprocal,
        ndcg_at_k=ndcg,
        leakage_rate=leakage_rate(
            ranked,
            filters=case.filters,
            forbidden_chunk_ids=case.forbidden_chunk_ids,
            k=valid_k,
        ),
        retrieved_count=len(top_ranked),
        relevant_retrieved_count=relevant_item_count,
    )


def evaluate_suite(
    observations: tuple[RetrievalEvalObservation, ...],
    *,
    k: int,
) -> RetrievalEvaluationReport:
    """Return macro Recall/Precision/MRR/nDCG/leakage for a fixed suite."""

    valid_k = _validate_k(k)
    if (
        not isinstance(observations, tuple)
        or not observations
        or len(observations) > MAX_EVALUATION_CASES
        or any(
            not isinstance(observation, RetrievalEvalObservation) for observation in observations
        )
    ):
        raise RetrievalContractError("evaluation observations must be a bounded non-empty tuple")
    case_names = tuple(observation.case.name for observation in observations)
    if len(set(case_names)) != len(case_names):
        raise RetrievalContractError("evaluation case names must be unique")

    case_metrics = tuple(
        evaluate_case(observation.case, observation.ranked_candidates, k=valid_k)
        for observation in observations
    )
    denominator = len(case_metrics)
    return RetrievalEvaluationReport(
        k=valid_k,
        cases=case_metrics,
        mean_recall_at_k=sum(metrics.recall_at_k for metrics in case_metrics) / denominator,
        mean_precision_at_k=(sum(metrics.precision_at_k for metrics in case_metrics) / denominator),
        mean_reciprocal_rank=(
            sum(metrics.reciprocal_rank for metrics in case_metrics) / denominator
        ),
        mean_ndcg_at_k=sum(metrics.ndcg_at_k for metrics in case_metrics) / denominator,
        mean_leakage_rate=(sum(metrics.leakage_rate for metrics in case_metrics) / denominator),
    )
