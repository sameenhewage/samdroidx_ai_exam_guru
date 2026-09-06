"""Benchmark mechanics use synthetic sources; quality still requires human ground truth."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from exam_guru_api.documents.tesseract_ocr import RenderedPageImage
from tests.test_tesseract_file_input import FileCommandRunner, file_config, source_pdf

MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "benchmark_source_fidelity.py"
# The CLI intentionally shares only pure/local evidence helpers with the audit script.
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("source_fidelity_benchmark", MODULE_PATH)
assert spec is not None
assert spec.loader is not None
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)

EXPECTED_SELECTION = {
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


def document(checksum: str = "a" * 64, *aliases: str) -> dict[str, Any]:
    return {
        "sha256": checksum,
        "size_bytes": 123,
        "page_count": 2,
        "aliases": list(aliases) or ["Grade 3/source.pdf"],
        "error": None,
        "page_errors": 0,
    }


def test_exact_prior_proposal_is_preserved_not_resampled() -> None:
    assert benchmark.EXACT_SELECTION == EXPECTED_SELECTION
    assert len(benchmark.EXACT_SELECTION) == 25
    assert sum(len(pages) for pages in benchmark.EXACT_SELECTION.values()) == 40


def test_prefix_resolution_deduplicates_checksums_but_keeps_all_aliases() -> None:
    manifest = {"documents": [document("a" * 64, "Grade 3/source.pdf", "Grade 4/copy.pdf")]}
    selected = benchmark.resolve_selection(manifest, {"aaaaaaaa": [1, 2]})
    assert len(selected) == 2
    assert selected[0]["source_sha256"] == "a" * 64
    assert selected[0]["aliases"] == ["Grade 3/source.pdf", "Grade 4/copy.pdf"]
    assert selected[0]["page_number"] == 1
    assert selected[1]["page_number"] == 2
    assert selected[0]["source_page_count"] == 2
    assert selected[0]["primary_relative_path"] == "Grade 3/source.pdf"


@pytest.mark.parametrize(
    ("documents", "proposal", "message"),
    [
        ([document()], {"bbbbbbbb": [1]}, "unique"),
        ([document(), document("a" * 8 + "b" * 56)], {"aaaaaaaa": [1]}, "unique"),
        ([document()], {"aaaaaaaa": [3]}, "page"),
        ([document()], {"aaaaaaaa": [True]}, "page"),
        ([document()], {"aaaaaaaa": [1, 1]}, "page"),
        ([document("a" * 64, "../escape.pdf")], {"aaaaaaaa": [1]}, "alias"),
        ([{**document(), "error": "MalformedPDF"}], {"aaaaaaaa": [1]}, "unreadable"),
    ],
)
def test_selection_fails_closed_without_silent_substitutions(
    documents: list[dict[str, Any]], proposal: dict[str, list[int]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        benchmark.resolve_selection({"documents": documents}, proposal)


def test_comparison_profiles_hold_english_and_raster_configuration_fixed(tmp_path: Path) -> None:
    profiles = benchmark.build_profiles(
        tmp_path / "installed", tmp_path / "mixed", tmp_path / "best"
    )
    fast = profiles[benchmark.FAST_PROFILE]
    best = profiles[benchmark.BEST_PROFILE]
    for name in (
        "dpi",
        "page_segmentation_mode",
        "timeout_seconds",
        "language",
        "max_pages",
        "batch_size",
        "max_pixels_per_page",
    ):
        assert getattr(fast, name) == getattr(best, name)
    assert fast.language == "sin+eng"
    assert fast.dpi == 300
    assert fast.page_segmentation_mode == 3
    assert fast.batch_size == 1
    assert profiles[benchmark.TRILINGUAL_PROFILE].language == "sin+tam+eng"
    assert fast.tessdata_directory != best.tessdata_directory


def test_custom_traineddata_directories_get_the_same_installed_tsv_configuration(
    tmp_path: Path,
) -> None:
    installed = tmp_path / "installed"
    (installed / "configs").mkdir(parents=True)
    (installed / "configs" / "tsv").write_bytes(b"tessedit_create_tsv 1\n")
    mixed, best = tmp_path / "mixed", tmp_path / "best"
    metadata = benchmark.stage_output_configuration(installed, (mixed, best))
    expected = hashlib.sha256(b"tessedit_create_tsv 1\n").hexdigest()
    assert metadata["sha256"] == expected
    for directory in (mixed, best):
        assert (directory / "configs" / "tsv").read_bytes() == b"tessedit_create_tsv 1\n"
    benchmark.verify_output_configuration((installed, mixed, best), expected)
    (best / "configs" / "tsv").write_bytes(b"tessedit_create_txt 1\n")
    with pytest.raises(ValueError, match="configuration"):
        benchmark.verify_output_configuration((installed, mixed, best), expected)


def test_model_hash_verification_refuses_mismatches(tmp_path: Path) -> None:
    model = tmp_path / "sin.traineddata"
    model.write_bytes(b"fake model")
    checksum = hashlib.sha256(b"fake model").hexdigest()
    assert benchmark.verify_model_hashes(tmp_path, {"sin": checksum})["sin"]["sha256"] == checksum
    with pytest.raises(ValueError, match="checksum"):
        benchmark.verify_model_hashes(tmp_path, {"sin": "0" * 64})


def test_real_command_recorder_keeps_nonzero_stdout_stderr_privately(tmp_path: Path) -> None:
    runner = benchmark.EvidenceCommandRunner(tmp_path / "commands")
    result = runner(
        (
            sys.executable,
            "-c",
            "import sys; print('RAW ' + 'FIXTURE'); print('warning', file=sys.stderr); sys.exit(7)",
        ),
        cwd=tmp_path,
        timeout_seconds=2,
        max_output_bytes=4096,
    )
    assert result.returncode == 7
    assert result.stdout == b"RAW FIXTURE\n"
    assert result.stderr == b"warning\n"
    metadata = json.loads((tmp_path / "commands" / "command-001.json").read_text())
    assert metadata["returncode"] == 7
    assert metadata["status"] == "process_error"
    assert "RAW FIXTURE" not in json.dumps(metadata)
    assert (tmp_path / "commands" / "command-001.stdout").read_bytes() == result.stdout


def test_command_timeout_retains_partial_raw_output_not_just_successes(tmp_path: Path) -> None:
    runner = benchmark.EvidenceCommandRunner(tmp_path / "commands")
    with pytest.raises(subprocess.TimeoutExpired):
        runner(
            (sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(5)"),
            cwd=tmp_path,
            timeout_seconds=0.2,
            max_output_bytes=4096,
        )
    metadata = json.loads((tmp_path / "commands" / "command-001.json").read_text())
    assert metadata["status"] == "timeout"
    assert metadata["timeout_seconds"] == 0.2
    assert (tmp_path / "commands" / "command-001.stdout").read_bytes() == b"partial\n"


def test_command_output_limit_keeps_a_bounded_prefix_and_error(tmp_path: Path) -> None:
    runner = benchmark.EvidenceCommandRunner(tmp_path / "commands")
    result = runner(
        (sys.executable, "-c", "import os; os.write(1, b'x' * 50000)"),
        cwd=tmp_path,
        timeout_seconds=2,
        max_output_bytes=1024,
    )
    assert result.output_limit_exceeded
    assert len(result.stdout) <= 1024
    metadata = json.loads((tmp_path / "commands" / "command-001.json").read_text())
    assert metadata["status"] == "output_limit"


def test_render_provenance_rejects_different_images_for_the_same_comparison(tmp_path: Path) -> None:
    raw = tmp_path / "render.png"
    raw.write_bytes(b"fixture render")
    image = RenderedPageImage(1, raw, hashlib.sha256(b"fixture render").hexdigest(), 12, 13, 300)
    target = tmp_path / "saved.png"
    benchmark.persist_render(image, target)
    benchmark.persist_render(image, target)
    assert target.read_bytes() == b"fixture render"
    raw.write_bytes(b"different render")
    altered = RenderedPageImage(
        1, raw, hashlib.sha256(b"different render").hexdigest(), 12, 13, 300
    )
    with pytest.raises(ValueError, match="identical"):
        benchmark.persist_render(altered, target)


def test_fixed_profile_keeps_render_and_failed_candidate_for_a_timeout(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models", fail_ocr=True)
    selected = {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "page_number": 1}
    result = benchmark.execute_fixed_page(
        path,
        selected,
        file_config(runner.models),
        tmp_path / "result",
        tmp_path / "page.png",
        command_runner=runner,
    )
    assert result["status"] == "timeout"
    assert result["page_image"]["sha256"] == runner.image_hashes[0]
    assert (tmp_path / "page.png").exists()
    assert (tmp_path / "result" / "candidate.txt").exists()
    assert result["error"]["type"] == "TesseractTimeoutError"
    assert "PRIVATE" not in json.dumps(result)


def test_fixed_profile_uses_explicit_bounded_scratch_not_the_durable_result_directory(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    selected = {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "page_number": 1}
    scratch = tmp_path / "bounded-scratch"
    scratch.mkdir(mode=0o700)
    result = benchmark.execute_fixed_page(
        path,
        selected,
        file_config(runner.models),
        tmp_path / "result",
        tmp_path / "page.png",
        command_runner=runner,
        temporary_directory=scratch,
    )
    assert result["status"] == "success"
    assert all(Path(call[1]).is_relative_to(scratch) for call in runner.calls if "tsv" in call)
    assert not list(scratch.iterdir())
    assert (tmp_path / "page.png").is_file()


def test_executed_source_snapshot_is_checksum_bound_for_later_working_tree_edits(
    tmp_path: Path,
) -> None:
    snapshots = benchmark.snapshot_execution_sources(tmp_path / "executed-sources")
    for name in (
        "benchmark_source_fidelity.py",
        "audit_source_fidelity.py",
        "fidelity.py",
        "page_reading.py",
        "tesseract_ocr.py",
    ):
        saved = tmp_path / "executed-sources" / name
        assert saved.is_file()
        assert hashlib.sha256(saved.read_bytes()).hexdigest() == snapshots[name]["sha256"]


def test_lighton_applies_local_safetensors_adapter_before_generation(tmp_path: Path) -> None:
    base = object()
    calls: list[dict[str, Any]] = []

    class Adapted:
        def __init__(self) -> None:
            self.peft_config = {"default": object()}

        def named_parameters(self) -> list[tuple[str, object]]:
            return [("layer.lora_A.default.weight", object())]

        def eval(self) -> "Adapted":
            return self

    adapted = Adapted()

    class Loader:
        @staticmethod
        def from_pretrained(model: object, directory: str, **kwargs: Any) -> Adapted:
            assert model is base
            calls.append({"directory": directory, **kwargs})
            return adapted

    assert benchmark.apply_local_cpu_adapter(base, Loader, tmp_path) is adapted
    assert calls == [
        {
            "directory": str(tmp_path),
            "is_trainable": False,
            "local_files_only": True,
            "use_safetensors": True,
        }
    ]


def test_lighton_refuses_an_unapplied_adapter(tmp_path: Path) -> None:
    class Loader:
        @staticmethod
        def from_pretrained(*args: Any, **kwargs: Any) -> object:
            return object()

    with pytest.raises(ValueError, match="adapter"):
        benchmark.apply_local_cpu_adapter(object(), Loader, tmp_path)


def test_lighton_decodes_only_generated_tokens_not_the_input_prompt() -> None:
    class Processor:
        def decode(self, tokens: list[int], *, skip_special_tokens: bool) -> str:
            assert tokens == [91, 92]
            assert skip_special_tokens
            return "generated fixture"

    assert (
        benchmark.decode_generated_only(Processor(), [[11, 12, 13, 91, 92]], 3)
        == "generated fixture"
    )


def test_lighton_model_directory_rejects_pickle_and_unpinned_weights(tmp_path: Path) -> None:
    path = tmp_path / "model.safetensors"
    path.write_bytes(b"fixture")
    expected = {"model.safetensors": hashlib.sha256(b"fixture").hexdigest()}
    (tmp_path / "config.json").write_text("{}")
    metadata = benchmark.verify_safetensor_directory(tmp_path, expected)
    assert metadata["model.safetensors"]["sha256"] == expected["model.safetensors"]
    (tmp_path / "training_args.bin").write_bytes(b"must never be loaded")
    with pytest.raises(ValueError, match="unsafe"):
        benchmark.verify_safetensor_directory(tmp_path, expected)


def test_summary_counts_ocr_member_of_current_reader_without_counting_native_text() -> None:
    result = {
        "profile": benchmark.CURRENT_PROFILE,
        "page_key": "one",
        "status": "success",
        "elapsed_seconds": 1.0,
        "candidates": [
            {"method": "native", "artifact": {"characters": 90}},
            {"method": "ocr", "artifact": {"characters": 42}},
        ],
    }
    summary = benchmark.operational_summary([result], selected_pages=1)
    assert summary["profiles"][benchmark.CURRENT_PROFILE]["candidate_characters_total"] == 42


def test_summary_refresh_preserves_original_evidence_and_records_its_hash(tmp_path: Path) -> None:
    previous = {"selected_pages": 1, "originals_unchanged": True}
    original = tmp_path / "summary.json"
    original.write_text(json.dumps(previous))
    result = {
        "profile": benchmark.FAST_PROFILE,
        "page_key": "one",
        "status": "success",
        "elapsed_seconds": 1.0,
        "candidate": {"characters": 8},
    }
    (tmp_path / "results.jsonl").write_text(json.dumps(result) + "\n")
    refreshed = benchmark.refresh_operational_summary(
        tmp_path, tmp_path / "operational-summary.json"
    )
    assert json.loads(original.read_text()) == previous
    assert refreshed["source_summary_sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert refreshed["profiles"][benchmark.FAST_PROFILE]["candidate_characters_total"] == 8
    assert refreshed["human_adjudicated_references"] == 0


def test_summary_counts_errors_and_zero_references_without_accuracy_claims() -> None:
    results = [
        {
            "profile": benchmark.FAST_PROFILE,
            "page_key": "one",
            "status": "success",
            "elapsed_seconds": 1.0,
            "candidate_sha256": "a",
        },
        {
            "profile": benchmark.BEST_PROFILE,
            "page_key": "one",
            "status": "timeout",
            "elapsed_seconds": 2.0,
        },
        {
            "profile": benchmark.CURRENT_PROFILE,
            "page_key": "one",
            "status": "missing_languages",
            "elapsed_seconds": 0.1,
        },
    ]
    summary = benchmark.operational_summary(results, selected_pages=1)
    assert summary["human_adjudicated_references"] == 0
    assert summary["quality_status"] == "awaiting_human_adjudication"
    assert summary["cer"] is None
    assert summary["wer"] is None
    assert summary["profiles"][benchmark.BEST_PROFILE]["status_counts"] == {"timeout": 1}
    assert summary["paired_successes"] == 0
    assert summary["corpus_accuracy_estimate"] is None
