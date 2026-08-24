import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from time import perf_counter_ns
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.knowledge.domain import DifficultyLabel, ReviewState

from .backtest import (
    BacktestConfig,
    BacktestMetrics,
    RollingBacktestResult,
    run_rolling_backtest,
)
from .domain import (
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    QuestionType,
    SourceVersion,
    SyllabusSkill,
    observation_fingerprint,
    validate_syllabus,
)
from .forecast import (
    PRACTICE_PRIORITY_ALGORITHM_VERSION,
    SYLLABUS_BASELINE_ALGORITHM_VERSION,
    PracticePriorityConfig,
    PracticePriorityRun,
)
from .repository import (
    AnalyticsQuestionCandidate,
    AnalyticsRunRecord,
    AnalyticsRunWrite,
    RepositoryAnalyticsRunResult,
    SqlAlchemyAnalyticsRepository,
)
from .statistics import (
    HISTORICAL_STATISTICS_VERSION,
    HistoricalStatistics,
    calculate_historical_statistics,
)

MAX_SYNC_ANALYTICS_RECORDS = 5_000
MAX_SYNC_ANALYTICS_YEARS = 50
PERSISTED_ANALYTICS_LIMITATIONS = (
    "The syllabus-balanced baseline assigns equal weight to each active reviewed skill because "
    "taxonomy records do not carry a separate balance weight.",
)
_ANALYTICS_RUN_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/analytics-runs")


class AnalyticsExclusionReason(StrEnum):
    COMPETENCY_MISMATCH = "competency_mismatch"
    INCOMPLETE_DIFFICULTY_EVIDENCE = "incomplete_difficulty_evidence"
    INVALID_SOURCE_CHECKSUM = "invalid_source_checksum"
    MISSING_COMPETENCY_ID = "missing_competency_id"
    MISSING_SKILL_ID = "missing_skill_id"
    MISSING_SOURCE_BLOCK_ID = "missing_source_block_id"
    MISSING_SOURCE_CHECKSUM = "missing_source_checksum"
    NON_FINITE_DIFFICULTY_CONFIDENCE = "non_finite_difficulty_confidence"
    NOT_REVIEWED = "not_reviewed"
    SKILL_NOT_IN_REVIEWED_SYLLABUS = "skill_not_in_reviewed_syllabus"
    SOURCE_NOT_TRUSTED = "source_not_trusted"


