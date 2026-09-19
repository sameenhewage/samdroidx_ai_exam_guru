# Exam Guru — Fresh sankhya-rata Rebuild + Complete Remaining System Regression

This is a continuation prompt after the first third of the 5+ hour regression run.

It must be executed in a **fresh agent session** that has not seen old `sankhya-rata` transcripts/candidates/verified text.

## Verified starting point

Start from current `master`.

Known stable commits:

- `89591a4` — D17 direct-agent-only source reading.
- `b21d630` — exact bidirectional Studio region linking.
- `1c08eb7` — D18 domain/persistence foundation.
- `77432e2` — D18 service/repository/API wiring.
- `db1f1f3` — D18 Studio UX proven on real page 186.
- `33a1be5` — STATE/MASTER cleaned to one active architecture.
- `dabec0b` — D18 action regression matrix against a real PostgreSQL database.

Current regression evidence from the previous session:

- 85 tests pass.
- ruff clean.
- page 156 and page 186 both restored to:
  - 6 verified
  - 2 excluded
  - 0 unverified
- both documents survive container restart.
- `mawbasa` gate is `usable=True`.
- `sankhya-rata` gate is `usable=False` and its active Source V2 pages are absent after reset.
- `sankhya-rata` has already been freshly:
  - rendered
  - layout-detected
  - cut into 17 canonical crops
- **no fresh `sankhya-rata` transcript has been written yet**.
- unrelated local stash exists and must remain untouched.

A regression defect was already found and corrected:
- `p156-r002`, a real 703-character text region, had been accidentally excluded by a stray browser click using a fallback exclusion note.
- It was restored through the Studio.
- The erroneous historical exclude remains in append-only review history, which is correct.
- Do not erase that history.

Before doing anything, read:

1. `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
2. `prompts/source-v2/03_5H_FULL_SYSTEM_REGRESSION_MCP_AND_SANKHYA_REBUILD.md`
3. `docs/source-v2/DECISIONS.md`
4. `docs/source-v2/STATE.md`
5. this file

This prompt completes the unfinished sections of the 5+ hour run. Do not restart the architecture discussion.

---

# 1. Fresh-session independence gate

Before reading any `sankhya-rata` crop, answer this internally:

> Has this execution session already seen old `sankhya-rata` transcript text, candidates, verified text, comparison output, reader output, or archive text?

If YES:

**DO NOT TRANSCRIBE `sankhya-rata`.**

Stop that part and hand it to a genuinely fresh session.

If NO:

Proceed.

Before direct reading, you may inspect only:

- immutable source PDF
- rendered page image
- deterministic layout
- canonical crops under:
  `<document>/crops/`

Do not inspect:

- old `primary/`
- old `candidates/`
- old `verified/`
- old `comparison/`
- old `readers/`
- archives containing previous text
- docs/logs reproducing previous `sankhya-rata` transcription
- historical machine candidate output

This rule exists to prevent anchoring.

---

# 2. Long-running execution requirement

Continue the unfinished long regression run.

Aim for **at least 5 hours of cumulative active engineering + runtime validation across the combined regression sessions if the environment/session permits**.

This fresh session should spend substantial time on:

- independent `sankhya-rata` source reading
- all-region review
- Chrome DevTools MCP
- negative-path regression
- restart persistence
- cross-document regression
- full/broad backend + frontend testing
- repository-wide active-path audit
- fixing any discovered defects
- rerunning the same real cases after fixes

Do not stop because:
- one document becomes usable
- tests pass once
- the Studio looks correct once
- no error appears in Console once

If context runs low again:
- stop only at a clean committed checkpoint
- push
- update STATE
- explicitly list remaining regression sections
- do not claim final PASS

---

# 3. Active architecture remains D17 + D18

Only active flow:

```
immutable source
→ deterministic 300-DPI render
→ deterministic layout
→ canonical crop for EVERY region
→ executing agent directly visually reads each crop
→ primary transcript
→ seal/schema/checksum/bbox validation
→ deterministic validators
→ Machine Candidate = primary text + proposed source kind
→ human Confirm / Correct / Confirm-visual / Reclassify / Exclude
→ Verified Source Content
→ only then knowledge / embeddings / RAG / generation
```

No OCR.

Do not run:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna/OpenAI source extraction
- any OCR prefill
- any reader consensus/voting

Code must never produce source transcription text.

---

# 4. First task: verify current baseline did not drift

Before `sankhya-rata` work:

1. pull/fetch current master
2. verify `dabec0b` is present
3. `git status` must be understood
4. leave unrelated stash untouched
5. start/restart local stack
6. verify migration head
7. verify page 156 and 186 after restart
8. verify:
   - page 156 = 6 verified / 2 excluded / 0 unverified
   - page 186 = 6 verified / 2 excluded / 0 unverified
   - `p186-r002 = visual_only`, 0 chars, crop present
   - `p186-r003 = visual_with_text`
   - active OCR reader rows = 0
9. verify current `mawbasa` gate remains usable

If any of these differ, investigate before continuing.

Do not silently “repair” database state without understanding why it changed.

---

# 5. Complete browser/MCP regression left from prompt 03

Use Chrome DevTools MCP continuously.

Re-run page 186:

## r002
- exact overlay
- Visual only label
- no empty-text error
- no fabricated text
- verified visual persists
- Network mutation correct
- Console clean
- DB state correct

## r003
- exact overlay
- Visual + text
- all six labels remain exact against source
- candidate text unchanged by classification
- crop/bbox provenance intact
- reload persistence

Re-run page 156 representative regions, including `p156-r002`.

For `p156-r002` specifically:
- verify it is currently a legitimate text region, not excluded
- verify text is present
- verify the stray historical exclude remains only as append-only history
- verify no fallback exclusion reason is currently applied
- verify restart does not revert it

Test multiple viewport sizes.

Confirm:
- overlay alignment
- bidirectional navigation
- no focus-driven scroll
- no same-selection re-scroll
- correction textarea stable
- keyboard interaction acceptable
- no duplicate mutations

---

# 6. Fresh independent `sankhya-rata` read — all 17 crops

This is the highest-priority remaining task.

The 17 canonical crops already exist.

First verify:
- crop manifest exists
- each expected crop exists
- each crop checksum matches the manifest
- each crop bbox matches current layout
- no hand-recut file is substituted

Then for each of the 17 regions:

1. open the exact canonical crop
2. optionally open the full rendered page for context
3. visually read only what is printed
4. write exact primary transcript
5. preserve uncertainty instead of guessing
6. do not read prior generated text

Source fidelity:

- Sinhala spelling exactly as printed
- apparent source typos exactly
- punctuation exactly
- spaces / double spaces exactly
- meaningful line breaks/hyphens
- digits/operators/equations
- `X` vs `×` vs lowercase `x`
- Latin labels/capitalization
- URLs/e-mail spacing
- table positions
- blank cells remain blank

Never:
- correct
- translate
- solve
- infer
- normalize meaning
- fill missing content
- invent labels
- describe an image unless text is actually printed

---

# 7. Fresh source-kind decisions for `sankhya-rata`

Do not reuse historical classifications mechanically.

For every region classify using D18:

- `TEXT_ONLY`
- `VISUAL_ONLY`
- `VISUAL_WITH_TEXT`
- `DECORATIVE`
- `UNDECIDED` if truly unresolved

Important case:

`p001-r001`

Earlier it appeared to be stock/watermarked digit clip-art used as a title illustration.

Inspect it fresh.

If it truly carries no educational meaning and is merely decoration:
- `DECORATIVE`

If it carries educational meaning:
- `VISUAL_ONLY` or `VISUAL_WITH_TEXT`

Do not choose based on:
- the previous result
- absence of text alone
- convenience

Record uncertainty if needed.

---

# 8. Seal/build/publish `sankhya-rata`

After all fresh primary transcripts are written:

1. seal each page
2. require crop checksum
3. require bbox/layout binding
4. run deterministic validators
5. build Machine Candidates
6. verify Machine Candidate text equals primary text
7. publish to Studio
8. do not auto-verify
9. do not reuse old verification

Any checksum/bbox mismatch is a hard stop.

---

# 9. Human review all 17 regions

Use the real Studio.

Every region must be explicitly resolved.

For each region:
- select card
- verify exact left bbox
- compare candidate to source
- verify source kind
- Confirm / Correct / Confirm-visual / Reclassify / Exclude
- inspect mutation in Network
- inspect Console
- verify returned revision/state

Do not rush the 17-region review.

If correction is needed:
- correct exact text
- new revision remains unverified
- then explicitly confirm

If visual-only:
- verify visual via `confirm-visual`
- never invent source text

If decorative:
- preserve correct non-educational semantics

If undecided:
- explicit human reclassification required

No unresolved educational region may remain.

---

# 10. MCP all 3 `sankhya-rata` pages

Chrome DevTools MCP is mandatory on every page.

For page 1, 2, 3:

- source image visible
- region overlays accurate
- cards correspond to correct source
- review actions correct
- no unexpected scroll/focus behavior
- Network clean
- Console clean
- source kind survives reload
- exact text survives reload
- verification survives reload
- no duplicate request
- no OCR rows created

Resize at least once during the document review and verify alignment remains correct.

---

# 11. Restart persistence

After `sankhya-rata` review:

1. record DB/API state
2. reload browser
3. restart API/web containers/services
4. reopen all three pages
5. verify all review decisions persist
6. verify source kinds persist
7. verify crop_sha256 persists
8. verify bbox provenance persists
9. verify document gate state persists

A reload-only result is insufficient.

---

# 12. Re-earn the `sankhya-rata` gate

Query the gate directly after review and restart.

Record:

- total pages
- total regions
- source-kind counts
- verified text count
- verified visual-only count
- verified visual-with-text count
- decorative/excluded count
- corrected-region count
- undecided count
- unresolved required count
- final `usable`

The old `usable:true` must not influence this result.

Only the fresh D17+D18 rebuild may earn the gate.

---

# 13. Complete negative-path regression

Finish the negative cases from prompt 03.

Prove explicit rejection for:

- normal confirm on visual_only
- confirm-visual on wrong/contradictory kind
- visual_with_text with no labels
- decorative as educational verified source
- undecided without explicit classification
- stale revision mutation
- unknown source kind
- verified visual missing crop hash
- unverified text → text embedding eligibility denied
- unverified visual → image embedding eligibility denied
- visual_only → text embedding denied
- knowledge preparation without verified source denied
- stale/missing crop during seal
- mismatched bbox/crop where a disposable fixture permits

No silent fallback.

No generated source text.

No weakening of database guards.

---

# 14. Broad backend regression

Run the broadest practical backend suite.

At minimum:
- Source V2 domain
- service
- repository
- PostgreSQL
- API
- migrations
- gate/boundary tests
- D18 action matrix

Run ruff.

Run configured type/static checks.

If the project-wide backend suite is practical, run it.

Document any pre-existing unrelated failure explicitly.

Do not hide failures by shrinking the test set after discovering them.

---

# 15. Broad frontend regression

Run:
- TypeScript/typecheck
- lint if configured
- Studio Playwright suite
- D18 review UX tests
- broader frontend tests where practical
- production build where practical

If build cannot be completed because of a real environment/time blocker:
- record exact blocker
- do not report frontend PASS as if build ran

---

# 16. Migration regression

Reconfirm migrations through current head:

- clean DB → head
- pre-D18 compatible state → head
- trust levels preserved
- no unverified data promoted
- historical migrations untouched
- downgrade guards behave intentionally
- 0059 refuses unsafe downgrade while verified visual-only rows exist
- no migration silently removes source provenance

Do not treat an intentional downgrade refusal as a failure.

---

# 17. Cross-document regression after sankhya fixes

After all `sankhya-rata` work, revisit teacher-guide pages 156 and 186.

Verify again:

- both load
- 6 verified / 2 excluded / 0 unverified each
- `p156-r002` remains restored as text
- `p186-r002` remains verified visual_only with zero chars
- `p186-r003` remains visual_with_text
- overlays still align
- no Source V2 review regression
- mawbasa gate still usable

This is mandatory.

---

# 18. Repository active-path audit

Search current repository for:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna
- OpenAI source extraction
- `readers/crops`
- source OCR prefill
- voting/consensus selecting source text

Classify every relevant hit:

- historical evidence
- archive
- test fixture
- stale comment/doc
- active runtime/config

Fix active drift.

Do not delete historical evidence or migrations to make search results disappear.

---

# 19. Downstream trust-boundary regression

Inspect available downstream boundaries.

Prove:

- verified text can proceed where intended
- verified visual-only counts as verified source even without text
- decorative does not count as educational source
- unresolved/undecided blocks use
- unverified source cannot enter knowledge preparation
- no source visual is replaced by generated prose
- no image vector is fabricated because no image vectorizer exists

Do not claim vectorization is implemented.

Do not implement AI-generated exam-paper images in this task.

---

# 20. Runtime stability sanity

During the extended MCP run, watch for:

- repeated requests
- duplicate mutation
- runaway polling
- obvious rerender loops
- browser console churn
- memory/resource growth obvious in DevTools
- slow mutation regressions
- unexpected scroll jumps
- review state flicker
- page state changing after restart

Fix only observed regressions.

Do not drift into unrelated optimization work.

---

# 21. Security/input sanity

Check review endpoints for strict input behavior:

- malformed IDs
- unknown source kind
- missing revision
- stale revision
- contradictory source-kind/text combinations
- client attempts to overwrite trusted bbox/crop provenance
- auth/permission path if the existing harness supports it

Do not expand into a general application security audit.

---

# 22. Update STATE only with verified facts

At the end update `docs/source-v2/STATE.md`.

Record:

- new commits
- actual regression suites run
- fresh independent `sankhya-rata` status
- source-kind counts
- gate result
- MCP evidence
- restart persistence
- teacher-guide cross-regression
- active OCR row count
- remaining blockers
- exact next step

Do not claim 5 hours if actual cumulative active work did not reach it.

Do not claim independent accuracy from circular confirmation.

Do not quote 0.0 CER as independent accuracy.

---

# 23. Commit discipline

Work on `master`.

Do not touch unrelated stash.

Commit stable checkpoints, suggested:

1. fresh `sankhya-rata` primary/candidate rebuild
2. `sankhya-rata` Studio review + runtime fixes
3. remaining negative/full regression fixes
4. final STATE/regression evidence

Push each.

Verify remote contains each commit.

Do not leave:
- half-applied migration
- partially published page
- half-reviewed document
as the final session state.

---

# 24. No false PASS

Final PASS is forbidden if any of these remain:

- session independence violated
- any required `sankhya-rata` region unresolved
- `sankhya-rata` gate not re-earned
- MCP not run on all three `sankhya-rata` pages
- page 156/186 cross-regression not rerun
- restart persistence not checked
- DB state not inspected
- negative regression materially incomplete
- broad test failure unexplained
- active OCR path reappeared
- source gate bypass succeeds
- unrelated stash was altered/lost

---

# 25. Final report format

Return an evidence report only after completion.

## SESSION
- active duration
- cumulative 5-hour target reached or not
- context/tool limitations

## COMMITS
hash + purpose

## FRESHNESS
- explicitly confirm this session had not seen old `sankhya-rata` text before transcription

## SANKHYA-RATA
- pages
- 17-region result
- source-kind counts
- verified/corrected/visual/decorative/undecided counts
- gate result

## MCP
- all 3 sankhya pages
- page 156
- page 186
- Network
- Console
- resize/alignment
- focus/scroll
- reload/restart

## DATABASE
- provenance checks
- review-event counts
- OCR reader rows
- r002/r003 teacher-guide state
- sankhya state

## REGRESSION
- backend suites
- frontend suites
- migration tests
- negative paths
- stale revision
- downstream gates
- repository active-path audit

## BLOCKERS
only genuine unresolved blockers

Do not claim vectorization or generated-paper-image support is implemented.

---

# Final success standard

This continuation is complete only when:

1. `sankhya-rata` is independently read in a fresh session from canonical crops only.
2. all 17 regions are accurately reviewed.
3. D18 source kinds are resolved honestly.
4. `sankhya-rata` re-earns `usable:true` only if justified.
5. all three pages are runtime-validated with MCP.
6. restart persistence is proven.
7. pages 156/186 still pass after all fixes.
8. `p156-r002` remains correctly restored.
9. `p186-r002` remains verified visual-only with zero source characters.
10. D17 remains OCR-free.
11. negative paths and downstream gates hold.
12. broad regression is green or every remaining failure is explicitly accounted for.
13. no unrelated stash/local work is damaged.
14. every final claim is backed by runtime, database, or test evidence.

Execute carefully. Accuracy over speed. Do not rush the 17-region read.
