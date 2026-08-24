"""Bounded structured context that keeps retrieved text out of instruction space."""

import math
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from exam_guru_api.retrieval.domain import (
    RetrievalContractError,
    RetrievalScope,
    SourceProvenance,
)
from exam_guru_api.retrieval.fusion import FusedCandidate

MAX_CONTEXT_ITEMS = 100
MAX_CONTEXT_CHARACTERS = 100_000
MAX_CONTEXT_ITEM_CHARACTERS = 20_000


class ContextTrust(StrEnum):
    """Trust classification attached to every retrieval context boundary."""

    UNTRUSTED_SOURCE_DATA = "untrusted_source_data"


@dataclass(frozen=True, slots=True)
class ContextLimits:
    """Hard context and cost-amplification bounds."""

    max_items: int = 8
    max_total_characters: int = 12_000
    max_item_characters: int = 3_000

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("max_items", self.max_items, MAX_CONTEXT_ITEMS),
            ("max_total_characters", self.max_total_characters, MAX_CONTEXT_CHARACTERS),
            ("max_item_characters", self.max_item_characters, MAX_CONTEXT_ITEM_CHARACTERS),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise RetrievalContractError(f"{field_name} must be between 1 and {maximum}")


@dataclass(frozen=True, slots=True)
class UntrustedContextItem:
    """One ranked source payload; text is hidden from repr and remains data."""

    rank: int
    text: str = field(repr=False)
    scope: RetrievalScope
    source_chunk_ids: tuple[UUID, ...]
    provenances: tuple[SourceProvenance, ...]
    fusion_score: float
    original_character_count: int
    truncated: bool
    trust: ContextTrust = ContextTrust.UNTRUSTED_SOURCE_DATA

    def __post_init__(self) -> None:
        if not isinstance(self.rank, int) or isinstance(self.rank, bool) or self.rank < 1:
            raise RetrievalContractError("context item rank must be a positive integer")
        if not isinstance(self.text, str) or not self.text:
            raise RetrievalContractError("context item text must be non-empty")
        if not isinstance(self.scope, RetrievalScope):
            raise RetrievalContractError("context item scope must be a RetrievalScope")
        if (
            not isinstance(self.source_chunk_ids, tuple)
            or not self.source_chunk_ids
            or any(not isinstance(chunk_id, UUID) for chunk_id in self.source_chunk_ids)
            or len(set(self.source_chunk_ids)) != len(self.source_chunk_ids)
            or self.source_chunk_ids
            != tuple(sorted(self.source_chunk_ids, key=lambda chunk_id: chunk_id.int))
        ):
            raise RetrievalContractError("context item requires unique sorted source chunk ids")
        if (
            not isinstance(self.provenances, tuple)
            or not self.provenances
            or any(not isinstance(provenance, SourceProvenance) for provenance in self.provenances)
            or len(set(self.provenances)) != len(self.provenances)
        ):
            raise RetrievalContractError("context item requires unique source provenance")
        if (
            not isinstance(self.fusion_score, (int, float))
            or isinstance(self.fusion_score, bool)
            or not math.isfinite(self.fusion_score)
            or self.fusion_score < 0
        ):
            raise RetrievalContractError(
                "context item fusion_score must be finite and non-negative"
            )
        if (
            not isinstance(self.original_character_count, int)
            or isinstance(self.original_character_count, bool)
            or self.original_character_count < len(self.text)
        ):
            raise RetrievalContractError("original_character_count is invalid")
        if not isinstance(self.truncated, bool) or self.truncated is not (
            len(self.text) < self.original_character_count
        ):
            raise RetrievalContractError("context item truncated flag is inconsistent")
        if self.trust is not ContextTrust.UNTRUSTED_SOURCE_DATA:
            raise RetrievalContractError("retrieved context must remain untrusted")


@dataclass(frozen=True, slots=True)
class OpaqueRetrievalContext:
    """Structured context envelope with no prompt-rendering behavior."""

    items: tuple[UntrustedContextItem, ...]
    limits: ContextLimits
    character_count: int
    omitted_candidate_count: int
    trust: ContextTrust = ContextTrust.UNTRUSTED_SOURCE_DATA

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, UntrustedContextItem) for item in self.items
        ):
            raise RetrievalContractError("context items must contain UntrustedContextItem values")
        if not isinstance(self.limits, ContextLimits):
            raise RetrievalContractError("context limits must be ContextLimits")
        if self.character_count != sum(len(item.text) for item in self.items):
            raise RetrievalContractError("context character_count is inconsistent")
        if self.character_count > self.limits.max_total_characters:
            raise RetrievalContractError("context exceeds its character bound")
        if len(self.items) > self.limits.max_items:
            raise RetrievalContractError("context exceeds its item bound")
        if any(len(item.text) > self.limits.max_item_characters for item in self.items):
            raise RetrievalContractError("context item exceeds its character bound")
        if (
            not isinstance(self.omitted_candidate_count, int)
            or isinstance(self.omitted_candidate_count, bool)
            or self.omitted_candidate_count < 0
        ):
            raise RetrievalContractError("omitted_candidate_count must be non-negative")
        if self.trust is not ContextTrust.UNTRUSTED_SOURCE_DATA:
            raise RetrievalContractError("retrieval context must remain untrusted")

    def __str__(self) -> str:
        """Return safe metadata only, preventing accidental source interpolation."""

        return (
            "OpaqueRetrievalContext("
            f"items={len(self.items)}, characters={self.character_count}, "
            f"omitted={self.omitted_candidate_count}, trust={self.trust.value!r})"
        )


def build_context(
    ranked_candidates: tuple[FusedCandidate, ...],
    *,
    limits: ContextLimits | None = None,
) -> OpaqueRetrievalContext:
    """Build a deterministic context without interpreting any source text."""

    active_limits = ContextLimits() if limits is None else limits
    if not isinstance(active_limits, ContextLimits):
        raise RetrievalContractError("limits must be ContextLimits")
    if not isinstance(ranked_candidates, tuple) or any(
        not isinstance(candidate, FusedCandidate) for candidate in ranked_candidates
    ):
        raise RetrievalContractError("ranked_candidates must contain FusedCandidate values")

    seen_chunk_ids: set[object] = set()
    for candidate in ranked_candidates:
        candidate_ids = set(candidate.source_chunk_ids)
        if seen_chunk_ids & candidate_ids:
            raise RetrievalContractError("ranked candidates contain duplicate source chunks")
        seen_chunk_ids.update(candidate_ids)

    remaining_characters = active_limits.max_total_characters
    items: list[UntrustedContextItem] = []
    omitted_candidate_count = 0
    for index, candidate in enumerate(ranked_candidates):
        if len(items) >= active_limits.max_items or remaining_characters == 0:
            omitted_candidate_count = len(ranked_candidates) - index
            break
        original_character_count = len(candidate.record.text)
        allowed_characters = min(
            original_character_count,
            active_limits.max_item_characters,
            remaining_characters,
        )
        bounded_text = candidate.record.text[:allowed_characters]
        items.append(
            UntrustedContextItem(
                rank=index + 1,
                text=bounded_text,
                scope=candidate.record.scope,
                source_chunk_ids=candidate.source_chunk_ids,
                provenances=candidate.provenances,
                fusion_score=candidate.score,
                original_character_count=original_character_count,
                truncated=allowed_characters < original_character_count,
            )
        )
        remaining_characters -= allowed_characters

    character_count = sum(len(item.text) for item in items)
    return OpaqueRetrievalContext(
        items=tuple(items),
        limits=active_limits,
        character_count=character_count,
        omitted_candidate_count=omitted_candidate_count,
    )
