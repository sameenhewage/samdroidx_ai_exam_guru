# Source V2 — Locked Decisions

Decisions recorded here are **locked**. Do not redesign them in a later session.
To change one, add a new dated entry that supersedes it and say why the evidence
changed. Never silently reverse one.

Specification: `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`.

---

## D1 — Source reading is local + Codex/Astra. OpenAI is banned from it.
**Locked 2026-09-19.**
OpenAI must not be called for source OCR/extraction — not as primary, not as a
witness, not as a sanity check. Evidence: general providers hallucinated Sinhala
and substituted `www.moe.gov.lk` for the printed `www.nie.lk`. OpenAI remains
allowed for non-source work (generation, validation) where a human gate exists.


## D2 — Readers are language specialists, chosen by measurement.
**SUPERSEDED by D19 - there is exactly one machine source reader. Retained as history.**
**Locked 2026-09-19.**
Sinhala: `avishadilhara/sinhala-lightonocr-2-1b-Qlora`,
`avishadilhara/sinhala-deepseek-ocr-Qlora`. Tamil: selected by benchmark.
A published benchmark is not evidence. Selection is per **language × region
type** from our own measured table (CER, hallucinations, insertions, deletions,
exact match, critical tokens, runtime, VRAM).

## D3 — Layout is deterministic and carries no text.
**Locked 2026-09-19.**
Geometry is produced by deterministic code from the rendered image, never by a
model, and the layout contract explicitly holds no text. Naive whole-page
vertical projection is forbidden as the primary algorithm: columns come from a
row-tolerant coverage profile, and elements crossing a gutter become straddlers.

## D4 — No blind majority voting.
**Locked 2026-09-19. Narrowed by D19: there is nothing left to vote between.**
Witnesses are aligned token- and character-wise. A conflict pins the specific
token; it never makes the whole region uncertain. Where no witness is
trustworthy the Machine Candidate abstains. **Abstaining is a correct answer.**
Under D19 the only surviving clause is the last one: a region the agent read
as carrying no text abstains, and abstaining is a correct answer.

## D5 — Only a human creates Verified Source Content.
**Locked 2026-09-19.**
A reader result, a `can_confirm` diagnostic, model agreement, a legacy trusted
flag or a successful job is not ground truth. Confirmation binds an explicit
original-page comparison to the current candidate and review version. A
correction creates an unverified child candidate.

## D6 — Machine first; the teacher is not an OCR typist.
**Locked 2026-09-19.**
The human workflow is Confirm / Correct / Exclude over a Machine Candidate
presented beside the original page. Never present an empty box to type into.

## D7 — The hard invariant is enforced in code.
**Locked 2026-09-19.**
`NO VERIFIED SOURCE CONTENT → NO EDUCATIONAL ANALYSIS → NO KNOWLEDGE → NO
EMBEDDINGS → NO RAG → NO GENERATION`. Enforced at the domain boundary and proved
by a test that fails when bypass is attempted. A document stating it is not enough.

## D8 — Forward migrations only; Studio data is preserved.
**Locked 2026-09-19.**
Historical migrations are never deleted or rewritten. A forward migration must
not promote a legacy record to verified. Removing a material from use must not
destroy provenance or audit history.

## D9 — Exactly one source architecture ships.
**Locked 2026-09-19.**
Cleanup happens only after V2 passes, and then it is complete: old source OCR
runtime, the obsolete OpenAI document-understanding source path, and the
Qwen/Ornith/Luna/Tesseract source-reading paths are removed along with their
env/config/dependencies/tests. Two active source architectures must not coexist.

## D10 — Reuse is narrow and deliberate.
**Locked 2026-09-19. Narrowed by D19: the ported alignment is gone too.**
Only these V1 pieces survive: deterministic rendering, source identities and
checksums, and the human verification concepts. They are **ported** into the V2
package, not imported from the old module, so the old module can be deleted
whole. The provider-neutral `build_disagreement_map` / `align_source_tokens`
alignment was also ported, and has since been deleted with the rest of the
multi-reader machinery: with one reading there is nothing to align it against.

