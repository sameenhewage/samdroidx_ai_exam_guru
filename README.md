# AI Exam Guru

AI-assisted Sri Lankan examination practice platform. V1 validates the **Grade 5 Scholarship examination** first while the reusable curriculum/content architecture is designed for Grades 1–13.

## System architecture

The authoritative whole-system architecture is:

- [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md)

The product is deliberately split into:

1. **Exam Guru Studio — private/local content + AI factory** — raw educational materials, OCR/extraction, PostgreSQL + pgvector, RAG, generation, validation and teacher review stay on the operator-owned local/private environment by default.
2. **Exam Guru Student — hosted/public student platform** — receives only human-approved, validated, immutable/versioned student-ready content and stores student runtime data.

Approved papers are **published/copied**, not moved, so the local Studio remains the source-of-truth. Bulk raw materials and the private RAG corpus are not required on the hosted student server.

## V1 priority order

1. **Priority 1 — Admin + Content Intelligence + RAG + LLM** — must reach 100% acceptance before Priority 2 starts.
2. **Priority 2 — Student paper experience + marking + progress analytics**.

## Development model

This repository uses **continuous loop engineering + mandatory TDD/eval-driven development**, not one implementation prompt per phase.

Read:

- [`AGENTS.md`](AGENTS.md)
- [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md)
- [`docs/v1/00_V1_MASTER_PLAN.md`](docs/v1/00_V1_MASTER_PLAN.md)
- [`docs/v1/01_ENGINEERING_WORKFLOW.md`](docs/v1/01_ENGINEERING_WORKFLOW.md)
- [`docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`](docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md)
- [`docs/v1/03_PRIORITY_2_STUDENT_SPEC.md`](docs/v1/03_PRIORITY_2_STUDENT_SPEC.md)
- [`docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md`](docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md)
- [`docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`](docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md)
- [`docs/v1/PHASE_TRACKER.md`](docs/v1/PHASE_TRACKER.md)

## Automatic repository skills

Reusable agent workflows live in `.agents/skills/<skill-name>/SKILL.md`.

`AGENTS.md` contains the authoritative skill registry and automatic trigger rules. Every engineering task always loads:

- `loop-engineering`
- `tdd-eval-engineering`

The agent must then automatically load all matching domain skills, including teacher-content UX, FastAPI, Next.js, OCR/ingestion, RAG, forecast/backtesting, LLM generation/validation, security/acceptance, and later the P10-gated student product skill, without waiting for the operator to name them.

See [`docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md`](docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md) for compositions and maintenance rules.

## GPT-5.6 Sol operator prompts

Start/resume the general V1 loop with:

- [`prompts/v1/00_GPT_5_6_SOL_MASTER_EXECUTION.md`](prompts/v1/00_GPT_5_6_SOL_MASTER_EXECUTION.md)

Resume an interrupted engineering session with:

- [`prompts/v1/01_CONTINUE_ENGINEERING_LOOP.md`](prompts/v1/01_CONTINUE_ENGINEERING_LOOP.md)

Run adversarial review/fix loops with:

- [`prompts/v1/02_ADVERSARIAL_REVIEW_FIX_LOOP.md`](prompts/v1/02_ADVERSARIAL_REVIEW_FIX_LOOP.md)

Before unlocking student development run:

- [`prompts/v1/03_PRIORITY_1_ACCEPTANCE_AUDIT.md`](prompts/v1/03_PRIORITY_1_ACCEPTANCE_AUDIT.md)

Only after P10 is proven DONE, continue with:

- [`prompts/v1/04_PRIORITY_2_UNLOCK_AND_CONTINUE.md`](prompts/v1/04_PRIORITY_2_UNLOCK_AND_CONTINUE.md)

Use the local Grade 5 dataset steering prompt when working with operator-provided material:

- [`prompts/v1/05_FULL_V1_CONTINUOUS_EXECUTION_WITH_LOCAL_DATA.md`](prompts/v1/05_FULL_V1_CONTINUOUS_EXECUTION_WITH_LOCAL_DATA.md)

