"""Run one reader over the fixed benchmark crops and measure it.

Runs in the dedicated `.venv-sourcev2` environment because it needs a CUDA
torch build:

    .venv-sourcev2\\Scripts\\python.exe tools/source_factory/readers/bench.py --reader sinhala-lightonocr

A reader that cannot run is recorded as a result with its exact blocker. A
missing number is reported as missing; it is never filled in with an estimate.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.source_factory.layout.corpus import load_document  # noqa: E402
from tools.source_factory.readers import crops as crop_tools  # noqa: E402
from tools.source_factory.readers import groundtruth, metrics  # noqa: E402
from tools.source_factory.readers.port import ReaderUnavailable, ReadRequest  # noqa: E402
from tools.source_factory.readers.sinhala import READERS  # noqa: E402


def environment() -> dict:
    info: dict = {"platform": platform.platform(), "python": platform.python_version()}
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            info["device"] = torch.cuda.get_device_name(0)
            info["device_total_mib"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1048576
            )
    except ImportError as error:
        info["torch"] = f"missing: {error}"
    try:
        import transformers

        info["transformers"] = transformers.__version__
    except ImportError:
        info["transformers"] = "missing"
    return info


def run(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    selected = crop_tools.load(document)
    if arguments.only:
        wanted = set(arguments.only)
        selected = [crop for crop in selected if crop.crop_id in wanted]
    if arguments.limit:
        selected = selected[: arguments.limit]
    if not selected:
        raise SystemExit("no crops selected")

    references = groundtruth.load(document)
    factory = READERS.get(arguments.reader)
    if factory is None:
        raise SystemExit(f"unknown reader {arguments.reader!r}; known: {sorted(READERS)}")
    reader = factory(max_new_tokens=arguments.max_new_tokens)

    started = time.perf_counter()
    unavailable: str | None = None
    try:
        reader.load()
    except ReaderUnavailable as error:
        unavailable = str(error)
    except Exception as error:  # noqa: BLE001 - a load failure is a result
        unavailable = f"{type(error).__name__}: {error}"

    summary = metrics.ReaderSummary(reader=arguments.reader)
    rows: list[dict] = []
    readings: dict[str, str] = {}

    for crop in selected:
        if unavailable:
            measurement = metrics.measure(
                crop_id=crop.crop_id,
                reader=arguments.reader,
                region_type=crop.region_type,
                text="",
                seconds=0.0,
                abstained=False,
                failure=unavailable,
                pixels=1,
                dpi=document.dpi,
                language=arguments.language,
                reference=None,
                peak_vram_bytes=None,
            )
            summary.add(measurement)
            rows.append(measurement.to_json())
            continue

        image = crop_tools.read_page_image(crop.path)
        result = reader.read(
            ReadRequest(
                crop=image,
                region_id=crop.region_id,
                region_type=crop.region_type,
                language=arguments.language,
                page_number=crop.page_number,
                document_id=crop.document_id,
            )
        )
        entry = references.get(crop.crop_id, {})
        measurement = metrics.measure(
            crop_id=crop.crop_id,
            reader=arguments.reader,
            region_type=crop.region_type,
            text=result.text,
            seconds=result.seconds,
            abstained=result.abstained,
            failure=result.failure,
            pixels=image.shape[0] * image.shape[1],
            dpi=document.dpi,
            language=arguments.language,
            reference=entry.get("text"),
            peak_vram_bytes=result.peak_vram_bytes,
        )
        must, must_not = groundtruth.token_checks(entry)
        if must or must_not:
            hits = [token for token in must if token in result.text]
            strays = [token for token in must_not if token in result.text]
            measurement.critical_exact = len(hits) == len(must) and not strays
            row_extra = {"expected_tokens": must, "found_tokens": hits, "stray_tokens": strays}
        else:
            row_extra = {}
        summary.add(measurement)
        payload = measurement.to_json() | row_extra
        payload["text"] = result.text
        rows.append(payload)
        readings[crop.crop_id] = result.text
        print(
            f"{crop.crop_id:>10} {crop.region_type:<8} "
            f"{result.seconds:6.2f}s {len(result.text):5d} chars "
            f"{'FAIL ' + (result.failure or '') if result.failure else ''}",
            flush=True,
        )

    reader.unload()
    report = {
        "reader": arguments.reader,
        "document": document.document_id,
        "language": arguments.language,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": environment(),
        "unavailable": unavailable,
        "load_seconds": round(getattr(reader, "load_seconds", 0.0), 1),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "summary": summary.to_json(),
        "crops": rows,
    }
    target = document.folder / "readers" / "results" / f"{arguments.reader}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(target), "summary": report["summary"]}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark one Source V2 reader")
    parser.add_argument("--document", type=Path, default=None)
    parser.add_argument("--reader", required=True)
    parser.add_argument("--language", default="sinhala")
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
