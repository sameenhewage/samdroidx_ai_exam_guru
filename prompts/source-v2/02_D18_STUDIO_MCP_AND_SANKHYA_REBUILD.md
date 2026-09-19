# Source V2 — D18 Studio Runtime Acceptance + sankhya-rata Clean Rebuild

This is the continuation prompt after checkpoint 2.

## Baseline

Start from current `master`.

Verified stable checkpoints:

- `89591a4` — D17 direct-agent-only source reading.
- `b21d630` — bidirectional right-card ↔ left-bbox Studio linking.
- `1c08eb7` — D18 domain/persistence foundation.
- `77432e2` — D18 service/repository/API wiring, including:
  - `confirm-visual`
  - `reclassify`
  - candidate + verified `source_kind`
  - candidate `crop_sha256`
  - migration `0058`
  - correct real page-186 proposals
  - 76 tests passing, ruff clean

Before coding, read:

1. `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
2. `prompts/source-v2/01_D18_RUNTIME_WIRING_AND_SANKHYA_REBUILD.md`
3. `docs/source-v2/DECISIONS.md`
4. `docs/source-v2/STATE.md`
5. this file

Do not redesign D17 or D18.

No OCR in the active source pipeline.

---

# 1. Remaining work only

Checkpoint 2 is complete. Do not rewrite the domain/service/API layers unless the running product proves a defect.

The remaining mandatory sequence is:

1. Studio D18 UX
2. Chrome DevTools MCP acceptance on real page 186
3. representative page-156 MCP acceptance
4. inspect actual persisted/API state
5. fix runtime defects and rerun the same cases
6. docs cleanup
7. independently rebuild `sankhya-rata` from zero under D17+D18
8. MCP acceptance on all three `sankhya-rata` pages
9. re-earn `usable:true`
10. only then allow broader corpus migration

Do not stop after the Studio renders.

Do not stop after Playwright/unit tests.

Do not claim PASS before MCP + persisted-state evidence.

---

# 2. Studio D18 UX

Wire the frontend to the existing API/source-kind contract from `77432e2`.

The review card must understand:

- `text_only`
- `visual_only`
- `visual_with_text`
- `decorative`
- `undecided`

Keep the UI compact. Do not redesign the page.

Use human-readable labels such as:

- Text
- Visual only
- Visual + text
- Decorative
- Needs decision

## visual_only

A `visual_only` educational figure must NOT render as an "empty candidate" failure.

The reviewer must clearly understand:

> This is an educational visual with no printed text. Verify the visual itself.

Provide an explicit action wired to the existing API endpoint:

`confirm-visual`

Do not fabricate text.

Do not invite the reviewer to type a description just to make the region valid.

## visual_with_text

Show:

- visual source region
- exact candidate text
- normal correction/confirmation behavior

Do not lose the visual semantics after text confirmation.

## decorative

Do not treat it like educational verified content.

Make the status clear without clutter.

## undecided

Require explicit reviewer reclassification via the existing `reclassify` endpoint.

Never silently choose a kind in the UI.

---

# 3. Preserve existing bbox UX exactly

Do not regress `b21d630`.

One `selectedRegionId` drives both panes.

Required:

- right card click → exact left bbox overlay + auto-locate
- left overlay click → matching right card + auto-locate
- no OCR/text-search based location
- percentage/image-box alignment remains correct through resize
- selected overlay remains visually clear
- correction textarea focus must not trigger re-selection
- clicking inside editor must not scroll the panes
- selecting an already-selected region must not cause pointless movement

Any regression here is a blocker.

---

# 4. Real page 186 is mandatory acceptance

Use Chrome DevTools MCP on the real Studio.

## p186-r002 — required VISUAL_ONLY proof

Expected:

```
region_id = p186-r002
source_kind = visual_only
candidate text length = 0
crop present
educational figure = yes
```

Runtime procedure:

1. open real page 186 in Studio
2. click r002 review card
3. visually verify left overlay exactly surrounds the drawing
4. verify UI says Visual only
5. verify no "empty text" error
6. verify no generated/fabricated description exists
7. click Confirm visual-only
8. inspect request in Network
9. inspect response payload
10. inspect Console
11. reload
12. verify region remains verified visual_only
13. verify card↔overlay navigation still works after reload
14. inspect API/database state

Persisted evidence must prove:

- `source_kind = visual_only`
- verified
- text empty/null according to contract
- `crop_sha256` present
- bbox present and unchanged
- review event persisted

## p186-r003 — required VISUAL_WITH_TEXT proof

Expected:

```
region_id = p186-r003
source_kind = visual_with_text
```

Visually compare the exact printed labels against the canonical source:

- කෝටුව
- නූල
- කඩදාසි සමනලයා
- ඇල්නෙත්ත
- චුමිබකය
- රෙජිෆෝමි/මැටි

Procedure:

1. select r003
2. verify exact bbox
3. verify Visual + text state
4. compare every label against the source image
5. verify classification does not rewrite candidate text
6. use normal confirmation/correction as required
7. inspect Network
8. inspect Console
9. reload
10. verify source kind + exact text + visual provenance persist

Do not replace the labels with a prose diagram description.

Also exercise:

- `p186-r001` — text_only
- `p186-r004` — text_only
- `p186-r000` or `p186-r007` — decorative

Verify the machine proposals remain:

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

---

# 5. Reclassification runtime proof

The `reclassify` endpoint must be proven in the real Studio, not just OpenAPI/tests.

Use a safe real test case or reversible controlled state.

Verify:

- reviewer can disagree with proposed kind
- explicit source-kind decision is recorded
- review history remains append-only
- current candidate text is not silently rewritten
- no verification is accidentally granted by reclassification alone
- stale revision protection still works
- reload persists the decision

Do not permanently corrupt page-186 acceptance data just to test the button. If necessary, reclassify and then restore through another explicit reviewer decision, preserving history.

---

# 6. Network / Console / failure-path checks

During MCP acceptance, inspect Network and Console continuously.

Required Network evidence:

- correct endpoint used for visual confirmation
- correct source kind in payload/response
- no duplicate mutations
- no accidental automatic confirmation
- stale revision still gives 409
- invalid combinations return strict 4xx and are not coerced

Required Console result:

- no new D18 errors
- no new frontend warnings caused by this implementation
- pre-existing unrelated advisory may be noted separately

Test failure paths deliberately where practical:

- normal `confirm` on visual_only must be refused
- `confirm-visual` with contradictory kind/text must be refused
- decorative cannot be verified as educational content
- undecided cannot satisfy verification without explicit decision

---

# 7. Page 156 representative regression

After page 186 is clean, open real page 156.

Test representative regions covering:

- text
- any figure/visual
- decorative/header/footer region if present

Verify:

- bbox alignment
- source-kind rendering
- normal Confirm/Correct/Exclude still works
- no D18-specific empty-text regression
- reload persistence
- Network clean
- Console clean

Do not spend time re-reviewing every region unless runtime evidence indicates a systemic problem.

---

# 8. Tests

Run the existing D18/domain/API suites plus frontend review tests.

At minimum preserve:

- 76 current backend tests or more
- ruff clean
- Source V2 API/repository tests
- Studio review Playwright tests
- new tests for visual-only UI action
- new tests for visual-with-text behavior
- new tests for reclassify action
- regression for focus/no-scroll behavior
- reload persistence

Add a failing regression test first for each newly found defect where practical.

Do not loosen assertions to make tests pass.

---

# 9. Inspect actual stored state

UI success is insufficient.

After MCP acceptance, inspect the API/database and record the final stored state of:

## p186-r002

Required:

```
source_kind = visual_only
verified = true
text = empty/null
crop_sha256 = present
bbox = present
```

## p186-r003

Required:

```
source_kind = visual_with_text
verified = true
exact labels preserved
crop_sha256 = present
bbox = present
```

Also prove:

- no OCR reader rows were created by the active D17 workflow
- current canonical crop provenance is intact
- review events are append-only

---

# 10. Clean current documentation

Only after runtime behavior is proven, update current operational docs.

`DECISIONS.md` keeps historical D14/D15 and locked D17/D18.

But current-state docs must describe one active architecture:

```
render
→ layout
→ canonical crop
→ direct executing-agent reading
→ deterministic validators
→ Machine Candidate
→ human verification
→ Verified Source Content
```

No active OCR stage.

Clean `docs/source-v2/STATE.md` of stale operational instructions such as:

- active DeepSeek/LightOnOCR audit flow
- `readers/crops` as current crop location
- old reader tiering as current runtime behavior

Current canonical crop path is:

```
<document>/crops/
```

Also inspect the active architecture section of the MASTER and remove stale claims that OCR is part of the current D17 flow.

Historical benchmark results may remain clearly labelled historical evidence.

---

# 11. Then rebuild sankhya-rata from zero

Do this only after D18 Studio/API/MCP acceptance is green.

The prior `usable:true` is not reusable.

Do not restore old generated Source V2 text.

Do not inspect:

- old primary
- old candidates
- old verified text
- comparison output
- reader output
- archives

before direct transcription.

Fresh sequence only:

```
immutable source
→ deterministic 300-DPI render
→ deterministic layout
→ canonical crop for every region
→ executing agent directly opens every canonical crop
→ exact primary transcription
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

