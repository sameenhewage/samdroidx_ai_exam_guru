# Source V2 — D9 Complete Legacy/V1 Source Cutover

This is an execution prompt, not a design discussion.

## Starting point

Start from current `master` at/after:

- `c453f16` — `sankhya-rata` independently rebuilt and re-earned `usable:true`
- pages 156 and 186 remain resolved under D17 + D18
- active Source V2 OCR reader rows = 0
- current Source V2 runtime is proven on real documents

Read first:

1. `docs/source-v2/DECISIONS.md`
2. `docs/source-v2/STATE.md`
3. `prompts/source-v2/00_MASTER_SOURCE_V2_REBUILD.md`
4. this prompt

Locked decisions are authoritative.

Especially:

- D9 — exactly one source architecture ships
- D10 — only narrow reusable deterministic pieces survive
- D17 — executing agent is the ONLY text-extraction step
- D18 — verified visual-source semantics

Do not redesign D17/D18.

---

# 1. Objective

Complete the D9 cutover.

The repository must stop shipping the legacy/V1 source-reading architecture.

After this task, the source path must be unambiguous:

```
immutable source
→ deterministic render
→ deterministic layout
→ canonical crop
→ executing agent directly reads crop
→ seal/checksum/schema validation
→ deterministic validators
→ Machine Candidate
→ human review
→ Verified Source Content
```

There must be no active/runtime source OCR, no source model reader, no source consensus engine, and no obsolete document-understanding source reader.

This is a **removal/cutover task**, not a compatibility task.

Do not retain a hidden fallback.

Do not leave dormant configuration that can reactivate the old source architecture.

---

# 2. Important finding: legacy code is currently still actively wired

Do not assume these files are dead merely because Source V2 itself does not call them.

Current repository evidence shows legacy/V1 source paths are still wired into application startup and workers.

Examples already observed:

## API startup

`apps/api/src/exam_guru_api/main.py` currently imports/creates:

- `ExtractionDispatcher`
- `SourceReadDispatcher`
- `UnderstandingJobDispatcher`
- `UnderstandingRuntime`

and stores them on `application.state`.

## Worker startup

`apps/api/src/exam_guru_api/worker.py` currently registers:

- `extract_document`
- `recover_extraction_jobs`
- `read_source`
- `recover_source_read_jobs`
- `understand_source_page`
- `recover_understanding_page_jobs`

These are active actor registrations, not historical files.

## Tesseract path

`apps/api/src/exam_guru_api/documents/jobs.py` currently imports:

- `OCRPort`
- `TesseractCliOCRAdapter`
- `TesseractOCRConfig`

and can build a real Tesseract OCR adapter from Settings.

## Source upload path

`apps/api/src/exam_guru_api/documents/upload_jobs.py` currently imports and dispatches the legacy page-reading pipeline after upload finalization.

A completed upload can find a queued `SourceReadJobModel` and dispatch it through `DramatiqSourceReadDispatcher`.

## Registered HTTP routes

`apps/api/src/exam_guru_api/api/router.py` currently includes legacy routes such as:

- old document extraction/review surfaces
- source understanding routes

These must be audited and removed where they belong to V1.

Therefore the D9 cutover is not just deleting `tesseract_ocr.py`.

It requires removing the entire reachable legacy source call graph safely.

---

# 3. Hard safety boundary: do NOT delete unrelated AI capabilities

The following may legitimately remain if they are used by downstream product functions:

- OpenAI question generation
- OpenAI embeddings
- semantic verification
- retrieval
- teacher-paper generation
- downstream derived educational analysis that consumes VERIFIED SOURCE CONTENT

Do not perform a blind grep-and-delete for the word `openai`.

Remove OpenAI only where it is part of the **obsolete source-reading / source-understanding architecture**.

Likewise, do not remove generic:

- immutable source storage
- resumable upload mechanics
- source identities/checksums
- deterministic PDF rendering
- deterministic page geometry/layout
- page image serving required by Studio
- Source V2 persistence/review
- verified-source gates
- generic audit/history required by current product
- downstream generation/retrieval/embedding infrastructure