## D11 — Real pages are the acceptance evidence.
**Locked 2026-09-19.**
Synthetic fixtures prove mechanics only and may never be used to claim real
Sinhala or Tamil reading quality. Benchmark page membership is fixed; if the
code struggles on a page, fix the code. Chrome DevTools MCP acceptance runs on
the real Studio against the real source, continuously during integration.

## D12 — V2 code lives in `tools/source_factory/`, then moves.
**Locked 2026-09-19.**
Development and benchmarking happen in `tools/source_factory/` where they can
run without the API stack. Domain logic that the product needs is moved into
`apps/api/src/exam_guru_api/` behind ports when Phase 5 wires persistence. The
tools package must never become a second production runtime.

## D13 — Python environments are declared per script (PEP 723).
**Locked 2026-09-19.**
`tools/` scripts declare their own dependencies inline so `uv run` needs no
project environment. numpy must stay `< 2.3` while opencv is `4.12`. Heavy model
dependencies (torch, transformers, peft) are declared only by the scripts that
need them, never by the layout or contract tooling. Under D19 no script needs
them at all: the offline pipeline is renders, geometry and checksums.


## D14 - The executing agent reads the source first. Local OCR is a witness.
**SUPERSEDED by D19 - there is exactly one machine source reader. Retained as history.**
**Locked 2026-09-19. Supersedes the DeepSeek-first ordering shipped earlier.**

The reader order is:

```
1. deterministic render / layout, no text
2. PRIMARY VISUAL READING by the executing agent, from the original pixels
3. the primary candidate JSON is written to <document>/primary/pages/page-NNN.json
4. ONLY THEN the local specialist readers run on the SAME regions/crops
5. comparison of primary against each secondary witness
6. Machine Candidate
7. human Confirm / Correct
8. Verified Source Content
```

**The executing agent is the primary source reader.** Not a provider, not an
adapter, not a service called anything. There is no "Astra API" to build: the
agent looks at the rendered page or crop and writes down exactly what is
printed.

DeepSeek and LightOnOCR are **independent secondary witnesses**. They are run
after the primary JSON exists, on the same pixels, and they exist to corroborate
or contradict. They may not create the initial Machine Candidate and may not
silently replace the primary reading.

The primary reading must not be seeded with any local reader output. Reading the
OCR first and then "checking" it is not an independent reading; it is anchoring.

Consequences, all enforced rather than described:

- `tools/source_factory/primary/` owns the primary candidate schema and its
  validation. A Machine Candidate build fails if the primary JSON is missing
  for a region.
- Comparison preserves every disagreement. A critical-token disagreement
  between the primary reading and a witness forces human review; it is never
  resolved by rank or by majority.
- Obvious hallucinations — decoder collapse, foreign script, a witness showing
  none of the page's script when another reader found it — disqualify that
  witness's evidence. They never disqualify the primary reading, which a human
  produced by looking.
- Superseding an older DeepSeek-first candidate creates a new revision with the
  previous one as parent. Earlier reader results and review events are
  historical evidence and are never deleted.
- Verification is withdrawn automatically only where the proposed text actually
  changes.

### Fidelity rules for the primary reading

Transcribe what is printed, not what it should say:

- Sinhala/Tamil spelling exactly as printed, including apparent typos
- `X` versus `×` as printed; never normalise one to the other
- punctuation, spacing, line breaks, blank lines
- URLs and spaced emails such as `info @ nie.lk` exactly as spaced
- numbers, equations, table cell positions, and **blank cells as blank**
- never correct, infer, translate, solve, normalise or fill in missing content
- where a glyph is genuinely unreadable, record uncertainty instead of guessing

### D14 addendum - reader tiers, measured (2026-09-19)

Local OCR is not one tier. Measured against human-confirmed Verified Source
Content on pages 156 and 186 (`benchmark/primary-vs-readers.json`):

| reading | regions | mean CER | exact | insertions |
|---|---|---|---|---|
| `sinhala-deepseek` | 10 | **3.06** | 0 | **10 591** |
| `sinhala-lightonocr` | 10 | **0.48** | 1 | 951 |

DeepSeek has the lower CER on the earlier 30-crop crop benchmark but here it
over-generates catastrophically - ten thousand inserted characters across ten
regions. LightOnOCR substitutes glyphs heavily and has produced 1808 characters
of invented LaTeX on a two-character folio. **Neither is fit to lead.**

