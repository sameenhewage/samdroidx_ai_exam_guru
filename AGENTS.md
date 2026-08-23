# AI Exam Guru — Engineering Agent Instructions

## Mission
Build V1 of AI Exam Guru as a production-quality Grade 5 Scholarship examination platform for Sri Lanka. V1 has two strict priorities:

1. **Priority 1 — Admin + Content Intelligence + RAG + LLM**
   - curriculum and source-document ingestion
   - extraction/OCR review
   - Grade 5 domain taxonomy
   - past-question normalization/classification
   - PostgreSQL + pgvector knowledge base
   - hybrid retrieval/RAG
   - historical analysis and backtesting
   - deterministic paper blueprints
   - LLM question generation
   - automated validation/evaluation
   - human review and paper publishing
2. **Priority 2 — Student Experience**
   - authentication/subscription
   - paper attempt flow
   - autosave/timer/navigation
   - marking
   - skill analytics
   - progress dashboard
   - simple recommendation engine

**Do not start Priority 2 until every Priority 1 acceptance gate is DONE.**

## Execution model
This project is **not developed by feeding one implementation prompt per phase**. Work as a continuous engineering loop:

`inspect -> plan -> write failing tests -> implement -> run tests -> inspect failures -> fix -> refactor -> re-run full gates -> update tracker -> continue`

Phases in `docs/v1/PHASE_TRACKER.md` are acceptance/status gates, not isolated prompt-driven implementation units.

## TDD is mandatory
Use RED -> GREEN -> REFACTOR for application behavior.

- Write or update a failing test before changing behavior.
- Implement the smallest correct change that makes the test pass.
- Refactor only while tests remain green.
- Every defect discovered during review/evaluation must first become a regression test or eval case.
- Infrastructure-only bootstrap may precede tests when no executable behavior exists, but immediately add smoke/integration coverage afterward.
- Never weaken/delete a valid test merely to make CI pass.

### Required test layers
- unit tests for deterministic domain logic
- integration tests against real PostgreSQL + pgvector and Valkey containers
- API contract tests for FastAPI/OpenAPI
- RAG retrieval/evaluation tests using fixed Grade 5 fixtures
- AI structured-output/validator contract tests using deterministic provider fakes
- opt-in real-model evaluation suite for quality benchmarking; never make normal CI depend on paid external model availability
- end-to-end tests for admin workflows before Priority 1 closes
- student end-to-end tests only after Priority 1 closes

## Quality rules
- Do not use LLM output as source-of-truth without validation.
- Do not let the LLM decide deterministic exam rules that can be encoded in domain logic.
- Every generated question must carry provenance, blueprint slot, retrieved context references, generation metadata, validation results, and review state.
- Never publish a question automatically in V1 without the configured review gate.
- Preserve original uploaded documents and immutable source references.
- No claims of exact future-exam prediction. Use evidence-backed practice priority/forecast language only.
- Backtest forecasting against held-out historical papers and compare against a simple syllabus-balanced baseline.
- Keep the LLM provider replaceable. OpenAI is the initial provider, not an architectural dependency.
- Keep RAG/data ownership inside our application/database.

## V1 technology baseline
Use the versions selected at bootstrap time after verifying current stable/security-patched releases.

- Web: Next.js + React + TypeScript
- UI: shadcn/ui + React Aria + Tailwind CSS
- API/backend: Python + FastAPI + Pydantic
- API style: REST + OpenAPI, generate the TypeScript client
- ORM/migrations: SQLAlchemy 2 + Alembic
- Database: PostgreSQL + pgvector
- queue/cache: Valkey
- object storage: S3-compatible
- Python tooling: uv
- document extraction: native PDF extraction first; benchmark open-source OCR for scans
- AI: provider abstraction; OpenAI initially; benchmark alternatives where quality/cost matters
- deployment: Docker
- observability: OpenTelemetry + Sentry-compatible error tracking

Do not add GraphQL, microservices, Kubernetes, a separate vector database, fine-tuning, or a broad agent framework unless a documented requirement proves the need.

## LangChain / LangGraph policy
LangChain is allowed selectively when it provides measurable value, but it must not own our domain architecture. The V1 deterministic RAG, curriculum rules, forecasting, blueprinting, validation, and publishing workflow remain first-party code. LangGraph may be introduced later if a genuinely stateful/agentic workflow requires durable orchestration.

## Repository discipline
- Treat `docs/v1/` as the V1 contract.
- Treat `prompts/v1/` as reusable operator prompts, not product runtime prompts.
- Update `docs/v1/PHASE_TRACKER.md` only when evidence exists.
- A phase can be marked `DONE` only when all listed acceptance criteria, tests, evals, docs, and runtime verification pass.
- Never mark partial work as DONE.
- Keep changes cohesive and committed with meaningful messages.
- Keep the default branch green.

## Definition of Priority 1 complete
Priority 1 is complete only when an admin can ingest real Grade 5 source content, review extraction, produce a structured knowledge base, retrieve grounded context, run historical analysis/backtests, generate a complete paper from a deterministic blueprint, validate every question, review/edit/approve it, publish it, and reproduce the process with automated tests/evals and documented evidence.

Until then, do not implement student-facing product functionality beyond minimal technical scaffolding required to support Priority 1.