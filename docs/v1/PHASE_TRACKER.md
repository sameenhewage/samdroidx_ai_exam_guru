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
**Status:** DONE

### Scope
- admin authentication/authorization boundary using the secure deterministic development/test identity adapter
- Grade 5 Scholarship exam configuration
- medium/version metadata
- competency/skill/sub-skill/learning-concept taxonomy
- admin CRUD/review screens required for content operations
- immutable/auditable state transitions for reviewed content

Production OAuth/OIDC/external identity-provider integration is deferred to P10. P1 proves the same authentication port, role checks, negative authorization paths and admin browser workflow with the secure deterministic adapter.

### Exit criteria
- [x] Grade 5 domain taxonomy represented in database and validated
- [x] admin can manage allowed taxonomy/configuration
- [x] role/permission tests exist
- [x] invalid state transitions are rejected
- [x] audit metadata exists for sensitive admin changes
- [x] admin UI/API E2E coverage exists for core taxonomy workflow

### Evidence
- `19b7ae8` adds the Grade 5 exam configuration, medium, curriculum-version and competency/skill/sub-skill/learning-concept hierarchy domain/persistence foundation.
- Domain validation rejects malformed codes/titles, missing or wrong-level parents, cross-curriculum parents, inactive-parent activation, duplicate IDs and duplicate sibling codes while supporting incremental hierarchy writes.
- Alembic `0002_grade5_taxonomy` creates audit-stamped PostgreSQL tables, Grade 5/check constraints, null-safe sibling uniqueness, same-curriculum composite foreign keys and hierarchy/active-parent triggers; clean migration plus `alembic check` pass against real PostgreSQL/pgvector.
- Backend gate after the taxonomy foundation: 61 tests passed with 100% statements and branches; Ruff check/format and strict mypy pass. Integration coverage includes audit metadata, Grade 5 enforcement, cross-curriculum isolation, parent-level/active-parent invariants and database-enforced sibling uniqueness.
- `83d1845` adds secure-by-default identity/authorization ports, admin/reviewer permission boundaries, bearer-token input limits, authorized taxonomy list/create REST contracts, generated TypeScript contracts and transactional audit events.
- Alembic `0003_admin_audit_events` is applied in the Compose runtime and makes audit events append-only at the database boundary; unauthenticated taxonomy access returns machine-readable HTTP 401 while health endpoints remain available.
- Backend gate after the authorization/API slice: 89 tests passed with 100% statements and branches; API integration tests cover 401/403, admin create, reviewer read, object not found, domain validation, duplicate/database conflict handling, transaction rollback and append-only audit behavior.
- `d1556f5` completes authorized exam/medium/curriculum create/list/update/deactivate workflows, deterministic non-production admin/reviewer identity, audited taxonomy draft/reviewed/deprecated lifecycle, generated client integration, Next.js admin UI, same-origin HttpOnly cookie proxy and Playwright acceptance coverage.
- Alembic `0004_taxonomy_review_lifecycle` enforces forward-only review state, immutable reviewed content, reviewed-parent requirements, active-state consistency and hierarchy-safe deactivation in PostgreSQL; concurrent review/deactivate integration coverage proves row-lock serialization and a valid final state.
- Final backend gate: 123 tests passed with 100% statements and branches; Ruff check/format and strict mypy pass. Final frontend gate: 5 Vitest tests pass with 100% coverage for unit-scoped modules, ESLint/typecheck/build pass, and the full Chromium E2E passes in 6.6 seconds.
- Browser/API E2E evidence covers admin and reviewer identities, negative 403 authorization, exam/medium/curriculum creation, duplicate validation failure, taxonomy draft update, review immutability, reviewed-parent selection, blocked parent deactivation, ordered child/parent deactivation and visible append-only audit evidence. The only browser console errors are the two explicitly asserted HTTP 409 negative paths.
- CI includes clean migrations/schema drift checks, generated OpenAPI checks, npm audit, secrets scan, full Compose startup and Playwright admin E2E. Production identity integration remains explicitly deferred to P10 hardening.

---

## P2 — Source Document Ingestion & Extraction
**Status:** IN_PROGRESS

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
- [x] real Grade 5 fixture documents can be uploaded and preserved
- [x] native PDF extraction works with deterministic tests
- [ ] OCR abstraction exists and chosen open-source OCR has a benchmark record on representative Sinhala scans
- [x] admin can compare/correct extracted content
- [x] every extracted block retains immutable source/page provenance
- [x] retrying ingestion does not duplicate trusted content
- [x] failure/recovery paths are integration-tested
- [x] extraction quality metrics are recorded