So the tiers, in `candidate/selection.py`:

- `primary-agent-reading` - **PRIMARY**. The executing agent, reading pixels.
- `sinhala-deepseek` - **STRONG_SECONDARY**.
- `sinhala-lightonocr` - **WEAK_CORROBORATING**. Never carries a region alone;
  kept because it is genuinely better on Latin tokens and so contradicts
  DeepSeek usefully. It read the printed `JICA OBIHIRO` correctly where
  DeepSeek read `JICA ORHRO`.

Tier orders how loudly evidence is reported. It never selects text.

### D14 addendum - the JICA rule

Where **every** local reader agrees against the primary reading on a token, the
primary text still stands and the conflict is escalated. Two OCR models
agreeing is not evidence that the page says what they say. Implemented in
`machine.build`; locked by
`test_every_reader_agreeing_against_the_primary_escalates_but_never_overwrites`.

All-caps Latin runs (`OBIHIRO`, `JICA`, `NIE`) are now classified as
`identifier` and treated as critical tokens, because a substitution there is
silent, plausible and damaging.

### D14 addendum - circularity in the primary benchmark

When the same party writes the primary reading and confirms it in the Studio,
the resulting CER of 0.0 is **circular** and is not evidence of accuracy. It
shows only that the Machine Candidate carried the primary reading through
unmutated. `benchmark_primary.py` prints this caveat in every report. An
independent reviewer is required before any accuracy claim is made for the
primary reading.


## D15 - Local Sinhala OCR is AUDIT-ONLY. It is not a source of text.
**SUPERSEDED by D19 - there is exactly one machine source reader. Retained as history.**
**Locked 2026-09-19. Narrows D14 further; supersedes the strong/weak tiering.**

Measured against human-confirmed source, reported as ratio and percentage:

| document | reader | mean CER | exact | insertions |
|---|---|---|---|---|
| sankhya-rata | `sinhala-deepseek` | 3.27 (**327%**) | 2/16 | 6 870 |
| sankhya-rata | `sinhala-lightonocr` | 75.06 (**7 506%**) | 0/16 | 2 617 |
| mawbasa 156+186 | `sinhala-deepseek` | 3.06 (**306%**) | - | 10 591 |
| mawbasa 156+186 | `sinhala-lightonocr` | 0.48 (**48%**) | - | 951 |

A CER above 1.0 means the reading contains more errors than the reference has
characters: the model is inventing, not misreading. Both readers have also
produced foreign-script output on Sinhala pages, and LightOnOCR invented 1808
characters of LaTeX on a two-character folio.

Therefore, in `candidate/selection.py` there are now exactly two tiers:

- `primary-agent-reading` - **PRIMARY**
- `sinhala-deepseek` - **AUDIT_ONLY**
- `sinhala-lightonocr` - **AUDIT_ONLY**

An audit-only reader **may**: raise a warning, contribute disagreement
evidence, and set `requires_human_attention`.
An audit-only reader **may never**: supply candidate text, replace or rewrite
the primary reading, or outvote it. There is no majority voting anywhere.

Do not spend further effort trying to make either model the primary reader.
Locked by `tests/test_validators.py::test_an_audit_reader_cannot_supply_the_candidate_text`.

### The pipeline inside a region

```
PRIMARY reading
  -> deterministic validators   (brackets, mixed script, placeholders, NFC,
                                 control characters, expected script)
  -> OCR audit warnings         (rejected readers, over/under generation,
                                 unanimous disagreement on a token)
  -> Machine Candidate          (text is ALWAYS the primary reading)
  -> human Confirm / Correct
```

Validators run *before* any OCR is consulted. They cannot hallucinate and they
need no second opinion. Any validator finding sets `requires_human_attention`.

### The audit earns its keep

On page 186 the audit caught a mistake in the **primary** reading: region
`p186-r006` had been transcribed with the wrong paragraph and confirmed. Both
audit readers disagreed wholesale, which is what prompted re-reading the crop
and finding the error. The verification was withdrawn by the supersession rule
and the region re-read.

This is exactly why audit-only readers are kept rather than deleted: they are
not good enough to write source, and they are good enough to notice when the
primary reading is wrong.

### Crop freshness