Use the teacher-first multi-grade product correction prompt for the current UI/domain redesign:

- [`prompts/v1/06_TEACHER_FIRST_MULTI_GRADE_REDESIGN.md`](prompts/v1/06_TEACHER_FIRST_MULTI_GRADE_REDESIGN.md)

All prompts re-apply the repository skill-routing rules so skill use survives session/resume boundaries.

## Current state

Implementation is active across Priority 1 acceptance gates. P0 and P1 are DONE; P2 and P3 have substantial implementation evidence but remain incomplete on representative human-reviewed real-data quality gates. Later non-blocked Priority 1 engineering may continue because tracker phases are acceptance gates, not waterfall implementation locks. See [`docs/v1/PHASE_TRACKER.md`](docs/v1/PHASE_TRACKER.md) for the authoritative current status and evidence.

## Local Studio bootstrap

### Prerequisites

- Docker Engine 29+ with Docker Compose 2.40+
- `uv` 0.11.26+ for host-side backend development
- Node.js 24.19 with npm 11.17 for host-side frontend development

The local Studio is intended to run through versioned Docker/Docker Compose while persistent source data lives on durable host storage rather than ephemeral container layers.

The storage-provider abstraction follows [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md): **local durable host filesystem storage is the bootstrap/default Studio backend**, while S3/MinIO is an optional profile/provider.

### Windows: one-file startup

Open Docker Desktop with Linux containers, then double-click **`start-studio.cmd`** in the repository root. It builds the current code and starts all core services through the existing `compose.yaml`, shown as one **ai-exam-guru** group under Docker Desktop **Containers**. Do not run the individual images separately. With the default ports, open `http://localhost:3000` after startup succeeds.

The launcher keeps the existing env file, credentials and storage path, and applies the documented local legacy OCR profile for that process: 256 MiB upload/OCR bytes, 40 pages and five seconds per command. Use Compose directly for deliberately customized limits. Existing database volumes are not reset; if the Desktop PostgreSQL volume is absent, the launcher requires an explicit **Y** before creating a new empty database. PDFs are not automatically imported or restored. Back up important data before upgrades, because startup applies pending forward migrations.

Optional PowerShell commands:

```powershell
.\start-studio.cmd --check
.\start-studio.cmd --build-only
```

`--check` validates configuration without starting services; `--build-only` builds images without starting them. The default double-click action builds **and** starts the system. Afterwards, use the **ai-exam-guru** group in Docker Desktop to stop or start its existing containers; run the launcher again after source updates to rebuild them.

For other local shells, typical startup is:

```bash
test -f .env || cp .env.example .env
docker compose up --build --wait
```

Core services include:

- teacher/admin web;
- FastAPI API;
- PostgreSQL + pgvector;
- Valkey/background workers;
- durable local source storage bind-mounted from `EXAM_GURU_DATA_PATH` (default `./.exam-guru-data`) to `/data` in the API, worker, and maintenance containers.

The container startup makes the storage root private and runs backend processes as the bind-directory owner when possible. Use a local POSIX-compatible filesystem that supports hard links, atomic replacement, directory `fsync`, and Unix permissions; pre-create/chown a custom host path if Docker cannot manage its ownership. Do not use an ephemeral container path as `EXAM_GURU_DATA_PATH`. Local reconciliation metadata is kept in private bounded sidecars that preserve the reserved operator-tag map, but local storage has no external provider-console tagging interface.

MinIO remains available only when explicitly selected and configured, for example with `EXAM_GURU_STORAGE_BACKEND=s3 docker compose --profile s3 up --build --wait`. Existing objects in the retained `minio-data` volume are **not** silently copied into local storage: migration requires an explicit checksum-preserving export/import process before switching the database's storage backend. Never remove the MinIO volume until that migration and its backup have been verified.

