---
name: priority1-admin-acceptance
description: Use for Priority 1 admin product flows, tracker gate closure, full admin-to-published-paper acceptance, human review workflow, evidence collection, and deciding whether P10 can legitimately unlock student development.
---

# Priority 1 Admin Acceptance

## Mission
Priority 1 is the product before the student product. Do not dilute it into backend-only APIs. The admin must be able to operate the content-intelligence pipeline end to end.

## Required end-to-end journey
Acceptance must demonstrate with representative Grade 5 data:

`admin login -> manage taxonomy/curriculum -> upload source -> extract/OCR -> review/correct extraction -> ingest structured knowledge/question data -> inspect grounded RAG retrieval/provenance -> run historical analysis/backtest -> inspect deterministic blueprint -> run grounded LLM generation -> inspect automated validation -> human edit/review/approve/reject -> assemble paper -> publish immutable paper`

## Gate rules
Before marking a Priority 1 tracker phase DONE:
1. Read the phase exit criteria in `docs/v1/PHASE_TRACKER.md`.
2. Map each criterion to concrete evidence.
3. Run the relevant automated tests/evals.
4. Run required runtime/browser/manual checks.
5. Run relevant security/reliability review.
6. Record limitations honestly.
7. Add evidence/commit references to the tracker.
8. Mark DONE only if **all** criteria pass.

## P10 hard gate
P10 may be DONE only when:
- P0-P9 are legitimately DONE;
- clean bootstrap/migrations and CI pass;
- admin end-to-end acceptance passes;
- RAG/evals/backtesting/generation/validation evidence exists;
- human review/publishing state invariants pass;
- security/adversarial review is closed or documented with no release-blocking defects;
- observability and cost/token accounting required by the spec are functional;
- known limitations are explicit.

If any item is missing, P10 remains IN_PROGRESS/BLOCKED and Priority 2 remains BLOCKED.

## Evidence over narrative
Do not accept screenshots/prose alone where executable proof is possible. Prefer test names, eval metrics, runtime commands, browser journeys, DB invariants and commit ids.

Use `security-reliability-review` before P10 closure and always pair implementation fixes with `tdd-eval-engineering`.
