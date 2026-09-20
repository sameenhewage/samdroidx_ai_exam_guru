import json
from pathlib import Path

import pytest

from exam_guru_api.openapi import main, write_openapi


def test_openapi_export_is_deterministic(tmp_path: Path) -> None:
    output_path = tmp_path / "client" / "openapi.json"

    write_openapi(output_path)
    first_export = output_path.read_bytes()
    write_openapi(output_path)

    assert output_path.read_bytes() == first_export
    assert first_export.endswith(b"\n")
    schema = json.loads(first_export)
    assert schema["info"]["title"] == "AI Exam Guru API"
    assert "/api/v1/health/live" in schema["paths"]
    session_operation = schema["paths"]["/api/v1/auth/session"]["get"]
    assert session_operation["operationId"] == "get_auth_session"
    assert set(session_operation["responses"]) >= {"200", "401", "503"}
    session_properties = schema["components"]["schemas"]["AuthSessionResponse"]["properties"]
    assert set(session_properties) == {"subject_id", "roles"}
    document_properties = schema["components"]["schemas"]["SourceDocumentResponse"]["properties"]
    assert {
        "ocr_page_count",
        "extraction_config",
        "extraction_queue_message_id",
    } <= document_properties.keys()
    page_properties = schema["components"]["schemas"]["SourcePageResponse"]["properties"]
    block_properties = schema["components"]["schemas"]["ExtractedBlockResponse"]["properties"]
    assert {"extraction_config", "confidence"} <= page_properties.keys()
    assert {"extraction_config", "confidence"} <= block_properties.keys()
    assert block_properties["bbox"]["anyOf"][1] == {"type": "null"}
    assert not [
        path
        for path in schema["paths"]
        if "/source-read-jobs" in path
        or "/understanding" in path
        or path.endswith("/extract")
        or "/review-workspace" in path
        or "/source-benchmarks" in path
    ]
    assert "/api/v1/admin/source-v2/pages/{page_id}" in schema["paths"]
    # A visual region publishes its three concepts separately: the text
    # printed inside the crop, the derived description, and the crop itself.
    region_properties = schema["components"]["schemas"]["RegionView"]["properties"]
    assert {
        "text",
        "visual_description",
        "detected_labels",
        "crop_url",
        "technical_evidence",
    } <= region_properties.keys()
    evidence_properties = schema["components"]["schemas"]["TechnicalEvidence"]["properties"]
    assert {
        "reason",
        "findings",
        "uncertainty",
        "abstained",
        "proposed_source_kind",
        "origin",
        "revision",
        "crop_sha256",
    } == evidence_properties.keys()
    crop_path = "/api/v1/admin/source-v2/pages/{page_id}/regions/{region_id}/crop"
    assert "image/png" in schema["paths"][crop_path]["get"]["responses"]["200"]["content"]
    describe_path = "/api/v1/admin/source-v2/pages/{page_id}/regions/{region_id}/describe"
    assert {"put", "post"} <= schema["paths"][describe_path].keys()
    describe_properties = schema["components"]["schemas"]["DescribeRequest"]["properties"]
    # Describing is not confirming, so the request cannot cite compared evidence.
    assert "compared_with_image_sha256" not in describe_properties


def test_openapi_export_cli_accepts_an_output_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["exam-guru-openapi", str(output_path)])

    main()

    assert output_path.is_file()
