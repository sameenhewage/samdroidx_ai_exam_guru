# Source V2 — Fresh-Conversation Blind sankhya-rata Read + Final Regression

This prompt is designed to be used in a **new conversation/session only**.

It deliberately avoids reproducing any prior sankhya-rata transcript text or prior
region-by-region source-kind decisions.

Start from current `master` at/after commit `e3efa4b`.

The objective is:

1. perform a genuinely unanchored D17 read of all 17 canonical sankhya-rata crops,
2. seal/build/publish/review all three pages under D18,
3. re-earn the document gate only if justified,
4. then complete the remaining full-system regression with Chrome DevTools MCP.

Do not redesign D17 or D18.

---

# PHASE A — BLIND READ BOUNDARY

Before the fresh primary transcripts are sealed, the allowed inputs are deliberately narrow.

## Allowed before sealing all fresh primary transcripts

You may inspect:

- this prompt
- the current D17/D18 implementation code needed to run the pipeline
- schemas/contracts needed to write valid primary JSON
- the immutable sankhya-rata source PDF
- deterministic rendered pages
- deterministic layout JSON/geometry
- canonical crop manifest
- the 17 canonical crops under the active sankhya-rata `crops/` directory

You may use the full rendered page only for spatial/source context.

## Forbidden before sealing all fresh primary transcripts

Do NOT inspect:

- `docs/source-v2/STATE.md`
- earlier Source V2 continuation prompts 01–04
- old `primary/` outputs
- old `candidates/`
- old `verified/`
- old `comparison/`
- old `readers/`
- archives containing prior generated text
- benchmark outputs containing prior sankhya-rata transcript text
- database rows from previous sankhya-rata review runs
- old review-event history for sankhya-rata
- old commit diffs/messages that reproduce or characterize sankhya-rata source content
- any previous Machine Candidate text
- any OCR/provider output

Do not search the repo for sankhya-rata transcript strings.

Do not ask another model/OCR system for a transcription.

If this session has already seen old sankhya-rata transcript/candidate/verified text,
STOP and do not perform the blind read.

A new prompt inside an old conversation does not count as a fresh session.

---

# PHASE B — D17 BLIND PRIMARY TRANSCRIPTION

The active source path is:

```
immutable source
→ deterministic 300-DPI render
→ deterministic layout
→ canonical crop for every region
→ executing agent directly visually reads each exact crop
→ primary transcript JSON
→ seal/schema/checksum/bbox validation
→ deterministic validators
→ Machine Candidate = primary text + deterministic source-kind proposal
→ human review
→ Verified Source Content
```

There is no OCR in the active source-reading path.

Do not run or reintroduce:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna
- OpenAI source OCR/extraction
- OCR prefill
- reader voting
- consensus text
- old candidate prefill

Code may render, segment, crop, checksum, validate, persist, gate and display.
Code must not generate source transcription text.

## Canonical crop rule

Use only the exact active canonical crop for each region.

No hand recrops.

Before reading, verify:

- all 17 expected crops exist
- manifest entries exist
- checksums match
- crop bbox association matches the current layout
- no stale alternate crop path is being used

Any crop/manifest/layout mismatch is a hard stop for that region.

---

# PHASE C — EXACT SOURCE FIDELITY

For each of the 17 regions:

1. open the exact canonical crop
2. visually read only what is printed
3. optionally inspect the corresponding rendered page for context
4. write exact source text
5. record uncertainty rather than guessing
6. continue until all regions have a fresh primary record

Preserve exactly:

- Sinhala spelling
- apparent source typos
- punctuation
- spaces
- repeated/double spaces
- meaningful line breaks
- source hyphenation where relevant
- digits
- equations
- operators
- `X` versus `×` versus lowercase `x`
- Latin words and capitalization
- URLs
- e-mail spacing
- labels
- printed page/folio numbers
- table cell positions
- blank cells as blank

Never:

- correct
- rewrite
- summarize
- translate
- solve
- infer missing text
- normalize semantic meaning
- fill blanks
- invent figure labels
- generate a description for a picture and call it source text

