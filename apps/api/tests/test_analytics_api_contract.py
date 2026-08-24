import pytest
from pydantic import ValidationError

from exam_guru_api.analytics.schemas import AnalyticsRunRequest, ExactFraction
from exam_guru_api.main import create_app

RUNS_PATH = "/api/v1/admin/curricula/{curriculum_version_id}/analytics/runs"
RUN_PATH = RUNS_PATH + "/{run_id}"


def test_analytics_openapi_exposes_authorized_bounded_run_resources() -> None:
    schema = create_app().openapi()

    assert {"get", "post"} <= set(schema["paths"][RUNS_PATH])
    assert "get" in schema["paths"][RUN_PATH]
    for path, method in ((RUNS_PATH, "post"), (RUNS_PATH, "get"), (RUN_PATH, "get")):
        assert schema["paths"][path][method]["security"] == [{"HTTPBearer": []}]

    parameters = {item["name"]: item for item in schema["paths"][RUNS_PATH]["get"]["parameters"]}
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["offset"]["schema"]["minimum"] == 0
    assert parameters["offset"]["schema"]["maximum"] == 100_000


def test_analytics_request_and_response_contracts_are_exact_and_do_not_claim_prediction() -> None:
    schema = create_app().openapi()
    request = schema["components"]["schemas"]["AnalyticsRunRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {
        "minimum_training_years",
        "top_k_skills",
        "meaningful_improvement",
    }
    fraction = schema["components"]["schemas"]["ExactFraction"]
    assert set(fraction["properties"]) == {"numerator", "denominator"}
    assert fraction["properties"]["denominator"]["minimum"] == 1

    operation = schema["paths"][RUNS_PATH]["post"]
    assert operation["operationId"] == "create_analytics_run"
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AnalyticsRunResponse"
    }
    assert "prediction" not in operation["summary"].casefold()


def test_analytics_request_rejects_non_positive_or_above_one_exact_thresholds() -> None:
    for value in (
        ExactFraction(numerator=0, denominator=1),
        ExactFraction(numerator=2, denominator=1),
    ):
        with pytest.raises(ValidationError):
            AnalyticsRunRequest(meaningful_improvement=value)
