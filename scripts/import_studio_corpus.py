import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import ValidationError

from exam_guru_api.documents.extraction import KNOWN_CORRUPT_SOURCE_FINGERPRINT
from exam_guru_api.documents.schemas import SourceIntakeMetadata
from exam_guru_api.documents.upload_schemas import (
    MAX_UPLOAD_INTEGER,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_RECEIPT_PAGE_SIZE,
    SourceUploadChunkPageResponse,
    SourceUploadCreateRequest,
    SourceUploadResponse,
)

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_RETRIES = 3
MAX_RETRY_DELAY = 120
LEDGER_SCHEMA = "studio-corpus-import.v2"

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
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pdfs"), list):
        raise ValueError("Invalid corpus manifest")
    groups = {}
    paths = set()
    for record in manifest["pdfs"]:
        if not isinstance(record, dict):
            raise ValueError("Invalid source record")
        if record.get("unreadable") or record.get("needs_password") or record.get("error"):
            raise ValueError("Corpus contains unreadable or password-required source")
        checksum = record.get("sha256")
        size = record.get("size_bytes")
        path = record.get("relative_path")
        if (
            not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
            or type(size) is not int
            or not 5 <= size <= MAX_UPLOAD_INTEGER
            or not isinstance(path, str)
            or not path
            or path in paths
        ):
            raise ValueError("Invalid source identity")
        paths.add(path)
        aliases = groups.setdefault(checksum, [])
        if aliases and aliases[0]["size_bytes"] != size:
            raise ValueError("Duplicate source size integrity mismatch")
        aliases.append(record)
    return [sorted(group, key=lambda record: record["relative_path"]) for group in groups.values()]


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
            "Candidate metadata from checksum-bound download manifests and PDF inspection; "
            "requires human confirmation."
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
        candidates = [record.get("candidate_metadata", {}).get(field, {}) for record in records]
        values = {candidate.get("candidate_value") for candidate in candidates}
        values.discard(None)
        ambiguous = any(
            candidate.get("status") == "ambiguous_candidates" for candidate in candidates
        )
        if len(values) == 1 and not ambiguous:
            value = next(iter(values))
            metadata[target] = LABELS.get(value, value) if isinstance(value, str) else value
        else:
            metadata["warnings"].append(
                f"{field.title()} is unresolved or has conflicting evidence."
            )
    if metadata["medium_label"]:
        metadata["warnings"].append(
            "Medium is a candidate only; download category and script "
            "do not prove the teaching medium."
        )
    if metadata["year"]:
        metadata["warnings"].append(
            "Year is an unverified source token, not a confirmed examination or curriculum year."
        )
    if any(record.get("legacy_font_risk") for record in records):
        metadata["warnings"].append(
            "Legacy-font risk: compare visible PDF glyphs with extracted text; "
            "do not approve until corrected."
        )
    if f"sha256:{first['sha256']}" == KNOWN_CORRUPT_SOURCE_FINGERPRINT:
        metadata["warnings"].append(
            "Known Grade 3 Sinhala text corruption: previously rejected by visual review; "
            "blocked from trust."
        )
    if any(record.get("parser_warnings") for record in records):
        metadata["warnings"].append(
            "PDF parser reported recoverable layout/text warnings; review page coverage carefully."
        )
    bindings = first.get("manifest_bindings", [])
    urls = sorted({binding["source_url"] for binding in bindings if binding.get("source_url")})
    metadata["evidence"].extend(f"Download reference: {url}" for url in urls[:4] if len(url) < 990)
    metadata["evidence"].append(f"Identical local PDF file instances: {len(records)}.")
    return SourceIntakeMetadata.model_validate(metadata).model_dump(mode="json")


def document_type(metadata):
    return {
        "Teacher Guide": "teacher_guide",
        "Syllabus": "syllabus",
        "Marking Scheme": "marking_scheme",
        "Assessment Paper": "past_paper",
    }.get(metadata.get("document_type_label"), "other_approved")


