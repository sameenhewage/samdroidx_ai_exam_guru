"""Private, opt-in 40-page coverage pilot, never a corpus-wide accuracy estimate.

Prepare public model files in a separate step, then run inference offline in a
resource-limited worker-image container with read-only corpus/models/source
mounts. No database, HTTP upload, source confirmation, or embedding is performed.
Raw outputs (including failures) stay in the private output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath
from tempfile import gettempdir
from typing import Any, BinaryIO, cast

from audit_source_fidelity import (
    QUALITY_STATUS,
    JsonRecord,
    assessment_metadata,
    candidate_metadata,
    json_line,
    prepare_output,
    private_directory,
    private_text_file,
    runtime_metadata,
    sha256_file,
    utc_now,
    write_json,
)
from exam_guru_api.documents.fidelity import assess_page
from exam_guru_api.documents.page_reading import (
    FilePageReader,
    PageReadingConfiguration,
    extract_native_page,
)
from exam_guru_api.documents.tesseract_ocr import (
    CommandResult,
    CommandRunner,
    RenderedPageImage,
    TesseractCliOCRAdapter,
    TesseractInputError,
    TesseractMalformedOutputError,
    TesseractOCRConfig,
    TesseractOutputLimitError,
    TesseractProcessError,
    TesseractTimeoutError,
    TesseractUnavailableError,
    open_pdf_file,
)

EXACT_SELECTION = {
    "e694977b": [1, 2, 3],
    "6d0e43eb": [1, 2, 3, 4],
    "4508d836": [30, 144, 260, 277],
    "f3149358": [1, 3, 5],
    "a5678c45": [1, 4, 7],
    "26574170": [1, 4, 186, 371],
    "a7a158b9": [265],
    "cddcc8ad": [3],
    "086ec023": [6],
    "f905aaa0": [2],
    "ad2fabbd": [1],
    "2cf6f60a": [1],
    "869c1231": [1],
    "5076eca3": [1],
    "5bf7d8e7": [1],
    "18072688": [1],
    "be12288c": [12],
    "01c7c4d8": [37],
    "2ef95994": [89],
    "d5b5492d": [459],
    "3de83881": [117],
    "0b6c7f78": [1],
    "284b82a5": [1],
    "cdecd006": [33],
    "55d1ff77": [1],
}
BEST_SOURCE_COMMIT = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
BEST_MODEL_SHA256 = {
    "sin": "1d95b36e935c8c12cc890684fad15bd3ad8d904c3a6ffa2fccb81658eabd8454",
    "eng": "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
    "tam": "4b9ce85987f629dd31eaf87443a1646452a43cdf91fcf05e017382ad595dcb9e",
}
GUIDE_SHA256 = "265741707fc6944913df8ee4081d948ee19a4aee20678e9589e2f8130f578cdd"
FAST_PROFILE = "worker_fast_sin_eng"
BEST_PROFILE = "best_sin_same_fast_eng"
TRILINGUAL_PROFILE = "best_sin_tam_eng"
CURRENT_PROFILE = "current_file_reader_forced_ocr"
PAGE_DEADLINE_SECONDS = 90.0


def _validate_alias(alias: object) -> str:
    if (
        not isinstance(alias, str)
        or not alias
        or "\\" in alias
        or "\x00" in alias
        or PurePosixPath(alias).is_absolute()
        or any(part in {".", "..", ""} for part in alias.split("/"))
    ):
        raise ValueError("invalid source alias")
    return alias


def resolve_selection(
    inventory: JsonRecord, proposal: Mapping[str, Sequence[int]] | None = None
) -> list[JsonRecord]:
    proposal = EXACT_SELECTION if proposal is None else proposal
    documents = inventory["documents"]
    selected: list[JsonRecord] = []
    used: set[tuple[str, int]] = set()
    for prefix, numbers in proposal.items():
        if re.fullmatch(r"[0-9a-f]{8,64}", prefix) is None:
            raise ValueError("selection requires a unique hexadecimal checksum prefix")
        matches = [item for item in documents if item["sha256"].startswith(prefix)]
        checksums = {item["sha256"] for item in matches}
        if len(checksums) != 1:
            raise ValueError("selection requires unique full checksum resolution")
        checksum = next(iter(checksums))
        if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise ValueError("selection requires a unique full SHA-256 checksum")
        if any(item.get("error") for item in matches):
            raise ValueError("selected original is unreadable")
        first = matches[0]
        if any(
            (item["page_count"], item["size_bytes"]) != (first["page_count"], first["size_bytes"])
            for item in matches
        ):
            raise ValueError("inconsistent metadata for the same full checksum")
        aliases = sorted({_validate_alias(alias) for item in matches for alias in item["aliases"]})
        if not aliases:
            raise ValueError("selected original requires source aliases")
        if not numbers:
            raise ValueError("explicit page selection is required")
        for number in numbers:
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or not 1 <= number <= first["page_count"]
                or (checksum, number) in used
            ):
                raise ValueError("invalid or duplicate selected page")
            used.add((checksum, number))
            selected.append(
                {
                    "checksum_prefix": prefix,
                    "source_sha256": checksum,
                    "source_size_bytes": first["size_bytes"],
                    "source_page_count": first["page_count"],
                    "page_number": number,
                    "page_key": f"{checksum}-p{number:06d}",
                    "aliases": aliases,
                    "primary_relative_path": aliases[0],
                    "storage_grade": candidate_metadata(aliases[0])["grade"]["candidate_value"],
                    "metadata_verified": False,
                }
            )
    return selected


def build_profiles(installed: Path, mixed: Path, best: Path) -> dict[str, TesseractOCRConfig]:
    common = TesseractOCRConfig(
        language="sin+eng",
        allowed_languages=("sin", "tam", "eng"),
        dpi=300,
        page_segmentation_mode=3,
        timeout_seconds=60.0,
        max_pages=1,
        batch_size=1,
        max_pixels_per_page=40_000_000,
        max_command_output_bytes=8 * 1024 * 1024,
        tessdata_directory=installed,
        model_family="fast",
    )
    return {
        FAST_PROFILE: common,
        BEST_PROFILE: replace(common, tessdata_directory=mixed, model_family="custom"),
        TRILINGUAL_PROFILE: replace(
            common, language="sin+tam+eng", tessdata_directory=best, model_family="best"
        ),
        CURRENT_PROFILE: common,
    }


def verify_model_hashes(directory: Path, expected: Mapping[str, str]) -> JsonRecord:
    results: JsonRecord = {}
    for language, checksum in expected.items():
        path = directory / f"{language}.traineddata"
        actual = sha256_file(path)
        if actual != checksum:
            raise ValueError(f"traineddata checksum mismatch: {language}")
        results[language] = {"sha256": actual, "size_bytes": path.stat().st_size, "path": str(path)}
    return results


def _private_binary_file(path: Path) -> BinaryIO:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
    )
    return os.fdopen(descriptor, "wb")


def copy_private(source: Path, target: Path) -> None:
    if target.exists():
        if sha256_file(source) != sha256_file(target):
            raise ValueError("existing private artifact has a different checksum")
        return
    with source.open("rb") as input_file, _private_binary_file(target) as output:
        shutil.copyfileobj(input_file, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def persist_render(image: RenderedPageImage, target: Path) -> JsonRecord:
    if sha256_file(image.path) != image.sha256:
        raise ValueError("render checksum differs from its provenance")
    if target.exists() and sha256_file(target) != image.sha256:
        raise ValueError("comparison page images must be byte-identical")
    copy_private(image.path, target)
    return {**image.metadata(), "artifact": str(target)}


class EvidenceCommandRunner:
    """Execute argv-only with file-backed, bounded raw stdout/stderr retention.

    RLIMIT_FSIZE bounds each stream even between polling ticks. The combined
    budget is checked too. Unlike the production in-memory collector, this
    benchmark recorder retains partial output on timeout/error for adjudication.
    It uses the same minimal environment as the current production CLI runner.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = private_directory(directory)
        self.sequence = 0

    def __call__(
        self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float, max_output_bytes: int
    ) -> CommandResult:
        self.sequence += 1
        stem = f"command-{self.sequence:03d}"
        stdout_path = self.directory / f"{stem}.stdout"
        stderr_path = self.directory / f"{stem}.stderr"
        metadata: JsonRecord = {
            "argv": list(argv),
            "cwd": str(cwd),
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
            "environment": {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            "started_at": utc_now(),
            "returncode": None,
            "status": "not_started",
        }
        start = time.monotonic()
        timed_out = False
        exceeded = False
        process: subprocess.Popen[bytes] | None = None
        unavailable: OSError | None = None

        def limit_output() -> None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes))

        with (
            _private_binary_file(stdout_path) as stdout,
            _private_binary_file(stderr_path) as stderr,
        ):
            try:
                process = subprocess.Popen(  # noqa: S603
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=cwd,
                    shell=False,
                    env=metadata["environment"],
                    close_fds=True,
                    start_new_session=True,
                    preexec_fn=limit_output,
                )
                while process.poll() is None:
                    if stdout_path.stat().st_size + stderr_path.stat().st_size >= max_output_bytes:
                        exceeded = True
                        break
                    if time.monotonic() - start >= timeout_seconds:
                        timed_out = True
                        break
                    time.sleep(0.01)
            except OSError as error:
                unavailable = error
            finally:
                if process is not None:
                    if process.poll() is None:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    metadata["returncode"] = process.returncode
        size = stdout_path.stat().st_size + stderr_path.stat().st_size
        exceeded = exceeded or size >= max_output_bytes or metadata["returncode"] == -signal.SIGXFSZ
        metadata["status"] = (
            "unavailable"
            if unavailable
            else "output_limit"
            if exceeded
            else "timeout"
            if timed_out
            else "process_error"
            if metadata["returncode"]
            else "success"
        )
        metadata.update(
            elapsed_seconds=time.monotonic() - start,
            stdout={
                "path": stdout_path.name,
                "sha256": sha256_file(stdout_path),
                "size_bytes": stdout_path.stat().st_size,
            },
            stderr={
                "path": stderr_path.name,
                "sha256": sha256_file(stderr_path),
                "size_bytes": stderr_path.stat().st_size,
            },
        )
        write_json(self.directory / f"{stem}.json", metadata)
        if unavailable:
            raise unavailable
        if timed_out:
            raise subprocess.TimeoutExpired(argv, timeout_seconds)
        with stdout_path.open("rb") as stdout, stderr_path.open("rb") as stderr:
            return CommandResult(
                int(metadata["returncode"]),
                stdout.read(max_output_bytes),
                stderr.read(max_output_bytes),
                output_limit_exceeded=exceeded,
            )


