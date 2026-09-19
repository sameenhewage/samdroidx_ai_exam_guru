"""Deterministically render one source PDF to page images.

Usage:
    python scripts/source_pipeline/render_pdf.py <document-folder> [--dpi 300]

The document folder holds `source/original.pdf` and `manifest.json`. Pages are
written to `rendered/page-NNN.png`. The original PDF is opened read-only and is
never mutated. Re-running is idempotent: a page whose rendered bytes already
match its recorded checksum is left untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

SCHEMA_VERSION = "1.0.0"
MAX_PAGES = 5000


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_manifest(folder: Path) -> dict:
    path = folder / "manifest.json"
    if not path.exists():
        raise SystemExit(f"missing manifest: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for field in ("document_id", "grade", "subject", "medium", "document_type"):
        if field not in manifest:
            raise SystemExit(f"manifest is missing required field {field!r}")
    return manifest


def render(folder: Path, dpi: float) -> dict:
    source = folder / "source" / "original.pdf"
    if not source.exists():
        raise SystemExit(f"missing source PDF: {source}")
    manifest = load_manifest(folder)
    payload = source.read_bytes()
    rendered = folder / "rendered"
    rendered.mkdir(parents=True, exist_ok=True)

    existing = {
        entry["page_number"]: entry
        for entry in (manifest.get("render") or {}).get("pages", [])
        if isinstance(entry, dict) and "page_number" in entry
    }
    pages: list[dict] = []
    written = skipped = 0
    with pymupdf.open(stream=payload, filetype="pdf") as document:
        if document.page_count > MAX_PAGES:
            raise SystemExit(f"page count {document.page_count} exceeds the {MAX_PAGES} bound")
        matrix = pymupdf.Matrix(dpi / 72.0, dpi / 72.0)
        for index in range(document.page_count):
            number = index + 1
            target = rendered / f"page-{number:03d}.png"
            previous = existing.get(number)
            if previous and target.exists() and digest(target.read_bytes()) == previous.get("sha256"):
                pages.append(previous)
                skipped += 1
                continue
            pixmap = document.load_page(index).get_pixmap(matrix=matrix, alpha=False)
            image = pixmap.tobytes("png")
            target.write_bytes(image)
            pages.append({
                "page_number": number,
                "file": target.name,
                "width": pixmap.width,
                "height": pixmap.height,
                "sha256": digest(image),
            })
            written += 1

    manifest["schema_version"] = SCHEMA_VERSION
    manifest["source"] = {"filename": source.name, "sha256": digest(payload), "bytes": len(payload)}
    manifest["page_count"] = len(pages)
    manifest["render"] = {
        "dpi": dpi,
        "engine": f"pymupdf {pymupdf.VersionBind}",
        "colour_space": "rgb",
        "rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pages": pages,
    }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"document_id": manifest["document_id"], "pages": len(pages), "written": written, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render one source PDF deterministically.")
    parser.add_argument("folder", type=Path, help="document folder containing source/original.pdf")
    parser.add_argument("--dpi", type=float, default=300.0, help="render DPI baseline (default 300)")
    arguments = parser.parse_args()
    if not 72 <= arguments.dpi <= 1200:
        raise SystemExit("dpi must be between 72 and 1200")
    print(json.dumps(render(arguments.folder.resolve(), arguments.dpi), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
