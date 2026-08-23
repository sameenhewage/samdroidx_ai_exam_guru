import json
from pathlib import Path

import pymupdf
import pytest

from exam_guru_api.documents.inventory import (
    PdfTextMode,
    _document_type,
    _feasibility,
    _subject,
    build_inventory,
    detect_language,
    inventory_pdf,
    main,
    write_inventory,
)


def make_pdf(path: Path, *page_texts: str) -> None:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_inventory_records_safe_native_metadata_without_source_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "Grade 5 English test paper 2015.pdf"
    make_pdf(pdf_path, "Body content should never be persisted")

    record = inventory_pdf(pdf_path, root=tmp_path)

    assert record.relative_path == pdf_path.name
    assert len(record.sha256) == 64
    assert record.page_count == 1
    assert record.native_text_pages == 1
    assert record.image_only_pages == 0
    assert record.text_mode is PdfTextMode.NATIVE
    assert record.language == "english"
    assert record.document_type == "past_paper"
    assert record.year == 2015
    serialized = json.dumps(record.to_dict())
    assert "Body content should never be persisted" not in serialized


def test_inventory_distinguishes_scanned_and_mixed_documents(tmp_path: Path) -> None:
    scanned = tmp_path / "scan.pdf"
    mixed = tmp_path / "mixed.pdf"
    four_pages = tmp_path / "four-pages.pdf"
    make_pdf(scanned, "")
    make_pdf(mixed, "Native English content for page one", "")
    make_pdf(
        four_pages,
        "page one native content",
        "page two native content",
        "page three native content",
        "page four native content",
    )

    scanned_record = inventory_pdf(scanned, root=tmp_path)
    mixed_record = inventory_pdf(mixed, root=tmp_path)
    four_page_record = inventory_pdf(four_pages, root=tmp_path)

    assert scanned_record.text_mode is PdfTextMode.SCANNED
    assert scanned_record.representative_ocr_pages == (1,)
    assert mixed_record.text_mode is PdfTextMode.MIXED
    assert detect_language("සිංහල පාඩම") == "sinhala"
    assert mixed_record.representative_native_pages == (1,)
    assert mixed_record.representative_ocr_pages == (2,)
    assert four_page_record.page_count == 4


def test_inventory_preserves_errors_and_writes_aggregate_manifest(tmp_path: Path) -> None:
    make_pdf(tmp_path / "valid.pdf", "valid native content")
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    output = tmp_path / "evidence" / "inventory.json"

    inventory = build_inventory(tmp_path)
    write_inventory(inventory, output)
    persisted = json.loads(output.read_text())

    assert persisted["summary"]["total_files"] == 2
    assert persisted["summary"]["malformed_files"] == 1
    assert len(persisted["records"]) == 2
    assert all("text" not in record for record in persisted["records"])


def test_inventory_language_and_filename_classifiers_cover_supported_categories() -> None:
    assert detect_language("abc සිංහල") == "mixed"
    assert detect_language("12 !") == "unknown"
    assert _subject("grade 5 Maths/file.pdf") == "maths"
    assert _subject("grade 5 Parisaraya/file.pdf") == "parisaraya"
    assert _subject("grade 5 English/file.pdf") == "english"
    assert _subject("grade 5 Sinhala/file.pdf") == "sinhala"
    assert _subject("misc/file.pdf") == "unknown"

    expected_types = {
        "Teacher Guide.pdf": "teacher_guide",
        "Worksheet Answers.pdf": "marking_scheme",
        "Past Paper.pdf": "past_paper",
        "Work Sheet.pdf": "worksheet",
        "textbook.pdf": "textbook",
        "activity.pdf": "activity",
        "Story.pdf": "supplementary",
        "misc.pdf": "other",
    }
    assert {name: _document_type(name) for name in expected_types} == expected_types
    assert _feasibility(PdfTextMode.ERROR) == "manual_inspection_required"


def test_inventory_handles_encrypted_empty_images_and_page_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fixture")

    class FakePage:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail

        def get_text(self, _mode: str, *, sort: bool) -> str:
            assert sort
            if self.fail:
                raise RuntimeError("page failure")
            return "Document release 2022 with enough native text"

        def get_images(self, *, full: bool) -> list[object]:
            assert full
            return [object()]

    class FakeDocument:
        def __init__(self, *, needs_pass: bool, page_count: int, fail: bool = False) -> None:
            self.needs_pass = needs_pass
            self.page_count = page_count
            self.page = FakePage(fail=fail)

        def __getitem__(self, _index: int) -> FakePage:
            return self.page

        def close(self) -> None:
            return None

    documents = iter(
        [
            FakeDocument(needs_pass=True, page_count=1),
            FakeDocument(needs_pass=False, page_count=0),
            FakeDocument(needs_pass=False, page_count=1),
            FakeDocument(needs_pass=False, page_count=1, fail=True),
        ]
    )
    monkeypatch.setattr(pymupdf, "open", lambda _path: next(documents))

    encrypted = inventory_pdf(source, root=tmp_path)
    empty = inventory_pdf(source, root=tmp_path)
    image = inventory_pdf(source, root=tmp_path)
    failed = inventory_pdf(source, root=tmp_path)

    assert encrypted.encrypted
    assert empty.error == "empty_pdf"
    assert image.pages_with_images == 1
    assert image.year == 2022
    assert image.year_evidence == "document_text"
    assert failed.error == "RuntimeError"


def test_inventory_cli_writes_manifest(tmp_path: Path) -> None:
    make_pdf(tmp_path / "source.pdf", "native content for command output")
    output = tmp_path / "evidence" / "manifest.json"

    main([str(tmp_path), str(output)])

    assert json.loads(output.read_text())["summary"]["total_files"] == 1