### Evidence
- `7628bfa` adds bounded PDF-only upload validation, unsafe-name/content spoof rejection, deterministic SHA-256 identity/object keys, immutable object writes, PostgreSQL source metadata, transactional upload auditing and same-checksum idempotent retries.
- Alembic `0005_source_documents` passes clean migration and schema-drift checks; authorized upload integration tests prove 201 creation, 200 deduplication, reviewer 403 rejection, one immutable storage write and one persisted audit event.
- PyMuPDF `1.28.2` native extraction has deterministic tests for page numbering, reading-order blocks, bounding-box/page provenance, engine/version metadata, character/page quality metrics, malformed/encrypted/page-limit failures and explicit OCR routing for textless PDFs.
- `e28cb06` adds migration `0006_extraction_persistence`, database-enforced forward/recovery/review/trust states, immutable page/block provenance, persisted quality metrics, optimistic correction versions, auditable human review/trust transitions and a bounded Dramatiq extraction worker.
- Real PostgreSQL integration tests cover extraction idempotency, concurrent serialization, interrupted-attempt cleanup, malformed/source-integrity failure, retry recovery, contiguous reading order, immutable raw provenance, correction conflicts and reviewed-to-trusted promotion. Request-size middleware, exact PDF headers, S3 timeouts and source-specific permissions close adversarial findings.
- A provider-independent OCR port and deterministic benchmark harness record engine/version/configuration, page/block provenance, normalized character error rate, page coverage and question-structure coverage; 56 focused tests prove the harness while explicitly making no real Sinhala quality claim.
- The Compose-backed Chromium journey proves `admin upload -> immutable MinIO source -> queued Valkey/Dramatiq extraction -> PostgreSQL pages/blocks/metrics -> compare/correct -> trusted -> same-checksum reuse`, visible audit evidence and reviewer 403 denial. The P1 taxonomy journey also remains green; 2 browser tests pass in 7.0 seconds.
- Final gate for this progress point: 289 backend tests pass with 100% statements and branches; 18 frontend tests pass, configured unit coverage remains 100%, and Ruff/format/mypy/ESLint/typecheck/build/OpenAPI reproducibility/npm audit/actionlint/Compose health all pass.
- `7274ae7` adds deterministic metadata-only local inventory tooling. The ignored operator dataset contains 90 PDFs / 859 pages: 1 Sinhala teacher guide (293 pages), 51 English documents (77 pages), 13 Maths documents (397 pages), 6 Parisaraya scan-dominant activities (17 pages), and 19 Sinhala activities (75 pages), with zero malformed files.
- Four real local PDFs were checksum-verified against the inventory and preserved through the API/MinIO pipeline. A five-page Unicode Sinhala Maths source was extracted by PyMuPDF `1.28.2` into 39 provenance blocks / 1,109 characters with native-text ratio `1.0` and remains queued for human trust review; scan-dominant Parisaraya and overlay-heavy Sinhala sources remain untrusted OCR candidates.
- `5e31dda` adds a real-data regression that detects sparse native overlays over near-full-page raster images: the representative Parisaraya file reports image-dominant ratio `1.0` and `needs_ocr=true`; the Sinhala activity reports `0.25` and `needs_ocr=true`; the teacher guide remains flagged separately for nonstandard font-mapping review. Backend gate: 297 tests pass with 100% statements and branches.
- `14c0c42` adds a bounded open-source Tesseract CLI adapter behind the OCR port: exact PDF/checksum validation, page/DPI/pixel/language/time/output limits, isolated temporary raster batches, argv-only execution with a secret-free child environment, typed availability/process/timeout/output failures, TSV block/bbox/confidence mapping, and reproducible engine/config provenance. `655cb47` applies the same control/bidi-text rejection to native extraction while preserving Sinhala joiners. The adapter gate is 107 tests with 100% statements and branches; its live integration is explicitly skipped because this host has no Tesseract executable or Sinhala traineddata.
- P2 remains IN_PROGRESS because representative scanned Sinhala pages do not yet have human-adjudicated ground truth, no Sinhala-capable OCR executable/traineddata is installed for a defensible benchmark, and the new adapter is not yet wired into the persisted worker state machine. No OCR quality claim is made. A secondary accepted risk is that a permanently abandoned upload can leave a content-addressed S3 object if PostgreSQL fails after object creation; retries self-heal, and lifecycle/tagged orphan cleanup remains P10 reliability hardening.

