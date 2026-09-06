"""Synthetic mechanics only: none of these tests establishes real OCR accuracy."""

import importlib.util
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import pymupdf
import pytest

MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "audit_source_fidelity.py"
spec = importlib.util.spec_from_file_location("source_fidelity_inventory", MODULE_PATH)
assert spec is not None
assert spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def make_pdf(path: Path, *texts: str, font_name: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        for text in texts:
            page = document.new_page(width=220, height=240)
            if text:
                page.insert_text((12, 25), text, fontsize=6)
        if font_name:
            for font in document[0].get_fonts():
                document.xref_set_key(font[0], "BaseFont", f"/{font_name}")
        document.save(path)


def test_inventory_counts_every_path_but_inspects_identical_pdf_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "RAG DATA"
    source = root / "Grade 3" / "English worksheet 2022.pdf"
    private_text = "Read the question and choose the PRIVATE FIXTURE answer"
    make_pdf(source, private_text, "")
    copy = root / "Grade 4" / "copy.PDF"
    copy.parent.mkdir()
    shutil.copyfile(source, copy)
    (root / "download.json").write_text('{"untrusted": "manifest"}')
    (root / "broken.pdf").write_bytes(b"%PDF-broken")

    def no_whole_file_bytes(_self: Path) -> bytes:
        raise AssertionError("whole-file bytes must never be used by the audit")

    monkeypatch.setattr(Path, "read_bytes", no_whole_file_bytes)
    output = tmp_path / "evidence" / "audit"
    summary = audit.run_audit(root, output)
    inventory = json.loads((output / "inventory.json").read_text())
    pages = [json.loads(line) for line in (output / "pages.jsonl").read_text().splitlines()]

    assert summary["total_files"] == 4
    assert summary["pdf_paths"] == 3
    assert summary["unique_pdf_documents"] == 2
    assert summary["unique_pdf_pages"] == 2
    assert summary["pdf_path_pages"] == 4
    assert summary["document_errors"] == 1
    assert summary["human_adjudicated_references"] == 0
    assert summary["quality_status"] == "awaiting_human_adjudication"
    assert summary["originals_unchanged"] is True
    assert len(pages) == 2
    assert pages[0]["text_layer_present"] is True
    assert pages[1]["text_layer_present"] is False
    assert pages[0]["assessment"]["recommended_route"] == "native_review"
    assert pages[1]["assessment"]["recommended_route"] == "ocr_review"
    assert pages[0]["resource_fonts"]
    assert pages[0]["span_fonts"][0]["name"] == "Helvetica"
    assert pages[0]["native_text_sha256"]
    assert private_text not in json.dumps(inventory)
    assert private_text not in json.dumps(pages)
    assert all("raw_text" not in page for page in pages)
    assert len(inventory["pdfs"]) == 3
    assert all(
        record["candidate_metadata"]["grade"]["verified"] is False for record in inventory["pdfs"]
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "inventory.json").stat().st_mode) == 0o600


