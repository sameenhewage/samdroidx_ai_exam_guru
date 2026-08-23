# V1 Phase / Acceptance Tracker

> **Important:** This tracker does **not** define a prompt-per-phase development process. Development follows `01_ENGINEERING_WORKFLOW.md` as one continuous loop. These phases are status/acceptance gates only.

## Status legend
- `NOT_STARTED` — no validated implementation evidence yet
- `IN_PROGRESS` — implementation/evidence exists but exit criteria are incomplete
- `BLOCKED` — external/human blocker prevents completion
- `DONE` — every exit criterion is satisfied with tests/evals/runtime evidence

## Priority rule
**Priority 1 must reach 100% DONE before any Priority 2 product feature begins.**

---

# PRIORITY 1 — ADMIN + EXAM INTELLIGENCE + RAG + LLM

## P0 — Repository & Engineering Foundation
**Status:** DONE

### Scope
- monorepo/workspace bootstrap
- Next.js web shell
- FastAPI API
- Python tooling with uv
- PostgreSQL + pgvector
- Valkey
- object-storage abstraction/local dev storage
- Docker/local dev environment
- migration framework
- generated OpenAPI TypeScript client
- test/eval foundations
- CI quality gates
- observability baseline
- secrets/config handling

### Exit criteria
- [x] clean clone can bootstrap documented local environment
- [x] web/API/worker processes start successfully
- [x] database migrations run from empty database
- [x] pgvector extension verified
- [x] Valkey connectivity verified
- [x] OpenAPI client generation is reproducible
- [x] unit/integration/API test harnesses pass
- [x] CI runs lint/type/test/build/migration checks
- [x] no secret committed to repository

### Evidence
- 2026-08-23 bootstrap/runtime: `docker compose up --build --detach --wait --wait-timeout 240` completed with web, API, Dramatiq worker, PostgreSQL 18/pgvector, Valkey and MinIO running; `/api/v1/health/ready` returned database/Valkey `ok`, web returned HTTP 200, worker logged `ready for action`, and PostgreSQL reported pgvector `0.8.6`.
- Backend gate: `uv run --project apps/api pytest apps/api/tests --cov=exam_guru_api --cov-report=term-missing` — 42 passed with 100% statement coverage; real disposable pgvector/PostgreSQL, Valkey and MinIO integration tests include clean migration, readiness, worker startup and immutable/idempotent object storage.
- Backend quality: Ruff check/format and strict mypy pass; production configuration rejects local credentials and unencrypted database, Valkey and object-storage connections; readiness checks have explicit deadlines and every API response carries a validated request ID.
- Frontend gate: ESLint and strict TypeScript pass; Vitest — 2 passed with 100% statements/branches/functions/lines; Next.js 16.3.1 production build succeeds with `/`, `/_not-found` and `/icon.svg` prerendered.
- OpenAPI: deterministic FastAPI export test passes; regenerating `packages/api-client/openapi.json` and `src/schema.d.ts` produced identical SHA-256 hashes; the `openapi-fetch` client contract type-checks.
- CI/security: pinned GitHub Actions run backend lint/format/type/test/migration, frontend audit/lint/type/test/build, generated-artifact checks and full Compose runtime smoke; `actionlint` passes, `npm audit --audit-level=moderate` reports zero vulnerabilities, and `detect-secrets-hook --baseline .secrets.baseline` passes on all repository files.
- Browser runtime: Chrome desktop and 390px mobile checks show the accessible Admin Content Studio workflow, no horizontal overflow and no console warnings/errors.
- Commit reference: `bb61052` (`feat(foundation): bootstrap P0 application platform`).

---

## P1 — Grade 5 Domain Model & Admin Foundation
**Status:** NOT_STARTED

### Scope
- admin authentication/authorization
- Grade 5 Scholarship exam configuration
- medium/version metadata
- competency/skill/sub-skill/learning-concept taxonomy
- admin CRUD/review screens required for content operations
- immutable/auditable state transitions for reviewed content

### Exit criteria
- [ ] Grade 5 domain taxonomy represented in database and validated
- [ ] admin can manage allowed taxonomy/configuration
- [ ] role/permission tests exist
- [ ] invalid state transitions are rejected
- [ ] audit metadata exists for sensitive admin changes
- [ ] admin UI/API E2E coverage exists for core taxonomy workflow

### Evidence
TBD

---

## P2 — Source Document Ingestion & Extraction
**Status:** NOT_STARTED

### Scope
- syllabus/teacher-guide/past-paper/marking-scheme upload
- S3-compatible file persistence
- file type/size/security validation
- native PDF extraction
- OCR adapter for scanned content
- extraction state machine/retry/idempotency
- source page/block provenance
- admin extraction-review/correction workflow

