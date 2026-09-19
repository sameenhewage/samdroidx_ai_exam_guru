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
from tools.source_factory.candidate.machine import PrimaryReading, Witness, build  # noqa: E402
from tools.source_factory.layout.corpus import load_document  # noqa: E402
from tools.source_factory.primary.model import PROVENANCE as PRIMARY_PROVENANCE  # noqa: E402
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


def _add_uncropped_regions(
    primary_pages: dict[int, dict],
    pages: dict[int, list[dict]],
    counters: dict,
    layout_lines: dict[str, int] | None = None,
) -> None:
    """Carry the primary reading of regions no local reader was given.

    Figures, decorative bars and headers are not cropped for OCR, so the local
    readers never see them. The agent did: it read the whole page. Those
    regions therefore still arrive with the agent's own text — a header, a
    folio, a figure caption — or abstain when the agent recorded that the
    region carries no text. Either way the reviewer has to decide them.
    """

    for page_number, payload in primary_pages.items():
        seen = {region["region_id"] for region in pages.get(page_number, [])}
        for region in payload["regions"]:
            if region["region_id"] in seen:
                continue
            result = build(
                primary=PrimaryReading(
                    region_id=region["region_id"],
                    region_type=region["region_type"],
                    text=region["exact_text"],
                    uncertainty=tuple(region.get("uncertainty_reason", [])),
                    language=region.get("language", "sinhala"),
                ),
                witnesses=[],
            )
            pages[page_number].append(result.to_json() | {"bbox": region["bbox"]})
            counters["regions"] += 1
            counters["abstained"] += 1 if result.abstained else 0
            counters["critical_conflict"] += 1 if result.critical_conflict else 0
        pages[page_number].sort(key=lambda item: item["region_id"])