No DeepSeek.

No LightOnOCR.

No hand recrops.

No previous text hints.

This rebuild is intended to be the first genuinely independent D17+D18 document.

---

# 12. sankhya-rata visual handling

Do not reuse the historical "1 figure excluded" judgement automatically.

Inspect every real visual again.

Educational visual with no text:

`VISUAL_ONLY`

Educational visual with printed labels/text:

`VISUAL_WITH_TEXT`

Non-educational decoration:

`DECORATIVE`

Use Exclude only when exclusion is semantically correct.

"contains no text" is not a reason to discard an educational image.

Every required educational region must be resolved.

No unresolved `undecided` educational region may pass the gate.

---

# 13. sankhya-rata MCP acceptance

Use Chrome DevTools MCP on all three pages.

For each page:

1. open real Studio
2. inspect original source
3. select representative/all necessary regions
4. verify bbox
5. verify candidate text directly against source
6. verify source kind
7. make explicit human review decision
8. inspect Network
9. inspect Console
10. reload
11. verify persistence

At the end, directly query the document gate.

Only actual D17+D18 review may re-earn:

`usable:true`

Record:

- pages
- total regions
- source-kind counts
- verified counts
- decorative/excluded counts
- undecided count
- final gate result

---

# 14. Accuracy rules

Preserve exact source fidelity:

- Sinhala/Tamil spelling
- apparent printed typos
- punctuation
- spaces and double spaces
- meaningful line breaks/hyphens
- `X` vs `×`
- lowercase `x`
- digits
- equations
- URLs
- spaced emails
- Latin labels/capitalization
- figure labels
- printed folios
- table positions
- blank cells

Never correct, solve, translate, normalize semantically, infer missing text, or fill blanks.

If uncertain, record uncertainty instead of guessing.

---

# 15. No false PASS

Do not claim PASS because:

- frontend compiles
- API is live
- OpenAPI shows endpoints
- unit tests pass
- a button is visible
- a request returns 200
- a card displays a source kind

PASS requires:

- real source visually inspected
- correct bbox
- correct source kind
- correct provenance
- explicit review action
- stored state inspected
- reload persistence
- Network inspected
- Console inspected
- runtime defects fixed
- same real region rerun after fixes

---

# 16. Commit discipline

Work on `master`.

Do not create a speculative branch.

Keep unrelated stash/content untouched.

Make stable checkpoints:

1. Studio D18 UX + frontend tests
2. real page-186/page-156 MCP acceptance + runtime fixes
3. current-doc cleanup
4. `sankhya-rata` independent rebuild + MCP acceptance

Push each stable checkpoint and verify the remote contains it.

If context is running out:

- stop only at a stable checkpoint
- commit and push
- update STATE with exact continuation point
- report what remains
- explicitly say not PASS if acceptance is incomplete

---

# 17. Final report

When this prompt is fully complete, return concise evidence:

## COMMITS
hashes + purpose

## STUDIO
visual-only / visual-with-text / reclassify behavior

## PAGE 186
r002 stored state
r003 stored state

## MCP
page 186
page 156
Network
Console
resize/alignment
reload persistence
focus/no-scroll regression

## TESTS
exact suites + pass counts

## DOCS
STATE
MASTER
DECISIONS

## SANKHYA-RATA
pages
regions
kind counts
verified/decorative/excluded/undecided
final gate result

## BLOCKERS
only genuine unresolved blockers

Do not claim vectorization itself is implemented.
Do not claim generated paper-image support is implemented.

D18 only establishes the source/trust foundation for those later phases.

Execute the work. Do not stop at a plan.
