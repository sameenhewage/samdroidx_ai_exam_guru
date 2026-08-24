import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.knowledge.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingConfig,
)
from exam_guru_api.knowledge.models import EmbeddingConfigurationModel
from exam_guru_api.retrieval.domain import RetrievalContractError, RetrievalScope
from exam_guru_api.retrieval.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
)
from exam_guru_api.retrieval.explorer import (
    EmbeddingConfigurationNotFoundError,
    RetrievalExplorationLatency,
    RetrievalExplorationResult,
    RetrievalExploreLimits,
    RetrievalExplorerService,
    RetrievalScopeNotFoundError,
)
from exam_guru_api.retrieval.repository import RetrievalCandidateSet
from exam_guru_api.retrieval.schemas import RetrievalExploreResponse
from exam_guru_api.retrieval.service import (
    HybridCandidateRepository,
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

ACTOR_ID = UUID(int=600)
CONFIGURATION_ID = UUID(int=601)
CONFIG = EmbeddingConfig(
    provider="deterministic",
    model="grade5-fixture",
    dimension=3,
    version="v1",
    config_fingerprint=EMBEDDING_FINGERPRINT,
)
LIMITS = RetrievalExploreLimits(
    candidate_limit=4,
    top_k=2,
    max_context_items=2,
    max_context_characters=40,
    max_context_item_characters=20,
)


class ScalarSession:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = iter(values)
        self.statements: list[object] = []

    async def scalar(self, statement: object) -> object:
        self.statements.append(statement)
        return next(self._values)


@dataclass
class FakeRepository:
    candidates: RetrievalCandidateSet
    embedding_config: EmbeddingConfig = CONFIG
    calls: list[tuple[str, Sequence[object], RetrievalScope]] = field(default_factory=list)

    async def retrieve_candidates(
        self,
        *,
        query: str,
        query_vector: Sequence[object],
        filters: RetrievalScope,
    ) -> RetrievalCandidateSet:
        self.calls.append((query, query_vector, filters))
        return self.candidates


def _configuration_model() -> EmbeddingConfigurationModel:
    return EmbeddingConfigurationModel.from_domain(CONFIGURATION_ID, CONFIG, ACTOR_ID)


def _registry() -> EmbeddingProviderRegistry:
    return EmbeddingProviderRegistry(
        {"deterministic": cast(EmbeddingProvider, DeterministicEmbeddingProvider())}
    )


def _empty_retrieval_result() -> HybridRetrievalResult:
    repository = FakeRepository(RetrievalCandidateSet((), ()))
    return asyncio.run(
        HybridRetrievalService(cast(HybridCandidateRepository, repository)).retrieve(
            query="square",
            query_vector=(1.0, 0.0, 0.0),
            filters=grade_five_filter(),
        )
    )


def _valid_exploration_result() -> RetrievalExplorationResult:
    return RetrievalExplorationResult(
        query="square",
        scope=grade_five_filter(),
        embedding_config=CONFIG,
        limits=LIMITS,
        retrieval=_empty_retrieval_result(),
        latency=RetrievalExplorationLatency(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )


def test_explorer_resolves_metadata_embeds_server_side_and_returns_inspectable_phases() -> None:
    injected = retrieval_record(610, PROMPT_INJECTION_TEXT, block_id=1_610)
    duplicate = retrieval_record(611, PROMPT_INJECTION_TEXT, block_id=1_611)
    second = retrieval_record(612, "Square sides are equal.", block_id=1_612)
    forbidden = retrieval_record(
        613,
        "square " * 50,
        scope=grade_five_scope(medium_id=OTHER_MEDIUM_ID),
        block_id=1_613,
    )
    repository = FakeRepository(
        RetrievalCandidateSet(
            lexical_candidates=(
                lexical(forbidden, 100.0),
                lexical(injected, 10.0),
                lexical(duplicate, 9.0),
            ),
            vector_candidates=(
                vector(forbidden, 1.0),
                vector(second, 0.9),
                vector(duplicate, 0.8),
            ),
        )
    )
    factory_calls: list[tuple[EmbeddingConfig, int]] = []

    def repository_factory(
        session: AsyncSession,
        *,
        embedding_config: EmbeddingConfig,
        candidate_limit: int,
    ) -> HybridCandidateRepository:
        del session
        factory_calls.append((embedding_config, candidate_limit))
        return repository

    session = ScalarSession((_configuration_model(), UUID(int=700)))
    ticks = iter((1.000, 1.001, 1.002, 1.003, 1.005, 1.006, 1.009, 1.010, 1.014, 1.020))
    service = RetrievalExplorerService(
        cast(AsyncSession, session),
        _registry(),
        repository_factory=repository_factory,
        clock=lambda: next(ticks),
    )

    result = asyncio.run(
        service.explore(
            query="square perimeter",
            scope=grade_five_filter(),
            embedding_config=CONFIG,
            limits=LIMITS,
        )
    )
    body = RetrievalExploreResponse.from_domain(result).model_dump(mode="json")

    expected_vector = DeterministicEmbeddingProvider().embed("square perimeter", CONFIG).vector
    assert repository.calls == [("square perimeter", expected_vector, grade_five_filter())]
    assert factory_calls == [(CONFIG, 4)]
    assert len(session.statements) == 2
    assert result.latency == RetrievalExplorationLatency(
        validation_ms=1.0,
        embedding_ms=1.0,
        candidate_retrieval_ms=2.0,
        fusion_ms=3.0,
        context_building_ms=4.0,
        total_ms=20.0,
    )
    assert body["query"] == "square perimeter"
    assert body["embedding_config"] == {
        "provider": "deterministic",
        "model": "grade5-fixture",
        "dimension": 3,
        "version": "v1",
        "config_fingerprint": EMBEDDING_FINGERPRINT,
    }
    assert len(body["channels"]["lexical"]) == 2
    assert len(body["channels"]["vector"]) == 2
    assert [candidate["rank"] for candidate in body["fused_candidates"]] == [1, 2]
    fused_injected = next(
        candidate
        for candidate in body["fused_candidates"]
        if candidate["text"] == PROMPT_INJECTION_TEXT
    )
    assert fused_injected["source_chunk_ids"] == [str(injected.chunk_id), str(duplicate.chunk_id)]
    assert fused_injected["trust"] == "untrusted_source_data"
    assert body["context"]["trust"] == "untrusted_source_data"
    injected_context = next(
        item for item in body["context"]["items"] if item["rank"] == fused_injected["rank"]
    )
    assert PROMPT_INJECTION_TEXT.startswith(injected_context["text"])
    assert injected_context["truncated"] is True
    assert injected_context["original_character_count"] == len(PROMPT_INJECTION_TEXT)
    assert body["diagnostics"] == {
        "hard_scope_filter_applied": True,
        "lexical_candidate_count": 2,
        "vector_candidate_count": 2,
        "filtered_out_candidate_count": 2,
        "fused_candidate_count": 2,
        "deduplicated_source_count": 1,
        "context_item_count": 2,
        "context_character_count": 40,
        "omitted_fused_candidate_count": 0,
    }
    assert str(forbidden.chunk_id) not in str(body)
    assert "query_vector" not in str(body)
    assert "embedding_values" not in str(body)


def test_scope_validation_accepts_a_complete_active_taxonomy_chain() -> None:
    session = ScalarSession((UUID(int=700),))
    service = RetrievalExplorerService(cast(AsyncSession, session), _registry())

    assert asyncio.run(service._scope_exists(grade_five_scope())) is True
    assert len(session.statements) == 1


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ((None,), EmbeddingConfigurationNotFoundError),
        ((_configuration_model(), None), RetrievalScopeNotFoundError),
    ],
)
def test_explorer_returns_stable_not_found_errors(
    values: tuple[object, ...],
    error: type[Exception],
) -> None:
    session = ScalarSession(values)
    service = RetrievalExplorerService(cast(AsyncSession, session), _registry())

    with pytest.raises(error):
        asyncio.run(
            service.explore(
                query="square",
                scope=grade_five_filter(),
                embedding_config=CONFIG,
                limits=LIMITS,
            )
        )