---

## P3 — Historical Question Bank & Curriculum Knowledge Base
**Status:** IN_PROGRESS

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
- [x] past-paper questions are stored as structured records
- [x] curriculum content is chunked by meaningful educational boundaries, not blind character windows alone
- [x] questions can be linked to competency/skill/sub-skill/source
- [x] reviewer can correct classifications
- [x] embeddings are versioned by provider/model/config
- [x] re-embedding is safe/idempotent
- [ ] representative data-quality tests pass

### Evidence
- `8325efe` starts P3 with strict historical-question and educational-boundary chunk contracts, immutable source document/page/block provenance, forward-only review states, taxonomy classification requirements for reviewed records, and a provider/model/dimension/version/config-fingerprint-aware deterministic embedding port.
- `d654bf4` adds PostgreSQL/pgvector persistence, trusted-source and same-curriculum provenance enforcement, taxonomy hierarchy validation, immutable forward review-state triggers, atomic duplicate import handling, immutable versioned embedding configurations, source-text hash binding, idempotent re-embedding, transactional audit events, and real PostgreSQL 18/pgvector integration tests.
- `502198f` keeps the isolated integration credential scanner-safe. The unified backend gate is 984 tests with 100% statements and branches; all 35 integration tests, Ruff check/format, strict mypy, local-data boundary checks, and the committed-file secret scan pass.
- `021f772` and `d085769` add curriculum-scoped authorized import/list/get/classification/review APIs, bounded list filters, server identities, required trusted source-block provenance, version-zero inserts, atomic optimistic CAS, stable errors, audit events and vector-free embedding status/configuration responses. Migration `0008` enforces initial/incrementing versions at the database boundary.
- `eaa40f5` adds backward-compatible historical media/options/source-encoded answer/marking/archetype/difficulty-evidence persistence through migrations `0009`/`0010`, bounded JSON snapshots and full import-conflict identity without inventing unavailable metadata.
- `3bb15d7` and `908d84d` add the generated-client Knowledge Studio: admin import only from selectable trusted document/page/block provenance, reviewer taxonomy correction and forward review transitions with conflict refresh, metadata/embedding inspection, terminal locks, loading/error/empty/permission states and accessibility coverage. A real Compose/PostgreSQL/MinIO/worker Chromium journey imports both record types and completes reviewer classification/review; synthetic fixture content proves mechanics only.
- Remaining: human-reviewed representative real Grade 5 chunks/questions and associated data-quality evidence. The preserved database confirms all real local sources remain `EXTRACTED`, not `TRUSTED`; no real-review claim is made.

---

## P4 — RAG Retrieval & Grounding
**Status:** IN_PROGRESS

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
- [x] retrieval cannot leak content across disallowed grade/medium/curriculum boundaries
- [x] hybrid retrieval works against real PostgreSQL + pgvector integration tests
- [x] every returned context item includes source provenance
- [x] fixed Grade 5 eval set measures retrieval relevance
- [x] baseline metrics recorded before tuning
- [ ] retrieval meets documented acceptance threshold on the agreed fixture set
- [x] adversarial/irrelevant queries are handled safely

### Evidence
- `1c1a80d` starts P4 with exact grade/exam/medium/curriculum/taxonomy boundaries applied before ranking, embedding-space validation, deterministic weighted reciprocal-rank fusion, Unicode-normalized deduplication, bounded opaque context with complete provenance, and fixed identifier-based Recall@K, Precision@K, MRR, nDCG and leakage metrics.
- One hundred thirty focused retrieval tests pass with 100% statements and branches, including stronger-scoring forbidden scopes, mixed vector spaces, prompt-injection source text, irrelevant queries, duplicate poisoning, control-character rejection and context amplification limits.
- `dcc64dc` adds the real PostgreSQL `simple` full-text/pgvector adapter and fixed Grade 5 integration eval. Reviewed records are hard-filtered before ranking; a stronger-scoring forbidden grade/medium/curriculum record is physically present but never enters either channel. The recorded deterministic baseline is Recall@3 `1.0`, MRR `1.0`, leakage `0.0`, with complete provenance and prompt-injection text retained only as untrusted data.
- `36af06f` adds the authorized vector-free RAG Explorer API: server-side provider registry, exact persisted embedding metadata, active/reviewed taxonomy scope, bounded query/candidate/context work, lexical/vector/fused/context inspection, deduplication, truncation, diagnostics and phase latency. Deterministic embeddings are local/test only and staging/production fail closed until a real adapter is configured. The merged backend gate is 1,371 passed / 1 optional OCR integration skipped with 100% statements and branches.
- `e196e1a` adds the generated-client RAG Explorer UI with active curriculum/taxonomy selection, paginated embedding discovery, no-vector requests, inspectable channels/fusion/context/provenance/latency/diagnostics, untrusted source labels, truncation and explicit no-embedding/provider/permission/retry states. Component/axe coverage proves full-result rendering; the real browser journey proves the actionable no-embedding state because no normal embedding-ingestion API exists yet.
- Remaining: document the representative real Grade 5 acceptance threshold, meet it on human-reviewed embedded content, and prove the successful hybrid browser path. No reranker is added because no benchmark currently proves benefit.

