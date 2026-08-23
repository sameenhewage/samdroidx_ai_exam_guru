# Priority 1 Acceptance Audit Prompt

Use this only when the implementation appears close to `P10 — Priority 1 Full Acceptance`.

---

Audit `sameenhewage/samdroidx_ai_exam_guru` for **Priority 1 completion**. Your job is to prove or disprove readiness; do not assume prior tracker statuses are correct.

Read all repository V1 instructions/specifications and inspect implementation, tests, migrations, eval fixtures, CI, runtime configuration and tracker evidence.

## Rule
You may mark P10 DONE only if **P0-P9 are individually proven DONE** and the complete Priority 1 acceptance journey passes. If any criterion is incomplete, downgrade the relevant phase status, create/fix the missing regression coverage, and continue engineering instead of declaring completion.

## Revalidate every gate
Independently verify:
- P0 repository/engineering foundation;
- P1 Grade 5 domain/admin foundation;
- P2 source ingestion/extraction/OCR review;
- P3 historical question bank/curriculum knowledge base;
- P4 RAG retrieval/grounding and evals;
- P5 historical analytics/forecasting/backtesting with baseline comparison;
- P6 deterministic blueprint engine;
- P7 provider-independent structured LLM generation;
- P8 validation/evals/duplicate detection;
- P9 human review/question bank/immutable publishing.

## Mandatory end-to-end acceptance
Run or construct the most realistic repository-supported journey possible:

`admin login -> upload representative Grade 5 document -> extraction/OCR -> review/correction -> trusted ingestion -> knowledge/question normalization -> RAG retrieval with provenance -> historical analysis/backtest -> deterministic blueprint -> grounded LLM generation -> automated validation -> reviewer edit/approve/reject path -> immutable publish`

Where real licensed/official source documents are not available in the repository, use explicit fixtures for automation and clearly separate fixture proof from real-content validation requirements. Never claim synthetic fixtures prove Sinhala OCR/content quality.

## Mandatory adversarial checks
Before closing P10, specifically verify:
- authz/IDOR;
- malicious/malformed upload handling;
- prompt injection/RAG poisoning resistance;
- grade/medium/curriculum isolation;
- duplicate ingestion/generation retry safety;
- transaction/data integrity;
- source/embedding/prompt/model version provenance;
- publish-state bypass protection;
- immutable published versions;
- provider outage/retry behavior;
- token/cost limits/observability;
- migration from a clean database;
- CI from clean checkout;
- no hidden high/critical security or correctness findings.

Any valid defect must first become a failing regression test/eval, then be fixed and revalidated.

## Forecasting truthfulness gate
Re-run historical held-out backtests and verify no future information leaks into each held-out prediction. Compare against the syllabus-balanced baseline. If forecast value is weak, retain the implementation only as analytics and ensure product wording does not imply reliable prediction.

## RAG quality gate
Re-run the fixed Grade 5 retrieval suite. Verify metadata isolation and provenance completeness. Compare current metrics against the recorded baseline and reject regressions unless explicitly justified by a better quality trade-off.

## LLM quality gate
Normal CI may use deterministic fakes, but Priority 1 acceptance must also include a documented real-model evaluation when credentials/environment permit. Record provider/model/version, prompt version, retrieval config, cost, latency and quality results. If live credentials are unavailable, mark that acceptance criterion BLOCKED rather than inventing evidence.

## Completion decision
At the end output one of:

### `PRIORITY_1_COMPLETE`
Only if every P0-P10 criterion passes with evidence. Update the tracker to DONE with concise evidence and explicitly state that Priority 2 is now unlocked.

### `PRIORITY_1_INCOMPLETE`
If anything remains. Update tracker statuses/evidence accurately, fix everything you safely can in the same run, and list only genuine external/human blockers that remain.

Do not use percentages such as 95% as a substitute for acceptance. Priority 2 remains blocked until the result is `PRIORITY_1_COMPLETE`.