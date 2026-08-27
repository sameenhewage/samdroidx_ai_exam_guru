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
- `68afa05` wires the provider-independent OCR port into the native-first persisted worker. Only empty or sparse-overlay/image-dominant pages are routed; configured OCR receives exact checksum-bound page IDs, merges deterministically with native pages and persists bounded document/page/block engine, version, scalar config, confidence and nullable layout provenance through migration `0017`. Page-set mismatch, unsafe output, provider/config/input/unavailable/timeout/process/output-limit failures, partial persistence, duplicate workers and direct-database bypasses fail closed with sanitized codes. An explicit five-minute actor budget bounds the configured Tesseract page/command policy; no provider configured preserves native extraction and `needs_ocr=true` without failure. Backend gate: 1,844 passed / 2 expected optional skips at 100% statements and branches.
- `eecc238` adds bounded plain-text OCR/native/hybrid provenance inspection to Extraction Review, including routed page numbers, confidence, nullable bbox, honest legacy config and a prominent untrusted/no-quality warning. Web gate: 87 tests with configured 100% coverage; lint, generated-client typecheck and production build pass.
- `c967ff2` closes the abandoned-upload accumulation risk without adding automatic deletion. A scheduled, leased reconciliation actor walks the exact content-addressed `sources/` namespace through a durable pagination cursor, compares bounded pages to PostgreSQL, applies a one-day grace window, persists immutable scan/finding evidence and optionally merges only two app-owned reversible lifecycle tags while preserving operator tags. Findings resolve/reopen safely, dry-run state remains truthful, object keys/cursors never enter APIs or telemetry, and deletion remains an explicit external lifecycle approval. Real PostgreSQL/MinIO tests cover candidate, reference, resolution, cursor continuation and concurrent lease behavior.
- P2 remains IN_PROGRESS only because representative scanned Sinhala pages do not yet have human-adjudicated ground truth and no Sinhala-capable OCR executable/traineddata is installed for a defensible benchmark. No OCR quality claim is made.

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
- `bae816a` adds server-owned durable embedding ingestion: authorized ID-only batches commit queued jobs before dispatch, persist immutable provider/model/dimension/version/fingerprint/source snapshots, claim and progress through CAS, skip existing source/config vectors before provider work, serialize overlapping batches on source-row locks, preserve partial progress for retry convergence, and recover outbox plus stale claims with bounded internal actors. Real PostgreSQL/Valkey coverage proves cross-scope/non-reviewed rejection, queue crashes, idempotency races, duplicate messages and overlapping jobs without duplicate provider cost, partial retry, source conflicts, direct-database invariants and successful hybrid retrieval. Deterministic ingestion remains local/test only; staging/production fail closed without a real provider. Backend gate: 1,930 passed / 2 expected optional skips at 100% statements and branches.
- `4326a2a` adds generated-client embedding controls to Knowledge Studio with reviewed-record selection, ID-only bounded requests, durable polling, config/count/failure inspection, reviewer read-only behavior and refreshed record metadata. The real worker/browser journey embeds a reviewed question and chunk, then proves nonempty lexical, vector, fused and bounded-context results with exact provenance and hard-scope filtering in RAG Explorer. Web gate: 97 tests with configured 100% coverage; full Chrome suite passes 6/6.
- Remaining: document the representative real Grade 5 acceptance threshold and meet it on human-reviewed embedded content. The successful deterministic browser path proves mechanics only. No reranker is added because no benchmark currently proves benefit.

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
**Status:** DONE

### Scope
- paper structure rules
- competency/skill allocation
- difficulty allocation
- marks/question-type constraints
- forecast-informed practice priorities
- versioned blueprint creation

### Exit criteria
- [x] blueprint generation is deterministic for the same inputs/seed/config where designed
- [x] coverage constraints are validated in code
- [x] impossible blueprints fail clearly
- [x] blueprint slots carry all generation requirements and rationale/evidence metadata
- [x] tests cover boundary distributions and rule conflicts
- [x] admin can inspect a blueprint before generation

