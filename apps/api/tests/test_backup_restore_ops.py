import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
OPS_DIRECTORY = REPOSITORY_ROOT / "scripts" / "ops"
BACKUP_SCRIPT = OPS_DIRECTORY / "backup_postgres.sh"
RESTORE_SCRIPT = OPS_DIRECTORY / "restore_postgres.sh"
CHECK_SCRIPT = OPS_DIRECTORY / "check_backup_restore.sh"
RUNBOOK = REPOSITORY_ROOT / "docs" / "ops" / "BACKUP_RESTORE.md"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_tools(tmp_path: Path) -> tuple[dict[str, str], Path]:
    binary_directory = tmp_path / "bin"
    capture_directory = tmp_path / "capture"
    binary_directory.mkdir()
    capture_directory.mkdir()

    _write_executable(
        binary_directory / "pg_dump",
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

capture = Path(os.environ["CAPTURE_DIRECTORY"])
with (capture / "pg_dump.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if "--version" in sys.argv:
    print("pg_dump (PostgreSQL) 18.6")
    raise SystemExit(0)
if os.environ.get("FAKE_PG_DUMP_FAIL") == "1":
    raise SystemExit(42)
output = next(
    argument.removeprefix("--file=")
    for argument in sys.argv[1:]
    if argument.startswith("--file=")
)
Path(output).write_bytes(b"PGDMP\\x01fake-custom-archive")
""",
    )
    _write_executable(
        binary_directory / "pg_restore",
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

capture = Path(os.environ["CAPTURE_DIRECTORY"])
with (capture / "pg_restore.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if "--version" in sys.argv:
    print("pg_restore (PostgreSQL) 18.6")
    raise SystemExit(0)
if "--list" in sys.argv:
    archive = Path(sys.argv[-1])
    if not archive.read_bytes().startswith(b"PGDMP"):
        raise SystemExit(1)
    print("; Archive created by pg_dump 18.6")
    print("1; 0 0 TABLE public source_documents exam_guru")
""",
    )
    _write_executable(
        binary_directory / "psql",
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

capture = Path(os.environ["CAPTURE_DIRECTORY"])
with (capture / "psql.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
print(os.environ.get("FAKE_PSQL_STATE", "restore_target|0|0|0|0"))
""",
    )

    password_file = tmp_path / "pgpass"
    password_file.write_text("localhost:5432:*:operator:integration-only\n", encoding="utf-8")
    password_file.chmod(0o600)
    environment = {
        **os.environ,
        "CAPTURE_DIRECTORY": str(capture_directory),
        "PATH": f"{binary_directory}:{os.environ.get('PATH', os.defpath)}",
        "PG_DUMP_BIN": str(binary_directory / "pg_dump"),
        "PG_RESTORE_BIN": str(binary_directory / "pg_restore"),
        "PSQL_BIN": str(binary_directory / "psql"),
        "PGHOST": "localhost",
        "PGPORT": "5432",
        "PGUSER": "operator",
        "PGDATABASE": "restore_target",
        "PGPASSFILE": str(password_file),
    }
    environment.pop("PGPASSWORD", None)
    return environment, capture_directory


def _run(
    script: Path,
    *arguments: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed test-owned script and arguments
        ["/bin/bash", str(script), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _captured_arguments(capture_directory: Path, tool: str) -> list[list[str]]:
    path = capture_directory / f"{tool}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _create_backup(
    tmp_path: Path,
    environment: dict[str, str],
) -> Path:
    destination = tmp_path / "evidence-backup"
    completed = _run(
        BACKUP_SCRIPT,
        "--destination",
        str(destination),
        environment=environment,
    )
    assert completed.returncode == 0, completed.stderr
    return destination


def test_backup_requires_an_explicit_new_destination_and_never_exposes_passwords(
    tmp_path: Path,
) -> None:
    environment, capture = _fake_tools(tmp_path)
    password_sentinel = "sensitive-restore-value"  # pragma: allowlist secret
    environment["PGPASSWORD"] = password_sentinel

    missing = _run(BACKUP_SCRIPT, environment=environment)
    rejected_password = _run(
        BACKUP_SCRIPT,
        "--destination",
        str(tmp_path / "backup"),
        environment=environment,
    )

    assert missing.returncode != 0
    assert rejected_password.returncode != 0
    assert password_sentinel not in missing.stdout + missing.stderr
    assert password_sentinel not in rejected_password.stdout + rejected_password.stderr
    assert _captured_arguments(capture, "pg_dump") == []


def test_backup_does_not_echo_a_rejected_credential_bearing_argument(tmp_path: Path) -> None:
    environment, capture = _fake_tools(tmp_path)
    password_sentinel = "argument-secret-sentinel"  # pragma: allowlist secret

    completed = _run(
        BACKUP_SCRIPT,
        f"postgresql://operator:{password_sentinel}@localhost/example",
        environment=environment,
    )

    assert completed.returncode != 0
    assert password_sentinel not in completed.stdout + completed.stderr
    assert _captured_arguments(capture, "pg_dump") == []


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PGCONNECT_TIMEOUT", "0"),
        ("PGCONNECT_TIMEOUT", "not-a-number"),
        ("PG_DUMP_LOCK_WAIT_TIMEOUT_MS", "0"),
        ("PG_DUMP_LOCK_WAIT_TIMEOUT_MS", "300001"),
    ],
)
def test_backup_rejects_unbounded_connection_and_lock_wait_configuration(
    tmp_path: Path,
    variable: str,
    value: str,
) -> None:
    environment, capture = _fake_tools(tmp_path)
    environment[variable] = value

    completed = _run(
        BACKUP_SCRIPT,
        "--destination",
        str(tmp_path / "backup"),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "bounded" in completed.stderr.casefold()
    assert not any(
        "--format=custom" in arguments for arguments in _captured_arguments(capture, "pg_dump")
    )


def test_backup_is_custom_format_atomic_and_has_a_closed_checksum_manifest(
    tmp_path: Path,
) -> None:
    environment, capture = _fake_tools(tmp_path)
    destination = _create_backup(tmp_path, environment)

    expected_files = {
        "SHA256SUMS",
        "backup-metadata.txt",
        "database.dump",
        "database.dump.list",
    }
    assert {item.name for item in destination.iterdir()} == expected_files
    manifest_lines = (destination / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(manifest_lines) == 3
    for line in manifest_lines:
        digest, relative_name = line.split("  ", maxsplit=1)
        assert relative_name in expected_files - {"SHA256SUMS"}
        assert digest == hashlib.sha256((destination / relative_name).read_bytes()).hexdigest()
    assert all(
        not (item.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)) for item in destination.iterdir()
    )
    assert list(tmp_path.glob(".evidence-backup.tmp.*")) == []
    metadata = (destination / "backup-metadata.txt").read_text(encoding="utf-8")
    assert "pg_dump_no_owner_option=true" in metadata
    assert "pg_dump_no_acl_option=true" in metadata
    assert "required_restore_no_owner=true" in metadata
    assert "required_restore_no_acl=true" in metadata
    assert "owner_statements_included=false" not in metadata

    dump_calls = _captured_arguments(capture, "pg_dump")
    archive_call = next(arguments for arguments in dump_calls if "--version" not in arguments)
    assert "--format=custom" in archive_call
    assert "--no-owner" in archive_call
    assert "--no-acl" in archive_call
    assert any(argument.startswith("--file=") for argument in archive_call)
    flattened = " ".join(argument for call in dump_calls for argument in call)
    assert "integration-only" not in flattened


def test_backup_failure_leaves_neither_final_nor_partial_destination(tmp_path: Path) -> None:
    environment, _capture = _fake_tools(tmp_path)
    environment["FAKE_PG_DUMP_FAIL"] = "1"
    destination = tmp_path / "failed-backup"

    completed = _run(
        BACKUP_SCRIPT,
        "--destination",
        str(destination),
        environment=environment,
    )

    assert completed.returncode == 42
    assert not destination.exists()
    assert list(tmp_path.glob(".failed-backup.tmp.*")) == []


def test_restore_defaults_to_offline_verification_without_connecting_to_target(
    tmp_path: Path,
) -> None:
    environment, capture = _fake_tools(tmp_path)
    backup = _create_backup(tmp_path, environment)
    (capture / "pg_restore.jsonl").unlink()

    completed = _run(
        RESTORE_SCRIPT,
        "--backup-dir",
        str(backup),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run" in completed.stdout.casefold()
    assert _captured_arguments(capture, "psql") == []
    restore_calls = _captured_arguments(capture, "pg_restore")
    assert len(restore_calls) == 1
    assert "--list" in restore_calls[0]
    assert not any(argument.startswith("--dbname") for argument in restore_calls[0])


def test_restore_rejects_password_environment_without_logging_its_value(tmp_path: Path) -> None:
    environment, capture = _fake_tools(tmp_path)
    backup = _create_backup(tmp_path, environment)
    password_sentinel = "restore-secret-sentinel"  # pragma: allowlist secret
    environment["PGPASSWORD"] = password_sentinel

    completed = _run(
        RESTORE_SCRIPT,
        "--backup-dir",
        str(backup),
        environment=environment,
    )

    assert completed.returncode != 0
    assert password_sentinel not in completed.stdout + completed.stderr
    assert _captured_arguments(capture, "psql") == []


def test_restore_rejects_backup_files_with_group_or_other_permissions(tmp_path: Path) -> None:
    environment, capture = _fake_tools(tmp_path)
    backup = _create_backup(tmp_path, environment)
    (backup / "database.dump").chmod(0o640)

    completed = _run(
        RESTORE_SCRIPT,
        "--backup-dir",
        str(backup),
        environment=environment,
    )

    assert completed.returncode != 0
    assert "permissions" in completed.stderr.casefold()
    assert _captured_arguments(capture, "psql") == []


@pytest.mark.parametrize(
    ("confirmation", "target_state", "expected_error"),
    [
        ("RESTORE:wrong_target", "restore_target|0|0|0|0", "confirmation"),
        ("RESTORE:restore_target", "restore_target|1|0|0|0", "not empty"),
        ("RESTORE:restore_target", "other_target|0|0|0|0", "identity"),
        ("RESTORE:restore_target", "restore_target|0|1|0|0", "extension"),
        ("RESTORE:restore_target", "restore_target|0|0|1|0", "session"),
        ("RESTORE:restore_target", "restore_target|0|0|0|1", "object"),
    ],
)
def test_restore_execute_fails_closed_before_restore_when_guard_is_not_satisfied(
    tmp_path: Path,
    confirmation: str,
    target_state: str,
    expected_error: str,
) -> None:
    environment, capture = _fake_tools(tmp_path)
    backup = _create_backup(tmp_path, environment)
    (capture / "pg_restore.jsonl").unlink()
    environment["FAKE_PSQL_STATE"] = target_state

    completed = _run(
        RESTORE_SCRIPT,
        "--backup-dir",
        str(backup),
        "--execute",
        "--confirm-empty-target",
        confirmation,
        environment=environment,
    )

    assert completed.returncode != 0
    assert expected_error in completed.stderr.casefold()
    assert not any(
        any(argument.startswith("--dbname") for argument in arguments)
        for arguments in _captured_arguments(capture, "pg_restore")
    )


def test_restore_requires_exact_target_confirmation_then_uses_safe_restore_flags(
    tmp_path: Path,
) -> None:
    environment, capture = _fake_tools(tmp_path)
    backup = _create_backup(tmp_path, environment)
    (capture / "pg_restore.jsonl").unlink()

    completed = _run(
        RESTORE_SCRIPT,
        "--backup-dir",
        str(backup),
        "--execute",
        "--confirm-empty-target",
        "RESTORE:restore_target",
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "completed" in completed.stdout.casefold()
    restore_calls = _captured_arguments(capture, "pg_restore")
    database_call = next(
        arguments
        for arguments in restore_calls
        if any(argument.startswith("--dbname") for argument in arguments)
    )
    assert "--exit-on-error" in database_call
    assert "--single-transaction" in database_call
    assert "--no-owner" in database_call
    assert "--no-acl" in database_call
    assert "--dbname=restore_target" in database_call
    assert "integration-only" not in " ".join(database_call)


def test_runbook_and_static_check_cover_the_source_verified_recovery_contract() -> None:
    assert CHECK_SCRIPT.is_file()
    text = RUNBOOK.read_text(encoding="utf-8")

    required_evidence = (
        "PostgreSQL 18",
        "pgvector",
        "source_documents",
        "knowledge_embeddings",
        "generation_runs",
        "validation_runs",
        "question_candidates",
        "published_paper_versions",
        "admin_audit_events",
        "sources/<sha256-prefix>/<sha256>.pdf",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "pg_dumpall --roles-only --no-role-passwords",
        "object versioning",
        "mc mirror",
        "write freeze",
        "isolated",
        "alembic",
        "readiness",
        "paper_canonical_jsonb",
        "key rotation",
        "failure rollback",
        "RPO",
        "RTO",
        "deployment decision",
        "https://www.postgresql.org/docs/18/app-pgdump.html",
        "apps/api/migrations/versions/0016_published_papers.py",
        "apps/api/src/exam_guru_api/infrastructure/object_storage.py",
    )
    for evidence in required_evidence:
        assert evidence in text
