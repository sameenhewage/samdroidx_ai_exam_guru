# Source V2 — Master Rebuild Specification

**This file is the single source of truth for the Source V2 rebuild.**
`docs/source-v2/STATE.md` holds resumable execution state only.
`docs/source-v2/DECISIONS.md` holds locked decisions so they are not redesigned.

If this file conflicts with an older source-fidelity doc, this file wins for new
work; record the conflict rather than silently ignoring it.

---

## 1. Why V2 exists

V1 source reading failed for one structural reason: it asked general-purpose
providers to transcribe Sinhala pages they could not read, then tried to repair
the result downstream. Five sessions of evidence recorded in
`docs/v1/PHASE_TRACKER.md` show Tesseract producing the only coherent Sinhala
prose but corrupting numerals; Qwen, Ornith and Luna returning empty output,
inventing Sinhala, or substituting `www.moe.gov.lk` for the printed
`www.nie.lk`. No amount of consensus logic fixes a witness that cannot read.

V2 changes the input, not the repair: **language-specialist readers**, a
deterministic layout frame, provider-neutral comparison, and a human gate.

---

## 2. Final architecture (locked)

```
Raw PDF / Image
  │
  ├─ 1. deterministic render       (300 dpi, checksum-bound, idempotent)
  ├─ 2. deterministic layout       (regions: text/heading/figure/table/decorative/unknown)
  │                                 bbox + reading order + column + parent, NO text
  │
  ├─ 3. PRIMARY VISUAL READING  ◄── the executing agent reads the original pixels
  │      writes  <document>/primary/pages/page-NNN.json
  │      exact visible text, uncertainty, checksums, provenance
  │      NOT seeded with any local reader output
  │
  ├─ 4. deterministic validators  ◄── on the primary text, before any OCR
  │      brackets, mixed script, placeholders, NFC, expected script
  │
  ├─ 5. local readers: AUDIT ONLY ◄── warnings and disagreement evidence only
  │      run on the SAME original regions/crops, independently
  │      Sinhala : avishadilhara/sinhala-deepseek-ocr-Qlora     AUDIT_ONLY
  │                avishadilhara/sinhala-lightonocr-2-1b-Qlora  AUDIT_ONLY
  │      they may NEVER supply, replace, rewrite or outvote the primary text
  │
  ├─ 5. comparison / validation
  │      primary vs each secondary witness, token + character alignment,
  │      disagreement map, critical-token rules, deterministic validators
  │      NO blind majority voting, NO silent overwrite of the primary reading
  │
  ├─ 6. Machine Candidate           (one proposed reading per region, with evidence)
  │
  ├─ 7. human Confirm / Correct / Exclude
  │
  └─ 8. Verified Source Content     (immutable, versioned)
        │
        └─ ONLY THEN: educational analysis → knowledge → embeddings → RAG → generation
```

### Reader order (LOCKED — see D14 and D15)

**The executing agent is the primary source reader.** It looks at the original
rendered page or crop and writes the primary candidate itself.

**DeepSeek and LightOnOCR are AUDIT-ONLY.** Measured at 306–7506% character
error against human-confirmed source, they are not competing transcriptions;
they are audit signals. They may raise warnings, contribute disagreement
evidence and force human attention. They may never supply candidate text,
replace or rewrite the primary reading, or outvote it. There is no majority
voting anywhere in the pipeline.

Reversing this order is a regression, not an optimisation.

### Hard invariant

```
NO VERIFIED SOURCE CONTENT
  → NO EDUCATIONAL ANALYSIS
  → NO KNOWLEDGE
  → NO EMBEDDINGS
  → NO RAG
  → NO GENERATION
```

This must be enforced in code and proved by a test, not stated in a document.


### Verified visual source and vectorization (LOCKED — see D18)

Verified Source Content includes educational visuals, not only text. A figure
with no printed text is still source content when it carries educational
meaning.

Downstream source kinds are:

- `TEXT_ONLY`
- `VISUAL_ONLY`
- `VISUAL_WITH_TEXT`
- `DECORATIVE`

Vectorization is modality-specific and remains behind the verification gate:

```
TEXT_ONLY        -> verified text embedding
VISUAL_ONLY      -> verified canonical image/crop embedding
VISUAL_WITH_TEXT -> verified image/crop embedding + verified text embedding
DECORATIVE       -> no educational embedding
```

Additional hard invariants:

```
UNVERIFIED TEXT   -> NO TEXT EMBEDDING
UNVERIFIED VISUAL -> NO IMAGE EMBEDDING
```

Never fabricate source text for a visual-only region. If a model later
describes an image, that description is **Derived Knowledge**, never Verified
Source Content. If image embeddings are not yet implemented, the visual stays
verified but unembedded rather than being replaced by synthetic text.

Text/image vectors belonging to one region must preserve and link the same
source provenance: `document_id`, `page_number`, `region_id`, canonical
`bbox`, `crop_sha256`, plus explicit modality.

The embedding model/provider is intentionally deferred until the vectorization
phase; D18 locks the trust boundary and modality behavior, not a vendor.

### Provider rules

- **OpenAI must not be used for source OCR/extraction.** Not as primary, not as
  witness, not as "just a sanity check". Source reading is local + Codex/Astra only.
- Readers are selected **by language and region type using measured evidence**,
  never by published benchmark claims.
- Every reader sits behind one small first-party port. No SDK types in the domain.
- A reader result is *evidence*, never trust. Only a human creates Verified
  Source Content.

---

## 3. What is reused from V1 (and nothing else)

| Reused | Where it lives in V1 | Why it survives |
|---|---|---|
| Deterministic rendering | `scripts/source_pipeline/render_pdf.py` | Correct, idempotent, checksum-recorded |
| Source identities / checksums | document + page sha256 | Provenance is language-neutral |
| Human verification concepts | Confirm / Correct / Exclude, append-only review events | The workflow shape is right |
| Provider-neutral alignment | `documents/source_consensus.py::build_disagreement_map`, `align_source_tokens` | Genuinely provider-neutral; ported, not imported |

Everything else in the V1 source-reading path is replaced and then deleted.

---

## 4. Phases

Each phase runs the loop:
`IMPLEMENT → RUN → INSPECT REAL OUTPUT → VALIDATE → FIND DEFECTS → FIX → RERUN SAME CASE → VALIDATE → COMMIT → NEXT`.

### Phase 1 — Deterministic layout segmentation ✅

Rendered page image → typed regions with bbox, reading order, column, parent. No OCR.

**Acceptance**
1. page 156 segmentation visually usable
2. page 186 two-column segmentation visually usable
3. a figure crossing the gutter does not collapse columns
4. full-width decorative bars do not create false columns
5. ≥ 6 real pages with acceptable annotated previews

Naive whole-page vertical projection is forbidden as the primary algorithm.

### Phase 2 — Contracts + Codex/Astra import

- `schemas/source-content/page-layout.schema.json` — geometry contract, enforced on write.
- `schemas/source-content/source-page.schema.json` — reader transcription contract (exists).
- Importer: bind a reader's `source-page` document to a layout page by bbox
  overlap, carrying the layout's reading order / column / parent.
- Unbound reader regions and empty layout regions are **reported**, never hidden.
  An unbound region is evidence of a layout or reading defect.

**Acceptance:** one real Codex/Astra page imports, binds and validates; the
binding report names every unbound region.

### Phase 3 — Local reader benchmark (evidence before selection)

Build a reader port + a benchmark harness. For each candidate model, on the
**same real crops/pages**, measure:

- CER against human-confirmed ground truth where available
- hallucinations (content present in output, absent from the page)
- insertions / deletions / exact-match rate
- critical-token fidelity: digits, `×` vs `x`, URLs, e-mail spacing
- runtime per region and per page
- peak VRAM

Rules:
- do not assume a model wins because of its published benchmark
- report "could not run" as a result, with the exact blocker
- select readers **per language × region type** from the measured table
- no blind majority voting anywhere

**Acceptance:** a committed benchmark report with real numbers for every
attempted reader, and a written selection justified only by that table.

### Phase 4 — Provider-neutral comparison → Machine Candidate

- Port `build_disagreement_map` / `align_source_tokens` into the V2 package.
- Combine witnesses into one Machine Candidate per region with:
  - the proposed reading
  - per-token agreement / disagreement evidence
  - explicit `uncertain` marking where witnesses conflict on a critical token
  - explicit abstention where no witness is trustworthy (abstaining is correct)
- Never average text. Never vote blindly. A conflict pins that token; it does
  not make the whole region uncertain.

**Acceptance:** Machine Candidate produced for real pages 156 and 186 with a
disagreement map that survives inspection.

### Phase 5 — Persistence + human verification

