import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "import_studio_corpus.py"
spec = importlib.util.spec_from_file_location("studio_corpus_import", MODULE_PATH)
assert spec is not None
assert spec.loader is not None
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def record(path: str = "Grade 3/source.pdf", grade: int = 3) -> dict[str, object]:
    return {
        "relative_path": path,
        "storage_grade": grade,
        "sha256": hashlib.sha256(b"%PDF-test").hexdigest(),
        "size_bytes": 9,
        "candidate_metadata": {
            "grade": {"candidate_value": grade, "candidates": [{"value": grade}]},
            "subject": {"candidate_value": "maths", "candidates": [{"value": "maths"}]},
            "medium": {"candidate_value": "sinhala", "candidates": []},
            "type": {"candidate_value": "worksheet", "candidates": []},
            "year": {"candidate_value": None, "candidates": []},
            "authority": {"candidate_value": None, "candidates": []},
        },
        "manifest_bindings": [],
        "legacy_font_risk": False,
        "parser_warnings": [],
    }


def test_groups_hash_duplicates_without_losing_cross_grade_paths() -> None:
    groups = importer.group_sources({"pdfs": [record(), record("Grade 4/copy.pdf", 4)]})
    assert len(groups) == 1
    assert [item["storage_grade"] for item in groups[0]] == [3, 4]
    metadata = importer.build_intake_metadata(groups[0])
    assert metadata["candidate_grade"] is None
    assert any("grade" in warning.lower() for warning in metadata["warnings"])
    assert "curriculum_version_id" not in metadata
    assert "trusted" not in json.dumps(metadata)


def test_metadata_preserves_unresolved_values_and_font_warnings() -> None:
    source = record()
    source["legacy_font_risk"] = True
    metadata = importer.build_intake_metadata([source])
    assert metadata["candidate_grade"] == 3
    assert metadata["subject_label"] == "Maths"
    assert metadata["year"] is None
    assert metadata["curriculum_label"] is None
    assert metadata["document_type_label"] == "Worksheet"
    assert any("font" in warning.lower() for warning in metadata["warnings"])
    assert importer.document_type(metadata) == "other_approved"


def test_source_resolution_preserves_bytes_and_rejects_tampering(tmp_path: Path) -> None:
    folder = tmp_path / "Grade 3"
    folder.mkdir()
    source = folder / "source.pdf"
    source.write_bytes(b"%PDF-test")
    assert importer.resolve_source(tmp_path, record()) == source
    assert source.read_bytes() == b"%PDF-test"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        importer.resolve_source(tmp_path, record())
    with pytest.raises(ValueError, match="outside"):
        importer.resolve_source(tmp_path, record("../outside.pdf"))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://evil:8000",
        "http://localhost:8000/path",
        "http://user@localhost:8000",
    ],
)
def test_import_client_refuses_remote_or_credential_urls(url: str) -> None:
    with pytest.raises(ValueError, match="local Studio"):
        importer.LocalStudioClient(url, "test-token")


def test_resumed_import_preserves_new_upload_count_without_reupload(tmp_path: Path) -> None:
    folder = tmp_path / "Grade 3"
    folder.mkdir()
    (folder / "source.pdf").write_bytes(b"%PDF-test")
    manifest = {"pdfs": [record()]}
    ledger = tmp_path / "ledger.json"
    document = {
        "id": "00000000-0000-0000-0000-000000000001",
        "checksum_sha256": record()["sha256"],
        "extraction_status": "uploaded",
        "metadata_review_required": True,
    }

    class Client:
        def __init__(self) -> None:
            self.existing: list[dict[str, object]] = []
            self.uploads = 0

        def request(self, method: str, path: str, **kwargs: object) -> tuple[int, object]:
            del kwargs
            if method == "GET":
                return 200, self.existing
            if path == "/source-documents":
                self.uploads += 1
                self.existing = [document]
                return 201, document
            return 202, {"status": "extraction_pending"}

    client = Client()
    importer.run_import(tmp_path, manifest, client, ledger)
    document["extraction_status"] = "extracted"
    resumed = importer.run_import(tmp_path, manifest, client, ledger)
    assert client.uploads == 1
    assert resumed["preexisting_documents"] == 0
    assert resumed["entries"][0]["new_upload"] is True


def test_only_supported_ingestion_actions_are_exposed() -> None:
    client = importer.LocalStudioClient("http://api:8000", "test-token")
    with pytest.raises(ValueError, match="ingestion"):
        client.request("POST", "/source-documents/anything/trust")
    with pytest.raises(ValueError, match="ingestion"):
        client.request("POST", "/embedding-jobs")