---

## P5 — Historical Exam Intelligence, Forecasting & Backtesting
**Status:** IN_PROGRESS

### Scope
- deterministic historical statistics
- competency/skill/question-type/difficulty/marks distributions
- recency/coverage features
- forecast/practice-priority model
- rolling historical backtests
- syllabus-balanced baseline comparison
- admin visualization/report

### Exit criteria
- [x] statistics are reproducible from source data
- [x] no LLM is required for deterministic scoring calculations
- [x] rolling held-out backtests exist for multiple historical years where data permits
- [x] baseline comparison is stored and visible
- [x] metrics/limitations are documented
- [x] if forecasting does not beat baseline meaningfully, product wording falls back to syllabus-balanced practice
- [x] no UI/API claims exact future-exam certainty

### Evidence
- `04a391d` starts P5 with provenance-backed historical observations, exact deterministic competency/skill/type/difficulty/marks distributions, syllabus-balanced baseline and practice-priority methods, explicit pre-holdout leakage rejection, expanding rolling held-out windows, baseline deltas/variance/limitations, and safe baseline fallback when improvement is not meaningful.
- Fifty-seven focused analytics/backtest tests pass with 100% statements and branches. Synthetic fixed fixtures prove mechanics only; no future-exam prediction claim is made.
- `36af06f` adds immutable append-only analytics runs (migration `0011`) and authorized APIs. Only reviewed questions with active reviewed skills, complete difficulty evidence, trusted source blocks and checksum-bound source versions are included; all exclusions and question IDs are recorded. Statistics, exact Fraction metrics, expanding held-out windows/leakage audits, baseline and method runs, limitations, recommendation/fallback, source/config/input/result fingerprints and algorithm versions are persisted idempotently and audited.
- Real PostgreSQL integration seeds reviewed 2018–2021 fixtures plus incomplete and cross-curriculum records, verifies leakage exclusion, exact baseline visibility, idempotency, scoping, authorization and database immutability. This proves mechanics against real PostgreSQL, not real exam forecasting quality.
- `e196e1a` adds the generated-client Analytics Report Studio: admin-run/reviewer-read permissions, exact Fraction controls/rendering, data-quality exclusions, all five distributions, practice priorities, rolling held-out leakage audits, baseline/method comparison, limitations, fallback wording, source versions and fingerprints. The real PostgreSQL/API browser journey creates four held-out years, runs the report and verifies reviewer visibility and no future-prediction wording. Web gate: 41 component/axe tests, lint, typecheck and production build pass; the combined intelligence/knowledge browser gate passes 2/2.
- Remaining: repeat the acceptance report on multi-year human-reviewed real Grade 5 historical records. Until then, P5 remains IN_PROGRESS and makes no product-quality forecasting claim despite every deterministic mechanics criterion passing.

---

## P6 — Deterministic Paper Blueprint Engine
**Status:** IN_PROGRESS

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
- `7a04c3a` starts P6 with a deterministic versioned blueprint constraint solver, exact paper/section marks, question-type/difficulty/taxonomy allocations, impossible-constraint diagnostics, baseline-safe forecast priorities and self-contained generation slots with scope, rationale and evidence.
- One hundred twenty-six focused blueprint tests pass with 100% statements and branches across deterministic seeds, boundary distributions, conflicts, backtracking and forged aggregate invariants.
- Remaining: persisted blueprint versions, direct P5 backtest integration, authorized API and admin inspection/browser acceptance.

---

