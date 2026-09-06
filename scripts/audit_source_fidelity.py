"""Read-only, file-backed corpus audit. Outputs are private, unverified evidence.

Run in an isolated worker-image container with a read-only corpus mount. This
script imports no application configuration, database, queue, or HTTP clients.
It never stores page text in the inventory and never creates reviewed content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

import pymupdf
from exam_guru_api.documents import fidelity, page_reading, tesseract_ocr
from exam_guru_api.documents.fidelity import PageAssessment, assess_page
from exam_guru_api.documents.page_reading import extract_native_page
from exam_guru_api.documents.tesseract_ocr import open_pdf_file

SCHEMA_VERSION = "source-fidelity-inventory-v1"
HASH_CHUNK_BYTES = 1024 * 1024
QUALITY_STATUS = "awaiting_human_adjudication"
JsonRecord = dict[str, Any]
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_GRADE = re.compile(r"^Grade\s+(1[0-3]|[1-9])$", re.IGNORECASE)
_STABLE_FIELDS = ("size_bytes", "mtime_ns", "ctime_ns", "inode", "device", "mode", "sha256")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("regular file required")
        for chunk in iter(lambda: source.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(path: Path) -> Path:
    """Create only missing directories; refuse symlinks or a public output leaf."""
    path = path.absolute()
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("private output must not traverse symlinks")
    missing = [item for item in (path, *path.parents) if not item.exists()]
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError("private output directory requires mode 0700")
    return path


def private_text_file(path: Path) -> TextIO:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
    )
    return os.fdopen(descriptor, "w", encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    with private_text_file(path) as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def json_line(output: TextIO, value: object) -> None:
    output.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    output.flush()


def _file_paths(root: Path) -> Iterator[Path]:
    def fail_closed(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(root, followlinks=False, onerror=fail_closed):
        names.sort()
        symlinks = [name for name in names if (Path(directory) / name).is_symlink()]
        names[:] = [name for name in names if name not in symlinks]
        for name in sorted([*files, *symlinks]):
            yield Path(directory) / name


def _stat_fields(metadata: os.stat_result) -> JsonRecord:
    return {
        "size_bytes": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "inode": metadata.st_ino,
        "device": metadata.st_dev,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def snapshot_files(root: Path) -> list[JsonRecord]:
    """Hash every regular file in chunks, including non-PDF download manifests."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("corpus must be an existing non-symlink directory")
    records: list[JsonRecord] = []
    for path in sorted(_file_paths(root)):
        record: JsonRecord = {
            "relative_path": path.relative_to(root).as_posix(),
            "is_pdf": path.suffix.casefold() == ".pdf",
            "sha256": None,
            "error": None,
        }
        try:
            before = path.lstat()
            record.update(_stat_fields(before))
            if stat.S_ISLNK(before.st_mode):
                record["error"] = "symlink_not_followed"
            elif not stat.S_ISREG(before.st_mode):
                record["error"] = "non_regular_file_not_read"
            else:
                record["sha256"] = sha256_file(path)
                if _stat_fields(before) != _stat_fields(path.lstat()):
                    record["error"] = "source_changed_during_hash"
        except (OSError, ValueError) as error:
            record["error"] = type(error).__name__
        records.append(record)
    return records


def compare_snapshots(before: Sequence[JsonRecord], after: Sequence[JsonRecord]) -> JsonRecord:
    left = {item["relative_path"]: item for item in before}
    right = {item["relative_path"]: item for item in after}
    changed = [
        {"relative_path": path, "fields": fields}
        for path in sorted(left.keys() & right.keys())
        if (
            fields := [
                field for field in _STABLE_FIELDS if left[path].get(field) != right[path].get(field)
            ]
        )
    ]
    errors = [
        {"relative_path": item["relative_path"], "phase": phase, "error": item["error"]}
        for phase, records in (("before", before), ("after", after))
        for item in records
        if item.get("error")
    ]
    added, removed = sorted(right.keys() - left.keys()), sorted(left.keys() - right.keys())
    return {
        "unchanged": not (changed or errors or added or removed),
        "files_before": len(before),
        "files_after": len(after),
        "changed": changed,
        "added": added,
        "removed": removed,
        "errors": errors,
        "compared_fields": list(_STABLE_FIELDS),
        "atime_excluded": "Reading can change atime; content and nanosecond mtime are compared.",
    }


