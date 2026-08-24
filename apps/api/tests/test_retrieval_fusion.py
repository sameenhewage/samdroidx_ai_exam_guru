import math
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalRecord,
    RetrievalScope,
    SourceProvenance,
    TaxonomyScope,
    VectorCandidate,
)
from exam_guru_api.retrieval.fusion import (
    FusedCandidate,
    FusionConfig,
    VectorSpaceMismatchError,
    fuse_candidates,
)
from tests.test_retrieval_fixtures import (
    COMPETENCY_ID,
    EMBEDDING_FINGERPRINT,
    OTHER_EMBEDDING_FINGERPRINT,
    OTHER_MEDIUM_ID,
    grade_five_filter,
    grade_five_scope,
    lexical,
    retrieval_record,
    vector,
)


def test_weighted_reciprocal_rank_fusion_is_deterministic_and_deduplicates_channels() -> None:
    first = retrieval_record(100, "First reviewed source")
    second = retrieval_record(101, "Second reviewed source", page_number=3)
    config = FusionConfig(limit=10, rank_constant=60)

    forward = fuse_candidates(
        (lexical(first, 10.0), lexical(second, 5.0)),
        (vector(second, 0.99), vector(first, 0.80)),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        config=config,
    )
    shuffled = fuse_candidates(
        (lexical(second, 5.0), lexical(first, 10.0)),
        (vector(first, 0.80), vector(second, 0.99)),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        config=config,
    )

    assert forward == shuffled
    assert [item.record.chunk_id for item in forward] == [first.chunk_id, second.chunk_id]
    assert forward[0].score == pytest.approx((1 / 61) + (1 / 62))
    assert forward[0].lexical_rank == 1
    assert forward[0].vector_rank == 2
    assert forward[0].source_chunk_ids == (first.chunk_id,)


def test_fusion_deduplicates_normalized_segments_without_losing_provenance() -> None:
    canonical = retrieval_record(
        110,
        "Equivalent reviewed text",
        block_id=1_100,
    )
    duplicate = retrieval_record(
        111,
        "  equivalent   REVIEWED text  ",
        block_id=1_101,
    )

    fused = fuse_candidates(
        (lexical(canonical, 2.0), lexical(canonical, 1.0)),
        (vector(duplicate, 0.8), vector(duplicate, 0.7)),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
    )

    assert len(fused) == 1
    assert fused[0].record.chunk_id == canonical.chunk_id
    assert fused[0].source_chunk_ids == (canonical.chunk_id, duplicate.chunk_id)
    assert fused[0].provenances == (canonical.provenance, duplicate.provenance)
    assert fused[0].lexical_rank == 1
    assert fused[0].vector_rank == 1


def test_fusion_normalizes_compatibility_characters_and_combining_marks() -> None:
    canonical = retrieval_record(112, "Café test", block_id=1_102)
    duplicate = retrieval_record(
        113,
        "\uff23\uff41\uff46\uff45\u0301\u3000\uff34\uff25\uff33\uff34",
        block_id=1_103,
    )

    fused = fuse_candidates(
        (lexical(canonical, 2.0), lexical(duplicate, 1.0)),
        (),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
    )

    assert len(fused) == 1
    assert fused[0].source_chunk_ids == (canonical.chunk_id, duplicate.chunk_id)


@pytest.mark.parametrize(
    "outside_scope",
    [
        replace(grade_five_scope(), grade=6),
        replace(grade_five_scope(), exam_id=UUID(int=999)),
        replace(grade_five_scope(), medium_id=OTHER_MEDIUM_ID),
        replace(grade_five_scope(), curriculum_version_id=UUID(int=998)),
        replace(
            grade_five_scope(),
            taxonomy=TaxonomyScope(
                competency_id=COMPETENCY_ID,
                skill_id=UUID(int=997),
            ),
        ),
    ],
)
def test_hard_scope_is_applied_before_scores_can_affect_ranking(
    outside_scope: RetrievalScope,
) -> None:
    allowed = retrieval_record(120, "Allowed source")
    forbidden = retrieval_record(121, "Forbidden but high scoring", scope=outside_scope)

    fused = fuse_candidates(
        (lexical(forbidden, 1_000_000.0), lexical(allowed, 1.0)),
        (vector(forbidden, 1_000_000.0), vector(allowed, 0.1)),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
    )

    assert tuple(item.record for item in fused) == (allowed,)


def test_vector_candidates_must_all_match_the_declared_embedding_space() -> None:
    first = retrieval_record(130, "First")
    second = retrieval_record(131, "Second", page_number=3)

    with pytest.raises(VectorSpaceMismatchError, match="mixed vector spaces"):
        fuse_candidates(
            (),
            (
                vector(first, 0.9),
                vector(second, 0.8, fingerprint=OTHER_EMBEDDING_FINGERPRINT),
            ),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        )

    with pytest.raises(VectorSpaceMismatchError, match="declared embedding space"):
        fuse_candidates(
            (),
            (vector(first, 0.9, fingerprint=OTHER_EMBEDDING_FINGERPRINT),),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        )