## P7 — LLM Provider Layer & Question Generation
**Status:** IN_PROGRESS

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
- [x] domain services do not depend directly on an OpenAI SDK type
- [x] generated outputs use validated structured schemas
- [ ] model/provider/prompt/retrieval versions are stored for every generation
- [x] paid/live provider is not required for normal CI
- [x] provider failure/retry/idempotency is tested
- [ ] cost/token usage is captured per generation run
- [x] generation uses blueprint + grounded context, not a generic "make a paper" prompt

### Evidence
- `e9a3d3b` starts P7 with provider-independent typed contracts, canonical P6 blueprint-slot integration, bounded untrusted provenance context, strict structured question/answer/marking schemas, prompt/provider/model/blueprint/retrieval/schema versions, token/cost/latency accounting, typed failures, deterministic fakes, idempotency identity and append-only prompt version registration.
- The generation boundary has no SDK dependency or publish authority; all candidates require validation. Prompt templates reject reserved context interpolation while retrieved prompt-injection text remains opaque data. Generation and blueprint integration tests pass with 100% generation-package statements and branches.
- `4ff624c` adds bounded orchestration with retryable-code-only attempts, linked identities, injected backoff scheduling, atomic result-cache semantics and cumulative input/output token and integer-microusd budgets across successes and accounted failures.
- `5a28ecd` adds the initial OpenAI adapter with the SDK isolated behind the port. `openai==3.1.0` was the newest release older than seven days and had no OSV advisory at selection. SDK retries are disabled, time/output/idempotency are bounded, strict schema parsing maps into first-party contracts, trusted blueprint instructions and digest-delimited untrusted context use separate roles, errors are sanitized, and versioned integer pricing captures latency/tokens/cost. The mocked adapter gate is 266 passed with 100% statements and branches; the paid live eval is explicitly skipped without opt-in credentials and makes no quality claim.
- Remaining: durable generation runs/attempts/jobs/API with every version and accounting record, admin inspection, and opt-in live-model quality/cost acceptance.

---

## P8 — Automated Validation, Evals & Duplicate Detection
**Status:** IN_PROGRESS

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
- `f0f45b8` starts P8 with immutable versioned validation inputs/reports/findings, stable pass/warn/fail codes, bounded non-leaking evidence, deterministic schema/blueprint/option/answer/marks/grounding/injection/language/age indicators and exact/hash duplicate checks composed through a canonical pipeline.
- Ninety-four focused validator tests pass with 100% statements and branches. Reports explicitly state that deterministic success does not prove semantic correctness, factual grounding, age appropriateness, fluency or paraphrase uniqueness.
- Remaining: canonical P7 adapter, persisted/auditable runs and findings, stronger semantic/paraphrase and deterministic subject solvers where feasible, fixed real-content evals, live-model baseline, APIs and admin inspection.

---

## P9 — Human Review, Question Bank & Paper Publishing
**Status:** IN_PROGRESS

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
- `279c4b7` starts P9 with a strict generated→validated→in-review→approved/rejected candidate lifecycle, immutable generation/provenance/validation lineage and reviewer revisions, optimistic command versions, approved-only exact-slot paper assembly, explicit publish authorization, deterministic immutable published snapshots/content hashes and forward-only archive contracts.
- One hundred fourteen focused paper tests pass with 100% statements and branches, including direct publish construction, forged states, stale commands, prompt-like authorization text, duplicate slots, unapproved candidates and publication mutation attempts.
- Remaining: canonical generation/validation adapters, persistence and database bypass triggers, append-only audit events, authorized APIs, reviewer/admin UI, approved question bank, and complete browser-tested Grade 5 paper publication.

---

## P10 — Priority 1 Full Acceptance / Production Readiness Gate
**Status:** NOT_STARTED

### Mandatory end-to-end journey
`admin login -> upload real Grade 5 source -> extraction/OCR -> human correction -> ingest -> question/knowledge normalization -> RAG retrieval -> historical analysis/backtest -> blueprint -> LLM generation -> automated validation -> human review -> publish`

### Exit criteria
- [ ] every P0-P9 phase is DONE
- [ ] Priority 1 E2E journey passes
- [ ] production identity-provider/login integration replaces the deterministic adapter and passes session/authentication security tests
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
P2 remains IN_PROGRESS on human-adjudicated Sinhala OCR ground truth. P3 is now IN_PROGRESS; the next non-blocked slice is PostgreSQL/pgvector persistence for reviewed knowledge chunks, historical questions and versioned embeddings, followed by reviewer classification APIs and metadata-safe P4 retrieval evals. Student Priority 2 remains blocked by P10.