class AnalyticsCurriculumNotFoundError(LookupError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(f"curriculum version not found: {curriculum_version_id}")


class AnalyticsSyllabusEmptyError(ValueError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(f"no active reviewed skills for curriculum {curriculum_version_id}")


class AnalyticsRecordLimitError(ValueError):
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        super().__init__(f"analytics synchronous record limit is {maximum}")


class AnalyticsYearLimitError(ValueError):
    def __init__(self, maximum: int, actual: int) -> None:
        self.maximum = maximum
        self.actual = actual
        super().__init__(f"analytics synchronous year limit is {maximum}; found {actual}")


class AnalyticsInsufficientHistoryError(ValueError):
    def __init__(
        self,
        *,
        minimum_training_years: int,
        available_years: tuple[int, ...],
        data_quality: "AnalyticsDataQuality",
    ) -> None:
        self.minimum_training_years = minimum_training_years
        self.required_year_count = minimum_training_years + 1
        self.available_years = available_years
        self.data_quality = data_quality
        super().__init__(
            "rolling backtest requires "
            f"{self.required_year_count} eligible years; found {len(available_years)}"
        )


@dataclass(frozen=True, slots=True)
class AnalyticsDataQualityExclusion:
    reason: AnalyticsExclusionReason
    count: int
    question_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsDataQuality:
    considered_count: int
    included_count: int
    excluded_count: int
    exclusions: tuple[AnalyticsDataQualityExclusion, ...]


@dataclass(frozen=True, slots=True)
class LoadedAnalyticsInputs:
    observations: tuple[HistoricalQuestionObservation, ...]
    syllabus: tuple[SyllabusSkill, ...]
    data_quality: AnalyticsDataQuality
    selection_fingerprint: str


@dataclass(frozen=True, slots=True)
class AnalyticsRunConfig:
    minimum_training_years: int = 2
    top_k_skills: int = 3
    meaningful_improvement: Fraction = Fraction(1, 100)

    def to_backtest_config(self) -> BacktestConfig:
        return BacktestConfig(
            minimum_training_years=self.minimum_training_years,
            top_k_skills=self.top_k_skills,
            meaningful_improvement=self.meaningful_improvement,
        )


@dataclass(frozen=True, slots=True)
class AnalyticsRunCreationResult:
    record: AnalyticsRunRecord
    deduplicated: bool


def build_loaded_analytics_inputs(
    candidates: tuple[AnalyticsQuestionCandidate, ...],
    syllabus: tuple[SyllabusSkill, ...],
) -> LoadedAnalyticsInputs:
    canonical_syllabus = validate_syllabus(syllabus)
    canonical_candidates = tuple(sorted(candidates, key=lambda item: item.id.int))
    skills_by_id = {item.skill_id: item for item in canonical_syllabus}
    exclusions: dict[AnalyticsExclusionReason, list[UUID]] = {}
    observations: list[HistoricalQuestionObservation] = []

    for candidate in canonical_candidates:
        reasons: list[AnalyticsExclusionReason] = []
        if candidate.review_state is not ReviewState.REVIEWED:
            reasons.append(AnalyticsExclusionReason.NOT_REVIEWED)
        if candidate.competency_id is None:
            reasons.append(AnalyticsExclusionReason.MISSING_COMPETENCY_ID)
        if candidate.skill_id is None:
            reasons.append(AnalyticsExclusionReason.MISSING_SKILL_ID)

        difficulty_values = (
            candidate.difficulty_label,
            candidate.difficulty_confidence,
            candidate.difficulty_source,
        )
        if any(value is None for value in difficulty_values):
            reasons.append(AnalyticsExclusionReason.INCOMPLETE_DIFFICULTY_EVIDENCE)
        elif not math.isfinite(cast(float, candidate.difficulty_confidence)):
            reasons.append(AnalyticsExclusionReason.NON_FINITE_DIFFICULTY_CONFIDENCE)

        if candidate.source_block_id is None:
            reasons.append(AnalyticsExclusionReason.MISSING_SOURCE_BLOCK_ID)
        if candidate.source_status is not ExtractionStatus.TRUSTED:
            reasons.append(AnalyticsExclusionReason.SOURCE_NOT_TRUSTED)
        if candidate.source_checksum_sha256 is None:
            reasons.append(AnalyticsExclusionReason.MISSING_SOURCE_CHECKSUM)
        elif not _valid_sha256(candidate.source_checksum_sha256):
            reasons.append(AnalyticsExclusionReason.INVALID_SOURCE_CHECKSUM)

        syllabus_skill = (
            skills_by_id.get(candidate.skill_id) if candidate.skill_id is not None else None
        )
        if candidate.skill_id is not None and syllabus_skill is None:
            reasons.append(AnalyticsExclusionReason.SKILL_NOT_IN_REVIEWED_SYLLABUS)
        elif (
            syllabus_skill is not None
            and candidate.competency_id is not None
            and syllabus_skill.competency_id != candidate.competency_id
        ):
            reasons.append(AnalyticsExclusionReason.COMPETENCY_MISMATCH)

        if reasons:
            for reason in reasons:
                exclusions.setdefault(reason, []).append(candidate.id)
            continue

        observations.append(
            HistoricalQuestionObservation(
                id=candidate.id,
                curriculum_version_id=candidate.curriculum_version_id,
                year=candidate.year,
                paper_code=candidate.paper_code,
                question_number=candidate.question_number,
                competency_id=cast(UUID, candidate.competency_id),
                skill_id=cast(UUID, candidate.skill_id),
                question_type=QuestionType(candidate.question_type.value),
                difficulty=Difficulty(cast(DifficultyLabel, candidate.difficulty_label).value),
                marks=candidate.marks,
                provenance=ObservationProvenance(
                    source_document_id=candidate.source_document_id,
                    source_version=f"sha256:{candidate.source_checksum_sha256}",
                    page_number=candidate.page_number,
                    source_block_id=candidate.source_block_id,
                ),
            )
        )

    canonical_observations = tuple(sorted(observations, key=lambda item: item.id.int))
    exclusion_records = tuple(
        AnalyticsDataQualityExclusion(
            reason=reason,
            count=len(question_ids),
            question_ids=tuple(sorted(question_ids, key=lambda item: item.int)),
        )
        for reason, question_ids in sorted(exclusions.items(), key=lambda item: item[0].value)
    )
    quality = AnalyticsDataQuality(
        considered_count=len(candidates),
        included_count=len(canonical_observations),
        excluded_count=len(candidates) - len(canonical_observations),
        exclusions=exclusion_records,
    )
    return LoadedAnalyticsInputs(
        observations=canonical_observations,
        syllabus=canonical_syllabus,
        data_quality=quality,
        selection_fingerprint=fingerprint_payload(
            {
                "candidates": [
                    _candidate_fingerprint_payload(item) for item in canonical_candidates
                ],
                "syllabus": [_syllabus_skill_payload(item) for item in canonical_syllabus],
            }
        ),
    )


class AnalyticsRunService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SqlAlchemyAnalyticsRepository(session)

    async def create_run(
        self,
        curriculum_version_id: UUID,
        config: AnalyticsRunConfig,
        *,
        actor_id: UUID,
    ) -> AnalyticsRunCreationResult:
        await self._ensure_curriculum_exists(curriculum_version_id)
        syllabus = await self._repository.list_syllabus(curriculum_version_id)
        if not syllabus:
            raise AnalyticsSyllabusEmptyError(curriculum_version_id)
        candidates = await self._repository.list_question_candidates(
            curriculum_version_id,
            limit=MAX_SYNC_ANALYTICS_RECORDS + 1,
        )
        if len(candidates) > MAX_SYNC_ANALYTICS_RECORDS:
            raise AnalyticsRecordLimitError(MAX_SYNC_ANALYTICS_RECORDS)
        candidate_years = tuple(sorted({item.year for item in candidates}))
        if len(candidate_years) > MAX_SYNC_ANALYTICS_YEARS:
            raise AnalyticsYearLimitError(MAX_SYNC_ANALYTICS_YEARS, len(candidate_years))

        loaded = build_loaded_analytics_inputs(candidates, syllabus)
        eligible_years = tuple(sorted({item.year for item in loaded.observations}))
        if len(eligible_years) <= config.minimum_training_years:
            raise AnalyticsInsufficientHistoryError(
                minimum_training_years=config.minimum_training_years,
                available_years=eligible_years,
                data_quality=loaded.data_quality,
            )

        started_at = perf_counter_ns()
        statistics = calculate_historical_statistics(loaded.observations)
        priority_config = PracticePriorityConfig()
        backtest = run_rolling_backtest(
            loaded.observations,
            loaded.syllabus,
            config=config.to_backtest_config(),
            priority_config=priority_config,
        )
        compute_duration_ms = (perf_counter_ns() - started_at + 999_999) // 1_000_000
        result_payload = serialize_analytics_results(statistics, backtest)
        data_quality_payload = serialize_data_quality(loaded.data_quality)
        sources_payload = serialize_sources(backtest.sources)
        source_fingerprint = fingerprint_payload(sources_payload)
        input_snapshot = _input_snapshot(loaded)
        input_fingerprint = fingerprint_payload(input_snapshot)
        config_payload = _config_payload(config, priority_config)
        versions = _version_payload(backtest)
        run_fingerprint = fingerprint_payload(
            {
                "config_fingerprint": backtest.config_fingerprint,
                "curriculum_version_id": str(curriculum_version_id),
                "input_fingerprint": input_fingerprint,
                "source_fingerprint": source_fingerprint,
                "versions": versions,
            }
        )
        result_fingerprint = fingerprint_payload(result_payload)
        run = AnalyticsRunWrite(
            id=uuid5(_ANALYTICS_RUN_NAMESPACE, run_fingerprint),
            curriculum_version_id=curriculum_version_id,
            run_fingerprint=run_fingerprint,
            config_fingerprint=backtest.config_fingerprint,
            input_fingerprint=input_fingerprint,
            source_fingerprint=source_fingerprint,
            result_fingerprint=result_fingerprint,
            statistics_algorithm_version=statistics.algorithm_version,
            practice_priority_algorithm_version=PRACTICE_PRIORITY_ALGORITHM_VERSION,
            baseline_algorithm_version=SYLLABUS_BASELINE_ALGORITHM_VERSION,
            backtest_algorithm_version=backtest.backtest_version,
            config=config_payload,
            input_snapshot=input_snapshot,
            source_versions=sources_payload,
            data_quality=data_quality_payload,
            result=result_payload,
            compute_duration_ms=compute_duration_ms,
            created_by=actor_id,
        )
        stored = await self._repository.store_run(run)
        if stored.created:
            self._audit_created(stored, actor_id=actor_id)
            await self._session.commit()
        return AnalyticsRunCreationResult(
            record=stored.record,
            deduplicated=not stored.created,
        )

    async def get_run(
        self,
        curriculum_version_id: UUID,
        run_id: UUID,
    ) -> AnalyticsRunRecord:
        await self._ensure_curriculum_exists(curriculum_version_id)
        return await self._repository.get_run(curriculum_version_id, run_id)

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[AnalyticsRunRecord, ...]:
        await self._ensure_curriculum_exists(curriculum_version_id)
        return await self._repository.list_runs(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        )

    async def _ensure_curriculum_exists(self, curriculum_version_id: UUID) -> None:
        if not await self._repository.curriculum_exists(curriculum_version_id):
            raise AnalyticsCurriculumNotFoundError(curriculum_version_id)

    def _audit_created(
        self,
        stored: RepositoryAnalyticsRunResult,
        *,
        actor_id: UUID,
    ) -> None:
        record = stored.record
        aggregate = cast(
            dict[str, object], cast(dict[str, object], record.result["backtest"])["aggregate"]
        )
        recommendation = cast(
            dict[str, object],
            cast(dict[str, object], record.result["backtest"])["recommendation"],
        )
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="analytics.run.created",
                resource_type="analytics_run",
                resource_id=record.id,
                payload={
                    "curriculum_version_id": str(record.curriculum_version_id),
                    "run_fingerprint": record.run_fingerprint,
                    "config_fingerprint": record.config_fingerprint,
                    "input_fingerprint": record.input_fingerprint,
                    "source_fingerprint": record.source_fingerprint,
                    "result_fingerprint": record.result_fingerprint,
                    "included_count": record.data_quality["included_count"],
                    "excluded_count": record.data_quality["excluded_count"],
                    "window_count": aggregate["window_count"],
                    "recommendation": recommendation["mode"],
                    "algorithm_versions": {
                        "statistics": record.statistics_algorithm_version,
                        "practice_priority": record.practice_priority_algorithm_version,
                        "baseline": record.baseline_algorithm_version,
                        "backtest": record.backtest_algorithm_version,
                    },
                },
            )
        )


