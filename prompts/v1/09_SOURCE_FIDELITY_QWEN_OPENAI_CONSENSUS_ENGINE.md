# 09 — SOURCE FIDELITY ENGINE: QWEN LOCAL + OPENAI CONSENSUS OCR

## EXECUTION MODE

You are the implementation engineer for Exam Guru.

Repository:

`sameenhewage/samdroidx_ai_exam_guru`

This is a **single-user local Windows Studio**. The user will be away while this task runs. Work independently and complete the task end-to-end.

### Do not ask routine questions

Do **not** stop for ordinary implementation choices, package choices, reversible local configuration, test failures, browser defects, or normal debugging decisions. Inspect the machine, repository, current local state, and existing code, choose the safest non-destructive option, implement it, test it, and continue.

Only stop for a genuinely unrecoverable blocker that cannot be solved locally without user input.

Do not reset, delete, overwrite, or discard existing local work or source data. If the local working tree is ahead of GitHub, preserve and reconcile it. Do not `git reset --hard`, delete the Studio database, delete managed source files, or replace original uploaded documents.

---

# 1. PRIMARY GOAL

Rebuild the Exam Guru source-reading stage into a high-fidelity **multi-model OCR / visual source-reading engine** using:

1. **Qwen3-VL local** as an independent vision/source reader.
2. **OpenAI vision** as an independent vision/source reader.
3. deterministic alignment and validation.
4. targeted high-resolution re-reading of disagreements.
5. a machine quality gate before teacher review.
6. human final confirmation/correction before trusted knowledge exists.

The required architecture is:

```text
PDF / Image
   ↓
High-quality render
   ↓
Page segmentation
(text / table / maths / footer / image)
   ↓
┌──────────────────────────┐     ┌──────────────────────────┐
│ Qwen Local Source Reader │     │ OpenAI Source Reader     │
│ Candidate A              │     │ Candidate B              │
└────────────┬─────────────┘     └────────────┬─────────────┘
             └──────────────┬─────────────────┘
                            ↓
                 TOKEN / REGION ALIGNMENT
                            ↓
                    CONSENSUS ENGINE
                            ↓
              ┌─────────────┴──────────────┐
              ↓                            ↓
          AGREEMENT                   DISAGREEMENT
              ↓                            ↓
       validate region           high-resolution crop
                                           ↓
                                Qwen + OpenAI re-read
                                           ↓
                                deterministic validators
                                           ↓
                                  adjudication / merge
                                           ↓
                                     still uncertain?
                                      ↓            ↓
                                     NO           YES
                                      ↓            ↓
                                   accept       flag region
                                       \          /
                                        \        /
                                         ↓
                              MACHINE SOURCE CANDIDATE
                                         ↓
                                HUMAN FINAL REVIEW
                                         ↓
                               VERIFIED SOURCE CONTENT
```

This architecture is mandatory.

Do **not** replace it with a single-model OCR implementation.

---

# 2. CURRENT LOCAL QWEN ENVIRONMENT

A local Qwen runtime is already installed. Verify it before modifying application code, but do not reinstall it unless it is actually broken.

Expected current state:

- Ollama version: `0.34.0`
- API: `http://127.0.0.1:11434`
- model: `qwen3-vl:8b`
- model size: approximately `6.1 GB`
- model storage: `E:\Local LLM\ollama\models`
- GPU: NVIDIA RTX 3060 12 GB
- model observed at approximately 8.9 GB VRAM
- GPU acceleration observed as 100% GPU
- context size observed: 4096

Verify:

- Ollama is running.
- `qwen3-vl:8b` is available.
- image input works.
- local API works.
- GPU acceleration remains active during an image request.
- model storage remains on `E:\Local LLM\ollama\models`.

Do **not** download another model unless `qwen3-vl:8b` is proven unusable for this task.

Do not expose Ollama to the LAN or public network. Use loopback only.

---

# 3. EXISTING OPENAI CONFIGURATION

The Studio already has an OpenAI API key/configuration locally.

Reuse the existing `OPENAI_API_KEY` or the existing canonical local OpenAI credential path.