def stage_output_configuration(installed: Path, targets: Sequence[Path]) -> JsonRecord:
    source = installed / "configs" / "tsv"
    checksum = sha256_file(source)
    for directory in targets:
        copy_private(source, private_directory(directory / "configs") / "tsv")
    verify_output_configuration((installed, *targets), checksum)
    return {"relative_path": "configs/tsv", "sha256": checksum, "source": str(source)}


def verify_output_configuration(directories: Sequence[Path], expected: str) -> None:
    for directory in directories:
        if sha256_file(directory / "configs" / "tsv") != expected:
            raise ValueError("Tesseract output configuration differs between model profiles")


def prepare_models(models_root: Path, installed: Path, best: Path) -> JsonRecord:
    models_root = private_directory(models_root)
    verified_best = verify_model_hashes(best, BEST_MODEL_SHA256)
    if not (best / "LICENSE").is_file():
        raise ValueError("official best model LICENSE is required")
    runner = EvidenceCommandRunner(models_root / "installed-probe-commands")
    probe = TesseractCliOCRAdapter(
        config=build_profiles(installed, models_root / "best-sin-fast-eng", best)[FAST_PROFILE],
        command_runner=runner,
    ).probe(temporary_directory=models_root, hash_traineddata=True, check_selected_languages=False)
    if probe.engine_version != "5.3.0":
        raise ValueError("worker Tesseract version differs from the requested 5.3.0 baseline")
    installed_hashes = dict(probe.traineddata_sha256)
    if not {"sin", "eng"} <= installed_hashes.keys():
        raise ValueError("installed worker must provide Sinhala and English")
    verified_installed = verify_model_hashes(installed, installed_hashes)
    snapshot = private_directory(models_root / "installed-fast")
    mixed = private_directory(models_root / "best-sin-fast-eng")
    for language in ("sin", "eng"):
        copy_private(installed / f"{language}.traineddata", snapshot / f"{language}.traineddata")
    copy_private(best / "sin.traineddata", mixed / "sin.traineddata")
    copy_private(installed / "eng.traineddata", mixed / "eng.traineddata")
    verify_model_hashes(mixed, {"sin": BEST_MODEL_SHA256["sin"], "eng": installed_hashes["eng"]})
    best_runtime = private_directory(models_root / "all-best")
    for language in BEST_MODEL_SHA256:
        copy_private(best / f"{language}.traineddata", best_runtime / f"{language}.traineddata")
    copy_private(best / "LICENSE", best_runtime / "LICENSE")
    output_configuration = stage_output_configuration(installed, (snapshot, mixed, best_runtime))
    verified_best = verify_model_hashes(best_runtime, BEST_MODEL_SHA256)
    licenses = {}
    for package in ("tesseract-ocr", "tesseract-ocr-sin", "tesseract-ocr-eng"):
        source = Path("/usr/share/doc") / package / "copyright"
        if source.is_file():
            target = snapshot / f"{package}-copyright"
            copy_private(source, target)
            licenses[package] = {"path": str(target), "sha256": sha256_file(target)}
    metadata = {
        "created_at": utc_now(),
        "engine_version": probe.engine_version,
        "available_worker_languages": list(probe.available_languages),
        "missing_worker_languages_for_trilingual": [
            language
            for language in ("sin", "tam", "eng")
            if language not in probe.available_languages
        ],
        "worker_tamil_support": "available" if "tam" in probe.available_languages else "missing",
        "installed_directory": str(installed),
        "installed_models": verified_installed,
        "installed_snapshot_directory": str(snapshot),
        "installed_copyright": licenses,
        "best_directory": str(best_runtime),
        "best_source_directory": str(best),
        "output_configuration": output_configuration,
        "best_source_repository": "https://github.com/tesseract-ocr/tessdata_best.git",
        "best_source_commit": BEST_SOURCE_COMMIT,
        "best_models": verified_best,
        "best_license": {"path": str(best / "LICENSE"), "sha256": sha256_file(best / "LICENSE")},
        "mixed_directory": str(mixed),
        "mixed_profile_models": {"sin": "official_best", "eng": "same_worker_installed_fast"},
        "human_adjudicated_references": 0,
    }
    write_json(models_root / "model-provenance.json", metadata)
    return metadata