def serialize_analytics_results(
    statistics: HistoricalStatistics,
    backtest: RollingBacktestResult,
) -> dict[str, object]:
    return {
        "statistics": {
            "curriculum_version_id": str(statistics.curriculum_version_id),
            "algorithm_version": statistics.algorithm_version,
            "years": list(statistics.years),
            "observation_count": statistics.observation_count,
            "total_marks": statistics.total_marks,
            "competency_distribution": [
                _distribution_payload(item, key=str(item.key))
                for item in statistics.competency_distribution
            ],
            "skill_distribution": [
                _distribution_payload(item, key=str(item.key))
                for item in statistics.skill_distribution
            ],
            "question_type_distribution": [
                _distribution_payload(item, key=item.key.value)
                for item in statistics.question_type_distribution
            ],
            "difficulty_distribution": [
                _distribution_payload(item, key=item.key.value)
                for item in statistics.difficulty_distribution
            ],
            "marks_distribution": [
                _distribution_payload(item, key=item.key) for item in statistics.marks_distribution
            ],
            "input_observation_ids": [str(item) for item in statistics.input_observation_ids],
            "sources": serialize_sources(statistics.sources),
            "input_fingerprint": statistics.input_fingerprint,
        },
        "backtest": {
            "backtest_version": backtest.backtest_version,
            "config_fingerprint": backtest.config_fingerprint,
            "input_fingerprint": backtest.input_fingerprint,
            "sources": serialize_sources(backtest.sources),
            "limitations": [*backtest.limitations, *PERSISTED_ANALYTICS_LIMITATIONS],
            "windows": [_window_payload(item) for item in backtest.windows],
            "aggregate": {
                "window_count": backtest.aggregate.window_count,
                "mean_method_score": fraction_payload(backtest.aggregate.mean_method_score),
                "mean_baseline_score": fraction_payload(backtest.aggregate.mean_baseline_score),
                "baseline_delta": fraction_payload(backtest.aggregate.baseline_delta),
                "method_score_variance": fraction_payload(backtest.aggregate.method_score_variance),
                "baseline_score_variance": fraction_payload(
                    backtest.aggregate.baseline_score_variance
                ),
            },
            "recommendation": {
                "mode": backtest.recommendation.mode.value,
                "selected_method": backtest.recommendation.selected_method.value,
                "observed_baseline_delta": fraction_payload(
                    backtest.recommendation.observed_baseline_delta
                ),
                "meaningful_improvement": fraction_payload(
                    backtest.recommendation.meaningful_improvement
                ),
                "language": backtest.recommendation.language,
            },
            "recommended_run": _priority_run_payload(backtest.recommended_run),
        },
    }


