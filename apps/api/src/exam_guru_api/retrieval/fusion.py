"""Deterministic hard filtering, deduplication, and weighted RRF fusion."""

import math
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalFilters,
    RetrievalRecord,
    RetrievalScope,
    RetrievalScopeSet,
    SourceProvenance,
    VectorCandidate,
    validate_embedding_config_fingerprint,
)

MAX_FUSION_RESULTS = 100
MAX_CANDIDATES_PER_CHANNEL = 10_000
MAX_RANK_CONSTANT = 10_000

type SegmentKey = tuple[RetrievalScope, UUID, int, str]


class VectorSpaceMismatchError(RetrievalContractError):
    """Raised before fusion when vector candidates do not share one space."""


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class FusionConfig:
    """Versionable weighted reciprocal-rank fusion parameters."""

    limit: int = 10
    rank_constant: int = 60
    lexical_weight: float = 1.0
    vector_weight: float = 1.0
    max_candidates_per_channel: int = 1_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or not 1 <= self.limit <= MAX_FUSION_RESULTS
        ):
            raise RetrievalContractError(f"fusion limit must be between 1 and {MAX_FUSION_RESULTS}")
        if (
            not isinstance(self.rank_constant, int)
            or isinstance(self.rank_constant, bool)
            or not 1 <= self.rank_constant <= MAX_RANK_CONSTANT
        ):
            raise RetrievalContractError(f"rank_constant must be between 1 and {MAX_RANK_CONSTANT}")
        if (
            not _is_finite_number(self.lexical_weight)
            or self.lexical_weight < 0
            or not _is_finite_number(self.vector_weight)
            or self.vector_weight < 0
            or (self.lexical_weight == 0 and self.vector_weight == 0)
        ):
            raise RetrievalContractError(
                "fusion weights must be finite, non-negative, and non-zero"
            )
        if (
            not isinstance(self.max_candidates_per_channel, int)
            or isinstance(self.max_candidates_per_channel, bool)
            or not 1 <= self.max_candidates_per_channel <= MAX_CANDIDATES_PER_CHANNEL
        ):
            raise RetrievalContractError(
                "max_candidates_per_channel must be within the candidate limit"
            )


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One uniquely ranked segment with all deduplicated source provenance."""

    record: RetrievalRecord
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    source_chunk_ids: tuple[UUID, ...] = ()
    provenances: tuple[SourceProvenance, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.record, RetrievalRecord):
            raise RetrievalContractError("fused candidate record must be a RetrievalRecord")
        if not _is_finite_number(self.score) or self.score < 0:
            raise RetrievalContractError("fused score must be a finite non-negative number")
        for field_name, rank in (
            ("lexical_rank", self.lexical_rank),
            ("vector_rank", self.vector_rank),
        ):
            if rank is not None and (
                not isinstance(rank, int) or isinstance(rank, bool) or rank < 1
            ):
                raise RetrievalContractError(f"{field_name} must be a positive integer or None")
        if self.lexical_rank is None and self.vector_rank is None:
            raise RetrievalContractError("a fused candidate requires at least one source rank")

        source_chunk_ids = (
            (self.record.chunk_id,) if self.source_chunk_ids == () else self.source_chunk_ids
        )
        if (
            not isinstance(source_chunk_ids, tuple)
            or any(not isinstance(chunk_id, UUID) for chunk_id in source_chunk_ids)
            or len(set(source_chunk_ids)) != len(source_chunk_ids)
            or self.record.chunk_id not in source_chunk_ids
        ):
            raise RetrievalContractError(
                "source_chunk_ids must be unique UUIDs including the record"
            )
        sorted_chunk_ids = tuple(sorted(source_chunk_ids, key=lambda chunk_id: chunk_id.int))
        if source_chunk_ids != sorted_chunk_ids:
            raise RetrievalContractError("source_chunk_ids must be deterministically sorted")

        provenances = (self.record.provenance,) if self.provenances == () else self.provenances
        if (
            not isinstance(provenances, tuple)
            or any(not isinstance(provenance, SourceProvenance) for provenance in provenances)
            or len(set(provenances)) != len(provenances)
            or self.record.provenance not in provenances
        ):
            raise RetrievalContractError(
                "provenances must be unique SourceProvenance values including the record"
            )
        sorted_provenances = tuple(sorted(provenances, key=_provenance_sort_key))
        if provenances != sorted_provenances:
            raise RetrievalContractError("provenances must be deterministically sorted")

        object.__setattr__(self, "source_chunk_ids", source_chunk_ids)
        object.__setattr__(self, "provenances", provenances)


def _provenance_sort_key(provenance: SourceProvenance) -> tuple[int, int, int]:
    block_id = provenance.source_block_id.int if provenance.source_block_id is not None else -1
    return provenance.source_document_id.int, provenance.page_number, block_id


def _normalized_segment_key(record: RetrievalRecord) -> SegmentKey:
    normalized_text = " ".join(unicodedata.normalize("NFKC", record.text).split()).casefold()
    return (
        record.scope,
        record.provenance.source_document_id,
        record.provenance.page_number,
        normalized_text,
    )


def _candidate_records(
    lexical_candidates: tuple[LexicalCandidate, ...],
    vector_candidates: tuple[VectorCandidate, ...],
) -> Iterable[RetrievalRecord]:
    for lexical_candidate in lexical_candidates:
        yield lexical_candidate.record
    for vector_candidate in vector_candidates:
        yield vector_candidate.record


def _validate_record_integrity(
    lexical_candidates: tuple[LexicalCandidate, ...],
    vector_candidates: tuple[VectorCandidate, ...],
) -> None:
    records_by_chunk_id: dict[UUID, RetrievalRecord] = {}
    for record in _candidate_records(lexical_candidates, vector_candidates):
        existing = records_by_chunk_id.setdefault(record.chunk_id, record)
        if existing != record:
            raise RetrievalContractError("the same chunk_id has conflicting payloads")


def _validate_vector_space(
    vector_candidates: tuple[VectorCandidate, ...],
    expected_fingerprint: str,
) -> None:
    fingerprints = {candidate.embedding_config_fingerprint for candidate in vector_candidates}
    if len(fingerprints) > 1:
        raise VectorSpaceMismatchError("mixed vector spaces cannot be fused")
    if fingerprints and fingerprints != {expected_fingerprint}:
        raise VectorSpaceMismatchError(
            "vector candidates do not match the declared embedding space"
        )


def _rank_lexical(
    candidates: tuple[LexicalCandidate, ...],
    filters: RetrievalFilters,
) -> tuple[LexicalCandidate, ...]:
    ordered = sorted(
        (candidate for candidate in candidates if filters.allows(candidate.record.scope)),
        key=lambda candidate: (-candidate.score, candidate.record.chunk_id.int),
    )
    ranked: list[LexicalCandidate] = []
    seen_segments: set[SegmentKey] = set()
    for candidate in ordered:
        key = _normalized_segment_key(candidate.record)
        if key not in seen_segments:
            seen_segments.add(key)
            ranked.append(candidate)
    return tuple(ranked)


def _rank_vector(
    candidates: tuple[VectorCandidate, ...],
    filters: RetrievalFilters,
) -> tuple[VectorCandidate, ...]:
    ordered = sorted(
        (candidate for candidate in candidates if filters.allows(candidate.record.scope)),
        key=lambda candidate: (-candidate.score, candidate.record.chunk_id.int),
    )
    ranked: list[VectorCandidate] = []
    seen_segments: set[SegmentKey] = set()
    for candidate in ordered:
        key = _normalized_segment_key(candidate.record)
        if key not in seen_segments:
            seen_segments.add(key)
            ranked.append(candidate)
    return tuple(ranked)


def fuse_candidates(
    lexical_candidates: Iterable[LexicalCandidate],
    vector_candidates: Iterable[VectorCandidate],
    *,
    filters: RetrievalFilters,
    embedding_config_fingerprint: str,
    config: FusionConfig | None = None,
) -> tuple[FusedCandidate, ...]:
    """Hard-filter, rank, deduplicate, and fuse two candidate channels.

    Candidate scores establish deterministic within-channel ranks only.  The
    resulting score is weighted reciprocal-rank fusion.  Vector fingerprints
    are checked before any vector result can influence ranking.
    """

    active_config = FusionConfig() if config is None else config
    if not isinstance(active_config, FusionConfig):
        raise RetrievalContractError("config must be a FusionConfig")
    if not isinstance(filters, (RetrievalScope, RetrievalScopeSet)):
        raise RetrievalContractError("filters must be a RetrievalScope or RetrievalScopeSet")
    expected_fingerprint = validate_embedding_config_fingerprint(embedding_config_fingerprint)

    lexical_snapshot = tuple(lexical_candidates)
    vector_snapshot = tuple(vector_candidates)
    if (
        len(lexical_snapshot) > active_config.max_candidates_per_channel
        or len(vector_snapshot) > active_config.max_candidates_per_channel
    ):
        raise RetrievalContractError("retriever candidate limit exceeded")
    if any(not isinstance(candidate, LexicalCandidate) for candidate in lexical_snapshot):
        raise RetrievalContractError("lexical_candidates must contain LexicalCandidate values")
    if any(not isinstance(candidate, VectorCandidate) for candidate in vector_snapshot):
        raise RetrievalContractError("vector_candidates must contain VectorCandidate values")

    _validate_record_integrity(lexical_snapshot, vector_snapshot)
    _validate_vector_space(vector_snapshot, expected_fingerprint)

    ranked_lexical = _rank_lexical(lexical_snapshot, filters)
    ranked_vector = _rank_vector(vector_snapshot, filters)
    lexical_ranks = {
        _normalized_segment_key(candidate.record): rank
        for rank, candidate in enumerate(ranked_lexical, start=1)
    }
    vector_ranks = {
        _normalized_segment_key(candidate.record): rank
        for rank, candidate in enumerate(ranked_vector, start=1)
    }

    records_by_segment: dict[SegmentKey, dict[UUID, RetrievalRecord]] = {}
    for record in _candidate_records(lexical_snapshot, vector_snapshot):
        if not filters.allows(record.scope):
            continue
        key = _normalized_segment_key(record)
        records_by_segment.setdefault(key, {})[record.chunk_id] = record

    fused: list[FusedCandidate] = []
    for key in lexical_ranks.keys() | vector_ranks.keys():
        lexical_rank = lexical_ranks.get(key)
        vector_rank = vector_ranks.get(key)
        score = 0.0
        if lexical_rank is not None:
            score += active_config.lexical_weight / (active_config.rank_constant + lexical_rank)
        if vector_rank is not None:
            score += active_config.vector_weight / (active_config.rank_constant + vector_rank)

        records = tuple(
            sorted(records_by_segment[key].values(), key=lambda record: record.chunk_id.int)
        )
        canonical_record = records[0]
        source_chunk_ids = tuple(record.chunk_id for record in records)
        provenances = tuple(
            sorted({record.provenance for record in records}, key=_provenance_sort_key)
        )
        fused.append(
            FusedCandidate(
                record=canonical_record,
                score=score,
                lexical_rank=lexical_rank,
                vector_rank=vector_rank,
                source_chunk_ids=source_chunk_ids,
                provenances=provenances,
            )
        )

    fused.sort(
        key=lambda candidate: (
            -candidate.score,
            min(
                rank for rank in (candidate.lexical_rank, candidate.vector_rank) if rank is not None
            ),
            candidate.record.chunk_id.int,
        )
    )
    return tuple(fused[: active_config.limit])