Do not create unnecessary duplicate API-key variables for every capability.

Provider/model configuration may remain separate, but the secret itself should be reused unless the current code has a strong documented reason otherwise.

Never print the secret in logs, browser responses, reports, screenshots, commits, or test fixtures.

---

# 4. SOURCE FIDELITY IS THE ONLY PRIORITY

Stop unrelated feature work during this task.

Do not spend implementation effort on:

- student product features;
- new paper-generation features;
- new RAG features;
- new indexing features;
- analytics;
- publishing enhancements;
- unrelated UI redesign;
- broad corpus backfill;
- general architecture refactors unrelated to source fidelity.

The current product bottleneck is not buttons or screens.

The bottleneck is:

> Can Exam Guru digitize the original Sinhala educational source accurately enough that the teacher is verifying a near-clean result instead of manually repairing OCR garbage?

Solve that first.

---

# 5. HARD TRUST INVARIANT

The system must enforce:

```text
NO VERIFIED SOURCE CONTENT
→ NO educational analysis
→ NO KnowledgeUnits
→ NO embeddings
→ NO RAG
→ NO paper generation from that source
```

Provider success is not source truth.

JSON-schema success is not source truth.

Two models agreeing is not source truth.

Only human-confirmed/corrected source content becomes `VerifiedSourceContent` or the equivalent trusted source revision.

---

# 6. MACHINE QUALITY TARGET BEFORE TEACHER REVIEW

The teacher must not be used as an OCR cleanup worker.

For the fixed clean printed Sinhala benchmark, machine output should target:

- minimum: **99.5% character accuracy**;
- preferred: **99.9%+** where practical.

Critical source content requires exactness before normal machine-ready review:

- numbers: 100% exact;
- maths operators: 100% exact;
- equations: 100% exact;
- URLs: 100% exact;
- emails: 100% exact;
- known populated table-cell values: 100% exact;
- table row/column association: 100% exact;
- blank cells: 100% preserved as blank.

Do not claim future universal machine-only 100% OCR accuracy. The product target is **100% verified final source content**, achieved through machine ensemble + deterministic checks + targeted re-reading + human final confirmation.

No known unflagged source error may enter trusted source content.

---

# 7. INSPECT THE REAL CURRENT SYSTEM FIRST

Before implementing the new engine, inspect and record the current local state:

1. current Git branch/HEAD and local uncommitted changes;
2. current DB migration revision;
3. current Studio source records;
4. presence/integrity of the existing managed source files;
5. current document rendering implementation;
6. current native PDF extraction/Tesseract paths;
7. current OpenAI document-understanding adapter;
8. current teacher review flow;
9. current `TrustedPageKnowledge` / verification gates;
10. current source-reading prompt/schema;
11. current browser flow and known real failures;
12. current local environment configuration without exposing secrets.

Preserve existing data and history.

If a DB migration is necessary, first create a simple local rollback checkpoint sufficient to restore the DB and managed files if migration fails. Verify it, migrate forward, and continue. Do not over-engineer the checkpoint.

---

# 8. SEPARATE SOURCE READING FROM EDUCATIONAL INTERPRETATION

The first AI stage must be **SOURCE ONLY**.

The current/legacy pattern of asking one model call for visible source transcription plus educational interpretation must be removed from the OCR/source-reading stage.

Create or refactor to a provider-independent contract such as:

`SourceReadCandidate`

It represents only what is visibly present on the source page.

It should support, where appropriate:

- source/page revision identity;
- render revision;
- provider/model identity;
- page regions;
- region IDs;
- region type;
- reading order;
- bounding boxes/geometry when reliable;
- exact visible Unicode text;
- language/script evidence;
- numbers;
- equations;
- URLs;
- emails;
- labels;
- table/grid structures;
- cell coordinates;
- cell states;
- diagrams/visual labels;
- repeated-object groups;
- blank answer areas;
- uncertainties;
- provider evidence/provenance.

Useful region types include:

- heading;
- paragraph;
- instruction;
- question;
- worked example;
- equation;
- vertical arithmetic;
- table;
- grid;
- chart;
- diagram;
- illustration;
- repeated-object group;
- label;
- URL;
- email;
- footer;
- page number;
- blank answer area;
- decorative image;
- unknown.

A source-reading result must **not** include:

- topic;
- skill;
- competency;
- learning objective;
- educational purpose;
- curriculum mapping;
- generated answer;
- teaching explanation.

Those belong only after source verification.

---

# 9. TWO INDEPENDENT READERS

Implement/retain two independent source readers:

## A. Qwen local

Implement a dedicated provider such as:

`QwenSourceReadProvider`

Use the local Ollama endpoint:

`http://127.0.0.1:11434`

Use `qwen3-vl:8b`.

Use image input and deterministic/low-variance settings appropriate for exact transcription. Prefer structured JSON/schema output if the runtime supports it reliably.

Bound timeouts/output size and fail safely.

## B. OpenAI

Refactor/retain an OpenAI source reader such as:

`OpenAISourceReadProvider`

It must also perform source-only reading.

### Independence rule

OpenAI must never see Qwen's extraction while independently reading the source.

Qwen must never see OpenAI's extraction while independently reading the source.

Do not ask one provider to verify the other provider's text during the independent-read stage.

Both providers are independent witnesses of the same rendered page/crop.

Persist their candidates separately with provenance.

---

# 10. HIGH-QUALITY RENDERING

A bad render cannot produce accurate OCR.

Inspect the real page-rendering path and benchmark real pages.

Use lossless or source-faithful image rendering. Avoid unnecessary JPEG compression and avoid merely upscaling a low-resolution render.

Benchmark sensible true render resolutions, including at least:

- 300 DPI;
- 400 DPI;
- 600 DPI for small/critical crops when needed.

Record:

- true render DPI;
- pixel dimensions;
- any provider-side resize;
- crop resolution;
- latency/quality tradeoffs.

Select the best practical default based on the fixed benchmark, not assumptions.

---

# 11. PAGE SEGMENTATION

Do not treat every educational page as a flat paragraph.

Segment into meaningful visual regions where possible:

- title/headings;
- Sinhala paragraph blocks;
- instructions;
- maths/equations;
- vertical arithmetic;
- table/grid;
- footer;
- URL/email;
- illustration/object area;
- answer blank;
- page number.

Use deterministic image/layout geometry where reliable.

Provider geometry can be evidence, but provider free-form structure must not be blindly treated as authoritative table geometry.

---

# 12. TOKEN / REGION ALIGNMENT

Build a real comparison/alignment layer.

Do not compare only a single giant text string.

Align candidates using appropriate evidence such as:

- region type;
- reading order;
- bounding boxes/overlap;
- line boundaries;
- Unicode text similarity;
- token sequence;
- table coordinates;
- visual region association.

Compare at:

- region level;
- line level;
- word/token level;
- critical-symbol level.

Normalization may be used internally for matching, but persisted source text must preserve the actual visible character where known.

Do not silently replace:

- `×` with `x`;
- `−` with `-`;
- `÷` with `/`;
- Sinhala characters with Latin approximations.

---

# 13. CONSENSUS ENGINE

Create explicit machine evidence states equivalent to:

- `AGREED`;
- `DISAGREED`;
- `RESOLVED_BY_REREAD`;
- `VALIDATED`;
- `UNRESOLVED`;
- `HUMAN_VERIFIED`.

If both models agree, still run deterministic validators for critical content.

Two-model agreement is strong evidence, not final truth.

If the models disagree, do not choose a winner arbitrarily and do not average/merge characters blindly.

Escalate the exact disagreement region.

---

# 14. TARGETED HIGH-RES RE-READ

When a disagreement exists, isolate the smallest useful original visual region and re-read that region independently with both providers.

Examples:

- word crop;
- line crop;
- paragraph crop;
- equation crop;
- small table cell;
- table row/column header;
- footer;
- URL/email.

Use a genuine higher-resolution source crop when beneficial.

