import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from exam_guru_api.documents.extraction import KNOWN_CORRUPT_SOURCE_FINGERPRINT
from exam_guru_api.documents.schemas import SourceIntakeMetadata

LABELS = {
    "maths": "Maths",
    "sinhala": "Sinhala",
    "english": "English",
    "parisaraya": "Environmental Studies",
    "buddhism": "Buddhism",
    "catholicism": "Catholicism",
    "christianity": "Christianity",
    "islam": "Islam",
    "tamil": "Tamil",
    "teacher_guide": "Teacher Guide",
    "syllabus": "Syllabus",
    "textbook": "Textbook",
    "worksheet": "Worksheet",
    "workbook": "Workbook",
    "marking_scheme": "Marking Scheme",
    "assessment_paper": "Assessment Paper",
    "activity": "Activity",
}


def group_sources(manifest):
    groups = {}
    for record in manifest["pdfs"]:
        if record.get("unreadable") or record.get("needs_password"):
            raise ValueError("Corpus contains unreadable or password-required source")
        groups.setdefault(record["sha256"], []).append(record)
    return list(groups.values())


def build_intake_metadata(records):
    first = records[0]
    metadata = {
        "candidate_grade": None,
        "subject_label": None,
        "medium_label": None,
        "curriculum_label": None,
        "document_type_label": None,
        "year": None,
        "term": None,
        "publisher": None,
        "source_reference": "sha256:" + first["sha256"],
        "evidence": [
            "Candidate metadata from checksum-bound download manifests and PDF inspection; requires human confirmation."
        ],
        "warnings": ["Curriculum version and metadata require review before AI use."],
    }
    field_map = {
        "grade": "candidate_grade",
        "subject": "subject_label",
        "medium": "medium_label",
        "type": "document_type_label",
        "year": "year",
        "authority": "publisher",
    }
    for field, target in field_map.items():
        candidates = [
            record.get("candidate_metadata", {}).get(field, {}) for record in records
        ]
        values = {candidate.get("candidate_value") for candidate in candidates}
        values.discard(None)
        ambiguous = any(
            candidate.get("status") == "ambiguous_candidates"
            for candidate in candidates
        )
        if len(values) == 1 and not ambiguous:
            value = next(iter(values))
            metadata[target] = (
                LABELS.get(value, value) if isinstance(value, str) else value
            )
        else:
            metadata["warnings"].append(
                f"{field.title()} is unresolved or has conflicting evidence."
            )
    if metadata["medium_label"]:
        metadata["warnings"].append(
            "Medium is a candidate only; download category and script do not prove the teaching medium."
        )
    if metadata["year"]:
        metadata["warnings"].append(
            "Year is an unverified source token, not a confirmed examination or curriculum year."
        )
    if any(record.get("legacy_font_risk") for record in records):
        metadata["warnings"].append(
            "Legacy-font risk: compare visible PDF glyphs with extracted text; do not approve until corrected."
        )
    if f"sha256:{first['sha256']}" == KNOWN_CORRUPT_SOURCE_FINGERPRINT:
        metadata["warnings"].append(
            "Known Grade 3 Sinhala text corruption: previously rejected by visual review; blocked from trust."
        )
    if any(record.get("parser_warnings") for record in records):
        metadata["warnings"].append(
            "PDF parser reported recoverable layout/text warnings; review page coverage carefully."
        )
    bindings = first.get("manifest_bindings", [])
    urls = sorted(
        {binding["source_url"] for binding in bindings if binding.get("source_url")}
    )
    metadata["evidence"].extend(
        f"Download reference: {url}" for url in urls[:4] if len(url) < 990
    )
    metadata["evidence"].append(f"Identical local PDF file instances: {len(records)}.")
    return SourceIntakeMetadata.model_validate(metadata).model_dump(mode="json")


def document_type(metadata):
    return {
        "Teacher Guide": "teacher_guide",
        "Syllabus": "syllabus",
        "Marking Scheme": "marking_scheme",
        "Assessment Paper": "past_paper",
    }.get(metadata.get("document_type_label"), "other_approved")


def resolve_source(root, record):
    root = root.resolve()
    path = root / record["relative_path"]
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink():
        raise ValueError("Source path is outside the immutable corpus")
    if not resolved.is_file() or resolved.stat().st_size != record["size_bytes"]:
        raise ValueError("Source integrity mismatch")
    with resolved.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != record["sha256"]:
        raise ValueError("Source integrity mismatch")
    return resolved