def _candidate(values: Sequence[tuple[object, str]]) -> JsonRecord:
    distinct = {value for value, _ in values}
    return {
        "candidate_value": next(iter(distinct)) if len(distinct) == 1 else None,
        "status": (
            "ambiguous_candidates"
            if len(distinct) > 1
            else "unverified_candidate"
            if distinct
            else "unresolved"
        ),
        "verified": False,
        "candidates": [{"value": value, "evidence": evidence} for value, evidence in values],
    }


def candidate_metadata(relative_path: str) -> JsonRecord:
    """Filename/folder hints only: never feed these to fidelity language routing."""
    parts = Path(relative_path).parts
    folded = relative_path.casefold().replace("_", " ").replace("-", " ")
    grade = [
        (int(match.group(1)), f"folder:{part}")
        for part in parts[:-1]
        if (match := _GRADE.fullmatch(part))
    ]
    subjects = {
        "maths": ("math", "ගණිත"),
        "sinhala": ("sinhala", "සිංහල"),
        "tamil": ("tamil", "தமிழ்"),
        "english": ("english",),
        "parisaraya": ("parisaraya", "environment", "පරිසර"),
        "buddhism": ("buddh", "බුද්ධ"),
        "catholicism": ("catholic",),
        "christianity": ("christian",),
        "islam": ("islam",),
    }
    subject = [
        (name, "filename_or_folder_token")
        for name, tokens in subjects.items()
        if any(token in folded for token in tokens)
    ]
    kinds = {
        "teacher_guide": ("teacher guide", "teacher's guide", "teachers guide", "ගුරු"),
        "syllabus": ("syllabus",),
        "marking_scheme": ("marking", "answer", "පිළිතුරු"),
        "assessment_paper": ("past paper", "test paper", "term test", "විභාග"),
        "worksheet": ("worksheet", "work sheet"),
        "workbook": ("workbook", "work book", "වැඩපොත"),
        "textbook": ("textbook", "text book", "පෙළ පොත"),
        "activity": ("activity", "ක්‍රියාකාරක"),
    }
    document_type = [
        (name, "filename_or_folder_token")
        for name, tokens in kinds.items()
        if any(token in folded for token in tokens)
    ]
    years = [
        (int(year), "filename_token" if index == len(parts) - 1 else "folder_token")
        for index, part in enumerate(parts)
        for year in _YEAR.findall(part)
    ]
    return {
        "grade": _candidate(grade),
        "subject": _candidate(subject),
        "type": _candidate(document_type),
        "year": _candidate(years),
        "medium": _candidate([]),
        "authority": _candidate([]),
    }


def assessment_metadata(assessment: PageAssessment) -> JsonRecord:
    # Explicit allowlist: never serialize normalized/display/source text or can_confirm.
    return {
        "languages": list(assessment.languages),
        "script_counts": dict(assessment.script_counts),
        "risk_codes": list(assessment.risk_codes),
        "classifications": list(assessment.classifications),
        "recommended_route": assessment.recommended_route,
        "algorithm_version": assessment.algorithm_version,
        "language_status": "unverified_text_and_font_evidence_not_teaching_medium",
    }


def _safe_font(value: object) -> str:
    return str(value).encode("utf-8", errors="backslashreplace").decode().replace("\x00", "\\u0000")


