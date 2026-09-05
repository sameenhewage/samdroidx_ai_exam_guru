# AI Exam Guru — Engineering Agent Instructions

## Authoritative system architecture
Before changing storage, deployment, networking, RAG data location, publishing, hosted/student boundaries, backup strategy, multi-grade core hierarchy, or other architecture-level behavior, read **`docs/SYSTEM_ARCHITECTURE.md` in full**.

`docs/SYSTEM_ARCHITECTURE.md` is the authoritative whole-system architecture contract. The current bootstrap architecture is a **private/local Exam Guru Studio** plus a **hosted Exam Guru Student platform**. Raw educational source material, RAG/vector state, AI generation history and private content-production data remain in the local/private Studio by default. Only human-approved, validated, immutable/versioned student-ready content crosses the explicit publication boundary to the hosted platform.

Do not silently reintroduce cloud bulk source storage, make MinIO/S3 mandatory, expose the local Studio publicly, or couple student exam serving to live RAG/LLM generation unless the architecture contract is intentionally changed and documented in the same engineering change.

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

For P1 admin acceptance, preserve the authentication port, role/permission enforcement, append-only auditing and secure deterministic development/test identity adapter. Defer production OAuth/OIDC/external identity-provider integration to P10 production hardening; do not let it block proving the core Priority 1 content-intelligence system.

## Execution model
This project is **not developed by feeding one implementation prompt per phase**. Work as a continuous engineering loop:

`inspect -> plan -> write failing tests -> implement -> run tests -> inspect failures -> fix -> refactor -> re-run full gates -> update change log/tracker -> commit -> push -> verify remote -> continue`

Phases in `docs/v1/PHASE_TRACKER.md` are acceptance/status gates, not isolated prompt-driven implementation units.

## Agent Skills — mandatory automatic routing
Repository skills live under `.agents/skills/<skill-name>/SKILL.md` and use the Agent Skills `SKILL.md` format.

### Mandatory trigger rules
1. **For every engineering turn, always read and apply:**
   - `.agents/skills/loop-engineering/SKILL.md`
   - `.agents/skills/tdd-eval-engineering/SKILL.md`
2. Before changing code, tests, prompts, schemas, migrations, infrastructure or docs that affect behavior, inspect the available skill list below and select **all** skills whose description matches the current work.
3. If a task clearly matches a skill description, using that skill is mandatory. Do not wait for the user to name the skill.
4. Multiple skills may be active at the same time. Example: a RAG API change normally uses `loop-engineering` + `tdd-eval-engineering` + `rag-retrieval-evaluation` + `fastapi-domain-engineering` + possibly `security-reliability-review`.
5. Read the full relevant `SKILL.md` before implementation; do not rely on remembered summaries from earlier sessions.
6. Re-evaluate skill selection whenever the active work item changes during the continuous loop.
7. If a skill conflicts with `AGENTS.md`, `docs/SYSTEM_ARCHITECTURE.md`, or the V1 contract docs, the more specific repository contract/`AGENTS.md` rule wins; document the conflict rather than silently ignoring it.
8. At the end of an execution session, report which repo skills materially governed the work.

### Available repository skills
- **loop-engineering** — `.agents/skills/loop-engineering/SKILL.md`  
  Always use for implementation, refactors, bug fixes, integration, acceptance and documentation work. Drives the continuous loop, mandatory change logging, authorized post-commit pushes and Priority 1 lock.
- **tdd-eval-engineering** — `.agents/skills/tdd-eval-engineering/SKILL.md`  
  Always use when behavior changes. Enforces RED→GREEN→REFACTOR and eval-first AI/RAG work.
- **fastapi-domain-engineering** — `.agents/skills/fastapi-domain-engineering/SKILL.md`  
  Use for Python/FastAPI, Pydantic, SQLAlchemy/Alembic, REST/OpenAPI, background jobs, modular-monolith/domain architecture and provider ports.
- **nextjs-product-engineering** — `.agents/skills/nextjs-product-engineering/SKILL.md`  
  Use for Next.js/React/TypeScript, shadcn/ui, React Aria, Tailwind, typed API integration and admin/student product UI.
- **teacher-content-studio-ux** — `.agents/skills/teacher-content-studio-ux/SKILL.md`  
  Use for teacher/non-technical content-operator UX, Materials Library, simple generation/review flows, progressive disclosure and hiding engineering internals from the normal product experience.
- **document-ingestion-ocr** — `.agents/skills/document-ingestion-ocr/SKILL.md`  
  Use for PDF upload, native extraction, OCR, layout/reading order, provenance, extraction review and ingestion/chunk preparation.