def _write_comparison(document, pages: dict[int, list[dict]]) -> None:
    """The evidence behind each proposal, kept separately from the proposal.

    A reviewer asking "why does it say that" should not have to read the
    candidate file, and a later audit should be able to see what every reader
    said without trusting the candidate's summary of it.
    """

    root = document.folder / "comparison" / "pages"
    root.mkdir(parents=True, exist_ok=True)
    for page_number, regions in sorted(pages.items()):
        payload = {
            "document_id": document.document_id,
            "page_number": page_number,
            "regions": [
                {
                    "region_id": region["region_id"],
                    "primary_text": region.get("primary_text", ""),
                    "selected_text": region.get("selected_text", region.get("text", "")),
                    "selected_source": region.get("selected_source"),
                    "supporting_readers": region.get("supporting_readers", []),
                    "rejected_readers": region.get("rejected_readers", {}),
                    "disagreements": region.get("disagreement", {}),
                    "critical_conflict": region.get("critical_conflict", False),
                    "validation_findings": region.get("validation_findings", []),
                    "uncertain": region.get("uncertain", False),
                    "requires_human_attention": region.get(
                        "requires_human_attention", False
                    ),
                }
                for region in regions
            ],
        }
        (root / f"page-{page_number:03d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


MAX_CROP_PADDING = 32


def assert_crops_are_current(document, crops) -> None:
    """Refuse reader evidence cut from a layout that no longer exists.

    Crops are written once and the readers are run against them. If the layout
    is re-detected afterwards the boundaries move, and the stored OCR text
    silently describes a *different part of the page* than the region it is
    filed under. That produces confident, coherent, completely wrong evidence -
    seen for real on page 186, where both readers "disagreed" with the primary
    reading because they had been shown another paragraph entirely.

    The crop geometry is the check: a crop must still be its region's bounding
    box grown by an even padding on all four sides.
    """

    stale: list[str] = []
    for path in sorted((document.folder / "layout" / "regions").glob("page-*.json")):
        layout = json.loads(path.read_text(encoding="utf-8"))
        boxes = {region["id"]: region["bbox"] for region in layout["regions"]}
        for crop in crops.values():
            expected = boxes.get(crop.region_id)
            if expected is None or crop.page_number != int(layout["page_number"]):
                continue
            # The crop is the region grown by a small padding, and clamped at
            # the page edges. So it must contain the region and never stray
            # far from it.
            grown = [
                expected[0] - crop.bbox[0],
                expected[1] - crop.bbox[1],
                crop.bbox[2] - expected[2],
                crop.bbox[3] - expected[3],
            ]
            if any(side < 0 for side in grown) or any(side > MAX_CROP_PADDING for side in grown):
                stale.append(
                    f"{crop.crop_id}: crop {list(crop.bbox)} is not layout {list(expected)} "
                    f"grown by 0..{MAX_CROP_PADDING}px (got {grown})"
                )
    if stale:
        raise SystemExit(
            "reader crops are stale - the layout moved after they were cut, so the OCR "
            "evidence describes different pixels than the regions it is filed under:\n  "
            + "\n  ".join(stale[:10])
            + f"\n  ({len(stale)} total)\nRe-cut the crops and re-run the readers:\n"
            f"  uv run tools/source_factory/readers/cli.py --document {document.folder} crops\n"
            "  then re-run each reader's bench.py"
        )


def _layout_line_counts(document) -> dict[str, int]:
    """Geometric line counts per region, straight from the layout.

    Used to notice a primary reading that stopped part-way through a region.
    """

    counts: dict[str, int] = {}
    for path in sorted((document.folder / "layout" / "regions").glob("page-*.json")):
        for region in json.loads(path.read_text(encoding="utf-8"))["regions"]:
            counts[region["id"]] = int(region.get("line_count") or 0)
    return counts


def load_primary(document) -> dict[int, dict]:
    """The agent's own reading, keyed by page. Required: it is the base text.

    Building a candidate without it would put local OCR back in front of the
    source, which is the exact regression D14 exists to prevent.
    """

    folder = document.folder / "primary" / "pages"
    if not folder.exists():
        raise SystemExit(
            f"no primary readings under {folder}\n"
            "Look at the rendered pages first and seal them with "
            "tools/source_factory/primary/cli.py before building candidates."
        )
    pages: dict[int, dict] = {}
    for path in sorted(folder.glob("page-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("provenance") != PRIMARY_PROVENANCE:
            raise SystemExit(f"{path}: not a primary agent reading")
        pages[int(payload["page_number"])] = payload
    if not pages:
        raise SystemExit(f"no primary readings under {folder}")
    return pages


def command_build(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    table = load_results(document)
    crops = {crop.crop_id: crop for crop in crop_tools.load(document)}
    assert_crops_are_current(document, crops)
    primary_pages = load_primary(document)
    layout_lines = _layout_line_counts(document)
    primary_regions = {
        region["region_id"]: (page["page_number"], region)
        for page in primary_pages.values()
        for region in page["regions"]
    }
    active = selection.ACTIVE
    pages: dict[int, list[dict]] = defaultdict(list)
    counters = {"regions": 0, "abstained": 0, "critical_conflict": 0}

    for crop_id, by_reader in sorted(table.items()):
        crop = crops.get(crop_id)
        if crop is None:
            continue
        if crop.region_id not in primary_regions:
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
        _, region = primary_regions[crop.region_id]
        result = build(
            primary=PrimaryReading(
                region_id=region["region_id"],
                region_type=region["region_type"],
                text=region["exact_text"],
                uncertainty=tuple(region.get("uncertainty_reason", [])),
                    language=region.get("language", "sinhala"),
            ),
            witnesses=witnesses,
        )
        payload = result.to_json() | {"crop_id": crop_id, "bbox": crop.bbox}
        pages[crop.page_number].append(payload)
        counters["regions"] += 1
        counters["abstained"] += 1 if result.abstained else 0
        counters["critical_conflict"] += 1 if result.critical_conflict else 0

    _add_uncropped_regions(primary_pages, pages, counters, layout_lines)

    _write_comparison(document, pages)

    root = document.folder / "candidates" / "pages"
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