Local/test deployments keep deterministic embeddings and generation when no provider is selected. Real `text-embedding-3-small`, generation and grounded semantic-verifier providers can be enabled explicitly with the `EXAM_GURU_RETRIEVAL_EMBEDDING_*`, `EXAM_GURU_GENERATION_*` and `EXAM_GURU_SEMANTIC_VERIFIER_*` settings in `.env.example`; keep API keys in an ignored local env file or external secret store. Compose forwards those secrets only to the API and worker, never migration or maintenance containers. Live calls use no hidden SDK retries, retain exact model/config/pricing identity, record bounded token/cost/latency evidence and remain subject to deterministic validation plus human review. Normal CI never invokes a paid provider.

Stop services without intentionally deleting durable data using:

```bash
docker compose down
```

## Real local corpus intake

Files under `RAG DATA` are not automatically imported, moved, or committed. The operator import tool `scripts/import_studio_corpus.py` consumes a checksum-audited manifest with a `pdfs` array. Each record carries `relative_path`, `storage_grade`, `size_bytes`, `sha256`, and separately evidenced `candidate_metadata`; it must not treat folder labels as approved curriculum metadata. Keep the manifest and import ledger outside Git.

```bash
uv run --project apps/api python scripts/import_studio_corpus.py \
  --root "RAG DATA" --manifest /path/to/audited-manifest.json \
  --ledger /path/to/private-import-ledger.json
```

Run this operator tool in a POSIX/Linux environment with the actual corpus root and a separate private writable ledger directory; the example paths must be replaced with verified mounts, not historical WSL locations. It defaults to a read-only checksum/metadata preflight. To import, supply `--execute --base-url http://localhost:8000 --token-env EXAM_GURU_IMPORT_TOKEN` with an authorized token in that environment variable. The importer uses the supported resumable upload API, with at most 4 MiB per part, complete receipt-prefix verification and bounded recovery of ambiguous responses. Its locked, atomically saved v2 ledger binds owner, local origin/runtime report, source root and checksum/request identities. Missing saved sessions pause rather than silently recreate; migrating a v1 ledger requires explicit `--migrate-legacy-ledger` and preserves its history. Finalization queues the existing page-reader outbox. It never trusts, embeds or publishes sources, and upload completion is not reading completion. Mount corpus originals read-only; no direct database insertion is used.

### Upload and reading contracts

The legacy byte API and the current Materials workflow have **separate limits**; increasing a legacy upload setting is not the large-file solution.

**Uploading a PDF does not require an approved curriculum to exist first.** When the chosen grade has no admitted catalogue, Materials offers **Upload for metadata review**: choose Sinhala, Tamil, English, Mixed / other, or Not sure; enter the subject and curriculum/edition only if known. Past-paper years can also remain unknown in this path. Where listed curriculum choices exist but do not fit the PDF, use **Enter details for review instead**. These labels are unverified intake descriptions, not new language/subject/curriculum records. Unknown values stay empty, curriculum/unit/lesson IDs stay unassigned, and the uploaded source requires metadata and text review before AI use. The normal approved-curriculum assignment path remains available; uploads do not grant catalogue admission or source trust. After a successful upload, the library opens the material's grade with old list filters cleared; saved-upload recovery also resolves the completed material's current grade.

| Path                                                                  | Current contract                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Legacy `POST /api/v1/admin/source-documents` and `/extract`           | Retained for legacy callers, not the large-file corpus importer. Byte uploads default to 25 MiB in backend settings, with a 256 MiB maximum; Compose defaults to 256 MiB; the legacy browser multipart proxy remains bounded to 26 MiB including form overhead. Legacy Compose OCR allows 40 routed pages and five seconds per command under its actor budget. `EXAM_GURU_OCR_*` settings, including an empty provider to disable legacy OCR, apply to this path.                                                                                  |
| Materials resumable multipart upload (`/api/v1/admin/source-uploads`) | Uses 4 MiB raw octet-stream parts (shorter final part), not a whole-PDF browser buffer. An owner-scoped `request_id` recovers a lost create response; replay must match the original metadata. Resuming with a reselected file verifies the complete saved prefix against paginated SHA-256 chunk receipts before appending. Server finalization rechecks receipts, total size and content hash, deduplicates the immutable original and queues reading.                                                                                           |
| `SourceReadJob` (`/api/v1/admin/source-documents/{id}/read`)          | File-backed native/OCR reading commits per-page candidates and progress in batches of at most eight pages, then resumes through durable jobs/lease recovery. There is no 1,000-page document cap or 40-page OCR cap on this path. Each job snapshots `PageReadingConfiguration`; its current default is 30 seconds per Tesseract command, 300 dpi, 40 million pixels and 8 MiB command output, within a 300-second execution budget (360-second actor limit, 600-second lease). These are resource budgets, not accuracy or completion guarantees. |

