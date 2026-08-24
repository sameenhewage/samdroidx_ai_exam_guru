from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from exam_guru_api.analytics.repository import AnalyticsRunRecord
from exam_guru_api.analytics.service import fingerprint_payload, serialize_analytics_results
from exam_guru_api.analytics.statistics import calculate_historical_statistics
from exam_guru_api.blueprints import adapt_rolling_backtest_priorities
from exam_guru_api.blueprints.analytics import (
    PersistedAnalyticsEvidenceError,
    adapt_persisted_analytics_priorities,
)
from tests.test_blueprint_analytics_integration import (
    CURRICULUM_ID,
    meaningful_result,
    repeated_distribution,
    syllabus_targets,
)

RUN_ID = UUID(int=81_001)
ACTOR_ID = UUID(int=81_002)
FINGERPRINT = "sha256:" + "a" * 64


def analytics_record() -> AnalyticsRunRecord:
    backtest = meaningful_result()
    result = serialize_analytics_results(
        calculate_historical_statistics(repeated_distribution(9, 1)),
        backtest,
    )
    return AnalyticsRunRecord(
        id=RUN_ID,
        curriculum_version_id=CURRICULUM_ID,
        run_fingerprint=FINGERPRINT,
        config_fingerprint=backtest.config_fingerprint,
        input_fingerprint=FINGERPRINT,
        source_fingerprint=FINGERPRINT,
        result_fingerprint=fingerprint_payload(result),
        statistics_algorithm_version="historical-distributions-v1",
        practice_priority_algorithm_version="deterministic-practice-priority-v1",
        baseline_algorithm_version="syllabus-balanced-baseline-v1",
        backtest_algorithm_version=backtest.backtest_version,
        config={},
        input_snapshot={},
        source_versions=[],
        data_quality={},
        result=result,
        compute_duration_ms=1,
        created_by=ACTOR_ID,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_persisted_p5_adapter_reconstructs_the_committed_result_and_adds_run_provenance() -> None:
    record = analytics_record()

    adapted = adapt_persisted_analytics_priorities(record, syllabus_targets())
    direct = adapt_rolling_backtest_priorities(meaningful_result(), syllabus_targets())

    assert tuple(adapted) == tuple(direct)
    for target, priority in adapted.items():
        expected = direct[target]
        assert (
            replace(
                priority,
                forecast_evidence_refs=tuple(
                    ref
                    for ref in priority.forecast_evidence_refs
                    if not ref.startswith("analytics:persisted-")
                ),
            )
            == expected
        )
        assert f"analytics:persisted-run:{RUN_ID}" in priority.forecast_evidence_refs
        assert (
            f"analytics:persisted-result:{record.result_fingerprint}"
            in priority.forecast_evidence_refs
        )


def test_persisted_p5_adapter_rejects_tampering_scope_and_version_mismatch() -> None:
    record = analytics_record()
    tampered_result = {**record.result, "unexpected": True}

    invalid_records = (
        replace(record, result=tampered_result),
        replace(record, curriculum_version_id=UUID(int=999)),
        replace(record, backtest_algorithm_version="other-backtest-v1"),
    )
    for invalid in invalid_records:
        with pytest.raises(PersistedAnalyticsEvidenceError):
            adapt_persisted_analytics_priorities(invalid, syllabus_targets())


def test_persisted_p5_adapter_rejects_invalid_shape_and_failed_leakage_audit() -> None:
    record = analytics_record()
    malformed_result: dict[str, object] = {"statistics": {}, "backtest": {}}
    malformed = replace(
        record,
        result=malformed_result,
        result_fingerprint=fingerprint_payload(malformed_result),
    )
    with pytest.raises(PersistedAnalyticsEvidenceError) as malformed_error:
        adapt_persisted_analytics_priorities(malformed, syllabus_targets())
    assert "shape" in malformed_error.value.detail

    failed_audit_result = deepcopy(record.result)
    backtest = failed_audit_result["backtest"]
    assert isinstance(backtest, dict)
    windows = backtest["windows"]
    assert isinstance(windows, list)
    first_window = windows[0]
    assert isinstance(first_window, dict)
    leakage_audit = first_window["leakage_audit"]
    assert isinstance(leakage_audit, dict)
    leakage_audit["passed"] = False
    failed_audit = replace(
        record,
        result=failed_audit_result,
        result_fingerprint=fingerprint_payload(failed_audit_result),
    )
    with pytest.raises(PersistedAnalyticsEvidenceError) as leakage_error:
        adapt_persisted_analytics_priorities(failed_audit, syllabus_targets())
    assert "leakage" in leakage_error.value.detail
