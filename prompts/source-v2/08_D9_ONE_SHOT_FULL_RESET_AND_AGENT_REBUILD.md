# D9 ONE-SHOT — Full legacy reset + fresh direct visual agent rebuild

## Mission

Execute this task END-TO-END in one autonomous run.

Do NOT stop after:
- mapping
- audit
- partial deletion
- one checkpoint
- one passing test group
- one document
- one UI fix
- one restart

Keep looping:

```
inspect
→ implement
→ run
→ inspect failures
→ fix
→ rerun
→ validate runtime
→ validate data
→ continue
```

until the full acceptance criteria at the end are satisfied, or there is a genuine hard blocker that cannot be resolved inside the repository/runtime.

This is intentionally a "loop engineering" task.

The goal is to remove the old source-reading world completely and rebuild source content from immutable originals using ONLY the current executing AI agent as a direct visual transcription / visual source-reading agent.

---

# 1. FINAL ARCHITECTURE — ONLY THIS MAY SHIP

The final source pipeline must be:

```
Immutable original source
→ deterministic 300-DPI render
→ deterministic layout / region segmentation
→ canonical crop for EVERY region
→ CURRENT EXECUTING AI AGENT visually reads the exact canonical crop
→ primary transcript JSON
→ schema/checksum/bbox/provenance seal
→ deterministic validators
→ Machine Candidate = primary transcript
→ human review
→ Verified Source Content
→ only then educational analysis / knowledge / embeddings / RAG / generation
```

There must be NO second source-reading architecture.

There must be NO active:

- Tesseract source OCR
- generic OCR source-reader pipeline
- Qwen source reader
- Ollama source reader
- old OpenAI source reader
- old document-understanding source reader
- source consensus / voting reader
- OCR prefill
- reader witness voting
- old page-reading worker
- old source-reading worker
- old source-understanding worker
- old extraction-to-reader flow
- old whole-page machine-text review workflow

The current AI agent is the ONLY machine reader of canonical crop pixels.

Code may prepare, validate, persist, hash, crop, gate and serve source evidence.

Code MUST NEVER generate or infer source transcription text.

---

# 2. START FROM THE BEGINNING FOR SOURCE-DERIVED CONTENT

Do not trust or reuse old machine-readable source text.

For every document being rebuilt:

DO NOT feed the current agent:

- old Tesseract text
- old OCR TSV
- old Qwen text
- old OpenAI source-reading output
- old consensus result
- old machine candidate
- old verified source text
- old corrections
- old witness output
- old source-reading logs
- old source-understanding content
- previous generated source text

The fresh read must come from:

- immutable original source
- deterministic render
- canonical crop image
- source identity
- bbox/crop provenance
- D17 exact-fidelity rules
- D18 source-kind rules
- output JSON schema

Nothing else.

If independence requires a fresh isolated subagent/session, create one with empty source-text context and give it ONLY the canonical crop(s), rules and output schema.

Seal the agent output VERBATIM before any comparison with historical content.

---

# 3. WHAT MUST BE PRESERVED

Preserve the things that are not legacy readers:

- immutable original PDFs/files
- immutable object-storage source identity
- checksums
- deterministic 300-DPI rendering
- deterministic layout / region segmentation
- canonical crops
- bbox provenance
- crop SHA-256
- page-image serving
- generic PDF rendering primitives
- Source V2 persistence structure where still appropriate
- D18 source kinds
- human verification workflow
- append-only audit/review history where required for compliance
- verified-source gate semantics
- generic alignment helpers
- all historical database migrations
- generation, embeddings and semantic verification systems that are downstream and are NOT source readers

Historical migrations must NEVER be deleted or rewritten.

Historical docs/benchmarks/archive may remain if clearly non-runtime.

---

# 4. WHAT MUST BE REMOVED

Remove the legacy source architecture completely.

At minimum audit and remove active runtime/files/wiring for:

- `documents/tesseract_ocr.py`
- `documents/ocr.py`
- `documents/page_reading.py`
- `documents/page_reading_jobs.py`
- `documents/extraction_service.py`
- `documents/jobs.py`
- `documents/source_reading.py`
- `documents/source_reading_journal.py`
- `documents/source_reading_openai.py`
- `documents/source_reading_qwen.py`
- `documents/source_consensus.py`
- `documents/source_consensus_provider.py`
- `documents/source_machine.py`
- `documents/source_machine_models.py`
- `documents/source_machine_service.py`
- `documents/source_renders.py` if legacy-only
- old understanding reader/provider/runtime/job/service files if they exist only for source reading
- old source-fidelity/page-reading routes if they belong to the V1 reader workflow
- old source-understanding routes if they belong to the V1 reader workflow
- old extraction review/source-reader UI
- old whole-page review-text UI
- old OCR/Qwen/OpenAI source-reader status/help text
- legacy reader buttons/actions
- legacy reader API contracts
- legacy source-read job API fields such as `source_read_job_id`
- legacy reader workers/actors
- legacy reader dispatchers
- legacy reader startup registration
- legacy reader upload hooks
- legacy reader config/env
- legacy reader secrets
- legacy reader dependencies
- Tesseract installation in runtime images if no surviving feature needs it
- Qwen/Ollama source-reader config
- source-reader OpenAI model/config
- tests that exist only for removed reader architecture

Do not keep duplicate architecture "just in case."

---

# 5. IMPORTANT KNOWN DEPENDENCIES — HANDLE THEM, DO NOT STOP

The previous map already proved two important hazards.

## 5.1 PDF render helpers inside tesseract_ocr.py

`page_images.py` depends on:

- `RenderedPageImage`
- `open_pdf_file`

These are deterministic PDF primitives, NOT OCR.

Move them and the minimum neutral supporting validation/errors into a neutral module such as:

`exam_guru_api.documents.pdf_render`

Then repoint all surviving code.

Do NOT keep `tesseract_ocr.py` merely because of these helpers.

The neutral helper must preserve:

- regular file descriptor checks
- PDF signature validation
- optional SHA-256 validation
- source mutation detection
- malformed/encrypted PDF rejection
- guaranteed cleanup
- existing page-image behavior

After the move, no surviving Source V2/page-image code may import a Tesseract module.

## 5.2 Upload completion currently dispatches V1 reading

The previous map proved:

`resumable_uploads.py`
→ `queue_source_read`
→ `page_reading_jobs.py`

and `upload_jobs.py` dispatches V1 reading after finalization.

Remove this coupling completely.

After the cutover:

```
upload completes
→ immutable source is stored
→ source becomes available for Source V2 / agent rebuild
→ NO legacy source-read job is created
→ NO legacy reader actor is dispatched
```

Keep resumable upload/recovery functionality itself.

---

# 6. REMOVE THE OLD REVIEW UI COMPLETELY

The old UI represented by the whole-page `review-text` workflow must be removed.

That means remove the UI/routes/API behavior where reviewers see:

- whole PDF page on the left
- old machine/OCR text on the right
- Tesseract TSV-like text
- Qwen/OpenAI/consensus machine text
- legacy "AI read this page" state
- legacy re-read buttons/jobs

Do not merely hide it.

Remove its active route/component/data source/runtime dependencies.

The surviving review experience must be Source V2:

```
document/page
→ deterministic regions
→ exact canonical crop
→ source kind
→ current-agent primary transcript
→ human confirm/correct/exclude
```

Reviewer-facing help/status text should be Sinhala where practical.

Original source text itself must NEVER be translated or normalized.

---

# 7. RESET ACTIVE DERIVED SOURCE DATA

Because this is a "start from the beginning" rebuild, do not continue trusting old machine-derived source content.

Create a SAFE forward-only reset/migration/admin rebuild procedure for ACTIVE source-derived data.

Do NOT delete historical migration files.

Do NOT delete immutable originals.

The reset should clear or supersede ACTIVE legacy-derived artifacts such as, where applicable:

- old OCR candidates
- old page-reading candidates
- old source-read jobs
- old source-understanding jobs
- old reader witnesses
- old consensus candidates
- old whole-page machine text
- old active verified-source content that came from the pre-reset source-reading chain
- old Source V2 active primary/verified rows for documents that are intentionally being rebuilt from scratch

Preserve append-only historical audit evidence if required, but mark/supersede it so it is NOT eligible as current source evidence and is NEVER provided to the fresh reading agent.

Never make old content eligible for knowledge/RAG during the rebuild.

Hard invariant during reset/rebuild:

```
NO VERIFIED SOURCE CONTENT
→ NO EDUCATIONAL ANALYSIS
→ NO KNOWLEDGE
→ NO EMBEDDINGS
→ NO RAG
→ NO GENERATION
```

until each source region/document earns verification again.

---

# 8. FRESH CURRENT-AGENT VISUAL REBUILD

After the legacy system and active legacy-derived source data are removed/reset, rebuild source evidence from immutable originals.

For each source document:

1. validate immutable source identity/checksum
2. render deterministically at 300 DPI
3. produce deterministic regions
4. produce canonical crop for every region
5. assign/propose D18 source kind
6. invoke a fresh isolated current-agent visual-reading context
7. pass ONLY the canonical crop + fidelity rules + source-kind rules + JSON schema
8. visually read the crop directly
9. preserve exact printed Unicode
10. preserve apparent source typos
11. preserve punctuation and spacing as visible
12. do not solve exercises
13. do not normalize Sinhala/Tamil/English
14. do not invent hidden text
15. abstain when unreadable
16. return exact primary transcript JSON
17. seal transcript + crop checksum + bbox + source identity
18. persist Machine Candidate = primary transcript
19. human-review gate
20. only verified source becomes downstream-eligible

D18 behavior:

- TEXT_ONLY → exact printed text required
- VISUAL_ONLY → educational visual, zero source characters allowed
- VISUAL_WITH_TEXT → visual + exact printed labels/text
- DECORATIVE → no educational source embedding
- UNDECIDED → explicit human decision required

Generated image descriptions are DERIVED KNOWLEDGE, never Verified Source Content.

---

# 9. REBUILD THE CORPUS, NOT JUST A DEMO PAGE

Do not stop after proving one page.

Rebuild all currently active source documents that are in the current working corpus / Studio set.

At minimum reprocess and verify the known acceptance documents:

- teacher-guide page 156
- teacher-guide page 186
- sankhya-rata full document

The rebuild must come from fresh visual reads of canonical crops, not reused text.

For `sankhya-rata`, use a genuinely isolated blind read again.

The final system must be capable of continuing the same process for the rest of the corpus.

---

# 10. DELETE LEGACY TESTS, WRITE NEW ARCHITECTURE TESTS

Delete tests whose only purpose is to validate:

- Tesseract
- OCR port
- Qwen source reader
- OpenAI source reader
- source consensus
- old page reading
- old source-read jobs
- old source-understanding jobs
- old extraction-reader workflow
- old review-text UI

Do NOT delete historical migrations.

Keep and strengthen Source V2 tests.

Add/adjust tests proving:

- upload completion does not create/dispatch legacy read jobs
- page-image rendering works without Tesseract
- no legacy reader actor is registered
- no legacy reader route is exposed
- no Source V2 code imports legacy reader modules
- visual_only verifies with zero text
- visual_with_text cannot verify empty
- text-only cannot verify empty/abstained text
- crop SHA and bbox survive
- review history remains append-only
- verified-source gate blocks downstream use until verification
- reset content cannot leak into fresh agent input
- fresh agent transcript is sealed before comparison/review

---

# 11. REMOVE LEGACY WORKERS

Remove these actors and their recovery actors from worker registration and implementation:

- `extract_document`
- `recover_extraction_jobs`
- `read_source`
- `recover_source_read_jobs`
- `understand_source_page`
- `recover_understanding_page_jobs`

Keep unrelated actors:

- source upload finalization/recovery
- generation/recovery
- embeddings/recovery
- knowledge preparation/recovery
- storage reconciliation
- teacher-paper generation/recovery

---

# 12. REMOVE LEGACY CONFIG/ENV/DEPS

Remove active source-reader settings families including, where legacy-only:

- `ocr_provider`
- `ocr_tesseract_*`
- `source_consensus_enabled`
- `source_qwen_*`
- source-reader Ollama/Qwen endpoints
- old source-understanding OpenAI provider settings
- old source-reader API secrets/model config

Do NOT remove OpenAI settings for surviving downstream systems such as:
- generation
- embeddings
- semantic verification

Keep PyMuPDF if deterministic render/page-image code needs it.

Remove Tesseract OS packages/runtime dependencies if nothing surviving uses them.

---