def page_metadata(document: pymupdf.Document, page_number: int) -> JsonRecord:
    page = document[page_number - 1]
    native = extract_native_page(document, page_number)
    # Exclude embedded image bytes from the text dictionary: page metadata only.
    layout = page.get_text(
        "dict", sort=True, flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    )
    fonts: dict[str, JsonRecord] = {}
    text_blocks = 0
    text_lines = 0
    spans_count = 0
    for block in layout["blocks"]:
        if block["type"] != 0:
            continue
        text_blocks += 1
        for line in block.get("lines", []):
            text_lines += 1
            for span in line.get("spans", []):
                name = _safe_font(span["font"])
                item = fonts.setdefault(
                    name, {"name": name, "span_count": 0, "character_count": 0, "sizes": set()}
                )
                item["span_count"] += 1
                item["character_count"] += len(span["text"])
                item["sizes"].add(round(float(span["size"]), 4))
                spans_count += 1
    span_names = tuple(sorted(fonts))
    resources = [
        dict(
            zip(
                (
                    "xref",
                    "extension",
                    "font_type",
                    "base_font",
                    "resource_name",
                    "encoding",
                    "referencer",
                ),
                (_safe_font(value) if isinstance(value, str) else value for value in font),
                strict=True,
            )
        )
        for font in page.get_fonts(full=True)
    ]
    assessment = assess_page(
        native.raw_text, font_names=native.font_names, image_coverage=native.image_coverage
    )
    span_assessment = assess_page(
        native.raw_text,
        font_names=tuple(name[:256] for name in span_names[:128]),
        image_coverage=native.image_coverage,
    )
    return {
        "page_number": page_number,
        "width_points": float(page.rect.width),
        "height_points": float(page.rect.height),
        "rotation": page.rotation,
        "native_text_characters": len(native.raw_text),
        "native_non_whitespace_characters": sum(
            not character.isspace() for character in native.raw_text
        ),
        "native_text_sha256": hashlib.sha256(
            native.raw_text.encode("utf-8", errors="surrogatepass")
        ).hexdigest(),
        "text_layer_present": bool(spans_count or native.raw_text.strip()),
        "text_block_count": text_blocks,
        "text_line_count": text_lines,
        "text_span_count": spans_count,
        "span_fonts": [
            {**fonts[name], "sizes": sorted(fonts[name]["sizes"])} for name in span_names
        ],
        "resource_fonts": resources,
        "assessment_font_names": list(native.font_names),
        "assessment_fonts_truncated": len(resources) > 128,
        "span_assessment_fonts_truncated": len(span_names) > 128,
        "image_count": len(page.get_image_info()),
        "image_coverage": native.image_coverage,
        "image_coverage_method": "current_file_reader_sum_clipped_rectangles_capped_at_one",
        "assessment": assessment_metadata(assessment),
        "span_assessment": assessment_metadata(span_assessment),
        "error": None,
    }


def runtime_metadata() -> JsonRecord:
    return {
        "python": sys.version,
        "pymupdf": pymupdf.VersionBind,
        "fidelity_algorithm": fidelity.ALGORITHM_VERSION,
        "source_sha256": {
            name: sha256_file(Path(cast(str, module.__file__)))
            for name, module in (
                ("fidelity.py", fidelity),
                ("page_reading.py", page_reading),
                ("tesseract_ocr.py", tesseract_ocr),
            )
        },
        "audit_script_sha256": sha256_file(Path(__file__)),
        "input_mode": "file_backed_pdf_descriptor",
        "hash_chunk_bytes": HASH_CHUNK_BYTES,
        "expected_languages": [],
        "font_assessment": "current resource-font routing plus separate used-span routing",
        "native_extraction": {"mode": "text", "sort": True},
        "human_adjudicated_references": 0,
        "quality_status": QUALITY_STATUS,
        "automatic_verification": False,
    }


