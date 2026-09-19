# Source V2 — STATE

phase: 2 — contracts + reader import (Phase 1 PASS)
status: contract landed; Astra import blocked on one input
last_commit: 92950a4 (Source V2 Phase 1 detector; pushed to origin/master)
updated: 2026-09-19

## completed

- fixed benchmark of 8 real Grade 5 Sinhala teacher-guide pages: 4, 152, 156, 157, 163, 171, 186, 197
- `tools/source_factory/layout/` detector: rendered page image -> typed regions, bboxes, reading order, column/parent
- region types: text | heading | figure | table | decorative | unknown. No OCR anywhere in this package.
- annotated previews + region JSON written per page and inspected by eye for all 8 pages
- 14 tests pass (6 synthetic structural + 8 real benchmark pages, skipped when corpus absent)

## acceptance (Phase 1)

1. page 156 segmentation visually usable — PASS (2 columns, column-major order, no clipping)
2. page 186 two-column segmentation visually usable — PASS (2 columns, 2 in-column figures separated from prose)
3. figure crossing the gutter does not collapse columns — PASS (page 157: diagram is one `figure`, columns below survive)
4. full-width decorative bars do not create false columns — PASS (pages 152/156/163: bars are `decorative`)
5. >= 6 real pages with acceptable annotated previews — PASS (8/8)

## blockers

- (none for Phase 1)

## known limitations

- reverse-out (white-on-colour) title bands classify as `decorative`, not `heading`
- long justified paragraphs occasionally split one line early (over-segmentation, not merging)
- a table with no rules and no tint is still segmented as columns of text
- tuned against one document; a second document should be benchmarked before trusting the constants

## phase 2 progress

- DONE: `schemas/source-content/page-layout.schema.json` — the geometry-only
  region contract. Every page written by `cli.py detect` is validated against it,
  plus dense reading order and parent-containment checks (`layout/contract.py`).
- Astra is the reader named alongside Codex in `schemas/source-content/source-page.schema.json`
  (`"Used unchanged by Codex/Astra, Qwen, Ornith and the machine candidate"`).
  So "Astra JSON" means a `source-page.schema.json` document produced by that reader.

## next exact step

Write `tools/source_factory/import_reader.py`:

- load one reader page JSON, validate it against `schemas/source-content/source-page.schema.json`
- load the matching `layout/regions/page-NNN.json`
- bind each reader region to a layout region by bbox overlap (IoU, then containment),
  carrying the layout's `reading_order`, `column` and `parent`
- report unbound reader regions and empty layout regions rather than guessing;
  an unbound region is evidence of a layout or reading error, not something to hide
- emit the joined document and validate it; do not merge, correct or normalise any text

BLOCKED until one real Astra output file for a benchmark page exists on disk.
No such file is present under `.exam-guru-data/`; ask for one, or run the Astra
reader over page 156 and 186 first.

## relevant files

- `tools/source_factory/layout/detect.py` — detector (masks, rules, glyphs, fragments, zones, columns, blocks, classification)
- `tools/source_factory/layout/model.py` — `Box`, `Region`, `PageLayout` contract
- `tools/source_factory/layout/corpus.py` — locates rendered pages on local disk
- `tools/source_factory/layout/preview.py` — annotated previews, element debug view, contact sheets
- `tools/source_factory/layout/benchmark.py` — fixed benchmark page list (do not edit to make results look better)
- `tools/source_factory/layout/contract.py` — schema + reading-order + parent validation
- `schemas/source-content/page-layout.schema.json` — the committed region contract
- `tools/source_factory/layout/cli.py` — `contact-sheet` | `detect` | `elements` | `benchmark`
- `tools/source_factory/layout/tests/test_detect.py`
- `.exam-guru-data/source-content/grade-05/sinhala/mawbasa-teacher-guide/` — rendered pages + `layout/` output (gitignored)

## commands

```
uv run tools/source_factory/layout/cli.py benchmark          # segment the 8 fixed pages + previews
uv run tools/source_factory/layout/cli.py detect --pages 156,186 --sheet
uv run tools/source_factory/layout/cli.py elements --pages 157   # raw element debug view
uv run tools/source_factory/layout/cli.py contact-sheet --pages 146-175
uv run tools/source_factory/layout/tests/test_detect.py      # 14 tests
```

Dependencies are declared inline (PEP 723) in `cli.py` and the test file, so
`uv run` needs no project environment. numpy must stay < 2.3 for opencv 4.12.

## algorithm notes (why, not what)

- whole-page vertical projection is **not** the primary algorithm
- two ink views: adaptive `ink` (black type survives on dark tinted bands) and `mark` (any non-paper pixel, so a gradient bar or tint panel is recovered whole)
- glyphs are smeared into line fragments, then merged across a **page-learned** word gap (Otsu over same-row gaps, clamped to 1.2-2.5 line heights) so justified Sinhala word spacing is bridged but a gutter never is
- artwork is closed into zones that absorb their own labels/pictograms, but only while the zone stays far too sparse to be prose
- columns come from a **row-tolerant** coverage profile: how many printed rows reach each pixel column, allowing 15% of rows to cross. One stray line or a decorative bar cannot veto a gutter.
- elements crossing a gutter become straddlers that cut the region into slabs, which keeps reading order column-major instead of interleaving band by band
- a filled panel that is packed with evenly sized lines is a prose container (recursed into, `parent` set); an outlined diagram quadrant with the same marks but low density is artwork