Do not show either provider the previous provider output.

Do not endlessly re-run the whole page because one word differs.

Use bounded retries and retain re-read provenance.

---

# 15. DETERMINISTIC VALIDATORS

AI output alone is insufficient.

Implement strong deterministic checks.

## Sinhala/script checks

Flag suspicious patterns such as:

- Latin-heavy corruption inside Sinhala regions;
- malformed/abnormal Unicode;
- obvious legacy-glyph-like output;
- unexpected script switching;
- missing large text areas;
- badly fragmented Sinhala.

A dictionary may be used to flag suspicious words but must not silently rewrite visible source text.

## URL/email checks

Examples such as:

- `www.nie.lk`
- `info@nie.lk`

must survive exactly.

`info ante lk` must be rejected/flagged, not accepted as a valid source reading.

## Number checks

Treat numbers as critical tokens.

## Maths checks

Preserve exact visible operators and values including:

- `+`
- `−`
- `×`
- `÷`
- `=`
- `<`
- `>`
- `%`
- fractions;
- decimals;
- currency;
- units.

## Coverage checks

A provider returning empty/unreadable output for a clearly readable large Sinhala region must not silently pass.

## Table checks

Validate geometry and exact cell association separately from textual extraction.

---

# 16. TABLE / GRID ENGINE

The current real failures show that table values can be read but assigned to the wrong cell. This is a layout-fidelity failure.

Do not allow a VLM to free-form reconstruct an entire grid and then trust the row/column assignment.

Use geometry-first processing:

```text
page/table image
→ detect table/grid bounds
→ detect rows
→ detect columns
→ derive cell bounds
→ crop populated/ambiguous cells
→ Qwen independent read
+ OpenAI independent read
→ consensus/validation
→ bind exact value to exact row/column coordinate
```

Cell states must distinguish at least:

- visible;
- blank;
- unreadable.

Blank cells must remain blank.

Never calculate/fill a missing answer because the arithmetic is obvious.

Known acceptance anchors include:

- row 5 / column 10 → `50`;
- row 8 / column 8 → `64`;
- row 6 / column 9 → `54`.

Correct value in the wrong cell is a failure.

---

# 17. MATH / VERTICAL ARITHMETIC

Treat mathematical content as high-risk source evidence.

Known real anchors include:

- `1 × 8 = 8`
- `2 × 8 = 16`
- `3 × 8 = 24`
- `3 × 9 = 27`

Vertical exercises include visible values such as:

- `28 × 8`
- `39 × 8`
- `105 × 8`
- `476 × 8`

Preserve source relationships/layout where meaningful.

Do not solve unanswered exercises during source reading.

---

# 18. MACHINE SOURCE CANDIDATE

Build the best machine-side source candidate only from evidence-backed regions.

Conceptually:

```text
Qwen evidence
+
OpenAI evidence
+
deterministic validation
+
targeted re-read evidence
=
MachineSourceCandidate
```

Do not replace the source with an AI summary.

Each merged/resolved region must retain provenance describing how it was resolved.

---

# 19. MACHINE QUALITY GATE

Create a state equivalent to:

`MachineReadyForReview`

It may become true only when the machine pipeline has accounted for the page sufficiently and there are no unresolved high-risk source errors.

Gate conditions should include, where applicable:

- no missing major region;
- no major Sinhala corruption;
- critical numbers validated;
- maths validated;
- URL/email validated;
- table geometry valid;
- table critical values bound to correct coordinates;
- blank cells preserved;
- stale provider outputs rejected;
- render revision current;
- high-risk disagreements resolved or explicitly isolated.

If the page fails the quality gate, automatically attempt bounded recovery:

```text
re-render if needed
→ isolate problem region
→ higher-resolution crop
→ Qwen re-read
→ OpenAI re-read
→ align
→ validate
```

The normal teacher review screen should not receive obvious garbage OCR.

After bounded recovery is exhausted, the page may be shown as a problem page with the unresolved regions explicitly highlighted.

---

# 20. TEACHER REVIEW EXPERIENCE

