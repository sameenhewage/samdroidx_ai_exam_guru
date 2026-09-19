# Exam Guru — 5+ Hour Full Regression, MCP Runtime Validation, and Independent sankhya-rata Rebuild

This is a long-running execution prompt. It is not a planning document.

## Required starting point

Start from current `master`.

Known verified checkpoints:

- `89591a4` — D17: executing agent is the only active source text-extraction step.
- `b21d630` — Studio card ↔ exact source-bbox linking.
- `1c08eb7` — D18 domain/persistence foundation.
- `77432e2` — D18 service/repository/API wiring.
- `db1f1f3` — D18 Studio UX proven on real page 186.
- `33a1be5` — STATE and MASTER cleaned to one current architecture.

Current known runtime evidence:

```
p186-r002 visual_only       0 chars   crop dd117945
p186-r003 visual_with_text 56 chars   crop 125cbda9
page 156: 6 verified, 2 excluded, 0 unverified
page 186: 6 verified, 2 excluded, 0 unverified
active OCR reader rows: 0
overlay max measured error: 0.02 px
```

Current `sankhya-rata` state:

- immutable source retained
- rendered again
- deterministic layout re-detected
- 17 canonical crops created
- **no fresh primary transcripts written yet**
- previous `usable:true` must not be reused

Before doing anything, read:

1. `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
2. `prompts/source-v2/01_D18_RUNTIME_WIRING_AND_SANKHYA_REBUILD.md`
3. `prompts/source-v2/02_D18_STUDIO_MCP_AND_SANKHYA_REBUILD.md`
4. `docs/source-v2/DECISIONS.md`
5. `docs/source-v2/STATE.md`
6. this file

Do not redesign D17 or D18 unless a real, reproducible runtime defect proves a new decision is required.

---

# 1. Time / depth requirement

This task is intentionally a sustained engineering + validation session.

**Budget at least 5 hours of active development, runtime inspection, regression testing, and defect fixing if the environment/session allows it.**

Do not finish after:

- a successful build
- a green unit test
- one clean page
- one browser pass
- one successful API call
- one `usable:true`

If the core work becomes green early, spend the remaining time on:

- negative/failure paths
- full regression
- migration safety
- reload/restart persistence
- browser resize/focus/scroll behavior
- API/database consistency
- source-gate bypass attempts
- stale revision/concurrency behavior
- repository-wide search for stale architecture paths
- repeat MCP runs on the same real cases

Do not manufacture meaningless busywork. Use the time to increase confidence.

If context or tool limits force an early stop:
- stop only at a clean checkpoint
- commit/push completed work
- update STATE with the exact continuation point
- explicitly say the 5-hour regression is incomplete
- never call the task PASS

---

# 2. Fresh-session independence requirement for sankhya-rata

This is non-negotiable.

The `sankhya-rata` rebuild is intended to be the first genuinely independent D17+D18 read.

Before transcribing any `sankhya-rata` crop, confirm that **this execution session has not previously seen its old transcripts/candidates/verified text**.

If this session has already seen old `sankhya-rata` text:

**DO NOT TRANSCRIBE IT.**

Stop that part and hand it to a fresh session.

A valid fresh-reading session may inspect only:

- immutable original source
- deterministic rendered page
- deterministic layout
- canonical crops under `<document>/crops/`

Before primary transcription, do not inspect:

- old `primary/`
- old `candidates/`
- old `verified/`
- old `comparison/`
- old `readers/`
- archives containing previous text
- logs/docs that reproduce the old transcript
- previous Machine Candidate text

Do not use any previous generated text as a hint.

Reading prior text first and then “checking” the pixels is anchoring, not independent source reading.

---

# 3. Active architecture — do not violate it

The only active Source V2 path is:

```
immutable source PDF/image
→ deterministic 300-DPI render
→ deterministic layout, geometry only
→ canonical crop for every region
→ EXECUTING AGENT DIRECTLY OPENS EACH CROP AND TRANSCRIBES VISIBLE TEXT
→ primary seal with schema + checksums
→ deterministic validators, flags only
→ Machine Candidate = primary text + source-kind proposal
→ human Confirm / Correct / Confirm-visual / Reclassify / Exclude
→ Verified Source Content
→ only then knowledge / embedding / RAG / generation
```

No OCR is active.

Never run/reintroduce:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna/OpenAI source extraction
- consensus/voting
- OCR pre-fill
- old candidate pre-fill

Canonical region image:

```
<document>/crops/crop-NNN-rNNN.png
```

No hand recrops.

---

# 4. D18 source kinds remain locked

Current domain kinds:

- `TEXT_ONLY`
- `VISUAL_ONLY`
- `VISUAL_WITH_TEXT`
- `DECORATIVE`
- `UNDECIDED`

Rules:

## TEXT_ONLY
Verified printed source text.

## VISUAL_ONLY
Educational visual with no printed source text.

It may be Verified Source Content with zero characters, but visual provenance must be preserved.

## VISUAL_WITH_TEXT
Educational visual + exact printed labels/caption/text.

Both visual and text remain source evidence.

## DECORATIVE
Non-educational layout/decorative material.

It must not become verified educational content or educational embedding input.

## UNDECIDED
A non-trusted intermediate state requiring explicit reviewer decision.

Never silently convert `UNDECIDED` to a trusted source kind.

---

# 5. Hard trust invariants

These are code-level invariants, not documentation slogans:

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

Also:

- `VISUAL_ONLY` must never get fabricated source text merely to make retrieval easier.
- generated visual descriptions belong to Derived Knowledge, never Verified Source Content.
- future generated exam-paper images are Generated Paper Assets, never source crops.
- vectorization is not implemented merely because eligibility/gates exist.

Try to break these boundaries during regression.

A bypass that succeeds is a blocker.

---

# 6. First task: establish a clean baseline

Before modifying anything:

1. fetch/pull current `master`
2. verify latest commits include `db1f1f3` and `33a1be5`
3. inspect `git status`
4. leave unrelated stash entries untouched
5. record current migration head
6. bring up the real local stack
7. verify API/web/PostgreSQL are healthy
8. record baseline test results before new changes when practical

Do not commit unrelated local files.

Do not pop or mutate unrelated stashes.

Do not delete local source evidence.

---

# 7. Full regression strategy

Use this loop throughout the whole session:

```
RUN
→ INSPECT REAL OUTPUT
→ USE CHROME DEVTOOLS MCP
→ INSPECT NETWORK
→ INSPECT CONSOLE
→ INSPECT PERSISTED/API STATE
→ FIND DEFECT
→ ADD REGRESSION TEST WHERE PRACTICAL
→ FIX
→ RERUN THE SAME CASE
→ RELOAD/RESTART
→ VERIFY AGAIN
→ COMMIT STABLE CHECKPOINT
→ CONTINUE
```

Chrome DevTools MCP is not a final checkbox.

Use it continuously.

---

# 8. Backend / API regression

Run the broadest practical backend test suite, not only the 76 Source V2 tests.

At minimum:

- Source V2 domain tests
- Source V2 service tests
- repository/PostgreSQL tests
- API route tests
- verification gate tests
- migration tests
- stale-revision tests
- any document/knowledge boundary tests related to verified-source gating

If the full backend suite is practical, run it.

If unrelated failures already exist:
- prove they are pre-existing
- document exact test names
- do not hide them
- do not weaken assertions

Run `ruff` on affected/backend scopes, preferably project-wide if practical.

Run relevant type checking if configured.

---

# 9. Database and migration regression

Migrations `0057`, `0058`, `0059` are critical.

Test on disposable/local test data:

- migrate from a pre-D18 migration up through current head
- rollback/upgrade only where the migration framework/project permits safely
- clean new database → current head
- existing pre-D18 rows retain trust level
- no migration promotes unverified source to verified
- historical migrations are unchanged
- canonical provenance fields survive
- constraints reject impossible D18 states

Specifically test:

- `visual_only` may be verified with no text only when required visual provenance exists
- `decorative` cannot be verified as educational source
- verified abstention is accepted only for the intended `visual_only` case
- text-bearing verified kinds still require appropriate text
- candidate → verified copy of `crop_sha256` is correct
- bbox comes from layout/source geometry, not a missing candidate field

Do not use production data for destructive migration experiments.

---

# 10. API action regression matrix

Exercise all Source V2 actions through the real API and the Studio where applicable:

## Confirm
- valid text_only
- invalid visual_only
- invalid decorative
- undecided rejected

## Correct
- creates unverified child revision
- exact corrected text preserved
- previous revision/history retained
- verification not silently carried over
- new revision can later be confirmed

## Exclude
- explicit review event
- persistence survives reload
- exclusion is not confused with decorative classification

## Confirm-visual
- valid visual_only with zero text
- invalid visual_only carrying contradictory text
- invalid visual_with_text missing labels
- stale revision protection
- crop provenance required

## Reclassify
- reviewer can disagree with proposal
- action alone does not grant verification
- review history append-only
- candidate text not silently rewritten
- reload persists source kind
- stale revision protection

## Concurrency / stale version
Create a safe stale-revision case and prove 409 behavior still works.

No silent last-write-wins corruption.

---

# 11. Mandatory MCP regression — page 186

Use the real page 186.

Re-run the acceptance even though it passed previously.

This is regression, not trust in an old report.

Expected current proposals/states:

```
r000 decorative
r001 text_only
r002 visual_only
r003 visual_with_text
r004 text_only
r005 text_only
r006 text_only
r007 decorative
```

## r002

Prove again:

- exact bbox highlights drawing
- UI says visual only
- no empty-text error
- no generated description
- confirmed visual state survives reload
- API response correct
- PostgreSQL state correct
- crop hash present
- bbox present
- zero source characters remains legitimate
- no reader/OCR rows created

## r003

Prove again:

- exact bbox
- visual + text state
- six labels remain exact against source
- visual classification does not rewrite text
- reload persistence
- canonical crop hash preserved

## r001/r004

Regression normal text Confirm/Correct behavior.

## decorative

Regression decorative/exclusion semantics.

Inspect Console + Network after every mutation class.

---

# 12. Mandatory MCP regression — page 156

Use representative regions on real page 156.

Verify:

- text source fidelity
- card ↔ bbox navigation
- source-kind labels
- Confirm
- Correct flow where safe
- Exclude/decorative behavior
- reload persistence
- Console clean
- Network clean

Preserve fidelity anchors when encountered:

- exact punctuation
- exact spaces/double spaces
- exact Latin capitalization
- exact printed folio
- `X` vs `×` vs lowercase `x`

---

# 13. Browser interaction / layout regression matrix

Run MCP at multiple viewport sizes.

At minimum test:

- wide desktop
- medium/narrow desktop
- viewport height small enough to force both-pane scrolling

Verify:

- overlay alignment remains effectively exact
- no drift after resize
- right-card → left overlay navigation
- left overlay → right card navigation
- selected-state persistence
- no repeated auto-scroll on same selection
- correction textarea focus does not trigger card selection
- clicking inside textarea does not move source pane
- typing does not steal focus
- keyboard navigation remains usable
- selected region is not indicated by color alone
- no overlay blocks important source content unnecessarily

Record actual observed scrollTop/alignment evidence where useful.

---

# 14. Frontend regression

Run:

- TypeScript/typecheck
- lint if configured
- existing Studio Playwright suite
- new D18 UX tests
- broader frontend tests if practical
- production build if practical

Do not stop at `tsc` clean.

If a full production build is too expensive, record the exact blocker/cost and run the strongest available equivalent; do not silently skip it.

---

# 15. Restart / persistence regression

A successful browser reload is not enough.

Where practical:

1. perform reviewed mutations
2. reload browser
3. restart web/API services
4. reconnect
5. verify persisted review state
6. verify source kinds
7. verify crop/bbox provenance
8. verify document gate
9. verify no duplicate side effects occurred

The system must not depend on in-memory UI state.

---

# 16. Negative-path regression

Deliberately attempt invalid behavior:

- verify unverified source downstream
- request educational analysis without verified source
- request embedding eligibility for unverified text
- request image embedding eligibility for unverified visual
- request text embedding for visual_only
- verify decorative as educational source
- normal confirm on visual_only
- confirm-visual on contradictory kind/text
- stale revision mutation
- missing/incorrect crop hash where test harness permits
- stale bbox/layout binding where test harness permits
- missing canonical crop at sealing stage on disposable fixture

Every failure must be explicit and deterministic.

No silent fallback.

No fabricated source content.

---

# 17. Repository-wide architecture regression

Search the repository for active/stale source-reading paths.

Inspect hits for:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna
- OpenAI source extraction
- `readers/crops`
- OCR prefill
- reader voting/consensus
- old candidate-as-truth behavior

Historical docs/benchmarks may remain.

Active runtime/config/instructions must not accidentally invoke them.

Do not delete historical migration files.

Do not remove historical evidence just to make grep empty.

Classify each hit:

- active runtime defect
- historical evidence
- test fixture
- archived content
- harmless dependency/comment

Fix only real active drift.

---

# 18. Fresh independent sankhya-rata rebuild

Only perform this in a genuinely fresh-reading session as defined in section 2.

The 17 canonical crops already exist, but verify them against current layout and checksums before reading.

For every region:

1. open the exact canonical crop
2. optionally inspect the full rendered page for spatial context
3. transcribe only visible printed text
4. preserve uncertainty rather than guessing
5. choose/propose source kind consistently with D18
6. never read old generated text first

Fidelity must preserve exactly:

- Sinhala spelling
- printed typos
- punctuation
- spaces
- double spaces
- line breaks/hyphens where meaningful
- digits
- equations
- operators
- `X` vs `×` vs `x`
- URLs
- e-mail spacing
- English labels
- capitalization
- table positions
- blank cells

Never:

- correct
- infer
- solve
- translate
- normalize meaning
- fill blanks
- generate descriptions for figures

---

# 19. sankhya-rata visual classification

Do not reuse historical classifications blindly.

In particular, `p001-r001` was previously judged as stock/watermarked digit clip-art used as a title illustration.

Inspect it fresh.

If the visual is genuinely non-educational decoration:

`DECORATIVE`

If it carries educational source meaning:

`VISUAL_ONLY` or `VISUAL_WITH_TEXT`

The reason must be semantic educational meaning, not “contains no text.”

Do not classify based on previous outcome.

---

# 20. Build / publish / review sankhya-rata

After direct reading:

1. write new primary transcript JSON
2. seal every page
3. require crop checksum/bbox binding
4. run deterministic validators
5. build Machine Candidates
6. publish to Studio
7. review every region against source
8. Confirm / Correct / Confirm-visual / Reclassify / Exclude as required
9. resolve every required educational region
10. reload/restart and verify persistence

No OCR.

No archived text.

No prior candidates.

No bulk shortcut.

---

# 21. MCP on all 3 sankhya-rata pages

Use Chrome DevTools MCP throughout review.

For all three pages:

- inspect original source
- verify card ↔ bbox mapping
- verify exact candidate
- verify source kind
- perform review action
- inspect request/response
- inspect Console
- inspect persisted state
- reload
- revisit regions

Do not rush 17 regions.

Accuracy is more important than finishing quickly.

If any crop is ambiguous:
- record uncertainty
- do not guess
- do not mark usable until resolved appropriately

---

# 22. Re-earn document gate

At the end of the fresh rebuild:

Directly query the document gate.

Record:

- total pages
- total regions
- source-kind counts
- verified counts
- corrected counts
- visual-only verified count
- visual-with-text verified count
- decorative/excluded count
- undecided count
- unverified required count
- final `usable` result

`usable:true` may only be accepted if every required source region is properly resolved.

Never carry forward the old `usable:true`.

---

# 23. Cross-document regression after sankhya-rata

After `sankhya-rata` is resolved, return to pages 156 and 186.

Quickly re-check:

- Studio loads
- source kinds unchanged
- r002 still verified visual_only
- r003 still visual_with_text
- overlays still aligned
- review history remains
- no regression from any sankhya fixes

This catches document-specific fixes that accidentally break the teacher-guide flow.

---

# 24. Full gate / downstream regression

Inspect all available downstream boundaries touching Verified Source Content.

Prove:

- verified text can proceed where intended
- verified visual_only is accepted as verified source even without source text
- decorative is not educational source
- unresolved/undecided blocks downstream use
- unverified source cannot enter knowledge preparation
- no synthetic description is inserted as source text
- no embedding path is falsely reported as implemented

If no real image vectorizer exists, verified visual-only remaining unembedded is valid.

Do not invent a vector.

---

# 25. Performance / stability sanity

This is not a benchmark project, but during the 5+ hour session watch for:

- repeated duplicate API calls
- runaway polling
- unnecessary rerenders
- browser console churn
- memory growth obvious in DevTools
- slow review actions
- repeated database mutations
- accidental re-publish loops

Fix obvious regressions caused by Source V2/D18 changes.

Do not perform speculative optimization unrelated to observed behavior.

---

# 26. Security / input validation sanity

Regression-check the Source V2 review endpoints for basic strictness:

- invalid source kind
- missing required revision/version
- malformed IDs
- contradictory visual/text states
- unauthorized path if auth harness exists
- arbitrary client-provided provenance values should not override trusted server/source facts

Do not broaden scope into a full application security audit unless a real issue appears.

---

# 27. Documentation at the end

Update `docs/source-v2/STATE.md` only with verified current facts.

It should include:

- exact final commits
- D17/D18 current architecture
- sankhya-rata fresh independent status
- gate result
- MCP evidence
- test evidence
- real blockers
- next exact step

Do not resurrect old OCR instructions.

MASTER and DECISIONS only change if implementation exposed a genuinely new architectural decision.

Avoid documentation churn.

---

# 28. Commit strategy

Work on `master`.

Do not create speculative branches.

Keep unrelated local stash untouched.

Commit stable checkpoints, e.g.:

1. regression fixes discovered before sankhya reading
2. fresh sankhya primary/candidate rebuild
3. sankhya Studio review + MCP fixes
4. full-system regression fixes
5. final STATE evidence

Push each checkpoint and verify remote presence.

Never leave a half-applied migration or half-published document as the final state.

---

# 29. No false PASS

You must explicitly say NOT PASS if any of these remain:

- sankhya read was not independent
- any required region remains unresolved
- gate was not re-earned
- MCP was not used on all 3 sankhya pages
- page 186 regression was not rerun
- persisted database state was not inspected
- broad regression was skipped without a blocker
- migrations are unverified
- Console/Network have new unexplained failures
- source gate can be bypassed
- active OCR path has reappeared

Tests passing alone are never enough.

---

# 30. Final evidence report

Return a concise but evidence-heavy report.

## TIME / SESSION
- actual active work duration if measurable
- whether 5+ hour target was reached
- any context/tool limit

## COMMITS
- hash
- purpose

## BASELINE REGRESSION
- backend suites
- frontend suites
- type/lint/build
- migration tests

## MCP — PAGE 186
- r002
- r003
- Network
- Console
- overlay alignment
- reload/restart persistence

## MCP — PAGE 156
- representative regions
- Network/Console
- persistence

## SANKHYA-RATA
- confirm fresh-session independence
- pages
- regions
- kind counts
- corrected regions
- visual-only / visual-with-text / decorative counts
- unresolved count
- gate result

## DATABASE
- r002/r003 stored state
- sankhya stored state
- review-event counts
- OCR reader rows

## NEGATIVE TESTS
- stale revision
- wrong confirm action
- decorative verification refusal
- gate bypass refusal
- embedding eligibility refusals
- provenance/crop failures tested

## REPOSITORY AUDIT
- active OCR paths
- stale docs/runtime paths
- historical evidence intentionally preserved

## CROSS-DOCUMENT REGRESSION
- teacher guide after sankhya fixes

## BLOCKERS
- only genuine unresolved blockers

Do not claim:
- vectorization is implemented unless a real vectorizer was built/tested
- AI paper-image generation is implemented
- independent accuracy if the session saw old text
- 0.0 CER as independent evidence

---

# Final completion standard

This task is complete only when there is strong evidence that:

1. D17 remains direct-agent-only.
2. D18 correctly preserves text, visual-only, visual+text, decorative, and undecided semantics.
3. page 186 still works after regression.
4. page 156 still works after regression.
5. all three `sankhya-rata` pages were independently re-read and reviewed in a fresh session.
6. `sankhya-rata` re-earned its gate honestly.
7. migrations and DB constraints are safe.
8. Source V2 review actions and failure paths are tested.
9. Studio interaction/resize/focus behavior remains stable.
10. persisted state survives reload/restart.
11. downstream verified-source gates cannot be bypassed.
12. active OCR/source-reading regressions are absent.
13. broad backend/frontend regression is green or every failure is explicitly accounted for.
14. Chrome DevTools MCP evidence supports the runtime claims.
15. no claim exceeds what was actually proven.

Execute deeply. Do not rush the 17-region read. Do not stop at a superficial green check.
