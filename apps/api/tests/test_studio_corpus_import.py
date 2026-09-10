import hashlib
import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, BinaryIO
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from exam_guru_api.documents.upload_schemas import UPLOAD_CHUNK_BYTES
from tests.test_resumable_uploads import ADMIN, AUTH, UPLOAD_ID, upload_view

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


class FakeUploads:
    def __init__(self, ledger: Path) -> None:
        self.origin = "http://api:8000"
        self.ledger = ledger
        self.owner = str(ADMIN.subject_id)
        self.runtime = {"application_env": "test", "test_runtime_id": "ai-exam-guru-e2e-import"}
        self.session: dict[str, Any] | None = None
        self.receipts: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.sent_sizes: list[int] = []
        self.creates = 0
        self.complete_calls = 0
        self.lost_response: str | None = None
        self.interrupt_after_chunk = False
        self.bad_receipt = False
        self.bad_response: dict[str, Any] = {}
        self.pending_forever = False
        self.deduplicated = False
        self.receipt_page_size = 64

    def identity(self) -> dict[str, Any]:
        return {"origin": self.origin, "owner_id": self.owner, "runtime": self.runtime}

    def request(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        self.calls.append((method, path))
        parsed = urlsplit(path)
        route = parsed.path
        if method == "POST" and route == "/source-uploads":
            body = kwargs["fields"]
            saved = json.loads(self.ledger.read_text())
            assert saved["entries"][0]["request_id"] == body["request_id"]
            assert saved["entries"][0]["create_attempted"] is True
            self.creates += 1
            self.session = upload_view().model_dump(mode="json")
            self.session.update(body, id=str(UPLOAD_ID))
            self.session.update(status="uploading", next_offset=0, version=0)
            self.session.update(self.bad_response)
            if self.lost_response == "create":
                self.lost_response = None
                raise importer.UploadError("upload_interrupted")
            return 201, deepcopy(self.session)
        if self.session is None:
            raise importer.UploadError("source_upload_not_found", status=404)
        if method == "GET" and route.endswith("/chunks"):
            offset = int(parse_qs(parsed.query)["offset"][0])
            selected = [r for r in self.receipts if r["offset"] >= offset]
            page = deepcopy(selected[: self.receipt_page_size])
            if self.bad_receipt and page:
                page[0]["checksum_sha256"] = hashlib.sha256(b"different").hexdigest()
            return 200, {
                "upload_id": str(UPLOAD_ID),
                "next_offset": self.session["next_offset"],
                "receipts": page,
                "next_receipt_offset": (
                    page[-1]["offset"] + page[-1]["size_bytes"]
                    if len(selected) > len(page)
                    else None
                ),
            }
        if method == "PUT" and route.endswith("/chunks"):
            data = kwargs["data"]
            assert isinstance(data, bytes)
            assert 0 < len(data) <= UPLOAD_CHUNK_BYTES
            offset = int(parse_qs(parsed.query)["offset"][0])
            assert offset == self.session["next_offset"]
            checksum = hashlib.sha256(data).hexdigest()
            assert kwargs["headers"]["X-Chunk-SHA256"] == checksum
            self.receipts.append(
                {"offset": offset, "size_bytes": len(data), "checksum_sha256": checksum}
            )
            self.sent_sizes.append(len(data))
            self.session["next_offset"] += len(data)
            self.session["version"] += 1
            if self.interrupt_after_chunk:
                self.interrupt_after_chunk = False
                raise KeyboardInterrupt
            if self.lost_response == "chunk":
                self.lost_response = None
                raise importer.UploadError("upload_interrupted")
            return 200, deepcopy(self.session)
        if method == "POST" and route.endswith("/complete"):
            assert kwargs["fields"] == {"expected_version": self.session["version"]}
            assert self.session["next_offset"] == self.session["size_bytes"]
            self.complete_calls += 1
            self.session["status"] = "pending"
            self.session["version"] += 1
            if self.lost_response == "complete":
                self.lost_response = None
                raise importer.UploadError("upload_interrupted")
            return 202, deepcopy(self.session)
        if method == "GET":
            if self.session["status"] == "pending" and not self.pending_forever:
                self.session.update(
                    status="completed",
                    verified_bytes=self.session["size_bytes"],
                    checksum_sha256=self.session["expected_checksum_sha256"],
                    document_id=str(UUID(int=36005)),
                    source_read_job_id=None if self.deduplicated else str(UUID(int=36006)),
                    deduplicated=self.deduplicated,
                )
            return 200, deepcopy(self.session)
        raise AssertionError((method, path))


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, dict[str, Any], Path]:
    root = tmp_path / "corpus"
    folder = root / "Grade 3"
    folder.mkdir(parents=True)
    (folder / "source.pdf").write_bytes(b"%PDF-test")
    return root, {"pdfs": [record()]}, tmp_path / "ledger.json"


