# Source V2 — Execution State

Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
Locked decisions: `docs/source-v2/DECISIONS.md`

```
phase:          7 — 156/186 green; sankhya-rata BLOCKED on session independence
status:         Full-stack restart verified. sankhya-rata needs a fresh agent session.
last_validated: dabec0b
updated:        2026-09-19
```

## BLOCKED: sankhya-rata needs a genuinely fresh agent session

Its 17 canonical crops are cut and waiting under `sankhya-rata/crops/`. Nothing
else can be done for it here.

D17's independence rule says: do not read previous text before transcribing.
The agent session that ran the D17/D18 work **wrote sankhya-rata's earlier
transcripts in the same conversation**, so any transcription it produces now is
anchored no matter how carefully the crops are read. Continuing in the same
conversation does not reset that — a *new* session is required, one that has
never seen the old text.

Prompts 02, 03 and 04 each asked for this rebuild in that same conversation, so
it was correctly refused three times rather than producing a read that only
looks independent. **The next session must be a new conversation** and may open
only: the original PDF, the rendered page, the layout, and `crops/`.


---

## THE CURRENT ARCHITECTURE (D17 + D18)

This section describes what actually executes today. Everything else in this
file is evidence for it.

```
immutable source PDF
  -> deterministic 300 dpi render
  -> deterministic layout                (geometry only, no text)
  -> canonical crop for EVERY region     <document>/crops/crop-NNN-rNNN.png
  -> THE EXECUTING AGENT OPENS THE CROP AND WRITES THE TEXT
  -> seal: schema + source/render/crop checksums
  -> deterministic validators            (flag only, never rewrite)
  -> Machine Candidate = the primary text, plus a proposed source kind
  -> human Confirm / Correct / Confirm-visual / Reclassify / Exclude
  -> Verified Source Content
  -> only then: knowledge, embeddings, RAG, generation
```

**There is no OCR in the active pipeline.** Not DeepSeek, not LightOnOCR, not
Tesseract, not Qwen/Ornith/Luna, no prefill, no consensus, no voting. Code
renders, segments, crops, checksums, validates, persists, gates and displays.
**Code never produces source text.**

**The canonical crop is the only region image anyone may read from** — the
agent transcribing and the reviewer confirming look at the same file. Sealing
hard fails on a missing crop, a checksum mismatch, or a bbox that no longer
matches the layout. Re-cutting a crop by hand is what produced five wrong
regions on page 186.

**Every region carries a source kind (D18).** `text_only`, `visual_only`,
`visual_with_text`, `decorative`, `undecided`. A figure with no printed text
is real source content, not an empty mistake; `figure` alone never implies
`visual_only`. The machine proposes from region type and whether text was
transcribed, and only a human decides.

Historical OCR measurements are kept in `docs/source-v2/BENCHMARK_READERS.md`
and under `.exam-guru-data/_archive/` as the evidence for *why* the readers
were removed. They are not instructions. Anything elsewhere in this repository
that tells you to run a reader, or to read from `readers/crops`, is superseded.

---

## exact next step

1. **Rebuild `sankhya-rata` from zero** under D17 + D18. Its old `usable:true`
   is gone with the data reset and must be re-earned. Render, layout, canonical
   crops for every region, then read each crop directly — no archived text, no
   old candidates, no OCR. Classify its clip-art honestly rather than repeating
   the historical "1 figure excluded": *contains no text* is not a reason to
   exclude an educational image.
2. Then corpus migration, which is still blocked on the scaling decision
   recorded below.
3. Legacy V1 cleanup last.

---

## acceptance: pages 156 and 186 under D17 + D18

Both fully resolved through the Studio with Chrome DevTools MCP, and persisted
through reload.

| page | verified | excluded | unverified |
|---|---|---|---|
| 156 | 6 | 2 (header/folio bars) | **0** |
| 186 | 6 | 2 (header/folio bars) | **0** |

Stored state on page 186, read back from PostgreSQL rather than the UI:

```
p186-r001 text_only         796 chars  crop 2305c1eb  bbox [408,464,1203,2070]
p186-r002 visual_only         0 chars  crop dd117945  bbox [467,2087,1077,2421]
p186-r003 visual_with_text   56 chars  crop 125cbda9  bbox [349,2461,1168,2957]
p186-r004 text_only          49 chars  crop 09413a84  bbox [350,3009,1141,3042]
p186-r005 text_only         716 chars  crop dec362b8  bbox [1335,461,2130,1885]
p186-r006 text_only         560 chars  crop a57e789e  bbox [1335,1961,2131,2946]
review events: confirm 6, exclude 2   OCR reader rows in the active workflow: 0
```

`p186-r002` is the case that previously had no correct outcome: an educational
drawing with no printed text could only be excluded. It is now **verified
source content with zero characters**, naming the crop it was confirmed
against.

Overlay alignment measured against the API bbox at three viewports: **max
error 0.02px**. Editing is stable — opening the corrector, focusing and
clicking inside it leaves both panes at `scrollTop 123 / 858`. Console clean
apart from a pre-existing form-field advisory; all network requests 200.

## defects found by running it, all fixed

Each surfaced in the real Studio, not in a test.

- `_region_facts` read `bbox` from the candidate, where it has never existed.
  Geometry belongs to the deterministic layout.
- `ck_source_v2_no_verified_abstention` blocked the entire visual case.
  `p186-r002` abstains because the figure carries no printed text — the
  correct reading, not a failure. Migration 0059 narrows the rule instead of
  dropping it: an abstained candidate may reach `verified` only when it is
  `visual_only`.
- Migration 0057 put `crop_sha256` only on the verified row, which *copies*
  from the candidate, so a visual could never actually have been confirmed.
  0058 adds it to candidates.
- Restoring transcripts picked the wrong archive because `Sort-Object Name`
  ranks `superseded` above `preD17`, silently reverting two regions to earlier
  wrong text. Caught by reading the Studio rather than trusting publish output.

## honest caveats

- **Independence was not achievable for 156/186.** The rule is "do not read
  previous text before transcribing". Those two pages were re-read in a session
  that had already seen their earlier transcripts. The text was re-verified
  against the canonical crops, but a genuinely unanchored re-read needs a fresh
  session. `sankhya-rata` is the first document where independence can actually
  hold.
- **The primary CER of 0.0 is circular** wherever the same party wrote the
  reading and confirmed it. It shows only that the candidate carried the
  primary reading through unmutated. `benchmark_primary.py` reads
  `benchmark/independent-groundtruth.json` when present and refuses it if
  `reviewer` names the agent.
- **Nothing is vectorised.** D18 implements the modality *gate* and chooses no
  embedding model. A verified `visual_only` region with no image embedding is a
  valid state, not a gap to fill with synthetic text.

## scaling: primary-first does not scale like OCR-first

The agent read is the bottleneck, not the GPU: reading a page costs image
context that does not compress, so a session covers roughly two to three pages
of this density. The 293-page guide is on the order of a hundred sessions.

Options, still awaiting a product decision:

1. Migrate only the pages the product actually uses — the gate already makes
   everything else unusable, so nothing leaks.
2. **Split the guide into per-lesson documents** so a lesson reaches
   `usable:true` on its own. Cheapest, weakens no rule.
3. Two explicit tiers of source trust — contradicts D17 and would need a new
   decision, not silence.

## blockers

- **Corpus migration** — the scaling decision above. Engineering is ready.
- **Independent ground truth** — needs a reviewer who did not write the
  primary reading.
- **Tamil** — no real source material. No synthetic data will be generated.

## running it

```bash
D=.exam-guru-data/source-content/grade-05/sinhala/<document>
uv run --no-project --with pymupdf==1.26.4 python scripts/source_pipeline/render_pdf.py $D
uv run tools/source_factory/layout/cli.py       --document $D detect --pages 1,2,3
uv run tools/source_factory/readers/cli.py      --document $D crops  --pages 1,2,3
#   read every crop under $D/crops yourself, write $D/primary/transcripts/page-NNN.json
uv run tools/source_factory/primary/cli.py      --document $D seal   --page 1
uv run tools/source_factory/candidate/cli.py    --document $D build
uv run tools/source_factory/publish_to_studio.py --document $D --document-id <uuid> [--refresh]
```
