# D9 — Execute the legacy source-reading cutover from the proven map

## Read first

This is an EXECUTION prompt, not another discovery prompt.

Authoritative inputs:

- `docs/source-v2/D9_CUTOVER_MAP.md` — commit `1e495ab46d51bb0460c9079751a615f44a4e9c00`
- `prompts/source-v2/06_D9_LEGACY_SOURCE_CUTOVER.md`
- current D17/D18 Source V2 implementation and acceptance state

The call graph has already been mapped. Do not spend the session re-doing the map unless actual code has materially changed since the map was produced.

Goal: remove the old V1/OCR/source-reader architecture completely while leaving exactly one active source architecture: Source V2 D17 + D18.

Work on `master`. Keep the repository buildable at every checkpoint. Commit/push the final verified result.

## Locked architecture

Active Source V2 remains:

```
Immutable source
→ deterministic 300-DPI render
→ deterministic layout
→ canonical crop for EVERY region
→ executing agent directly visually reads the exact canonical crop
→ primary transcript JSON
→ schema/checksum/bbox seal
→ deterministic validators
→ Machine Candidate = primary text
→ human review
→ Verified Source Content
→ only then knowledge / embeddings / RAG / generation
```

No OCR exists in the active Source V2 reading path.

D18 source kinds remain unchanged:

- TEXT_ONLY
- VISUAL_ONLY
- VISUAL_WITH_TEXT
- DECORATIVE
- UNDECIDED

Do not redesign D17/D18.
Do not begin vectorization.
Do not begin AI image generation.
Do not begin broad corpus migration.
Never translate/normalize/correct original source evidence.

## Proven map findings — treat as facts to execute against

The map proved two ordering hazards.

### Hazard 1 — deterministic PDF primitives live in a legacy file

`page_images.py` imports:

- `RenderedPageImage`
- `open_pdf_file`

from `tesseract_ocr.py`.

Those two are deterministic PDF rendering/input primitives, not OCR behavior. Source V2 Studio depends on page-image serving. They MUST survive in a neutral module, preferably:

`exam_guru_api.documents.pdf_render`

Do not delete `tesseract_ocr.py` until `page_images.py` no longer imports it.

Preserve existing security/validation behavior of the PDF helper:
- regular-file descriptor requirement
- PDF signature check
- optional SHA-256 check
- source mutation detection
- encrypted/malformed rejection
- guaranteed descriptor/document cleanup

Use neutral error/type names in the new module where appropriate; do not leave Source V2 depending on Tesseract-named errors.

### Hazard 2 — upload finalization currently starts V1 reading

The map proved:

- `resumable_uploads.py` imports `queue_source_read`
- `upload_jobs.py` imports source-read dispatcher symbols
- successful new upload completion creates/dispatches a V1 source-read job
- `finalize_source_upload` itself must remain, but it must stop queueing/dispatching V1 reading

Cut this path BEFORE unregistering workers or deleting source-read modules.

A completed upload after D9 must persist the immutable source and return normal upload state, but must NOT create or dispatch a V1 source-read job.

Remove obsolete upload response fields/contracts that exist only to expose the V1 read job, including `source_read_job_id`, once all consumers/tests are migrated.

## Legacy worker actors — remove exactly these

Unregister and remove:

- `extract_document`
- `recover_extraction_jobs`
- `read_source`
- `recover_source_read_jobs`
- `understand_source_page`
- `recover_understanding_page_jobs`

Keep:

- `finalize_source_upload`
- `recover_source_upload_jobs`
- `generate_question`
- `recover_generation_jobs`
- `ingest_embeddings`
- `recover_embedding_jobs`
- `prepare_knowledge_page`
- `recover_material_knowledge`
- `recover_material_knowledge_indexing`
- `reconcile_source_objects`
- `advance_teacher_paper`
- `recover_teacher_papers`

Do not remove downstream OpenAI usage for generation, embeddings, or semantic verification. D9 removes OpenAI SOURCE READING / SOURCE UNDERSTANDING runtime only.

## Historical migrations are immutable

Never delete or rewrite historical migrations, including OCR/extraction/understanding/source-reading migrations.

Old tables/columns may remain as historical schema evidence unless a forward migration is genuinely required for correctness. D9 is primarily an active-runtime cutover, not destructive historical-schema rewriting.

