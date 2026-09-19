"""Build a local human ground-truth harness for one dense prose page.

Usage:
    python scripts/source_pipeline/ground_truth_harness.py <document-folder> \
        --page 186 --lines 12 [--band 0.10 0.12 0.94 0.78]

Slices the rendered page into single text lines by deterministic horizontal
projection, writes them next to a static `index.html`, and opens nothing. A human
then types what is visibly printed for each line.

Known limitation: horizontal projection merges side-by-side columns into one
strip. Automatic gutter detection was attempted and failed on page 186, where
figures cross the gutter, so it was reverted rather than shipped. Use a
single-column page, or pass a `--band` covering one column only.

Verified clean on page 156 with --band 0.08 0.10 0.95 0.92: twelve single-line
crops, no column merging and no clipped glyphs.

The text fields are deliberately EMPTY. No Tesseract, TrOCR, Qwen, Ornith, Luna
or Candidate A output is ever loaded here, so the reference stays independent of
every prediction it will later score.

Output lives under `benchmark/ground-truth/` inside the document folder. It is
benchmark data, never production Verified Source Content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

PAGE_HTML = """<!doctype html>
<html lang="si">
<meta charset="utf-8">
<title>Sinhala ground truth</title>
<style>
 :root { color-scheme: light dark; }
 body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 24px; }
 h1 { font-size: 18px; margin: 0 0 4px; }
 .note { opacity: .75; margin-bottom: 20px; max-width: 70ch; }
 .row { display: grid; grid-template-columns: 1fr; gap: 10px; padding: 16px 0; border-top: 1px solid #8884; }
 .row.done { opacity: .55; }
 img { width: 100%; image-rendering: -webkit-optimize-contrast; background: #fff; border: 1px solid #8884; }
 textarea { width: 100%; min-height: 64px; font-size: 22px; font-family: "Iskoola Pota", "Nirmala UI", sans-serif; padding: 8px; }
 .bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
 button { font: inherit; padding: 6px 14px; cursor: pointer; }
 .status { margin-left: auto; font-variant-numeric: tabular-nums; }
 .sticky { position: sticky; top: 0; background: Canvas; padding: 12px 0; z-index: 2; border-bottom: 1px solid #8884; }
</style>
<h1>Type exactly what is printed</h1>
<p class="note">
 Copy the visible characters only. Do not correct spelling, do not translate, do not
 tidy punctuation or spacing, and do not fill anything that is not printed. If a line is
 genuinely unreadable, press <b>Unreadable</b> instead of guessing. Nothing here is
 pre-filled by any OCR engine or model on purpose.
</p>
<div class="sticky bar">
 <button id="export">Export ground truth JSON</button>
 <button id="clear">Clear all drafts</button>
 <span class="status" id="status"></span>
</div>
<div id="rows"></div>
<script>
const KEY = "eg-ground-truth-" + DOC + "-p" + PAGE;
const store = JSON.parse(localStorage.getItem(KEY) || "{}");
const rows = document.getElementById("rows");
const status = document.getElementById("status");

function persist() {
  localStorage.setItem(KEY, JSON.stringify(store));
  const done = LINES.filter(l => store[l.id] && (store[l.id].confirmed || store[l.id].unreadable)).length;
  status.textContent = done + " / " + LINES.length + " confirmed";
}

for (const line of LINES) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML =
    '<img src="' + line.file + '" alt="line ' + line.index + '">' +
    '<textarea spellcheck="false" placeholder="Type the printed text for line ' + line.index + '"></textarea>' +
    '<div class="bar">' +
      '<button data-a="save">Save draft</button>' +
      '<button data-a="confirm">Confirm line</button>' +
      '<button data-a="unreadable">Unreadable</button>' +
      '<span class="status"></span>' +
    '</div>';
  const area = row.querySelector("textarea");
  const state = row.querySelector(".bar .status");
  const saved = store[line.id];
  if (saved) {
    area.value = saved.text || "";
    if (saved.confirmed) { row.classList.add("done"); state.textContent = "confirmed"; }
    if (saved.unreadable) { row.classList.add("done"); state.textContent = "unreadable"; }
  }
  row.querySelector('[data-a="save"]').onclick = () => {
    store[line.id] = { text: area.value, confirmed: false, unreadable: false };
    state.textContent = "draft saved"; row.classList.remove("done"); persist();
  };
  row.querySelector('[data-a="confirm"]').onclick = () => {
    store[line.id] = { text: area.value, confirmed: true, unreadable: false };
    state.textContent = "confirmed"; row.classList.add("done"); persist();
  };
  row.querySelector('[data-a="unreadable"]').onclick = () => {
    store[line.id] = { text: "", confirmed: false, unreadable: true };
    state.textContent = "unreadable"; row.classList.add("done"); persist();
  };
  rows.appendChild(row);
}
persist();

document.getElementById("clear").onclick = () => {
  if (confirm("Delete every draft on this page?")) { localStorage.removeItem(KEY); location.reload(); }
};
document.getElementById("export").onclick = () => {
  const payload = {
    kind: "human ground truth benchmark reference",
    note: "Typed by a human from the visible original. Never derived from OCR or model output.",
    document_id: DOC, page_number: PAGE, created_at: new Date().toISOString(),
    lines: LINES.map(l => ({
      id: l.id, index: l.index, file: l.file, sha256: l.sha256,
      text: (store[l.id] || {}).text || "",
      confirmed: !!(store[l.id] || {}).confirmed,
      unreadable: !!(store[l.id] || {}).unreadable
    }))
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ground-truth-" + DOC + "-p" + String(PAGE).padStart(3, "0") + ".json";
  a.click();
};
</script>
</html>
"""


def segment(band: np.ndarray, minimum_height: int = 12) -> list[tuple[int, int]]:
    grey = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    ink = (grey < 160).sum(axis=1)
    threshold = max(3, int(0.004 * band.shape[1]))
    rows: list[tuple[int, int]] = []
    start: int | None = None
    for y, value in enumerate(ink):
        if value > threshold and start is None:
            start = y
        elif value <= threshold and start is not None:
            if y - start >= minimum_height:
                rows.append((start, y))
            start = None
    if start is not None and len(ink) - start >= minimum_height:
        rows.append((start, len(ink)))
    return rows


def build(folder: Path, page_number: int, wanted: int, band_box: tuple[float, ...]) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    source = folder / "rendered" / f"page-{page_number:03d}.png"
    if not source.exists():
        raise SystemExit(f"missing rendered page: {source}")
    page = cv2.imdecode(np.frombuffer(source.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    height, width = page.shape[:2]
    x0, y0, x1, y1 = band_box
    band = page[round(y0 * height):round(y1 * height), round(x0 * width):round(x1 * width)]

    out = folder / "benchmark" / "ground-truth" / f"page-{page_number:03d}"
    (out / "lines").mkdir(parents=True, exist_ok=True)
    entries = []
    for index, (top, bottom) in enumerate(segment(band)):
        if len(entries) >= wanted:
            break
        pad = 6
        crop = band[max(0, top - pad):min(band.shape[0], bottom + pad), :]
        ok, buffer = cv2.imencode(".png", crop)
        if not ok:
            continue
        payload = buffer.tobytes()
        name = f"lines/line-{index:02d}.png"
        (out / name).write_bytes(payload)
        entries.append({
            "id": f"p{page_number:03d}-l{index:02d}", "index": index, "file": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "pixels": [int(crop.shape[1]), int(crop.shape[0])],
        })
    if not entries:
        raise SystemExit("no text lines were found in the requested band")

    document_id = manifest["document_id"]
    html = (
        f"<script>const DOC={json.dumps(document_id)};const PAGE={page_number};"
        f"const LINES={json.dumps(entries, ensure_ascii=False)};</script>\n" + PAGE_HTML
    )
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "lines.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"document_id": document_id, "page": page_number, "lines": len(entries), "open": str(out / "index.html")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a human Sinhala ground-truth harness.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--lines", type=int, default=12)
    parser.add_argument("--band", type=float, nargs=4, default=(0.10, 0.12, 0.94, 0.80),
                        metavar=("X0", "Y0", "X1", "Y1"))
    arguments = parser.parse_args()
    if not 1 <= arguments.lines <= 100:
        raise SystemExit("lines must be between 1 and 100")
    print(json.dumps(build(arguments.folder.resolve(), arguments.page, arguments.lines, tuple(arguments.band)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
