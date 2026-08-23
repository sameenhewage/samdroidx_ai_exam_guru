# Adversarial Review + Fix Loop Prompt

Use this after a meaningful implementation tranche or before an acceptance gate is closed.

---

Perform an adversarial engineering review of the current **active Priority 1 implementation** in `sameenhewage/samdroidx_ai_exam_guru`.

Read `AGENTS.md`, `docs/v1/00_V1_MASTER_PLAN.md`, `docs/v1/01_ENGINEERING_WORKFLOW.md`, `docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`, and `docs/v1/PHASE_TRACKER.md` first.

Do not only write a review report. Execute a full review/fix loop.

## Review areas
At minimum inspect and actively try to break:

### Architecture / boundaries
- framework leakage into domain code;
- OpenAI/provider SDK leakage into domain services;
- unnecessary coupling;
- hidden microservice-like complexity;
- unsafe global state;
- broken async/job boundaries.

### Security
- admin/reviewer authorization bypass;
- IDOR/object-level authorization;
- upload content-type spoofing;
- oversized/malformed files;
- path traversal;
- SSRF/URL ingestion risks if any;
- prompt injection from source documents;
- RAG poisoning/instruction injection;
- secrets/log leakage;
- unsafe error disclosure;
- rate/cost abuse.

### Data integrity
- duplicate ingestion;
- duplicate generation jobs;
- unsafe retries;
- partial writes;
- missing transactions;
- stale embedding versions;
- broken provenance;
- illegal state transitions;
- publish-state bypass;
- mutable published papers;
- missing audit trail.

### RAG quality
- metadata-filter bypass;
- cross-grade/cross-medium leakage;
- poor lexical/vector fusion;
- irrelevant top-K context;
- citation/provenance loss;
- adversarial query behavior;
- retrieval eval regressions.

### Forecasting/backtesting
- data leakage from held-out year;
- non-reproducible metrics;
- LLM-derived deterministic scores;
- missing baseline comparison;
- misleading future-exam claims.

### LLM generation
- unstructured/unvalidated responses;
- unsupported curriculum claims;
- hallucinated sources;
- duplicate/paraphrased historical questions;
- incorrect MCQ answers;
- excessive context/token use;
- missing model/prompt/retrieval version metadata;
- provider outage/retry problems.

### Admin UX
- impossible recovery from failed jobs;
- hidden validation findings;
- insufficient source visibility for reviewers;
- accidental approval/publishing;
- poor status/error clarity.

### Testing/evals
- happy-path-only coverage;
- mocked database/vector behavior where real integration is required;
- tests that assert implementation details instead of behavior;
- missing regression fixtures;
- nondeterministic paid-provider dependency in normal CI;
- weak or absent quality baselines.

## Mandatory fix process
For every genuine issue you find:
1. reproduce it with a failing test/eval;
2. record severity and affected acceptance criterion;
3. implement the smallest robust fix;
4. re-run focused tests;
5. re-run relevant integration/eval suites;
6. refactor if needed;
7. confirm no regression;
8. update tracker evidence if the fix changes acceptance status.

Do not dismiss a finding merely because it is inconvenient to fix. Do not weaken existing tests.

## Completion
Continue review/fix cycles until no known high/critical defect remains in the reviewed scope and relevant gates are green.

At the end, report:
- defects found grouped by severity;
- regression tests/evals added;
- fixes made;
- remaining accepted limitations;
- test/eval results;
- tracker changes;
- commits.

Do not mark a tracker phase DONE unless all of its exit criteria are actually satisfied.