def _error_status(error: Exception) -> str:
    for kind, status in (
        (TesseractTimeoutError, "timeout"),
        (TesseractInputError, "input_rejected"),
        (TesseractMalformedOutputError, "invalid_output"),
        (TesseractOutputLimitError, "output_limit"),
        (TesseractProcessError, "process_error"),
    ):
        if isinstance(error, kind):
            return status
    if isinstance(error, TesseractUnavailableError):
        return "missing_languages" if error.missing_languages else "unavailable"
    return "error"


def _error_metadata(error: Exception) -> JsonRecord:
    result: JsonRecord = {"type": type(error).__name__}
    for field in ("missing_languages", "operation", "timeout_seconds", "returncode", "violation"):
        if hasattr(error, field):
            value = getattr(error, field)
            result[field] = list(value) if isinstance(value, tuple) else value
    return result


def save_text(path: Path, text: str) -> JsonRecord:
    content = text.encode("utf-8", errors="surrogatepass")
    with _private_binary_file(path) as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    return {
        "artifact": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "characters": len(text),
        "size_bytes": len(content),
        "encoding": "utf-8/surrogatepass",
    }


def execute_fixed_page(
    path: Path,
    selected: JsonRecord,
    config: TesseractOCRConfig,
    output: Path,
    image_path: Path,
    *,
    command_runner: CommandRunner | None = None,
    temporary_directory: Path | None = None,
) -> JsonRecord:
    output = private_directory(output)
    scratch = private_directory(temporary_directory) if temporary_directory else output
    result: JsonRecord = {
        "status": "not_started",
        "source_sha256": selected["source_sha256"],
        "page_number": selected["page_number"],
        "error": None,
        "page_image": None,
        "started_at": utc_now(),
        "automatic_verification": False,
    }
    start = time.monotonic()
    text = ""

    def rendered(image: RenderedPageImage) -> None:
        result["page_image"] = persist_render(image, image_path)

    try:
        adapter = TesseractCliOCRAdapter(
            config=config,
            command_runner=command_runner or EvidenceCommandRunner(output / "commands"),
            execution_deadline=start + PAGE_DEADLINE_SECONDS,
        )
        with path.open("rb") as source:
            extracted = adapter.extract_file(
                source,
                page_numbers=(selected["page_number"],),
                source_checksum_sha256=selected["source_sha256"],
                temporary_directory=scratch,
                on_render=rendered,
            )
        text = extracted.pages[0].text
        result.update(
            status="success",
            engine=extracted.engine,
            engine_version=extracted.engine_version,
            config=dict(extracted.config),
            confidence=extracted.pages[0].confidence,
            blocks=[asdict(block) for block in extracted.pages[0].blocks],
        )
        # Raw blocks are private artifacts, not public operational summary fields.
        write_json(output / "ocr-blocks.json", result.pop("blocks"))
        result["assessment"] = assessment_metadata(assess_page(text, method="ocr"))
    except Exception as error:
        result.update(status=_error_status(error), error=_error_metadata(error))
    artifact = save_text(output / "candidate.txt", text)
    result.update(
        candidate=artifact,
        candidate_sha256=artifact["sha256"],
        elapsed_seconds=time.monotonic() - start,
    )
    return result