class LocalStudioClient:
    def __init__(self, base_url, token):
        url = urlsplit(base_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"api", "localhost", "127.0.0.1"}
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Import requires a local Studio origin without credentials"
            )
        if not token or any(character.isspace() for character in token):
            raise ValueError(
                "Import authentication environment variable is missing or invalid"
            )
        self.host = url.hostname
        self.port = url.port or 80
        self.token = token

    def request(self, method, path, *, fields=None, source=None):
        if not (
            (method == "GET" and path == "/source-documents")
            or (
                method == "POST"
                and (
                    path == "/source-documents"
                    or re.fullmatch(r"/source-documents/[0-9a-f-]{36}/extract", path)
                )
            )
        ):
            raise ValueError("Only source ingestion actions are allowed")
        for attempt in range(8):
            connection = http.client.HTTPConnection(self.host, self.port, timeout=180)
            boundary = "exam-guru-intake-" + uuid4().hex
            prefix = b""
            suffix = b""
            if source is not None:
                for name, value in (fields or {}).items():
                    prefix += (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                    ).encode()
                filename = source.name
                if any(char in filename for char in ('"', "\r", "\n", "\\")):
                    raise ValueError("Source filename is not safe for multipart upload")
                prefix += (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'
                ).encode()
                suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
            try:
                connection.putrequest(method, "/api/v1/admin" + path)
                connection.putheader("Authorization", "Bearer " + self.token)
                connection.putheader(
                    "Content-Length",
                    str(
                        len(prefix)
                        + len(suffix)
                        + (source.stat().st_size if source else 0)
                    ),
                )
                if source is not None:
                    connection.putheader(
                        "Content-Type", f"multipart/form-data; boundary={boundary}"
                    )
                connection.endheaders()
                if source is not None:
                    connection.send(prefix)
                    with source.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            connection.send(chunk)
                    connection.send(suffix)
                response = connection.getresponse()
                status = response.status
                retry_after = response.getheader("Retry-After", "1")
                data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise RuntimeError(
                        "Studio response exceeded the bounded metadata limit"
                    )
                if status == 429:
                    delay = min(120, max(1, int(retry_after)))
                    sys.stdout.write(
                        json.dumps({"event": "rate_limited", "wait_seconds": delay})
                        + "\n"
                    )
                    sys.stdout.flush()
                    time.sleep(delay)
                    continue
                if status in {401, 403}:
                    raise RuntimeError(
                        f"Studio authentication/authorization failed: HTTP {status}"
                    )
                payload = json.loads(data)
                if status >= 400:
                    detail = (
                        payload.get("detail", {}) if isinstance(payload, dict) else {}
                    )
                    code = (
                        detail.get("code", "request_failed")
                        if isinstance(detail, dict)
                        else "request_failed"
                    )
                    raise RuntimeError(
                        f"Studio ingestion failed: HTTP {status}, code {code}"
                    )
                return status, payload
            except (OSError, http.client.HTTPException):
                if attempt == 7:
                    raise RuntimeError("Local Studio network request failed") from None
                time.sleep(min(10, attempt + 1))
            finally:
                connection.close()
        raise RuntimeError("Local Studio retry budget exhausted")


def save_ledger(path, ledger):
    temporary = path.with_suffix(".pending.json")
    temporary.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def run_import(root, manifest, client, ledger_path, *, limit=None):
    groups = group_sources(manifest)
    _, existing = client.request("GET", "/source-documents")
    by_hash = {record["checksum_sha256"]: record for record in existing}
    ledger = {
        "schema": "studio-corpus-import.v1",
        "discovered_pdfs": sum(map(len, groups)),
        "unique_pdfs": len(groups),
        "preexisting_documents": len(existing),
        "entries": [],
    }
    previous_entries = {}
    if ledger_path.exists():
        previous = json.loads(ledger_path.read_text(encoding="utf-8"))
        if previous.get("schema") != ledger["schema"]:
            raise ValueError("Unexpected import ledger schema")
        ledger["preexisting_documents"] = previous["preexisting_documents"]
        previous_entries = {entry["sha256"]: entry for entry in previous["entries"]}
    for group in groups[:limit]:
        record = group[0]
        source = resolve_source(root, record)
        entry = {
            "sha256": record["sha256"],
            "paths": [r["relative_path"] for r in group],
            "storage_grades": [r["storage_grade"] for r in group],
        }
        try:
            document = by_hash.get(record["sha256"])
            if document is None:
                metadata = build_intake_metadata(group)
                _, document = client.request(
                    "POST",
                    "/source-documents",
                    source=source,
                    fields={
                        "document_type": document_type(metadata),
                        "intake_metadata": json.dumps(metadata, ensure_ascii=False),
                    },
                )
                by_hash[record["sha256"]] = document
                entry["new_upload"] = not document.get("deduplicated", False)
            else:
                entry["new_upload"] = previous_entries.get(record["sha256"], {}).get(
                    "new_upload", False
                )
            entry.update(
                {
                    "document_id": document["id"],
                    "status": document["extraction_status"],
                    "metadata_review_required": document.get(
                        "metadata_review_required", False
                    ),
                }
            )
            if document["extraction_status"] == "uploaded":
                _, queued = client.request(
                    "POST", f"/source-documents/{document['id']}/extract"
                )
                entry["status"] = queued["status"]
        except RuntimeError as error:
            entry["error"] = str(error)
            ledger["entries"].append(entry)
            save_ledger(ledger_path, ledger)
            raise
        ledger["entries"].append(entry)
        save_ledger(ledger_path, ledger)
        sys.stdout.write(
            json.dumps(
                {
                    "processed_unique": len(ledger["entries"]),
                    "total_unique": len(groups),
                    "document_id": entry["document_id"],
                    "status": entry["status"],
                }
            )
            + "\n"
        )
        sys.stdout.flush()
    return ledger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--token-env", default="EXAM_GURU_DETERMINISTIC_ADMIN_TOKEN")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    groups = group_sources(manifest)
    if not args.execute:
        for group in groups:
            for record in group:
                resolve_source(args.root, record)
            build_intake_metadata(group)
        sys.stdout.write(
            json.dumps(
                {
                    "dry_run": True,
                    "discovered_pdfs": sum(map(len, groups)),
                    "unique_pdfs": len(groups),
                }
            )
            + "\n"
        )
        return
    client = LocalStudioClient(args.base_url, os.environ.get(args.token_env, ""))
    run_import(args.root, manifest, client, args.ledger, limit=args.limit)


if __name__ == "__main__":
    main()
