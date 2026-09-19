# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "jsonschema==4.25.1",
#   "numpy==2.2.6",
#   "opencv-python-headless==4.12.0.88",
# ]
# ///
"""Source V2 reader benchmark CLI (no model dependencies).

    uv run tools/source_factory/readers/cli.py crops --pages 156,186,171,4
    uv run tools/source_factory/readers/cli.py sheet
    uv run tools/source_factory/readers/cli.py report

Running an actual model reader needs torch and lives in `bench.py`, which
declares its own heavier dependency set.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.source_factory.layout.corpus import load_document  # noqa: E402
from tools.source_factory.readers import crops as crop_tools  # noqa: E402
from tools.source_factory.readers import groundtruth  # noqa: E402


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


def command_crops(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    extracted = crop_tools.extract(document, parse_pages(arguments.pages))
    by_type: dict[str, int] = {}
    for crop in extracted:
        by_type[crop.region_type] = by_type.get(crop.region_type, 0) + 1
    print(
        json.dumps(
            {
                "root": str(document.folder / "readers" / "crops"),
                "crops": len(extracted),
                "by_type": by_type,
                "pages": sorted({crop.page_number for crop in extracted}),
            },
            indent=2,
        )
    )
    return 0


def command_sheet(arguments: argparse.Namespace) -> int:
    import cv2

    from tools.source_factory.layout.preview import contact_sheet

    document = load_document(arguments.document)
    extracted = crop_tools.load(document)
    selected = [crop for crop in extracted if crop.crop_id in set(arguments.only)] or extracted
    images = [(crop.crop_id, crop_tools.read_page_image(crop.path)) for crop in selected]
    sheet = contact_sheet(images, columns=arguments.columns, cell_width=arguments.cell_width)
    target = document.folder / "readers" / f"crop-sheet-{arguments.name}.png"
    ok, buffer = cv2.imencode(".png", sheet)
    if not ok:
        raise SystemExit("cannot encode the crop sheet")
    target.write_bytes(buffer.tobytes())
    print(json.dumps({"sheet": str(target), "crops": [c.crop_id for c in selected]}))
    return 0


def command_groundtruth(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    references = groundtruth.load(document)
    extracted = crop_tools.load(document)
    covered = [crop.crop_id for crop in extracted if crop.crop_id in references]
    print(
        json.dumps(
            {
                "file": str(groundtruth.path(document)),
                "references": len(references),
                "crops": len(extracted),
                "covered": covered,
                "uncovered": [c.crop_id for c in extracted if c.crop_id not in references],
            },
            indent=2,
        )
    )
    return 0


def command_rescore(arguments: argparse.Namespace) -> int:
    """Recompute every measurement from the stored readings.

    Adding a reference must not require re-running the models: the readings are
    the evidence and they do not change.
    """

    from tools.source_factory.readers import metrics

    document = load_document(arguments.document)
    references = groundtruth.load(document)
    sizes = {
        crop.crop_id: crop_tools.read_page_image(crop.path).shape
        for crop in crop_tools.load(document)
    }
    folder = document.folder / "readers" / "results"
    changed = []
    for path in sorted(folder.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        summary = metrics.ReaderSummary(reader=report["reader"])
        rows = []
        for row in report.get("crops", []):
            shape = sizes.get(row["crop_id"], (1, 1, 3))
            entry = references.get(row["crop_id"], {})
            measurement = metrics.measure(
                crop_id=row["crop_id"],
                reader=report["reader"],
                region_type=row["region_type"],
                text=row.get("text", ""),
                seconds=float(row.get("seconds", 0.0)),
                abstained=bool(row.get("abstained")),
                failure=row.get("failure"),
                pixels=shape[0] * shape[1],
                dpi=document.dpi,
                language=report.get("language", "sinhala"),
                reference=entry.get("text"),
                peak_vram_bytes=row.get("peak_vram_bytes"),
            )
            must, must_not = groundtruth.token_checks(entry)
            extra: dict = {}
            if must or must_not:
                hits = [token for token in must if token in row.get("text", "")]
                strays = [token for token in must_not if token in row.get("text", "")]
                measurement.critical_exact = len(hits) == len(must) and not strays
                extra = {
                    "expected_tokens": must,
                    "found_tokens": hits,
                    "stray_tokens": strays,
                }
            summary.add(measurement)
            rows.append(measurement.to_json() | extra | {"text": row.get("text", "")})
        report["crops"] = rows
        report["summary"] = summary.to_json()
        report["rescored"] = True
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(report["summary"])
    print(json.dumps(changed, indent=2))
    return 0


def command_report(arguments: argparse.Namespace) -> int:
    document = load_document(arguments.document)
    results = document.folder / "readers" / "results"
    if not results.exists():
        raise SystemExit(f"no benchmark results yet: {results}")
    summaries = []
    for path in sorted(results.glob("*.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8")).get("summary"))
    print(json.dumps(summaries, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Source V2 reader benchmark")
    parser.add_argument("--document", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    crops = subparsers.add_parser("crops", help="cut region crops from the benchmark pages")
    crops.add_argument("--pages", default=None)
    crops.set_defaults(handler=command_crops)

    sheet = subparsers.add_parser("sheet", help="contact sheet of the crops, for eyes")
    sheet.add_argument("--only", nargs="*", default=[])
    sheet.add_argument("--columns", type=int, default=3)
    sheet.add_argument("--cell-width", type=int, default=520)
    sheet.add_argument("--name", default="all")
    sheet.set_defaults(handler=command_sheet)

    truth = subparsers.add_parser("groundtruth", help="show reference coverage")
    truth.set_defaults(handler=command_groundtruth)

    rescore = subparsers.add_parser(
        "rescore", help="recompute measurements from stored readings, no model needed"
    )
    rescore.set_defaults(handler=command_rescore)

    report = subparsers.add_parser("report", help="print every stored reader summary")
    report.set_defaults(handler=command_report)

    arguments = parser.parse_args()
    return arguments.handler(arguments)


if __name__ == "__main__":
    sys.exit(main())
