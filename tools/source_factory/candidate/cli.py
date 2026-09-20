# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy==2.2.6", "opencv-python-headless==4.12.0.88"]
# ///
"""Seal the primary readings into Machine Candidates.

    uv run tools/source_factory/candidate/cli.py --document <folder> build
    uv run tools/source_factory/candidate/cli.py --document <folder> show --page 156

Reads only what is already on disk — the deterministic layout, the canonical
crops and the sealed primary readings — so it needs no GPU, no model and no
network.
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

from tools.source_factory.candidate.machine import PrimaryReading, build  # noqa: E402
from tools.source_factory.crops import cutter as crop_tools  # noqa: E402
from tools.source_factory.layout.corpus import load_document  # noqa: E402
from tools.source_factory.primary.model import PROVENANCE as PRIMARY_PROVENANCE  # noqa: E402

MAX_CROP_PADDING = 32


def assert_crops_are_current(document, crops) -> None:
    """Refuse crops cut from a layout that no longer exists.

    Crops are written once and the agent reads them. If the layout is
    re-detected afterwards the boundaries move, and a sealed reading filed
    under a region silently describes *a different part of the page*. That
    produces confident, coherent, completely wrong source content — seen for
    real on page 186 (D16).

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
            "the crops are stale - the layout moved after they were cut, so a sealed "
            "reading describes different pixels than the region it is filed under:\n  "
            + "\n  ".join(stale[:10])
            + f"\n  ({len(stale)} total)\nRe-cut the crops and re-read them:\n"
            f"  uv run tools/source_factory/crops/cli.py --document {document.folder} cut"
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
    """The agent's own reading, keyed by page. Required: it is the only text."""

    folder = document.folder / "primary" / "pages"
    if not folder.exists():
        raise SystemExit(
            f"no primary readings under {folder}\n"
            "Look at the canonical crops first and seal them with "
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
    crops = {crop.crop_id: crop for crop in crop_tools.load(document)}
    assert_crops_are_current(document, crops)
    primary_pages = load_primary(document)
    layout_lines = _layout_line_counts(document)
    boxes = {crop.region_id: crop.bbox for crop in crops.values()}

    pages: dict[int, list[dict]] = defaultdict(list)
    counters = {"regions": 0, "abstained": 0, "needs_attention": 0}

    for page_number, payload in sorted(primary_pages.items()):
        for region in payload["regions"]:
            result = build(
                primary=PrimaryReading(
                    region_id=region["region_id"],
                    region_type=region["region_type"],
                    text=region["exact_text"],
                    uncertainty=tuple(region.get("uncertainty_reason", [])),
                    language=region.get("language", arguments.language),
                    layout_lines=layout_lines.get(region["region_id"]),
                )
            )
            pages[page_number].append(
                result.to_json()
                | {
                    # Geometry belongs to the deterministic layout; the crop
                    # checksum names the exact pixels that were read.
                    "bbox": region.get("bbox") or boxes.get(region["region_id"]),
                    "crop_sha256": region.get("crop_sha256"),
                }
            )
            counters["regions"] += 1
            counters["abstained"] += 1 if result.abstained else 0
            counters["needs_attention"] += 1 if result.requires_human_attention else 0
        pages[page_number].sort(key=lambda item: item["region_id"])

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
    target = document.folder / "candidates" / "pages" / f"page-{arguments.page:03d}.json"
    if not target.exists():
        raise SystemExit(f"no candidate for page {arguments.page}: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    for region in payload["regions"]:
        print(f"=== {region['region_id']} [{region['region_type']}] {region['reason']}")
        for finding in region["validation_findings"]:
            print(f"    CHECK {finding}")
        preview = region["text"][: arguments.chars].replace("\n", " / ")
        print(f"    {preview}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Source V2 Machine Candidate")
    parser.add_argument("--document", type=Path, default=None)
    parser.add_argument("--language", default="sinhala")
    subparsers = parser.add_subparsers(dest="command", required=True)

    builder = subparsers.add_parser(
        "build", help="seal the primary readings into Machine Candidates"
    )
    builder.set_defaults(handler=command_build)

    show = subparsers.add_parser("show", help="print one page's candidate")
    show.add_argument("--page", type=int, required=True)
    show.add_argument("--chars", type=int, default=160)
    show.set_defaults(handler=command_show)

    arguments = parser.parse_args()
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