`candidate/cli.py` refuses to build when a crop is not its region's bounding
box grown by an even padding. Stale crops silently attach OCR text to the
wrong pixels, which looks identical to a disagreement.

## D17 - The executing agent is the ONLY text-extraction step.
**Locked 2026-09-19. Supersedes D15's audit-witness role for the active pipeline.**

Direct agent reading of the canonical crop was measured against every
alternative and is the only one fit to produce source text. So:

```
immutable source -> render -> layout -> canonical crop
  -> THE EXECUTING AGENT OPENS THE CROP AND WRITES THE TEXT
  -> seal (schema + checksum) -> deterministic validators
  -> Machine Candidate (= the primary text)
  -> human Confirm / Correct / Exclude -> Verified Source Content
```

**Code prepares, identifies, validates and stores evidence. Code never
generates source text.** There is no OCR in the active pipeline: not DeepSeek,
not LightOnOCR, not Tesseract, no prefill, no consensus, no voting.

### Canonical crop binding

`<document>/crops/crop-NNN-rNNN.png` is the only image a region may be read
from - by the agent transcribing, and by the reviewer confirming. `readers/crops`
is gone. Sealing **hard fails** when the crop is missing, when its checksum
does not match `crops.json`, or when its bbox is not the current layout box
grown by 0-32px. `crop_sha256` is required and non-null on every region.

Re-cutting a crop by hand is what produced five wrong regions on page 186.

### Independence

Before transcribing, do not open `readers/`, an older `primary/`,
`candidates/`, `comparison/`, `verified/` or any archive. Open the canonical
crop, and optionally the full rendered page for surrounding context. Reading
previous text first is anchoring, not independence.

### Validators flag, never rewrite

Coverage (layout line count vs transcribed lines), empty-text against visible
ink, duplicate and missing regions, crop checksums, Unicode and control
characters, bracket sanity, critical tokens. Line-count mismatch is judged by
region type: a figure's labels are scattered around the drawing, so the
geometric count says nothing and the check is skipped for figures, tables and
decorative bars.

### The OCR models

`sinhala-deepseek` and `sinhala-lightonocr` are removed from the active
architecture. Their measured results stay in `docs/source-v2/BENCHMARK_READERS.md`
and under `_archive/` as historical evidence for why. Do not run them in the
rebuild. Re-introducing them requires a new measured decision that explicitly
supersedes this one.

## D18 — Verified visual source is preserved and vectorized by modality.
**Locked 2026-09-19. Extends D5/D7 and does not supersede D17.**

Source fidelity applies to visuals as well as text. An educational figure must
not be excluded merely because it contains no printed text, and a model-generated
caption must never be promoted to Verified Source Content.

Every reviewed region is classified for downstream use as one of:

- `TEXT_ONLY` — source meaning is carried by verified printed text.
- `VISUAL_ONLY` — an educational figure/diagram/image with no printed text.
- `VISUAL_WITH_TEXT` — an educational visual that also contains verified
  printed labels/caption text.
- `DECORATIVE` — layout/decorative material with no educational source meaning.

### Human verification semantics

- `TEXT_ONLY`: normal Confirm / Correct flow.
- `VISUAL_ONLY`: reviewer explicitly confirms the crop as **visual-only**.
  Verification preserves the canonical crop, bbox, checksum and provenance;
  verified text remains empty/null.
- `VISUAL_WITH_TEXT`: reviewer verifies both the visual region and its exact
  printed text. Labels are source text and follow the same fidelity rules as
  prose.
- `DECORATIVE`: may be excluded from educational retrieval/vectorization.

A real educational visual is not `DECORATIVE` simply because it has no text.

### Embedding / vectorization contract

Vectorization is modality-specific and only runs after verification:

| verified source kind | downstream vectorization |
|---|---|
| `TEXT_ONLY` | verified text embedding |
| `VISUAL_ONLY` | canonical image/crop embedding only |
| `VISUAL_WITH_TEXT` | canonical image/crop embedding **and** verified text embedding |
| `DECORATIVE` | no embedding |

Hard rules:

- **UNVERIFIED TEXT → NO TEXT EMBEDDING.**
- **UNVERIFIED VISUAL → NO IMAGE EMBEDDING.**
- Never invent a caption/description to make a `VISUAL_ONLY` region embeddable
  as text.