- Source V2 tables: document, page, layout region, reader candidate, machine
  candidate, review event, verified source content.
- Append-only review events. Confirm / Correct / Exclude.
- A correction creates an unverified child candidate; confirmation binds the
  explicit original-page comparison to the current candidate and review version.
- Forward migrations only. Historical migrations are never deleted. Existing
  Studio data is preserved.
- Enforce the hard invariant in code with a test.

**Acceptance:** a page can be confirmed, corrected and excluded through the API;
state survives restart; the invariant test fails when bypass is attempted.

### Phase 6 — Studio UI + Chrome DevTools MCP acceptance

Machine first, human verifies. **The teacher must never be an OCR typist.**

Chrome DevTools MCP is mandatory from the moment UI/runtime integration starts
and used continuously, not once at the end:

```
open real Studio → inspect original source → run/import candidates
→ inspect page/layout/result → inspect Network → inspect Console
→ compare Machine Candidate to original → fix defects
→ restart/reload → rerun the SAME source → repeat
```

**Acceptance:** real Grade 5 Sinhala pages reviewed end to end in the real
Studio, clean Console, clean Network, and the Machine Candidate visibly
matching the original page.

### Phase 7 — Cleanup (only after V2 passes)

Remove:
- old source OCR/runtime code
- the obsolete OpenAI document-understanding **source** path
- Qwen / Ornith / Luna / Tesseract source-reading paths
- obsolete env vars, config, dependencies and their tests

Preserve:
- useful generic logic (rendering, identities, verification concepts, alignment)
- **all** historical migrations — use forward migrations
- existing Studio data

Update `AGENTS.md`, `README`, and docs to describe **only** the final
architecture. Two active source architectures must not coexist in the final
repository.

### Phase 8 — Final audit

Repository-wide grep proving no residual old source-reading path, no OpenAI
source-extraction call, no orphaned config, and docs describing one architecture.

---

## 5. Benchmarks and corpus

**Primary corpus:** Grade 5 Sinhala teacher guide, 293 pages rendered at 300 dpi
(`.exam-guru-data/source-content/grade-05/sinhala/mawbasa-teacher-guide/`).

**Fixed layout benchmark** (`tools/source_factory/layout/benchmark.py`):
pages 4, 152, 156, 157, 163, 171, 186, 197. Membership is fixed. If the detector
struggles on a page, fix the detector — never swap the page out.

**Reader benchmark:** region crops taken from those pages, covering prose,
heading, table cell, figure label and numeral/operator content.

**Tamil:** survey real Tamil source material in `RAG DATA/`. If no suitable real
Tamil page exists, that is an explicit documented blocker, not a silent skip.

Synthetic fixtures may prove pipeline mechanics only. They may never be used to
claim real Sinhala or Tamil reading quality.

---

## 6. Migration rules

- Forward migrations only; never delete or rewrite a historical migration.
- Never retrofit trust onto legacy rows.
- Preserve immutable originals, rendered evidence, and every candidate's raw
  UTF-8 evidence plus its separate NFC view.
- Never use NFKC to rewrite source text.
- Removing a material from use must not destroy provenance or audit history.
- A forward migration must not promote a legacy record to verified.

---

## 7. Execution rules

- Do not write plans and stop. After the master files exist, implement.
- Run every phase sequentially in long sessions; do not stop after one helper,
  schema, benchmark or phase if work can continue.
- Do not ask routine questions. Do not emit repeated long status reports.
- Spend context on implementation, runtime inspection, validation and fixes.
- Every defect becomes a failing regression test/eval **before** the fix.
- Commit a stable checkpoint at the end of each phase; push to the configured
  upstream and verify the remote contains it.
- If a technique fails: one short note in `STATE.md`, then the next
  evidence-based approach. Do not spend a session narrating a failure.
- If the session is forced to stop: leave the repo stable, commit, push, record
  the exact continuation point in `STATE.md`, and keep the handoff short.

## 8. Definition of SOURCE V2 PASS

All of the following, with evidence:

1. the real Sinhala pipeline works on real pages
2. the Tamil path is benchmarked, or an explicit real-data blocker is documented
3. the Machine Candidate works
4. human verification persists across restart
5. Chrome MCP real-page acceptance passes
6. the old source architecture is removed
7. the final repository audit is clean

Anything less is not a pass. Provider completion is not source accuracy, and an
inability to verify is never a PASS.