def test_resource_and_used_span_fonts_expose_tamil_legacy_instead_of_english(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tamil.pdf"
    make_pdf(path, "vkJ nkhop khztu; ghlk;", font_name="ABCDEF+Bamini")
    with path.open("rb") as source, audit.open_pdf_file(source) as document:
        page = audit.page_metadata(document, 1)
    assert "legacy_font_tamil" in page["assessment"]["risk_codes"]
    assert page["assessment"]["languages"] == ["ta"]
    assert "ta" in page["span_assessment"]["languages"]
    assert page["assessment"]["script_counts"]["latin"] > 0
    assert page["resource_fonts"][0]["base_font"] == "ABCDEF+Bamini"
    assert page["span_fonts"]
    assert page["assessment"]["recommended_route"] == "ocr_review"


def test_snapshot_checks_hash_and_stable_nanosecond_mtime_even_when_size_matches(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"first")
    before = audit.snapshot_files(tmp_path)
    assert audit.compare_snapshots(before, before)["unchanged"] is True
    old = source.stat()
    source.write_bytes(b"other")
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    changed = audit.compare_snapshots(before, audit.snapshot_files(tmp_path))
    assert changed["unchanged"] is False
    assert changed["changed"][0]["relative_path"] == "source.bin"
    assert "sha256" in changed["changed"][0]["fields"]
    assert "size_bytes" not in changed["changed"][0]["fields"]
    source.unlink()
    assert audit.compare_snapshots(before, audit.snapshot_files(tmp_path))["removed"] == [
        "source.bin"
    ]


def test_snapshot_records_symlink_error_without_following_outside_corpus(tmp_path: Path) -> None:
    root = tmp_path / "RAG DATA"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private outside content")
    (root / "link.pdf").symlink_to(outside)
    records = audit.snapshot_files(root)
    assert len(records) == 1
    assert records[0]["error"] == "symlink_not_followed"
    assert records[0]["sha256"] is None
    assert audit.compare_snapshots(records, records)["unchanged"] is False


def test_filename_folder_candidates_are_never_authoritative() -> None:
    metadata = audit.candidate_metadata("Grade 4/Maths/Teacher Guides/2018/2020-guide.pdf")
    assert metadata["grade"]["candidate_value"] == 4
    assert metadata["subject"]["candidate_value"] == "maths"
    assert metadata["type"]["candidate_value"] == "teacher_guide"
    assert metadata["year"]["candidate_value"] is None
    assert metadata["year"]["status"] == "ambiguous_candidates"
    assert {item["value"] for item in metadata["year"]["candidates"]} == {2018, 2020}
    assert all(item["verified"] is False for item in metadata.values())
    assert metadata["medium"]["candidate_value"] is None


def test_inventory_refuses_output_inside_corpus_and_existing_evidence(tmp_path: Path) -> None:
    root = tmp_path / "RAG DATA"
    make_pdf(root / "source.pdf", "the question and answer")
    with pytest.raises(ValueError, match="outside"):
        audit.run_audit(root, root / "output")
    output = tmp_path / "private"
    audit.run_audit(root, output)
    before = (output / "inventory.json").read_text()
    with pytest.raises(FileExistsError):
        audit.run_audit(root, output)
    assert (output / "inventory.json").read_text() == before


def test_metadata_retains_page_failure_without_copying_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "RAG DATA"
    make_pdf(root / "source.pdf", "the question and answer", "read the next answer")
    original = audit.page_metadata

    def fail_one(document: Any, number: int) -> dict[str, Any]:
        if number == 1:
            raise RuntimeError("PRIVATE SOURCE in parser exception")
        return dict(original(document, number))

    monkeypatch.setattr(audit, "page_metadata", fail_one)
    summary = audit.run_audit(root, tmp_path / "output")
    assert summary["unique_pdf_pages"] == 2
    assert summary["page_errors"] == 1
    pages = (tmp_path / "output" / "pages.jsonl").read_text()
    assert "RuntimeError" in pages
    assert "PRIVATE SOURCE" not in pages
    assert len(pages.splitlines()) == 2


def test_walk_permission_failure_cannot_be_reported_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inaccessible_walk(_root: Path, **kwargs: Any) -> list[object]:
        handler = kwargs.get("onerror")
        if handler is not None:
            handler(PermissionError("fixture unreadable directory"))
        return []

    monkeypatch.setattr(os, "walk", inaccessible_walk)
    with pytest.raises(PermissionError):
        audit.snapshot_files(tmp_path)


def test_final_integrity_verification_cannot_write_inside_originals(tmp_path: Path) -> None:
    root = tmp_path / "RAG DATA"
    root.mkdir()
    (root / "data.bin").write_bytes(b"immutable original")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(audit.snapshot_files(root)))
    with pytest.raises(ValueError, match="outside"):
        audit.verify_originals(root, baseline, root / "illegal-output")
    assert not (root / "illegal-output").exists()