D9 is about old source extraction/reading/understanding, not deleting the whole document domain.

---

# 4. First step — build an exact active call graph

Before deleting code, produce a concrete dependency map.

Trace from these roots:

- `main.py`
- `worker.py`
- `api/router.py`
- source upload completion
- document routes
- startup/lifespan
- background recovery jobs
- DI/application state
- config/settings
- compose/Docker/env

For every legacy module classify:

1. active runtime
2. active route dependency
3. active worker dependency
4. test-only
5. historical migration/data model
6. safe reusable deterministic primitive
7. dead code

Do not remove a module until its callers are understood.

Do not call a module dead merely because current DB rows are zero.

---

# 5. Legacy families that must be investigated for removal

At minimum audit these files/families:

## OCR

- `apps/api/src/exam_guru_api/documents/ocr.py`
- `apps/api/src/exam_guru_api/documents/tesseract_ocr.py`

## legacy extraction pipeline

- `apps/api/src/exam_guru_api/documents/extraction.py`
- `apps/api/src/exam_guru_api/documents/extraction_service.py`
- `apps/api/src/exam_guru_api/documents/extraction_outbox.py`
- `apps/api/src/exam_guru_api/documents/jobs.py`

Do not automatically delete `extraction.py` if a deterministic reusable PDF primitive is still required elsewhere. D10 requires narrow deterministic reuse to be **ported into a current package**, not retained through the legacy module.

## legacy page reading

- `apps/api/src/exam_guru_api/documents/page_reading.py`
- `apps/api/src/exam_guru_api/documents/page_reading_jobs.py`

## source model readers / consensus

- `apps/api/src/exam_guru_api/documents/source_reading.py`
- `apps/api/src/exam_guru_api/documents/source_reading_journal.py`
- `apps/api/src/exam_guru_api/documents/source_reading_openai.py`
- `apps/api/src/exam_guru_api/documents/source_reading_qwen.py`
- `apps/api/src/exam_guru_api/documents/source_consensus.py`
- `apps/api/src/exam_guru_api/documents/source_consensus_provider.py`

## obsolete source understanding

Audit the full family:

- `apps/api/src/exam_guru_api/documents/understanding_contracts.py`
- `apps/api/src/exam_guru_api/documents/understanding_jobs.py`
- `apps/api/src/exam_guru_api/documents/understanding_models.py`
- `apps/api/src/exam_guru_api/documents/understanding_openai.py`
- `apps/api/src/exam_guru_api/documents/understanding_provider.py`
- `apps/api/src/exam_guru_api/documents/understanding_runtime.py`
- `apps/api/src/exam_guru_api/documents/understanding_service.py`
- `apps/api/src/exam_guru_api/documents/understanding_verification.py`
- `apps/api/src/exam_guru_api/api/routes/understanding.py`

Determine whether every member belongs to the obsolete source-understanding architecture.

If some generic downstream educational-analysis concept has been placed in this family, move/port only the needed current concept behind the verified-source gate before deleting the old source-reader implementation.

Do not keep the legacy family just because one neutral helper is convenient.

## old fidelity/review families

Audit:

- `fidelity*.py`
- legacy page candidate/review models
- source-read job models
- source-consensus models
- source-understanding models

Keep only what current Source V2 or current downstream functionality truly requires.

Do not delete Source V2 review history.

---

# 6. Remove active startup wiring

After the dependency map is clear, remove legacy startup/runtime registration.

`main.py` must no longer:

- create a legacy extraction dispatcher for source OCR
- create a source-read dispatcher
- create a source-understanding dispatcher
- create an obsolete source-understanding runtime
- expose those objects through application state

unless a specific surviving non-source legacy function is proven necessary.

`worker.py` must no longer register:

- old extraction/OCR actors
- source page-reading actors
- source-understanding actors
- their recovery actors

Worker startup after cutover must contain only current product jobs.

Prove via runtime actor registration, not just source inspection.

---

# 7. Cut over source upload finalization

Preserve generic resumable upload if it is still part of the current product.

But completion must not dispatch V1 reading.

