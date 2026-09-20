# CLEAN SLATE — remove all old source data and rebuild a 2–4 PDF pilot with direct visual agent reading

## Authority

This prompt SUPERSEDES the corpus-rebuild scope in:
- `prompts/source-v2/08_D9_ONE_SHOT_FULL_RESET_AND_AGENT_REBUILD.md`

The architectural removal requirements from D9 remain, but the DATA strategy changes:

**Do not preserve old source/corpus/review/knowledge data. Start clean.**
Only immutable original PDF bytes selected for the new pilot may be retained/reused.
Do not retain or reuse old OCR, transcripts, candidates, verified text, reader output, knowledge, embeddings or generated educational data.

The user explicitly accepts destructive reset of old data.

---

# 1. END STATE

Ship exactly one source architecture:

```
fresh original PDF
→ deterministic 300-DPI render
→ deterministic layout / region segmentation
→ canonical crop for every region
→ current executing AI agent visually reads the exact crop
→ primary transcript JSON
→ checksum / bbox / crop provenance seal
→ deterministic validation
→ human verify / correct / exclude
→ Verified Source Content
→ downstream gates
```

No active:
- Tesseract source OCR
- Qwen/Ollama reader
- old OpenAI source reader
- consensus/voting reader
- old page reader
- old source-understanding reader
- old review-text UI
- old source-read jobs
- old OCR/source reader config or dependencies

---

# 2. THIS IS A DESTRUCTIVE CLEAN-SLATE RESET

Old source-derived data is NOT valuable for this rebuild.

Do NOT migrate it.
Do NOT compare against it.
Do NOT feed it to the new agent.
Do NOT preserve it merely for convenience.

The only historical things that remain are:
- Git history
- historical migration files
- historical docs/benchmarks/archive
- immutable original PDF bytes that are intentionally selected for the NEW pilot

## Database strategy

Prefer the cleanest reliable reset:

1. stop API/worker/web writes
2. identify the active application database
3. DROP/RECREATE the application database/schema, or equivalently truncate/recreate all application tables if infrastructure makes database recreation impractical
4. run Alembic migrations from base to HEAD
5. seed ONLY the minimum reference/config/auth/catalogue records required for the application to run
6. do NOT restore old source documents, old review records, old knowledge, old embeddings, old generations, old jobs, old audit-derived source text, or old candidates
7. prove old source-derived row counts are zero before importing pilot PDFs

Do not edit or delete historical migration files.

If authentication/admin bootstrap data is required, recreate it from deterministic seed/config rather than restoring old DB rows.

## Object-storage / filesystem strategy

Do not let old derived artifacts leak into the new run.

Delete/reset old derived:
- crops
- OCR artifacts
- reader outputs
- candidate JSON
- verified JSON
- source-understanding artifacts
- embeddings/knowledge artifacts
- old generated source text
- job artifacts

For original PDFs:
- before destructive storage cleanup, select 2–4 pilot PDFs and preserve ONLY their immutable original bytes + checksum/filename metadata, or re-upload them from the known source files after reset
- never preserve sidecar text/candidates/readers/verified data with them

The fresh pilot must begin from PDF bytes only.

---

# 3. PILOT SET — ONLY 2 TO 4 PDFs

Do NOT rebuild the full corpus in this run.

Choose 2–4 representative PDFs from the existing original source set.

The pilot should include, when their immutable originals are available:

1. the teacher-guide document containing pages 156 and 186
2. `sankhya-rata`
3. optionally 1–2 additional PDFs chosen for diversity:
   - Sinhala-heavy text
   - visual-only regions
   - visual-with-text/diagram labels
   - mixed layout/table/list content

Selection must be based on original PDFs and document identity only, not old transcripts.

After reset, import ONLY these pilot PDFs.

No other document should be active in the new database unless required as deterministic seed/reference data.

---

# 4. REMOVE OLD SOURCE ARCHITECTURE IN THE SAME RUN

Use the proven map in:
`docs/source-v2/D9_CUTOVER_MAP.md`

Do not stop after mapping.

## Preserve neutral render primitives

Before deleting `tesseract_ocr.py`:
- move `RenderedPageImage`
- move `open_pdf_file`
- move minimum neutral validation/errors

to a neutral module such as:
`exam_guru_api.documents.pdf_render`

