# AI Exam Guru

AI-assisted Sri Lankan examination practice platform. V1 targets the **Grade 5 Scholarship examination** and is governed by repository-first engineering instructions.

## V1 priority order
1. **Priority 1 — Admin + Content Intelligence + RAG + LLM** — must reach 100% acceptance before Priority 2 starts.
2. **Priority 2 — Student paper experience + marking + progress analytics**.

## Development model
This repository uses **continuous loop engineering + mandatory TDD/eval-driven development**, not one implementation prompt per phase.

Read:
- [`AGENTS.md`](AGENTS.md)
- [`docs/v1/00_V1_MASTER_PLAN.md`](docs/v1/00_V1_MASTER_PLAN.md)
- [`docs/v1/01_ENGINEERING_WORKFLOW.md`](docs/v1/01_ENGINEERING_WORKFLOW.md)
- [`docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`](docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md)
- [`docs/v1/03_PRIORITY_2_STUDENT_SPEC.md`](docs/v1/03_PRIORITY_2_STUDENT_SPEC.md)
- [`docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md`](docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md)
- [`docs/v1/PHASE_TRACKER.md`](docs/v1/PHASE_TRACKER.md)

## Automatic repository skills
Reusable agent workflows live in `.agents/skills/<skill-name>/SKILL.md`.

`AGENTS.md` contains the authoritative skill registry and automatic trigger rules. Every engineering task always loads:
- `loop-engineering`
- `tdd-eval-engineering`

The agent must then automatically load all matching domain skills (FastAPI, Next.js, OCR/ingestion, RAG, forecast/backtesting, LLM generation/validation, security/acceptance, and later the P10-gated student product skill) without waiting for the operator to name them.

See [`docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md`](docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md) for compositions and maintenance rules.

## GPT-5.6 Sol operator prompts
Start V1 with:
- [`prompts/v1/00_GPT_5_6_SOL_MASTER_EXECUTION.md`](prompts/v1/00_GPT_5_6_SOL_MASTER_EXECUTION.md)

Resume an interrupted engineering session with:
- [`prompts/v1/01_CONTINUE_ENGINEERING_LOOP.md`](prompts/v1/01_CONTINUE_ENGINEERING_LOOP.md)

Run adversarial review/fix loops with:
- [`prompts/v1/02_ADVERSARIAL_REVIEW_FIX_LOOP.md`](prompts/v1/02_ADVERSARIAL_REVIEW_FIX_LOOP.md)

Before unlocking student development run:
- [`prompts/v1/03_PRIORITY_1_ACCEPTANCE_AUDIT.md`](prompts/v1/03_PRIORITY_1_ACCEPTANCE_AUDIT.md)

Only after P10 is proven DONE, continue with:
- [`prompts/v1/04_PRIORITY_2_UNLOCK_AND_CONTINUE.md`](prompts/v1/04_PRIORITY_2_UNLOCK_AND_CONTINUE.md)

All prompts re-apply the repository skill-routing rules so skill use survives session/resume boundaries.

## Current state
Implementation is active in **P0 — Repository & Engineering Foundation**. Priority 2 remains blocked until P10 is DONE. See [`docs/v1/PHASE_TRACKER.md`](docs/v1/PHASE_TRACKER.md) for acceptance status and evidence.

## Local bootstrap

### Prerequisites
- Docker Engine 29+ with Docker Compose 2.40+
- `uv` 0.11.26+ for host-side backend development
- Node.js 24.19 with npm 11.17 for host-side frontend development

The full local stack can run without host Python or Node installations:

```bash
cp .env.example .env
docker compose up --build --wait
```

Services:
- admin web: `http://localhost:3000`
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- MinIO console: `http://localhost:9001`
- PostgreSQL/pgvector: `localhost:55432`
- Valkey: `localhost:56379`

The committed credentials are local-development placeholders only. Production mode rejects them. Stop services without deleting persisted local data using:

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

The integration suite uses disposable real PostgreSQL/pgvector, Valkey, and MinIO containers.

## Frontend development

```bash
npm ci
npm run lint --prefix apps/web
npm run typecheck
npm run test:coverage --prefix apps/web
npm run build --prefix apps/web
```

If the host Node version is older than Node 24, use the Docker runtime instead of relaxing the pinned engine requirement.

## Generated API client

The FastAPI OpenAPI document and TypeScript client types are reproducible repository artifacts:

```bash
npm run generate:client
npm run typecheck --prefix packages/api-client
```

CI regenerates both artifacts and fails if the committed output differs.

## Configuration and observability

Backend settings use the `EXAM_GURU_` environment prefix. Database, Valkey, and object-storage credentials are redacted through `SecretStr`. Production configuration rejects local placeholders and requires PostgreSQL TLS (`ssl=require` or stronger), `rediss://` for Valkey, and an HTTPS object-storage endpoint. Optional observability variables include:

- `EXAM_GURU_SENTRY_DSN`
- `EXAM_GURU_OTEL_EXPORTER_OTLP_ENDPOINT`
- `EXAM_GURU_OTEL_SERVICE_NAME`
- `EXAM_GURU_TRACE_SAMPLE_RATIO`

Every API response carries a validated `X-Request-ID`. OpenTelemetry FastAPI instrumentation is active, and Sentry-compatible error reporting is enabled only when a DSN is configured.

P1 browser acceptance uses deterministic admin/reviewer tokens only when `ENABLE_DETERMINISTIC_IDENTITY=true`; backend production settings reject those tokens. The development cookie is HttpOnly and SameSite=Strict. `ADMIN_COOKIE_SECURE=false` is allowed only for local HTTP, while the P10 production identity integration must set secure cookies and re-run authentication/session security tests.
