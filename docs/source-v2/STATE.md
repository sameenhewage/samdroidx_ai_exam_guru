# Source V2 — STATE

Resumable execution state only.
Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
Locked decisions: `docs/source-v2/DECISIONS.md`

```
phase:          3 — local reader benchmark
status:         IN_PROGRESS
last_validated: c1046a5
updated:        2026-09-19
```

## completed

- **Phase 1 — layout segmentation: PASS.** `tools/source_factory/layout/`.
  8 fixed real pages (4, 152, 156, 157, 163, 171, 186, 197) segment correctly;
  all five acceptance criteria met and verified on annotated previews.
  Commit `92950a4`.
- **Phase 2 part 1 — layout contract: DONE.**
  `schemas/source-content/page-layout.schema.json` + `layout/contract.py`,
  enforced on every write (schema, dense reading order, parent containment).
  Commit `c1046a5`.

## blockers

- none

## exact next step

Phase 3. Build `tools/source_factory/readers/` with a reader port, then a
benchmark harness over region crops from the fixed benchmark pages.

```
uv run tools/source_factory/readers/cli.py crops --pages 156,186
uv run tools/source_factory/readers/cli.py benchmark --reader sinhala-lightonocr
```

Environment: RTX 3060, 12 GiB VRAM, driver 616.92. Torch is **not** installed in
the system interpreter; reader scripts must declare a CUDA torch build via PEP 723.

## notes

- `build_disagreement_map` / `align_source_tokens` to port from
  `apps/api/src/exam_guru_api/documents/source_consensus.py` (Phase 4, decision D10).
- Old source-reading surface to delete in Phase 7 is large: ~50 modules under
  `apps/api/src/exam_guru_api/documents/`, including `tesseract_ocr.py`,
  `source_reading_qwen.py`, `source_reading_openai.py`, `understanding_openai.py`.