The normal teacher experience should be:

LEFT:

original page

RIGHT:

clean best machine source reading

Teacher question:

> Does the machine reading on the right match the original page on the left?

Actions should remain simple:

- Confirm correct;
- Correct source;
- Re-read;
- Exclude page.

Do not make the teacher inspect provider JSON, model IDs, request IDs, token counts, fingerprints, or raw diagnostics in the normal flow.

Technical/provider details can live under advanced diagnostics.

Only remaining uncertain regions should be visually highlighted.

---

# 21. VERIFIED SOURCE CONTENT

Only human confirmation/correction creates `VerifiedSourceContent` or the equivalent trusted revision.

It must be:

- immutable by revision;
- bound to the exact source/page revision;
- bound to the exact reviewed machine candidate/correction;
- reload persistent;
- auditable;
- safe from stale provider results.

A provider-completed candidate is not trusted knowledge.

If verified source content changes, invalidate downstream derived educational analysis/knowledge/embeddings associated with the obsolete source revision.

---

# 22. EDUCATIONAL ANALYSIS ONLY AFTER SOURCE VERIFICATION

Only after `VerifiedSourceContent` exists may the system perform educational analysis.

Input should be conceptually:

```text
ORIGINAL PAGE
+
VERIFIED SOURCE CONTENT
```

Output may contain:

- topic;
- concepts;
- skills;
- educational relationships;
- learning objective candidates;
- curriculum mapping candidates.

This is a separate operation from OCR/source reading.

Do not reintroduce educational interpretation into the pre-verification source-reading stage.

---

# 23. FIXED REAL ACCEPTANCE SET

Use the real local Studio and real sources. Synthetic fixtures may test implementation details but are not product-quality proof.

Do not backfill the whole corpus yet.

## Acceptance A — Grade 5 Sinhala guide cover

The machine output must correctly preserve visible anchors including, where present on the selected real page:

- `මව්බස`
- `ගුරු මාර්ගෝපදේශය`
- `2020`
- `5`
- `පස් වන ශ්‍රේණිය`
- `www.nie.lk`
- `info@nie.lk`

No output such as `info ante lk`, Latin-like Sinhala corruption, or invented characters may silently pass.

## Acceptance B — multiplication/flower page

Must preserve exactly:

- `1 × 8 = 8`
- `2 × 8 = 16`
- `3 × 8 = 24`

Surrounding Sinhala must be readable Unicode.

## Acceptance C — grid page

Must preserve:

- `3 × 9 = 27`

The grid must remain structurally correct.

Blank cells must remain blank.

## Acceptance D — vertical multiplication/table page

Must preserve visible exercises including:

- `28 × 8`
- `39 × 8`
- `105 × 8`
- `476 × 8`

Known table values must remain in their exact source positions:

- R5 C10 = `50`
- R8 C8 = `64`
- R6 C9 = `54`

## Acceptance E — dense Sinhala prose page

The page visibly contains readable Sinhala paragraphs.

The machine output must produce readable Sinhala Unicode paragraphs.

A clearly readable page being returned as empty/unreadable is a failure.

---

# 24. BENCHMARK AND METRICS

Create/use a small fixed human-reference benchmark for the acceptance pages.

Measure separately:

1. Qwen result;
2. OpenAI result;
3. final machine consensus candidate.

Record useful metrics including:

- character error rate (CER);
- word error rate where meaningful;
- missing text;
- unsupported/added text;
- critical-token accuracy;
- number accuracy;
- maths/operator accuracy;
- equation accuracy;
- URL accuracy;
- email accuracy;
- table-cell value accuracy;
- table-cell position accuracy;
- blank-cell preservation;
- unresolved region count;
- latency;
- OpenAI cost where available.

Do not hide poor results from either provider.

Do not call schema-valid output accurate OCR.

---

# 25. CHROME DEVTOOLS MCP IS MANDATORY

Chrome DevTools MCP is part of the implementation loop, not a ceremonial final test.

Use the **real localhost Studio** continuously while fixing this task.

Required loop:

```text
open real Studio
→ open real acceptance source/page
→ inspect original visually
→ trigger machine reading
→ verify Qwen request/result
→ verify OpenAI request/result
→ inspect consensus result
→ visually compare output against original
→ inspect disagreement highlights
→ inspect network
→ inspect console
→ inspect actual page/render/crop identity
→ diagnose defect
→ modify code
→ restart/reload if needed
→ rerun the same page
→ compare again
```

If MCP reveals a mismatch that can be fixed, **fix it and rerun**.

Do not merely write a report saying a mismatch exists.

For the real acceptance pages verify through MCP:

- correct source document;
- correct page number;
- correct high-quality rendered page;
- correct crop when re-reading;
- Qwen call corresponds to the current page/crop;
- OpenAI call corresponds to the current page/crop;
- no stale candidate is displayed;
- independent provider results persist correctly;
- disagreement detection is correct;
- consensus result corresponds to evidence;
- Sinhala output is readable Unicode;
- numbers/operators visually match;
- table positions visually match;
- correction works;
- confirmation works;
- browser reload preserves the verified revision;
- exclusion remains excluded;
- unverified content remains blocked downstream;
- no relevant console/runtime errors remain.

Also verify through local/network inspection that Qwen remains local at loopback and no Qwen image payload is being sent to an unintended external endpoint.

---

# 26. STATUS SEMANTICS

Fix misleading status language where necessary.

A provider returning valid JSON must not be described to the teacher as successful OCR.

Use states conceptually equivalent to:

- RENDERED;
- QWEN_COMPLETED;
- OPENAI_COMPLETED;
- CONSENSUS_PENDING;
- DISAGREEMENT;
- REREADING;
- MACHINE_READY;
- NEEDS_ATTENTION;
- HUMAN_VERIFIED;
- EXCLUDED;
- FAILED.

`provider_completed` is transport/schema status.

`machine_ready` is source-fidelity status.

`human_verified` is trust status.

Do not conflate them.

---

# 27. FAILURE BEHAVIOR

Test and handle safely:

- Ollama unavailable;
- Qwen model missing;
- Qwen timeout;
- malformed Qwen JSON;
- OpenAI unavailable;
- OpenAI timeout;
- malformed OpenAI output;
- one provider available and one unavailable;
- both unavailable;
- wrong/stale source revision;
- wrong page number;
- render failure;
- partial/missing region;
- table geometry failure;
- stale provider response arriving late;
- crop retry failure.

Never silently promote a failed/partial read to trusted content.

---

# 28. TESTS

Add/maintain tests for the source-fidelity architecture, including at minimum:

- source-only contract;
- Qwen provider;
- OpenAI provider;
- independent-provider isolation;
- exact agreement;
- disagreement;
- token/region alignment;
- Sinhala corruption detection;
- URL validation;
- email validation;
- number mismatch;
- maths operator mismatch;
- equation mismatch;
- table geometry;
- table cell position;
- blank-cell preservation;
- high-resolution crop re-read;
- stale result fencing;
- machine quality gate;
- human correction;
- human confirmation;
- reload persistence;
- downstream trust gate.

Do not weaken existing tests just to make the suite green.

Automated tests are not sufficient evidence of product quality; Chrome MCP real-data acceptance remains mandatory.

---

# 29. DO NOT BACKFILL YET

Do not process the full Grade 3/4/5 corpus.

Do not OCR hundreds of pages.

Do not mass-embed.

Do not rebuild the whole vector database.

Do not generate papers from the new source path yet.

First make the fixed real acceptance pages pass the Source Fidelity Gate.

---

# 30. PERFORMANCE / COST

Accuracy is the priority for this phase, but avoid obviously wasteful recomputation.

Use whole-page independent reads where needed for initial page understanding/coverage.

After disagreements, re-read only targeted high-resolution regions rather than repeatedly paying to re-read an unchanged whole page.

Persist/cache provider candidates keyed by the exact relevant identity, such as:

- source revision;
- page;
- render revision;
- crop identity;
- provider;
- model;
- prompt/schema version.