### Evidence
- `7a04c3a` starts P6 with a deterministic versioned blueprint constraint solver, exact paper/section marks, question-type/difficulty/taxonomy allocations, impossible-constraint diagnostics, baseline-safe forecast priorities and self-contained generation slots with scope, rationale and evidence.
- One hundred twenty-six focused blueprint tests pass with 100% statements and branches across deterministic seeds, boundary distributions, conflicts, backtracking and forged aggregate invariants.
- `e76df48` adds migration `0012`, immutable bounded specification/blueprint/taxonomy snapshots, exact version/seed/marks/slot/fingerprint columns, same-curriculum analytics FKs, reviewed taxonomy hierarchy checks, atomic idempotency/race convergence and append-only protection. Persisted P5 results are fingerprint-, curriculum-, algorithm- and leakage-validated before server-side priority adaptation; clients cannot supply forecast evidence. Fully typed authorized create/list/get APIs expose stable diagnostics.
- Real PostgreSQL integration covers exact marks/slots, analytics linkage and cross-scope rejection, client forecast spoofing, taxonomy attacks, impossible rules, audit, composite FKs, JSON bounds and immutability. The P6 gate is 168 focused tests with 100% statements and branches; the complete backend point passed 1,491 tests with two explicit optional provider/OCR skips.
- `f350080` adds the generated-client Blueprint Studio with guided exact constraints, optional persisted analytics linkage, baseline-only client priorities, deterministic/idempotent feedback, impossible/conflict/permission states and immutable inspection of versions, fingerprints, allocations, slots, rationale/evidence and taxonomy snapshots. Component/axe gate: 50 web tests; the real API browser suite passes 5/5 including admin generation/deduplication and reviewer read-only inspection.

---

## P7 — LLM Provider Layer & Question Generation
**Status:** DONE

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
- [x] model/provider/prompt/retrieval versions are stored for every generation
- [x] paid/live provider is not required for normal CI
- [x] provider failure/retry/idempotency is tested
- [x] cost/token usage is captured per generation run
- [x] generation uses blueprint + grounded context, not a generic "make a paper" prompt

### Evidence
- `e9a3d3b` starts P7 with provider-independent typed contracts, canonical P6 blueprint-slot integration, bounded untrusted provenance context, strict structured question/answer/marking schemas, prompt/provider/model/blueprint/retrieval/schema versions, token/cost/latency accounting, typed failures, deterministic fakes, idempotency identity and append-only prompt version registration.
- The generation boundary has no SDK dependency or publish authority; all candidates require validation. Prompt templates reject reserved context interpolation while retrieved prompt-injection text remains opaque data. Generation and blueprint integration tests pass with 100% generation-package statements and branches.
- `4ff624c` adds bounded orchestration with retryable-code-only attempts, linked identities, injected backoff scheduling, atomic result-cache semantics and cumulative input/output token and integer-microusd budgets across successes and accounted failures.
- `5a28ecd` adds the initial OpenAI adapter with the SDK isolated behind the port. `openai==3.1.0` was the newest release older than seven days and had no OSV advisory at selection. SDK retries are disabled, time/output/idempotency are bounded, strict schema parsing maps into first-party contracts, trusted blueprint instructions and digest-delimited untrusted context use separate roles, errors are sanitized, and versioned integer pricing captures latency/tokens/cost. The mocked adapter gate is 266 passed with 100% statements and branches; the paid live eval is explicitly skipped without opt-in credentials and makes no quality claim.
- `41103d0` persists curriculum-scoped generation runs, queued jobs and append-only attempts on immutable P6 blueprints and reviewed trusted P3 context. Server-owned prompt/provider/model/retrieval/schema/pricing configuration, complete request/context snapshots, CAS state transitions, sanitized failures, explicit retries, integer-microusd accounting, audit events, role-separated APIs and database invariants are covered against real PostgreSQL/Valkey.
- `15ffc27` closes queue crash windows with bounded outbox redelivery, stale worker-lease recovery, persisted accounting reconciliation and completion locks. At-least-once duplicate messages converge through claim CAS without a duplicate provider call; periodic scheduler activation remains a P10 deployment responsibility.
- `9306b1e` adds the generated-client Generation Studio with immutable blueprint-slot and exact-taxonomy context selection, IDs-only requests, bounded idempotency, durable polling, explicit retry, full versions/provenance/attempt/token/cost/latency inspection and a prominent `REQUIRES VALIDATION` no-publish state. The web gate is 63 tests at configured 100% coverage; the real API/worker/browser suite passes 6/6 including admin generation and reviewer read-only inspection.
- The paid OpenAI live evaluation remains opt-in and is deferred to P10 model-quality/cost acceptance; P7 makes no Sinhala question-quality claim from deterministic or mocked evidence.

