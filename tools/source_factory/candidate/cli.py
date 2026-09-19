# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy==2.2.6", "opencv-python-headless==4.12.0.88"]
# ///
"""Combine reader results into Machine Candidates.

    uv run tools/source_factory/candidate/cli.py build
    uv run tools/source_factory/candidate/cli.py show --page 156

Reads the per-reader benchmark results already on disk, so it needs no GPU and
no model environment.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

# Sinhala must survive the console. A Windows code page must never be allowed
# to decide what source text looks like.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.source_factory.candidate import selection  # noqa: E402
from tools.source_factory.candidate.machine import Witness, build  # noqa: E402
from tools.source_factory.layout.corpus import load_document  # noqa: E402
from tools.source_factory.readers import crops as crop_tools  # noqa: E402


def load_results(document) -> dict[str, dict[str, dict]]:
    """crop_id -> reader -> measured row."""

    folder = document.folder / "readers" / "results"
    if not folder.exists():
        raise SystemExit(f"no reader results: {folder}")
    table: dict[str, dict[str, dict]] = defaultdict(dict)
    for path in sorted(folder.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        reader = report["reader"]
        for row in report.get("crops", []):
            table[row["crop_id"]][reader] = row
    return table


def _add_unread_regions(document, pages: dict[int, list[dict]], counters: dict) -> None:
    """Emit an abstaining candidate for every region no reader was given.

    Figures, tables and decorative bars carry no text, so nothing is cropped
    for them and they never reach the reviewer. A page would then count as
    resolved while a figure had never been decided at all. Every region the
    detector found must reach a terminal review state, so the ones with no
    reading abstain and the reviewer has to exclude them or say what they are.
    """

    folder = document.folder / "layout" / "regions"
    if not folder.exists():
        return
    for path in sorted(folder.glob("page-*.json")):
        layout = json.loads(path.read_text(encoding="utf-8"))
        page_number = int(layout["page_number"])
        seen = {region["region_id"] for region in pages.get(page_number, [])}
        for region in layout.get("regions", []):
            if region["id"] in seen:
                continue
            pages[page_number].append(
                {
                    "region_id": region["id"],
                    "region_type": region["type"],
                    "text": "",
                    "abstained": True,
                    "chosen_reader": None,
                    "reason": f"no text was read for this {region['type']} region",
                    "critical_conflict": False,
                    "agreement_ratio": 0.0,
                    "disagreement": {},
                    "rejected": {},
                    "witnesses": [],
                    "bbox": region["bbox"],
                }
            )
            counters["regions"] += 1
            counters["abstained"] += 1
        pages[page_number].sort(key=lambda item: item["region_id"])


def command_build(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    table = load_results(document)
    crops = {crop.crop_id: crop for crop in crop_tools.load(document)}
    active = selection.ACTIVE
    pages: dict[int, list[dict]] = defaultdict(list)
    counters = {"regions": 0, "abstained": 0, "critical_conflict": 0}

    for crop_id, by_reader in sorted(table.items()):
        crop = crops.get(crop_id)
        if crop is None:
            continue
        allowed = active.readers_for(arguments.language, crop.region_type)
        witnesses = [
            Witness(
                reader=reader,
                text=row.get("text", ""),
                seconds=float(row.get("seconds", 0.0)),
                failed=bool(row.get("failure")),
                repetition=float(row.get("repetition", 0.0)),
                structural_repetition=float(row.get("structural_repetition", 0.0)),
                foreign_script=float(row.get("foreign_script", 0.0)),
                expected_script=float(row.get("expected_script", 1.0)),
                rank=active.rank(arguments.language, crop.region_type, reader),
            )
            for reader, row in sorted(by_reader.items())
            if not allowed or reader in allowed
        ]
        if not witnesses:
            continue
        result = build(
            region_id=crop.region_id, region_type=crop.region_type, witnesses=witnesses
        )
        payload = result.to_json() | {"crop_id": crop_id, "bbox": crop.bbox}
        pages[crop.page_number].append(payload)
        counters["regions"] += 1
        counters["abstained"] += 1 if result.abstained else 0
        counters["critical_conflict"] += 1 if result.critical_conflict else 0

    _add_unread_regions(document, pages, counters)

    root = document.folder / "candidates"
    root.mkdir(parents=True, exist_ok=True)
    for page_number, regions in sorted(pages.items()):
        target = root / f"page-{page_number:03d}.json"
        target.write_text(
            json.dumps(
                {
                    "document_id": document.document_id,
                    "page_number": page_number,
                    "language": arguments.language,
                    "selection_evidence": active.evidence,
                    "regions": sorted(regions, key=lambda item: item["region_id"]),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"root": str(root), "pages": sorted(pages), **counters}, indent=2))
    return 0


def command_show(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    target = document.folder / "candidates" / f"page-{arguments.page:03d}.json"
    if not target.exists():
        raise SystemExit(f"no candidate for page {arguments.page}: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    for region in payload["regions"]:
        print(f"=== {region['region_id']} [{region['region_type']}] {region['reason']}")
        if region["rejected"]:
            print(f"    rejected: {region['rejected']}")
        if region["uncertain_tokens"]:
            for cell in region["uncertain_tokens"]:
                variants = " | ".join(
                    f"{v['value']!r}<-{','.join(v['readers'])}" for v in cell["variants"]
                )
                print(f"    UNCERTAIN {cell['kind']} line {cell['line']}: {variants}")
        preview = region["text"][: arguments.chars].replace("\n", " / ")
        print(f"    {preview}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Source V2 Machine Candidate")
    parser.add_argument("--document", type=Path, default=None)
    parser.add_argument("--language", default="sinhala")
    subparsers = parser.add_subparsers(dest="command", required=True)

    builder = subparsers.add_parser("build", help="combine reader results into candidates")
    builder.set_defaults(handler=command_build)

    show = subparsers.add_parser("show", help="print one page's candidate")
    show.add_argument("--page", type=int, required=True)
    show.add_argument("--chars", type=int, default=160)
    show.set_defaults(handler=command_show)

    arguments = parser.parse_args()
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