def reject_symlinks(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Source or ledger path traverses a symlink")


def source_path(root, record):
    reject_symlinks(root)
    root = root.resolve(strict=True)
    relative = record["relative_path"]
    if (
        Path(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("Source path is outside the immutable corpus")
    path = root / relative
    reject_symlinks(path)
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("Source path is outside the immutable corpus")
    return path


def source_stat(handle):
    value = os.fstat(handle.fileno())
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("Source integrity requires a regular file")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def verify_source(handle, record):
    before = source_stat(handle)
    if before[2] != record["size_bytes"]:
        raise ValueError("Source integrity size mismatch")
    handle.seek(0)
    digest = hashlib.sha256()
    remaining = record["size_bytes"]
    while remaining:
        size = min(UPLOAD_CHUNK_BYTES, remaining)
        chunk = handle.read(size)
        if len(chunk) != size:
            raise ValueError("Source integrity size mismatch")
        if remaining == record["size_bytes"] and not chunk.startswith(b"%PDF-"):
            raise ValueError("Source integrity PDF signature mismatch")
        digest.update(chunk)
        remaining -= size
    if handle.read(1) or digest.hexdigest() != record["sha256"] or source_stat(handle) != before:
        raise ValueError("Source integrity checksum mismatch")
    handle.seek(0)
    return before


@contextmanager
def open_source(root, record):
    path = source_path(root, record)
    absolute_root = root.absolute()
    directory = os.open(absolute_root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (*absolute_root.parts[1:], *Path(record["relative_path"]).parts[:-1]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as handle:
        before = verify_source(handle, record)
        yield handle
        if source_stat(handle) != before:
            raise ValueError("Source integrity changed during upload")


def resolve_source(root, record):
    path = source_path(root, record)
    with open_source(root, record):
        pass
    return path


class UploadError(RuntimeError):
    def __init__(self, code, *, status=0, retry_after=None):
        self.code = (
            code if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", str(code)) else "upload_request_failed"
        )
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"{self.code} (HTTP {status}); saved progress retained, not cancelled")

    @property
    def recoverable(self):
        return (
            self.code == "upload_interrupted"
            or self.status in {408, 429, 500, 502, 503, 504}
            or (
                self.status == 409
                and self.code in {"source_upload_offset_conflict", "source_upload_version_conflict"}
            )
        )


def retry_wait(error, attempt):
    delay = error.retry_after if error.retry_after is not None else min(2**attempt, 10)
    if delay > MAX_RETRY_DELAY:
        raise UploadError("upload_backpressure_pause", status=error.status, retry_after=delay)
    time.sleep(max(0, delay))


def json_object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("invalid constant")

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise UploadError("upload_response_invalid") from None


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
            or any(character.isspace() for character in base_url)
        ):
            raise ValueError("Import requires a local Studio origin without credentials")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 8192
            or not token.isascii()
            or any(character.isspace() or not character.isprintable() for character in token)
        ):
            raise ValueError("Import authentication environment variable is missing or invalid")
        self.host = url.hostname
        self.port = url.port or 80
        self.origin = f"http://{self.host}:{self.port}"
        self.token = token

    def request(self, method, path, *, fields=None, data=None, headers=None):
        identifier = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
        allowed = (
            (
                method == "GET"
                and (
                    path in {"/auth/session", "/studio-safety/runtime-identity"}
                    or re.fullmatch(rf"/source-uploads/(?:by-request/)?{identifier}", path)
                    or re.fullmatch(
                        rf"/source-uploads/{identifier}/chunks\?offset=[0-9]+&limit=64", path
                    )
                )
            )
            or (
                method == "POST"
                and (
                    path == "/source-uploads"
                    or re.fullmatch(rf"/source-uploads/{identifier}/complete", path)
                )
            )
            or (
                method == "PUT"
                and re.fullmatch(rf"/source-uploads/{identifier}/chunks\?offset=[0-9]+", path)
            )
        )
        if not allowed:
            raise ValueError("Only supported resumable ingestion actions are allowed")
        if method == "PUT":
            if (
                not isinstance(data, bytes)
                or not 0 < len(data) <= UPLOAD_CHUNK_BYTES
                or fields is not None
            ):
                raise ValueError("Invalid bounded upload chunk")
            body = data
            media_type = "application/octet-stream"
            if set(headers or {}) != {"X-Chunk-SHA256"} or not re.fullmatch(
                r"[0-9a-f]{64}", headers["X-Chunk-SHA256"]
            ):
                raise ValueError("Invalid chunk checksum header")
        else:
            if data is not None or headers:
                raise ValueError("Invalid upload request body")
            body = (
                json.dumps(fields, ensure_ascii=True, allow_nan=False).encode()
                if fields is not None
                else b""
            )
            media_type = "application/json"
            if len(body) > 65536:
                raise ValueError("Upload metadata exceeds bounded request limit")
        target = "/api/v1" + ("" if path == "/auth/session" else "/admin") + path
        for attempt in range(MAX_RETRIES + 1 if method == "GET" else 1):
            connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
            try:
                connection.request(
                    method,
                    target,
                    body=body,
                    headers={
                        "Authorization": "Bearer " + self.token,
                        "Content-Type": media_type,
                        "Content-Length": str(len(body)),
                        **(headers or {}),
                    },
                )
                response = connection.getresponse()
                raw_length = response.getheader("Content-Length")
                if raw_length is not None and (
                    not re.fullmatch(r"[0-9]{1,10}", raw_length)
                    or int(raw_length) > MAX_RESPONSE_BYTES
                ):
                    raise UploadError("upload_response_too_large")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise UploadError("upload_response_too_large")
                if raw_length is not None and len(raw) != int(raw_length):
                    raise UploadError("upload_response_length_mismatch")
                if (
                    response.getheader("Content-Type", "").split(";", 1)[0].lower()
                    != "application/json"
                ):
                    raise UploadError("upload_response_invalid")
                payload = json_object(raw)
                if not 200 <= response.status < 300:
                    detail = payload.get("detail")
                    code = detail.get("code") if isinstance(detail, dict) else None
                    retry = response.getheader("Retry-After")
                    if retry is not None and re.fullmatch(r"[0-9]{1,6}", retry) is None:
                        raise UploadError("upload_response_retry_after_invalid")
                    delay = int(retry) if retry is not None else None
                    raise UploadError(code, status=response.status, retry_after=delay)
                expected_statuses = (
                    {201}
                    if path == "/source-uploads"
                    else {200, 202}
                    if path.endswith("/complete")
                    else {200}
                )
                if response.status not in expected_statuses:
                    raise UploadError("upload_response_status_invalid")
                return response.status, payload
            except (OSError, http.client.HTTPException):
                error = UploadError("upload_interrupted")
            except UploadError as caught:
                error = caught
            finally:
                connection.close()
            if method != "GET" or not error.recoverable or attempt == MAX_RETRIES:
                raise error from None
            retry_wait(error, attempt)
        raise UploadError("upload_interrupted")

    def identity(self):
        _, owner = self.request("GET", "/auth/session")
        _, runtime = self.request("GET", "/studio-safety/runtime-identity")
        try:
            subject = str(UUID(owner["subject_id"]))
            if runtime.get("application_env") not in {"local", "test", "staging", "production"}:
                raise ValueError("invalid runtime")
            test_id = runtime.get("test_runtime_id")
            if test_id is not None and not isinstance(test_id, str):
                raise ValueError("invalid runtime")
        except (KeyError, TypeError, ValueError):
            raise UploadError("upload_response_invalid") from None
        return {
            "origin": self.origin,
            "owner_id": subject,
            "runtime": {
                "application_env": runtime["application_env"],
                "test_runtime_id": test_id,
            },
        }


def save_ledger(path, ledger):
    reject_symlinks(path)
    data = json.dumps(ledger, ensure_ascii=True, allow_nan=False, indent=2).encode() + b"\n"
    if len(data) > MAX_LEDGER_BYTES:
        raise ValueError("Upload ledger exceeds bounded metadata limit; history retained")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def ledger_lock(root, path):
    import fcntl

    reject_symlinks(path)
    if path.resolve().is_relative_to(root.resolve()) or not path.parent.is_dir():
        raise ValueError(
            "Upload ledger must be outside the immutable corpus in an existing directory"
        )
    descriptor = os.open(
        path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Upload ledger is already in use") from None
        yield
    finally:
        os.close(descriptor)


def bounded_json_file(path):
    reject_symlinks(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Regular metadata file required")
        data = source.read(MAX_LEDGER_BYTES + 1)
    if len(data) > MAX_LEDGER_BYTES:
        raise ValueError("Metadata file exceeds bounded limit")
    return json_object(data)


def creation_request(group, request_id):
    metadata = build_intake_metadata(group)
    return SourceUploadCreateRequest(
        request_id=request_id,
        filename=Path(group[0]["relative_path"]).name,
        size_bytes=group[0]["size_bytes"],
        document_type=document_type(metadata),
        intake_metadata=metadata,
        expected_checksum_sha256=group[0]["sha256"],
    ).model_dump(mode="json")


def accept_session(payload, entry):
    try:
        view = SourceUploadResponse.model_validate_json(
            json.dumps(payload), strict=True
        ).model_dump(mode="json")
    except (TypeError, ValueError, ValidationError, RecursionError):
        raise UploadError("upload_response_invalid") from None
    request = entry["request"]
    for key in [
        "request_id",
        "filename",
        "size_bytes",
        "document_type",
        "intake_metadata",
        "expected_checksum_sha256",
    ]:
        if view[key] != request[key]:
            raise UploadError("upload_response_identity_mismatch")
    size, offset = view["size_bytes"], view["next_offset"]
    if (
        entry.get("upload_id") not in {None, view["id"]}
        or not 0 <= offset <= size
        or (offset != size and offset % UPLOAD_CHUNK_BYTES != 0)
        or not 0 <= view["verified_bytes"] <= size
        or not 0 <= view["version"] <= MAX_UPLOAD_INTEGER
        or view["chunk_size_bytes"] != UPLOAD_CHUNK_BYTES
        or offset < entry.get("next_offset", 0)
        or view["version"] < entry.get("version", 0)
        or (view["status"] in {"pending", "finalizing", "completed"} and offset != size)
        or (entry.get("server_status") == "completed" and view["status"] != "completed")
    ):
        raise UploadError("upload_response_invalid")
    if view["status"] == "completed" and (
        view["verified_bytes"] != size
        or view["checksum_sha256"] != entry["sha256"]
        or view["document_id"] is None
        or entry.get("document_id") not in {None, view["document_id"]}
        or ("deduplicated" in entry and entry["deduplicated"] != view["deduplicated"])
    ):
        raise UploadError("upload_completion_identity_mismatch")
    entry.update(
        upload_id=view["id"],
        next_offset=offset,
        version=view["version"],
        server_status=view["status"],
        status=view["status"],
    )
    if view["status"] == "completed":
        entry.update(
            document_id=view["document_id"],
            source_read_job_id=view["source_read_job_id"],
            new_upload=not view["deduplicated"],
            deduplicated=view["deduplicated"],
            metadata_review_required=True,
        )
    return view


def check_prefix(client, handle, session):
    offset = 0
    while offset < session["next_offset"]:
        _, payload = client.request(
            "GET",
            f"/source-uploads/{session['id']}/chunks?offset={offset}&limit={UPLOAD_RECEIPT_PAGE_SIZE}",
        )
        try:
            page = SourceUploadChunkPageResponse.model_validate_json(
                json.dumps(payload), strict=True
            ).model_dump(mode="json")
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Upload receipt response invalid") from None
        if (
            page["upload_id"] != session["id"]
            or page["next_offset"] != session["next_offset"]
            or not page["receipts"]
        ):
            raise ValueError("Upload receipt prefix changed")
        for receipt in page["receipts"]:
            size = min(UPLOAD_CHUNK_BYTES, session["size_bytes"] - offset)
            if (
                receipt["offset"] != offset
                or receipt["size_bytes"] != size
                or offset + size > session["next_offset"]
            ):
                raise ValueError("Upload receipt prefix invalid")
            handle.seek(offset)
            data = handle.read(size)
            if len(data) != size or hashlib.sha256(data).hexdigest() != receipt["checksum_sha256"]:
                raise ValueError("Upload receipt source checksum mismatch")
            offset += size
        expected = offset if offset < session["next_offset"] else None
        if page["next_receipt_offset"] != expected:
            raise ValueError("Upload receipt pagination invalid")


def upload_source(root, group, client, entry, checkpoint, *, max_polls, poll_interval):
    with open_source(root, group[0]) as handle:
        original_stat = source_stat(handle)

        def accept(payload):
            view = accept_session(payload, entry)
            checkpoint()
            if view["status"] == "failed":
                raise UploadError("upload_failed")
            return view

        def get():
            _, payload = client.request("GET", f"/source-uploads/{entry['upload_id']}")
            return accept(payload)

        if entry.get("upload_id"):
            session = get()
        elif entry["create_attempted"]:
            _, payload = client.request("GET", f"/source-uploads/by-request/{entry['request_id']}")
            session = accept(payload)
        else:
            entry.update(create_attempted=True, status="creating")
            checkpoint()
            try:
                _, payload = client.request("POST", "/source-uploads", fields=entry["request"])
            except UploadError as error:
                if not error.recoverable:
                    raise
                retry_wait(error, 0)
                _, payload = client.request(
                    "GET", f"/source-uploads/by-request/{entry['request_id']}"
                )
            session = accept(payload)
        check_prefix(client, handle, session)
        recoveries = 0
        while session["status"] == "uploading" and session["next_offset"] < session["size_bytes"]:
            offset = session["next_offset"]
            size = min(UPLOAD_CHUNK_BYTES, session["size_bytes"] - offset)
            handle.seek(offset)
            data = handle.read(size)
            if len(data) != size or source_stat(handle) != original_stat:
                raise ValueError("Source integrity changed before append")
            try:
                _, payload = client.request(
                    "PUT",
                    f"/source-uploads/{session['id']}/chunks?offset={offset}",
                    data=data,
                    headers={"X-Chunk-SHA256": hashlib.sha256(data).hexdigest()},
                )
                updated = accept(payload)
                if updated["next_offset"] != offset + size:
                    raise UploadError("upload_response_offset_mismatch")
                session = updated
            except UploadError as error:
                if not error.recoverable or recoveries >= MAX_RETRIES:
                    raise
                retry_wait(error, recoveries)
                recoveries += 1
                session = get()
                check_prefix(client, handle, session)
            finally:
                del data
        if session["status"] == "uploading":
            check_prefix(client, handle, session)
            verify_source(handle, group[0])
            while session["status"] == "uploading":
                try:
                    _, payload = client.request(
                        "POST",
                        f"/source-uploads/{session['id']}/complete",
                        fields={"expected_version": session["version"]},
                    )
                    session = accept(payload)
                    if session["status"] == "uploading":
                        raise UploadError("upload_response_invalid")
                except UploadError as error:
                    if not error.recoverable or recoveries >= MAX_RETRIES:
                        raise
                    retry_wait(error, recoveries)
                    recoveries += 1
                    session = get()
                    check_prefix(client, handle, session)
        polls = 0
        while session["status"] in {"pending", "finalizing"}:
            if polls >= max_polls:
                raise UploadError("upload_still_finishing")
            time.sleep(poll_interval)
            session = get()
            polls += 1
        if session["status"] != "completed":
            raise UploadError("upload_failed")


def run_import(
    root,
    manifest,
    client,
    ledger_path,
    *,
    limit=None,
    migrate_legacy=False,
    max_polls=120,
    poll_interval=1,
):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Import limit must be positive")
    if type(max_polls) is not int or not 1 <= max_polls <= 600 or not 0 <= poll_interval <= 30:
        raise ValueError("Invalid bounded polling configuration")
    root, ledger_path = Path(root), Path(ledger_path).absolute()
    groups = group_sources(manifest)
    identities = [
        {
            "sha256": g[0]["sha256"],
            "size_bytes": g[0]["size_bytes"],
            "paths": [r["relative_path"] for r in g],
            "request": creation_request(g, None),
        }
        for g in groups
    ]
    manifest_hash = hashlib.sha256(
        json.dumps(sorted(identities, key=lambda item: item["sha256"]), sort_keys=True).encode()
    ).hexdigest()
    with ledger_lock(root, ledger_path):
        previous = bounded_json_file(ledger_path) if ledger_path.exists() else None
        if previous and previous.get("schema") == "studio-corpus-import.v1" and not migrate_legacy:
            raise ValueError(
                "Unbound legacy ledger requires explicit --migrate-legacy-ledger; history retained"
            )
        if previous and previous.get("schema") not in {"studio-corpus-import.v1", LEDGER_SCHEMA}:
            raise ValueError("Unexpected import ledger schema")
        for group in groups[:limit]:
            for record in group:
                resolve_source(root, record)
        binding = {
            **client.identity(),
            "source_root": str(root.resolve()),
            "manifest_sha256": manifest_hash,
        }
        if previous and previous["schema"] == LEDGER_SCHEMA:
            if previous.get("binding") != binding:
                raise ValueError("Upload ledger origin/owner/runtime/source identity mismatch")
            ledger = previous
        else:
            ledger = {
                "schema": LEDGER_SCHEMA,
                "binding": binding,
                "legacy_history": [previous] if previous else [],
                "discovered_pdfs": sum(map(len, groups)),
                "unique_pdfs": len(groups),
                "entries": [],
            }
        entries = ledger.get("entries")
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("Invalid upload ledger entries")
        by_hash = {e.get("sha256"): e for e in entries}
        if len(by_hash) != len(entries) or not set(by_hash).issubset(
            {g[0]["sha256"] for g in groups}
        ):
            raise ValueError("Invalid upload ledger source identities")
        for group in groups[:limit]:
            checksum = group[0]["sha256"]
            entry = by_hash.get(checksum)
            if entry is None:
                request_id = str(uuid4())
                entry = {
                    "sha256": checksum,
                    "size_bytes": group[0]["size_bytes"],
                    "paths": [r["relative_path"] for r in group],
                    "storage_grades": [r.get("storage_grade") for r in group],
                    "request_id": request_id,
                    "request": creation_request(group, request_id),
                    "create_attempted": False,
                    "status": "prepared",
                    "events": [],
                }
                ledger["entries"].append(entry)
                by_hash[checksum] = entry
            try:
                request_id_valid = str(UUID(entry.get("request_id"))) == entry.get("request_id")
                upload_id = entry.get("upload_id")
                upload_id_valid = upload_id is None or str(UUID(upload_id)) == upload_id
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Upload ledger request identity mismatch") from None
            if (
                not request_id_valid
                or not upload_id_valid
                or type(entry.get("create_attempted")) is not bool
                or entry.get("size_bytes") != group[0]["size_bytes"]
                or entry.get("paths") != [record["relative_path"] for record in group]
                or entry.get("request") != creation_request(group, entry.get("request_id"))
                or not isinstance(entry.get("events"), list)
            ):
                raise ValueError("Upload ledger request identity mismatch")
            save_ledger(ledger_path, ledger)
            try:
                upload_source(
                    root,
                    group,
                    client,
                    entry,
                    lambda: save_ledger(ledger_path, ledger),
                    max_polls=max_polls,
                    poll_interval=poll_interval,
                )
            except (KeyboardInterrupt, Exception) as error:
                code = (
                    error.code
                    if isinstance(error, UploadError)
                    else "upload_paused"
                    if isinstance(error, KeyboardInterrupt)
                    else "upload_local_validation_failed"
                )
                entry["events"].append({"event": "paused", "code": code})
                entry.update(status="paused", error=code)
                save_ledger(ledger_path, ledger)
                raise
            entry.pop("error", None)
            entry["events"].append(
                {"event": "upload_completed", "document_id": entry["document_id"]}
            )
            save_ledger(ledger_path, ledger)
            sys.stdout.write(
                json.dumps(
                    {
                        "processed_unique": len(ledger["entries"]),
                        "total_unique": len(groups),
                        "document_id": entry["document_id"],
                        "status": entry["status"],
                        "source_read_job_id": entry["source_read_job_id"],
                        "metadata_review_required": True,
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
    parser.add_argument("--migrate-legacy-ledger", action="store_true")
    parser.add_argument("--max-polls", type=int, default=120)
    parser.add_argument("--poll-interval", type=float, default=1)
    args = parser.parse_args()
    manifest = bounded_json_file(args.manifest)
    groups = group_sources(manifest)
    if not args.execute:
        for group in groups:
            for record in group:
                resolve_source(args.root, record)
            creation_request(group, None)
        sys.stdout.write(
            json.dumps(
                {
                    "dry_run": True,
                    "discovered_pdfs": sum(map(len, groups)),
                    "unique_pdfs": len(groups),
                    "upload_chunk_bytes": UPLOAD_CHUNK_BYTES,
                }
            )
            + "\n"
        )
        return
    client = LocalStudioClient(args.base_url, os.environ.get(args.token_env, ""))
    run_import(
        args.root,
        manifest,
        client,
        args.ledger,
        limit=args.limit,
        migrate_legacy=args.migrate_legacy_ledger,
        max_polls=args.max_polls,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    main()