If genuinely unreadable, preserve uncertainty explicitly.

Uncertainty is preferable to invented certainty.

---

# PHASE D — D18 SOURCE-KIND PROPOSAL FROM FRESH EVIDENCE

After/directly alongside the fresh crop read, classify each region only from the current source evidence and D18 semantics.

Allowed kinds:

- `TEXT_ONLY`
- `VISUAL_ONLY`
- `VISUAL_WITH_TEXT`
- `DECORATIVE`
- `UNDECIDED`

Rules:

## TEXT_ONLY
Printed educational text is the source content.

## VISUAL_ONLY
Educational visual carries source meaning but has no printed source text requiring transcription.

It may later become Verified Source Content with zero characters if a reviewer explicitly confirms the visual.

## VISUAL_WITH_TEXT
Educational visual plus exact printed labels/caption/text.

The visual and text must both remain source evidence.

## DECORATIVE
Non-educational decoration/layout material.

Do not use "no text" as the reason by itself.

## UNDECIDED
Use when the source evidence does not justify a confident semantic classification.

Do not guess just to finish.

Do not look up any historical classification before the fresh proposals are written and sealed.

---

# PHASE E — SEAL BEFORE READING HISTORY

When all 17 fresh primary transcripts are written:

1. seal every page
2. validate schema
3. validate source/render/crop checksums
4. validate bbox association
5. run deterministic validators
6. build Machine Candidates
7. verify candidate text is exactly the primary text
8. verify no OCR rows/output participated

Only **after this checkpoint is committed or otherwise durably recorded** may you read:

- `docs/source-v2/STATE.md`
- prior continuation prompts
- prior regression reports
- historical review metadata

This ordering is mandatory.

Record that the primary transcription was completed before historical generated text was consulted.

Do not call this independent ground truth; it is only an independent read with respect to prior generated transcripts.

---

# PHASE F — PUBLISH AND REVIEW ALL 17 REGIONS

Publish the fresh candidates to the real Studio.

Do not import old verification.

Do not carry forward the old document gate.

Every region must be reviewed against the source.

Use:

- Confirm
- Correct
- Confirm visual
- Reclassify
- Exclude

as semantically appropriate.

## Review rules

- machine proposal is not trust
- explicit review is required
- Correct creates an unverified child revision
- corrected revision must then be explicitly confirmed
- visual-only verification must preserve crop/bbox/checksum provenance
- visual-with-text must preserve both visual and exact text
- decorative must not become educational verified source
- undecided must be explicitly resolved before it can satisfy the source gate

Do not rush the 17-region review.

---

# PHASE G — CHROME DEVTOOLS MCP ON ALL THREE PAGES

Chrome DevTools MCP is mandatory continuously during Studio review.

For each of the three pages:

- open the real source page
- inspect every relevant region/card
- verify right-card ↔ left-bbox mapping
- verify exact visual location
- verify candidate against source
- verify source kind
- perform the review action
- inspect Network request/response
- inspect Console
- inspect persisted state
- reload and revisit the region

At least once per page/session, resize the browser/split layout and verify bbox overlays remain aligned.

Also verify:

- no same-selection re-scroll
- correction textarea focus does not cause page jumps
- typing does not steal/lose focus unexpectedly
- selected state does not rely only on color
- no duplicate review mutations
- no hidden auto-confirmation

---

# PHASE H — DATABASE / API EVIDENCE

After all three pages are reviewed, inspect actual API/PostgreSQL state.

Record:

- page count
- total region count
- count by source kind
- verified text count
- verified visual-only count
- verified visual-with-text count
- decorative/excluded count
- corrected-region count
- undecided count
- unresolved required count
- review-event counts
- crop provenance presence
- bbox provenance presence
- active OCR reader row count

The UI is not sufficient evidence by itself.

---

# PHASE I — RESTART PERSISTENCE

After review:

1. record the current state
2. reload browser
3. restart web/API/database-dependent services as appropriate
4. reopen all three pages
5. verify source kinds persist
6. verify text persists
7. verify verification state persists
8. verify crop hashes persist
9. verify bbox provenance persists
10. verify no duplicate review side effects appear
11. inspect gate again