Resumable uploads currently require local POSIX storage; S3/MinIO streaming/resumable support fails explicitly rather than falling back to whole-file reads. Defaults reserve **8 GiB per owner, 32 GiB globally and eight active sessions per owner** through `EXAM_GURU_SOURCE_UPLOAD_MAX_OWNER_STAGED_BYTES`, `EXAM_GURU_SOURCE_UPLOAD_MAX_STAGED_BYTES` and `EXAM_GURU_SOURCE_UPLOAD_MAX_ACTIVE_SESSIONS_PER_OWNER`. These are **operational staging quotas**, not PDF-format/product limits. Retained completed/failed sessions still consume the byte reservation; clearing a browser recovery link does not free server staging. There is no automatic staging purge. Capacity, integer, raster, text and runtime bounds still apply.

The controlled model-comparison benchmark uses a **60-second command budget**, separate from both the legacy five-second Compose profile and the page-reader's 30-second default. Do not infer runtime defaults or Sinhala accuracy from that benchmark.

### Review, provenance and readiness

Imported candidate metadata is labeled **Metadata needs review**; unresolved grades appear under **Unassigned materials**. Unassigned materials can use **Edit metadata → Change detected details** to save corrected descriptions even when the admitted catalogue is empty. Sinhala descriptions expose Sinhala editing labels. These are separate, append-only, checksum/scope/version-bound candidates with reasons and audit history; original intake metadata is not overwritten. The current proposal may change unverified display/filter hints, but saving it never clears metadata review or verifies page text. Draft versions remain fixed until explicitly reopened, and conflicts retain unsaved edits.

Explicit metadata confirmation still requires current, evidence-backed catalogue admission and the exact current candidate revision when one exists. It adopts the reviewed year/material category with old/new audit evidence. Catalogue/metadata admission is independent of page-text confirmation and benchmark ground truth; neither grants the other.

Materials uses `SOURCE_READ`-authorized original-PDF streaming (GET/HEAD and a validated single byte Range) and page-image comparison. Workers retain native-comparison and exact OCR-input images under durable `STORAGE_ROOT/fidelity-page-images`, with schema-versioned source/page/rasterizer/hash/size provenance. Reads verify the artifact; a declared image that is lost or corrupt fails closed, not as a cache miss. Bounded original rendering is only a fallback when no durable artifact was declared. Originals, upload staging and image artifacts must survive rebuilds and be covered by backup inventory.

Per-page candidates preserve raw UTF-8 evidence, a separate NFC text view, engine/configuration versions and append-only review history. **No NFKC source rewrite, automatic legacy-font conversion, LLM source authorship or bulk trust is permitted.** Corrections create unverified child candidates; confirmation requires explicit comparison with the original and the exact current candidate/version. Rereads preserve prior candidates and human edits. New knowledge/history, embeddings, retrieval contexts, generation, validation, review approval and publication require current verified page lineage, an exact nonblank NFC source span, admitted metadata/scope and the applicable record/taxonomy review gates. Old published versions remain immutable history; a legacy `trusted` flag does not authorize new work.

**Review selected source pages → Add evaluation reference** is a separate route for collecting human comparison text when a reading is still blocked. It starts blank or from the prior human reference, verifies the original image, requires explicit comparison and a reason, and keeps drafts on version conflicts. References are append-only, audited and evaluation-only; blank pages require an explicit declaration. Separate progress counts never imply source confirmation, cleared Maths/table failures, admitted metadata, Ready for AI or measured OCR accuracy. Migration `0042_evaluation_references` preserves the existing source-confirmed ground-truth workflow and all downstream trust gates.