def test_weights_and_limit_are_explicit_and_reproducible() -> None:
    lexical_only = retrieval_record(140, "Lexical evidence")
    vector_only = retrieval_record(141, "Vector evidence", page_number=3)

    fused = fuse_candidates(
        (lexical(lexical_only, 1.0),),
        (vector(vector_only, 1.0),),
        filters=grade_five_filter(),
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        config=FusionConfig(
            limit=1,
            rank_constant=10,
            lexical_weight=2.0,
            vector_weight=1.0,
        ),
    )

    assert len(fused) == 1
    assert fused[0].record is lexical_only
    assert fused[0].score == pytest.approx(2 / 11)


def test_same_chunk_id_with_conflicting_payload_is_rejected() -> None:
    lexical_record = retrieval_record(150, "Original")
    conflicting_record = retrieval_record(150, "Conflicting", page_number=3)

    with pytest.raises(RetrievalContractError, match="conflicting payloads"):
        fuse_candidates(
            (lexical(lexical_record, 1.0),),
            (vector(conflicting_record, 0.9),),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: FusionConfig(limit=0),
        lambda: FusionConfig(limit=cast(int, True)),
        lambda: FusionConfig(limit=101),
        lambda: FusionConfig(rank_constant=0),
        lambda: FusionConfig(rank_constant=cast(int, True)),
        lambda: FusionConfig(rank_constant=10_001),
        lambda: FusionConfig(lexical_weight=cast(float, "weight")),
        lambda: FusionConfig(lexical_weight=math.nan),
        lambda: FusionConfig(lexical_weight=-1),
        lambda: FusionConfig(vector_weight=math.inf),
        lambda: FusionConfig(vector_weight=-1),
        lambda: FusionConfig(lexical_weight=0, vector_weight=0),
        lambda: FusionConfig(max_candidates_per_channel=0),
        lambda: FusionConfig(max_candidates_per_channel=cast(int, True)),
        lambda: FusionConfig(max_candidates_per_channel=10_001),
    ],
)
def test_fusion_configuration_is_bounded(build: Callable[[], FusionConfig]) -> None:
    with pytest.raises(RetrievalContractError):
        build()


def test_fusion_rejects_candidate_amplification_over_the_configured_bound() -> None:
    record = retrieval_record(160, "Bounded")

    with pytest.raises(RetrievalContractError, match="candidate limit"):
        fuse_candidates(
            (lexical(record, 1.0), lexical(record, 0.5)),
            (),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
            config=FusionConfig(max_candidates_per_channel=1),
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: FusedCandidate(cast(RetrievalRecord, "record"), 1.0, 1, None),
        lambda: FusedCandidate(retrieval_record(170, "text"), math.nan, 1, None),
        lambda: FusedCandidate(retrieval_record(170, "text"), -1.0, 1, None),
        lambda: FusedCandidate(retrieval_record(170, "text"), 1.0, 0, None),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            cast(int | None, True),
            None,
        ),
        lambda: FusedCandidate(retrieval_record(170, "text"), 1.0, None, None),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            source_chunk_ids=cast(tuple[UUID, ...], []),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            source_chunk_ids=(cast(UUID, "chunk"),),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            source_chunk_ids=(UUID(int=170), UUID(int=170)),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            source_chunk_ids=(UUID(int=999),),
        ),
        lambda: FusedCandidate(
            retrieval_record(180, "text"),
            1.0,
            1,
            None,
            source_chunk_ids=(UUID(int=180), UUID(int=179)),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            provenances=cast(tuple[SourceProvenance, ...], []),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            provenances=(cast(SourceProvenance, "provenance"),),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            provenances=(
                retrieval_record(170, "text").provenance,
                retrieval_record(170, "text").provenance,
            ),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            provenances=(SourceProvenance(UUID(int=999), 1),),
        ),
        lambda: FusedCandidate(
            retrieval_record(170, "text"),
            1.0,
            1,
            None,
            provenances=(
                retrieval_record(170, "text").provenance,
                SourceProvenance(UUID(int=999), 1),
            ),
        ),
    ],
)
def test_fused_candidate_rejects_malformed_rank_and_provenance(
    build: Callable[[], FusedCandidate],
) -> None:
    with pytest.raises(RetrievalContractError):
        build()


def test_fusion_boundary_rejects_untyped_dependencies_and_candidates() -> None:
    record = retrieval_record(190, "Boundary")

    with pytest.raises(RetrievalContractError, match="config"):
        fuse_candidates(
            (),
            (),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
            config=cast(FusionConfig, 0),
        )
    with pytest.raises(RetrievalContractError, match="filters"):
        fuse_candidates(
            (),
            (),
            filters=cast(RetrievalScope, "filters"),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        )
    with pytest.raises(RetrievalContractError, match="fingerprint"):
        fuse_candidates(
            (),
            (),
            filters=grade_five_filter(),
            embedding_config_fingerprint=" ",
        )
    with pytest.raises(RetrievalContractError, match="LexicalCandidate"):
        fuse_candidates(
            cast(tuple[LexicalCandidate, ...], ("candidate",)),
            (),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        )
    with pytest.raises(RetrievalContractError, match="VectorCandidate"):
        fuse_candidates(
            (lexical(record, 1.0),),
            cast(tuple[VectorCandidate, ...], ("candidate",)),
            filters=grade_five_filter(),
            embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
        )
