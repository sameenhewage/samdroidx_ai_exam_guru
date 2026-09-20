# Source V2 — Tamil path: real-data blocker

**Status: BLOCKED on source material, not on engineering. Recorded 2026-09-19.**

The master specification requires the Tamil path to be benchmarked *or* an
explicit real-data blocker to be documented. This is that blocker.

## What was surveyed

The whole local corpus under `RAG DATA/`:

```
RAG DATA/
  Grade 3/  grade 3 Buddhism, Catholicism, Christianity, English, Islam,
            Maths, Parisaraya, Sinhala, Teacher Guides (NIE)
  Grade 4/  grade 4 English, Maths, Parisaraya, Sinhala, Teacher Guides (NIE)
  Grade 5/  grade 5 - teachers guide book - sinhala medium, Buddhism, English,
            Maths, Parisaraya, Sinhala, Teacher Guides (NIE)
  sources/  3 checksum-addressed PDFs
```

A recursive search for `tamil` in every directory and PDF filename returns
nothing. There is no Tamil-medium material on this machine at all — not a
teacher guide, not a past paper, not a worksheet.

## Why there is no Tamil OCR reader

Historical note: local OCR readers were once going to be selected per language
by measurement. That whole subsystem has been removed — the executing AI agent
reading the canonical crop is the only machine source reader, for every
language. There is therefore nothing language-specific left to benchmark or
select.

## What is already in place for Tamil

The remaining engineering is language-agnostic and waiting:

- the layout detector is script-independent: it works on ink geometry;
- `tools/source_factory/crops/cutter.py` cuts canonical crops from geometry
  alone and has no script assumption;
- `schemas/source-content/primary-reading.schema.json` carries `language` per
  page and per region;
- `tools/source_factory/candidate/validators.py` applies its Sinhala-specific
  checks only when `language == "sinhala"`; a Tamil rule set is an addition,
  not a rewrite.

## Exact unblock step

1. Place real Tamil-medium source PDFs under `RAG DATA/` and render them with
   `scripts/source_pipeline/render_pdf.py`.
2. Detect layout and cut the canonical crops for the chosen pages.
3. Have the executing agent transcribe each crop and seal it with
   `tools/source_factory/primary/cli.py`.
4. Add Tamil-script validator rules alongside the Sinhala ones, then have a
   Tamil-reading human verify the regions in the Studio.

Until step 1 happens, **Source V2 cannot claim a Tamil pass**, and this file is
the evidence for why.
