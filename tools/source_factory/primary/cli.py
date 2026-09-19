# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Seal an agent transcription into a validated primary reading.

    uv run tools/source_factory/primary/cli.py seal --document <folder> --page 156

The agent writes what it read into

    <document>/primary/transcripts/page-NNN.json
    { "p156-r001": { "exact_text": "...", "uncertainty": [ ... ] }, ... }

and this command joins it to the deterministic layout (bbox, region type,
reading order), stamps the render and crop checksums, validates it against
`schemas/source-content/primary-reading.schema.json`, and writes

    <document>/primary/pages/page-NNN.json

The transcription must be made by looking at the original render or crop.
Seeding it from DeepSeek or LightOnOCR output is not an independent reading;
see `docs/source-v2/DECISIONS.md` D14.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from tools.source_factory.primary.model import (  # noqa: E402
    Cell,
    PrimaryPage,
    PrimaryReadingError,
    PrimaryRegion,
    Table,
    Uncertainty,
    sha256_of,
    write,
)


def _table(entry: dict | None) -> Table | None:
    """Grid structure for a table region, if the agent recorded one.

    A visually blank cell is carried through as blank; the model refuses any
    cell whose `blank` flag disagrees with its text.
    """

    if entry is None:
        return None
    return Table(
        bbox=tuple(entry["bbox"]),
        rows=int(entry["rows"]),
        columns=int(entry["columns"]),
        cells=tuple(
            Cell(
                row=int(cell["row"]),
                column=int(cell["column"]),
                bbox=tuple(cell["bbox"]),
                exact_text=cell.get("exact_text", ""),
                uncertainty=tuple(
                    Uncertainty(
                        kind=item["kind"],
                        detail=item["detail"],
                        excerpt=item.get("excerpt"),
                    )
                    for item in cell.get("uncertainty", [])
                ),
            )
            for cell in entry.get("cells", [])
        ),
    )


def load_layout(document: Path, page_number: int) -> dict:
    path = document / "layout" / "regions" / f"page-{page_number:03d}.json"
    if not path.exists():
        raise SystemExit(f"no layout for page {page_number}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def command_seal(arguments: argparse.Namespace) -> int:
    document = arguments.document.resolve()
    page_number = arguments.page
    layout = load_layout(document, page_number)

    transcript_path = (
        document / "primary" / "transcripts" / f"page-{page_number:03d}.json"
    )
    if not transcript_path.exists():
        raise SystemExit(
            f"no transcription at {transcript_path}\n"
            "Look at the rendered page or its crops and write down what is printed first."
        )
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    crops = document / "readers" / "crops"
    regions: list[PrimaryRegion] = []
    missing: list[str] = []
    for region in layout["regions"]:
        entry = transcript.get(region["id"])
        if entry is None:
            missing.append(region["id"])
            continue
        crop = crops / f"crop-{page_number:03d}-{region['id'].split('-')[-1]}.png"
        regions.append(
            PrimaryRegion(
                region_id=region["id"],
                region_type=region["type"],
                bbox=tuple(region["bbox"]),
                reading_order=region["reading_order"],
                language=arguments.language,
                exact_text=entry["exact_text"],
                source_image_sha256=layout["image_sha256"],
                uncertainty=tuple(
                    Uncertainty(
                        kind=item["kind"],
                        detail=item["detail"],
                        excerpt=item.get("excerpt"),
                    )
                    for item in entry.get("uncertainty", [])
                ),
                crop_sha256=sha256_of(crop) if crop.exists() else None,
                table=_table(entry.get("table")),
            )
        )
    if missing:
        raise SystemExit(
            f"page {page_number}: no transcription for {missing}. "
            "Every region the detector found must be read or explicitly marked."
        )

    source = document / "source" / "original.pdf"
    page = PrimaryPage(
        document_id=layout["document_id"],
        page_number=page_number,
        source_sha256=sha256_of(source) if source.exists() else layout["image_sha256"],
        image_sha256=layout["image_sha256"],
        render_dpi=float(layout["dpi"]),
        language=arguments.language,
        notes=arguments.notes,
        regions=regions,
    )
    target = document / "primary" / "pages" / f"page-{page_number:03d}.json"
    try:
        payload = write(target, page)
    except PrimaryReadingError as error:
        raise SystemExit(f"page {page_number}: {error}") from error

    print(
        json.dumps(
            {
                "primary": str(target),
                "page": page_number,
                "regions": len(payload["regions"]),
                "blank": sum(1 for r in payload["regions"] if not r["exact_text"].strip()),
                "uncertain": sum(1 for r in payload["regions"] if r["uncertain"]),
                "characters": sum(len(r["exact_text"]) for r in payload["regions"]),
            },
            indent=2,
        )
    )
    return 0


def command_show(arguments: argparse.Namespace) -> int:
    path = (
        arguments.document.resolve()
        / "primary"
        / "pages"
        / f"page-{arguments.page:03d}.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"# page {payload['page_number']}  render {payload['image_sha256'][:12]}")
    for region in payload["regions"]:
        head = f"=== {region['region_id']} [{region['region_type']}]"
        if region["uncertain"]:
            head += f"  uncertain: {[u[chr(39) + chr(39)] for u in []]}"
        print(head)
        print(region["exact_text"][: arguments.chars] or "(blank)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Primary visual reading")
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--language", default="sinhala")
    sub = parser.add_subparsers(dest="command", required=True)

    seal = sub.add_parser("seal", help="validate and store a transcription")
    seal.add_argument("--page", type=int, required=True)
    seal.add_argument("--notes", default="")
    seal.set_defaults(handler=command_seal)

    show = sub.add_parser("show", help="print a stored primary reading")
    show.add_argument("--page", type=int, required=True)
    show.add_argument("--chars", type=int, default=400)
    show.set_defaults(handler=command_show)

    arguments = parser.parse_args()
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    sys.exit(main())
