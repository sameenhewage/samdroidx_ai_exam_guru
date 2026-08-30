"""Read-only application service for authorized retrieval exploration."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.knowledge.models import EmbeddingConfigurationModel
from exam_guru_api.observability import OperationalTelemetry, get_operational_telemetry
from exam_guru_api.retrieval.context import (
    MAX_CONTEXT_CHARACTERS,
    MAX_CONTEXT_ITEM_CHARACTERS,
    MAX_CONTEXT_ITEMS,
    ContextLimits,
)
from exam_guru_api.retrieval.domain import RetrievalContractError, RetrievalScope
from exam_guru_api.retrieval.embeddings import (
    MAX_EMBEDDING_QUERY_CHARACTERS,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
)
from exam_guru_api.retrieval.fusion import MAX_FUSION_RESULTS, FusionConfig
from exam_guru_api.retrieval.repository import PostgresHybridRetrievalRepository
from exam_guru_api.retrieval.service import (
    HybridCandidateRepository,
    HybridRetrievalResult,
    HybridRetrievalService,
)

MAX_EXPLORER_CANDIDATES = 100


class EmbeddingConfigurationNotFoundError(LookupError):
    """The explicitly requested embedding metadata is not persisted."""


class RetrievalScopeNotFoundError(LookupError):
    """The exact active/reviewed retrieval scope does not exist."""


@dataclass(frozen=True, slots=True)
class RetrievalExploreLimits:
    """Per-request bounds for candidate work, fusion output, and source context."""

    candidate_limit: int
    top_k: int
    max_context_items: int
    max_context_characters: int
    max_context_item_characters: int

    def __post_init__(self) -> None:
        for field_name, value, maximum in (
            ("candidate_limit", self.candidate_limit, MAX_EXPLORER_CANDIDATES),
            ("top_k", self.top_k, MAX_FUSION_RESULTS),
            ("max_context_items", self.max_context_items, MAX_CONTEXT_ITEMS),
            (
                "max_context_characters",
                self.max_context_characters,
                MAX_CONTEXT_CHARACTERS,
            ),
            (
                "max_context_item_characters",
                self.max_context_item_characters,
                MAX_CONTEXT_ITEM_CHARACTERS,
            ),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise RetrievalContractError(f"{field_name} must be between 1 and {maximum}")
        if self.top_k > self.candidate_limit:
            raise RetrievalContractError("top_k cannot exceed candidate_limit")
        if self.max_context_items > self.top_k:
            raise RetrievalContractError("max_context_items cannot exceed top_k")
        if self.max_context_item_characters > self.max_context_characters:
            raise RetrievalContractError(
                "max_context_item_characters cannot exceed max_context_characters"
            )


@dataclass(frozen=True, slots=True)
class RetrievalExplorationLatency:
    validation_ms: float
    embedding_ms: float
    candidate_retrieval_ms: float
    fusion_ms: float
    context_building_ms: float
    total_ms: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("validation_ms", self.validation_ms),
            ("embedding_ms", self.embedding_ms),
            ("candidate_retrieval_ms", self.candidate_retrieval_ms),
            ("fusion_ms", self.fusion_ms),
            ("context_building_ms", self.context_building_ms),
            ("total_ms", self.total_ms),
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise RetrievalContractError(f"{field_name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class RetrievalExplorationResult:
    query: str
    scope: RetrievalScope
    embedding_config: EmbeddingConfig
    limits: RetrievalExploreLimits
    retrieval: HybridRetrievalResult
    latency: RetrievalExplorationLatency

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise RetrievalContractError("query must be non-blank")
        if not isinstance(self.scope, RetrievalScope):
            raise RetrievalContractError("scope must be a RetrievalScope")
        if not isinstance(self.embedding_config, EmbeddingConfig):
            raise RetrievalContractError("embedding_config must be an EmbeddingConfig")
        if not isinstance(self.limits, RetrievalExploreLimits):
            raise RetrievalContractError("limits must be RetrievalExploreLimits")
        if not isinstance(self.retrieval, HybridRetrievalResult):
            raise RetrievalContractError("retrieval must be a HybridRetrievalResult")
        if not isinstance(self.latency, RetrievalExplorationLatency):
            raise RetrievalContractError("latency must be RetrievalExplorationLatency")


class RetrievalRepositoryFactory(Protocol):
    def __call__(
        self,
        session: AsyncSession,
        *,
        embedding_config: EmbeddingConfig,
        candidate_limit: int,
    ) -> HybridCandidateRepository: ...


def _postgres_repository_factory(
    session: AsyncSession,
    *,
    embedding_config: EmbeddingConfig,
    candidate_limit: int,
) -> PostgresHybridRetrievalRepository:
    return PostgresHybridRetrievalRepository(
        session,
        embedding_config=embedding_config,
        candidate_limit=candidate_limit,
    )


def _elapsed_ms(start: float, end: float) -> float:
    return round(max(0.0, (end - start) * 1_000), 6)


def _retrieval_failure_code(error: Exception) -> str:
    if isinstance(error, EmbeddingConfigurationNotFoundError):
        return "embedding_configuration_not_found"
    if isinstance(error, RetrievalScopeNotFoundError):
        return "retrieval_scope_not_found"
    if isinstance(error, EmbeddingProviderUnavailableError):
        return "embedding_provider_unavailable"
    if isinstance(error, RetrievalContractError):
        return "invalid_retrieval_request"
    return "retrieval_internal_error"


class RetrievalExplorerService:
    """Validate exact metadata, embed server-side, and run read-only hybrid retrieval."""

    def __init__(
        self,
        session: AsyncSession,
        embedding_providers: EmbeddingProviderRegistry,
        *,
        repository_factory: RetrievalRepositoryFactory = _postgres_repository_factory,
        clock: Callable[[], float] = perf_counter,
        telemetry: OperationalTelemetry | None = None,
    ) -> None:
        if not isinstance(embedding_providers, EmbeddingProviderRegistry):
            raise RetrievalContractError("embedding_providers must be an EmbeddingProviderRegistry")
        if not callable(repository_factory):
            raise RetrievalContractError("repository_factory must be callable")
        if not callable(clock):
            raise RetrievalContractError("clock must be callable")
        self._session = session
        self._embedding_providers = embedding_providers
        self._repository_factory = repository_factory
        self._clock = clock
        self._telemetry = telemetry or get_operational_telemetry()

    async def explore(
        self,
        *,
        query: str,
        scope: RetrievalScope,
        embedding_config: EmbeddingConfig,
        limits: RetrievalExploreLimits,
    ) -> RetrievalExplorationResult:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_EMBEDDING_QUERY_CHARACTERS
        ):
            raise RetrievalContractError("query must be non-blank and bounded")
        if not isinstance(scope, RetrievalScope):
            raise RetrievalContractError("scope must be a RetrievalScope")
        if not isinstance(embedding_config, EmbeddingConfig):
            raise RetrievalContractError("embedding_config must be an EmbeddingConfig")
        if not isinstance(limits, RetrievalExploreLimits):
            raise RetrievalContractError("limits must be RetrievalExploreLimits")

        query_sha256 = hashlib.sha256(query.encode("utf-8")).hexdigest()
        trace_attributes = {
            "retrieval.query.sha256": query_sha256,
            "retrieval.scope.grade": scope.grade,
            "retrieval.scope.medium_id": str(scope.medium_id),
            "retrieval.scope.curriculum_version_id": str(scope.curriculum_version_id),
        }
        with self._telemetry.span("retrieval.explore", attributes=trace_attributes) as span:
            total_started = self._clock()
            validation_finished = total_started
            embedding_finished = total_started
            try:
                self._embedding_providers.ensure_provider(embedding_config)
                persisted_config = await self._resolve_embedding_configuration(embedding_config)
                if not await self._scope_exists(scope):
                    raise RetrievalScopeNotFoundError
                validation_finished = self._clock()

                embedding_result = await self._embedding_providers.embed_query_async(
                    query,
                    persisted_config,
                )
                embedding_finished = self._clock()

                repository = self._repository_factory(
                    self._session,
                    embedding_config=persisted_config,
                    candidate_limit=limits.candidate_limit,
                )
                retrieval = await HybridRetrievalService(
                    repository,
                    fusion_config=FusionConfig(
                        limit=limits.top_k,
                        max_candidates_per_channel=limits.candidate_limit,
                    ),
                    context_limits=ContextLimits(
                        max_items=limits.max_context_items,
                        max_total_characters=limits.max_context_characters,
                        max_item_characters=limits.max_context_item_characters,
                    ),
                    clock=self._clock,
                ).retrieve(
                    query=query,
                    query_vector=embedding_result.vector,
                    filters=scope,
                )
            except Exception as error:
                total_finished = self._clock()
                self._telemetry.retrieval_completed(
                    span=span,
                    query_sha256=query_sha256,
                    outcome="failed",
                    failure_code=_retrieval_failure_code(error),
                    candidate_count=0,
                    context_count=0,
                    validation_latency_ms=_elapsed_ms(total_started, validation_finished),
                    embedding_latency_ms=_elapsed_ms(validation_finished, embedding_finished),
                    candidate_retrieval_latency_ms=0.0,
                    fusion_latency_ms=0.0,
                    context_building_latency_ms=0.0,
                    total_latency_ms=_elapsed_ms(total_started, total_finished),
                )
                raise

            total_finished = self._clock()
            result = RetrievalExplorationResult(
                query=query,
                scope=scope,
                embedding_config=persisted_config,
                limits=limits,
                retrieval=retrieval,
                latency=RetrievalExplorationLatency(
                    validation_ms=_elapsed_ms(total_started, validation_finished),
                    embedding_ms=_elapsed_ms(validation_finished, embedding_finished),
                    candidate_retrieval_ms=retrieval.latency.candidate_retrieval_ms,
                    fusion_ms=retrieval.latency.fusion_ms,
                    context_building_ms=retrieval.latency.context_building_ms,
                    total_ms=_elapsed_ms(total_started, total_finished),
                ),
            )
            self._telemetry.retrieval_completed(
                span=span,
                query_sha256=query_sha256,
                outcome="succeeded",
                failure_code=None,
                candidate_count=len(retrieval.ranked_candidates),
                context_count=len(retrieval.context.items),
                validation_latency_ms=result.latency.validation_ms,
                embedding_latency_ms=result.latency.embedding_ms,
                candidate_retrieval_latency_ms=result.latency.candidate_retrieval_ms,
                fusion_latency_ms=result.latency.fusion_ms,
                context_building_latency_ms=result.latency.context_building_ms,
                total_latency_ms=result.latency.total_ms,
            )
            return result

    async def _resolve_embedding_configuration(
        self,
        requested: EmbeddingConfig,
    ) -> EmbeddingConfig:
        model = await self._session.scalar(
            select(EmbeddingConfigurationModel).where(
                EmbeddingConfigurationModel.provider == requested.provider,
                EmbeddingConfigurationModel.model == requested.model,
                EmbeddingConfigurationModel.dimension == requested.dimension,
                EmbeddingConfigurationModel.version == requested.version,
                EmbeddingConfigurationModel.config_fingerprint == requested.config_fingerprint,
            )
        )
        if not isinstance(model, EmbeddingConfigurationModel):
            raise EmbeddingConfigurationNotFoundError
        return model.to_domain()

    async def _scope_exists(self, scope: RetrievalScope) -> bool:
        if scope.unit_ids:
            unit_count = await self._session.scalar(
                select(func.count(CurriculumUnitModel.id)).where(
                    CurriculumUnitModel.id.in_(scope.unit_ids),
                    CurriculumUnitModel.curriculum_version_id == scope.curriculum_version_id,
                    CurriculumUnitModel.active.is_(True),
                )
            )
            if unit_count != len(scope.unit_ids):
                return False
        if scope.lesson_ids:
            lesson_count = await self._session.scalar(
                select(func.count(CurriculumLessonModel.id)).where(
                    CurriculumLessonModel.id.in_(scope.lesson_ids),
                    CurriculumLessonModel.curriculum_version_id == scope.curriculum_version_id,
                    CurriculumLessonModel.unit_id.in_(scope.unit_ids),
                    CurriculumLessonModel.active.is_(True),
                )
            )
            if lesson_count != len(scope.lesson_ids):
                return False
        competency = aliased(TaxonomyNodeModel, name="explorer_competency")
        statement = (
            select(CurriculumVersionModel.id)
            .join(
                ExamConfigurationModel,
                ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
            )
            .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
            .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
            .join(
                competency,
                and_(
                    competency.id == scope.taxonomy.competency_id,
                    competency.curriculum_version_id == CurriculumVersionModel.id,
                    competency.parent_id.is_(None),
                    competency.level == TaxonomyLevel.COMPETENCY,
                    competency.active.is_(True),
                    competency.review_state == TaxonomyReviewState.REVIEWED,
                ),
            )
            .where(
                ExamConfigurationModel.id == scope.exam_id,
                ExamConfigurationModel.grade == scope.grade,
                ExamConfigurationModel.active.is_(True),
                MediumModel.id == scope.medium_id,
                MediumModel.active.is_(True),
                SubjectModel.id == scope.subject_id,
                SubjectModel.active.is_(True),
                CurriculumVersionModel.id == scope.curriculum_version_id,
                CurriculumVersionModel.active.is_(True),
            )
        )
        parent = competency
        for identifier, level in (
            (scope.taxonomy.skill_id, TaxonomyLevel.SKILL),
            (scope.taxonomy.sub_skill_id, TaxonomyLevel.SUB_SKILL),
            (scope.taxonomy.learning_concept_id, TaxonomyLevel.LEARNING_CONCEPT),
        ):
            if identifier is None:
                break
            node = aliased(TaxonomyNodeModel, name=f"explorer_{level.value}")
            statement = statement.join(
                node,
                and_(
                    node.id == identifier,
                    node.curriculum_version_id == CurriculumVersionModel.id,
                    node.parent_id == parent.id,
                    node.level == level,
                    node.active.is_(True),
                    node.review_state == TaxonomyReviewState.REVIEWED,
                ),
            )
            parent = node
        return await self._session.scalar(statement) is not None