def execute_current_reader(
    path: Path, selected: JsonRecord, config: TesseractOCRConfig, output: Path, image_path: Path
) -> JsonRecord:
    output = private_directory(output)
    result: JsonRecord = {
        "status": "not_started",
        "source_sha256": selected["source_sha256"],
        "page_number": selected["page_number"],
        "page_image": None,
        "error": None,
        "started_at": utc_now(),
        "automatic_verification": False,
        "force_ocr": True,
        "expected_languages": [],
    }
    start = time.monotonic()

    def rendered(image: RenderedPageImage) -> None:
        result["page_image"] = persist_render(image, image_path)

    try:
        reader = FilePageReader(
            command_runner=EvidenceCommandRunner(output / "commands"),
            on_render=rendered,
            execution_deadline=start + PAGE_DEADLINE_SECONDS,
        )
        with (
            path.open("rb") as source,
            reader.open(
                source, configuration=PageReadingConfiguration(force_ocr=True, ocr=config)
            ) as session,
        ):
            reading = session.read_page(selected["page_number"])
        candidates = []
        for index, candidate in enumerate(reading.candidates):
            artifact = save_text(
                output / f"candidate-{index}-{candidate.method}.txt", candidate.raw_text
            )
            candidates.append(
                {
                    "method": candidate.method,
                    "provenance": candidate.provenance,
                    "artifact": artifact,
                }
            )
        result["candidates"] = candidates
        result["failure_code"] = reading.failure_code
        result["status"] = {
            "ocr_languages_unavailable": "missing_languages",
            "ocr_timeout": "timeout",
            "ocr_input_rejected": "input_rejected",
            "ocr_output_invalid": "invalid_output",
            "ocr_output_limit": "output_limit",
            "ocr_process_failed": "process_error",
        }.get(reading.failure_code or "", "error" if reading.failure_code else "success")
        if reading.failure_code:
            result["error"] = {
                "failure_code": reading.failure_code,
                "missing_languages": [
                    language
                    for candidate in reading.candidates
                    for language in cast(
                        list[str], candidate.provenance.get("missing_languages", [])
                    )
                ],
            }
    except Exception as error:
        result.update(status=_error_status(error), error=_error_metadata(error))
    result["elapsed_seconds"] = time.monotonic() - start
    return result


def operational_summary(results: list[JsonRecord], *, selected_pages: int) -> JsonRecord:
    grouped: dict[str, list[JsonRecord]] = defaultdict(list)
    for result in results:
        grouped[result["profile"]].append(result)
    profiles = {}
    for profile, records in grouped.items():
        latencies = sorted(item["elapsed_seconds"] for item in records)
        profiles[profile] = {
            "attempts": len(records),
            "status_counts": dict(sorted(Counter(item["status"] for item in records).items())),
            "elapsed_seconds_total": sum(latencies),
            "median_seconds": statistics.median(latencies),
            "maximum_seconds": max(latencies),
            "candidate_characters_total": sum(
                item.get("candidate", {}).get("characters", 0)
                + sum(
                    candidate["artifact"]["characters"]
                    for candidate in item.get("candidates", [])
                    if candidate["method"] == "ocr"
                )
                for item in records
            ),
        }
    fast = {item["page_key"]: item for item in grouped[FAST_PROFILE]}
    best = {item["page_key"]: item for item in grouped[BEST_PROFILE]}
    successful = [
        key
        for key in fast.keys() & best.keys()
        if fast[key]["status"] == best[key]["status"] == "success"
    ]
    return {
        "selected_pages": selected_pages,
        "profiles": profiles,
        "fixed_comparison_attempts": len(fast) + len(best),
        "fixed_comparison_complete": len(fast) == len(best) == selected_pages,
        "paired_successes": len(successful),
        "paired_identical_outputs": sum(
            fast[key].get("candidate_sha256") == best[key].get("candidate_sha256")
            for key in successful
        ),
        "paired_different_outputs": sum(
            fast[key].get("candidate_sha256") != best[key].get("candidate_sha256")
            for key in successful
        ),
        "human_adjudicated_references": 0,
        "quality_status": QUALITY_STATUS,
        "cer": None,
        "wer": None,
        "corpus_accuracy_estimate": None,
        "scope": "40-page purposive coverage pilot; NOT a corpus-wide accuracy estimate",
        "accuracy_blocker": "No human ground truth; raw native/OCR text is not a reference.",
        "automatic_verification": False,
    }


def refresh_operational_summary(directory: Path, output: Path) -> JsonRecord:
    private_directory(output.parent)
    with (directory / "summary.json").open(encoding="utf-8") as source:
        previous = json.load(source)
    with (directory / "results.jsonl").open(encoding="utf-8") as source:
        results = [json.loads(line) for line in source]
    summary = {
        **previous,
        **operational_summary(results, selected_pages=previous["selected_pages"]),
        "source_summary_sha256": sha256_file(directory / "summary.json"),
        "source_results_sha256": sha256_file(directory / "results.jsonl"),
        "recomputed_at": utc_now(),
        "summary_note": "OCR character totals include the OCR member of current-reader results.",
    }
    write_json(output, summary)
    return summary


def _selection_page_metadata(
    path: Path, selected: list[JsonRecord]
) -> dict[tuple[str, int], JsonRecord]:
    keys = {(item["source_sha256"], item["page_number"]) for item in selected}
    metadata = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            page = json.loads(line)
            key = (page["source_sha256"], page["page_number"])
            if key in keys:
                if key in metadata or page.get("error"):
                    raise ValueError("duplicate or failed audit page metadata")
                metadata[key] = {
                    **page,
                    "audit_record_sha256": hashlib.sha256(line.encode()).hexdigest(),
                }
    if metadata.keys() != keys:
        raise ValueError("audit page metadata does not cover the exact selection")
    return metadata


def _source_path(root: Path, selected: JsonRecord) -> Path:
    relative = _validate_alias(selected["primary_relative_path"])
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or any(
        item.is_symlink() for item in (path, *path.parents)
    ):
        raise ValueError("source aliases must stay inside the read-only corpus")
    if (
        path.stat().st_size != selected["source_size_bytes"]
        or sha256_file(path) != selected["source_sha256"]
    ):
        raise ValueError("original source checksum or byte size changed after audit")
    return path


