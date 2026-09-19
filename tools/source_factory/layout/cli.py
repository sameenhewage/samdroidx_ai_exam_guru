# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "numpy==2.2.6",
#   "opencv-python-headless==4.12.0.88",
#   "pillow==11.3.0",
#   "pymupdf==1.26.4",
# ]
# ///
"""Source V2 Phase 1 layout CLI.

    uv run tools/source_factory/layout/cli.py contact-sheet --pages 150-165
    uv run tools/source_factory/layout/cli.py detect --pages 156,186
    uv run tools/source_factory/layout/cli.py benchmark

Output goes to the gitignored document folder under `.exam-guru-data`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.source_factory.layout import benchmark as benchmark_set  # noqa: E402
from tools.source_factory.layout.corpus import Document, load_document  # noqa: E402
from tools.source_factory.layout.detect import analyse_page, detect_layout  # noqa: E402
from tools.source_factory.layout.preview import (  # noqa: E402
    annotate,
    annotate_elements,
    contact_sheet,
)


def read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot decode image: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise SystemExit(f"cannot encode image: {path}")
    buffer.tofile(str(path))


def parse_pages(spec: str | None, document: Document) -> list[int]:
    if not spec:
        return [page for page, _ in benchmark_set.PAGES]
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
    available = set(document.page_numbers())
    return [page for page in pages if page in available]


def command_contact_sheet(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    pages = parse_pages(arguments.pages, document)
    if not pages:
        raise SystemExit("no pages selected")
    images = [(f"p{page}", read_image(document.page(page).path)) for page in pages]
    sheet = contact_sheet(images, columns=arguments.columns, cell_width=arguments.cell_width)
    target = document.layout_root / "contact-sheets" / f"{arguments.name}.png"
    write_image(target, sheet)
    print(json.dumps({"sheet": str(target), "pages": pages}))
    return 0


def command_detect(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    pages = parse_pages(arguments.pages, document)
    if not pages:
        raise SystemExit("no pages selected")
    summary = []
    previews: list[tuple[str, np.ndarray]] = []
    for page_number in pages:
        page = document.page(page_number)
        image = read_image(page.path)
        layout = detect_layout(
            image,
            document_id=document.document_id,
            page_number=page_number,
            dpi=document.dpi,
            image_sha256=page.sha256,
        )
        json_target = document.layout_root / "regions" / f"page-{page_number:03d}.json"
        json_target.parent.mkdir(parents=True, exist_ok=True)
        json_target.write_text(
            json.dumps(layout.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        preview = annotate(image, layout, max_side=arguments.preview_width)
        preview_target = document.layout_root / "previews" / f"page-{page_number:03d}.png"
        write_image(preview_target, preview)
        previews.append((f"p{page_number}", preview))
        summary.append(
            {
                "page": page_number,
                "regions": len(layout.regions),
                "columns": layout.diagnostics.get("max_columns"),
                "bands": layout.diagnostics.get("bands"),
                "types": _type_counts(layout),
            }
        )
    if arguments.sheet and previews:
        sheet = contact_sheet(previews, columns=arguments.columns, cell_width=arguments.cell_width)
        write_image(document.layout_root / "contact-sheets" / f"{arguments.name}.png", sheet)
    print(json.dumps({"document": document.document_id, "pages": summary}, indent=2))
    return 0


def _type_counts(layout) -> dict[str, int]:
    counts: dict[str, int] = {}
    for region in layout.regions:
        counts[region.type] = counts.get(region.type, 0) + 1
    return counts


def command_elements(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    pages = parse_pages(arguments.pages, document)
    summary = []
    for page_number in pages:
        page = document.page(page_number)
        image = read_image(page.path)
        analysis = analyse_page(image, document.dpi)
        target = document.layout_root / "elements" / f"page-{page_number:03d}.png"
        write_image(target, annotate_elements(image, analysis.elements, arguments.preview_width))
        containers = [
            {"bbox": element.box.as_list(), "area": element.box.area}
            for element in analysis.elements
            if element.kind == "graphic" and element.container
        ]
        artwork = [
            {"bbox": element.box.as_list(), "area": element.box.area}
            for element in analysis.elements
            if element.is_artwork
        ]
        summary.append(
            {
                "page": page_number,
                "counts": analysis.counts,
                "median_line_height": analysis.context.median_line_height,
                "content": analysis.context.content.as_list(),
                "containers": containers[:12],
                "artwork": sorted(artwork, key=lambda item: -item["area"])[:8],
            }
        )
    print(json.dumps(summary, indent=2))
    return 0


def command_benchmark(arguments: argparse.Namespace) -> int:
    arguments.pages = ",".join(str(page) for page, _ in benchmark_set.PAGES)
    arguments.sheet = True
    arguments.name = "benchmark"
    return command_detect(arguments)


def main() -> int:
    parser = argparse.ArgumentParser(description="Source V2 layout segmentation")
    parser.add_argument("--document", type=Path, default=None, help="document folder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sheet = subparsers.add_parser("contact-sheet", help="downscaled raw page contact sheet")
    sheet.add_argument("--pages", default=None)
    sheet.add_argument("--columns", type=int, default=5)
    sheet.add_argument("--cell-width", type=int, default=420)
    sheet.add_argument("--name", default="pages")
    sheet.set_defaults(handler=command_contact_sheet)

    detect = subparsers.add_parser("detect", help="segment pages and write annotated previews")
    detect.add_argument("--pages", default=None)
    detect.add_argument("--columns", type=int, default=4)
    detect.add_argument("--cell-width", type=int, default=520)
    detect.add_argument("--preview-width", type=int, default=1500)
    detect.add_argument("--name", default="detected")
    detect.add_argument("--sheet", action="store_true")
    detect.set_defaults(handler=command_detect)

    elements = subparsers.add_parser("elements", help="debug view of raw structural elements")
    elements.add_argument("--pages", default=None)
    elements.add_argument("--preview-width", type=int, default=1500)
    elements.set_defaults(handler=command_elements)

    bench = subparsers.add_parser("benchmark", help="segment the fixed Phase 1 benchmark pages")
    bench.add_argument("--columns", type=int, default=4)
    bench.add_argument("--cell-width", type=int, default=520)
    bench.add_argument("--preview-width", type=int, default=1500)
    bench.set_defaults(handler=command_benchmark)

    arguments = parser.parse_args()
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