- If image embeddings are not implemented yet, verified visuals remain verified
  but unembedded; they are never replaced by synthetic text.
- Any later generated image description belongs to **Derived Knowledge**, not
  Verified Source Content, and its provenance must say it was generated.
- Text and image vectors from the same region remain linked by the same
  `document_id`, `page_number`, `region_id`, canonical `bbox` and
  `crop_sha256`; modality is stored explicitly.
- Retrieval may use neighboring **verified** source regions as context, but must
  not rewrite or merge that context back into the source record.

The embedding model/provider is deliberately **not locked here**. It will be
selected and measured when the vectorization phase begins. This decision locks
what may be vectorized and the trust boundary, not a vendor.

### Required acceptance when vectorization is implemented

Tests/runtime acceptance must prove:

1. an unverified text region cannot produce a text vector;
2. an unverified visual region cannot produce an image vector;
3. a verified `VISUAL_ONLY` region produces no fabricated source text;
4. a verified `VISUAL_WITH_TEXT` region can emit separate image and text
   vectors linked to the same source region;
5. a `DECORATIVE` region emits no educational vector;
6. generated visual descriptions, if added later, are stored only as derived
   knowledge with explicit provenance.

Example from the real acceptance page: `p186-r002` is an educational
`VISUAL_ONLY` figure (drawing, no printed text); `p186-r003` is
`VISUAL_WITH_TEXT` because its six printed diagram labels are source text.


### D18 implementation notes

Landed in migration `0057_source_v2_source_kind` (forward-only) and
`source_v2/source_kind.py`. The decision above is the contract; this records
how it is enforced.

### Enforced in the database (migration 0057, forward-only)

- `text_only` and `visual_with_text` must carry non-empty verified text
- `visual_only` may be verified with **no text**, and must keep `crop_sha256`
- `decorative` can never become verified source content
- an unknown kind is rejected
- pre-D18 rows default to `undecided`, preserving their trust level rather
  than asserting an educational judgement nobody made


---

## D19 - There is exactly one machine source reader.
**Locked 2026-09-19. Supersedes D2, D14 and D15 in their entirety.**

The current executing AI agent directly visually transcribes the exact
canonical crop. That is the sole machine source reading. It remains unverified
until a genuine human compares it with the original/crop evidence.

Removed as active architecture:

- `tools/source_factory/readers/**` - the DeepSeek/LightOnOCR reader classes,
  the reader port, the reader benchmark harness and its ground-truth/metrics
  helpers. The deterministic crop cutter moved to `tools/source_factory/crops/`,
  which produces geometry and nothing else.
- `tools/source_factory/candidate/selection.py` - reader tiers and ranking.
- `tools/source_factory/candidate/alignment.py` - the cross-reader
  disagreement map. It existed only to compare competing readings.
- `audit_warnings()` in `candidate/validators.py` - reader-derived suspicion.
- `reader_results` / `ReaderResultInput` on the import contract, and
  `readers` / `ReaderEvidence` on `RegionView`.
- `source_v2_reader_candidates`, plus the `chosen_reader`,
  `critical_conflict`, `agreement_ratio` and `disagreement` columns on
  `source_v2_machine_candidates` (migration `0060_source_v2_single_reader`,
  forward-only; the table was empty).
- the reader-evidence disclosure and the "readers disagree" badge in the
  Studio review screen.

What replaces cross-reader disagreement as a safety net: **deterministic
checks on the one reading and on the geometry it came from**. Unbalanced
brackets, mixed-script words, replacement characters, non-NFC text, leftover
placeholders, and - the important one - line coverage measured against the
layout's geometric line count, which is what actually caught the truncated
transcription of page 186 r001. A finding never rewrites text; it sets
`requires_human_attention`.

The reviewer sees one machine proposal and its provenance, never an ensemble.
A Machine Candidate is the sealed primary text plus deterministic validator
findings, and it is never trust.

Historical reader measurements remain in `docs/source-v2/BENCHMARK_READERS.md`
and under `.exam-guru-data/_archive/`, marked historical. They are the evidence
for *why* the readers were removed and may not be used to reintroduce one.
