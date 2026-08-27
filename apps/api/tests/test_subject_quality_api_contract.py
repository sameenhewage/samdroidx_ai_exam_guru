from exam_guru_api.main import create_app

BASE = "/api/v1/admin/subject-quality"
FEEDBACK = f"{BASE}/feedback"
PROMOTE = f"{FEEDBACK}/{{feedback_id}}/promote"
EVAL_CASES = f"{BASE}/eval-cases"
APPROVE = f"{EVAL_CASES}/{{eval_case_id}}/approve"
EXPORT = f"{EVAL_CASES}/export"
RUNS = f"{BASE}/eval-runs"
RUN = f"{RUNS}/{{run_id}}"


def test_private_subject_quality_api_exposes_bounded_review_promotion_and_replay_contracts() -> (
    None
):
    schema = create_app().openapi()
    expected = {
        FEEDBACK: ("get",),
        PROMOTE: ("post",),
        EVAL_CASES: ("get",),
        APPROVE: ("post",),
        EXPORT: ("get",),
        RUNS: ("post",),
        RUN: ("get",),
    }
    for path, methods in expected.items():
        assert path in schema["paths"]
        for method in methods:
            operation = schema["paths"][path][method]
            assert operation["security"] == [{"HTTPBearer": []}]
            assert "student" not in " ".join(operation.get("tags", ())).lower()

    promotion = schema["paths"][PROMOTE]["post"]
    assert {parameter["name"] for parameter in promotion["parameters"]} == {
        "feedback_id",
        "Idempotency-Key",
    }
    assert promotion["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SubjectQualityEvalCaseResponse"
    }

    export = schema["paths"][EXPORT]["get"]
    parameters = {parameter["name"]: parameter["schema"] for parameter in export["parameters"]}
    assert parameters["limit"]["maximum"] == 100
    assert parameters["offset"]["maximum"] == 100_000
    assert export["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SubjectQualityEvalExportResponse"
    }

    run_request = schema["components"]["schemas"]["SubjectQualityEvalRunRequest"]
    assert run_request["additionalProperties"] is False
    promotion_request = schema["components"]["schemas"]["SubjectQualityPromotionRequest"]
    assert promotion_request["additionalProperties"] is False
    assert set(promotion_request["properties"]) == {
        "expected_status",
        "expected_finding_codes",
        "defect_category",
    }