A reload-only check is not enough.

---

# PHASE J — RE-EARN THE DOCUMENT GATE

Query the sankhya-rata document gate directly.

The prior gate value must not be reused.

`usable:true` is valid only if every required educational source region is correctly resolved under this fresh D17+D18 run.

If any required region remains unresolved/undecided, the gate must remain false.

Record the final gate result and its reason.

---

# PHASE K — NOW READ CURRENT STATE AND PRIOR REGRESSION HANDOFF

Only after the blind primary read is sealed and the fresh document has been reviewed may you read:

- `docs/source-v2/STATE.md`
- `prompts/source-v2/03_5H_FULL_SYSTEM_REGRESSION_MCP_AND_SANKHYA_REBUILD.md`
- `prompts/source-v2/04_FRESH_SANKHYA_REBUILD_AND_FINAL_REGRESSION.md`

Use them only to complete regression/history tasks.

Do not rewrite the fresh source text merely because historical text differs.

If historical content disagrees with the fresh read:
- return to the canonical crop
- inspect the pixels
- preserve the new evidence
- record the discrepancy
- require explicit review
- never silently copy the old text

---

# PHASE L — COMPLETE THE REMAINING LONG REGRESSION

Continue the unfinished 5+ hour cumulative regression effort.

The previous session completed part of the matrix but did not finish it.

Spend substantial active time on the remaining work if the environment/session allows.

Do not create meaningless busywork; use the time to increase confidence.

## Backend

Run the broadest practical backend regression:

- Source V2 domain
- services
- repositories
- PostgreSQL integration
- API
- D18 action matrix
- migration tests
- verification/gate boundary tests
- stale-revision tests
- downstream verified-source boundary tests

Run ruff and configured static/type checks.

Run the project-wide backend suite if practical.

## Frontend

Run:

- TypeScript/typecheck
- lint if configured
- Studio Playwright
- D18 UX tests
- broader frontend suite if practical
- production build if practical

Do not claim frontend PASS if build was skipped without stating why.

---

# PHASE M — NEGATIVE / FAILURE-PATH MATRIX

Explicitly prove refusal for:

- normal text confirm on `visual_only`
- `confirm-visual` on contradictory kind/text
- `visual_with_text` without required labels/text
- `decorative` as educational verified source
- `undecided` passing verification without explicit decision
- unknown source kind
- stale revision mutation
- verified visual missing crop provenance
- unverified text entering text embedding eligibility
- unverified visual entering image embedding eligibility
- `visual_only` entering source-text embedding
- knowledge preparation without verified source
- missing canonical crop at seal time on a disposable fixture
- checksum mismatch
- stale bbox/layout association

No silent fallback.

No source-text fabrication.

---

# PHASE N — MIGRATION / COLD-START REGRESSION

Migrations 0057–0059 are critical.

Verify:

- clean database → current head
- compatible pre-D18 state → current head
- no unverified row is promoted
- source provenance survives
- historical migrations remain unchanged
- downgrade guards reject unsafe downgrade where intended
- verified visual-only state remains valid

A previous cold-start regression showed that a stale migrate container image may not know the newest revision.

Test the real cold-start workflow carefully.

If current `docker compose up` behavior can still start a stale migrate image and leave the stack down:
- reproduce it
- decide whether the repo/runbook/compose setup should be fixed so normal development startup does not silently use stale migration code
- add a regression guard or operational fix if appropriate
- rerun cold start

Do not paper over it by manually rebuilding every time if the repository can safely encode the correct behavior.

---

# PHASE O — TEACHER-GUIDE CROSS-DOCUMENT REGRESSION

After all sankhya-rata changes, revisit the existing teacher-guide acceptance pages.

Use current stored state and real Studio.

Verify:

- page 156 still loads correctly
- page 186 still loads correctly
- review counts/gate remain correct
- legitimate text regions remain text source
- verified visual-only case remains zero-text verified source
- labelled visual remains visual-with-text
- overlays still align
- append-only history remains intact
- no new OCR reader rows
- reload/restart persistence remains intact