---

## P8 — Automated Validation, Evals & Duplicate Detection
**Status:** DONE

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
- [x] invalid structured output is rejected
- [x] generated question cannot bypass validation to published state
- [x] duplicate/paraphrase regression fixtures exist
- [x] MCQ single-correct-answer rules are tested where relevant
- [x] deterministic answer verification is used where possible
- [x] validation findings are persisted and auditable
- [x] every discovered quality defect becomes a regression test/eval case
- [x] live-model eval baseline exists for the chosen generation configuration

### Evidence
- `f0f45b8` starts P8 with immutable versioned validation inputs/reports/findings, stable pass/warn/fail codes, bounded non-leaking evidence, deterministic schema/blueprint/option/answer/marks/grounding/injection/language/age indicators and exact/hash duplicate checks composed through a canonical pipeline.
- Ninety-four focused validator tests pass with 100% statements and branches. Reports explicitly state that deterministic success does not prove semantic correctness, factual grounding, age appropriateness, fluency or paraphrase uniqueness.
- `3fce869` adds immutable PostgreSQL validation runs and append-only findings bound by same-curriculum foreign keys to the exact succeeded generation result. The API accepts only a generation-run ID, reconstructs the candidate, blueprint and trusted provenance server-side, verifies every fingerprint, loads a bounded reviewed duplicate bank, executes the canonical pipeline, persists complete reports transactionally and race-idempotently, records audit events, and exposes role-separated list/detail/finding reads. Database triggers reject incomplete, inconsistent, updated or deleted reports.
- `1c5e930` adds a versioned Unicode character-ngram lexical-overlap indicator and fixed English/Sinhala near-copy, clause-reordering, conservative false-positive, dissimilar and meaning-similar-but-lexically-different fixtures. Work and evidence are bounded and source text is never copied into findings. The report explicitly states that this is not semantic paraphrase detection and can produce false positives and negatives. The opt-in paid-provider test now carries the exact generated result through canonical P8 validation and records versions, fingerprints, pass/warn/fail counts, tokens, latency and integer cost; it remains skipped without explicit credentials.
- `285f690` adds the generated-client Validation Studio with admin-only execution, reviewer read-only inspection, immutable report/version/fingerprint/provenance/finding views, bounded plain-text rendering and a prominent no-automated-approval limitation. The web gate is 67 tests at configured 100% coverage; the real API/worker/browser suite passes 6/6 through admin generation, validation and reviewer inspection, including reviewer POST 403.
- At the prior deterministic checkpoint, 1,677 tests passed with two optional skips and 100% statements and branches; Ruff, format and strict mypy passed. The then-unexecuted optional baseline is superseded by the live evidence below. No deterministic or lexical result is represented as factual, semantic, language or curriculum approval; P9 human review remains mandatory.
- The opt-in paid baseline was executed against `openai` / `gpt-4o-mini-2024-07-18` with SDK `3.1.0`, prompt `question-generation:live-contract-v2`, retrieval `live-fixture-retrieval-v1`, schema `question.v1`, pricing `openai-gpt-4o-mini-2024-07-18`, temperature `0.0`, seed `9`, no SDK retries and provider storage disabled. The successful structured result remained `requires_validation`: 1,086 input + 163 output = 1,249 tokens, 4,530 ms, and 261 microusd ($0.000261). Canonical validation produced 12 PASS, one bounded `heuristic.language_script` WARN and zero FAIL across 13 findings; generation fingerprint `40e5f60428f642e3bb1a8a48a0b4f3c572ce9494cf1cc7ccbb23ba01228cc53c`, validation-input fingerprint `9d6f81647903b5016fbe505a7f30634f8a48bb17d8116a3842df4e7d3d728ae3`, and report fingerprint `e6870f30b1ddc41bbe107fc252dbb8af77dcc973e1464ce9568df90b7de6dc03` make the measured run auditable without recording generated text or credentials.
- `2de2cc5` records both defects discovered by the first live execution as regressions. The adapter no longer sends provider metadata while `store=false`, and blueprint question type now selects a strict transport schema plus trusted type-specific output contract before first-party domain validation. Production prompt configuration advances to `1.1.0`; normal CI remains paid-provider-independent. Current gate: 2,347 backend tests / two expected optional skips plus isolated restore at 100% statements and branches, 325 web tests at configured 100% coverage, and a clean-volume 8/8 Chromium suite whose integrated P10 journey completes in 11.9 seconds.
- This is a single English contract/cost baseline, not proof of Sinhala quality, broad semantic correctness, factual correctness or population-level model quality. Human review and the separate real-data quality gates remain mandatory.