def _inspect_pdf(root: Path, records: list[JsonRecord], pages: TextIO) -> JsonRecord:
    first = records[0]
    checksum = first["sha256"]
    result: JsonRecord = {
        "sha256": checksum,
        "size_bytes": first["size_bytes"],
        "aliases": [item["relative_path"] for item in records],
        "page_count": 0,
        "page_errors": 0,
        "error": None,
        "needs_password": False,
        "route_counts": {},
        "risk_counts": {},
        "language_counts": {},
        "classification_counts": {},
        "parser_warnings": [],
    }
    counters: dict[str, Counter[str]] = {
        name: Counter() for name in ("route", "risk", "language", "classification")
    }
    pymupdf.TOOLS.mupdf_warnings(reset=True)
    try:
        with (
            (root / first["relative_path"]).open("rb") as source,
            open_pdf_file(source, source_checksum_sha256=checksum) as document,
        ):
            result["page_count"] = document.page_count
            for number in range(1, document.page_count + 1):
                try:
                    page = page_metadata(document, number)
                    assessment = page["assessment"]
                    counters["route"][assessment["recommended_route"]] += 1
                    for name, key in (
                        ("risk", "risk_codes"),
                        ("language", "languages"),
                        ("classification", "classifications"),
                    ):
                        counters[name].update(assessment[key])
                except Exception as error:
                    # Keep the denominator and error, not arbitrary private exception messages.
                    page = {"page_number": number, "error": type(error).__name__}
                    result["page_errors"] += 1
                json_line(pages, {"source_sha256": checksum, **page})
    except Exception as error:
        result["error"] = type(error).__name__
        result["input_violation"] = (
            str(error.violation) if isinstance(error, tesseract_ocr.TesseractInputError) else None
        )
        result["needs_password"] = result["input_violation"] == "encrypted_pdf"
    warnings = pymupdf.TOOLS.mupdf_warnings(reset=True)
    if warnings:
        result["parser_warnings"] = ["mupdf_parser_warning"]
        result["parser_warning_lines"] = len(warnings.splitlines())
        result["parser_warning_sha256"] = hashlib.sha256(warnings.encode()).hexdigest()
    for name, counts in counters.items():
        result[f"{name}_counts"] = dict(sorted(counts.items()))
    return result


def _summary(files: list[JsonRecord], documents: list[JsonRecord]) -> JsonRecord:
    pdfs = [item for item in files if item["is_pdf"]]
    result: JsonRecord = {
        "total_files": len(files),
        "pdf_paths": len(pdfs),
        "non_pdf_paths": len(files) - len(pdfs),
        "global_unique_files": len({item["sha256"] for item in files if item["sha256"]}),
        "unique_pdf_documents": len(documents),
        "duplicate_pdf_paths": len(pdfs) - len(documents),
        "total_path_bytes": sum(item.get("size_bytes", 0) for item in files),
        "pdf_path_bytes": sum(item.get("size_bytes", 0) for item in pdfs),
        "unique_pdf_bytes": sum(item["size_bytes"] for item in documents),
        "unique_pdf_pages": sum(item["page_count"] for item in documents),
        "pdf_path_pages": sum(item["page_count"] * len(item["aliases"]) for item in documents),
        "file_errors": sum(bool(item["error"]) for item in files),
        "document_errors": sum(bool(item["error"]) for item in documents),
        "page_errors": sum(item["page_errors"] for item in documents),
        "documents_with_parser_warnings": sum(bool(item["parser_warnings"]) for item in documents),
        "human_adjudicated_references": 0,
        "quality_status": QUALITY_STATUS,
        "automatic_verification": False,
        "corpus_accuracy_estimate": None,
        "metadata_status": "filename_folder_year_type_candidates_unverified",
    }
    for name in ("route", "risk", "language", "classification"):
        unique: Counter[str] = Counter()
        weighted: Counter[str] = Counter()
        for item in documents:
            unique.update(item[f"{name}_counts"])
            weighted.update(
                {key: count * len(item["aliases"]) for key, count in item[f"{name}_counts"].items()}
            )
        result[f"unique_page_{name}_counts"] = dict(sorted(unique.items()))
        result[f"path_page_{name}_counts"] = dict(sorted(weighted.items()))
    grades: dict[str, JsonRecord] = {}
    for item in files:
        grade = candidate_metadata(item["relative_path"])["grade"]["candidate_value"]
        counts = grades.setdefault(str(grade), {"files": 0, "pdf_paths": 0, "pdf_path_pages": 0})
        counts["files"] += 1
        counts["pdf_paths"] += bool(item["is_pdf"])
    for item in documents:
        for alias in item["aliases"]:
            grade = candidate_metadata(alias)["grade"]["candidate_value"]
            grades[str(grade)]["pdf_path_pages"] += item["page_count"]
    result["storage_grade_counts_unverified"] = grades
    return result


