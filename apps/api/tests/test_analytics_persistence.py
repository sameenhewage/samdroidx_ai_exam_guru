from dataclasses import replace
from fractions import Fraction
from typing import cast
from uuid import UUID

from exam_guru_api.analytics.backtest import BacktestConfig, run_rolling_backtest
from exam_guru_api.analytics.domain import (
    Difficulty,
    HistoricalQuestionObservation,
    ObservationProvenance,
    SyllabusSkill,
)
from exam_guru_api.analytics.domain import (
    QuestionType as AnalyticsQuestionType,
)
from exam_guru_api.analytics.repository import AnalyticsQuestionCandidate
from exam_guru_api.analytics.service import (
    AnalyticsExclusionReason,
    build_loaded_analytics_inputs,
    serialize_analytics_results,
)
from exam_guru_api.analytics.statistics import calculate_historical_statistics
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.knowledge.domain import (
    DifficultyLabel,
    QuestionType,
    ReviewState,
)

CURRICULUM_ID = UUID(int=1)
COMPETENCY_ID = UUID(int=2)
SKILL_A = UUID(int=3)
SKILL_B = UUID(int=4)


def candidate(identifier: int = 10) -> AnalyticsQuestionCandidate:
    return AnalyticsQuestionCandidate(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        year=2020,
        paper_code="P1",
        question_number=str(identifier),
        competency_id=COMPETENCY_ID,
        skill_id=SKILL_A,
        question_type=QuestionType.MULTIPLE_CHOICE,
        difficulty_label=DifficultyLabel.MEDIUM,
        difficulty_confidence=0.9,
        difficulty_source="reviewer_confirmed",
        marks=2,
        source_document_id=UUID(int=100 + identifier),
        page_number=1,
        source_block_id=UUID(int=200 + identifier),
        review_state=ReviewState.REVIEWED,
        source_status=ExtractionStatus.TRUSTED,
        source_checksum_sha256=f"{identifier:064x}",
    )


def test_loader_includes_only_complete_reviewed_trusted_evidence_without_invention() -> None:
    syllabus = (SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_A, "Numbers"),)
    candidates = (
        candidate(10),
        replace(candidate(11), review_state=ReviewState.IN_REVIEW),
        replace(candidate(12), competency_id=None),
        replace(candidate(13), skill_id=None),
        replace(
            candidate(14),
            difficulty_label=None,
            difficulty_confidence=None,
            difficulty_source=None,
        ),
        replace(candidate(15), difficulty_confidence=float("nan")),
        replace(candidate(16), source_block_id=None),
        replace(candidate(17), source_status=ExtractionStatus.IN_REVIEW),
        replace(candidate(18), source_checksum_sha256=None),
        replace(candidate(19), skill_id=UUID(int=999)),
        replace(candidate(20), competency_id=UUID(int=998)),
        replace(candidate(21), source_checksum_sha256="A" * 64),
        replace(candidate(22), source_status=None),
    )

    loaded = build_loaded_analytics_inputs(candidates, syllabus)

    assert len(loaded.observations) == 1
    observation = loaded.observations[0]
    assert observation.id == UUID(int=10)
    assert observation.difficulty is Difficulty.MEDIUM
    assert observation.provenance.source_block_id == UUID(int=210)
    assert observation.provenance.source_version == f"sha256:{10:064x}"
    assert loaded.data_quality.considered_count == 13
    assert loaded.data_quality.included_count == 1
    assert loaded.data_quality.excluded_count == 12
    counts = {item.reason: item.count for item in loaded.data_quality.exclusions}
    assert counts == {
        AnalyticsExclusionReason.COMPETENCY_MISMATCH: 1,
        AnalyticsExclusionReason.INCOMPLETE_DIFFICULTY_EVIDENCE: 1,
        AnalyticsExclusionReason.INVALID_SOURCE_CHECKSUM: 1,
        AnalyticsExclusionReason.MISSING_COMPETENCY_ID: 1,
        AnalyticsExclusionReason.MISSING_SKILL_ID: 1,
        AnalyticsExclusionReason.MISSING_SOURCE_BLOCK_ID: 1,
        AnalyticsExclusionReason.MISSING_SOURCE_CHECKSUM: 1,
        AnalyticsExclusionReason.NON_FINITE_DIFFICULTY_CONFIDENCE: 1,
        AnalyticsExclusionReason.NOT_REVIEWED: 1,
        AnalyticsExclusionReason.SKILL_NOT_IN_REVIEWED_SYLLABUS: 1,
        AnalyticsExclusionReason.SOURCE_NOT_TRUSTED: 2,
    }
    assert loaded.selection_fingerprint.startswith("sha256:")
    assert (
        build_loaded_analytics_inputs(tuple(reversed(candidates)), tuple(reversed(syllabus)))
        == loaded
    )