At the verified 12 September Desktop checkpoint, release `59ad760` and schema 0042 preserve **three originals / 376 pages**, with **370 failed readings / six needing review** and zero trusted pages, human references, chunks or embeddings. The [current nine-page reference-only review set](http://localhost:3000/admin/materials/benchmark-review?benchmark_id=9fd3f582-44c2-4f96-a8ed-55d3931e9f50) is ready for genuine human transcription. Its nine original-image previews do not count as human references or accuracy evidence.

The historical 6 September 2026 checkpoint belongs to a separate runtime: it had **all 587 latest whole-document `SourceReadJob`s completed** and all **5,234 current page-review states `needs_review`**: zero failed, verified or excluded pages and zero ground truth. The 116 image-failure whole-document jobs were retried through the API after renderer diagnostic/slot-contention fixes, without larger budgets; old failed attempts remain history. Normal Materials at that checkpoint contained 587 real originals, active but metadata-required, untrusted and unindexed. The 10 September direct inspection of the current Windows Docker Desktop Studio instead found one real four-page source, 24 candidate versions and zero verified pages, ground truth, chunks, embeddings or admitted curricula. A fresh raw-corpus inventory still found 711 files / 696 PDF paths / 587 distinct PDFs / 5,234 unique pages, with all originals unchanged. Raw inventory/footer counts are not imported database counts or educational authority.

Forward migration to `0038_upload_request_identity` and audited quarantine of 72 exactly proven E2E sources preserve the Studio's 659 source rows and downstream history. Final original-corpus proof retains all 711 files/696 PDF paths with unchanged SHA-256, size, nanosecond mtime/ctime, inode, device and mode; private evidence is `.exam-guru-evidence/studio-rollout/final-original-corpus-proof/original-integrity.json`. This is processing/integrity evidence, not Sinhala accuracy or final release acceptance. Generate Papers now derives its choices from current admitted scope and requires a source-scope fingerprint; the live empty-admission state shows no fixture choices. Exact final verification and CI outcomes are recorded in the phase tracker. See [known limitations](docs/v1/06_KNOWN_LIMITATIONS.md) and the [NOT READY teacher-pilot verdict](docs/v1/07_GRADE5_TEACHER_PILOT_READINESS.md).

The current source-confirmation rules require `source-fidelity-v2/rules-2/` after migration 0040. Older diagnostics cannot authorize new content use merely because they once reported `can_confirm=true`; historical evidence remains intact. Short suspicious legacy carriers are blocked without treating tested valid mixed-language terms as corruption. These heuristics and a more usable bounded review workspace are not OCR-accuracy guarantees or automatic source approval.

Materials now handles acknowledged-upload backpressure without asking a teacher to repeatedly press Continue: it honors bounded `Retry-After` hints, shows a waiting state and supports immediate Pause. Retries keep the same session, part bytes and expected version; missing/invalid/excessive hints and unacknowledged creation still require explicit recovery. Server rate limits and storage quotas are not raised. This does not imply the real large-document ingestion/reading gate is complete.

Actual local Studio/source acceptance is primary. GitHub is secondary source control and regression/security infrastructure, not a runtime dependency. Fix current-change regressions and genuine security/data-integrity failures; classify and document unrelated hosted-runner failures without weakening checks or stopping otherwise safe local product work.

The UI self-hosts Noto Sans Sinhala/Tamil via Fontsource packages `5.3.0`; their licenses ship in `apps/web/public/licenses/`. This identifies the installed packages, not an independently verified official Noto `v3.000` binary. Display fonts do not repair source encoding.

## Backend development

```bash
uv sync --project apps/api --frozen
uv run --project apps/api ruff check apps/api
uv run --project apps/api ruff format --check apps/api
uv run --directory apps/api mypy
uv run --directory apps/api pytest tests -m 'not backup_restore' --cov=exam_guru_api --cov-report=term-missing
uv run --directory apps/api pytest tests/integration/test_backup_restore_postgres.py -m backup_restore
```

The integration suite uses real disposable PostgreSQL/pgvector and Valkey infrastructure and exercises the configured storage provider/integration adapters as required by the active implementation. The backup/restore test restores synthetic data into a disposable database, never the persistent Studio.

Source-fidelity regression anchors include [resumable upload/recovery](apps/api/tests/integration/test_resumable_uploads_postgres.py), [page-reading jobs](apps/api/tests/integration/test_page_reading_postgres.py), [image integrity/rendering](apps/api/tests/test_page_images.py), [catalogue admission](apps/api/tests/integration/test_catalogue_admission_postgres.py) and [verified downstream lineage](apps/api/tests/integration/test_verified_knowledge_lineage_postgres.py). These prove contracts, not Sinhala educational accuracy. Record current gate results and explicit skips in the phase tracker; do not reuse an earlier run's counts after changes.

## Frontend development

Select Node 24.19.0 first (the installed workstation path is below; see `AGENTS.md` for browser-cache requirements).

```bash
export PATH="/home/sameen/.nvm/versions/node/v24.19.0/bin:$PATH"
npm ci
npm run lint --prefix apps/web
npm run typecheck
npm run test:coverage --prefix apps/web
npm run build --prefix apps/web
npm run test:e2e:isolated
```

Browser acceptance always starts a throwaway Compose project on non-Studio ports, uses disposable database, Valkey, and source-storage state, and removes that state after the run. Direct `npm run test:e2e --prefix apps/web` invocation fails closed so acceptance fixtures cannot be written into the long-lived local Studio.

If the pinned host runtime is unavailable, use the Docker runtime instead of relaxing the engine requirement. For interactive runtime inspection use Chrome DevTools MCP, not an IDE/browser preview. Upload and review browser regressions are in `apps/web/e2e/material-upload.spec.ts` and `apps/web/e2e/source-page-review.spec.ts`; never run fixture writes against the long-lived Studio.

## Operational verification

```bash
docker compose config --quiet
REQUIRE_SHELLCHECK=1 uv run --no-project --with shellcheck-py==0.11.0.1 -- bash scripts/ops/check_backup_restore.sh
```

The second command supplies ShellCheck when it is absent on the host; a syntax-only fallback is not the full static gate. PostgreSQL backup/restore scripts cover the database, not original PDFs, retained `.source-uploads` staging or `fidelity-page-images`. Coordinate their copy with the database and restore only into a new empty isolated database/filesystem with correct private ownership, following the [local Studio recovery runbook](docs/ops/BACKUP_RESTORE.md) and [architecture backup contract](docs/SYSTEM_ARCHITECTURE.md#16-backup-and-disaster-recovery). Use known local configuration through a secure helper and protected temporary `PGPASSFILE`, never credential discovery, password arguments or secret logging. Local backup verification and the disposable restore test do not prove off-host recovery or authorize an in-place restore.

## Generated API client

The FastAPI OpenAPI document and TypeScript client types are reproducible repository artifacts:

```bash
npm run generate:client
npm run typecheck --prefix packages/api-client
```

CI regenerates both artifacts and fails if the committed output differs.

## Configuration and observability

Backend settings use the `EXAM_GURU_` environment prefix. Secrets must remain outside Git and be redacted from logs/evidence. Local Studio production-hardening rules must respect the private/local network and durable-host-storage boundaries in `docs/SYSTEM_ARCHITECTURE.md`.

Optional observability variables include:

- `EXAM_GURU_SENTRY_DSN`
- `EXAM_GURU_OTEL_EXPORTER_OTLP_ENDPOINT`
- `EXAM_GURU_OTEL_SERVICE_NAME`
- `EXAM_GURU_TRACE_SAMPLE_RATIO`

Every API response carries a validated `X-Request-ID`. OpenTelemetry FastAPI instrumentation is active, and Sentry-compatible error reporting is enabled only when a DSN is configured.

P1 browser acceptance uses deterministic admin/reviewer tokens only when `ENABLE_DETERMINISTIC_IDENTITY=true`; backend production settings reject those tokens. The development cookie is HttpOnly and SameSite=Strict. Production identity/session hardening remains a later acceptance concern and must not weaken the private Studio boundary or the hosted Student platform's public-security requirements.