Current upload finalization must be changed from:

```
upload complete
→ queued legacy SourceReadJob
→ DramatiqSourceReadDispatcher
→ page reading / OCR / model reader
```

to a current D17-compatible state.

Because D17 requires the executing agent to read canonical crops, the API must not invent an automatic replacement source reader.

A valid post-upload result can be:

```
upload complete
→ immutable source/document persisted
→ source becomes available/pending for Source V2 deterministic preparation
→ no automatic text extraction
```

If current product has a deterministic Source V2 preparation trigger for render/layout/crop, use it only if it performs no text extraction.

Do not create an “agent API” or model provider.

Do not auto-run OCR as a transitional fallback.

Add a clear status/gate so an uploaded source cannot reach educational downstream use until Source V2 verification is complete.

---

# 8. Remove legacy HTTP routes

Audit every route in:

- `api/routes/documents.py`
- `api/routes/understanding.py`
- other source-related route modules

Remove routes that expose:

- old extraction trigger/retry
- OCR retry
- V1 page reading/reread
- source-reader job state
- old source-understanding jobs
- old witness/consensus provider flows
- V1 candidate verification surfaces superseded by Source V2

Do not remove:

- immutable source upload/download needed by current system
- metadata/scope management still needed
- Source V2 routes
- Source V2 Studio review
- page images required by current review UI

After cutover, OpenAPI must contain no old source-reading/understanding endpoints.

Add an explicit API surface regression test.

---

# 9. Remove configuration and environment switches

Audit `Settings`, `.env.example`, Compose, Dockerfiles and docs.

Remove obsolete source settings, including as applicable:

## Tesseract / OCR
- `ocr_provider`
- `ocr_tesseract_*`

## source consensus / model readers
- `source_consensus_enabled`
- `source_qwen_*`
- source-reading OpenAI/Qwen/provider settings

## obsolete document-understanding source path
- `document_understanding_provider`
- `document_understanding_openai_api_key`
- obsolete understanding model/version/pricing/temperature/image-input/source-reading flags
- source-understanding worker/recovery settings

Only remove settings proven to belong to the old source architecture.

Do not remove downstream generation, embedding or semantic-verifier configuration.

After cutover there must be no environment variable that can reactivate legacy source reading.

---

# 10. Remove obsolete dependencies and container packages

Audit:

- `apps/api/pyproject.toml`
- `apps/api/uv.lock`
- `apps/api/Dockerfile`
- Compose/service definitions
- CI setup

Remove dependencies/system packages used solely for removed source OCR/readers.

Examples may include:

- Tesseract runtime packages
- OCR-only Python dependencies
- source-reader provider dependencies
- obsolete model-client dependencies that are not used elsewhere

Be precise.

Do not remove a dependency that is still used by current rendering, storage, generation, embeddings, or other active functionality.

Re-lock dependencies cleanly.

Build containers from scratch after changes.

---

# 11. Remove obsolete tests; replace with cutover regression tests

Delete tests whose only purpose is to test removed legacy functionality.

Likely families include:

- Tesseract adapter/integration tests
- OCR port/evaluation/extraction tests
- page-reading tests
- source-reading OpenAI/Qwen tests
- source consensus tests
- old understanding provider/runtime/job/API tests
- old extraction OCR retry tests

Do not blindly delete all files with “extraction” or “understanding” in their name; first classify what they test.

Preserve tests for generic immutable-source/upload behavior that remains current.

Add new tests proving D9 itself:

1. old source routes are absent from OpenAPI
2. old workers/actors are not registered
3. application startup does not instantiate legacy dispatchers/runtimes
4. upload completion cannot dispatch V1 source reading
5. no active config can select Tesseract/Qwen/OpenAI source readers
6. Source V2 route remains registered
7. verified-source gate still blocks unresolved upload/source
8. current upload still persists immutable source correctly
9. Source V2 review/persistence remains intact
10. downstream generation/embedding OpenAI configuration remains unaffected

---

# 12. Database/history rules

Historical Alembic migrations MUST remain.

Do not rewrite/delete old migration files.