## Execution order — keep green after every checkpoint

### Checkpoint 1 — cut upload → V1 read dispatch

Modify `resumable_uploads.py` and `upload_jobs.py` so upload completion no longer:
- imports `page_reading_jobs`
- creates a V1 source-read job
- queries a V1 source-read job
- dispatches a V1 source-read actor
- returns `source_read_job_id`

Update upload schemas, API clients, frontend upload code, tests and E2E expectations accordingly.

Important:
- resumable upload persistence/recovery remains intact
- `finalize_source_upload` and `recover_source_upload_jobs` remain intact
- no dangling `.send()` / dispatcher call may remain

Run focused backend + frontend upload tests before continuing.

### Checkpoint 2 — extract neutral PDF rendering primitives

Create `documents/pdf_render.py`.

Move/port the deterministic PDF primitives required by `page_images.py`, including:
- `RenderedPageImage`
- `open_pdf_file`
- the minimal neutral validation/error types required by that helper

Repoint `page_images.py` to `pdf_render.py`.

Do not change page-image metadata, rasterizer identity, checksum behavior, limits, bbox/crop behavior, or Studio image-serving contracts.

Run focused page-image / Source V2 tests before continuing.

### Checkpoint 3 — remove startup and worker wiring

Update `main.py`, `worker.py`, and `api/dependencies.py`.

Remove:
- `ExtractionDispatcher` app-state wiring used only by legacy extraction
- `SourceReadDispatcher` app-state wiring
- legacy understanding dispatcher/runtime wiring
- the six legacy worker actors listed above

Preserve unrelated application resources and actors.

Import the API / build the worker immediately after this checkpoint to prove no startup import is broken.

### Checkpoint 4 — remove legacy routes

Remove V1/source-reading/source-understanding route registration from `api/router.py`.

At minimum remove route trees that only exist for:
- source fidelity V1 reading/re-read flow
- V1 document-understanding/source-understanding job flow
- old extraction review/source-reader workflow

Keep Source V2 routes and page-image routes.

Do not remove a route merely because it contains the word “source”; classify it by architecture.

After this checkpoint inspect generated OpenAPI and prove the legacy endpoints are absent while Source V2 + upload + page-image endpoints remain.

### Checkpoint 5 — delete legacy implementation leaves first

Using `docs/source-v2/D9_CUTOVER_MAP.md` as the dependency order, delete legacy-only implementation modules after all active imports have been cut.

Expected legacy deletion set includes, where still proven legacy-only:

- `documents/page_reading_jobs.py`
- `documents/page_reading.py`
- `documents/ocr.py`
- `documents/tesseract_ocr.py`
- `documents/extraction_service.py`
- `documents/jobs.py`
- `documents/understanding_jobs.py`
- old `page_understanding*` modules if present
- `documents/understanding_openai.py`
- old understanding provider/runtime/service modules that only power the removed reader path
- `documents/source_reading.py`
- `documents/source_reading_journal.py`
- `documents/source_reading_openai.py`
- `documents/source_reading_qwen.py`
- `documents/source_consensus.py`
- `documents/source_consensus_provider.py`
- `documents/source_machine.py`
- `documents/source_machine_models.py`
- `documents/source_machine_service.py`
- `documents/source_renders.py` if it is legacy-reader-only

Do NOT delete a module if Source V2 or a surviving non-reader subsystem still imports it.
If a generic helper is still required, move only that generic helper into a neutral module first.

Do not delete:
- `source_v2/**`
- Source V2 models/repository/service/gate/source_kind
- page-image serving
- immutable upload/object storage
- source verification required by D17/D18
- deterministic layout/crop/alignment helpers required by V2
- historical migrations

Run import/type/lint/tests before continuing.

### Checkpoint 6 — remove legacy configuration/dependencies/container setup

Remove active settings/env/config keys used only by the deleted reader architecture, including the mapped families:

- `ocr_provider`
- `ocr_tesseract_*`
- `source_consensus_enabled`
- `source_qwen_*`
- `document_understanding_*` fields that exist only for source reading/understanding

Do NOT remove similarly named settings used by surviving generation, embeddings or semantic verification.

