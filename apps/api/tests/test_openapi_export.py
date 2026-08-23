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


def test_openapi_export_cli_accepts_an_output_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["exam-guru-openapi", str(output_path)])

    main()

    assert output_path.is_file()