Historical review/audit evidence must not be falsified.

If legacy runtime tables/models are no longer needed:

- determine whether current data must be retained for audit/history
- if tables can safely be dropped, create a NEW forward migration
- if tables must remain as historical storage, remove runtime ORM/service access and document them as inert historical tables

Do not drop data merely to make the architecture visually cleaner.

Source V2 tables/history must remain intact.

Current verified data for:

- pages 156/186
- `sankhya-rata`

must survive the cutover unchanged.

---

# 13. Port narrow reusable pieces, do not import legacy modules

D10 permits only narrow reuse:

- deterministic rendering
- source identities/checksums
- human verification concepts
- provider-neutral alignment primitives explicitly locked by decision

If current code still imports such a primitive from a legacy V1 module:

1. move/port the primitive into an appropriate current package
2. update callers
3. test equivalence
4. remove the legacy module

Do not preserve a 1,000-line V1 module because one 20-line deterministic helper is useful.

---

# 14. Source V2 tooling audit

D17's active source text path must remain agent-only.

Audit `tools/source_factory/`.

Historical reader/benchmark code may be retained only if it is clearly non-production historical evidence and cannot be mistaken for an active workflow.

However D9 says one source architecture ships.

Therefore distinguish:

- historical benchmark artifacts/docs that should remain
- executable reader tooling that would effectively ship a second source architecture

If `tools/source_factory/readers/` is still executable as a source reader and D17 says not to run it, strongly prefer removing or quarantining the executable reader implementation while preserving benchmark evidence/results needed for history.

Do not remove canonical crop creation merely because a command lives under a directory named `readers`; port/rename the deterministic crop function if necessary.

The final operational commands/docs must not tell users to run a reader module.

---

# 15. Update stale operational documentation

Current STATE contains stale “exact next step” text saying `sankhya-rata` must be rebuilt even though `c453f16` already completed it.

Fix STATE.

After D9 completion, current docs must say:

- sankhya-rata independently rebuilt and `usable:true`
- pages 156/186 still green
- exactly one source architecture remains
- no source OCR/model-reader runtime ships
- no V1 source route/worker/config remains
- next step is corpus migration / product scoping decision, not sankhya rebuild

Historical D14/D15 decisions stay as history because D17 supersedes them.

Do not rewrite history.

---

# 16. Runtime regression — mandatory

This task is not complete from grep/tests alone.

Run the full application.

Use Chrome DevTools MCP.

Verify real Studio:

## page 186
- loads
- r002 remains `visual_only`
- r002 remains verified with zero source text
- r003 remains `visual_with_text`
- bbox overlays align
- review UI still works
- Network clean
- Console clean

## page 156
- loads
- legitimate text regions remain verified
- p156-r002 remains verified text_only
- history intact
- overlays/navigation intact

## sankhya-rata
- all three pages load
- final 15 verified / 2 excluded state survives
- gate remains `usable:true`
- source-kind decisions survive
- visual-only content survives
- no regression from deleting V1

Do not mutate verified source just to generate test traffic.

Use safe read/reload actions and reversible review actions only where needed.

---

# 17. Cold-start / worker regression

Perform a true cold rebuild/start after deletion.

Because a previous defect showed stale migration/container images can hide problems:

- rebuild API
- rebuild migrate
- rebuild worker
- start from cold
- inspect migration success
- inspect worker actor list/registration
- inspect API startup
- inspect logs

Prove:

- no import error from deleted modules
- no old queue/actor registered
- no old route registered
- no old settings validation path required
- no Tesseract binary/package dependency needed by startup
- no source model provider initialized

---

# 18. Repository-wide forbidden-active-path audit

Search the repository after cutover for:

- `tesseract`
- `OCRPort`
- `source_reading_openai`
- `source_reading_qwen`
- `source_consensus`
- `page_reading`
- old understanding runtime/provider/jobs
- source-reader env keys
- old source-reader routes
- old queue names

For every remaining match classify it.

Allowed remaining matches should be limited to things such as:

- historical migration text
- historical benchmark evidence
- locked decision history explaining removal

