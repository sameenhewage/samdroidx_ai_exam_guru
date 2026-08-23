# V1 Engineering Workflow — Loop Engineering + TDD

## Purpose
This document defines **how GPT-5.6 Sol should execute the project**. The system is not implemented by completing one large phase prompt and then waiting for another. The agent should operate continuously against acceptance gates, always working on the highest-priority incomplete requirement.

## Operating loop
Repeat this loop until the active priority gate is fully satisfied:

1. **Inspect**
   - read `AGENTS.md`, this workflow, `00_V1_MASTER_PLAN.md`, `PHASE_TRACKER.md`, and relevant code/tests;
   - inspect current repository state, migrations, APIs, test coverage, eval results and CI;
   - identify the smallest high-value incomplete acceptance item inside the active priority.
2. **Plan locally**
   - define desired behavior, risks, tests/evals, data migration impact and rollback/recovery needs;
   - avoid broad speculative rewrites.
3. **RED**
   - add a failing test/eval that demonstrates the missing behavior or defect;
   - for an existing bug, reproduce it before fixing it.
4. **GREEN**
   - implement the smallest production-quality change that satisfies the test.
5. **REFACTOR**
   - improve naming/boundaries/duplication while all relevant tests remain green.
6. **Integrate**
   - run affected unit, integration, contract and eval suites;
   - run database migrations forward from a clean state;
   - verify job/worker behavior when applicable.
7. **Review adversarially**
   - inspect security, data integrity, race conditions, retries/idempotency, hallucination/grounding, cost and observability;
   - deliberately test unhappy paths and malformed inputs.
8. **Fix the findings**
   - each genuine finding gets a regression test/eval first, then a fix.
9. **Run the gate suite**
   - run the broadest practical test/eval/quality gates before calling an acceptance item complete.
10. **Document evidence**
   - update docs/ADRs only when decisions changed;
   - update `PHASE_TRACKER.md` with evidence and mark a phase DONE only when all its exit criteria pass.
11. **Commit cohesive work**
   - commit tested, coherent changes;
   - continue the loop without waiting for a new implementation prompt unless blocked by a genuinely external/human requirement.

## Priority discipline
### Active priority at V1 start: Priority 1
Priority 1 includes all Admin + content intelligence + RAG + LLM + generation + validation + publishing work.

Do not implement Priority 2 student features while any Priority 1 phase or acceptance criterion remains incomplete.

Allowed before Priority 1 closes:
- shared repo/app bootstrap;
- shared auth primitives if required for admin security;
- shared design primitives if required by the admin UI;
- infrastructure that will later also support students.

Not allowed before Priority 1 closes:
- student dashboard;
- student exam runner;
- student progress UI;
- student recommendations;
- student subscription UX beyond shared billing infrastructure that is required for foundation work.

## TDD rules
### Deterministic code
Use strict RED -> GREEN -> REFACTOR.

Examples:
- taxonomy validation
- document state transitions
- chunking metadata
- retrieval filters
- ranking/fusion algorithms
- forecasting scores
- backtest metrics
- blueprint constraints
- validation state machine
- publish rules
- permissions
- API behavior

### AI code
AI behavior is probabilistic, so use **eval-driven TDD**:

1. establish a fixed fixture/golden evaluation set;
2. define objective assertions where possible;
3. use structured output schemas;
4. test provider adapter behavior with deterministic fakes in normal CI;
5. run paid/live-provider eval suites explicitly/periodically, not as the only correctness gate;
6. record model/provider/version/prompt/retrieval configuration for every eval run;
7. compare changes against the previous baseline, not merely against an arbitrary pass/fail threshold.

## Required test policy
### Unit
Fast, deterministic tests for domain logic.

### Integration
Use real containerized PostgreSQL + pgvector and Valkey. Do not mock away critical SQL/vector/job behavior.

### API contracts
FastAPI/OpenAPI contract tests must ensure request/response schemas and error semantics remain stable.

### RAG evals
Maintain a Grade 5 retrieval fixture set that tests:
- correct curriculum scope;
- metadata filtering;
- retrieval relevance;
- source provenance;
- no cross-grade/cross-medium leakage;
- hybrid search behavior;
- reranking if introduced.

### Forecast/backtest evals
Use held-out years. Compare the active forecasting method against a syllabus-balanced baseline and record metrics.

### Generation/validation evals
Maintain cases for:
- structured output validity;
- syllabus alignment;
- age appropriateness;
- correct option count/single-correct MCQ behavior;
- answer correctness checks where deterministic verification exists;
- duplicate/paraphrase rejection;
- malicious/irrelevant retrieved context;
- refusal to invent unsupported curriculum claims.

### E2E
Priority 1 cannot close without an admin journey that covers:

`login -> upload real fixture -> extract -> review -> ingest -> retrieve -> analyze/backtest -> blueprint -> generate -> validate -> human review -> publish`

## Definition of done for an acceptance item
An item is not done because code exists.

It is DONE only when:
- requirements are implemented;
- tests/evals are present and passing;
- failure paths are handled;
- migrations/data consistency are verified;
- security/access control is verified where relevant;
- observability exists for production-critical paths;
- documentation is updated if behavior/architecture changed;
- no known blocker remains hidden in TODOs;
- tracker evidence points to concrete tests/commands/results/commits.

## Stop conditions
The agent should continue autonomously through the workflow unless one of these occurs:

- a secret/credential is required and unavailable;
- a paid/external account action cannot be performed safely;
- a legal/content-rights decision requires a human;
- an irreversible production operation requires explicit approval;
- source material/domain truth is genuinely ambiguous and cannot be resolved from repository evidence or trusted official material.

When blocked, record the blocker precisely in the tracker and continue other non-blocked work inside the same priority.

## Anti-patterns
Do not:
- create a phase-specific implementation prompt for every tracker phase;
- mark work DONE based on code review alone;
- skip failing tests because implementation looks correct;
- use live LLM responses as unversioned golden truth;
- call an LLM for deterministic calculations that code can perform reliably;
- hide low retrieval quality behind a larger model;
- introduce frameworks solely because they are fashionable;
- start Priority 2 because Priority 1 is "mostly done";
- leave broken tests/evals for later.

## Engineering target
At every point, the repository should tell the next agent exactly:
- what the product is;
- what priority is active;
- what acceptance gates remain;
- what evidence exists;
- what to work on next.

The loop should be resumable from repository state alone.