def _needs_tamil(selected: JsonRecord, page: JsonRecord) -> list[str]:
    reasons = []
    if "ta" in page["assessment"]["languages"]:
        reasons.append("current_resource_font_or_script_evidence")
    if "ta" in page["span_assessment"]["languages"]:
        reasons.append("used_span_font_or_script_evidence")
    if any("tamil" in alias.casefold() or "தமிழ்" in alias for alias in selected["aliases"]):
        reasons.append("unverified_filename_or_folder_candidate")
    return reasons


def snapshot_execution_sources(output: Path) -> JsonRecord:
    output = private_directory(output)
    modules = (
        "audit_source_fidelity",
        "exam_guru_api.documents.fidelity",
        "exam_guru_api.documents.page_reading",
        "exam_guru_api.documents.tesseract_ocr",
        "exam_guru_api.documents.ocr",
    )
    sources = [Path(__file__), *(Path(cast(str, sys.modules[name].__file__)) for name in modules)]
    snapshots = {}
    for source in sources:
        checksum = sha256_file(source)
        target = output / source.name
        copy_private(source, target)
        if sha256_file(target) != checksum:
            raise ValueError("executed source snapshot changed while copying")
        snapshots[source.name] = {"sha256": checksum, "artifact": str(target)}
    return snapshots


def run_benchmark(
    root: Path,
    audit_directory: Path,
    models_root: Path,
    output: Path,
    *,
    temporary_directory: Path,
) -> JsonRecord:
    output = prepare_output(root, output)
    if temporary_directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("temporary files must stay outside the read-only corpus")
    scratch = private_directory(temporary_directory)
    if Path(gettempdir()).resolve() != scratch.resolve():
        raise ValueError("TMPDIR must match the explicit bounded scratch directory")
    with (audit_directory / "inventory.json").open(encoding="utf-8") as source:
        inventory = json.load(source)
    if not inventory["summary"]["originals_unchanged"] or inventory["summary"]["page_errors"]:
        raise ValueError("benchmark requires an unchanged, complete original corpus audit")
    selected = resolve_selection(inventory)
    grades = dict(Counter(str(item["storage_grade"]) for item in selected))
    if len(selected) != 40 or len({item["source_sha256"] for item in selected}) != 25:
        raise ValueError("exact pilot must contain 40 pages from 25 originals")
    if grades != {"3": 9, "4": 9, "5": 22}:
        raise ValueError(f"exact pilot storage-grade counts differ: {grades}")
    guide = next(item for item in selected if item["source_sha256"] == GUIDE_SHA256)
    if (guide["source_size_bytes"], guide["source_page_count"]) != (169816530, 371):
        raise ValueError("the 371-page guide checksum-bound metadata differs")
    pages = _selection_page_metadata(audit_directory / "pages.jsonl", selected)
    with (models_root / "model-provenance.json").open(encoding="utf-8") as source:
        models = json.load(source)
    installed, mixed, best = (
        Path(models[key]) for key in ("installed_directory", "mixed_directory", "best_directory")
    )
    installed_hashes = {
        language: item["sha256"] for language, item in models["installed_models"].items()
    }
    verify_model_hashes(installed, installed_hashes)
    verify_model_hashes(best, BEST_MODEL_SHA256)
    verify_model_hashes(mixed, {"sin": BEST_MODEL_SHA256["sin"], "eng": installed_hashes["eng"]})
    verify_output_configuration((installed, mixed, best), models["output_configuration"]["sha256"])
    profiles = build_profiles(installed, mixed, best)
    runtime = runtime_metadata()
    start = time.monotonic()
    runtime.update(
        started_at=utc_now(),
        benchmark_script_sha256=sha256_file(Path(__file__)),
        inventory_sha256=sha256_file(audit_directory / "inventory.json"),
        audit_pages_sha256=sha256_file(audit_directory / "pages.jsonl"),
        model_provenance_sha256=sha256_file(models_root / "model-provenance.json"),
        page_deadline_seconds=PAGE_DEADLINE_SECONDS,
        temporary_directory=str(scratch),
        temporary_filesystem_capacity_bytes=(
            os.statvfs(scratch).f_blocks * os.statvfs(scratch).f_frsize
        ),
        execution_source_snapshots=snapshot_execution_sources(output / "execution-sources"),
        profile_configs={
            name: {**asdict(config), "tessdata_directory": str(config.tessdata_directory)}
            for name, config in profiles.items()
        },
        comparison_notes={
            FAST_PROFILE: (
                "Current worker installed Sinhala+English, forced fixed-language ablation "
                "on all 40 pages; NOT Tamil-capable."
            ),
            BEST_PROFILE: (
                "Only Sinhala model changed; byte-identical installed English model, "
                "raster, PSM, language order and deadlines."
            ),
            CURRENT_PROFILE: (
                "Working-tree FilePageReader with force_ocr=True and no folder-derived "
                "expected languages; missing languages are failures."
            ),
            TRILINGUAL_PROFILE: (
                "Separate all-best sin+tam+eng profile only for Tamil signals; "
                "not a Sinhala-only model ablation."
            ),
        },
        models=models,
    )
    write_json(output / "runtime.json", runtime)
    write_json(
        output / "selection.json",
        {
            "proposal": EXACT_SELECTION,
            "pages": selected,
            "storage_grade_counts_unverified": grades,
            "human_adjudicated_references": 0,
            "scope": "purposive_coverage_pilot_not_corpus_accuracy",
        },
    )
    verified_sources = {item["source_sha256"]: _source_path(root, item) for item in selected}
    results: list[JsonRecord] = []
    with private_text_file(output / "results.jsonl") as journal:
        for selected_page in selected:
            checksum, number = selected_page["source_sha256"], selected_page["page_number"]
            path = verified_sources[checksum]
            directory = private_directory(output / "pages" / checksum / f"page-{number:06d}")
            image_path = directory / "page-300dpi.png"
            audited_page = pages[(checksum, number)]
            write_json(directory / "audit-page.json", audited_page)
            with path.open("rb") as source_stream, open_pdf_file(source_stream) as document:
                native = extract_native_page(document, number)
            native_artifact = save_text(directory / "native.txt", native.raw_text)
            if native_artifact["sha256"] != audited_page["native_text_sha256"]:
                raise ValueError("native extraction changed from the checksum-bound audit")
            write_json(
                directory / "native-provenance.json",
                {
                    "engine": "pymupdf",
                    "input_mode": "file",
                    "source_sha256": checksum,
                    "page_number": number,
                    "native": native_artifact,
                    "assessment": audited_page["assessment"],
                    "automatic_verification": False,
                },
            )
            tamil_reasons = _needs_tamil(selected_page, audited_page)
            profile_names = [FAST_PROFILE, BEST_PROFILE, CURRENT_PROFILE]
            if tamil_reasons:
                profile_names.append(TRILINGUAL_PROFILE)
            for profile in profile_names:
                if profile == CURRENT_PROFILE:
                    result = execute_current_reader(
                        path, selected_page, profiles[profile], directory / profile, image_path
                    )
                else:
                    result = execute_fixed_page(
                        path,
                        selected_page,
                        profiles[profile],
                        directory / profile,
                        image_path,
                        temporary_directory=scratch,
                    )
                result.update(
                    profile=profile,
                    page_key=selected_page["page_key"],
                    source_aliases=selected_page["aliases"],
                    quality_status=QUALITY_STATUS,
                    human_adjudicated_reference=False,
                    tamil_profile_reasons=tamil_reasons,
                )
                write_json(directory / profile / "result.json", result)
                json_line(journal, result)
                results.append(result)
    summary = operational_summary(results, selected_pages=len(selected))
    summary.update(
        selected_originals=len(verified_sources),
        storage_grade_counts_unverified=grades,
        elapsed_seconds=time.monotonic() - start,
        finished_at=utc_now(),
        source_code_unchanged=runtime["source_sha256"] == runtime_metadata()["source_sha256"],
        page_images=sum(
            (
                output
                / "pages"
                / item["source_sha256"]
                / f"page-{item['page_number']:06d}"
                / "page-300dpi.png"
            ).is_file()
            for item in selected
        ),
        selected_native_text_layers=sum(page["text_layer_present"] for page in pages.values()),
        selected_classification_counts=dict(
            Counter(
                value for page in pages.values() for value in page["assessment"]["classifications"]
            )
        ),
        selected_resource_risk_counts=dict(
            Counter(value for page in pages.values() for value in page["assessment"]["risk_codes"])
        ),
        selected_tamil_signal_pages=sum(
            bool(_needs_tamil(item, pages[(item["source_sha256"], item["page_number"])]))
            for item in selected
        ),
        originals_after={
            checksum: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for checksum, path in verified_sources.items()
        },
    )
    summary["selected_original_hashes_unchanged"] = all(
        checksum == metadata["sha256"] for checksum, metadata in summary["originals_after"].items()
    )
    verify_model_hashes(installed, installed_hashes)
    verify_model_hashes(best, BEST_MODEL_SHA256)
    verify_model_hashes(mixed, {"sin": BEST_MODEL_SHA256["sin"], "eng": installed_hashes["eng"]})
    verify_output_configuration((installed, mixed, best), models["output_configuration"]["sha256"])
    summary["model_hashes_unchanged"] = True
    write_json(output / "summary.json", summary)
    return summary


