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

## Why no Tamil reader was benchmarked

Decision D2 says readers are selected by measurement on real pages, and D11
says synthetic fixtures may never be used to claim real reading quality.
Benchmarking a Tamil VLM against Sinhala pages, or against generated Tamil
images, would produce a number that means nothing and would be worse than
having none. So no Tamil reader was selected.

## What is already in place for Tamil

The engineering is language-agnostic and waiting:

- `tools/source_factory/readers/port.py` — `ReadRequest.language` carries the
  language; the port has no Sinhala assumption.
- `tools/source_factory/readers/metrics.py` — `foreign_script_ratio` already
  scores Tamil, and `TAMIL` covers `U+0B80–U+0BFF`.
- `tools/source_factory/candidate/selection.py` — selection is keyed by
  `(language, region_type)`; a Tamil row is a data addition, not a code change.
- The layout detector is script-independent: it works on ink geometry.

## Exact unblock step

1. Place real Tamil-medium source PDFs under `RAG DATA/` and render them with
   `scripts/source_pipeline/render_pdf.py`.
2. Choose a fixed Tamil benchmark page set the same way as the Sinhala one:
   single-column, two-column, a table, a figure page.
3. Add Tamil reader classes beside `SinhalaLightOnOCRReader` and benchmark the
   candidates on those pages.
4. Add the measured `("tamil", "*")` row to `selection.py`, citing the report.

Until step 1 happens, **Source V2 cannot claim a Tamil pass**, and this file is
the evidence for why.
