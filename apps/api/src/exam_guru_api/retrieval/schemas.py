"""Strict REST contracts for the read-only RAG retrieval explorer."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    model_validator,
)

from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.retrieval.context import ContextTrust
from exam_guru_api.retrieval.domain import RetrievalScope, SourceProvenance, TaxonomyScope
from exam_guru_api.retrieval.explorer import (
    MAX_EXPLORER_CANDIDATES,
    RetrievalExplorationResult,
    RetrievalExploreLimits,
)
from exam_guru_api.retrieval.fusion import MAX_FUSION_RESULTS

BoundedQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096),
]
ProviderName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
ModelName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
VersionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
ConfigFingerprint = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalTaxonomyScopeRequest(StrictRequestModel):
    competency_id: UUID
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None

    @model_validator(mode="after")
    def validate_hierarchy(self) -> Self:
        if self.sub_skill_id is not None and self.skill_id is None:
            raise ValueError("sub_skill_id requires skill_id")
        if self.learning_concept_id is not None and self.sub_skill_id is None:
            raise ValueError("learning_concept_id requires sub_skill_id")
        return self

    def to_domain(self) -> TaxonomyScope:
        return TaxonomyScope(
            competency_id=self.competency_id,
            skill_id=self.skill_id,
            sub_skill_id=self.sub_skill_id,
            learning_concept_id=self.learning_concept_id,
        )


class RetrievalScopeRequest(StrictRequestModel):
    grade: Literal[5]
    exam_id: UUID
    medium_id: UUID
    curriculum_version_id: UUID
    taxonomy: RetrievalTaxonomyScopeRequest

    def to_domain(self) -> RetrievalScope:
        return RetrievalScope(
            grade=self.grade,
            exam_id=self.exam_id,
            medium_id=self.medium_id,
            curriculum_version_id=self.curriculum_version_id,
            taxonomy=self.taxonomy.to_domain(),
        )


class EmbeddingConfigRequest(StrictRequestModel):
    provider: ProviderName
    model: ModelName
    dimension: int = Field(ge=1, le=4_096, strict=True)
    version: VersionName
    config_fingerprint: ConfigFingerprint

    def to_domain(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            provider=self.provider,
            model=self.model,
            dimension=self.dimension,
            version=self.version,
            config_fingerprint=self.config_fingerprint,
        )


class RetrievalExploreLimitsRequest(StrictRequestModel):
    candidate_limit: int = Field(ge=1, le=MAX_EXPLORER_CANDIDATES, strict=True)
    top_k: int = Field(ge=1, le=MAX_FUSION_RESULTS, strict=True)
    max_context_items: int = Field(ge=1, le=100, strict=True)
    max_context_characters: int = Field(ge=1, le=100_000, strict=True)
    max_context_item_characters: int = Field(ge=1, le=20_000, strict=True)

    @model_validator(mode="after")
    def validate_amplification_bounds(self) -> Self:
        if self.top_k > self.candidate_limit:
            raise ValueError("top_k cannot exceed candidate_limit")
        if self.max_context_items > self.top_k:
            raise ValueError("max_context_items cannot exceed top_k")
        if self.max_context_item_characters > self.max_context_characters:
            raise ValueError("max_context_item_characters cannot exceed max_context_characters")
        return self

    def to_domain(self) -> RetrievalExploreLimits:
        return RetrievalExploreLimits(
            candidate_limit=self.candidate_limit,
            top_k=self.top_k,
            max_context_items=self.max_context_items,
            max_context_characters=self.max_context_characters,
            max_context_item_characters=self.max_context_item_characters,
        )


class RetrievalExploreRequest(StrictRequestModel):
    query: BoundedQuery
    scope: RetrievalScopeRequest
    embedding_config: EmbeddingConfigRequest
    limits: RetrievalExploreLimitsRequest


class RetrievalTaxonomyScopeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    competency_id: UUID
    skill_id: UUID | None
    sub_skill_id: UUID | None
    learning_concept_id: UUID | None

    @classmethod
    def from_domain(cls, taxonomy: TaxonomyScope) -> Self:
        return cls(
            competency_id=taxonomy.competency_id,
            skill_id=taxonomy.skill_id,
            sub_skill_id=taxonomy.sub_skill_id,
            learning_concept_id=taxonomy.learning_concept_id,
        )


class RetrievalScopeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    grade: int
    exam_id: UUID
    medium_id: UUID
    curriculum_version_id: UUID
    taxonomy: RetrievalTaxonomyScopeResponse

    @classmethod
    def from_domain(cls, scope: RetrievalScope) -> Self:
        return cls(
            grade=scope.grade,
            exam_id=scope.exam_id,
            medium_id=scope.medium_id,
            curriculum_version_id=scope.curriculum_version_id,
            taxonomy=RetrievalTaxonomyScopeResponse.from_domain(scope.taxonomy),
        )


class EmbeddingConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    dimension: int
    version: str
    config_fingerprint: str

    @classmethod
    def from_domain(cls, config: EmbeddingConfig) -> Self:
        return cls(
            provider=config.provider,
            model=config.model,
            dimension=config.dimension,
            version=config.version,
            config_fingerprint=config.config_fingerprint,
        )


class RetrievalProvenanceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_document_id: UUID
    page_number: int
    source_block_id: UUID | None

    @classmethod
    def from_domain(cls, provenance: SourceProvenance) -> Self:
        return cls(
            source_document_id=provenance.source_document_id,
            page_number=provenance.page_number,
            source_block_id=provenance.source_block_id,
        )


class RetrievalChannelCandidateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    chunk_id: UUID
    text: str
    score: FiniteFloat
    scope: RetrievalScopeResponse
    provenance: RetrievalProvenanceResponse
    trust: ContextTrust = ContextTrust.UNTRUSTED_SOURCE_DATA


class RetrievalChannelsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    lexical: list[RetrievalChannelCandidateResponse]
    vector: list[RetrievalChannelCandidateResponse]


class FusedRetrievalCandidateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    chunk_id: UUID
    text: str
    score: FiniteFloat = Field(ge=0)
    lexical_rank: int | None
    vector_rank: int | None
    source_chunk_ids: list[UUID]
    provenances: list[RetrievalProvenanceResponse]
    scope: RetrievalScopeResponse
    trust: ContextTrust = ContextTrust.UNTRUSTED_SOURCE_DATA


class RetrievalContextLimitsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_items: int
    max_total_characters: int
    max_item_characters: int


class RetrievalContextItemResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    text: str
    scope: RetrievalScopeResponse
    source_chunk_ids: list[UUID]
    provenances: list[RetrievalProvenanceResponse]
    fusion_score: FiniteFloat = Field(ge=0)
    original_character_count: int
    truncated: bool
    trust: ContextTrust


class RetrievalContextResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[RetrievalContextItemResponse]
    limits: RetrievalContextLimitsResponse
    character_count: int
    omitted_candidate_count: int
    trust: ContextTrust


class RetrievalLatencyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    validation_ms: FiniteFloat = Field(ge=0)
    embedding_ms: FiniteFloat = Field(ge=0)
    candidate_retrieval_ms: FiniteFloat = Field(ge=0)
    fusion_ms: FiniteFloat = Field(ge=0)
    context_building_ms: FiniteFloat = Field(ge=0)
    total_ms: FiniteFloat = Field(ge=0)


class RetrievalDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    hard_scope_filter_applied: Literal[True] = True
    lexical_candidate_count: int = Field(ge=0)
    vector_candidate_count: int = Field(ge=0)
    filtered_out_candidate_count: int = Field(ge=0)
    fused_candidate_count: int = Field(ge=0)
    deduplicated_source_count: int = Field(ge=0)
    context_item_count: int = Field(ge=0)
    context_character_count: int = Field(ge=0)
    omitted_fused_candidate_count: int = Field(ge=0)


class RetrievalExploreResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    scope: RetrievalScopeResponse
    embedding_config: EmbeddingConfigResponse
    limits: RetrievalExploreLimitsRequest
    channels: RetrievalChannelsResponse
    fused_candidates: list[FusedRetrievalCandidateResponse]
    context: RetrievalContextResponse
    latency_ms: RetrievalLatencyResponse
    diagnostics: RetrievalDiagnosticsResponse

    @classmethod
    def from_domain(cls, result: RetrievalExplorationResult) -> Self:
        retrieval = result.retrieval
        context = retrieval.context
        return cls(
            query=result.query,
            scope=RetrievalScopeResponse.from_domain(result.scope),
            embedding_config=EmbeddingConfigResponse.from_domain(result.embedding_config),
            limits=RetrievalExploreLimitsRequest(
                candidate_limit=result.limits.candidate_limit,
                top_k=result.limits.top_k,
                max_context_items=result.limits.max_context_items,
                max_context_characters=result.limits.max_context_characters,
                max_context_item_characters=result.limits.max_context_item_characters,
            ),
            channels=RetrievalChannelsResponse(
                lexical=[
                    RetrievalChannelCandidateResponse(
                        rank=rank,
                        chunk_id=candidate.record.chunk_id,
                        text=candidate.record.text,
                        score=candidate.score,
                        scope=RetrievalScopeResponse.from_domain(candidate.record.scope),
                        provenance=RetrievalProvenanceResponse.from_domain(
                            candidate.record.provenance
                        ),
                    )
                    for rank, candidate in enumerate(retrieval.lexical_candidates, start=1)
                ],
                vector=[
                    RetrievalChannelCandidateResponse(
                        rank=rank,
                        chunk_id=candidate.record.chunk_id,
                        text=candidate.record.text,
                        score=candidate.score,
                        scope=RetrievalScopeResponse.from_domain(candidate.record.scope),
                        provenance=RetrievalProvenanceResponse.from_domain(
                            candidate.record.provenance
                        ),
                    )
                    for rank, candidate in enumerate(retrieval.vector_candidates, start=1)
                ],
            ),
            fused_candidates=[
                FusedRetrievalCandidateResponse(
                    rank=rank,
                    chunk_id=candidate.record.chunk_id,
                    text=candidate.record.text,
                    score=candidate.score,
                    lexical_rank=candidate.lexical_rank,
                    vector_rank=candidate.vector_rank,
                    source_chunk_ids=list(candidate.source_chunk_ids),
                    provenances=[
                        RetrievalProvenanceResponse.from_domain(provenance)
                        for provenance in candidate.provenances
                    ],
                    scope=RetrievalScopeResponse.from_domain(candidate.record.scope),
                )
                for rank, candidate in enumerate(retrieval.ranked_candidates, start=1)
            ],
            context=RetrievalContextResponse(
                items=[
                    RetrievalContextItemResponse(
                        rank=item.rank,
                        text=item.text,
                        scope=RetrievalScopeResponse.from_domain(item.scope),
                        source_chunk_ids=list(item.source_chunk_ids),
                        provenances=[
                            RetrievalProvenanceResponse.from_domain(provenance)
                            for provenance in item.provenances
                        ],
                        fusion_score=item.fusion_score,
                        original_character_count=item.original_character_count,
                        truncated=item.truncated,
                        trust=item.trust,
                    )
                    for item in context.items
                ],
                limits=RetrievalContextLimitsResponse(
                    max_items=context.limits.max_items,
                    max_total_characters=context.limits.max_total_characters,
                    max_item_characters=context.limits.max_item_characters,
                ),
                character_count=context.character_count,
                omitted_candidate_count=context.omitted_candidate_count,
                trust=context.trust,
            ),
            latency_ms=RetrievalLatencyResponse(
                validation_ms=result.latency.validation_ms,
                embedding_ms=result.latency.embedding_ms,
                candidate_retrieval_ms=result.latency.candidate_retrieval_ms,
                fusion_ms=result.latency.fusion_ms,
                context_building_ms=result.latency.context_building_ms,
                total_ms=result.latency.total_ms,
            ),
            diagnostics=RetrievalDiagnosticsResponse(
                lexical_candidate_count=retrieval.lexical_candidate_count,
                vector_candidate_count=retrieval.vector_candidate_count,
                filtered_out_candidate_count=retrieval.filtered_candidate_count,
                fused_candidate_count=len(retrieval.ranked_candidates),
                deduplicated_source_count=sum(
                    len(candidate.source_chunk_ids) - 1 for candidate in retrieval.ranked_candidates
                ),
                context_item_count=len(context.items),
                context_character_count=context.character_count,
                omitted_fused_candidate_count=context.omitted_candidate_count,
            ),
        )
