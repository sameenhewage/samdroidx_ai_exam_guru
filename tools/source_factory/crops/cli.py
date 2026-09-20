# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy==2.2.6",
#   "opencv-python-headless==4.12.0.88",
# ]
# ///
"""Cut and inspect the canonical region crops.

    uv run tools/source_factory/crops/cli.py --document <folder> cut --pages 1,2,3
    uv run tools/source_factory/crops/cli.py --document <folder> sheet

Deterministic geometry only. The crops produced here are the images the
executing agent transcribes from and the reviewer confirms against.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.source_factory.crops import cutter  # noqa: E402
from tools.source_factory.layout.corpus import load_document  # noqa: E402


def parse_pages(spec: str | None) -> list[int]:
    from tools.source_factory.layout import benchmark as benchmark_set

    if not spec:
        return list(benchmark_set.NUMBERS)
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(chunk))
    return pages


def command_cut(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    extracted = cutter.extract(document, parse_pages(arguments.pages))
    by_type: dict[str, int] = {}
    for crop in extracted:
        by_type[crop.region_type] = by_type.get(crop.region_type, 0) + 1
    print(
        json.dumps(
            {
                "root": str(document.folder / "crops"),
                "crops": len(extracted),
                "by_type": by_type,
                "pages": sorted({crop.page_number for crop in extracted}),
            },
            indent=2,
        )
    )
    return 0


def command_sheet(arguments: argparse.Namespace) -> int:
    """One contact sheet of the crops, for eyes."""

    import cv2

    from tools.source_factory.layout.preview import contact_sheet

    document = load_document(arguments.document)
    extracted = cutter.load(document)
    selected = [crop for crop in extracted if crop.crop_id in set(arguments.only)] or extracted
    images = [(crop.crop_id, cutter.read_page_image(crop.path)) for crop in selected]
    sheet = contact_sheet(images, columns=arguments.columns, cell_width=arguments.cell_width)
    target = document.folder / "crops" / f"crop-sheet-{arguments.name}.png"
    ok, buffer = cv2.imencode(".png", sheet)
    if not ok:
        raise SystemExit("cannot encode the crop sheet")
    target.write_bytes(buffer.tobytes())
    print(json.dumps({"sheet": str(target), "crops": [c.crop_id for c in selected]}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Source V2 canonical crops")
    parser.add_argument("--document", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    cut = subparsers.add_parser("cut", help="cut a canonical crop for every region")
    cut.add_argument("--pages", default=None)
    cut.set_defaults(handler=command_cut)

    sheet = subparsers.add_parser("sheet", help="contact sheet of the crops, for eyes")
    sheet.add_argument("--only", nargs="*", default=[])
    sheet.add_argument("--columns", type=int, default=3)
    sheet.add_argument("--cell-width", type=int, default=520)
    sheet.add_argument("--name", default="all")
    sheet.set_defaults(handler=command_sheet)

    arguments = parser.parse_args()
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