### Exit criteria
- [ ] real Grade 5 fixture documents can be uploaded and preserved
- [ ] native PDF extraction works with deterministic tests
- [ ] OCR abstraction exists and chosen open-source OCR has a benchmark record on representative Sinhala scans
- [ ] admin can compare/correct extracted content
- [ ] every extracted block retains immutable source/page provenance
- [ ] retrying ingestion does not duplicate trusted content
- [ ] failure/recovery paths are integration-tested
- [ ] extraction quality metrics are recorded

### Evidence
TBD

---

## P3 — Historical Question Bank & Curriculum Knowledge Base
**Status:** NOT_STARTED

### Scope
- question segmentation/normalization
- historical question metadata
- curriculum chunks
- classification assistance
- reviewer confirmation
- embeddings
- question/source provenance
- duplicate source import protection

### Exit criteria
- [ ] past-paper questions are stored as structured records
- [ ] curriculum content is chunked by meaningful educational boundaries, not blind character windows alone
- [ ] questions can be linked to competency/skill/sub-skill/source
- [ ] reviewer can correct classifications
- [ ] embeddings are versioned by provider/model/config
- [ ] re-embedding is safe/idempotent
- [ ] representative data-quality tests pass

### Evidence
TBD

---

## P4 — RAG Retrieval & Grounding
**Status:** NOT_STARTED

### Scope
- metadata filters
- lexical/full-text search
- pgvector semantic search
- hybrid fusion/ranking
- optional reranking only if benchmark proves value
- context builder
- citations/provenance
- retrieval eval dataset

### Exit criteria
- [ ] retrieval cannot leak content across disallowed grade/medium/curriculum boundaries
- [ ] hybrid retrieval works against real PostgreSQL + pgvector integration tests
- [ ] every returned context item includes source provenance
- [ ] fixed Grade 5 eval set measures retrieval relevance
- [ ] baseline metrics recorded before tuning
- [ ] retrieval meets documented acceptance threshold on the agreed fixture set
- [ ] adversarial/irrelevant queries are handled safely

### Evidence
TBD

---

## P5 — Historical Exam Intelligence, Forecasting & Backtesting
**Status:** NOT_STARTED

### Scope
- deterministic historical statistics
- competency/skill/question-type/difficulty/marks distributions
- recency/coverage features
- forecast/practice-priority model
- rolling historical backtests
- syllabus-balanced baseline comparison
- admin visualization/report

### Exit criteria
- [ ] statistics are reproducible from source data
- [ ] no LLM is required for deterministic scoring calculations
- [ ] rolling held-out backtests exist for multiple historical years where data permits
- [ ] baseline comparison is stored and visible
- [ ] metrics/limitations are documented
- [ ] if forecasting does not beat baseline meaningfully, product wording falls back to syllabus-balanced practice
- [ ] no UI/API claims exact future-exam certainty

### Evidence
TBD

---

## P6 — Deterministic Paper Blueprint Engine
**Status:** NOT_STARTED

### Scope
- paper structure rules
- competency/skill allocation
- difficulty allocation
- marks/question-type constraints
- forecast-informed practice priorities
- versioned blueprint creation

### Exit criteria
- [ ] blueprint generation is deterministic for the same inputs/seed/config where designed
- [ ] coverage constraints are validated in code
- [ ] impossible blueprints fail clearly
- [ ] blueprint slots carry all generation requirements and rationale/evidence metadata
- [ ] tests cover boundary distributions and rule conflicts
- [ ] admin can inspect a blueprint before generation

### Evidence
TBD

---

## P7 — LLM Provider Layer & Question Generation
**Status:** NOT_STARTED

### Scope
- provider-independent LLM interface
- OpenAI initial adapter
- structured output schemas
- prompt/version registry
- RAG-context injection
- candidate generation
- token/cost/latency accounting
- retry/idempotency/error handling
- deterministic fakes for CI

### Exit criteria
- [ ] domain services do not depend directly on an OpenAI SDK type
- [ ] generated outputs use validated structured schemas
- [ ] model/provider/prompt/retrieval versions are stored for every generation
- [ ] paid/live provider is not required for normal CI
- [ ] provider failure/retry/idempotency is tested
- [ ] cost/token usage is captured per generation run
- [ ] generation uses blueprint + grounded context, not a generic "make a paper" prompt

### Evidence
TBD

---

## P8 — Automated Validation, Evals & Duplicate Detection
**Status:** NOT_STARTED

