"""Locate rendered benchmark pages on the local Studio disk.

Rendered pages live under the gitignored `.exam-guru-data` area and are produced
by `scripts/source_pipeline/render_pdf.py`. Nothing here is committed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / ".exam-guru-data" / "source-content"
DEFAULT_DOCUMENT = DATA_ROOT / "grade-05" / "sinhala" / "mawbasa-teacher-guide"


@dataclass(frozen=True)
class RenderedPage:
    document_id: str
    page_number: int
    path: Path
    width: int
    height: int
    sha256: str


@dataclass(frozen=True)
class Document:
    folder: Path
    document_id: str
    dpi: float
    pages: dict[int, RenderedPage]

    @property
    def layout_root(self) -> Path:
        return self.folder / "layout"

    def page(self, number: int) -> RenderedPage:
        if number not in self.pages:
            raise SystemExit(f"page {number} is not rendered under {self.folder / 'rendered'}")
        return self.pages[number]

    def page_numbers(self) -> list[int]:
        return sorted(self.pages)


def load_document(folder: Path | None = None) -> Document:
    folder = (folder or DEFAULT_DOCUMENT).resolve()
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    render = manifest.get("render") or {}
    rendered = folder / "rendered"
    pages: dict[int, RenderedPage] = {}
    for entry in render.get("pages", []):
        number = entry.get("page_number")
        path = rendered / str(entry.get("file", ""))
        if number is None or not path.exists():
            continue
        pages[int(number)] = RenderedPage(
            document_id=str(manifest.get("document_id", folder.name)),
            page_number=int(number),
            path=path,
            width=int(entry.get("width", 0)),
            height=int(entry.get("height", 0)),
            sha256=str(entry.get("sha256", "")),
        )
    if not pages:
        raise SystemExit(f"no rendered pages recorded in {manifest_path}")
    return Document(
        folder=folder,
        document_id=str(manifest.get("document_id", folder.name)),
        dpi=float(render.get("dpi", 300.0)),
        pages=pages,
    )


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