Do not modify source content merely to exercise tests.

Use safe/reversible review actions only where necessary.

---

# PHASE P — REPOSITORY ACTIVE-PATH AUDIT

Search current active code/config/docs for source-reading drift involving:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna
- OpenAI source extraction
- `readers/crops`
- OCR prefill
- source-text voting/consensus

Classify hits as:

- active runtime defect
- stale current instruction
- historical evidence
- archived evidence
- test fixture/comment

Fix only active drift/stale current instructions.

Do not delete historical evidence or historical migrations to make grep empty.

---

# PHASE Q — DOWNSTREAM TRUST REGRESSION

Prove the current boundaries still hold:

```
NO VERIFIED SOURCE CONTENT
→ NO EDUCATIONAL ANALYSIS
→ NO KNOWLEDGE
→ NO EMBEDDINGS
→ NO RAG
→ NO GENERATION

UNVERIFIED TEXT
→ NO TEXT EMBEDDING

UNVERIFIED VISUAL
→ NO IMAGE EMBEDDING
```

Also prove:

- verified visual-only is valid verified source with no fabricated text
- decorative does not become educational source
- unresolved/undecided blocks downstream use
- no generated visual description is stored as source truth
- no image vector is fabricated when no image vectorizer exists

Do not claim vectorization is implemented.

Do not implement future AI exam-paper image generation in this task.

---

# PHASE R — COMMIT DISCIPLINE

Work on `master`.

Do not touch unrelated local stash.

Use stable checkpoints, suggested:

1. fresh blind sankhya primary/candidate build
2. full sankhya Studio review + MCP fixes
3. remaining regression/cold-start fixes
4. final STATE evidence

Push each checkpoint.

Verify remote contains each commit.

Do not leave:

- half-applied migration
- partially published page
- half-reviewed document

as the final state.

---

# PHASE S — UPDATE STATE WITH VERIFIED FACTS ONLY

At the end update `docs/source-v2/STATE.md`.

Record:

- that the blind primary transcription occurred in a fresh conversation before reading historical generated text
- final sankhya page/region/source-kind counts
- gate result
- MCP evidence
- restart evidence
- broad backend/frontend regression
- negative-path results
- migration/cold-start result
- teacher-guide cross-regression
- active OCR reader row count
- real remaining blockers
- exact next step

Do not claim independent ground truth.

The same agent writing and reviewing source remains circular for accuracy measurement.

Do not quote 0.0 CER as independent accuracy.

---

# NO FALSE PASS

Final PASS is forbidden if any of these remain:

- this was not a genuinely fresh session
- historical generated sankhya text was read before sealing fresh primary transcripts
- any required sankhya region is unresolved
- the document gate was not re-earned
- MCP was not used across all three pages
- restart persistence was not tested
- actual DB/API state was not inspected
- teacher-guide cross-regression was not rerun
- material negative-path coverage is incomplete
- source-gate bypass succeeds
- new Console/Network errors are unexplained
- active OCR path reappeared
- unrelated stash/local work was damaged

---

# FINAL REPORT

Return concise evidence:

## FRESHNESS
- confirm new conversation/session
- confirm no old sankhya generated text was read before fresh primary sealing

## COMMITS
- hash + purpose

## SANKHYA-RATA
- pages
- regions
- source-kind counts
- corrections
- visual-only
- visual-with-text
- decorative/excluded
- unresolved
- final gate

## MCP
- all three sankhya pages
- Network
- Console
- resize/alignment
- focus/scroll
- reload/restart

## DATABASE
- provenance
- review history
- OCR reader rows

## REGRESSION
- backend
- frontend
- migration/cold-start
- negative paths
- downstream gates
- active-path audit
- teacher-guide cross-regression

## SESSION
- actual active duration if measurable
- whether cumulative long-run target was reached
- context/tool limitations

## BLOCKERS
- only genuine unresolved blockers

Do not claim:
- independent ground truth
- implemented vectorization
- implemented AI-generated paper images

Accuracy over speed. Do not rush the 17-region read.