# 13. REPO-WIDE ZERO-LEGACY PROOF

Search the entire repository for:

- tesseract
- OCR / ocr
- qwen
- ollama
- source_reading
- source-reading
- page_reading
- page-reading
- source_consensus
- consensus reader
- source_read_job
- source_read_job_id
- understanding_openai
- document_understanding in source-reader context
- old actor names
- old review-text route/component names

Every remaining match must be classified.

Allowed remaining matches:

1. immutable historical migration
2. historical benchmark/docs/archive
3. unrelated non-source-reader feature
4. explicit migration compatibility code that is not reachable at runtime

No active source-reader runtime/config/UI/test dependency may remain.

---

# 14. RUNTIME VALIDATION — USE CHROME DEVTOOLS MCP THROUGHOUT

Do not validate only with static code.

Use Chrome DevTools MCP.

Run the real stack and validate:

- Materials
- upload flow
- Source V2 Studio
- page images
- canonical crops
- region focus
- source kinds
- fresh agent-derived transcripts
- verify/correct/exclude actions
- network
- console
- API
- DB

Remove the old `review-text` workflow completely and prove it is no longer reachable.

Confirm the new Source V2 review flow is the only source-review UI.

---

# 15. COLD RESTART / CONTAINER REGRESSION

Perform a true cold rebuild/restart.

Explicitly rebuild:

- migrate
- api
- worker
- web if required

Do not rely on stale images.

The known stale migrate-image problem around migration 0059 must not recur.

After cold restart, rerun the SAME runtime acceptance cases.

---

# 16. REQUIRED FINAL ACCEPTANCE

Do not stop until all are true:

1. only one active source architecture exists
2. Tesseract source OCR runtime is gone
3. Qwen/Ollama source-reader runtime is gone
4. old OpenAI source-reader runtime is gone
5. source consensus/voting runtime is gone
6. old whole-page review-text UI is gone
7. old legacy reader routes are gone
8. old legacy reader actors are gone
9. upload completion creates no legacy source-read job
10. no active reader env/config remains
11. no dead source-reader dependency remains
12. page-image rendering still works
13. deterministic PDF primitives live in neutral modules
14. fresh current-agent canonical-crop reading works
15. agent input contains no old source text
16. transcripts are sealed before review/comparison
17. Source V2 human verification works
18. teacher-guide pages 156 and 186 are freshly rebuilt/resolved
19. p186-r002 is correctly handled under D18 visual-only rules
20. sankhya-rata is freshly rebuilt from an isolated blind current-agent read
21. active OCR reader rows are 0
22. downstream gates block unverified content
23. backend tests pass
24. frontend tests pass
25. Ruff passes
26. TypeScript passes
27. builds pass
28. migration sanity passes
29. Chrome DevTools MCP regression passes
30. Network is clean
31. Console is clean except explicitly accepted non-blocking advisories
32. cold restart passes
33. second runtime validation after cold restart passes
34. repo-wide legacy search is fully classified
35. master is committed and pushed

Do NOT use the old 85-test count as a target. Report actual surviving/new test counts.

---

# 17. AUTONOMOUS LOOP RULE

This is critical.

Do NOT return after saying:

- "mapping complete"
- "I found a dependency"
- "next session should continue"
- "context is low"
- "I created a plan"
- "I created a prompt"
- "I removed part 1"
- "tests are partially passing"

Instead:

- compact context if needed
- use durable repo notes if needed
- continue the SAME mission
- fix failures
- rerun
- keep going

Only stop for:

A. FULL PASS

or

B. a genuine hard external blocker that cannot be solved by code/runtime work.

If B occurs, leave the repo buildable and explain the exact blocker.

---

# 18. FINAL COMMIT

When all acceptance criteria pass:

commit:

`refactor(source-v2): replace legacy readers with direct visual agent source rebuild`

push `master`.

---

# 19. FINAL RESPONSE FORMAT

Return ONLY:

- D9 ONE-SHOT status: PASS / BLOCKED
- final commit hash
- old runtime/UI removed summary
- fresh-agent rebuild summary
- documents/pages rebuilt
- actual backend/frontend/lint/type/build results
- Chrome DevTools MCP result
- cold restart result
- repo-wide zero-legacy proof
- any hard blocker if BLOCKED