Do not reuse stale evidence after source/render changes.

Measure OpenAI usage/cost honestly.

Qwen is local and may be used more aggressively for recovery passes, but evidence independence must be preserved.

---

# 31. FINAL UI EXPECTATION

The current garbage-looking source-reading output is not acceptable.

For a good page, teacher review should show a near-clean machine transcription/representation.

Example conceptually:

```text
Machine reading quality: High

[clean Sinhala text]
[correct equations]
[correct table/grid representation]

Needs attention: 1 region
```

The teacher should inspect a small number of highlighted uncertainties, not repair entire pages of broken OCR.

Advanced technical details may expose:

- Qwen candidate;
- OpenAI candidate;
- consensus evidence;
- crop retry history;
- model identity;
- timing/cost.

Do not expose those by default.

---

# 32. EXECUTION EXPECTATION

This is an end-to-end implementation task.

Do not stop after:

- confirming Qwen is installed;
- writing an architecture document;
- adding a provider class;
- getting unit tests green;
- receiving JSON from OpenAI;
- receiving JSON from Qwen;
- finding the first real OCR mismatch.

Continue through:

1. verify Qwen local environment;
2. inspect current source-reading implementation;
3. refactor source-only contracts;
4. integrate Qwen independent reader;
5. refactor OpenAI independent reader;
6. implement alignment/consensus;
7. implement deterministic validators;
8. implement high-resolution disagreement re-read;
9. implement/repair table and maths fidelity handling;
10. implement machine quality gate;
11. integrate clean teacher review;
12. preserve human trust gate;
13. run real acceptance pages;
14. use Chrome DevTools MCP;
15. fix observed defects;
16. rerun until the fixed acceptance set meets the defined gate or a genuine evidenced model/hardware limitation remains.

Do not ask for permission at every step.

---

# 33. FINAL REPORT

Keep the final report evidence-driven.

Report:

1. local Git/DB/source state preserved;
2. Qwen runtime/model verified;
3. Qwen GPU execution verified;
4. exact OpenAI model used;
5. selected page render strategy;
6. source-only contract changes;
7. Qwen provider implementation;
8. OpenAI provider changes;
9. consensus/alignment implementation;
10. deterministic validators implemented;
11. table/grid strategy implemented;
12. targeted re-read strategy implemented;
13. machine quality gate implemented;
14. teacher review changes;
15. migration changes, if any;
16. Qwen accuracy per real acceptance page;
17. OpenAI accuracy per real acceptance page;
18. final consensus accuracy per page;
19. numbers/math exactness;
20. URL/email exactness;
21. table-cell value/position exactness;
22. unresolved regions remaining;
23. Chrome DevTools MCP evidence;
24. correction + reload-persistence evidence;
25. downstream trust-gate evidence;
26. OpenAI cost/latency evidence where available;
27. final Source Fidelity Gate result.

Final gate must be exactly one of:

`SOURCE FIDELITY GATE: PASS`

or

`SOURCE FIDELITY GATE: FAIL`

Do not label the task complete while fixed real acceptance pages contain known unflagged source errors.

---

# ABSOLUTE DONE CONDITION

The task is not complete because the code compiles, tests pass, Qwen runs, or OpenAI returns a schema-valid response.

It is complete only when the real local Studio demonstrates this working chain on the fixed real acceptance set:

```text
PDF / Image
→ high-quality render
→ page segmentation
→ Qwen independent source read
→ OpenAI independent source read
→ token/region alignment
→ consensus
→ disagreement isolation
→ high-resolution targeted re-read
→ deterministic validation
→ clean MachineSourceCandidate
→ machine quality gate
→ human correction/confirmation
→ VerifiedSourceContent
→ browser reload persistence
```

and the machine-side output is good enough that the teacher is performing final verification rather than manual OCR cleanup.

After source verification, demonstrate only enough of the existing downstream gate to prove that unverified content cannot proceed and verified content can become eligible for later educational analysis. Do not resume broad RAG/generation work during this task.

**SOURCE FIDELITY IS THE PRIORITY.**
