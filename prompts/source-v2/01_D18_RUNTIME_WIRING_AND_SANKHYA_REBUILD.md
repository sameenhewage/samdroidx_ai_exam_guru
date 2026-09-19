# Source V2 — D18 Runtime Wiring + sankhya-rata Clean Rebuild

This is an execution prompt, not a design discussion.

## Baseline

Start from current `master`.

Known stable checkpoints:

- `89591a4` — D17: direct executing-agent visual reading is the **only** active source text-extraction step.
- `b21d630` — Studio review cards are bidirectionally linked to exact deterministic source bboxes.
- `7eff406` / `bece87c` — D18 visual-source/vectorization contract is locked in docs.
- `1c08eb7` — D18 checkpoint 1 complete: domain enum, deterministic proposal, forward migration 0057, persistence constraints, modality gate, tests.

Before touching code, read:

1. `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
2. `docs/source-v2/DECISIONS.md`
3. `docs/source-v2/STATE.md`
4. this file

The locked D18 kinds are:

- `TEXT_ONLY`
- `VISUAL_ONLY`
- `VISUAL_WITH_TEXT`
- `DECORATIVE`
- `UNDECIDED` only as an unresolved machine/human decision state; never silently trusted

Do **not** redesign or rename these unless a real runtime defect proves a new decision is required.

---

# 1. Non-negotiable architecture

D17 remains active:

```
immutable source
→ deterministic 300-DPI render
→ deterministic layout
→ canonical crop
→ executing agent DIRECTLY reads the crop
→ primary JSON
→ deterministic validators
→ Machine Candidate = primary text
→ human review
→ Verified Source Content
```

No OCR is active.

Do not run or reintroduce:

- DeepSeek
- LightOnOCR
- Tesseract
- Qwen
- Ornith
- Luna/OpenAI source extraction
- consensus/voting/pre-fill readers

Canonical region image:

```
<document>/crops/crop-NNN-rNNN.png
```

Never hand-recut a region for source reading.

Hard trust invariants:

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

A generated visual description is **Derived Knowledge**, never source truth.

---

# 2. What is already done — do not redo checkpoint 1

Commit `1c08eb7` already established:

- D18 source-kind domain model
- deterministic `propose()`
- migration `0057`
- kind-aware persistence constraints
- `VISUAL_ONLY` may be verified with no source text when visual provenance exists
- `DECORATIVE` cannot become verified educational source
- pre-D18 rows default safely to `UNDECIDED`
- modality/embedding eligibility rules
- domain/PostgreSQL tests

Treat this as baseline.

Do not spend the session rewriting that layer unless downstream integration exposes a real defect.

---

# 3. Immediate task — wire D18 through service/API/review semantics

The current known defect is:

> `p186-r002` still behaves in the running Studio like an empty text candidate because service/API/Studio layers are not yet wired to `source_kind`.

Fix the real product path end-to-end.

Trace the Source V2 flow from persistence/domain through application service, API DTOs/schemas, review mutations and Studio.

The review payload must expose enough information for a reviewer to make the correct decision without guessing:

- `region_id`
- `region_type`
- `source_kind`
- candidate/verified text where applicable
- `bbox`
- verification state
- current revision/version
- visual provenance required by the existing API contract

Do not expose unnecessary internal implementation details.

Invalid combinations must fail clearly with 4xx responses; do not silently coerce.

Preserve stale revision / 409 semantics.

Preserve append-only review events.

---

# 4. Human verification semantics

Human review remains the trust boundary.

## TEXT_ONLY

Normal existing flow:

```
Confirm
Correct
Exclude
```

Do not regress it.

## VISUAL_ONLY

This is real educational source content with no printed source text.

The reviewer needs an explicit semantic action, e.g.:

```
Confirm visual-only
```

Exact wording may match existing Studio language, but the action must be unmistakable.

After confirmation, the stored result must preserve:

- verified state
- `source_kind = VISUAL_ONLY`
- empty/null text according to the existing canonical storage contract
- `crop_sha256`
- bbox
- document/page/region identity
- review provenance/revision

Do not fabricate text.

Do not require Exclude merely because text is empty.

## VISUAL_WITH_TEXT

The visual and the printed text are both source evidence.

The reviewer verifies exact printed text with the normal correction workflow while the visual remains preserved.

Do not downgrade to text-only after confirmation.

## DECORATIVE

Decorative/non-educational material must not become verified educational source accidentally.

Keep exclusion/decorative semantics explicit.

## UNDECIDED

Never auto-trust.

Require an explicit human source-kind decision before the region can satisfy the verified-source gate.

---

# 5. Real page-186 acceptance cases

Use the real Source V2 page 186.

These are required acceptance anchors.

## p186-r002

Expected semantic outcome:

```
region type: figure
source kind: VISUAL_ONLY
printed source text: none
educational visual: yes
```

It is a drawing with no printed text.

The Studio must not present this as an error merely because the transcription is empty.

The reviewer must be able to verify the visual itself.

After runtime confirmation, inspect actual persisted state and prove:

- verified
- `source_kind = VISUAL_ONLY`
- no fabricated text
- `crop_sha256` present
- bbox unchanged/present
- review event persisted

## p186-r003

Expected semantic outcome:

```
source kind: VISUAL_WITH_TEXT
```

The diagram has six printed labels:

- කෝටුව
- නූල
- කඩදාසි සමනලයා
- ඇල්නෙත්ත
- චුමිබකය
- රෙජිෆෝමි/මැටි

Verify against the real canonical crop/original pixels.

Do not create a prose description.

Do not throw away the visual after labels are verified.

After runtime confirmation, inspect actual persisted state and prove:

- verified
- `source_kind = VISUAL_WITH_TEXT`
- exact verified labels preserved
- `crop_sha256` present
- bbox present
- visual + text semantics survive reload

Also inspect representative:

- `p186-r001` — normal text
- `p186-r004` — normal text/resource line
- one decorative/header region

Then test representative page-156 regions.

---

# 6. Studio UX requirements

Do not redesign the Studio.

Keep the existing `b21d630` behaviour intact:

```
RIGHT review card
↔
LEFT deterministic bbox overlay
```

One `selectedRegionId` drives both.

Do not locate by OCR or candidate-text search.

Source-kind UI should be compact and obvious:

- Text
- Visual only
- Visual + text
- Decorative
- Needs decision

For `VISUAL_ONLY`, the reviewer should understand immediately:

> This is an educational visual with no printed text; verify the visual itself.

For `VISUAL_WITH_TEXT`:

- source visual remains visible
- exact source text is reviewable/correctable
- existing correction flow remains intact

Do not introduce an "empty candidate" warning when empty text is correct for a verified visual-only case.

Preserve existing fixes:

- correction textarea focus must not trigger region re-selection
- editing must not cause unexpected pane scrolling
- reselecting the already selected region must not cause pointless movement
- selected overlay remains aligned through resize

Accessibility:

- do not rely only on color
- keyboard focus remains usable
- do not steal focus from active editing

---

# 7. API and persistence validation

Do not trust the UI label alone.

After mutations, verify actual API/database state.

At minimum check:

### r002

```
source_kind = VISUAL_ONLY
verified = true
text = empty/null by contract
crop_sha256 != null
bbox exists
```

### r003

```
source_kind = VISUAL_WITH_TEXT
verified = true
exact labels preserved
crop_sha256 != null
bbox exists
```

Verify no active OCR reader rows are created by this workflow.

D17 agent-only extraction must remain untouched.

If any additional persistence migration is required:

- forward migration only
- never rewrite/delete historical migrations
- never promote legacy unverified data to verified
- preserve provenance/history

---

# 8. Required tests

Do not claim PASS from runtime alone; keep regression coverage.

Ensure coverage for:

## Domain/proposal

1. figure + no printed text → `VISUAL_ONLY`
2. figure + printed labels → `VISUAL_WITH_TEXT`
3. normal text → `TEXT_ONLY`
4. decorative region → `DECORATIVE`
5. ambiguous region → `UNDECIDED`, never silently trusted

## Verification

6. `VISUAL_ONLY` can become Verified Source Content with no text
7. `VISUAL_WITH_TEXT` preserves exact verified labels
8. educational visual-only does not require Exclude
9. decorative does not accidentally become educational verified source
10. undecided cannot pass the source gate without explicit decision

## Modality gate

11. unverified text → text embedding denied
12. unverified visual → image embedding denied
13. verified visual-only → image embedding eligible
14. verified visual-only → fabricated source-text embedding denied
15. verified visual-with-text → separate text/image modalities eligible
16. decorative → educational embedding denied

## Provenance/persistence

17. `crop_sha256` survives verification
18. bbox survives verification unchanged
19. `source_kind` survives reload
20. review history remains append-only
21. stale revision still returns 409

## Regression

22. existing Confirm works
23. Correct → unverified child → Confirm works
24. Exclude works
25. page/review reload persistence works
26. bbox card↔overlay selection still works

Add a regression test before fixing any newly discovered defect where practical.

---

# 9. Chrome DevTools MCP — mandatory continuous runtime validation

Unit/integration tests are not completion.

Bring up the real application and use Chrome DevTools MCP continuously.

The loop is:

```
implement
→ open real Studio
→ inspect real source
→ perform action
→ inspect Network
→ inspect Console
→ inspect stored result
→ find defect
→ fix
→ rerun SAME region
→ reload
→ verify persistence
```

For `p186-r002`:

1. open real Studio
2. click r002 card
3. confirm left overlay exactly matches the figure
4. confirm UI identifies Visual only
5. confirm there is no fabricated text
6. perform visual-only confirmation
7. inspect request payload
8. inspect response payload
9. inspect Console
10. reload
11. verify persisted VISUAL_ONLY state
12. verify bbox/crop linkage still correct

For `p186-r003`:

1. select r003
2. verify overlay
3. verify Visual + text
4. visually compare all six labels against source
5. verify candidate text is not changed by classification
6. confirm
7. inspect Network
8. reload
9. verify source kind + exact text persist

Then exercise r001/r004/decorative and representative page 156 regions.

Also resize the viewport/split pane and verify overlay alignment remains effectively exact.

Editing regression:

- open Correct
- focus textarea
- type
- click inside textarea
- verify no unwanted page/card jump

Console:

- no new D18 errors/warnings

Network:

- no failed requests
- no duplicate mutations
- no accidental auto-confirm
- correct source-kind payloads
- stale revision protection intact

Do not call this PASS until MCP acceptance is complete.

---

# 10. Clean current docs after runtime wiring

The current active docs must tell one story.

`DECISIONS.md` may retain historical D14/D15 because D17 explicitly supersedes them.

But current operational docs must be unambiguous:

- D17 = direct agent only
- D18 = verified visual-source semantics + modality-aware future vectorization
- no active OCR readers
- canonical crops live under `<document>/crops/`

Clean `docs/source-v2/STATE.md`.

Remove/relocate stale active instructions such as:

- local OCR as current audit stage
- `readers/crops` as current path
- old reader tiers as current runtime truth

Historical benchmark evidence may remain clearly labelled historical.

Also clean the active architecture section of the MASTER if it still describes OCR as part of the current path.

Do not erase history from DECISIONS.

---

# 11. Only after D18 runtime acceptance: rebuild sankhya-rata from zero

Do not start this until the D18 Studio/API/runtime checkpoint is green.

The previous `usable:true` must not be reused.

Do not restore old primary/candidate/verified text.

Do not inspect old transcription/candidate/archive outputs before direct reading.

Fresh sequence:

```
immutable original
→ deterministic 300-DPI render
→ deterministic layout
→ canonical crop for every region
→ executing agent opens each crop and directly transcribes it
→ seal/checksum validation
→ deterministic validators
→ Machine Candidate = primary
→ publish
→ human review
→ D18 source-kind decision
→ Verified Source Content
→ document gate
```

No OCR.

No archived hints.

No old candidate hints.

This should be the first clean, independent D17+D18 document after the 156/186 same-session caveat.

---

# 12. sankhya-rata visual decisions

Do not mechanically repeat the historical "1 figure excluded" decision.

Inspect the real source again.

If a visual carries educational meaning:

- `VISUAL_ONLY`
- or `VISUAL_WITH_TEXT`

If it is truly non-educational decoration:

- `DECORATIVE`

"contains no text" is never sufficient reason to exclude an educational visual.

Every required educational region must reach a terminal verified state.

No unresolved `UNDECIDED` educational region may pass the document gate.

---

# 13. sankhya-rata MCP acceptance

Use Chrome DevTools MCP on all three pages.

For every page:

- inspect source
- select representative regions
- verify bbox
- verify candidate
- verify source kind
- make review decision
- inspect Network
- inspect Console
- reload
- verify persistence

At the end, query the document gate directly.

`usable:true` may only be re-earned after actual review under the new D17+D18 path.

Record:

- page count
- region count
- count by source kind
- verified count
- excluded/decorative count
- undecided count
- final gate result

---

# 14. Corpus migration gate

Do **not** start broad corpus migration until all are true:

- D17 agent-only extraction PASS
- D18 domain + persistence + API + Studio PASS
- real page-186 MCP PASS
- page-156 representative MCP PASS
- sankhya-rata independently rebuilt from zero
- sankhya-rata gate re-earned
- tests green
- current docs clean
- no active OCR path reintroduced

Never weaken source verification for scale.

Never fall back to OCR-first bulk ingestion.

If later the 293-page teacher guide needs lesson-level scope for practical gating, record that as a separate explicit decision before implementing it.

---

# 15. Source-fidelity checklist

Direct transcription must preserve exactly:

- Sinhala/Tamil spelling
- apparent source typos
- punctuation
- spaces
- double spaces
- meaningful line breaks
- `X` vs `×`
- lowercase `x`
- digits
- equations
- URLs
- emails
- English labels/capitalization
- figure labels
- printed folios
- table structure
- blank table cells

Never:

- correct
- infer
- translate
- solve
- normalize semantically
- fill missing source text

If uncertain, record uncertainty.

Uncertainty is better than invented confidence.

---

# 16. No false PASS

Do not declare PASS merely because:

- migration ran
- API returned 200
- tests passed
- UI rendered
- source_kind appeared in JSON
- the page has no undecided text
- the agent believes an image is decorative

PASS requires:

```
real source visually inspected
+ correct source kind
+ correct provenance
+ explicit human verification
+ reload persistence
+ Network inspected
+ Console inspected
+ stored state inspected
+ same real case rerun after defects are fixed
```

---

# 17. Commit discipline

Work on `master`.

Do not create a speculative branch.

Make stable checkpoints only.

Recommended remaining checkpoints:

1. service/API/review semantics + tests
2. Studio D18 UX + real MCP acceptance
3. current-doc cleanup
4. sankhya-rata independent D17+D18 rebuild + MCP acceptance

Push each stable checkpoint and verify the remote commit exists.

If context is exhausted:

- leave repo stable
- commit/push the completed checkpoint
- update `STATE.md` with the exact continuation point
- explicitly say what remains
- do not claim PASS

---

# 18. Final evidence report

When this entire prompt is complete, return a concise evidence report.

Include:

## COMMITS
hash + purpose

## D18
domain/persistence/service/API/Studio behavior

## PAGE 186
- r002 final stored state
- r003 final stored state

## MCP
- pages/regions inspected
- Console
- Network
- resize/alignment
- reload/persistence
- editing/focus regression

## TESTS
exact suites and pass/fail counts

## SANKHYA-RATA
- pages
- total regions
- source-kind counts
- verified/excluded/undecided counts
- final `usable` result

## DOCS
- STATE current
- MASTER current
- DECISIONS current

## BLOCKERS
only genuine unresolved blockers

Do not claim:

- vectorization is implemented unless a real embedding pipeline was implemented and runtime-tested
- AI-generated paper images are implemented

D18 only establishes the correct verified source/trust foundation for those later phases.

---

# Final success condition

At completion:

1. Educational visuals survive even when they contain no text.
2. Text and visuals have explicit verified source semantics.
3. Educational figures are not accidentally discarded as empty.
4. Generated descriptions cannot become source truth.
5. Future text/image embedding eligibility is gated by verification and modality.
6. D17 remains agent-only with no OCR.
7. Studio behavior is proven on real source using Chrome DevTools MCP.
8. `sankhya-rata` is independently rebuilt from zero under D17+D18.
9. Only then may broader corpus migration begin.

Execute the work. Do not stop at a plan.
