import math
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.retrieval.context import (
    ContextLimits,
    ContextTrust,
    OpaqueRetrievalContext,
    UntrustedContextItem,
    build_context,
)
from exam_guru_api.retrieval.domain import (
    RetrievalContractError,
    RetrievalScope,
    SourceProvenance,
)
from exam_guru_api.retrieval.fusion import FusedCandidate, fuse_candidates
from tests.test_retrieval_fixtures import (
    EMBEDDING_FINGERPRINT,
    PROMPT_INJECTION_TEXT,
    grade_five_filter,
    lexical,
    retrieval_record,
)


def fuse_lexical(*candidates: tuple[int, str]) -> tuple[FusedCandidate, ...]:
    return fuse_candidates(
        tuple(
            lexical(retrieval_record(identifier, text, page_number=index + 1), 100.0 - index)
            for index, (identifier, text) in enumerate(candidates)
        ),
        (),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
    )


def test_context_treats_prompt_injection_as_opaque_untrusted_data_with_provenance() -> None:
    fused = fuse_lexical((200, PROMPT_INJECTION_TEXT))

    context = build_context(
        fused,
        limits=ContextLimits(
            max_items=4,
            max_total_characters=1_000,
            max_item_characters=500,
        ),
    )

    assert isinstance(context, OpaqueRetrievalContext)
    assert context.items[0].text == PROMPT_INJECTION_TEXT
    assert context.items[0].trust is ContextTrust.UNTRUSTED_SOURCE_DATA
    assert context.items[0].provenances == (fused[0].record.provenance,)
    assert context.items[0].source_chunk_ids == (fused[0].record.chunk_id,)
    assert not context.items[0].truncated
    assert PROMPT_INJECTION_TEXT not in repr(context.items[0])
    assert PROMPT_INJECTION_TEXT not in str(context)


def test_context_enforces_item_per_item_and_total_character_bounds() -> None:
    fused = fuse_lexical(
        (210, "abcdefghij"),
        (211, "klmnopqrst"),
        (212, "uvwxyz"),
    )

    context = build_context(
        fused,
        limits=ContextLimits(
            max_items=2,
            max_total_characters=12,
            max_item_characters=10,
        ),
    )

    assert [item.text for item in context.items] == ["abcdefghij", "kl"]
    assert [item.truncated for item in context.items] == [False, True]
    assert [item.original_character_count for item in context.items] == [10, 10]
    assert context.character_count == 12
    assert context.omitted_candidate_count == 1
    assert len(context.items) <= context.limits.max_items
    assert all(len(item.text) <= context.limits.max_item_characters for item in context.items)


def test_empty_ranked_results_create_an_empty_but_explicitly_untrusted_context() -> None:
    context = build_context(())

    assert context.items == ()
    assert context.character_count == 0
    assert context.omitted_candidate_count == 0
    assert context.trust is ContextTrust.UNTRUSTED_SOURCE_DATA


@pytest.mark.parametrize(
    "build",
    [
        lambda: ContextLimits(max_items=0),
        lambda: ContextLimits(max_items=cast(int, True)),
        lambda: ContextLimits(max_items=101),
        lambda: ContextLimits(max_total_characters=0),
        lambda: ContextLimits(max_total_characters=cast(int, True)),
        lambda: ContextLimits(max_total_characters=100_001),
        lambda: ContextLimits(max_item_characters=0),
        lambda: ContextLimits(max_item_characters=cast(int, True)),
        lambda: ContextLimits(max_item_characters=20_001),
    ],
)
def test_context_limits_have_hard_cost_amplification_bounds(
    build: Callable[[], ContextLimits],
) -> None:
    with pytest.raises(RetrievalContractError):
        build()


def test_context_rejects_unranked_or_duplicate_fused_items() -> None:
    item = fuse_lexical((220, "one"))[0]

    with pytest.raises(RetrievalContractError, match="FusedCandidate"):
        build_context(cast(tuple[FusedCandidate, ...], ("not-ranked",)))

    with pytest.raises(RetrievalContractError, match="duplicate"):
        build_context((item, item))


def test_untrusted_context_item_rejects_malformed_metadata() -> None:
    item = build_context(fuse_lexical((230, "source"))).items[0]
    invalid_builds: tuple[Callable[[], object], ...] = (
        lambda: replace(item, rank=0),
        lambda: replace(item, rank=cast(int, True)),
        lambda: replace(item, text=""),
        lambda: replace(item, scope=cast(RetrievalScope, "scope")),
        lambda: replace(item, source_chunk_ids=cast(tuple[UUID, ...], [])),
        lambda: replace(item, source_chunk_ids=(cast(UUID, "chunk"),)),
        lambda: replace(item, source_chunk_ids=(item.source_chunk_ids[0],) * 2),
        lambda: replace(item, source_chunk_ids=(item.source_chunk_ids[0], UUID(int=1))),
        lambda: replace(item, provenances=cast(tuple[SourceProvenance, ...], [])),
        lambda: replace(item, provenances=(cast(SourceProvenance, "provenance"),)),
        lambda: replace(item, provenances=(item.provenances[0], item.provenances[0])),
        lambda: replace(item, fusion_score=math.nan),
        lambda: replace(item, fusion_score=-1.0),
        lambda: replace(item, original_character_count=len(item.text) - 1),
        lambda: replace(item, original_character_count=cast(int, True)),
        lambda: replace(item, truncated=True),
        lambda: replace(item, truncated=cast(bool, 0)),
        lambda: replace(item, trust=cast(ContextTrust, "trusted")),
    )

    for build in invalid_builds:
        with pytest.raises(RetrievalContractError):
            build()


def test_opaque_context_rejects_inconsistent_or_over_bound_state() -> None:
    first = build_context(fuse_lexical((240, "abc"))).items[0]
    second = build_context(fuse_lexical((241, "def"))).items[0]
    valid = OpaqueRetrievalContext(
        items=(first,),
        limits=ContextLimits(max_items=1, max_total_characters=3, max_item_characters=3),
        character_count=3,
        omitted_candidate_count=0,
    )
    invalid_builds: tuple[Callable[[], object], ...] = (
        lambda: replace(valid, items=cast(tuple[UntrustedContextItem, ...], [])),
        lambda: replace(valid, items=(cast(UntrustedContextItem, "item"),)),
        lambda: replace(valid, limits=cast(ContextLimits, "limits")),
        lambda: replace(valid, character_count=2),
        lambda: OpaqueRetrievalContext(
            items=(first,),
            limits=ContextLimits(max_items=1, max_total_characters=2, max_item_characters=3),
            character_count=3,
            omitted_candidate_count=0,
        ),
        lambda: OpaqueRetrievalContext(
            items=(first, second),
            limits=ContextLimits(max_items=1, max_total_characters=6, max_item_characters=3),
            character_count=6,
            omitted_candidate_count=0,
        ),
        lambda: OpaqueRetrievalContext(
            items=(first,),
            limits=ContextLimits(max_items=1, max_total_characters=3, max_item_characters=2),
            character_count=3,
            omitted_candidate_count=0,
        ),
        lambda: replace(valid, omitted_candidate_count=-1),
        lambda: replace(valid, omitted_candidate_count=cast(int, True)),
        lambda: replace(valid, trust=cast(ContextTrust, "trusted")),
    )

    for build in invalid_builds:
        with pytest.raises(RetrievalContractError):
            build()


def test_context_boundary_rejects_falsey_untyped_limits() -> None:
    with pytest.raises(RetrievalContractError, match="limits"):
        build_context((), limits=cast(ContextLimits, 0))