def prepare_output(root: Path, output: Path) -> Path:
    if output.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        output.resolve()
    ):
        raise ValueError("evidence must be outside and separate from the original corpus")
    output = private_directory(output)
    if any(output.iterdir()):
        raise FileExistsError("output must be empty; previous evidence is never overwritten")
    return output


def run_audit(root: Path, output: Path) -> JsonRecord:
    root = root.absolute()
    output = prepare_output(root, output)
    start = time.monotonic()
    runtime = runtime_metadata()
    runtime["started_at"] = utc_now()
    runtime["corpus_root"] = str(root)
    write_json(output / "runtime.json", runtime)
    before = snapshot_files(root)
    write_json(output / "originals-before.json", before)
    groups: dict[str, list[JsonRecord]] = defaultdict(list)
    for record in before:
        if record["is_pdf"] and not record["error"]:
            groups[record["sha256"]].append(record)
    documents = []
    with (
        private_text_file(output / "pages.jsonl") as pages,
        private_text_file(output / "progress.jsonl") as progress,
    ):
        for checksum, aliases in sorted(groups.items()):
            result = _inspect_pdf(root, aliases, pages)
            documents.append(result)
            json_line(
                progress,
                {
                    "completed_documents": len(documents),
                    "source_sha256": checksum,
                    "page_count": result["page_count"],
                    "error": result["error"],
                    "elapsed_seconds": time.monotonic() - start,
                },
            )
    after = snapshot_files(root)
    write_json(output / "originals-after.json", after)
    integrity = compare_snapshots(before, after)
    write_json(output / "original-integrity.json", integrity)
    summary = _summary(before, documents)
    summary.update(
        originals_unchanged=integrity["unchanged"],
        elapsed_seconds=time.monotonic() - start,
        finished_at=utc_now(),
    )
    by_checksum = {item["sha256"]: item for item in documents}
    pdfs: list[JsonRecord] = []
    for record in before:
        if not record["is_pdf"]:
            continue
        document = by_checksum.get(record["sha256"], {})
        metadata = candidate_metadata(record["relative_path"])
        pdfs.append(
            {
                **record,
                "storage_grade": metadata["grade"]["candidate_value"],
                "candidate_metadata": metadata,
                "page_count": document.get("page_count", 0),
                "unreadable": bool(record["error"] or document.get("error")),
                "needs_password": document.get("needs_password", False),
                "parser_warnings": document.get("parser_warnings", []),
                "legacy_font_risk": any(
                    key.startswith("legacy_font_") for key in document.get("risk_counts", {})
                ),
                "manifest_bindings": [],
            }
        )
    write_json(
        output / "inventory.json",
        {
            "schema_version": SCHEMA_VERSION,
            "summary": summary,
            "files": before,
            "pdfs": pdfs,
            "documents": documents,
            "page_metadata_file": "pages.jsonl",
            "candidate_metadata_basis": (
                "local filenames and folders only; download manifests hashed, not authority"
            ),
        },
    )
    write_json(output / "summary.json", summary)
    return summary


def verify_originals(root: Path, baseline: Path, output: Path) -> JsonRecord:
    output = prepare_output(root, output)
    with baseline.open(encoding="utf-8") as source:
        before = json.load(source)
    after = snapshot_files(root)
    proof = compare_snapshots(before, after)
    proof.update(verified_at=utc_now(), baseline_sha256=sha256_file(baseline))
    write_json(output / "originals-after.json", after)
    write_json(output / "original-integrity.json", proof)
    return proof


def main(argv: Sequence[str] | None = None) -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit_command = commands.add_parser("audit")
    audit_command.add_argument("--corpus-root", required=True, type=Path)
    audit_command.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--corpus-root", required=True, type=Path)
    verify.add_argument("--baseline", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "audit":
        summary = run_audit(arguments.corpus_root, arguments.output)
        success = summary["originals_unchanged"]
    else:
        summary = verify_originals(arguments.corpus_root, arguments.baseline, arguments.output)
        success = summary["unchanged"]
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