- **rag-retrieval-evaluation** — `.agents/skills/rag-retrieval-evaluation/SKILL.md`  
  Use for embeddings, pgvector, full-text/hybrid retrieval, metadata filters, ranking/reranking, context building, provenance and retrieval evals.
- **exam-forecast-backtesting** — `.agents/skills/exam-forecast-backtesting/SKILL.md`  
  Use for historical analytics, practice-priority forecasting, held-out rolling backtests, baseline comparison and forecast calibration.
- **llm-question-generation-validation** — `.agents/skills/llm-question-generation-validation/SKILL.md`  
  Use for LLM/provider adapters, structured generation, prompts, answer/marking generation, validation, duplicate detection, AI evals and cost/latency tracking.
- **subject-quality-validation** — `.agents/skills/subject-quality-validation/SKILL.md`  
  Use whenever generated educational content must be checked for subject-specific correctness: trusted subject routing, Maths solver/tool checks, factual grounding, language/ambiguity checks, structured semantic verification, reviewer correction memory and subject-quality evals. Read `docs/v1/06_SUBJECT_QUALITY_VALIDATION_ENGINE.md` in full.
- **security-reliability-review** — `.agents/skills/security-reliability-review/SKILL.md`  
  Use for authz/security, uploads, prompt injection/RAG poisoning, leakage, retries/idempotency/races, publish bypass, provider failure, cost abuse and adversarial review.
- **priority1-admin-acceptance** — `.agents/skills/priority1-admin-acceptance/SKILL.md`  
  Use for Priority 1 admin workflows, tracker phase closure, full admin→published-paper acceptance and especially P10 unlock decisions.
- **student-exam-product** — `.agents/skills/student-exam-product/SKILL.md`  
  Use only after P10 is DONE, for Priority 2 student identity/entitlements, exam runner, autosave/resume, marking, analytics, progress and recommendations.

### Task-to-skill examples
- Bootstrap backend/API/database → always-on skills + `fastapi-domain-engineering`.
- Build teacher/admin UI → always-on skills + `nextjs-product-engineering` + `teacher-content-studio-ux` + `priority1-admin-acceptance`.
- Upload/extract/OCR → always-on skills + `document-ingestion-ocr` + `fastapi-domain-engineering` + security when handling untrusted files.
- Build/tune RAG → always-on skills + `rag-retrieval-evaluation` + `fastapi-domain-engineering` + security for injection/leakage cases.
- Forecast/backtest → always-on skills + `exam-forecast-backtesting`.
- Generate questions → always-on skills + `llm-question-generation-validation` + `rag-retrieval-evaluation`; add security for prompt-injection/provider-failure work.
- Validate subject correctness of questions/answers/marking → always-on skills + `llm-question-generation-validation` + `subject-quality-validation` + `rag-retrieval-evaluation` + `fastapi-domain-engineering`; add `security-reliability-review` for solver parsing, provider calls and publish-gate changes.
- Build teacher-facing validation/review UX → always-on skills + `subject-quality-validation` + `teacher-content-studio-ux` + `nextjs-product-engineering` + `priority1-admin-acceptance`.
- Close P0–P10 gates → always-on skills + `priority1-admin-acceptance`; use `security-reliability-review` before major/P10 closure.
- After P10 only, build student product → always-on skills + `student-exam-product` + `nextjs-product-engineering` + `fastapi-domain-engineering` + security as relevant.

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
- subject-quality regression/eval tests covering machine-verifiable answers, grounded factual claims, unsupported/ambiguous cases and reviewer corrections
- opt-in real-model evaluation suite for quality benchmarking; never make normal CI depend on paid external model availability
- end-to-end tests for admin workflows before Priority 1 closes
- student end-to-end tests only after Priority 1 closes

## Quality rules
- Do not use LLM output as source-of-truth without validation.
- Do not let the LLM decide deterministic exam rules that can be encoded in domain logic.
- For subject-specific correctness, use the strongest available path: `deterministic rule/tool -> grounded subject checker -> structured semantic verifier -> human review`.
- Subject/grade/medium/curriculum scope used for validation must come from trusted server-owned domain state; never infer validator routing from generated text or filenames.
- An inability to verify content is not a PASS. Represent uncertainty explicitly and route it to review.
- Do not use unrestricted `eval`/`exec` or execute generated code for Maths checking; use bounded parsers and controlled tools.
- Public-web pages are not the default educational source of truth. Prefer reviewed local curriculum/teacher-guide/past-paper material with provenance.
- Every generated question must carry provenance, blueprint slot, retrieved context references, generation metadata, validation results, and review state.
- Never publish a question automatically in V1 without the configured review gate.
- Preserve original uploaded documents and immutable source references.
- No claims of exact future-exam prediction. Use evidence-backed practice priority/forecast language only.
- Backtest forecasting against held-out historical papers and compare against a simple syllabus-balanced baseline.
- Keep the LLM provider replaceable. OpenAI is the initial provider, not an architectural dependency.
- Keep RAG/data ownership inside our application/database and respect the local/private Studio boundary defined by `docs/SYSTEM_ARCHITECTURE.md`.