---

## P9 — Human Review, Question Bank & Paper Publishing
**Status:** DONE

### Scope
- reviewer queue
- source/context visibility
- edit/approve/reject workflow
- approved question bank
- paper composition
- immutable published paper versions
- archive/unpublish rules

### Exit criteria
- [x] reviewer can inspect question, answer, blueprint, retrieved sources and validation results together
- [x] approve/reject/edit actions are authorized and audited
- [x] rejected questions cannot be published
- [x] published paper versions are immutable/reproducible
- [x] student serving path requires no live LLM call
- [x] complete Grade 5 practice paper can be generated, reviewed and published end-to-end

### Evidence
- `279c4b7` starts P9 with a strict generated→validated→in-review→approved/rejected candidate lifecycle, immutable generation/provenance/validation lineage and reviewer revisions, optimistic command versions, approved-only exact-slot paper assembly, explicit publish authorization, deterministic immutable published snapshots/content hashes and forward-only archive contracts.
- One hundred fourteen focused paper tests pass with 100% statements and branches, including direct publish construction, forged states, stale commands, prompt-like authorization text, duplicate slots, unapproved candidates and publication mutation attempts.
- `6a9150e` adds the persisted approved-question-bank foundation: server-derived candidates can be created only from the exact same-curriculum succeeded generation and immutable PASS validation run; PostgreSQL enforces the upstream blueprint/slot/provenance/finding lineage, normalized append-only revisions and review events, CAS transitions, terminal immutability and direct-SQL completeness. Reviewer/admin APIs support bounded queue reads and audited start/edit/approve/reject commands; automated validation is explicitly bound to generated revision 1 while type and marks remain immutable across human edits.
- `83b6d81` adds the generated-client Reviewer Studio. Reviewers inspect generation content, blueprint, context provenance and complete P8 findings together; preserve/conflict-resolve unsaved edits; and execute audited start/edit/approve/reject transitions. The real worker/browser journey reaches terminal approval and proves a terminal mutation returns 409.
- `f3bfc38` adds normalized practice-paper aggregates, immutable draft selections, immutable publication snapshots and append-only archive events. Exact approved/current candidate versions must cover every persisted blueprint slot. Canonical Unicode JSON is reconstructed from authoritative PostgreSQL rows and SHA-256 checked in both Python and PostgreSQL. Revision chains are contiguous, archive is terminal, reviewer/admin reads and assembly are separated from admin-only publish/archive, snapshots are bounded and later serving requires no generation or provider call. Direct-SQL bypass, concurrent publish, idempotency, rejected/foreign/stale candidates, audit rollback, downgrade/reapply and hash tampering are covered against real PostgreSQL.
- `5621f8d` adds the generated-client Paper Studio with exact-slot approved-bank selection, draft/revision inspection, admin-only publication/archive controls, immutable content/version/hash/provenance/validation/reviewer views and a no-live-provider serving statement. Final web gate: 86 tests at configured 100% coverage; the full real browser suite passes 6/6 through upload, extraction/trust, knowledge review, blueprint, worker generation, validation, human edit/approval, reviewer paper assembly, reviewer publish denial, admin publication and immutable snapshot inspection.
- Final backend gate after multi-slot acceptance: 1,756 passed and 2 expected optional skips with 100% statements and branches; Ruff, format, strict mypy, migration-head, npm audit and secret scans pass.
- `14da846` closes the complete-paper gate with a deterministic three-slot mixed blueprint covering MCQ, short-answer and structured-response contracts at two marks each. Type-specific local/test outputs remain pairwise below the bounded lexical-overlap warning threshold without claiming semantic quality. The real browser journey generates all three slots through the worker, validates them sequentially with duplicate-bank evidence, human-reviews and approves all three (including one retained edit), assembles exact ordered coverage, proves reviewer publish 403, publishes as admin, and verifies the immutable three-question snapshot, hash, provenance, validation and review lineage without changing generation/provider state. The targeted acceptance passes in 10.1 seconds and the full browser suite passes 6/6.

