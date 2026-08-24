from datetime import datetime
from fractions import Fraction
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import PracticeRecommendation
from .forecast import PracticePriorityMethod
from .repository import AnalyticsRunRecord
from .service import (
    AnalyticsDataQuality,
    AnalyticsExclusionReason,
    AnalyticsRunConfig,
    serialize_data_quality,
)

StrictInteger = Annotated[int, Field(strict=True)]
PositiveDenominator = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]


class ExactFraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: StrictInteger
    denominator: PositiveDenominator

    def to_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


class AnalyticsRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_training_years: int = Field(default=2, ge=1, le=20)
    top_k_skills: int = Field(default=3, ge=1, le=100)
    meaningful_improvement: ExactFraction = Field(
        default_factory=lambda: ExactFraction(numerator=1, denominator=100)
    )

    @model_validator(mode="after")
    def validate_meaningful_improvement(self) -> Self:
        value = self.meaningful_improvement.to_fraction()
        if not Fraction() < value <= 1:
            raise ValueError("meaningful_improvement must be in (0, 1]")
        return self

    def to_domain(self) -> AnalyticsRunConfig:
        return AnalyticsRunConfig(
            minimum_training_years=self.minimum_training_years,
            top_k_skills=self.top_k_skills,
            meaningful_improvement=self.meaningful_improvement.to_fraction(),
        )


class AnalyticsDataQualityExclusionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: AnalyticsExclusionReason
    count: int
    question_ids: list[UUID]


class AnalyticsDataQualityResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    considered_count: int
    included_count: int
    excluded_count: int
    exclusions: list[AnalyticsDataQualityExclusionResponse]


class SourceVersionResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_document_id: UUID
    source_version: str


class SyllabusSkillResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    curriculum_version_id: UUID
    competency_id: UUID
    skill_id: UUID
    title: str
    balance_weight: int


class AnalyticsInputResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    observation_ids: list[UUID]
    observation_fingerprint: str
    selection_fingerprint: str
    syllabus: list[SyllabusSkillResponse]
    years: list[int]


class PriorityWeightsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    syllabus: ExactFraction
    frequency: ExactFraction
    marks: ExactFraction
    recency: ExactFraction


class SynchronousLimitsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    maximum_records: int
    maximum_years: int


class AnalyticsConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_training_years: int
    top_k_skills: int
    meaningful_improvement: ExactFraction
    priority_weights: PriorityWeightsResponse
    synchronous_limits: SynchronousLimitsResponse


class AnalyticsVersionsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    statistics: str
    practice_priority: str
    baseline: str
    backtest: str


class DistributionBucketResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str | int
    question_count: int
    total_marks: int
    question_share: ExactFraction
    marks_share: ExactFraction


class HistoricalStatisticsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    curriculum_version_id: UUID
    algorithm_version: str
    years: list[int]
    observation_count: int
    total_marks: int
    competency_distribution: list[DistributionBucketResponse]
    skill_distribution: list[DistributionBucketResponse]
    question_type_distribution: list[DistributionBucketResponse]
    difficulty_distribution: list[DistributionBucketResponse]
    marks_distribution: list[DistributionBucketResponse]
    input_observation_ids: list[UUID]
    sources: list[SourceVersionResponse]
    input_fingerprint: str


class PracticePriorityFeaturesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    syllabus_share: ExactFraction
    question_frequency_share: ExactFraction
    marks_share: ExactFraction
    recency_gap_share: ExactFraction
    evidence_question_count: int
    evidence_marks: int
    last_observed_year: int | None


class PracticePriorityResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    competency_id: UUID
    skill_id: UUID
    skill_title: str
    practice_share: ExactFraction
    features: PracticePriorityFeaturesResponse
    evidence_language: str


class PracticePriorityRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    curriculum_version_id: UUID
    target_year: int
    evidence_through_year: int | None
    method: PracticePriorityMethod
    recommendation: PracticeRecommendation
    algorithm_version: str
    config_fingerprint: str
    run_fingerprint: str
    feature_definitions: list[str]
    random_seed: None
    input_observation_ids: list[UUID]
    sources: list[SourceVersionResponse]
    priorities: list[PracticePriorityResponse]


class BacktestMetricsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    competency_distribution_error: ExactFraction
    skill_distribution_error: ExactFraction
    competency_distribution_accuracy: ExactFraction
    skill_distribution_accuracy: ExactFraction
    top_k_skill_hit_rate: ExactFraction
    composite_score: ExactFraction


class LeakageAuditResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    training_cutoff_exclusive: int
    latest_training_year: int
    training_observation_ids: list[UUID]
    heldout_observation_ids: list[UUID]
    overlapping_observation_ids: list[UUID]


class BacktestWindowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    training_years: list[int]
    heldout_year: int
    leakage_audit: LeakageAuditResponse
    training_input_fingerprint: str
    heldout_input_fingerprint: str
    heldout_sources: list[SourceVersionResponse]
    method_run: PracticePriorityRunResponse
    baseline_run: PracticePriorityRunResponse
    method_metrics: BacktestMetricsResponse
    baseline_metrics: BacktestMetricsResponse
    baseline_delta: ExactFraction


class BacktestAggregateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_count: int
    mean_method_score: ExactFraction
    mean_baseline_score: ExactFraction
    baseline_delta: ExactFraction
    method_score_variance: ExactFraction
    baseline_score_variance: ExactFraction


class PracticeRecommendationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: PracticeRecommendation
    selected_method: PracticePriorityMethod
    observed_baseline_delta: ExactFraction
    meaningful_improvement: ExactFraction
    language: str


class RollingBacktestResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    backtest_version: str
    config_fingerprint: str
    input_fingerprint: str
    sources: list[SourceVersionResponse]
    limitations: list[str]
    windows: list[BacktestWindowResponse]
    aggregate: BacktestAggregateResponse
    recommendation: PracticeRecommendationResponse
    recommended_run: PracticePriorityRunResponse


class AnalyticsResultsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    statistics: HistoricalStatisticsResponse
    backtest: RollingBacktestResponse


class AnalyticsRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    curriculum_version_id: UUID
    run_fingerprint: str
    config_fingerprint: str
    input_fingerprint: str
    source_fingerprint: str
    result_fingerprint: str
    versions: AnalyticsVersionsResponse
    config: AnalyticsConfigResponse
    input: AnalyticsInputResponse
    sources: list[SourceVersionResponse]
    data_quality: AnalyticsDataQualityResponse
    result: AnalyticsResultsResponse
    compute_duration_ms: int
    created_by: UUID
    created_at: datetime
    deduplicated: bool = False

    @classmethod
    def from_record(
        cls,
        record: AnalyticsRunRecord,
        *,
        deduplicated: bool = False,
    ) -> Self:
        return cls(
            id=record.id,
            curriculum_version_id=record.curriculum_version_id,
            run_fingerprint=record.run_fingerprint,
            config_fingerprint=record.config_fingerprint,
            input_fingerprint=record.input_fingerprint,
            source_fingerprint=record.source_fingerprint,
            result_fingerprint=record.result_fingerprint,
            versions=AnalyticsVersionsResponse(
                statistics=record.statistics_algorithm_version,
                practice_priority=record.practice_priority_algorithm_version,
                baseline=record.baseline_algorithm_version,
                backtest=record.backtest_algorithm_version,
            ),
            config=AnalyticsConfigResponse.model_validate(record.config),
            input=AnalyticsInputResponse.model_validate(record.input_snapshot),
            sources=[SourceVersionResponse.model_validate(item) for item in record.source_versions],
            data_quality=AnalyticsDataQualityResponse.model_validate(record.data_quality),
            result=AnalyticsResultsResponse.model_validate(record.result),
            compute_duration_ms=record.compute_duration_ms,
            created_by=record.created_by,
            created_at=record.created_at,
            deduplicated=deduplicated,
        )


class AnalyticsRunSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    curriculum_version_id: UUID
    run_fingerprint: str
    config_fingerprint: str
    input_fingerprint: str
    source_fingerprint: str
    result_fingerprint: str
    statistics_algorithm_version: str
    practice_priority_algorithm_version: str
    baseline_algorithm_version: str
    backtest_algorithm_version: str
    included_count: int
    excluded_count: int
    aggregate: BacktestAggregateResponse
    recommendation: PracticeRecommendationResponse
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_record(cls, record: AnalyticsRunRecord) -> Self:
        quality = AnalyticsDataQualityResponse.model_validate(record.data_quality)
        result = AnalyticsResultsResponse.model_validate(record.result)
        return cls(
            id=record.id,
            curriculum_version_id=record.curriculum_version_id,
            run_fingerprint=record.run_fingerprint,
            config_fingerprint=record.config_fingerprint,
            input_fingerprint=record.input_fingerprint,
            source_fingerprint=record.source_fingerprint,
            result_fingerprint=record.result_fingerprint,
            statistics_algorithm_version=record.statistics_algorithm_version,
            practice_priority_algorithm_version=record.practice_priority_algorithm_version,
            baseline_algorithm_version=record.baseline_algorithm_version,
            backtest_algorithm_version=record.backtest_algorithm_version,
            included_count=quality.included_count,
            excluded_count=quality.excluded_count,
            aggregate=result.backtest.aggregate,
            recommendation=result.backtest.recommendation,
            created_by=record.created_by,
            created_at=record.created_at,
        )


def data_quality_error_payload(quality: AnalyticsDataQuality) -> dict[str, object]:
    return serialize_data_quality(quality)