There must be no active import, route, worker, setting or executable source-reader path.

Do not declare success from “0 reader rows”.

Prove reachability is gone.

---

# 19. Preserve unrelated OpenAI and AI functionality

After deletion explicitly regression-test:

- question generation
- retrieval embeddings
- semantic verifier if configured
- teacher-paper generation boundaries

The goal is not “no OpenAI string anywhere”.

The goal is:

```
NO OpenAI/Qwen/Tesseract/OCR/model-based SOURCE READER
```

while legitimate downstream AI remains available behind verified-source gates.

---

# 20. Broad test matrix

Run the broadest practical suites.

At minimum:

## backend
- full Source V2 suite
- source upload tests that remain
- Source V2 API/repository/Postgres
- migration tests
- verified-source gates
- generation/retrieval smoke tests affected by config cleanup
- worker/startup tests
- OpenAPI surface regression

## frontend
- TypeScript
- Studio Playwright
- D18 UX tests
- production build if practical

## static
- ruff
- configured type checks

Do not report PASS if you only ran the old 85 tests.

The test count will legitimately change because many V1 tests should be deleted.

Report exact removed test families and exact new/remaining suite counts.

---

# 21. Commit strategy

Work on `master`.

Keep unrelated stash/local work untouched.

Use stable commits, for example:

1. remove active V1 wiring/routes/workers
2. remove legacy source modules/config/dependencies/tests
3. port narrow deterministic survivors + migration/history cleanup
4. runtime/cold-start regression fixes
5. docs/STATE final evidence

Push each stable checkpoint.

Do not leave half-deleted imports or a broken worker in the repository.

---

# 22. No false PASS

D9 is NOT complete if any of these remain:

- Tesseract source adapter reachable from runtime
- old page-reading actor registered
- old source-understanding actor registered
- Qwen/OpenAI source reader reachable
- source consensus reachable
- upload completion dispatches old reading
- old source routes remain in OpenAPI
- obsolete source-reader settings remain capable of activation
- Docker still installs source-OCR packages unnecessarily
- active tests still depend on deleted V1 architecture
- current Source V2 data/gates regress
- worker/API cold start fails
- Studio runtime not MCP-validated
- downstream legitimate AI was accidentally deleted

---

# 23. Final evidence report

Return concise evidence.

## COMMITS
hash + purpose

## REMOVED ACTIVE ARCHITECTURE
- modules
- routes
- workers/actors
- settings/env
- dependencies/container packages
- tests

## PRESERVED
- current Source V2
- upload/storage pieces retained
- deterministic primitives ported
- downstream OpenAI/generation/embedding functionality retained
- historical migrations/evidence retained

## CALL-GRAPH PROOF
- old source reader no longer reachable from main/router/worker/upload

## OPENAPI
- removed route groups
- Source V2 routes still present

## WORKER
- final actor list relevant to source pipeline
- prove no extraction/read/understanding V1 actors

## RUNTIME MCP
- page 156
- page 186
- all sankhya pages
- Console
- Network
- gate results

## TESTS
- exact suite counts
- removed legacy test families
- new D9 guard tests
- ruff/type/build

## COLD START
- migration
- API
- worker
- no stale/deleted import failures

## REPOSITORY AUDIT
- remaining Tesseract/Qwen/OpenAI-source-reader matches and why each is historical/non-active

## BLOCKERS
only genuine unresolved blockers

---

# Final completion standard

After this task, the repository ships exactly one source architecture.

The statement must be literally true:

> Source text can only enter Verified Source Content through D17 direct agent reading of canonical crops followed by D18 human verification.

No Tesseract fallback.
No Qwen source reader.
No OpenAI source reader.
No source consensus.
No legacy page-reading worker.
No obsolete source-understanding worker.
No config switch that turns them back on.
No hidden upload path that dispatches them.

Historical migrations and benchmark evidence may remain.

Downstream generation/embedding/semantic AI may remain, but only after verified-source gates.

Execute the cutover completely, then prove it with call graph, OpenAPI, worker startup, cold start, tests, database state, and Chrome DevTools MCP.