### Scope
- curriculum-scope checks
- answer/option validation
- age-appropriateness checks
- language checks
- format/marks checks
- duplicate/paraphrase similarity
- provenance/grounding checks
- evaluation datasets and quality scoring

### Exit criteria
- [ ] invalid structured output is rejected
- [ ] generated question cannot bypass validation to published state
- [ ] duplicate/paraphrase regression fixtures exist
- [ ] MCQ single-correct-answer rules are tested where relevant
- [ ] deterministic answer verification is used where possible
- [ ] validation findings are persisted and auditable
- [ ] every discovered quality defect becomes a regression test/eval case
- [ ] live-model eval baseline exists for the chosen generation configuration

### Evidence
TBD

---

## P9 — Human Review, Question Bank & Paper Publishing
**Status:** NOT_STARTED

### Scope
- reviewer queue
- source/context visibility
- edit/approve/reject workflow
- approved question bank
- paper composition
- immutable published paper versions
- archive/unpublish rules

### Exit criteria
- [ ] reviewer can inspect question, answer, blueprint, retrieved sources and validation results together
- [ ] approve/reject/edit actions are authorized and audited
- [ ] rejected questions cannot be published
- [ ] published paper versions are immutable/reproducible
- [ ] student serving path requires no live LLM call
- [ ] complete Grade 5 practice paper can be generated, reviewed and published end-to-end

### Evidence
TBD

---

## P10 — Priority 1 Full Acceptance / Production Readiness Gate
**Status:** NOT_STARTED

### Mandatory end-to-end journey
`admin login -> upload real Grade 5 source -> extraction/OCR -> human correction -> ingest -> question/knowledge normalization -> RAG retrieval -> historical analysis/backtest -> blueprint -> LLM generation -> automated validation -> human review -> publish`

### Exit criteria
- [ ] every P0-P9 phase is DONE
- [ ] Priority 1 E2E journey passes
- [ ] security/adversarial review completed with regression tests for findings
- [ ] migrations verified from clean database
- [ ] backup/restore or data recovery approach documented for critical source/question data
- [ ] observability covers ingestion, jobs, retrieval, LLM calls, validation and publishing failures
- [ ] AI cost metrics are observable
- [ ] representative Sinhala Grade 5 real-content quality review completed
- [ ] known limitations documented
- [ ] CI green on the release commit

### Evidence
TBD

### Priority gate
**Priority 2 remains BLOCKED until P10 is DONE.**

---

# PRIORITY 2 — STUDENT PRODUCT

## P11 — Student Identity, Entitlements & Published Paper Catalog
**Status:** BLOCKED

Blocked by: P10

### Exit criteria
- [ ] student authentication/profile
- [ ] Grade 5 entitlements/subscription access rules
- [ ] free/sample vs premium access tested
- [ ] published paper catalog only exposes publishable versions

### Evidence
TBD

---

## P12 — Student Exam Runner
**Status:** BLOCKED

Blocked by: P10

### Exit criteria
- [ ] timed attempt lifecycle
- [ ] answer autosave/idempotency
- [ ] navigation/review flags
- [ ] reconnect/resume safety
- [ ] submit finalization rules
- [ ] E2E coverage

### Evidence
TBD

---

## P13 — Marking & Skill Analytics
**Status:** BLOCKED

Blocked by: P10

### Exit criteria
- [ ] deterministic marking for supported question types
- [ ] attempt/answer auditability
- [ ] competency/skill score aggregation
- [ ] wrong-answer review data
- [ ] edge cases tested

### Evidence
TBD

---

## P14 — Progress Dashboard & Recommendations
**Status:** BLOCKED

Blocked by: P10

### Exit criteria
- [ ] historical score trends
- [ ] skill trend calculations
- [ ] weak-skill identification
- [ ] deterministic next-paper/practice recommendation baseline
- [ ] dashboard E2E coverage

### Evidence
TBD

---

## P15 — Full V1 Acceptance Gate
**Status:** BLOCKED

Blocked by: P10-P14

### Exit criteria
- [ ] Priority 1 remains green/regression-free
- [ ] P11-P14 DONE
- [ ] full student journey passes from entitlement to progress dashboard
- [ ] no student paper serving depends on live LLM availability
- [ ] security/privacy review for student data completed
- [ ] load/performance baseline documented
- [ ] release CI green

### Evidence
TBD

---

# Current next action
P0 is complete. The highest-priority active gate is **P1 — Grade 5 Domain Model & Admin Foundation**, beginning with the database-backed Grade 5 taxonomy and admin authorization/audit boundaries. Priority 2 remains blocked by P10.
