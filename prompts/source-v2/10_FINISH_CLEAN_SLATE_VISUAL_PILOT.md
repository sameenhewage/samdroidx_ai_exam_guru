# Source V2 — Finish Clean-Slate Visual Pilot from checkpoint 11feeec

## Mission

Continue from commit:

`11feeec4cfbd3ece148d1fb4c179667fd05a513c`

Do **not** redo the destructive reset or re-audit work that this checkpoint already proved unless current code materially differs.

The previous session successfully:
- removed most V1 source-reading runtime;
- reset the DB cleanly and migrated BASE → HEAD at 0059;
- retained only two pilot originals:
  - `mawbasa-teacher-guide`
  - `sankhya-rata`
- cleared derived source artifacts;
- removed upload → old-reader dispatch;
- moved deterministic PDF primitives to `documents/pdf_render.py`;
- kept the repo buildable.

The previous session stopped only because its context window ended. **Context exhaustion is not an architectural blocker.**

This run must finish the remaining cleanup, rebuild the pilot from the immutable PDF pixels using the current executing AI agent, validate the runtime, and leave the pilot honestly ready for real human verification.

Do not claim human verification unless a genuine human actually reviewed the crop/page evidence. AI-agent inspection is the primary machine reading, not human review.

---

# 1. Fix the three known residual dead-legacy items first

The checkpoint reported three active leftovers. Remove them completely:

1. `documents/service.py` still reads legacy `source_read_jobs` to compute material status.
   - Remove that runtime dependency.
   - Material status must be derived from the current Source V2 / upload / review state only.
   - Historical migration tables may remain as migration history, but active runtime code must not depend on them.

2. `Permission.EXTRACTION_TRIGGER` and two now-unused extraction/read rate-limit keys are still present.
   - Identify the exact dead permission/rate-limit constants.
   - Remove them and their dead config/tests/routes.
   - Do not weaken unrelated auth/rate-limiting.

3. `documents-studio.tsx` still renders a disabled “Extraction review” legacy block.
   - Remove it completely rather than hiding/disabling it.
   - No teacher-facing wording should imply the old extraction/OCR workflow still exists.

Add regression tests where useful.

---

# 2. Finish the Source V2 single-reader contract cleanup

The codebase still contains stale multi-reader/consensus concepts even though the active legacy readers were deleted.

The final Source V2 contract must be:

```
canonical crop
→ current executing AI agent
→ ONE primary transcript
→ sealed provenance
→ human review
```

There is no second machine reader and no reader voting.

Audit and remove active Source V2 concepts that exist only for the removed multi-reader/consensus architecture, including where applicable:

- `source_v2_reader_candidates`
- `reader_results`
- `ReaderEvidence`
- `chosen_reader`
- `agreement_ratio`
- `critical_conflict`
- `disagreement`
- source-reader result lists
- reader-selection/reader-voting fields
- stale “reader evidence” UI
- stale reader benchmark/runtime assumptions

If schema cleanup requires a new forward migration, add one (e.g. 0060).  
**Never edit or delete historical migrations 0001–0059.**

A clean DB migrated BASE → HEAD must end with the new single-reader schema.

If some generic deterministic helper lives under `tools/source_factory/readers/**` but is still needed for rendering/canonical cropping/metrics, move it to a neutral path. Do not retain an old “readers” subsystem merely to keep utility code.

Likewise inspect `tools/source_factory/candidate/**`:
- keep only deterministic candidate construction/validation that directly converts the primary agent transcript into Machine Candidate state;
- delete consensus/ranking/reader-selection behavior;
- rename/move neutral utilities if the old names misrepresent the architecture.

---

# 3. Fix stale schema/docs that still describe old readers

The current `schemas/source-content/primary-reading.schema.json` description still mentions secondary readers such as DeepSeek/LightOnOCR.

Remove all such stale architecture language.

The schema must describe exactly this:

> The executing AI agent directly visually reads the exact canonical crop. This is the sole machine source reading. It is unverified until a human compares it with the source evidence.

Update all authoritative documentation that still presents Tesseract/OCR/Qwen/OpenAI source readers as the active architecture, especially where applicable:

- `README.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/v1/00_V1_MASTER_PLAN.md`
- `docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`
- `docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `docs/v1/PHASE_TRACKER.md`
- affected `AGENTS.md` / skill docs

Historical decision records/old benchmark docs may remain clearly historical.

Do not leave the authoritative architecture contradicting the shipped code.

---

# 4. Clean DB again after schema cleanup

Because the user wants a true clean slate and there is no old source data to preserve:

1. stop API/worker/web writes;
2. drop/recreate the application schema/database as appropriate;
3. run Alembic BASE → HEAD using the updated schema;
4. seed only minimum required auth/config/reference data;
5. verify all source/corpus/knowledge/embedding/generation-derived tables are empty;
6. verify no active legacy source-read/source-understanding/source-consensus data exists;
7. verify only the two immutable pilot PDFs are available for re-import.

Do not restore old candidates or old verified text.

---

# 5. Pilot scope

Use only:

## A. mawbasa-teacher-guide

For this pilot, process:
- page 156
- page 186

Do not process the entire large guide yet.

## B. sankhya-rata

Process the entire pilot PDF.

No other educational PDF should be imported into the fresh DB in this run.

---

# 6. Deterministic render/layout/canonical crops

For every selected pilot page:

1. validate original PDF SHA-256;
2. render deterministically at 300 DPI;
3. produce deterministic page image checksum;
4. run the deterministic layout/region detector;
5. produce a canonical crop for EVERY region;
6. bind each crop to:
   - document identity
   - page number
   - region ID
   - bbox
   - source SHA-256
   - page image SHA-256
   - crop SHA-256
   - detector version
   - render DPI
7. validate crop dimensions/bounds/checksum.

Use surviving Source Factory deterministic render/layout/crop tooling where it matches the new architecture.

Do not run OCR or any old reader.

---

# 7. Fresh direct visual transcription by the CURRENT EXECUTING AI AGENT

This is the key requirement.

For every canonical crop, the executing AI agent must visually inspect the crop itself.

The agent input may contain ONLY:

- crop image pixels
- document/page/region identity
- bbox
- source/page/crop checksums
- D17 exact-source fidelity rules
- D18 source-kind rules
- primary-reading JSON schema

The agent MUST NOT see or consume:

- old Tesseract/OCR text
- old Qwen output
- old OpenAI source-reader output
- old consensus output
- previous machine candidates
- previous verified text
- previous corrections
- previous ground truth/reference text
- old reader logs
- old knowledge/embeddings
- old screenshots containing candidate text

For `sankhya-rata`, use a genuinely isolated blind reading context.

For each region, determine from pixels:

- proposed source kind:
  - TEXT_ONLY
  - VISUAL_ONLY
  - VISUAL_WITH_TEXT
  - DECORATIVE
  - UNDECIDED
- exact text where text is visibly present
- uncertainty/abstention when unreadable

Rules:

- preserve Sinhala/Tamil/English Unicode exactly as printed;
- preserve apparent typos;
- preserve visible punctuation, numbers, mathematical symbols and labels;
- do not translate;
- do not normalize source wording;
- do not solve exercises;
- do not infer text hidden/cut off/not visible;
- do not manufacture captions for VISUAL_ONLY;
- VISUAL_ONLY may contain zero source characters;
- VISUAL_WITH_TEXT must preserve its printed labels/text;
- DECORATIVE must not become educational text;
- unreadable/ambiguous content must abstain/flag uncertainty rather than guess.

---

# 8. Seal primary transcripts BEFORE review

For each page/region, persist a sealed primary-reading artifact before any review/comparison.

The sealed artifact must bind:

- primary transcript
- source kind proposal
- source SHA-256
- page image SHA-256
- crop SHA-256
- bbox
- page/region identity
- reader provenance = current executing agent primary visual reading
- schema version
- timestamp/version identity

Compute/store a deterministic hash of the sealed transcript artifact.

After sealing, do not mutate the artifact. Corrections/review create new review/candidate lineage rather than rewriting the original machine reading.

Keep these pilot reading artifacts in private runtime/evidence storage, not Git, unless they contain no private educational content and the repository contract explicitly allows them.

---

# 9. Import only the primary Machine Candidates

Publish the deterministic page/layout + one primary agent reading into Source V2.

No `reader_results`, no secondary reader rows, no consensus fields.

The Source V2 Studio should show:

```
canonical region
+ source kind
+ one primary machine transcript
+ crop provenance
+ human review actions
```

Nothing should suggest reader voting.

---

# 10. Human review honesty rule

The product vision is:

```
AI source reading
→ HUMAN verify/correct/exclude
→ Verified Source Content
```

Do not let the AI agent impersonate the human verifier.

For real pilot data:

- create/import fresh Machine Candidates;
- make the Source V2 UI fully reviewable;
- if a genuine human reviewer has actually supplied a decision, persist it normally;
- otherwise leave the real candidate `unverified` and report `READY_FOR_HUMAN_REVIEW`.

Do **not** mark real source content verified merely to make `usable:true`.

It is acceptable for this run to finish engineering/runtime acceptance as:

`TECHNICAL PILOT PASS — READY_FOR_HUMAN_REVIEW`

if the only remaining action is genuine human visual confirmation.

This is not a BLOCKED result.

For automated API/browser regression of confirm/correct/exclude mechanics, use disposable synthetic fixtures, not fabricated human decisions on the real PDFs.

---

# 11. Source V2 UI acceptance

The Source V2 UI must be the only source review experience.

For every pilot region, reviewer must be able to see:

- page context
- canonical crop or clearly highlighted region
- source kind
- fresh primary transcript
- uncertainty state
- provenance/crop identity under technical details
- Confirm
- Correct
- Exclude / source-kind-specific equivalent

D18 behavior must be correct:

- TEXT_ONLY: cannot confirm empty
- VISUAL_ONLY: may verify with zero source characters
- VISUAL_WITH_TEXT: cannot verify empty
- DECORATIVE: cannot enter educational downstream corpus
- UNDECIDED: requires explicit human source-kind decision

Remove stale OCR/reader wording.

Reviewer-facing guidance should be Sinhala where practical, but original source text is never translated.

---

# 12. Downstream gates

On the fresh real pilot:

Until genuine human verification occurs:

- no KnowledgeUnits
- no embeddings
- no vector indexing
- no RAG eligibility
- no educational generation eligibility

Prove this in DB/API.

After synthetic verified fixtures, prove the positive path still works without relying on removed reader tables.

Do not vectorize the real pilot yet unless genuine human verification has actually occurred.

---

# 13. Tests and regression

Run focused tests continuously and then broad gates.

Required focused coverage:

- neutral PDF render module
- deterministic layout/crops
- primary-reading schema
- Source V2 import
- D18 source kinds
- crop SHA/bbox provenance
- no multi-reader fields/rows
- no legacy material-status read-job dependency
- no legacy permissions/rate-limit keys
- no old Extraction review UI
- upload creates no source-read job
- Source V2 review actions
- downstream gate blocks unverified real pilot
- synthetic verified positive path

Then run:

- backend unit tests
- relevant real-PostgreSQL Source V2 integration tests
- frontend tests
- Ruff check + format check
- strict type checks
- frontend production build
- backend/package build if defined
- OpenAPI/client regeneration checks
- Alembic BASE → HEAD on a new empty DB

The previous checkpoint reported a large pre-existing integration failure set. Do not blindly spend the entire run fixing unrelated historical failures, but:

1. run the relevant integration suites for all touched architecture;
2. compare broad failures to the `11feeec` baseline;
3. fix every new/current-change failure;
4. delete/migrate tests that only exercise architecture removed by this cutover;
5. report unrelated surviving failures honestly with evidence.

The clean Source V2 pilot paths themselves must be green.

---

# 14. Start the real stack and use Chrome DevTools MCP

The previous session left API/web/maintenance stopped.

Start/rebuild the real local stack safely.

Use Chrome DevTools MCP, not only automated tests.

Validate:

1. Materials shows only the two pilot PDFs.
2. No old `review-text`, `review-content`, `benchmark-review`, Extraction review or reader-consensus UI is reachable.
3. Source V2 Studio is the only source-review flow.
4. Teacher-guide page 156 loads.
5. Teacher-guide page 186 loads.
6. `sankhya-rata` pages load.
7. page render loads.
8. canonical crop/region focus is correct.
9. fresh primary transcript is shown.
10. source-kind state is shown.
11. review controls render correctly.
12. no old reader evidence panels.
13. no unexpected failed Network requests.
14. Console clean except explicitly accepted non-blocking advisory.
15. API payload matches DB state.

Do not perform fake human confirmation of real candidates.

---

# 15. DB validation

Directly inspect PostgreSQL and record counts.

Prove:

- exactly two pilot source documents active;
- only selected teacher-guide pages plus all sankhya pages exist in Source V2;
- canonical regions/crops are present;
- each current Machine Candidate has one primary-agent source;
- no active reader-candidate/consensus rows/table dependencies remain after new schema;
- no source-read jobs participate in runtime status;
- real pilot verified-region counts remain zero unless genuinely human-reviewed;
- no KnowledgeUnits/embeddings/RAG projections exist for unverified pilot content;
- crop SHA/bbox/source/page provenance is complete.

---

# 16. Cold rebuild/restart

After the first runtime acceptance:

1. stop stack;
2. rebuild `migrate`, `api`, `worker`, `web`;
3. start from the fresh DB/storage state;
4. verify migrations at HEAD;
5. rerun the same Chrome/API/DB checks.

Do not trust stale images.

---

# 17. Repo-wide zero-legacy proof

Search the full repository for at least:

- `tesseract`
- `ocr`
- `qwen`
- `ollama`
- `source_reading`
- `page_reading`
- `source_consensus`
- `source_machine`
- `source_read_job`
- `source_read_jobs`
- `source_read_job_id`
- `understanding_openai`
- `EXTRACTION_TRIGGER`
- `Extraction review`
- `review-text`
- `review-content`
- `benchmark-review`
- `DeepSeek`
- `LightOnOCR`
- `reader_results`
- `chosen_reader`
- `agreement_ratio`
- `critical_conflict`
- `disagreement`

Every remaining match must be classified as:

1. immutable historical migration;
2. clearly historical docs/benchmark/archive;
3. unrelated non-source-reading use;
4. active architecture bug — which must be fixed before completion.

No active source runtime/UI/config/schema may describe the removed reader architecture.

---

# 18. Loop engineering rule

Do not stop after:
- fixing the three residuals;
- adding migration 0060;
- rendering the PDFs;
- producing crops;
- reading one page;
- writing transcripts;
- passing unit tests;
- starting containers;
- first Chrome pass.

Keep looping:

```
inspect
→ remove residual legacy
→ migrate clean DB
→ render/layout/crop
→ visually read crops
→ seal primary readings
→ import Machine Candidates
→ validate DB/API
→ run tests
→ Chrome MCP
→ cold rebuild
→ repeat validation
```

until the technical pilot is complete.

If the only remaining action is genuine human review, return `READY_FOR_HUMAN_REVIEW`, not BLOCKED.

A context-window limit is not a product blocker. Persist concise checkpoint notes in the repo/private evidence if necessary and continue as far as the environment allows.

---

# 19. Final status choices

Return exactly one:

## A. `CLEAN SLATE VISUAL PILOT: PASS`

Use only if genuine human verification was actually performed and all required source gates passed.

## B. `CLEAN SLATE VISUAL PILOT: READY_FOR_HUMAN_REVIEW`

Use when:
- zero-legacy architecture is complete;
- both pilot PDFs are freshly rendered/read/imported;
- all technical/runtime validations pass;
- real candidates are correctly unverified;
- only genuine human review remains before vector/RAG work.

## C. `CLEAN SLATE VISUAL PILOT: BLOCKED`

Use only for a genuine external/environmental blocker that prevents the technical rebuild itself.

Context exhaustion alone is not a BLOCKED reason.

---

# 20. Commit/push

If technical acceptance is complete and only human review remains, commit and push:

`refactor(source-v2): complete direct visual agent pilot rebuild`

If fully human-verified PASS is achieved, the same commit message is acceptable.

Do not label a partial implementation as PASS.

---

# 21. Final response format

Return only:

- CLEAN SLATE VISUAL PILOT: PASS / READY_FOR_HUMAN_REVIEW / BLOCKED
- final commit hash
- residual legacy cleanup result
- schema/single-reader cleanup result
- DB reset/migration result
- pilot documents/pages processed
- number of canonical regions/crops
- number of primary visual transcripts
- real human verification count
- downstream gate counts
- backend/frontend/type/lint/build results
- Chrome DevTools MCP result
- DB/API validation result
- cold restart result
- repo-wide zero-legacy proof
- exact remaining human-review action OR external blocker