def serialize_data_quality(quality: AnalyticsDataQuality) -> dict[str, object]:
    return {
        "considered_count": quality.considered_count,
        "included_count": quality.included_count,
        "excluded_count": quality.excluded_count,
        "exclusions": [
            {
                "reason": item.reason.value,
                "count": item.count,
                "question_ids": [str(question_id) for question_id in item.question_ids],
            }
            for item in quality.exclusions
        ],
    }


def serialize_sources(sources: tuple[SourceVersion, ...]) -> list[dict[str, object]]:
    return [
        {
            "source_document_id": str(item.source_document_id),
            "source_version": item.source_version,
        }
        for item in sources
    ]


def fraction_payload(value: Fraction) -> dict[str, object]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _candidate_fingerprint_payload(candidate: AnalyticsQuestionCandidate) -> dict[str, object]:
    return {
        "competency_id": _optional_uuid(candidate.competency_id),
        "curriculum_version_id": str(candidate.curriculum_version_id),
        "difficulty_confidence": (
            candidate.difficulty_confidence.hex()
            if candidate.difficulty_confidence is not None
            else None
        ),
        "difficulty_label": (
            candidate.difficulty_label.value if candidate.difficulty_label is not None else None
        ),
        "difficulty_source": candidate.difficulty_source,
        "id": str(candidate.id),
        "marks": candidate.marks,
        "page_number": candidate.page_number,
        "paper_code": candidate.paper_code,
        "question_number": candidate.question_number,
        "question_type": candidate.question_type.value,
        "review_state": candidate.review_state.value,
        "skill_id": _optional_uuid(candidate.skill_id),
        "source_block_id": _optional_uuid(candidate.source_block_id),
        "source_checksum_sha256": candidate.source_checksum_sha256,
        "source_document_id": str(candidate.source_document_id),
        "source_status": (
            candidate.source_status.value if candidate.source_status is not None else None
        ),
        "year": candidate.year,
    }