LIGHTON_BASE_COMMIT = "c97bd377f04481830395218fa8951df9deaba756"
LIGHTON_ADAPTER_COMMIT = "5256a3b9786f204b415f10205bb709968484b75f"
LIGHTON_WEIGHTS = {
    "lighton-base": {
        "model.safetensors": "cbe12d0831cca119facce91268fc7c5fb72babdf919a6e352e3131bda85c8fbb",
        "tokenizer.json": "f54b55fa0c3aba0c91ce09ea79ae4e62da24e2a1a630f96c4bae34aba25e234a",
    },
    "lighton-adapter": {
        "adapter_model.safetensors": (
            "ddd80839c8ddbce74e0225eded5a229c9a6874297d1091f252fc9a491a381db8"
        ),
        "tokenizer.json": "4ea9e138e0805971156081d68100cc439b1ce91024eb7f43ecfbd9b0dda858bd",
    },
}
LIGHTON_VERSIONS = {"transformers": "5.0.0", "peft": "0.18.1", "torch": "2.13.0+cpu"}
LIGHTON_SIX = (
    ("e694977b", 1),
    ("6d0e43eb", 2),
    ("4508d836", 260),
    ("f3149358", 3),
    ("26574170", 186),
    ("55d1ff77", 1),
)
LIGHTON_MAX_NEW_TOKENS = 2048


def verify_safetensor_directory(directory: Path, expected: Mapping[str, str]) -> JsonRecord:
    files = sorted(directory.iterdir())
    if not set(expected) <= {path.name for path in files}:
        raise ValueError("missing pinned safetensors model files")
    for path in files:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.suffix not in {".json", ".jinja", ".safetensors"}
            or (path.suffix == ".safetensors" and path.name not in expected)
        ):
            raise ValueError("unsafe or unpinned model file; pickle and remote code are prohibited")
    result = {
        path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for path in files
    }
    if any(result[name]["sha256"] != checksum for name, checksum in expected.items()):
        raise ValueError("pinned safetensors model checksum mismatch")
    return result


def apply_local_cpu_adapter(base: Any, adapter_loader: Any, directory: Path) -> Any:
    model = adapter_loader.from_pretrained(
        base, str(directory), is_trainable=False, local_files_only=True, use_safetensors=True
    )
    if not getattr(model, "peft_config", None) or not any(
        "lora_" in name for name, _ in model.named_parameters()
    ):
        raise ValueError("PEFT adapter was not applied")
    model.eval()
    return model


def decode_generated_only(processor: Any, output_ids: Any, input_length: int) -> str:
    if isinstance(input_length, bool) or not isinstance(input_length, int) or input_length < 0:
        raise ValueError("invalid input token count")
    result = processor.decode(output_ids[0][input_length:], skip_special_tokens=True)
    if not isinstance(result, str):
        raise ValueError("OCR decoder must return text")
    return result