## V1 technology baseline
Use the versions selected at bootstrap time after verifying current stable/security-patched releases.

- Web: Next.js + React + TypeScript
- UI: shadcn/ui + React Aria + Tailwind CSS
- API/backend: Python + FastAPI + Pydantic
- API style: REST + OpenAPI, generate the TypeScript client
- ORM/migrations: SQLAlchemy 2 + Alembic
- Database: PostgreSQL + pgvector
- queue/cache: Valkey
- source/object storage: provider abstraction with **local durable host filesystem as the bootstrap/default Studio backend**; S3/MinIO remain optional future providers, not mandatory runtime dependencies
- Python tooling: uv
- document extraction: native PDF extraction first; benchmark open-source OCR for scans
- AI: provider abstraction; OpenAI initially; benchmark alternatives where quality/cost matters
- Studio deployment: versioned Docker/Docker Compose on the private/local operator machine with host-mounted durable data
- Student deployment: hosted/public service containing published content and student runtime data, without the private raw RAG corpus
- observability: OpenTelemetry + Sentry-compatible error tracking

Do not add GraphQL, microservices, Kubernetes, a separate vector database, fine-tuning, mandatory cloud object storage, or a broad agent framework unless a documented requirement proves the need.

## LangChain / LangGraph policy
LangChain is allowed selectively when it provides measurable value, but it must not own our domain architecture. The V1 deterministic RAG, curriculum rules, forecasting, blueprinting, validation, and publishing workflow remain first-party code. LangGraph may be introduced later if a genuinely stateful/agentic workflow requires durable orchestration.

## Repository discipline
- Treat `docs/SYSTEM_ARCHITECTURE.md` as the authoritative whole-system architecture and update it in the same change whenever architecture boundaries change.
- Treat `docs/v1/` as the V1 product/acceptance contract beneath that architecture.
- Treat `docs/v1/06_SUBJECT_QUALITY_VALIDATION_ENGINE.md` as the V1 contract for subject-aware correctness/tooling and reviewer-learning behavior.
- Treat `prompts/v1/` as reusable operator prompts, not product runtime prompts.
- Treat `.agents/skills/` as mandatory reusable engineering workflows; keep skill descriptions accurate because they drive automatic selection.
- Update `docs/v1/PHASE_TRACKER.md` only when evidence exists.
- A phase can be marked `DONE` only when all listed acceptance criteria, tests, evals, docs, and runtime verification pass.
- Never mark partial work as DONE.
- Record every completed cohesive change in `docs/v1/PHASE_TRACKER.md#change-log`, including code, tests, configuration, infrastructure, documentation and skills. Include the date, reason, affected paths, exact verification/results and limitations in the same commit as the change; backfill already-committed work with its known commit reference.
- Keep changes cohesive and committed with meaningful messages; stage only related files and preserve other work.
- The repository owner's standing instruction (2026-09-05) is to push each verified commit to the current branch's configured upstream and verify the remote ref afterward. Respect later no-push instructions and higher-priority restrictions. If the push is blocked, report it without force-pushing, changing Git configuration, rewriting history or bypassing hooks/security controls.
- Keep the default branch green; a successful push is not evidence that remote CI passed.

## Definition of Priority 1 complete
Priority 1 is complete only when an admin can ingest real Grade 5 source content, review extraction, produce a structured knowledge base, retrieve grounded context, run historical analysis/backtests, generate a complete paper from a deterministic blueprint, validate every question including applicable subject-specific correctness/grounding checks, review/edit/approve it, publish it, and reproduce the process with automated tests/evals and documented evidence.

Until then, do not implement student-facing product functionality beyond minimal technical scaffolding required to support Priority 1.

## Local verification environment
- The workstation's default shell can resolve obsolete Node 10. Before npm gates, select the repository-pinned Node 24.19.0 runtime; the installed path is `/home/sameen/.nvm/versions/node/v24.19.0/bin`. Do not relax the engine requirement to accommodate the shell default.
- `docker compose exec` does not run the container entrypoint that selects the application UID. Run storage diagnostics as the actual API process owner (inspect `docker compose top api`; currently `10001:10001`), not the default root exec user. The local storage adapter intentionally rejects owner mismatches; do not change source-directory permissions to bypass that protection.