Remove:
- Tesseract installation from active API/worker images if no surviving runtime needs it
- legacy Qwen/Ollama source-reader config
- source-reader OpenAI secrets/model settings
- dead Python dependencies only when no surviving subsystem imports them
- stale compose/env examples
- stale reviewer/UI text describing a local reader/OCR path

Keep PyMuPDF if Source V2/page-image rendering still needs it.

### Checkpoint 7 — delete legacy tests and migrate surviving tests

Delete tests whose only purpose is the removed architecture.

Expected candidates include:
- Tesseract adapter/integration/file-input tests
- OCR port/evaluation/extraction pipeline tests
- page-reading tests
- source-reading OpenAI/Qwen tests
- source-consensus tests
- old source-fidelity V1 tests
- old understanding reader/job/provider/runtime tests
- old extraction worker/service tests if no longer reachable

Do not delete Source V2 tests.

Update surviving upload/page-image/runtime tests to assert the new architecture rather than weakening coverage.

Keep:
`apps/api/tests/source_v2/**`

## Required repo-wide classification after deletion

Search the full repository for:

- `tesseract`
- `ocr`
- `qwen`
- `ollama`
- `source_reading`
- `source-reading`
- `source_consensus`
- `page_reading`
- `source_read_job`
- `source_read_job_id`
- `understanding_openai`
- `document_understanding`
- removed actor names

For every remaining match classify it as:

1. historical migration
2. historical docs/benchmark/archive
3. unrelated surviving non-source-reader usage
4. Source V2 valid usage
5. obsolete active reference — MUST remove

No unexplained active-runtime match is allowed.

Historical docs such as `docs/source-v2/BENCHMARK_READERS.md` and `_archive/` may remain.

## Regression requirements

Run the broad backend/frontend suite and report ACTUAL results.

At minimum:
- backend tests
- Source V2 tests
- D18 real-database action tests
- frontend unit tests
- TypeScript checks
- Ruff
- builds
- migration sanity
- upload flow tests
- page-image tests

Do not hardcode the previous count of 85 tests; report whatever the post-cutover suite actually contains.

D18 behaviors must still prove:
- visual_only can verify with zero source characters
- abstained text cannot verify
- visual_with_text cannot verify empty
- verified visual requires canonical crop provenance
- decorative cannot become educational verified source
- unknown kind rejected
- bbox/crop_sha256 survive
- review history is append-only

## Runtime validation — mandatory before PASS

Use Chrome DevTools MCP throughout final runtime validation.

Cold rebuild/restart:
- rebuild `migrate` and `api` explicitly so the known stale migration-image problem cannot recur
- rebuild/restart worker after actor removal

Then prove:

1. Source Studio loads
2. page-image rendering still works from the new neutral PDF module
3. upload completion creates no V1 source-read job and dispatches no V1 actor
4. legacy endpoints are absent
5. six legacy actors are absent from worker registration
6. Source V2 works end-to-end
7. `sankhya-rata` remains `usable:true`
8. teacher-guide page 156 remains resolved
9. teacher-guide page 186 remains resolved
10. `p186-r002` remains verified `visual_only` with zero source characters
11. active OCR reader rows remain 0
12. Network is clean
13. Console is clean except previously accepted non-blocking advisories
14. cold restart succeeds a second time

Inspect DB/API evidence, not only UI labels.

## Stop conditions

Do not claim PASS if:
- upload completion can still dispatch V1 reading
- a removed actor/module still has an active import
- page-image serving broke
- Source V2 acceptance changed unexpectedly
- active OCR rows become nonzero
- legacy source-reader config remains active
- Tesseract/Qwen/OpenAI source reader remains reachable
- tests/type/lint/build fail
- Network/Console/runtime regression fails
- cold restart fails
- remaining legacy-keyword matches are unclassified

If a checkpoint fails, repair it before advancing. Do not leave master halfway through a later checkpoint.

## Final commit

After all acceptance checks pass, commit and push `master`.

Preferred final commit message:

`refactor(source-v2): complete D9 legacy source cutover`

## Final response

Return only:

- D9 status: PASS / BLOCKED
- execution prompt path
- final commit hash
- concise modules/routes/actors/config removed
- actual backend/frontend/lint/type/build results
- Chrome DevTools MCP runtime result
- Source V2 acceptance state
- any blocker if not PASS
