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

Typical local startup is:

```bash
cp .env.example .env
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

## Backend development

```bash
uv sync --project apps/api --frozen
uv run --project apps/api ruff check apps/api
uv run --project apps/api ruff format --check apps/api
uv run --directory apps/api mypy
uv run --project apps/api pytest apps/api/tests --cov=exam_guru_api --cov-report=term-missing
```

The integration suite uses real disposable PostgreSQL/pgvector and Valkey infrastructure and exercises the configured storage provider/integration adapters as required by the active implementation.

## Frontend development

```bash
npm ci
npm run lint --prefix apps/web
npm run typecheck
npm run test:coverage --prefix apps/web
npm run build --prefix apps/web
npm run test:e2e:isolated
```

Browser acceptance always starts a throwaway Compose project on non-Studio ports, uses disposable database, Valkey, and source-storage state, and removes that state after the run. Direct `npm run test:e2e --prefix apps/web` invocation fails closed so acceptance fixtures cannot be written into the long-lived local Studio.

If the host Node version is older than Node 24, use the Docker runtime instead of relaxing the pinned engine requirement.

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