def _syllabus_skill_payload(skill: SyllabusSkill) -> dict[str, object]:
    return {
        "balance_weight": skill.balance_weight,
        "competency_id": str(skill.competency_id),
        "curriculum_version_id": str(skill.curriculum_version_id),
        "skill_id": str(skill.skill_id),
        "title": skill.title,
    }


def _input_snapshot(loaded: LoadedAnalyticsInputs) -> dict[str, object]:
    return {
        "observation_ids": [str(item.id) for item in loaded.observations],
        "observation_fingerprint": observation_fingerprint(loaded.observations),
        "selection_fingerprint": loaded.selection_fingerprint,
        "syllabus": [_syllabus_skill_payload(item) for item in loaded.syllabus],
        "years": sorted({item.year for item in loaded.observations}),
    }


def _config_payload(
    config: AnalyticsRunConfig,
    priority_config: PracticePriorityConfig,
) -> dict[str, object]:
    return {
        "minimum_training_years": config.minimum_training_years,
        "top_k_skills": config.top_k_skills,
        "meaningful_improvement": fraction_payload(config.meaningful_improvement),
        "priority_weights": {
            "syllabus": fraction_payload(priority_config.syllabus_weight),
            "frequency": fraction_payload(priority_config.frequency_weight),
            "marks": fraction_payload(priority_config.marks_weight),
            "recency": fraction_payload(priority_config.recency_weight),
        },
        "synchronous_limits": {
            "maximum_records": MAX_SYNC_ANALYTICS_RECORDS,
            "maximum_years": MAX_SYNC_ANALYTICS_YEARS,
        },
    }


def _version_payload(backtest: RollingBacktestResult) -> dict[str, object]:
    return {
        "statistics": HISTORICAL_STATISTICS_VERSION,
        "practice_priority": PRACTICE_PRIORITY_ALGORITHM_VERSION,
        "baseline": SYLLABUS_BASELINE_ALGORITHM_VERSION,
        "backtest": backtest.backtest_version,
    }


def _distribution_payload(item: object, *, key: object) -> dict[str, object]:
    bucket = cast("DistributionProtocol", item)
    return {
        "key": key,
        "question_count": bucket.question_count,
        "total_marks": bucket.total_marks,
        "question_share": fraction_payload(bucket.question_share),
        "marks_share": fraction_payload(bucket.marks_share),
    }