def observation(
    identifier: int, year: int, skill_id: UUID, marks: int
) -> HistoricalQuestionObservation:
    return HistoricalQuestionObservation(
        id=UUID(int=identifier),
        curriculum_version_id=CURRICULUM_ID,
        year=year,
        paper_code=f"P{year}",
        question_number=str(identifier),
        competency_id=COMPETENCY_ID,
        skill_id=skill_id,
        question_type=AnalyticsQuestionType.MULTIPLE_CHOICE,
        difficulty=Difficulty.MEDIUM,
        marks=marks,
        provenance=ObservationProvenance(
            source_document_id=UUID(int=10_000 + year),
            source_version=f"sha256:{year:064x}",
            page_number=1,
            source_block_id=UUID(int=20_000 + identifier),
        ),
    )


def test_persisted_result_serialization_keeps_exact_fractions_and_visible_baseline_audits() -> None:
    syllabus = (
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_A, "Numbers"),
        SyllabusSkill(CURRICULUM_ID, COMPETENCY_ID, SKILL_B, "Operations"),
    )
    observations = tuple(
        item
        for offset, year in enumerate(range(2018, 2022))
        for item in (
            observation(100 + offset * 2, year, SKILL_A, 9),
            observation(101 + offset * 2, year, SKILL_B, 1),
        )
    )
    statistics = calculate_historical_statistics(observations)
    backtest = run_rolling_backtest(
        observations,
        syllabus,
        config=BacktestConfig(top_k_skills=1),
    )

    payload = serialize_analytics_results(statistics, backtest)

    backtest_payload = cast(dict[str, object], payload["backtest"])
    aggregate = cast(dict[str, object], backtest_payload["aggregate"])
    limitations = cast(list[str], backtest_payload["limitations"])
    assert any("equal weight" in limitation for limitation in limitations)
    assert aggregate["baseline_delta"] == {"numerator": 2, "denominator": 75}
    assert aggregate["mean_baseline_score"] == {"numerator": 13, "denominator": 15}
    windows = cast(list[dict[str, object]], backtest_payload["windows"])
    first_window = windows[0]
    leakage_audit = cast(dict[str, object], first_window["leakage_audit"])
    assert leakage_audit["passed"] is True
    assert leakage_audit["overlapping_observation_ids"] == []
    baseline_run = cast(dict[str, object], first_window["baseline_run"])
    assert baseline_run["algorithm_version"] == "syllabus-balanced-baseline-v1"
    method_metrics = cast(dict[str, object], first_window["method_metrics"])
    assert method_metrics["composite_score"] == {
        "numerator": 67,
        "denominator": 75,
    }
    statistics_payload = cast(dict[str, object], payload["statistics"])
    skill_distribution = cast(list[dict[str, object]], statistics_payload["skill_distribution"])
    assert skill_distribution[0]["marks_share"] == {
        "numerator": 9,
        "denominator": 10,
    }
    assert backtest.aggregate.baseline_delta == Fraction(2, 75)

    fallback_observations = tuple(replace(item, marks=1) for item in observations)
    fallback = run_rolling_backtest(fallback_observations, syllabus)
    fallback_payload = serialize_analytics_results(
        calculate_historical_statistics(fallback_observations),
        fallback,
    )
    fallback_backtest = cast(dict[str, object], fallback_payload["backtest"])
    recommendation = cast(dict[str, object], fallback_backtest["recommendation"])
    recommended_run = cast(dict[str, object], fallback_backtest["recommended_run"])
    assert recommendation["mode"] == "syllabus_balanced_practice"
    assert recommended_run["method"] == "syllabus_balanced"