Preserve:
- FD regular-file validation
- PDF signature validation
- optional source checksum validation
- mutation detection
- encrypted/malformed rejection
- deterministic cleanup
- page image behavior

Then no surviving code may import `tesseract_ocr.py`.

## Cut upload-to-V1-reader coupling

After reset/new architecture:

```
upload finalizes
→ original PDF stored
→ Source V2 document/import state created
→ NO source-read job
→ NO page-reading job
→ NO legacy actor dispatch
```

Remove obsolete response/API fields such as `source_read_job_id`.

## Remove legacy runtime/UI

Remove active implementation/wiring for:
- Tesseract/OCR source reader
- page reading
- extraction reader pipeline
- source_reading*
- source_consensus*
- source_machine*
- Qwen source reader
- old OpenAI source reader
- old understanding reader/jobs/runtime
- old source fidelity/re-read workflow where V1-only
- old whole-page `review-text` UI
- legacy reader APIs/buttons/status text
- legacy reader worker actors
- legacy startup dispatchers
- legacy env/config/dependencies
- legacy-only tests

Do not remove Source V2, page image serving, deterministic rendering/layout/crops, D18 kinds, or human verification.

---

# 5. FRESH PILOT REBUILD — ABSOLUTELY NO OLD TEXT

For every region in every pilot PDF, the reading agent may receive ONLY:

- canonical crop image
- document/page/region identity
- crop SHA-256
- bbox
- D17 fidelity rules
- D18 source-kind definitions
- transcript JSON schema

The reading agent MUST NOT receive:

- old OCR/Tesseract
- old Qwen
- old OpenAI
- old consensus
- old page text
- old candidate text
- old verified source
- old correction history
- old reader logs
- old understanding output
- old embeddings/knowledge

Use fresh isolated context/subagents where needed.

Seal the returned transcript VERBATIM before human review or comparison.

No code-generated transcription.

---

# 6. D18 RULES

`TEXT_ONLY`
→ exact printed text required

`VISUAL_ONLY`
→ educational visual with no printed text
→ zero source characters allowed

`VISUAL_WITH_TEXT`
→ preserve visual evidence + exact printed labels/text

`DECORATIVE`
→ no educational embedding/source use

`UNDECIDED`
→ explicit human decision required

Generated image descriptions are derived knowledge, never Verified Source Content.

---

# 7. HUMAN REVIEW

After each pilot PDF is freshly read:

- reviewer sees canonical crop / relevant page context
- reviewer sees fresh primary transcript only
- reviewer can confirm / correct / exclude
- Sinhala reviewer-facing instructions/status text where practical
- original source text is never translated or normalized

Do not reintroduce the old whole-page OCR review screen.

---

# 8. HARD DATA GATES

Fresh database begins with zero source evidence.

Until regions are verified:

```
NO VERIFIED SOURCE CONTENT
→ NO EDUCATIONAL ANALYSIS
→ NO KNOWLEDGE
→ NO EMBEDDINGS
→ NO RAG
→ NO GENERATION
```

Prove downstream queries/jobs respect this.

Do not generate embeddings/knowledge for the pilot until the relevant source is verified.

---

# 9. REQUIRED PILOT ACCEPTANCE

At the end, the fresh DB must contain only:
- required seed/reference/auth/config data
- 2–4 newly imported pilot PDFs
- their newly generated deterministic render/layout/crops
- fresh current-agent primary transcripts
- new review/verification state
- new append-only audit events generated during this run
- downstream derived data only where the new verified-source gate legitimately allows it

There must be NO old corpus/source-derived rows.

For the known pilot documents:

## Teacher guide
Freshly rebuild from original PDF.
Pages 156 and 186 must be processed under the new architecture.

`p186-r002` must correctly work as VISUAL_ONLY with zero source characters if the new visual inspection supports that classification.

Do not copy its old classification merely because it existed before.

## sankhya-rata
Freshly rebuild the full pilot document using an isolated blind visual read.
Do not reuse old text.
Final gate may become `usable:true` only if the newly verified data earns it.

Do not force old counts/results to match.

---

# 10. TESTS

Delete obsolete reader tests and update surviving tests.

Add/retain tests proving:

- fresh DB bootstrap works
- old source tables/data are empty after reset where expected
- only pilot docs are imported
- upload does not queue old reader jobs
- page image rendering works without Tesseract
- no legacy actors registered
- no legacy routes
- old review-text UI unreachable
- Source V2 canonical crops work
- fresh transcript provenance is crop-bound
- agent input cannot include old transcript fields
- visual_only supports zero text
- visual_with_text requires text
- text-only cannot verify empty
- crop SHA/bbox survive
- review history append-only
- downstream gate blocks unverified content

Run:
- backend tests
- Source V2 real DB tests
- frontend tests
- Ruff
- TypeScript
- builds
- migration base→HEAD
- upload tests
- page image tests

Report ACTUAL post-reset test counts.

---

# 11. CHROME DEVTOOLS MCP — REQUIRED

Use Chrome DevTools MCP on the real running stack.

Validate:

1. fresh Materials list contains only pilot docs
2. upload/import flow works without old reader dispatch
3. old `review-text` route/UI is gone
4. Source V2 Studio is the only source-review workflow
5. canonical page/crop images load
6. new transcript/review data comes from the new run
7. Network clean
8. Console clean except accepted non-blocking advisory
9. API data matches DB

---

# 12. COLD REBUILD / RESTART

Do a true cold rebuild after code + DB reset:

- migrate
- api
- worker
- web

Explicitly rebuild images so the known stale migrate-image issue cannot recur.

Then run migrations on the new database and rerun the same pilot validation.

---

# 13. REPO-WIDE ZERO-LEGACY PROOF

Search entire repo for:

- tesseract
- ocr
- qwen
- ollama
- source_reading
- page_reading
- source_consensus
- source_read_job
- source_read_job_id
- understanding_openai
- old reader actor names
- old review-text route/component

Remaining occurrences are allowed only if:
- historical migration
- historical docs/benchmark/archive
- unrelated non-source-reader use

No active runtime/UI/config/test reference may remain.

---

# 14. AUTONOMOUS LOOP

Do not stop at intermediate milestones.

Keep going:

```
inspect
→ change
→ run
→ inspect
→ fix
→ rerun
→ reset DB
→ migrate
→ import pilot
→ visually read
→ verify
→ runtime test
→ cold restart
→ retest
```

until PASS.

Do not return because:
- context is low
- mapping is done
- a dependency was found
- one PDF worked
- one test group passed

Use durable repo notes/commits if context management is needed, but continue the same mission.

Only stop for:
A. full PASS
B. genuine external blocker

---

# 15. FINAL PASS CRITERIA

All must be true:

1. old source/corpus/review/knowledge data removed
2. fresh DB migrated from clean state
3. only 2–4 pilot PDFs imported
4. Tesseract source reader gone
5. Qwen/Ollama source reader gone
6. old OpenAI source reader gone
7. consensus reader gone
8. old page reader gone
9. old source-understanding reader gone
10. old review-text UI gone
11. old reader routes gone
12. old reader actors gone
13. old reader config/env/dependencies gone
14. deterministic PDF rendering still works from neutral module
15. upload creates no legacy source-read job
16. current AI agent visually reads canonical crops directly
17. agent receives no old source text
18. fresh transcripts are sealed with crop/bbox/checksum provenance
19. human review works
20. D18 visual-only zero-text case works
21. downstream gates work
22. teacher-guide pilot rebuilt
23. sankhya-rata pilot rebuilt blindly
24. no non-pilot old source document remains active
25. backend tests pass
26. frontend tests pass
27. Ruff passes
28. TypeScript passes
29. builds pass
30. migrations pass from clean DB
31. Chrome DevTools MCP passes
32. Network clean
33. Console clean except accepted advisory
34. cold restart passes
35. second post-restart pilot validation passes
36. repo-wide zero-legacy search classified
37. master committed and pushed

---

# 16. FINAL COMMIT

Commit:

`refactor(source-v2): reset source data and rebuild visual-agent pilot`

Push `master`.

---

# 17. FINAL RESPONSE FORMAT

Return ONLY:

- CLEAN SLATE PILOT status: PASS / BLOCKED
- final commit hash
- DB reset summary
- object/artifact reset summary
- pilot PDFs imported
- old architecture/UI removed summary
- fresh visual-agent rebuild summary
- new data row/count summary
- backend/frontend/lint/type/build results
- Chrome DevTools MCP result
- cold restart result
- zero-legacy proof
- blocker if BLOCKED
