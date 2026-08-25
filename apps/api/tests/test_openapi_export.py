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
    page_properties = schema["components"]["schemas"]["SourcePageResponse"]["properties"]
    block_properties = schema["components"]["schemas"]["ExtractedBlockResponse"]["properties"]
    assert {
        "ocr_page_count",
        "extraction_config",
        "extraction_queue_message_id",
    } <= document_properties.keys()
    assert {"extraction_config", "confidence"} <= page_properties.keys()
    assert {"extraction_config", "confidence"} <= block_properties.keys()
    assert block_properties["bbox"]["anyOf"][1] == {"type": "null"}


def test_openapi_export_cli_accepts_an_output_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["exam-guru-openapi", str(output_path)])

    main()

    assert output_path.is_file()