def run_lighton_page(
    models_root: Path, image_path: Path, image_sha256: str, output: Path
) -> JsonRecord:
    output = private_directory(output)
    started = time.monotonic()
    record: JsonRecord = {
        "started_at": utc_now(),
        "status": "not_started",
        "device": "cpu",
        "dtype": "float32",
        "batch_size": 1,
        "longest_edge": 1540,
        "max_new_tokens": LIGHTON_MAX_NEW_TOKENS,
        "image_sha256": image_sha256,
        "human_adjudicated_references": 0,
        "quality_status": QUALITY_STATUS,
        "automatic_verification": False,
    }
    with private_text_file(output / "stages.jsonl") as stages:

        def stage(name: str, **values: object) -> None:
            json_line(
                stages,
                {
                    "stage": name,
                    "elapsed_seconds": time.monotonic() - started,
                    "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    **values,
                },
            )

        try:
            if sha256_file(image_path) != image_sha256:
                raise ValueError("selected page PNG checksum mismatch")
            record["models"] = {
                name: verify_safetensor_directory(models_root / name, expected)
                for name, expected in LIGHTON_WEIGHTS.items()
            }
            stage("model_files_verified")
            transformers = importlib.import_module("transformers")
            peft = importlib.import_module("peft")
            torch = importlib.import_module("torch")
            image_module: Any = importlib.import_module("PIL.Image")
            modules = {"transformers": transformers, "peft": peft, "torch": torch}
            record["versions"] = {name: str(module.__version__) for name, module in modules.items()}
            if record["versions"] != LIGHTON_VERSIONS or torch.version.cuda is not None:
                raise ValueError("exact stable CPU-only model environment is unavailable")
            torch.set_num_threads(2)
            torch.set_num_interop_threads(1)
            torch.manual_seed(0)
            stage("imports_verified", versions=record["versions"])
            processor = transformers.LightOnOcrProcessor.from_pretrained(
                str(models_root / "lighton-adapter"), local_files_only=True, trust_remote_code=False
            )
            processor.tokenizer.padding_side = "left"
            base = transformers.LightOnOcrForConditionalGeneration.from_pretrained(
                str(models_root / "lighton-base"),
                dtype=torch.float32,
                device_map={"": "cpu"},
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
            )
            stage("base_loaded")
            model = apply_local_cpu_adapter(base, peft.PeftModel, models_root / "lighton-adapter")
            lora_tensors = sum("lora_" in name for name, _ in model.named_parameters())
            devices = sorted({str(parameter.device) for parameter in model.parameters()})
            if devices != ["cpu"]:
                raise ValueError("non-CPU model device is prohibited")
            record.update(
                adapter_applied=True, lora_parameter_tensors=lora_tensors, parameter_devices=devices
            )
            stage("adapter_applied", lora_parameter_tensors=lora_tensors, parameter_devices=devices)
            image_module.MAX_IMAGE_PIXELS = 40_000_000
            with image_module.open(image_path) as original:
                if original.width * original.height > 40_000_000:
                    raise ValueError("input page raster exceeds its bound")
                image = original.convert("RGB")
                prompt = processor.apply_chat_template(
                    [{"role": "user", "content": [{"type": "image"}]}],
                    tokenize=False,
                    add_generation_prompt=True,
                )
                inputs = processor(
                    text=prompt, images=[image], return_tensors="pt", size={"longest_edge": 1540}
                )
                image.close()
            inputs = {
                key: value.to(device="cpu", dtype=torch.float32)
                if value.is_floating_point()
                else value.to("cpu")
                for key, value in inputs.items()
            }
            prompt_tokens = inputs["input_ids"].shape[1]
            record["input_token_count"] = prompt_tokens
            stage("preprocessed", input_token_count=prompt_tokens)
            with private_text_file(output / "partial-generated-token-ids.jsonl") as tokens:

                class GeneratedTokenRecorder:
                    first = True

                    def put(self, value: Any) -> None:
                        if self.first:
                            self.first = (
                                False  # HF streams prompt IDs first; never decode/store them.
                            )
                            return
                        json_line(tokens, value.tolist())

                    def end(self) -> None:
                        tokens.flush()

                stage("generation_started")
                with torch.inference_mode():
                    generated = model.generate(
                        **inputs,
                        max_new_tokens=LIGHTON_MAX_NEW_TOKENS,
                        do_sample=False,
                        use_cache=True,
                        streamer=GeneratedTokenRecorder(),
                    )
            generated_tokens = generated[0][prompt_tokens:]
            text = decode_generated_only(processor, generated, prompt_tokens)
            record["candidate"] = save_text(output / "candidate.txt", text)
            record["generated_token_count"] = len(generated_tokens)
            write_json(output / "generated-token-ids.json", generated_tokens.tolist())
            record["status"] = (
                "token_limit" if len(generated_tokens) >= LIGHTON_MAX_NEW_TOKENS else "success"
            )
            stage("generation_finished", generated_token_count=len(generated_tokens))
        except Exception as error:
            record.update(
                status="unavailable"
                if isinstance(error, (ImportError, ModuleNotFoundError))
                else "error",
                error={"type": type(error).__name__},
            )
            with private_text_file(output / "exception.txt") as diagnostic:
                diagnostic.write(traceback.format_exc())
            stage("failed", error_type=type(error).__name__)
    record.update(
        elapsed_seconds=time.monotonic() - started,
        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    write_json(output / "child-result.json", record)
    return record


def run_lighton_six(tesseract_directory: Path, models_root: Path, output: Path) -> JsonRecord:
    output = prepare_output(tesseract_directory, output)
    with (tesseract_directory / "summary.json").open(encoding="utf-8") as source:
        tesseract_summary = json.load(source)
    if not tesseract_summary["fixed_comparison_complete"]:
        raise ValueError("the whole 40-page Tesseract comparison must complete first")
    with (tesseract_directory / "selection.json").open(encoding="utf-8") as source:
        selection = json.load(source)["pages"]
    selected = []
    for prefix, number in LIGHTON_SIX:
        matches = [
            page
            for page in selection
            if page["source_sha256"].startswith(prefix) and page["page_number"] == number
        ]
        if len(matches) != 1:
            raise ValueError("LightOn feasibility selection requires unique checksum-bound pages")
        selected.append(matches[0])
    model_files = {
        name: verify_safetensor_directory(models_root / name, expected)
        for name, expected in LIGHTON_WEIGHTS.items()
    }
    write_json(
        output / "runtime.json",
        {
            "started_at": utc_now(),
            "base_commit": LIGHTON_BASE_COMMIT,
            "adapter_commit": LIGHTON_ADAPTER_COMMIT,
            "models": model_files,
            "required_versions": LIGHTON_VERSIONS,
            "source_snapshots": snapshot_execution_sources(output / "execution-sources"),
            "page_timeout_seconds": 180,
            "cpu_threads": 2,
            "device": "cpu",
            "dtype": "float32",
            "batch_size": 1,
            "max_new_tokens": LIGHTON_MAX_NEW_TOKENS,
            "inference_network": "disabled_by_required_container_configuration",
            "quality_status": QUALITY_STATUS,
            "human_adjudicated_references": 0,
            "published_paper_metrics_are_not_corpus_metrics": True,
        },
    )
    write_json(output / "selection.json", selected)
    results = []
    with private_text_file(output / "results.jsonl") as journal:
        for page in selected:
            directory = private_directory(output / "pages" / page["page_key"])
            image_path = (
                tesseract_directory
                / "pages"
                / page["source_sha256"]
                / f"page-{page['page_number']:06d}"
                / "page-300dpi.png"
            )
            image_hash = sha256_file(image_path)
            runner = EvidenceCommandRunner(directory / "commands")
            record: JsonRecord = {
                "source_sha256": page["source_sha256"],
                "page_number": page["page_number"],
                "page_key": page["page_key"],
                "image_sha256": image_hash,
                "image_artifact": str(image_path),
                "status": "not_started",
                "human_adjudicated_references": 0,
                "quality_status": QUALITY_STATUS,
            }
            started = time.monotonic()
            # Explicit allowlisted child environment: no inherited tokens or Studio configuration.
            command = (
                "/usr/bin/env",
                "-i",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "LANG=C.UTF-8",
                "LC_ALL=C.UTF-8",
                f"PYTHONPATH={Path(__file__).parent}:/src",
                f"HOME={gettempdir()}",
                f"TMPDIR={gettempdir()}",
                f"HF_HOME={gettempdir()}/hf",
                "HF_HUB_OFFLINE=1",
                "TRANSFORMERS_OFFLINE=1",
                "HF_HUB_DISABLE_TELEMETRY=1",
                "PYTHONDONTWRITEBYTECODE=1",
                "OMP_NUM_THREADS=2",
                "MKL_NUM_THREADS=2",
                "OPENBLAS_NUM_THREADS=2",
                sys.executable,
                str(Path(__file__)),
                "lighton-page",
                "--models-root",
                str(models_root),
                "--image",
                str(image_path),
                "--image-sha256",
                image_hash,
                "--output",
                str(directory),
            )
            try:
                command_result = runner(
                    command, cwd=directory, timeout_seconds=180.0, max_output_bytes=1024 * 1024
                )
                child_result = directory / "child-result.json"
                if child_result.is_file():
                    with child_result.open(encoding="utf-8") as source:
                        record.update(json.load(source))
                else:
                    record.update(status="process_error", returncode=command_result.returncode)
            except subprocess.TimeoutExpired:
                record.update(status="timeout", timeout_seconds=180)
            except Exception as error:
                record.update(status="error", error={"type": type(error).__name__})
            record["total_attempt_seconds"] = time.monotonic() - started
            record["partial_generated_tokens_artifact"] = (
                str(directory / "partial-generated-token-ids.jsonl")
                if (directory / "partial-generated-token-ids.jsonl").is_file()
                else None
            )
            write_json(directory / "result.json", record)
            json_line(journal, record)
            results.append(record)
    summary = {
        "pages_attempted": len(results),
        "status_counts": dict(Counter(item["status"] for item in results)),
        "page_timeout_seconds": 180,
        "max_attempt_seconds": max(item["total_attempt_seconds"] for item in results),
        "cpu_only": True,
        "batch_size": 1,
        "quality_status": QUALITY_STATUS,
        "human_adjudicated_references": 0,
        "cer": None,
        "wer": None,
        "corpus_accuracy_estimate": None,
        "automatic_verification": False,
        "scope": "six-page CPU feasibility only; not a quality or production-adoption decision",
    }
    write_json(output / "summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-models")
    prepare.add_argument("--models-root", required=True, type=Path)
    prepare.add_argument("--installed-directory", required=True, type=Path)
    prepare.add_argument("--best-directory", required=True, type=Path)
    benchmark = commands.add_parser("run")
    benchmark.add_argument("--corpus-root", required=True, type=Path)
    benchmark.add_argument("--audit-directory", required=True, type=Path)
    benchmark.add_argument("--models-root", required=True, type=Path)
    benchmark.add_argument("--output", required=True, type=Path)
    benchmark.add_argument("--temporary-directory", required=True, type=Path)
    summarize = commands.add_parser("summarize")
    summarize.add_argument("--benchmark-directory", required=True, type=Path)
    summarize.add_argument("--output", required=True, type=Path)
    feasibility = commands.add_parser("lighton-six")
    feasibility.add_argument("--tesseract-directory", required=True, type=Path)
    feasibility.add_argument("--models-root", required=True, type=Path)
    feasibility.add_argument("--output", required=True, type=Path)
    page = commands.add_parser("lighton-page")
    page.add_argument("--models-root", required=True, type=Path)
    page.add_argument("--image", required=True, type=Path)
    page.add_argument("--image-sha256", required=True)
    page.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "prepare-models":
        summary = prepare_models(
            arguments.models_root, arguments.installed_directory, arguments.best_directory
        )
    elif arguments.command == "run":
        summary = run_benchmark(
            arguments.corpus_root,
            arguments.audit_directory,
            arguments.models_root,
            arguments.output,
            temporary_directory=arguments.temporary_directory,
        )
    elif arguments.command == "summarize":
        summary = refresh_operational_summary(arguments.benchmark_directory, arguments.output)
    elif arguments.command == "lighton-six":
        summary = run_lighton_six(
            arguments.tesseract_directory, arguments.models_root, arguments.output
        )
    else:
        summary = run_lighton_page(
            arguments.models_root, arguments.image, arguments.image_sha256, arguments.output
        )
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if arguments.command == "lighton-page" and summary["status"] not in {"success", "token_limit"}:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