def test_explorer_fails_closed_before_database_access_without_provider() -> None:
    session = ScalarSession(())
    service = RetrievalExplorerService(
        cast(AsyncSession, session),
        EmbeddingProviderRegistry({}),
    )

    with pytest.raises(EmbeddingProviderUnavailableError):
        asyncio.run(
            service.explore(
                query="square",
                scope=grade_five_filter(),
                embedding_config=CONFIG,
                limits=LIMITS,
            )
        )
    assert session.statements == []


@pytest.mark.parametrize("query", ["", "   ", "x" * 4_097, cast(str, 1)])
def test_explorer_rejects_invalid_queries_before_database_or_provider(query: str) -> None:
    session = ScalarSession(())
    service = RetrievalExplorerService(cast(AsyncSession, session), _registry())

    with pytest.raises(RetrievalContractError, match="query"):
        asyncio.run(
            service.explore(
                query=query,
                scope=grade_five_filter(),
                embedding_config=CONFIG,
                limits=LIMITS,
            )
        )
    assert session.statements == []


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("query", "", "query"),
        ("scope", "invalid", "scope"),
        ("embedding_config", "invalid", "embedding_config"),
        ("limits", "invalid", "limits"),
        ("retrieval", "invalid", "retrieval"),
        ("latency", "invalid", "latency"),
    ],
)
def test_exploration_result_rejects_untyped_fields(
    field_name: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(RetrievalContractError, match=message):
        replace(_valid_exploration_result(), **cast(Any, {field_name: value}))


@pytest.mark.parametrize(
    ("providers", "repository_factory", "clock", "message"),
    [
        ("invalid", None, None, "embedding_providers"),
        (_registry(), "invalid", None, "repository_factory"),
        (_registry(), None, "invalid", "clock"),
    ],
)
def test_explorer_constructor_rejects_untyped_collaborators(
    providers: object,
    repository_factory: object,
    clock: object,
    message: str,
) -> None:
    kwargs: dict[str, object] = {}
    if repository_factory is not None:
        kwargs["repository_factory"] = repository_factory
    if clock is not None:
        kwargs["clock"] = clock
    with pytest.raises(RetrievalContractError, match=message):
        RetrievalExplorerService(
            cast(AsyncSession, ScalarSession(())),
            cast(EmbeddingProviderRegistry, providers),
            **cast(Any, kwargs),
        )


@pytest.mark.parametrize(
    ("scope", "embedding_config", "limits", "message"),
    [
        ("invalid", CONFIG, LIMITS, "scope"),
        (grade_five_filter(), "invalid", LIMITS, "embedding_config"),
        (grade_five_filter(), CONFIG, "invalid", "limits"),
    ],
)
def test_explorer_rejects_untyped_domain_input_before_database_access(
    scope: object,
    embedding_config: object,
    limits: object,
    message: str,
) -> None:
    session = ScalarSession(())
    service = RetrievalExplorerService(cast(AsyncSession, session), _registry())

    with pytest.raises(RetrievalContractError, match=message):
        asyncio.run(
            service.explore(
                query="square",
                scope=cast(RetrievalScope, scope),
                embedding_config=cast(EmbeddingConfig, embedding_config),
                limits=cast(RetrievalExploreLimits, limits),
            )
        )
    assert session.statements == []


@pytest.mark.parametrize(
    "build",
    [
        lambda: RetrievalExploreLimits(0, 1, 1, 1, 1),
        lambda: RetrievalExploreLimits(1, 2, 1, 1, 1),
        lambda: RetrievalExploreLimits(2, 2, 3, 3, 1),
        lambda: RetrievalExploreLimits(2, 2, 2, 1, 2),
    ],
)
def test_explorer_domain_limits_reject_unbounded_or_inconsistent_values(
    build: Any,
) -> None:
    with pytest.raises(RetrievalContractError):
        build()


@pytest.mark.parametrize("value", [-1.0, math.inf, math.nan, True, cast(float, "1")])
def test_exploration_latency_rejects_invalid_values(value: float) -> None:
    with pytest.raises(RetrievalContractError, match="validation_ms"):
        RetrievalExplorationLatency(value, 0.0, 0.0, 0.0, 0.0, 0.0)