class DistributionProtocol:
    question_count: int
    total_marks: int
    question_share: Fraction
    marks_share: Fraction


def _window_payload(window: object) -> dict[str, object]:
    item = cast("BacktestWindowProtocol", window)
    audit = item.leakage_audit
    return {
        "training_years": list(item.training_years),
        "heldout_year": item.heldout_year,
        "leakage_audit": {
            "passed": audit.passed,
            "training_cutoff_exclusive": audit.training_cutoff_exclusive,
            "latest_training_year": audit.latest_training_year,
            "training_observation_ids": [str(value) for value in audit.training_observation_ids],
            "heldout_observation_ids": [str(value) for value in audit.heldout_observation_ids],
            "overlapping_observation_ids": [
                str(value) for value in audit.overlapping_observation_ids
            ],
        },
        "training_input_fingerprint": item.training_input_fingerprint,
        "heldout_input_fingerprint": item.heldout_input_fingerprint,
        "heldout_sources": serialize_sources(item.heldout_sources),
        "method_run": _priority_run_payload(item.method_run),
        "baseline_run": _priority_run_payload(item.baseline_run),
        "method_metrics": _metrics_payload(item.method_metrics),
        "baseline_metrics": _metrics_payload(item.baseline_metrics),
        "baseline_delta": fraction_payload(item.baseline_delta),
    }


class LeakageAuditProtocol:
    passed: bool
    training_cutoff_exclusive: int
    latest_training_year: int
    training_observation_ids: tuple[UUID, ...]
    heldout_observation_ids: tuple[UUID, ...]
    overlapping_observation_ids: tuple[UUID, ...]


class BacktestWindowProtocol:
    training_years: tuple[int, ...]
    heldout_year: int
    leakage_audit: LeakageAuditProtocol
    training_input_fingerprint: str
    heldout_input_fingerprint: str
    heldout_sources: tuple[SourceVersion, ...]
    method_run: PracticePriorityRun
    baseline_run: PracticePriorityRun
    method_metrics: BacktestMetrics
    baseline_metrics: BacktestMetrics
    baseline_delta: Fraction


def _metrics_payload(metrics: BacktestMetrics) -> dict[str, object]:
    return {
        "competency_distribution_error": fraction_payload(metrics.competency_distribution_error),
        "skill_distribution_error": fraction_payload(metrics.skill_distribution_error),
        "competency_distribution_accuracy": fraction_payload(
            metrics.competency_distribution_accuracy
        ),
        "skill_distribution_accuracy": fraction_payload(metrics.skill_distribution_accuracy),
        "top_k_skill_hit_rate": fraction_payload(metrics.top_k_skill_hit_rate),
        "composite_score": fraction_payload(metrics.composite_score),
    }


def _priority_run_payload(run: PracticePriorityRun) -> dict[str, object]:
    return {
        "curriculum_version_id": str(run.curriculum_version_id),
        "target_year": run.target_year,
        "evidence_through_year": run.evidence_through_year,
        "method": run.method.value,
        "recommendation": run.recommendation.value,
        "algorithm_version": run.algorithm_version,
        "config_fingerprint": run.config_fingerprint,
        "run_fingerprint": run.run_fingerprint,
        "feature_definitions": list(run.feature_definitions),
        "random_seed": run.random_seed,
        "input_observation_ids": [str(item) for item in run.input_observation_ids],
        "sources": serialize_sources(run.sources),
        "priorities": [
            {
                "rank": item.rank,
                "competency_id": str(item.competency_id),
                "skill_id": str(item.skill_id),
                "skill_title": item.skill_title,
                "practice_share": fraction_payload(item.practice_share),
                "features": {
                    "syllabus_share": fraction_payload(item.features.syllabus_share),
                    "question_frequency_share": fraction_payload(
                        item.features.question_frequency_share
                    ),
                    "marks_share": fraction_payload(item.features.marks_share),
                    "recency_gap_share": fraction_payload(item.features.recency_gap_share),
                    "evidence_question_count": item.features.evidence_question_count,
                    "evidence_marks": item.features.evidence_marks,
                    "last_observed_year": item.features.last_observed_year,
                },
                "evidence_language": item.evidence_language,
            }
            for item in run.priorities
        ],
    }


def _optional_uuid(value: UUID | None) -> str | None:
    return str(value) if value is not None else None