---

## P10 — Priority 1 Full Acceptance / Production Readiness Gate
**Status:** IN_PROGRESS

### Mandatory end-to-end journey
`admin login -> upload real Grade 5 source -> extraction/OCR -> human correction -> ingest -> question/knowledge normalization -> RAG retrieval -> historical analysis/backtest -> blueprint -> LLM generation -> automated validation -> human review -> publish`

### Exit criteria
- [ ] every P0-P9 phase is DONE
- [x] Priority 1 E2E journey passes
- [x] teacher-first multi-grade Materials, Generate Papers, Review & Approve and Published Papers workflows pass
- [x] reusable Grade 1–13 subject/unit/lesson scope is enforced before retrieval and generation
- [ ] production identity-provider/login integration replaces the deterministic adapter and passes session/authentication security tests
- [x] security/adversarial review completed with regression tests for findings
- [x] migrations verified from clean database
- [x] backup/restore or data recovery approach documented for critical source/question data
- [x] observability covers ingestion, jobs, retrieval, LLM calls, validation and publishing failures
- [x] AI cost metrics are observable
- [ ] representative Sinhala Grade 5 real-content quality review completed
- [x] known limitations documented
- [x] CI green on the release commit

### Evidence
- `a7366bd` adds deployment-level recovery scheduling and closes extraction's commit-before-dispatch window. Migration `0019` persists bounded per-attempt extraction queue identity; same-request replay and a `FOR UPDATE SKIP LOCKED` recovery actor safely redrive pending/null outbox rows with sanitized audits. A long-running maintenance service enqueues extraction, generation and embedding recovery actors every bounded interval with monotonic timing, error isolation and clean shutdown. Compose/CI require the scheduler to be healthy; the real Compose service is verified healthy. Backend gate: 1,973 passed / 2 expected optional skips at 100% statements and branches.
- `a1a179e` adds validated web runtime configuration, production HTTPS/secure-cookie enforcement, CSP and defense-in-depth security headers, plus exact Origin/Fetch-Metadata rejection before cookie mutation or same-origin proxy calls. Malicious cross-site requests cannot reach upstream/cookie side effects; bearer-authenticated backend APIs remain outside browser-cookie CSRF semantics. Header, hydration and login acceptance passes in real Chrome.
- `94f6d57` adds two production-readiness controls. First, a source-verified backup/restore runbook and guarded `pg_dump`/`pg_restore` scripts default to verification/dry-run, reject credential leakage and nonempty targets, and require exact destructive confirmation. A disposable PostgreSQL 18/pgvector source→target restore preserves critical extraction, knowledge/embedding, generation, validation, review, publication/hash and audit invariants; migration `0020` fixes a real empty-`search_path` restore defect in canonical publication hashing. Second, admin-only operations aggregation and content-free structured logs/manual OpenTelemetry spans cover extraction, embedding, retrieval, generation tokens/cost/latency, validation and publish/archive outcomes. Main backend gate: 2,039 passed / 2 expected skips / 1 isolated restore test at 100% statements and branches; the dedicated restore test passes separately.
- `baab94c` adds the admin-only Operations dashboard with fixed UTC windows, exact integer microusd and lossless USD rendering, status/failure/token/latency/OCR/embedding/publication aggregates, explicit units and collector/dashboard limitations. Reviewer navigation is hidden and direct access is 403.
- `e1e3cf5` adds atomic Valkey fixed-window cost controls after authentication/authorization and before side effects for source uploads, extraction, embedding, generation/retry, validation and publish/archive operations. Principal/scope keys are SHA-256-derived without tokens or content; exact 429 `Retry-After`, fail-closed sanitized 503 behavior, production non-disablement, scope isolation, expiry and concurrency bounds are executable against real Valkey. `a6814e3` regenerates the typed contracts.
- `c967ff2`, `ab3102a` and `56c3cd2` add no-delete source-object reconciliation, typed operational aggregates and dashboard inspection for scan/candidate/resolution/tag/truncation/failure health. Scheduler/lease duplication is safe, truncated inventories progress through a durable opaque cursor, operator tags are preserved, and candidate keys/cursors never leave persistence. Backend gate at this point is 2,151 passed / 2 expected optional skips at 100% statements and branches.
- `97ca985` fixes the release-only Blueprint Studio race where re-selecting an already auto-selected curriculum invalidated its in-flight taxonomy request without changing React scope. The exact regression fails before the guard and passes after it. A disposable empty-volume Compose environment now passes all 8 Chromium admin/security journeys in 29.8 seconds; web unit gate passes 153 tests at configured 100% coverage, plus lint, root typecheck and production build.
- `d33362b` and `5945a1e` add the production OIDC backend boundary and typed session contract. Production now requires OIDC; access tokens are bounded and verified against pinned asymmetric RS256/ES256 JWKS keys with exact issuer/audience/time/lifetime checks, key-strength enforcement, bounded cache/network work, strict role mapping and stable UUIDv5 subjects. `PyJWT 2.13.0` and `cryptography 50.0.0` are older than seven days and the dependency audit reports no known vulnerabilities. Backend gate: 2,307 normal-CI tests / 2 expected optional skips plus the isolated restore test, with 100% statements and branches.
- `0d233e7` adds the provider-independent web authorization-code flow: same-origin POST initiation, 256-bit state and PKCE, single-use bounded callback including RFC 9207 issuer validation, server-side bounded token exchange, authoritative backend session introspection, cookie-octet-safe Strict session cookies, fixed redirects and optional RP logout. Invalid callbacks preserve existing sessions and cannot replay transient state. Local deterministic identity now also derives display roles from backend authority. Web gate: 321 tests, lint, root typecheck and production build; a fresh empty-volume Compose runtime preserves all 8 deterministic admin/security browser journeys.
- `docs/v1/06_KNOWN_LIMITATIONS.md` records the unresolved human-data, live-IdP and deployment responsibilities without waiving any gate. The generic OIDC implementation is complete, but the production identity criterion remains open until a real external tenant/client/role mapping and browser session are exercised.
- The final P10 adversarial review independently covered OIDC/browser sessions, uploads/OCR/RAG/generation/validation/publishing invariants, migrations, recovery, storage, observability and abuse controls. Reported JWKS stampede, publication race, pagination-loop, lease-race, prompt-sanitization and logout-redirect concerns were verified as already bounded by double-checked locking, CAS/unique constraints, seen-token plus object limits, immutable untrusted-data separation, or exact deployment configuration. Deployment-only ingress/live-IdP limitations remain documented.
- `6c378c1`, `dfab67a` and `be2e8c4` close the one reproducible cost-amplification defect. Migration `0022` backfills and enforces immutable retry depth 0–3, failed/same-request predecessor identity and one-child lineage for both generation and embedding; corrupt legacy chains fail migration rather than being capped. Application/API and admin UI reject or hide a fourth retry, preserve same-key deduplication and serialize different-key retry forks. Final gate: 2,347 normal-CI backend tests / 2 expected skips plus isolated restore at 100% statements and branches; 324 web tests at configured 100% coverage; fresh Compose Chromium remains 8/8.
- `71d72dd` and `6184f3e` close the automated Priority 1 journey as one same-curriculum lineage: native extraction, expected-version human correction with raw-text preservation, trust, reviewed historical records, leakage-audited held-out analytics, analytics-linked blueprint, persisted embedding, exact-scope hybrid RAG, three generated and validated slots, reviewer edit/approval, reviewer publish denial and administrator hash-verified publication. Request-generation guards also prevent stale validation responses crossing curriculum scope. A fresh clean-volume Compose run passes all 8 Chromium journeys in 31.8 seconds, with the integrated path completing in 11.9 seconds and no arbitrary sleeps.
- The automated journey uses bounded synthetic Grade 5 mechanics fixtures so normal CI is reproducible; it does not satisfy the separate representative Sinhala/real-data quality criteria. P8 live English contract/cost evidence is recorded in its phase section.
- Release checkpoint `dcf3bf1` is green in CI run `32820481954`: backend lint/format/mypy, 100% coverage tests, isolated restore, OpenAPI verification and committed-file secret scan; frontend dependency audit, generated-client verification, lint/typecheck, 100% coverage tests and production build; and clean Compose health plus all 8 Chromium admin/security journeys. Backend, frontend and runtime jobs all completed successfully.
- `357fd4a` and migration `0023` generalize reusable persistence and hard retrieval/generation boundaries to Grades 1–13 with first-class subjects, curriculum units, lessons and independent lesson-to-taxonomy mappings. Existing Grade 5 IDs/provenance are preserved under an explicit legacy-unclassified subject; direct SQL and real PostgreSQL tests enforce cross-grade, cross-subject and lesson isolation, active-material exclusion, embedding eligibility and guarded downgrade.
- `5189b1a` and migration `0024` add server-owned subject/scope validation input v3, a versioned subject router, bounded exact-arithmetic/fraction/percentage Maths checks, equivalent-option and marking consistency failures, explicit unsupported-subject/factual WARN findings and a structured grounded semantic-verifier port. WARN enters mandatory human review; FAIL cannot create a candidate. Arbitrary expressions are parsed through a bounded AST and never executed.
- `2dbc3c8`, `4234534` and `cb953d0` replace the primary engineering IA with Home, Materials, Generate Papers, Review & Approve and Published Papers while retaining specialist studios under Advanced. Teachers can browse Grades 1–13, upload through a guided scope wizard, identify duplicates, correct/remove/restore wrong materials with CAS, generate Grade 7 Maths Lessons 1–3 or full-subject deterministic fixtures without IDs, inspect answers/marking/sources/friendly checks together, and keep technical lineage collapsed.
- `ea9855e` and migration `0025` add durable teacher paper aggregates, per-slot state and append-only run lineage. Teacher intent resolves exact curriculum/lesson/taxonomy scope server-side, hard-scoped RAG precedes generation, workers/recovery advance generation and subject validation, partial failures and regenerations are bounded, stale edited validation cannot approve, and immutable draft creation requires every current question to be approved.
- Redesign gate: 2,562 backend tests / two expected optional skips at 100% statements and branches plus isolated restore; 370 web tests at configured 100% coverage; Ruff, mypy, generated-client typecheck, ESLint and production build pass. A fresh empty-volume production Compose run applies migrations through `0025` and passes all 17 Chromium journeys in 1.2 minutes, including all nine teacher-content contracts. Grade 7 evidence is deterministic architecture/isolation evidence only, not an educational-quality claim.
- `1ca3da3` makes secure local host-filesystem storage the private Studio default while retaining S3/MinIO as an optional profile. Immutable no-clobber writes, checksum reads, restart-safe pagination, reconciliation sidecars, shared non-root container mounts and authenticated no-store PDF delivery are covered at 100%; teacher extraction review now presents the authorized original PDF beside corrected text. Existing MinIO data is intentionally not auto-migrated or deleted.
- Current storage gate: 2,674 backend tests / two expected skips at 100% coverage, 373 web tests at configured 100%, restore/static/build gates, and 17/17 clean-Compose Chromium journeys all pass with the default runtime healthy without MinIO.
- Migration `0026` adds append-only reviewer feedback and immutable versioned quality examples with exact candidate/generation/validation/curriculum lineage, structured reason codes, explicit promotion, second-reviewer CAS approval, bounded private export and deterministic replay. Edit feedback replays the judged pre-edit revision while preserving both generated and corrected snapshots; non-passing expectations require stable finding codes, unavailable semantic verification cannot mask a deterministic regression, overlapping eval cohorts have run-scoped result identities, and replay mutations use the existing fail-closed validation rate limit.
- Teacher Review & Approve captures plain-language correction reasons, keeps model/prompt changes explicitly manual, and browser-proves edit -> feedback -> draft quality example -> second-reviewer approval with technical details collapsed. Direct SQL mutation guards, transaction rollback, concurrent approval, replay deduplication and overlapping-cohort persistence are covered against real PostgreSQL.
- Correction-memory final gate: 2,713 backend tests passed with two expected optional skips and one backup/restore deselection at 100% statements and branches; the isolated PostgreSQL backup/restore test passed; Ruff, formatting, strict mypy, OpenAPI generation, secret scan and backup/restore static checks passed. All 374 web tests passed at 100% statements/branches/functions/lines; generated-client typecheck, ESLint, npm audit and production build passed. A separate fresh-volume local-filesystem Compose project applied migration `0026`, exposed the private quality contracts and passed all 18 Chromium journeys in 1.2 minutes before the original healthy Studio runtime and storage mount were restored.
- The factual-verifier adapter slice adds an optional SDK-contained OpenAI implementation behind the first-party `GroundedSemanticVerifier` port. Strict structured outputs are limited to supported/contradicted/insufficient evidence; private candidate/source payloads stay in the untrusted user role; returned evidence must match exact supplied context/source/page identities; duplicate context IDs, oversized payloads, malformed output and provider failures fail closed. Provider/model/prompt/pricing lineage plus exact token/cost/latency accounting survive in immutable validation findings, while model agreement still has no publication authority.
- Runtime configuration is complete but disabled by default. API and teacher-paper workers build the same configuration-derived pipeline version; secrets are not forwarded to migration or maintenance containers; validation executes off the async event loop; authenticated rate limits and a 30-second provider deadline bound work. A generation-row single-flight lock plus deterministic provider idempotency prevents concurrent duplicate cost, and explicit rollback releases locks on every failure. Deterministic mocked and real PostgreSQL concurrency evidence passes; the opt-in three-case English Science live benchmark is committed but correctly skipped without explicit credentials and pricing.
- Factual-verifier final gate: 2,746 backend tests passed with three expected optional skips and one backup/restore deselection at 100% statements and branches; Ruff, formatting, strict mypy, secret scan, Compose validation, npm audit, backup/restore static checks and the isolated PostgreSQL restore passed. A separate fresh-volume local-filesystem Compose build kept semantic credentials out of migration/maintenance, started healthy with the adapter disabled, and passed all 18 Chromium journeys in 1.2 minutes; the original healthy Studio database and `.exam-guru-data` mount were then restored.
- Remaining repository hardening requires live production identity acceptance, human Sinhala OCR/data review, representative P3-P5 real-data thresholds and a configured/live-evaluated factual semantic verifier.

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
P2 remains IN_PROGRESS on human-adjudicated Sinhala OCR ground truth. P3-P5 remain IN_PROGRESS on representative human-reviewed real Grade 5 data and measured quality thresholds, and live production identity plus the semantic-verifier live baseline remain externally blocked on credentials/deployment evidence. The next non-blocked Priority 1 slice is deterministic factual-claim decomposition plus claim-level structured result persistence and operational cost/latency aggregation, exercised through deterministic verifier fixtures before any paid call. No production factual-quality claim may be made until a real provider configuration and representative human-adjudicated evidence are available. Student Priority 2 remains blocked by P10.