@pytest.fixture(autouse=True)
def no_upload_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importer.time, "sleep", lambda _: None)


def test_resumed_import_preserves_new_upload_count_without_reupload(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    initial = importer.run_import(root, manifest, client, ledger)
    resumed = importer.run_import(root, manifest, client, ledger)
    assert client.creates == 1
    assert client.sent_sizes == [9]
    assert client.complete_calls == 1
    assert initial["entries"][0]["request_id"] == resumed["entries"][0]["request_id"]
    assert resumed["entries"][0]["new_upload"] is True
    assert resumed["entries"][0]["source_read_job_id"] == str(UUID(int=36006))
    assert all("source-documents" not in path for _, path in client.calls)


def test_only_supported_ingestion_actions_are_exposed() -> None:
    client = importer.LocalStudioClient("http://api:8000", "test-token")
    with pytest.raises(ValueError, match="ingestion"):
        client.request("POST", "/source-documents/anything/trust")
    with pytest.raises(ValueError, match="ingestion"):
        client.request("POST", "/embedding-jobs")


@pytest.mark.parametrize("phase", ["create", "chunk", "complete"])
def test_lost_responses_reconcile_without_reuploading(
    corpus: tuple[Path, dict[str, Any], Path],
    phase: str,
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    client.lost_response = phase
    result = importer.run_import(root, manifest, client, ledger)
    assert result["entries"][0]["status"] == "completed"
    assert client.creates == client.complete_calls == 1
    assert client.sent_sizes == [9]
    assert any("/by-request/" in path for _, path in client.calls) or phase != "create"
    assert any("/chunks?" in path and method == "GET" for method, path in client.calls)


def test_interrupt_preserves_identity_and_checks_receipts_before_resume(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    client.interrupt_after_chunk = True
    with pytest.raises(KeyboardInterrupt):
        importer.run_import(root, manifest, client, ledger)
    saved = json.loads(ledger.read_text())
    assert saved["entries"][0]["status"] == "paused"
    client.bad_receipt = True
    with pytest.raises(ValueError, match=r"(receipt|mismatch)"):
        importer.run_import(root, manifest, client, ledger)
    assert client.complete_calls == 0
    assert client.sent_sizes == [9]
    client.bad_receipt = False
    result = importer.run_import(root, manifest, client, ledger)
    assert result["entries"][0]["request_id"] == saved["entries"][0]["request_id"]
    assert result["entries"][0]["new_upload"] is True
    assert any(e["event"] == "paused" for e in result["entries"][0]["events"])


@pytest.mark.parametrize("change", ["origin", "owner", "runtime", "missing", "source", "metadata"])
def test_resume_refuses_different_binding_or_missing_saved_upload(
    corpus: tuple[Path, dict[str, Any], Path],
    change: str,
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    importer.run_import(root, manifest, client, ledger)
    if change == "origin":
        client.origin = "http://localhost:8001"
    elif change == "owner":
        client.owner = str(UUID(int=36099))
    elif change == "runtime":
        client.runtime = {"application_env": "local"}
    elif change == "missing":
        client.session = None
    elif change == "source":
        (root / "Grade 3/source.pdf").write_bytes(b"%PDF-fail")
    else:
        manifest["pdfs"][0]["candidate_metadata"]["grade"]["candidate_value"] = 4
    before = (client.creates, len(client.sent_sizes), client.complete_calls)
    with pytest.raises((ValueError, RuntimeError)):
        importer.run_import(root, manifest, client, ledger)
    assert (client.creates, len(client.sent_sizes), client.complete_calls) == before


def test_legacy_migration_is_explicit_preserves_history_and_does_not_trust_old_ids(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    old: dict[str, Any] = {
        "schema": "studio-corpus-import.v1",
        "preexisting_documents": 7,
        "entries": [
            {
                "sha256": record()["sha256"],
                "document_id": str(UUID(int=1)),
                "new_upload": True,
                "status": "extracted",
            }
        ],
    }
    ledger.write_text(json.dumps(old))
    client = FakeUploads(ledger)
    with pytest.raises(ValueError, match="legacy"):
        importer.run_import(root, manifest, client, ledger)
    assert json.loads(ledger.read_text()) == old
    client.deduplicated = True
    result = importer.run_import(root, manifest, client, ledger, migrate_legacy=True)
    assert result["legacy_history"] == [old]
    assert result["entries"][0]["document_id"] != old["entries"][0]["document_id"]
    assert result["entries"][0]["new_upload"] is False
    assert result["entries"][0]["source_read_job_id"] is None


@pytest.mark.parametrize("source_size", [169_816_530, 191_788_974])
def test_large_source_reads_are_bounded_and_receipts_paginate(
    corpus: tuple[Path, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
    source_size: int,
) -> None:
    root, manifest, ledger = corpus
    path = root / "Grade 3/source.pdf"
    with path.open("wb") as writer:
        writer.write(b"%PDF-")
        writer.truncate(source_size)
    with path.open("rb") as handle:
        manifest["pdfs"][0]["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    manifest["pdfs"][0]["size_bytes"] = path.stat().st_size
    client = FakeUploads(ledger)
    observed: list[int] = []
    original = importer.open_source

    class BoundedReader:
        def __init__(self, wrapped: BinaryIO) -> None:
            self.wrapped = wrapped

        def read(self, size: int = -1) -> bytes:
            assert 0 <= size <= UPLOAD_CHUNK_BYTES
            observed.append(size)
            return self.wrapped.read(size)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

    @contextmanager
    def guarded(*args: Any, **kwargs: Any) -> Iterator[BoundedReader]:
        with original(*args, **kwargs) as handle:
            yield BoundedReader(handle)

    verifier = importer.verify_source

    def bounded_verifier(handle: BinaryIO, value: dict[str, Any]) -> Any:
        return verifier(BoundedReader(handle), value)

    monkeypatch.setattr(importer, "verify_source", bounded_verifier)
    monkeypatch.setattr(importer, "open_source", guarded)
    importer.run_import(root, manifest, client, ledger)
    client.receipt_page_size = 2
    resumed = importer.run_import(root, manifest, client, ledger)
    assert max(observed) == UPLOAD_CHUNK_BYTES
    assert max(client.sent_sizes) == UPLOAD_CHUNK_BYTES
    assert sum(client.sent_sizes) == source_size
    assert client.sent_sizes[-1] == source_size % UPLOAD_CHUNK_BYTES
    assert resumed["entries"][0]["status"] == "completed"
    assert sum(method == "GET" and "/chunks?" in p for method, p in client.calls) > 2


@pytest.mark.parametrize(
    "bad",
    [
        {"size_bytes": 10},
        {"next_offset": True},
        {"next_offset": 1},
        {"chunk_size_bytes": 1024},
        {"status": "trusted"},
        {"request_id": str(UUID(int=9))},
        {"expected_checksum_sha256": "invalid"},
    ],
)
def test_invalid_upload_response_never_sends_a_chunk(
    corpus: tuple[Path, dict[str, Any], Path],
    bad: dict[str, Any],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    client.bad_response = bad
    with pytest.raises((ValueError, RuntimeError)):
        importer.run_import(root, manifest, client, ledger)
    assert client.sent_sizes == []
    assert client.complete_calls == 0


def test_poll_budget_pauses_without_cancel_or_recreation(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    client.pending_forever = True
    with pytest.raises(RuntimeError, match="finishing"):
        importer.run_import(root, manifest, client, ledger, max_polls=2)
    assert json.loads(ledger.read_text())["entries"][0]["status"] == "paused"
    client.pending_forever = False
    importer.run_import(root, manifest, client, ledger)
    assert client.creates == client.complete_calls == 1
    assert all(method != "DELETE" for method, _ in client.calls)


def test_duplicate_aliases_checked_and_cross_grade_metadata_remains_unverified(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    (root / "Grade 4").mkdir()
    (root / "Grade 4/copy.pdf").write_bytes(b"%PDF-test")
    manifest["pdfs"].append(record("Grade 4/copy.pdf", 4))
    client = FakeUploads(ledger)
    importer.run_import(root, manifest, client, ledger)
    assert client.session is not None
    assert client.session["intake_metadata"]["candidate_grade"] is None
    assert client.creates == 1
    assert len(json.loads(ledger.read_text())["entries"][0]["paths"]) == 2
    (root / "Grade 4/copy.pdf").write_bytes(b"%PDF-fail")
    with pytest.raises(ValueError, match="integrity"):
        importer.run_import(root, manifest, client, ledger)


def test_directory_symlink_and_ledger_inside_corpus_are_rejected(
    corpus: tuple[Path, dict[str, Any], Path],
    tmp_path: Path,
) -> None:
    root, manifest, _ = corpus
    (tmp_path / "alias").symlink_to(root / "Grade 3", target_is_directory=True)
    with pytest.raises(ValueError, match=r"(symlink|outside)"):
        importer.resolve_source(tmp_path, record("alias/source.pdf"))
    ledger = root / "ledger.json"
    client = FakeUploads(ledger)
    with pytest.raises(ValueError, match="ledger"):
        importer.run_import(root, manifest, client, ledger)
    assert not ledger.exists()
    assert client.calls == []


class FakeHTTPResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.read_sizes: list[int] = []

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.body[:size]


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"x":1,"x":2}', b'{"x":NaN}', b"x" * (1024 * 1024 + 1)],
    ids=["invalid-json", "not-object", "duplicate-key", "non-finite", "oversize"],
)
def test_http_response_is_bounded_and_rejects_malformed_payloads(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    response = FakeHTTPResponse(200, body)
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(importer.http.client, "HTTPConnection", lambda *a, **k: connection)
    client = importer.LocalStudioClient("http://api:8000", AUTH["Authorization"].split()[1])
    with pytest.raises(RuntimeError, match="response"):
        client.request("GET", f"/source-uploads/{UPLOAD_ID}")
    assert response.read_sizes == [1024 * 1024 + 1]
    connection.close.assert_called_once()


def test_get_retries_bounded_backpressure_without_leaking_error_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(importer.time, "sleep", waits.append)
    connection = Mock()
    connection.getresponse.side_effect = [
        FakeHTTPResponse(429, b'{"detail":{"code":"rate_limit_exceeded"}}', {"Retry-After": "2"}),
        FakeHTTPResponse(200, b'{"ok":true}'),
    ]
    monkeypatch.setattr(importer.http.client, "HTTPConnection", lambda *a, **k: connection)
    client = importer.LocalStudioClient("http://api:8000", AUTH["Authorization"].split()[1])
    assert client.request("GET", f"/source-uploads/{UPLOAD_ID}") == (200, {"ok": True})
    assert waits == [2]
    connection.getresponse.side_effect = None
    connection.getresponse.return_value = FakeHTTPResponse(
        503, b'{"detail":{"code":"PRIVATE details"}}'
    )
    with pytest.raises(RuntimeError) as caught:
        client.request("GET", f"/source-uploads/{UPLOAD_ID}")
    assert "PRIVATE" not in str(caught.value)
    assert len(waits) <= 4


def test_dry_run_never_creates_client_or_ledger(
    corpus: tuple[Path, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, manifest, ledger = corpus
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(
        importer.sys,
        "argv",
        [
            "import_studio_corpus",
            "--root",
            str(root),
            "--manifest",
            str(manifest_path),
            "--ledger",
            str(ledger),
        ],
    )
    monkeypatch.setattr(
        importer, "LocalStudioClient", lambda *a, **k: pytest.fail("network client created")
    )
    importer.main()
    assert not ledger.exists()


@pytest.mark.parametrize("corruption", ["pagination", "gap", "boolean", "empty", "wrong-upload"])
def test_receipt_corruption_cannot_complete_or_append(
    corpus: tuple[Path, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    client.interrupt_after_chunk = True
    with pytest.raises(KeyboardInterrupt):
        importer.run_import(root, manifest, client, ledger)
    original = client.request

    def damaged(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        status, payload = original(method, path, **kwargs)
        if method == "GET" and "/chunks?" in path:
            if corruption == "pagination":
                payload["next_receipt_offset"] = 0
            elif corruption == "gap":
                payload["receipts"][0]["offset"] = 1
            elif corruption == "boolean":
                payload["receipts"][0]["offset"] = False
            elif corruption == "empty":
                payload["receipts"] = []
            else:
                payload["upload_id"] = str(UUID(int=99))
        return status, payload

    monkeypatch.setattr(client, "request", damaged)
    with pytest.raises(ValueError, match="receipt"):
        importer.run_import(root, manifest, client, ledger)
    assert client.sent_sizes == [9]
    assert client.complete_calls == 0


@pytest.mark.parametrize("corruption", ["checksum", "document", "failed"])
def test_invalid_terminal_state_is_not_success(
    corpus: tuple[Path, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    original = client.request

    def damaged(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        status, payload = original(method, path, **kwargs)
        if isinstance(payload, dict) and payload.get("status") == "completed":
            if corruption == "checksum":
                payload["checksum_sha256"] = hashlib.sha256(b"mismatched").hexdigest()
            elif corruption == "document":
                payload["document_id"] = None
            else:
                payload["status"] = "failed"
        return status, payload

    monkeypatch.setattr(client, "request", damaged)
    with pytest.raises(RuntimeError):
        importer.run_import(root, manifest, client, ledger)
    assert json.loads(ledger.read_text())["entries"][0]["status"] == "paused"


def test_root_swap_to_symlink_after_path_validation_never_opens_outside(
    corpus: tuple[Path, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, manifest, _ = corpus
    outside = tmp_path / "outside"
    (outside / "Grade 3").mkdir(parents=True)
    (outside / "Grade 3/source.pdf").write_bytes(b"%PDF-test")
    original = importer.source_path

    def swap(*args: Any) -> Path:
        path = original(*args)
        root.rename(tmp_path / "original")
        root.symlink_to(outside, target_is_directory=True)
        return Path(path)

    monkeypatch.setattr(importer, "source_path", swap)
    with pytest.raises((OSError, ValueError)), importer.open_source(root, manifest["pdfs"][0]):
        pytest.fail("opened source outside root after symlink swap")


def test_corrupt_ledger_request_identity_is_rejected_before_create(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    importer.run_import(root, manifest, client, ledger)
    saved = json.loads(ledger.read_text())
    entry = saved["entries"][0]
    entry.update(request_id=None, upload_id=None, create_attempted=False)
    entry["request"]["request_id"] = None
    ledger.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="identity"):
        importer.run_import(root, manifest, client, ledger)
    assert client.creates == 1


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (201, {}),
        (200, {"Content-Length": "100"}),
        (429, {"Retry-After": "not-valid"}),
        (429, {"Retry-After": "999999"}),
    ],
)
def test_http_protocol_anomalies_pause_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
) -> None:
    response = FakeHTTPResponse(status, b'{"ok":true}', headers)
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(importer.http.client, "HTTPConnection", lambda *a, **k: connection)
    client = importer.LocalStudioClient("http://api:8000", AUTH["Authorization"].split()[1])
    with pytest.raises(RuntimeError):
        client.request("GET", f"/source-uploads/{UPLOAD_ID}")
    assert connection.getresponse.call_count == 1


def test_concurrent_ledger_use_cannot_create_second_request(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    with importer.ledger_lock(root, ledger), pytest.raises(ValueError, match="already in use"):
        importer.run_import(root, manifest, client, ledger)
    assert client.creates == 0


def test_source_hashing_stops_at_declared_size_on_growth(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []

    class GrowingSource:
        def seek(self, offset: int) -> None:
            assert offset == 0

        def read(self, size: int) -> bytes:
            reads.append(size)
            assert sum(reads) <= 10
            return b"%PDF-test"[:size]

    monkeypatch.setattr(importer, "source_stat", lambda _: (1, 1, 9, 0, 0))
    with pytest.raises(ValueError, match="integrity"):
        importer.verify_source(GrowingSource(), record())


def test_terminal_deduplication_history_cannot_silently_change(
    corpus: tuple[Path, dict[str, Any], Path],
) -> None:
    root, manifest, ledger = corpus
    client = FakeUploads(ledger)
    importer.run_import(root, manifest, client, ledger)
    assert client.session is not None
    client.session["deduplicated"] = True
    with pytest.raises(RuntimeError, match="identity"):
        importer.run_import(root, manifest, client, ledger)
    assert json.loads(ledger.read_text())["entries"][0]["new_upload"] is True


def test_real_client_contract_with_fake_http_and_lost_chunk_response(
    corpus: tuple[Path, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, manifest, ledger = corpus
    server = FakeUploads(ledger)
    server.lost_response = "chunk"
    requests: list[tuple[str, str]] = []

    class Connection:
        response: FakeHTTPResponse

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            requests.append((method, path))
            assert headers["Authorization"] == AUTH["Authorization"]
            assert headers["Content-Length"] == str(len(body))
            if path == "/api/v1/auth/session":
                self.response = FakeHTTPResponse(
                    200,
                    json.dumps(
                        {
                            "subject_id": server.owner,
                            "roles": ["admin"],
                        }
                    ).encode(),
                )
                return
            assert path.startswith("/api/v1/admin/")
            route = path.removeprefix("/api/v1/admin")
            if route == "/studio-safety/runtime-identity":
                self.response = FakeHTTPResponse(200, json.dumps(server.runtime).encode())
                return
            kwargs: dict[str, Any] = {}
            if method == "PUT":
                assert headers["Content-Type"] == "application/octet-stream"
                kwargs = {"data": body, "headers": {"X-Chunk-SHA256": headers["X-Chunk-SHA256"]}}
            elif method == "POST":
                assert headers["Content-Type"] == "application/json"
                kwargs = {"fields": json.loads(body)}
            try:
                status, payload = server.request(method, route, **kwargs)
            except importer.UploadError:
                raise ConnectionError("synthetic interrupted response") from None
            self.response = FakeHTTPResponse(status, json.dumps(payload).encode())

        def getresponse(self) -> FakeHTTPResponse:
            return self.response

        def close(self) -> None:
            pass

    monkeypatch.setattr(importer.http.client, "HTTPConnection", lambda *a, **k: Connection())
    client = importer.LocalStudioClient("http://api:8000", AUTH["Authorization"].split()[1])
    result = importer.run_import(root, manifest, client, ledger)
    assert result["entries"][0]["status"] == "completed"
    assert result["binding"]["owner_id"] == server.owner
    assert result["binding"]["runtime"] == server.runtime
    assert ("POST", "/api/v1/admin/source-uploads") in requests
    assert ("POST", f"/api/v1/admin/source-uploads/{UPLOAD_ID}/complete") in requests
    assert server.sent_sizes == [9]
